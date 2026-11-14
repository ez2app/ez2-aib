//! `aib-mergewatch` slice 1: the merge queue plan, read only.
//!
//! Input is one `aib-slice sweep` object (`aib-slice-sweep.v1`, see
//! `crates/aib-slice/IFACE.md`) and the open PRs' labels (the JSON of
//! `gh pr list --json number,labels`). Output is the order the queue would
//! merge in, and for every PR that cannot go yet, what blocks it. Nothing
//! here merges, pushes, labels or comments: slice 1 is `--plan` only, to be
//! compared with the hand-kept MERGE-QUEUE.md table before it takes over
//! (DEV-mergewatch-rust s15).
//!
//! Rules (MERGE-QUEUE.md 11-14, RULES §20 / S73j):
//! - A PR goes only with: a non-author review whose first line is 通過 with
//!   H/M 0/0 at the PR head (or carried: only merge commits after it), a
//!   green complete receipt at the PR head, no conflict with main, no red
//!   check, and the `ready-merge` label.
//! - A PR that changes a gate file (`new_gate`) goes after the others,
//!   unless the sweep says the `not-gate` demotion is effective (label +
//!   a non-author `非閘理由:` line at head).
//! - Unknown sweep fields are ignored, so a newer sweep still reads (s12).

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// The sweep schema this slice reads.
pub const SCHEMA_IN: &str = "aib-slice-sweep.v1";
/// The plan schema this slice writes.
pub const SCHEMA_OUT: &str = "aib-mergewatch-plan.v1";

/// The labels that matter to the queue.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Labels {
    pub ready: bool,
    pub not_gate: bool,
}

/// One open PR as the queue sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub pr: u64,
    pub head: String,
    pub branch: Option<String>,
    pub seat: Option<String>,
    /// Changes a gate file and is not demoted: goes after the plain ones.
    pub gate: bool,
    pub ready: bool,
    /// Anything here keeps the PR out of the queue.
    pub blockers: Vec<String>,
    /// Worth a look, does not block (carried review, checks still running).
    pub notes: Vec<String>,
}

/// The whole plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub main: String,
    /// False when the sweep could not read everything: do not merge on it.
    pub complete: bool,
    pub errors: Vec<String>,
    /// Ready to go, in merge order.
    pub queue: Vec<Entry>,
    /// Not ready, by PR number.
    pub hold: Vec<Entry>,
}

fn s<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(Value::as_str)
}

fn b(v: &Value, k: &str) -> Option<bool> {
    v.get(k).and_then(Value::as_bool)
}

fn n(v: &Value, k: &str) -> Option<u64> {
    v.get(k).and_then(Value::as_u64)
}

fn strs(v: &Value, k: &str) -> Vec<String> {
    v.get(k)
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn short(h: &str) -> &str {
    h.get(..8).unwrap_or(h)
}

/// Labels from `gh pr list --json number,labels`.
pub fn labels_from_gh(v: &Value) -> Result<BTreeMap<u64, Labels>, String> {
    let list = v.as_array().ok_or("labels: not a list")?;
    let mut out = BTreeMap::new();
    for (i, pr) in list.iter().enumerate() {
        let num = n(pr, "number").ok_or_else(|| format!("labels: item {i}: no number"))?;
        let names: BTreeSet<&str> = pr
            .get("labels")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("labels: #{num}: no labels list"))?
            .iter()
            .filter_map(|l| s(l, "name"))
            .collect();
        out.insert(
            num,
            Labels {
                ready: names.contains("ready-merge"),
                not_gate: names.contains("not-gate"),
            },
        );
    }
    Ok(out)
}

