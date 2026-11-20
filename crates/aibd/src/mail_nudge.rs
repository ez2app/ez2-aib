//! N-webonly-c32 (7ebca 11-20, DESIGN-seat-open-mail-gate r1; 5d33a PREREAD-mailgate): a seat
//! whose turn ends with another seat's message written to it and still not closed is told so,
//! as a terminal seat's turn-end gate does. What the seat reads is aibd's own fixed line: the
//! messages' ids and the senders' seat ids, never a message's text.
//!
//! - A message is due [`AFTER_SECS`] after it was written to the seat, while it is open
//!   (`delivered` / `read`), from a seat (the person's own words are a conversation, not a
//!   task), and not a call in a shared conversation (that one is answered there).
//! - Each message is named at most [`PER_LETTER`] times (`nudged` in its item; absent = 0),
//!   and a seat is reminded at most [`PER_HOUR`] times in a rolling hour (5d33a L1: twenty
//!   small messages named twice each would be forty turns, and a reminder is outside the
//!   seat-to-seat limits). Past either, or when the seat's mode takes no input, the messages
//!   are given up on (`nudged` = [`PER_LETTER`]) and shown as a slot-4 card ([`cards`]).
//! - The reminder is no message: nothing is written to the library for it, so it is never
//!   itself an open message.

use crate::api::AppState;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Seconds after a message was written to the seat before it is named.
pub const AFTER_SECS: i64 = 180;
/// How many times one message is named.
pub const PER_LETTER: i64 = 2;
/// How many reminders one seat is written in a rolling hour.
pub const PER_HOUR: usize = 6;
/// The ids one reminder lists (the count it says is of all that are due).
const LISTED: usize = 10;
const HOUR: Duration = Duration::from_secs(3600);

fn text_of<'a>(r: &'a aib_store::MessageRow, k: &str) -> Option<&'a str> {
    r.item.get(k).and_then(Value::as_str)
}

/// How many times `r` was named already.
pub fn nudged(r: &aib_store::MessageRow) -> i64 {
    r.item.get("nudged").and_then(Value::as_i64).unwrap_or(0)
}

/// Another seat's message written to its receiver at or before `now - AFTER_SECS` and still
/// open (whatever `nudged` says).
fn open_past(r: &aib_store::MessageRow, now: i64) -> bool {
    matches!(text_of(r, "status"), Some("delivered" | "read"))
        && text_of(r, "sender_kind") == Some("seat")
        && r.item.get("post_id").is_none_or(Value::is_null)
        && r.item
            .get("delivered_at")
            .and_then(Value::as_i64)
            .is_some_and(|at| at <= now - AFTER_SECS)
}

/// The rows of one seat's open messages a reminder names now, oldest first as given.
pub fn due(rows: &[aib_store::MessageRow], now: i64) -> Vec<&aib_store::MessageRow> {
    rows.iter()
        .filter(|r| open_past(r, now) && nudged(r) < PER_LETTER)
        .collect()
}

/// What to do with the due messages of a seat.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    /// write the reminder, each message named once more
    Write,
    /// write nothing; the messages go straight to the card
    GiveUp,
}

/// `takes_input`: the seat's mode takes input now; `under_hour`: the seat has reminders left
/// this hour.
pub fn step(takes_input: bool, under_hour: bool) -> Step {
    if takes_input && under_hour {
        Step::Write
    } else {
        Step::GiveUp
    }
}

/// The reminder's text: how many, then up to [`LISTED`] `id 來自 seat` pairs.
pub fn text(due: &[(&str, &str)]) -> String {
    let named: Vec<String> = due
        .iter()
        .take(LISTED)
        .map(|(id, from)| format!("{id} 來自 {from}"))
        .collect();
    let more = if due.len() > LISTED { " 等" } else { "" };
    format!(
        "你有 {} 封別席的信還沒結:{}{more}。做完用 seat_done 結(帶 why 或 evidence);還在等人也用 seat_done 帶 why 寫等誰。要看內文用 seat_inbox。",
        due.len(),
        named.join("、")
    )
}

