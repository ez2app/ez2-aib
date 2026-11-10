// SPDX-License-Identifier: Apache-2.0
//! The aib-persona binary end to end, in a temp home: pointers checked against the file system,
//! the dm queue and a fake gh; void rules; groups; the tools-root pin.
#![cfg(unix)]

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;

struct Env {
    _t: tempfile::TempDir,
    home: PathBuf,
    aib: PathBuf,
    cand: PathBuf,
    root: PathBuf,
    outside: PathBuf,
}

fn env() -> Env {
    let t = tempfile::tempdir().expect("tempdir");
    let base = std::fs::canonicalize(t.path()).expect("canon");
    let (home, aib, cand) = (base.join("home"), base.join("aib"), base.join("cand"));
    let (root, outside) = (base.join("root"), base.join("outside"));
    for d in [&home, &aib, &cand, &root, &outside] {
        std::fs::create_dir_all(d).expect("mkdir");
    }
    // a fake gh: PR 5 exists, PR 6 does not, anything else cannot be asked
    let gh = base.join("gh");
    std::fs::write(
        &gh,
        "#!/bin/sh\ncase \"$2\" in\n  */pulls/5) echo 5 ;;\n  */pulls/6) echo 'gh: Not Found (HTTP 404)' >&2; exit 1 ;;\n  *) echo 'network down' >&2; exit 1 ;;\nesac\n",
    )
    .expect("gh");
    let mut p = std::fs::metadata(&gh).expect("md").permissions();
    std::os::unix::fs::PermissionsExt::set_mode(&mut p, 0o755);
    std::fs::set_permissions(&gh, p).expect("chmod");
    Env {
        _t: t,
        home,
        aib,
        cand,
        root,
        outside,
    }
}

impl Env {
    fn run(&self, args: &[&str]) -> (i32, Value, String) {
        let out = Command::new(env!("CARGO_BIN_EXE_aib-persona"))
            .args(args)
            .current_dir(&self.root)
            .env("HOME", &self.home)
            .env("EZ2_AIB_STATE_ROOT", &self.aib)
            .env_remove("EZ2_AIB_HOME")
            .env("EZ2_PERSONA_CANDIDATE_ROOT", &self.cand)
            .env("EZ2_PERSONA_RECEIPT_ROOTS", &self.root)
            .env(
                "AIB_PERSONA_GH",
                self.home.parent().expect("base").join("gh"),
            )
            .output()
            .expect("run aib-persona");
        let v = serde_json::from_slice(&out.stdout).unwrap_or(Value::Null);
        (
            out.status.code().unwrap_or(-1),
            v,
            String::from_utf8_lossy(&out.stderr).into_owned(),
        )
    }

    fn add(&self, ptr: &str, lane: &str, by: &str) -> (i32, Value, String) {
        self.run(&[
            "evidence",
            "add",
            "--persona-code",
            "p1",
            "--lane",
            lane,
            "--day",
            "2026-11-10",
            "--pointer",
            ptr,
            "--by",
            by,
        ])
    }

    fn dm(&self, sub: &str, id: &str) {
        let d = self.aib.join("state").join("dm-queue").join(sub);
        std::fs::create_dir_all(&d).expect("mkdir");
        std::fs::write(d.join(format!("{id}.json")), "{}").expect("dm");
    }

    fn ledger(&self) -> String {
        std::fs::read_to_string(self.cand.join("p1").join("evidence.jsonl")).unwrap_or_default()
    }
}

