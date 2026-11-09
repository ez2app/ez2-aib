//! M5's premise against the real kernel: an ignored SIGCHLD auto-reaps our
//! children, so M5 only sees ECHILD (043dd #522 M1). Its own test binary
//! (= own process): it changes the process-wide SIGCHLD disposition.
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // test pacing only

use aib_sys::{sigchld, sigchld_keep_zombies, ChildExit, ExitState, Macos, Sigchld, SysError};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn exit7() -> u32 {
    Command::new("/bin/sh")
        .args(["-c", "exit 7"])
        .stdin(Stdio::null())
        .spawn()
        .unwrap()
        .id() // never waited on: the point is who reaps it
}

fn set(handler: libc::sighandler_t, flags: libc::c_int) {
    let sa = std::mem::MaybeUninit::<libc::sigaction>::zeroed();
    // SAFETY: an all-zero `sigaction` is valid; handler and flags set below.
    let mut sa = unsafe { sa.assume_init() };
    sa.sa_sigaction = handler;
    sa.sa_flags = flags;
    // SAFETY: `sa` is a valid `sigaction`; the old action is not needed.
    let rc = unsafe { libc::sigaction(libc::SIGCHLD, &sa, std::ptr::null_mut()) };
    assert_eq!(rc, 0);
}

fn settle(pid: u32) -> Result<ExitState, SysError> {
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        let r = Macos.peek(pid);
        if r != Ok(ExitState::Running) || Instant::now() > end {
            return r;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// One test (one thread): the steps share the process-wide disposition.
#[test]
fn an_ignored_sigchld_auto_reaps_and_m5_says_not_child_until_it_is_reset() {
    assert!(
        sigchld().unwrap().keeps_zombies(),
        "the test runner must start with zombies kept"
    );

    set(libc::SIG_IGN, 0);
    assert_eq!(sigchld().unwrap(), Sigchld::Ignored);
    let pid = exit7();
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        settle(pid),
        Err(SysError::ChildrenAutoReaped),
        "SIG_IGN: the kernel reaped it, and M5 says why"
    );

    set(libc::SIG_DFL, libc::SA_NOCLDWAIT);
    assert_eq!(sigchld().unwrap(), Sigchld::NoChildWait);
    let pid = exit7();
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        settle(pid),
        Err(SysError::ChildrenAutoReaped),
        "SA_NOCLDWAIT: the kernel reaped it, and M5 says why"
    );
    // M5 cannot tell "not ours" from "reaped by the kernel" while auto-reap is on, so every ECHILD then
    // carries the cause; both mean: send nothing.
    assert_eq!(Macos.peek(1), Err(SysError::ChildrenAutoReaped));

    assert_eq!(sigchld_keep_zombies().unwrap(), Sigchld::NoChildWait);
    assert_eq!(sigchld().unwrap(), Sigchld::Default);
    let pid = exit7();
    assert_eq!(
        settle(pid),
        Ok(ExitState::Exited(7)),
        "default again: a zombie M5 can read"
    );
    assert_eq!(
        Macos.peek(1),
        Err(SysError::NotChild),
        "default again: not ours is NotChild"
    );
    // reap it ourselves: no zombie left behind by the test
    assert_eq!(Macos.wait_exit(pid), Ok(ExitState::Exited(7)));
    let mut st = 0;
    // SAFETY: plain waitpid on our own child.
    let r = unsafe { libc::waitpid(pid as libc::pid_t, &mut st, 0) };
    assert_eq!(r, pid as libc::pid_t);

    // a handler that keeps zombies is left alone
    set(noop as extern "C" fn(libc::c_int) as libc::sighandler_t, 0);
    assert_eq!(sigchld_keep_zombies().unwrap(), Sigchld::Handled);
    assert_eq!(sigchld().unwrap(), Sigchld::Handled);
    set(libc::SIG_DFL, 0);
}

extern "C" fn noop(_: libc::c_int) {}
