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
//! so a reused pid is never hit. A group signal (`killpg(root)`) goes out only
//! while the group number is provably still ours: the root itself still runs
//! with its recorded start time, or a recorded member whose identity was just
//! checked is in that group (a group number cannot be reused while any member
//! exists). Otherwise only the checked single pids are signalled (043dd #510 H3).
//!
//! Misses that some layer KNOWS about (the fork watcher could not watch or
//! list, the root was never recorded) are collected as `known_gaps`; a cancel
//! with any of them is `unverified` with that code, never `observed_clean`.
//!
//! macOS lineage (M1, `crate::lineage`): processes re-parented to launchd
//! are linked back through their creator's unique id and added as
//! candidates. A candidate is signalled only after the public identity
//! check, plus its unique id. Without lineage (self-check failed, not
//! macOS, or an incomplete pass) a cancel report is never `clean`: it is
//! `unverified` at best.

use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use crate::lineage::{Lineage, M1Cache};
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
    /// Start from an empty environment: `env` is then the whole of it
    /// (sessions: `session::child_env`, one rule for every platform).
    pub env_clear: bool,
    pub session_id: String,
    /// Layer 2 on macOS: watch forks with kqueue (ADR 0004 update). Always
    /// true in aibd; tests turn it off to check the other layers alone.
    pub watch_forks: bool,
    /// stdout / stderr go to the null device instead of pipes (sessions API:
    /// nobody reads them, and an unread pipe would block the program once it
    /// fills). stdin is always the null device.
    pub discard_output: bool,
    /// M1 lineage, shared by every session (`None` = off).
    pub lineage: Option<Arc<Lineage>>,
    /// Test hook: fail this step after the program started (bb7dc #544), or
    /// (`RootUnrecorded`) act as if the root were already gone when it is
    /// recorded (043dd #510 M1). Always `None` in aibd.
    #[doc(hidden)]
    pub inject: Option<Inject>,
}

/// A step of [`start_tree`] after `spawn()` succeeded that a test can make fail.
#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Inject {
    RootWait,
    System,
    ForkWatch,
    /// Not a failure: the root lookup at record time finds nothing, as when
    /// a CLI exits before it could be recorded (the race itself cannot be
    /// made to happen on demand).
    RootUnrecorded,
}

fn injected(spec: &SpawnSpec, at: Inject) -> std::io::Result<()> {
    if spec.inject == Some(at) {
        return Err(std::io::Error::other("injected"));
    }
    Ok(())
}

/// [`start_tree`] failed after the program had started (bb7dc #544 High):
/// something may still be running. The tree is always handed back under
/// control (a [`TreeHandle`]: every later signal goes through the H1
/// checks), so the caller can stop it and must not call it "not started"
/// unless that stop releases a slot.
pub struct StartFailed {
    pub err: std::io::Error,
    pub handle: Box<TreeHandle>,
}

impl StartFailed {
    pub fn root(&self) -> u32 {
        self.handle.root
    }
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
    /// First recorded through M1 lineage (re-parented, creator recorded).
    pub found_by_lineage: Vec<u32>,
    /// Lineage was off or a pass was incomplete: a missing survivor cannot
    /// be ruled out, so the result is never `clean`.
    pub lineage_unavailable: bool,
    /// The platform can prove containment (Windows J0 + session Job). Never
    /// on macOS / Linux today, so a cancel there is at best `unverified`
    /// with reason `observed_clean` (DECISION-23).
    pub containment_proven: bool,
    /// DECISION-23 (c)/(d): same-uid processes with the "re-parented, then
    /// exec'd" signature that lineage cannot place.
    pub candidates: usize,
    /// Candidates whose environment could not be read (or was empty): they
    /// might be ours, so the cancel cannot be `observed_clean`. Never signalled.
    pub candidates_unreadable: Vec<u32>,
    /// The candidate list itself was read in full.
    pub candidates_complete: bool,
    /// Misses a layer knows about (codes, sorted): `root_unrecorded` and the
    /// fork watcher's `fork_watch_*`. Any of them = never `observed_clean`.
    pub known_gaps: Vec<&'static str>,
    pub exit_code: Option<i32>,
}

/// The root was gone before its start time could be recorded: nothing it
/// started can be followed.
pub const GAP_ROOT_UNRECORDED: &str = "root_unrecorded";

