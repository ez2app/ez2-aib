//! `aib-slice sweep` end to end on throwaway git repos: a bare "origin", a clone with branches
//! in each class, review / receipt files in a pending folder. The printed JSON must match
//! schema/sweep.v1.json (aib_slice::validate) and put each branch in its class.

use aib_slice::{validate, SCHEMA};
use serde_json::Value;
use std::path::Path;
use std::process::Command;

fn git(dir: &Path, args: &[&str]) -> String {
    let o = Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@example.invalid")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@example.invalid")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .expect("git runs");
    assert!(o.status.success(), "git {args:?}: {o:?}");
    String::from_utf8_lossy(&o.stdout).trim().to_owned()
}

fn commit(dir: &Path, file: &str, text: &str, msg: &str) -> String {
    let path = dir.join(file);
    if let Some(up) = path.parent() {
        std::fs::create_dir_all(up).expect("mkdir");
    }
    std::fs::write(path, text).expect("write");
    git(dir, &["add", file]);
    git(dir, &["commit", "-q", "-m", msg]);
    git(dir, &["rev-parse", "HEAD"])
}

struct Repo {
    _t: tempfile::TempDir,
    work: std::path::PathBuf,
    pending: std::path::PathBuf,
    heads: std::collections::BTreeMap<&'static str, String>,
}

/// origin + clone. Branches (from main):
/// - feat/picked-1ced7-1114: its one patch was also put on main ⇒ merged (5)
/// - feat/ready-1ced7-1114: review passed + green receipt at head, pushed ⇒ 2 (no PR) or 1 (PR)
/// - feat/wip-043dd-1114: nothing ⇒ in progress (4); it changes a CI workflow ⇒ new_gate
/// - feat/fixme-a9136-1114: review at head with M 1 ⇒ fix (3)
/// - feat/dropped-1ced7-1114: acked in sweep-ack.txt ⇒ stale (6)
/// - feat/top-1ced7-1114: stacked on feat/ready-1ced7-1114
/// - feat/clash-519d0-1114: changes the same line as ready, receipt at head (conflict candidate)
fn repo() -> Repo {
    let t = tempfile::tempdir().expect("tempdir");
    let origin = t.path().join("origin.git");
    let work = t.path().join("work");
    let pending = t.path().join("pending");
    std::fs::create_dir_all(&pending).expect("pending");
    git(
        t.path(),
        &["init", "-q", "--bare", "-b", "main", "origin.git"],
    );
    git(t.path(), &["init", "-q", "-b", "main", "work"]);
    git(
        &work,
        &["remote", "add", "origin", "https://github.com/o/r.git"],
    );
    git(
        &work,
        &["config", "remote.origin.pushurl", &origin.to_string_lossy()],
    );
    git(
        &work,
        &[
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        ],
    );
    let mut heads = std::collections::BTreeMap::new();
    commit(&work, "a.txt", "a\n", "base");
    let push = |work: &Path, refspec: &str| {
        git(work, &["push", "-q", &origin.to_string_lossy(), refspec]);
        let (_, dst) = refspec.split_once(':').unwrap_or((refspec, refspec));
        let name = dst.trim_start_matches("refs/heads/");
        git(
            work,
            &[
                "update-ref",
                &format!("refs/remotes/origin/{name}"),
                &git(
                    work,
                    &["rev-parse", refspec.split(':').next().unwrap_or(refspec)],
                ),
            ],
        );
    };
    // merged by patch: same change on main and on the branch
    git(&work, &["checkout", "-q", "-b", "feat/picked-1ced7-1114"]);
    heads.insert("picked", commit(&work, "p.txt", "p\n", "picked"));
    git(&work, &["checkout", "-q", "main"]);
    commit(&work, "p.txt", "p\n", "picked on main");
    push(&work, "main");
    for (key, name, file, text) in [
        ("ready", "feat/ready-1ced7-1114", "r.txt", "r\n"),
        (
            "wip",
            "feat/wip-043dd-1114",
            ".github/workflows/w.yml",
            "w\n",
        ),
        ("fixme", "feat/fixme-a9136-1114", "f.txt", "f\n"),
        ("dropped", "feat/dropped-1ced7-1114", "d.txt", "d\n"),
        ("clash", "feat/clash-519d0-1114", "r.txt", "x\n"),
    ] {
        git(&work, &["checkout", "-q", "-b", name, "main"]);
        heads.insert(key, commit(&work, file, text, key));
    }
    git(
        &work,
        &[
            "checkout",
            "-q",
            "-b",
            "feat/top-1ced7-1114",
            "feat/ready-1ced7-1114",
        ],
    );
    heads.insert("top", commit(&work, "t.txt", "t\n", "top"));
    git(&work, &["checkout", "-q", "main"]);
    push(&work, "feat/ready-1ced7-1114:integ-src/ready-1ced7");
    let review = |name: &str, head: &str, hm: &str| {
        std::fs::write(
            pending.join(format!("REVIEW-{name}.md")),
            format!("結論: 通過 頭: {head} H/M/L/N: {hm} 範圍: 全文\n"),
        )
        .expect("review");
    };
    review("ready", &heads["ready"], "0/0/1/0");
    review("fixme", &heads["fixme"], "0/1/0/0");
    std::fs::write(
        pending.join("TEST-ready.txt"),
        format!(
            "head {} want {}\nfmt_rc=0\nKNIFE x rc=101\nHEAD-END {}\n",
            heads["ready"], heads["ready"], heads["ready"]
        ),
    )
    .expect("receipt");
    std::fs::write(
        pending.join("TEST-clash.txt"),
        format!("head {0}\nfmt_rc=0\nHEAD-END {0}\n", heads["clash"]),
    )
    .expect("receipt");
    std::fs::write(
        pending.join("sweep-ack.txt"),
        "# branch<TAB>reason\nfeat/dropped-1ced7-1114\tAndy 不要\n",
    )
    .expect("ack");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs();
    std::fs::write(
        pending.join("build-claims.tsv"),
        format!(
            "feat/ready-1ced7-1114\ta9136\t{}\tS61 整合\nfeat/wip-043dd-1114\t043dd\t{}\t過期\n",
            now - 3600,
            now - 7 * 3600
        ),
    )
    .expect("claims");
    Repo {
        _t: t,
        work,
        pending,
        heads,
    }
}

