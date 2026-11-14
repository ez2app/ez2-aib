//! The aib-drive snapshot layer against a fake GitHub, with real lines from
//! 11-11: PR #678 (aib-release trial scratch name, head 7bcb6ee0) and its
//! review first line, as GitHub and the pending folder had them.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::{decide, CellState, Intent, KnifeScan, Known, Roles, Snapshot};
use aib_drive::snapshot::{
    authors_of, build, cell_of, freeze, knife_receipts, latest_runs, lines_for, merged_branch,
    owner_claim, scan_receipt, writer_claim, FileLine, GhRead, Inputs, Read,
};
use aib_drive::source::{read_notes, run_fixed, AibQueue, Queue};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

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
        format!("repos/{R}/rules/branches/main?per_page=100"),
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

/// The dm queues as recorded; a seat with no recording is an error.
struct FakeQueue(HashMap<String, Result<Value, String>>);

impl Queue for FakeQueue {
    fn waiting(&self, seat: &str) -> Result<Value, String> {
        self.0
            .get(seat)
            .cloned()
            .unwrap_or_else(|| Err(format!("not recorded: {seat}")))
    }
}

const SEATS: [&str; 2] = ["c477a", "2240e"];

fn quiet() -> FakeQueue {
    FakeQueue(
        SEATS
            .iter()
            .map(|s| ((*s).to_owned(), Ok(json!([]))))
            .collect(),
    )
}

/// Each call its own folder: tests run in parallel.
fn pending_with(tag: &str, lines: &[(&str, &str)]) -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    let k = N.fetch_add(1, Ordering::Relaxed);
    let d = std::env::temp_dir().join(format!("aib-drive-snap-{}-{tag}-{k}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    for (name, line) in lines {
        std::fs::write(d.join(name), format!("{line}\n\n# body\n")).unwrap();
    }
    d
}

fn empty() -> PathBuf {
    pending_with("empty", &[])
}

fn snap(m: HashMap<String, Value>, pending: &Path) -> Result<Snapshot, String> {
    snap_q(m, pending, &quiet())
}

fn snap_q(m: HashMap<String, Value>, pending: &Path, q: &FakeQueue) -> Result<Snapshot, String> {
    let prev = BTreeMap::new();
    let seats: Vec<String> = SEATS.iter().map(|s| (*s).to_owned()).collect();
    build(
        &Fake(m),
        &Inputs {
            repo: R,
            roles: roles(),
            pending,
            prev_heads: &prev,
            queue: q,
            seats: &seats,
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
    let e = snap(m, &empty()).unwrap_err();
    assert_eq!(e, "open pulls: 100 items, may have a next page");
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
    assert_eq!(
        s.prs[0].commits,
        Known::Unknown("commits: 100 items, may have a next page".into())
    );
    assert!(s.prs[0].reviews.is_empty());
}

#[test]
fn required_cells_come_from_the_rules_and_unreadable_rules_are_unknown() {
    let s = snap(gh678(), &empty()).unwrap();
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
        format!("repos/{R}/rules/branches/main?per_page=100"),
        json!([{"type": "required_status_checks", "ruleset_id": 7, "parameters": {
            "required_status_checks": [{"integration_id": 15368}]}}]),
    );
    let s = snap(m, &empty()).unwrap();
    assert!(matches!(s.required_cells, Known::Unknown(_)));
    assert!(matches!(s.rules_version, Known::Unknown(_)));
    let mut m = gh678();
    m.remove(&format!("repos/{R}/rules/branches/main?per_page=100"));
    let s = snap(m, &empty()).unwrap();
    assert!(matches!(s.required_cells, Known::Unknown(_)));
}

#[test]
fn a_review_counts_only_at_a_commit_of_the_pr_and_its_writer_is_the_file_names_seat() {
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
            // broken and names no commit of the PR: left out (one that names
            // the head is a_broken_note_about_the_head_makes_only_that_pr_unknown)
            line: "結論: 通過 頭: 未填".into(),
        },
        FileLine {
            name: "VERIFY-desktop-678-2240e.md".into(),
            line: format!("驗收: 通過 頭: {H678} 機: MacB 樣本: 5 沒驗: 無"),
        },
    ];
    let notes = lines_for(&files, &[H678.to_owned()]);
    assert!(notes.unknown.is_empty(), "{:?}", notes.unknown);
    let (reviews, verifies) = (notes.reviews, notes.verifies);
    assert_eq!(reviews.len(), 1);
    assert_eq!(reviews[0].head, H678);
    assert_eq!(reviews[0].from.as_deref(), Some(B678));
    assert_eq!(reviews[0].by, Known::Known("2240e".to_owned()));
    assert_eq!(verifies.len(), 1);
    assert_eq!(verifies[0].kind, "desktop");
    assert_eq!(verifies[0].by, Known::Known("2240e".to_owned()));
    // only REVIEW-* / VERIFY-* .md files, first line only
    let d = pending_with(
        "lines",
        &[
            ("REVIEW-678-release-tempdir-2240e-7bcb6ee0.md", REV678),
            ("NOTE-678.md", REV678),
            ("REVIEW-678.txt", REV678),
        ],
    );
    let got = read_notes(&d).unwrap();
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
    let s = snap(m, &empty()).unwrap();
    assert!(matches!(s.prs[0].cells, Known::Unknown(_)));
    // was a draft once, no ready event read: the ready time is unknown
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/issues/678/timeline?per_page=100"),
        json!([{"event": "convert_to_draft", "created_at": "2026-11-11T11:21:00Z"}]),
    );
    let s = snap(m, &empty()).unwrap();
    assert!(matches!(s.prs[0].cells, Known::Unknown(_)));
}