/// bb7dc H1 (11-09): the root could not be kept unreaped until the last
/// signal (M5 said it is not our child any more: reaped elsewhere, or
/// `SIGCHLD` auto-reaps). Latched: no group signal is sent after that.
pub const GAP_ROOT_IDENTITY_LOST: &str = "root_identity_lost";

/// How the root ended, read without reaping it (macOS: M5 `WNOWAIT`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RootEnd {
    pub code: Option<i32>,
    /// POSIX signal number that ended it.
    pub signal: Option<i32>,
}

/// Blind spot named in every report without lineage.
pub const BLIND_SPOT_REPARENTED: &str = "reparented_after_intermediate_exit";
/// DECISION-23: what no layer on macOS can rule out, named in every report
/// without proven containment. `env_cleared` covers `env -i` dropping the
/// marker; `uid_changed` a child that switched account (sudo / setuid): the
/// candidate check only looks at our own uid (1ced7, 11-08).
pub const BLIND_SPOTS_UNCONTAINED: [&str; 4] = [
    "exec_after_creator_exit",
    "env_cleared",
    "fork_watch_miss",
    "uid_changed",
];

impl Report {
    /// No recorded process is still running. NOT "clean" (043dd #510 M3):
    /// see [`Report::clean_status`] / [`Report::tree_status`].
    pub fn no_recorded_survivors(&self) -> bool {
        self.survivors.is_empty()
    }

    /// The three-way result for `aib_core::outcome::Observed::tree`.
    pub fn tree_status(&self) -> aib_core::outcome::Tree {
        use aib_core::outcome::Tree;
        match self.clean_status() {
            "clean" => Tree::Clean,
            "tree_not_clean" => Tree::NotClean,
            _ => Tree::Unverified(self.unverified_reason().unwrap_or("unverified")),
        }
    }

    /// What a cancel may claim (DECISION-23): `tree_not_clean` (a recorded
    /// or marked process still runs), `clean` only with proven containment
    /// and complete lineage, otherwise `unverified` (see
    /// [`Report::unverified_reason`]).
    pub fn clean_status(&self) -> &'static str {
        if !self.survivors.is_empty() {
            "tree_not_clean"
        } else if self.containment_proven && !self.lineage_unavailable {
            "clean"
        } else {
            "unverified"
        }
    }

    /// Why a cancel is `unverified` (a code, not user text): `observed_clean`
    /// = (a)-(d) all hold, nothing left within what can be seen.
    pub fn unverified_reason(&self) -> Option<&'static str> {
        if self.clean_status() != "unverified" {
            None
        } else if self.lineage_unavailable {
            Some("lineage_unavailable")
        } else if let Some(g) = self.known_gaps.first() {
            Some(*g)
        } else if !self.candidates_complete {
            Some("candidates_incomplete")
        } else if !self.candidates_unreadable.is_empty() {
            Some("candidates_unreadable")
        } else {
            Some("observed_clean")
        }
    }

    pub fn blind_spots(&self) -> Vec<&'static str> {
        let mut v = Vec::new();
        if self.lineage_unavailable {
            v.push(BLIND_SPOT_REPARENTED);
        }
        if !self.containment_proven {
            v.extend(BLIND_SPOTS_UNCONTAINED);
        }
        v
    }
}

#[derive(Default)]
struct CandidateCheck {
    count: usize,
    complete: bool,
    unreadable: Vec<u32>,
    marked_alive: Vec<u32>,
}

pub struct TreeHandle {
    session_id: String,
    stdout: Option<ChildStdout>,
    /// Held (not read yet) so the CLI never gets EPIPE on stderr.
    _stderr: Option<ChildStderr>,
    exit: Arc<RootExit>,
    root: u32,
    /// The root's `Child`, kept unreaped until the last signal is sent (H1;
    /// macOS). `None` once reaped, or where the wait thread owns it.
    child: Option<Child>,
    /// No root-exit thread could be started (a failed start, bb7dc #544):
    /// nobody sets [`RootExit`], so the handle reaps the root itself after
    /// its last signal and on drop.
    own_wait: bool,
    /// H1 latch: the root stopped being provably our unreaped child.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    root_lost: bool,
    known: BTreeMap<u32, u64>,
    found_by_marker: Vec<u32>,
    found_by_watch: Vec<u32>,
    found_by_lineage: Vec<u32>,
    lineage: Option<Arc<Lineage>>,
    /// unique ids read while each process was alive (lineage on only)
    uniques: BTreeMap<u32, u64>,
    lineage_incomplete: bool,
    /// Misses known to this handle (merged with the fork watcher's).
    gaps: BTreeSet<&'static str>,
    /// M1 reads of processes that are not ours, between passes (M4).
    m1_cache: M1Cache,
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

/// [`running`] from a fresh read of `pid` alone.
fn running_now(pid: u32, start: u64) -> bool {
    let Ok(mut one) = new_system() else {
        return false; // cannot check: do not send
    };
    one.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[Pid::from_u32(pid)]),
        true,
        ProcessRefreshKind::nothing().without_tasks(),
    );
    running(&one, pid, start)
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

