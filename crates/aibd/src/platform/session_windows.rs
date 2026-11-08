//! Windows sessions (preview.2; 043dd, file boundary BOUNDARY-sessions-win-043dd-1109).
//!
//! One Job per session ([`aib_sys::session::SessionTree`]), nested in J0 (aibd's
//! own Job, adopted first thing in `main`): aibd dying kills every session tree.
//! This file only turns the aib-sys results into the shared
//! [`super::session`] types; the proofs live in aib-sys:
//!
//! - `stop` = `SessionTree::kill`: open every member before `TerminateJobObject`,
//!   then one hard deadline for the Job to empty and every member handle to
//!   signal. `Clean` only when both hold (`KillReport::all_gone`); survivors ⇒
//!   `TreeNotClean`; an error ⇒ `Unverified("job_error:<tag>")`, never a guess.
//! - no TERM step: CTRL_BREAK needs a console shared with aibd, which a service
//!   or a hidden aibd does not have. `grace` only widens the wait.
//! - no WNOWAIT: the Job and the handles opened before the terminate pin each
//!   identity (pid + creation time); the root's waiter handle is opened while
//!   the unwaited `Child` still pins its pid.
//! - blind spots: none (the Job holds the whole tree; no breakaway).
//!
//! The child environment is the shared [`child_env`] (aibd's minus `AIBD_*`,
//! any case; the request's; `AIBD_SESSION` last), passed with `clear_env`.
//!
//! UNVERIFIED on a real machine until the stage-0 laptop run (a63ab plan).

use std::sync::Arc;
use std::time::Duration;

use aib_sys::job::Process;
use aib_sys::session::{KillReport, SessionTree, SpawnError};
use aib_sys::SysError;

use super::session::{
    child_env, Exit, ExitWait, Running, Signal, SpawnFail, Spec, Stop, StopStatus, JOB_ERROR_PREFIX,
};
use crate::lineage::Lineage;

/// Extra time on top of `grace` for the Job to empty and the handles to signal.
const KILL_SLACK: Duration = Duration::from_secs(2);

/// aib-sys spawn failure → the API's `spawn_failed` (same tags as POSIX).
/// `Spawn(NotReaped)` = started, not under a Job, not confirmed gone:
/// `left_running` carries its pid so the API keeps the slot (#539 M2).
pub fn spawn_fail(e: &SpawnError) -> SpawnFail {
    let left_running = match e {
        SpawnError::Spawn(SysError::NotReaped { pid, .. }) => Some(*pid),
        _ => None,
    };
    let tag = match e {
        SpawnError::NoProgram => "no_program".to_owned(),
        SpawnError::Job(err) => format!("{JOB_ERROR_PREFIX}{}", err.tag()),
        SpawnError::Spawn(err) => match err {
            SysError::Os(2) | SysError::Os(3) => "enoent".to_owned(), // FILE / PATH_NOT_FOUND
            SysError::PermissionDenied | SysError::Os(5) => "eacces".to_owned(),
            SysError::Os(n) => format!("os_{n}"),
            other => other.tag().to_owned(),
        },
    };
    SpawnFail { tag, left_running }
}

/// A kill result (or its error) → the shared `stop`.
pub fn stop_from(r: Result<KillReport, SysError>) -> Stop {
    match r {
        Ok(k) => {
            let status = if k.all_gone() {
                StopStatus::Clean
            } else if !k.survivors.is_empty() {
                StopStatus::TreeNotClean
            } else {
                // the Job did not report empty, yet every handle we opened says
                // exited: something we did not open may still be in the Job
                StopStatus::Unverified(format!("{JOB_ERROR_PREFIX}not_emptied"))
            };
            Stop {
                status,
                survivors: k.survivors,
                tree_size: k.members.len(),
                signals: vec![Signal {
                    signal: "job_terminate".into(),
                    targets: k.members,
                }],
                blind_spots: vec![],
            }
        }
        Err(e) => Stop {
            status: StopStatus::Unverified(format!("{JOB_ERROR_PREFIX}{}", e.tag())),
            survivors: vec![],
            tree_size: 0,
            signals: vec![],
            blind_spots: vec![],
        },
    }
}

