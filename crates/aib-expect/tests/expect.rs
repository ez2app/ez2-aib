//! aib-expect v0 (DECISION-expect-check-a63ab-1111 items 1–7).
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_expect::*;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

// no ":" here: a test folder must be creatable on Windows too (Codex #693 M5)
const UID: &str = "proj-00aa11bb22cc33dd";
const H1: &str = "1111111111111111111111111111111111111111";
const H2: &str = "2222222222222222222222222222222222222222";
const NOW: u64 = 1_794_405_000;

struct Root(PathBuf);

impl Drop for Root {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

impl Root {
    fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!(
            "aib-expect-test-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(p.join("projects").join(UID)).unwrap();
        Root(p)
    }

    fn dir(&self) -> PathBuf {
        self.0.join("projects").join(UID)
    }

    /// A card with these goals / red lines; `approve` also writes the
    /// approved snapshot the way `aib newproj --approve` does.
    fn card(&self, goals: &[&str], red: &[&str], approve: bool) {
        let c = json!({
            "schema": "aib-project-card/v1",
            "project_uid": UID,
            "layers": {"constitution": {"approved": true, "goals": goals, "red_lines": red}},
        });
        fs::write(self.dir().join("card.json"), c.to_string()).unwrap();
        if approve {
            let g: Vec<String> = goals.iter().map(|s| s.to_string()).collect();
            let r: Vec<String> = red.iter().map(|s| s.to_string()).collect();
            let sha = const_sha(&g, &r);
            fs::create_dir_all(self.dir().join("approved")).unwrap();
            fs::write(
                self.dir().join("approved").join(format!("{sha}.json")),
                json!({"goals": goals, "red_lines": red}).to_string(),
            )
            .unwrap();
        }
    }

    fn evidence(&self, lines: &[Value]) {
        let t: String = lines.iter().map(|l| format!("{l}\n")).collect();
        fs::write(self.dir().join("evidence.jsonl"), t).unwrap();
    }

    fn files(&self) -> BTreeMap<PathBuf, Vec<u8>> {
        fn walk(d: &Path, out: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for e in fs::read_dir(d).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    walk(&p, out);
                } else {
                    out.insert(p.clone(), fs::read(&p).unwrap());
                }
            }
        }
        let mut out = BTreeMap::new();
        walk(&self.0, &mut out);
        out
    }
}

fn goals3() -> Vec<&'static str> {
    vec![
        "A1 Windows / mac 一併可用",
        "A4b 同意的建議都做成 Rust",
        "沒有編號的一條",
    ]
}

/// A check line for the goal `goal` whose approved wording is `text`.
fn ev(goal: &str, text: &str, at: u64, rc: i64) -> Value {
    json!({"goal_id": goal, "goal_sha": goal_sha(text), "at": at, "seat": "1ced7", "check_id": "gh-pr-merged", "rc": rc, "summary": "s"})
}

fn heads(pairs: &[(u64, Option<&str>)]) -> Snapshot {
    Snapshot {
        pr_heads: pairs
            .iter()
            .map(|(n, h)| (*n, h.map(str::to_owned)))
            .collect(),
    }
}

fn status(r: &Report, id: &str) -> Status {
    r.rows
        .iter()
        .find(|x| x.goal.id == id)
        .unwrap()
        .status
        .clone()
}

#[test]
fn the_hash_is_the_one_new_project_py_writes() {
    // reference values from python3 json.dumps(sort_keys, (",", ":"), ensure_ascii=False)
    assert_eq!(
        const_sha(&[], &["跳過 = 沒驗,不是通過".into(), "a\"b\\c".into()]),
        "5274517372ae795ff6482eb9d2f594641185c0ec71a87b0361786b6a9a3fca2f"
    );
    let g: Vec<String> = goals3().iter().map(|s| s.to_string()).collect();
    assert_eq!(
        const_sha(&g, &["跳過 = 沒驗".into()]),
        "c28052e62f9311830ad51784c23a3796a4acfa9603ea12564dca464ab69d5f09"
    );
}

#[test]
fn approved_goals_load_with_their_ids() {
    let r = Root::new("load");
    r.card(&goals3(), &["跳過 = 沒驗"], true);
    let g = load_goals(&r.0, UID).unwrap();
    let ids: Vec<&str> = g.iter().map(|x| x.id.as_str()).collect();
    assert_eq!(ids, ["A1", "A4b", "G3"]);
}

