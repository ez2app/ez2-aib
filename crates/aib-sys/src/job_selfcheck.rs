//! Start-up self-check for the Windows Job Object (a63ab plan B2, 2026-11-08).
//!
//! Runs the real kernel path once: a Job with `KILL_ON_JOB_CLOSE` and no
//! breakaway, a child that starts a grandchild (`cmd /c ping`), both seen in
//! the Job, `TerminateJobObject`, the Job empty, and each member's own
//! handle (opened **before** the terminate, so a reused pid is never asked)
//! reports exited. Any step failing is a [`Failure`] with a stable tag.
//!
//! What this does **not** check (the report must say so): the owner
//! crashing (needs a second process that dies; the tests cover it), J0
//! (`Job::adopt_current` would put the caller in a Job for good), real AI
//! CLIs, ConPTY.

use crate::job::{Job, Process, WaitMode, BREAKAWAY_FLAGS, LIMIT_FLAGS};
use crate::SysError;
use std::os::windows::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// `CREATE_NO_WINDOW`: the probe never opens a console window.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Why the Job self-check failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Failure {
    /// `Job::new` failed.
    Create(SysError),
    /// The kernel's limit flags are not exactly what the Job promises.
    Limits(u32),
    /// Reading the limit flags failed.
    LimitsRead(SysError),
    /// The probe child could not be started in the Job.
    Spawn(SysError),
    /// The grandchild never showed up in the Job within the time limit.
    TreeNotSeen(u32),
    /// Listing the Job's members failed.
    Members(SysError),
    /// A member could not be opened before the terminate.
    OpenMember(SysError),
    /// `TerminateJobObject` failed.
    Terminate(SysError),
    /// The Job still had processes after the time limit.
    NotEmpty,
    /// Waiting for the Job to empty failed.
    Wait(SysError),
    /// A member's own handle says it is still running.
    Survivor(u32),
}

impl Failure {
    /// Stable tag: `windows_job=failed:<tag>`.
    pub fn tag(&self) -> String {
        match self {
            Failure::Create(e) => format!("create:{}", e.tag()),
            Failure::Limits(f) => format!("limits:{f:#x}"),
            Failure::LimitsRead(e) => format!("limits_read:{}", e.tag()),
            Failure::Spawn(e) => format!("spawn:{}", e.tag()),
            Failure::TreeNotSeen(n) => format!("tree_not_seen:{n}"),
            Failure::Members(e) => format!("members:{}", e.tag()),
            Failure::OpenMember(e) => format!("open_member:{}", e.tag()),
            Failure::Terminate(e) => format!("terminate:{}", e.tag()),
            Failure::NotEmpty => "not_empty".into(),
            Failure::Wait(e) => format!("wait:{}", e.tag()),
            Failure::Survivor(pid) => format!("survivor:{pid}"),
        }
    }
}

/// What passed, for the report.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Passed {
    /// How [`Job::wait_empty`] waited (a port, or the documented poll).
    pub wait_mode: WaitMode,
    /// Processes seen in the Job before the terminate (child + grandchild).
    pub members: usize,
}

/// Run the check. `limit` bounds each wait (tree showing up, Job emptying).
pub fn run(limit: Duration) -> Result<Passed, Failure> {
    let job = Job::new().map_err(Failure::Create)?;
    let flags = job.limit_flags().map_err(Failure::LimitsRead)?;
    if flags & LIMIT_FLAGS != LIMIT_FLAGS || flags & BREAKAWAY_FLAGS != 0 {
        return Err(Failure::Limits(flags));
    }
    let comspec = std::env::var_os("ComSpec").unwrap_or_else(|| "cmd.exe".into());
    let mut cmd = Command::new(comspec);
    cmd.args(["/d", "/c", "ping -n 60 127.0.0.1 >nul"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);
    let mut child = job.spawn(&mut cmd).map_err(Failure::Spawn)?;
    let out = probe(&job, limit);
    // the Job is dropped at the end too (KILL_ON_JOB_CLOSE); reap our child
    // either way so no handle is left behind
    let _ = job.terminate(1);
    let _ = child.wait();
    out
}

/// Everything after the spawn; the caller terminates and reaps whatever happens.
fn probe(job: &Job, limit: Duration) -> Result<Passed, Failure> {
    let deadline = Instant::now() + limit;
    let members = loop {
        let m = job.members().map_err(Failure::Members)?;
        if m.len() >= 2 {
            break m;
        }
        if Instant::now() >= deadline {
            return Err(Failure::TreeNotSeen(
                u32::try_from(m.len()).unwrap_or(u32::MAX),
            ));
        }
        // start-up only, bounded by `limit`: no aibd timer exists yet (ADR 0005)
        #[allow(clippy::disallowed_methods)]
        std::thread::sleep(Duration::from_millis(20));
    };
    // open every member before the terminate: our handle pins the pid, so the
    // "exited" answer below is about this process, not a reused pid
    let mut handles = Vec::with_capacity(members.len());
    for m in &members {
        match Process::open(m.pid) {
            // same pid AND same creation time: it is the member, not a reuse
            Ok(p) if p.created_100ns().map_err(Failure::OpenMember)? == m.created_100ns => {
                handles.push(p)
            }
            Ok(_) => {} // the member exited and its pid was reused: nothing of ours
            Err(SysError::Gone) => {} // already exited on its own: nothing to check
            Err(e) => return Err(Failure::OpenMember(e)),
        }
    }
    job.terminate(1).map_err(Failure::Terminate)?;
    if !job.wait_empty(limit).map_err(Failure::Wait)? {
        return Err(Failure::NotEmpty);
    }
    for p in &handles {
        if !p.wait(Duration::ZERO).map_err(Failure::Wait)? {
            return Err(Failure::Survivor(p.pid()));
        }
    }
    Ok(Passed {
        wait_mode: job.wait_mode(),
        members: members.len(),
    })
}