#[test]
fn guarded_paths_are_seen_and_an_unreadable_file_list_is_unknown() {
    let s = snap(gh678(), &empty()).unwrap();
    assert_eq!(s.prs[0].touches_guarded, Known::Known(false));
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls/678/files?per_page=100"),
        json!([{"filename": "crates/aib-release/src/trial.rs"}, {"filename": ".github/workflows/aibd.yml"}]),
    );
    let s = snap(m, &empty()).unwrap();
    assert_eq!(s.prs[0].touches_guarded, Known::Known(true));
    let mut m = gh678();
    m.remove(&format!("repos/{R}/pulls/678/files?per_page=100"));
    let s = snap(m, &empty()).unwrap();
    assert!(matches!(s.prs[0].touches_guarded, Known::Unknown(_)));
}

#[test]
fn the_gh_reader_passes_only_api_and_a_path() {
    assert_eq!(
        GhRead::args("repos/o/r/pulls").unwrap(),
        vec!["api".to_owned(), "repos/o/r/pulls".to_owned()]
    );
    for bad in [
        "",
        "-X",
        "--method",
        "repos/o/r -X POST",
        "-f",
        "graphql",
        "user",
        "/repos/o/r",
    ] {
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

// ---- a9136 PR-B: sources that cannot be read, deadlines, busy seats ----

#[test]
fn an_unreadable_notes_folder_is_no_snapshot() {
    let e = snap(gh678(), Path::new("/nonexistent/aib-drive-notes")).unwrap_err();
    assert_eq!(e, "notes /nonexistent/aib-drive-notes: entity not found");
}

#[test]
fn an_unreadable_dm_queue_is_no_snapshot() {
    let mut q = quiet();
    q.0.insert("2240e".into(), Err("aib: timed out after 30000 ms".into()));
    let e = snap_q(gh678(), &empty(), &q).unwrap_err();
    assert_eq!(e, "dm 2240e: aib: timed out after 30000 ms");
    let mut q = quiet();
    q.0.insert("c477a".into(), Ok(json!({"not": "a list"})));
    let e = snap_q(gh678(), &empty(), &q).unwrap_err();
    assert_eq!(e, "dm c477a: not a list");
}

#[test]
fn unread_urgent_mail_makes_a_seat_busy() {
    let mut q = quiet();
    q.0.insert(
        "c477a".into(),
        Ok(json!([{"priority": "urgent", "read_at": null}])),
    );
    q.0.insert(
        "2240e".into(),
        Ok(json!([
            {"priority": "urgent", "read_at": 1794405236},
            {"priority": "normal", "read_at": null}
        ])),
    );
    let s = snap_q(gh678(), &empty(), &q).unwrap();
    assert_eq!(s.busy_seats.iter().collect::<Vec<_>>(), vec!["c477a"]);
}

#[test]
fn a_commit_list_that_does_not_end_at_the_head_is_unknown() {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls/678/commits?per_page=100"),
        json!([{"sha": H678}, {"sha": B678}]),
    );
    let d = pending_with("moved", &[("REVIEW-678-x-2240e-7bcb6ee0.md", REV678)]);
    let s = snap(m, &d).unwrap();
    assert_eq!(
        s.prs[0].commits,
        Known::Unknown("commits do not end at the head (moved between reads)".into())
    );
    assert!(s.prs[0].reviews.is_empty());
}

#[test]
fn a_pull_without_its_label_list_is_no_snapshot() {
    let mut m = gh678();
    let mut p = pull(678, H678, false);
    p.as_object_mut().unwrap().remove("labels");
    m.insert(
        format!("repos/{R}/pulls?state=open&per_page=100"),
        json!([p]),
    );
    assert_eq!(snap(m, &empty()).unwrap_err(), "pull without labels");
}

#[test]
fn the_owner_is_the_seat_the_branch_name_claims() {
    let s = snap(gh678(), &empty()).unwrap();
    assert_eq!(s.prs[0].owner, "c477a");
    assert_eq!(owner_claim("feat/aib-drive-snapshot-a9136-1111"), "a9136");
    assert_eq!(
        owner_claim("integ/aib-cloud-site-create-4542e-1111"),
        "4542e"
    );
    assert_eq!(owner_claim("fix/no-seat-here"), "unknown");
}

#[test]
fn a_cancelled_or_runner_red_cell_is_not_re_run_without_a_journal() {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/commits/{H678}/check-runs?filter=latest&per_page=100"),
        json!({"total_count": 2, "check_runs": [
            run("rust (macos-latest)", "completed", Some("cancelled"), "2026-11-11T11:25:00Z", 43001),
            run("rust (ubuntu-latest)", "completed", Some("failure"), "2026-11-11T11:25:00Z", 43002),
        ]}),
    );
    let s = snap(m, &empty()).unwrap();
    let cells = s.prs[0].cells.get().unwrap();
    assert!(cells.iter().all(|c| c.rerun_done), "{cells:?}");
    let out = decide(&s);
    assert!(
        !out.iter().any(|i| matches!(i, Intent::Rerun { .. })),
        "{out:?}"
    );
}

