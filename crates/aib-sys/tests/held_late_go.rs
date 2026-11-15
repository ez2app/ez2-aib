//! A go byte that is already waiting when a held child is scheduled again
//! after its limit must not start the program (043dd 11-15: a loaded receipt
//! machine ran `a_hold_past_the_limit_is_the_spawn_error_or_the_panic` red
//! once). Here the child is stopped while held, so it is certainly late: it
//! is continued only after the hold ran past the limit and the go byte went
//! in. Signalling by pid is safe here: the child is held and not yet reaped
//! (std still waits on its exec status), so the pid cannot be reused. Its
//! own test file because held.rs itself never signals by pid (aibd.yml).
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // test pacing only

use aib_sys::held::{spawn_held, HOLD_LIMIT};
use std::process::Command;
use std::time::Duration;

#[test]
fn a_go_byte_read_after_the_limit_does_not_start_the_program() {
    let d = std::env::temp_dir().join(format!("aib-sys-late-go-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    let f = d.join("ran");
    let mut c = Command::new("/bin/sh");
    c.arg("-c").arg(format!("echo ran > '{}'", f.display()));
    let past = Duration::from_millis(u64::try_from(HOLD_LIMIT).unwrap() + 500);
    let r = spawn_held(&mut c, |pid| {
        let pid = libc::pid_t::try_from(pid).unwrap();
        // SAFETY: `pid` is our held, unreaped child (see the module doc).
        assert_eq!(unsafe { libc::kill(pid, libc::SIGSTOP) }, 0);
        std::thread::sleep(past);
        // continue it only once the go byte is in the pipe (written as soon
        // as this closure returns)
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(400));
            // SAFETY: as above; std reaps it only after this (exec status).
            let _ = unsafe { libc::kill(pid, libc::SIGCONT) };
        });
    });
    std::thread::sleep(Duration::from_millis(300));
    let ran = f.exists();
    let _ = std::fs::remove_dir_all(&d);
    assert!(
        r.is_err(),
        "a go byte read after the limit started the program: {:?}",
        r.map(|(c, h)| (c.id(), h.is_some()))
    );
    assert!(!ran, "the program ran");
}
