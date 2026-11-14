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

const CI: &str = "name: aibd\non: [push]\njobs:\n  t:\n    runs-on: macos-14\n    steps:\n      - name: fmt\n        run: cargo fmt --all --check\n";
const STEP: &str =
    "      - name: x knives\n        run: |\n          cargo test -p aib-x --locked\n";

/// origin + clone. Branches (from main):
/// - feat/picked-1ced7-1114: its one patch was also put on main ⇒ merged (5)
/// - feat/ready-1ced7-1114: review passed + green receipt at head, pushed ⇒ 2 (no PR) or 1 (PR)
/// - feat/wip-043dd-1114: nothing ⇒ in progress (4); it changes a CI workflow ⇒ new_gate
/// - feat/fixme-a9136-1114: review at head with M 1 ⇒ fix (3)
/// - feat/dropped-1ced7-1114: acked in sweep-ack.txt ⇒ stale (6)
/// - feat/top-1ced7-1114: stacked on feat/ready-1ced7-1114
/// - feat/clash-519d0-1114: changes the same line as ready, receipt at head (conflict candidate)
/// - feat/step-1ced7-1114: changes crates/aib-x and adds one knife step for it to aibd.yml (S73k)
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
    // S73k: a workflow on main that a branch adds one step to
    commit(&work, ".github/workflows/aibd.yml", CI, "ci");
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
        ("gone", "feat/gone-1ced7-1114", "tools/g.py", "g\n"),
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
    // S73k: one step added for the crate the branch changes ⇒ a non-gate by content
    git(
        &work,
        &["checkout", "-q", "-b", "feat/step-1ced7-1114", "main"],
    );
    commit(&work, "crates/aib-x/src/lib.rs", "//! x\n", "x");
    heads.insert(
        "step",
        commit(
            &work,
            ".github/workflows/aibd.yml",
            &format!("{CI}{STEP}"),
            "step",
        ),
    );
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
    // s35: the .py files a branch carries; without GitHub nothing is abandonable
    let gone = branch(&v, "feat/gone-1ced7-1114");
    assert_eq!(gone["py_files"], serde_json::json!(["tools/g.py"]));
    assert_eq!(gone["abandonable"], false);
    assert_eq!(gone["class"], 4);
    assert_eq!(ready["py_files"], serde_json::json!([]));
    // S73k: one step for its own crate ⇒ a non-gate by content, no label or reason needed;
    // a workflow the base does not have stays a gate and says which rule
    let step = branch(&v, "feat/step-1ced7-1114");
    assert_eq!(step["new_gate"], true);
    assert_eq!(
        step["not_gate"],
        serde_json::json!({"label": false, "reason": null, "effective": true, "by_content": true,
            "content": "one step added (x knives) for aib-x"}),
        "{step:#}"
    );
    assert_eq!(wip["not_gate"]["effective"], false);
    assert_eq!(wip["not_gate"]["by_content"], false);
    assert!(
        wip["not_gate"]["content"]
            .as_str()
            .is_some_and(|c| c.starts_with("rule 1:")),
        "{wip:#}"
    );
    assert_eq!(ready["not_gate"]["content"], Value::Null);
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
             *state=closed*feat/gone-1ced7-1114*) echo '[{{\"number\":5,\"merged_at\":null,\"head\":{{\"ref\":\"feat/gone-1ced7-1114\",\"sha\":\"{1}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}}}}]' ;;\n\
             */pulls/7) echo '{{\"mergeable\":true,\"mergeable_state\":\"clean\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[{{\"name\":\"rust\",\"status\":\"completed\",\"conclusion\":\"success\"}}]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            r.heads["ready"],
            r.heads["gone"]
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
        serde_json::json!({"label": true, "reason": null, "effective": false, "by_content": false, "content": null})
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
            "checks": "SUCCESS", "red_checks": [], "infra_checks": []})
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
    // s33: PR #5 closed without merge at gone's head ⇒ 可放棄, class 6, not a to-do
    let gone = branch(&v, "feat/gone-1ced7-1114");
    assert_eq!(gone["abandonable"], true, "{gone:#}");
    assert_eq!(gone["class"], 6);
    assert!(
        gone["why"]
            .as_str()
            .is_some_and(|w| w.starts_with("abandonable: PR #5")),
        "{gone:#}"
    );
    assert_eq!(gone["pr"]["state"], "closed");
    assert_eq!(ready["abandonable"], false);
    // a merged branch never asks GitHub; picked is all in main
    assert_eq!(branch(&v, "feat/picked-1ced7-1114")["pr"], Value::Null);
}

