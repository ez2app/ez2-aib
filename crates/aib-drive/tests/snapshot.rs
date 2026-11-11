//! The aib-drive snapshot layer against a fake GitHub, with real lines from
//! 11-11: PR #678 (aib-release trial scratch name, head 7bcb6ee0) and its
//! review first line, as GitHub and the pending folder had them.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::{decide, CellState, Intent, Known, Roles, Snapshot};
use aib_drive::snapshot::{build, cell_of, lines_for, read_lines, FileLine, GhRead, Inputs, Read};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};

const R: &str = "ez2app/ez2-aib";
const H678: &str = "7bcb6ee014c446d41455fa32664e646860421fe4";
const B678: &str = "cdddab4398e8effcbb67bd01d5303f1ab00000f6";
const REV678: &str = "結論: 通過 頭: 7bcb6ee014c446d41455fa32664e646860421fe4 H/M/L/N: 0/0/0/1 範圍: 增量 cdddab4398e8effcbb67bd01d5303f1ab00000f6..7bcb6ee014c446d41455fa32664e646860421fe4";

struct Fake(HashMap<String, Value>);

impl Read for Fake {
    fn get(&self, path: &str) -> Result<Value, String> {
        self.0
            .get(path)
            .cloned()
            .ok_or_else(|| format!("404 {path}"))
    }
}

fn pull(n: u64, head: &str, draft: bool) -> Value {
    json!({
        "number": n, "draft": draft, "created_at": "2026-11-11T11:20:00Z",
        "user": {"login": "c477a"},
        "labels": [],
        "head": {"sha": head, "ref": "fix/aib-release-trial-tempdir-c477a-1111"},
        "base": {"sha": B678, "ref": "main"},
    })
}

fn run(name: &str, status: &str, conclusion: Option<&str>, started: &str, id: u64) -> Value {
    json!({
        "name": name, "status": status, "conclusion": conclusion, "started_at": started,
        "id": id * 10,
        "details_url": format!("https://github.com/{R}/actions/runs/{id}/job/{}", id * 10),
    })
}

