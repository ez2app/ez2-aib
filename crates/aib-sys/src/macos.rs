//! macOS M1–M4 through libproc, M5 through `waitid`. Every `unsafe` block is one FFI call with
//! its own SAFETY note (`clippy::undocumented_unsafe_blocks` and
//! `multiple_unsafe_ops_per_block` are denied for this crate).

use crate::list::read_pid_list;
use crate::{
    check_struct_read, exit_state_from, BsdInfo, ChildExit, ExitState, ProcessInfo, SysError,
    UniqueIds,
};
use libc::{c_int, c_void};
use std::mem::{size_of, MaybeUninit};

/// Not in any public SDK header (checked in the 13.3, 14.4, 15.5 and 26.0
/// SDKs). Value and layout from XNU `bsd/sys/proc_info.h` (`#ifdef PRIVATE`).
const PROC_PIDUNIQIDENTIFIERINFO: c_int = 17;
/// `sys/proc_info.h`: `#define PROC_PPID_ONLY 6`.
const PROC_PPID_ONLY: u32 = 6;

/// XNU `struct proc_uniqidentifierinfo` (56 bytes). The size is checked
/// against what the kernel returns on every call.
#[repr(C)]
#[derive(Clone, Copy)]
struct ProcUniqIdentifierInfo {
    p_uuid: [u8; 16],
    p_uniqueid: u64,
    p_puniqueid: u64,
    p_idversion: i32,
    p_reserve2: u32,
    p_reserve3: u64,
    p_reserve4: u64,
}

const _: () = assert!(size_of::<ProcUniqIdentifierInfo>() == 56);

fn clear_errno() {
    // SAFETY: `__error()` has no preconditions; it returns this thread's
    // errno slot, which lives as long as the thread.
    let slot = unsafe { libc::__error() };
    // SAFETY: `slot` is this thread's errno, non-null and aligned; only this
    // thread writes it, and writing an int to it is what C does.
    unsafe { *slot = 0 };
}

fn errno() -> i32 {
    std::io::Error::last_os_error().raw_os_error().unwrap_or(0)
}

fn pid_arg(pid: u32) -> Result<c_int, SysError> {
    c_int::try_from(pid).map_err(|_| SysError::BadPid)
}

/// Read one fixed-size `proc_pidinfo` flavor into `T`.
///
/// `T` must be a plain C structure (`repr(C)`, integers and arrays only) for
/// which every bit pattern is valid; both callers in this file are.
fn pidinfo<T: Copy>(pid: u32, flavor: c_int) -> Result<T, SysError> {
    pidinfo_with(pid, |pid, buf, size| {
        // SAFETY: `buf` is a live, exclusively borrowed `size`-byte buffer
        // (see `pidinfo_with`); the kernel writes at most `size` bytes.
        unsafe { libc::proc_pidinfo(pid, flavor, 0, buf, size) }
    })
}

/// [`pidinfo`] with the raw call passed in, so the errno rules are tested
/// without the kernel. `raw` gets `(pid, buffer, size)` and returns what
/// `proc_pidinfo` returns.
///
/// errno is cleared right before the call: a failure that sets no errno is
/// `Unknown`, never a classification left over from an earlier call (a stale
/// ESRCH would read as `Gone`).
fn pidinfo_with<T: Copy>(
    pid: u32,
    raw: impl FnOnce(c_int, *mut c_void, c_int) -> c_int,
) -> Result<T, SysError> {
    let pid = pid_arg(pid)?;
    let want = size_of::<T>();
    let size = c_int::try_from(want).map_err(|_| SysError::Unknown)?;
    let mut out = MaybeUninit::<T>::zeroed();
    clear_errno();
    let n = raw(pid, out.as_mut_ptr().cast::<c_void>(), size);
    check_struct_read(n, want, errno())?;
    // SAFETY: `out` was zeroed and every bit pattern is valid for `T`, so it
    // is initialised whatever `raw` wrote; the length was checked above.
    Ok(unsafe { out.assume_init() })
}

