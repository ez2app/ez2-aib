//! S79 E10 (IFACE-s79 §3, ACCEPT E10): work act sent is followed in four steps, kept
//! apart:
//! * 送達 — it reached the seat (the `done` line's stage is `delivered`, or the queue file
//!   says delivered / read / done);
//! * 已讀 — the seat read it (`read_at`);
//! * 接單 — the seat took it up: closed it (`done_at`) or parked it to wait on something
//!   (`wait`);
//! * 開工 — the seat's `aib now` item started after the dm was sent (act cannot tell which
//!   work an item is, so this says only that the seat moved on to something after it).
//!
//! A step that cannot be read (the queue or the seat's `aib now` file unreadable, the dm no
//! longer in the queue) is `None`, never taken as no. Work not taken up [`ACCEPT_WAIT_MS`]
//! after it was sent is marked once in the act journal (`kind: mark`) for the coordinator
//! to look at. It is never sent again, nor handed to another seat: act::History keeps
//! which seat a row went to for [`FOLLOW_FOR_MS`], and plan holds the row back from others.

use crate::source::DmLookup;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

/// The actions whose dm is work for the seat (a nudge or telling the sender is not).
pub const TRACKED: [&str; 4] = ["wrap_up", "notify_fix", "open_review", "dispatch"];
/// Sent work is followed this long after it was sent; a row stays with its seat as long.
pub const FOLLOW_FOR_MS: i64 = 24 * 3_600_000;
/// Work not taken up this long after it was sent is marked (once).
pub const ACCEPT_WAIT_MS: i64 = 2 * 3_600_000;

/// One piece of work act sent: its `intent` and `done` lines in the intents ledger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SentWork {
    pub key: String,
    pub action: String,
    pub to: String,
    pub dm_id: String,
    /// The delivery stage `aib dm` answered (delivered / queued / sort-queued).
    pub stage: String,
    /// When the `done` line was written.
    pub at_ms: i64,
}

/// The four steps; `None` = could not be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Steps {
    pub delivered: Option<bool>,
    pub read: Option<bool>,
    pub accepted: Option<bool>,
    pub started: Option<bool>,
}

impl Steps {
    pub fn to_json(self) -> Value {
        json!({"delivered": self.delivered, "read": self.read,
               "accepted": self.accepted, "started": self.started})
    }
}

/// The work act sent in the last [`FOLLOW_FOR_MS`], in ledger order (a key sent again
/// after a retry keeps its last send). Lines the ledger cannot read are skipped here; the
/// ledger itself refuses to write over them.
pub fn sent_work(ledger: &str, now_ms: i64) -> Vec<SentWork> {
    let mut intent: BTreeMap<String, (String, String)> = BTreeMap::new();
    let mut out: Vec<SentWork> = Vec::new();
    let _ = aib_ledger::replay(ledger, crate::intents::MAX_LINE, |o| {
        let s = |k: &str| {
            o.get(k)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        };
        let key = s("key");
        match o.get("kind").and_then(Value::as_str) {
            Some("intent") => {
                intent.insert(key, (s("action"), s("to")));
            }
            Some("done") => {
                let at_ms = o.get("at_ms").and_then(Value::as_i64).unwrap_or(0);
                let Some((action, to)) = intent.get(&key) else {
                    return Ok(());
                };
                if !TRACKED.contains(&action.as_str())
                    || s("dm_id").is_empty()
                    || now_ms.saturating_sub(at_ms) > FOLLOW_FOR_MS
                {
                    return Ok(());
                }
                out.retain(|w| w.key != key);
                out.push(SentWork {
                    key,
                    action: action.clone(),
                    to: to.clone(),
                    dm_id: s("dm_id"),
                    stage: s("stage"),
                    at_ms,
                });
            }
            _ => {}
        }
        Ok(())
    });
    out
}

/// A field that is there and not empty.
fn set(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::String(s) => !s.trim().is_empty(),
        _ => true,
    }
}

/// The four steps of `w`, from the recipient's queue (`aib dm-queue list --tab all`; None =
/// unreadable) and its `aib now` file (None = unreadable or missing).
pub fn steps(w: &SentWork, queue: Option<&Value>, now: Option<&Value>) -> Steps {
    let dm = queue.and_then(Value::as_array).and_then(|a| {
        a.iter()
            .find(|d| d["id"].as_str() == Some(w.dm_id.as_str()))
    });
    let status = |d: &Value| d["status"].as_str().unwrap_or_default().to_owned();
    let delivered = if w.stage == "delivered" {
        Some(true)
    } else {
        dm.map(|d| {
            set(&d["delivered_at"]) || matches!(status(d).as_str(), "delivered" | "read" | "done")
        })
    };
    let read = dm.map(|d| set(&d["read_at"]) || matches!(status(d).as_str(), "read" | "done"));
    let accepted = dm.map(|d| set(&d["done_at"]) || status(d) == "done" || set(&d["wait"]));
    let started = now.map(|n| {
        let item = n["item"].as_str().unwrap_or_default().trim();
        // seconds, as aib now writes them
        let since = n["started_at"].as_f64().map(|s| (s * 1000.0) as i64);
        !item.is_empty() && since.is_some_and(|s| s >= w.at_ms)
    });
    Steps {
        delivered,
        read,
        accepted,
        started,
    }
}

