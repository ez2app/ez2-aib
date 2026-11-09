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

use std::sync::{Arc, Mutex};
use std::time::Duration;

use aib_sys::job::Process;
use aib_sys::session::{KillReport, Leftover, SessionTree, SpawnError};
use aib_sys::SysError;

use super::session::{
    child_env, Exit, ExitWait, Running, Signal, SpawnFail, Spec, Stop, StopStatus, Waited,
    JOB_ERROR_PREFIX,
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

/// An exit is only reported when the handle signaled **and** the code was
/// read. A failed wait or an unreadable code is [`Waited::Failed`], never an
/// exit (so never a clean end) and never "not yet" (a9136 three-state
/// ExitWait; a63ab ruling on 1ced7 #543 Medium 1). Those answers come back at
/// once; the caller's loop backs off (`Sessions::watch_root`, #543 Medium 2).
struct RootWait<P: RootHandle>(Arc<P>);

impl<P: RootHandle> ExitWait for RootWait<P> {
    fn wait(&self, timeout: Duration) -> Waited {
        match self.0.wait(timeout) {
            Ok(true) => match self.0.exit_code() {
                Ok(Some(c)) => Waited::Exited(code(c)),
                // signaled but the code cannot be read: stays so on every call
                _ => Waited::Failed(FAILED_CODE_UNREADABLE),
            },
            Ok(false) => Waited::TimedOut, // the wait itself took `timeout`
            Err(_) => Waited::Failed(FAILED_WAIT),
        }
    }
}

const FAILED_WAIT: &str = "wait_failed";
const FAILED_CODE_UNREADABLE: &str = "exit_code_unreadable";

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
    SessionTree::spawn_or_leftover(&sys)
        .map(|t| Box::new(WinSession(t)) as Box<dyn Running>)
        .map_err(|(e, left)| {
            let f = spawn_fail(&e);
            keep(f.left_running, left);
            f
        })
}

/// Failed starts not confirmed gone, with their Job (a9136 / a63ab #555,
/// the Windows side of #544 D4): stopped again when aibd ends.
static LEFTOVERS: Mutex<Vec<Leftover>> = Mutex::new(Vec::new());

/// Failed starts not confirmed gone whose root never got into a Job: nothing
/// to stop them with, so the shutdown sweep can only list them (never silent).
static UNHELD: Mutex<Vec<u32>> = Mutex::new(Vec::new());

/// Keep what a failed start may have left running (`left_running` is what the
/// API reports and what keeps the slot).
#[doc(hidden)]
pub fn keep(left_running: Option<u32>, left: Option<Box<Leftover>>) {
    match (left_running, left) {
        (Some(_), Some(l)) => LEFTOVERS.lock().unwrap_or_else(|e| e.into_inner()).push(*l),
        (Some(pid), None) => UNHELD.lock().unwrap_or_else(|e| e.into_inner()).push(pid),
        // confirmed gone: dropping it closes its (empty) Job
        (None, _) => {}
    }
}

