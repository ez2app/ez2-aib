//! N-workspace-c3 (519d0 11-18 21:17, from 0eed7's cardview M1): the one copy of the station
//! display rules. The page and stations-gen read `display_station` / `tag` from aibd instead of
//! working them out themselves.
//!
//! 能看 stage (DECISION-andy-519d0-1118-2320): the shape of the verify-fail record is the
//! simplest one that draws (`verify_fail` on the node, see [`verify_fail_of`]); aib-plan does not
//! write it yet. Listed for 519d0 to settle before the 能用 stage.
//!
//! 1. The old station name 「實查不通過」 shows at station 3 (開發中) with a red 「實查不通過」 tag.
//! 2. A card at 未驗 with an open verify-fail record (H or M not closed) stays at 未驗 and gets a
//!    red 「驗不通過」 tag; it never jumps back by itself (11-17 ruling: a failed check does not
//!    move the station).
//! 3. Both are 「壞了」 (`broken: true`).

use serde_json::{json, Value};

/// The station the old 「實查不通過」 shows at.
const BACK_TO: &str = "開發中";
const OLD_FAIL: &str = "實查不通過";
const UNVERIFIED: &str = "未驗";

/// The display timezone (board-config's `tz_name` / `tz_offset_hours` / `tz_label`), from
/// `AIBD_DISPLAY_TZ` as `<name>,<offset hours>,<label>`; unset or unreadable: Taipei.
pub fn display_tz() -> Value {
    let raw = std::env::var("AIBD_DISPLAY_TZ").unwrap_or_default();
    let mut it = raw.splitn(3, ',');
    let (name, off, label) = (it.next(), it.next(), it.next());
    match (name, off.and_then(|o| o.trim().parse::<i32>().ok()), label) {
        (Some(n), Some(o), Some(l)) if !n.trim().is_empty() && (-12..=14).contains(&o) => {
            json!({"name": n.trim(), "offset_hours": o, "label": l.trim()})
        }
        _ => json!({"name": "Asia/Taipei", "offset_hours": 8, "label": "台北"}),
    }
}

/// The node's open verify-fail record, normalised: `{"closed": false, "sev": "H"|"M"}`, or `None`.
/// Accepted shapes: `true`, or an object with `closed` (missing = open) and `sev` (H or M; L and
/// missing do not count). Anything else is no record.
pub fn verify_fail_of(n: &Value) -> Option<Value> {
    match n.get("verify_fail") {
        Some(Value::Bool(true)) => Some(json!({"closed": false, "sev": "H"})),
        Some(Value::Object(o)) => {
            let closed = o.get("closed").and_then(Value::as_bool).unwrap_or(false);
            let sev = o
                .get("sev")
                .and_then(Value::as_str)
                .filter(|s| matches!(*s, "H" | "M"));
            match (closed, sev) {
                (false, Some(s)) => Some(json!({"closed": false, "sev": s})),
                _ => None,
            }
        }
        _ => None,
    }
}

/// `(display_station, tag, broken)` for one node (`status` is its station).
pub fn display_of(n: &Value) -> (Value, Value, bool) {
    let station = n.get("status").and_then(Value::as_str);
    match station {
        Some(OLD_FAIL) => (
            json!(BACK_TO),
            json!({"text": OLD_FAIL, "tone": "bad"}),
            true,
        ),
        Some(UNVERIFIED) if verify_fail_of(n).is_some() => (
            json!(UNVERIFIED),
            json!({"text": "驗不通過", "tone": "bad"}),
            true,
        ),
        Some(s) => (json!(s), Value::Null, false),
        None => (Value::Null, Value::Null, false),
    }
}

/// Puts `display_station`, `tag`, `broken` and `verify_fail` (normalised, or null) into a view.
pub fn add_to(view: &mut serde_json::Map<String, Value>, n: &Value) {
    let (st, tag, broken) = display_of(n);
    view.insert("display_station".into(), st);
    view.insert("tag".into(), tag);
    view.insert("broken".into(), json!(broken));
    view.insert(
        "verify_fail".into(),
        verify_fail_of(n).unwrap_or(Value::Null),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_old_fail_name_shows_at_station_three_with_its_tag() {
        let (st, tag, broken) = display_of(&json!({"status": "實查不通過"}));
        assert_eq!(st, "開發中");
        assert_eq!(tag["text"], "實查不通過");
        assert!(broken);
    }

    #[test]
    fn an_open_verify_fail_keeps_the_station_and_adds_its_tag() {
        let n = json!({"status": "未驗", "verify_fail": {"sev": "M"}});
        let (st, tag, broken) = display_of(&n);
        assert_eq!(st, "未驗", "reason: jumped_back");
        assert_eq!(tag["text"], "驗不通過");
        assert!(broken);
        // closed, or only L: no tag
        for vf in [
            json!({"sev": "H", "closed": true}),
            json!({"sev": "L"}),
            json!("x"),
        ] {
            let n = json!({"status": "未驗", "verify_fail": vf});
            assert_eq!(display_of(&n).1, Value::Null, "{n}");
        }
        // a record on a card that is not at 未驗 does not tag it
        let n = json!({"status": "開發中", "verify_fail": true});
        assert_eq!(display_of(&n), (json!("開發中"), Value::Null, false));
    }

    #[test]
    fn the_display_timezone_is_taipei_unless_set() {
        // (the env var is unset in tests)
        assert_eq!(display_tz()["offset_hours"], 8);
        assert_eq!(display_tz()["name"], "Asia/Taipei");
    }
}