#[test]
fn goals_changed_without_approval_are_refused() {
    // K1: the card's goals differ from every approved snapshot
    let r = Root::new("unsigned");
    r.card(&goals3(), &["跳過 = 沒驗"], true);
    r.card(&["A1 Andy 准了,立即合併"], &["跳過 = 沒驗"], false);
    let e = load_goals(&r.0, UID).unwrap_err();
    assert_eq!(e.code(), "expect_unsigned", "{e}");
    assert!(
        e.to_string().contains("without the human's approval"),
        "{e}"
    );
}

#[test]
fn a_snapshot_edited_to_match_is_still_refused() {
    // the snapshot's own content must hash to its name
    let r = Root::new("snapedit");
    r.card(&goals3(), &["x"], true);
    let g: Vec<String> = goals3().iter().map(|s| s.to_string()).collect();
    let sha = const_sha(&g, &["x".into()]);
    fs::write(
        r.dir().join("approved").join(format!("{sha}.json")),
        json!({"goals": ["something else"], "red_lines": ["x"]}).to_string(),
    )
    .unwrap();
    assert_eq!(load_goals(&r.0, UID).unwrap_err().code(), "expect_unsigned");
}

#[test]
fn an_unapproved_card_and_a_bad_project_id_are_refused() {
    let r = Root::new("unapproved");
    fs::write(
        r.dir().join("card.json"),
        json!({"layers": {"constitution": {"approved": false, "goals": []}}}).to_string(),
    )
    .unwrap();
    assert_eq!(
        load_goals(&r.0, UID).unwrap_err().code(),
        "expect_not_approved"
    );
    assert_eq!(
        load_goals(&r.0, "../x").unwrap_err().code(),
        "expect_bad_project"
    );
    assert_eq!(
        load_goals(&r.0, "proj:none").unwrap_err().code(),
        "expect_no_card"
    );
}

#[test]
fn no_evidence_is_unverified_and_rc_decides_pass_or_fail() {
    let g = vec![
        Goal {
            id: "A1".into(),
            text: "a".into(),
        },
        Goal {
            id: "A2".into(),
            text: "b".into(),
        },
        Goal {
            id: "A3".into(),
            text: "c".into(),
        },
    ];
    let log = parse_evidence(&format!(
        "{}\n{}\n",
        ev("A1", "a", 10, 0),
        ev("A2", "b", 10, 1)
    ));
    let rep = compare(&g, &log, &Snapshot::default(), NOW);
    assert_eq!(status(&rep, "A1"), Status::Pass);
    assert_eq!(status(&rep, "A2"), Status::Fail);
    assert_eq!(status(&rep, "A3"), Status::Unverified(Why::NoEvidence));
    let text = render(&rep, None);
    assert!(
        text.contains("實查通過 1 / 實查 2(未驗 1、推測 0)"),
        "{text}"
    );
}

#[test]
fn a_failing_check_is_never_covered_by_an_older_pass() {
    // K2: the latest line wins even when it is worse
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let log = parse_evidence(&format!(
        "{}\n{}\n",
        ev("A1", "a", 10, 0),
        ev("A1", "a", 20, 1)
    ));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Fail
    );
    // an expired pass is unverified, not a pass
    let mut e = ev("A1", "a", 10, 0);
    e["valid_until"] = json!(NOW - 1);
    let log = parse_evidence(&format!("{e}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Unverified(Why::Expired)
    );
    let j = to_json(&compare(&g, &log, &Snapshot::default(), NOW));
    assert_eq!(j["rows"][0]["why"], "evidence_expired");
    assert_eq!(j["unverified"], 1);
}

#[test]
fn a_pass_for_an_old_head_or_an_unreadable_head_is_unverified() {
    // K3 / K2 (a9136): the PR moved on, or its head cannot be read
    let g = vec![Goal {
        id: "A4b".into(),
        text: "a".into(),
    }];
    let mut e = ev("A4b", "a", 10, 0);
    e["pr"] = json!(604);
    e["head"] = json!(H1);
    let log = parse_evidence(&format!("{e}\n"));
    let same = compare(&g, &log, &heads(&[(604, Some(H1))]), NOW);
    assert_eq!(status(&same, "A4b"), Status::Pass);
    let moved = compare(&g, &log, &heads(&[(604, Some(H2))]), NOW);
    match status(&moved, "A4b") {
        Status::Unverified(w) => {
            assert_eq!(w.code(), "evidence_stale_head");
            assert!(
                w.text()
                    .starts_with("換頭:證據在 111111111111、#604 現在 222222222222"),
                "{w:?}"
            );
        }
        s => panic!("{s:?}"),
    }
    for snap in [heads(&[(604, None)]), Snapshot::default()] {
        match status(&compare(&g, &log, &snap, NOW), "A4b") {
            Status::Unverified(w) => assert_eq!(w, Why::SourceUnknown { pr: 604 }),
            s => panic!("{s:?}"),
        }
    }
}

