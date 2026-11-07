#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! aib-forkcount end to end (macOS only: the watcher is kqueue).
#![cfg(target_os = "macos")]
#![allow(clippy::disallowed_methods)] // test pacing

use std::process::Command;

fn tool() -> Command {
    Command::new(env!("CARGO_BIN_EXE_aib-forkcount"))
}

fn json(out: std::process::Output) -> serde_json::Value {
    assert!(
        out.status.success(),
        "rc {:?}: {}",
        out.status,
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}

/// Sequential, slow single children: both counters must be exact. This is
/// the plumbing check; the other patterns are measurement, not assertions.
#[test]
fn calibrate_single_is_exact() {
    let v = json(
        tool()
            .args(["calibrate", "--pattern", "single", "--count", "20"])
            .output()
            .unwrap(),
    );
    assert_eq!(v["expected_process_creations"], 20, "{v}");
    assert_eq!(v["result"]["children_adopted"], 20, "{v}");
    assert_eq!(v["result"]["fork_events"], 20, "{v}");
    assert_eq!(v["root_watched"], true);
}

/// A child that starts a grandchild: the grandchild is only visible if the
/// watcher follows new children recursively.
#[test]
fn calibrate_grandchild_follows_the_tree() {
    let v = json(
        tool()
            .args(["calibrate", "--pattern", "grandchild", "--count", "10"])
            .output()
            .unwrap(),
    );
    assert_eq!(v["expected_process_creations"], 20, "{v}");
    let ev = v["result"]["fork_events"].as_u64().unwrap();
    assert!(ev >= 15, "grandchild forks not followed: {v}");
}

#[test]
fn run_reports_coverage_and_lower_bound() {
    let mut root = Command::new("/bin/sh")
        .args([
            "-c",
            "sleep 0.5; for i in 1 2 3 4 5; do /usr/bin/true; sleep 0.1; done; sleep 0.5",
        ])
        .spawn()
        .unwrap();
    let v = json(
        tool()
            .args([
                "run",
                "--seconds",
                "2",
                "--pid",
                &root.id().to_string(),
                "--label",
                "t",
            ])
            .output()
            .unwrap(),
    );
    let _ = root.wait();
    assert_eq!(v["coverage"]["roots"][0]["watched"], true, "{v}");
    assert!(!v["coverage"]["blind_spots"].as_array().unwrap().is_empty());
    assert!(v["result"]["metric"]
        .as_str()
        .unwrap()
        .contains("lower bound"));
    // the shell waits 0.5 s so the watch is in place; allow for coalescing
    assert!(v["result"]["fork_events"].as_u64().unwrap() >= 3, "{v}");
}

#[test]
fn no_roots_is_a_usage_error() {
    let o = tool().args(["run", "--seconds", "1"]).output().unwrap();
    assert_eq!(o.status.code(), Some(2));
}

/// A tree that already exists before the watch starts (sh -> sh -> sleep):
/// the watcher must walk it recursively when the root is added.
#[test]
fn existing_tree_is_recorded_when_the_root_is_added() {
    let mut root = Command::new("/bin/sh")
        .args(["-c", "/bin/sh -c '/bin/sleep 3; :' & /bin/sleep 3"])
        .spawn()
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(400));
    let v = json(
        tool()
            .args(["run", "--seconds", "1", "--pid", &root.id().to_string()])
            .output()
            .unwrap(),
    );
    let _ = root.kill();
    let _ = root.wait();
    // root sh, its sleep, the inner sh and the inner sleep
    let n = v["result"]["tracked_processes"].as_u64().unwrap();
    assert!(n >= 4, "existing tree not walked: {v}");
}