/// git that may exit non-zero (a merge that stops on a conflict).
fn git_any(dir: &Path, args: &[&str]) {
    Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@example.invalid")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@example.invalid")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .expect("git runs");
}

/// S78 r-2a (RULES §20): a merge of main that git made by itself is carried and not listed; a
/// conflict resolved by hand, and a change slipped into a clean merge, are listed with the
/// files a non-author has to read; a merge of something that is not main says so.
#[test]
fn merges_resolved_by_hand_are_listed_with_their_files() {
    let r = repo();
    let w = &r.work;
    for (name, file, text) in [
        ("feat/plain-1ced7-1114", "plain.txt", "x\n"),
        ("feat/hand-1ced7-1114", "a.txt", "branch\n"),
        ("feat/evil-1ced7-1114", "evil.txt", "x\n"),
        ("feat/side-1ced7-1114", "other.txt", "side\n"),
        ("feat/other-1ced7-1114", "other.txt", "other\n"),
    ] {
        git(w, &["checkout", "-q", "-b", name, "main"]);
        let h = commit(w, file, text, name);
        // each branch is reviewed here, before anything is merged into it
        std::fs::write(
            r.pending
                .join(format!("REVIEW-pre-{}.md", name.replace('/', "_"))),
            format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
        )
        .expect("review");
    }
    git(w, &["checkout", "-q", "main"]);
    commit(w, "a.txt", "main\n", "main moves on");
    let main = git(w, &["rev-parse", "HEAD"]);
    git(w, &["update-ref", "refs/remotes/origin/main", &main]);

    // git merges main by itself
    git(w, &["checkout", "-q", "feat/plain-1ced7-1114"]);
    git(w, &["merge", "-q", "--no-edit", "main"]);
    // both sides changed a.txt: resolved by hand
    git(w, &["checkout", "-q", "feat/hand-1ced7-1114"]);
    git_any(w, &["merge", "-q", "--no-edit", "main"]);
    std::fs::write(w.join("a.txt"), "both\n").expect("resolve");
    git(w, &["add", "a.txt"]);
    git(w, &["commit", "-q", "--no-edit"]);
    let hand = git(w, &["rev-parse", "HEAD"]);
    // a clean merge of main, and a file changed inside the merge commit
    git(w, &["checkout", "-q", "feat/evil-1ced7-1114"]);
    git(w, &["merge", "-q", "--no-commit", "--no-ff", "main"]);
    std::fs::write(w.join("evil.txt"), "changed in the merge\n").expect("edit");
    git(w, &["add", "evil.txt"]);
    git(w, &["commit", "-q", "--no-edit"]);
    // a conflict with a branch that is not in main
    git(w, &["checkout", "-q", "feat/side-1ced7-1114"]);
    git_any(w, &["merge", "-q", "--no-edit", "feat/other-1ced7-1114"]);
    std::fs::write(w.join("other.txt"), "side and other\n").expect("resolve");
    git(w, &["add", "other.txt"]);
    git(w, &["commit", "-q", "--no-edit"]);
    let other = git(w, &["rev-parse", "feat/other-1ced7-1114"]);
    git(w, &["checkout", "-q", "main"]);

    let (_, v) = sweep(&r, &["--no-gh"]);
    // no fetch, no GitHub: the only two errors (nothing about a merge that could not be read)
    assert_eq!(
        v["errors"],
        serde_json::json!(["no_fetch", "no_gh"]),
        "{v:#}"
    );
    let merges = |name: &str| branch(&v, name)["resolved_merges"].clone();
    assert_eq!(merges("feat/plain-1ced7-1114"), serde_json::json!([]));
    assert_eq!(merges("feat/wip-043dd-1114"), serde_json::json!([]));
    assert_eq!(
        merges("feat/hand-1ced7-1114"),
        serde_json::json!([{"commit": hand, "parent": main, "from_main": true, "files": ["a.txt"]}])
    );
    let evil = merges("feat/evil-1ced7-1114");
    assert_eq!(
        evil[0]["files"],
        serde_json::json!(["evil.txt"]),
        "{evil:#}"
    );
    assert_eq!(evil[0]["from_main"], true);
    let side = merges("feat/side-1ced7-1114");
    assert_eq!(
        side[0]["files"],
        serde_json::json!(["other.txt"]),
        "{side:#}"
    );
    assert_eq!(side[0]["parent"], other.as_str());
    assert_eq!(side[0]["from_main"], false);

    // the reviews were written before the merges: what came after each of them
    let rv = |name: &str| branch(&v, name)["review"].clone();
    let plain = rv("feat/plain-1ced7-1114");
    assert_eq!(plain["at_head"], false);
    // the commit main brought in is not this branch's to review
    assert_eq!(
        plain["unreviewed_after"],
        serde_json::json!([]),
        "{plain:#}"
    );
    assert_eq!(plain["resolved_after_review"], serde_json::json!([]));
    let after = rv("feat/hand-1ced7-1114");
    assert_eq!(
        after["unreviewed_after"],
        serde_json::json!([]),
        "{after:#}"
    );
    assert_eq!(
        after["resolved_after_review"],
        merges("feat/hand-1ced7-1114"),
        "{after:#}"
    );
    // a review at the head has nothing after it
    assert_eq!(
        rv("feat/ready-1ced7-1114")["resolved_after_review"],
        serde_json::json!([])
    );
}

