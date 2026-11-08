//! `aib_sys::session` against the real kernel (Windows only; preview.2 sessions,
//! 043dd 11-09). The tree is `cmd /c ping`: cmd (root) + ping (grandchild).
#![cfg(windows)]
// test code: a panic is the failure report; the polls are paced (ADR 0005 covers
// aibd's loops, not tests; same as tests/windows.rs)
#![allow(clippy::unwrap_used, clippy::disallowed_methods)]

use aib_sys::job::Process;
use aib_sys::session::{SessionTree, SpawnError, Spec};
use std::time::{Duration, Instant};

fn cmd(line: &str) -> Spec {
    let comspec = std::env::var("ComSpec").unwrap_or_else(|_| "cmd.exe".into());
    Spec {
        argv: vec![comspec, "/d".into(), "/c".into(), line.into()],
        ..Spec::default()
    }
}

/// Wait until the Job holds at least `n` processes; their handles, opened now.
fn tree_of(s: &SessionTree, n: usize) -> Vec<Process> {
    let end = Instant::now() + Duration::from_secs(10);
    loop {
        let m = s.members().unwrap();
        if m.len() >= n {
            return m.iter().filter_map(|m| Process::open(m.pid).ok()).collect();
        }
        assert!(Instant::now() < end, "tree never had {n} members: {m:?}");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn killing_a_session_takes_the_whole_tree() {
    let mut s = SessionTree::spawn(&cmd("ping -n 60 127.0.0.1 >nul")).unwrap();
    let ours = tree_of(&s, 2);
    let r = s.kill(Duration::from_secs(10)).unwrap();
    assert!(r.members >= 2, "{r:?}");
    assert!(r.all_gone(), "{r:?}");
    // our own handles, opened before the kill, independently say exited
    for p in &ours {
        assert!(
            p.wait(Duration::ZERO).unwrap(),
            "pid {} still running",
            p.pid()
        );
    }
    assert_eq!(s.active().unwrap(), 0);
}

#[test]
fn dropping_a_session_kills_its_tree() {
    let s = SessionTree::spawn(&cmd("ping -n 60 127.0.0.1 >nul")).unwrap();
    let ours = tree_of(&s, 2);
    drop(s);
    for p in &ours {
        assert!(
            p.wait(Duration::from_secs(10)).unwrap(),
            "pid {} outlived its session",
            p.pid()
        );
    }
}

#[test]
fn the_root_exit_code_is_reported() {
    let mut s = SessionTree::spawn(&cmd("exit 7")).unwrap();
    let end = Instant::now() + Duration::from_secs(10);
    let code = loop {
        if let Some(c) = s.try_exit().unwrap() {
            break c;
        }
        assert!(Instant::now() < end, "root never exited");
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(code, 7);
    // killing an already-exited session is fine and reports nothing left
    let r = s.kill(Duration::from_secs(5)).unwrap();
    assert!(r.all_gone(), "{r:?}");
}

#[test]
fn an_empty_argv_and_a_missing_program_start_nothing() {
    assert_eq!(
        SessionTree::spawn(&Spec::default()).unwrap_err(),
        SpawnError::NoProgram
    );
    let e = SessionTree::spawn(&Spec {
        argv: vec!["C:\\no\\such\\program-043dd.exe".into()],
        ..Spec::default()
    })
    .unwrap_err();
    assert!(matches!(e, SpawnError::Spawn(_)), "{e:?}");
    assert!(e.tag().starts_with("spawn:"), "{}", e.tag());
}

#[test]
fn cwd_and_env_reach_the_root() {
    let dir = std::env::temp_dir();
    let mut spec = cmd(
        "if \"%AIB_SESSION_T%\"==\"ok\" (if exist aib-session-cwd-043dd.txt exit 3) else exit 4",
    );
    std::fs::write(dir.join("aib-session-cwd-043dd.txt"), b"x").unwrap();
    spec.cwd = Some(dir.clone());
    spec.env = vec![("AIB_SESSION_T".into(), "ok".into())];
    let mut s = SessionTree::spawn(&spec).unwrap();
    let end = Instant::now() + Duration::from_secs(10);
    let code = loop {
        if let Some(c) = s.try_exit().unwrap() {
            break c;
        }
        assert!(Instant::now() < end, "root never exited");
        std::thread::sleep(Duration::from_millis(20));
    };
    let _ = std::fs::remove_file(dir.join("aib-session-cwd-043dd.txt"));
    assert_eq!(code, 3, "4 = env missing, 0 = cwd file not seen");
}