fn comm(raw: &[libc::c_char]) -> String {
    let bytes: Vec<u8> = raw
        .iter()
        .take_while(|c| **c != 0)
        .map(|c| c.to_ne_bytes()[0])
        .collect();
    String::from_utf8_lossy(&bytes).into_owned()
}

/// The real implementation.
#[derive(Debug, Default, Clone, Copy)]
pub struct Macos;

impl ProcessInfo for Macos {
    fn unique_ids(&self, pid: u32) -> Result<UniqueIds, SysError> {
        let u: ProcUniqIdentifierInfo = pidinfo(pid, PROC_PIDUNIQIDENTIFIERINFO)?;
        Ok(UniqueIds {
            unique: u.p_uniqueid,
            parent_unique: u.p_puniqueid,
        })
    }

    fn bsd_info(&self, pid: u32) -> Result<BsdInfo, SysError> {
        let b: libc::proc_bsdinfo = pidinfo(pid, libc::PROC_PIDTBSDINFO)?;
        Ok(BsdInfo {
            pid: b.pbi_pid,
            ppid: b.pbi_ppid,
            pgid: b.pbi_pgid,
            uid: b.pbi_uid,
            start_sec: b.pbi_start_tvsec,
            start_usec: b.pbi_start_tvusec,
            comm: comm(&b.pbi_comm),
        })
    }

    fn children(&self, ppid: u32) -> Result<Vec<u32>, SysError> {
        list(Unit::Bytes, |buf, size| {
            // SAFETY: `buf` is null with size 0 (size query) or a live,
            // exclusively borrowed `size`-byte buffer.
            unsafe { libc::proc_listpids(PROC_PPID_ONLY, ppid, buf, size) }
        })
    }

    fn all_pids(&self) -> Result<Vec<u32>, SysError> {
        list(Unit::Pids, |buf, size| {
            // SAFETY: as in `children`.
            unsafe { libc::proc_listallpids(buf, size) }
        })
    }
}

/// `sizeof(struct kinfo_proc)` on 64-bit macOS (XNU `bsd/sys/sysctl.h`). The
/// libc crate does not define the structure for Apple targets; the size the
/// kernel returns is checked on every call.
const KINFO_PROC_SIZE: usize = 648;

impl Macos {
    /// G1 fallback (043dd THREAT-G1-setuid, 11-09): the start time in seconds
    /// through `sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID)`, i.e.
    /// `kp_proc.p_un.__p_starttime.tv_sec`, the first field of
    /// `struct kinfo_proc` (offset 0). Same source as M2's `pbi_start_tvsec`
    /// (`p_start`), but readable for a setuid-root child, where M2 is EPERM.
    /// A pid that does not exist comes back as 0 bytes ⇒ `Gone`.
    pub fn start_sec_sysctl(&self, pid: u32) -> Result<u64, SysError> {
        let mut mib = [
            libc::CTL_KERN,
            libc::KERN_PROC,
            libc::KERN_PROC_PID,
            pid_arg(pid)?,
        ];
        let mut buf = [0u8; KINFO_PROC_SIZE];
        let mut len: libc::size_t = KINFO_PROC_SIZE;
        clear_errno();
        // SAFETY: `mib` is 4 valid ints; `buf` is a live, exclusively borrowed
        // `len`-byte buffer and `len` points at its size; no new value is set.
        let rc = unsafe {
            libc::sysctl(
                mib.as_mut_ptr(),
                4,
                buf.as_mut_ptr().cast::<c_void>(),
                &mut len,
                std::ptr::null_mut(),
                0,
            )
        };
        if rc != 0 {
            return Err(SysError::from_errno(errno()));
        }
        if len == 0 {
            return Err(SysError::Gone);
        }
        if len != KINFO_PROC_SIZE {
            return Err(SysError::LengthMismatch {
                got: len,
                want: KINFO_PROC_SIZE,
            });
        }
        let mut sec = [0u8; 8];
        sec.copy_from_slice(&buf[..8]);
        u64::try_from(i64::from_ne_bytes(sec)).map_err(|_| SysError::Unknown)
    }

