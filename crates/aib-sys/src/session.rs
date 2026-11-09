//! Windows session trees (preview.2, a63ab plan 11-09; 043dd).
//!
//! Each session runs in its **own** Job Object from [`Job::new`]
//! (`KILL_ON_JOB_CLOSE`, no breakaway). aibd itself is in J0
//! (`Job::adopt_current`, first thing in `main`), so session Jobs are nested
//! inside J0: if aibd dies for any reason, J0's last handle closes and the OS
//! kills every session tree; if a session is killed, only its own Job goes.
//!
//! Kill = the same proof as the B2 self-check: open every member **before**
//! `TerminateJobObject` (pid + creation time, so a reused pid is never
//! asked), terminate, wait for the Job to empty, then each member's own
//! handle must say "exited". The report says what was seen, not what was hoped.
//!
//! Not here: a graceful stop first (CTRL_BREAK needs a shared console; aibd
//! may have none), PTY / ConPTY, stdout capture (stdio is null for now).

use crate::job::{Job, Member, Process};
use crate::settle::{kill_tree, KillOps};
use crate::SysError;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::Duration;
#[cfg(test)]
use std::time::Instant;

/// How long a spawn whose root handle could not be opened waits for its tree
/// to be confirmed gone.
const SPAWN_CONFIRM_LIMIT: Duration = Duration::from_secs(5);

/// What to start. `env` is added to aibd's environment, or is the whole
/// environment when `clear_env` is set (aibd passes its own environment minus
/// `AIBD_*` that way); the API layer decides which keys are allowed.
#[derive(Debug, Clone, Default)]
pub struct Spec {
    pub argv: Vec<String>,
    pub cwd: Option<PathBuf>,
    pub env: Vec<(OsString, OsString)>,
    pub clear_env: bool,
}

/// Why a session could not start.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SpawnError {
    /// `argv` was empty.
    NoProgram,
    /// `Job::new` failed.
    Job(SysError),
    /// The process could not be started (or assigned) in its Job. aib-sys kills
    /// and waits for it, but that is only confirmed when the inner error is
    /// **not** [`SysError::NotReaped`]: `NotReaped` = it may still be running,
    /// outside any session Job (see [`SpawnError::may_have_left_running`]).
    Spawn(SysError),
}

impl SpawnError {
    /// True when a process may have been left running (`Spawn(NotReaped)`):
    /// the API must not report "nothing started" for this one.
    pub fn may_have_left_running(&self) -> bool {
        matches!(self, SpawnError::Spawn(SysError::NotReaped { .. }))
    }

    pub fn tag(&self) -> String {
        match self {
            SpawnError::NoProgram => "no_program".into(),
            SpawnError::Job(e) => format!("job:{}", e.tag()),
            SpawnError::Spawn(e) => format!("spawn:{}", e.tag()),
        }
    }
}

pub use crate::settle::KillReport;

/// One running session: its Job, its root child, and a second handle to the
/// root that can be shared with a waiter thread.
#[derive(Debug)]
pub struct SessionTree {
    job: Job,
    child: Child,
    pid: u32,
    root: Arc<Process>,
}

impl SessionTree {
    /// Start `spec` inside a new Job. stdin / stdout / stderr are null.
    pub fn spawn(spec: &Spec) -> Result<SessionTree, SpawnError> {
        Self::spawn_or_leftover(spec).map_err(|(e, _)| e)
    }

    /// [`spawn`](Self::spawn), and when the failure is `NotReaped` with the
    /// tree still in its Job, that tree as a [`Leftover`] (Job + unwaited
    /// root), so the caller can stop the same identity again later (a9136 /
    /// a63ab #555: aibd's shutdown sweep). `None` = nothing held (nothing
    /// started, confirmed gone, or the root never got into its Job).
    pub fn spawn_or_leftover(
        spec: &Spec,
    ) -> Result<SessionTree, (SpawnError, Option<Box<Leftover>>)> {
        Self::spawn_keep(
            spec,
            |pid, _| Process::open(pid),
            |j| j.terminate(1),
            SPAWN_CONFIRM_LIMIT,
        )
    }

    /// [`spawn`](Self::spawn) with the root open and the Job terminate
    /// injectable (tests) and the confirm limit.
    #[cfg(test)]
    pub(crate) fn spawn_with(
        spec: &Spec,
        open: impl FnOnce(u32, &Job) -> Result<Process, SysError>,
        terminate: impl FnMut(&Job) -> Result<(), SysError>,
        limit: Duration,
    ) -> Result<SessionTree, SpawnError> {
        Self::spawn_keep(spec, open, terminate, limit).map_err(|(e, _)| e)
    }

