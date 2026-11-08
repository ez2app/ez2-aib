//! Windows process trees through a Job Object (DECISION-8 item 2,
//! DECISION-11, interface agreed in IFACE-win-job / REPLY-IFACE 2026-11-08).
//!
//! Every `unsafe` block is one FFI call with its own SAFETY note
//! (`clippy::undocumented_unsafe_blocks` and `multiple_unsafe_ops_per_block`
//! are denied for this crate).
//!
//! What the caller can rely on:
//!
//! - **Nothing runs outside the Job.** [`Job::spawn`] starts the child with
//!   `CREATE_SUSPENDED`, puts it in the Job, and only then resumes it. If the
//!   assign or the resume fails, the child is terminated and the call
//!   returns `Err`; it never runs unmanaged. Everything the child starts
//!   later is in the Job too, and the Job does not allow breakaway
//!   (`BREAKAWAY_OK` / `SILENT_BREAKAWAY_OK` are not set).
//! - **Closing the Job kills the tree.** `KILL_ON_JOB_CLOSE` is set. When the
//!   last handle closes, every process still in the Job is terminated. That
//!   includes the owner (aibd) crashing, since the kernel closes its handles
//!   (DECISION-11 B: an aibd crash ends the seats it manages). Dropping a
//!   [`Job`] therefore kills its processes.
//! - **"Clean" is decided by the Job only.** [`Job::active_processes`] `== 0`
//!   and a successful [`Job::members`] mean the tree is gone. ppid is never
//!   read. Identity is `(pid, creation time in 100 ns FILETIME units)`.
//! - **Errors are classified, never guessed.** Every call clears the thread's
//!   last-error with `SetLastError(0)` first. A failure that leaves it at 0
//!   is [`SysError::Unknown`], never a code left over from an earlier call.
//!   This follows the errno rule of the macOS half.
//!
//! UNVERIFIED (not measured; the CI cell is "CI-windows" level, ADR 0001):
//! real AI CLIs under a Job, `CTRL_BREAK` under ConPTY, and `CTRL_BREAK`
//! when aibd has no console (background start).