#[test]
fn a_renamed_required_check_is_a_new_rules_version() {
    let a = snap(gh678(), &empty()).unwrap().rules_version;
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/rules/branches/main?per_page=100"),
        json!([{"type": "required_status_checks", "ruleset_id": 7, "parameters": {
            "required_status_checks": [{"context": "rust (macos-14)"}, {"context": "rust (ubuntu-latest)"}]}}]),
    );
    let b = snap(m, &empty()).unwrap().rules_version;
    assert!(matches!(a, Known::Known(_)));
    assert_ne!(a, b);
}

#[test]
fn a_matching_note_that_cannot_be_read_is_an_error() {
    let d = pending_with("unreadable", &[]);
    // a folder named like a note cannot be read as a file
    std::fs::create_dir_all(d.join("REVIEW-678-dir.md")).unwrap();
    let e = read_notes(&d).unwrap_err();
    assert!(e.starts_with("note REVIEW-678-dir.md"), "{e}");
}

#[test]
fn the_dm_reader_passes_only_a_seat_code() {
    assert_eq!(
        AibQueue::args("a9136").unwrap(),
        ["dm-queue", "list", "--seat", "a9136", "--tab", "waiting", "--origin", "live"]
            .map(str::to_owned)
            .to_vec()
    );
    for bad in [
        "",
        "-x",
        "A9136",
        "a913",
        "a91366",
        "a9136 --tab all",
        "../x",
    ] {
        assert!(AibQueue::args(bad).is_err(), "{bad:?}");
    }
}

#[test]
fn a_program_that_cannot_start_is_an_error() {
    let e = run_fixed(
        "/nonexistent/aib-drive-no-such-bin",
        &[],
        Duration::from_secs(5),
    )
    .unwrap_err();
    assert!(e.contains("not run"), "{e}");
}

#[cfg(unix)]
#[test]
fn a_call_past_its_deadline_is_killed_and_an_error() {
    let t = std::time::Instant::now();
    let e = run_fixed("/bin/sleep", &["30".to_owned()], Duration::from_millis(300)).unwrap_err();
    assert_eq!(e, "/bin/sleep: timed out after 300 ms");
    assert!(t.elapsed() < Duration::from_secs(10), "{:?}", t.elapsed());
}

#[cfg(unix)]
#[test]
fn a_non_zero_exit_is_an_error_even_with_output() {
    let args = ["-c", "echo '[]'; echo nope >&2; exit 3"].map(str::to_owned);
    let e = run_fixed("/bin/sh", &args, Duration::from_secs(10)).unwrap_err();
    assert_eq!(e, "/bin/sh: exit 3: nope");
    let ok = run_fixed(
        "/bin/sh",
        &["-c".to_owned(), "echo '[]'".to_owned()],
        Duration::from_secs(10),
    )
    .unwrap();
    assert_eq!(ok, b"[]\n");
}

// ---- 1ced7 #685 L1 / L2 / L3 ----

#[test]
fn a_full_page_of_rules_is_unknown() {
    let mut m = gh678();
    let many: Vec<Value> = (0..100)
        .map(|i| json!({"type": "deletion", "ruleset_id": i}))
        .collect();
    m.insert(
        format!("repos/{R}/rules/branches/main?per_page=100"),
        json!(many),
    );
    let s = snap(m, &empty()).unwrap();
    let full = "rules: 100 items, may have a next page";
    assert_eq!(s.required_cells, Known::Unknown(full.into()));
    assert_eq!(s.rules_version, Known::Unknown(full.into()));
}

#[test]
fn a_file_moved_out_of_a_guarded_path_touches_it() {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls/678/files?per_page=100"),
        json!([{"filename": "tools/knife-old.py", "previous_filename": "scripts/knife.py",
                "status": "renamed"}]),
    );
    let s = snap(m, &empty()).unwrap();
    assert_eq!(s.prs[0].touches_guarded, Known::Known(true));
}