    pub(crate) fn spawn_keep(
        spec: &Spec,
        open: impl FnOnce(u32, &Job) -> Result<Process, SysError>,
        terminate: impl FnMut(&Job) -> Result<(), SysError>,
        limit: Duration,
    ) -> Result<SessionTree, (SpawnError, Option<Box<Leftover>>)> {
        let (program, args) = spec
            .argv
            .split_first()
            .ok_or((SpawnError::NoProgram, None))?;
        let job = Job::new().map_err(|e| (SpawnError::Job(e), None))?;
        let mut cmd = Command::new(program);
        cmd.args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if let Some(dir) = &spec.cwd {
            cmd.current_dir(dir);
        }
        if spec.clear_env {
            cmd.env_clear();
        }
        for (k, v) in &spec.env {
            cmd.env(k, v);
        }
        // a NotReaped here = the root never got into the Job: nothing to hold
        let mut child = job
            .spawn(&mut cmd)
            .map_err(|e| (SpawnError::Spawn(e), None))?;
        let pid = child.id();
        // the unwaited `child` pins the pid, so this handle is the root's;
        // it stays valid (and readable) after the root exits
        let root = match open(pid, &job) {
            Ok(p) => Arc::new(p),
            Err(e) => {
                // it already runs (resumed) and may have forked: the same proof
                // as a kill (#535) — open every member, terminate the Job, then
                // one deadline for the Job to empty AND every handle to signal
                // (the Job count drops before the handles do: 1ced7 #543 CI).
                // Anything unconfirmed is NotReaped and keeps its slot
                // (definition #11).
                // its start time, read before the kill (the Job may stop
                // listing it), for the shutdown report
                let start = job
                    .members()
                    .ok()
                    .and_then(|m| m.iter().find(|m| m.pid == pid).map(|m| m.created_100ns));
                let mut term = terminate;
                let k = kill_tree(
                    &mut Ops {
                        job: &job,
                        child: &mut child,
                        pid,
                        term: Some(&mut term),
                    },
                    limit,
                );
                let e = match k {
                    Ok(k) if k.all_gone() => e,
                    _ => SysError::NotReaped {
                        pid,
                        cause: Box::new(e),
                    },
                };
                if let SysError::NotReaped { .. } = e {
                    // keep the Job and the unwaited root: the caller stops
                    // this same identity again (dropping it closes the Job,
                    // KILL_ON_JOB_CLOSE)
                    let left = Leftover {
                        job,
                        child,
                        pid,
                        start,
                    };
                    return Err((SpawnError::Spawn(e), Some(Box::new(left))));
                }
                drop(job); // only now; KILL_ON_JOB_CLOSE stays the backstop
                return Err((SpawnError::Spawn(e), None));
            }
        };
        Ok(SessionTree {
            job,
            child,
            pid,
            root,
        })
    }

    /// A handle to the root for waiting from another thread (never reaps;
    /// Windows has no reaping race: the handle pins the identity).
    pub fn root(&self) -> Arc<Process> {
        Arc::clone(&self.root)
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    /// The root's exit code if it has exited (descendants may still run: the
    /// session is not "gone" until [`SessionTree::kill`] or the Job empties).
    pub fn try_exit(&mut self) -> std::io::Result<Option<i32>> {
        Ok(self.child.try_wait()?.and_then(|s| s.code()))
    }

    /// Who is in the Job now (pid + creation time each).
    pub fn members(&self) -> Result<Vec<Member>, SysError> {
        self.job.members()
    }

    /// Processes still in the Job (root + descendants).
    pub fn active(&self) -> Result<u32, SysError> {
        self.job.active_processes()
    }

    /// Kill the whole tree and report what was seen ([`crate::settle`]).
    /// `limit` is one hard deadline for the Job to empty and every member to
    /// signal; every unanswered question makes the report not clean. Only a
    /// failed terminate is an `Err`. Safe when the tree already exited.
    pub fn kill(&mut self, limit: Duration) -> Result<KillReport, SysError> {
        kill_tree(
            &mut Ops {
                job: &self.job,
                child: &mut self.child,
                pid: self.pid,
                term: None,
            },
            limit,
        )
    }
}

// Dropping a SessionTree drops its Job: KILL_ON_JOB_CLOSE kills whatever is
// left, so a session can never outlive the struct that owns it.

/// A start that failed `NotReaped` with its tree still in its Job
/// ([`SessionTree::spawn_or_leftover`]): the Job and the unwaited root (which
/// pins the pid), so a later stop hits this identity and no other. Dropping
/// it closes the Job (KILL_ON_JOB_CLOSE).
#[derive(Debug)]
pub struct Leftover {
    job: Job,
    child: Child,
    pid: u32,
    start: Option<u64>,
}

impl Leftover {
    pub fn pid(&self) -> u32 {
        self.pid
    }

