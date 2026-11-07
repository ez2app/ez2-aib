#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! ADR 0004 on a real OS process tree, driven by the TEST FIXTURE
//! `aib-fixture-tree` (not a provider). Unix only until the Windows Job Object
//! path exists.
#![cfg(unix)]

use aib_core::outcome::{decide, Observed};
use aib_core::state::SessionState;
use aibd::lineage::Lineage;
use aibd::platform::tree::{spawn_tree, SpawnSpec, TreeHandle};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

/// Path of the fixture binary, built fresh on first use. `cargo test` does not
/// rebuild another package's binary, so a stale fixture from an earlier build
/// would otherwise be tested silently (it happened, 11-07).
fn fixture() -> PathBuf {
    static BUILD: std::sync::Once = std::sync::Once::new();
    // target/<profile>/deps/<test-bin>
    let exe = std::env::current_exe().unwrap();
    let profile_dir = exe.parent().unwrap().parent().unwrap().to_path_buf();
    let target_dir = profile_dir.parent().unwrap().to_path_buf();
    BUILD.call_once(|| {
        let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
        let mut c = Command::new(cargo);
        c.args([
            "build",
            "-q",
            "--locked",
            "-p",
            "aib-fixture-tree",
            "--target-dir",
        ])
        .arg(&target_dir)
        .current_dir(env!("CARGO_MANIFEST_DIR"));
        if profile_dir.file_name().is_some_and(|n| n == "release") {
            c.arg("--release");
        }
        let st = c.status().unwrap();
        assert!(st.success(), "building aib-fixture-tree failed: {st}");
    });
    let p = profile_dir.join("aib-fixture-tree");
    assert!(p.exists(), "{} missing after build", p.display());
    p
}

fn spec(mode: &str, session: &str) -> SpawnSpec {
    spec_watch(mode, session, true)
}

/// The real lineage of this test process (self-check run once).
fn lineage() -> Arc<Lineage> {
    static L: OnceLock<Arc<Lineage>> = OnceLock::new();
    L.get_or_init(|| Arc::new(Lineage::detect())).clone()
}

/// As aibd runs it: fork watcher plus M1 lineage.
fn spec_lineage(mode: &str, session: &str) -> SpawnSpec {
    SpawnSpec {
        lineage: Some(lineage()),
        ..spec(mode, session)
    }
}

/// `watch_forks = false` tests the scan and marker layers on their own; with
/// the watcher on, it would record everything first and hide a broken layer.
fn spec_watch(mode: &str, session: &str, watch_forks: bool) -> SpawnSpec {
    SpawnSpec {
        program: fixture(),
        args: vec![mode.into()],
        cwd: None,
        env: vec![("AIB_FIXTURE_TTL".into(), "30".into())],
        session_id: session.to_string(),
        watch_forks,
        // the layer tests run without M1, so they check the layer alone
        lineage: None,
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
    let mut h = spawn_tree(&spec_watch("tree", "s-tree-scanned", false)).unwrap();
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
    let mut h = spawn_tree(&spec_watch("tree", "s-tree-marker", false)).unwrap();
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

/// ADR 0004 update: the shape real Claude Code leaked. A short-lived parent
/// starts the system `/bin/sleep` in its own group and exits at once, so the
/// sleep is re-parented to launchd before any scan, and as an Apple platform
/// binary it hides `AIBD_SESSION` from the marker sweep. Only the kqueue fork
/// watcher can record it, or M1 lineage when the watcher listed the parent
/// but missed the child (the watcher alone missed about 1 in 3 under load).
#[cfg(target_os = "macos")]
#[test]
fn fork_watcher_catches_reparented_platform_binary() {
    assert!(lineage().enabled(), "self-check failed: {:?}", lineage());
    let mut h = spawn_tree(&spec_lineage("tree-platform", "s-tree-platform")).unwrap();
    let v = first_line(&mut h);
    assert_eq!(v["type"], "ready", "{v}");
    let main = v["pids"]["main"].as_u64().unwrap() as u32;
    let orphan = v["pids"]["platform_orphan"]
        .as_u64()
        .unwrap_or_else(|| panic!("fixture did not report the orphan: {v}"))
        as u32;
    // preconditions: re-parented, and really the system binary
    assert_eq!(ps_field(orphan, "ppid"), "1");
    assert_eq!(ps_field(orphan, "comm"), "/bin/sleep");
    let rep = h.cancel(Duration::from_millis(500)).unwrap();
    assert!(
        rep.found_by_watch.contains(&orphan) || rep.found_by_lineage.contains(&orphan),
        "{rep:?}"
    );
    assert!(
        !rep.found_by_marker.contains(&orphan),
        "marker layer saw it: {rep:?}"
    );
    assert!(rep.forks_seen >= 1, "{rep:?}");
    assert!(rep.tree_clean(), "{rep:?}");
    assert_eq!(rep.clean_status(), "clean", "{rep:?}");
    assert_eq!(still_running(&[main, orphan]), Vec::<u32>::new());
}

/// DECISION-4 A-4: without lineage a cancel that saw no survivor may still
/// have missed one, so it is `unverified`, never `clean`, and says why.
#[test]
fn without_lineage_a_clean_looking_cancel_is_unverified() {
    let mut h = spawn_tree(&spec("tree", "s-tree-nolineage")).unwrap();
    let _ = ready_pids(&first_line(&mut h));
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(rep.tree_clean(), "{rep:?}");
    assert!(rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
    assert_eq!(
        rep.blind_spots(),
        vec!["reparented_after_intermediate_exit"]
    );
}

/// With lineage on and nothing left, the same cancel may say `clean`.
#[cfg(target_os = "macos")]
#[test]
fn with_lineage_a_clean_cancel_is_clean() {
    let mut h = spawn_tree(&spec_lineage("tree", "s-tree-lineage")).unwrap();
    let _ = ready_pids(&first_line(&mut h));
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.clean_status(), "clean", "{rep:?}");
    assert!(rep.blind_spots().is_empty());
}