#[cfg(unix)]
#[test]
fn a_child_that_closes_its_output_and_keeps_running_is_killed_at_the_deadline() {
    let t = std::time::Instant::now();
    let args = ["-c", "exec >&- 2>&-; exec /bin/sleep 30"].map(str::to_owned);
    let e = run_fixed("/bin/sh", &args, Duration::from_millis(500)).unwrap_err();
    assert_eq!(e, "/bin/sh: timed out after 500 ms");
    assert!(t.elapsed() < Duration::from_secs(10), "{:?}", t.elapsed());
}

// 1ced7 #685 r2 L1: the child exits at once and a grandchild keeps stdout open.
// The waiter has reaped the child by the deadline, so its pid may already
// belong to another process: it is not killed, and the error says why.
#[cfg(unix)]
#[test]
fn a_child_already_reaped_is_not_killed_by_pid_at_the_deadline() {
    let t = std::time::Instant::now();
    let args = ["-c", "/bin/sleep 3 & exit 0"].map(str::to_owned);
    let e = run_fixed("/bin/sh", &args, Duration::from_millis(500)).unwrap_err();
    assert_eq!(
        e,
        "/bin/sh: timed out after 500 ms (exited, output held open)"
    );
    assert!(t.elapsed() < Duration::from_secs(10), "{:?}", t.elapsed());
}

// ---- Codex #685 M2 / M3 (second review of 040077a8) ----

/// M2: a dm item that does not read is not "not urgent".
#[test]
fn a_dm_item_that_does_not_read_is_no_snapshot() {
    let cases = [
        (json!([null]), "dm c477a: item 0: priority not a string"),
        (
            json!([{"priority": 1, "read_at": null}]),
            "dm c477a: item 0: priority not a string",
        ),
        (
            json!([{"priority": "normal", "read_at": null}, {"priority": "urgent", "read_at": true}]),
            "dm c477a: item 1: read_at missing or not a time",
        ),
        (
            json!([{"priority": "urgent"}]),
            "dm c477a: item 0: read_at missing or not a time",
        ),
    ];
    for (items, want) in cases {
        let mut q = quiet();
        q.0.insert("c477a".into(), Ok(items));
        let e = snap_q(gh678(), &empty(), &q).unwrap_err();
        assert_eq!(e, want);
    }
}

/// M3: a note that names a commit of the PR but does not parse is an
/// error with its name and why; one that names no commit of it is left out.
const H679: &str = "679679a1b2c3d4e5f60718293a4b5c6d7e8f9012";
const O679: &str = "679000a1b2c3d4e5f60718293a4b5c6d7e8f9012";

/// #678 and a second PR #679 (made-up SHAs) with two commits: O679, then the head H679.
fn gh678_679() -> HashMap<String, Value> {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls?state=open&per_page=100"),
        json!([pull(678, H678, false), pull(679, H679, false)]),
    );
    m.insert(
        format!("repos/{R}/pulls/679/commits?per_page=100"),
        json!([{"sha": O679}, {"sha": H679}]),
    );
    m.insert(
        format!("repos/{R}/pulls/679/files?per_page=100"),
        json!([{"filename": "crates/aib-drive/src/snapshot.rs"}]),
    );
    m.insert(
        format!("repos/{R}/issues/679/timeline?per_page=100"),
        json!([{"event": "committed"}]),
    );
    m.insert(
        format!("repos/{R}/commits/{H679}/check-runs?filter=latest&per_page=100"),
        json!({"total_count": 1, "check_runs": [
            run("rust (macos-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43002),
        ]}),
    );
    m
}

fn rev679() -> String {
    format!("結論: 通過 頭: {H679} H/M/L/N: 0/0/0/0 範圍: 全文")
}