/// `--fast` (S79, 519d0 11-15): only branches with an open PR or a commit inside the window
/// are read, and closed PRs come from one list of the recently changed ones; a branch is
/// never asked about one by one. The output says it is not the whole picture.
#[cfg(unix)]
#[test]
fn fast_reads_recent_branches_and_asks_github_for_closed_prs_once() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let w = &r.work;
    // a branch nobody touched for years, with a commit of its own
    git(w, &["checkout", "-q", "-b", "feat/old-1ced7-1001", "main"]);
    std::fs::write(w.join("old.txt"), "old\n").expect("write");
    git(w, &["add", "old.txt"]);
    let o = Command::new("git")
        .args(["commit", "-q", "-m", "old"])
        .current_dir(w)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@example.invalid")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@example.invalid")
        .env("GIT_AUTHOR_DATE", "2020-01-01T00:00:00Z")
        .env("GIT_COMMITTER_DATE", "2020-01-01T00:00:00Z")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .expect("git runs");
    assert!(o.status.success(), "{o:?}");
    git(w, &["checkout", "-q", "main"]);

    // one list of closed PRs, newest change first: #5 (gone) inside the window, #9 (wip)
    // changed long ago. A question about one branch (`head=`) fails: fast must not ask it.
    let gh = r.pending.join("fake-gh");
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             case \"$2\" in\n\
             *state=open*) echo '[]' ;;\n\
             *state=closed*head=*) exit 1 ;;\n\
             *state=closed*sort=updated*) echo '[{{\"number\":5,\"merged_at\":null,\"updated_at\":\"2099-01-01T00:00:00Z\",\"head\":{{\"ref\":\"feat/gone-1ced7-1114\",\"sha\":\"{}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}}}},{{\"number\":9,\"merged_at\":null,\"updated_at\":\"2000-01-01T00:00:00Z\",\"head\":{{\"ref\":\"feat/wip-043dd-1114\",\"sha\":\"{}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}}}}]' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            r.heads["gone"], r.heads["wip"]
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();

    let (_, v) = sweep(&r, &["--gh", &gh, "--fast"]);
    assert_eq!(v["fast"], true);
    assert_eq!(v["errors"], serde_json::json!(["no_fetch"]), "{v:#}");
    let listed = |v: &Value, name: &str| {
        v["branches"]
            .as_array()
            .expect("branches")
            .iter()
            .any(|b| b["branch"] == name)
    };
    assert!(!listed(&v, "feat/old-1ced7-1001"), "an old branch is read");
    assert!(listed(&v, "feat/ready-1ced7-1114"));
    let gone = branch(&v, "feat/gone-1ced7-1114");
    assert_eq!(gone["pr"]["number"], 5, "{gone:#}");
    assert_eq!(gone["abandonable"], true);
    // a PR that GitHub last changed before the window is not seen
    assert_eq!(branch(&v, "feat/wip-043dd-1114")["pr"], Value::Null);

    // the full sweep lists the old branch and says it is the whole picture
    let (_, full) = sweep(&r, &["--no-gh"]);
    assert_eq!(full["fast"], false);
    assert!(listed(&full, "feat/old-1ced7-1001"));
}

