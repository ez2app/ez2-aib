#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! The thermal guard's hooks in the sessions (R1 PR 3a, a9136 11-10 15:18):
//! `on_running_changed` fires once per change of the live count, and a NOGO
//! refuses new POSTs (`thermal_nogo`, not queued), holds the queue, and only
//! `kick_now` makes the queue move again once it ends. The sessions are a
//! fake that starts no process and whose stop is clean, so the slot comes
//! back on every machine (with no lineage a real stop keeps it).
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::sessions::{Hooks, Request, Sessions};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Never exits by itself; its stop is clean. Touches no process.
struct Quiet;

struct NeverExits;

impl aibd::platform::session::ExitWait for NeverExits {
    fn wait(&self, t: Duration) -> Option<aibd::platform::session::Exit> {
        std::thread::sleep(t.min(Duration::from_millis(50)));
        None
    }
}

impl aibd::platform::session::Running for Quiet {
    fn pid(&self) -> u32 {
        999_996
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> Arc<dyn aibd::platform::session::ExitWait> {
        Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{CandidateDiag, Stop, StopStatus};
        Stop {
            status: StopStatus::Clean,
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
            candidates: CandidateDiag::default(),
        }
    }
}

fn quiet_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Quiet))
}

fn sessions(dir: &std::path::Path, max: usize) -> Arc<Sessions> {
    let store = Arc::new(aib_store::Store::open(&dir.join("db.sqlite")).unwrap());
    Sessions::new(store, None, quiet_spawn, max, dir.to_path_buf())
}

fn sleeper() -> Request {
    Request {
        argv: vec!["/fake/quiet".into()],
        cwd: None,
        env: vec![],
    }
}

fn wait_until(d: Duration, mut f: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + d;
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    f()
}

fn state(s: &Sessions, id: &str) -> &'static str {
    s.get(id).map(|x| x.state).unwrap_or("gone")
}

#[test]
fn running_count_changes_reach_the_hook_once_each() {
    let tmp = tempfile::tempdir().unwrap();
    let s = sessions(tmp.path(), 2);
    let seen: Arc<Mutex<Vec<(usize, usize)>>> = Arc::default();
    let seen2 = seen.clone();
    assert!(s.set_hooks(Hooks {
        on_running_changed: Arc::new(move |a, b| seen2.lock().unwrap().push((a, b))),
        ..Hooks::default()
    }));
    assert!(!s.set_hooks(Hooks::default()), "hooks are set once");
    let a = s.start(sleeper()).unwrap();
    assert_eq!(a.state, "running", "{a:?}");
    assert_eq!(*seen.lock().unwrap(), vec![(0, 1)]);
    let a = s.stop(&a.id, Duration::from_secs(5)).unwrap();
    assert!(!a.holds_slot, "{a:?}");
    assert_eq!(
        *seen.lock().unwrap(),
        vec![(0, 1), (1, 0)],
        "one call per change, none for an unchanged count"
    );
}

/// a9136 3a L1: hooks set while a session already runs hear (0, 1) at once.
#[test]
fn hooks_set_late_hear_the_current_count() {
    let tmp = tempfile::tempdir().unwrap();
    let s = sessions(tmp.path(), 2);
    let a = s.start(sleeper()).unwrap();
    assert_eq!(a.state, "running", "{a:?}");
    let seen: Arc<Mutex<Vec<(usize, usize)>>> = Arc::default();
    let seen2 = seen.clone();
    assert!(s.set_hooks(Hooks {
        on_running_changed: Arc::new(move |a, b| seen2.lock().unwrap().push((a, b))),
        ..Hooks::default()
    }));
    assert_eq!(
        *seen.lock().unwrap(),
        vec![(0, 1)],
        "a late guard missed 0→1"
    );
    s.stop(&a.id, Duration::from_secs(5)).unwrap();
    assert_eq!(*seen.lock().unwrap(), vec![(0, 1), (1, 0)]);
}

#[test]
fn a_nogo_refuses_posts_holds_the_queue_and_kick_now_resumes_it() {
    let tmp = tempfile::tempdir().unwrap();
    let s = sessions(tmp.path(), 1);
    let nogo = Arc::new(AtomicBool::new(false));
    let n2 = nogo.clone();
    assert!(s.set_hooks(Hooks {
        launch_allowed: Arc::new(move || !n2.load(Ordering::SeqCst)),
        ..Hooks::default()
    }));
    let a = s.start(sleeper()).unwrap();
    assert_eq!(a.state, "running", "{a:?}");
    let b = s.start(sleeper()).unwrap();
    assert_eq!(b.state, "pending", "the limit is full: queued {b:?}");

    nogo.store(true, Ordering::SeqCst);
    let c = s.start(sleeper()).unwrap_err();
    assert_eq!(c.0, aib_core::ErrorCode::ThermalNogo, "{c:?}");
    assert_eq!(c.0.http_status(), 503);
    assert_eq!(s.queued(), 1, "a NOGO POST is not queued");

    // the slot comes back, but the queue must hold while it is hot
    s.stop(&a.id, Duration::from_secs(5)).unwrap();
    std::thread::sleep(Duration::from_millis(800));
    assert_eq!(
        state(&s, &b.id),
        "pending",
        "the queue started one while NOGO"
    );

    // NOGO ends: nothing changes until the guard kicks
    nogo.store(false, Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(state(&s, &b.id), "pending");
    s.kick_now();
    assert!(
        wait_until(Duration::from_secs(10), || state(&s, &b.id) == "running"),
        "kick_now did not start the waiting session: {}",
        state(&s, &b.id)
    );
    s.stop(&b.id, Duration::from_secs(5)).unwrap();
}
