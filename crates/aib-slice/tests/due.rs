//! S73l `aib-slice due`: the two lists a reply ends with, from one sweep (IFACE-s73l r1).
//! The lists are made from a [`Report`], so these tests build one by hand; the CLI at the end
//! runs the real binary on a folder that is not a git repository.

use aib_slice::due::{due, render, to_json, Item};
use aib_slice::{Branch, Clash, PrInfo, Receipt, Report, Review};

const NOW: i64 = 1_794_630_000;
const H: i64 = 3600;

fn review(head: &str, at_head: bool, h: u32, m: u32) -> Review {
    Review {
        head: head.into(),
        verdict: "通過".into(),
        h,
        m,
        l: 0,
        n: 0,
        file: "REVIEW-x-043dd-aaaaaaaa.md".into(),
        at_head,
        unreviewed_after: if at_head {
            vec![]
        } else {
            vec!["c1".into(), "c2".into()]
        },
        reviewer: Some("043dd".into()),
        not_gate_reason: None,
    }
}

fn receipt(head: &str, nonzero: &[&str]) -> Receipt {
    Receipt {
        head: head.into(),
        file: "TEST-x.txt".into(),
        nonzero: nonzero.iter().map(|s| (*s).to_owned()).collect(),
        at_head: true,
        steps: vec!["fmt".into(), "test".into()],
        sealed: Some(true),
        complete: true,
    }
}

fn pr(number: u64, labels: &[&str]) -> PrInfo {
    PrInfo {
        number,
        open: true,
        base: Some("main".into()),
        labels: labels.iter().map(|s| (*s).to_owned()).collect(),
        ..PrInfo::default()
    }
}

/// A branch with its own commit, reviewed and green at its head, pushed.
fn ready(name: &str, head: &str, age_h: i64) -> Branch {
    let head = head.repeat(40);
    Branch {
        branch: name.into(),
        seat: aib_slice::seat_of(name),
        review: Some(review(&head, true, 0, 0)),
        receipt: Some(receipt(&head, &[])),
        head,
        local: true,
        last_commit_at: NOW - age_h * H,
        age_h,
        unique: 1,
        unique_not_in_prs: 1,
        pushed_to: vec![format!("origin/{name}")],
        conflicts_with_main: Some(Clash::default()),
        ..Branch::default()
    }
}

fn report(branches: Vec<Branch>) -> Report {
    Report {
        generated_at: NOW,
        main: "f".repeat(40),
        errors: vec![],
        open_prs: vec![],
        branches,
    }
}

fn texts(i: &Item) -> Vec<&str> {
    i.notes.iter().map(|n| n.text.as_str()).collect()
}

#[test]
fn what_can_merge_says_what_it_still_lacks() {
    // class 1: PR open, no label yet, 3 cells queued for 2 h 58 min and 1 running for 41 min
    let mut a = ready("feat/seatview-a9136-1114", "a", 5);
    a.pr = Some(PrInfo {
        queued: Some((3, Some(NOW - 10_680))),
        running: Some((1, Some(NOW - 41 * 60))),
        ..pr(750, &[])
    });
    // class 2: receipt only, not pushed, no PR, nobody reviewed
    let mut b = ready("feat/cireuse-ledger-b433b-1114", "b", 9);
    b.review = None;
    b.pushed_to.clear();
    // class 2: review of an older head, stacked on a's branch, changes a workflow
    let mut c = ready("feat/top-1ced7-1114", "c", 2);
    c.review = Some(review(&"9".repeat(40), false, 0, 0));
    c.stacked_on = vec!["feat/seatview-a9136-1114".into()];
    c.gate_files = vec![".github/workflows/aibd.yml".into()];
    // class 1 with everything: nothing lacking
    let mut d = ready("feat/done-043dd-1114", "d", 1);
    d.pr = Some(pr(753, &["ready-merge", "not-gate"]));
    let r = report(vec![a, b, c, d]);
    let got = due(&r, 72, NOW);
    assert!(got.fix.is_empty(), "{:?}", got.fix);
    let names: Vec<&str> = got.merge.iter().map(|i| i.target.as_str()).collect();
    assert_eq!(
        names,
        [
            "feat/cireuse-ledger-b433b-1114",
            "#750 seatview",
            "feat/top-1ced7-1114",
            "#753 done"
        ],
        "oldest commit first"
    );
    assert_eq!(texts(&got.merge[0]), ["沒推", "沒開 PR", "沒人審"]);
    assert_eq!(got.merge[0].has, ["receipt"]);
    assert_eq!(
        texts(&got.merge[1]),
        [
            "沒貼 ready-merge",
            "CI 3 格排隊 2 小時 58 分",
            "CI 1 格在跑 41 分"
        ]
    );
    assert_eq!(got.merge[1].notes[1].seconds, Some(10_680));
    assert_eq!(
        texts(&got.merge[2]),
        [
            "沒開 PR",
            "審查頭不在現在的頭(之後 2 個 commit 沒審)",
            "等 #750 先合",
            "新閘:排同批最後"
        ]
    );
    assert!(got.merge[3].notes.is_empty());
    let text = render(&got, &[], 8);
    assert!(text.starts_with("應合未合(4 件):\n"), "{text}");
    assert!(
        text.contains(
            "- #750 seatview · aaaaaaaa · 審過 + 收據齊 · 差:沒貼 ready-merge;CI 3 格排隊 2 小時 58 分;CI 1 格在跑 41 分\n"
        ),
        "{text}"
    );
    assert!(
        text.contains(
            "- feat/cireuse-ledger-b433b-1114 · bbbbbbbb · 收據齊 · 差:沒推;沒開 PR;沒人審\n"
        ),
        "{text}"
    );
    assert!(
        text.contains("- #753 done · dddddddd · 審過 + 收據齊 · 可以合了\n"),
        "{text}"
    );
    assert!(text.ends_with("應修未修(0 件):\n"), "{text}");
}

