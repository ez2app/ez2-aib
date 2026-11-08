//! bb7dc #544 c5603b91 point 2: a start that fails while aibd is shutting
//! down adds its leftover tree AFTER the shutdown began; the shutdown must
//! still sweep it. Its own test binary: the leftover list is per process.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // test code: a fixed pause to order the race
#![cfg(target_os = "macos")]

use aibd::platform::session::{Running, SpawnFail, Spec};
use aibd::platform::tree::{start_tree, Inject, SpawnSpec, StartFailedOr};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

static SHUTDOWN_CALLED: AtomicBool = AtomicBool::new(false);

/// Starts a real tree, waits until the test has begun the shutdown, then
/// fails after spawn() (fork watch) with M1 lineage off, so its stop can
/// never release a slot: the tree becomes a leftover.
fn fails_during_shutdown(
    spec: &Spec,
    _: Option<Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn Running>, SpawnFail> {
    while !SHUTDOWN_CALLED.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(10));
    }
    // let shutdown_all reach its sweep in the old (wrong) order
    std::thread::sleep(Duration::from_millis(300));
    let r = start_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), "sleep 60 & wait".into()],
        cwd: Some(spec.cwd.clone()),
        env: vec![],
        env_clear: false,
        session_id: spec.id.clone(),
        watch_forks: true,
        discard_output: true,
        lineage: None,
        inject: Some(Inject::ForkWatch),
    });
    match r {
        Err(StartFailedOr::Started(f)) => Err(aibd::platform::session_unix::settle_failed_start(f)),
        _ => panic!("the injected failure did not fail after spawn"),
    }
}

#[test]
fn a_leftover_added_during_shutdown_is_still_swept() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store,
        None,
        fails_during_shutdown,
        2,
        tmp.path().to_path_buf(),
    );
    let post = {
        let s = s.clone();
        std::thread::spawn(move || {
            s.start(aibd::sessions::Request {
                argv: vec!["/bin/sh".into()],
                cwd: None,
                env: vec![],
            })
        })
    };
    // the POST holds its reservation and waits for us
    let op = std::time::Instant::now();
    while s.slots_in_use() == 0 {
        assert!(op.elapsed() < Duration::from_secs(5), "POST never reserved");
        std::thread::sleep(Duration::from_millis(5));
    }
    let swept_before = aibd::platform::session_unix::leftovers_swept();
    SHUTDOWN_CALLED.store(true, Ordering::SeqCst);
    let busy = s.shutdown_all();
    let e = post.join().unwrap().unwrap_err();
    assert!(e.1.contains("may be left running"), "{}", e.1);
    assert_eq!(busy, 0);
    assert_eq!(
        aibd::platform::session_unix::leftovers_swept(),
        swept_before + 1,
        "the leftover added during the shutdown was not swept"
    );
}