fn sweep(r: &Repo, extra: &[&str]) -> (i32, Value) {
    let mut args = vec![
        "sweep".to_owned(),
        "--repo".into(),
        r.work.to_string_lossy().into_owned(),
        "--pending".into(),
        r.pending.to_string_lossy().into_owned(),
        "--no-fetch".into(),
    ];
    args.extend(extra.iter().map(|s| (*s).to_owned()));
    let o = Command::new(env!("CARGO_BIN_EXE_aib-slice"))
        .args(&args)
        .output()
        .expect("aib-slice runs");
    let v: Value = serde_json::from_slice(&o.stdout).expect("one JSON object");
    let schema: Value = serde_json::from_str(SCHEMA).expect("schema");
    assert_eq!(validate(&schema, &v), Vec::<String>::new(), "{v:#}");
    (o.status.code().unwrap_or(-1), v)
}

fn branch<'a>(v: &'a Value, name: &str) -> &'a Value {
    v["branches"]
        .as_array()
        .expect("branches")
        .iter()
        .find(|b| b["branch"] == name)
        .unwrap_or_else(|| panic!("{name} not listed: {v:#}"))
}

#[test]
fn every_branch_lands_in_its_class() {
    let r = repo();
    let (rc, v) = sweep(&r, &["--no-gh"]);
    // no fetch, no GitHub ⇒ not complete, rc 2, but the facts are still there
    assert_eq!(rc, 2);
    assert_eq!(v["complete"], false);
    assert_eq!(v["errors"], serde_json::json!(["no_fetch", "no_gh"]));
    let class = |n: &str| branch(&v, n)["class"].as_i64().expect("class");
    assert_eq!(class("feat/picked-1ced7-1114"), 5);
    assert_eq!(branch(&v, "feat/picked-1ced7-1114")["unique"], 0);
    assert_eq!(class("feat/ready-1ced7-1114"), 2);
    assert_eq!(
        branch(&v, "feat/ready-1ced7-1114")["why"],
        "review / receipt at head but no open PR"
    );
    assert_eq!(class("feat/wip-043dd-1114"), 4);
    assert_eq!(class("feat/fixme-a9136-1114"), 3);
    assert_eq!(class("feat/dropped-1ced7-1114"), 6);
    assert_eq!(
        branch(&v, "feat/dropped-1ced7-1114")["why"],
        "ack: Andy 不要"
    );
    let ready = branch(&v, "feat/ready-1ced7-1114");
    assert_eq!(ready["seat"], "1ced7");
    assert_eq!(ready["head"], r.heads["ready"].as_str());
    assert_eq!(
        ready["pushed_to"],
        serde_json::json!(["origin/integ-src/ready-1ced7"])
    );
    assert_eq!(ready["review"]["at_head"], true);
    assert_eq!(ready["receipt"]["green"], true);
    // a review of the branch below says nothing about the head on top
    let top = branch(&v, "feat/top-1ced7-1114");
    // both names of the branch below (local and its pushed copy) are listed
    assert_eq!(
        top["stacked_on"],
        serde_json::json!(["feat/ready-1ced7-1114", "integ-src/ready-1ced7"])
    );
    assert_eq!(top["review"]["at_head"], false);
    assert_eq!(
        top["review"]["unreviewed_after"],
        serde_json::json!([r.heads["top"]])
    );
    assert_eq!(top["class"], 4);
    assert_eq!(top["stack_top"], true);
    assert_eq!(ready["claimed_by"]["seat"], "a9136");
    assert_eq!(
        branch(&v, "feat/wip-043dd-1114")["claimed_by"],
        Value::Null,
        "older than 6 h"
    );
    assert_eq!(ready["stack_top"], false);
    let wip = branch(&v, "feat/wip-043dd-1114");
    assert_eq!(wip["new_gate"], true);
    assert_eq!(
        wip["gate_files"],
        serde_json::json!([".github/workflows/w.yml"])
    );
    assert_eq!(ready["new_gate"], false);
    // every branch with its own commits is tried against main; picked has none
    assert_eq!(
        ready["conflicts_with_main"],
        serde_json::json!({"conflict": false, "files": []})
    );
    assert_eq!(
        branch(&v, "feat/picked-1ced7-1114")["conflicts_with_main"],
        Value::Null
    );
    // the pushed copy is listed too, under its own name
    assert_eq!(class("integ-src/ready-1ced7"), 2);
}