    /// The root's creation time (FILETIME 100 ns) if it could be read.
    pub fn start_100ns(&self) -> Option<u64> {
        self.start
    }

    /// The same proof as [`SessionTree::kill`].
    pub fn kill(&mut self, limit: Duration) -> Result<KillReport, SysError> {
        kill_tree(
            &mut Ops {
                job: &self.job,
                child: &mut self.child,
                pid: self.pid,
                term: None,
            },
            limit,
        )
    }

    /// Test hook (aibd): hold a running tree as if its start had failed.
    #[doc(hidden)]
    pub fn from_tree_for_test(t: SessionTree) -> Leftover {
        let start = t.root.created_100ns().ok();
        Leftover {
            job: t.job,
            child: t.child,
            pid: t.pid,
            start,
        }
    }
}

/// The Windows calls behind [`crate::settle::kill_tree`].
/// Terminates a session Job (injectable in tests).
type Terminate<'t> = dyn FnMut(&Job) -> Result<(), SysError> + 't;

struct Ops<'a> {
    job: &'a Job,
    child: &'a mut Child,
    pid: u32,
    /// `None` = `TerminateJobObject`; tests inject their own.
    term: Option<&'a mut Terminate<'a>>,
}

impl KillOps for Ops<'_> {
    type Handle = Process;
    type Error = SysError;

    fn root_pid(&self) -> u32 {
        self.pid
    }

    fn members(&mut self) -> Result<Vec<(u32, u64)>, SysError> {
        Ok(self
            .job
            .members()?
            .into_iter()
            .map(|m| (m.pid, m.created_100ns))
            .collect())
    }

    fn open(&mut self, pid: u32) -> Result<Option<Process>, SysError> {
        match Process::open(pid) {
            Ok(p) => Ok(Some(p)),
            Err(SysError::Gone) => Ok(None),
            Err(e) => Err(e),
        }
    }

    fn pid_of(&self, h: &Process) -> u32 {
        h.pid()
    }

    fn created(&mut self, h: &Process) -> Result<u64, SysError> {
        h.created_100ns()
    }

    fn terminate(&mut self) -> Result<(), SysError> {
        if let Some(t) = self.term.as_mut() {
            return t(self.job);
        }
        self.job.terminate(1)
    }

    fn wait_empty(&mut self, left: Duration) -> Result<bool, SysError> {
        self.job.wait_empty(left)
    }

    fn wait(&mut self, h: &Process, left: Duration) -> Result<bool, SysError> {
        h.wait(left)
    }

    fn reap_root(&mut self, block: bool) -> Result<bool, SysError> {
        let r = if block {
            self.child.wait().map(Some)
        } else {
            self.child.try_wait()
        };
        r.map(|s| s.is_some())
            .map_err(|e| e.raw_os_error().map_or(SysError::Unknown, SysError::Os))
    }
}

#[cfg(test)]
mod tests {
    //! Definition #11 for a root whose own handle cannot be opened after the
    //! spawn (1ced7 #543 review High): it already runs and may have forked, so
    //! "not started" is only said once the root is seen gone **and** the Job is
    //! empty; otherwise `NotReaped` (the slot stays taken).
    use super::*;
    use std::cell::RefCell;

    /// `cmd` (root) that starts `ping` (a grandchild) and stays.
    fn tree_spec() -> Spec {
        Spec {
            argv: ["cmd", "/C", "ping -n 60 127.0.0.1 >NUL"]
                .iter()
                .map(|a| a.to_string())
                .collect(),
            ..Spec::default()
        }
    }

    /// Wait until the Job holds the root and its child, then hold a
    /// terminable handle on every member (so the test can see them go).
    /// Called from the injected open, i.e. before the failure path lists the
    /// members: a member born between that list and the terminate is only
    /// covered by the Job count (the same gap as `kill`, #535).
    fn hold_members(job: &Job, held: &RefCell<Vec<Process>>) {
        let t = Instant::now();
        while job.active_processes().unwrap_or(0) < 2 {
            assert!(t.elapsed() < Duration::from_secs(10), "child never started");
            #[allow(clippy::disallowed_methods)] // test pacing only
            std::thread::sleep(Duration::from_millis(20));
        }
        for m in job.members().expect("members") {
            if let Ok(p) = Process::open_terminable(m.pid) {
                held.borrow_mut().push(p);
            }
        }
    }

