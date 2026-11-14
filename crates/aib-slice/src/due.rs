// SPDX-License-Identifier: Apache-2.0
//! S73l 應合未合 / 應修未修(Andy 11-14 13:1x: 「把應合未合、應修未修 也寫成 rust 程式,依序放在
//! 自動建議 的最下方」): the two lists a seat's reply ends with, made from one sweep. Nothing is
//! scanned again here; [`due`] only reads a [`Report`].
//!
//! - 應合未合 = class 1 (merge first) + class 2 (push / open the PR), each with what it still
//!   lacks.
//! - 應修未修 = class 3 (red checks, conflicts, review H / M open, receipt not green), a class
//!   1 / 2 branch that conflicts with main, and an open PR nobody touched for [`IDLE_H`] hours.
//! - 可放棄 (s33), merged and plain in-progress branches are in neither.
//!
//! A sweep that is not complete still prints both lists, under a first line that says so: an
//! unread source is never shown as 「0 件」 (IFACE-s73l).

use crate::{classify, not_gate, Branch, Report};
use serde_json::{json, Value};

/// `--json` schema id.
pub const SCHEMA_ID: &str = "aib-slice-due.v1";
/// An open PR with no commit for this many hours is 應修未修.
pub const IDLE_H: i64 = 72;
/// The label a PR needs before the merge watch takes it.
pub const READY: &str = "ready-merge";

/// One thing a branch lacks (應合未合) or one thing wrong with it (應修未修).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Note {
    pub kind: &'static str,
    /// the fixed wording of IFACE-s73l §3
    pub text: String,
    /// how many (check cells, commits, days …) when the kind counts something
    pub n: Option<u64>,
    /// how long (the longest waiting check cell)
    pub seconds: Option<i64>,
    pub names: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Item {
    pub target: String,
    pub pr: Option<u64>,
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
    /// `review` / `receipt` at the head (passed / green)
    pub has: Vec<&'static str>,
    pub notes: Vec<Note>,
    /// last commit time: the list is oldest first
    pub since: i64,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Due {
    pub merge: Vec<Item>,
    pub fix: Vec<Item>,
}

fn note(kind: &'static str, text: String) -> Note {
    Note {
        kind,
        text,
        ..Note::default()
    }
}

/// Text from outside (branch, check and file names) without control characters.
pub fn clean(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).collect()
}

/// `2 小時 58 分` / `41 分`; under a minute is `不到 1 分`.
pub fn dur(seconds: i64) -> String {
    let m = seconds.max(0) / 60;
    match (m / 60, m % 60) {
        (0, 0) => "不到 1 分".to_owned(),
        (0, m) => format!("{m} 分"),
        (h, m) => format!("{h} 小時 {m} 分"),
    }
}

/// The branch name without `feat/` … in front and `-<seat>-<mmdd>` behind.
pub fn short_name(branch: &str) -> String {
    let last = branch.rsplit('/').next().unwrap_or(branch);
    let parts: Vec<&str> = last.split('-').collect();
    let hex5 = |s: &str| s.len() == 5 && s.bytes().all(|c| c.is_ascii_hexdigit());
    let date = |s: &str| s.len() == 4 && s.bytes().all(|c| c.is_ascii_digit());
    let keep = match parts.as_slice() {
        [head @ .., s, d] if hex5(s) && date(d) && !head.is_empty() => head,
        [head @ .., s] if hex5(s) && !head.is_empty() => head,
        all => all,
    };
    keep.join("-")
}

fn target(b: &Branch) -> String {
    match b.pr.as_ref().filter(|p| p.open) {
        Some(p) => format!("#{} {}", p.number, clean(&short_name(&b.branch))),
        None => clean(&b.branch),
    }
}

fn has(b: &Branch) -> Vec<&'static str> {
    let mut v = Vec::new();
    if b.review.as_ref().is_some_and(|r| r.at_head && r.passed()) {
        v.push("review");
    }
    if b.receipt.as_ref().is_some_and(|r| r.at_head && r.green()) {
        v.push("receipt");
    }
    v
}

