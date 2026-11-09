//! Three-state ExitWait (a9136; 1ced7 #543 Medium 1 + 2, a63ab rulings):
//! a root-exit wait that cannot look says so (`Waited::Failed`), is never
//! taken as an exit, and the session's watcher backs off instead of spinning
//! on it, on every platform (the back-off lives in `Sessions::watch_root`,
//! not in one platform's wait). Fake trees only: nothing here touches the OS,
//! so it runs on Windows too. Its own test binary (the timer counters only
//! see these tests).
// test code: a panic is the failure report
#![allow(clippy::unwrap_used)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::platform::session::{Exit, ExitWait, Running, SpawnFail, Spec, Stop, StopStatus, Waited};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Answers `Failed` at once for the first `fail_for` calls, then `then`.
struct Answers {
    calls: &'static AtomicUsize,
    fail_for: usize,
    then: Waited,
}

impl ExitWait for Answers {
    fn wait(&self, _: Duration) -> Waited {
        let n = self.calls.fetch_add(1, Ordering::SeqCst);
        if n < self.fail_for {
            Waited::Failed("wait_failed")
        } else {
            self.then.clone()
        }
    }
}

struct Fake(Arc<Answers>);

impl Running for Fake {
    fn pid(&self) -> u32 {
        999_997
    }
    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        self.0.clone()
    }
    fn stop(&mut self, _: Duration) -> Stop {
        Stop {
            status: StopStatus::Clean,
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
        }
    }
}

/// The next spawn's answers (a `SpawnFn` is a plain fn, so it reads them here).
static NEXT: Mutex<Option<Arc<Answers>>> = Mutex::new(None);
/// One test at a time: they share `NEXT` and the timer counter.
static SERIAL: Mutex<()> = Mutex::new(());

fn spawn(_: &Spec, _: Option<Arc<aibd::lineage::Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
    Ok(Box::new(Fake(NEXT.lock().unwrap().take().unwrap())))
}

fn start(a: Answers) -> (tempfile::TempDir, Arc<aibd::sessions::Sessions>, String) {
    *NEXT.lock().unwrap() = Some(Arc::new(a));
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(store, None, spawn, 1, tmp.path().to_path_buf());
    let rec = s
        .start(aibd::sessions::Request {
            argv: vec!["fake".into()],
            cwd: None,
            env: vec![],
        })
        .unwrap();
    (tmp, s, rec.id)
}

fn wait_until(d: Duration, mut f: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + d;
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    f()
}

/// A wait that fails at once on every call: without a back-off the watcher
/// would call it (and take the sessions lock) millions of times a second.
/// 100 ms doubling ⇒ about 5 calls in 1.6 s. Not an exit: still running.
/// A DELETE then ends the watcher at once, mid-pause.
#[test]
fn a_wait_that_keeps_failing_is_backed_off_not_spun_and_is_never_an_exit() {
    let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    let (_tmp, s, id) = start(Answers {
        calls: &CALLS,
        fail_for: usize::MAX,
        then: Waited::TimedOut,
    });
    std::thread::sleep(Duration::from_millis(1600));
    let calls = CALLS.load(Ordering::SeqCst);
    eprintln!("failing wait: {calls} calls in 1.6 s");
    assert!((2..=8).contains(&calls), "{calls} calls in 1.6 s");
    assert_eq!(
        s.get(&id).unwrap().state,
        "running",
        "a failed wait is not an exit"
    );

    let paused = aibd::timers::SESSION_ROOT_WATCH_BACKOFF.active();
    assert!(paused >= 1, "the watcher should be in its pause");
    let t = Instant::now();
    assert_eq!(s.stop(&id, Duration::ZERO).unwrap().state, "killed");
    assert!(
        wait_until(Duration::from_secs(2), || {
            aibd::timers::SESSION_ROOT_WATCH_BACKOFF.active() < paused
        }),
        "the paused watcher outlived its session"
    );
    // the pause at this point is 1.6 s or more: woken by the end, not the clock
    assert!(
        t.elapsed() < Duration::from_millis(1200),
        "{:?}",
        t.elapsed()
    );
}

/// A "timeout" that comes back at once is no blocking wait either: the
/// same back-off (043dd's Windows wait used to return at once like this).
#[test]
fn a_timeout_that_comes_back_at_once_is_backed_off_too() {
    let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    let (_tmp, s, id) = start(Answers {
        calls: &CALLS,
        fail_for: 0,
        then: Waited::TimedOut,
    });
    std::thread::sleep(Duration::from_millis(1600));
    let calls = CALLS.load(Ordering::SeqCst);
    eprintln!("instant timeout: {calls} calls in 1.6 s");
    assert!((2..=8).contains(&calls), "{calls} calls in 1.6 s");
    let _ = s.stop(&id, Duration::ZERO);
}

/// Failures, then a real exit: the back-off does not hide the exit, and
/// the session ends by `root_exit`.
#[test]
fn an_exit_after_failed_waits_still_ends_the_session() {
    let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    let (_tmp, s, id) = start(Answers {
        calls: &CALLS,
        fail_for: 2,
        then: Waited::Exited(Exit::Code(0)),
    });
    assert!(
        wait_until(Duration::from_secs(3), || s.get(&id).unwrap().state
            == "exited"),
        "{:?}",
        s.get(&id)
    );
    assert_eq!(s.get(&id).unwrap().ended_by, Some("root_exit"));
}
