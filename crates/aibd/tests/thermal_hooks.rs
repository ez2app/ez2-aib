#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! The thermal guard's hooks in the sessions (R1 PR 3a, a9136 11-10 15:18):
//! `on_running_changed` fires once per change of the live count, and a NOGO
//! refuses new POSTs (`thermal_nogo`, not queued), holds the queue, and only
//! `kick_now` makes the queue move again once it ends. POSIX only
//! (`/bin/sleep`); every process is collected by aibd's own stop.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::sessions::{Hooks, Request, Sessions};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn sessions(dir: &std::path::Path, max: usize) -> Arc<Sessions> {
    let store = Arc::new(aib_store::Store::open(&dir.join("db.sqlite")).unwrap());
    Sessions::new(
        store,
        None,
        aibd::platform::session_unix::spawn,
        max,
        dir.to_path_buf(),
    )
}

fn sleeper() -> Request {
    Request {
        argv: vec!["/bin/sleep".into(), "30".into()],
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
    assert_eq!(state(&s, &b.id), "pending", "the queue started one while NOGO");

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