/// RULES §25 (S79 C1a): a branch whose merge into main would drop a workflow step main has
/// says which; a branch that only adds a step loses none; no workflow change = not checked.
#[test]
fn a_merge_that_drops_a_workflow_step_names_it() {
    let r = repo();
    let w = &r.work;
    git(w, &["checkout", "-q", "-b", "feat/cut-1ced7-1114", "main"]);
    commit(
        w,
        ".github/workflows/aibd.yml",
        &CI.replace("- name: fmt", "- name: format"),
        "cut",
    );
    git(w, &["checkout", "-q", "main"]);
    let (_, v) = sweep(&r, &["--no-gh"]);
    assert_eq!(
        branch(&v, "feat/cut-1ced7-1114")["steps_lost"],
        serde_json::json!([".github/workflows/aibd.yml: fmt"]),
        "{v:#}"
    );
    assert_eq!(
        branch(&v, "feat/step-1ced7-1114")["steps_lost"],
        serde_json::json!([])
    );
    assert_eq!(
        branch(&v, "feat/ready-1ced7-1114")["steps_lost"],
        Value::Null
    );
}

/// `--out` / `--due-out` (S79): one run writes the sweep and the due lists as whole files,
/// prints nothing, and leaves no temporary file behind.
#[test]
fn one_run_writes_the_sweep_and_the_due_lists_as_files() {
    let r = repo();
    let (out, due) = (
        r.pending.join("cache-sweep.json"),
        r.pending.join("cache-due.json"),
    );
    let o = Command::new(env!("CARGO_BIN_EXE_aib-slice"))
        .args(["sweep", "--no-fetch", "--no-gh", "--repo"])
        .arg(&r.work)
        .arg("--pending")
        .arg(&r.pending)
        .arg("--out")
        .arg(&out)
        .arg("--due-out")
        .arg(&due)
        .output()
        .expect("aib-slice runs");
    assert_eq!(o.status.code(), Some(2), "no fetch, no GitHub: {o:?}");
    assert!(o.stdout.is_empty(), "nothing is printed with --out");
    let read = |p: &Path| -> Value {
        serde_json::from_slice(&std::fs::read(p).expect("file written")).expect("JSON")
    };
    let s = read(&out);
    let schema: Value = serde_json::from_str(SCHEMA).expect("schema");
    assert_eq!(validate(&schema, &s), Vec::<String>::new());
    let d = read(&due);
    assert_eq!(d["schema"], "aib-slice-due.v1");
    assert_eq!(d["seat"], Value::Null);
    assert_eq!(d["generated_at"], s["generated_at"], "the same run");
    // ready is reviewed and green at its head: it is in the merge list
    assert!(d["merge"]["items"]
        .as_array()
        .expect("items")
        .iter()
        .any(|i| i["branch"] == "feat/ready-1ced7-1114"));
    let left: Vec<String> = std::fs::read_dir(&r.pending)
        .expect("dir")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.contains(".tmp"))
        .collect();
    assert!(left.is_empty(), "{left:?}");
}

/// The cache file (`--due-out`) takes every item: the 8 of a reply's tail are the oldest, so
/// the newest (an open PR's red check) was cut off (519d0 11-15: #748 not seen by next).
#[test]
fn the_due_cache_file_takes_every_item_unless_max_is_given() {
    let r = repo();
    let w = &r.work;
    // ten more branches, each reviewed and green at its head: ten more to merge
    for i in 0..10 {
        let name = format!("feat/more{i}-1ced7-1114");
        git(w, &["checkout", "-q", "-b", &name, "main"]);
        let h = commit(w, &format!("m{i}.txt"), "m\n", &name);
        std::fs::write(
            r.pending.join(format!("REVIEW-more{i}.md")),
            format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
        )
        .expect("review");
        std::fs::write(
            r.pending.join(format!("TEST-more{i}.txt")),
            format!("head {h}\nfmt_rc=0\nHEAD-END {h}\n"),
        )
        .expect("receipt");
    }
    git(w, &["checkout", "-q", "main"]);
    let run = |extra: &[&str]| -> Value {
        let due = r.pending.join("cache-due.json");
        let mut c = Command::new(env!("CARGO_BIN_EXE_aib-slice"));
        c.args(["sweep", "--no-fetch", "--no-gh", "--repo"])
            .arg(&r.work)
            .arg("--pending")
            .arg(&r.pending)
            .arg("--out")
            .arg(r.pending.join("cache-sweep.json"))
            .arg("--due-out")
            .arg(&due)
            .args(extra);
        c.output().expect("aib-slice runs");
        serde_json::from_slice(&std::fs::read(&due).expect("file written")).expect("JSON")
    };
    let all = run(&[]);
    let total = all["merge"]["total"].as_u64().expect("total");
    assert!(total > 8, "{all:#}");
    assert_eq!(
        u64::try_from(all["merge"]["items"].as_array().expect("items").len()).ok(),
        Some(total),
        "every item is in the cache file"
    );
    let cut = run(&["--max", "3"]);
    assert_eq!(cut["merge"]["items"].as_array().expect("items").len(), 3);
    assert_eq!(cut["merge"]["total"], total);
}

