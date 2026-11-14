//! The `aib-idea` binary end to end (DEV-S76 r2 §7), on a root folder whose name has a space
//! and Chinese in it.

use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

const NOW_MS: &str = "1794623580000"; // 2026-11-14 10:33 Taipei

struct Home {
    _tmp: tempfile::TempDir,
    root: PathBuf,
}

impl Home {
    fn new() -> Home {
        let tmp = tempfile::tempdir().expect("tmp");
        let root = tmp.path().join("home 有 空白").join(".ez2-aib");
        Home { _tmp: tmp, root }
    }
    fn ledger(&self) -> PathBuf {
        self.root.join("state").join("ideas").join("ideas.jsonl")
    }
    fn text(&self) -> String {
        fs::read_to_string(self.ledger()).unwrap_or_default()
    }
    fn cmd(&self, args: &[&str]) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_aib-idea"));
        c.args(args)
            .env("EZ2_AIB_HOME", &self.root)
            .env("AIB_IDEA_NOW_MS", NOW_MS)
            .env("AIB_IDEA_TZ_OFFSET_MIN", "480")
            .env_remove("AIB_SEAT_CODE");
        c
    }
    fn run(&self, args: &[&str]) -> Output {
        self.cmd(args).output().expect("run aib-idea")
    }
    fn add(&self, title: &str, touches: &str) -> String {
        let o = self.run(&add_args(title, touches));
        assert_eq!(o.status.code(), Some(0), "reason: add_failed {o:?}");
        let line = out(&o);
        line.split_whitespace()
            .next()
            .unwrap_or_default()
            .to_string()
    }
}

fn add_args<'a>(title: &'a str, touches: &'a str) -> Vec<&'a str> {
    vec![
        "add",
        "--seat",
        "043dd",
        "--title",
        title,
        "--now",
        "現況",
        "--suggest",
        "建議",
        "--benefit",
        "好處",
        "--cost",
        "十分鐘;回復:revert",
        "--touches",
        touches,
        "--grade",
        "A",
        "--axis",
        "stable",
        "--node",
        "S76",
    ]
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn ok_args<'a>(ids: &[&'a str]) -> Vec<&'a str> {
    let mut v = vec!["review", "--ok"];
    v.extend_from_slice(ids);
    v.extend(["--by", "andy", "--quote", "同意(dm q_x)", "--seat", "043dd"]);
    v
}