    /// Effective uid through `PROC_PIDT_SHORTBSDINFO` (`pbsi_uid`), readable
    /// for a setuid child where M2 is refused.
    pub fn euid(&self, pid: u32) -> Result<u32, SysError> {
        let s: libc::proc_bsdshortinfo = pidinfo(pid, libc::PROC_PIDT_SHORTBSDINFO)?;
        Ok(s.pbsi_uid)
    }

    /// G1 (043dd M1): the process's resource coalition id, through the
    /// private `PROC_PIDCOALITIONINFO` flavor (20, `<sys/proc_info.h>` in
    /// xnu). A process starts in its parent's coalition; launchd puts each
    /// service it starts in its own, so a launchd-started system service is
    /// told apart from a re-parented process of a session. Private: the
    /// caller checks it on a pair it knows share one before trusting it.
    pub fn coalition(&self, pid: u32) -> Result<u64, SysError> {
        let c: ProcPidCoalitionInfo = pidinfo(pid, PROC_PIDCOALITIONINFO)?;
        Ok(c.coalition_id[0])
    }
}

/// `PROC_PIDCOALITIONINFO` (private, xnu `bsd/sys/proc_info.h`).
const PROC_PIDCOALITIONINFO: c_int = 20;

/// `struct proc_pidcoalitioninfo`: `coalition_id[COALITION_NUM_TYPES]`
/// (resource, jetsam) and three reserved words, 40 bytes; the length check
/// in [`pidinfo`] refuses any other size.
#[repr(C)]
#[derive(Clone, Copy)]
struct ProcPidCoalitionInfo {
    coalition_id: [u64; 2],
    reserved: [u64; 3],
}

/// What a list call returns: `proc_listpids` counts bytes,
/// `proc_listallpids` counts pids (libproc.c).
#[derive(Clone, Copy)]
enum Unit {
    Bytes,
    Pids,
}

impl Unit {
    fn to_bytes(self, n: usize) -> usize {
        match self {
            Unit::Bytes => n,
            Unit::Pids => n.saturating_mul(size_of::<i32>()),
        }
    }
}

/// Size query (an estimate), then [`read_pid_list`], which grows the buffer
/// and refuses to return a list that may be truncated. errno is cleared
/// before **each** call (the size query and every fill), so a classification
/// is always this call's own. A failed size query is an error even if a fill
/// would work: the estimate is what the buffer is sized from.
fn list(unit: Unit, raw: impl Fn(*mut c_void, c_int) -> c_int) -> Result<Vec<u32>, SysError> {
    clear_errno();
    let need = raw(std::ptr::null_mut(), 0);
    if need < 0 || (need == 0 && errno() != 0) {
        return Err(SysError::from_errno(errno()));
    }
    let hint = unit.to_bytes(usize::try_from(need).unwrap_or(0)) / size_of::<i32>();
    read_pid_list(hint, |buf| {
        let size = c_int::try_from(std::mem::size_of_val(buf)).map_err(|_| 0)?;
        clear_errno();
        let n = raw(buf.as_mut_ptr().cast::<c_void>(), size);
        // Errors: a negative return, with this call's errno. A success never
        // touches errno (measured: a 0 keeps whatever errno was before), so a
        // 0 that comes with an errno this very call set is not trusted as
        // "no pids" (fail closed).
        if n < 0 || (n == 0 && errno() != 0) {
            return Err(errno());
        }
        usize::try_from(n).map(|n| unit.to_bytes(n)).map_err(|_| 0)
    })
}

/// M5: one `waitid(P_PID, pid, WEXITED | WNOWAIT | extra)`. Never reaps.
fn waitid_nowait(pid: u32, extra: c_int) -> Result<ExitState, SysError> {
    let id = libc::id_t::try_from(pid_arg(pid)?).map_err(|_| SysError::BadPid)?;
    let mut si = MaybeUninit::<libc::siginfo_t>::zeroed();
    clear_errno();
    // SAFETY: `si` is a live, writable, correctly sized and aligned
    // `siginfo_t`; `waitid` writes at most one `siginfo_t` into it.
    let rc = unsafe {
        libc::waitid(
            libc::P_PID,
            id,
            si.as_mut_ptr(),
            libc::WEXITED | libc::WNOWAIT | extra,
        )
    };
    if rc != 0 {
        let e = SysError::from_errno(errno());
        // say why when it is our own setting (043dd / a63ab #522 M1)
        if e == SysError::NotChild && matches!(sigchld(), Ok(s) if !s.keeps_zombies()) {
            return Err(SysError::ChildrenAutoReaped);
        }
        return Err(e);
    }
    // SAFETY: `si` started all-zero (a valid `siginfo_t`: integers, padding
    // and a null pointer) and the kernel only wrote a valid one over it.
    let si = unsafe { si.assume_init() };
    exit_state_from(pid, si.si_pid, si.si_code, si.si_status)
}