/// Exit status of the CLI itself, set by a thread blocked in `wait()`: no
/// polling, and our direct child is reaped the moment it exits.
#[derive(Default)]
struct RootExit {
    status: Mutex<Option<RootEnd>>,
    /// The wait thread could not keep the root unreaped (H1).
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    lost: std::sync::atomic::AtomicBool,
    cv: Condvar,
}

impl RootExit {
    fn set(&self, end: RootEnd) {
        *self.status.lock().unwrap_or_else(|e| e.into_inner()) = Some(end);
        self.cv.notify_all();
    }

    fn get(&self) -> Option<RootEnd> {
        *self.status.lock().unwrap_or_else(|e| e.into_inner())
    }

    // Waits for the root's exit event, bounded by the caller's timeout: one
    // wake-up on the event (or at the deadline), not a periodic timer (ADR 0005).
    #[allow(clippy::disallowed_methods)]
    fn wait(&self, timeout: Duration) -> Option<RootEnd> {
        let g = self.status.lock().unwrap_or_else(|e| e.into_inner());
        let (g, _) = self
            .cv
            .wait_timeout_while(g, timeout, |s| s.is_none())
            .unwrap_or_else(|e| e.into_inner());
        *g
    }
}

/// Where a signal goes: the root's group, or one recorded `(pid, start)`.
#[derive(Clone, Copy)]
enum To {
    Group,
    Pid(u32, u64),
}

pub fn spawn_tree(spec: &SpawnSpec) -> std::io::Result<TreeHandle> {
    start_tree(spec).map_err(|f| match f {
        StartFailedOr::NotStarted(e) => e,
        StartFailedOr::Started(mut f) => {
            let _ = f.handle.cleanup(Duration::from_secs(2));
            f.err
        }
    })
}

/// [`spawn_tree`], but a failure after the program started hands back what
/// may still be running ([`StartFailed`]) instead of dropping it. A failure
/// before it started is `left: None` via the plain error.
pub fn start_tree(spec: &SpawnSpec) -> Result<TreeHandle, StartFailedOr> {
    let mut c = Command::new(&spec.program);
    if spec.env_clear {
        c.env_clear();
    }
    c.args(&spec.args)
        .env(SESSION_ENV, &spec.session_id)
        .stdin(Stdio::null());
    if spec.discard_output {
        c.stdout(Stdio::null()).stderr(Stdio::null());
    } else {
        c.stdout(Stdio::piped()).stderr(Stdio::piped());
    }
    for (k, v) in &spec.env {
        c.env(k, v);
    }
    if let Some(d) = &spec.cwd {
        c.current_dir(d);
    }
    imp::new_group(&mut c);
    let mut child = c.spawn().map_err(StartFailedOr::NotStarted)?;
    let root = child.id();
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let exit = Arc::new(RootExit::default());
    let waited = match injected(spec, Inject::RootWait) {
        Ok(()) => start_root_wait_owned(child, root, exit.clone()),
        Err(e) => Err((e, Some(child))),
    };
    // bb7dc #544: no root-exit thread is no reason to drop the tree. macOS
    // gets the `Child` back (still unreaped, so every H1 check works) and
    // the handle reaps it itself after its last signal (`own_wait`).
    let (child, wait_err) = match waited {
        Ok(c) => (c, None),
        Err((e, c)) => (c, Some(e)),
    };
    let mut h = TreeHandle {
        session_id: spec.session_id.clone(),
        stdout,
        _stderr: stderr,
        exit,
        root,
        child,
        own_wait: wait_err.is_some(),
        root_lost: false,
        known: BTreeMap::new(),
        found_by_marker: Vec::new(),
        found_by_watch: Vec::new(),
        found_by_lineage: Vec::new(),
        lineage: spec.lineage.clone().filter(|l| l.enabled()),
        uniques: BTreeMap::new(),
        lineage_incomplete: false,
        gaps: BTreeSet::new(),
        m1_cache: M1Cache::default(),
        #[cfg(target_os = "macos")]
        watch: None,
    };
    if let Some(u) = h.lineage.as_ref().and_then(|l| l.unique_of(root)) {
        h.uniques.insert(root, u);
    }
    let fail = |h: TreeHandle, err| {
        StartFailedOr::Started(StartFailed {
            err,
            handle: Box::new(h),
        })
    };
    let mut sys = match injected(spec, Inject::System).and_then(|()| new_system()) {
        Ok(s) => s,
        Err(e) => {
            // nothing is recorded yet, so a later stop would see nothing to
            // signal: kill the group now, through the H1 check, and wait for
            // the root (bb7dc #544)
            if h.kill_group_unrecorded() {
                let _ = h.exit.wait(Duration::from_secs(2));
            }
            return Err(fail(h, e));
        }
    };
    refresh(&mut sys, false);
    let recorded = sys
        .process(Pid::from_u32(root))
        .filter(|_| injected(spec, Inject::RootUnrecorded).is_ok());
    if let Some(p) = recorded {
        h.known.insert(root, p.start_time());
        #[cfg(target_os = "macos")]
        if spec.watch_forks {
            let w = injected(spec, Inject::ForkWatch).and_then(|()| {
                super::fork_watch::ForkWatch::start(root, p.start_time(), h.lineage.clone())
            });
            match w {
                Ok(w) => h.watch = Some(w),
                // 043dd #510 M2 / bb7dc #544: never leave it running with
                // nobody holding it; the caller stops it and checks
                Err(e) => return Err(fail(h, e)),
            }
        }
    } else {
        // 043dd #510 M1: gone before it could be recorded (a CLI that exits at once)
        h.gaps.insert(GAP_ROOT_UNRECORDED);
    }
    #[cfg(not(target_os = "macos"))]
    let _ = spec.watch_forks;
    if let Some(e) = wait_err {
        return Err(fail(h, e));
    }
    Ok(h)
}