#[test]
fn add_footer_review_round_trip() {
    let h = Home::new();
    let a = h.add("加隊序變動行", "none");
    let b = h.add("收據加耗時欄", "none");
    let p = h.add("看板改公開分享", "external");
    let f = out(&h.run(&["footer", "--seat", "043dd"]));
    assert!(
        f.starts_with("真人未審:本次列出 2 條可後審核准;另有 1 條需個別授權。"),
        "{f}"
    );
    assert!(
        f.contains(&format!(
            "- A | 穩定 | 自動同意 | {a} S76 加隊序變動行(10:33)\n"
        )),
        "{f}"
    );
    assert!(
        f.contains(&format!(
            "- A | 穩定 | 需人同意 | {p} S76 看板改公開分享 [external](10:33)\n"
        )),
        "{f}"
    );
    // something added after the footer is not in Andy's 「同意」
    let c = h.add("晚來的", "none");
    let o = h.run(&ok_args(&[&a, &b]));
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert_eq!(out(&o), format!("{a} ok\n{b} ok\n"));
    let o = h.run(&["list", "--unreviewed", "--seat", "043dd", "--json"]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    let left: Vec<&str> = v["ideas"]
        .as_array()
        .expect("ideas")
        .iter()
        .filter_map(|i| i["short"].as_str())
        .collect();
    assert_eq!(
        left,
        vec![p.as_str(), c.as_str()],
        "reason: footer_batch_took_more"
    );
    let o = h.run(&["show", &a]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(v["human_review"], "ok");
    assert_eq!(v["review"]["quote"], "同意(dm q_x)");
}

#[test]
fn a_batch_that_breaks_a_rule_writes_nothing() {
    let h = Home::new();
    let a = h.add("一", "none");
    let b = h.add("二", "none");
    let p = h.add("正式機", "prod");
    let before = h.text();
    for ids in [vec![a.as_str(), p.as_str()], vec![a.as_str(), "i_ffffffff"]] {
        let o = h.run(&ok_args(&ids));
        assert_eq!(
            o.status.code(),
            Some(3),
            "reason: bad_batch_not_rc3 {ids:?} {o:?}"
        );
        assert_eq!(h.text(), before, "reason: partial_batch_written {ids:?}");
    }
    // a no is allowed on anything; then an ok of the same is refused whole
    let o = h.run(&[
        "review",
        "--no",
        &a,
        "--by",
        "andy",
        "--quote",
        "不要",
        "--why",
        "先不做",
        "--seat",
        "043dd",
    ]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let before = h.text();
    let o = h.run(&ok_args(&[&b, &a]));
    assert_eq!(o.status.code(), Some(3), "reason: decided_again {o:?}");
    assert_eq!(h.text(), before, "reason: partial_batch_written");
}

#[test]
fn add_help_shows_the_touches_examples() {
    let h = Home::new();
    let o = h.run(&["add", "--help"]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let t = out(&o);
    for w in [
        "--grade",
        "--axis",
        "S → A → B → C",
        "算 none",
        "算 rule",
        "算 machine",
        "算 external",
        "五條全成立",
    ] {
        assert!(t.contains(w), "reason: help_missing {w}");
    }
    assert!(!h.ledger().exists(), "reason: help_wrote");
}

#[test]
fn usage_errors_write_nothing() {
    let h = Home::new();
    let cases: Vec<Vec<&str>> = vec![
        vec!["add", "--seat", "043dd", "--title", "t"],
        add_args("t", "none,prod"),
        add_args("t", ""),
        add_args("t", "code"),
        {
            let mut v = add_args("t", "none");
            v[2] = "043DD";
            v
        },
        vec![
            "review",
            "--ok",
            "i_00000000",
            "--by",
            "043dd",
            "--quote",
            "同意",
            "--seat",
            "043dd",
        ],
        vec![
            "review",
            "--ok",
            "i_00000000",
            "--by",
            "andy",
            "--seat",
            "043dd",
        ],
        vec![
            "review",
            "--no",
            "i_00000000",
            "--by",
            "andy",
            "--quote",
            "不要",
            "--seat",
            "043dd",
        ],
        {
            let mut v = add_args("t", "none");
            v.truncate(v.len() - 6); // drops --grade A --axis stable --node S76
            v.extend(["--node", "S76"]);
            v
        },
        {
            let mut v = add_args("t", "none");
            let at = v.iter().position(|w| *w == "--grade").expect("grade");
            v[at + 1] = "D";
            v
        },
        {
            let mut v = add_args("t", "none");
            let at = v.iter().position(|w| *w == "--grade").expect("grade");
            v[at + 1] = "a";
            v
        },
        {
            let mut v = add_args("t", "none");
            let at = v.iter().position(|w| *w == "--axis").expect("axis");
            v[at + 1] = "穩定";
            v
        },
        vec!["list"],
        vec!["footer"],
    ];
    for args in cases {
        let o = h.run(&args);
        assert_eq!(
            o.status.code(),
            Some(64),
            "reason: usage_not_64 {args:?} {o:?}"
        );
    }
    assert!(!h.ledger().exists(), "reason: usage_error_wrote");
}

#[test]
fn an_incomplete_ledger_refuses_every_write() {
    let h = Home::new();
    let a = h.add("一", "none");
    // a crash left half a line with no newline
    let mut t = h.text();
    t.push_str("{\"v\":1,\"ev\":\"review_batch\"");
    fs::write(h.ledger(), &t).expect("write");
    for args in [add_args("二", "none"), ok_args(&[&a])] {
        let o = h.run(&args);
        assert_eq!(
            o.status.code(),
            Some(2),
            "reason: write_on_damage {args:?} {o:?}"
        );
        assert_eq!(h.text(), t, "reason: wrote_on_damage {args:?}");
    }
    let o = h.run(&["footer", "--seat", "043dd"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).contains("帳本不完整"), "{o:?}");
    let o = h.run(&["list", "--all"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(out(&o).contains(&a), "reason: readable_lines_hidden {o:?}");
}

#[test]
fn the_same_source_is_added_once() {
    let h = Home::new();
    let mut args = add_args("手記 m01", "none");
    args.extend(["--source", "manual:m01"]);
    let first = out(&h.run(&args));
    let again = h.run(&args);
    assert_eq!(again.status.code(), Some(0));
    assert_eq!(out(&again), first, "reason: source_not_idempotent");
    assert_eq!(h.text().lines().count(), 1, "reason: source_added_twice");
}

#[test]
fn two_writers_fifty_adds_each_lose_nothing() {
    let h = Home::new();
    let kids: Vec<_> = (0..2)
        .map(|w| {
            let root = h.root.clone();
            std::thread::spawn(move || {
                for n in 0..50 {
                    let title = format!("寫手 {w} 第 {n} 條");
                    let o = Command::new(env!("CARGO_BIN_EXE_aib-idea"))
                        .args(add_args(&title, "none"))
                        .env("EZ2_AIB_HOME", &root)
                        .env_remove("AIB_SEAT_CODE")
                        .output()
                        .expect("run");
                    assert_eq!(o.status.code(), Some(0), "{o:?}");
                }
            })
        })
        .collect();
    for k in kids {
        k.join().expect("writer");
    }
    let o = h.run(&["list", "--all", "--json"]);
    assert_eq!(
        o.status.code(),
        Some(0),
        "reason: concurrent_ledger_broken {o:?}"
    );
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    let ids: std::collections::BTreeSet<&str> = v["ideas"]
        .as_array()
        .expect("ideas")
        .iter()
        .filter_map(|i| i["id"].as_str())
        .collect();
    assert_eq!(ids.len(), 100, "reason: concurrent_add_lost_or_doubled");
}

#[test]
fn a_held_lock_times_out_with_rc_4() {
    let h = Home::new();
    h.add("先建資料夾", "none");
    let lock = fs::OpenOptions::new()
        .write(true)
        .open(h.root.join("state").join("ideas").join("ideas.lock"))
        .expect("lock file");
    lock.lock().expect("hold the lock");
    let before = h.text();
    let o = h.run(&add_args("等不到鎖", "none"));
    assert_eq!(o.status.code(), Some(4), "reason: lock_wait_not_rc4 {o:?}");
    assert_eq!(h.text(), before);
    drop(lock);
}

#[test]
fn a_write_that_cannot_land_is_not_a_success() {
    let h = Home::new();
    let dir = h.root.join("state").join("ideas");
    fs::create_dir_all(dir.join("ideas.jsonl")).expect("a folder where the ledger goes");
    let o = h.run(&add_args("寫不進去", "none"));
    assert_eq!(
        o.status.code(),
        Some(4),
        "reason: failed_write_not_rc4 {o:?}"
    );
}
