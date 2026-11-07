#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! ADR 0004 on a real OS process tree, driven by the TEST FIXTURE
//! `aib-fixture-tree` (not a provider). Unix only until the Windows Job Object
//! path exists.
#![cfg(unix)]

use aib_core::outcome::{decide, Observed};
use aib_core::state::SessionState;
use aibd::platform::tree::{spawn_tree, SpawnSpec, TreeHandle};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

fn fixture() -> PathBuf {
    // target/<profile>/deps/<test-bin> -> target/<profile>/aib-fixture-tree
    let exe = std::env::current_exe().unwrap();
    let p = exe
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("aib-fixture-tree");
    assert!(
        p.exists(),
        "{} missing: run `cargo test --workspace` (builds the fixture) or `cargo build -p aib-fixture-tree`",
        p.display()
    );
    p
}

fn spec(mode: &str, session: &str) -> SpawnSpec {
    SpawnSpec {
        program: fixture(),
        args: vec![mode.into()],
        cwd: None,
        env: vec![("AIB_FIXTURE_TTL".into(), "30".into())],
        session_id: session.to_string(),
    }
}

fn lines(h: &mut TreeHandle) -> Vec<serde_json::Value> {
    let out = h.take_stdout().unwrap();
    BufReader::new(out)
        .lines()
        .map_while(Result::ok)
        .filter_map(|l| serde_json::from_str(&l).ok())
        .collect()
}

fn first_line(h: &mut TreeHandle) -> serde_json::Value {
    let out = h.take_stdout().unwrap();
    let mut r = BufReader::new(out);
    let mut l = String::new();
    r.read_line(&mut l).unwrap();
    serde_json::from_str(&l).unwrap()
}

/// Independent check with ps(1): pids that are still running (not zombies).
fn still_running(pids: &[u32]) -> Vec<u32> {
    pids.iter()
        .copied()
        .filter(|p| {
            let o = Command::new("ps")
                .args(["-o", "stat=", "-p", &p.to_string()])
                .output()
                .unwrap();
            let st = String::from_utf8_lossy(&o.stdout).trim().to_string();
            !st.is_empty() && !st.starts_with('Z')
        })
        .collect()
}

