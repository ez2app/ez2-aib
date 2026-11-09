//! M1–M4 against the real kernel (macOS only).
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_sys::{selfcheck, ChildExit, ExitState, Macos, ProcessInfo, SysError};
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

fn me() -> u32 {
    std::process::id()
}

fn sleeper() -> std::process::Child {
    Command::new("/bin/sleep")
        .arg("30")
        .stdin(Stdio::null())
        .spawn()
        .unwrap()
}

#[test]
fn m1_own_ids_link_to_the_parent() {
    let own = Macos.unique_ids(me()).unwrap();
    assert_ne!(own.unique, 0);
    let parent = Macos
        .unique_ids(std::os::unix::process::parent_id())
        .unwrap();
    assert_eq!(own.parent_unique, parent.unique);
}

#[test]
fn m2_reads_our_identity() {
    let b = Macos.bsd_info(me()).unwrap();
    assert_eq!(b.pid, me());
    assert_eq!(b.ppid, std::os::unix::process::parent_id());
    assert!(b.start_sec > 0);
    assert!(!b.comm.is_empty());
}

#[test]
fn m3_m4_list_a_child() {
    let mut c = sleeper();
    let kids = Macos.children(me()).unwrap();
    let all = Macos.all_pids().unwrap();
    let _ = c.kill();
    let _ = c.wait();
    assert!(kids.contains(&c.id()), "{kids:?}");
    // only children: neither us nor our parent
    assert!(!kids.contains(&me()), "{kids:?}");
    assert!(
        !kids.contains(&std::os::unix::process::parent_id()),
        "{kids:?}"
    );
    assert!(all.contains(&me()) && all.contains(&c.id()));
    assert!(!kids.contains(&0) && !all.contains(&0));
}

#[test]
fn a_reaped_child_is_gone_not_denied() {
    let mut c = sleeper();
    let pid = c.id();
    c.kill().unwrap();
    c.wait().unwrap();
    assert_eq!(Macos.unique_ids(pid), Err(SysError::Gone));
    assert_eq!(Macos.bsd_info(pid), Err(SysError::Gone));
    assert_eq!(Macos.children(pid), Ok(Vec::new()));
}

/// launchd's BSD info is refused to a normal user (measured on 15.7.9):
/// that must surface as PermissionDenied, never as Gone.
#[test]
fn launchd_bsd_info_is_permission_denied_for_a_user() {
    let uid = Command::new("/usr/bin/id").arg("-u").output().unwrap();
    if String::from_utf8_lossy(&uid.stdout).trim() == "0" {
        eprintln!("running as root: launchd is readable, nothing to check");
        return;
    }
    assert_eq!(Macos.bsd_info(1), Err(SysError::PermissionDenied));
}

/// M1 on launchd as a user (measured on 15.7.9: readable). Whatever a
/// later OS does, it must never read as Gone: launchd is alive.
#[test]
fn m1_of_launchd_reads_or_is_denied_never_gone() {
    match Macos.unique_ids(1) {
        Ok(u) => assert_ne!(u.unique, 0),
        Err(e) => assert_eq!(e, SysError::PermissionDenied),
    }
}

#[test]
fn an_out_of_range_pid_is_refused() {
    assert_eq!(Macos.unique_ids(u32::MAX), Err(SysError::BadPid));
    assert_eq!(Macos.bsd_info(u32::MAX), Err(SysError::BadPid));
}