/// A check-cell note: `CI 3 格排隊 2 小時 58 分`; without a start time only the count.
fn cells(kind: &'static str, word: &str, w: (u32, Option<i64>), now: i64) -> Note {
    let secs = w.1.map(|t| (now - t).max(0));
    let how_long = secs.map_or(String::new(), |s| format!(" {}", dur(s)));
    Note {
        kind,
        text: format!("CI {} 格{word}{how_long}", w.0),
        n: Some(u64::from(w.0)),
        seconds: secs,
        names: Vec::new(),
    }
}

/// What a class 1 / 2 branch still lacks before it can merge.
fn lacks(b: &Branch, r: &Report, now: i64) -> Vec<Note> {
    let mut v = Vec::new();
    let pr = b.pr.as_ref().filter(|p| p.open);
    if b.pushed_to.is_empty() {
        v.push(note("not_pushed", "沒推".into()));
    }
    if pr.is_none() {
        v.push(note("no_pr", "沒開 PR".into()));
    }
    match &b.review {
        Some(r) if r.at_head => {}
        Some(r) => v.push(Note {
            n: u64::try_from(r.unreviewed_after.len()).ok(),
            ..note(
                "review_behind",
                format!(
                    "審查頭不在現在的頭(之後 {} 個 commit 沒審)",
                    r.unreviewed_after.len()
                ),
            )
        }),
        None => v.push(note("no_review", "沒人審".into())),
    }
    match &b.receipt {
        Some(r) if r.at_head => {}
        Some(_) => v.push(note("receipt_behind", "收據不在現在的頭".into())),
        None => v.push(note("no_receipt", "沒有收據".into())),
    }
    if let Some(p) = pr {
        if !p.labels.iter().any(|l| l == READY) {
            v.push(note("no_ready_merge", format!("沒貼 {READY}")));
        }
        if let Some(q) = p.queued.filter(|q| q.0 > 0) {
            v.push(cells("ci_queued", "排隊", q, now));
        }
        if let Some(r) = p.running.filter(|r| r.0 > 0) {
            v.push(cells("ci_running", "在跑", r, now));
        }
        // stopped by the merge queue or a newer event: not red, it has to run again
        if let Some(c) = p.cancelled.filter(|c| *c > 0) {
            v.push(Note {
                n: Some(u64::from(c)),
                ..note("ci_cancelled", format!("CI {c} 格被取消(等重跑)"))
            });
        }
    }
    for under in &b.stacked_on {
        // the open PR of the branch below: from the swept branches, else from the open PR
        // table (with --seat another seat's branch is not swept; 043dd S73l review L1)
        let n = r
            .branches
            .iter()
            .find(|x| &x.branch == under)
            .and_then(|x| x.pr.as_ref())
            .filter(|p| p.open)
            .map(|p| p.number)
            .or_else(|| {
                r.open_prs
                    .iter()
                    .find(|p| &p.head_ref == under)
                    .map(|p| p.number)
            });
        let what = n.map_or_else(|| clean(under), |n| format!("#{n}"));
        v.push(Note {
            n,
            ..note("waits_pr", format!("等 {what} 先合"))
        });
    }
    if !b.gate_files.is_empty() && !not_gate(b).2 {
        v.push(note("new_gate", "新閘:排同批最後".into()));
    }
    v
}

fn main_conflict(b: &Branch) -> bool {
    b.conflicts_with_main.as_ref().is_some_and(|c| c.conflict)
}

fn idle(b: &Branch) -> bool {
    b.pr.as_ref().is_some_and(|p| p.open) && b.age_h > IDLE_H
}

fn files(f: &[String], max: usize) -> String {
    f.iter()
        .take(max)
        .map(|x| clean(x))
        .collect::<Vec<_>>()
        .join(", ")
}