/// IFACE-s79 r11 5: the receipts of one head are read together (a run split over files is
/// one receipt; a green rerun clears an older red), `.out` and odd names are receipts too.
#[test]
fn the_receipts_of_one_head_are_read_as_one() {
    let r = repo();
    let w = &r.work;
    let mut heads = Vec::new();
    for name in ["split", "rerun", "odd"] {
        let b = format!("feat/{name}-1ced7-1114");
        git(w, &["checkout", "-q", "-b", &b, "main"]);
        let h = commit(w, &format!("{name}.txt"), "x\n", name);
        std::fs::write(
            r.pending.join(format!("REVIEW-{name}.md")),
            format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
        )
        .expect("review");
        heads.push(h);
    }
    git(w, &["checkout", "-q", "main"]);
    let put = |file: &str, text: String, age_s: u64| {
        let p = r.pending.join(file);
        std::fs::write(&p, text).expect("receipt");
        let at = std::time::SystemTime::now() - std::time::Duration::from_secs(age_s);
        std::fs::File::options()
            .write(true)
            .open(&p)
            .and_then(|f| f.set_modified(at))
            .expect("mtime");
    };
    // split: the whole-workspace part finished (older), the docs-only part has no HEAD-END
    let h = &heads[0];
    put(
        "RECEIPT-split-ws-1ced7.out",
        format!("HEAD {h}\nRC fmt=0\nRC test=0\nHEAD-END {h}\n"),
        600,
    );
    put(
        "RECEIPT-split-docsonly-1ced7.txt",
        format!("HEAD {h}\nRC docs_only=0\n"),
        60,
    );
    // rerun: red first, the same step green later
    let h = &heads[1];
    put(
        "TEST-rerun-a.txt",
        format!("HEAD {h}\nRC test=101\nHEAD-END {h}\n"),
        600,
    );
    put(
        "TEST-rerun-b.txt",
        format!("HEAD {h}\nRC test=0\nHEAD-END {h}\n"),
        60,
    );
    // odd: a name that is neither TEST- nor RECEIPT-
    let h = &heads[2];
    put(
        &format!("777-receipt-{}-full.out", &h[..9]),
        format!("HEAD {h}\nRC test=0\nHEAD-END {h}\n"),
        60,
    );

    let (_, v) = sweep(&r, &["--no-gh"]);
    let split = branch(&v, "feat/split-1ced7-1114");
    assert_eq!(split["receipt"]["green"], true, "{split:#}");
    assert_eq!(split["receipt"]["complete"], true);
    assert_eq!(
        split["receipt"]["file"],
        "RECEIPT-split-docsonly-1ced7.txt (+1)"
    );
    assert_eq!(split["class"], 2, "{split:#}");
    let rerun = branch(&v, "feat/rerun-1ced7-1114");
    assert_eq!(
        rerun["receipt"]["nonzero"],
        serde_json::json!([]),
        "{rerun:#}"
    );
    assert_eq!(rerun["class"], 2);
    let odd = branch(&v, "feat/odd-1ced7-1114");
    assert_eq!(odd["receipt"]["green"], true, "{odd:#}");
    assert_eq!(odd["class"], 2);
}

/// IFACE-s79 r11 4: a red check whose job died in the runner's set-up is INFRA, not a fix:
/// the reviewed, green PR waits for CI (class 8) and names the cell for the merge queue.
#[cfg(unix)]
#[test]
fn a_job_that_died_in_set_up_is_infra_not_a_fix() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let gh = r.pending.join("fake-gh");
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             case \"$2\" in\n\
             *state=open*) echo '[{{\"number\":7,\"head\":{{\"ref\":\"feat/ready-1ced7-1114\",\"sha\":\"{}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}},\"labels\":[]}}]' ;;\n\
             */pulls/7) echo '{{\"mergeable\":true,\"mergeable_state\":\"blocked\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[{{\"id\":55,\"name\":\"rust\",\"status\":\"completed\",\"conclusion\":\"failure\"}}]}}' ;;\n\
             */actions/jobs/55) echo '{{\"steps\":[{{\"name\":\"Set up runner\",\"conclusion\":\"failure\"}},{{\"name\":\"test\",\"conclusion\":\"skipped\"}}]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            r.heads["ready"]
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();
    let (_, v) = sweep(&r, &["--gh", &gh]);
    let ready = branch(&v, "feat/ready-1ced7-1114");
    assert_eq!(ready["pr"]["checks"], "INFRA", "{ready:#}");
    assert_eq!(ready["pr"]["infra_checks"], serde_json::json!(["rust"]));
    assert_eq!(ready["pr"]["red_checks"], serde_json::json!(["rust"]));
    assert_eq!(ready["class"], 8, "{ready:#}");
    assert_eq!(ready["class_name"], "waiting_ci");
}