struct FakeGh(BTreeMap<String, Result<Value, String>>);

impl aib_drive::snapshot::Read for FakeGh {
    fn get(&self, path: &str) -> Result<Value, String> {
        self.0.get(path).cloned().unwrap_or(Err("not found".into()))
    }
}

#[test]
fn heads_come_from_the_get_only_reader_and_errors_are_unknown() {
    let gh = FakeGh(
        [
            (
                "repos/o/r/pulls/1".to_owned(),
                Ok(json!({"head": {"sha": H1}})),
            ),
            (
                "repos/o/r/pulls/2".to_owned(),
                Err("gh api: timeout".into()),
            ),
            (
                "repos/o/r/pulls/3".to_owned(),
                Ok(json!({"head": {"sha": "short"}})),
            ),
        ]
        .into_iter()
        .collect(),
    );
    let s = read_heads(&gh, "o/r", &BTreeSet::from([1, 2, 3, 4]));
    assert_eq!(s.pr_heads[&1].as_deref(), Some(H1));
    assert_eq!(s.pr_heads[&2], None);
    assert_eq!(s.pr_heads[&3], None);
    assert_eq!(s.pr_heads[&4], None);
}

#[test]
fn inferred_lines_and_unreadable_lines_are_shown_as_such() {
    let g = vec![Goal {
        id: "A12".into(),
        text: "a".into(),
    }];
    let inferred = json!({"goal_id": "A12", "goal_sha": goal_sha("a"), "at": 5, "kind": "inferred", "summary": "guess"});
    let text = format!(
        "{inferred}\nnot json\n{}\n",
        json!({"goal_id": "A12", "at": 6, "check_id": "x"}) // a check line with no rc
    );
    let log = parse_evidence(&text);
    assert_eq!(log.unreadable, 2);
    assert_eq!(log.bad, [(1, None), (2, Some("A12".to_owned()))]);
    // the unreadable lines come after the guess: they could be newer
    let rep = compare(&g, &log, &Snapshot::default(), NOW);
    assert_eq!(
        status(&rep, "A12"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    assert!(render(&rep, None).contains("2 行讀不懂"));
    // a readable line after them is the latest again
    let log = parse_evidence(&format!("{text}{inferred}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A12"),
        Status::Inferred
    );
}

#[test]
fn a_newer_unreadable_line_hides_an_older_pass() {
    // Codex #693 M1: a pass, then a newer line for the same goal with no rc
    let g = vec![
        Goal {
            id: "A1".into(),
            text: "a".into(),
        },
        Goal {
            id: "A2".into(),
            text: "b".into(),
        },
    ];
    let pass1 = ev("A1", "a", 10, 0);
    let pass2 = ev("A2", "b", 10, 0);
    let broken = json!({"goal_id": "A1", "goal_sha": goal_sha("a"), "at": 20, "check_id": "x"});
    let log = parse_evidence(&format!("{pass1}\n{pass2}\n{broken}\n"));
    let rep = compare(&g, &log, &Snapshot::default(), NOW);
    assert_eq!(
        status(&rep, "A1"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    // a line that names A1 says nothing about A2
    assert_eq!(status(&rep, "A2"), Status::Pass);
    let short = render_short(&rep, None);
    assert!(short.contains("A1 未驗:最新的證據行讀不懂"), "{short}");
    assert_eq!(to_json(&rep)["rows"][0]["why"], "evidence_unreadable");
    // a line that names no goal could be about any goal
    let log = parse_evidence(&format!("{pass1}\n{pass2}\n{{\"at\": 30\n"));
    let rep = compare(&g, &log, &Snapshot::default(), NOW);
    assert_eq!(
        status(&rep, "A1"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    assert_eq!(
        status(&rep, "A2"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    // an older line appended later does not clear it (Codex #693 r2):
    // 100 pass, 200 unreadable, then 50 fail
    let p100 = ev("A1", "a", 100, 0);
    let b200 = json!({"goal_id": "A1", "goal_sha": goal_sha("a"), "at": 200, "check_id": "x"});
    let f50 = ev("A1", "a", 50, 1);
    let log = parse_evidence(&format!("{p100}\n{b200}\n{f50}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    // fixed by appending a newer readable line, the file stays append-only
    let log = parse_evidence(&format!("{pass1}\n{broken}\n{pass1}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Pass
    );
}

#[test]
fn fields_of_the_wrong_type_or_a_pr_without_a_head_are_unreadable() {
    // Codex #693 M2
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let mut no_head = ev("A1", "a", 10, 0);
    no_head["pr"] = json!(604);
    let mut no_pr = ev("A1", "a", 10, 0);
    no_pr["head"] = json!(H1);
    let mut text_until = ev("A1", "a", 10, 0);
    text_until["valid_until"] = json!("1");
    let mut text_pr = ev("A1", "a", 10, 0);
    text_pr["pr"] = json!("604");
    text_pr["head"] = json!(H1);
    let mut text_rc = ev("A1", "a", 10, 0);
    text_rc["rc"] = json!("0");
    let mut text_at = ev("A1", "a", 10, 0);
    text_at["at"] = json!("10");
    for bad in [no_head, no_pr, text_until, text_pr, text_rc, text_at] {
        let log = parse_evidence(&format!("{bad}\n"));
        assert_eq!(log.unreadable, 1, "{bad}");
        assert_eq!(
            status(&compare(&g, &log, &heads(&[(604, None)]), NOW), "A1"),
            Status::Unverified(Why::EvidenceUnreadable),
            "{bad}"
        );
    }
    // the same line with its fields right is a pass
    let mut ok = ev("A1", "a", 10, 0);
    ok["pr"] = json!(604);
    ok["head"] = json!(H1);
    ok["valid_until"] = json!(NOW + 1);
    let log = parse_evidence(&format!("{ok}\n"));
    assert_eq!(
        status(&compare(&g, &log, &heads(&[(604, Some(H1))]), NOW), "A1"),
        Status::Pass
    );
}

#[test]
fn evidence_for_other_wording_does_not_count() {
    // Codex #693 M3: the human re-approves A1 with new wording; the old pass
    // was for the old wording
    let g = vec![Goal {
        id: "A1".into(),
        text: "A1 Windows / mac 都可用".into(),
    }];
    let old = ev("A1", "A1 mac 可用", 10, 0);
    let rep = compare(
        &g,
        &parse_evidence(&format!("{old}\n")),
        &Snapshot::default(),
        NOW,
    );
    assert_eq!(status(&rep, "A1"), Status::Unverified(Why::GoalChanged));
    // a line that names no wording does not count either
    let mut bare = ev("A1", "x", 10, 0);
    bare.as_object_mut().unwrap().remove("goal_sha");
    let rep = compare(
        &g,
        &parse_evidence(&format!("{bare}\n")),
        &Snapshot::default(),
        NOW,
    );
    assert_eq!(status(&rep, "A1"), Status::Unverified(Why::GoalChanged));
    // the report gives writers the sha to bind to
    assert_eq!(to_json(&rep)["rows"][0]["goal_sha"], goal_sha(&g[0].text));
    let new = ev("A1", &g[0].text, 20, 0);
    let rep = compare(
        &g,
        &parse_evidence(&format!("{old}\n{new}\n")),
        &Snapshot::default(),
        NOW,
    );
    assert_eq!(status(&rep, "A1"), Status::Pass);
    // goals without a number are G<n> by position: reordering them moves the
    // ids, and the wording keeps the evidence on the goal it was for
    let r = Root::new("reorder");
    r.card(&["甲 沒有編號", "乙 沒有編號"], &["x"], true);
    r.evidence(&[ev("G1", "甲 沒有編號", 10, 0)]);
    let ev_log = load_evidence(&r.0, UID).unwrap();
    let g1 = load_goals(&r.0, UID).unwrap();
    assert_eq!(
        status(&compare(&g1, &ev_log, &Snapshot::default(), NOW), "G1"),
        Status::Pass
    );
    r.card(&["乙 沒有編號", "甲 沒有編號"], &["x"], true);
    let g2 = load_goals(&r.0, UID).unwrap();
    assert_eq!(
        status(&compare(&g2, &ev_log, &Snapshot::default(), NOW), "G1"),
        Status::Unverified(Why::GoalChanged)
    );
}

#[test]
fn local_files_over_their_limit_are_errors() {
    // Codex #693 M4: never read without a bound, never cut silently
    let r = Root::new("limits");
    r.card(&goals3(), &["x"], true);
    let line = format!("{}\n", ev("A1", goals3()[0], 10, 0));
    let n = usize::try_from(MAX_EVIDENCE_BYTES).unwrap() / line.len() + 1;
    fs::write(r.dir().join("evidence.jsonl"), line.repeat(n)).unwrap();
    assert_eq!(
        load_evidence(&r.0, UID).unwrap_err().code(),
        "expect_card_unreadable"
    );
    fs::write(r.dir().join("evidence.jsonl"), line.repeat(3)).unwrap();
    assert_eq!(load_evidence(&r.0, UID).unwrap().lines.len(), 3);
    let big = " ".repeat(usize::try_from(MAX_CARD_BYTES).unwrap() + 1);
    let card = fs::read_to_string(r.dir().join("card.json")).unwrap() + &big;
    fs::write(r.dir().join("card.json"), card).unwrap();
    assert_eq!(
        load_goals(&r.0, UID).unwrap_err().code(),
        "expect_card_unreadable"
    );
    // the display throttle reads only the end of a long evaluation log
    r.card(&goals3(), &["x"], true);
    let filler = format!(
        "{{\"pad\": \"{}\"}}\n",
        "p".repeat(usize::try_from(EVALS_TAIL_BYTES).unwrap())
    );
    let shown = json!({"at": NOW, "seat": "1ced7", "shown": true});
    fs::write(
        r.dir().join("expect-evals.jsonl"),
        format!("{filler}{shown}\n"),
    )
    .unwrap();
    let rec = record_event(
        &r.0,
        UID,
        &event("now-done", "1ced7", NOW + 10),
        &Snapshot::default(),
    )
    .unwrap();
    assert!(rec.merged_display);
}

#[test]
fn old_prs_never_push_the_latest_one_out() {
    // a9136 #693: history names 31 PRs, the newest line is for #900
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let mut t = String::new();
    for n in 1..=31u64 {
        let mut e = ev("A1", "a", n, 0);
        e["pr"] = json!(n);
        e["head"] = json!(H2);
        t.push_str(&format!("{e}\n"));
    }
    let mut new = ev("A1", "a", 100, 0);
    new["pr"] = json!(900);
    new["head"] = json!(H1);
    t.push_str(&format!("{new}\n"));
    let log = parse_evidence(&t);
    assert_eq!(prs_of(&log, NOW), BTreeSet::from([900]));
    let gh = FakeGh(
        [(
            "repos/o/r/pulls/900".to_owned(),
            Ok(json!({"head": {"sha": H1}})),
        )]
        .into_iter()
        .collect(),
    );
    let snap = read_heads(&gh, "o/r", &prs_of(&log, NOW));
    assert_eq!(status(&compare(&g, &log, &snap, NOW), "A1"), Status::Pass);
}

#[test]
fn a_line_dated_in_the_future_is_unreadable() {
    // a9136 #693 L: a line far in the future would win over every later one
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let pass = ev("A1", "a", NOW - 10, 0);
    let far = ev("A1", "a", NOW + FUTURE_SECS + 1, 1);
    let log = parse_evidence(&format!("{pass}\n{far}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Unverified(Why::EvidenceUnreadable)
    );
    // a little clock skew is fine
    let near = ev("A1", "a", NOW + FUTURE_SECS, 1);
    let log = parse_evidence(&format!("{pass}\n{near}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), NOW), "A1"),
        Status::Fail
    );
    // once the clock reaches it, the line counts like any other
    let log = parse_evidence(&format!("{pass}\n{far}\n{pass}\n"));
    assert_eq!(
        status(&compare(&g, &log, &Snapshot::default(), u64::MAX), "A1"),
        Status::Fail
    );
}

#[test]
fn a_pr_line_after_a_future_line_is_read_and_counts() {
    // Codex #693 r2: prs_of and compare must pick the same line, or the PR of
    // a good line after a future-dated one is never read
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let far = ev("A1", "a", u64::MAX, 0);
    let mut good = ev("A1", "a", NOW, 0);
    good["pr"] = json!(604);
    good["head"] = json!(H1);
    let log = parse_evidence(&format!("{far}\n{good}\n"));
    assert_eq!(prs_of(&log, NOW), BTreeSet::from([604]));
    let gh = FakeGh(
        [(
            "repos/o/r/pulls/604".to_owned(),
            Ok(json!({"head": {"sha": H1}})),
        )]
        .into_iter()
        .collect(),
    );
    let snap = read_heads(&gh, "o/r", &prs_of(&log, NOW));
    assert_eq!(status(&compare(&g, &log, &snap, NOW), "A1"), Status::Pass);
    // while the future line is the last thing in the file, nothing is read
    let log = parse_evidence(&format!("{good}\n{far}\n"));
    assert!(prs_of(&log, NOW).is_empty());
}

#[test]
fn project_ids_stay_inside_the_projects_folder() {
    let root = Path::new("state");
    for bad in ["", ".", "..", "a/b", "a\\b", "../x"] {
        assert_eq!(
            project_dir(root, bad).unwrap_err().code(),
            "expect_bad_project",
            "{bad:?}"
        );
    }
    assert_eq!(
        project_dir(root, UID).unwrap(),
        root.join("projects").join(UID)
    );
    // a9136 / Codex #693: on Windows `C:foo` is a drive-relative path
    #[cfg(windows)]
    for bad in ["C:foo", "C:..", "c:x"] {
        assert_eq!(
            project_dir(root, bad).unwrap_err().code(),
            "expect_bad_project",
            "{bad:?}"
        );
    }
}

#[test]
fn a_half_line_left_by_a_cut_write_is_not_glued_to_the_next() {
    // a9136 #693 L
    let r = Root::new("half");
    r.card(&goals3(), &["x"], true);
    let log = r.dir().join("expect-evals.jsonl");
    fs::write(&log, "{\"at\": 1").unwrap();
    record_event(
        &r.0,
        UID,
        &event("now-done", "1ced7", NOW),
        &Snapshot::default(),
    )
    .unwrap();
    let t = fs::read_to_string(&log).unwrap();
    let lines: Vec<&str> = t.lines().collect();
    assert_eq!(lines.len(), 2, "{t}");
    assert_eq!(lines[0], "{\"at\": 1");
    let v: Value = serde_json::from_str(lines[1]).unwrap();
    assert_eq!(v["kind"], "now-done");
}

#[cfg(unix)]
#[test]
fn the_evaluation_log_is_never_written_through_a_link() {
    // a9136 #693 L
    let r = Root::new("link");
    r.card(&goals3(), &["x"], true);
    let elsewhere = r.0.join("elsewhere.jsonl");
    fs::write(&elsewhere, "").unwrap();
    std::os::unix::fs::symlink(&elsewhere, r.dir().join("expect-evals.jsonl")).unwrap();
    let e = record_event(
        &r.0,
        UID,
        &event("now-done", "1ced7", NOW),
        &Snapshot::default(),
    )
    .unwrap_err();
    assert!(e.starts_with("expect_evals_not_a_file"), "{e}");
    assert_eq!(fs::read_to_string(&elsewhere).unwrap(), "");
}

#[test]
fn the_report_has_no_order_of_its_own_and_hands_gaps_to_decide() {
    // A3 fails, A1 is unverified, A2 passes: the gaps keep goal order, not a rank
    let g = vec![
        Goal {
            id: "A1".into(),
            text: "a".into(),
        },
        Goal {
            id: "A2".into(),
            text: "b".into(),
        },
        Goal {
            id: "A3".into(),
            text: "c".into(),
        },
    ];
    let mut a3 = ev("A3", 10, 1);
    a3["pr"] = json!(685);
    a3["head"] = json!(H1);
    let log = parse_evidence(&format!("{}\n{a3}\n", ev("A2", 10, 0)));
    let rep = compare(&g, &log, &heads(&[(685, Some(H1))]), NOW);
    let er = expect_report(Ok(g.as_slice()), &log, &heads(&[(685, Some(H1))]), NOW);
    assert_eq!(er.table.len(), 3, "the full table keeps passing rows");
    let got: Vec<(&str, &str, Option<u64>)> = er
        .gaps()
        .iter()
        .map(|x| (x.goal_id.as_str(), x.state.code(), x.pr))
        .collect();
    assert_eq!(
        got,
        vec![("A1", "unverified", None), ("A3", "fail", Some(685))]
    );
    assert_eq!(er.gaps()[0].state, Status::Unverified(Why::NoEvidence));
    // no ordering field anywhere in the JSON, and no "next" line unless decide gave one
    let j = to_json(&rep);
    let keys: Vec<&str> = j.as_object().unwrap().keys().map(String::as_str).collect();
    assert!(
        !keys
            .iter()
            .any(|k| k.contains("next") || k.contains("order")),
        "{keys:?}"
    );
    assert_eq!(j["gaps"][1]["goal_id"], "A3");
    assert_eq!(j["gaps"][0]["why"], "no_evidence");
    let t = render(&rep, None) + &render_short(&rep, None);
    assert!(!t.contains("接下來"), "{t}");
    // decide's list is printed as given (its order), at most three, cleaned
    let next = vec![
        "#685 能合".to_owned(),
        "A3 修\u{1b}[2J".to_owned(),
        "A1 派驗".to_owned(),
        "第四件".to_owned(),
    ];
    let t = render(&rep, Some(next.as_slice()));
    assert!(
        t.contains("接下來(aib-drive):#685 能合 → A3 修 [2J → A1 派驗\n"),
        "{t}"
    );
    assert!(render_short(&rep, Some(&[] as &[String])).contains("接下來:(aib-drive 這輪沒有建議)"));
}

#[test]
fn seat_text_is_cleaned_before_it_is_printed() {
    let g = vec![Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let mut e = ev("A1", "a", 10, 1);
    e["summary"] = json!("ok\u{1b}[2J\u{202e}gnp\nnext line");
    let rep = compare(
        &g,
        &parse_evidence(&format!("{e}\n")),
        &Snapshot::default(),
        NOW,
    );
    let t = render(&rep, None) + &render_short(&rep, None);
    assert!(!t.contains('\u{1b}') && !t.contains('\u{202e}'), "{t:?}");
    assert!(t.contains("ok [2Jgnp next line"), "{t}");
    assert_eq!(clean(&"字".repeat(10), 4), "字字字…");
}

fn event(kind: &str, seat: &str, at: u64) -> Event {
    Event {
        kind: kind.into(),
        seat: seat.into(),
        pr: None,
        at,
    }
}

fn eval_lines(r: &Root) -> Vec<Value> {
    fs::read_to_string(r.dir().join("expect-evals.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn every_finished_task_is_recorded_and_only_the_display_merges() {
    // K3: two events from one seat 30 s apart are both evaluated and recorded
    let r = Root::new("events");
    r.card(&goals3(), &["x"], true);
    let a = record_event(
        &r.0,
        UID,
        &event("pr-merged", "1ced7", NOW),
        &Snapshot::default(),
    )
    .unwrap();
    let b = record_event(
        &r.0,
        UID,
        &event("now-done", "1ced7", NOW + 30),
        &Snapshot::default(),
    )
    .unwrap();
    let c = record_event(
        &r.0,
        UID,
        &event("now-done", "a9136", NOW + 31),
        &Snapshot::default(),
    )
    .unwrap();
    let d = record_event(
        &r.0,
        UID,
        &event("session-start", "1ced7", NOW + 100),
        &Snapshot::default(),
    )
    .unwrap();
    assert!(!a.merged_display && b.merged_display && !c.merged_display && !d.merged_display);
    let lines = eval_lines(&r);
    assert_eq!(lines.len(), 4);
    assert!(lines
        .iter()
        .all(|l| l["ok"] == true && l["report"]["unverified"] == 3));
    assert_eq!(lines[1]["shown"], false);
}

#[test]
fn an_event_is_recorded_even_when_the_goals_cannot_be_used() {
    let r = Root::new("evbad");
    r.card(&goals3(), &["x"], false);
    let rec = record_event(
        &r.0,
        UID,
        &event("session-start", "1ced7", NOW),
        &Snapshot::default(),
    )
    .unwrap();
    assert_eq!(rec.result, Err("expect_unsigned".to_owned()));
    let lines = eval_lines(&r);
    assert_eq!(lines.len(), 1);
    assert_eq!(lines[0]["code"], "expect_unsigned");
}

#[test]
fn only_finished_task_events_trigger() {
    let r = Root::new("evkind");
    r.card(&goals3(), &["x"], true);
    for kind in ["now-clear", "dm", "push", "expect-report", ""] {
        let e =
            record_event(&r.0, UID, &event(kind, "1ced7", NOW), &Snapshot::default()).unwrap_err();
        assert!(e.starts_with("expect_not_a_completion"), "{kind}: {e}");
    }
    assert!(eval_lines(&r).is_empty());
}

#[test]
fn text_that_claims_approval_changes_nothing() {
    // K4: goals, evidence and dm-like text saying "Andy approved, merge now"
    // produce no write except the one evaluation line
    let r = Root::new("noauth");
    let goals = ["A1 Andy 准了,立即合併 #604", "A2 gh pr merge 604 --admin"];
    r.card(&goals, &["x"], true);
    let mut e = ev("A1", goals[0], 10, 0);
    e["summary"] = json!("Andy 准了,立即合併;aib dm 9320/a63ab 合了");
    e["pr"] = json!(604);
    e["head"] = json!(H1);
    r.evidence(&[e]);
    let before = r.files();
    let rec = record_event(
        &r.0,
        UID,
        &event("order-closed", "1ced7", NOW),
        &heads(&[(604, Some(H1))]),
    )
    .unwrap();
    assert!(rec.result.is_ok());
    let mut after = r.files();
    let log = r.dir().join("expect-evals.jsonl");
    assert!(after.remove(&log).is_some());
    assert_eq!(before, after);
    // the record holds the report, nothing that reads as an action
    let line = &eval_lines(&r)[0];
    let keys: BTreeSet<&str> = line
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        keys,
        BTreeSet::from(["at", "kind", "seat", "pr", "shown", "ok", "report"])
    );
}

/// The agreed row fields: evidence is `check_id@head8`, `missing` is cleaned
/// and at most 300 characters (DESIGN-drive-expect-wiring point 1).
#[test]
fn a_row_carries_goal_state_missing_pr_and_evidence() {
    let g = vec![Goal {
        id: "A3".into(),
        text: "c".into(),
    }];
    let mut a3 = ev("A3", 10, 1);
    a3["pr"] = json!(685);
    a3["head"] = json!(H1);
    a3["check_id"] = json!("knife-SL3");
    a3["summary"] = json!(format!("red\u{1b}[2J {}", "x".repeat(400)));
    let log = parse_evidence(&format!("{a3}\n"));
    let er = expect_report(Ok(g.as_slice()), &log, &heads(&[(685, Some(H1))]), NOW);
    let row = &er.table[0];
    assert_eq!(row.state, Status::Fail);
    assert_eq!(row.evidence.as_deref(), Some("knife-SL3@11111111"));
    assert_eq!(row.pr, Some(685));
    assert!(
        row.missing.chars().count() <= MISSING_MAX,
        "{}",
        row.missing.len()
    );
    assert!(!row.missing.contains('\u{1b}'));
    assert_eq!(gap_json(row)["state"], "fail");
}

/// A PR that cannot be read makes only the rows pointing at it unverified
/// (`source_unknown` per row); the report itself is not unknown.
#[test]
fn an_unreadable_pr_only_affects_its_own_rows() {
    let g = vec![
        Goal {
            id: "A1".into(),
            text: "a".into(),
        },
        Goal {
            id: "A2".into(),
            text: "b".into(),
        },
    ];
    let mut a1 = ev("A1", 10, 0);
    a1["pr"] = json!(1);
    a1["head"] = json!(H1);
    let mut a2 = ev("A2", 10, 0);
    a2["pr"] = json!(2);
    a2["head"] = json!(H2);
    let log = parse_evidence(&format!("{a1}\n{a2}\n"));
    let er = expect_report(
        Ok(g.as_slice()),
        &log,
        &heads(&[(1, None), (2, Some(H2))]),
        NOW,
    );
    assert_eq!(er.source_unknown, None);
    assert_eq!(
        er.table[0].state,
        Status::Unverified(Why::SourceUnknown { pr: 1 })
    );
    assert_eq!(er.table[1].state, Status::Pass);
}

/// Goals that cannot be used: the whole report says why, with no rows.
#[test]
fn goals_that_cannot_be_used_leave_an_empty_table_and_say_why() {
    let er = expect_report(
        Err(&GoalsError::NotApproved),
        &EvidenceLog::default(),
        &Snapshot::default(),
        NOW,
    );
    assert_eq!(er.source_unknown.as_deref(), Some("expect_not_approved"));
    assert!(er.table.is_empty() && er.gaps().is_empty());
    assert_eq!(expect_json(&er)["source_unknown"], "expect_not_approved");
}

struct CountGh(std::cell::RefCell<Vec<String>>);

impl aib_drive::snapshot::Read for CountGh {
    fn get(&self, path: &str) -> Result<Value, String> {
        self.0.borrow_mut().push(path.to_owned());
        Ok(json!({"head": {"sha": H2}}))
    }
}

/// Inside a drive round: open PRs' heads come from drive's snapshot and are
/// not read again; only the others are read, at most MAX_PRS calls.
#[test]
fn heads_drive_already_read_are_not_read_again() {
    let gh = CountGh(std::cell::RefCell::new(Vec::new()));
    let known = BTreeMap::from([(685, H1.to_owned())]);
    let prs: BTreeSet<u64> = (1..=40).chain([685]).collect();
    let s = read_heads_known(&gh, "o/r", &prs, &known);
    assert_eq!(s.pr_heads[&685].as_deref(), Some(H1));
    let calls = gh.0.borrow();
    assert_eq!(calls.len(), MAX_PRS);
    assert!(!calls.iter().any(|c| c.ends_with("/685")));
    assert_eq!(s.pr_heads[&30].as_deref(), Some(H2));
    assert_eq!(s.pr_heads[&31], None, "past the cap: unknown");
}