impl ChildExit for Macos {
    fn peek(&self, pid: u32) -> Result<ExitState, SysError> {
        waitid_nowait(pid, libc::WNOHANG)
    }

    fn wait_exit(&self, pid: u32) -> Result<ExitState, SysError> {
        loop {
            match waitid_nowait(pid, 0) {
                Err(SysError::Os(e)) if e == libc::EINTR => continue,
                Ok(ExitState::Running) => return Err(SysError::Unknown), // blocking waitid never says "running"
                r => return r,
            }
        }
    }
}

/// How this process treats `SIGCHLD`: M5's premise (ADR 0008 rule 3, 043dd
/// #522 M1). Ignored (`SIG_IGN`) or `SA_NOCLDWAIT` ⇒ the kernel reaps exited
/// children at once, so M5 only ever sees `ECHILD` and every group stop is
/// unverified. Both are inherited across `exec`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sigchld {
    /// `SIG_DFL`: exited children stay zombies until reaped.
    Default,
    /// A handler without `SA_NOCLDWAIT`: zombies stay too.
    Handled,
    /// `SIG_IGN`: auto-reaped.
    Ignored,
    /// `SA_NOCLDWAIT`: auto-reaped.
    NoChildWait,
}

impl Sigchld {
    /// `true` when exited children are kept for us to look at (M5 works).
    pub fn keeps_zombies(self) -> bool {
        matches!(self, Sigchld::Default | Sigchld::Handled)
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Sigchld::Default => "default",
            Sigchld::Handled => "handled",
            Sigchld::Ignored => "ignored",
            Sigchld::NoChildWait => "no_child_wait",
        }
    }
}

/// Read the current `SIGCHLD` disposition without changing it.
pub fn sigchld() -> Result<Sigchld, SysError> {
    let mut old = MaybeUninit::<libc::sigaction>::zeroed();
    // SAFETY: a null new action only reads; `old` is a live, writable
    // `sigaction` the kernel fills.
    let rc = unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), old.as_mut_ptr()) };
    if rc != 0 {
        return Err(SysError::from_errno(errno()));
    }
    // SAFETY: zero-initialised, then written by the kernel.
    let old = unsafe { old.assume_init() };
    Ok(if old.sa_sigaction == libc::SIG_IGN {
        Sigchld::Ignored
    } else if old.sa_flags & libc::SA_NOCLDWAIT != 0 {
        Sigchld::NoChildWait
    } else if old.sa_sigaction == libc::SIG_DFL {
        Sigchld::Default
    } else {
        Sigchld::Handled
    })
}

