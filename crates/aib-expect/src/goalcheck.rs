//! `check-goal`: does this seat's current stage name a goal the human
//! approved? (RULES-seats-aib2 §17, DECISION-goalcheck-gate-a63ab-1112.)
//!
//! `aib now --goal A8|E6|none` records the goal in
//! `<now-dir>/<seat>.json` (`goal`, `goal_at`, `goal_why` for `none`) and
//! in each history row of `<now-dir>/<seat>.history.jsonl` (`goal`). This
//! module only judges what a machine can know for sure; whether the work
//! really serves the goal is not decided here (the decision's "形式合格冒充
//! 方向正確" risk: a seat can still name a wrong but live goal).
//!
//! Codes and exit codes:
//! - rc 4, may later block (the decision: only these two):
//!   `goal_missing` (no record, no goal, or `none` without a reason),
//!   `goal_unknown` (not on the card and never in the evidence).
//! - rc 5, a reminder only, never a block:
//!   `goal_retired` (not on the card, but the evidence names it: a goal
//!   taken off the card), `goal_done` (its row is 實查通過 already),
//!   `goal_late` (the stage started without a goal and got one later),
//!   `pr_goal_mismatch` (the item names `#N` and the evidence binds #N only
//!   to other goals).
//! - rc 3: the goals or the record cannot be used (the caller lets it pass).
//! - rc 0: `ok`.
//!
//! Always offline: PR heads are never read, so a PR-bound row cannot be
//! 實查通過 here and `goal_done` is only found for rows without a PR.

use crate::{EvidenceLog, Goal, Report, Status};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::Path;

/// What `aib now` recorded for the current stage.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Stage {
    pub item: String,
    pub started_at: Option<u64>,
    pub goal: Option<String>,
    pub goal_why: Option<String>,
}

/// One history row (only the fields used here).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HistoryRow {
    pub started_at: Option<u64>,
    pub goal: Option<String>,
}

/// The result: every code found (in a fixed order), the normalised goal,
/// its row's status and the PRs the item names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub goal: Option<String>,
    pub codes: Vec<&'static str>,
    pub status: Option<&'static str>,
    pub prs: Vec<u64>,
}

/// Codes that may later block (rc 4).
pub const BLOCKABLE: &[&str] = &["goal_missing", "goal_unknown"];

impl Verdict {
    pub fn rc(&self) -> u8 {
        if self.codes.iter().any(|c| BLOCKABLE.contains(c)) {
            4
        } else if self.codes.is_empty() {
            0
        } else {
            5
        }
    }

    pub fn to_json(&self) -> Value {
        json!({
            "goal": self.goal,
            "code": self.codes.first().copied().unwrap_or("ok"),
            "codes": self.codes,
            "status": self.status,
            "prs": self.prs,
            "rc": self.rc(),
        })
    }
}

/// `A8`, `a08`, `A4B` ⇒ `A8`, `A8`, `A4b`; `none` ⇒ `none`; anything else
/// (more than one suffix letter, no digits, non-ASCII digits) ⇒ `None`. The
/// same form `aib now --goal` writes and `goal_id` reads off the card.
pub fn norm_goal(s: &str) -> Option<String> {
    let s = s.trim();
    if s.eq_ignore_ascii_case("none") {
        return Some("none".to_owned());
    }
    let mut cs = s.chars();
    let head = cs.next()?.to_ascii_uppercase();
    if !head.is_ascii_uppercase() {
        return None;
    }
    let rest: Vec<char> = cs.collect();
    let n = rest.iter().take_while(|c| c.is_ascii_digit()).count();
    let digits: String = rest[..n].iter().collect();
    let tail = &rest[n..];
    if n == 0 || tail.len() > 1 || tail.iter().any(|c| !c.is_ascii_alphabetic()) {
        return None;
    }
    let num: u64 = digits.parse().ok()?;
    let suffix: String = tail.iter().map(|c| c.to_ascii_lowercase()).collect();
    Some(format!("{head}{num}{suffix}"))
}

/// The `#N` PR numbers in a line of text, in order, without repeats.
pub fn prs_named(text: &str) -> Vec<u64> {
    let mut out = Vec::new();
    let b = text.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'#' {
            let j = b[i + 1..].iter().take_while(|c| c.is_ascii_digit()).count();
            if j > 0 {
                if let Ok(n) = text[i + 1..i + 1 + j].parse::<u64>() {
                    if !out.contains(&n) {
                        out.push(n);
                    }
                }
            }
            i += 1 + j;
        } else {
            i += 1;
        }
    }
    out
}

