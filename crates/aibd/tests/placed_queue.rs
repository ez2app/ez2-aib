//! L2 (f163c REVIEW-l2-c2f0b-f163c-b029c9cc L1): a session that had to wait for a slot is
//! checked again when it leaves the queue: a program in aibd's providers folder that is not
//! the measured file does not start, even when it was put there while the session waited.
#![cfg(unix)]

use aib_store::Store;
use std::sync::Arc;
use std::time::{Duration, Instant};

fn req(argv: &[&str]) -> aibd::sessions::Request {
    aibd::sessions::Request {
        argv: argv.iter().map(|s| (*s).to_owned()).collect(),
        cwd: None,
        env: vec![],
        timeout: None,
        withhold: vec![],
        input: false,
    }
}

#[test]
fn a_placed_program_changed_while_its_session_waited_does_not_start() {
    let tmp = tempfile::tempdir().unwrap();
    let data = tmp.path();
    let sessions_dir = data.join("sessions");
    std::fs::create_dir_all(&sessions_dir).unwrap();
    let store = Arc::new(Store::open(&data.join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store,
        None,
        aibd::platform::session_unix::spawn,
        1,
        sessions_dir,
    );

    // the one slot is taken
    let a = s.start_for_seat(None, req(&["/bin/sleep", "30"])).unwrap();
    assert_eq!(a.state, "running");

    // a program in the providers folder, by the measured version's own path; while its
    // session waits, the file there is not the measured one (put there, or changed)
    let dir = data.join("providers").join("claude").join("2.1.326");
    std::fs::create_dir_all(&dir).unwrap();
    let placed = dir.join("claude");
    std::fs::copy("/bin/echo", &placed).unwrap();
    let b = s
        .start_for_seat(None, req(&[placed.to_str().unwrap(), "hi"]))
        .unwrap();
    assert_eq!(b.state, "pending", "reason: not_queued");

    // the slot frees: the waiting session is checked before it starts, and refused
    s.stop(&a.id, Duration::from_secs(2)).unwrap();
    let end = Instant::now() + Duration::from_secs(10);
    let got = loop {
        let g = s.get(&b.id).unwrap();
        if g.state != "pending" || Instant::now() > end {
            break g;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(
        got.state, "failed",
        "reason: changed_program_started {got:?}"
    );
    assert_eq!(got.pid, 0, "reason: changed_program_started");
    s.shutdown_all();
}