/// Why [`start_tree`] failed: before the program started (nothing to stop),
/// or after (see [`StartFailed`]).
pub enum StartFailedOr {
    NotStarted(std::io::Error),
    Started(StartFailed),
}

/// The root-exit thread. macOS: M5 `waitid(WNOWAIT)` sees the exit WITHOUT
/// reaping, so the root's pid (= the group number) cannot be reused until
/// [`TreeHandle`] reaps it after its last signal (bb7dc H1); the `Child`
/// stays with the handle. Elsewhere the thread reaps at once (the old way;
/// group signals then rely on a live member's identity only).
#[cfg(target_os = "macos")]
fn start_root_wait_owned(
    child: Child,
    root: u32,
    exit: Arc<RootExit>,
) -> Result<Option<Child>, (std::io::Error, Option<Child>)> {
    use aib_sys::{ChildExit, ExitState};
    let started = std::thread::Builder::new()
        .name("aibd-root-wait".into())
        .spawn(move || {
            let end = match aib_sys::Macos.wait_exit(root) {
                Ok(ExitState::Exited(c)) => RootEnd {
                    code: Some(c),
                    signal: None,
                },
                Ok(ExitState::Signaled(n)) => RootEnd {
                    code: None,
                    signal: Some(n),
                },
                Ok(ExitState::Running) | Err(_) => {
                    // not our unreaped child any more: say so, never guess a status
                    exit.lost.store(true, std::sync::atomic::Ordering::SeqCst);
                    RootEnd {
                        code: None,
                        signal: None,
                    }
                }
            };
            exit.set(end);
        });
    match started {
        Ok(_) => Ok(Some(child)),
        Err(e) => Err((e, Some(child))),
    }
}

#[cfg(not(target_os = "macos"))]
fn start_root_wait_owned(
    mut child: Child,
    _root: u32,
    exit: Arc<RootExit>,
) -> Result<Option<Child>, (std::io::Error, Option<Child>)> {
    // the closure owns the child: if the thread cannot start it is dropped
    // unreaped (a drop never waits), so the group number stays ours
    std::thread::Builder::new()
        .name("aibd-root-wait".into())
        .spawn(move || {
            let end = match child.wait() {
                Ok(st) => root_end_of(&st),
                Err(_) => RootEnd {
                    code: None,
                    signal: None,
                },
            };
            exit.set(end);
        })
        .map_err(|e| (e, None))?;
    Ok(None)
}