/// 043dd L2: a pending folder that cannot be read is an error, not "nothing reviewed".
#[test]
fn an_unreadable_pending_folder_is_an_error() {
    let r = repo();
    std::fs::remove_dir_all(&r.pending).expect("rm pending");
    let (rc, v) = sweep(&r, &["--no-gh"]);
    assert_eq!(rc, 2);
    assert!(
        v["errors"]
            .as_array()
            .expect("errors")
            .iter()
            .any(|e| e.as_str().is_some_and(|e| e.starts_with("pending "))),
        "{v:#}"
    );
}

#[test]
fn one_seat_only() {
    let r = repo();
    let (_, v) = sweep(&r, &["--no-gh", "--seat", "043dd"]);
    let names: Vec<&str> = v["branches"]
        .as_array()
        .expect("branches")
        .iter()
        .filter_map(|b| b["branch"].as_str())
        .collect();
    assert_eq!(names, vec!["feat/wip-043dd-1114"]);
}

#[test]
fn bad_arguments_are_rc_64() {
    for args in [
        &["sweep", "--seat", "1CED7"][..],
        &["sweep", "--hours", "0"],
        &["merge"],
        &["sweep", "--repo"],
    ] {
        let o = Command::new(env!("CARGO_BIN_EXE_aib-slice"))
            .args(args)
            .output()
            .expect("runs");
        assert_eq!(o.status.code(), Some(64), "{args:?}");
        assert!(o.stdout.is_empty());
    }
}

/// With a (fake) GitHub that has one open PR for ready: ready is class 1, clash conflicts with
/// it, top's own commit is not in the PR, and `--gate` says rc 3.
#[cfg(unix)]
#[test]
fn an_open_pr_makes_merge_first_and_conflicts_show() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let gh = r.pending.join("fake-gh");
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             case \"$2\" in\n\
             *state=open*) echo '[{{\"number\":7,\"head\":{{\"ref\":\"feat/ready-1ced7-1114\",\"sha\":\"{}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}},\"labels\":[{{\"name\":\"not-gate\"}}]}}]' ;;\n\
             */pulls/7) echo '{{\"mergeable\":true,\"mergeable_state\":\"clean\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[{{\"name\":\"rust\",\"status\":\"completed\",\"conclusion\":\"success\"}}]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            r.heads["ready"]
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();
    let (rc, v) = sweep(&r, &["--gh", &gh]);
    assert_eq!(rc, 2, "still no fetch");
    assert_eq!(v["open_prs"][0]["number"], 7);
    let ready = branch(&v, "feat/ready-1ced7-1114");
    assert_eq!(ready["class"], 1);
    // the label alone does not lower anything: no reason line, and ready is not a new gate
    assert_eq!(
        ready["not_gate"],
        serde_json::json!({"label": true, "reason": null, "effective": false})
    );
    let clash = branch(&v, "feat/clash-519d0-1114");
    assert_eq!(
        clash["conflicts_with"],
        serde_json::json!([{"pr": 7, "files": ["r.txt"]}])
    );
    assert_eq!(
        ready["pr"],
        serde_json::json!({"number": 7, "state": "open", "merged": false, "base": "main",
            "author": "someone", "mergeable": "MERGEABLE", "merge_state": "clean",
            "checks": "SUCCESS", "red_checks": []})
    );
    // clash (class 2) and ready (class 1) change the same file: listed on both sides
    assert!(
        clash["conflicts_with_branches"]
            .as_array()
            .expect("list")
            .contains(&serde_json::json!({"branch": "feat/ready-1ced7-1114", "files": ["r.txt"]})),
        "{clash:#}"
    );
    assert!(ready["conflicts_with_branches"]
        .as_array()
        .expect("list")
        .iter()
        .any(|c| c["branch"] == "feat/clash-519d0-1114"));
    let top = branch(&v, "feat/top-1ced7-1114");
    assert_eq!(top["in_prs"], serde_json::json!([7]));
    assert_eq!(top["unique"], 2);
    assert_eq!(top["unique_not_in_prs"], 1);
    // a merged branch never asks GitHub; picked is all in main
    assert_eq!(branch(&v, "feat/picked-1ced7-1114")["pr"], Value::Null);
}