/// Set `SIGCHLD` back to `SIG_DFL` (no flags) when it does not keep zombies;
/// returns what it was. A handler that keeps zombies is left alone.
pub fn sigchld_keep_zombies() -> Result<Sigchld, SysError> {
    let was = sigchld()?;
    if was.keeps_zombies() {
        return Ok(was);
    }
    let sa = MaybeUninit::<libc::sigaction>::zeroed();
    // SAFETY: an all-zero `sigaction` is valid: SIG_DFL, empty mask, no flags.
    let sa = unsafe { sa.assume_init() };
    // SAFETY: `sa` is a valid `sigaction`; the old action is not needed.
    let rc = unsafe { libc::sigaction(libc::SIGCHLD, &sa, std::ptr::null_mut()) };
    if rc != 0 {
        return Err(SysError::from_errno(errno()));
    }
    Ok(was)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn me() -> u32 {
        std::process::id()
    }

    /// Measured on 15.7.9: the kernel writes the real size (56 / 136 bytes)
    /// into a larger buffer. That must be a mismatch, never a success.
    #[test]
    fn m1_m2_a_larger_struct_than_the_kernels_is_a_length_mismatch() {
        assert_eq!(
            pidinfo::<[u8; 64]>(me(), PROC_PIDUNIQIDENTIFIERINFO).map(|_| ()),
            Err(SysError::LengthMismatch { got: 56, want: 64 })
        );
        let real = size_of::<libc::proc_bsdinfo>();
        assert_eq!(
            pidinfo::<[u8; 144]>(me(), libc::PROC_PIDTBSDINFO).map(|_| ()),
            Err(SysError::LengthMismatch {
                got: real,
                want: 144
            })
        );
    }

    /// A smaller buffer is refused by the kernel (ENOMEM, measured).
    #[test]
    fn m1_m2_a_smaller_struct_is_refused() {
        assert_eq!(
            pidinfo::<[u8; 48]>(me(), PROC_PIDUNIQIDENTIFIERINFO).map(|_| ()),
            Err(SysError::Os(libc::ENOMEM))
        );
        assert_eq!(
            pidinfo::<[u8; 128]>(me(), libc::PROC_PIDTBSDINFO).map(|_| ()),
            Err(SysError::Os(libc::ENOMEM))
        );
    }

    /// pid 0 (kernel_task), measured as a user: M1 reads, M2 is EPERM.
    #[test]
    fn pid_0_m1_reads_and_m2_is_permission_denied_for_a_user() {
        assert!(Macos.unique_ids(0).is_ok());
        // SAFETY: getuid has no preconditions.
        if unsafe { libc::getuid() } != 0 {
            assert_eq!(Macos.bsd_info(0), Err(SysError::PermissionDenied));
        }
    }

    fn set_errno(e: i32) {
        // SAFETY: as in `clear_errno`.
        let slot = unsafe { libc::__error() };
        // SAFETY: as in `clear_errno`.
        unsafe { *slot = e };
    }

    /// M3 counts bytes, M4 counts pids (libproc.c): the glue converts both,
    /// so a list longer than the first guess is still read whole.
    #[test]
    fn m3_m4_list_glue_counts_bytes_and_pids_right() {
        let pids: Vec<i32> = (1..=200).collect();
        for unit in [Unit::Bytes, Unit::Pids] {
            let got = list(unit, |buf, size| {
                if buf.is_null() {
                    // size query: an estimate that is too small
                    return match unit {
                        Unit::Bytes => 10 * 4,
                        Unit::Pids => 10,
                    };
                }
                let cap = usize::try_from(size).unwrap() / 4;
                let n = pids.len().min(cap);
                // SAFETY: `buf` is the live `size`-byte buffer `list` passed.
                let out = unsafe { std::slice::from_raw_parts_mut(buf.cast::<i32>(), cap) };
                out[..n].copy_from_slice(&pids[..n]);
                let n = i32::try_from(n).unwrap();
                match unit {
                    Unit::Bytes => n * 4,
                    Unit::Pids => n,
                }
            });
            assert_eq!(got.map(|v| v.len()), Ok(200));
        }
    }

    #[test]
    fn m3_m4_a_failed_call_is_classified_by_errno() {
        let r = list(Unit::Pids, |_, _| {
            set_errno(libc::EPERM);
            -1
        });
        assert_eq!(r, Err(SysError::PermissionDenied));
        let r = list(Unit::Bytes, |buf, _| {
            if buf.is_null() {
                return 64;
            }
            set_errno(libc::ESRCH);
            -1
        });
        assert_eq!(r, Err(SysError::Gone));
    }
    /// errno hygiene (043dd K6 / K11): a failure that sets no errno must be
    /// `Unknown`, even when an earlier call left ESRCH behind. Without the
    /// clear it would read as `Gone`, the most dangerous mistake.
    #[test]
    fn a_failure_without_errno_is_unknown_not_a_stale_gone() {
        set_errno(libc::ESRCH);
        let r = pidinfo_with::<[u8; 8]>(me(), |_, _, _| -1);
        assert_eq!(r.map(|_| ()), Err(SysError::Unknown));
        set_errno(libc::ESRCH);
        let r = pidinfo_with::<[u8; 8]>(me(), |_, _, _| 0);
        assert_eq!(r.map(|_| ()), Err(SysError::Unknown));
        // the list: stale ESRCH before the size query, and again before a fill
        set_errno(libc::ESRCH);
        assert_eq!(list(Unit::Pids, |_, _| -1), Err(SysError::Unknown));
        let r = list(Unit::Bytes, |buf, _| {
            if buf.is_null() {
                return 64;
            }
            set_errno(libc::ESRCH); // left by "something else" before the fill
            -1
        });
        assert_eq!(r, Err(SysError::Gone), "this call's own ESRCH is Gone");
        let first = std::cell::Cell::new(true);
        let r = list(Unit::Bytes, |buf, _| {
            if buf.is_null() {
                set_errno(libc::ESRCH); // stale from the size query
                return 64;
            }
            if first.replace(false) {
                -1 // fill fails without setting errno
            } else {
                0
            }
        });
        assert_eq!(r, Err(SysError::Unknown));
    }

    /// A stale errno never turns an empty list into an error (the libproc
    /// crate bug seen in calibration), and an errno set by this very call
    /// with a 0 is not trusted as empty.
    #[test]
    fn an_empty_list_is_empty_and_a_zero_with_errno_is_refused() {
        set_errno(libc::ESRCH);
        let r = list(Unit::Bytes, |buf, _| {
            if buf.is_null() {
                return 64;
            }
            0
        });
        assert_eq!(r, Ok(Vec::new()));
        let r = list(Unit::Bytes, |buf, _| {
            if buf.is_null() {
                return 64;
            }
            set_errno(libc::EPERM);
            0
        });
        assert_eq!(r, Err(SysError::PermissionDenied));
    }

    /// G1: the sysctl fallback reads the same start time as M2 (pinned on
    /// ourselves and on a child), and a pid that does not exist is `Gone`.
    #[test]
    fn the_sysctl_start_time_equals_m2s_and_a_missing_pid_is_gone() {
        let me = me();
        assert_eq!(
            Macos.start_sec_sysctl(me),
            Macos.bsd_info(me).map(|b| b.start_sec)
        );
        let mut c = std::process::Command::new("/bin/sleep")
            .arg("5")
            .spawn()
            .unwrap();
        let got = (
            Macos.start_sec_sysctl(c.id()),
            Macos.bsd_info(c.id()).map(|b| b.start_sec),
        );
        let _ = c.kill();
        let _ = c.wait();
        assert_eq!(got.0, got.1);
        assert!(got.0.is_ok());
        assert_eq!(Macos.start_sec_sysctl(i32::MAX as u32), Err(SysError::Gone));
        // SAFETY: geteuid has no preconditions.
        let uid = unsafe { libc::geteuid() };
        assert_eq!(Macos.euid(me), Ok(uid));
    }

    /// G1: a child starts in our coalition, and the id is not zero; a pid
    /// that does not exist is `Gone`.
    #[test]
    fn a_child_shares_our_coalition() {
        let mine = Macos.coalition(me()).unwrap();
        assert_ne!(mine, 0);
        let mut c = std::process::Command::new("/bin/sleep")
            .arg("5")
            .spawn()
            .unwrap();
        let got = Macos.coalition(c.id());
        let _ = c.kill();
        let _ = c.wait();
        assert_eq!(got, Ok(mine));
        assert_eq!(Macos.coalition(i32::MAX as u32), Err(SysError::Gone));
    }

    /// 043dd K10: a failed size query is an error even if the fill works.
    #[test]
    fn a_failed_size_query_is_an_error() {
        let r = list(Unit::Pids, |buf, _| {
            if buf.is_null() {
                set_errno(libc::EPERM);
                return -1;
            }
            0
        });
        assert_eq!(r, Err(SysError::PermissionDenied));
    }
}