/// n-1 (519d0 11-14, f1fae N0-drive-blocked): a note about a PR's head
/// whose first line does not parse makes that PR unknown with the file and
/// why; the round still has a snapshot and the other PR keeps its review.
#[test]
fn a_broken_note_about_the_head_makes_only_that_pr_unknown() {
    let bad = "結論: 要改 頭: 7bcb6ee014c446d41455fa32664e646860421fe4 H/M/L/N: 0/1/0/0 範圍: 全文";
    let d = pending_with(
        "broken-head",
        &[
            ("REVIEW-678-x-1ced7-7bcb6ee0.md", bad),
            ("REVIEW-679-y-043dd-679679a1.md", &rev679()),
        ],
    );
    let s = snap(gh678_679(), &d).unwrap();
    let p678 = s.prs.iter().find(|p| p.number == 678).unwrap();
    let p679 = s.prs.iter().find(|p| p.number == 679).unwrap();
    assert_eq!(p678.unknown_notes.len(), 1, "{:?}", p678.unknown_notes);
    assert!(
        p678.unknown_notes[0].starts_with("REVIEW-678-x-1ced7-7bcb6ee0.md: "),
        "{:?}",
        p678.unknown_notes
    );
    assert!(p679.unknown_notes.is_empty(), "{:?}", p679.unknown_notes);
    assert_eq!(p679.reviews.len(), 1);
    // an acceptance note about the head counts the same way
    let d = pending_with(
        "broken-head-v",
        &[("VERIFY-gem-678-c477a.md", "驗收 7bcb6ee014c4 看起來可以")],
    );
    let s = snap(gh678_679(), &d).unwrap();
    let p678 = s.prs.iter().find(|p| p.number == 678).unwrap();
    assert_eq!(
        p678.unknown_notes,
        vec!["VERIFY-gem-678-c477a.md: first line label missing".to_owned()]
    );
    // the round lists it with why; the other PR is judged
    let r = aib_drive::observe::record_of(1, R, &BTreeMap::new(), Ok(s));
    assert!(r.unknown.is_none());
    assert_eq!(r.unknown_prs.keys().copied().collect::<Vec<_>>(), vec![678]);
    assert_eq!(r.heads.len(), 2);
    let line = aib_drive::observe::summary(&r);
    assert!(line.contains("#678") && !line.contains("#679"), "{line}");
}

/// A broken note about an older commit of a PR, or about no commit of any
/// open PR, changes nothing: `pending` keeps such notes for good (S81).
#[test]
fn a_broken_note_off_the_head_changes_nothing() {
    let older = format!("結論: 需修 頭: {O679} H/M/L/N: 0/1/0/0 範圍: 全文");
    let d = pending_with(
        "broken-off-head",
        &[
            ("REVIEW-1-old-2240e.md", "# old review of 0123456789abcdef"),
            ("REVIEW-679-old-043dd-679000a1.md", &older),
            ("REVIEW-679-y-043dd-679679a1.md", &rev679()),
            ("REVIEW-678-x-2240e-7bcb6ee0.md", REV678),
        ],
    );
    let s = snap(gh678_679(), &d).unwrap();
    for p in &s.prs {
        assert!(
            p.unknown_notes.is_empty(),
            "#{} {:?}",
            p.number,
            p.unknown_notes
        );
        assert_eq!(p.reviews.len(), 1, "#{}", p.number);
    }
    let r = aib_drive::observe::record_of(1, R, &BTreeMap::new(), Ok(s));
    assert!(r.unknown_prs.is_empty());
    let line = serde_json::to_string(&r).unwrap();
    assert!(!line.contains("unknown_prs"), "{line}");
}

/// GitHub as recorded in tests/fixtures/v8: a path is served from
/// `<path with / ? & = as _>.json`.
struct Dir(PathBuf);

impl Read for Dir {
    fn get(&self, path: &str) -> Result<Value, String> {
        let k: String = path
            .chars()
            .map(|c| {
                if matches!(c, '/' | '?' | '&' | '=') {
                    '_'
                } else {
                    c
                }
            })
            .collect();
        let text = std::fs::read_to_string(self.0.join(format!("{k}.json")))
            .map_err(|_| format!("404 {path}"))?;
        serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))
    }
}

/// V8 with real data (043dd V8 L1): #661 / #667 / #678 as GitHub had them on
/// 11-11 and their four review first lines. Each PR carries exactly the
/// lines about its own commits: #661 the failed full review at 0c327b35 and
/// the passing increment at d4bafde2; #667 and #678 one pass at their head.
/// Still no merge in v0.
#[test]
fn real_prs_661_667_678_carry_their_own_review_lines() {
    let d = pending_with(
        "v8",
        &[
            ("REVIEW-661-consent-run-2240e-0c327b35.md", "結論: 不通過 頭: 0c327b358846842e54e843380f57a0617bbe0ae6 H/M/L/N: 0/2/3/4 範圍: 增量 35c384b90ec8043de2f3cf6a1521ec2d842ab912..0c327b358846842e54e843380f57a0617bbe0ae6"),
            ("REVIEW-661-r2-2240e-d4bafde2.md", "結論: 通過 頭: d4bafde28b279252c5b5916da30f63395aaa4143 H/M/L/N: 0/0/1/0 範圍: 增量 0c327b358846842e54e843380f57a0617bbe0ae6..d4bafde28b279252c5b5916da30f63395aaa4143"),
            ("REVIEW-667-unixonly-2240e-cca3249e.md", "結論: 通過 頭: cca3249e502694a89becbd48f7db31b072b9098f H/M/L/N: 0/0/0/0 範圍: 增量 347e16c045eed58b07b87884817a7a045fe66992..cca3249e502694a89becbd48f7db31b072b9098f(讀碼，待 CI)"),
            ("REVIEW-678-release-tempdir-2240e-7bcb6ee0.md", REV678),
        ],
    );
    let prev = BTreeMap::new();
    let seats: Vec<String> = SEATS.iter().map(|s| (*s).to_owned()).collect();
    let s = build(
        &Dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/v8")),
        &Inputs {
            repo: R,
            roles: roles(),
            pending: &d,
            prev_heads: &prev,
            queue: &quiet(),
            seats: &seats,
        },
    )
    .unwrap();
    let got: Vec<(u64, Vec<(String, bool)>)> = s
        .prs
        .iter()
        .map(|p| {
            let mut r: Vec<(String, bool)> = p
                .reviews
                .iter()
                .map(|l| (l.head[..8].to_owned(), l.pass))
                .collect();
            r.sort();
            (p.number, r)
        })
        .collect();
    assert_eq!(
        got,
        vec![
            (
                661,
                vec![("0c327b35".into(), false), ("d4bafde2".into(), true)]
            ),
            (667, vec![("cca3249e".into(), true)]),
            (678, vec![("7bcb6ee0".into(), true)]),
        ]
    );
    let out = decide(&s);
    assert!(
        !out.iter().any(|i| matches!(i, Intent::Merge { .. })),
        "{out:?}"
    );
}