/// Command name of `pid` as `ps` shows it ("" once it is gone).
fn comm(pid: u32) -> String {
    let o = Command::new("/bin/ps")
        .args(["-o", "comm=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

/// Poll `f` every 20 ms for up to 2 s; true once it holds.
fn within_2s(mut f: impl FnMut() -> bool) -> bool {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    loop {
        if f() {
            return true;
        }
        if std::time::Instant::now() >= deadline {
            return false;
        }
        #[allow(clippy::disallowed_methods)] // test pacing only
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
}

/// The property M1 is here for: after the intermediate process exits and
/// its (already exec'd) child is re-parented to launchd, `parent_unique`
/// still names the intermediate process.
///
/// The child must have exec'd BEFORE the intermediate exits: an exec after
/// it resets `parent_unique` to launchd (pinned by the next test). Without
/// that ordering this test raced and went red about 1 run in 7 (11-08).
#[test]
fn m1_parent_unique_survives_reparenting() {
    let mut sh = Command::new("/bin/sh")
        .args([
            "-c",
            "read go; /bin/sleep 30 >/dev/null 2>&1 & echo $!; read done",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let sh_ids = Macos.unique_ids(sh.id()).unwrap();
    let mut stdin = sh.stdin.take().unwrap();
    stdin.write_all(b"go\n").unwrap();
    let mut line = String::new();
    BufReader::new(sh.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let grandchild: u32 = line.trim().parse().unwrap();
    let execd = within_2s(|| comm(grandchild).ends_with("sleep"));
    stdin.write_all(b"done\n").unwrap();
    sh.wait().unwrap();

    let reparented = within_2s(|| matches!(Macos.bsd_info(grandchild), Ok(i) if i.ppid == 1));
    let info = Macos.bsd_info(grandchild);
    let ids = Macos.unique_ids(grandchild);
    // only the pid we were told about, which our own child started
    let _ = Command::new("/bin/kill")
        .arg(grandchild.to_string())
        .status();

    assert!(execd, "grandchild {grandchild} never exec'd /bin/sleep");
    assert!(
        reparented,
        "not re-parented to launchd within 2 s: {info:?}"
    );
    let ids = ids.unwrap_or_else(|e| panic!("unique_ids({grandchild}) failed: {e:?}"));
    assert_eq!(
        ids.parent_unique, sh_ids.unique,
        "creator id lost after re-parenting: {ids:?} vs sh {sh_ids:?}"
    );
}

/// Kernel behaviour M1 cannot see past (11-08): a process re-parented to
/// launchd that exec's AFTERWARDS gets `parent_unique` = launchd's (1); its
/// own `unique` stays. If macOS ever changes this, this test goes red and
/// the `exec_after_creator_exit` blind spot is to be reviewed.
#[test]
fn m1_an_exec_after_the_creator_exited_resets_parent_unique_to_launchd() {
    let mut sh = Command::new("/bin/sh")
        .args([
            "-c",
            "(/bin/sleep 0.3; exec /bin/sleep 30) >/dev/null 2>&1 & echo $!",
        ])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let sh_ids = Macos.unique_ids(sh.id()).unwrap();
    let mut line = String::new();
    BufReader::new(sh.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let pid: u32 = line.trim().parse().unwrap();
    let before = Macos.unique_ids(pid);
    sh.wait().unwrap();
    // the subshell outlives sh, then exec's /bin/sleep 30 about 0.3 s later
    let execd = within_2s(|| comm(pid).ends_with("sleep"));
    let after = Macos.unique_ids(pid);
    let _ = Command::new("/bin/kill").arg(pid.to_string()).status();

    let before = before.unwrap_or_else(|e| panic!("unique_ids({pid}) before: {e:?}"));
    assert_eq!(
        before.parent_unique, sh_ids.unique,
        "{before:?} vs sh {sh_ids:?}"
    );
    assert!(execd, "{pid} never exec'd /bin/sleep");
    let after = after.unwrap_or_else(|e| panic!("unique_ids({pid}) after: {e:?}"));
    assert_eq!(after.unique, before.unique, "exec changed the unique id");
    assert_eq!(after.parent_unique, 1, "exec after re-parenting: {after:?}");
}

#[test]
fn selfcheck_passes_on_this_machine() {
    assert_eq!(selfcheck::run(&Macos), Ok(()));
}

fn sh(script: &str) -> std::process::Child {
    Command::new("/bin/sh")
        .args(["-c", script])
        .stdin(Stdio::null())
        .spawn()
        .unwrap()
}

/// Poll M5 until `pid` is no longer running (at most 10 s).
#[allow(clippy::disallowed_methods)] // test pacing only
fn peek_until_exited(pid: u32) -> ExitState {
    for _ in 0..1000 {
        match Macos.peek(pid).unwrap() {
            ExitState::Running => std::thread::sleep(std::time::Duration::from_millis(10)),
            st => return st,
        }
    }
    panic!("child {pid} did not exit within 10 s");
}

#[test]
fn m5_sees_a_running_child_as_running() {
    let mut c = sleeper();
    assert_eq!(Macos.peek(c.id()).unwrap(), ExitState::Running);
    c.kill().unwrap();
    c.wait().unwrap();
}

#[test]
fn m5_reads_the_exit_status_without_reaping() {
    let mut c = sh("exit 7");
    let pid = c.id();
    assert_eq!(peek_until_exited(pid), ExitState::Exited(7));
    // WNOWAIT: still a zombie, so the same answer again, and the pid still
    // belongs to it (M2 still reads it)
    assert_eq!(Macos.peek(pid).unwrap(), ExitState::Exited(7));
    assert_eq!(Macos.wait_exit(pid).unwrap(), ExitState::Exited(7));
    // measured 11-08 (macOS 15.7.9): M1 / M2 say Gone for our own unreaped
    // zombie although it still holds the pid. For our own child, M5 is the
    // answer; "Gone" from M1 / M2 does not mean the pid is free.
    let why = "macOS version behaviour differs: see ADR 0008 Context (measured on 15.7.9)";
    assert_eq!(Macos.bsd_info(pid), Err(SysError::Gone), "{why}");
    assert_eq!(Macos.unique_ids(pid), Err(SysError::Gone), "{why}");
    // the one real reap still gets the status: nobody took it
    assert_eq!(c.wait().unwrap().code(), Some(7));
    // after the reap it is not our child any more: NotChild, never "gone"
    assert_eq!(Macos.peek(pid), Err(SysError::NotChild));
}

#[test]
#[allow(clippy::disallowed_methods)] // test pacing only
fn m5_blocks_until_a_killed_child_exits_and_reports_the_signal() {
    let mut c = sleeper();
    let pid = c.id();
    let t = std::thread::spawn(move || Macos.wait_exit(pid));
    std::thread::sleep(std::time::Duration::from_millis(100));
    c.kill().unwrap(); // SIGKILL
    assert_eq!(t.join().unwrap().unwrap(), ExitState::Signaled(9));
    use std::os::unix::process::ExitStatusExt;
    assert_eq!(c.wait().unwrap().signal(), Some(9));
}

#[test]
fn m5_refuses_a_process_that_is_not_our_child() {
    assert_eq!(Macos.peek(1), Err(SysError::NotChild));
    assert_eq!(
        Macos.peek(std::os::unix::process::parent_id()),
        Err(SysError::NotChild)
    );
}

static USR2_HITS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

extern "C" fn ignore_signal(_: libc::c_int) {
    USR2_HITS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
}

#[test]
#[allow(clippy::disallowed_methods)] // test pacing only
fn m5_wait_retries_when_a_signal_interrupts_it() {
    // a SIGUSR2 handler WITHOUT SA_RESTART: a blocked waitid returns EINTR
    let sa = std::mem::MaybeUninit::<libc::sigaction>::zeroed();
    // SAFETY: an all-zero `sigaction` is valid (empty mask, no flags); only
    // the handler field is set below.
    let mut sa = unsafe { sa.assume_init() };
    sa.sa_sigaction = ignore_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
    sa.sa_flags = 0;
    // SAFETY: `sa` is a valid `sigaction`; the old action is not needed.
    let rc = unsafe { libc::sigaction(libc::SIGUSR2, &sa, std::ptr::null_mut()) };
    assert_eq!(rc, 0);
    let mut c = sleeper();
    let pid = c.id();
    let (tx, rx) = std::sync::mpsc::channel();
    let t = std::thread::spawn(move || {
        // SAFETY: `pthread_self` has no preconditions.
        tx.send(unsafe { libc::pthread_self() }).unwrap();
        Macos.wait_exit(pid)
    });
    let th = rx.recv().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(200));
    // three sends 50 ms apart (043dd #522 L2): at least one lands while the
    // thread is blocked in waitid, whatever the scheduling
    for _ in 0..3 {
        // SAFETY: `th` is the live waiting thread (it cannot finish before
        // the child is killed below); SIGUSR2 has a handler installed above.
        let rc = unsafe { libc::pthread_kill(th, libc::SIGUSR2) };
        assert_eq!(rc, 0);
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    std::thread::sleep(std::time::Duration::from_millis(100));
    // the handler really ran, and the wait did not come back on EINTR: an
    // EINTR returned to the caller would have ended the thread already
    assert!(USR2_HITS.load(std::sync::atomic::Ordering::SeqCst) >= 1);
    assert!(
        !t.is_finished(),
        "wait_exit returned on EINTR instead of retrying"
    );
    c.kill().unwrap();
    assert_eq!(t.join().unwrap().unwrap(), ExitState::Signaled(9));
    c.wait().unwrap();
}

// ---- T1 / T2 (R1 thermal protection) ----------------------------------------------------------

/// T1 on the real machine. Apple Silicon must give a plausible reading (this is the check that
/// the private signatures are right: a wrong one would be undefined behaviour, not a red test,
/// so it must have passed here before aibd uses T1). Intel has no `PMU tdie` sensors and must
/// say `Unsupported` (or `SpiMissing` if IOKit lacks the symbols), never a number.
#[test]
fn t1_die_temps_real() {
    let r = aib_sys::ThermalInfo::die_temps(&Macos);
    if cfg!(target_arch = "aarch64") {
        let d = r.unwrap();
        assert!(d.n >= 1, "{d:?}");
        assert!(d.max > 0.0 && d.max < 150.0, "{d:?}");
        assert!(d.avg > 0.0 && d.avg <= d.max, "{d:?}");
        eprintln!("T1: max {:.2} avg {:.2} n {}", d.max, d.avg, d.n);
    } else {
        assert!(
            matches!(r, Err(SysError::Unsupported | SysError::SpiMissing)),
            "{r:?}"
        );
    }
    // repeated reads keep working (nothing over-released on the first one)
    for _ in 0..20 {
        let again = aib_sys::ThermalInfo::die_temps(&Macos);
        assert_eq!(again.is_ok(), cfg!(target_arch = "aarch64"), "{again:?}");
    }
}

/// T2 on the real machine: a level in `0..=4`.
#[test]
fn t2_pressure_real() {
    let p = aib_sys::ThermalInfo::pressure(&Macos).unwrap();
    assert!((0..=4).contains(&p), "{p}");
    eprintln!("T2: pressure level {p}");
}

/// The start-up check passes where T1 is usable and reports a tag where it is not.
#[test]
fn t1_selfcheck_real() {
    match aib_sys::thermal::selfcheck_t1(&Macos) {
        Ok(d) => assert!(d.n >= 1),
        Err(e) => {
            assert!(!cfg!(target_arch = "aarch64"), "{e:?}");
            assert!(["unsupported", "spi_missing"].contains(&e.tag()));
        }
    }
}