/// What still blocks one sweep row from merging at `head`.
fn judge(row: &Value, head: &str, labels: &Labels, e: &mut Entry) {
    let pr = row.get("pr").unwrap_or(&Value::Null);

    match row.get("review").filter(|r| !r.is_null()) {
        None => e.blockers.push("no review".into()),
        Some(r) => {
            let verdict = s(r, "verdict").unwrap_or("?");
            if verdict != "通過" {
                e.blockers.push(format!("review verdict {verdict}"));
            }
            let (h, m) = (n(r, "h").unwrap_or(u64::MAX), n(r, "m").unwrap_or(u64::MAX));
            if h != 0 || m != 0 {
                e.blockers.push(format!(
                    "review H/M not 0/0 ({})",
                    s(r, "file").unwrap_or("?")
                ));
            }
            let rh = s(r, "head").unwrap_or("");
            if rh != head {
                let after = strs(r, "unreviewed_after");
                if after.is_empty() {
                    e.notes.push(format!(
                        "review carried from {} (merge commits only; check no hand-resolve)",
                        short(rh)
                    ));
                } else {
                    e.blockers.push(format!(
                        "review at {}, {} commit(s) after it unreviewed",
                        short(rh),
                        after.len()
                    ));
                }
            }
        }
    }

    match row.get("receipt").filter(|r| !r.is_null()) {
        None => e.blockers.push("no receipt".into()),
        Some(r) => {
            if s(r, "head").unwrap_or("") != head {
                e.blockers.push(format!(
                    "receipt at {}, not the PR head",
                    short(s(r, "head").unwrap_or("?"))
                ));
            }
            if b(r, "complete") != Some(true) {
                e.blockers.push("receipt incomplete".into());
            }
            if b(r, "sealed") == Some(false) {
                e.blockers.push("receipt seal broken".into());
            }
            let nz = strs(r, "nonzero");
            if !nz.is_empty() {
                e.blockers.push(format!("receipt red: {}", nz.join(",")));
            } else if b(r, "green") != Some(true) && b(r, "complete") == Some(true) {
                e.blockers.push("receipt not green".into());
            }
        }
    }

    match s(pr, "checks") {
        Some("FAILURE") => {
            let red = strs(pr, "red_checks");
            e.blockers.push(if red.is_empty() {
                "checks red".into()
            } else {
                format!("checks red: {}", red.join(","))
            });
        }
        Some("PENDING") | None => e.notes.push("checks not finished".into()),
        Some(_) => {}
    }

    if let Some(c) = row.get("conflicts_with_main").filter(|c| !c.is_null()) {
        if b(c, "conflict") == Some(true) {
            e.blockers.push(format!(
                "conflicts with main: {}",
                strs(c, "files").join(",")
            ));
        }
    } else {
        e.notes.push("not trial-merged with main".into());
    }
    if s(pr, "mergeable") == Some("CONFLICTING") {
        e.blockers.push("GitHub: CONFLICTING".into());
    }

    if !labels.ready {
        e.blockers.push("no ready-merge label".into());
    }
    let new_gate = b(row, "new_gate") == Some(true);
    let demoted = row
        .get("not_gate")
        .map(|g| b(g, "effective") == Some(true))
        .unwrap_or(false);
    e.gate = new_gate && !demoted;
    if new_gate && labels.not_gate && !demoted {
        e.notes
            .push("not-gate label but no 非閘理由 at head: stays a gate".into());
    }
}