// ---- F9 (519d0 11-15 S79-dec): observe reads today's data ----

/// A receipt the way the shared receipt-run.sh prints it (tail only).
fn receipt(head: &str, knives: &str, tail: &str) -> String {
    format!("== HEAD={head}\nRC clippy=0\nRC test=0\nTREE-CLEAN\nHEAD-END {head}\n{knives}{tail}")
}

const OK_TAIL: &str =
    "KNIVES-RC 0\nBITES-COUNT 2\nMISSES-COUNT 0\nTREE-CLEAN\nHEAD-END H\nKNIVES-OK\n";

fn ok_receipt(head: &str) -> String {
    receipt(
        head,
        "RC knife_a1=101 (want non-0)\nRC knife_a2=101 (want non-0)\n",
        &OK_TAIL.replace("HEAD-END H", &format!("HEAD-END {head}")),
    )
}

fn write_at(dir: &Path, name: &str, body: &str, secs: u64) {
    let p = dir.join(name);
    std::fs::write(&p, body).unwrap();
    let f = std::fs::File::options().write(true).open(&p).unwrap();
    f.set_modified(std::time::UNIX_EPOCH + Duration::from_secs(1_700_000_000 + secs))
        .unwrap();
}

#[test]
fn a_writer_is_the_last_seat_code_in_the_file_name() {
    assert_eq!(
        writer_claim("REVIEW-drive-wl1-5d33a-ae9e225f.md"),
        Known::Known("5d33a".to_owned())
    );
    // a slice of another seat named in the topic: the writer comes last
    assert_eq!(
        writer_claim("REVIEW-c4-ebb36-5d33a-425f22c2.md"),
        Known::Known("5d33a".to_owned())
    );
    assert_eq!(
        writer_claim("VERIFY-desktop-678-2240e.md"),
        Known::Known("2240e".to_owned())
    );
    assert!(matches!(
        writer_claim("REVIEW-678-nobody.md"),
        Known::Unknown(_)
    ));
    // the REVIEW / VERIFY word itself is never the writer
    assert!(matches!(writer_claim("REVIEW.md"), Known::Unknown(_)));
}

#[test]
fn authors_are_the_branch_seat_and_the_seats_of_merged_branches() {
    let s = |v: &[&str]| v.iter().map(|m| Some((*m).to_owned())).collect::<Vec<_>>();
    let set = |v: &[&str]| {
        Known::Known(
            v.iter()
                .map(|x| (*x).to_owned())
                .collect::<std::collections::BTreeSet<String>>(),
        )
    };
    assert_eq!(
        merged_branch("Merge pull request #772 from ez2app/integ-src/publeak-local-7b81e\n\nbody"),
        Some("ez2app/integ-src/publeak-local-7b81e")
    );
    assert_eq!(
        merged_branch("Merge branch 'feat/s79-b3-refresh-a9136-1115' into integ"),
        Some("feat/s79-b3-refresh-a9136-1115")
    );
    assert_eq!(
        merged_branch("Merge remote-tracking branch 'origin/main'"),
        Some("origin/main")
    );
    assert_eq!(merged_branch("aib-drive: F9 reads receipts"), None);
    // a plain slice: the branch's seat only
    assert_eq!(
        authors_of("feat/drive-f9-7b81e-1115", &s(&["F9 a", "F9 b"])),
        set(&["7b81e"])
    );
    // an integration head: the integration seat and every merged slice's seat;
    // main adds nobody
    assert_eq!(
        authors_of(
            "int/s79-third-a9136-1115",
            &s(&[
                "Merge branch 'feat/drive-f9-7b81e-1115'",
                "Merge remote-tracking branch 'origin/main'",
                "Merge branch 'main' into int/s79-third-a9136-1115",
                "Merge pull request #9 from ez2app/feat/c4-ebb36-1115",
            ])
        ),
        set(&["7b81e", "a9136", "ebb36"])
    );
    // no claim, a merged branch with no seat, a commit with no message: unknown
    assert!(matches!(
        authors_of("fix/no-seat", &s(&["x"])),
        Known::Unknown(_)
    ));
    assert!(matches!(
        authors_of("feat/x-7b81e-1115", &s(&["Merge branch 'hotfix/nobody'"])),
        Known::Unknown(_)
    ));
    assert!(matches!(
        authors_of("feat/x-7b81e-1115", &[Some("a".into()), None]),
        Known::Unknown(_)
    ));
}

