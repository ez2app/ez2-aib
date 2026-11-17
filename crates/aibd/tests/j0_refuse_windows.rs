//! S79-p11a (Windows acceptance 1.3): when aibd cannot put itself in its Job (J0) it
//! refuses to start, rc 1, and says why; it never serves without J0 (DECISION-21).
//! `AIBD_TEST_J0_REFUSE=1` makes the J0 step fail the way a refused assign does
//! (`platform::lifecycle::J0_REFUSE_ENV`), so the real binary's refusal path runs.
#![cfg(windows)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[test]
#[allow(clippy::disallowed_methods)] // ADR 0005 named exception: test pacing (bounded wait for the child)
fn a_refused_job_makes_aibd_refuse_to_start() {
    let dir = tempfile::tempdir().unwrap();
    let mut c = Command::new(env!("CARGO_BIN_EXE_aibd"))
        .env("AIBD_TEST_J0_REFUSE", "1")
        .env("AIBD_DATA_DIR", dir.path())
        .env("AIBD_BIND", "127.0.0.1:0")
        .env("AIBD_LOG", "info")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    // a broken switch would leave aibd serving: bound the wait, then kill our own child
    let (tx, rx) = std::sync::mpsc::channel::<()>();
    let end = Instant::now() + Duration::from_secs(30);
    let status = loop {
        if let Some(s) = c.try_wait().unwrap() {
            break Some(s);
        }
        if Instant::now() >= end {
            break None;
        }
        let _ = rx.recv_timeout(Duration::from_millis(100));
    };
    drop(tx);
    let Some(status) = status else {
        c.kill().unwrap();
        c.wait().unwrap();
        panic!("aibd still running 30 s after a refused J0: it must refuse to start");
    };
    let mut err = String::new();
    c.stderr.take().unwrap().read_to_string(&mut err).unwrap();
    assert_eq!(status.code(), Some(1), "refused J0 ⇒ rc 1; stderr: {err}");
    assert!(
        err.contains("refusing to start") && err.contains("j0_refused_by_test"),
        "the log says it refuses and why; stderr: {err}"
    );
}
