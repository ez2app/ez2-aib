//! Item revisions and the confirm-before-change check (item_rev / rev_check in 1.x).
//!
//! Two different numbers carry the name "rev": the item's own `rev` (changes when the
//! content changes, NOT on every status move, so callers that care about the status pass
//! an expected status too) and the per-seat source rev (every write bumps it). This module
//! is only the first one.

use serde_json::Value;

use crate::spec::Reason;
use crate::util::py_int;

/// What `--if-rev` carried.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IfRev {
    Absent,
    Value(i64),
    /// Present but not a whole number: always a mismatch.
    Garbage,
}

impl IfRev {
    pub fn parse(text: Option<&str>) -> IfRev {
        match text {
            None => IfRev::Absent,
            Some(t) => py_int(t).map_or(IfRev::Garbage, IfRev::Value),
        }
    }
}

/// The item as the store found it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Presence {
    /// No readable item file.
    Missing,
    /// The file is there but the item is deleted: gone for every caller.
    Tombstone,
    Live {
        rev: i64,
    },
}

/// `int(item.get("rev") or 0)`, 0 when that would raise.
pub fn item_rev(item: &Value) -> i64 {
    match item.get("rev") {
        Some(Value::Bool(b)) => i64::from(*b),
        Some(Value::Number(n)) => n
            .as_i64()
            .or_else(|| {
                n.as_f64()
                    .filter(|f| f.is_finite() && f.abs() < 9.2e18)
                    .map(|f| f.trunc() as i64)
            })
            .unwrap_or(0),
        Some(Value::String(s)) if !s.is_empty() => py_int(s).unwrap_or(0),
        _ => 0,
    }
}

/// (ok, reason, the rev the caller should retry with). No `--if-rev` is always refused
/// (`no_rev`, fail-closed); only an explicit, logged `--no-rev-check` skips the check, and
/// even then a tombstone is `not_found`.
pub fn rev_check(
    found: Presence,
    if_rev: IfRev,
    no_rev_check: bool,
) -> (bool, Reason, Option<i64>) {
    let cur = match found {
        Presence::Live { rev } => Some(rev),
        _ => None,
    };
    match if_rev {
        IfRev::Absent if no_rev_check => match found {
            Presence::Tombstone => (false, Reason::NotFound, None),
            _ => (true, Reason::Ok, None),
        },
        IfRev::Absent => (false, Reason::NoRev, cur),
        _ if cur.is_none() => (false, Reason::NotFound, None),
        IfRev::Value(want) if Some(want) == cur => (true, Reason::Ok, cur),
        _ => (false, Reason::RevMismatch, cur),
    }
}