/// What is wrong with a branch (class 3, a conflict with main, an idle PR).
fn problems(b: &Branch) -> Vec<Note> {
    let mut v = Vec::new();
    let pr = b.pr.as_ref().filter(|p| p.open);
    if pr.is_some() {
        for (n, f) in &b.conflicts_with {
            v.push(Note {
                n: Some(*n),
                names: f.clone(),
                ..note("conflict_pr", format!("跟 #{n} 衝突({})", files(f, 2)))
            });
        }
    }
    if main_conflict(b) {
        v.push(note("conflict_main", "跟 main 衝突".into()));
    }
    if let Some(red) = pr
        .filter(|p| p.checks.as_deref() == Some("FAILURE"))
        .and_then(|p| p.red_checks.as_ref())
    {
        v.push(Note {
            n: u64::try_from(red.len()).ok(),
            names: red.clone(),
            ..note(
                "ci_red",
                format!("CI {} 格紅({})", red.len(), files(red, 3)),
            )
        });
    }
    if let Some(r) = b.review.as_ref().filter(|r| r.at_head && r.h + r.m > 0) {
        v.push(note(
            "review_open",
            format!("審查中度 {} / 嚴重 {} 未修", r.m, r.h),
        ));
    }
    if let Some(r) = b.receipt.as_ref().filter(|r| r.at_head) {
        if r.sealed == Some(false) {
            v.push(note("receipt_seal", "收據封口不符".into()));
        } else if !r.finished() {
            v.push(note("receipt_red", "收據不綠(沒跑完)".into()));
        } else if !r.green() {
            v.push(Note {
                names: r.nonzero.clone(),
                ..note("receipt_red", format!("收據不綠({})", files(&r.nonzero, 3)))
            });
        }
    }
    if idle(b) {
        let days = b.age_h / 24;
        v.push(Note {
            n: u64::try_from(days).ok(),
            ..note("idle_pr", format!("開著 {days} 天沒動"))
        });
    }
    v
}

fn item(b: &Branch, notes: Vec<Note>) -> Item {
    Item {
        target: target(b),
        pr: b.pr.as_ref().filter(|p| p.open).map(|p| p.number),
        branch: b.branch.clone(),
        seat: b.seat.clone(),
        head: b.head.clone(),
        has: has(b),
        notes,
        since: b.last_commit_at,
    }
}

/// The two lists of one sweep, each oldest first. A head listed under two names (the local
/// branch and its pushed copy) is one item: the copy with an open PR, else the first.
pub fn due(r: &Report, hours: i64, now: i64) -> Due {
    let mut seen: Vec<&Branch> = Vec::new();
    for b in &r.branches {
        match seen.iter().position(|x| x.head == b.head) {
            Some(at) => {
                let open = |x: &Branch| x.pr.as_ref().is_some_and(|p| p.open);
                if open(b) && !open(seen[at]) {
                    seen[at] = b;
                }
            }
            None => seen.push(b),
        }
    }
    let mut d = Due::default();
    for b in seen {
        let class = classify(b, hours).0;
        let to_fix = class == 3 || (matches!(class, 1 | 2) && main_conflict(b));
        if to_fix || (class == 4 && idle(b)) {
            d.fix.push(item(b, problems(b)));
        } else if matches!(class, 1 | 2) {
            d.merge.push(item(b, lacks(b, r, now)));
        }
    }
    d.merge.sort_by_key(|i| i.since);
    d.fix.sort_by_key(|i| i.since);
    d
}

fn head8(h: &str) -> &str {
    h.get(..8).unwrap_or(h)
}

fn section(out: &mut String, title: &str, items: &[Item], max: usize, merge: bool) {
    out.push_str(&format!("{title}({} 件):\n", items.len()));
    for i in items.iter().take(max) {
        let notes = i
            .notes
            .iter()
            .map(|n| n.text.as_str())
            .collect::<Vec<_>>()
            .join(";");
        if merge {
            let have = match i.has.as_slice() {
                ["review", "receipt"] => "審過 + 收據齊",
                ["review"] => "審過",
                ["receipt"] => "收據齊",
                _ => "都沒有",
            };
            let lacks = if notes.is_empty() {
                "可以合了".to_owned()
            } else {
                format!("差:{notes}")
            };
            out.push_str(&format!(
                "- {} · {} · {have} · {lacks}\n",
                i.target,
                head8(&i.head)
            ));
        } else {
            out.push_str(&format!("- {} · {} · {notes}\n", i.target, head8(&i.head)));
        }
    }
    if items.len() > max {
        out.push_str(&format!("還有 {} 件\n", items.len() - max));
    }
}

