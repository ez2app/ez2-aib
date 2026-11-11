//! The aib-drive snapshot layer against a fake GitHub, with real lines from
//! 11-11: PR #678 (aib-release trial scratch name, head 7bcb6ee0) and its
//! review first line, as GitHub and the pending folder had them.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::{decide, CellState, Intent, Known, Roles, Snapshot};
use aib_drive::snapshot::{build, cell_of, lines_for, owner_claim, FileLine, GhRead, Inputs, Read};
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
            // broken and names no commit of the PR: left out (one that
            // names one is a_broken_note_about_a_commit_of_the_pr_is_no_snapshot)
            line: "結論: 通過 頭: 未填".into(),
        },
        FileLine {
            name: "VERIFY-desktop-678-2240e.md".into(),
            line: format!("驗收: 通過 頭: {H678} 機: MacB 樣本: 5 沒驗: 無"),
        },
    ];
    let (reviews, verifies) = lines_for(&files, &[H678.to_owned()]).unwrap();
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
    let full = Known::Unknown("rules: 100 items, may have a next page".into());
    assert_eq!(s.required_cells, full);
    assert_eq!(s.rules_version, full);
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
#[test]
fn a_broken_note_about_a_commit_of_the_pr_is_no_snapshot() {
    let bad = "審查: 通過 頭: 7bcb6ee0 H/M/L/N: 0/0/0/0 範圍: 全文";
    let d = pending_with("broken", &[("REVIEW-678-x-2240e-7bcb6ee0.md", bad)]);
    let e = snap(gh678(), &d).unwrap_err();
    assert_eq!(
        e,
        "REVIEW-678-x-2240e-7bcb6ee0.md: first line label missing"
    );
    let d = pending_with(
        "broken-v",
        &[("VERIFY-gem-678-c477a.md", "驗收 7bcb6ee014c4 看起來可以")],
    );
    let e = snap(gh678(), &d).unwrap_err();
    assert_eq!(e, "VERIFY-gem-678-c477a.md: first line label missing");
    let other = "# old review of 0123456789abcdef";
    let d = pending_with(
        "broken-other",
        &[
            ("REVIEW-1-old-2240e.md", other),
            ("REVIEW-678-x-2240e-7bcb6ee0.md", REV678),
        ],
    );
    let s = snap(gh678(), &d).unwrap();
    assert_eq!(s.prs[0].reviews.len(), 1);
}
