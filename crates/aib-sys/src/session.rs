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
use crate::settle::{root_reap, still_running, RootReap};
use crate::SysError;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

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

/// What a kill actually did (the session API's `stop`: `tree_size` = `members.len()`,
/// `survivors` = the pids below; clean only when [`KillReport::all_gone`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KillReport {
    /// Pids in the Job just before the terminate (root + descendants).
    pub members: Vec<u32>,
    /// The Job reported empty within the limit.
    pub emptied: bool,
    /// Members whose own handle still said "running" afterwards (empty = all gone).
    pub survivors: Vec<u32>,
}

impl KillReport {
    /// The Job is empty and every member we opened says exited: on Windows this is
    /// a proof (the Job contains the whole tree), so the API may report `clean`.
    pub fn all_gone(&self) -> bool {
        self.emptied && self.survivors.is_empty()
    }
}

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
        let (program, args) = spec.argv.split_first().ok_or(SpawnError::NoProgram)?;
        let job = Job::new().map_err(SpawnError::Job)?;
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
        let mut child = job.spawn(&mut cmd).map_err(SpawnError::Spawn)?;
        let pid = child.id();
        // the unwaited `child` pins the pid, so this handle is the root's;
        // it stays valid (and readable) after the root exits
        let root = match Process::open(pid) {
            Ok(p) => Arc::new(p),
            Err(e) => {
                // the root already exited (Gone) or cannot be opened: no waiter
                // handle, no session. The Job still holds whatever it started.
                drop(job); // KILL_ON_JOB_CLOSE
                let _ = child.wait();
                return Err(SpawnError::Spawn(e));
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

    /// Kill the whole tree and prove it. `limit` is one hard deadline for both
    /// the Job to empty and every member's handle to signal (they can lag the
    /// Job count: see [`crate::settle`]). Safe to call when the tree already
    /// exited (members = 0).
    pub fn kill(&mut self, limit: Duration) -> Result<KillReport, SysError> {
        let deadline = Instant::now() + limit;
        let members = self.job.members()?;
        // open each member before the terminate: our handle pins the pid, so
        // the answer below is about this process, not a reused pid
        let mut handles = Vec::with_capacity(members.len());
        for m in &members {
            match Process::open(m.pid) {
                Ok(p) => {
                    if p.created_100ns()? == m.created_100ns {
                        handles.push(p);
                    }
                }
                Err(SysError::Gone) => {}
                Err(e) => return Err(e),
            }
        }
        self.job.terminate(1)?;
        let emptied = self
            .job
            .wait_empty(deadline.saturating_duration_since(Instant::now()))?;
        let left = still_running(&handles, deadline, |p, left| p.wait(left))?;
        // collect the root only once its own handle said exited: "Job empty"
        // comes before the handle signals, so it is no reason to block
        let root = handles.iter().position(|p| p.pid() == self.pid);
        match root_reap(root, &left) {
            RootReap::Wait => {
                let _ = self.child.wait();
            }
            RootReap::TryOnly => {
                let _ = self.child.try_wait();
            }
        }
        let survivors = left.into_iter().map(|i| handles[i].pid()).collect();
        Ok(KillReport {
            members: members.iter().map(|m| m.pid).collect(),
            emptied,
            survivors,
        })
    }
}

// Dropping a SessionTree drops its Job: KILL_ON_JOB_CLOSE kills whatever is
// left, so a session can never outlive the struct that owns it.
