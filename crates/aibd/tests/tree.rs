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
        inject: None,
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
        env_clear: false,
        // the marker sweep matches `AIBD_SESSION=<id>` across every same-user
        // process: a fixed id let another test run on this machine (a full
        // suite next to a single test, or parallel copies) find and stop this
        // run's fixture before its first line (a63ab 11-09 "JSON EOF")
        session_id: format!("{session}-{}", std::process::id()),
        watch_forks,
        discard_output: false, // these tests read the fixture's stdout
        // the layer tests run without M1, so they check the layer alone
        lineage: None,
        inject: None,
    }
}

/// `tree-platform` / `tree-plain` fork once the go file exists. Started this
/// way, the fork happens after `spawn_tree` returned, so the fork watcher is
/// already on the root: under load the fork could otherwise come before the
/// watch (forks_seen 0, the exiting parent unreadable). The returned guard
/// gives the go and removes the file.
struct Go(std::path::PathBuf);

impl Go {
    fn give(&self) {
        std::fs::write(&self.0, b"go").unwrap();
    }
}

impl Drop for Go {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn spawn_after_watch(mode: &str, session: &str) -> (TreeHandle, Go) {
    let go =
        Go(std::env::temp_dir().join(format!("aib-fixture-go-{}-{session}", std::process::id())));
    let _ = std::fs::remove_file(&go.0);
    let mut s = spec_lineage(mode, session);
    s.env
        .push(("AIB_FIXTURE_GO".into(), go.0.clone().into_os_string()));
    let h = spawn_tree(&s).unwrap();
    go.give();
    (h, go)
}

fn lines(h: &mut TreeHandle) -> Vec<serde_json::Value> {
    let out = h.take_stdout().unwrap();
    BufReader::new(out)
        .lines()
        .map_while(Result::ok)
        .filter_map(|l| serde_json::from_str(&l).ok())
        .collect()
}

/// The fixture's first stdout line as JSON. A closed stdout with no line
/// (a63ab 11-09: "JSON EOF", 1 in 5 under heavy load) is said as what it is,
/// with how the fixture ended, instead of a bare serde EOF.
fn first_line(h: &mut TreeHandle) -> serde_json::Value {
    let out = h.take_stdout().unwrap();
    let mut r = BufReader::new(out);
    let mut l = String::new();
    let n = r.read_line(&mut l);
    if matches!(n, Ok(0)) {
        let root = h.root_pid();
        let exit = h.wait_root(Duration::from_secs(2));
        panic!(
            "fixture {root} closed stdout before its first line (killed or crashed); \
             root exit within 2 s: {exit:?} (Some(-1) = by a signal)"
        );
    }
    let l = l.trim_end();
    serde_json::from_str(l)
        .unwrap_or_else(|e| panic!("fixture's first line is not JSON ({e}): {l:?}"))
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

/// What this machine's process reader sees in a system `/bin/sleep`'s
/// environment, read the way `check_candidates` reads it. Apple platform
/// binaries hide it on a stock Mac; the GitHub macOS runner shows it (11-08,
/// #510 CI). The two tests below assert the branch that applies and say which
/// one on stderr (not captured, so it is in the CI log). An unclear probe is a
/// failure, never a silent pick of the weaker branch.
#[cfg(target_os = "macos")]
#[derive(Debug, Clone, Copy, PartialEq)]
enum PlatformEnv {
    Hidden,
    Readable,
}

#[cfg(target_os = "macos")]
fn platform_env(test: &str) -> PlatformEnv {
    use std::io::Write;
    use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};
    let mut c = Command::new("/bin/sleep")
        .arg("30")
        .env_clear()
        .env("AIB_ENV_PROBE", "1")
        .spawn()
        .unwrap();
    let pid = c.id();
    let mut sys = System::new().unwrap();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::All,
        true,
        ProcessRefreshKind::nothing()
            .without_tasks()
            .with_environ(UpdateKind::OnlyIfNotSet),
    );
    let env: Option<Vec<String>> = sys.process(Pid::from_u32(pid)).map(|p| {
        p.environ()
            .iter()
            .map(|e| e.to_string_lossy().into_owned())
            .collect()
    });
    let _ = c.kill();
    let _ = c.wait();
    let got = match &env {
        Some(e) if e.is_empty() => PlatformEnv::Hidden,
        Some(e) if e.iter().any(|v| v == "AIB_ENV_PROBE=1") => PlatformEnv::Readable,
        other => panic!("platform env probe unclear for /bin/sleep {pid}: {other:?}"),
    };
    let _ = writeln!(
        std::io::stderr(),
        "[{test}] /bin/sleep environment: {got:?}"
    );
    got
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
        tree: rep.tree_status(),
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
        tree: rep.tree_status(),
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
        rep.no_recorded_survivors(),
        "survivors: {:?} steps: {:?}",
        rep.survivors,
        rep.steps
    );
    assert!(rep.tree_size >= 4, "{rep:?}");
    // the root still ran with its recorded identity: the group signal was allowed
    assert!(
        rep.steps.iter().any(|s| s.signal == "SIGINT(group)"),
        "{:?}",
        rep.steps
    );
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
    assert!(rep.no_recorded_survivors(), "{rep:?}");
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