/// The item of a row named once more, or given up on.
pub fn named_item(
    mut item: serde_json::Map<String, Value>,
    how: Step,
    at: i64,
) -> serde_json::Map<String, Value> {
    let n = item.get("nudged").and_then(Value::as_i64).unwrap_or(0);
    let now = match how {
        Step::Write => (n + 1).min(PER_LETTER),
        Step::GiveUp => PER_LETTER,
    };
    item.insert("nudged".into(), json!(now));
    item.insert("nudged_at".into(), json!(at));
    item
}

/// One seat's reminders of the last hour.
#[derive(Default)]
pub struct Hour(HashMap<String, VecDeque<Instant>>);

impl Hour {
    /// Whether `seat` has a reminder left at `now`; taking it when it has.
    pub fn take(&mut self, seat: &str, now: Instant) -> bool {
        let q = self.0.entry(seat.to_owned()).or_default();
        while q
            .front()
            .is_some_and(|t| now.saturating_duration_since(*t) >= HOUR)
        {
            q.pop_front();
        }
        if q.len() >= PER_HOUR {
            return false;
        }
        q.push_back(now);
        true
    }
}

fn hour() -> std::sync::MutexGuard<'static, Hour> {
    static H: std::sync::OnceLock<Mutex<Hour>> = std::sync::OnceLock::new();
    H.get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

/// `seat` started or ended a turn (after [`crate::seat_send::redeliver`]): its open messages
/// that are due are named in one line, or given up on.
pub async fn nudge(st: AppState, seat: String) {
    if st.sessions.in_turn(&seat) {
        return;
    }
    let (db, key) = (st.messages.clone(), format!("seat:{seat}"));
    let Ok(Ok(rows)) =
        tokio::task::spawn_blocking(move || db.open_to(&key, aib_store::PAGE_MAX)).await
    else {
        return;
    };
    // what still waits to be written goes first (the seat reads it before any reminder)
    if rows.iter().any(crate::seat_send::waits_to_be_written) {
        return;
    }
    let now = unix_now();
    let rows: Vec<aib_store::MessageRow> = due(&rows, now).into_iter().cloned().collect();
    if rows.is_empty() {
        return;
    }
    let store = st.store.clone();
    let Ok(Ok(metas)) = tokio::task::spawn_blocking(move || store.seat_metas()).await else {
        return;
    };
    let mode = metas
        .iter()
        .find(|m| m.seat_id == seat)
        .and_then(|m| m.mode.clone());
    let takes =
        crate::seat_input::mode_takes_input(mode.as_deref(), crate::seat_input::dgrs_input_on());
    let how = step(takes, !takes || hour().take(&seat, Instant::now()));
    if how == Step::Write {
        let pairs: Vec<(&str, &str)> = rows
            .iter()
            .map(|r| {
                (
                    r.message_id.as_str(),
                    r.from_key.trim_start_matches("seat:"),
                )
            })
            .collect();
        let Ok(line) = crate::seat_send::reminder_line(&text(&pairs)) else {
            return;
        };
        let (s, t) = (st.sessions.clone(), seat.clone());
        let wrote =
            tokio::task::spawn_blocking(move || s.write_input_between_turns(&t, &line)).await;
        if !matches!(wrote, Ok(Ok(_))) {
            // in a turn again, or not running: the next turn end asks again
            return;
        }
    }
    let ids: Vec<String> = rows.iter().map(|r| r.message_id.clone()).collect();
    for r in rows {
        let (db, item, at) = (
            st.messages.clone(),
            named_item(r.item, how, now),
            crate::sessions::now_rfc3339(),
        );
        let _ =
            tokio::task::spawn_blocking(move || db.update(&r.message_id, r.rev, item, &at)).await;
    }
    crate::seat_send::journal_event(
        &st,
        "seat.message.nudged",
        json!({"to_seat": seat, "count": ids.len(), "message_ids": ids,
            "written": how == Step::Write}),
    )
    .await;
}