/// Judge one stage. `stage` is `None` when the seat has no record at all.
pub fn check(
    evidence: &EvidenceLog,
    report: &Report,
    stage: Option<&Stage>,
    history: &[HistoryRow],
) -> Verdict {
    let mut v = Verdict {
        goal: None,
        codes: Vec::new(),
        status: None,
        prs: Vec::new(),
    };
    let Some(st) = stage else {
        v.codes.push("goal_missing");
        return v;
    };
    v.prs = prs_named(&st.item);
    // an empty goal is no goal (review 1ced7-1113 L1)
    let named = st.goal.as_deref().filter(|g| !g.trim().is_empty());
    let Some(goal) = named.and_then(norm_goal) else {
        v.codes.push(if named.is_some() {
            "goal_unknown"
        } else {
            "goal_missing"
        });
        return v;
    };
    v.goal = Some(goal.clone());
    // late: the first history row of this stage had no goal
    let late = st.started_at.is_some()
        && history
            .iter()
            .find(|r| r.started_at == st.started_at)
            .is_some_and(|r| r.goal.is_none());
    if goal == "none" {
        if st.goal_why.as_deref().is_none_or(|w| w.trim().is_empty()) {
            v.codes.push("goal_missing");
        } else if late {
            v.codes.push("goal_late");
        }
        return v;
    }
    let card_id = |g: &Goal| norm_goal(&g.id).unwrap_or_else(|| g.id.clone());
    let Some(row) = report.rows.iter().find(|r| card_id(&r.goal) == goal) else {
        let named = evidence
            .lines
            .iter()
            .any(|e| norm_goal(&e.goal).as_deref() == Some(goal.as_str()));
        v.codes.push(if named {
            "goal_retired"
        } else {
            "goal_unknown"
        });
        return v;
    };
    v.status = Some(row.status.label());
    if row.status == Status::Pass {
        v.codes.push("goal_done");
    }
    if late {
        v.codes.push("goal_late");
    }
    let mismatch = v.prs.iter().any(|n| {
        let bound: BTreeSet<String> = evidence
            .lines
            .iter()
            .filter(|e| e.pr == Some(*n))
            .map(|e| norm_goal(&e.goal).unwrap_or_else(|| e.goal.clone()))
            .collect();
        !bound.is_empty() && !bound.contains(&goal)
    });
    if mismatch {
        v.codes.push("pr_goal_mismatch");
    }
    v
}

/// `<seat>.json` as written by `aib now`; `Err` when it is not an object.
pub fn parse_stage(v: &Value) -> Result<Stage, String> {
    let o = v.as_object().ok_or("the seat record is not an object")?;
    let s = |k: &str| o.get(k).and_then(Value::as_str).map(str::to_owned);
    Ok(Stage {
        item: s("item").unwrap_or_default(),
        started_at: o.get("started_at").and_then(Value::as_u64),
        goal: s("goal"),
        goal_why: s("goal_why"),
    })
}

/// History rows; lines that cannot be read are skipped (a row that cannot be
/// read never makes a stage late).
pub fn parse_history(text: &str) -> Vec<HistoryRow> {
    text.lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter_map(|v| {
            let o = v.as_object()?;
            Some(HistoryRow {
                started_at: o.get("started_at").and_then(Value::as_u64),
                goal: o.get("goal").and_then(Value::as_str).map(str::to_owned),
            })
        })
        .collect()
}

/// The seat's record and history from `<now_dir>/<seat>.json` and
/// `<seat>.history.jsonl`. No record file ⇒ `Ok((None, …))` (never wrote
/// one: `goal_missing`); a file that cannot be read ⇒ `Err` (rc 3).
pub fn read_seat(now_dir: &Path, seat: &str) -> Result<(Option<Stage>, Vec<HistoryRow>), String> {
    if !seat_ok(seat) {
        return Err(format!("bad seat code {seat:?}"));
    }
    let rec = now_dir.join(format!("{seat}.json"));
    // a stat that fails is unreadable (rc 3), not a missing record (review L4)
    let there = |p: &Path| p.try_exists().map_err(|e| format!("{}: {e}", p.display()));
    let stage = if there(&rec)? {
        let t = crate::read_capped(&rec, crate::MAX_CARD_BYTES)
            .map_err(|e| format!("{}: {e}", rec.display()))?;
        let v: Value = serde_json::from_str(&t).map_err(|e| format!("{}: {e}", rec.display()))?;
        Some(parse_stage(&v)?)
    } else {
        None
    };
    let hist = now_dir.join(format!("{seat}.history.jsonl"));
    let history = if there(&hist)? {
        let t = crate::read_capped(&hist, crate::MAX_CARD_BYTES)
            .map_err(|e| format!("{}: {e}", hist.display()))?;
        parse_history(&t)
    } else {
        Vec::new()
    };
    Ok((stage, history))
}

/// Whether a command may read PR heads over the network. `check-goal` runs
/// in the Stop hook and never does, `--offline` or not.
pub fn reads_pr_heads(cmd: &str, offline: bool) -> bool {
    !offline && cmd != "check-goal"
}

/// A seat code: 1-16 ASCII lowercase letters or digits (never a path).
pub fn seat_ok(code: &str) -> bool {
    !code.is_empty()
        && code.len() <= 16
        && code
            .chars()
            .all(|c| c.is_ascii_digit() || c.is_ascii_lowercase())
}
