//! One report per completion event (DESIGN-drive-expect-wiring §1, §3, §4).
//!
//! Pure layer only: the event, aib-expect's table (as the JSON aib-expect's
//! `gap_json` makes, so this crate does not depend on aib-expect), and what
//! [`decide_weighted`](crate::decide::decide_weighted) would do. Nothing here
//! reads or writes anything; writing the report is the journal's job.
//!
//! - `next`: the first [`NEXT_MAX`] intents that are not notes, in decide's
//!   order. Only decide orders; aib-expect never does.
//! - `ai_order`: an AI may propose another order; it is kept next to the
//!   mechanical one with its reason, never in place of it (1ced7).
//! - A report is not a completion event: [`is_event`] refuses the ids a
//!   report or a journal write would carry, so writing one never starts a
//!   round (§4).
//! - One event, one report: [`already_reported`] says whether the journal
//!   already has a report for this event id.

use crate::decide::Intent;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

/// Most intents a report lists as "next".
pub const NEXT_MAX: usize = 3;
/// Longest event id kept.
pub const EVENT_MAX: usize = 120;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AiOrder {
    /// Indexes into `next`, in the AI's order.
    pub order: Vec<usize>,
    pub why: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Report {
    /// `pr/<n>@<sha40>`, `dm/<id>`, `now/<seat>/<n>` (cleaned).
    pub event: String,
    pub at: u64,
    /// aib-expect's whole table (passing rows too), one JSON object a row.
    pub table: Vec<Value>,
    /// Set when the goals could not be used (aib-expect's code).
    pub expect_unknown: Option<String>,
    pub next: Vec<Intent>,
    pub ai_order: Option<AiOrder>,
    /// Always true in v0.
    pub dry_run: bool,
}

/// The completion events that start a round. Anything else (a report, a
/// suggestion, a journal line) is not one.
pub fn is_event(id: &str) -> bool {
    let Some((kind, rest)) = id.split_once('/') else {
        return false;
    };
    if rest.is_empty() || id.chars().count() > EVENT_MAX {
        return false;
    }
    match kind {
        "pr" => rest.split_once('@').is_some_and(|(n, sha)| {
            !n.is_empty()
                && n.chars().all(|c| c.is_ascii_digit())
                && sha.len() == 40
                && sha.chars().all(|c| c.is_ascii_hexdigit())
        }),
        // every segment non-empty (043dd wiring N1: no `dm//`)
        "dm" | "now" => rest.split('/').all(|seg| {
            !seg.is_empty()
                && seg
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        }),
        _ => false,
    }
}

/// The first [`NEXT_MAX`] intents that are not notes.
pub fn next_of(would: &[Intent]) -> Vec<Intent> {
    would
        .iter()
        .filter(|i| !matches!(i, Intent::Note { .. }))
        .take(NEXT_MAX)
        .cloned()
        .collect()
}

/// The PRs the non-passing rows point at (decide's weights). A row with
/// `"state":"pass"` or no `pr` adds nothing.
pub fn gap_prs(table: &[Value]) -> BTreeSet<u64> {
    table
        .iter()
        .filter(|g| g.get("state").and_then(Value::as_str) != Some("pass"))
        .filter_map(|g| g.get("pr").and_then(Value::as_u64))
        .collect()
}

/// An AI's order is kept only when it is a permutation of `next`'s indexes;
/// its reason is cleaned like any outside text.
pub fn ai_order(next_len: usize, order: Vec<usize>, why: &str) -> Option<AiOrder> {
    let mut seen = order.clone();
    seen.sort_unstable();
    if seen != (0..next_len).collect::<Vec<_>>() {
        return None;
    }
    Some(AiOrder {
        order,
        why: crate::observe::clean(why, crate::observe::MAX_REASON),
    })
}

/// The report for one event; `None` when `event` is not a completion event.
pub fn report(
    event: &str,
    at: u64,
    table: Vec<Value>,
    expect_unknown: Option<String>,
    would: &[Intent],
) -> Option<Report> {
    if !is_event(event) {
        return None;
    }
    Some(Report {
        event: event.to_owned(),
        at,
        table,
        expect_unknown: expect_unknown
            .map(|w| crate::observe::clean(&w, crate::observe::MAX_REASON)),
        next: next_of(would),
        ai_order: None,
        dry_run: true,
    })
}

/// Whether one of the journal's lines already reports `event`.
pub fn already_reported<'a>(lines: impl IntoIterator<Item = &'a str>, event: &str) -> bool {
    lines.into_iter().any(|l| {
        serde_json::from_str::<Value>(l)
            .ok()
            .and_then(|v| {
                v.pointer("/report/event")
                    .and_then(Value::as_str)
                    .map(|e| e == event)
            })
            .unwrap_or(false)
    })
}
