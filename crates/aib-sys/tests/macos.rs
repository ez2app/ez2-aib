//! M1–M4 against the real kernel (macOS only).
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_sys::{selfcheck, Macos, ProcessInfo, SysError};
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