fn s(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

#[test]
fn receipts_and_file_lines_are_checked_on_the_real_path() {
    let e = env();
    let r = e.root.join("REVIEW-x.md");
    std::fs::write(&r, "line 1\nline 2\n").expect("w");
    // absolute receipt, absolute path:line, relative path:line — all under the root
    let (rc, v, err) = e.add(&s(&r), "a", "seat:aaaaa");
    assert_eq!(rc, 0, "{err}");
    assert_eq!(v["pointer_kind"], "receipt");
    assert_eq!(v["resolved"], s(&r));
    assert_eq!(v["sha256"].as_str().map(str::len), Some(64), "{v}");
    let (rc, v, err) = e.add(&format!("{}:2", s(&r)), "b", "seat:aaaaa");
    assert_eq!(
        (rc, &v["pointer_kind"], &v["line_no"]),
        (0, &Value::from("file_line"), &Value::from(2)),
        "{err}"
    );
    let (rc, _, err) = e.add("./REVIEW-x.md:1", "c", "seat:aaaaa");
    assert_eq!(rc, 0, "{err}");
    // line 0, a line past the end, a missing file, a directory
    for bad in [
        format!("{}:0", s(&r)),
        format!("{}:3", s(&r)),
        s(&e.root.join("nope.md")),
        format!("{}:1", s(&e.root)),
    ] {
        let (rc, _, err) = e.add(&bad, "d", "seat:aaaaa");
        assert_eq!(rc, 2, "{bad}: {err}");
    }
    // a receipt needs a receipt extension; any file works as path:line
    std::fs::write(e.root.join("a.rs"), "fn x() {}\n").expect("w");
    assert_eq!(e.add(&s(&e.root.join("a.rs")), "e", "seat:aaaaa").0, 2);
    assert_eq!(
        e.add(&format!("{}:1", s(&e.root.join("a.rs"))), "e", "seat:aaaaa")
            .0,
        0
    );
}

#[test]
fn paths_outside_the_roots_are_refused_however_they_are_spelled() {
    let e = env();
    let o = e.outside.join("forged.md");
    std::fs::write(&o, "x\n").expect("w");
    let (rc, _, err) = e.add(&s(&o), "a", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    assert!(err.contains("not under an allowed root"), "{err}");
    // `..` from inside the root
    let (rc, _, err) = e.add("../outside/forged.md", "a", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    // a link inside the root that points outside
    std::os::unix::fs::symlink(&o, e.root.join("link.md")).expect("ln");
    let (rc, _, err) = e.add(&s(&e.root.join("link.md")), "a", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    assert_eq!(e.ledger(), "", "nothing written");
}

#[test]
fn dm_ids_must_be_in_the_queue_including_retired() {
    let e = env();
    e.dm("1ced7", "q_00000000000000aa");
    e.dm("retired/1ced7-1", "q_00000000000000bb");
    assert_eq!(e.add("q_00000000000000aa", "a", "seat:aaaaa").0, 0);
    assert_eq!(e.add("q_00000000000000bb", "b", "seat:aaaaa").0, 0);
    let (rc, _, err) = e.add("q_00000000000000cc", "c", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
}

#[test]
fn pull_requests_are_asked_of_github() {
    let e = env();
    let (rc, v, err) = e.add("pr:ez2app/ez2-aib#5", "a", "seat:aaaaa");
    assert_eq!((rc, &v["pointer_kind"]), (0, &Value::from("pr")), "{err}");
    let (rc, _, err) = e.add("pr:ez2app/ez2-aib#6", "b", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    assert!(err.contains("no such pull request"), "{err}");
    let (rc, _, err) = e.add("pr:ez2app/ez2-aib#7", "c", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    assert!(err.contains("could not ask"), "{err}");
}

#[test]
fn void_takes_a_line_out_of_the_count_and_keeps_it_on_record() {
    let e = env();
    let (_, a, _) = e.add("deadbee", "a", "seat:aaaaa");
    let (_, b, _) = e.add("cafef00d", "b", "seat:bbbbb");
    let (_, g, _) = e.run(&["groups", "--persona-code", "p1"]);
    assert_eq!(g["groups"], 2, "{g}");
    let id_b = b["id"].as_str().expect("id").to_string();
    let id_a = a["id"].as_str().expect("id").to_string();
    // another writer's line needs a dm id that exists
    let void = |id: &str, by: &str, why: &str| {
        e.run(&[
            "evidence",
            "void",
            "--persona-code",
            "p1",
            "--id",
            id,
            "--by",
            by,
            "--why",
            why,
        ])
    };
    let (rc, _, err) = void(&id_b, "seat:aaaaa", "not mine but wrong");
    assert_eq!(rc, 2, "{err}");
    let (rc, _, err) = void(&id_b, "seat:aaaaa", "per q_00000000000000dd");
    assert_eq!(rc, 2, "a dm id that is not in the queue: {err}");
    e.dm("bbbbb", "q_00000000000000dd");
    let (rc, v, err) = void(&id_b, "seat:aaaaa", "per q_00000000000000dd");
    assert_eq!((rc, &v["already"]), (0, &Value::from(false)), "{err}");
    // own line: no dm id needed; again: nothing new written
    let (rc, _, err) = void(&id_a, "seat:aaaaa", "wrong lane");
    assert_eq!(rc, 0, "{err}");
    let before = e.ledger();
    let (rc, v, _) = void(&id_a, "seat:aaaaa", "wrong lane");
    assert_eq!((rc, &v["already"]), (0, &Value::from(true)));
    assert_eq!(e.ledger(), before, "a repeated void writes nothing");
    let (_, g, _) = e.run(&["groups", "--persona-code", "p1"]);
    assert_eq!(
        (&g["groups"], &g["voided_count"]),
        (&Value::from(0), &Value::from(2)),
        "{g}"
    );
    // list hides withdrawn lines; --all shows them with their void
    let (_, l, _) = e.run(&["evidence", "list", "--persona-code", "p1"]);
    assert_eq!(l["rows"].as_array().map(Vec::len), Some(0), "{l}");
    let (_, l, _) = e.run(&["evidence", "list", "--persona-code", "p1", "--all"]);
    assert_eq!(l["rows"].as_array().map(Vec::len), Some(4), "{l}");
    assert!(l["rows"][0]["voided_by"].is_string(), "{l}");
    // the ledger still has every line: nothing was rewritten
    assert_eq!(e.ledger().lines().count(), 4);
}

#[test]
fn old_lines_without_by_are_read_and_need_a_dm_id_to_void() {
    let e = env();
    let dir = e.cand.join("p1");
    std::fs::create_dir_all(&dir).expect("mkdir");
    let old = r#"{"at_ms":1,"day":"2026-11-09","lane":"x","note":"t","persona_code":"p1","pointer":"9e13480e","pointer_kind":"commit","schema":"aib-persona-evidence/v1","session":null}"#;
    std::fs::write(dir.join("evidence.jsonl"), format!("{old}\n")).expect("w");
    let (_, g, _) = e.run(&["groups", "--persona-code", "p1"]);
    assert_eq!(g["groups"], 1, "{g}");
    let id = aib_persona::line_id(1, old.as_bytes());
    let (rc, _, err) = e.run(&[
        "evidence",
        "void",
        "--persona-code",
        "p1",
        "--id",
        &id,
        "--by",
        "seat:a9136",
        "--why",
        "lane x note t is junk",
    ]);
    assert_eq!(rc, 2, "{err}");
    e.dm("f1fae", "q_00000000000000ee");
    let (rc, _, err) = e.run(&[
        "evidence",
        "void",
        "--persona-code",
        "p1",
        "--id",
        &id,
        "--by",
        "seat:a9136",
        "--why",
        "junk, per q_00000000000000ee",
    ]);
    assert_eq!(rc, 0, "{err}");
    // a new line after it gets the next line number
    let (_, v, _) = e.add("deadbee", "a", "seat:a9136");
    assert!(
        v["id"].as_str().is_some_and(|i| i.starts_with("e3-")),
        "{v}"
    );
}

#[test]
fn an_unfinished_ledger_is_not_appended_to() {
    let e = env();
    let dir = e.cand.join("p1");
    std::fs::create_dir_all(&dir).expect("mkdir");
    std::fs::write(
        dir.join("evidence.jsonl"),
        "{\"schema\":\"aib-persona-evidence/v1\"",
    )
    .expect("w");
    let (rc, _, err) = e.add("deadbee", "a", "seat:aaaaa");
    assert_eq!(rc, 3, "{err}");
    let (rc, g, _) = e.run(&["groups", "--persona-code", "p1"]);
    assert_eq!((rc, &g["torn_tail"]), (0, &Value::from(true)), "{g}");
}

/// 043dd #627 M4: a file in the working folder written without a folder part (`x.md:1`,
/// `x.md`), as the old writer took it; L1: a FIFO is refused, not opened (it would block).
#[test]
fn a_bare_file_name_in_the_working_folder_is_a_path() {
    let e = env();
    std::fs::write(e.root.join("SUMMARY-x.md"), "one\ntwo\n").expect("w");
    let (rc, v, err) = e.add("SUMMARY-x.md:2", "a", "seat:aaaaa");
    assert_eq!(
        (rc, &v["pointer_kind"]),
        (0, &Value::from("file_line")),
        "{err}"
    );
    assert_eq!(v["resolved"], s(&e.root.join("SUMMARY-x.md")));
    let (rc, v, err) = e.add("SUMMARY-x.md", "b", "seat:aaaaa");
    assert_eq!(
        (rc, &v["pointer_kind"]),
        (0, &Value::from("receipt")),
        "{err}"
    );
    // still checked: a name that is not there, and a FIFO in the root
    assert_eq!(e.add("NOPE.md:1", "c", "seat:aaaaa").0, 2);
    let st = Command::new("mkfifo")
        .arg(e.root.join("pipe.md"))
        .status()
        .expect("mkfifo");
    assert!(st.success());
    let (rc, _, err) = e.add("pipe.md", "c", "seat:aaaaa");
    assert_eq!(rc, 2, "{err}");
    assert!(err.contains("not a regular file"), "{err}");
}

/// 043dd #627 M3: a ledger that is a link, or a persona folder that leads out of the
/// candidate root, is not written through.
#[test]
fn the_ledger_is_never_written_through_a_link() {
    let e = env();
    // the ledger file is a link to some other file. The target is empty on purpose: an empty
    // file is a valid (empty) ledger, so only the link check stands between the append and
    // it; a target with text in it would be refused by the ledger parser anyway (a63ab, CI
    // run 42817347354: the knife on this check missed while the target held "keep me").
    let target = e.outside.join("role.md");
    std::fs::write(&target, "").expect("w");
    std::fs::create_dir_all(e.cand.join("p1")).expect("mkdir");
    std::os::unix::fs::symlink(&target, e.cand.join("p1").join("evidence.jsonl")).expect("ln");
    let (rc, _, err) = e.add("deadbee", "a", "seat:aaaaa");
    assert_eq!(rc, 3, "{err}");
    assert!(err.contains("is a link"), "{err}");
    assert_eq!(std::fs::read_to_string(&target).expect("r"), "");
    // the persona folder is a link out of the candidate root
    let e = env();
    std::os::unix::fs::symlink(&e.outside, e.cand.join("p1")).expect("ln");
    let (rc, _, err) = e.add("deadbee", "a", "seat:aaaaa");
    assert_eq!(rc, 3, "{err}");
    assert!(!e.outside.join("evidence.jsonl").exists());
}

#[test]
fn recheck_tells_same_changed_and_missing() {
    let e = env();
    for (lane, body) in [("a", "one\n"), ("b", "two\n"), ("c", "three\n")] {
        let p = e.root.join(format!("{lane}.md"));
        std::fs::write(&p, body).expect("w");
        assert_eq!(e.add(&s(&p), lane, "seat:aaaaa").0, 0);
    }
    std::fs::write(e.root.join("b.md"), "changed\n").expect("w");
    std::fs::remove_file(e.root.join("c.md")).expect("rm");
    let (_, l, _) = e.run(&["evidence", "list", "--persona-code", "p1", "--recheck"]);
    let got: Vec<&str> = l["rows"]
        .as_array()
        .expect("rows")
        .iter()
        .filter_map(|r| r["recheck"].as_str())
        .collect();
    assert_eq!(got, ["same", "changed", "missing"], "{l}");
}

fn git(dir: &Path, args: &[&str]) {
    let ok = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args([
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
        ])
        .args(args)
        .status()
        .expect("git")
        .success();
    assert!(ok, "git {args:?}");
}

#[test]
fn doctor_pins_a_sha_and_refuses_anything_else() {
    let e = env();
    let repo = e.outside.join("ez2-ai");
    std::fs::create_dir_all(repo.join("tools")).expect("mkdir");
    git(&repo, &["init", "-q"]);
    std::fs::write(repo.join("tools/ez2-role-birth-check.js"), "// v1\n").expect("w");
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "v1"]);
    // nothing pinned: usage error, no scan
    let (rc, _, err) = e.run(&["doctor"]);
    assert_eq!(rc, 2, "{err}");
    let (rc, v, err) = e.run(&["doctor", "--pin", "--repo", &s(&repo), "--ref", "HEAD"]);
    assert_eq!(rc, 0, "{err}");
    let sha = v["pinned"]["sha"].as_str().expect("sha").to_string();
    assert_eq!(sha.len(), 40);
    // the pinned tree is locked, so auto-archive / prune leave it (043dd #627 L4); pinning
    // the same sha again finds it already locked and still succeeds
    let list = Command::new("git")
        .arg("-C")
        .arg(&repo)
        .args(["worktree", "list", "--porcelain"])
        .output()
        .expect("git worktree list");
    let list = String::from_utf8_lossy(&list.stdout);
    assert!(list.contains("locked aib-persona pin"), "{list}");
    let (rc, _, err) = e.run(&["doctor", "--pin", "--repo", &s(&repo), "--ref", "HEAD"]);
    assert_eq!(rc, 0, "{err}");
    // a later commit does not move the pin
    std::fs::write(repo.join("tools/ez2-role-birth-check.js"), "// v2\n").expect("w");
    git(&repo, &["commit", "-qam", "v2"]);
    let (rc, v, err) = e.run(&["doctor"]);
    assert_eq!(rc, 0, "{err}");
    assert_eq!(v["pinned"]["sha"], sha);
    let root = PathBuf::from(v["root"].as_str().expect("root"));
    assert!(root.join("tools/ez2-role-birth-check.js").is_file());
    // a changed pinned tree is broken, not silently used
    std::fs::write(root.join("tools/ez2-role-birth-check.js"), "// edited\n").expect("w");
    let (rc, _, err) = e.run(&["doctor"]);
    assert_eq!(rc, 3, "{err}");
    assert!(err.contains("local changes"), "{err}");
    // a ref that does not resolve pins nothing
    let (rc, _, _) = e.run(&[
        "doctor",
        "--pin",
        "--repo",
        &s(&repo),
        "--ref",
        "no-such-ref",
    ]);
    assert_eq!(rc, 3);
}

#[test]
fn usage_errors() {
    let e = env();
    for args in [
        vec![
            "evidence",
            "add",
            "--persona-code",
            "p1",
            "--lane",
            "a",
            "--day",
            "2026-99-99",
            "--pointer",
            "deadbee",
            "--by",
            "seat:a",
        ],
        vec![
            "evidence",
            "add",
            "--persona-code",
            "..",
            "--lane",
            "a",
            "--day",
            "2026-11-10",
            "--pointer",
            "deadbee",
            "--by",
            "seat:a",
        ],
        vec![
            "evidence",
            "add",
            "--persona-code",
            "p1",
            "--lane",
            "a",
            "--day",
            "2026-11-10",
            "--pointer",
            "deadbee",
        ],
        vec![
            "evidence",
            "add",
            "--persona-code",
            "p1",
            "--lane",
            "a",
            "--day",
            "2026-11-10",
            "--pointer",
            "a plain sentence",
            "--by",
            "seat:a",
        ],
        vec!["evidence", "list", "--persona-code", "p1", "--bogus", "x"],
        vec!["nope"],
    ] {
        let (rc, _, err) = e.run(&args);
        assert_eq!(rc, 2, "{args:?}: {err}");
    }
    assert_eq!(e.ledger(), "");
    // --help prints the usage and writes nothing (043dd #627 L3)
    for args in [vec!["evidence", "--help"], vec!["groups", "-h"]] {
        let out = Command::new(env!("CARGO_BIN_EXE_aib-persona"))
            .args(&args)
            .output()
            .expect("run");
        assert_eq!(out.status.code(), Some(0), "{args:?}");
        assert!(String::from_utf8_lossy(&out.stdout).contains("evidence add"));
    }
    // an argument that is not UTF-8 is a usage error, not a panic
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let bad = std::ffi::OsStr::from_bytes(b"\xff.md");
        let out = Command::new(env!("CARGO_BIN_EXE_aib-persona"))
            .args(["evidence", "add", "--pointer"])
            .arg(bad)
            .output()
            .expect("run");
        assert_eq!(out.status.code(), Some(2));
    }
    assert_eq!(e.ledger(), "");
}
