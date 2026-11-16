//! `aib-drive refresh --auto` end to end (519d0 11-15 19:54: aibd's refresh failed from
//! 17:39 to 19:12 and, its output dropped, left no reason): each run says what it did in
//! one line of `drive/refresh.log`.

use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

struct Root {
    base: PathBuf,
}

impl Root {
    fn new(tag: &str) -> Root {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let base = std::env::temp_dir().join(format!(
            "aib-drive-refresh-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(base.join("root").join("drive")).expect("mkdir");
        fs::create_dir_all(base.join("clone")).expect("mkdir");
        fs::create_dir_all(base.join("bin")).expect("mkdir");
        Root { base }
    }
    fn drive(&self) -> PathBuf {
        self.base.join("root").join("drive")
    }
    /// `refresh --auto` with only `self.base/bin` on PATH (no real gh, no real aib-slice).
    fn auto(&self) -> Output {
        Command::new(env!("CARGO_BIN_EXE_aib-drive"))
            .args(["refresh", "--auto", "--repo"])
            .arg(self.base.join("clone"))
            .env("EZ2_AIB_STATE_ROOT", self.base.join("root"))
            .env_remove("AIB_DRIVE_REPO_DIR")
            .env("PATH", self.base.join("bin"))
            .output()
            .expect("run aib-drive refresh --auto")
    }
    fn log(&self) -> Vec<Value> {
        fs::read_to_string(self.drive().join("refresh.log"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("log json"))
            .collect()
    }
}

impl Drop for Root {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.base);
    }
}

fn now_s() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

#[test]
fn a_fresh_cache_is_one_log_line_and_no_scan() {
    let r = Root::new("fresh");
    fs::write(r.drive().join("slice-sweep.json"), "{}").expect("cache");
    let o = r.auto();
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let log = r.log();
    assert_eq!(log.len(), 1, "{log:?}");
    assert_eq!(log[0]["what"], "fresh", "{log:?}");
    assert_eq!(log[0]["rc"], 0, "{log:?}");
    assert!(
        log[0]["at"].as_str().is_some_and(|a| a.ends_with('Z')),
        "{log:?}"
    );
}

/// A scan another run holds is not a failure and not a second scan; the line names it.
#[test]
fn a_held_lock_is_not_a_failure_and_names_the_holder() {
    let r = Root::new("held");
    fs::write(r.drive().join("refresh.lock"), format!("4242 {}", now_s())).expect("lock");
    let o = r.auto();
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let log = r.log();
    assert_eq!(log.len(), 1, "{log:?}");
    assert_eq!(log[0]["what"], "held", "{log:?}");
    assert!(
        log[0]["why"].as_str().is_some_and(|w| w.contains("4242")),
        "{log:?}"
    );
}

/// No gh to read the quota with: exit 3 and the reason kept.
#[test]
fn an_unreadable_quota_is_exit_3_with_the_reason_kept() {
    let r = Root::new("quota");
    let o = r.auto();
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    let log = r.log();
    assert_eq!(log.len(), 1, "{log:?}");
    assert_eq!(log[0]["what"], "quota", "{log:?}");
    assert!(
        log[0]["why"]
            .as_str()
            .is_some_and(|w| w.contains("rate_limit")),
        "{log:?}"
    );
    // the lock is given back
    assert!(!r.drive().join("refresh.lock").exists());
}

/// A scan that fails: its own error line is what the log keeps (aib-slice's last stderr
/// line, here a network reset).
#[cfg(unix)]
#[test]
fn a_failed_scan_keeps_the_scan_error_in_the_log() {
    use std::os::unix::fs::PermissionsExt;
    let r = Root::new("scan");
    let script = |p: PathBuf, body: &str| {
        fs::write(&p, format!("#!/bin/sh\n{body}\n")).expect("script");
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).expect("chmod");
    };
    script(
        r.base.join("bin").join("gh"),
        r#"echo '{"resources":{"core":{"remaining":4000}}}'"#,
    );
    fs::create_dir_all(r.base.join("root").join("bin")).expect("mkdir");
    script(
        r.base.join("root").join("bin").join("aib-slice"),
        "echo 'connection reset by peer' >&2; exit 3",
    );
    let o = r.auto();
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    let log = r.log();
    assert_eq!(log.len(), 1, "{log:?}");
    assert_eq!(log[0]["what"], "scan-failed", "{log:?}");
    assert!(
        log[0]["why"]
            .as_str()
            .is_some_and(|w| w.contains("connection reset by peer")),
        "{log:?}"
    );
    // tried is written, so the next try waits 20 minutes, and the lock is given back
    assert!(r.drive().join("refresh-tried").is_file());
    assert!(!r.drive().join("refresh.lock").exists());
}

/// The log is moved aside past 1 MiB, so a line every 5 minutes never grows without end.
#[test]
fn a_full_log_is_moved_aside_and_a_new_one_started() {
    let r = Root::new("rotate");
    fs::write(r.drive().join("slice-sweep.json"), "{}").expect("cache");
    fs::write(r.drive().join("refresh.log"), vec![b'x'; 1024 * 1024 + 1]).expect("big log");
    let o = r.auto();
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(r.drive().join("refresh.log.1").is_file());
    assert_eq!(r.log().len(), 1);
}

/// 5d33a a704784a L1: the caches refreshed but tidy-scan.json not written (no aib-xtask
/// here) is `refreshed` with the tidy reason kept, not a silent line.
#[cfg(unix)]
#[test]
fn a_refresh_whose_tidy_scan_fails_keeps_the_tidy_reason() {
    use std::os::unix::fs::PermissionsExt;
    let r = Root::new("tidy");
    let script = |p: PathBuf, body: &str| {
        fs::write(&p, format!("#!/bin/sh\n{body}\n")).expect("script");
        fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).expect("chmod");
    };
    script(
        r.base.join("bin").join("gh"),
        r#"echo '{"resources":{"core":{"remaining":4000}}}'"#,
    );
    fs::create_dir_all(r.base.join("root").join("bin")).expect("mkdir");
    // writes both staged files whole, as aib-slice does
    script(
        r.base.join("root").join("bin").join("aib-slice"),
        r#"while [ $# -gt 0 ]; do case "$1" in --out|--due-out) echo '{"complete":true}' > "$2";; esac; shift; done"#,
    );
    let o = r.auto();
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let log = r.log();
    assert_eq!(log.len(), 1, "{log:?}");
    assert_eq!(log[0]["what"], "refreshed", "{log:?}");
    let tidy = log[0]["tidy"].as_str().unwrap_or_default();
    assert!(tidy.chars().count() > 3, "{log:?}");
    // a9136 11-16 01:41: the line says where aib-xtask was looked for, not only "not found"
    assert!(
        tidy.contains("not found: tried") && tidy.contains("PATH folders"),
        "{log:?}"
    );
    assert!(r.drive().join("slice-sweep.json").is_file());
}