use crate::SysError;
use std::mem::{size_of, MaybeUninit};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
use std::os::windows::process::CommandExt;
use std::process::{Child, Command};
use std::ptr::{null, null_mut};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{
    GetLastError, SetLastError, ERROR_ACCESS_DENIED, ERROR_INVALID_HANDLE, ERROR_INVALID_PARAMETER,
    ERROR_MORE_DATA, FILETIME, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::System::Console::{GenerateConsoleCtrlEvent, CTRL_BREAK_EVENT};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob,
    JobObjectAssociateCompletionPortInformation, JobObjectBasicAccountingInformation,
    JobObjectBasicProcessIdList, JobObjectExtendedLimitInformation, QueryInformationJobObject,
    SetInformationJobObject, TerminateJobObject, JOBOBJECTINFOCLASS,
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::Threading::{
    GetProcessTimes, OpenProcess, OpenThread, ResumeThread, TerminateProcess, WaitForSingleObject,
    CREATE_NEW_PROCESS_GROUP, CREATE_SUSPENDED, PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SYNCHRONIZE, PROCESS_TERMINATE, THREAD_SUSPEND_RESUME,
};
use windows_sys::Win32::System::IO::{
    CreateIoCompletionPort, GetQueuedCompletionStatus, OVERLAPPED,
};

/// Flags this module sets on the Job and nothing else.
pub const LIMIT_FLAGS: u32 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
/// Flags that would let a process leave the Job; never set.
pub const BREAKAWAY_FLAGS: u32 =
    JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK;
/// Creation flags [`Job::spawn`] sets. They **replace** any
/// `creation_flags` set on the `Command` before (std keeps one value).
pub const SPAWN_FLAGS: u32 = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP;
/// How often a pid list is retried with a larger buffer (same rule as
/// [`crate::list::RETRIES`]).
pub const RETRIES: usize = crate::list::RETRIES;
/// Polling step of [`Job::wait_empty`] when no completion port is attached.
pub const POLL_STEP: Duration = Duration::from_millis(20);

impl SysError {
    /// Classify a Win32 error from `GetLastError`. `0` means none was set.
    /// Call sites that know a code means "gone" (e.g. `OpenProcess` →
    /// `ERROR_INVALID_PARAMETER`) map it themselves before calling this.
    pub fn from_win32(code: u32) -> Self {
        match code {
            0 => SysError::Unknown,
            ERROR_ACCESS_DENIED => SysError::PermissionDenied,
            c => SysError::Os(i32::try_from(c).unwrap_or(i32::MAX)),
        }
    }
}

/// Run one raw call with the thread's last-error cleared first; returns the
/// call's result and the last-error right after it.
fn with_last_error<T>(call: impl FnOnce() -> T) -> (T, u32) {
    // SAFETY: `SetLastError` only writes this thread's last-error value.
    unsafe { SetLastError(0) };
    let r = call();
    // SAFETY: `GetLastError` only reads this thread's last-error value.
    let e = unsafe { GetLastError() };
    (r, e)
}

/// A `BOOL` call: non-zero is success, zero is classified from last-error.
fn check_bool(call: impl FnOnce() -> i32) -> Result<(), SysError> {
    let (ok, e) = with_last_error(call);
    if ok != 0 {
        Ok(())
    } else {
        Err(SysError::from_win32(e))
    }
}

/// A handle-returning call: null / `INVALID_HANDLE_VALUE` is a failure.
fn check_handle(call: impl FnOnce() -> HANDLE) -> Result<OwnedHandle, SysError> {
    let (h, e) = with_last_error(call);
    if h.is_null() || h == INVALID_HANDLE_VALUE {
        return Err(SysError::from_win32(e));
    }
    // SAFETY: `h` is a valid handle just returned to us, owned by nobody else;
    // `OwnedHandle` closes it exactly once.
    Ok(unsafe { OwnedHandle::from_raw_handle(h as RawHandle) })
}

fn raw(h: &OwnedHandle) -> HANDLE {
    h.as_raw_handle() as HANDLE
}

fn size_u32<T>() -> Result<u32, SysError> {
    u32::try_from(size_of::<T>()).map_err(|_| SysError::Unknown)
}

/// FILETIME (100 ns units since 1601) as one number.
fn filetime(ft: FILETIME) -> u64 {
    (u64::from(ft.dwHighDateTime) << 32) | u64::from(ft.dwLowDateTime)
}

/// One process in the Job: pid plus creation time, so a reused pid is a
/// different member (matches the macOS `(pid, start_time)` identity).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Member {
    pub pid: u32,
    /// `GetProcessTimes` creation time, FILETIME 100 ns units.
    pub created_100ns: u64,
}

/// How [`Job::wait_empty`] waits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitMode {
    /// `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` on an I/O completion port; no
    /// work while nothing changes.
    CompletionPort,
    /// The port could not be attached; [`Job::active_processes`] is polled
    /// every [`POLL_STEP`]. Reports should say so.
    Poll,
}

/// An open process handle (synchronize + limited query). Used for
/// identity checks and for waiting on a process the Job no longer lists.
#[derive(Debug)]
pub struct Process {
    h: OwnedHandle,
    pid: u32,
}

impl Process {
    /// Open `pid` for identity checks and waiting. A pid with no process is
    /// [`SysError::Gone`].
    pub fn open(pid: u32) -> Result<Process, SysError> {
        Self::open_access(pid, PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)
    }

    /// [`Process::open`] plus the right to [`Process::terminate`] it. For
    /// start-up reconciliation (ADR 0008): open, check
    /// [`Process::created_100ns`] against the recorded member, then
    /// terminate through the same handle, so a reused pid is never hit.
    pub fn open_terminable(pid: u32) -> Result<Process, SysError> {
        Self::open_access(
            pid,
            PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
        )
    }