/// The plain text of IFACE-s73l §3. `errors` not empty ⇒ the first line says the sweep is
/// not complete (the lists may lack items; they are never shown as nothing).
pub fn render(d: &Due, errors: &[String], max: usize) -> String {
    let mut out = String::new();
    if let Some(first) = errors.first() {
        out.push_str(&format!(
            "掃描不完整,以下可能有缺:{}(共 {} 個)\n",
            clean(first),
            errors.len()
        ));
    }
    section(&mut out, "應合未合", &d.merge, max, true);
    section(&mut out, "應修未修", &d.fix, max, false);
    out
}

fn notes_json(notes: &[Note]) -> Value {
    notes
        .iter()
        .map(|n| {
            json!({"kind": n.kind, "text": n.text, "n": n.n, "seconds": n.seconds, "names": n.names})
        })
        .collect()
}

fn items_json(items: &[Item], max: usize, key: &str) -> Value {
    let list: Vec<Value> = items
        .iter()
        .take(max)
        .map(|i| {
            let mut v = json!({
                "target": i.target, "pr": i.pr, "branch": i.branch, "seat": i.seat,
                "head": i.head, "has": i.has, "since": i.since,
            });
            v[key] = notes_json(&i.notes);
            v
        })
        .collect();
    json!({"total": items.len(), "items": list})
}

/// `aib-slice-due.v1` (schema/due.v1.json).
pub fn to_json(d: &Due, r: &Report, seat: Option<&str>, max: usize) -> Value {
    json!({
        "schema": SCHEMA_ID,
        "generated_at": r.generated_at,
        "complete": r.errors.is_empty(),
        "errors": r.errors,
        "seat": seat,
        "merge": items_json(&d.merge, max, "lacks"),
        "fix": items_json(&d.fix, max, "problems"),
    })
}

