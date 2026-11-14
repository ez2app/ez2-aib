//! The `aib-idea` binary end to end, on a ledger in a temp folder.

use std::path::Path;
use std::process::{Command, Output};

const NOW: &str = "1794623580"; // 2026-11-14 10:33 Taipei

fn run(ledger: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_aib-idea"))
        .args(args)
        .env("AIB_IDEA_LEDGER", ledger)
        .env("AIB_IDEA_NOW", NOW)
        .env("AIB_IDEA_TZ_OFFSET_MIN", "480")
        .output()
        .expect("run aib-idea")
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn add(ledger: &Path, title: &str, touches: Option<&str>) -> (String, String) {
    let mut args = vec![
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
        "代價",
        "--node",
        "S76",
    ];
    if let Some(t) = touches {
        args.extend(["--touches", t]);
    }
    let o = run(ledger, &args);
    assert_eq!(o.status.code(), Some(0), "reason: add_failed {o:?}");
    let line = out(&o);
    let mut w = line.split_whitespace();
    (
        w.next().unwrap_or_default().to_string(),
        w.next().unwrap_or_default().to_string(),
    )
}

#[test]
fn add_review_and_footer_round_trip() {
    let dir = tempfile::tempdir().expect("tmp");
    let l = dir.path().join("state").join("ideas.jsonl");
    let (a, va) = add(&l, "加隊序變動行", Some("code,docs"));
    let (b, vb) = add(&l, "改正式機設定", Some("prod"));
    let (c, vc) = add(&l, "沒寫類別", None);
    assert_eq!(
        (va.as_str(), vb.as_str(), vc.as_str()),
        ("auto", "needs_human", "needs_human")
    );

    let f = out(&run(&l, &["footer", "--seat", "043dd"]));
    assert!(f.starts_with("真人未審(3 條"), "reason: footer_count {f}");
    assert!(
        f.contains(&format!("- {a} S76 加隊序變動行(auto,10:33)")),
        "{f}"
    );

    // one 「同意」 takes only the automatic ones
    let o = run(
        &l,
        &[
            "review",
            "--ok-all-unreviewed",
            "--seat",
            "043dd",
            "--by",
            "andy",
            "--quote",
            "同意",
        ],
    );
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert_eq!(out(&o), format!("{a} ok\n"), "reason: ok_all_took_more");

    let o = run(
        &l,
        &[
            "review",
            "--no",
            &b,
            "--by",
            "andy",
            "--quote",
            "不要",
            "--why",
            "正式機",
        ],
    );
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let o = run(&l, &["list", "--unreviewed", "--json"]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    let ids: Vec<&str> = v["ideas"]
        .as_array()
        .expect("ideas")
        .iter()
        .filter_map(|i| i["id"].as_str())
        .collect();
    assert_eq!(ids, vec![c.as_str()], "reason: unreviewed_list_wrong");
    let o = run(&l, &["list", "--status", "no"]);
    assert!(
        out(&o).starts_with(&format!("{b} no 043dd 10:33 S76 改正式機設定")),
        "{o:?}"
    );
    let o = run(&l, &["show", &a]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(v["status"], "ok");
    assert_eq!(v["reviews"][0]["quote"], "同意");
}

#[test]
fn usage_errors_write_nothing() {
    let dir = tempfile::tempdir().expect("tmp");
    let l = dir.path().join("ideas.jsonl");
    let cases: [&[&str]; 8] = [
        &["add", "--seat", "043dd", "--title", "t"],
        &[
            "add",
            "--seat",
            "043DD",
            "--title",
            "t",
            "--now",
            "n",
            "--suggest",
            "s",
            "--benefit",
            "b",
            "--cost",
            "c",
        ],
        &[
            "add",
            "--seat",
            "043dd",
            "--title",
            "t",
            "--now",
            "n",
            "--suggest",
            "s",
            "--benefit",
            "b",
            "--cost",
            "c",
            "--touches",
            "mony",
        ],
        &[
            "review",
            "--ok",
            "i_00000000",
            "--by",
            "andy",
            "--quote",
            "同意",
        ],
        &[
            "review",
            "--ok-all-unreviewed",
            "--by",
            "andy",
            "--quote",
            "同意",
        ],
        &[
            "review",
            "--ok-all-unreviewed",
            "--seat",
            "043dd",
            "--by",
            "043dd",
            "--quote",
            "同意",
        ],
        &[
            "review",
            "--ok-all-unreviewed",
            "--seat",
            "043dd",
            "--by",
            "andy",
        ],
        &["list", "--since", "24時"],
    ];
    for args in cases {
        let o = run(&l, args);
        assert_eq!(
            o.status.code(),
            Some(64),
            "reason: usage_not_64 {args:?} {o:?}"
        );
    }
    assert!(!l.exists(), "reason: usage_error_wrote");
}

#[test]
fn a_bad_line_gives_rc_2_and_the_rest_still_lists() {
    let dir = tempfile::tempdir().expect("tmp");
    let l = dir.path().join("ideas.jsonl");
    let (a, _) = add(&l, "一", Some("code"));
    // a line cut short by a crash, with no newline: the next add starts a fresh line
    let mut text = std::fs::read_to_string(&l).expect("read");
    text.push_str("{\"ev\":\"add\",\"id\"");
    std::fs::write(&l, text).expect("write");
    let o = run(
        &l,
        &[
            "add",
            "--seat",
            "043dd",
            "--title",
            "二",
            "--now",
            "n",
            "--suggest",
            "s",
            "--benefit",
            "b",
            "--cost",
            "c",
            "--touches",
            "code",
        ],
    );
    assert_eq!(o.status.code(), Some(2), "reason: bad_line_not_rc2 {o:?}");
    let o = run(&l, &["list"]);
    assert_eq!(o.status.code(), Some(2));
    let listed = out(&o);
    assert!(
        listed.contains(&a) && listed.contains(" 二"),
        "reason: good_lines_lost {listed}"
    );
    assert!(
        String::from_utf8_lossy(&o.stderr).contains("line 2:"),
        "{o:?}"
    );
}

#[test]
fn concurrent_adds_all_land_whole() {
    let dir = tempfile::tempdir().expect("tmp");
    let l = dir.path().join("ideas.jsonl");
    let kids: Vec<_> = (0..8)
        .map(|n| {
            Command::new(env!("CARGO_BIN_EXE_aib-idea"))
                .args([
                    "add",
                    "--seat",
                    "043dd",
                    "--title",
                    &format!("並行 {n}"),
                    "--now",
                    "n",
                    "--suggest",
                    "s",
                    "--benefit",
                    "b",
                    "--cost",
                    "c",
                    "--touches",
                    "code",
                ])
                .env("AIB_IDEA_LEDGER", &l)
                .spawn()
                .expect("spawn")
        })
        .collect();
    for mut k in kids {
        assert_eq!(k.wait().expect("wait").code(), Some(0));
    }
    let o = run(&l, &["list", "--json"]);
    assert_eq!(
        o.status.code(),
        Some(0),
        "reason: concurrent_lines_broken {o:?}"
    );
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    assert_eq!(
        v["ideas"].as_array().map(Vec::len),
        Some(8),
        "reason: concurrent_add_lost"
    );
}
