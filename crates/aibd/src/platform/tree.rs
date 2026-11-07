//! Process trees for sessions (ADR 0004). A session owns every process it
//! starts, not just a process group:
//!
//! 1. the CLI leads a new process group;
//! 2. descendants are recorded as `(pid, start_time)`: on macOS by the
//!    kqueue fork watcher as they appear (`fork_watch`), plus parent-pid
//!    scans as a backstop;
//! 3. every process inherits `AIBD_SESSION=<id>`; a sweep over same-user
//!    processes finds ones the scans missed (double forks, re-parenting).
//!
//! Signals only ever go to a pid whose start time still matches the record,
//! so a reused pid is never hit.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Child, ChildStdout, Command, Stdio};
use std::time::{Duration, Instant};
use sysinfo::{Pid, ProcessRefreshKind, ProcessStatus, ProcessesToUpdate, System, UpdateKind};

pub const SESSION_ENV: &str = "AIBD_SESSION";

#[derive(Debug, Clone)]
pub struct SpawnSpec {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub cwd: Option<PathBuf>,
    /// Extra environment. The caller decides what is inherited; aibd adds
    /// only `AIBD_SESSION`.
    pub env: Vec<(OsString, OsString)>,
    pub session_id: String,
    /// Layer 2 on macOS: watch forks with kqueue (ADR 0004 update). Always
    /// true in aibd; tests turn it off to check the other layers alone.
    pub watch_forks: bool,
}

/// One step of a cancel / cleanup, for the `session.cancelled` event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub signal: &'static str,
    pub targets: Vec<u32>,
}

#[derive(Debug, Clone, Default)]
pub struct Report {
    pub steps: Vec<Step>,
    /// Recorded processes still running (not zombies) at the end.
    pub survivors: Vec<u32>,
    /// How many processes the session was known to own.
    pub tree_size: usize,
    /// Found only by the environment-marker sweep.
    pub found_by_marker: Vec<u32>,
    /// First recorded by the fork watcher (macOS).
    pub found_by_watch: Vec<u32>,
    /// Fork events the watcher saw (proxy for the B1 descendant count).
    pub forks_seen: u64,
    pub exit_code: Option<i32>,
}

impl Report {
    pub fn tree_clean(&self) -> bool {
        self.survivors.is_empty()
    }
}

pub struct TreeHandle {
    session_id: String,
    child: Child,
    root: u32,
    known: BTreeMap<u32, u64>,
    found_by_marker: Vec<u32>,
    found_by_watch: Vec<u32>,
    #[cfg(target_os = "macos")]
    watch: Option<super::fork_watch::ForkWatch>,
}

fn new_system() -> std::io::Result<System> {
    System::new().map_err(|e| std::io::Error::other(format!("{e:?}")))
}

fn refresh(sys: &mut System, with_env: bool) {
    let mut kind = ProcessRefreshKind::nothing().without_tasks();
    if with_env {
        kind = kind.with_environ(UpdateKind::OnlyIfNotSet);
    }
    sys.refresh_processes_specifics(ProcessesToUpdate::All, true, kind);
}

fn running(sys: &System, pid: u32, start: u64) -> bool {
    match sys.process(Pid::from_u32(pid)) {
        Some(p) => {
            p.start_time() == start
                && !matches!(p.status(), ProcessStatus::Zombie | ProcessStatus::Dead)
        }
        None => false,
    }
}