    fn open_access(pid: u32, access: u32) -> Result<Process, SysError> {
        if pid == 0 {
            return Err(SysError::BadPid);
        }
        let (h, e) = with_last_error(|| {
            // SAFETY: plain call with a pid and access mask; the returned
            // handle (if any) is taken over below.
            unsafe { OpenProcess(access, 0, pid) }
        });
        if h.is_null() {
            return Err(match e {
                ERROR_INVALID_PARAMETER => SysError::Gone,
                e => SysError::from_win32(e),
            });
        }
        // SAFETY: `h` is a valid process handle just returned to us and owned
        // by nobody else; `OwnedHandle` closes it once.
        let h = unsafe { OwnedHandle::from_raw_handle(h as RawHandle) };
        Ok(Process { h, pid })
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    /// Creation time (FILETIME 100 ns). Stable for the life of the process;
    /// still readable after it exits while this handle is open.
    pub fn created_100ns(&self) -> Result<u64, SysError> {
        let zero = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let (mut c, mut x, mut k, mut u) = (zero, zero, zero, zero);
        let h = raw(&self.h);
        check_bool(|| {
            // SAFETY: `h` is a live process handle with query access; the four
            // out-pointers are distinct, aligned local FILETIMEs.
            unsafe { GetProcessTimes(h, &mut c, &mut x, &mut k, &mut u) }
        })?;
        Ok(filetime(c))
    }

    /// Wait until the process has exited. `Ok(false)` = still running after
    /// `timeout`.
    pub fn wait(&self, timeout: Duration) -> Result<bool, SysError> {
        let ms = u32::try_from(timeout.as_millis()).unwrap_or(u32::MAX - 1);
        let h = raw(&self.h);
        let (r, e) = with_last_error(|| {
            // SAFETY: `h` is a live process handle with SYNCHRONIZE access.
            unsafe { WaitForSingleObject(h, ms) }
        });
        match r {
            WAIT_OBJECT_0 => Ok(true),
            WAIT_TIMEOUT => Ok(false),
            _ => Err(SysError::from_win32(e)),
        }
    }

    /// `TerminateProcess` through this handle (needs
    /// [`Process::open_terminable`]; otherwise `PermissionDenied`).
    pub fn terminate(&self, exit_code: u32) -> Result<(), SysError> {
        let h = raw(&self.h);
        check_bool(|| {
            // SAFETY: `h` is a live process handle owned by `self`.
            unsafe { TerminateProcess(h, exit_code) }
        })
    }

    fn in_job(&self, job: HANDLE) -> Result<bool, SysError> {
        let mut yes = 0i32;
        let h = raw(&self.h);
        check_bool(|| {
            // SAFETY: both handles are live; `yes` is a local BOOL.
            unsafe { IsProcessInJob(h, job, &mut yes) }
        })?;
        Ok(yes != 0)
    }
}

/// A Job Object that owns one process tree. Drop = close = the tree is
/// killed (`KILL_ON_JOB_CLOSE`).
#[derive(Debug)]
pub struct Job {
    job: OwnedHandle,
    port: Option<OwnedHandle>,
}

impl Job {
    /// `CreateJobObjectW` (unnamed, handle not inheritable) with
    /// `KILL_ON_JOB_CLOSE` and no breakaway; then attach a completion port
    /// for [`Job::wait_empty`]. A port that cannot be attached is not an
    /// error: the Job falls back to [`WaitMode::Poll`].
    pub fn new() -> Result<Job, SysError> {
        let job = check_handle(|| {
            // SAFETY: null security attributes and null name are allowed
            // (default security, unnamed Job).
            unsafe { CreateJobObjectW(null(), null()) }
        })?;
        // SAFETY: the structure is plain integers; all zeros is valid.
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        info.BasicLimitInformation.LimitFlags = LIMIT_FLAGS;
        let size = size_u32::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()?;
        let jh = raw(&job);
        check_bool(|| {
            // SAFETY: `jh` is our live Job; `info` is a valid structure of
            // exactly `size` bytes that outlives the call.
            unsafe {
                SetInformationJobObject(
                    jh,
                    JobObjectExtendedLimitInformation,
                    (&info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                    size,
                )
            }
        })?;
        let port = Self::attach_port(jh).ok();
        Ok(Job { job, port })
    }

    fn attach_port(jh: HANDLE) -> Result<OwnedHandle, SysError> {
        let port = check_handle(|| {
            // SAFETY: INVALID_HANDLE_VALUE + null existing port = create a new
            // port not associated with any file.
            unsafe { CreateIoCompletionPort(INVALID_HANDLE_VALUE, null_mut(), 0, 1) }
        })?;
        let assoc = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
            CompletionKey: jh.cast(),
            CompletionPort: raw(&port),
        };
        let size = size_u32::<JOBOBJECT_ASSOCIATE_COMPLETION_PORT>()?;
        check_bool(|| {
            // SAFETY: `jh` is our live Job; `assoc` is a valid structure of
            // `size` bytes that outlives the call.
            unsafe {
                SetInformationJobObject(
                    jh,
                    JobObjectAssociateCompletionPortInformation,
                    (&assoc as *const JOBOBJECT_ASSOCIATE_COMPLETION_PORT).cast(),
                    size,
                )
            }
        })?;
        Ok(port)
    }