/// PR lists are read five rows a page, and a read that fails is tried once more (519d0
/// 11-15: a hundred rows a page timed out on a slow line and the whole round was partial).
#[cfg(unix)]
#[test]
fn pr_lists_are_read_in_small_pages_and_a_failed_read_is_tried_again() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let gh = r.pending.join("fake-gh");
    let row = |n: u32| {
        format!(
            "{{\"number\":{n},\"head\":{{\"ref\":\"x/pr{n}\",\"sha\":\"{}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}},\"labels\":[]}}",
            r.heads["ready"]
        )
    };
    let page1: Vec<String> = (1..=5).map(row).collect();
    // the very first call fails (a marker file remembers it); page 1 is full, page 2 is not
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             if [ ! -e \"$0.once\" ]; then : > \"$0.once\"; echo timed out >&2; exit 1; fi\n\
             case \"$2\" in\n\
             *state=open*per_page=5*page=1) echo '[{}]' ;;\n\
             *state=open*per_page=5*page=2) echo '[{}]' ;;\n\
             *state=open*) echo 'unexpected page size' >&2; exit 1 ;;\n\
             */pulls/*) echo '{{\"mergeable\":true,\"mergeable_state\":\"clean\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            page1.join(","),
            row(6)
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();
    let (_, v) = sweep(&r, &["--gh", &gh]);
    assert_eq!(v["errors"], serde_json::json!(["no_fetch"]), "{v:#}");
    let got: Vec<u64> = v["open_prs"]
        .as_array()
        .expect("open_prs")
        .iter()
        .filter_map(|p| p["number"].as_u64())
        .collect();
    assert_eq!(got, [1, 2, 3, 4, 5, 6]);
}

/// 5d33a M1: an open PR stays on its row when the local branch of its name is on another
/// commit. Ahead (a local commit not pushed): still class 1 at the PR's head. Behind (the PR
/// was pushed from elsewhere): not "carried by its own PR".
#[cfg(unix)]
#[test]
fn an_open_pr_is_judged_at_its_head_wherever_the_local_branch_is() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let w = &r.work;
    // ahead: one more local commit on ready, not pushed
    git(w, &["checkout", "-q", "feat/ready-1ced7-1114"]);
    let ahead = commit(w, "more.txt", "more\n", "not pushed yet");
    // behind: origin has p2, the local branch is still on p1
    git(
        w,
        &["checkout", "-q", "-b", "feat/behind-1ced7-1114", "main"],
    );
    let p1 = commit(w, "b1.txt", "1\n", "behind 1");
    let p2 = commit(w, "b2.txt", "2\n", "behind 2");
    git(
        w,
        &[
            "update-ref",
            "refs/remotes/origin/feat/behind-1ced7-1114",
            &p2,
        ],
    );
    git(w, &["checkout", "-q", "main"]);
    git(w, &["branch", "-f", "feat/behind-1ced7-1114", &p1]);
    let gh = r.pending.join("fake-gh");
    let row = |n: u32, name: &str, sha: &str| {
        format!(
            "{{\"number\":{n},\"head\":{{\"ref\":\"{name}\",\"sha\":\"{sha}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}},\"labels\":[]}}"
        )
    };
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             case \"$2\" in\n\
             *state=open*page=1) echo '[{},{}]' ;;\n\
             */pulls/*) echo '{{\"mergeable\":true,\"mergeable_state\":\"clean\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[{{\"name\":\"rust\",\"status\":\"completed\",\"conclusion\":\"success\"}}]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            row(7, "feat/ready-1ced7-1114", &r.heads["ready"]),
            row(8, "feat/behind-1ced7-1114", &p2)
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();
    let (_, v) = sweep(&r, &["--gh", &gh]);
    assert_eq!(v["errors"], serde_json::json!(["no_fetch"]), "{v:#}");
    let ready = branch(&v, "feat/ready-1ced7-1114");
    assert_eq!(ready["head"], r.heads["ready"].as_str());
    assert_eq!(ready["local_head"], ahead.as_str());
    assert_eq!(ready["pr"]["number"], 7);
    assert_eq!(ready["class"], 1, "{ready:#}");
    let behind = branch(&v, "feat/behind-1ced7-1114");
    assert_eq!(behind["head"], p2.as_str());
    assert_eq!(behind["local_head"], p1.as_str());
    assert_eq!(behind["pr"]["number"], 8);
    assert_ne!(behind["class"], 7, "{behind:#}");
    // a branch at its PR's head, and one without a PR, say nothing
    assert_eq!(branch(&v, "feat/wip-043dd-1114")["local_head"], Value::Null);
}