/// Seconds since 1970 of `YYYY-MM-DDTHH:MM:SSZ` (what GitHub gives); None when it is not that.
pub fn epoch_of(iso: &str) -> Option<i64> {
    let b = iso.as_bytes();
    if b.len() != 20 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[19] != b'Z' {
        return None;
    }
    let num = |from: usize, to: usize| iso.get(from..to)?.parse::<i64>().ok();
    let (y, m, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let (hh, mm, ss) = (num(11, 13)?, num(14, 16)?, num(17, 19)?);
    if !(1..=12).contains(&m) || hh > 23 || mm > 59 || ss > 60 {
        return None;
    }
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let last = match m {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    // 043dd S73l review N1: the day must exist in that month
    if !(1..=last).contains(&d) {
        return None;
    }
    // days from the civil date (proleptic Gregorian)
    let y2 = if m <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2.rem_euclid(400);
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + hh * 3600 + mm * 60 + ss)
}

/// Check cells of one commit that have not finished: (queued, running, cancelled). queued and
/// running are (how many, the earliest `started_at`; None when no cell gives one); GitHub's
/// `started_at` is when the job was made, so 「在跑 X」 counts from then too (043dd N2).
/// cancelled = cells whose newest run was cancelled (they have to run again). Only the newest
/// run of each name counts. None = no check runs to read.
#[allow(clippy::type_complexity)]
pub fn waiting(v: &Value) -> Option<((u32, Option<i64>), (u32, Option<i64>), u32)> {
    let runs = crate::scan::latest_runs(v)?;
    let (mut q, mut r) = ((0_u32, None::<i64>), (0_u32, None::<i64>));
    let mut cancelled = 0_u32;
    for c in runs {
        let slot = match c.get("status").and_then(Value::as_str) {
            Some("completed") => {
                if c.get("conclusion").and_then(Value::as_str) == Some("cancelled") {
                    cancelled += 1;
                }
                continue;
            }
            None => continue,
            Some("in_progress") => &mut r,
            Some(_) => &mut q,
        };
        slot.0 += 1;
        let at = c
            .get("started_at")
            .and_then(Value::as_str)
            .and_then(epoch_of);
        slot.1 = match (slot.1, at) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
    }
    Some((q, r, cancelled))
}

// tests: tests/due.rs (the lists) and the unit tests below (text helpers)
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn durations_names_and_times() {
        assert_eq!(dur(10_680), "2 小時 58 分");
        assert_eq!(dur(41 * 60 + 59), "41 分");
        assert_eq!(dur(7200), "2 小時 0 分");
        assert_eq!(dur(30), "不到 1 分");
        assert_eq!(dur(-5), "不到 1 分");
        assert_eq!(short_name("feat/aib-idea-043dd-1114"), "aib-idea");
        assert_eq!(short_name("integ-src/slice-sweep-1ced7"), "slice-sweep");
        assert_eq!(short_name("main"), "main");
        assert_eq!(short_name("fix/1ced7-1114"), "1ced7-1114");
        assert_eq!(clean("a\u{1b}[31mb\n"), "a[31mb");
        assert_eq!(epoch_of("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(epoch_of("2026-11-14T03:34:20Z"), Some(1_794_627_260));
        assert_eq!(epoch_of("2024-02-29T12:00:00Z"), Some(1_709_208_000));
        for bad in [
            "",
            "2026-11-14 03:34:20Z",
            "2026-13-14T03:34:20Z",
            "2026-11-14T03:34:20+08",
        ] {
            assert_eq!(epoch_of(bad), None, "{bad}");
        }
    }

    #[test]
    fn check_cells_that_wait_are_counted_by_kind() {
        let v = json!({"check_runs": [
            {"name": "a", "status": "queued", "started_at": "2026-11-14T03:00:00Z"},
            {"name": "b", "status": "queued", "started_at": "2026-11-14T02:00:00Z"},
            {"name": "c", "status": "pending"},
            {"name": "d", "status": "in_progress", "started_at": "2026-11-14T04:00:00Z"},
            {"name": "e", "status": "completed", "conclusion": "success"},
            // stopped by the merge queue and not run again yet
            {"name": "f", "status": "completed", "conclusion": "cancelled",
             "started_at": "2026-11-14T03:34:20Z"},
            // cancelled, then the same cell ran again and passed: only the newer run counts
            {"name": "g", "status": "completed", "conclusion": "cancelled",
             "started_at": "2026-11-14T03:34:20Z", "id": 1},
            {"name": "g", "status": "completed", "conclusion": "success",
             "started_at": "2026-11-14T03:34:29Z", "id": 2},
        ]});
        let t = |s: &str| epoch_of(s);
        assert_eq!(
            waiting(&v),
            Some((
                (3, t("2026-11-14T02:00:00Z")),
                (1, t("2026-11-14T04:00:00Z")),
                1
            ))
        );
        assert_eq!(
            waiting(&json!({"check_runs": []})),
            Some(((0, None), (0, None), 0))
        );
        assert_eq!(waiting(&json!({})), None);
    }

    /// 1ced7 r3: #710 showed 6 cancelled cells of a run the label event replaced; the sweep
    /// called the PR red.
    #[test]
    fn a_cancelled_cell_is_not_red_and_an_older_run_of_a_cell_does_not_count() {
        use crate::scan::rollup;
        let run = |name: &str, conclusion: &str, at: &str| {
            json!({"name": name, "status": "completed", "conclusion": conclusion,
                   "started_at": at})
        };
        let (old, new) = ("2026-11-14T03:34:20Z", "2026-11-14T03:34:29Z");
        // cancelled and not run again: it still has to run, so pending, nothing red
        let v = json!({"check_runs": [run("smoke", "cancelled", old), run("fmt", "success", old)]});
        assert_eq!(rollup(&v), Some(("PENDING".to_owned(), vec![])));
        // the same cell ran again and passed
        let v =
            json!({"check_runs": [run("smoke", "cancelled", old), run("smoke", "success", new)]});
        assert_eq!(rollup(&v), Some(("SUCCESS".to_owned(), vec![])));
        // an old failure replaced by a pass is not red; a new failure is
        let v = json!({"check_runs": [run("smoke", "failure", old), run("smoke", "success", new)]});
        assert_eq!(rollup(&v), Some(("SUCCESS".to_owned(), vec![])));
        let v = json!({"check_runs": [run("smoke", "success", old), run("smoke", "failure", new)]});
        assert_eq!(
            rollup(&v),
            Some(("FAILURE".to_owned(), vec!["smoke".to_owned()]))
        );
        assert_eq!(epoch_of("2026-02-31T00:00:00Z"), None, "043dd N1");
        assert_eq!(epoch_of("2024-02-29T00:00:00Z"), Some(1_709_164_800));
        assert_eq!(epoch_of("2026-02-29T00:00:00Z"), None);
    }
}
