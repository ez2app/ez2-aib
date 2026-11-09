//! The real server, started by a parent that ignores SIGCHLD (inherited
//! across exec): it must set SIGCHLD back to default before serving, or every
//! group stop would be unverified (ADR 0008 rule 3, 043dd #522 M1).
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[test]
#[allow(clippy::disallowed_methods)] // ADR 0005 named exception: test pacing (bounded wait for the child's log line)
fn a_server_started_with_sigchld_ignored_resets_it_and_says_so() {
    let dir = tempfile::tempdir().unwrap();
    // python3 sets SIG_IGN and execs aibd: the same inheritance a launcher
    // that ignores SIGCHLD would hand over
    let mut c = Command::new("/usr/bin/python3")
        .args([
            "-c",
            "import os, signal, sys; signal.signal(signal.SIGCHLD, signal.SIG_IGN); os.execv(sys.argv[1], [sys.argv[1]])",
            env!("CARGO_BIN_EXE_aibd"),
        ])
        .env("AIBD_DATA_DIR", dir.path())
        .env("AIBD_BIND", "127.0.0.1:0")
        .env("AIBD_LOG", "warn")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    let err = c.stderr.take().unwrap();
    std::thread::spawn(move || {
        for line in BufReader::new(err).lines().map_while(Result::ok) {
            if tx.send(line).is_err() {
                break;
            }
        }
    });
    let end = Instant::now() + Duration::from_secs(20);
    let mut seen = Vec::new();
    let mut found = false;
    while Instant::now() < end {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(l) => {
                found =
                    l.contains("SIGCHLD reset to default") && l.contains("\"sigchld\":\"ignored\"");
                seen.push(l);
                if found {
                    break;
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(_) => break,
        }
    }
    c.kill().unwrap(); // our own child (the exec'd aibd)
    c.wait().unwrap();
    assert!(found, "no reset warning; stderr: {seen:?}");
}