/// The queue plan from one sweep and the PR labels.
pub fn plan(sweep: &Value, labels: &BTreeMap<u64, Labels>) -> Result<Plan, String> {
    match s(sweep, "schema") {
        Some(SCHEMA_IN) => {}
        other => return Err(format!("sweep schema {other:?}, want {SCHEMA_IN}")),
    }
    let main = s(sweep, "main").ok_or("sweep: no main")?.to_owned();
    let complete = b(sweep, "complete").unwrap_or(false);
    let errors = strs(sweep, "errors");

    let open: BTreeMap<u64, String> = sweep
        .get("open_prs")
        .and_then(Value::as_array)
        .ok_or("sweep: no open_prs")?
        .iter()
        .filter_map(|p| Some((n(p, "number")?, s(p, "head")?.to_owned())))
        .collect();
    let rows = sweep
        .get("branches")
        .and_then(Value::as_array)
        .ok_or("sweep: no branches")?;

    let mut queue = Vec::new();
    let mut hold = Vec::new();
    for (&num, head) in &open {
        // The row whose head is the PR head; several branches can name one PR.
        let row = rows.iter().find(|r| {
            r.get("pr").and_then(|p| n(p, "number")) == Some(num)
                && s(r, "head") == Some(head.as_str())
        });
        let lab = labels.get(&num).cloned().unwrap_or_default();
        let mut e = Entry {
            pr: num,
            head: head.clone(),
            branch: row.and_then(|r| s(r, "branch")).map(str::to_owned),
            seat: row.and_then(|r| s(r, "seat")).map(str::to_owned),
            gate: false,
            ready: lab.ready,
            blockers: Vec::new(),
            notes: Vec::new(),
        };
        match row {
            None => e.blockers.push("no sweep row at the PR head".into()),
            Some(r) => {
                let base = r.get("pr").and_then(|p| s(p, "base")).unwrap_or("?");
                if base != "main" {
                    e.blockers.push(format!("base is {base}, not main"));
                }
                judge(r, head, &lab, &mut e);
            }
        }
        if e.blockers.is_empty() {
            queue.push(e);
        } else {
            hold.push(e);
        }
    }
    // Plain before gate; inside each, by PR number (the label time comes in slice 2).
    queue.sort_by_key(|e| (e.gate, e.pr));
    Ok(Plan {
        main,
        complete,
        errors,
        queue,
        hold,
    })
}

fn entry_json(e: &Entry) -> Value {
    serde_json::json!({
        "pr": e.pr,
        "head": e.head,
        "branch": e.branch,
        "seat": e.seat,
        "gate": e.gate,
        "ready": e.ready,
        "blockers": e.blockers,
        "notes": e.notes,
    })
}

/// The plan as `aib-mergewatch-plan.v1` JSON.
pub fn to_json(p: &Plan) -> Value {
    serde_json::json!({
        "schema": SCHEMA_OUT,
        "main": p.main,
        "complete": p.complete,
        "errors": p.errors,
        "queue": p.queue.iter().map(entry_json).collect::<Vec<_>>(),
        "hold": p.hold.iter().map(entry_json).collect::<Vec<_>>(),
    })
}

