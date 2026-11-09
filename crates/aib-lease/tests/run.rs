// SPDX-License-Identifier: Apache-2.0
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! `aib-lease run` end to end, without a network and without the real lease: a fake `ssh` runs
//! the remote command with `/bin/sh` on this machine, and a fake `aib` logs every lease call and
//! answers `acquire` with `$FAKE_LEASE_RC`. The real `aib` is never reached (`--remote-aib` is
//! an absolute path to the fake, `--remote-path` holds only system folders), and the history
//! line goes to a temp file (`AIB_LEASE_HISTORY`).
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

struct World {
    _tmp: tempfile::TempDir,
    repo: PathBuf,
    ssh: PathBuf,
    aib: PathBuf,
    log: PathBuf,
    remote_tmp: PathBuf,
    out: PathBuf,
    history: PathBuf,
}

fn script(p: &Path, body: &str) {
    std::fs::write(p, body).unwrap();
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755)).unwrap();
}

fn git(repo: &Path, args: &[&str]) {
    let s = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
        .args(args)
        .status()
        .unwrap();
    assert!(s.success(), "git {args:?}");
}

fn world() -> World {
    let tmp = tempfile::tempdir().unwrap();
    let t = tmp.path();
    let repo = t.join("repo");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q"]);
    std::fs::write(repo.join("hello.txt"), "hi\n").unwrap();
    git(&repo, &["add", "hello.txt"]);
    git(&repo, &["commit", "-q", "-m", "one"]);
    let ssh = t.join("fake-ssh");
    // skip options up to "--", then the host, then run the command like a login shell would
    script(
        &ssh,
        "#!/bin/sh\n\
         [ -n \"${FAKE_SSH_FAIL:-}\" ] && { echo 'ssh: connect: timed out' >&2; exit 255; }\n\
         while [ \"$1\" != \"--\" ]; do shift; done; shift\n\
         echo \"host=$1\" >> \"$FAKE_LOG\"; shift\n\
         exec /bin/sh -c \"$1\"\n",
    );
    let aib = t.join("fake-aib");
    script(
        &aib,
        "#!/bin/sh\n\
         echo \"aib $*\" >> \"$FAKE_LOG\"\n\
         case \"$2\" in acquire) exit \"${FAKE_LEASE_RC:-0}\";; *) exit 0;; esac\n",
    );
    let remote_tmp = t.join("remote-tmp");
    std::fs::create_dir_all(&remote_tmp).unwrap();
    World {
        log: t.join("log"),
        out: t.join("out"),
        history: t.join("hist").join("history.jsonl"),
        _tmp: tmp,
        repo,
        ssh,
        aib,
        remote_tmp,
    }
}

fn run(w: &World, extra_env: &[(&str, &str)], args: &[&str]) -> (i32, String) {
    let mut c = Command::new(env!("CARGO_BIN_EXE_aib-lease"));
    c.arg("run")
        .args(["--host", "pro13", "--owner", "043dd", "--ttl", "600"])
        .arg("--repo")
        .arg(&w.repo)
        .arg("--ssh")
        .arg(&w.ssh)
        .arg("--remote-aib")
        .arg(&w.aib)
        .args(["--remote-path", "/usr/bin:/bin"])
        .args(args)
        .env("FAKE_LOG", &w.log)
        .env("TMPDIR", &w.remote_tmp)
        .env("OUT", &w.out)
        .env("AIB_LEASE_HISTORY", &w.history)
        .env_remove("FAKE_LEASE_RC")
        .env_remove("FAKE_SSH_FAIL");
    for (k, v) in extra_env {
        c.env(k, v);
    }
    let o = c.output().unwrap();
    let all = format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    );
    (o.status.code().unwrap_or(-1), all)
}

fn log(w: &World) -> String {
    std::fs::read_to_string(&w.log).unwrap_or_default()
}

