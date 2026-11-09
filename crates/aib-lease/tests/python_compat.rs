// SPDX-License-Identifier: Apache-2.0
//! The 1.x tool (aib/tools/governance/host_lease.py, frozen) and aib-lease share the files.
//! Same inputs, both tools, decisions compared (RULES §9). Expected differences are named:
//!   D1  1.x has no --host: a holder on another machine is probed here and taken over at once;
//!       aib-lease with --host keeps it (b127).
//!   D2  1.x logs a dropped entry as why=gone; aib-lease adds gone_why.
//! Unix only: host_lease.py needs fcntl.
#![cfg(unix)]
#![allow(clippy::unwrap_used)]

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

const T: f64 = 1_794_230_000.0;
const FAR: &str = "far-away-mac.invalid";
const RUST: &str = env!("CARGO_BIN_EXE_aib-lease");

static N: AtomicUsize = AtomicUsize::new(0);

fn lease_file(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!(
        "aib-lease-compat-{}-{}-{}",
        std::process::id(),
        tag,
        N.fetch_add(1, Ordering::SeqCst)
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d.join("host-lease.json")
}

fn py_tool() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/tools/governance/host_lease.py")
}

/// (rc, stdout)
fn py(lease: &Path, now: f64, args: &[&str]) -> (i32, String) {
    let o = Command::new("python3")
        .arg(py_tool())
        .arg("--now")
        .arg(now.to_string())
        .arg("--locale")
        .arg("en")
        .args(args)
        .env("AIB_HOST_LEASE_FILE", lease)
        .output()
        .expect("python3 is needed for the 1.x comparison");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
    )
}

fn rs(lease: &Path, now: f64, args: &[&str]) -> (i32, Value) {
    let o = Command::new(RUST)
        .arg("--now")
        .arg(now.to_string())
        .args(args)
        .env("AIB_HOST_LEASE_FILE", lease)
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&o.stdout);
    (
        o.status.code().unwrap_or(-1),
        serde_json::from_str(out.trim()).unwrap_or(Value::Null),
    )
}

fn py_queue_ids(lease: &Path, now: f64) -> Vec<String> {
    let (rc, out) = py(lease, now, &["queue", "list", "--json"]);
    assert_eq!(rc, 0, "{out}");
    let v: Value = if out.trim().starts_with('[') {
        serde_json::from_str(out.trim()).unwrap()
    } else {
        Value::Array(vec![]) // "queue empty" text
    };
    v.as_array()
        .unwrap()
        .iter()
        .map(|e| e["run_id"].as_str().unwrap().to_string())
        .collect()
}

fn rs_queue_ids(lease: &Path, now: f64) -> Vec<String> {
    let (rc, v) = rs(lease, now, &["queue", "list"]);
    assert_eq!(rc, 0);
    v["live"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["run_id"].as_str().unwrap().to_string())
        .collect()
}

/// `<cmd...> --owner t --what ID --run-id ID <extra...>`
fn argv(cmd: &[&str], run_id: &str, extra: &[&str]) -> Vec<String> {
    cmd.iter()
        .copied()
        .chain(["--owner", "t", "--what", run_id, "--run-id", run_id])
        .chain(extra.iter().copied())
        .map(String::from)
        .collect()
}

fn refs(v: &[String]) -> Vec<&str> {
    v.iter().map(String::as_str).collect()
}

#[test]
fn a_beating_remote_queue_entry_survives_the_1x_cleanup_and_blocks_its_jump() {
    let l = lease_file("q");
    let a = argv(&["queue", "add"], "rq", &["--host", FAR, "--pid", "68643", "--heartbeat-s", "60"]);
    let (rc, v) = rs(&l, T, &refs(&a));
    assert_eq!((rc, v["result"].as_str()), (0, Some("queued")), "{v}");
    // 1.x registers someone else at T+100 and rewrites the file with what it thinks is live
    let (rc, out) = py(&l, T + 100.0, &refs(&argv(&["queue", "add"], "py1", &[])));
    assert_eq!(rc, 0, "{out}");
    assert_eq!(py_queue_ids(&l, T + 100.0), vec!["rq", "py1"]);
    assert_eq!(rs_queue_ids(&l, T + 100.0), vec!["rq", "py1"]);
    // 1.x acquire for a third run on the free machine: the remote entry is ahead ⇒ denied
    assert_eq!(py(&l, T + 100.0, &refs(&argv(&["acquire"], "py2", &[]))).0, 4);
    // a beat at T+150 keeps it past the first deadline (T+180) for both tools
    assert_eq!(rs(&l, T + 150.0, &["heartbeat", "--run-id", "rq"]).0, 0);
    assert_eq!(py_queue_ids(&l, T + 300.0), vec!["rq", "py1"]);
    assert_eq!(rs_queue_ids(&l, T + 300.0), vec!["rq", "py1"]);
    // silence: both drop it at the same second
    assert_eq!(py_queue_ids(&l, T + 330.0), vec!["py1"]);
    assert_eq!(rs_queue_ids(&l, T + 330.0), vec!["py1"]);
}

