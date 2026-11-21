//! D7b (PREREAD-drive-d7d5 r0, 5d33a 0/0/2/1; 1a93b's drive_status shape): what the drive
//! will do next, what it sent, what holds it and how many wait for the owner, beside the
//! dispatch switch. Read from aib-drive's own files under `<1.x root>/drive`; those are files
//! any AI on this account can write, so this is a view and decides nothing (the switch and
//! the send path keep their own checks). Each list is cut short and each file read only up to
//! a cap; a file that is missing or not readable is that list empty with its name in `unread`.

use serde_json::{json, Value};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

/// The most rows of each list shown.
pub const ROWS_MAX: usize = 10;
/// The most work orders sent shown (newest first).
pub const SENT_MAX: usize = 20;
/// A whole file read at most this long; the intents journal only its last this many bytes.
pub const READ_MAX: u64 = 1024 * 1024;

fn read_json(p: &Path) -> Option<Value> {
    let f = std::fs::File::open(p).ok()?;
    let mut t = String::new();
    f.take(READ_MAX).read_to_string(&mut t).ok()?;
    serde_json::from_str(&t).ok()
}

/// The last [`READ_MAX`] bytes of `p`, from its first whole line.
fn tail(p: &Path) -> Option<String> {
    let mut f = std::fs::File::open(p).ok()?;
    let len = f.metadata().ok()?.len();
    let from = len.saturating_sub(READ_MAX);
    f.seek(SeekFrom::Start(from)).ok()?;
    let mut b = Vec::new();
    f.read_to_end(&mut b).ok()?;
    let t = String::from_utf8_lossy(&b).into_owned();
    Some(if from > 0 {
        t.split_once('\n')
            .map(|(_, r)| r.to_owned())
            .unwrap_or_default()
    } else {
        t
    })
}

fn s(v: &Value, k: &str) -> Value {
    v.get(k)
        .filter(|x| x.is_string() || x.is_number() || x.is_boolean())
        .cloned()
        .unwrap_or(Value::Null)
}

fn pick(v: &Value, keys: &[&str]) -> Value {
    Value::Object(keys.iter().map(|k| ((*k).to_owned(), s(v, k))).collect())
}

/// The work orders the intents journal says went out (a `done` line whose `intent` line is a
/// dispatch), newest first: key, to, stage, dm id, when.
fn dispatches(journal: &str) -> Vec<Value> {
    let mut intent: std::collections::HashMap<String, Value> = Default::default();
    let mut out = Vec::new();
    for l in journal.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        let key = v
            .get("key")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        match v.get("kind").and_then(Value::as_str) {
            Some("intent") => {
                intent.insert(key, v);
            }
            Some("done") => {
                if let Some(i) = intent.get(&key) {
                    if i.get("action").and_then(Value::as_str) == Some("dispatch") {
                        out.push(json!({
                            "key": key,
                            "to": s(i, "to"),
                            "first_line": s(i, "first_line"),
                            "stage": s(&v, "stage"),
                            "dm_id": s(&v, "dm_id"),
                            "at_ms": s(&v, "at_ms"),
                        }));
                    }
                }
            }
            _ => {}
        }
    }
    out.reverse();
    out.truncate(SENT_MAX);
    out
}

/// The view of the drive folder `drive`.
pub fn view(drive: &Path) -> Value {
    let mut unread = Vec::new();
    let next = read_json(&drive.join("act-next.json"));
    if next.is_none() {
        unread.push("act-next.json");
    }
    let next = next.unwrap_or(Value::Null);
    let rows = |v: &Value, k: &str, keys: &[&str]| -> Vec<Value> {
        v.get(k)
            .and_then(Value::as_array)
            .map(|a| a.iter().take(ROWS_MAX).map(|r| pick(r, keys)).collect())
            .unwrap_or_default()
    };
    let andy_n = next
        .get("for_andy")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    let unblock = read_json(&drive.join("unblock.json"));
    if unblock.is_none() {
        unread.push("unblock.json");
    }
    let journal = tail(&drive.join("intents.jsonl"));
    if journal.is_none() {
        unread.push("intents.jsonl");
    }
    json!({
        "next_as_of": s(&next, "as_of"),
        "next_complete": s(&next, "complete"),
        "next": rows(&next, "rows", &["rank", "kind", "what", "to", "why_now", "needs", "waited_min"]),
        "dispatch": dispatches(journal.as_deref().unwrap_or_default()),
        "blockers": rows(&unblock.unwrap_or(Value::Null), "rows", &["code", "subject", "what", "blocks", "owner"]),
        "andy_count": andy_n,
        "andy": rows(&next, "for_andy", &["id", "title", "unblocks"]),
        "unread": unread,
    })
}

/// The view under this machine's 1.x root, `None` when there is no root.
pub fn view_now() -> Option<Value> {
    Some(view(&crate::slots::state_root()?.join("drive")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_view_shows_next_the_work_orders_sent_and_what_holds_the_drive() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        let rows: Vec<Value> = (0..15)
            .map(|i| json!({"rank": i, "kind": "push", "what": format!("w{i}"), "to": "7ebca", "secret": "x"}))
            .collect();
        std::fs::write(
            p.join("act-next.json"),
            json!({"as_of": "t", "complete": true, "rows": rows, "for_andy": [{"id": "i_1", "title": "決定", "unblocks": 1}]}).to_string(),
        )
        .unwrap();
        let j = [
            json!({"kind": "intent", "key": "k1", "action": "dispatch", "to": "7ebca", "first_line": "派工: N-1 → 7ebca", "at_ms": 1}),
            json!({"kind": "done", "key": "k1", "stage": "handed", "dm_id": "k1", "at_ms": 2}),
            json!({"kind": "intent", "key": "k2", "action": "nudge", "to": "519d0", "first_line": "提醒: x", "at_ms": 3}),
            json!({"kind": "done", "key": "k2", "stage": "queued", "dm_id": "q_1", "at_ms": 4}),
            json!({"kind": "intent", "key": "k3", "action": "dispatch", "to": "abcde", "first_line": "派工: N-3 → abcde", "at_ms": 5}),
            json!({"kind": "done", "key": "k3", "stage": "queued", "dm_id": "q_2", "at_ms": 6}),
        ]
        .iter()
        .map(|v| v.to_string() + "\n")
        .collect::<String>();
        std::fs::write(p.join("intents.jsonl"), j + "not json\n").unwrap();
        let v = view(p);
        assert_eq!(
            v["next"].as_array().unwrap().len(),
            ROWS_MAX,
            "reason: next_not_cut"
        );
        assert_eq!(v["next"][0]["what"], "w0");
        assert!(
            v["next"][0].get("secret").is_none(),
            "reason: next_leaks_fields"
        );
        let sent = v["dispatch"].as_array().unwrap();
        assert_eq!(sent.len(), 2, "reason: nudge_shown_as_dispatch {sent:?}");
        assert_eq!(sent[0]["key"], "k3", "reason: not_newest_first");
        assert_eq!(sent[1]["stage"], "handed");
        assert_eq!(v["andy_count"], 1);
        assert_eq!(v["unread"], json!(["unblock.json"]), "reason: unread");
        assert_eq!(v["blockers"], json!([]));
    }
}