/// Stop every kept tree once more (aibd is ending), all at once and all
/// within ONE absolute `deadline` (as `session_unix::stop_leftovers`, #544
/// D4): each gets its own thread and the same proof as a DELETE. Returns the
/// roots not confirmed gone by then, as (pid, creation time), plus every
/// root that was never held (time unknown); never reports them clean.
pub fn stop_leftovers(deadline: std::time::Instant) -> Vec<(u32, Option<u64>)> {
    let mut out: Vec<(u32, Option<u64>)> = UNHELD
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .map(|p| (*p, None))
        .collect();
    let left = std::mem::take(&mut *LEFTOVERS.lock().unwrap_or_else(|e| e.into_inner()));
    if !left.is_empty() {
        let limit = deadline.saturating_duration_since(std::time::Instant::now());
        let (tx, rx) = std::sync::mpsc::channel();
        let mut waiting = std::collections::BTreeSet::new();
        for mut l in left {
            let root = (l.pid(), l.start_100ns());
            waiting.insert(root);
            let tx = tx.clone();
            // a thread that cannot start drops its tree (KILL_ON_JOB_CLOSE)
            // unconfirmed: it stays in `waiting` and is reported
            let _ = std::thread::Builder::new()
                .name("aibd-leftover-stop".into())
                .spawn(move || {
                    let ok = matches!(l.kill(limit), Ok(k) if k.all_gone());
                    if !ok {
                        LEFTOVERS.lock().unwrap_or_else(|e| e.into_inner()).push(l);
                    }
                    let _ = tx.send((root, ok));
                });
        }
        drop(tx);
        while !waiting.is_empty() {
            let left = deadline.saturating_duration_since(std::time::Instant::now());
            // One wake-up per answer, or at the deadline: not a periodic timer
            // (ADR 0005 named exception, as `session_unix::stop_leftovers`).
            #[allow(clippy::disallowed_methods)]
            let got = rx.recv_timeout(left);
            match got {
                Ok((root, ok)) => {
                    waiting.remove(&root);
                    if !ok {
                        out.push(root);
                    }
                }
                Err(_) => break, // the deadline, or every thread gone
            }
        }
        out.extend(waiting);
    }
    out.sort_unstable();
    out
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    /// The two sweep tests share LEFTOVERS / UNHELD: one at a time, or one
    /// test's sweep stops the other's kept tree.
    static SWEEP: Mutex<()> = Mutex::new(());

    /// `cmd` (root) that starts `ping` (a grandchild) and stays.
    fn ping_tree() -> SessionTree {
        let spec = aib_sys::session::Spec {
            argv: ["cmd", "/C", "ping -n 60 127.0.0.1 >NUL"]
                .iter()
                .map(|a| a.to_string())
                .collect(),
            ..Default::default()
        };
        let t = SessionTree::spawn(&spec).unwrap();
        let end = std::time::Instant::now() + Duration::from_secs(10);
        while t.active().unwrap_or(0) < 2 {
            assert!(std::time::Instant::now() < end, "ping never started");
            #[allow(clippy::disallowed_methods)] // test pacing only
            std::thread::sleep(Duration::from_millis(20));
        }
        t
    }

    /// a9136 #555 Medium: a failed start that kept its slot is kept (not
    /// dropped) and stopped by the shutdown sweep; confirmed ones are not
    /// reported.
    #[test]
    fn a_kept_failed_start_is_stopped_by_the_shutdown_sweep() {
        let _g = SWEEP.lock().unwrap_or_else(|e| e.into_inner());
        let t = ping_tree();
        let root = t.pid();
        let held: Vec<Process> = t
            .members()
            .unwrap()
            .iter()
            .filter_map(|m| Process::open(m.pid).ok())
            .collect();
        assert!(held.len() >= 2, "root and ping not both held");
        keep(Some(root), Some(Box::new(Leftover::from_tree_for_test(t))));
        // kept = still running until the sweep (a drop would close its Job)
        assert!(
            held.iter()
                .all(|p| p.wait(Duration::from_millis(300)) == Ok(false)),
            "the kept tree did not stay running"
        );
        let out = stop_leftovers(std::time::Instant::now() + Duration::from_secs(10));
        assert!(
            !out.iter().any(|(p, _)| *p == root),
            "confirmed tree reported: {out:?}"
        );
        assert!(
            held.iter().all(|p| p.wait(Duration::ZERO) == Ok(true)),
            "a member survived the sweep"
        );
    }

    /// A root that never got into a Job cannot be stopped: the shutdown sweep
    /// lists it (through the platform entry aibd calls), never silent.
    #[test]
    fn an_unheld_failed_start_is_listed_at_shutdown() {
        let _g = SWEEP.lock().unwrap_or_else(|e| e.into_inner());
        let pid = u32::MAX - 41; // never a real pid: only listed
        keep(Some(pid), None);
        let out =
            crate::platform::stop_leftovers(std::time::Instant::now() + Duration::from_secs(1));
        assert!(out.contains(&(pid, None)), "{out:?}");
    }

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

    fn root_wait(wait: Result<bool, SysError>, code: Result<Option<u32>, SysError>) -> Waited {
        RootWait(Arc::new(FakeRoot { wait, code })).wait(Duration::ZERO)
    }

    /// 1ced7 #543 / a63ab 06:2x: a root wait or exit-code error is never an
    /// exit, and never "not yet" either: it says it could not look.
    #[test]
    fn a_root_wait_or_exit_code_error_is_never_an_exit() {
        assert_eq!(
            root_wait(Err(SysError::Os(6)), Ok(Some(0))),
            Waited::Failed("wait_failed")
        );
        assert_eq!(
            root_wait(Ok(true), Err(SysError::Os(5))),
            Waited::Failed("exit_code_unreadable")
        );
        assert_eq!(
            root_wait(Ok(true), Ok(None)),
            Waited::Failed("exit_code_unreadable")
        );
        assert_eq!(root_wait(Ok(false), Ok(Some(0))), Waited::TimedOut);
        assert_eq!(
            root_wait(Ok(true), Ok(Some(7))),
            Waited::Exited(Exit::Code(7))
        );
        assert_eq!(
            root_wait(Ok(true), Ok(Some(0xC000_0005))),
            Waited::Exited(Exit::Code(0xC000_0005_u32 as i32))
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