/// `/bin/sh` that starts `sleep 60`, prints its pid and exits 0.3 s later
/// (so the fork watcher records the child: `/bin/sleep`'s environment cannot
/// be read, a known macOS blind spot): the root is gone, its grandchild is
/// still in the group.
fn sh_root_exits(session: &str) -> (TreeHandle, u32) {
    let mut h = spawn_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), "sleep 60 & echo $!; sleep 0.3; exit 0".into()],
        ..spec_watch("unused", session, true)
    })
    .unwrap();
    let out = h.take_stdout().unwrap();
    let mut l = String::new();
    BufReader::new(out).read_line(&mut l).unwrap();
    let gc: u32 = l.trim().parse().unwrap();
    assert!(
        h.root_watch().wait(Duration::from_secs(5)).is_some(),
        "root did not exit"
    );
    (h, gc)
}

fn ps_stat(pid: u32) -> String {
    let o = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

/// bb7dc H1: after the root exits by itself it stays unreaped (a zombie we
/// hold) until the last signal, so the group signal is provably ours; only
/// then is it reaped.
#[cfg(target_os = "macos")]
#[test]
fn h1_the_root_stays_unreaped_until_the_group_signal_then_is_reaped() {
    let (mut h, gc) = sh_root_exits("s-h1-keep");
    let root = h.root_pid();
    assert!(
        ps_stat(root).starts_with('Z'),
        "root {root} was reaped early: {:?}",
        ps_stat(root)
    );
    let rep = h.cleanup(Duration::from_millis(300)).unwrap();
    let _ = Command::new("kill").args(["-9", &gc.to_string()]).status();
    assert!(
        rep.steps.iter().any(|s| s.signal == "SIGTERM(group)"),
        "{:?}",
        rep.steps
    );
    assert_eq!(still_running(&[gc]), Vec::<u32>::new(), "{rep:?}");
    assert_eq!(ps_stat(root), "", "root {root} not reaped after the stop");
    assert_eq!(h.root_end().and_then(|e| e.code), Some(0));
}

/// bb7dc H1: the root was reaped by someone else (here: this test, as
/// another handler would): the group number is no longer provably ours, so
/// no group signal, the loss is latched and reported; the recorded
/// grandchild is still stopped by its own checked pid.
#[cfg(target_os = "macos")]
#[test]
fn h1_a_root_reaped_elsewhere_gets_no_group_signal_and_is_reported() {
    let (mut h, gc) = sh_root_exits("s-h1-lost");
    let root = h.root_pid();
    let _ = nix::sys::wait::waitpid(nix::unistd::Pid::from_raw(root as i32), None);
    let rep = h.cleanup(Duration::from_millis(300)).unwrap();
    let again = h.cleanup(Duration::from_millis(300)).unwrap();
    let _ = Command::new("kill").args(["-9", &gc.to_string()]).status();
    assert!(
        rep.steps.iter().all(|s| !s.signal.ends_with("(group)")),
        "group signal after the root was reaped elsewhere: {:?}",
        rep.steps
    );
    assert!(rep.known_gaps.contains(&"root_identity_lost"), "{rep:?}");
    assert_ne!(rep.clean_status(), "clean");
    assert!(
        again.steps.iter().all(|s| !s.signal.ends_with("(group)")),
        "latch: {:?}",
        again.steps
    );
    assert_eq!(still_running(&[gc]), Vec::<u32>::new(), "{rep:?}");
}

/// 043dd #510 H3: when nothing proves the root's group number is still ours
/// (root recorded with another start time = "its pid is someone else's now",
/// no checked member left in that group), no group signal goes out; the
/// checked single pids are still stopped. Not on macOS: there the root is
/// kept unreaped (H1), so its pid cannot be someone else's; the macOS form
/// is `h1_a_root_reaped_elsewhere_gets_no_group_signal_and_is_reported`.
#[cfg(not(target_os = "macos"))]
#[test]
fn no_group_signal_when_the_group_number_is_not_provably_ours() {
    let mut h = spawn_tree(&spec_watch("tree", "s-tree-h3", false)).unwrap();
    let v = first_line(&mut h);
    let pids = ready_pids(&v);
    let main = v["pids"]["main"].as_u64().unwrap() as u32;
    let child = v["pids"]["child"].as_u64().unwrap() as u32;
    let escapee = v["pids"]["escapee"].as_u64().unwrap() as u32;
    let orphan = v["pids"]["orphan"].as_u64().unwrap() as u32;
    // the only other member of the root's group goes first (ours: the fixture printed it)
    let _ = Command::new("kill")
        .args(["-9", &child.to_string()])
        .status();
    h.record_for_test(main, 1);
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    // collect what is ours whatever the assertions say
    let _ = Command::new("kill")
        .args(["-9", &main.to_string()])
        .status();
    assert!(
        rep.steps.iter().all(|s| !s.signal.ends_with("(group)")),
        "a group signal went to a group number nothing proved ours: {:?}",
        rep.steps
    );
    assert_eq!(
        still_running(&[escapee, orphan]),
        Vec::<u32>::new(),
        "{rep:?}"
    );
    let _ = pids;
}

#[test]
fn second_cancel_is_a_clean_no_op() {
    let mut h = spawn_tree(&spec("tree", "s-tree-twice")).unwrap();
    let _ = ready_pids(&first_line(&mut h));
    assert!(h
        .cancel(Duration::from_millis(300))
        .unwrap()
        .no_recorded_survivors());
    let again = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(again.no_recorded_survivors());
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
    let (mut h, _go) = spawn_after_watch("tree-platform", "s-tree-platform");
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
    let env = platform_env("fork_watcher_catches_reparented_platform_binary");
    let rep = h.cancel(Duration::from_millis(500)).unwrap();
    let by_watch_or_lineage =
        rep.found_by_watch.contains(&orphan) || rep.found_by_lineage.contains(&orphan);
    match env {
        // the marker sweep cannot see it: only the watcher or lineage can
        PlatformEnv::Hidden => {
            assert!(by_watch_or_lineage, "{rep:?}");
            assert!(
                !rep.found_by_marker.contains(&orphan),
                "marker layer saw a hidden environment: {rep:?}"
            );
        }
        // the marker is visible, so the sweep may record it first; it must
        // still be recorded by some layer and stopped
        PlatformEnv::Readable => assert!(
            by_watch_or_lineage || rep.found_by_marker.contains(&orphan),
            "{rep:?}"
        ),
    }
    assert!(rep.forks_seen >= 1, "{rep:?}");
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    // DECISION-23: no proven containment on macOS, so never `clean`
    assert_eq!(rep.clean_status(), "unverified", "{rep:?}");
    assert!(
        matches!(
            rep.unverified_reason(),
            Some("observed_clean" | "candidates_unreadable")
        ),
        "{rep:?}"
    );
    assert_eq!(still_running(&[main, orphan]), Vec::<u32>::new());
}

/// 043dd #510: a regular program's environment is readable, so the marker
/// sweep can see a re-parented process the fork watcher already recorded.
/// It stays `found_by_watch`: `found_by_marker` means only the marker found it.
#[cfg(target_os = "macos")]
#[test]
fn a_watched_process_with_a_readable_marker_stays_found_by_watch() {
    let (mut h, _go) = spawn_after_watch("tree-plain", "s-tree-plain");
    let v = first_line(&mut h);
    assert_eq!(v["type"], "ready", "{v}");
    let main = v["pids"]["main"].as_u64().unwrap() as u32;
    let orphan = v["pids"]["platform_orphan"]
        .as_u64()
        .unwrap_or_else(|| panic!("fixture did not report the orphan: {v}"))
        as u32;
    // preconditions: re-parented once its parent left, and already recorded
    // by the watcher (its parent stayed 300 ms for that)
    let mut ready = false;
    for _ in 0..100 {
        if ps_field(orphan, "ppid") == "1" && h.watched_for_test().contains(&orphan) {
            ready = true;
            break;
        }
        #[allow(clippy::disallowed_methods)] // test pacing only
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(
        ready,
        "precondition: ppid {} watched {:?}",
        ps_field(orphan, "ppid"),
        h.watched_for_test()
    );
    let rep = h.cancel(Duration::from_millis(500)).unwrap();
    let _ = Command::new("kill")
        .args(["-9", &main.to_string(), &orphan.to_string()])
        .status();
    assert!(rep.found_by_watch.contains(&orphan), "{rep:?}");
    assert!(
        !rep.found_by_marker.contains(&orphan),
        "the marker sweep claimed what the watcher found: {rep:?}"
    );
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified", "{rep:?}");
}

/// DECISION-4 A-4: without lineage a cancel that saw no survivor may still
/// have missed one, so it is `unverified`, never `clean`, and says why.
#[test]
fn without_lineage_a_clean_looking_cancel_is_unverified() {
    let mut h = spawn_tree(&spec("tree", "s-tree-nolineage")).unwrap();
    let _ = ready_pids(&first_line(&mut h));
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    assert!(rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
    assert_eq!(rep.unverified_reason(), Some("lineage_unavailable"));
    let spots = rep.blind_spots();
    assert_eq!(spots[0], "reparented_after_intermediate_exit", "{spots:?}");
    assert!(spots.contains(&"exec_after_creator_exit"), "{spots:?}");
}

/// DECISION-23: with lineage on and nothing left, macOS still does not say
/// `clean` (no proven containment): `unverified` with `observed_clean`, or
/// `candidates_unreadable` if launchd started a same-user agent meanwhile.
/// `tree-stable` has no intermediate that exits at once (that case:
/// `with_lineage_an_intermediate_that_exits_at_once_is_not_lineage_unavailable`).
#[cfg(target_os = "macos")]
#[test]
fn with_lineage_a_clean_looking_cancel_is_observed_clean_never_clean() {
    let mut h = spawn_tree(&spec_lineage("tree-stable", "s-tree-lineage")).unwrap();
    let v = first_line(&mut h);
    assert_eq!(v["type"], "ready", "{v}");
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert!(rep.candidates_complete, "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified", "{rep:?}");
    let why = rep.unverified_reason();
    assert!(
        matches!(why, Some("observed_clean" | "candidates_unreadable")),
        "{why:?} {rep:?}"
    );
    assert!(rep.blind_spots().contains(&"exec_after_creator_exit"));
}

/// The hole DECISION-23 is about, on the real OS: a child that clears its
/// environment, outlives its creator and exec's afterwards is invisible to
/// lineage and to the marker sweep. The cancel must not look clean: it is a
/// candidate whose environment cannot be read. It is never signalled; this
/// test stops it itself, by the pid it printed.
#[cfg(target_os = "macos")]
#[test]
fn an_env_cleared_late_exec_makes_the_cancel_candidates_unreadable() {
    let mut h = spawn_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec![
            "-c".into(),
            "env -i /bin/sh -c '(/bin/sleep 0.5; exec /bin/sleep 30) >/dev/null 2>&1 & echo $!'"
                .into(),
        ],
        lineage: Some(lineage()),
        inject: None,
        watch_forks: false,
        discard_output: false,
        ..spec("complete", "s-env-cleared")
    })
    .unwrap();
    let mut line = String::new();
    BufReader::new(h.take_stdout().unwrap())
        .read_line(&mut line)
        .unwrap();
    let pid: u32 = line.trim().parse().unwrap();
    h.wait_root(Duration::from_secs(10)).unwrap();
    // wait until it has exec'd /bin/sleep (it is no longer anyone's child)
    let mut execd = false;
    for _ in 0..100 {
        if ps_field(pid, "comm").ends_with("sleep") {
            execd = true;
            break;
        }
        #[allow(clippy::disallowed_methods)] // test pacing only
        std::thread::sleep(Duration::from_millis(20));
    }
    let env = platform_env("an_env_cleared_late_exec_makes_the_cancel_candidates_unreadable");
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    let left = still_running(&[pid]);
    let _ = Command::new("/bin/kill").arg(pid.to_string()).status();
    assert!(execd, "{pid} never exec'd");
    assert_eq!(left, vec![pid], "it is not ours to stop: {rep:?}");
    assert!(
        rep.steps.iter().all(|s| !s.targets.contains(&pid)),
        "{rep:?}"
    );
    assert!(rep.candidates >= 1, "it is a late-exec candidate: {rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
    match env {
        // nothing readable: it cannot be ruled out
        PlatformEnv::Hidden => {
            assert!(rep.candidates_unreadable.contains(&pid), "{rep:?}");
            assert_eq!(rep.unverified_reason(), Some("candidates_unreadable"));
        }
        // readable without our marker: taken as someone else's, which is the
        // listed `env_cleared` blind spot, so still never clean
        PlatformEnv::Readable => {
            assert!(!rep.candidates_unreadable.contains(&pid), "{rep:?}");
            assert!(rep.found_by_marker.is_empty(), "{rep:?}");
            assert_eq!(rep.unverified_reason(), Some("observed_clean"), "{rep:?}");
            assert!(rep.blind_spots().contains(&"env_cleared"), "{rep:?}");
        }
    }
}

/// A process older than the recorded process it claims as parent is a reused
/// parent pid, not a child: the scan must not adopt it. Only `scan()` and
/// `known()` are called, so nothing is ever signalled.
#[test]
fn a_process_older_than_its_recorded_parent_is_not_adopted() {
    let mut bystander = Command::new("sleep").arg("30").spawn().unwrap();
    let bpid = bystander.id();
    let mut h = spawn_tree(&spec_watch("complete", "s-parent-age", false)).unwrap();
    let _ = lines(&mut h);
    h.wait_root(Duration::from_secs(10)).unwrap();
    // Record this test process as if it had started far in the future: its
    // real child `bystander` is then older than that "parent".
    h.record_for_test(std::process::id(), u64::MAX / 2);
    h.scan().unwrap();
    let known = h.known();
    bystander.kill().unwrap();
    bystander.wait().unwrap();
    assert!(!known.contains(&bpid), "adopted {bpid}: {known:?}");
}

/// A lineage pass that cannot list the processes is incomplete, so a cancel
/// that looks clean must still say `unverified`, not `clean`.
#[test]
fn an_incomplete_lineage_pass_makes_a_clean_looking_cancel_unverified() {
    use aib_sys::fake::{Call, FakeProcessInfo};
    use aib_sys::SysError;
    let f = FakeProcessInfo::new();
    f.fail(Call::AllPids, 0, SysError::Incomplete);
    let l = Arc::new(Lineage::from_selfcheck(Arc::new(f), Ok(())));
    let mut h = spawn_tree(&SpawnSpec {
        lineage: Some(l),
        inject: None,
        ..spec("tree", "s-tree-incomplete")
    })
    .unwrap();
    let _ = ready_pids(&first_line(&mut h));
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    assert!(rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
}

/// DECISION-lineage-gone-unread (c477a 11-13): a recorded process gone before
/// its unique id was read. `ghost` is recorded (as a scan would) but is not in
/// the process table, so the lineage pass finds it gone. `orphan` (optional)
/// is a same-user launchd child newer than the root whose creator (2000) is
/// a dead, unrecorded process newer than the root: what such a ghost leaves
/// behind. It is only in the fake table, never in the real one, so it is
/// never signalled; the candidate check then cannot rule it out.
fn gone_unread_cancel(
    session: &str,
    orphan: Option<u32>,
    root_readable: bool,
) -> aibd::platform::tree::Report {
    use aib_sys::fake::FakeProcessInfo;
    let me = std::process::id();
    let f = Arc::new(FakeProcessInfo::new());
    f.add(me, 1, 10, 1, 1);
    let l = Arc::new(Lineage::from_selfcheck(f.clone(), Ok(())));
    let mut h = spawn_tree(&SpawnSpec {
        lineage: Some(l),
        inject: None,
        ..spec_watch("complete", session, false)
    })
    .unwrap();
    let root = h.root_pid();
    let _ = lines(&mut h);
    h.wait_root(Duration::from_secs(10)).unwrap();
    // the root's unique id: read by the next lineage pass (it was not in the table at spawn)
    if root_readable {
        f.add(root, me, 1000, 10, 50);
    }
    // above Linux PID_MAX_LIMIT (4194304) and macOS PID_MAX (99999): never a real process
    let ghost = 4_194_305;
    h.record_for_test(ghost, 60);
    if let Some(o) = orphan {
        f.add(o, 1, 3000, 2000, 70);
    }
    h.cancel(Duration::from_millis(300)).unwrap()
}

/// Knife for `adopt`'s `Gone ⇒ gone_unread` (put `complete = false` back and
/// this is `lineage_unavailable` again, the slot never comes back).
#[test]
fn a_recorded_process_gone_before_its_unique_id_was_read_is_not_lineage_unavailable() {
    let rep = gone_unread_cancel("s-gone-unread", None, true);
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    assert!(rep.candidates_complete, "{rep:?}");
    assert_eq!(rep.orphan_candidates_extra, 0, "{rep:?}");
    assert_eq!(rep.unverified_reason(), Some("observed_clean"), "{rep:?}");
}

/// Knife for `lineage_pass`'s `gone_unread ⇒ unread_gone`: the ghost's
/// orphan is found only by the widened check (its creator is no recorded
/// process and not launchd), so without it this would be `observed_clean`.
#[test]
fn an_orphan_of_a_process_gone_before_its_unique_id_was_read_keeps_the_cancel_from_clean() {
    let orphan = 4_194_306; // above any real pid, like `ghost`
    let rep = gone_unread_cancel("s-gone-unread-orphan", Some(orphan), true);
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.orphan_candidates_extra, 1, "{rep:?}");
    assert!(rep.candidates_unreadable.contains(&orphan), "{rep:?}");
    assert_ne!(rep.unverified_reason(), Some("observed_clean"), "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
}

/// a9136 (DECISION-lineage-gone-unread): the root itself never had its unique
/// id read (gone or unreadable). Nothing can be linked to it, so the widened
/// check cannot run: still never `observed_clean` (`candidates_incomplete`).
#[test]
fn a_root_whose_unique_id_was_never_read_is_never_observed_clean() {
    let rep = gone_unread_cancel("s-gone-unread-root", None, false);
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert!(!rep.candidates_complete, "{rep:?}");
    assert_eq!(
        rep.unverified_reason(),
        Some("candidates_incomplete"),
        "{rep:?}"
    );
    assert_eq!(rep.clean_status(), "unverified");
}

/// The gate_replay G1 flake on the real OS: `tree` has an intermediate that
/// forks and exits at once, so it is often gone before its unique id is read
/// (before: `lineage_unavailable` most of the time under a parallel run, and
/// the session's slot was never released). Now never `lineage_unavailable`.
#[cfg(target_os = "macos")]
#[test]
fn with_lineage_an_intermediate_that_exits_at_once_is_not_lineage_unavailable() {
    let mut h = spawn_tree(&spec_lineage("tree", "s-tree-lineage-mid")).unwrap();
    let v = first_line(&mut h);
    assert_eq!(v["type"], "ready", "{v}");
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(!rep.lineage_unavailable, "{rep:?}");
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    let why = rep.unverified_reason();
    assert!(
        matches!(why, Some("observed_clean" | "candidates_unreadable")),
        "{why:?} {rep:?}"
    );
}

/// bb7dc #544 High: a start that fails after `spawn()` succeeded. The shell
/// starts a long `sleep` in its group and waits for it, so without a stop
/// the root would live ~60 s.
fn group_members(pgid: u32) -> String {
    let o = Command::new("pgrep")
        .args(["-g", &pgid.to_string()])
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

fn failing_start(
    session: &str,
    at: aibd::platform::tree::Inject,
) -> (aibd::platform::tree::StartFailed, std::time::Instant) {
    use aibd::platform::tree::{start_tree, StartFailedOr};
    let t = std::time::Instant::now();
    let r = start_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), "sleep 60 & wait".into()],
        inject: Some(at),
        ..spec_watch("unused", session, true)
    });
    match r {
        Err(StartFailedOr::Started(f)) => (f, t),
        Err(StartFailedOr::NotStarted(e)) => panic!("reported as not started: {e}"),
        Ok(_) => panic!("the injected failure did not fail"),
    }
}

