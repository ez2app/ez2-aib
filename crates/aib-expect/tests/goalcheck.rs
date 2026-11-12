//! `aib-expect check-goal` (RULES-seats-aib2 §17, DECISION-goalcheck-gate-a63ab-1112).
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_expect::goalcheck::*;
use aib_expect::*;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

const NOW: u64 = 1_794_405_000;

struct Tmp(PathBuf);

impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

impl Tmp {
    fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!(
            "aib-goalcheck-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&p).unwrap();
        Tmp(p)
    }
}

fn goals() -> Vec<Goal> {
    ["A1", "A2", "E6"]
        .iter()
        .map(|id| Goal {
            id: (*id).into(),
            text: format!("{id} text"),
        })
        .collect()
}

fn line(goal: &str, pr: Option<u64>) -> Value {
    let mut v = json!({"goal_id": goal, "goal_sha": goal_sha(&format!("{goal} text")),
        "at": NOW - 10, "seat": "1ced7", "check_id": "c", "rc": 0, "summary": "s"});
    if let Some(n) = pr {
        v["pr"] = json!(n);
        v["head"] = json!("1111111111111111111111111111111111111111");
    }
    v
}

fn log(lines: &[Value]) -> EvidenceLog {
    parse_evidence(&lines.iter().map(|l| format!("{l}\n")).collect::<String>())
}

fn stage(goal: Option<&str>, why: Option<&str>) -> Stage {
    Stage {
        item: "做 #693".into(),
        started_at: Some(100),
        goal: goal.map(str::to_owned),
        goal_why: why.map(str::to_owned),
        idle: false,
    }
}

fn judge(ev: &EvidenceLog, st: Option<&Stage>, h: &[HistoryRow]) -> Verdict {
    let rep = compare(&goals(), ev, &Snapshot::default(), NOW);
    check(ev, &rep, st, h)
}

