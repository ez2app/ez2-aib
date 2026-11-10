#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! aib-forkcount end to end (macOS only: the watcher is kqueue).
#![cfg(target_os = "macos")]
#![allow(clippy::disallowed_methods)] // test pacing

use std::process::Command;

fn tool() -> Command {
    Command::new(env!("CARGO_BIN_EXE_aib-forkcount"))
}

fn json(out: std::process::Output) -> serde_json::Value {
    // stdout carries the report (status, unverified_reasons): print it too,
    // a bare "rc 1" on CI cannot be traced (11-08, main bbf12897)
    assert!(
        out.status.success(),
        "rc {:?}\nstdout: {}\nstderr: {}",
        out.status,
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}

/// Sequential, slow single children: both counters must be exact. This is
/// the plumbing check; the other patterns are measurement, not assertions.
/// Skipped by name on shared GitHub macOS runners (aibd.yml), run locally.
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
    assert_eq!(v["status"], "ok", "{v}");
    assert_eq!(v["identity_confirmed"], 20, "{v}");
    assert_eq!(v["meets_threshold"], true, "{v}");
    assert_eq!(v["capture_rate"], 1.0, "{v}");
}

/// The miscount knife (DECISION-4 C): one extra fork the fixture did not
/// record must not raise the capture, and must mark the run unverified.
#[test]
fn calibrate_unlisted_fork_is_not_counted() {
    let out = tool()
        .args([
            "calibrate",
            "--pattern",
            "single",
            "--count",
            "10",
            "--inject-unlisted",
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1), "unverified must not exit 0");
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(v["expected_process_creations"], 10, "{v}");
    // The listed /usr/bin/true children may be missed under load (that is
    // what calibrate measures); the claim here is only that the one unlisted
    // child is adopted but never counted as confirmed.
    let confirmed = v["identity_confirmed"].as_u64().unwrap();
    assert!(confirmed <= 10, "{v}");
    assert!(
        v["raw"]["children_adopted"].as_u64().unwrap() > confirmed,
        "the unlisted child was not adopted: {v}"
    );
    assert_eq!(v["meets_threshold"], false, "{v}");
    assert_eq!(v["status"], "unverified", "{v}");
    let reasons = v["unverified_reasons"].to_string();
    assert!(reasons.contains("adopted_not_created"), "{v}");
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

/// bb7dc 11-08: mid-exit came out "ok" / rc 0 with 197 list failures.
/// Whatever the run sees, failures and rc 0 never come together.
#[test]
fn calibrate_mid_exit_never_passes_with_failures() {
    let out = tool()
        .args(["calibrate", "--pattern", "mid-exit", "--count", "20"])
        .output()
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    let raw = &v["raw"];
    let failed =
        raw["list_failures"].as_u64().unwrap() + raw["register_failures"].as_u64().unwrap();
    if failed > 0 {
        assert_eq!(v["status"], "unverified", "{v}");
        assert_eq!(out.status.code(), Some(1), "{v}");
    }
    if out.status.code() == Some(0) {
        assert_eq!(v["meets_threshold"], true, "{v}");
    }
}

/// a63ab 11-10 (PLAN-goal2-fixture §1.2): the calibration trace is complete
/// enough to explain a miss. Every fork event read has a listing of that
/// parent after it, every mid-exit step left its gen-one stamps, and every
/// miss carries one of the four causes. Knife: the listing is not recorded.
#[test]
fn calibrate_mid_exit_trace_explains_every_miss() {
    let out = tool()
        .args(["calibrate", "--pattern", "mid-exit", "--count", "20"])
        .output()
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    let t = &v["trace"];
    assert_eq!(t["enabled"], true, "{v}");
    assert_eq!(t["dropped"], 0, "{v}");
    assert!(t["events"].as_u64().unwrap() > 0, "{v}");
    assert_eq!(t["forks_without_listing"], 0, "{t}");
    assert_eq!(t["gen_one_stamps"], 20, "{t}");
    for m in v["missing_identities"].as_array().unwrap() {
        let c = m["cause"].as_str().unwrap();
        assert!(
            [
                "no_fork_event",
                "listed_but_absent",
                "listed_start_unreadable",
                "listed_older_than_parent",
                "parent_exited_before_list",
                "exit_race_unresolved",
                "unknown"
            ]
            .contains(&c),
            "{m}"
        );
        assert!(m["timeline"].is_array(), "{m}");
    }
}

/// 3f312 11-08 knife: one step never started and never recorded. The
/// expected count is fixed by --count, so the run is short, not 100 %.
#[test]
fn calibrate_a_missing_creation_is_unverified() {
    for (pattern, planned) in [
        ("single", 10),
        ("grandchild", 20),
        ("burst", 10),
        ("parallel", 40),
    ] {
        let out = tool()
            .args([
                "calibrate",
                "--pattern",
                pattern,
                "--count",
                "10",
                "--inject-short",
            ])
            .output()
            .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(out.status.code(), Some(1), "{pattern}: {v}");
        assert_eq!(v["expected_process_creations"], planned, "{pattern}: {v}");
        assert!(
            v["recorded_process_creations"].as_u64().unwrap() < planned,
            "{pattern}: {v}"
        );
        assert_eq!(v["status"], "unverified", "{pattern}: {v}");
        assert_eq!(v["meets_threshold"], false, "{pattern}: {v}");
        assert!(
            v["unverified_reasons"]
                .to_string()
                .contains("creations_short"),
            "{pattern}: {v}"
        );
    }
}

#[test]
fn calibrate_rejects_an_unknown_pattern_or_count() {
    for a in [
        ["--pattern", "nope", "--count", "5"],
        ["--pattern", "single", "--count", "x"],
    ] {
        let out = tool().arg("calibrate").args(a).output().unwrap();
        assert_eq!(out.status.code(), Some(2), "{a:?}");
    }
}

/// DECISION-10 B: run `calibrate --pattern single --count 10` with a fault
/// injected into the child listing (plus `extra` flags) and return
/// (rc, report).
fn calibrate_injected(fault: &str, extra: &[&str]) -> (Option<i32>, serde_json::Value) {
    let mut args = vec![
        "calibrate",
        "--pattern",
        "single",
        "--count",
        "10",
        "--inject-list",
        fault,
    ];
    args.extend_from_slice(extra);
    let out = tool().args(&args).output().unwrap();
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap_or_else(|e| {
        panic!(
            "{e}\nstdout: {}\nstderr: {}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        )
    });
    (out.status.code(), v)
}

/// The fill fails (EIO, through aib-sys's real `read_pid_list`): a failure,
/// never an empty list. The run is unverified for `list_failures` (not only
/// because a fault was injected) and exits non-zero.
#[test]
fn calibrate_a_failed_listing_is_unverified_not_empty() {
    let (rc, v) = calibrate_injected("error", &[]);
    assert_eq!(rc, Some(1), "{v}");
    assert_eq!(v["status"], "unverified", "{v}");
    assert_eq!(v["injected_list_fault"], "error", "{v}");
    let reasons = v["unverified_reasons"].to_string();
    assert!(reasons.contains("\"list_failures\""), "{v}");
    assert!(v["raw"]["list_failures"].as_u64().unwrap() >= 1, "{v}");
    assert!(
        v["result"]["list_failures_by_tag"]["os_error"]
            .as_u64()
            .unwrap_or(0)
            >= 1,
        "{v}"
    );
    assert_eq!(v["meets_threshold"], false, "{v}");
}

/// Every fill comes back full: aib-sys's real `read_pid_list` retries and
/// then refuses (`Incomplete`); a truncated list is never used and the run
/// is unverified with a non-zero exit.
#[test]
fn calibrate_an_incomplete_listing_is_unverified() {
    let (rc, v) = calibrate_injected("incomplete", &[]);
    assert_eq!(rc, Some(1), "{v}");
    let reasons = v["unverified_reasons"].to_string();
    assert!(reasons.contains("\"list_failures\""), "{v}");
    assert!(
        v["result"]["list_failures_by_tag"]["incomplete"]
            .as_u64()
            .unwrap_or(0)
            >= 1,
        "{v}"
    );
}

/// One child hidden from every listing: it is never adopted, so identity
/// coverage cannot reach the expected count, the missing identity is listed,
/// and the run never passes.
#[test]
fn calibrate_a_hidden_child_lowers_identity_and_never_passes() {
    let (rc, v) = calibrate_injected("hide-one", &[]);
    assert_eq!(rc, Some(1), "{v}");
    assert_eq!(v["result"]["list_faults_injected"], 1, "{v}");
    assert!(v["identity_confirmed"].as_u64().unwrap() <= 9, "{v}");
    assert!(
        !v["missing_identities"].as_array().unwrap().is_empty(),
        "{v}"
    );
    assert_eq!(v["meets_threshold"], false, "{v}");
}

/// One missed (hidden) + one foreign (unlisted) child in the same run: the
/// adopted count can still add up, but identity cannot. The run must be
/// unverified for `adopted_not_created` with a non-zero exit, and identity
/// stays below the expected count (DECISION-10 3, end to end).
#[test]
fn calibrate_one_missed_and_one_foreign_is_unverified() {
    let (rc, v) = calibrate_injected("hide-one", &["--inject-unlisted"]);
    assert_eq!(rc, Some(1), "{v}");
    assert_eq!(v["status"], "unverified", "{v}");
    assert_eq!(v["result"]["list_faults_injected"], 1, "{v}");
    let reasons = v["unverified_reasons"].to_string();
    assert!(reasons.contains("\"adopted_not_created\""), "{v}");
    assert!(
        v["miscount_checks"]["adopted_not_created"]
            .as_u64()
            .unwrap()
            >= 1,
        "{v}"
    );
    assert!(v["identity_confirmed"].as_u64().unwrap() <= 9, "{v}");
    assert_eq!(v["meets_threshold"], false, "{v}");
}