#[cfg(not(target_os = "macos"))]
fn root_end_of(st: &std::process::ExitStatus) -> RootEnd {
    root_end_of_status(st)
}

/// Waits for the root's exit without holding the [`TreeHandle`] (so a cancel
/// can run meanwhile). Woken by the root-wait thread, not by polling.
#[derive(Clone)]
pub struct RootWatch(Arc<RootExit>);

impl RootWatch {
    /// The root's exit once it has exited, or `None` at the timeout.
    pub fn wait(&self, timeout: Duration) -> Option<RootEnd> {
        self.0.wait(timeout)
    }
}

impl TreeHandle {
    pub fn root_watch(&self) -> RootWatch {
        RootWatch(self.exit.clone())
    }

    pub fn root_pid(&self) -> u32 {
        self.root
    }

    /// The root's recorded start time (with its pid: its identity), if it
    /// was recorded.
    pub fn root_start(&self) -> Option<u64> {
        self.known.get(&self.root).copied()
    }

    pub fn take_stdout(&mut self) -> Option<ChildStdout> {
        self.stdout.take()
    }

    /// Test hook: record a process as if a scan had found it.
    #[doc(hidden)]
    pub fn record_for_test(&mut self, pid: u32, start_time: u64) {
        self.known.insert(pid, start_time);
    }

