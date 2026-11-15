// SPDX-License-Identifier: Apache-2.0
//! S68b `aib-cireuse reasons`: why cells could not reuse, by reason prefix, over the same
//! judged pushes `week` counts. Rows here are written by `ledger_rows` itself, so the reader
//! and the writer cannot drift apart.

use aib_cireuse::*;
use std::process::Command;

const T0: i64 = 1_778_300_000;

fn cell(job: &str, reasons: &[&str]) -> Cell {
    Cell {
        workflow: "aibd".into(),
        job: job.into(),
        reusable: reasons.is_empty(),
        reasons: reasons.iter().map(|r| (*r).to_owned()).collect(),
        main_seconds: Some(600),
        main_conclusion: Some("success".into()),
        cand_job_id: Some(7),
        cand_run_attempt: Some(1),
        cand_completed_at: Some(T0),
        main_run_id: Some(1),
        main_run_attempt: Some(1),
        main_job_id: Some(2),
        main_image: Some("macos-15 20261110.1".into()),
        cand_tested_sha: Some("m".into()),
    }
}

fn verdict(sha: &str, state: &str, created: i64, cells: Vec<Cell>) -> Verdict {
    Verdict {
        main_sha: sha.into(),
        state: state.into(),
        note: String::new(),
        cells,
        observed_main_wall_minutes: Some(90.0),
        shadow_false_green: None,
        compared_cells: 0,
        uncomparable_cells: vec![],
        main_created_at: Some(created),
        main_tree: Some("t".into()),
    }
}

/// The ledger lines one judgement of `v` at second `judged` writes.
fn lines(v: &Verdict, judged: i64) -> Vec<String> {
    ledger_rows(v, "o/r", judged)
        .iter()
        .map(ToString::to_string)
        .collect()
}

#[test]
fn reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts() {
    let now = T0 + 7 * 86_400;
    let mut l = vec![];
    // push a: first judged with an older reason set, then judged again later: only the later
    // judgement's cells count (same head, the winning push row's `at`)
    l.extend(lines(
        &verdict(
            "a",
            "not_reusable",
            T0 + 100,
            vec![cell("mac", &["candidate_not_completed: run 1 is queued"])],
        ),
        T0 + 200,
    ));
    l.extend(lines(
        &verdict(
            "a",
            "partial",
            T0 + 100,
            vec![
                cell("mac", &["tree_differs: PR tested tree aa != main tree bb"]),
                cell("lin", &[]),
            ],
        ),
        T0 + 900,
    ));
    // push b: one cell with two reasons of different prefixes, another with the same prefix
    // twice (counted once for that cell), and one without a ':' in the reason
    l.extend(lines(
        &verdict(
            "b",
            "not_reusable",
            T0 + 300,
            vec![
                cell(
                    "mac",
                    &[
                        "tree_differs: PR tested tree cc != main tree dd",
                        "finished_after_main: PR job finished 30 s after main's run attempt 1 started",
                    ],
                ),
                cell(
                    "win",
                    &[
                        "image_differs: PR x / main y",
                        "image_differs: again",
                    ],
                ),
                cell("lin", &["duration_unknown"]),
            ],
        ),
        T0 + 400,
    ));
    // push c: a later `unknown` re-judge never replaces its judged row (week's rule), so its
    // cells still count; the unknown row's own cells (none) add nothing
    l.extend(lines(
        &verdict(
            "c",
            "not_reusable",
            T0 + 500,
            vec![cell(
                "mac",
                &["tree_differs: PR tested tree ee != main tree ff"],
            )],
        ),
        T0 + 600,
    ));
    l.extend(lines(&verdict("c", "unknown", T0 + 500, vec![]), T0 + 950));
    // push old: outside the window; push w: only ever `wait`
    l.extend(lines(
        &verdict(
            "old",
            "not_reusable",
            T0 - 10,
            vec![cell("mac", &["steps_differ: x"])],
        ),
        T0,
    ));
    l.extend(lines(
        &verdict(
            "w",
            "wait",
            T0 + 700,
            vec![cell("mac", &["steps_differ: y"])],
        ),
        T0 + 800,
    ));
    l.push("not json".into());

    let r = reasons_summary(&l, now, 7);
    let w = week_summary(&l, now, 7);
    assert_eq!(r.pushes, w.judged, "the same pushes week counts");
    assert_eq!((r.pushes, r.pushes_with_cannot), (3, 3));
    assert_eq!((r.cells, r.cells_cannot, r.cells_no_reason), (6, 5, 0));
    let got: Vec<(&str, usize, usize)> = r
        .by_prefix
        .iter()
        .map(|c| (c.prefix.as_str(), c.cells, c.pushes))
        .collect();
    assert_eq!(
        got,
        vec![
            ("tree_differs", 3, 3),
            ("duration_unknown", 1, 1),
            ("finished_after_main", 1, 1),
            ("image_differs", 1, 1),
        ],
        "most cells first, then by name; the superseded judgement's candidate_not_completed is gone"
    );
    let j = reasons_json(&r, 7);
    assert_eq!(j["by_prefix"][0]["prefix"], "tree_differs");
    assert_eq!(j["cells_cannot"], 5);
    assert!(!j.to_string().contains("could_save"), "{j}");
}

#[test]
fn a_cannot_reuse_cell_without_a_reason_is_counted_apart() {
    let now = T0 + 86_400;
    let mut v = verdict("a", "not_reusable", T0, vec![cell("mac", &["x: y"])]);
    v.cells[0].reasons.clear();
    v.cells[0].reusable = false;
    let r = reasons_summary(&lines(&v, T0 + 10), now, 7);
    assert_eq!((r.cells_cannot, r.cells_no_reason), (1, 1));
    assert!(r.by_prefix.is_empty());
    assert_eq!(reason_prefix("tree_differs: a: b"), "tree_differs");
    assert_eq!(reason_prefix("duration_unknown"), "duration_unknown");
}

#[test]
fn the_reasons_command_reads_the_ledger_folder() {
    let d = std::env::temp_dir().join(format!("aib-cireuse-reasons-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let v = verdict(
        "a",
        "not_reusable",
        now - 3600,
        vec![cell("mac", &["tree_differs: a != b"])],
    );
    std::fs::write(
        d.join("2026-11-15.jsonl"),
        lines(&v, now - 60).join("\n") + "\n",
    )
    .unwrap();
    let bin = env!("CARGO_BIN_EXE_aib-cireuse");
    let out = Command::new(bin)
        .args(["reasons", "--reuse-ledger"])
        .arg(&d)
        .args(["--days", "7"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let j: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(j["days"], 7);
    assert_eq!(j["by_prefix"][0]["prefix"], "tree_differs");
    assert_eq!(j["by_prefix"][0]["cells"], 1);
    let out = Command::new(bin).arg("reasons").output().unwrap();
    assert_eq!(out.status.code(), Some(2), "needs --reuse-ledger");
    let _ = std::fs::remove_dir_all(&d);
}
