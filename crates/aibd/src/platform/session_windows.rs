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
        SpawnError::NoProgram => "empty_argv".to_owned(), // same tag as POSIX
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
                // nothing is known to survive, but something went unanswered
                // (list / open / wait failed, root exit not confirmed, Job not
                // empty): say which, never clean
                let why = k.unconfirmed().unwrap_or("unconfirmed"); // unreachable: !all_gone && no survivors
                StopStatus::Unverified(format!("{JOB_ERROR_PREFIX}{why}"))
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

/// The two reads [`RootWait`] makes on the root's handle (a trait so a failing
/// handle can be tested).
trait RootHandle: Send + Sync {
    fn wait(&self, timeout: Duration) -> Result<bool, SysError>;
    fn exit_code(&self) -> Result<Option<u32>, SysError>;
}

impl RootHandle for Process {
    fn wait(&self, timeout: Duration) -> Result<bool, SysError> {
        Process::wait(self, timeout)
    }
    fn exit_code(&self) -> Result<Option<u32>, SysError> {
        Process::exit_code(self)
    }
}

/// An exit is only reported when the handle signaled **and** the code was read.
/// A failed wait or an unreadable code is never an exit (so never a clean end):
/// it reads as "not yet" until the three-state ExitWait (a9136, after preview.2;
/// a63ab ruling on 1ced7 #543 Medium 1).
struct RootWait<P: RootHandle>(Arc<P>);

impl<P: RootHandle> ExitWait for RootWait<P> {
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        match self.0.wait(timeout) {
            Ok(true) => match self.0.exit_code() {
                Ok(Some(c)) => Some(code(c)),
                // signaled but the code cannot be read: stays so on every call
                _ => idle(timeout),
            },
            Ok(false) => None, // the wait itself took `timeout`
            Err(_) => idle(timeout),
        }
    }
}

/// Longest pause before an unanswered wait returns `None`.
const IDLE_MAX: Duration = Duration::from_secs(1);

/// A wait that failed, or a signaled root whose code cannot be read, would
/// otherwise return `None` at once on every call; a caller that loops on
/// `wait` (#546 `watch_root`) would spin at 100% CPU holding its lock each
/// round (1ced7 #543 Medium 2, a63ab 07:12 ruling (a)). Pause `min(timeout,
/// 1 s)` first: still "not an exit", never a busy loop.
fn idle(timeout: Duration) -> Option<Exit> {
    // ADR 0005 named exception: a bounded back-off on a failed OS wait inside
    // a blocking ExitWait call (already on its own watcher thread); there is
    // no timer event to register for "the handle wait failed".
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(timeout.min(IDLE_MAX));
    None
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
            listed: true,
            members: members.to_vec(),
            emptied,
            survivors: survivors.to_vec(),
            unknown: vec![],
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
    fn an_unanswered_member_or_list_is_unverified_with_its_code() {
        let mut k = report(&[10, 11], true, &[]).unwrap();
        k.unknown = vec![10];
        let s = stop_from(Ok(k.clone()));
        assert_eq!(
            s.status,
            StopStatus::Unverified("job_error:exit_unconfirmed".into())
        );
        assert!(!s.releases_slot());
        k.unknown.clear();
        k.listed = false;
        let s = stop_from(Ok(k));
        assert_eq!(
            s.status,
            StopStatus::Unverified("job_error:members_unlisted".into())
        );
    }

    #[test]
    fn a_kill_error_is_unverified_with_its_tag() {
        let s = stop_from(Err(SysError::PermissionDenied));
        assert_eq!(
            s.status,
            StopStatus::Unverified("job_error:permission_denied".into())
        );
    }

    struct FakeRoot {
        wait: Result<bool, SysError>,
        code: Result<Option<u32>, SysError>,
    }

    impl RootHandle for FakeRoot {
        fn wait(&self, _: Duration) -> Result<bool, SysError> {
            self.wait.clone()
        }
        fn exit_code(&self) -> Result<Option<u32>, SysError> {
            self.code.clone()
        }
    }

    fn root_wait(
        wait: Result<bool, SysError>,
        code: Result<Option<u32>, SysError>,
    ) -> Option<Exit> {
        RootWait(Arc::new(FakeRoot { wait, code })).wait(Duration::ZERO)
    }

    /// 1ced7 #543 / a63ab 06:2x: a root wait or exit-code error is never an exit.
    #[test]
    fn a_root_wait_or_exit_code_error_is_never_an_exit() {
        assert_eq!(root_wait(Err(SysError::Os(6)), Ok(Some(0))), None);
        assert_eq!(root_wait(Ok(true), Err(SysError::Os(5))), None);
        assert_eq!(root_wait(Ok(true), Ok(None)), None);
        assert_eq!(root_wait(Ok(false), Ok(Some(0))), None);
        assert_eq!(root_wait(Ok(true), Ok(Some(7))), Some(Exit::Code(7)));
        assert_eq!(
            root_wait(Ok(true), Ok(Some(0xC000_0005))),
            Some(Exit::Code(0xC000_0005_u32 as i32))
        );
    }

    /// 1ced7 #543 Medium 2: the two "no exit" answers that would come back at
    /// once pause first (bounded), so a loop on `wait` cannot spin.
    #[test]
    fn an_unanswered_root_wait_pauses_before_none_and_is_bounded() {
        use std::time::Instant;
        for (wait, code) in [
            (Ok(true), Err(SysError::Os(5))),
            (Ok(true), Ok(None)),
            (Err(SysError::Os(6)), Ok(Some(0))),
        ] {
            let t = Instant::now();
            assert_eq!(
                RootWait(Arc::new(FakeRoot { wait, code })).wait(Duration::from_millis(200)),
                None
            );
            let took = t.elapsed();
            assert!(
                took >= Duration::from_millis(180),
                "returned at once: {took:?}"
            );
            assert!(took < Duration::from_secs(2), "not bounded: {took:?}");
        }
        let t = Instant::now();
        let r = RootWait(Arc::new(FakeRoot {
            wait: Err(SysError::Os(6)),
            code: Ok(None),
        }))
        .wait(Duration::from_secs(30));
        assert_eq!(r, None);
        assert!(
            t.elapsed() < Duration::from_millis(1500),
            "longer than IDLE_MAX"
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
        assert_eq!(spawn_fail(&SpawnError::NoProgram).tag, "empty_argv");
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