#[test]
fn no_record_or_no_goal_is_missing_and_may_block() {
    let ev = log(&[]);
    let v = judge(&ev, None, &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_missing"], 4));
    let v = judge(&ev, Some(&stage(None, None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_missing"], 4));
}

#[test]
fn a_chore_needs_its_reason() {
    let ev = log(&[]);
    let v = judge(&ev, Some(&stage(Some("none"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_missing"], 4));
    let v = judge(&ev, Some(&stage(Some("none"), Some("  "))), &[]);
    assert_eq!(v.rc(), 4);
    let v = judge(&ev, Some(&stage(Some("NONE"), Some("回 dm"))), &[]);
    assert_eq!(
        (v.codes.clone(), v.rc(), v.goal),
        (vec![], 0, Some("none".into()))
    );
}

#[test]
fn a_goal_not_on_the_card_is_unknown_and_may_block() {
    let ev = log(&[line("A1", None)]);
    for g in ["A9", "B", "A4bc", "A\u{0668}"] {
        let v = judge(&ev, Some(&stage(Some(g), None)), &[]);
        assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_unknown"], 4), "{g}");
    }
}

#[test]
fn a_goal_taken_off_the_card_is_only_a_reminder() {
    let ev = log(&[line("A7", None)]);
    let v = judge(&ev, Some(&stage(Some("a07"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_retired"], 5));
}

#[test]
fn a_live_goal_is_ok_and_a_passed_one_is_a_reminder() {
    let ev = log(&[line("A1", None)]);
    let v = judge(&ev, Some(&stage(Some("a2"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec![], 0));
    assert_eq!(v.goal.as_deref(), Some("A2"));
    assert_eq!(v.status, Some(Status::Unverified(Why::NoEvidence).label()));
    let v = judge(&ev, Some(&stage(Some("A01"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_done"], 5));
    assert_eq!(v.status, Some(Status::Pass.label()));
}

#[test]
fn a_goal_added_after_the_stage_started_is_late() {
    let ev = log(&[]);
    let row = |g: Option<&str>| HistoryRow {
        started_at: Some(100),
        goal: g.map(str::to_owned),
    };
    let late = [row(None), row(Some("A2"))];
    let v = judge(&ev, Some(&stage(Some("A2"), None)), &late);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_late"], 5));
    // a chore named late is late too
    let v = judge(&ev, Some(&stage(Some("none"), Some("回 dm"))), &late);
    assert_eq!(v.codes, vec!["goal_late"]);
    // named from the start: fine, and an earlier stage does not count
    let older = HistoryRow {
        started_at: Some(50),
        goal: None,
    };
    let v = judge(
        &ev,
        Some(&stage(Some("A2"), None)),
        &[older, row(Some("A2"))],
    );
    assert_eq!(v.rc(), 0);
}

#[test]
fn a_pr_bound_to_other_goals_only_is_a_mismatch() {
    let ev = log(&[line("A1", Some(693))]);
    let v = judge(&ev, Some(&stage(Some("A2"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec!["pr_goal_mismatch"], 5));
    assert_eq!(v.prs, vec![693]);
    // bound to this goal too: fine; a PR no line names: fine
    let ev = log(&[line("A1", Some(693)), line("A2", Some(693))]);
    assert_eq!(judge(&ev, Some(&stage(Some("A2"), None)), &[]).rc(), 0);
    let ev = log(&[line("A1", Some(700))]);
    assert_eq!(judge(&ev, Some(&stage(Some("A2"), None)), &[]).rc(), 0);
}

#[test]
fn goal_forms_and_pr_numbers() {
    for (raw, want) in [
        ("A8", Some("A8")),
        ("a08", Some("A8")),
        ("A4B", Some("A4b")),
        (" e6 ", Some("E6")),
        ("None", Some("none")),
        ("A", None),
        ("8", None),
        ("A4b1", None),
    ] {
        assert_eq!(norm_goal(raw).as_deref(), want, "{raw}");
    }
    assert_eq!(prs_named("修 #693、#710 和 #693;# 7 不算"), vec![693, 710]);
}

#[test]
fn the_record_is_read_from_the_now_folder() {
    let t = Tmp::new("read");
    assert_eq!(read_seat(&t.0, "1ced7").unwrap(), (None, vec![]));
    fs::write(
        t.0.join("1ced7.json"),
        r#"{"item":"做 #693","started_at":100,"goal":"none","goal_why":"回 dm","goal_at":100}"#,
    )
    .unwrap();
    fs::write(
        t.0.join("1ced7.history.jsonl"),
        "{\"item\":\"x\",\"started_at\":100}\nnot json\n{\"started_at\":100,\"goal\":\"none\"}\n",
    )
    .unwrap();
    let (st, h) = read_seat(&t.0, "1ced7").unwrap();
    assert_eq!(st, Some(stage(Some("none"), Some("回 dm"))));
    assert_eq!(h.len(), 2, "the unreadable line is skipped");
    fs::write(t.0.join("1ced7.json"), "[").unwrap();
    assert!(read_seat(&t.0, "1ced7").is_err());
    for bad in ["", "../x", "A1", "a/b"] {
        assert!(
            read_seat(&t.0, bad)
                .unwrap_err()
                .starts_with("bad seat code"),
            "{bad:?}"
        );
    }
}

#[test]
fn an_empty_goal_is_missing_not_unknown() {
    let ev = log(&[]);
    for g in ["", "   "] {
        let v = judge(&ev, Some(&stage(Some(g), None)), &[]);
        assert_eq!(
            (v.codes.clone(), v.rc()),
            (vec!["goal_missing"], 4),
            "{g:?}"
        );
    }
}

#[test]
fn check_goal_never_reads_pr_heads() {
    assert!(!reads_pr_heads("check-goal", false));
    assert!(!reads_pr_heads("check-goal", true));
    assert!(reads_pr_heads("show", false));
    assert!(!reads_pr_heads("show", true));
}

// ---- the command itself: rc 0 / 2 / 3 as the Stop hook sees them

const UID: &str = "proj-00aa11bb22cc33dd";

/// A state root with an approved card (goal A1) and a now folder.
fn cli_root(tag: &str) -> Tmp {
    let t = Tmp::new(tag);
    let dir = t.0.join("projects").join(UID);
    fs::create_dir_all(dir.join("approved")).unwrap();
    fs::create_dir_all(t.0.join("now")).unwrap();
    let goals = ["A1 Windows / mac 一併可用"];
    let card = json!({"schema": "aib-project-card/v1", "project_uid": UID,
        "layers": {"constitution": {"approved": true, "goals": goals, "red_lines": []}}});
    fs::write(dir.join("card.json"), card.to_string()).unwrap();
    let g: Vec<String> = goals.iter().map(|s| s.to_string()).collect();
    fs::write(
        dir.join("approved")
            .join(format!("{}.json", const_sha(&g, &[]))),
        json!({"goals": goals, "red_lines": []}).to_string(),
    )
    .unwrap();
    t
}

fn run_cli(t: &Tmp, seat: &str) -> (i32, String) {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aib-expect"))
        .args([
            "check-goal",
            "--project",
            UID,
            "--seat",
            seat,
            "--now",
            "1794405000",
        ])
        .arg("--state-root")
        .arg(&t.0)
        .arg("--now-dir")
        .arg(t.0.join("now"))
        .output()
        .unwrap();
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

#[test]
fn the_command_answers_ok_unreadable_and_bad_seat() {
    let t = cli_root("cli");
    let rec = t.0.join("now").join("1ced7.json");
    fs::write(&rec, r#"{"item":"做 #693","started_at":100,"goal":"a1"}"#).unwrap();
    assert_eq!(run_cli(&t, "1ced7"), (0, "ok A1\n".into()));
    // no record at all: missing (rc 4)
    assert_eq!(run_cli(&t, "2240e").0, 4);
    // a record that cannot be read: rc 3, the hook lets it pass
    fs::write(&rec, "[").unwrap();
    let (rc, out) = run_cli(&t, "1ced7");
    assert_eq!(rc, 3, "{out}");
    assert!(out.starts_with("expect_now_unreadable"), "{out}");
    // evidence that cannot be read: rc 3 too
    fs::write(&rec, r#"{"item":"x","started_at":100,"goal":"A1"}"#).unwrap();
    fs::create_dir_all(t.0.join("projects").join(UID).join("evidence.jsonl")).unwrap();
    assert_eq!(run_cli(&t, "1ced7").0, 3);
    // a seat code that is a path: usage error (rc 2), nothing read
    assert_eq!(run_cli(&t, "../x").0, 2);
}

#[test]
fn an_idle_seat_has_no_goal_to_check() {
    // a63ab 11-13 02:55: `aib now --clear` is rc 0, the goal reads `idle`
    let ev = log(&[]);
    for rec in [
        json!({"idle": true, "at": 100, "code": "1ced7"}),
        json!({"item": "", "goal": "idle"}),
        json!({"idle": true, "goal": "A2", "started_at": 100}),
    ] {
        let st = parse_stage(&rec).unwrap();
        assert!(st.idle, "{rec}");
        let v = judge(&ev, Some(&st), &[]);
        assert_eq!(
            (v.codes.clone(), v.rc(), v.goal.as_deref()),
            (vec![], 0, Some("idle")),
            "{rec}"
        );
    }
    // a record that is not idle is still judged
    let st = parse_stage(&json!({"item": "x", "idle": false})).unwrap();
    assert_eq!(judge(&ev, Some(&st), &[]).codes, vec!["goal_missing"]);
}

#[test]
fn a_card_without_a_or_e_ids_makes_a8_unknown() {
    // a63ab 11-13 02:55: until the cards carry A1–A12 / E1–E11 ids, every
    // card goal is G1…, so `--goal A8` / `E6` is goal_unknown (a reminder
    // for now; blocking waits for the ids)
    let card = vec![Goal {
        id: "G1".into(),
        text: "Windows / mac 一併可用".into(),
    }];
    let ev = log(&[]);
    let rep = compare(&card, &ev, &Snapshot::default(), NOW);
    for g in ["A8", "E6"] {
        let v = check(&ev, &rep, Some(&stage(Some(g), None)), &[]);
        assert_eq!((v.codes.clone(), v.rc()), (vec!["goal_unknown"], 4), "{g}");
    }
    let v = check(&ev, &rep, Some(&stage(Some("g1"), None)), &[]);
    assert_eq!((v.codes.clone(), v.rc()), (vec![], 0));
}
