//! aib-sys — the one crate allowed to call OS process APIs directly.
//!
//! Scope (API list v0.2, DECISION-4 A, 2026-11-07): macOS M1–M4; plus the
//! Windows Job Object in [`job`] (DECISION-8 item 2, DECISION-11, 2026-11-08).
//!
//! | # | call | grade |
//! |---|---|---|
//! | M1 | `proc_pidinfo(pid, 17 = PROC_PIDUNIQIDENTIFIERINFO)` | **private SPI** (not in any public SDK header) |
//! | M2 | `proc_pidinfo(pid, PROC_PIDTBSDINFO)` | declared, **no stability promise** (`libproc.h`: "private interfaces") |
//! | M3 | `proc_listpids(PROC_PPID_ONLY, ppid)` | declared, no stability promise |
//! | M4 | `proc_listallpids` | declared, no stability promise |
//! | M5 | `waitid(P_PID, pid, WEXITED \| WNOWAIT [\| WNOHANG])` | **public POSIX** (`sys/wait.h`); own children only |
//!
//! Rules that callers rely on:
//!
//! - **M1 only adds candidates.** A caller may use `parent_unique` to find a
//!   re-parented descendant; it must never decide an action from M1 alone.
//!   Before a signal, identity is checked with the public fields
//!   (pid, start time from M2), plus `unique` when it is available. If any of
//!   them cannot be read or does not match, no signal is sent, and no group
//!   signal that could reach an unverified process is used either.
//! - **M1 can be switched off.** [`selfcheck::run`] is meant to run once at
//!   start-up; if it fails, the caller disables M1 until restart, records the
//!   failure tag, and marks every cancel report `lineage_unavailable: true`.
//!   Without M1 the caller falls back to events plus the backstop scan, which
//!   can observe and mark but **cannot** promise a clean tree: it must report
//!   `tree_not_clean` / UNVERIFIED, never `clean` because a scan saw nothing.
//! - **Errors are classified, never guessed.** [`SysError::Gone`] is the only
//!   result that means "this process no longer exists"; permission errors and
//!   unknown failures are different variants. A list that does not fit after
//!   retries is [`SysError::Incomplete`], never a truncated list.
//! - **M5 looks, it never reaps.** [`ChildExit`] tells whether our own child
//!   has exited while leaving it a zombie, so its pid and process group
//!   number cannot be reused until the one real `wait()` after every signal
//!   (ADR 0008 rule 3, same rule as `proc_group` in Python, #509). The result
//!   counts only when the kernel's `si_pid` is the asked pid. `ECHILD` is
//!   [`SysError::NotChild`] (not ours, or already reaped elsewhere), never
//!   "gone". Measured on macOS 15.7.9: M1 and M2 return [`SysError::Gone`]
//!   for our own **unreaped zombie**, which still holds its pid ⇒ for our own
//!   child "Gone" from M1 / M2 does not mean the pid is free; ask M5.
//! - **PID reuse is narrowed, not removed.** Between an identity check and
//!   `kill()` there is still a short window; macOS has no pidfd.
//!
//! Tested platforms are listed in the API list (macOS 15 arm64); anything
//! else is UNVERIFIED and relies on the start-up self-check.
//!
//! Only `aibd` may depend on this crate (checked by `aib-xtask deps`).

pub mod fake;
#[cfg(windows)]
pub mod job;
pub mod list;
#[cfg(target_os = "macos")]
mod macos;
pub mod selfcheck;

#[cfg(target_os = "macos")]
pub use macos::{sigchld, sigchld_keep_zombies, Macos, Sigchld};

/// M1: `p_uniqueid` / `p_puniqueid`. `parent_unique` is the creator's unique
/// id and survives re-parenting (measured, not documented by Apple).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UniqueIds {
    pub unique: u64,
    pub parent_unique: u64,
}

/// M2: the public identity and lineage fields of `proc_bsdinfo`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BsdInfo {
    pub pid: u32,
    /// Current parent (1 after re-parenting to launchd).
    pub ppid: u32,
    pub pgid: u32,
    pub uid: u32,
    pub start_sec: u64,
    pub start_usec: u64,
    /// Short command name (at most 16 bytes, lossy UTF-8).
    pub comm: String,
}