/// IFACE-s79 r10 3: slices reviewed one by one and stacked into one head (a plain merge of
/// main on top): the head counts as reviewed. A commit no passed review covers is named.
#[test]
fn a_head_stacked_from_reviewed_slices_counts_as_reviewed() {
    let r = repo();
    let w = &r.work;
    let review = |name: &str, head: &str, range: &str| {
        let file = format!("REVIEW-{name}-043dd-{}.md", &head[..8]);
        std::fs::write(
            r.pending.join(&file),
            format!("結論: 通過 頭: {head} H/M/L/N: 0/0/0/0 範圍: {range}\n"),
        )
        .expect("review");
        file
    };
    git(
        w,
        &["checkout", "-q", "-b", "feat/stack-1ced7-1114", "main"],
    );
    let c1 = commit(w, "s1.txt", "1\n", "slice 1");
    let f1 = review("s1", &c1, "全文");
    let c2 = commit(w, "s2.txt", "2\n", "slice 2");
    let f2 = review("s2-inc", &c2, "增量");
    commit(w, "s3.txt", "3\n", "slice 3a");
    let c4 = commit(w, "s4.txt", "4\n", "slice 3b");
    let f4 = review("s3", &c4, &format!("{}..{}", &c2[..9], &c4[..9]));
    // one commit in the middle that no review covers, and one on top
    git(w, &["checkout", "-q", "-b", "feat/gap-1ced7-1114", "main"]);
    let g1 = commit(w, "g1.txt", "1\n", "gap 1");
    let fg1 = review("g1", &g1, "全文");
    let g2 = commit(w, "g2.txt", "2\n", "gap 2");
    let g3 = commit(w, "g3.txt", "3\n", "gap 3");
    let fg3 = review("g3", &g3, &format!("{}..{}", &g2[..9], &g3[..9]));
    let g4 = commit(w, "g4.txt", "4\n", "gap 4");
    // main moves on; the stack takes it in with a plain merge: nobody reviewed that head
    git(w, &["checkout", "-q", "main"]);
    commit(w, "m.txt", "m\n", "main moves on");
    let main = git(w, &["rev-parse", "HEAD"]);
    git(w, &["update-ref", "refs/remotes/origin/main", &main]);
    git(w, &["checkout", "-q", "feat/stack-1ced7-1114"]);
    git(w, &["merge", "-q", "--no-edit", "main"]);
    git(w, &["checkout", "-q", "main"]);

    let (_, v) = sweep(&r, &["--no-gh"]);
    assert_eq!(
        v["errors"],
        serde_json::json!(["no_fetch", "no_gh"]),
        "{v:#}"
    );
    let stack = branch(&v, "feat/stack-1ced7-1114");
    assert_eq!(stack["review"]["at_head"], false);
    assert_eq!(stack["review"]["scope"], format!("from:{}", &c2[..9]));
    assert_eq!(
        stack["review_parts"],
        serde_json::json!({"ok": true, "files": [f4, f2, f1], "uncovered": [],
            "unreviewed_merges": []})
    );
    assert_eq!(stack["reviewed"], true);
    let gap = branch(&v, "feat/gap-1ced7-1114");
    assert_eq!(
        gap["review_parts"],
        serde_json::json!({"ok": false, "files": [fg3, fg1], "uncovered": [g4, g2],
            "unreviewed_merges": []})
    );
    assert_eq!(gap["reviewed"], false);
    // a review at the head is judged as before; no review, nothing to add up
    let ready = branch(&v, "feat/ready-1ced7-1114");
    assert_eq!(ready["review_parts"], Value::Null);
    assert_eq!(ready["reviewed"], true);
    assert_eq!(ready["review"]["scope"], "whole");
    assert_eq!(branch(&v, "feat/wip-043dd-1114")["reviewed"], false);
}