/// Under control (the handle exists, for every failure point): the failure
/// stops the tree like a DELETE, every signal through the H1 checks, and
/// reports `left_running` exactly when that stop does not release a slot.
/// The root is reaped too (no zombie left behind).
fn a_late_failure_is_stopped(session: &str, at: aibd::platform::tree::Inject) {
    let (f, _) = failing_start(session, at);
    let root = f.root();
    let t = std::time::Instant::now();
    let fail = aibd::platform::session_unix::settle_failed_start(f);
    assert!(t.elapsed() < Duration::from_secs(10), "not stopped");
    assert!(
        still_running(&[root]).is_empty(),
        "root {root} still running"
    );
    assert_eq!(group_members(root), "", "group {root} not empty");
    assert_eq!(ps_stat(root), "", "root {root} left as a zombie");
    assert!(
        fail.left_running.is_none() || fail.left_running == Some(root),
        "{fail:?}"
    );
}

/// No root-exit thread (bb7dc #544 c5603b91): still a handle, never a raw
/// group kill; the handle reaps the root itself.
#[test]
fn a_root_wait_failure_is_stopped_through_the_handle() {
    a_late_failure_is_stopped("s-fail-rootwait", aibd::platform::tree::Inject::RootWait);
}

#[test]
fn a_system_failure_after_spawn_stops_the_tree() {
    a_late_failure_is_stopped("s-fail-system", aibd::platform::tree::Inject::System);
}