#[test]
fn what_must_be_fixed_says_where_it_is_red() {
    // red checks
    let mut a = ready("feat/r4-043dd-1112", "a", 30);
    a.pr = Some(PrInfo {
        checks: Some("FAILURE".into()),
        red_checks: Some(vec!["smoke".into(), "fresh-machine".into()]),
        ..pr(757, &[])
    });
    // conflicts with an open PR
    let mut b = ready("feat/brand-ebb36-1111", "b", 60);
    b.pr = Some(pr(664, &[]));
    b.conflicts_with = vec![(758, vec!["crates/x/probe.rs".into()])];
    // review at head with M 2, receipt red
    let mut c = ready("feat/mid-7b81e-1114", "c", 3);
    c.review = Some(review(&"c".repeat(40), true, 0, 2));
    c.receipt = Some(receipt(&"c".repeat(40), &["clippy"]));
    // would merge, but conflicts with main
    let mut d = ready("feat/behind-a9136-1114", "d", 4);
    d.conflicts_with_main = Some(Clash {
        conflict: true,
        files: vec!["Cargo.toml".into()],
    });
    // an open PR nobody touched for 5 days, nothing at its head
    let mut e = ready("feat/old-043dd-1108", "e", 121);
    e.review = None;
    e.receipt = None;
    e.pr = Some(pr(524, &[]));
    // 可放棄, merged, and plain work in progress are in neither list
    let mut gone = ready("feat/gone-1ced7-1108", "1", 200);
    gone.pr = Some(PrInfo {
        open: false,
        head: Some("1".repeat(40)),
        ..pr(500, &[])
    });
    let mut merged = ready("feat/merged-1ced7-1113", "2", 20);
    merged.unique_not_in_prs = 0;
    let mut wip = ready("feat/wip-1ced7-1114", "3", 1);
    wip.review = None;
    wip.receipt = None;
    let r = report(vec![a, b, c, d, e, gone, merged, wip]);
    let got = due(&r, 72, NOW);
    assert!(got.merge.is_empty(), "{:?}", got.merge);
    let rows: Vec<(String, Vec<&str>)> = got
        .fix
        .iter()
        .map(|i| (i.target.clone(), texts(i)))
        .collect();
    assert_eq!(
        rows,
        [
            ("#524 old".to_owned(), vec!["開著 5 天沒動"]),
            (
                "#664 brand".to_owned(),
                vec!["跟 #758 衝突(crates/x/probe.rs)"]
            ),
            (
                "#757 r4".to_owned(),
                vec!["CI 2 格紅(smoke, fresh-machine)"]
            ),
            ("feat/behind-a9136-1114".to_owned(), vec!["跟 main 衝突"]),
            (
                "feat/mid-7b81e-1114".to_owned(),
                vec!["審查中度 2 / 嚴重 0 未修", "收據不綠(clippy)"]
            ),
        ]
    );
    let text = render(&got, &[], 8);
    assert!(
        text.starts_with("應合未合(0 件):\n應修未修(5 件):\n"),
        "{text}"
    );
    assert!(
        text.contains("- #757 r4 · aaaaaaaa · CI 2 格紅(smoke, fresh-machine)\n"),
        "{text}"
    );
}