/// f1fae COMPARE r2 2: the merge queue runs one receipt on main + the heads in front, merged
/// one after another. A green one counts for an open PR inside it that has no receipt of
/// its own, when the stack is open PR heads and plain merges only.
#[cfg(unix)]
#[test]
fn a_green_receipt_on_a_stack_of_plain_merges_counts_for_the_prs_in_it() {
    use std::os::unix::fs::PermissionsExt;
    let r = repo();
    let w = &r.work;
    let mut heads = Vec::new();
    for (name, file) in [
        ("feat/stk-b433b-1114", "k1.txt"),
        ("feat/stray-b433b-1114", "k2.txt"),
    ] {
        git(w, &["checkout", "-q", "-b", name, "main"]);
        let h = commit(w, file, "x\n", name);
        git(
            w,
            &["update-ref", &format!("refs/remotes/origin/{name}"), &h],
        );
        std::fs::write(
            r.pending
                .join(format!("REVIEW-{file}-043dd-{}.md", &h[..8])),
            format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
        )
        .expect("review");
        heads.push(h);
    }
    let receipt = |s: &str| {
        std::fs::write(
            r.pending
                .join(format!("RECEIPT-andymini-stack-b433b-{}.out", &s[..9])),
            format!("head {s}\nfmt_rc=0\nHEAD-END {s}\n"),
        )
        .expect("receipt");
    };
    // a plain stack: main + stk, merged by git
    git(w, &["checkout", "-q", "-b", "tmp/stack", "main"]);
    git(
        w,
        &["merge", "-q", "--no-ff", "--no-edit", "feat/stk-b433b-1114"],
    );
    let stack = git(w, &["rev-parse", "HEAD"]);
    receipt(&stack);
    // a stack with something of its own inside the merge: what ran is in no PR
    git(w, &["checkout", "-q", "-b", "tmp/stack2", "main"]);
    git(
        w,
        &[
            "merge",
            "-q",
            "--no-commit",
            "--no-ff",
            "feat/stray-b433b-1114",
        ],
    );
    std::fs::write(w.join("k2.txt"), "changed in the merge\n").expect("edit");
    git(w, &["add", "k2.txt"]);
    git(w, &["commit", "-q", "--no-edit"]);
    receipt(&git(w, &["rev-parse", "HEAD"]));
    git(w, &["checkout", "-q", "main"]);
    git(w, &["branch", "-q", "-D", "tmp/stack", "tmp/stack2"]);

    let gh = r.pending.join("fake-gh");
    let row = |n: u32, name: &str, sha: &str| {
        format!(
            "{{\"number\":{n},\"head\":{{\"ref\":\"{name}\",\"sha\":\"{sha}\"}},\"base\":{{\"ref\":\"main\"}},\"user\":{{\"login\":\"someone\"}},\"labels\":[]}}"
        )
    };
    std::fs::write(
        &gh,
        format!(
            "#!/bin/sh\n\
             case \"$2\" in\n\
             *state=open*page=1) echo '[{},{}]' ;;\n\
             */pulls/*) echo '{{\"mergeable\":true,\"mergeable_state\":\"clean\"}}' ;;\n\
             */check-runs*) echo '{{\"check_runs\":[{{\"name\":\"rust\",\"status\":\"completed\",\"conclusion\":\"success\"}}]}}' ;;\n\
             *) echo '[]' ;;\n\
             esac\n",
            row(11, "feat/stk-b433b-1114", &heads[0]),
            row(12, "feat/stray-b433b-1114", &heads[1])
        ),
    )
    .expect("fake gh");
    std::fs::set_permissions(&gh, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let gh = gh.to_string_lossy().into_owned();
    let (_, v) = sweep(&r, &["--gh", &gh]);
    assert_eq!(v["errors"], serde_json::json!(["no_fetch"]), "{v:#}");
    let stk = branch(&v, "feat/stk-b433b-1114");
    assert_eq!(stk["receipt"]["stacked_at"], stack.as_str(), "{stk:#}");
    assert_eq!(stk["receipt"]["at_head"], true);
    assert_eq!(stk["receipt"]["head"], stack.as_str());
    assert_eq!(stk["class"], 1, "{stk:#}");
    let stray = branch(&v, "feat/stray-b433b-1114");
    assert_eq!(stray["receipt"], Value::Null, "{stray:#}");
    assert_ne!(stray["class"], 1);
    // a receipt at the head is an ordinary one
    assert_eq!(
        branch(&v, "feat/ready-1ced7-1114")["receipt"]["stacked_at"],
        Value::Null
    );
}