struct RootWait(Arc<Process>);

impl ExitWait for RootWait {
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        match self.0.wait(timeout) {
            Ok(true) => self.0.exit_code().ok().flatten().map(code),
            _ => None,
        }
    }
}

/// Windows exit codes are u32 (NTSTATUS for crashes); the wire carries i32 bits.
fn code(c: u32) -> Exit {
    Exit::Code(c as i32)
}

struct WinSession(SessionTree);

impl Running for WinSession {
    fn pid(&self) -> u32 {
        self.0.pid()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        let root = self.0.root();
        root.exit_code()
            .map(|c| c.map(code))
            .map_err(|e| std::io::Error::other(e.tag()))
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(RootWait(self.0.root()))
    }

    fn stop(&mut self, grace: Duration) -> Stop {
        stop_from(self.0.kill(grace + KILL_SLACK))
    }
}

/// [`super::session::SpawnFn`] for Windows. `lineage` is macOS-only: ignored.
pub fn spawn(spec: &Spec, _lineage: Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
    let sys = aib_sys::session::Spec {
        argv: spec.argv.clone(),
        cwd: Some(spec.cwd.clone()),
        env: child_env(spec, std::env::vars_os()),
        clear_env: true,
    };
    SessionTree::spawn(&sys)
        .map(|t| Box::new(WinSession(t)) as Box<dyn Running>)
        .map_err(|e| spawn_fail(&e))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn report(members: &[u32], emptied: bool, survivors: &[u32]) -> Result<KillReport, SysError> {
        Ok(KillReport {
            members: members.to_vec(),
            emptied,
            survivors: survivors.to_vec(),
        })
    }

    #[test]
    fn clean_only_when_the_job_emptied_and_every_handle_signaled() {
        let s = stop_from(report(&[10, 11], true, &[]));
        assert_eq!(s.status, StopStatus::Clean);
        assert_eq!(
            (s.tree_size, s.signals[0].targets.clone()),
            (2, vec![10, 11])
        );
        assert!(s.blind_spots.is_empty() && s.releases_slot());
    }

    #[test]
    fn survivors_are_tree_not_clean_and_keep_the_slot() {
        let s = stop_from(report(&[10, 11], true, &[11]));
        assert_eq!(
            (s.status.clone(), s.survivors.clone()),
            (StopStatus::TreeNotClean, vec![11])
        );
        assert!(!s.releases_slot());
    }

    #[test]
    fn a_job_that_did_not_empty_is_never_clean() {
        let s = stop_from(report(&[10], false, &[]));
        assert_eq!(
            s.status,
            StopStatus::Unverified("job_error:not_emptied".into())
        );
        assert!(!s.confirmed());
    }

    #[test]
    fn a_kill_error_is_unverified_with_its_tag() {
        let s = stop_from(Err(SysError::PermissionDenied));
        assert_eq!(
            s.status,
            StopStatus::Unverified("job_error:permission_denied".into())
        );
    }

    #[test]
    fn spawn_failures_use_the_posix_codes() {
        assert_eq!(
            spawn_fail(&SpawnError::Spawn(SysError::Os(2))).tag,
            "enoent"
        );
        assert_eq!(
            spawn_fail(&SpawnError::Spawn(SysError::PermissionDenied)).tag,
            "eacces"
        );
        assert_eq!(spawn_fail(&SpawnError::NoProgram).tag, "no_program");
        assert_eq!(
            spawn_fail(&SpawnError::Job(SysError::Unknown)).tag,
            "job_error:unknown"
        );
        assert_eq!(
            spawn_fail(&SpawnError::Spawn(SysError::Os(2))).left_running,
            None
        );
    }

    #[test]
    fn a_spawn_not_confirmed_gone_keeps_its_pid() {
        let f = spawn_fail(&SpawnError::Spawn(SysError::NotReaped {
            pid: 4242,
            cause: Box::new(SysError::Unknown),
        }));
        assert_eq!((f.tag.as_str(), f.left_running), ("not_reaped", Some(4242)));
    }
}
