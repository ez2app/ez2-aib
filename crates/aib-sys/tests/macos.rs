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

#[test]
fn an_out_of_range_pid_is_refused() {
    assert_eq!(Macos.unique_ids(u32::MAX), Err(SysError::BadPid));
    assert_eq!(Macos.bsd_info(u32::MAX), Err(SysError::BadPid));
}

/// The property M1 is here for: after the intermediate process exits and
/// its child is re-parented to launchd, `parent_unique` still names the
/// intermediate process.
#[test]
fn m1_parent_unique_survives_reparenting() {
    let mut sh = Command::new("/bin/sh")
        .args(["-c", "read go; /bin/sleep 30 >/dev/null 2>&1 & echo $!"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let sh_ids = Macos.unique_ids(sh.id()).unwrap();
    sh.stdin.take().unwrap().write_all(b"go\n").unwrap();
    let mut line = String::new();
    BufReader::new(sh.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let grandchild: u32 = line.trim().parse().unwrap();
    sh.wait().unwrap();

    let info = Macos.bsd_info(grandchild);
    let ids = Macos.unique_ids(grandchild);
    // only the pid we were told about, which our own child started
    let _ = Command::new("/bin/kill")
        .arg(grandchild.to_string())
        .status();

    assert_eq!(info.unwrap().ppid, 1, "not re-parented to launchd");
    assert_eq!(ids.unwrap().parent_unique, sh_ids.unique);
}

#[test]
fn selfcheck_passes_on_this_machine() {
    assert_eq!(selfcheck::run(&Macos), Ok(()));
}
