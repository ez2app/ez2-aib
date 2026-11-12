// SPDX-License-Identifier: Apache-2.0
//! Local mode (建議表 #12) on throwaway repositories: the private denylist file is required and
//! read strictly, pre-push input is checked commit by commit for every ref, a PR text draft is
//! checked, and no output ever carries a denylist word. Only made-up words.

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

const WORD: &str = "examplehost";
const CODE: &str = "zq01";
const Z: &str = "0000000000000000000000000000000000000000";

fn git(dir: &Path, args: &[&str]) -> String {
    let o = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args([
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
        ])
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .expect("git runs");
    assert!(o.status.success(), "git {args:?}");
    String::from_utf8(o.stdout)
        .expect("utf8")
        .trim()
        .to_string()
}

fn tmp(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let d = std::env::temp_dir().join(format!(
        "aib-public-leak-local-{name}-{}-{nanos}",
        std::process::id()
    ));
    std::fs::create_dir_all(&d).expect("mkdir");
    d
}

/// A repository whose `main` is also `origin/main` (as after a fetch), plus a denylist file.
fn repo(name: &str) -> (PathBuf, PathBuf) {
    let d = tmp(name);
    git(&d, &["init", "-q", "-b", "main"]);
    std::fs::write(d.join("keep.txt"), "kept\n").expect("write");
    git(&d, &["add", "keep.txt"]);
    git(&d, &["commit", "-q", "-m", "base"]);
    git(&d, &["update-ref", "refs/remotes/origin/main", "HEAD"]);
    let deny = d.join(".deny");
    std::fs::write(&deny, format!("# private\n{WORD}\nword:{CODE}\n")).expect("write");
    (d, deny)
}

fn commit(d: &Path, file: &str, body: &str, msg: &str) -> String {
    std::fs::write(d.join(file), body).expect("write");
    git(d, &["add", file]);
    git(d, &["commit", "-q", "-m", msg]);
    git(d, &["rev-parse", "HEAD"])
}

fn run(d: &Path, deny: &Path, args: &[&str], stdin: &str) -> Output {
    let mut c = Command::new(env!("CARGO_BIN_EXE_aib-public-leak"));
    c.arg("--repo")
        .arg(d)
        .arg("--local-sources")
        .arg("--denylist-file")
        .arg(deny)
        .arg("--hosts-json")
        .arg(d.join("no-hosts.json"))
        .args(args)
        .env_remove("AIB_PUBLIC_DENYLIST")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut ch = c.spawn().expect("runs");
    use std::io::Write;
    ch.stdin
        .take()
        .expect("stdin")
        .write_all(stdin.as_bytes())
        .expect("write stdin");
    ch.wait_with_output().expect("waits")
}

fn text(o: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    )
}

fn assert_no_leak(out: &str) {
    let low = out.to_lowercase();
    assert!(
        !low.contains(WORD) && !low.contains(CODE),
        "word printed: {out}"
    );
}

fn push_line(local: &str, remote: &str) -> String {
    format!("refs/heads/x {local} refs/heads/x {remote}\n")
}

#[test]
fn a_new_branch_is_checked_commit_by_commit_even_if_the_word_is_removed_again() {
    let (d, deny) = repo("addremove");
    git(&d, &["switch", "-q", "-c", "x"]);
    commit(&d, "a.txt", "on ExampleHost now\n", "add");
    let head = commit(&d, "a.txt", "clean now\n", "remove again");
    let o = run(&d, &deny, &["--pre-push"], &push_line(&head, Z));
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(out.contains("denylist entry #1"), "{out}");
    assert!(out.contains("2 commit(s) one by one"), "{out}");
    assert_no_leak(&out);
}

#[test]
fn only_the_commit_message_is_enough_to_stop_a_push() {
    let (d, deny) = repo("msg");
    let base = git(&d, &["rev-parse", "HEAD"]);
    let head = commit(
        &d,
        "b.txt",
        "fine\n",
        &format!("moved to {}", CODE.to_uppercase()),
    );
    let o = run(&d, &deny, &["--pre-push"], &push_line(&head, &base));
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(out.contains("message line 1: denylist entry #2"), "{out}");
    assert_no_leak(&out);
}

#[test]
fn every_ref_is_checked_not_only_head() {
    let (d, deny) = repo("multiref");
    let base = git(&d, &["rev-parse", "HEAD"]);
    let clean = commit(&d, "c.txt", "fine\n", "clean");
    git(&d, &["switch", "-q", "-c", "other", &base]);
    let dirty = commit(&d, "d.txt", &format!("到{CODE}機\n"), "other");
    git(&d, &["switch", "-q", "main"]);
    let input = format!("{}{}", push_line(&clean, &base), push_line(&dirty, Z));
    let o = run(&d, &deny, &["--pre-push"], &input);
    let out = text(&o);
    assert_eq!(
        o.status.code(),
        Some(1),
        "a short code next to Chinese still hits: {out}"
    );
    assert!(out.contains(&dirty[..12]), "{out}");
    assert_no_leak(&out);
}