    pub fn wait_mode(&self) -> WaitMode {
        if self.port.is_some() {
            WaitMode::CompletionPort
        } else {
            WaitMode::Poll
        }
    }

    /// The limit flags the kernel reports for this Job (for tests and
    /// reports: must contain [`LIMIT_FLAGS`] and none of [`BREAKAWAY_FLAGS`]).
    pub fn limit_flags(&self) -> Result<u32, SysError> {
        let info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION =
            self.query_struct(JobObjectExtendedLimitInformation)?;
        Ok(info.BasicLimitInformation.LimitFlags)
    }

    /// Start `cmd` inside the Job: suspended → assigned → resumed. On any
    /// failure after the process exists, it is terminated and reaped before
    /// `Err` is returned. stdio is whatever `cmd` says (std pipes work).
    pub fn spawn(&self, cmd: &mut Command) -> Result<Child, SysError> {
        self.spawn_full(cmd, || {}, |_| Ok(()))
    }

    /// [`Job::spawn`] with `record` called after the assign and **before**
    /// the child runs, with its identity. ADR 0008: the session member is
    /// written to disk here; if `record` fails, the child is terminated and
    /// never ran, so no unrecorded process exists.
    pub fn spawn_recorded(
        &self,
        cmd: &mut Command,
        record: impl FnOnce(Member) -> Result<(), SysError>,
    ) -> Result<Child, SysError> {
        self.spawn_full(cmd, || {}, record)
    }

    /// [`Job::spawn`] with `before_assign` run between process creation and
    /// the assign. Tests use it as a barrier: with `CREATE_SUSPENDED` the
    /// child cannot run during it; without, it would (that is the knife).
    pub fn spawn_with(
        &self,
        cmd: &mut Command,
        before_assign: impl FnOnce(),
    ) -> Result<Child, SysError> {
        self.spawn_full(cmd, before_assign, |_| Ok(()))
    }

    fn spawn_full(
        &self,
        cmd: &mut Command,
        before_assign: impl FnOnce(),
        record: impl FnOnce(Member) -> Result<(), SysError>,
    ) -> Result<Child, SysError> {
        let jh = raw(&self.job);
        spawn_inner(
            cmd,
            before_assign,
            |ph| {
                check_bool(|| {
                    // SAFETY: `jh` is our live Job, `ph` the live handle of the
                    // child std just created.
                    unsafe { AssignProcessToJobObject(jh, ph) }
                })
            },
            record,
            resume_only_thread,
        )
    }