#[test]
fn the_newest_run_of_a_cell_replaces_the_older_one() {
    let runs = vec![
        run(
            "docs",
            "completed",
            Some("cancelled"),
            "2026-11-14T23:35:15Z",
            1,
        ),
        run(
            "docs",
            "completed",
            Some("success"),
            "2026-11-14T23:35:42Z",
            2,
        ),
        // the same start: the larger id is the newer
        run(
            "tip",
            "completed",
            Some("failure"),
            "2026-11-14T23:35:42Z",
            3,
        ),
        run(
            "tip",
            "completed",
            Some("success"),
            "2026-11-14T23:35:42Z",
            4,
        ),
        // newest first in the answer: still the newest wins
        run(
            "smoke (macos-latest, 3.12)",
            "queued",
            None,
            "2026-11-14T23:36:15Z",
            6,
        ),
        run(
            "smoke (macos-latest, 3.12)",
            "completed",
            Some("cancelled"),
            "2026-11-14T23:35:40Z",
            5,
        ),
    ];
    let got: Vec<(String, u64)> = latest_runs(&runs)
        .iter()
        .map(|r| {
            (
                r["name"].as_str().unwrap().to_owned(),
                r["id"].as_u64().unwrap(),
            )
        })
        .collect();
    assert_eq!(
        got,
        vec![
            ("docs".to_owned(), 20),
            ("smoke (macos-latest, 3.12)".to_owned(), 60),
            ("tip".to_owned(), 40),
        ]
    );
    // a run with no start time is kept, so the cells stay unknown
    let no_start = json!({"name": "docs", "status": "queued", "id": 9});
    let mut with = runs.clone();
    with.push(no_start);
    assert_eq!(latest_runs(&with).len(), 4);
    // through build: a required cell run twice on one head is one result
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/commits/{H678}/check-runs?filter=latest&per_page=100"),
        json!({"total_count": 3, "check_runs": [
            run("rust (macos-latest)", "completed", Some("cancelled"), "2026-11-11T11:22:00Z", 42001),
            run("rust (macos-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
            run("rust (ubuntu-latest)", "completed", Some("success"), "2026-11-11T11:25:00Z", 43001),
        ]}),
    );
    let s = snap(m, &empty()).unwrap();
    let Known::Known(cells) = &s.prs[0].cells else {
        panic!("{:?}", s.prs[0].cells)
    };
    let mac: Vec<_> = cells
        .iter()
        .filter(|c| c.name == "rust (macos-latest)")
        .collect();
    assert_eq!(mac.len(), 1);
    assert_eq!(mac[0].state, CellState::Success);
}

#[test]
fn receipts_read_the_way_receipt_run_prints_them() {
    let ok = ok_receipt(H678);
    assert_eq!(scan_receipt(&ok), Some((H678.to_owned(), KnifeScan::Ok)));
    // a knife that did not bite
    let survives = receipt(
        H678,
        "RC knife_a1=0 (want non-0)\n",
        &format!("KNIVES-RC 1\nBITES-COUNT 0\nMISSES-COUNT 1\nHEAD-END {H678}\n"),
    );
    assert_eq!(
        scan_receipt(&survives),
        Some((H678.to_owned(), KnifeScan::Survives))
    );
    // a knife that broke the build is not a bite
    let invalid = receipt(
        H678,
        "RC knife_a1=101 INVALID(沒有 test result FAILED / panicked 行)\nINVALID:編譯錯 error[E0425]\n",
        &format!("KNIVES-RC 1\nBITES-COUNT 0\nMISSES-COUNT 1\nHEAD-END {H678}\n"),
    );
    assert_eq!(
        scan_receipt(&invalid),
        Some((H678.to_owned(), KnifeScan::Aborted))
    );
    let anchor = receipt(
        H678,
        "RC knife_a1=2 anchor-missing\n",
        &format!("KNIVES-RC 1\nBITES-COUNT 0\nMISSES-COUNT 1\nHEAD-END {H678}\n"),
    );
    assert_eq!(
        scan_receipt(&anchor),
        Some((H678.to_owned(), KnifeScan::Misses))
    );
    let skipped = receipt(
        H678,
        "",
        &format!("KNIVES-SKIPPED (a knife file was given; clippy or test failed, so no knife ran)\nKNIVES-RC 1\nMISSES-COUNT 1\nHEAD-END {H678}\n"),
    );
    assert_eq!(
        scan_receipt(&skipped),
        Some((H678.to_owned(), KnifeScan::Aborted))
    );
    // the head moved during the run
    let moved = ok.replace(
        &format!("TREE-CLEAN\nHEAD-END {H678}\nKNIVES-OK"),
        "TREE-CLEAN\nHEAD-END cca3249e502694a89becbd48f7db31b072b9098f\nKNIVES-OK",
    );
    assert_ne!(moved, ok);
    assert_eq!(
        scan_receipt(&moved),
        Some((H678.to_owned(), KnifeScan::Aborted))
    );
    // every knife bit, but the tree did not come back clean (no KNIVES-OK)
    let dirty = ok.replace("KNIVES-OK\n", "");
    assert_eq!(
        scan_receipt(&dirty),
        Some((H678.to_owned(), KnifeScan::Aborted))
    );
    // says nothing about knives
    let ci = receipt(
        H678,
        "",
        "KNIVES-NOT-RUN (no knife-file; knives are left to CI)\nKNIVES-RC 0\nKNIVES-OK\n",
    );
    assert_eq!(scan_receipt(&ci), None);
    assert_eq!(scan_receipt(&receipt(H678, "", "")), None);
    assert_eq!(scan_receipt(&ok.replace("HEAD-END ", "HEAD ")), None);
}

