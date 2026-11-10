// SPDX-License-Identifier: Apache-2.0
//! The binary on a throwaway git repository: exit codes, denylist_absent, and that the report
//! never carries the matched text. Only made-up words; addresses are built at run time so no
//! literal public address is in this file (RULES §16).

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const WORD: &str = "examplehost";

fn public() -> String {
    format!("{}.{}.{}.{}", 1, 2, 3, 4)
}

fn git(dir: &Path, args: &[&str]) {
    let st = Command::new("git")
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
        .status()
        .expect("git runs");
    assert!(st.success(), "git {args:?}");
}

/// A repository with tag `base` and a commit on top that adds a leaking line.
fn repo(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let d = std::env::temp_dir().join(format!(
        "aib-public-leak-{name}-{}-{nanos}",
        std::process::id()
    ));
    std::fs::create_dir_all(&d).expect("mkdir");
    git(&d, &["init", "-q"]);
    std::fs::write(d.join("keep.txt"), "kept\n").expect("write");
    git(&d, &["add", "keep.txt"]);
    git(&d, &["commit", "-q", "-m", "base"]);
    git(&d, &["tag", "base"]);
    std::fs::write(
        d.join("notes.txt"),
        format!("clean line\nmoved to ExampleHost at {}\n", public()),
    )
    .expect("write");
    git(&d, &["add", "notes.txt"]);
    git(&d, &["commit", "-q", "-m", "add notes\n\nnothing to see"]);
    d
}

fn run(dir: &Path, args: &[&str], denylist: Option<&str>) -> Output {
    let mut c = Command::new(env!("CARGO_BIN_EXE_aib-public-leak"));
    c.arg("--repo").arg(dir).args(args);
    match denylist {
        Some(d) => c.env("AIB_PUBLIC_DENYLIST", d),
        None => c.env_remove("AIB_PUBLIC_DENYLIST"),
    };
    c.output().expect("runs")
}

fn text(o: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    )
}

fn assert_no_leak(out: &str) {
    assert!(
        !out.to_lowercase().contains(WORD),
        "the word was printed:\n{out}"
    );
    assert!(!out.contains(&public()), "the address was printed:\n{out}");
}

#[test]
fn a_leaking_line_fails_with_place_and_rule_only() {
    let d = repo("hit");
    let o = run(&d, &["--base", "base"], Some("# list\nExampleHost\n"));
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(
        out.contains("public-leak: notes.txt:2 (file #1): denylist entry #1"),
        "{out}"
    );
    assert!(
        out.contains("public-leak: notes.txt:2 (file #1): public IPv4 address"),
        "{out}"
    );
    assert!(!out.contains("denylist_absent"), "{out}");
    assert_no_leak(&out);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn without_a_denylist_only_the_address_rule_runs_and_says_so() {
    let d = repo("absent");
    let o = run(&d, &["--base", "base"], None);
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(out.contains("denylist_absent"), "{out}");
    assert!(out.contains("public IPv4 address"), "{out}");
    assert!(!out.contains("denylist entry"), "{out}");
    assert_no_leak(&out);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn nothing_added_is_clean() {
    let d = repo("clean");
    let o = run(&d, &["--base", "base", "--head", "base"], Some(WORD));
    assert_eq!(o.status.code(), Some(0), "{}", text(&o));
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn the_pr_title_and_body_from_the_event_are_checked() {
    let d = repo("event");
    let ev = d.join("event.json");
    let payload = serde_json::json!({"pull_request": {"title": "roll out to examplehost", "body": format!("a\nsee {}", public())}});
    std::fs::write(&ev, payload.to_string()).expect("write");
    let o = run(
        &d,
        &[
            "--base",
            "base",
            "--head",
            "base",
            "--github-event",
            ev.to_str().expect("utf-8"),
        ],
        Some(WORD),
    );
    let out = text(&o);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(
        out.contains("public-leak: PR title: denylist entry #1"),
        "{out}"
    );
    assert!(
        out.contains("public-leak: PR body line 2: public IPv4 address"),
        "{out}"
    );
    assert_no_leak(&out);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn a_bad_revision_or_argument_is_exit_2() {
    let d = repo("bad");
    assert_eq!(
        run(&d, &["--base", "no-such-ref"], Some(WORD))
            .status
            .code(),
        Some(2)
    );
    assert_eq!(
        run(&d, &["--base", "--output=x"], Some(WORD)).status.code(),
        Some(2)
    );
    assert_eq!(run(&d, &[], Some(WORD)).status.code(), Some(2));
    let _ = std::fs::remove_dir_all(&d);
}
