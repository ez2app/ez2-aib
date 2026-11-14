//! Field normalisation and the v1 → v2 upgrade done in memory when an item is read
//! (normalize_* and _upgrade_item in 1.x).

use serde_json::{Map, Value};

use crate::spec::{Status, LEGACY_ITEM_SCHEMAS};
use crate::util::{py_int, py_strip};

pub const KINDS: &[&str] = &[
    "order", "rule", "pipeline", "deliver", "decide", "report", "other", "notice",
];
/// Only these broadcasts may be replaced by a newer one on the same thread.
pub const BROADCAST_KINDS: &[&str] = &["order", "rule", "pipeline"];
/// A reply does not close these (dispatch): they need `close --evidence`.
pub const CLOSE_BY_REPLY_REFUSED_KINDS: &[&str] = &["order", "pipeline"];
pub const PRIORITIES: &[&str] = &["urgent", "important", "normal", "reminder"];
pub const ORIGINS: &[&str] = &["live", "test"];
pub const SENDER_KINDS: &[&str] = &[
    "seat",
    "seat_claimed",
    "human",
    "system",
    "unknown",
    "external_seat",
    "owner_paired",
    "external_im",
];
pub const SENDER_SOURCES: &[&str] = &[
    "pane_pid_chain",
    "ppid_chain",
    "codex_session_meta",
    "codex_thread_env",
    "bg_fork_chain",
];

fn lower(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => py_strip(s).to_lowercase(),
        _ => String::new(),
    }
}

pub fn kind(value: Option<&Value>) -> &'static str {
    let v = lower(value);
    KINDS.iter().copied().find(|k| *k == v).unwrap_or("other")
}

pub fn priority(value: Option<&Value>, default: &'static str) -> &'static str {
    let v = lower(value);
    PRIORITIES
        .iter()
        .copied()
        .find(|k| *k == v)
        .unwrap_or(default)
}

/// Rank for sorting: urgent 0 .. reminder 3.
pub fn priority_rank(p: &str) -> usize {
    PRIORITIES.iter().position(|x| *x == p).unwrap_or(2)
}

/// `origin` given, else the AIB_DM_QUEUE_ORIGIN value the caller read, else `default`.
pub fn origin(
    value: Option<&Value>,
    env_origin: Option<&str>,
    default: &'static str,
) -> &'static str {
    let v = lower(value);
    if let Some(o) = ORIGINS.iter().copied().find(|k| *k == v) {
        return o;
    }
    let e = env_origin
        .map(|s| py_strip(s).to_lowercase())
        .unwrap_or_default();
    if let Some(o) = ORIGINS.iter().copied().find(|k| *k == e) {
        return o;
    }
    if ORIGINS.contains(&default) {
        default
    } else {
        "live"
    }
}

/// v1 wrote "ai" for a seat sender.
pub fn sender_kind(value: Option<&Value>) -> &'static str {
    let v = lower(value);
    if v == "ai" {
        return "seat";
    }
    SENDER_KINDS
        .iter()
        .copied()
        .find(|k| *k == v)
        .unwrap_or("unknown")
}

pub fn sender_source(value: Option<&Value>) -> Option<&'static str> {
    let v = lower(value);
    SENDER_SOURCES.iter().copied().find(|k| *k == v)
}

/// `<1 alnum><0-31 of alnum . _ -></<1-64 non-space non-slash>` (THREAD_RE), or None.
pub fn thread(value: Option<&Value>) -> Option<String> {
    let Some(Value::String(s)) = value else {
        return None;
    };
    let t = py_strip(s);
    let (head, tail) = t.split_once('/')?;
    let mut hb = head.bytes();
    let head_ok = hb.next().is_some_and(|b| b.is_ascii_alphanumeric())
        && head.len() <= 32
        && hb.all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'));
    let n = tail.chars().count();
    let tail_ok =
        (1..=64).contains(&n) && !tail.chars().any(|c| c == '/' || crate::util::py_isspace(c));
    (head_ok && tail_ok).then(|| t.to_string())
}

/// A dispatch dm: its kind, or the kind before a reclass, is order / pipeline.
pub fn is_dispatch(item: &Value) -> bool {
    ["kind", "kind_orig"].iter().any(|k| {
        item.get(*k)
            .filter(|v| crate::util::truthy(Some(*v)))
            .is_some_and(|v| CLOSE_BY_REPLY_REFUSED_KINDS.contains(&kind(Some(v))))
    })
}

/// The v2 fields a v1 item lacks, with their "not measured" defaults (V2_FIELDS).
pub fn v2_defaults() -> Map<String, Value> {
    let mut m = Map::new();
    for k in [
        "thread",
        "sender_seat",
        "human_id",
        "sender_source",
        "sent_at",
        "delivered_at",
        "read_at",
        "done_at",
        "done_evidence",
        "archived_at",
        "archived_why",
        "deleted_at",
        "deleted_by",
        "deleted_why",
        "supersedes",
        "superseded_by",
        "superseded_at",
        "expires",
        "expired",
        "expired_at",
        "expired_why",
        "hold_why",
        "hold_rc",
        "held_at",
    ] {
        m.insert(k.into(), Value::Null);
    }
    m.insert("kind".into(), Value::from("other"));
    m.insert("attempts".into(), Value::from(0));
    m
}

/// _upgrade_item: fill the v2 fields, normalise sender kind / source and kind, take
/// `sent_at` from `ts` when missing, an unknown status reads as queued. Returns the item
/// and whether it was a v1 file. Memory only: the file changes when it is written back.
pub fn upgrade(data: &Map<String, Value>) -> (Map<String, Value>, bool) {
    let mut out = data.clone();
    let legacy = out
        .get("schema")
        .and_then(Value::as_str)
        .is_some_and(|s| LEGACY_ITEM_SCHEMAS.contains(&s));
    for (k, v) in v2_defaults() {
        out.entry(k).or_insert(v);
    }
    let sk = sender_kind(out.get("sender_kind"));
    out.insert("sender_kind".into(), Value::from(sk));
    let ss = sender_source(out.get("sender_source"));
    out.insert("sender_source".into(), ss.map_or(Value::Null, Value::from));
    let k = kind(out.get("kind"));
    out.insert("kind".into(), Value::from(k));
    if crate::util::is_none(out.get("sent_at")) {
        let ts = match out.get("ts") {
            Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
            Some(Value::String(s)) if !s.is_empty() => py_int(s),
            Some(Value::Bool(b)) => Some(i64::from(*b)),
            _ => Some(0),
        };
        out.insert(
            "sent_at".into(),
            ts.filter(|t| *t != 0).map_or(Value::Null, Value::from),
        );
    }
    let st_ok = out
        .get("status")
        .and_then(Value::as_str)
        .and_then(Status::parse)
        .is_some();
    if !st_ok {
        out.insert("status".into(), Value::from("queued"));
    }
    (out, legacy)
}
