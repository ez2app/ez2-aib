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
        // the marker sweep matches `AIBD_SESSION=<id>` across every same-user
        // process: a fixed id let another test run on this machine (a full
        // suite next to a single test, or parallel copies) find and stop this
        // run's fixture before its first line (a63ab 11-09 "JSON EOF")
        session_id: format!("{session}-{}", std::process::id()),
        watch_forks,
        // the layer tests run without M1, so they check the layer alone
        lineage: None,
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

/// 043dd #510 H3: when nothing proves the root's group number is still ours
/// (root recorded with another start time = "its pid is someone else's now",
/// no checked member left in that group), no group signal goes out; the
/// checked single pids are still stopped.
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
/// `tree-stable` has no intermediate that exits at once: with one, lineage is
/// honestly incomplete whenever it exits before its unique id is read (H2),
/// which under a parallel test run is most of the time.
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
        watch_forks: false,
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
        ..spec("tree", "s-tree-incomplete")
    })
    .unwrap();
    let _ = ready_pids(&first_line(&mut h));
    let rep = h.cancel(Duration::from_millis(300)).unwrap();
    assert!(rep.no_recorded_survivors(), "{rep:?}");
    assert!(rep.lineage_unavailable, "{rep:?}");
    assert_eq!(rep.clean_status(), "unverified");
}