#[test]
fn a_short_code_inside_a_longer_word_and_deletions_pass() {
    let (d, deny) = repo("boundary");
    let base = git(&d, &["rev-parse", "HEAD"]);
    let head = commit(&d, "e.txt", &format!("s{CODE}x and {CODE}0\n"), "fine");
    let input = format!(
        "{}(delete) {Z} refs/heads/gone {base}\n",
        push_line(&head, &base)
    );
    let o = run(&d, &deny, &["--pre-push"], &input);
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
}

#[test]
fn a_pr_text_draft_is_checked() {
    let (d, deny) = repo("text");
    let f = d.join("pr.md");
    std::fs::write(&f, "title\n\nruns on EXAMPLEHOST\n").expect("write");
    let o = run(&d, &deny, &["--text-file", f.to_str().expect("path")], "");
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(out.contains("text file line 3"), "{out}");
    assert_no_leak(&out);
    std::fs::write(&f, "title\n\nall clean\n").expect("write");
    let o = run(&d, &deny, &["--text-file", f.to_str().expect("path")], "");
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
}

#[test]
fn a_missing_empty_or_unknown_denylist_is_exit_3() {
    let (d, _) = repo("deny");
    let f = d.join("pr.md");
    std::fs::write(&f, "clean\n").expect("write");
    let fa = f.to_str().expect("path");
    let missing = d.join("absent");
    let o = run(&d, &missing, &["--text-file", fa], "");
    assert_eq!(o.status.code(), Some(3), "{}", text(&o));
    for body in [
        "# only a comment\n\n",
        "ok\nre:secret.*name\n",
        "word:a-b\n",
    ] {
        let p = d.join(".deny2");
        std::fs::write(&p, body).expect("write");
        let o = run(&d, &p, &["--text-file", fa], "");
        let out = text(&o);
        assert_eq!(o.status.code(), Some(3), "{body:?}: {out}");
        assert!(!out.contains("secret") && !out.contains("a-b"), "{out}");
    }
}

#[test]
fn hosts_json_names_are_added_and_a_bad_file_is_exit_3() {
    let (d, deny) = repo("hosts");
    let h = d.join("hosts.json");
    std::fs::write(
        &h,
        r#"{"servers":[{"name":"boxnine","ssh":"root@boxnine"}]}"#,
    )
    .expect("write");
    let f = d.join("pr.md");
    std::fs::write(&f, "ran on BoxNine\n").expect("write");
    let args = |hj: &Path| -> Output {
        let mut c = Command::new(env!("CARGO_BIN_EXE_aib-public-leak"));
        c.args(["--local-sources", "--text-file"])
            .arg(&f)
            .arg("--denylist-file")
            .arg(&deny)
            .arg("--hosts-json")
            .arg(hj)
            .env_remove("AIB_PUBLIC_DENYLIST");
        c.output().expect("runs")
    };
    let o = args(&h);
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(!out.to_lowercase().contains("boxnine"), "{out}");
    std::fs::write(&h, "[]").expect("write");
    assert_eq!(args(&h).status.code(), Some(3));
}

#[test]
fn local_options_need_local_sources_and_one_mode() {
    let bin = env!("CARGO_BIN_EXE_aib-public-leak");
    for args in [
        vec!["--pre-push"],
        vec!["--local-sources"],
        vec!["--local-sources", "--pre-push", "--base", "main"],
    ] {
        let o = Command::new(bin).args(&args).output().expect("runs");
        assert_eq!(o.status.code(), Some(2), "{args:?}");
    }
}

#[test]
fn install_hook_writes_a_shim_once_per_clone_and_leaves_a_foreign_hook_alone() {
    let (d, _) = repo("hook");
    let bin = env!("CARGO_BIN_EXE_aib-public-leak");
    let install = || {
        Command::new(bin)
            .args(["--install-hook", "--repo"])
            .arg(&d)
            .output()
            .expect("runs")
    };
    let o = install();
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let hook = d.join(".git/hooks/pre-push");
    let body = std::fs::read_to_string(&hook).expect("hook written");
    assert!(body.contains(bin) && body.contains("--pre-push"), "{body}");
    assert_eq!(install().status.code(), Some(0), "ours is replaced");
    // a worktree of the clone uses the same hook
    let wt = d.with_extension("wt");
    git(
        &d,
        &[
            "worktree",
            "add",
            "-q",
            "-b",
            "w",
            wt.to_str().expect("path"),
        ],
    );
    let p = git(
        &wt,
        &["rev-parse", "--path-format=absolute", "--git-path", "hooks"],
    );
    assert_eq!(PathBuf::from(p).join("pre-push"), hook);
    std::fs::write(&hook, "#!/bin/sh\nexit 0\n").expect("write");
    let o = install();
    assert_eq!(o.status.code(), Some(2), "{}", text(&o));
    assert_eq!(
        std::fs::read_to_string(&hook).expect("read"),
        "#!/bin/sh\nexit 0\n"
    );
}