/// Reads a seat's `aib now` file.
pub trait SeatNowRead {
    /// `None` = missing or unreadable.
    fn seat_now(&self, seat: &str) -> Option<Value>;
}

/// `<root>/state/seat-now/<seat>.json`.
pub struct FsSeatNow {
    pub root: PathBuf,
}

impl SeatNowRead for FsSeatNow {
    fn seat_now(&self, seat: &str) -> Option<Value> {
        if !crate::source::is_seat(seat) {
            return None;
        }
        let p = self
            .root
            .join("state/seat-now")
            .join(format!("{seat}.json"));
        let t = std::fs::read_to_string(p).ok()?;
        serde_json::from_str(&t).ok()
    }
}

/// Each piece of work sent in the last day with its steps; each recipient's queue and
/// `aib now` file are read once.
pub fn follow(
    ledger: &str,
    now_ms: i64,
    queue: &dyn DmLookup,
    seat_now: &dyn SeatNowRead,
) -> Vec<(SentWork, Steps)> {
    let mut queues: BTreeMap<String, Option<Value>> = BTreeMap::new();
    let mut nows: BTreeMap<String, Option<Value>> = BTreeMap::new();
    sent_work(ledger, now_ms)
        .into_iter()
        .map(|w| {
            let q = queues
                .entry(w.to.clone())
                .or_insert_with(|| queue.all(&w.to).ok())
                .clone();
            let n = nows
                .entry(w.to.clone())
                .or_insert_with(|| seat_now.seat_now(&w.to))
                .clone();
            let s = steps(&w, q.as_ref(), n.as_ref());
            (w, s)
        })
        .collect()
}

/// The `mark` lines due this round: work surely not taken up [`ACCEPT_WAIT_MS`] after it
/// was sent and not marked before. Only a mark: nothing is sent, the row stays with its seat.
pub fn due_marks(
    followed: &[(SentWork, Steps)],
    marked: &BTreeSet<String>,
    now_ms: i64,
) -> Vec<String> {
    followed
        .iter()
        .filter(|(w, s)| {
            s.accepted == Some(false)
                && now_ms.saturating_sub(w.at_ms) >= ACCEPT_WAIT_MS
                && !marked.contains(&w.key)
        })
        .map(|(w, s)| {
            json!({"v": 1, "kind": "mark", "key": w.key, "action": w.action, "to": w.to,
                   "dm_id": w.dm_id, "at": crate::act::rfc3339_ms(now_ms), "at_ms": now_ms,
                   "sent_at_ms": w.at_ms, "steps": s.to_json(),
                   "why": format!("未接單 {} 分,只標記不改派", (now_ms - w.at_ms) / 60_000)})
            .to_string()
        })
        .collect()
}