    /// Test hook: what the fork watcher holds right now, without merging it.
    #[doc(hidden)]
    pub fn watched_for_test(&self) -> Vec<u32> {
        #[cfg(target_os = "macos")]
        if let Some(w) = &self.watch {
            return w.snapshot().into_iter().map(|(p, _)| p).collect();
        }
        Vec::new()
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
            for (pid, u) in w.uniques() {
                self.uniques.entry(pid).or_insert(u);
            }
            self.gaps.extend(w.gaps());
        }
    }

    /// Read `pid`'s unique id the moment it is recorded, while it is alive
    /// (043dd #510 H2: a scan used to leave this to the next lineage pass,
    /// by which time the process could be gone).
    fn note_unique(&mut self, pid: u32) {
        if let Some(u) = self.lineage.as_ref().and_then(|l| l.unique_of(pid)) {
            self.uniques.entry(pid).or_insert(u);
        }
    }

    /// The one place this handle sends a signal (a63ab 11-09: every path
    /// goes through the same identity check, right before the send). A group
    /// signal only while the group number is provably ours
    /// ([`Self::group_is_ours`]); a single pid only while it still runs with
    /// its recorded start time and unique id. Returns whether it was sent.
    fn signal_checked(
        &mut self,
        sys: &System,
        alive: &[(u32, u64)],
        to: To,
        sig: imp::Sig,
    ) -> bool {
        match to {
            To::Group => {
                if !self.group_is_ours(sys, alive) {
                    return false;
                }
                imp::signal_group(self.root, sig);
            }
            To::Pid(pid, start) => {
                // read this one pid again now, not from the pass's snapshot
                // (`alive` came from it already): the gap to the send is
                // this read, not the whole pass
                if !(running_now(pid, start) && self.unique_ok(pid)) {
                    return false;
                }
                imp::signal_pid(pid, sig);
            }
        }
        true
    }

    /// May `killpg(root)` be sent? Only while the group number is provably
    /// still this session's (see the module comment). `alive` = recorded
    /// processes believed alive.
    fn group_is_ours(&mut self, sys: &System, alive: &[(u32, u64)]) -> bool {
        #[cfg(target_os = "macos")]
        {
            let _ = (sys, alive);
            self.root_is_our_unreaped_child()
        }
        #[cfg(not(target_os = "macos"))]
        self.group_is_ours_by_members(sys, alive)
    }

    /// H1: the root is our child and not reaped (running or a zombie),
    /// checked right now: its pid, and so the group number, cannot have
    /// been reused. Once that fails, latched: no more group signals from
    /// this handle.
    #[cfg(target_os = "macos")]
    fn root_is_our_unreaped_child(&mut self) -> bool {
        if self.root_lost || self.child.is_none() {
            return false;
        }
        use aib_sys::ChildExit;
        if self.exit.lost.load(std::sync::atomic::Ordering::SeqCst)
            || aib_sys::Macos.peek(self.root).is_err()
        {
            self.root_lost = true;
            self.gaps.insert(GAP_ROOT_IDENTITY_LOST);
            return false;
        }
        true
    }

    /// A start failed before anything was recorded (no process table yet):
    /// SIGKILL the group if that is provably safe. macOS: the H1 check.
    /// Elsewhere only while the root-exit thread has not seen (and so not
    /// reaped) the root: best effort, not an H1 guarantee.
    fn kill_group_unrecorded(&mut self) -> bool {
        #[cfg(target_os = "macos")]
        let ours = self.root_is_our_unreaped_child();
        #[cfg(not(target_os = "macos"))]
        let ours = self.exit.get().is_none();
        if ours {
            imp::signal_group(self.root, imp::Sig::Kill);
        }
        ours
    }

    #[cfg(not(target_os = "macos"))]
    fn group_is_ours_by_members(&self, sys: &System, alive: &[(u32, u64)]) -> bool {
        if self
            .known
            .get(&self.root)
            .is_some_and(|s| running(sys, self.root, *s))
        {
            return true;
        }
        alive.iter().any(|(pid, start)| {
            *pid != self.root
                && running(sys, *pid, *start)
                && self.unique_ok(*pid)
                && imp::pgid_of(*pid) == Some(self.root)
        })
    }

    /// M1 pass: add processes whose creator is recorded. True if any.
    fn lineage_pass(&mut self) -> bool {
        let Some(l) = self.lineage.clone() else {
            return false;
        };
        let a = l.adopt(
            &self.known,
            &mut self.uniques,
            std::process::id(),
            &mut self.m1_cache,
        );
        if !a.complete {
            self.lineage_incomplete = true;
        }
        for f in &a.found {
            if self.known.insert(f.pid, f.start).is_none() {
                self.found_by_lineage.push(f.pid);
            }
        }
        !a.found.is_empty()
    }

    /// Extra identity check for a process whose unique id was recorded.
    /// Only ever refuses; a process without a recorded id relies on the
    /// `(pid, start_time)` check alone, as before.
    fn unique_ok(&self, pid: u32) -> bool {
        match (self.lineage.as_ref(), self.uniques.get(&pid)) {
            (Some(l), Some(u)) => l.same_unique(pid, *u),
            _ => true,
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
        // start times from this refresh validate the M1 cache (M4)
        self.m1_cache.starts = sys
            .processes()
            .iter()
            .map(|(p, pr)| (p.as_u32(), pr.start_time()))
            .collect();
        self.ppid_scan(sys);
        if self.lineage_pass() {
            self.ppid_scan(sys);
        }
    }

    fn ppid_scan(&mut self, sys: &System) {
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
                            self.note_unique(pid);
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
    /// What the fork watcher has recorded is merged first, so the sweep only
    /// claims what no other layer found (043dd #510: with readable
    /// environments it took the watcher's finds as `found_by_marker`).
    pub fn sweep_marker(&mut self) -> std::io::Result<()> {
        self.absorb();
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
                self.note_unique(pid);
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
        let op = crate::timers::TREE_CANCEL_WAIT.begin(Some(Instant::now() + grace));
        // macOS (#514): block on NOTE_EXIT of what is still alive and read the
        // process table once per wake-up, not every 50 ms. Only when a process
        // cannot be watched does this call fall back to the tick.
        #[cfg(target_os = "macos")]
        let mut exits = if grace.is_zero() {
            None
        } else {
            super::exit_watch::ExitWatch::new().ok()
        };
        loop {
            let mut sys = new_system()?;
            refresh(&mut sys, false);
            self.scan_with(&sys);
            let alive = self.alive(&sys);
            if alive.is_empty() {
                return Ok(alive);
            }
            #[cfg(target_os = "macos")]
            if let Some(w) = exits.as_mut() {
                if w.watch(alive.iter().map(|(p, _)| *p)) {
                    match op.remaining() {
                        Some(left) if !left.is_zero() => {
                            w.wait(left);
                            op.woke();
                            continue;
                        }
                        _ => return Ok(alive),
                    }
                }
                exits = None; // could not watch one: tick for the rest of this wait
            }
            if !op.tick() {
                return Ok(alive);
            }
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
            let mut sys = new_system()?;
            refresh(&mut sys, false);
            if self.signal_checked(&sys, &alive, To::Group, imp::Sig::Interrupt) {
                steps.push(Step {
                    signal: "SIGINT(group)",
                    targets: vec![self.root],
                });
            }
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
            let mut sys = new_system()?;
            refresh(&mut sys, false);
            if self.signal_checked(&sys, &alive, To::Group, sig) {
                steps.push(Step {
                    signal: sig.group_name(),
                    targets: vec![self.root],
                });
            }
            let mut targets = Vec::new();
            for &(pid, start) in &alive {
                if self.signal_checked(&sys, &alive, To::Pid(pid, start), sig) {
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
        let exit_code = self.exit.get().and_then(|s| s.code);
        // H1: every signal of this stop has been sent; only now may the root
        // be reaped (and its pid / group number become reusable)
        self.reap_root();
        let _ = self.sweep_marker();
        let mut survivors: Vec<u32> = self
            .wait_gone(Duration::ZERO)?
            .into_iter()
            .map(|(p, _)| p)
            .collect();
        let check = self.check_candidates();
        survivors.extend(&check.marked_alive);
        Ok(Report {
            steps,
            survivors,
            tree_size: self.known.len(),
            found_by_marker: self.found_by_marker.clone(),
            found_by_watch: self.found_by_watch.clone(),
            forks_seen: self.forks_seen(),
            found_by_lineage: self.found_by_lineage.clone(),
            lineage_unavailable: self.lineage.is_none() || self.lineage_incomplete,
            containment_proven: false,
            candidates: check.count,
            candidates_unreadable: check.unreadable,
            candidates_complete: check.complete,
            known_gaps: self.gaps.iter().copied().collect(),
            exit_code,
        })
    }

    /// DECISION-23 (c)/(d), run only at the end of a cancel / cleanup (never in
    /// the idle loop): read each candidate's environment once. Our marker on a
    /// live one = ours and still running; an unreadable or empty environment =
    /// cannot be ruled out. Nothing here is ever signalled.
    fn check_candidates(&mut self) -> CandidateCheck {
        let (Some(l), Some(&root_unique)) = (self.lineage.clone(), self.uniques.get(&self.root))
        else {
            return CandidateCheck::default();
        };
        let c = l.late_exec_candidates(root_unique, std::process::id(), &self.known);
        let mut out = CandidateCheck {
            count: c.pids.len(),
            complete: c.complete,
            ..CandidateCheck::default()
        };
        if c.pids.is_empty() {
            return out;
        }
        let Ok(mut sys) = new_system() else {
            out.complete = false;
            return out;
        };
        refresh(&mut sys, true);
        let want = format!("{SESSION_ENV}={}", self.session_id);
        for pid in c.pids {
            match sys.process(Pid::from_u32(pid)) {
                None => {} // gone since the list was read
                Some(p) if p.environ().is_empty() => out.unreadable.push(pid),
                Some(p)
                    if p.environ()
                        .iter()
                        .any(|e| e.to_str() == Some(want.as_str())) =>
                {
                    self.known.insert(pid, p.start_time());
                    self.found_by_marker.push(pid);
                    out.marked_alive.push(pid);
                }
                Some(_) => {} // readable, no marker: not this session's
            }
        }
        out
    }

    /// Wait for the CLI itself to exit (it may leave descendants running).
    pub fn wait_root(&mut self, timeout: Duration) -> std::io::Result<Option<i32>> {
        Ok(self.exit.wait(timeout).map(|s| s.code.unwrap_or(-1)))
    }

    /// The root's exit, if it has exited (never blocks, never reaps).
    pub fn root_end(&self) -> Option<RootEnd> {
        self.exit.get()
    }

    /// Reap the root if it has exited. Called after the last signal of a
    /// stop; afterwards no group signal is sent from this handle.
    fn reap_root(&mut self) {
        if self.own_wait {
            self.reap_own();
            return;
        }
        if self.exit.get().is_none() {
            return; // still running: keep it (a later stop may still need it)
        }
        if let Some(mut c) = self.child.take() {
            if !matches!(c.try_wait(), Ok(Some(_))) {
                // exited per M5 but not reapable now: keep trying on drop
                self.child = Some(c);
            }
        }
    }
}

impl TreeHandle {
    /// `own_wait`: reap the root if it has exited (never blocks), and record
    /// its end the way the root-exit thread would have.
    fn reap_own(&mut self) {
        if let Some(mut c) = self.child.take() {
            match c.try_wait() {
                Ok(Some(st)) => self.exit.set(root_end_of_status(&st)),
                _ => self.child = Some(c),
            }
        }
    }
}

fn root_end_of_status(st: &std::process::ExitStatus) -> RootEnd {
    #[cfg(unix)]
    let signal = std::os::unix::process::ExitStatusExt::signal(st);
    #[cfg(not(unix))]
    let signal = None;
    RootEnd {
        code: st.code(),
        signal,
    }
}

impl Drop for TreeHandle {
    /// Never leave our exited root as a zombie for aibd's lifetime. A root
    /// still running is left alone (dropping a handle is not a stop).
    fn drop(&mut self) {
        if self.own_wait {
            self.reap_own();
            return;
        }
        if self.exit.get().is_some() {
            if let Some(c) = self.child.as_mut() {
                let _ = c.try_wait();
            }
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
        pub fn group_name(self) -> &'static str {
            match self {
                Sig::Interrupt => "SIGINT(group)",
                Sig::Terminate => "SIGTERM(group)",
                Sig::Kill => "SIGKILL(group)",
            }
        }
    }

    pub fn new_group(c: &mut std::process::Command) {
        c.process_group(0);
    }

    /// Process group of `pid`, `None` if it cannot be read. macOS no longer
    /// needs it (H1 keeps the root unreaped instead of trusting a member).
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    pub fn pgid_of(pid: u32) -> Option<u32> {
        let p = i32::try_from(pid).ok()?;
        nix::unistd::getpgid(Some(Pid::from_raw(p)))
            .ok()
            .and_then(|g| u32::try_from(g.as_raw()).ok())
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
        pub fn group_name(self) -> &'static str {
            self.name()
        }
    }

    pub fn new_group(_c: &mut std::process::Command) {}
    pub fn pgid_of(_pid: u32) -> Option<u32> {
        None
    }
    pub fn signal_group(_leader: u32, _s: Sig) {}
    pub fn signal_pid(_pid: u32, _s: Sig) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rep() -> Report {
        Report {
            candidates_complete: true,
            ..Report::default()
        }
    }

    /// DECISION-23: without proven containment the best a cancel can say is
    /// `unverified` / `observed_clean`; survivors win over everything.
    #[test]
    fn a_known_gap_is_never_observed_clean_and_comes_after_lineage() {
        // 043dd #510 H1 / M1: a miss some layer knows about names itself
        let r = Report {
            known_gaps: vec!["fork_watch_stopped", GAP_ROOT_UNRECORDED],
            ..rep()
        };
        assert_eq!(r.clean_status(), "unverified");
        assert_eq!(r.unverified_reason(), Some("fork_watch_stopped"));
        assert_eq!(
            r.tree_status(),
            aib_core::outcome::Tree::Unverified("fork_watch_stopped")
        );
        let r = Report {
            known_gaps: vec![GAP_ROOT_UNRECORDED],
            lineage_unavailable: true,
            ..rep()
        };
        assert_eq!(r.unverified_reason(), Some("lineage_unavailable"));
        let r = Report {
            known_gaps: vec![GAP_ROOT_UNRECORDED],
            survivors: vec![7],
            ..rep()
        };
        assert_eq!(r.tree_status(), aib_core::outcome::Tree::NotClean);
        assert_eq!(
            rep().tree_status(),
            aib_core::outcome::Tree::Unverified("observed_clean")
        );
    }

    #[test]
    fn decision_23_status_reason_and_blind_spots() {
        let r = rep();
        assert_eq!(r.clean_status(), "unverified");
        assert_eq!(r.unverified_reason(), Some("observed_clean"));
        assert_eq!(r.blind_spots(), BLIND_SPOTS_UNCONTAINED.to_vec());

        let r = Report {
            candidates_unreadable: vec![9],
            ..rep()
        };
        assert_eq!(r.unverified_reason(), Some("candidates_unreadable"));
        let r = Report {
            candidates_complete: false,
            ..rep()
        };
        assert_eq!(r.unverified_reason(), Some("candidates_incomplete"));
        let r = Report {
            lineage_unavailable: true,
            ..rep()
        };
        assert_eq!(r.unverified_reason(), Some("lineage_unavailable"));
        assert_eq!(r.blind_spots()[0], BLIND_SPOT_REPARENTED);

        let r = Report {
            survivors: vec![7],
            containment_proven: true,
            ..rep()
        };
        assert_eq!(r.clean_status(), "tree_not_clean");
        assert_eq!(r.unverified_reason(), None);

        // only proven containment with complete lineage may say clean
        let r = Report {
            containment_proven: true,
            ..rep()
        };
        assert_eq!(r.clean_status(), "clean");
        assert!(r.blind_spots().is_empty());
        let r = Report {
            containment_proven: true,
            lineage_unavailable: true,
            ..rep()
        };
        assert_eq!(r.clean_status(), "unverified");
    }
}