#[test]
fn the_newest_receipt_about_the_head_is_its_knife_scan() {
    let d = empty();
    // an older green receipt, then a newer one where a knife survived
    write_at(&d, "RECEIPT-x-7b81e-7bcb6ee01.out", &ok_receipt(H678), 10);
    let red = receipt(
        H678,
        "RC knife_a1=0 (want non-0)\n",
        &format!("KNIVES-RC 1\nMISSES-COUNT 1\nHEAD-END {H678}\n"),
    );
    write_at(&d, "RECEIPT-x-r2-7b81e-7bcb6ee01.out", &red, 20);
    // the newest says nothing about knives: it replaces nothing
    write_at(
        &d,
        "RECEIPT-guards-7b81e-7bcb6ee01.txt",
        &receipt(H678, "", ""),
        30,
    );
    // not a receipt
    write_at(&d, "NOTE-7bcb6ee01.out", &ok_receipt(H678), 40);
    let Known::Known(m) = knife_receipts(&d) else {
        panic!("unknown")
    };
    assert_eq!(m.get(H678), Some(&KnifeScan::Survives));
    write_at(
        &d,
        "RECEIPT-x-r3-7b81e-7bcb6ee01.out",
        &ok_receipt(H678),
        50,
    );
    // through build: the PR's head has a green newest receipt; with none, the log is missing
    let s = snap(gh678(), &d).unwrap();
    assert_eq!(s.prs[0].knives, Known::Known(KnifeScan::Ok));
    let s = snap(gh678(), &empty()).unwrap();
    assert_eq!(s.prs[0].knives, Known::Known(KnifeScan::LogMissing));
    assert!(matches!(
        knife_receipts(&d.join("missing")),
        Known::Unknown(_)
    ));
}

#[test]
fn push_freeze_in_the_notes_folder_is_the_freeze() {
    let d = empty();
    assert_eq!(freeze(&d), Known::Known(None));
    assert_eq!(snap(gh678(), &d).unwrap().freeze, Known::Known(None));
    std::fs::write(d.join("PUSH-FREEZE"), "main 紅：全員暫停\n第二行\n").unwrap();
    assert_eq!(
        freeze(&d),
        Known::Known(Some("main 紅：全員暫停".to_owned()))
    );
    assert_eq!(
        snap(gh678(), &d).unwrap().freeze,
        Known::Known(Some("main 紅：全員暫停".to_owned()))
    );
    std::fs::write(d.join("PUSH-FREEZE"), "").unwrap();
    assert_eq!(freeze(&d), Known::Known(Some("no reason given".to_owned())));
}

#[test]
fn commit_authors_come_from_the_branch_and_its_merges() {
    let mut m = gh678();
    m.insert(
        format!("repos/{R}/pulls/678/commits?per_page=100"),
        json!([{"sha": H678, "commit": {"message": "Merge branch 'feat/x-ebb36-1111'\n\nbody"}}]),
    );
    let s = snap(m, &empty()).unwrap();
    assert_eq!(
        s.prs[0].authors,
        Known::Known(["c477a".to_owned(), "ebb36".to_owned()].into())
    );
    // GitHub's answer without messages: unknown, nobody is a non-author on a guess
    let s = snap(gh678(), &empty()).unwrap();
    assert!(matches!(s.prs[0].authors, Known::Unknown(_)));
}