/// Open messages given up on (named [`PER_LETTER`] times and still open): one slot-4 card per
/// seat, card id `mailopen:<seat>:<oldest>`. A message still unread past `unread_cutoff` is
/// left to [`crate::slots::unread_cards`] (one card for it, not two).
pub fn cards(
    rows: &[aib_store::MessageRow],
    now: i64,
    unread_cutoff: i64,
) -> Vec<crate::slots::Card> {
    let mut by: BTreeMap<&str, Vec<(i64, &aib_store::MessageRow)>> = BTreeMap::new();
    for r in rows {
        let at = r.item.get("delivered_at").and_then(Value::as_i64);
        let unread_card =
            text_of(r, "status") == Some("delivered") && at.is_some_and(|at| at < unread_cutoff);
        if !open_past(r, now) || nudged(r) < PER_LETTER || unread_card {
            continue;
        }
        if let (Some(seat), Some(at)) = (r.to_key.strip_prefix("seat:"), at) {
            by.entry(seat).or_default().push((at, r));
        }
    }
    by.into_iter()
        .filter_map(|(seat, rs)| {
            let (at, oldest) = rs
                .iter()
                .min_by(|a, b| (a.0, &a.1.message_id).cmp(&(b.0, &b.1.message_id)))?;
            Some(crate::slots::Card {
                card_id: format!("mailopen:{seat}:{}", oldest.message_id),
                slot: 4,
                name: crate::slots::NAMES[3],
                kind: "message.unclosed".into(),
                seat_id: Some(seat.to_owned()),
                session_id: None,
                at: crate::sessions::rfc3339_of_unix(*at),
                global_seq: None,
                item: Some(format!("{} 封信收了沒結", rs.len())),
                why: None,
                pct: None,
                card: None,
                rec: None,
                state: Some(format!("提醒過 {PER_LETTER} 次還沒結,或這一席現在不能提醒")),
                src: oldest.from_key.strip_prefix("seat:").map(str::to_owned),
                tag: None,
                open: None,
                actions: crate::slots::actions(4),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn row(id: &str, to: &str, pairs: &[(&str, Value)]) -> aib_store::MessageRow {
        let mut r =
            crate::seat_send::queued_row("seat_a", to, Some("safe"), "內文不外露", "00").unwrap();
        r.message_id = id.to_owned();
        for (k, v) in pairs {
            r.item.insert((*k).to_owned(), v.clone());
        }
        r
    }

    fn written(id: &str, at: i64) -> aib_store::MessageRow {
        row(
            id,
            "seat_b",
            &[("status", json!("delivered")), ("delivered_at", json!(at))],
        )
    }

    #[test]
    fn a_message_written_three_minutes_ago_and_still_open_is_due() {
        let now = 10_000;
        let rows = [
            written("q_old", now - AFTER_SECS),
            written("q_new", now - AFTER_SECS + 1),
            row(
                "q_read",
                "seat_b",
                &[
                    ("status", json!("read")),
                    ("delivered_at", json!(now - 900)),
                ],
            ),
            row(
                "q_done",
                "seat_b",
                &[
                    ("status", json!("done")),
                    ("delivered_at", json!(now - 900)),
                ],
            ),
            row("q_queued", "seat_b", &[]),
        ];
        let ids: Vec<&str> = due(&rows, now)
            .iter()
            .map(|r| r.message_id.as_str())
            .collect();
        assert_eq!(ids, ["q_old", "q_read"], "reason: due_wrong");
    }

    #[test]
    fn the_persons_words_and_a_call_in_a_conversation_are_not_named() {
        let now = 10_000;
        let rows = [
            row(
                "q_person",
                "seat_b",
                &[
                    ("status", json!("delivered")),
                    ("delivered_at", json!(now - 900)),
                    ("sender_kind", json!("human")),
                ],
            ),
            row(
                "q_call",
                "seat_b",
                &[
                    ("status", json!("delivered")),
                    ("delivered_at", json!(now - 900)),
                    ("post_id", json!("p_1")),
                ],
            ),
        ];
        assert!(
            due(&rows, now).is_empty(),
            "reason: named_not_a_seat_message"
        );
    }

    #[test]
    fn a_message_is_named_twice_and_then_left_to_the_card() {
        let now = 10_000;
        let mut r = written("q_1", now - 900);
        for n in 1..=PER_LETTER {
            assert_eq!(due(std::slice::from_ref(&r), now).len(), 1, "round {n}");
            assert!(cards(std::slice::from_ref(&r), now, 0).is_empty());
            r.item = named_item(r.item, Step::Write, now);
            assert_eq!(nudged(&r), n);
        }
        assert!(
            due(std::slice::from_ref(&r), now).is_empty(),
            "reason: named_a_third_time"
        );
        let c = cards(std::slice::from_ref(&r), now, 0);
        assert_eq!(c.len(), 1, "reason: no_card_after_the_last_reminder");
        assert_eq!(c[0].card_id, "mailopen:seat_b:q_1");
        assert_eq!(c[0].kind, "message.unclosed");
        assert_eq!(c[0].slot, 4);
        assert_eq!(c[0].item.as_deref(), Some("1 封信收了沒結"));
        // closed: the card goes
        r.item.insert("status".into(), json!("done"));
        assert!(cards(std::slice::from_ref(&r), now, 0).is_empty());
    }

    #[test]
    fn a_seat_that_takes_no_input_or_is_past_its_hour_goes_to_the_card() {
        assert_eq!(step(true, true), Step::Write);
        assert_eq!(
            step(false, true),
            Step::GiveUp,
            "reason: wrote_a_closed_seat"
        );
        assert_eq!(
            step(true, false),
            Step::GiveUp,
            "reason: wrote_past_the_hour"
        );
        let now = 10_000;
        let mut r = written("q_1", now - 900);
        r.item = named_item(r.item, Step::GiveUp, now);
        assert_eq!(nudged(&r), PER_LETTER, "reason: given_up_not_marked");
        assert_eq!(cards(std::slice::from_ref(&r), now, 0).len(), 1);
    }

    #[test]
    fn a_seat_is_reminded_six_times_an_hour() {
        let mut h = Hour::default();
        let t0 = Instant::now();
        for n in 0..PER_HOUR {
            let s = u64::try_from(n).unwrap();
            assert!(h.take("seat_b", t0 + Duration::from_secs(s)), "{n}");
        }
        assert!(
            !h.take("seat_b", t0 + Duration::from_secs(60)),
            "reason: no_hour_limit"
        );
        assert!(h.take("seat_c", t0), "reason: another_seat_counted");
        assert!(h.take("seat_b", t0 + HOUR), "reason: the_hour_never_passes");
    }

    #[test]
    fn the_reminder_names_ids_and_seats_and_never_a_text() {
        let rows = [written("q_1", 0), written("q_2", 0)];
        let pairs: Vec<(&str, &str)> = rows
            .iter()
            .map(|r| {
                (
                    r.message_id.as_str(),
                    r.from_key.trim_start_matches("seat:"),
                )
            })
            .collect();
        let t = text(&pairs);
        assert!(
            t.starts_with("你有 2 封別席的信還沒結:q_1 來自 seat_a、q_2 來自 seat_a。"),
            "{t}"
        );
        assert!(t.contains("seat_done") && !t.contains("內文不外露"), "{t}");
        let line = crate::seat_send::reminder_line(&t).unwrap();
        let v: Value = serde_json::from_slice(&line).unwrap();
        let first = v["message"]["content"][0]["text"].as_str().unwrap();
        assert!(
            first.starts_with("[aibd] 提醒") && first.contains("不是真人指示"),
            "reason: reminder_reads_as_the_person {first}"
        );
        // more than the line lists: the count is of all, the list stops
        let many: Vec<String> = (0..12).map(|n| format!("q_{n}")).collect();
        let pairs: Vec<(&str, &str)> = many.iter().map(|i| (i.as_str(), "seat_a")).collect();
        let t = text(&pairs);
        assert!(t.starts_with("你有 12 封") && !t.contains("q_10") && t.contains(" 等。"));
    }

    #[test]
    fn an_unread_message_past_its_own_card_is_not_carded_twice() {
        let now = 10_000;
        let mut r = written("q_1", now - 3_000);
        r.item = named_item(r.item, Step::GiveUp, now);
        // the unread card's cutoff is after it was written: that card shows it
        assert!(cards(std::slice::from_ref(&r), now, now - 1_800).is_empty());
        // read and still open: this card
        r.item.insert("status".into(), json!("read"));
        assert_eq!(cards(std::slice::from_ref(&r), now, now - 1_800).len(), 1);
    }
}