    fn cleanup(held: &RefCell<Vec<Process>>) {
        for p in held.borrow().iter() {
            let _ = p.terminate(1);
            let _ = p.wait(Duration::from_secs(5));
        }
    }

    #[test]
    fn a_failed_root_open_kills_and_confirms_the_whole_tree() {
        let held = RefCell::new(Vec::new());
        let r = SessionTree::spawn_with(
            &tree_spec(),
            |_, j| {
                hold_members(j, &held);
                Err(SysError::Os(8))
            },
            |j| j.terminate(1),
            Duration::from_secs(5),
        );
        let gone: Vec<bool> = held
            .borrow()
            .iter()
            .map(|p| p.wait(Duration::ZERO) == Ok(true))
            .collect();
        cleanup(&held);
        match r {
            Err(SpawnError::Spawn(SysError::Os(8))) => {}
            other => panic!("expected Spawn(Os(8)), got {:?}", other.err()),
        }
        assert!(gone.len() >= 2, "root and child were not both seen");
        assert!(
            gone.iter().all(|g| *g),
            "returned 'not started' while a member still ran: {gone:?}"
        );
    }

    #[test]
    fn a_failed_root_open_whose_child_outlives_the_root_is_not_reaped() {
        let held = RefCell::new(Vec::new());
        let root_pid = RefCell::new(0u32);
        let r = SessionTree::spawn_keep(
            &tree_spec(),
            |pid, j| {
                *root_pid.borrow_mut() = pid;
                hold_members(j, &held);
                Err(SysError::Os(8))
            },
            |_| {
                // only the root goes: the Job never empties
                let root = Process::open_terminable(*root_pid.borrow())?;
                root.terminate(1)
            },
            Duration::from_millis(800),
        );
        // this thread's Windows last-error right after the start, before any
        // other call can overwrite it (a63ab: a red run should carry a code to
        // look up; `SysError::Os` already carries its own)
        let last = std::io::Error::last_os_error();
        let Err((e, left)) = r else {
            panic!("must not start")
        };
        // the tree is handed back, and a later stop of it confirms it gone.
        // Say which failure it was: the windows-latest flake (2 of 14 test
        // steps since #555, 043dd 11-09) printed neither the error nor whether
        // the injected open ever ran; root pid 0 = Job::spawn failed before it.
        let mut left = left.unwrap_or_else(|| {
            let pid = *root_pid.borrow();
            if pid == 0 {
                panic!(
                    "Job::spawn failed before the injected open ran: {e:?}; \
                     last OS error {:?} ({last})",
                    last.raw_os_error()
                )
            }
            panic!(
                "NotReaped with its Job must hand the tree back; got {e:?}, root pid {pid}; \
                 last OS error {:?} ({last})",
                last.raw_os_error()
            )
        });
        assert_eq!(left.pid(), *root_pid.borrow());
        assert!(left.start_100ns().is_some(), "start time not read");
        let k = left.kill(Duration::from_secs(5)).expect("kill");
        let gone: Vec<bool> = held
            .borrow()
            .iter()
            .map(|p| p.wait(Duration::ZERO) == Ok(true))
            .collect();
        cleanup(&held);
        assert!(k.all_gone(), "{k:?}");
        assert!(
            gone.iter().all(|g| *g),
            "a member outlived the later stop: {gone:?}"
        );
        assert!(e.may_have_left_running(), "slot released: {e:?}");
        match e {
            SpawnError::Spawn(SysError::NotReaped { pid, .. }) => {
                assert_eq!(pid, *root_pid.borrow())
            }
            other => panic!("expected NotReaped, got {other:?}"),
        }
    }

    #[test]
    fn a_failed_root_open_that_cannot_kill_is_not_reaped_within_its_limit() {
        let held = RefCell::new(Vec::new());
        let t = Instant::now();
        let r = SessionTree::spawn_with(
            &tree_spec(),
            |_, j| {
                hold_members(j, &held);
                Err(SysError::Os(8))
            },
            |_| Ok(()), // a terminate that does nothing
            Duration::from_millis(300),
        );
        let waited = t.elapsed();
        cleanup(&held);
        let Err(e) = r else { panic!("must not start") };
        assert!(e.may_have_left_running(), "slot released: {e:?}");
        // hold_members may take a moment for the child to appear; the limit
        // itself is 300 ms
        assert!(waited < Duration::from_secs(8), "not bounded: {waited:?}");
    }
}