#[test]
fn a_sweep_that_is_not_complete_says_so_and_long_lists_are_cut() {
    let mut branches = Vec::new();
    for n in 0..5_u8 {
        let c = char::from(b'a' + n).to_string();
        branches.push(ready(&format!("feat/p{n}-1ced7-1114"), &c, i64::from(n)));
    }
    let mut r = report(branches);
    let got = due(&r, 72, NOW);
    let text = render(&got, &[], 2);
    // the two oldest, then how many more; an empty list still has its title
    assert_eq!(
        text.lines().collect::<Vec<_>>(),
        [
            "應合未合(5 件):",
            "- feat/p4-1ced7-1114 · eeeeeeee · 審過 + 收據齊 · 差:沒開 PR",
            "- feat/p3-1ced7-1114 · dddddddd · 審過 + 收據齊 · 差:沒開 PR",
            "還有 3 件",
            "應修未修(0 件):",
        ]
    );
    // something could not be read: the first line says so; never "0 件" alone
    r.errors = vec![
        "gh: connection reset\u{1b}[0m".into(),
        "PR #7: timeout".into(),
    ];
    let text = render(&got, &r.errors, 2);
    assert!(
        text.starts_with(
            "掃描不完整,以下可能有缺:gh: connection reset[0m(共 2 個)\n應合未合(5 件):\n"
        ),
        "{text}"
    );
    let j = to_json(&got, &r, Some("1ced7"), 2);
    assert_eq!(j["schema"], "aib-slice-due.v1");
    assert_eq!(j["complete"], false);
    assert_eq!(j["seat"], "1ced7");
    assert_eq!(j["merge"]["total"], 5);
    assert_eq!(j["merge"]["items"].as_array().map(Vec::len), Some(2));
    assert_eq!(j["merge"]["items"][0]["lacks"][0]["kind"], "no_pr");
    assert_eq!(j["fix"]["total"], 0);
}

/// One head under two names (the branch and its pushed copy) is one item: the one with the PR.
#[test]
fn a_head_listed_twice_is_one_item() {
    let local = ready("feat/x-1ced7-1114", "a", 2);
    let mut copy = ready("integ-src/x-1ced7", "a", 2);
    copy.pr = Some(pr(755, &["ready-merge"]));
    let got = due(&report(vec![local, copy]), 72, NOW);
    assert_eq!(got.merge.len(), 1, "{:?}", got.merge);
    assert_eq!(got.merge[0].target, "#755 x");
}

fn run(args: &[&str]) -> (i32, String, String) {
    let o = std::process::Command::new(env!("CARGO_BIN_EXE_aib-slice"))
        .args(args)
        .stdin(std::process::Stdio::null())
        .output()
        .expect("run aib-slice");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

#[test]
fn the_command_needs_one_of_seat_and_all_and_never_prints_zero_for_an_unread_repo() {
    for bad in [
        vec!["due", "--no-fetch", "--no-gh"],
        vec!["due", "--seat", "1ced7", "--all"],
        vec!["due", "--all", "--max", "0"],
        vec!["due", "--all", "--gate"],
        vec!["sweep", "--all"],
        vec!["nothing"],
    ] {
        let (rc, out, _) = run(&bad);
        assert_eq!(rc, 64, "{bad:?}");
        assert!(out.is_empty(), "{bad:?} {out}");
    }
    // a folder that is not a repository: rc 2, the warning first, both titles still there
    let d = tempfile::tempdir().expect("tmp");
    let dir = d.path().to_string_lossy().into_owned();
    let (rc, out, err) = run(&[
        "due",
        "--all",
        "--no-gh",
        "--no-fetch",
        "--repo",
        &dir,
        "--pending",
        &dir,
    ]);
    assert_eq!(rc, 2, "{out}{err}");
    let lines: Vec<&str> = out.lines().collect();
    assert!(lines[0].starts_with("掃描不完整,以下可能有缺:"), "{out}");
    assert!(
        out.contains("應合未合(") && out.contains("應修未修("),
        "{out}"
    );
    let (rc, out, _) = run(&[
        "due",
        "--seat",
        "1ced7",
        "--json",
        "--no-gh",
        "--no-fetch",
        "--repo",
        &dir,
        "--pending",
        &dir,
    ]);
    assert_eq!(rc, 2);
    let v: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert_eq!(v["complete"], false);
    assert_eq!(v["seat"], "1ced7");
}