    /// `JobObjectBasicAccountingInformation.ActiveProcesses`.
    pub fn active_processes(&self) -> Result<u32, SysError> {
        let info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION =
            self.query_struct(JobObjectBasicAccountingInformation)?;
        Ok(info.ActiveProcesses)
    }

    /// Every process in the Job with its creation time.
    ///
    /// The pid list grows and retries like [`crate::list::read_pid_list`];
    /// still short after [`RETRIES`] ⇒ [`SysError::Incomplete`], never a
    /// truncated list. A listed pid that exited before it could be opened is
    /// left out (it is no longer a member). A pid that was reused by a
    /// process outside the Job is left out too (`IsProcessInJob`). Any other
    /// failure to read a member's time is an `Err`; nothing is guessed.
    pub fn members(&self) -> Result<Vec<Member>, SysError> {
        let mut out = Vec::new();
        for pid in self.pids()? {
            let p = match Process::open(pid) {
                Ok(p) => p,
                Err(SysError::Gone) => continue,
                Err(e) => return Err(e),
            };
            if !p.in_job(raw(&self.job))? {
                continue;
            }
            out.push(Member {
                pid,
                created_100ns: p.created_100ns()?,
            });
        }
        out.sort();
        Ok(out)
    }

    fn pids(&self) -> Result<Vec<u32>, SysError> {
        let header = 2 * size_of::<u32>();
        let mut cap = usize::try_from(self.active_processes()?).unwrap_or(0);
        cap = (cap + cap / 4).max(16);
        for _ in 0..=RETRIES {
            let bytes = header + cap * size_of::<usize>();
            let words = bytes.div_ceil(size_of::<usize>());
            let mut buf = vec![0usize; words];
            let len = u32::try_from(words * size_of::<usize>()).map_err(|_| SysError::Unknown)?;
            let jh = raw(&self.job);
            let p = buf.as_mut_ptr();
            let r = check_bool(|| {
                // SAFETY: `p` points to `len` writable, usize-aligned bytes
                // owned by `buf`, which outlives the call.
                unsafe {
                    QueryInformationJobObject(
                        jh,
                        JobObjectBasicProcessIdList,
                        p.cast(),
                        len,
                        null_mut(),
                    )
                }
            });
            match r {
                Ok(()) => {}
                Err(SysError::Os(c)) if u32::try_from(c) == Ok(ERROR_MORE_DATA) => {
                    cap = cap.saturating_mul(2);
                    continue;
                }
                Err(e) => return Err(e),
            }
            // the two u32 counters of JOBOBJECT_BASIC_PROCESS_ID_LIST, read
            // from the words' native bytes (no pointer cast needed)
            let first = header.div_ceil(size_of::<usize>());
            let head: Vec<u8> = buf[..first].iter().flat_map(|w| w.to_ne_bytes()).collect();
            let word = |i: usize| {
                head.get(i..i + 4)
                    .and_then(|b| <[u8; 4]>::try_from(b).ok())
                    .map(u32::from_ne_bytes)
            };
            let (Some(assigned), Some(listed)) = (word(0), word(4)) else {
                return Err(SysError::Unknown);
            };
            let listed = usize::try_from(listed).map_err(|_| SysError::Unknown)?;
            let assigned = usize::try_from(assigned).map_err(|_| SysError::Unknown)?;
            if listed > cap {
                return Err(SysError::LengthMismatch {
                    got: listed,
                    want: cap,
                });
            }
            if listed < assigned {
                cap = assigned.max(cap.saturating_mul(2));
                continue;
            }
            return Ok(buf[first..first + listed]
                .iter()
                .filter_map(|&v| u32::try_from(v).ok())
                .filter(|&v| v != 0)
                .collect());
        }
        Err(SysError::Incomplete)
    }