#[cfg(target_os = "macos")]
#[test]
fn a_fork_watch_failure_after_spawn_stops_the_tree() {
    a_late_failure_is_stopped("s-fail-watch", aibd::platform::tree::Inject::ForkWatch);
}

/// G1 root_unrecorded (DECISION-root-unrecorded-a9136-1110): a program that
/// exits at once, recorded 300 ms late, is still recorded on macOS: it is
/// held before exec until it is. Without the hold it is gone by then.
#[cfg(target_os = "macos")]
fn held_spec(session: &str, program: &str, args: &[&str]) -> SpawnSpec {
    SpawnSpec {
        program: program.into(),
        args: args.iter().map(|a| (*a).into()).collect(),
        env: vec![],
        discard_output: true,
        inject: Some(aibd::platform::tree::Inject::RecordLate),
        // a63ab 11-10 ruling A: with the watcher alone, a middle process that
        // forks and exits at once is missed about 1 run in 3 under a full
        // suite (fork_watch_miss); M1 lineage links the grandchild back to
        // the recorded middle, as a real session does.
        lineage: Some(lineage()),
        ..spec_watch("unused", session, true)
    }
}

#[cfg(target_os = "macos")]
#[test]
fn an_instant_program_is_recorded_before_it_can_exit() {
    let mut h = spawn_tree(&held_spec("s-held-true", "/usr/bin/true", &[])).unwrap();
    let rep = h.cleanup(Duration::from_millis(300)).unwrap();
    assert!(
        !rep.known_gaps.contains(&"root_unrecorded"),
        "recorded too late: {rep:?}"
    );
}