fn history(w: &World) -> Vec<serde_json::Value> {
    std::fs::read_to_string(&w.history)
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

fn leftovers(w: &World) -> Vec<String> {
    std::fs::read_dir(&w.remote_tmp)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect()
}

#[test]
fn a_granted_lease_runs_every_step_reports_each_rc_and_releases() {
    let w = world();
    let (rc, out) = run(
        &w,
        &[],
        &[
            "--step",
            "test -f hello.txt",
            "--step",
            "exit 7",
            "--step",
            "cat hello.txt > \"$OUT\"",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("step 1/3 rc=0"), "{out}");
    assert!(out.contains("step 2/3 rc=7"), "{out}");
    assert!(
        out.contains("step 3/3 rc=0"),
        "a failed step does not stop the rest: {out}"
    );
    assert!(
        !out.contains("AIB_LEASE_STAT"),
        "markers are not shown: {out}"
    );
    assert_eq!(
        std::fs::read_to_string(&w.out).unwrap(),
        "hi\n",
        "ran in the sent tree"
    );
    let l = log(&w);
    assert!(l.contains("host=pro13"), "{l}");
    let acq = l.find("aib lease acquire --owner 043dd").unwrap();
    let rel = l.find("aib lease release --run-id 043dd-run-").unwrap();
    assert!(acq < rel, "{l}");
    assert!(l.contains("--ttl 600 --pid "), "{l}");
    assert!(
        leftovers(&w).is_empty(),
        "remote tree and script removed: {:?}",
        leftovers(&w)
    );
}

#[test]
fn every_run_leaves_one_history_line_with_each_steps_numbers() {
    let w = world();
    let (rc, out) = run(
        &w,
        &[],
        &[
            "--step",
            "sleep 1",
            "--step",
            "MY_TOKEN=s3cr3t-value exit 3",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    let h = history(&w);
    assert_eq!(h.len(), 1, "{h:?}");
    let v = &h[0];
    assert_eq!(v["schema"], "aib-lease-history/v1");
    assert_eq!(
        (v["seat"].as_str(), v["ssh_host"].as_str()),
        (Some("043dd"), Some("pro13"))
    );
    assert_eq!(v["outcome"], "step_failed");
    assert!(v["host"].as_str().is_some_and(|s| !s.is_empty()), "{v}");
    assert!(v["tree"].as_str().is_some_and(|s| s.len() >= 40), "{v}");
    let s = v["steps"].as_array().unwrap();
    assert_eq!(
        (s[0]["rc"].as_i64(), s[1]["rc"].as_i64()),
        (Some(0), Some(3))
    );
    assert!(s[0]["secs"].as_i64().unwrap() >= 1, "{v}");
    assert!(s[0]["peak_cpu_pct"].as_u64().is_some(), "{v}");
    assert_eq!(s[1]["cmd"], "MY_TOKEN=*** exit 3", "{v}");
    assert!(!v.to_string().contains("s3cr3t"), "{v}");
    if cfg!(target_os = "macos") {
        // /usr/bin/time -l is there on every Mac
        assert!(s[0]["maxrss_bytes"].as_u64().unwrap() > 0, "{v}");
        assert!(s[0]["cpu_user_s"].as_f64().is_some(), "{v}");
    }
}

#[test]
fn a_step_printing_a_marker_cannot_forge_its_numbers() {
    let w = world();
    let (rc, _) = run(
        &w,
        &[],
        &[
            "--step",
            "echo 'AIB_LEASE_STAT 0000 1 1 2 0 1 1 1 999'; exit 4",
        ],
    );
    assert_eq!(rc, 1);
    let v = &history(&w)[0];
    assert_eq!(v["steps"][0]["rc"], 4, "{v}");
    assert_ne!(v["steps"][0]["peak_cpu_pct"], 999, "{v}");
}

#[test]
fn a_refused_lease_runs_nothing_releases_nothing_and_is_recorded() {
    let w = world();
    let (rc, out) = run(&w, &[("FAKE_LEASE_RC", "4")], &["--step", "touch \"$OUT\""]);
    assert_eq!(rc, 75, "{out}");
    assert!(
        out.contains("lease not granted (rc 4), nothing ran"),
        "{out}"
    );
    assert!(!w.out.exists(), "a step ran without the lease");
    assert!(
        !log(&w).contains("lease release"),
        "released a lease it never had"
    );
    assert!(leftovers(&w).is_empty(), "{:?}", leftovers(&w));
    let v = &history(&w)[0];
    assert_eq!(
        (v["rc"].as_i64(), v["outcome"].as_str()),
        (Some(75), Some("not_granted"))
    );
    assert_eq!(v["steps"][0]["ran"], false);
}

#[test]
fn a_history_that_cannot_be_written_warns_and_keeps_the_rc() {
    let w = world();
    let blocker = w.remote_tmp.parent().unwrap().join("not-a-dir");
    std::fs::write(&blocker, "x").unwrap();
    let bad = blocker.join("history.jsonl");
    let (rc, out) = run(
        &w,
        &[("AIB_LEASE_HISTORY", bad.to_str().unwrap())],
        &["--step", "true"],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("warning: history line not written"), "{out}");
}

#[test]
fn all_steps_passing_is_zero_and_words_after_dashes_stay_words() {
    let w = world();
    let (rc, out) = run(
        &w,
        &[],
        &[
            "--",
            "/bin/sh",
            "-c",
            "printf '%s|' \"$@\" > \"$OUT\"",
            "x",
            "a b",
            "it's",
            "",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert_eq!(std::fs::read_to_string(&w.out).unwrap(), "a b|it's||");
}

#[test]
fn no_connection_is_three_not_a_pass() {
    let w = world();
    let (rc, out) = run(&w, &[("FAKE_SSH_FAIL", "1")], &["--step", "true"]);
    assert_eq!(rc, 3, "{out}");
    assert_eq!(history(&w)[0]["outcome"], "connect");
}

#[test]
fn a_tree_id_can_be_sent_and_a_bad_rev_is_four() {
    let w = world();
    let tree = Command::new("git")
        .arg("-C")
        .arg(&w.repo)
        .args(["rev-parse", "HEAD^{tree}"])
        .output()
        .unwrap();
    let tree = String::from_utf8_lossy(&tree.stdout).trim().to_string();
    let (rc, out) = run(&w, &[], &["--rev", &tree, "--step", "test -f hello.txt"]);
    assert_eq!(rc, 0, "{out}");
    let (rc, out) = run(&w, &[], &["--rev", "no-such-rev", "--step", "true"]);
    assert_eq!(rc, 4, "{out}");
    assert!(
        !log(&w).contains("no-such-rev"),
        "nothing was sent for a bad rev"
    );
}

#[test]
fn usage_errors_are_two() {
    let w = world();
    let (rc, out) = run(&w, &[], &[]);
    assert_eq!(rc, 2, "no steps: {out}");
    let (rc, _) = run(&w, &[], &["--ttl", "abc", "--step", "true"]);
    assert_eq!(rc, 2);
}