/// Every failure of an OS call, classified.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum SysError {
    /// The process does not exist (`ESRCH`). The only "it is gone" result.
    #[error("process does not exist")]
    Gone,
    /// `EPERM` / `EACCES`. Not "gone": the process may well be running.
    #[error("permission denied")]
    PermissionDenied,
    /// The OS returned a different number of bytes than the structure has.
    /// Nothing from a partial structure is ever read.
    #[error("returned {got} bytes, expected {want}")]
    LengthMismatch { got: usize, want: usize },
    /// A pid list still did not fit after the retries.
    #[error("pid list incomplete after retries")]
    Incomplete,
    /// Any other errno.
    #[error("os error {0}")]
    Os(i32),
    /// The call failed and set no errno.
    #[error("failed without an errno")]
    Unknown,
    /// Not available on this OS.
    #[error("not supported on this OS")]
    Unsupported,
    /// The pid does not fit the OS type (above `i32::MAX`).
    #[error("pid out of range")]
    BadPid,
    /// `ECHILD` from M5: not our child, or already reaped by someone else.
    /// Not "gone": whoever reaped it may be about to reuse the pid.
    #[error("not a child of this process, or already reaped")]
    NotChild,
    /// `ECHILD` from M5 while `SIGCHLD` is ignored / `SA_NOCLDWAIT`: the
    /// kernel reaps our children at once, so M5 can never see them (ADR 0008
    /// rule 3 premise). Like `NotChild`, nothing may be signalled; unlike it,
    /// the cause is ours to fix (`sigchld_keep_zombies`).
    #[error("SIGCHLD is ignored: the kernel reaped the child (ADR 0008 rule 3)")]
    ChildrenAutoReaped,
    /// A spawn failed with `cause` after the process existed, and the
    /// process could not be confirmed gone within the bound. It may still
    /// exist: the caller must report it, never treat it as cleaned up.
    #[error("spawn failed ({cause}); child {pid} not confirmed gone")]
    NotReaped { pid: u32, cause: Box<SysError> },
}

impl SysError {
    /// Classify an errno from a failed call. `0` means none was set.
    pub fn from_errno(errno: i32) -> Self {
        match errno {
            0 => SysError::Unknown,
            3 => SysError::Gone,                  // ESRCH
            1 | 13 => SysError::PermissionDenied, // EPERM, EACCES
            10 => SysError::NotChild,             // ECHILD
            e => SysError::Os(e),
        }
    }

    /// Short stable tag for metrics and reports.
    pub fn tag(&self) -> &'static str {
        match self {
            SysError::Gone => "gone",
            SysError::PermissionDenied => "permission_denied",
            SysError::LengthMismatch { .. } => "length_mismatch",
            SysError::Incomplete => "incomplete",
            SysError::Os(_) => "os_error",
            SysError::Unknown => "unknown",
            SysError::Unsupported => "unsupported",
            SysError::BadPid => "bad_pid",
            SysError::NotChild => "not_child",
            SysError::ChildrenAutoReaped => "children_auto_reaped",
            SysError::NotReaped { .. } => "not_reaped",
        }
    }
}

/// Check the byte count of a fixed-size `proc_pidinfo` read.
/// `n <= 0` is a failure classified by `errno`; any other count that is not
/// exactly `want` is a length mismatch.
pub fn check_struct_read(n: i32, want: usize, errno: i32) -> Result<(), SysError> {
    if n <= 0 {
        return Err(SysError::from_errno(errno));
    }
    let got = usize::try_from(n).map_err(|_| SysError::Unknown)?;
    if got == want {
        Ok(())
    } else {
        Err(SysError::LengthMismatch { got, want })
    }
}

/// M1–M4 behind one interface, so callers can be tested with
/// [`fake::FakeProcessInfo`] without touching the OS.
pub trait ProcessInfo: Send + Sync {
    /// M1 (private SPI).
    fn unique_ids(&self, pid: u32) -> Result<UniqueIds, SysError>;
    /// M2.
    fn bsd_info(&self, pid: u32) -> Result<BsdInfo, SysError>;
    /// M3: current children of `ppid`. A gone parent gives an empty list
    /// (the OS cannot tell the two apart here).
    fn children(&self, ppid: u32) -> Result<Vec<u32>, SysError>;
    /// M4: every pid on the machine.
    fn all_pids(&self) -> Result<Vec<u32>, SysError>;
}