/// The plan as lines a person reads next to MERGE-QUEUE.md.
pub fn to_text(p: &Plan) -> String {
    let mut out = format!(
        "main {}{}\n",
        short(&p.main),
        if p.complete {
            ""
        } else {
            " (sweep incomplete: do not merge on this)"
        }
    );
    for err in &p.errors {
        out.push_str(&format!("  error: {err}\n"));
    }
    for (i, e) in p.queue.iter().enumerate() {
        out.push_str(&format!(
            "{:>2}. #{} {} {}{}\n",
            i + 1,
            e.pr,
            short(&e.head),
            if e.gate { "gate" } else { "plain" },
            if e.notes.is_empty() {
                String::new()
            } else {
                format!("  ({})", e.notes.join("; "))
            }
        ));
    }
    for e in &p.hold {
        out.push_str(&format!(
            "  hold #{} {}: {}\n",
            e.pr,
            short(&e.head),
            e.blockers.join("; ")
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use serde_json::json;

    const H1: &str = "1111111111111111111111111111111111111111";
    const H2: &str = "2222222222222222222222222222222222222222";
    const H3: &str = "3333333333333333333333333333333333333333";
    const OLD: &str = "9999999999999999999999999999999999999999";

    fn row(pr: u64, head: &str) -> Value {
        json!({
            "branch": format!("feat/x-{pr}"), "seat": "abcde", "head": head,
            "pr": {"number": pr, "state": "open", "base": "main", "mergeable": "MERGEABLE",
                   "checks": "SUCCESS", "red_checks": []},
            "review": {"head": head, "verdict": "通過", "h": 0, "m": 0, "l": 1, "n": 2,
                       "file": "REVIEW-x.md", "at_head": true, "unreviewed_after": []},
            "receipt": {"head": head, "file": "RECEIPT-x.txt", "nonzero": [], "green": true,
                        "at_head": true, "steps": ["fmt", "test"], "sealed": true, "complete": true},
            "conflicts_with_main": {"conflict": false, "files": []},
            "new_gate": false, "not_gate": {"label": false, "reason": null, "effective": false},
            "future_field": {"ignored": true}
        })
    }

    fn sweep(rows: Vec<Value>) -> Value {
        let open: Vec<Value> = rows
            .iter()
            .map(|r| json!({"number": r["pr"]["number"], "head_ref": r["branch"], "head": r["head"]}))
            .collect();
        json!({"schema": SCHEMA_IN, "generated_at": 1, "main": OLD, "complete": true,
               "errors": [], "open_prs": open, "branches": rows})
    }

    fn ready(prs: &[u64]) -> BTreeMap<u64, Labels> {
        prs.iter()
            .map(|&p| {
                (
                    p,
                    Labels {
                        ready: true,
                        not_gate: false,
                    },
                )
            })
            .collect()
    }

    #[test]
    fn a_complete_pr_with_the_label_is_queued() {
        let p = plan(&sweep(vec![row(10, H1)]), &ready(&[10])).unwrap();
        assert_eq!(p.queue.len(), 1);
        assert!(p.hold.is_empty());
        assert!(!p.queue[0].gate);
    }

    #[test]
    fn without_the_label_it_is_held() {
        let p = plan(&sweep(vec![row(10, H1)]), &BTreeMap::new()).unwrap();
        assert_eq!(p.hold[0].blockers, vec!["no ready-merge label".to_owned()]);
    }

    #[test]
    fn a_gate_goes_after_the_plain_ones_whatever_its_number() {
        let mut g = row(5, H1);
        g["new_gate"] = json!(true);
        let p = plan(&sweep(vec![g, row(9, H2), row(7, H3)]), &ready(&[5, 7, 9])).unwrap();
        let order: Vec<u64> = p.queue.iter().map(|e| e.pr).collect();
        assert_eq!(order, vec![7, 9, 5]);
        assert!(p.queue[2].gate);
    }

    #[test]
    fn an_effective_not_gate_is_plain_and_a_bare_label_is_not() {
        let mut demoted = row(5, H1);
        demoted["new_gate"] = json!(true);
        demoted["not_gate"] = json!({"label": true, "reason": "只加自家刀", "effective": true});
        let mut bare = row(6, H2);
        bare["new_gate"] = json!(true);
        bare["not_gate"] = json!({"label": true, "reason": null, "effective": false});
        let mut labels = ready(&[5, 6]);
        labels.insert(
            6,
            Labels {
                ready: true,
                not_gate: true,
            },
        );
        let p = plan(&sweep(vec![demoted, bare]), &labels).unwrap();
        assert!(!p.queue[0].gate && p.queue[0].pr == 5);
        assert!(p.queue[1].gate && p.queue[1].notes.iter().any(|n| n.contains("非閘理由")));
    }

    #[test]
    fn a_review_or_receipt_off_the_head_blocks() {
        let mut r = row(10, H1);
        r["review"]["head"] = json!(OLD);
        r["review"]["unreviewed_after"] = json!(["abc"]);
        r["receipt"]["head"] = json!(OLD);
        let p = plan(&sweep(vec![r]), &ready(&[10])).unwrap();
        let b = &p.hold[0].blockers;
        assert!(b.iter().any(|x| x.starts_with("review at 99999999")));
        assert!(b.iter().any(|x| x.starts_with("receipt at 99999999")));
    }

    #[test]
    fn a_review_with_only_merge_commits_after_it_is_carried_not_blocked() {
        let mut r = row(10, H1);
        r["review"]["head"] = json!(OLD);
        let p = plan(&sweep(vec![r]), &ready(&[10])).unwrap();
        assert_eq!(p.queue.len(), 1);
        assert!(p.queue[0].notes.iter().any(|n| n.contains("carried")));
    }

    #[test]
    fn red_checks_conflicts_and_a_bad_review_each_block() {
        let mut r = row(10, H1);
        r["pr"]["checks"] = json!("FAILURE");
        r["pr"]["red_checks"] = json!(["rust (macos-latest)"]);
        r["conflicts_with_main"] =
            json!({"conflict": true, "files": [".github/workflows/aibd.yml"]});
        r["review"]["verdict"] = json!("要改");
        r["review"]["m"] = json!(1);
        let p = plan(&sweep(vec![r]), &ready(&[10])).unwrap();
        let b = p.hold[0].blockers.join("|");
        for want in [
            "checks red: rust (macos-latest)",
            "conflicts with main: .github",
            "review verdict 要改",
            "H/M not 0/0",
        ] {
            assert!(b.contains(want), "{want} missing in {b}");
        }
    }

    #[test]
    fn a_red_or_unfinished_receipt_blocks() {
        let mut red = row(10, H1);
        red["receipt"]["nonzero"] = json!(["test"]);
        red["receipt"]["green"] = json!(false);
        let mut half = row(11, H2);
        half["receipt"]["complete"] = json!(false);
        half["receipt"]["green"] = json!(false);
        let p = plan(&sweep(vec![red, half]), &ready(&[10, 11])).unwrap();
        assert!(p.hold[0].blockers.contains(&"receipt red: test".to_owned()));
        assert!(p.hold[1]
            .blockers
            .contains(&"receipt incomplete".to_owned()));
    }

    #[test]
    fn pending_checks_are_a_note_not_a_blocker() {
        let mut r = row(10, H1);
        r["pr"]["checks"] = json!("PENDING");
        let p = plan(&sweep(vec![r]), &ready(&[10])).unwrap();
        assert_eq!(p.queue.len(), 1);
        assert!(p.queue[0].notes.contains(&"checks not finished".to_owned()));
    }

    #[test]
    fn the_row_must_be_at_the_pr_head_and_based_on_main() {
        let mut stale = row(10, H1);
        stale["head"] = json!(OLD);
        let mut s = sweep(vec![stale]);
        s["open_prs"][0]["head"] = json!(H1);
        let mut other = row(11, H2);
        other["pr"]["base"] = json!("integ/x");
        let mut s2 = sweep(vec![other]);
        let p = plan(&s, &ready(&[10])).unwrap();
        assert_eq!(
            p.hold[0].blockers,
            vec!["no sweep row at the PR head".to_owned()]
        );
        s2["complete"] = json!(false);
        let p2 = plan(&s2, &ready(&[11])).unwrap();
        assert!(!p2.complete);
        assert!(p2.hold[0]
            .blockers
            .contains(&"base is integ/x, not main".to_owned()));
    }

    #[test]
    fn a_wrong_schema_is_refused() {
        let mut s = sweep(vec![]);
        s["schema"] = json!("aib-slice-sweep.v2");
        assert!(plan(&s, &BTreeMap::new()).is_err());
    }

    #[test]
    fn labels_are_read_from_gh_pr_list() {
        let v = json!([{"number": 3, "labels": [{"name": "ready-merge"}, {"name": "not-gate"}]},
                       {"number": 4, "labels": []}]);
        let l = labels_from_gh(&v).unwrap();
        assert_eq!(
            l[&3],
            Labels {
                ready: true,
                not_gate: true
            }
        );
        assert_eq!(l[&4], Labels::default());
        assert!(labels_from_gh(&json!({"number": 1})).is_err());
    }

    #[test]
    fn the_json_and_text_carry_the_order_and_the_reasons() {
        let p = plan(&sweep(vec![row(10, H1), row(11, H2)]), &ready(&[10])).unwrap();
        let j = to_json(&p);
        assert_eq!(j["schema"], SCHEMA_OUT);
        assert_eq!(j["queue"][0]["pr"], 10);
        assert_eq!(j["hold"][0]["pr"], 11);
        let t = to_text(&p);
        assert!(t.contains(" 1. #10 11111111 plain"));
        assert!(t.contains("hold #11 22222222: no ready-merge label"));
    }
}