    /// Graceful stop: `CTRL_BREAK_EVENT` to the process group of `root_pid`
    /// (the child [`Job::spawn`] started; it leads its own group).
    ///
    /// Refuses (`BadPid`) unless `root_pid` is a current member of this Job,
    /// so it can never reach a group outside the Job; `0` would mean "every
    /// process on our console" and is refused too. When the event cannot be
    /// delivered (no shared console, ConPTY) the result is
    /// [`SysError::Unsupported`] and the caller should go straight to
    /// [`Job::terminate`]. Delivery when it returns `Ok` is UNVERIFIED.
    pub fn ctrl_break(&self, root_pid: u32) -> Result<(), SysError> {
        if root_pid == 0 || !self.members()?.iter().any(|m| m.pid == root_pid) {
            return Err(SysError::BadPid);
        }
        match check_bool(|| {
            // SAFETY: plain call with an event id and a group id that is a
            // member of our own Job (checked above).
            unsafe { GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, root_pid) }
        }) {
            Err(SysError::Os(c))
                if u32::try_from(c) == Ok(ERROR_INVALID_HANDLE)
                    || u32::try_from(c) == Ok(ERROR_INVALID_PARAMETER) =>
            {
                Err(SysError::Unsupported)
            }
            r => r,
        }
    }

    /// `TerminateJobObject`: every process in the Job, asynchronously. Use
    /// [`Job::wait_empty`] to know when they are gone.
    pub fn terminate(&self, exit_code: u32) -> Result<(), SysError> {
        let jh = raw(&self.job);
        check_bool(|| {
            // SAFETY: `jh` is our live Job handle.
            unsafe { TerminateJobObject(jh, exit_code) }
        })
    }

    /// Wait until the Job has no active process. `Ok(false)` = still some
    /// after `timeout`. The answer is always re-checked with
    /// [`Job::active_processes`]: a queued "zero" from before a later spawn
    /// does not count.
    pub fn wait_empty(&self, timeout: Duration) -> Result<bool, SysError> {
        let deadline = Instant::now() + timeout;
        loop {
            if self.active_processes()? == 0 {
                return Ok(true);
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Ok(false);
            }
            match &self.port {
                None => std::thread::sleep(left.min(POLL_STEP)),
                Some(port) => {
                    // an ACTIVE_PROCESS_ZERO or a timeout both end in the
                    // re-check at the top of the loop
                    self.next_message(port, left)?;
                }
            }
        }
    }

    /// Block for one completion-port message or until `left` passes. What
    /// the message was does not matter: the caller re-checks the count.
    fn next_message(&self, port: &OwnedHandle, left: Duration) -> Result<(), SysError> {
        let ms = u32::try_from(left.as_millis())
            .unwrap_or(u32::MAX - 1)
            .max(1);
        let (mut msg, mut key, mut ov) = (0u32, 0usize, null_mut::<OVERLAPPED>());
        let ph = raw(port);
        let (ok, e) = with_last_error(|| {
            // SAFETY: `ph` is our live port; the three out-pointers are
            // distinct locals.
            unsafe { GetQueuedCompletionStatus(ph, &mut msg, &mut key, &mut ov, ms) }
        });
        if ok == 0 && !(ov.is_null() && e == WAIT_TIMEOUT) {
            return Err(SysError::from_win32(e));
        }
        Ok(())
    }

    fn query_struct<T: Copy>(&self, class: JOBOBJECTINFOCLASS) -> Result<T, SysError> {
        let mut out = MaybeUninit::<T>::zeroed();
        let size = size_u32::<T>()?;
        let jh = raw(&self.job);
        let p = out.as_mut_ptr();
        check_bool(|| {
            // SAFETY: `p` points to `size` writable bytes owned by `out`,
            // which outlives the call.
            unsafe { QueryInformationJobObject(jh, class, p.cast(), size, null_mut()) }
        })?;
        // SAFETY: `out` was zeroed; `T` is a plain C structure (integers
        // only) for which every bit pattern is valid, so it is initialised.
        Ok(unsafe { out.assume_init() })
    }
}