/// One printed line: `follow <key> → <seat>: 送達 是 已讀 否 接單 ? 開工 ? (<dm id>)`.
pub fn line(w: &SentWork, s: Steps) -> String {
    let f = |b: Option<bool>| match b {
        Some(true) => "是",
        Some(false) => "否",
        None => "?",
    };
    format!(
        "follow {} → {}: 送達 {} 已讀 {} 接單 {} 開工 {} ({})",
        w.key,
        w.to,
        f(s.delivered),
        f(s.read),
        f(s.accepted),
        f(s.started),
        w.dm_id
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_700_800_000;
    const H: &str = "1b0d079b58bf33909f6b8302e82b543d40043581";

    fn ledger(lines: &[Value]) -> String {
        lines.iter().map(|v| format!("{v}\n")).collect()
    }

    fn sent(key: &str, action: &str, to: &str, dm: &str, stage: &str, at: i64) -> Vec<Value> {
        vec![
            json!({"v":1,"kind":"intent","key":key,"at_ms":at,"action":action,"to":to}),
            json!({"v":1,"kind":"sending","key":key,"at_ms":at}),
            json!({"v":1,"kind":"done","key":key,"at_ms":at,"dm_id":dm,"stage":stage}),
        ]
    }

    fn work(stage: &str, at: i64) -> SentWork {
        SentWork {
            key: format!("dispatch:S79-x:{H}:a9136"),
            action: "dispatch".into(),
            to: "a9136".into(),
            dm_id: "q_00000001".into(),
            stage: stage.into(),
            at_ms: at,
        }
    }

    #[test]
    fn only_work_sent_in_the_last_day_is_followed() {
        let mut l = sent(
            "dispatch:S79-x:-:a9136",
            "dispatch",
            "a9136",
            "q_1",
            "delivered",
            NOW - 3_600_000,
        );
        l.extend(sent(
            "nudge:q_9:-:a9136",
            "nudge",
            "a9136",
            "q_2",
            "delivered",
            NOW,
        ));
        l.extend(sent(
            "wrap_up:2026-11-14:-:1ced7:handoff",
            "wrap_up",
            "1ced7",
            "q_3",
            "queued",
            NOW - FOLLOW_FOR_MS - 1,
        ));
        l.push(json!({"v":1,"kind":"intent","key":"open_review:#7:-:c477a","at_ms":NOW,"action":"open_review","to":"c477a"}));
        let w = sent_work(&ledger(&l), NOW);
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].key, "dispatch:S79-x:-:a9136");
        assert_eq!(
            (w[0].to.as_str(), w[0].dm_id.as_str(), w[0].stage.as_str()),
            ("a9136", "q_1", "delivered")
        );
    }

    #[test]
    fn the_four_steps_are_kept_apart() {
        let w = work("sort-queued", NOW - 60_000);
        let q = |d: Value| json!([{"id": "q_other", "status": "done"}, d]);
        let now_on = json!({"item": "x", "started_at": (NOW / 1000) as f64});
        // queued, nothing yet
        let s = steps(
            &w,
            Some(&q(
                json!({"id":"q_00000001","status":"queued","read_at":null,"done_at":null,"wait":null}),
            )),
            Some(&json!({"item":"y","started_at": (NOW - 600_000) / 1000})),
        );
        assert_eq!(
            s,
            Steps {
                delivered: Some(false),
                read: Some(false),
                accepted: Some(false),
                started: Some(false)
            }
        );
        // delivered, read, not taken up
        let s = steps(
            &w,
            Some(&q(
                json!({"id":"q_00000001","status":"read","delivered_at":1,"read_at":2}),
            )),
            None,
        );
        assert_eq!(
            s,
            Steps {
                delivered: Some(true),
                read: Some(true),
                accepted: Some(false),
                started: None
            }
        );
        // parked to wait = taken up
        let s = steps(
            &w,
            Some(&q(
                json!({"id":"q_00000001","status":"read","read_at":2,"wait":"等收據"}),
            )),
            Some(&now_on),
        );
        assert_eq!(s.accepted, Some(true));
        assert_eq!(s.started, Some(true));
        // closed = taken up; an empty item is not started
        let s = steps(
            &w,
            Some(&q(json!({"id":"q_00000001","status":"done","done_at":3}))),
            Some(&json!({"item":"","started_at": NOW / 1000})),
        );
        assert_eq!(
            (s.read, s.accepted, s.started),
            (Some(true), Some(true), Some(false))
        );
    }

    #[test]
    fn a_step_that_cannot_be_read_is_not_a_no() {
        let w = work("queued", NOW - 60_000);
        // queue unreadable, or the dm no longer in it
        for q in [None, Some(json!([{"id": "q_other", "status": "queued"}]))] {
            let s = steps(&w, q.as_ref(), None);
            assert_eq!(s, Steps::default(), "{q:?}");
        }
        // stage delivered is delivered even when the queue cannot be read
        assert_eq!(
            steps(&work("delivered", NOW), None, None).delivered,
            Some(true)
        );
    }

    #[test]
    fn work_not_taken_up_after_two_hours_is_marked_once() {
        let not = Steps {
            delivered: Some(true),
            read: Some(true),
            accepted: Some(false),
            started: Some(false),
        };
        let early = (work("delivered", NOW - ACCEPT_WAIT_MS + 60_000), not);
        let late = (work("delivered", NOW - ACCEPT_WAIT_MS), not);
        let unknown = (
            work("delivered", NOW - ACCEPT_WAIT_MS),
            Steps {
                accepted: None,
                ..not
            },
        );
        assert!(due_marks(&[early, unknown], &BTreeSet::new(), NOW).is_empty());
        let m = due_marks(&[late.clone()], &BTreeSet::new(), NOW);
        assert_eq!(m.len(), 1);
        let v: Value = serde_json::from_str(&m[0]).expect("json");
        assert_eq!(v["kind"], "mark");
        assert_eq!(v["key"], json!(late.0.key));
        assert_eq!(v["steps"]["accepted"], json!(false));
        assert_eq!(v["why"], "未接單 120 分,只標記不改派");
        let marked: BTreeSet<String> = [late.0.key.clone()].into();
        assert!(due_marks(&[late], &marked, NOW).is_empty());
    }

    #[test]
    fn the_printed_line_says_each_step() {
        let s = Steps {
            delivered: Some(true),
            read: Some(false),
            accepted: None,
            started: None,
        };
        assert_eq!(
            line(&work("delivered", NOW), s),
            format!("follow dispatch:S79-x:{H}:a9136 → a9136: 送達 是 已讀 否 接單 ? 開工 ? (q_00000001)")
        );
    }
}