/// #678 the way GitHub showed it: ready, two required cells green after
/// ready, one commit, three files under crates/ and the workflow.
fn gh678() -> HashMap<String, Value> {
    let mut m = HashMap::new();
    m.insert(
        format!("repos/{R}/pulls?state=open&per_page=100"),
        json!([pull(678, H678, false)]),
    );
    m.insert(
        format!("repos/{R}/rules/branches/main"),
        json!([
            {"type": "deletion", "ruleset_id": 7},
            {"type": "required_status_checks", "ruleset_id": 7, "parameters": {
                "required_status_checks": [{"context": "rust (macos-latest)"}, {"context": "rust (ubuntu-latest)"}]}}
        ]),
    );
    m.insert(
        format!("repos/{R}/pulls/678/commits?per_page=100"),
        json!([{"sha": H678}]),
    );
    m.insert(
        format!("repos/{R}/pulls/678/files?per_page=100"),
        json!([{"filename": "crates/aib-release/src/trial.rs"}, {"filename": "Cargo.lock"}]),
    );
    m.insert(
        format!("repos/{R}/issues/678/timeline?per_page=100"),
        json!([{"event": "committed"}]),
    );
    m.insert(
        format!("repos/{R}/commits/{H678}/check-runs?filter=latest&per_page=100"),
        json!({"total_count": 2, "check_runs": [
            run("rust (macos-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
            run("rust (ubuntu-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
        ]}),
    );
    m
}

fn roles() -> Roles {
    Roles {
        captain: "f1fae".into(),
        collector: "f1fae".into(),
    }
}

fn pending_with(tag: &str, lines: &[(&str, &str)]) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aib-drive-snap-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    for (name, line) in lines {
        std::fs::write(d.join(name), format!("{line}\n\n# body\n")).unwrap();
    }
    d
}

fn snap(m: HashMap<String, Value>, pending: &Path) -> Result<Snapshot, String> {
    let prev = BTreeMap::new();
    build(
        &Fake(m),
        &Inputs {
            repo: R,
            roles: roles(),
            pending,
            prev_heads: &prev,
        },
    )
}

#[test]
fn a_full_page_of_open_pulls_is_no_snapshot() {
    let mut m = gh678();
    let many: Vec<Value> = (1..=100).map(|n| pull(n, H678, false)).collect();
    m.insert(
        format!("repos/{R}/pulls?state=open&per_page=100"),
        json!(many),
    );
    let e = snap(m, Path::new("/nonexistent")).unwrap_err();
    assert!(e.contains("next page"), "{e}");
}

#[test]
fn a_full_page_of_commits_is_unknown_and_no_review_is_matched() {
    let mut m = gh678();
    let many: Vec<Value> = (0..100).map(|_| json!({"sha": H678})).collect();
    m.insert(
        format!("repos/{R}/pulls/678/commits?per_page=100"),
        json!(many),
    );
    let d = pending_with("commits", &[("REVIEW-678-x-2240e-7bcb6ee0.md", REV678)]);
    let s = snap(m, &d).unwrap();
    assert!(matches!(s.prs[0].commits, Known::Unknown(_)));
    assert!(s.prs[0].reviews.is_empty());
}

#[test]
fn required_cells_come_from_the_rules_and_unreadable_rules_are_unknown() {
    let s = snap(gh678(), Path::new("/nonexistent")).unwrap();
    assert_eq!(
        s.required_cells,
        Known::Known(vec![
            "rust (macos-latest)".to_owned(),
            "rust (ubuntu-latest)".to_owned()
        ])
    );
    assert!(matches!(s.rules_version, Known::Known(_)));
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/rules/branches/main"),
        json!([{"type": "required_status_checks", "ruleset_id": 7, "parameters": {
            "required_status_checks": [{"integration_id": 15368}]}}]),
    );
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert!(matches!(s.required_cells, Known::Unknown(_)));
    assert!(matches!(s.rules_version, Known::Unknown(_)));
    let mut m = gh678();
    m.remove(&format!("repos/{R}/rules/branches/main"));
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert!(matches!(s.required_cells, Known::Unknown(_)));
}

#[test]
fn a_review_counts_only_at_a_commit_of_the_pr_and_never_as_a_known_writer() {
    let files = vec![
        FileLine {
            name: "REVIEW-678-release-tempdir-2240e-7bcb6ee0.md".into(),
            line: REV678.into(),
        },
        FileLine {
            name: "REVIEW-667-unixonly-2240e-cca3249e.md".into(),
            line: REV678.replace(H678, "cca3249e502694a89becbd48f7db31b072b9098f"),
        },
        FileLine {
            name: "REVIEW-bad-2240e.md".into(),
            line: "結論: 通過 頭: 7bcb6ee0".into(),
        },
        FileLine {
            name: "VERIFY-desktop-678-2240e.md".into(),
            line: format!("驗收: 通過 頭: {H678} 機: MacB 樣本: 5 沒驗: 無"),
        },
    ];
    let (reviews, verifies) = lines_for(&files, &[H678.to_owned()]);
    assert_eq!(reviews.len(), 1);
    assert_eq!(reviews[0].head, H678);
    assert_eq!(reviews[0].from.as_deref(), Some(B678));
    assert!(matches!(reviews[0].by, Known::Unknown(_)));
    assert_eq!(verifies.len(), 1);
    assert_eq!(verifies[0].kind, "desktop");
    assert!(matches!(verifies[0].by, Known::Unknown(_)));
    // only REVIEW-* / VERIFY-* .md files, first line only
    let d = pending_with(
        "lines",
        &[
            ("REVIEW-678-release-tempdir-2240e-7bcb6ee0.md", REV678),
            ("NOTE-678.md", REV678),
            ("REVIEW-678.txt", REV678),
        ],
    );
    let got = read_lines(&d);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].line, REV678);
}