/// The spawn steps with the two OS calls passed in, so the failure paths
/// (assign fails, resume fails) are tested with real processes but injected
/// errors. Any failure after creation terminates and reaps the child.
fn spawn_inner(
    cmd: &mut Command,
    before_assign: impl FnOnce(),
    assign: impl FnOnce(HANDLE) -> Result<(), SysError>,
    record: impl FnOnce(Member) -> Result<(), SysError>,
    resume: impl FnOnce(u32) -> Result<(), SysError>,
) -> Result<Child, SysError> {
    cmd.creation_flags(SPAWN_FLAGS);
    let mut child = cmd.spawn().map_err(|e| {
        e.raw_os_error()
            .map_or(SysError::Unknown, |c| SysError::from_win32(c as u32))
    })?;
    before_assign();
    let pid = child.id();
    let step = assign(child.as_raw_handle() as HANDLE)
        .and_then(|()| record_member(pid, record))
        .and_then(|()| resume(pid));
    match step {
        Ok(()) => Ok(child),
        Err(e) => {
            // never leave a suspended or unmanaged child behind
            let _ = child.kill();
            let _ = child.wait();
            Err(e)
        }
    }
}

/// Read the identity of the still-suspended child and hand it to `record`.
fn record_member(
    pid: u32,
    record: impl FnOnce(Member) -> Result<(), SysError>,
) -> Result<(), SysError> {
    let created_100ns = Process::open(pid)?.created_100ns()?;
    record(Member { pid, created_100ns })
}

/// The one thread a just-created suspended process has.
fn resume_only_thread(pid: u32) -> Result<(), SysError> {
    let tid = only_thread(&threads_of(pid)?)?;
    let th = check_handle(|| {
        // SAFETY: plain call with an access mask and a thread id.
        unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, tid) }
    })?;
    let h = raw(&th);
    let (prev, e) = with_last_error(|| {
        // SAFETY: `h` is a live thread handle with SUSPEND_RESUME access.
        unsafe { ResumeThread(h) }
    });
    if prev == u32::MAX {
        return Err(SysError::from_win32(e));
    }
    Ok(())
}

/// Exactly one thread, or `Err`: zero or several mean this is not the
/// suspended process we just created, and nothing is resumed.
pub(crate) fn only_thread(tids: &[u32]) -> Result<u32, SysError> {
    match tids {
        [t] => Ok(*t),
        [] => Err(SysError::Gone),
        _ => Err(SysError::LengthMismatch {
            got: tids.len(),
            want: 1,
        }),
    }
}