/// M5 result for one of our own children, read without reaping it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitState {
    /// Still running (only from [`ChildExit::peek`]).
    Running,
    /// Exited with this status; still a zombie, not reaped.
    Exited(i32),
    /// Killed by this signal; still a zombie, not reaped.
    Signaled(i32),
}

/// M5 behind one interface (real: [`Macos`]; tests: [`fake::FakeChildExit`]).
/// Neither call ever reaps: the caller's single `wait()` does that, after
/// every signal it means to send has been sent.
pub trait ChildExit: Send + Sync {
    /// Has our child `pid` exited? Never blocks.
    fn peek(&self, pid: u32) -> Result<ExitState, SysError>;
    /// Block until our child `pid` has exited (`EINTR` is retried). Never
    /// returns [`ExitState::Running`].
    fn wait_exit(&self, pid: u32) -> Result<ExitState, SysError>;
}

/// Turn one `waitid` result into an [`ExitState`]. `si_pid == 0` with
/// `WNOHANG` = still running; any other pid than `pid` is not trusted
/// (a63ab #509: the result counts only for the target).
pub fn exit_state_from(
    pid: u32,
    si_pid: i32,
    si_code: i32,
    si_status: i32,
) -> Result<ExitState, SysError> {
    if si_pid == 0 {
        return Ok(ExitState::Running);
    }
    if u32::try_from(si_pid).ok() != Some(pid) {
        return Err(SysError::Unknown);
    }
    match si_code {
        1 => Ok(ExitState::Exited(si_status)),       // CLD_EXITED
        2 | 3 => Ok(ExitState::Signaled(si_status)), // CLD_KILLED, CLD_DUMPED
        _ => Err(SysError::Unknown),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn errno_classes_are_distinct() {
        assert_eq!(SysError::from_errno(3), SysError::Gone);
        assert_eq!(SysError::from_errno(1), SysError::PermissionDenied);
        assert_eq!(SysError::from_errno(13), SysError::PermissionDenied);
        assert_eq!(SysError::from_errno(0), SysError::Unknown);
        assert_eq!(SysError::from_errno(22), SysError::Os(22));
        assert_eq!(SysError::from_errno(10), SysError::NotChild);
        assert_eq!(SysError::NotChild.tag(), "not_child");
    }

    #[test]
    fn exit_state_counts_only_for_the_asked_pid() {
        assert_eq!(exit_state_from(42, 0, 0, 0), Ok(ExitState::Running));
        assert_eq!(exit_state_from(42, 42, 1, 7), Ok(ExitState::Exited(7)));
        assert_eq!(exit_state_from(42, 42, 2, 9), Ok(ExitState::Signaled(9)));
        assert_eq!(exit_state_from(42, 42, 3, 6), Ok(ExitState::Signaled(6)));
        assert_eq!(exit_state_from(42, 43, 1, 0), Err(SysError::Unknown));
        assert_eq!(exit_state_from(42, -1, 1, 0), Err(SysError::Unknown));
        // stopped / continued / trapped are not exits
        assert_eq!(exit_state_from(42, 42, 5, 19), Err(SysError::Unknown));
    }

    #[test]
    fn struct_read_needs_the_exact_length() {
        assert_eq!(check_struct_read(56, 56, 0), Ok(()));
        assert_eq!(
            check_struct_read(48, 56, 0),
            Err(SysError::LengthMismatch { got: 48, want: 56 })
        );
        assert_eq!(
            check_struct_read(64, 56, 0),
            Err(SysError::LengthMismatch { got: 64, want: 56 })
        );
        assert_eq!(check_struct_read(0, 56, 3), Err(SysError::Gone));
        assert_eq!(
            check_struct_read(-1, 56, 1),
            Err(SysError::PermissionDenied)
        );
        assert_eq!(check_struct_read(0, 56, 0), Err(SysError::Unknown));
    }
}