/// For a failure message only: the process line with its environment
/// (`ps -E`), to tell "not a candidate" from "read as not ours".
#[cfg(target_os = "macos")]
fn ps_env(pid: u32) -> String {
    Command::new("ps")
        .args([
            "-wwE",
            "-o",
            "pid,ppid,stat,command",
            "-p",
            &pid.to_string(),
        ])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default()
}

/// 043dd / bb7dc / Codex counterexample: the root forks at once, the child
/// clears the environment, leaves the session (`setsid`) and execs a long
/// sleep, the root exits. Held from before exec, the root is recorded. The
/// sleep is stopped when the watcher saw the middle process; when it did
/// not, it is reported as an unreadable candidate and the cancel is not
/// observed_clean (never silently left).
#[cfg(target_os = "macos")]
#[test]
fn a_detached_grandchild_of_an_instant_root_is_recorded_and_stopped() {
    let dir = std::env::temp_dir().join(format!("aibd-held-gc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let f = dir.join("pid");
    let script = format!(
        "( /usr/bin/env -i /usr/bin/perl -MPOSIX -e \
         'POSIX::setsid(); open(F, \">{f}\"); print F $$; close F; exec q(/bin/sleep), 30' & ) ; exit 0",
        f = f.display()
    );
    let mut h = spawn_tree(&held_spec("s-held-gc", "/bin/sh", &["-c", &script])).unwrap();
    let t = std::time::Instant::now();
    let gc = loop {
        if let Some(p) = std::fs::read_to_string(&f)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
        {
            break p;
        }
        assert!(t.elapsed() < Duration::from_secs(10), "no grandchild pid");
        #[allow(clippy::disallowed_methods)] // test pacing only
        std::thread::sleep(Duration::from_millis(20));
    };
    let rep = h.cleanup(Duration::from_millis(500)).unwrap();
    let left = still_running(&[gc]);
    let _ = Command::new("kill").args(["-9", &gc.to_string()]).status();
    let _ = std::fs::remove_dir_all(&dir);
    assert!(
        !rep.known_gaps.contains(&"root_unrecorded"),
        "recorded too late: {rep:?}"
    );
    // a63ab 11-10 ruling B (A measured 30/50 on Mac Pro 13): the grandchild
    // clears its environment and execs after its creator exited, so neither
    // lineage (parent_unique = 1) nor the marker can tie it to the session;
    // when the fork watcher missed the middle process it is an unreadable
    // candidate. That is never signalled; the cancel must then say so and
    // never claim observed_clean. Either way nothing is left unaccounted.
    if !left.is_empty() {
        assert!(
            rep.candidates_unreadable.contains(&gc),
            "grandchild {gc} left running and not reported: {rep:?}\nps: {}",
            ps_env(gc)
        );
        assert_eq!(
            rep.unverified_reason(),
            Some("candidates_unreadable"),
            "a left grandchild must not read as observed_clean: {rep:?}"
        );
    }
}

/// #745: `hold-gc`, the program of the Windows sessions tests, run here too: the root
/// exits with its code after `root_ms`, and the child it wrote down outlives it.
#[test]
fn hold_gc_root_exits_with_its_code_and_its_child_stays() {
    let tmp = tempfile::tempdir().unwrap();
    let f = tmp.path().join("gc");
    let st = Command::new(fixture())
        .args(["hold-gc", f.to_str().unwrap(), "100", "3"])
        .env("AIB_FIXTURE_TTL", "20")
        .status()
        .unwrap();
    assert_eq!(st.code(), Some(3), "{st}");
    let pid: i32 = std::fs::read_to_string(&f).unwrap().trim().parse().unwrap();
    let p = nix::unistd::Pid::from_raw(pid);
    let alive = nix::sys::signal::kill(p, None).is_ok();
    let _ = nix::sys::signal::kill(p, nix::sys::signal::Signal::SIGKILL);
    assert!(alive, "child {pid} ended with its root");
}