fn threads_of(pid: u32) -> Result<Vec<u32>, SysError> {
    let snap = check_handle(|| {
        // SAFETY: plain call; TH32CS_SNAPTHREAD ignores the pid argument.
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) }
    })?;
    let sh = raw(&snap);
    // SAFETY: THREADENTRY32 is plain integers; all zeros is valid.
    let mut te: THREADENTRY32 = unsafe { std::mem::zeroed() };
    te.dwSize = size_u32::<THREADENTRY32>()?;
    let mut out = Vec::new();
    let (mut ok, mut e) = with_last_error(|| {
        // SAFETY: `sh` is our live snapshot; `te` has dwSize set.
        unsafe { Thread32First(sh, &mut te) }
    });
    while ok != 0 {
        if te.th32OwnerProcessID == pid {
            out.push(te.th32ThreadID);
        }
        (ok, e) = with_last_error(|| {
            // SAFETY: as above.
            unsafe { Thread32Next(sh, &mut te) }
        });
    }
    // ERROR_NO_MORE_FILES (18) ends the walk; anything else is a failure
    if e != 18 {
        return Err(SysError::from_win32(e));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Stdio;

    fn sleeper() -> Command {
        let mut c = Command::new("cmd");
        c.args(["/C", "ping -n 30 127.0.0.1 >NUL"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        c
    }

    #[test]
    fn win32_errors_are_classified() {
        assert_eq!(SysError::from_win32(0), SysError::Unknown);
        assert_eq!(SysError::from_win32(5), SysError::PermissionDenied);
        assert_eq!(SysError::from_win32(87), SysError::Os(87));
    }

    #[test]
    fn exactly_one_thread_or_nothing_is_resumed() {
        assert_eq!(only_thread(&[7]), Ok(7));
        assert_eq!(only_thread(&[]), Err(SysError::Gone));
        assert!(matches!(
            only_thread(&[1, 2]),
            Err(SysError::LengthMismatch { .. })
        ));
    }

    /// Clearing last-error first: a failure that sets nothing is Unknown,
    /// not a stale code from an earlier call.
    #[test]
    fn a_failure_without_last_error_is_unknown() {
        // SAFETY: only writes this thread's last-error.
        unsafe { SetLastError(ERROR_ACCESS_DENIED) };
        assert_eq!(check_bool(|| 0), Err(SysError::Unknown));
        assert_eq!(check_handle(null_mut).map(|_| ()), Err(SysError::Unknown));
    }

    #[test]
    fn resume_failure_terminates_the_child() {
        let mut seen: Option<Process> = None;
        let r = spawn_inner(
            &mut sleeper(),
            || {},
            |_| Ok(()),
            |_| Ok(()),
            |p| {
                seen = Some(Process::open_terminable(p).expect("child exists while suspended"));
                Err(SysError::Unknown)
            },
        );
        assert_eq!(r.err(), Some(SysError::Unknown));
        let p = seen.expect("resume was called");
        let gone = p.wait(Duration::from_secs(5)).unwrap();
        if !gone {
            // knife case: clean up our own leftover so the run cannot hang
            let _ = p.terminate(1);
        }
        assert!(gone, "child still alive after a failed resume");
    }

    #[test]
    fn a_failed_assign_leaves_no_process() {
        let mut held: Option<Process> = None;
        let mut resumed = false;
        let r = spawn_inner(
            &mut sleeper(),
            || {},
            |ph| {
                // open our own handle to the child before failing
                // SAFETY: `ph` is the live child handle std created.
                let pid = unsafe { windows_sys::Win32::System::Threading::GetProcessId(ph) };
                held = Some(Process::open_terminable(pid).expect("child exists"));
                Err(SysError::PermissionDenied)
            },
            |_| Ok(()),
            |_| {
                resumed = true;
                Ok(())
            },
        );
        assert_eq!(r.err(), Some(SysError::PermissionDenied));
        assert!(!resumed, "resume ran after a failed assign");
        let p = held.expect("assign was called");
        let gone = p.wait(Duration::from_secs(5)).unwrap();
        if !gone {
            let _ = p.terminate(1);
        }
        assert!(gone, "child survived a failed assign");
    }

    #[test]
    fn a_failed_record_leaves_no_process_and_never_resumes() {
        let mut held: Option<Process> = None;
        let mut resumed = false;
        let r = spawn_inner(
            &mut sleeper(),
            || {},
            |_| Ok(()),
            |m| {
                held = Some(Process::open_terminable(m.pid).expect("child exists"));
                Err(SysError::Os(112)) // e.g. disk full while writing the member
            },
            |_| {
                resumed = true;
                Ok(())
            },
        );
        assert_eq!(r.err(), Some(SysError::Os(112)));
        assert!(
            !resumed,
            "resume ran after the member could not be recorded"
        );
        let p = held.expect("record was called");
        let gone = p.wait(Duration::from_secs(5)).unwrap();
        if !gone {
            let _ = p.terminate(1);
        }
        assert!(gone, "child survived a failed record");
    }
}