#[test]
fn check_runs_become_cells_and_a_run_before_ready_is_a_draft_run() {
    let ready = "2026-11-11T11:24:00Z";
    let c = |st, co, at| cell_of(&run("rust (macos-latest)", st, co, at, 43001), ready);
    let ok = c("completed", Some("success"), "2026-11-11T11:25:00Z").unwrap();
    assert_eq!(
        (ok.state, ok.after_ready, ok.run),
        (CellState::Success, true, 43001)
    );
    let early = c("completed", Some("success"), "2026-11-11T11:23:59Z").unwrap();
    assert!(!early.after_ready);
    let states = [
        ("completed", Some("failure"), CellState::Failure),
        ("completed", Some("timed_out"), CellState::Failure),
        ("completed", Some("cancelled"), CellState::Cancelled),
        ("completed", Some("skipped"), CellState::Skipped),
        ("completed", Some("neutral"), CellState::Neutral),
        ("in_progress", None, CellState::Pending),
        ("queued", None, CellState::Pending),
    ];
    for (st, co, want) in states {
        assert_eq!(
            c(st, co, "2026-11-11T11:25:00Z").unwrap().state,
            want,
            "{st} {co:?}"
        );
    }
    // a conclusion this version does not know is not read as anything
    assert!(c("completed", Some("action_required"), "2026-11-11T11:25:00Z").is_none());
}

#[test]
fn check_runs_that_may_have_more_or_no_ready_time_are_unknown() {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/commits/{H678}/check-runs?filter=latest&per_page=100"),
        json!({"total_count": 3, "check_runs": [
            run("rust (macos-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
            run("rust (ubuntu-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
        ]}),
    );
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert!(matches!(s.prs[0].cells, Known::Unknown(_)));
    // was a draft once, no ready event read: the ready time is unknown
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/issues/678/timeline?per_page=100"),
        json!([{"event": "convert_to_draft", "created_at": "2026-11-11T11:21:00Z"}]),
    );
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert!(matches!(s.prs[0].cells, Known::Unknown(_)));
}

#[test]
fn guarded_paths_are_seen_and_an_unreadable_file_list_is_unknown() {
    let s = snap(gh678(), Path::new("/nonexistent")).unwrap();
    assert_eq!(s.prs[0].touches_guarded, Known::Known(false));
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls/678/files?per_page=100"),
        json!([{"filename": "crates/aib-release/src/trial.rs"}, {"filename": ".github/workflows/aibd.yml"}]),
    );
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert_eq!(s.prs[0].touches_guarded, Known::Known(true));
    let mut m = gh678();
    m.remove(&format!("repos/{R}/pulls/678/files?per_page=100"));
    let s = snap(m, Path::new("/nonexistent")).unwrap();
    assert!(matches!(s.prs[0].touches_guarded, Known::Unknown(_)));
}

#[test]
fn the_gh_reader_passes_only_api_and_a_path() {
    assert_eq!(
        GhRead::args("repos/o/r/pulls").unwrap(),
        vec!["api".to_owned(), "repos/o/r/pulls".to_owned()]
    );
    for bad in ["", "-X", "--method", "repos/o/r -X POST", "-f"] {
        assert!(GhRead::args(bad).is_err(), "{bad:?}");
    }
}

/// #678 green, reviewed at its head: this version still never proposes a
/// merge, because nobody's identity is verified yet (authors and writers
/// are Unknown) and the freeze is not read.
#[test]
fn a_real_round_never_proposes_a_merge() {
    let d = pending_with(
        "round",
        &[
            ("REVIEW-678-release-tempdir-2240e-7bcb6ee0.md", REV678),
            ("NOTE.md", "x"),
        ],
    );
    let s = snap(gh678(), &d).unwrap();
    assert_eq!(s.prs[0].reviews.len(), 1);
    assert!(matches!(s.prs[0].cells, Known::Known(ref c) if c.len() == 2));
    let out = decide(&s);
    assert!(
        !out.iter().any(|i| matches!(i, Intent::Merge { .. })),
        "{out:?}"
    );
    assert!(!out.iter().any(|i| i.is_shared_write()), "{out:?}");
}