pub fn spawn_tree(spec: &SpawnSpec) -> std::io::Result<TreeHandle> {
    let mut c = Command::new(&spec.program);
    c.args(&spec.args)
        .env(SESSION_ENV, &spec.session_id)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (k, v) in &spec.env {
        c.env(k, v);
    }
    if let Some(d) = &spec.cwd {
        c.current_dir(d);
    }
    imp::new_group(&mut c);
    let child = c.spawn()?;
    let root = child.id();
    let mut h = TreeHandle {
        session_id: spec.session_id.clone(),
        child,
        root,
        known: BTreeMap::new(),
        found_by_marker: Vec::new(),
        found_by_watch: Vec::new(),
        #[cfg(target_os = "macos")]
        watch: None,
    };
    let mut sys = new_system()?;
    refresh(&mut sys, false);
    if let Some(p) = sys.process(Pid::from_u32(root)) {
        h.known.insert(root, p.start_time());
        #[cfg(target_os = "macos")]
        if spec.watch_forks {
            h.watch = Some(super::fork_watch::ForkWatch::start(root, p.start_time())?);
        }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = spec.watch_forks;
    Ok(h)
}

impl TreeHandle {
    pub fn root_pid(&self) -> u32 {
        self.root
    }

    pub fn take_stdout(&mut self) -> Option<ChildStdout> {
        self.child.stdout.take()
    }

    /// Test hook: record a process as if a scan had found it.
    #[doc(hidden)]
    pub fn record_for_test(&mut self, pid: u32, start_time: u64) {
        self.known.insert(pid, start_time);
    }

    pub fn known(&mut self) -> Vec<u32> {
        self.absorb();
        self.known.keys().copied().collect()
    }

    /// Merge what the fork watcher recorded since the last call.
    fn absorb(&mut self) {
        #[cfg(target_os = "macos")]
        if let Some(w) = &self.watch {
            for (pid, start) in w.snapshot() {
                if let std::collections::btree_map::Entry::Vacant(e) = self.known.entry(pid) {
                    e.insert(start);
                    self.found_by_watch.push(pid);
                }
            }
        }
    }

    fn forks_seen(&self) -> u64 {
        #[cfg(target_os = "macos")]
        if let Some(w) = &self.watch {
            return w.forks_seen();
        }
        0
    }

    /// Layer 2: add every live process whose parent is a recorded process.
    /// Call periodically while the session runs.
    pub fn scan(&mut self) -> std::io::Result<()> {
        let mut sys = new_system()?;
        refresh(&mut sys, false);
        self.scan_with(&sys);
        Ok(())
    }

    fn scan_with(&mut self, sys: &System) {
        self.absorb();
        let me = std::process::id();
        loop {
            let mut added = false;
            for (pid, p) in sys.processes() {
                let pid = pid.as_u32();
                if pid == me || self.known.contains_key(&pid) {
                    continue;
                }
                if let Some(parent) = p.parent() {
                    if let Some(&pstart) = self.known.get(&parent.as_u32()) {
                        // A child cannot be older than its recorded parent;
                        // an older one means the parent pid was reused.
                        if p.start_time() >= pstart {
                            self.known.insert(pid, p.start_time());
                            added = true;
                        }
                    }
                }
            }
            if !added {
                break;
            }
        }
    }

    /// Layer 3: same-user processes that carry this session's marker.
    pub fn sweep_marker(&mut self) -> std::io::Result<()> {
        let mut sys = new_system()?;
        refresh(&mut sys, true);
        self.sweep_with(&sys);
        self.scan_with(&sys);
        Ok(())
    }

    fn sweep_with(&mut self, sys: &System) {
        let want = format!("{SESSION_ENV}={}", self.session_id);
        let me = std::process::id();
        for (pid, p) in sys.processes() {
            let pid = pid.as_u32();
            if pid == me || self.known.contains_key(&pid) {
                continue;
            }
            if p.environ()
                .iter()
                .any(|e| e.to_str() == Some(want.as_str()))
            {
                self.known.insert(pid, p.start_time());
                self.found_by_marker.push(pid);
            }
        }
    }

    fn alive(&self, sys: &System) -> Vec<(u32, u64)> {
        self.known
            .iter()
            .filter(|(pid, start)| running(sys, **pid, **start))
            .map(|(p, s)| (*p, *s))
            .collect()
    }

    fn wait_gone(&mut self, grace: Duration) -> std::io::Result<Vec<(u32, u64)>> {
        let end = Instant::now() + grace;
        loop {
            let _ = self.child.try_wait(); // reap our direct child as soon as it exits
            let mut sys = new_system()?;
            refresh(&mut sys, false);
            self.scan_with(&sys);
            let alive = self.alive(&sys);
            if alive.is_empty() || Instant::now() >= end {
                return Ok(alive);
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    /// Cancel: graceful interrupt, then terminate, then kill; verify by
    /// `(pid, start_time)`. Idempotent: calling it again on a clean tree just
    /// verifies.
    pub fn cancel(&mut self, grace: Duration) -> std::io::Result<Report> {
        self.sweep_marker()?;
        let mut steps = Vec::new();
        let alive = self.wait_gone(Duration::ZERO)?;
        if !alive.is_empty() {
            imp::signal_group(self.root, imp::Sig::Interrupt);
            steps.push(Step {
                signal: "SIGINT(group)",
                targets: vec![self.root],
            });
            self.wait_gone(grace)?;
        }
        self.cleanup_into(grace, steps)
    }

    /// After the CLI exited by itself: stop anything it left behind.
    pub fn cleanup(&mut self, grace: Duration) -> std::io::Result<Report> {
        self.sweep_marker()?;
        self.cleanup_into(grace, Vec::new())
    }

    fn cleanup_into(&mut self, grace: Duration, mut steps: Vec<Step>) -> std::io::Result<Report> {
        for sig in [imp::Sig::Terminate, imp::Sig::Kill] {
            let _ = self.sweep_marker();
            let alive = self.wait_gone(Duration::ZERO)?;
            if alive.is_empty() {
                break;
            }
            imp::signal_group(self.root, sig);
            let mut sys = new_system()?;
            refresh(&mut sys, false);
            let mut targets = Vec::new();
            for (pid, start) in alive {
                // re-check the start time right before signalling
                if running(&sys, pid, start) {
                    imp::signal_pid(pid, sig);
                    targets.push(pid);
                }
            }
            steps.push(Step {
                signal: sig.name(),
                targets,
            });
            self.wait_gone(if sig == imp::Sig::Kill {
                Duration::from_secs(2)
            } else {
                grace
            })?;
        }
        let exit_code = self.child.try_wait().ok().flatten().and_then(|s| s.code());
        let survivors = self
            .wait_gone(Duration::ZERO)?
            .into_iter()
            .map(|(p, _)| p)
            .collect();
        Ok(Report {
            steps,
            survivors,
            tree_size: self.known.len(),
            found_by_marker: self.found_by_marker.clone(),
            found_by_watch: self.found_by_watch.clone(),
            forks_seen: self.forks_seen(),
            exit_code,
        })
    }

    /// Wait for the CLI itself to exit (it may leave descendants running).
    pub fn wait_root(&mut self, timeout: Duration) -> std::io::Result<Option<i32>> {
        let end = Instant::now() + timeout;
        loop {
            if let Some(s) = self.child.try_wait()? {
                return Ok(Some(s.code().unwrap_or(-1)));
            }
            if Instant::now() >= end {
                return Ok(None);
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

#[cfg(unix)]
mod imp {
    use nix::sys::signal::{kill, killpg, Signal};
    use nix::unistd::Pid;
    use std::os::unix::process::CommandExt;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Sig {
        Interrupt,
        Terminate,
        Kill,
    }

    impl Sig {
        fn nix(self) -> Signal {
            match self {
                Sig::Interrupt => Signal::SIGINT,
                Sig::Terminate => Signal::SIGTERM,
                Sig::Kill => Signal::SIGKILL,
            }
        }
        pub fn name(self) -> &'static str {
            match self {
                Sig::Interrupt => "SIGINT",
                Sig::Terminate => "SIGTERM",
                Sig::Kill => "SIGKILL",
            }
        }
    }

    pub fn new_group(c: &mut std::process::Command) {
        c.process_group(0);
    }

    pub fn signal_group(leader: u32, s: Sig) {
        if let Ok(p) = i32::try_from(leader) {
            let _ = killpg(Pid::from_raw(p), s.nix());
        }
    }

    pub fn signal_pid(pid: u32, s: Sig) {
        if let Ok(p) = i32::try_from(pid) {
            let _ = kill(Pid::from_raw(p), s.nix());
        }
    }
}

#[cfg(windows)]
mod imp {
    //! Windows: Job Object path not implemented yet (ADR 0004). Until it is,
    //! every operation is a no-op, so `cancel` reports survivors honestly
    //! instead of pretending the tree is gone. UNVERIFIED-desktop.

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum Sig {
        Interrupt,
        Terminate,
        Kill,
    }

    impl Sig {
        pub fn name(self) -> &'static str {
            match self {
                Sig::Interrupt => "CTRL_BREAK(unimplemented)",
                Sig::Terminate => "TERMINATE(unimplemented)",
                Sig::Kill => "TERMINATE_JOB(unimplemented)",
            }
        }
    }

    pub fn new_group(_c: &mut std::process::Command) {}
    pub fn signal_group(_leader: u32, _s: Sig) {}
    pub fn signal_pid(_pid: u32, _s: Sig) {}
}