#[test]
fn a_beating_remote_lease_holds_against_1x_acquire_and_frees_on_silence() {
    let l = lease_file("lease");
    let a = argv(&["acquire"], "rl", &["--host", FAR, "--pid", "68643", "--heartbeat-s", "60"]);
    let (rc, v) = rs(&l, T, &refs(&a));
    assert_eq!((rc, v["result"].as_str()), (0, Some("acquired")), "{v}");
    let b = argv(&["acquire"], "py1", &[]);
    let b = refs(&b);
    assert_eq!(py(&l, T + 100.0, &b).0, 4);
    assert_eq!(py(&l, T, &["status"]).0, 4); // 1.x status: machine busy
    assert_eq!(rs(&l, T + 150.0, &["heartbeat", "--run-id", "rl"]).0, 0);
    assert_eq!(py(&l, T + 300.0, &b).0, 4);
    assert_eq!(py(&l, T + 330.0, &b).0, 0); // took over after silence
    // the remote holder learns it on its next beat
    assert_eq!(rs(&l, T + 340.0, &["heartbeat", "--run-id", "rl"]).0, 4);
}

#[test]
fn d1_the_1x_tool_takes_over_a_holder_on_another_machine_at_once() {
    // what b127 fixes: 1.x records the Mac pid as a pid of this machine
    let l = lease_file("d1");
    assert_eq!(py(&l, T, &refs(&argv(&["acquire"], "old", &["--pid", "999999"]))).0, 0);
    let b = argv(&["acquire"], "other", &[]);
    assert_eq!(py(&l, T + 10.0, &refs(&b)).0, 0, "1.x: holder_dead takeover");
}

#[test]
fn local_records_get_the_same_decision_from_both_tools() {
    let me = std::process::id().to_string();
    // a lease taken by 1.x for a live local pid: aib-lease refuses to take it, and vice versa
    let l = lease_file("local");
    assert_eq!(py(&l, T, &refs(&argv(&["acquire"], "pylocal", &["--pid", &me]))).0, 0);
    let lease: Value = serde_json::from_str(&std::fs::read_to_string(&l).unwrap()).unwrap();
    assert!(lease["pid_start"].is_string(), "1.x measured the start time");
    let (rc, v) = rs(&l, T + 99_999.0, &refs(&argv(&["acquire"], "rsother", &[])));
    assert_eq!((rc, v["result"].as_str()), (4, Some("busy")), "live local holder, even long after expiry: {v}");

    let l = lease_file("local2");
    assert_eq!(rs(&l, T, &refs(&argv(&["acquire"], "rslocal", &["--pid", &me]))).0, 0);
    let b = argv(&["acquire"], "pyother", &[]);
    assert_eq!(py(&l, T + 99_999.0, &refs(&b)).0, 4, "1.x sees the aib-lease record as alive (same host + start text)");

    // a queue entry whose local pid has exited: neither tool counts it
    let l = lease_file("local3");
    let mut child = Command::new("sh").arg("-c").arg("exit 0").spawn().unwrap();
    let dead = child.id();
    child.wait().unwrap();
    let rows = serde_json::json!([{"run_id": "deadpid", "owner": "t", "what": "w", "pid": dead,
        "host": lease["host"], "pid_start": "Mon Jan  1 00:00:00 2001", "priority": false,
        "since_epoch": T as i64, "until_epoch": T as i64 + 3600}]);
    let qp = PathBuf::from(format!("{}.queue.json", l.display()));
    std::fs::write(&qp, rows.to_string()).unwrap();
    assert!(py_queue_ids(&l, T + 1.0).is_empty());
    assert!(rs_queue_ids(&l, T + 1.0).is_empty());
}