fn ps_field(pid: u32, field: &str) -> String {
    let o = Command::new("ps")
        .args(["-o", &format!("{field}="), "-p", &pid.to_string()])
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

fn ready_pids(v: &serde_json::Value) -> Vec<u32> {
    assert_eq!(v["type"], "ready", "{v}");
    let m = v["pids"].as_object().unwrap();
    for r in ["main", "child", "escapee", "orphan"] {
        assert!(m.contains_key(r), "fixture did not report {r}: {v}");
    }
    m.values().map(|x| x.as_u64().unwrap() as u32).collect()
}

#[test]
fn completed_event_means_completed() {
    let mut h = spawn_tree(&spec("complete", "s-complete")).unwrap();
    let evs = lines(&mut h);
    let code = h.wait_root(Duration::from_secs(10)).unwrap();
    let rep = h.cleanup(Duration::from_millis(500)).unwrap();
    let o = decide(Observed {
        completed_event: evs.iter().any(|e| e["type"] == "completed"),
        exit_code: code,
        tree_clean: rep.tree_clean(),
        ..Default::default()
    });
    assert_eq!(code, Some(0));
    assert_eq!(o.state, SessionState::Completed);
}

#[test]
fn exit_zero_without_completion_is_interrupted() {
    let mut h = spawn_tree(&spec("no-complete", "s-nocomplete")).unwrap();
    let evs = lines(&mut h);
    let code = h.wait_root(Duration::from_secs(10)).unwrap();
    let rep = h.cleanup(Duration::from_millis(500)).unwrap();
    let o = decide(Observed {
        completed_event: evs.iter().any(|e| e["type"] == "completed"),
        exit_code: code,
        tree_clean: rep.tree_clean(),
        ..Default::default()
    });
    assert_eq!(code, Some(0));
    assert_eq!(o.state, SessionState::Interrupted);
    assert_eq!(o.reason, Some("missing_completion_event"));
}

#[test]
fn cancel_clears_child_escapee_and_orphan() {
    let mut h = spawn_tree(&spec("tree", "s-tree-scanned")).unwrap();
    let v = first_line(&mut h);
    let pids = ready_pids(&v);
    let main = v["pids"]["main"].as_u64().unwrap() as u32;
    let escapee = v["pids"]["escapee"].as_u64().unwrap() as u32;
    // precondition: the escapee really left the CLI's process group
    assert_ne!(ps_field(main, "pgid"), ps_field(escapee, "pgid"));
    h.scan().unwrap(); // the periodic scan a running session gets
                       // layer 2 on its own must already see the same-group child and the escapee
                       // (the marker sweep would hide a broken scan otherwise)
    let child = v["pids"]["child"].as_u64().unwrap() as u32;
    let known = h.known();
    assert!(
        known.contains(&child) && known.contains(&escapee),
        "{known:?}"
    );
    let rep = h.cancel(Duration::from_millis(500)).unwrap();
    assert!(
        rep.tree_clean(),
        "survivors: {:?} steps: {:?}",
        rep.survivors,
        rep.steps
    );
    assert!(rep.tree_size >= 4, "{rep:?}");
    // main ignores SIGINT and SIGTERM, so the sequence must reach SIGKILL
    assert!(
        rep.steps.iter().any(|s| s.signal == "SIGKILL"),
        "{:?}",
        rep.steps
    );
    assert_eq!(still_running(&pids), Vec::<u32>::new());
}

#[test]
fn marker_sweep_finds_the_reparented_orphan_without_any_scan() {
    let mut h = spawn_tree(&spec("tree", "s-tree-marker")).unwrap();
    let v = first_line(&mut h);
    let pids = ready_pids(&v);
    let orphan = v["pids"]["orphan"].as_u64().unwrap() as u32;
    // precondition: the orphan was re-parented (launchd / init), so a
    // parent-pid scan from the CLI cannot reach it
    assert_eq!(ps_field(orphan, "ppid"), "1");
    // no h.scan(): only the pid recorded at spawn time is known
    assert_eq!(h.known(), vec![h.root_pid()]);
    let rep = h.cancel(Duration::from_millis(500)).unwrap();
    assert!(rep.found_by_marker.contains(&orphan), "{rep:?}");
    assert!(rep.tree_clean(), "{rep:?}");
    assert_eq!(still_running(&pids), Vec::<u32>::new());
}

#[test]
fn reused_pid_with_other_start_time_is_never_signalled() {
    // An unrelated process (not in any session) that we own and clean up.
    let mut bystander = Command::new("sleep").arg("30").spawn().unwrap();
    let bpid = bystander.id();
    let mut h = spawn_tree(&spec("complete", "s-reuse")).unwrap();
    let _ = lines(&mut h);
    h.wait_root(Duration::from_secs(10)).unwrap();
    // Pretend a scan once saw `bpid` with a different start time (pid reuse).
    h.record_for_test(bpid, 1);
    let rep = h.cleanup(Duration::from_millis(300)).unwrap();
    assert!(
        rep.steps.iter().all(|s| !s.targets.contains(&bpid)),
        "{rep:?}"
    );
    assert_eq!(still_running(&[bpid]), vec![bpid], "bystander must survive");
    bystander.kill().unwrap();
    bystander.wait().unwrap();
}

#[test]
fn second_cancel_is_a_clean_no_op() {
    let mut h = spawn_tree(&spec("tree", "s-tree-twice")).unwrap();
    let _ = ready_pids(&first_line(&mut h));
    assert!(h.cancel(Duration::from_millis(300)).unwrap().tree_clean());
    let again = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(again.tree_clean());
    assert!(again.steps.is_empty(), "{:?}", again.steps);
}
