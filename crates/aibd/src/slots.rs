//! N-workspace event slots (SPEC-workspace-multiseat-519d0-1118 §2, §3; ORDER-workspace 7ebca):
//! the right column of the workspace, cards ordered by how much they need a person, top down.
//!
//! | slot | name        | from (this slice)                                                    |
//! |------|-------------|----------------------------------------------------------------------|
//! | 1    | `decide`    | a seat's `aib now` line in `decide` (waiting for a person); an open  |
//! |      |             | item of the 待人決策 list ([`ANDY_PREFIX`], aib-drive `andy_open`)    |
//! | 2    | `broken`    | a session that failed to start, was refused, timed out, did not stop  |
//! |      |             | for sure, or exited with a non-zero code; aib-drive's next list's     |
//! |      |             | `fix` rows (紅或衝突: checks / receipt red, a review's M, a conflict); |
//! |      |             | a failed verification has no structured source yet (aib-plan fail)  |
//! | 3    | `low_power` | a seat's context at [`crate::usage::HANDOFF_PCT`] % or more; the     |
//! |      |             | thermal guard would refuse or reap                                    |
//! | 4    | `stuck`     | a seat waiting on another seat ([`STUCK_MIN`] min with no new line); |
//! |      |             | a busy seat that wrote nothing for [`STUCK_MIN`] min; a queued session; |
//! |      |             | messages to a seat still not written to it after [`STUCK_MIN`] min;   |
//! |      |             | written to it and still not read after [`STUCK_MIN`] min              |
//! | 5    | `next`      | aib-drive's next list (`<state root>/drive/act-next.json`, schema     |
//! |      |             | `aib-drive.next.v1`): its `merge` (審過、收據齊、CI 綠) and `push`    |
//! |      |             | (審過未推) rows; nothing in it says "installable" yet                |
//! | 6    | `done`      | a seat opened, a session exited cleanly or was stopped, a message closed; |
//! |      |             | a tree item moved to another station (`node.set` with `to`: 換站, card = |
//! |      |             | the item, `from → to`); 已合 / 已換上 have no structured source yet    |
//!
//! - The table ([`table`]) is this slice's; a kind it does not know is classified by the 1.x
//!   notification levels ([`aib_notify::classify`], B1dm-notify): red ⇒ 2, yellow ⇒ 1,
//!   green ⇒ 6, blue (routine) ⇒ no card (519d0 left the six-from-four mapping open; 7ebca's
//!   default, dm q_b658313ee1749e90).
//! - `GET /api/v1/slots`: every card, slot first, newest first within a slot; at most
//!   [`KEEP_DONE`] in slot 6 and [`MAX_CARDS`] in all; from the seats' lines and the last
//!   [`SCAN`] events. A page or the start-up token reads it; a seat token cannot (guard).
//! - act `decision_answer {seat_id, answer, card_id?}`: types the answer into that seat through
//!   [`crate::seat_input::type_to_seat`] (the same checks as `seat_input`: a user folder page
//!   types to safe seats only, the mode must take input) and takes the card off. Only a seat's
//!   own `decide` line is answered here: a permission prompt on a seat's screen (letting a tool
//!   run) is never a card and never answered by a page credential (SPEC §0.4).
//! - The 待人決策 list (第三波 1): the open items of aib-drive's `andy_open` events (#847) under
//!   the 1.x state root (`EZ2_AIB_STATE_ROOT`, `EZ2_AIB_HOME`, else `~/.ez2-aib`, as aib-drive
//!   reads it), card id `andy:<item>`, the item's own fields as SPEC §14's `text` / `rec` /
//!   `state` / `src` / `card` and its kind as the `tag` (「知道」 sits with slot 6). A list that cannot be read gives no cards (the rest still shows).
//!   `decision_answer {card_id: "andy:<item>", answer, seat_id?}` answers it while the item is
//!   open: the answer is typed into the seat the page names, if it names one, and aibd closes
//!   the item on the same list itself (`aib-drive andy-open close --state 已答(頁面)`, SPEC §14).
//! - act `slot_dismiss {card_id}`: takes a card off (「晚點」/ seen).
//! - Both acts are the built-in page's, paired now (5d33a REVIEW ca4a2458 M1 / M2, 519d0 r16
//!   裁定 3): a user folder page gets `slot_built_in_page_only`, a pairing carried over an update
//!   `carried_pair_read_and_safe_only` (seat_input). A user folder page reads a 待人決策 card's
//!   words as length and SHA-256 only ([`hide_words`], the tree's `need` rule; N1).
//! - What was answered or dismissed is kept in memory; a dismissal is replayed from its
//!   `slot.dismissed` / `slot.restored` events at start, so it stays off after a restart
//!   (N-ws-dismiss-persist); an answer is not (its source closes the card)
//!   and journaled as `decision.answered` (lengths and SHA-256, never the answer) /
//!   `slot.dismissed`.

use crate::api::AppState;
use crate::guard::request_id;
use crate::guard::Caller;
use crate::pairing::PageAccess;
use crate::seat_now::Now;
use aib_store::{NewEvent, StoredEvent};
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Extension;
use axum::Json;
use serde::Serialize;
use serde_json::{json, Value};
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{LazyLock, Mutex, MutexGuard};

pub const ANSWER: &str = "decision_answer";
pub const DISMISS: &str = "slot_dismiss";
/// act type: put a dismissed card back (043dd 11-18 22:34, 「先收起」還原).
pub const RESTORE: &str = "slot_restore";
/// act type: hang an event card on a plan item, or take it off (N-ws-event-pin, 519d0 11-19
/// 07:22; 043dd's 「掛到項目」).
pub const PIN: &str = "slot_pin";
/// act type: 同意 / 不同意 an idea that needs a person ([`crate::consent_cards`]).
pub const IDEA_ANSWER: &str = "idea_answer";
/// act type: 看過 one line of the 真人未審 list ([`crate::consent_cards`]).
pub const REVIEW_SEEN: &str = "review_seen";
/// Slot `n` is `NAMES[n - 1]`.
pub const NAMES: [&str; 6] = ["decide", "broken", "low_power", "stuck", "next", "done"];
/// How many of the newest events are looked at.
pub const SCAN: u32 = 500;
/// At most this many slot-6 cards (notices pile up; the newest are kept).
pub const KEEP_DONE: usize = 20;
pub const MAX_CARDS: usize = 100;
/// Longest card id a body may carry.
pub const MAX_ID: usize = 160;
/// Most answered / dismissed card ids kept in memory.
pub const MAX_TAKEN: usize = 4096;
/// Minutes a seat may wait on another seat, or stay busy writing nothing, before it is stuck
/// (1.x: a message unread 30 minutes is stale, `dm_queue.STALE_MINUTES_DEFAULT`).
pub const STUCK_MIN: i64 = 30;
/// A 待人決策 item's card id is this and the item number.
pub const ANDY_PREFIX: &str = "andy:";

/// A kind this slice knows: `Some(Some(n))` is slot `n`, `Some(None)` never a card; `None` is
/// left to the notification levels.
pub fn table(kind: &str, payload: &Value) -> Option<Option<u8>> {
    Some(match kind {
        "session.spawn_failed" | "session.spawn_rejected" | "session.timeout" => Some(2),
        // a stop whose every candidate is another coalition's service is seen clean: a done
        // notice, not broken (a9136 11-19 09:49, 0eed7 VERIFY-humanpath d95f4ab3f L1 r1; the
        // same predicate as aibd ctl)
        "session.stop_unconfirmed" => {
            if stop_clean(payload) {
                Some(6)
            } else {
                Some(2)
            }
        }
        // a seat brought back at aibd's start that ended at once (L7; 0eed7 8843ef5f N1)
        "seat.rejoin_failed" => Some(2),
        // G13: a seat called in a conversation did not get its turn done (519d0 05:2x)
        "seat.turn_failed" => Some(2),
        // 合回 stopped on a conflict (taken back): who resolves it is the person's call
        "seat.merge_conflict" => Some(1),
        // 合回 refused: the author's repository has settings aibd does not run git with
        "seat.merge_unsafe" => Some(2),
        // a crash is broken; a clean exit (0) or a stop by signal is a notice
        "session.exited" => {
            if payload
                .get("exit_code")
                .and_then(Value::as_i64)
                .is_some_and(|c| c != 0)
            {
                Some(2)
            } else {
                Some(6)
            }
        }
        "thermal.would_nogo" | "thermal.would_reap" => Some(3),
        // N-ws-slot-sources 6 號: a preview swapped to a new build (519d0 11-19 07:22); a failed
        // swap is broken
        "preview.swapped" => Some(6),
        "preview.swap_failed" => Some(2),
        // ⑥ 換站 (SPEC §2): a tree item moved to another station; an edit that keeps the
        // station, and a new item, are no card
        "node.set" => text_of(payload.get("to")).map(|_| 6),
        "node.added" => None,
        "session.queued" => Some(4),
        "seat.opened" | "session.killed" | "session.cancelled" | "message.done" => Some(6),
        // the seats' lines are read from the board, not from their events; the slots' own
        // events and the high-volume ones are never cards
        // an ask's card is built from the open asks (crate::asks::cards), not from the event
        "seat.now"
        | "seat.asked"
        | "decision.answered"
        | "slot.dismissed"
        | "slot.restored"
        | "slot.pinned"
        | "idea.answered"
        | "review.seen"
        | "idea.pressed"
        | "review.pressed"
        | "session.output"
        | "session.output_closed"
        | "session.output_truncated"
        | "session.started"
        | "seat.input"
        | "seat.message"
        | "seat.created"
        | "message.sent"
        | "message.read"
        | "message.recovered" => None,
        _ => return None,
    })
}

static RULES: LazyLock<Option<aib_notify::Rules>> =
    LazyLock::new(|| aib_notify::Rules::builtin().ok());

/// The notification rules 1.x ships (`None` if they do not load: then unknown kinds are no card).
pub fn rules() -> Option<&'static aib_notify::Rules> {
    RULES.as_ref()
}

/// Which slot an event is (1–6), or none.
pub fn slot_of(kind: &str, payload: &Value, rules: Option<&aib_notify::Rules>) -> Option<u8> {
    if let Some(s) = table(kind, payload) {
        return s;
    }
    let mut record = json!({ "kind": kind });
    if let (Some(sig), Some(m)) = (
        payload.get("signal_type").filter(|v| v.is_string()),
        record.as_object_mut(),
    ) {
        m.insert("signal_type".into(), sig.clone());
    }
    match aib_notify::classify(&record, rules?).ok()?.level {
        "red" => Some(2),
        "yellow" => Some(1),
        "green" => Some(6),
        _ => None,
    }
}

/// What a card offers (SPEC §14): ① 照建議 / 其他選項 / 晚點 (both answers are
/// `decision_answer`); ② 叫作者修 / 叫他交接 / 改派; ③ 叫他交接; ④ ⑤ 叫號 / 改派; ⑥ 晚點.
pub fn actions(slot: u8) -> &'static [&'static str] {
    match slot {
        1 => &["follow_rec", "answer", "later"],
        2 => &["fix", "handoff", "reassign", "later"],
        3 => &["handoff", "later"],
        4 | 5 => &["call", "reassign", "later"],
        _ => &["later"],
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Card {
    pub card_id: String,
    pub slot: u8,
    pub name: &'static str,
    pub kind: String,
    pub seat_id: Option<String>,
    pub session_id: Option<String>,
    pub at: String,
    pub global_seq: Option<i64>,
    /// A `decide` card: the seat's item and why (as `GET /api/v1/now` shows them).
    pub item: Option<String>,
    pub why: Option<String>,
    /// A `low_power` card: the context used, whole percent.
    pub pct: Option<u8>,
    /// SPEC §14: the plan card it belongs to (a 待人決策 item's `card`).
    pub card: Option<String>,
    /// SPEC §14: the expanded lines 我們的建議 / 狀態 / 出處 (each only when there is one).
    pub rec: Option<String>,
    pub state: Option<String>,
    pub src: Option<String>,
    /// SPEC §14: the label when it is not the slot's own (a 待人決策 item's kind).
    pub tag: Option<String>,
    /// Where the page opens what the card is about (a 1.x letter's `{thread_id, message_id}`,
    /// [`crate::dm_cards`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub open: Option<Value>,
    pub actions: &'static [&'static str],
}

/// SPEC §14: the label of each slot (a 待人決策 item carries its own: 決定 / 出手 / 知道); §21c
/// (Andy 11-18 22:2x「壞了 要改成 注意」): slot 2 reads 注意, its code stays `broken`.
pub const TAGS: [&str; 6] = ["決定", "注意", "用量", "卡住", "可推進", "知道"];

/// Why a seat's session ended, read from its last output lines (N-ws-slot-sources 3 號,
/// 519d0 11-19 07:22): its usage ran out (`額度`) or it must log in again (`登入`). Only these
/// fixed words reach a card, never the output itself.
pub fn end_reason(lines: &[&str]) -> Option<&'static str> {
    const USAGE: [&str; 8] = [
        "usage limit",
        "limit reached",
        "hit your limit",
        "rate limit",
        "5-hour limit",
        "weekly limit",
        "quota",
        "額度",
    ];
    const LOGIN: [&str; 8] = [
        "/login",
        "not logged in",
        "please log in",
        "log in again",
        "invalid api key",
        "token has expired",
        "authentication",
        "登入",
    ];
    let low: Vec<String> = lines.iter().map(|l| l.to_lowercase()).collect();
    let any = |words: &[&str]| low.iter().any(|l| words.iter().any(|w| l.contains(w)));
    if any(&LOGIN) {
        Some("登入")
    } else if any(&USAGE) {
        Some("額度")
    } else {
        None
    }
}

/// How many of a session's last output lines [`end_reason`] reads.
pub const END_LINES: usize = 30;

/// Session id → why it ended, for each `session.exited` among `events` whose last output lines
/// (in the same events) say so.
fn end_reasons(events: &[StoredEvent]) -> BTreeMap<String, &'static str> {
    let mut out = BTreeMap::new();
    for e in events.iter().filter(|e| e.event_type == "session.exited") {
        let agg = crate::session_output::aggregate_id(&e.aggregate_id);
        let mut tail: Vec<&str> = events
            .iter()
            .filter(|o| o.global_seq < e.global_seq && o.aggregate_id == agg)
            .filter_map(|o| o.payload.get("line").and_then(Value::as_str))
            .collect();
        let cut = tail.len().saturating_sub(END_LINES);
        tail.drain(..cut);
        if let Some(r) = end_reason(&tail) {
            out.insert(e.aggregate_id.clone(), r);
        }
    }
    // a seat's turn that failed (seat.turn_failed, a9136 G13): its reason is the line to read;
    // keyed by the event id, the card's id (0eed7 VERIFY-evcards 93d26f1b3 L2: 「Not logged in」
    // arrives here, the session never exits)
    for e in events.iter().filter(|e| e.event_type == "seat.turn_failed") {
        let reason = e
            .payload
            .get("reason")
            .and_then(Value::as_str)
            .unwrap_or("");
        if let Some(r) = end_reason(&[reason]) {
            out.insert(e.event_id.clone(), r);
        }
    }
    out
}

/// A session that ended for its usage or a login is slot 3 (低電量), not a plain notice.
fn low_power_end(mut c: Card, ended: &BTreeMap<String, &'static str>) -> Card {
    let key = match c.kind.as_str() {
        "session.exited" => c.session_id.clone(),
        "seat.turn_failed" => Some(c.card_id.clone()),
        _ => None,
    };
    let Some(r) = key.as_ref().and_then(|k| ended.get(k)) else {
        return c;
    };
    c.slot = 3;
    c.name = NAMES[2];
    c.actions = actions(3);
    c.why = Some((*r).to_owned());
    c
}

fn end_reason_line(c: &Card) -> String {
    match c.why.as_deref() {
        Some("登入") => "結束:要重新登入".to_owned(),
        _ => "結束:額度用完".to_owned(),
    }
}

/// A stop that was not confirmed but was seen clean ([`crate::platform::session::stop_seen_clean`]
/// on an `observed_clean` reason).
pub fn stop_clean(payload: &Value) -> bool {
    payload.get("reason").and_then(Value::as_str) == Some("observed_clean")
        && crate::platform::session::stop_seen_clean(payload)
}

/// The one line of a card that has no text of its own.
fn line_of(c: &Card) -> String {
    let t = match c.kind.as_str() {
        "seat.usage" => return format!("上下文用了 {}%", c.pct.unwrap_or(0)),
        "session.spawn_failed" => "開不起來",
        "session.spawn_rejected" => "開啟被拒",
        "session.stop_unconfirmed" if c.slot == 6 => "已停(看得到的都停了)",
        "session.stop_unconfirmed" => "停不確定",
        "session.timeout" => "逾時",
        "session.exited" if c.slot == 3 => return end_reason_line(c),
        "session.exited" if c.slot == 2 => "結束碼不是 0",
        "session.exited" => "已結束",
        "thermal.would_nogo" => "太熱:會拒開新席",
        "thermal.would_reap" => "太熱:會收掉席",
        "session.queued" => "排隊等位子",
        "seat.opened" => "席已開",
        "seat.rejoin_failed" => "接回失敗:要重開",
        "session.killed" => "已停止",
        "session.cancelled" => "已取消",
        "message.done" => "訊息已結案",
        "preview.swapped" => "預覽已換上新版",
        "preview.swap_failed" => "預覽換版失敗",
        other => other,
    };
    t.to_owned()
}

impl Card {
    /// The card as `GET /api/v1/slots` gives it: every field, and SPEC §14's `level` (the slot),
    /// `tag`, `text`, `since` (the time), `card`, `rec` / `state` / `src` and `actions`.
    pub fn view(&self) -> Value {
        let mut v = serde_json::to_value(self).unwrap_or(Value::Null);
        if let Some(o) = v.as_object_mut() {
            o.insert("level".into(), json!(self.slot));
            let tag = self
                .tag
                .clone()
                .unwrap_or_else(|| TAGS[usize::from(self.slot - 1)].to_owned());
            o.insert("tag".into(), json!(tag));
            let text = self.item.clone().unwrap_or_else(|| line_of(self));
            o.insert("text".into(), json!(text));
            o.insert("since".into(), json!(self.at));
            o.insert("origin".into(), json!(self.origin()));
            // the plan item the person hung this card on (N-ws-event-pin), beside `card`
            if let Some(n) = pins().get(&self.card_id) {
                o.insert("pin".into(), json!(n));
            }
        }
        v
    }
}

/// Where a card comes from (ORDER-autodrive-webdev-519d0-1120, a9136 3; Andy's run 11-20 00:06:
/// the 待人決策 count mixed old letters with the one card he had to press): `history` is what the
/// 1.x system wrote and the import brought over (its 待人決策 list, letters in its queue, its
/// review list, its idea consents), shown apart and not counted as waiting on the person;
/// `now` is everything else (a seat's ask, a seat's decide line, a session, aibd itself).
pub fn origin_of(kind: &str) -> &'static str {
    let old = kind.starts_with("dm.")
        || matches!(kind, "andy.open" | "andy.review_list" | "idea.consent");
    if old {
        "history"
    } else {
        "now"
    }
}

impl Card {
    /// [`origin_of`] its kind, and a `decide` line (`seat.now`) of a 1.x terminal seat (its id
    /// is not aibd's `seat_…`) is `history` too: the old system asking, not a 2.0 seat (5d33a
    /// PREREAD-nx 2dda591e N2; the page had put those with 以前的題目).
    pub fn origin(&self) -> &'static str {
        let old_seat = self.kind == "seat.now"
            && !self
                .seat_id
                .as_deref()
                .is_some_and(|s| s.starts_with("seat_"));
        if old_seat {
            "history"
        } else {
            origin_of(&self.kind)
        }
    }
}

/// The card id of a seat's `decide` line: a new line (another `updated_at`) is a new card.
pub fn now_card_id(seat_id: &str, updated_at: &str) -> String {
    format!("now:{seat_id}:{updated_at}")
}

fn text_of(v: Option<&Value>) -> Option<String> {
    v.and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
}

fn seat_of_event(e: &StoredEvent) -> Option<String> {
    text_of(e.payload.get("seat_id"))
        .or_else(|| (e.aggregate_type == "seat").then(|| e.aggregate_id.clone()))
}

/// A seat is back after these: a failed rejoin before one is no longer a card (5d33a PREREAD
/// 04e74d1c L2: 重開 succeeded, the card stayed until dismissed and came back at aibd's start).
pub const SEAT_BACK: [&str; 3] = ["seat.restarted", "seat.opened", "seat.rejoined"];

/// The `seat.rejoin_failed` events with a later [`SEAT_BACK`] event of the same seat.
pub fn rejoins_since_back(events: &[StoredEvent]) -> BTreeSet<String> {
    let mut back: BTreeMap<String, i64> = BTreeMap::new();
    for e in events
        .iter()
        .filter(|e| SEAT_BACK.contains(&e.event_type.as_str()))
    {
        if let Some(seat) = seat_of_event(e) {
            let m = back.entry(seat).or_insert(e.global_seq);
            *m = (*m).max(e.global_seq);
        }
    }
    events
        .iter()
        .filter(|e| e.event_type == "seat.rejoin_failed")
        .filter(|e| {
            seat_of_event(e)
                .and_then(|s| back.get(&s))
                .is_some_and(|b| *b > e.global_seq)
        })
        .map(|e| e.event_id.clone())
        .collect()
}

fn event_card(e: &StoredEvent, rules: Option<&aib_notify::Rules>) -> Option<Card> {
    let slot = slot_of(&e.event_type, &e.payload, rules)?;
    let seat_id = seat_of_event(e);
    let session_id = text_of(e.payload.get("session_id"))
        .or_else(|| (e.aggregate_type == "session").then(|| e.aggregate_id.clone()));
    // a station move names its tree item and the two stations (who moved it stays out: it is a
    // page's key)
    let moved = (e.event_type == "node.set")
        .then(|| {
            let node = text_of(e.payload.get("node_id"))?;
            let to = text_of(e.payload.get("to"))?;
            let from = text_of(e.payload.get("from")).unwrap_or_default();
            Some((node, format!("換站:{from} → {to}")))
        })
        .flatten();
    let (card, item) = moved.map_or((None, None), |(n, i)| (Some(n), Some(i)));
    let item = item.or_else(|| {
        (e.event_type == "seat.turn_failed")
            .then(|| text_of(e.payload.get("reason")).map(|r| format!("沒做成:{r}")))
            .flatten()
    });
    let item = item.or_else(|| {
        (e.event_type == "seat.merge_unsafe")
            .then(|| text_of(e.payload.get("why")).map(|w| format!("合回停住:作者倉設定 {w}")))
            .flatten()
    });
    let item = item.or_else(|| {
        (e.event_type == "seat.merge_conflict").then(|| {
            let files: Vec<&str> = e.payload["files"]
                .as_array()
                .map(|a| a.iter().filter_map(Value::as_str).take(3).collect())
                .unwrap_or_default();
            format!("合回衝突:{}", files.join("、"))
        })
    });
    Some(Card {
        card_id: e.event_id.clone(),
        slot,
        name: NAMES[usize::from(slot - 1)],
        kind: e.event_type.clone(),
        seat_id: seat_id.clone(),
        session_id,
        at: e.occurred_at.clone(),
        global_seq: Some(e.global_seq),
        item,
        why: None,
        pct: None,
        card,
        rec: None,
        state: None,
        src: seat_id.clone(),
        tag: None,
        // 0eed7 VERIFY-turnfail L2: a turn not done opens the thread at the post that called it
        open: (e.event_type == "seat.turn_failed")
            .then(|| {
                Some(json!({"thread_id": text_of(e.payload.get("thread_id"))?,
                    "message_id": text_of(e.payload.get("reply_to"))?}))
            })
            .flatten(),
        actions: actions(slot),
    })
}

/// The open items of the 待人決策 list as slot-1 cards. A card's time is the `at` of the event
/// that opened its item (`opened`, [`opened_at`]; 0eed7 VERIFY-slots d9e6d5133 M1: the item's
/// `since` is month-day only, so a new item sorted below the old ones and `as_of` never counted
/// it); an item with no such time keeps `since`.
pub fn andy_cards(
    items: &[aib_drive::andy_open::Item],
    opened: &BTreeMap<String, String>,
) -> Vec<Card> {
    items
        .iter()
        .filter(|i| !i.done)
        .map(|i| {
            let field = |k: &str| i.fields.get(k).filter(|v| !v.is_empty()).cloned();
            // 「知道」 is a notice: it sits with slot 6 (SPEC §14)
            let slot = if i.item_kind == "知道" { 6 } else { 1 };
            Card {
                card_id: format!("{ANDY_PREFIX}{}", i.item),
                slot,
                name: NAMES[usize::from(slot - 1)],
                kind: "andy.open".into(),
                seat_id: None,
                session_id: None,
                at: opened
                    .get(&i.item)
                    .filter(|a| !a.is_empty())
                    .unwrap_or(&i.since)
                    .clone(),
                global_seq: None,
                item: field("text"),
                why: None,
                pct: None,
                card: Some(i.card.clone()).filter(|c| !c.is_empty()),
                rec: field("rec"),
                state: field("state"),
                src: field("src"),
                tag: Some(i.item_kind.clone()).filter(|k| !k.is_empty()),
                open: None,
                actions: actions(slot),
            }
        })
        .collect()
}

/// The schema of aib-drive's next list this reads.
pub const NEXT_SCHEMA: &str = "aib-drive.next.v1";

/// The slot an aib-drive next row is a card in: `fix` (紅或衝突: checks, receipt, a review's M,
/// a conflict) ⇒ 2 注意; `merge` / `push` ⇒ 5 可推進; anything else is work, not a card.
pub fn next_slot(kind: &str) -> Option<u8> {
    match kind {
        "fix" => Some(2),
        "merge" | "push" => Some(5),
        // N-ws-slot-sources 5 號: a head reviewed, receipt green and verified, ready to be put in
        // (換上); aib-drive's row (a9136), same fields as merge
        "install" => Some(5),
        _ => None,
    }
}

/// aib-drive's `fix` rows as slot-2 cards (SPEC §2 ② 注意) and its `merge` / `push` rows as
/// slot-5 cards (⑤ 可推進).
pub fn next_cards(next: &aib_drive::next::Next) -> Vec<Card> {
    if next.schema != NEXT_SCHEMA {
        return Vec::new();
    }
    next.rows
        .iter()
        .filter_map(|r| next_slot(&r.kind).map(|slot| (slot, r)))
        .map(|(slot, r)| {
            let key = r
                .node
                .clone()
                .or_else(|| r.pr.map(|n| format!("#{n}")))
                .or_else(|| r.branch.clone())
                .unwrap_or_else(|| r.what.clone());
            Card {
                card_id: format!("next:{}:{key}", r.kind),
                slot,
                name: NAMES[usize::from(slot) - 1],
                kind: format!("drive.{}", r.kind),
                seat_id: None,
                session_id: None,
                at: r.data_at.clone(),
                global_seq: None,
                item: Some(r.what.clone()),
                why: None,
                pct: None,
                card: r.node.clone(),
                rec: Some(r.why_now.clone()).filter(|w| !w.is_empty()),
                state: Some(r.needs.clone()).filter(|n| !n.is_empty() && n != "none"),
                src: r.to.clone(),
                tag: None,
                open: None,
                actions: actions(slot),
            }
        })
        .collect()
}

/// Messages to a seat still waiting to be written to it (seat_send / a shared conversation's
/// post, [`crate::seat_send::waits_to_be_written`]) since before `cutoff_unix`: one slot-4 card
/// per seat (SPEC §2 ④ 信排隊沒送到), from the oldest, with how many wait.
pub fn mail_cards(rows: &[aib_store::MessageRow], cutoff_unix: i64) -> Vec<Card> {
    let mut by: BTreeMap<&str, Vec<&aib_store::MessageRow>> = BTreeMap::new();
    for r in rows
        .iter()
        .filter(|r| crate::seat_send::waits_to_be_written(r))
    {
        if let Some(seat) = r.to_key.strip_prefix("seat:") {
            by.entry(seat).or_default().push(r);
        }
    }
    by.into_iter()
        .filter_map(|(seat, rs)| {
            let oldest = rs.iter().min_by(|a, b| {
                (a.created_unix, &a.message_id).cmp(&(b.created_unix, &b.message_id))
            })?;
            (oldest.created_unix < cutoff_unix).then(|| Card {
                card_id: format!("mailq:{seat}:{}", oldest.message_id),
                slot: 4,
                name: NAMES[3],
                kind: "message.queued".into(),
                seat_id: Some(seat.to_owned()),
                session_id: None,
                at: oldest.created.clone(),
                global_seq: None,
                item: Some(format!("{} 封信排隊沒送到", rs.len())),
                why: None,
                pct: None,
                card: None,
                rec: None,
                state: Some(format!("最舊一封排了超過 {STUCK_MIN} 分鐘")),
                src: oldest.from_key.strip_prefix("seat:").map(str::to_owned),
                tag: None,
                open: None,
                actions: actions(4),
            })
        })
        .chain(unread_cards(rows, cutoff_unix))
        .collect()
}

/// A message written to a seat ([`crate::seat_send`] `delivered_at`) and still not read past
/// `cutoff_unix`: one slot-4 card per seat (043dd 11-18 20:48: the page counted it on its own
/// before; aibd gives it now), card id `mailunread:<seat>:<oldest>`.
pub fn unread_cards(rows: &[aib_store::MessageRow], cutoff_unix: i64) -> Vec<Card> {
    let mut by: BTreeMap<&str, Vec<(i64, &aib_store::MessageRow)>> = BTreeMap::new();
    for r in rows {
        let delivered = r.item.get("status").and_then(Value::as_str) == Some("delivered");
        let Some(at) = r.item.get("delivered_at").and_then(Value::as_i64) else {
            continue;
        };
        if let (true, Some(seat)) = (delivered, r.to_key.strip_prefix("seat:")) {
            by.entry(seat).or_default().push((at, r));
        }
    }
    by.into_iter()
        .filter_map(|(seat, rs)| {
            let (at, oldest) = rs
                .iter()
                .min_by(|a, b| (a.0, &a.1.message_id).cmp(&(b.0, &b.1.message_id)))?;
            (*at < cutoff_unix).then(|| Card {
                card_id: format!("mailunread:{seat}:{}", oldest.message_id),
                slot: 4,
                name: NAMES[3],
                kind: "message.unread".into(),
                seat_id: Some(seat.to_owned()),
                session_id: None,
                at: crate::sessions::rfc3339_of_unix(*at),
                global_seq: None,
                item: Some(format!("{} 封信送到沒讀", rs.len())),
                why: None,
                pct: None,
                card: None,
                rec: None,
                state: Some(format!("最舊一封送到超過 {STUCK_MIN} 分鐘還沒讀")),
                src: oldest.from_key.strip_prefix("seat:").map(str::to_owned),
                tag: None,
                open: None,
                actions: actions(4),
            })
        })
        .collect()
}

/// Every seat's waiting messages (the page of each seat's open messages), in one query.
fn waiting_mail(st: &AppState) -> Vec<aib_store::MessageRow> {
    let Ok(metas) = st.store.seat_metas() else {
        return Vec::new();
    };
    let keys: Vec<String> = metas
        .iter()
        .map(|m| format!("seat:{}", m.seat_id))
        .collect();
    st.messages
        .open_to_each(&keys, aib_store::PAGE_MAX)
        .unwrap_or_default()
}

/// The progress tree's node ids, in its order (none when there is no tree).
pub fn tree_ids() -> Vec<String> {
    crate::workspace::current_tree()
        .and_then(|t| {
            t.get("nodes").and_then(Value::as_array).map(|ns| {
                ns.iter()
                    .filter_map(|n| n.get("id").and_then(Value::as_str))
                    .filter(|id| !id.is_empty())
                    .map(str::to_owned)
                    .collect()
            })
        })
        .unwrap_or_default()
}

/// The first node id `text` names, by the board's rule (stations-gen.js usageData `idRx`): the
/// id with no letter, digit or `-` right before or after it, ids tried in the tree's order.
pub fn node_in(text: &str, ids: &[String]) -> Option<String> {
    let edge = |c: Option<char>| c.is_none_or(|c| !(c.is_ascii_alphanumeric() || c == '-'));
    ids.iter()
        .find(|id| {
            text.match_indices(id.as_str()).any(|(i, m)| {
                edge(text[..i].chars().next_back()) && edge(text[i + m.len()..].chars().next())
            })
        })
        .cloned()
}

/// SPEC §2 「每張卡帶 node id」: a card with no node of its own and a seat takes the node its
/// seat's status line names now.
pub fn attach_nodes(cards: &mut [Card], lines: &BTreeMap<String, Now>, ids: &[String]) {
    if ids.is_empty() {
        return;
    }
    for c in cards.iter_mut().filter(|c| c.card.is_none()) {
        let line = c.seat_id.as_ref().and_then(|s| lines.get(s));
        c.card = line.and_then(|n| node_in(&n.item, ids));
    }
}

/// aib-drive's next list now (`None` when it cannot be read).
pub fn next_list() -> Option<aib_drive::next::Next> {
    let p = state_root()?.join("drive").join("act-next.json");
    let t = std::fs::read_to_string(p).ok()?;
    serde_json::from_str(&t).ok()
}

/// The 1.x state root the 待人決策 list lives under (aib-drive's rule).
pub(crate) fn state_root() -> Option<std::path::PathBuf> {
    aib_drive::observe::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(std::path::PathBuf::from)
            .as_deref(),
    )
    .ok()
}

/// The 待人決策 list now (empty when it cannot be read).
pub fn andy_items() -> Vec<aib_drive::andy_open::Item> {
    andy_list().0
}

/// The 待人決策 list now and when each item was last opened ([`opened_at`]).
pub fn andy_list() -> (Vec<aib_drive::andy_open::Item>, BTreeMap<String, String>) {
    let Some(root) = state_root() else {
        return Default::default();
    };
    match aib_drive::andy_open::read_events(&root) {
        Ok((evs, _)) => (aib_drive::andy_open::replay(&evs).items, opened_at(&evs)),
        Err(_) => Default::default(),
    }
}

/// Each item's last `open` event's `at` (ISO), in file order as the replay reads them.
pub fn opened_at(evs: &[Value]) -> BTreeMap<String, String> {
    let s = |e: &Value, k: &str| e.get(k).and_then(Value::as_str).map(str::to_owned);
    evs.iter()
        .filter(|e| s(e, "kind").as_deref() == Some(aib_drive::andy_open::KIND))
        .filter(|e| s(e, "outcome").as_deref() == Some("open"))
        .filter_map(|e| Some((s(e, "item")?, s(e, "at")?)))
        .collect()
}

/// The cards read from the seats' state now, not from events: a context at the hand-over line
/// (slot 3, a new card when it crosses [`crate::usage::RED_PCT`]); a seat waiting on another
/// seat with no new line since `cutoff` (slot 4); a busy seat whose session wrote nothing since
/// `cutoff` (slot 4; a seat aibd does not run has no output to judge and gets none). `cutoff`
/// is an RFC 3339 time in aibd's own form, so it compares as text.
pub fn live_cards(
    lines: &BTreeMap<String, Now>,
    usage: &[crate::usage::Usage],
    cutoff: &str,
) -> Vec<Card> {
    let mut out = Vec::new();
    for u in usage {
        let (Some(p), Some(at)) = (u.pct, u.at.as_ref()) else {
            continue;
        };
        if p < crate::usage::HANDOFF_PCT {
            continue;
        }
        let line = if p >= crate::usage::RED_PCT {
            crate::usage::RED_PCT
        } else {
            crate::usage::HANDOFF_PCT
        };
        out.push(Card {
            card_id: format!("usage:{}:{line}", u.session_id),
            slot: 3,
            name: NAMES[2],
            kind: "seat.usage".into(),
            seat_id: u.seat_id.clone(),
            session_id: Some(u.session_id.clone()),
            at: at.clone(),
            global_seq: None,
            item: None,
            why: None,
            pct: Some(p),
            card: None,
            rec: None,
            state: None,
            src: u.seat_id.clone(),
            tag: None,
            open: None,
            actions: actions(3),
        });
    }
    for (seat, n) in lines {
        let quiet = || {
            usage
                .iter()
                .filter(|u| u.seat_id.as_deref() == Some(seat.as_str()))
                .filter_map(|u| u.last_output_at.as_deref())
                .max()
                .filter(|last| *last < cutoff)
                .map(str::to_owned)
        };
        let (id, kind, at) = match n.state {
            "reply" if n.updated_at.as_str() < cutoff => (
                format!("stuck:{seat}:{}", n.updated_at),
                "seat.waiting",
                n.updated_at.clone(),
            ),
            "busy" if n.updated_at.as_str() < cutoff => match quiet() {
                Some(last) => (format!("quiet:{seat}:{last}"), "seat.quiet", last),
                None => continue,
            },
            _ => continue,
        };
        out.push(Card {
            card_id: id,
            slot: 4,
            name: NAMES[3],
            kind: kind.into(),
            seat_id: Some(seat.clone()),
            session_id: None,
            at,
            global_seq: None,
            item: Some(n.item.clone()),
            why: n.why.clone(),
            pct: None,
            card: None,
            rec: None,
            state: Some(
                if kind == "seat.waiting" {
                    "等別席回覆超過 30 分鐘"
                } else {
                    "忙碌但 30 分鐘沒有輸出"
                }
                .to_owned(),
            ),
            src: Some(seat.clone()),
            tag: None,
            open: None,
            actions: actions(4),
        });
    }
    out
}

/// Every card: the seats' `decide` lines and the events, less the ones taken off; slot first,
/// newest first within a slot; at most [`KEEP_DONE`] notices and [`MAX_CARDS`] in all.
pub fn cards(
    lines: &BTreeMap<String, Now>,
    events: &[StoredEvent],
    live: &[Card],
    taken: &BTreeSet<String>,
    rules: Option<&aib_notify::Rules>,
) -> Vec<Card> {
    let back = rejoins_since_back(events);
    let ended = end_reasons(events);
    let mut out: Vec<Card> = lines
        .iter()
        .filter(|(_, n)| n.state == "decide")
        .map(|(seat, n)| Card {
            card_id: now_card_id(seat, &n.updated_at),
            slot: 1,
            name: NAMES[0],
            kind: "seat.now".into(),
            seat_id: Some(seat.clone()),
            session_id: None,
            at: n.updated_at.clone(),
            global_seq: None,
            item: Some(n.item.clone()),
            why: n.why.clone(),
            pct: None,
            card: None,
            rec: None,
            state: n.why.clone(),
            src: Some(seat.clone()),
            tag: None,
            open: None,
            actions: actions(1),
        })
        .chain(live.iter().cloned())
        .chain(events.iter().filter_map(|e| event_card(e, rules)))
        .map(|c| low_power_end(c, &ended))
        .filter(|c| !taken.contains(&c.card_id))
        .filter(|c| !back.contains(&c.card_id))
        .collect();
    out.sort_by(|a, b| {
        (a.slot, Reverse(&a.at), Reverse(a.global_seq), &a.card_id).cmp(&(
            b.slot,
            Reverse(&b.at),
            Reverse(b.global_seq),
            &b.card_id,
        ))
    });
    let mut done = 0;
    out.retain(|c| {
        if c.slot != 6 {
            return true;
        }
        done += 1;
        done <= KEEP_DONE
    });
    out.truncate(MAX_CARDS);
    out
}

/// Card ids answered or dismissed (this process); `true` = dismissed (「先收起」, can be put
/// back), `false` = answered.
#[derive(Default)]
pub struct Taken {
    inner: Mutex<BTreeMap<String, bool>>,
}

impl Taken {
    fn lock(&self) -> MutexGuard<'_, BTreeMap<String, bool>> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `false` if it was taken already (a second answer to the same card).
    pub fn take(&self, card_id: &str) -> bool {
        self.put(card_id, false)
    }

    /// [`Self::take`] for a dismissal: the card can be restored.
    pub fn dismiss(&self, card_id: &str) -> bool {
        self.put(card_id, true)
    }

    /// Put a dismissed card back; `false` if it is not dismissed (never was, answered, or back).
    pub fn restore(&self, card_id: &str) -> bool {
        let mut g = self.lock();
        if g.get(card_id) == Some(&true) {
            g.remove(card_id);
            return true;
        }
        false
    }

    /// The dismissed card ids.
    pub fn dismissed(&self) -> BTreeSet<String> {
        self.lock()
            .iter()
            .filter(|(_, d)| **d)
            .map(|(k, _)| k.clone())
            .collect()
    }

    fn put(&self, card_id: &str, dismissed: bool) -> bool {
        let mut g = self.lock();
        if g.len() >= MAX_TAKEN && !g.contains_key(card_id) {
            // the oldest cards have left the last SCAN events long ago; drop a few to make room
            let drop: Vec<String> = g.keys().take(MAX_TAKEN / 8).cloned().collect();
            for d in drop {
                g.remove(&d);
            }
        }
        if g.contains_key(card_id) {
            return false;
        }
        g.insert(card_id.to_owned(), dismissed);
        true
    }

    /// Replay journaled `slot.dismissed` / `slot.restored` (oldest first) so a card taken off
    /// stays off after aibd restarts (N-ws-dismiss-persist). Returns how many stay dismissed.
    pub fn replay(&self, events: &[StoredEvent]) -> usize {
        for e in events {
            if e.event_type == "review.seen" {
                if let Some(l) = e.payload.get("line_id").and_then(Value::as_str) {
                    crate::consent_cards::see(l);
                }
                continue;
            }
            let Some(card) = e.payload.get("card_id").and_then(Value::as_str) else {
                continue;
            };
            if !valid_card_id(card) {
                continue;
            }
            match e.event_type.as_str() {
                "slot.dismissed" => {
                    self.dismiss(card);
                }
                "slot.restored" => {
                    self.restore(card);
                }
                "idea.answered" => {
                    self.take(card);
                }
                "slot.pinned" => {
                    let node = e.payload.get("node_id").and_then(Value::as_str);
                    pins().set(card, node.filter(|n| valid_card_id(n)));
                }
                _ => {}
            }
        }
        self.dismissed().len()
    }

    /// Forget everything (a start begins from the journal, as a new process would).
    fn clear(&self) {
        self.lock().clear();
    }

    /// Put a card back (the answer could not be typed).
    pub fn give_back(&self, card_id: &str) {
        self.lock().remove(card_id);
    }

    pub fn all(&self) -> BTreeSet<String> {
        self.lock().keys().cloned().collect()
    }
}

static TAKEN: LazyLock<Taken> = LazyLock::new(Taken::default);

pub fn taken() -> &'static Taken {
    &TAKEN
}

/// The plan item each pinned card hangs on (N-ws-event-pin; replayed at start from
/// `slot.pinned`).
#[derive(Default)]
pub struct Pins {
    inner: Mutex<BTreeMap<String, String>>,
}

impl Pins {
    fn lock(&self) -> MutexGuard<'_, BTreeMap<String, String>> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Hang `card` on `node`, or take it off (`None`).
    pub fn set(&self, card: &str, node: Option<&str>) {
        let mut g = self.lock();
        match node {
            Some(n) => {
                if g.len() >= MAX_TAKEN && !g.contains_key(card) {
                    let drop: Vec<String> = g.keys().take(MAX_TAKEN / 8).cloned().collect();
                    for d in drop {
                        g.remove(&d);
                    }
                }
                g.insert(card.to_owned(), n.to_owned());
            }
            None => {
                g.remove(card);
            }
        }
    }

    pub fn get(&self, card: &str) -> Option<String> {
        self.lock().get(card).cloned()
    }

    fn clear(&self) {
        self.lock().clear();
    }
}

static PINS: LazyLock<Pins> = LazyLock::new(Pins::default);

pub fn pins() -> &'static Pins {
    &PINS
}

/// The slot events [`Taken::replay`] reads at start.
pub const TAKEN_EVENTS: [&str; 5] = [
    "slot.dismissed",
    "slot.restored",
    "slot.pinned",
    "idea.answered",
    "review.seen",
];

/// At start: the dismissals journaled before, back in [`taken`] (twice [`MAX_TAKEN`] events at
/// most, the newest).
pub fn reload_taken(store: &aib_store::Store) -> aib_store::Result<usize> {
    let max = u32::try_from(MAX_TAKEN * 2).unwrap_or(u32::MAX);
    let events = store.events_of_types(&TAKEN_EVENTS, max)?;
    // one process may start aibd again (the tests do): begin from the journal only
    taken().clear();
    pins().clear();
    crate::consent_cards::clear_seen();
    Ok(taken().replay(&events))
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// `GET /api/v1/slots`: `{names, slots: [card…]}`.
pub async fn list(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Query(q): Query<PageQuery>,
) -> Response {
    let paging = match Paging::of(&q) {
        Ok(p) => p,
        Err(why) => return refuse(StatusCode::BAD_REQUEST, "bad_slots_query", why),
    };
    let words = crate::workspace::gives_words(&c).await;
    let st2 = st.clone();
    let read = tokio::task::spawn_blocking(move || {
        st2.store.events_last(SCAN).map(|e| {
            let ids = tree_ids();
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX));
            let mut dm = crate::dm_cards::cards(&st2.messages, now);
            dm.extend(crate::ledger_cards::cards(now));
            dm.extend(crate::consent_cards::cards());
            (e, andy_list(), next_list(), ids, waiting_mail(&st2), dm)
        })
    });
    let (events, andy, next, ids, mail, dm) = match read.await {
        Ok(Ok(v)) => v,
        _ => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "events read failed",
            )
        }
    };
    let lines = crate::seat_now::board().all();
    let usage = crate::usage::board().all();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX));
    let cutoff = crate::sessions::rfc3339_of_unix(now.saturating_sub(STUCK_MIN * 60));
    let mut live = live_cards(&lines, &usage, &cutoff);
    live.extend(andy_cards(&andy.0, &andy.1));
    live.extend(next.as_ref().map(next_cards).unwrap_or_default());
    live.extend(mail_cards(&mail, now.saturating_sub(STUCK_MIN * 60)));
    live.extend(dm);
    live.extend(crate::asks::cards(&events));
    if paging.dismissed {
        // 「先收起」還原 (043dd 11-18 22:34): the cards the page took off, as they would show
        let off = taken().dismissed();
        let mut back = cards(&lines, &events, &live, &BTreeSet::new(), rules());
        back.retain(|c| off.contains(&c.card_id));
        attach_nodes(&mut back, &lines, &ids);
        let out: Vec<Value> = back
            .iter()
            .map(|c| {
                let mut v = c.view();
                if !words {
                    hide_words(&mut v);
                }
                v
            })
            .collect();
        return Json(json!({ "names": NAMES, "tags": TAGS, "dismissed": true, "slots": out }))
            .into_response();
    }
    let mut all = cards(&lines, &events, &live, &taken().all(), rules());
    attach_nodes(&mut all, &lines, &ids);
    let (shown, meta) = page_of(&all, &paging);
    let out: Vec<Value> = shown
        .iter()
        .map(|c| {
            let mut v = c.view();
            if !words {
                hide_words(&mut v);
            }
            v
        })
        .collect();
    let mut body = json!({ "names": NAMES, "tags": TAGS, "slots": out });
    if let (Some(o), Value::Object(m)) = (body.as_object_mut(), meta) {
        o.extend(m);
    }
    Json(body).into_response()
}

/// Most cards on one page of `GET /api/v1/slots?page=` (Andy 11-18 21:0x: 一頁 10 則).
pub const PER_PAGE: usize = 10;
/// The longest `as_of` a query may carry.
pub const MAX_AS_OF: usize = 64;

/// `GET /api/v1/slots` query (SPEC §21, N-ws-events-group): `slot` 1..=6 (one class), `page`
/// from 1, `per` 1..=[`PER_PAGE`], `as_of` (a time the page saw first). All optional; none ⇒
/// every card, as before.
#[derive(Debug, Default, serde::Deserialize)]
pub struct PageQuery {
    pub slot: Option<String>,
    pub page: Option<String>,
    pub per: Option<String>,
    pub as_of: Option<String>,
    /// `1`: the dismissed cards instead (043dd 11-18 22:34).
    pub dismissed: Option<String>,
}

/// A checked [`PageQuery`].
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Paging {
    pub slot: Option<u8>,
    /// `None`: no paging asked (every card, as before).
    pub page: Option<usize>,
    pub per: usize,
    pub as_of: Option<String>,
    /// The dismissed cards, every one, no paging.
    pub dismissed: bool,
}

impl Paging {
    /// Checks the query; the error is the one-line reason.
    pub fn of(q: &PageQuery) -> Result<Paging, &'static str> {
        let num = |s: &Option<String>,
                   lo: usize,
                   hi: usize,
                   why: &'static str|
         -> Result<Option<usize>, &'static str> {
            match s.as_deref() {
                None => Ok(None),
                Some(t) => match t.parse::<usize>() {
                    Ok(n) if (lo..=hi).contains(&n) && t.bytes().all(|b| b.is_ascii_digit()) => {
                        Ok(Some(n))
                    }
                    _ => Err(why),
                },
            }
        };
        let slot = num(&q.slot, 1, 6, "slot is 1 to 6")?.and_then(|n| u8::try_from(n).ok());
        let page = num(&q.page, 1, MAX_CARDS, "page is a whole number from 1")?;
        let per = num(&q.per, 1, PER_PAGE, "per is 1 to 10")?;
        let as_of = match q.as_of.as_deref() {
            None => None,
            Some(t)
                if !t.is_empty()
                    && t.chars().count() <= MAX_AS_OF
                    && !t.chars().any(char::is_control) =>
            {
                Some(t.to_owned())
            }
            Some(_) => return Err("as_of is a time the list gave, 1 to 64 characters"),
        };
        let dismissed = match q.dismissed.as_deref() {
            None => false,
            Some("1") => true,
            Some(_) => return Err("dismissed is 1"),
        };
        let paged = slot.is_some() || page.is_some() || per.is_some() || as_of.is_some();
        Ok(Paging {
            slot,
            page: paged.then(|| page.unwrap_or(1)),
            per: per.unwrap_or(PER_PAGE),
            as_of,
            dismissed,
        })
    }
}

/// The cards one page shows and the list's counts (SPEC §21): `counts` per slot and `total`
/// (every card now, for the class tabs), `as_of` (the newest card's time: send it back to keep
/// the pages still). Paged (any query field): `slot`, `page`, `per`, `pages`, `listed` (cards in
/// that class up to `as_of`) and `newer` (cards that came in after `as_of`: left off the pages,
/// so a page being read does not move; a fresh read without `as_of` shows them).
pub fn page_of<'a>(all: &'a [Card], p: &Paging) -> (Vec<&'a Card>, Value) {
    let mut counts = serde_json::Map::new();
    for s in 1..=6u8 {
        let n = all.iter().filter(|c| c.slot == s).count();
        counts.insert(s.to_string(), json!(n));
    }
    let newest = all
        .iter()
        .map(|c| c.at.as_str())
        .max()
        .unwrap_or("")
        .to_owned();
    // the same per slot, `now` cards only: what the person is waiting on (history apart)
    let mut counts_now = serde_json::Map::new();
    for s in 1..=6u8 {
        let n = all
            .iter()
            .filter(|c| c.slot == s && c.origin() == "now")
            .count();
        counts_now.insert(s.to_string(), json!(n));
    }
    let mut meta = json!({"counts": counts, "counts_now": counts_now, "total": all.len()});
    let Some(page) = p.page else {
        meta["as_of"] = json!(newest);
        return (all.iter().collect(), meta);
    };
    let as_of = p.as_of.clone().unwrap_or(newest);
    let class: Vec<&Card> = all
        .iter()
        .filter(|c| p.slot.is_none_or(|s| c.slot == s))
        .collect();
    let newer = class
        .iter()
        .filter(|c| c.at.as_str() > as_of.as_str())
        .count();
    let listed: Vec<&Card> = class
        .into_iter()
        .filter(|c| c.at.as_str() <= as_of.as_str())
        .collect();
    let pages = listed.len().div_ceil(p.per);
    let shown: Vec<&Card> = listed
        .iter()
        .skip((page - 1).saturating_mul(p.per))
        .take(p.per)
        .copied()
        .collect();
    meta["as_of"] = json!(as_of);
    meta["slot"] = json!(p.slot);
    meta["page"] = json!(page);
    meta["per"] = json!(p.per);
    meta["pages"] = json!(pages);
    meta["listed"] = json!(listed.len());
    meta["newer"] = json!(newer);
    (shown, meta)
}

/// The fields of a 待人決策 card that carry its words.
pub const ANDY_WORDS: [&str; 6] = ["item", "text", "rec", "state", "src", "why"];

/// 519d0 r16 (5d33a N1): a user folder page sees a 待人決策 card's id, node, kind, level and
/// time, and of its words only length and SHA-256 (the tree's `need` rule); the words go to a
/// person's built-in page only.
pub fn hide_words(v: &mut Value) {
    if v.get("kind").and_then(Value::as_str) != Some("andy.open") {
        // a 1.x letter's first line is a seat's words too (7ebca 11-19, dm_cards)
        let dm = v.get("kind").and_then(Value::as_str).is_some_and(|k| {
            k.starts_with("dm.") || k.starts_with("ledger.") || k.starts_with("idea.")
        });
        if v.get("kind").and_then(Value::as_str) == Some("andy.review_list") {
            crate::consent_cards::hide_list(v);
            return;
        }
        if !dm {
            return;
        }
    }
    let Some(o) = v.as_object_mut() else { return };
    for k in ANDY_WORDS {
        if let Some(s) = o.get(k).and_then(Value::as_str).map(str::to_owned) {
            o.insert(k.into(), crate::workspace::words_or_hash(&s, false));
        }
    }
}

/// A card id a body may carry: 1..=[`MAX_ID`] characters, no control characters.
pub fn valid_card_id(s: &str) -> bool {
    !s.is_empty() && s.chars().count() <= MAX_ID && !s.chars().any(char::is_control)
}

fn only_fields(body: &Value, allowed: &[&str]) -> bool {
    body.as_object()
        .is_some_and(|o| o.keys().all(|k| allowed.contains(&k.as_str())))
}

pub(crate) async fn journal(st: &AppState, kind: &str, payload: Value) -> bool {
    let Ok(event_id) = crate::sessions::new_id("ev_") else {
        return false;
    };
    let event = NewEvent {
        event_id: event_id.clone(),
        aggregate_type: "slot".into(),
        aggregate_id: event_id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    let store = st.store.clone();
    matches!(
        tokio::task::spawn_blocking(move || store.append(vec![event])).await,
        Ok(Ok(_))
    )
}

/// act `decision_answer {seat_id, answer, card_id?}` (a confirmed page).
pub async fn answer(st: AppState, page: PageAccess, body: Value) -> Response {
    if !only_fields(&body, &["type", "seat_id", "answer", "card_id"]) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, answer, card_id?}",
        );
    }
    let seat = body.get("seat_id").and_then(Value::as_str);
    let Some(text) = body.get("answer").and_then(Value::as_str) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, answer, card_id?}",
        );
    };
    if let Some(item) = body
        .get("card_id")
        .and_then(Value::as_str)
        .and_then(|c| c.strip_prefix(ANDY_PREFIX))
    {
        return answer_andy(st, page, seat, text, item.to_owned()).await;
    }
    // 519d0 11-19 (5): a seat's ask_person card takes one of two fixed answers
    if let Some(card) = body
        .get("card_id")
        .and_then(Value::as_str)
        .filter(|c| c.starts_with(crate::asks::PREFIX))
    {
        return crate::asks::answer(st, page, card, seat, text).await;
    }
    let Some(seat_id) = seat else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, answer, card_id?}",
        );
    };
    let Some(line) = crate::seat_now::board()
        .get(seat_id)
        .filter(|n| n.state == "decide")
    else {
        return refuse(
            StatusCode::CONFLICT,
            "no_decision",
            "this seat is not waiting for a decision",
        );
    };
    let card = now_card_id(seat_id, &line.updated_at);
    match body.get("card_id") {
        None | Some(Value::Null) => {}
        Some(c) if c.as_str() == Some(card.as_str()) => {}
        Some(_) => {
            return refuse(
                StatusCode::CONFLICT,
                "decision_changed",
                "the seat has written another line since this card",
            )
        }
    }
    if !taken().take(&card) {
        return refuse(
            StatusCode::CONFLICT,
            "already_answered",
            "this decision was answered or put aside already",
        );
    }
    let typed = match crate::seat_input::type_to_seat(&st, &page, seat_id, text).await {
        Ok(t) => t,
        Err(r) => {
            taken().give_back(&card);
            return *r;
        }
    };
    let journaled = journal(
        &st,
        "decision.answered",
        json!({
            "seat_id": seat_id,
            "session_id": typed.session_id,
            "card_id": card,
            "len": typed.len,
            "sha256": typed.digest,
            "mode": typed.mode,
            "delivery": if typed.late { "pending" } else { "written" },
        }),
    )
    .await;
    let status = if typed.late {
        StatusCode::ACCEPTED
    } else {
        StatusCode::OK
    };
    (
        status,
        Json(json!({
            "card_id": card,
            "session_id": typed.session_id,
            "delivery": if typed.late { "pending" } else { "written" },
            "journaled": journaled,
        })),
    )
        .into_response()
}

/// `decision_answer` on a 待人決策 card (SPEC §14): the item must be open; when the page names
/// a seat (the one that keeps the list) the answer is typed into it; the item is then closed on
/// the same list (an aib-drive `andy_open` close, `state` = 已答(頁面)), so the board loses it
/// too. The words are never journaled or written to the list.
async fn answer_andy(
    st: AppState,
    page: PageAccess,
    seat_id: Option<&str>,
    text: &str,
    item: String,
) -> Response {
    let card = format!("{ANDY_PREFIX}{item}");
    let it = item.clone();
    let open =
        tokio::task::spawn_blocking(move || andy_items().iter().any(|i| i.item == it && !i.done))
            .await
            .unwrap_or(false);
    if !open {
        return refuse(
            StatusCode::CONFLICT,
            "no_decision",
            "this item is not open on the 待人決策 list",
        );
    }
    if !taken().take(card.as_str()) {
        return refuse(
            StatusCode::CONFLICT,
            "already_answered",
            "this decision was answered or put aside already",
        );
    }
    let typed = match seat_id {
        Some(seat) => match crate::seat_input::type_to_seat(&st, &page, seat, text).await {
            Ok(t) => Some(t),
            Err(r) => {
                taken().give_back(&card);
                return *r;
            }
        },
        None => None,
    };
    let closed = tokio::task::spawn_blocking(move || close_andy(&item))
        .await
        .unwrap_or(false);
    let delivery = match &typed {
        Some(t) if t.late => "pending",
        Some(_) => "written",
        None => "none",
    };
    let journaled = journal(
        &st,
        "decision.answered",
        json!({
            "seat_id": seat_id,
            "session_id": typed.as_ref().map(|t| t.session_id.clone()),
            "card_id": card,
            "len": text.len(),
            "sha256": typed.as_ref().map(|t| t.digest.clone()),
            "mode": typed.as_ref().and_then(|t| t.mode.clone()),
            "delivery": delivery,
            "closed": closed,
        }),
    )
    .await;
    let status = if typed.as_ref().is_some_and(|t| t.late) {
        StatusCode::ACCEPTED
    } else {
        StatusCode::OK
    };
    (
        status,
        Json(json!({
            "card_id": card,
            "session_id": typed.as_ref().map(|t| t.session_id.clone()),
            "delivery": delivery,
            "closed": closed,
            "journaled": journaled,
        })),
    )
        .into_response()
}

/// Close one item on the 待人決策 list the way `aib-drive andy-open close` does.
fn close_andy(item: &str) -> bool {
    let Some(root) = state_root() else {
        return false;
    };
    let ctx = aib_ledger::events::Ctx::for_run(&root, None);
    let args: Vec<String> = ["close", "--item", item, "--state", "已答(頁面)"]
        .iter()
        .map(|a| (*a).to_owned())
        .collect();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX));
    aib_drive::andy_open::run(&args, &root, &ctx, now).0 == 0
}

/// act `slot_dismiss {card_id}` (a confirmed page).
pub async fn dismiss(st: AppState, body: Value) -> Response {
    let card = body.get("card_id").and_then(Value::as_str);
    let Some(card) = card.filter(|c| only_fields(&body, &["type", "card_id"]) && valid_card_id(c))
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, card_id} with a card id of 1-160 characters on one line",
        );
    };
    let first = taken().dismiss(card);
    let journaled = if first {
        journal(&st, "slot.dismissed", json!({ "card_id": card })).await
    } else {
        true
    };
    Json(json!({ "card_id": card, "dismissed": true, "already": !first, "journaled": journaled }))
        .into_response()
}

/// act `slot_pin {card_id, node_id}` (a confirmed built-in page, like `slot_dismiss`): hangs a
/// card on a plan item; `node_id: null` takes it off. 404 `node_not_found` when the item is not
/// on the tree. GET slots then gives the card `pin: <node_id>`.
pub async fn pin(st: AppState, body: Value) -> Response {
    let card = body.get("card_id").and_then(Value::as_str);
    let node = match body.get("node_id") {
        Some(Value::Null) => Some(None),
        Some(Value::String(n)) if valid_card_id(n) => Some(Some(n.clone())),
        _ => None,
    };
    let (Some(card), Some(node)) = (
        card.filter(|c| valid_card_id(c) && only_fields(&body, &["type", "card_id", "node_id"])),
        node,
    ) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, card_id, node_id} with node_id a plan item id or null",
        );
    };
    if let Some(n) = &node {
        let n2 = n.clone();
        let on_tree = tokio::task::spawn_blocking(move || tree_ids().contains(&n2))
            .await
            .unwrap_or(false);
        if !on_tree {
            return refuse(StatusCode::NOT_FOUND, "node_not_found", "no such plan item");
        }
    }
    pins().set(card, node.as_deref());
    let journaled = journal(
        &st,
        "slot.pinned",
        json!({ "card_id": card, "node_id": node }),
    )
    .await;
    Json(json!({ "card_id": card, "node_id": node, "journaled": journaled })).into_response()
}

/// act `slot_restore {card_id}` (a confirmed built-in page, like `slot_dismiss`): puts a dismissed
/// card back on the list. 404 `slot_not_dismissed` when that card is not dismissed (never was,
/// was answered, or is back already).
pub async fn restore(st: AppState, body: Value) -> Response {
    let card = body.get("card_id").and_then(Value::as_str);
    let Some(card) = card.filter(|c| valid_card_id(c) && only_fields(&body, &["type", "card_id"]))
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, card_id} with a card id of 1-160 characters on one line",
        );
    };
    if !taken().restore(card) {
        return refuse(
            StatusCode::NOT_FOUND,
            "slot_not_dismissed",
            "that card is not taken off",
        );
    }
    let journaled = journal(&st, "slot.restored", json!({ "card_id": card })).await;
    Json(json!({ "card_id": card, "restored": true, "journaled": journaled })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(
        seq: i64,
        kind: &str,
        agg_type: &str,
        agg: &str,
        at: &str,
        payload: Value,
    ) -> StoredEvent {
        StoredEvent {
            global_seq: seq,
            event_id: format!("ev_{seq}"),
            aggregate_type: agg_type.into(),
            aggregate_id: agg.into(),
            sequence: 1,
            event_type: kind.into(),
            schema_version: 1,
            correlation_id: None,
            occurred_at: at.into(),
            payload,
        }
    }

    /// N-ws-slot-sources 3 號: an exit whose last output says the usage ran out or a login is
    /// needed is slot 3 with fixed words; other exits stay notices; the output never shows.
    #[test]
    fn a_session_that_ended_for_its_usage_or_a_login_is_low_power() {
        let at = "2026-11-19T00:00:00.000Z";
        let out = |seq, sid: &str, line: &str| {
            ev(
                seq,
                "session.output",
                "session_output",
                &format!("{sid}/output"),
                at,
                json!({"line": line, "stream": "stdout", "cut": false}),
            )
        };
        let exit = |seq, sid: &str| {
            ev(
                seq,
                "session.exited",
                "session",
                sid,
                at,
                json!({"exit_code": 0}),
            )
        };
        let es = [
            out(1, "s_a", "working… sk-secret-in-output"),
            out(
                2,
                "s_a",
                "Claude usage limit reached. Your limit will reset at 5pm",
            ),
            exit(3, "s_a"),
            out(4, "s_b", "Invalid API key · Please run /login"),
            exit(5, "s_b"),
            out(6, "s_c", "all done"),
            exit(7, "s_c"),
        ];
        let c = cards(&BTreeMap::new(), &es, &[], &BTreeSet::new(), None);
        let by = |sid: &str| {
            c.iter()
                .find(|c| c.session_id.as_deref() == Some(sid))
                .unwrap_or_else(|| panic!("reason: no_card {sid}"))
        };
        assert_eq!(
            (by("s_a").slot, by("s_a").why.as_deref()),
            (3, Some("額度"))
        );
        assert_eq!(by("s_a").view()["text"], "結束:額度用完");
        assert_eq!(
            (by("s_b").slot, by("s_b").why.as_deref()),
            (3, Some("登入")),
            "reason: login_first"
        );
        assert_eq!(by("s_b").view()["text"], "結束:要重新登入");
        assert_eq!(by("s_c").slot, 6, "reason: plain_exit_low_power");
        let all = serde_json::to_string(&c.iter().map(Card::view).collect::<Vec<_>>()).unwrap();
        assert!(
            !all.contains("sk-secret") && !all.contains("5pm"),
            "reason: output_shown"
        );
        assert_eq!(end_reason(&["你的額度已用完"]), Some("額度"));
        // a failed turn's reason is read too (its card id is the event id)
        let tf = ev(
            9,
            "seat.turn_failed",
            "seat_message",
            "ev_9",
            at,
            json!({"seat_id": "a9136", "reason": "Not logged in · Please run /login"}),
        );
        let ended = end_reasons(&[tf]);
        let card = Card {
            kind: "seat.turn_failed".into(),
            card_id: "ev_9".into(),
            ..by("s_c").clone()
        };
        let low = low_power_end(card, &ended);
        assert_eq!(
            (low.slot, low.why.as_deref()),
            (3, Some("登入")),
            "reason: turn_failed_not_low_power"
        );
        assert_eq!(end_reason(&["all good"]), None);
    }

    /// N-ws-dismiss-persist: dismissals replayed in order; a restore after a dismissal puts
    /// the card back, a dismissal after a restore takes it off again.
    #[test]
    fn dismissals_come_back_from_their_events_in_order() {
        let at = "2026-11-19T00:00:00.000Z";
        let t = Taken::default();
        let es = [
            ev(
                1,
                "slot.dismissed",
                "slot",
                "e1",
                at,
                json!({"card_id": "a"}),
            ),
            ev(
                2,
                "slot.dismissed",
                "slot",
                "e2",
                at,
                json!({"card_id": "b"}),
            ),
            ev(
                3,
                "slot.restored",
                "slot",
                "e3",
                at,
                json!({"card_id": "a"}),
            ),
            ev(
                4,
                "slot.restored",
                "slot",
                "e4",
                at,
                json!({"card_id": "c"}),
            ),
            ev(
                5,
                "slot.dismissed",
                "slot",
                "e5",
                at,
                json!({"card_id": "c"}),
            ),
            ev(
                6,
                "slot.dismissed",
                "slot",
                "e6",
                at,
                json!({"card_id": "bad\nid"}),
            ),
            ev(7, "slot.dismissed", "slot", "e7", at, json!({})),
        ];
        assert_eq!(t.replay(&es), 2);
        let off: Vec<String> = t.dismissed().into_iter().collect();
        assert_eq!(off, ["b", "c"], "reason: order_or_bad_id");
        // a replayed dismissal can still be put back by the page
        assert!(t.restore("b"));
    }

    /// 5d33a PREREAD 04e74d1c L2: a failed rejoin is a card until the same seat is back.
    #[test]
    fn a_failed_rejoin_is_no_card_once_the_seat_is_back() {
        let at = "2026-11-18T00:00:00.000Z";
        let failed = |seq, seat: &str| {
            ev(
                seq,
                "seat.rejoin_failed",
                "seat",
                seat,
                at,
                json!({"seat_id": seat}),
            )
        };
        let shown = |events: &[StoredEvent]| -> Vec<String> {
            cards(&BTreeMap::new(), events, &[], &BTreeSet::new(), rules())
                .into_iter()
                .filter(|c| c.kind == "seat.rejoin_failed")
                .map(|c| c.card_id)
                .collect()
        };
        assert_eq!(
            shown(&[failed(1, "s_a")]),
            ["ev_1"],
            "reason: failed_rejoin_no_card"
        );
        for back in SEAT_BACK {
            let events = [
                failed(1, "s_a"),
                ev(2, back, "seat", "s_a", at, json!({"seat_id": "s_a"})),
                failed(3, "s_b"),
            ];
            assert_eq!(
                shown(&events),
                ["ev_3"],
                "reason: back_seat_still_carded {back}"
            );
        }
        // back before it failed again: the later failure stays
        let events = [
            ev(
                1,
                "seat.restarted",
                "seat",
                "s_a",
                at,
                json!({"seat_id": "s_a"}),
            ),
            failed(2, "s_a"),
        ];
        assert_eq!(shown(&events), ["ev_2"], "reason: later_failure_hidden");
    }

    fn line(state: &'static str, at: &str) -> Now {
        Now {
            item: "要不要合 #860".into(),
            done: 1,
            total: 2,
            state,
            track: None,
            why: Some("兩個做法".into()),
            idea: None,
            updated_at: at.into(),
        }
    }

    #[test]
    fn the_table_puts_each_known_kind_in_its_slot() {
        let p = json!({});
        for (kind, want) in [
            ("session.spawn_failed", Some(2)),
            ("session.spawn_rejected", Some(2)),
            ("session.stop_unconfirmed", Some(2)),
            ("session.timeout", Some(2)),
            ("seat.rejoin_failed", Some(2)),
            ("thermal.would_nogo", Some(3)),
            ("thermal.would_reap", Some(3)),
            ("session.queued", Some(4)),
            ("seat.opened", Some(6)),
            ("session.killed", Some(6)),
            ("session.cancelled", Some(6)),
            ("message.done", Some(6)),
            ("session.exited", Some(6)),
            ("preview.swapped", Some(6)),
            ("preview.swap_failed", Some(2)),
            ("session.output", None),
            ("seat.now", None),
            ("decision.answered", None),
            ("slot.dismissed", None),
        ] {
            assert_eq!(
                slot_of(kind, &p, rules()),
                want,
                "reason: wrong_slot {kind}"
            );
        }
        assert_eq!(
            slot_of("session.exited", &json!({"exit_code": 1}), rules()),
            Some(2),
            "reason: crash_not_broken"
        );
        // a stop seen clean (every candidate another coalition's service) is a done notice;
        // a survivor, an unreadable one or another reason keeps it broken (0eed7 L1 r1)
        let clean = json!({"reason": "observed_clean", "survivors": [],
            "candidates": {"count": 15, "other_coalition": [1, 2, 3], "other_coalition_more": 12,
                           "unreadable": [], "unreadable_more": 0}});
        assert_eq!(
            slot_of("session.stop_unconfirmed", &clean, rules()),
            Some(6),
            "reason: clean_stop_broken"
        );
        let mut survivor = clean.clone();
        survivor["survivors"] = json!([42]);
        assert_eq!(
            slot_of("session.stop_unconfirmed", &survivor, rules()),
            Some(2),
            "reason: survivor_done"
        );
        let mut other = clean.clone();
        other["reason"] = json!("late_exec_candidates");
        assert_eq!(
            slot_of("session.stop_unconfirmed", &other, rules()),
            Some(2),
            "reason: other_reason_done"
        );
        // the count is every candidate found, also one that ended before it was read: more than
        // listed is still clean (a9136 fdd75dd52, 0eed7 1f7fed004 H6: 14 found, 13 listed)
        let mut more = clean.clone();
        more["candidates"]["count"] = json!(16);
        assert_eq!(
            slot_of("session.stop_unconfirmed", &more, rules()),
            Some(6),
            "reason: uncounted_candidate_broken"
        );
        let mut unreadable = clean.clone();
        unreadable["candidates"]["unreadable"] = json!([7]);
        assert_eq!(
            slot_of("session.stop_unconfirmed", &unreadable, rules()),
            Some(2),
            "reason: unreadable_done"
        );
        assert_eq!(
            slot_of("session.exited", &json!({"exit_code": 0}), rules()),
            Some(6)
        );
    }

    #[test]
    fn an_unknown_kind_takes_the_notification_level() {
        assert!(rules().is_some(), "reason: builtin_rules_did_not_load");
        // red.event ⇒ broken, yellow.event ⇒ decide, green.event ⇒ done, routine ⇒ no card
        assert_eq!(slot_of("pane.dead", &json!({}), rules()), Some(2));
        assert_eq!(slot_of("decision.requested", &json!({}), rules()), Some(1));
        assert_eq!(slot_of("ci.succeeded", &json!({}), rules()), Some(6));
        assert_eq!(slot_of("something.new", &json!({}), rules()), None);
        assert_eq!(
            slot_of(
                "something.new",
                &json!({"signal_type": "decision_required"}),
                rules()
            ),
            Some(1),
            "reason: signal_type_not_read"
        );
        // without rules an unknown kind is no card; the table still works
        assert_eq!(slot_of("pane.dead", &json!({}), None), None);
        assert_eq!(slot_of("seat.opened", &json!({}), None), Some(6));
    }

    #[test]
    fn cards_go_slot_first_then_newest_and_a_decide_line_is_on_top() {
        let mut lines = BTreeMap::new();
        lines.insert(
            "seat_b".to_string(),
            line("decide", "2026-11-18T01:00:00.000Z"),
        );
        lines.insert(
            "seat_c".to_string(),
            line("busy", "2026-11-18T01:00:00.000Z"),
        );
        let evs = vec![
            ev(
                1,
                "seat.opened",
                "seat",
                "seat_a",
                "2026-11-18T00:00:01.000Z",
                json!({}),
            ),
            ev(
                2,
                "session.spawn_failed",
                "session",
                "ss_1",
                "2026-11-18T00:00:02.000Z",
                json!({"id": "ss_1"}),
            ),
            ev(
                3,
                "session.output",
                "session",
                "ss_1",
                "2026-11-18T00:00:03.000Z",
                json!({}),
            ),
            ev(
                4,
                "session.exited",
                "session",
                "ss_2",
                "2026-11-18T00:00:04.000Z",
                json!({"exit_code": 0}),
            ),
            ev(
                5,
                "session.timeout",
                "session",
                "ss_3",
                "2026-11-18T00:00:05.000Z",
                json!({}),
            ),
        ];
        let c = cards(&lines, &evs, &[], &BTreeSet::new(), rules());
        let got: Vec<(u8, &str)> = c.iter().map(|c| (c.slot, c.card_id.as_str())).collect();
        assert_eq!(
            got,
            [
                (1, "now:seat_b:2026-11-18T01:00:00.000Z"),
                (2, "ev_5"),
                (2, "ev_2"),
                (6, "ev_4"),
                (6, "ev_1"),
            ],
            "reason: wrong_order"
        );
        assert_eq!(c[0].item.as_deref(), Some("要不要合 #860"));
        assert_eq!(c[0].actions, ["follow_rec", "answer", "later"]);
        assert_eq!(c[0].name, "decide");
        assert_eq!(c[1].session_id.as_deref(), Some("ss_3"));
        assert_eq!(
            c[4].seat_id.as_deref(),
            Some("seat_a"),
            "reason: seat_of_seat_event_lost"
        );
        assert_eq!(c[3].name, "done");
    }

    #[test]
    fn a_taken_card_leaves_and_a_new_line_is_a_new_card() {
        let mut lines = BTreeMap::new();
        lines.insert(
            "seat_b".to_string(),
            line("decide", "2026-11-18T01:00:00.000Z"),
        );
        let evs = vec![ev(
            1,
            "session.timeout",
            "session",
            "ss_1",
            "2026-11-18T00:00:01.000Z",
            json!({}),
        )];
        let mut taken = BTreeSet::new();
        taken.insert(now_card_id("seat_b", "2026-11-18T01:00:00.000Z"));
        taken.insert("ev_1".to_string());
        assert!(
            cards(&lines, &evs, &[], &taken, rules()).is_empty(),
            "reason: taken_card_shown"
        );
        lines.insert(
            "seat_b".to_string(),
            line("decide", "2026-11-18T02:00:00.000Z"),
        );
        let c = cards(&lines, &evs, &[], &taken, rules());
        assert_eq!(c.len(), 1, "reason: new_line_hidden");
        assert_eq!(c[0].card_id, "now:seat_b:2026-11-18T02:00:00.000Z");
    }

    #[test]
    fn notices_are_capped_and_the_rest_are_kept() {
        let mut evs: Vec<StoredEvent> = (1..=30)
            .map(|i| {
                ev(
                    i,
                    "seat.opened",
                    "seat",
                    &format!("seat_{i}"),
                    &format!("2026-11-18T00:00:{i:02}.000Z"),
                    json!({}),
                )
            })
            .collect();
        evs.push(ev(
            31,
            "session.timeout",
            "session",
            "ss",
            "2026-11-18T00:00:00.000Z",
            json!({}),
        ));
        let c = cards(&BTreeMap::new(), &evs, &[], &BTreeSet::new(), rules());
        assert_eq!(c.iter().filter(|c| c.slot == 6).count(), KEEP_DONE);
        assert_eq!(c[0].card_id, "ev_31", "reason: broken_card_dropped");
        assert_eq!(c[1].card_id, "ev_30", "reason: not_newest_notice_first");
    }

    #[test]
    fn taking_a_card_twice_is_refused_and_giving_back_frees_it() {
        let t = Taken::default();
        assert!(t.take("c1"));
        assert!(!t.take("c1"), "reason: second_answer_taken");
        t.give_back("c1");
        assert!(t.take("c1"), "reason: give_back_kept_it");
        for i in 0..MAX_TAKEN + 10 {
            t.take(&format!("x{i:05}"));
        }
        assert!(
            t.all().len() <= MAX_TAKEN,
            "reason: taken_grows_without_bound"
        );
    }

    #[test]
    fn a_card_id_is_short_and_one_line() {
        assert!(valid_card_id("now:seat_1:2026-11-18T01:00:00.000Z"));
        assert!(!valid_card_id(""));
        assert!(!valid_card_id("a\nb"));
        assert!(!valid_card_id(&"x".repeat(MAX_ID + 1)));
        assert!(valid_card_id(&"字".repeat(MAX_ID)));
    }

    fn usage_of(seat: &str, session: &str, used: u64, at: &str) -> crate::usage::Usage {
        let b = crate::usage::Board::default();
        b.bind(session, Some(seat));
        let l = json!({"type":"assistant","message":{"usage":{"input_tokens":used}}}).to_string();
        b.observe(session, l.as_bytes(), at);
        b.all().remove(0)
    }

    #[test]
    fn a_full_context_is_slot_3_and_a_new_card_past_the_red_line() {
        const AT: &str = "2026-11-18T02:00:00.000Z";
        let low = usage_of("seat_a", "s_a", 169_999, AT);
        let hand = usage_of("seat_b", "s_b", 170_000, AT);
        let red = usage_of("seat_c", "s_c", 190_000, AT);
        let c = live_cards(
            &BTreeMap::new(),
            &[low, hand, red],
            "2026-11-18T00:00:00.000Z",
        );
        let got: Vec<(&str, u8, Option<u8>)> = c
            .iter()
            .map(|c| (c.card_id.as_str(), c.slot, c.pct))
            .collect();
        assert_eq!(
            got,
            vec![("usage:s_b:85", 3, Some(85)), ("usage:s_c:95", 3, Some(95))],
            "reason: handoff_line_moved_or_red_not_a_new_card"
        );
        assert_eq!(c[0].seat_id.as_deref(), Some("seat_b"));
        assert_eq!(c[0].name, "low_power");
    }

    #[test]
    fn a_seat_waiting_or_busy_and_silent_past_the_cutoff_is_stuck() {
        const OLD: &str = "2026-11-18T01:00:00.000Z";
        const NEW: &str = "2026-11-18T02:10:00.000Z";
        const CUT: &str = "2026-11-18T01:30:00.000Z";
        let mut lines = BTreeMap::new();
        lines.insert("w_old".to_owned(), line("reply", OLD));
        lines.insert("w_new".to_owned(), line("reply", NEW));
        lines.insert("b_quiet".to_owned(), line("busy", OLD));
        lines.insert("b_talks".to_owned(), line("busy", OLD));
        lines.insert("b_unrun".to_owned(), line("busy", OLD));
        lines.insert("d_old".to_owned(), line("decide", OLD));
        let usage = [
            usage_of("b_quiet", "s1", 1000, "2026-11-18T01:20:00.000Z"),
            usage_of("b_talks", "s2", 1000, NEW),
        ];
        let c = live_cards(&lines, &usage, CUT);
        let got: Vec<(&str, u8, &str)> = c
            .iter()
            .map(|c| (c.card_id.as_str(), c.slot, c.kind.as_str()))
            .collect();
        assert_eq!(
            got,
            vec![
                ("quiet:b_quiet:2026-11-18T01:20:00.000Z", 4, "seat.quiet"),
                ("stuck:w_old:2026-11-18T01:00:00.000Z", 4, "seat.waiting"),
            ],
            "reason: cutoff_ignored_or_unrun_or_talking_seat_flagged"
        );
        let all = cards(&lines, &[], &c, &BTreeSet::new(), rules());
        let slots: Vec<u8> = all.iter().map(|c| c.slot).collect();
        assert_eq!(slots, vec![1, 4, 4], "reason: live_cards_not_merged");
    }

    fn andy(item: &str, done: bool) -> aib_drive::andy_open::Item {
        let mut fields = BTreeMap::new();
        fields.insert("text".to_owned(), format!("{item} 要不要做"));
        fields.insert("rec".to_owned(), String::new());
        aib_drive::andy_open::Item {
            item: item.into(),
            item_kind: "decide".into(),
            card: "N-workspace".into(),
            fields,
            since: "11-18".into(),
            done,
        }
    }

    #[test]
    fn an_open_andy_item_is_a_slot_1_card_and_a_closed_one_is_not() {
        let c = andy_cards(&[andy("A1", false), andy("A2", true)], &BTreeMap::new());
        assert_eq!(c.len(), 1, "reason: closed_item_shown");
        assert_eq!(c[0].card_id, "andy:A1");
        assert_eq!((c[0].slot, c[0].kind.as_str()), (1, "andy.open"));
        assert_eq!(c[0].item.as_deref(), Some("A1 要不要做"));
        assert_eq!(c[0].why, None, "reason: empty_rec_kept");
        assert_eq!(c[0].card.as_deref(), Some("N-workspace"));
        let all = cards(&BTreeMap::new(), &[], &c, &BTreeSet::new(), rules());
        assert_eq!(all.len(), 1, "reason: andy_card_not_merged");
    }

    /// 0eed7 VERIFY-slots d9e6d5133 M1: a 待人決策 card's time is when its item was (last) opened,
    /// full ISO, so a new item heads class 1 and `as_of` counts it as newer.
    #[test]
    fn an_andy_card_is_timed_by_the_event_that_opened_it() {
        let ev = |item: &str, outcome: &str, at: &str| json!({"kind": "andy_open", "item": item, "outcome": outcome, "at": at});
        let opened = opened_at(&[
            ev("A1", "open", "2026-11-18T01:00:00.000Z"),
            json!({"kind": "other", "item": "A1", "outcome": "open", "at": "2026-11-18T09:00:00.000Z"}),
            ev("A1", "update", "2026-11-18T02:00:00.000Z"),
            ev("A1", "close", "2026-11-18T03:00:00.000Z"),
            ev("A1", "open", "2026-11-18T04:00:00.000Z"),
            ev("A2", "open", "2026-11-18T05:00:00.000Z"),
            ev("A2", "update", "2026-11-18T06:00:00.000Z"),
        ]);
        assert_eq!(
            opened.get("A1").map(String::as_str),
            Some("2026-11-18T04:00:00.000Z"),
            "reason: not_the_last_open_or_other_kind_read"
        );
        let c = andy_cards(
            &[andy("A1", false), andy("A2", false), andy("A9", false)],
            &opened,
        );
        let at: Vec<&str> = c.iter().map(|c| c.at.as_str()).collect();
        assert_eq!(
            at,
            [
                "2026-11-18T04:00:00.000Z",
                "2026-11-18T05:00:00.000Z",
                "11-18"
            ],
            "reason: month_day_kept_or_no_fallback"
        );
        // 12 old items and a new one: the new one is on page 1 and newer than an older as_of
        let items: Vec<_> = (0..13).map(|n| andy(&format!("A{n}"), false)).collect();
        let opened: BTreeMap<String, String> = (0..13)
            .map(|n| (format!("A{n}"), format!("2026-11-18T{n:02}:00:00.000Z")))
            .collect();
        // through cards(), which orders a class newest first as GET /api/v1/slots does
        let all = cards(
            &BTreeMap::new(),
            &[],
            &andy_cards(&items, &opened),
            &BTreeSet::new(),
            rules(),
        );
        let q = |as_of: Option<&str>| PageQuery {
            slot: Some("1".into()),
            as_of: as_of.map(str::to_owned),
            ..PageQuery::default()
        };
        let (listed, _) = page_of(&all, &Paging::of(&q(None)).unwrap());
        assert!(
            listed.iter().any(|c| c.card_id == "andy:A12"),
            "reason: new_item_not_on_page_1"
        );
        let (_, meta) = page_of(
            &all,
            &Paging::of(&q(Some("2026-11-18T11:00:00.000Z"))).unwrap(),
        );
        assert_eq!(meta["newer"], 1, "reason: new_item_not_newer {meta}");
    }

    #[test]
    fn a_card_shows_the_board_fields_and_a_know_item_sits_with_slot_6() {
        let mut know = andy("A3", false);
        know.item_kind = "知道".into();
        know.fields.insert("state".into(), "已執行".into());
        know.fields.insert("src".into(), "519d0".into());
        let c = andy_cards(&[andy("A1", false), know], &BTreeMap::new());
        let v: Vec<Value> = c.iter().map(Card::view).collect();
        assert_eq!(v[0]["level"], 1);
        assert_eq!(v[0]["tag"], "decide", "reason: item_kind_not_the_tag");
        assert_eq!(v[0]["text"], "A1 要不要做");
        assert_eq!(v[0]["since"], "11-18");
        assert_eq!(v[0]["card"], "N-workspace");
        assert_eq!(v[0]["rec"], Value::Null, "reason: empty_rec_shown");
        assert_eq!(
            (v[1]["level"].as_u64(), v[1]["tag"].as_str()),
            (Some(6), Some("知道"))
        );
        assert_eq!(v[1]["state"], "已執行");
        assert_eq!(v[1]["src"], "519d0");
        assert_eq!(v[1]["actions"], json!(["later"]));
        let e = ev(
            1,
            "session.timeout",
            "session",
            "s_1",
            "2026-11-18T00:00:00.000Z",
            json!({}),
        );
        let t = event_card(&e, rules()).unwrap().view();
        assert_eq!(
            (t["tag"].as_str(), t["text"].as_str()),
            (Some("注意"), Some("逾時"))
        );
        assert_eq!(t["actions"], json!(["fix", "handoff", "reassign", "later"]));
    }

    #[test]
    fn a_merge_back_conflict_is_a_decide_card_naming_its_files() {
        let e = ev(
            1,
            "seat.merge_conflict",
            "worktree",
            "ev_1",
            "2026-11-20T00:00:00.000Z",
            json!({"seat_id": "seat_c", "files": ["x.txt", "y.rs"]}),
        );
        let c = event_card(&e, rules()).unwrap();
        assert_eq!(c.slot, 1, "reason: merge_conflict_not_decide");
        assert_eq!(c.card_id, "ev_1");
        assert_eq!(c.view()["text"], "合回衝突:x.txt、y.rs");
        let e = ev(
            2,
            "seat.merge_unsafe",
            "worktree",
            "ev_2",
            "2026-11-20T00:00:00.000Z",
            json!({"seat_id": "seat_c", "why": ".git/config has filter.x.clean"}),
        );
        let c = event_card(&e, rules()).unwrap();
        assert_eq!(c.slot, 2, "reason: merge_unsafe_not_broken");
        assert_eq!(
            c.view()["text"],
            "合回停住:作者倉設定 .git/config has filter.x.clean"
        );
    }

    #[test]
    fn a_turn_not_done_is_a_notice_card_with_its_reason() {
        let e = ev(
            1,
            "seat.turn_failed",
            "seat_message",
            "ev_1",
            "2026-11-19T00:00:00.000Z",
            json!({"seat_id": "a9136", "thread_id": "t_1", "reply_to": "q_call",
                "reason": "Not logged in"}),
        );
        let t = event_card(&e, rules()).unwrap().view();
        assert_eq!(
            (t["tag"].as_str(), t["text"].as_str(), t["seat_id"].as_str()),
            (Some("注意"), Some("沒做成:Not logged in"), Some("a9136")),
            "reason: turn_failed_card"
        );
        assert_eq!(
            t["open"],
            json!({"thread_id": "t_1", "message_id": "q_call"}),
            "reason: card_does_not_open_the_thread"
        );
    }

    #[test]
    fn merge_and_push_rows_of_the_next_list_are_slot_5_cards() {
        let next: aib_drive::next::Next = serde_json::from_value(json!({
            "schema": "aib-drive.next.v1", "as_of": "2026-11-18T04:15:32Z", "complete": true,
            "sources": [], "unknown": [],
            "rows": [
                {"rank": 1, "layer": 1, "kind": "push", "what": "推 S91-selfcheck(審過未推)",
                 "node": "S91-selfcheck", "to": "043dd", "why_him": "043dd 是作者",
                 "why_now": "進度樹上審過、名下沒有開著的 PR", "unblocks": 0, "waited_min": 0,
                 "starving": false, "needs": "seat_busy", "size": "review",
                 "data_at": "2026-11-18T04:15:32Z"},
                {"rank": 2, "layer": 1, "kind": "merge", "what": "#860 可合", "pr": 860,
                 "why_him": "", "why_now": "審過、收據齊、CI 綠", "unblocks": 2,
                 "waited_min": 3, "needs": "none", "size": "merge",
                 "data_at": "2026-11-18T04:15:32Z"},
                {"rank": 3, "layer": 2, "kind": "fix", "what": "修 #861", "pr": 861,
                 "why_him": "", "why_now": "紅", "unblocks": 0, "waited_min": 0,
                 "needs": "none", "size": "fix", "data_at": "2026-11-18T04:15:32Z"}
            ]
        }))
        .unwrap();
        let c = next_cards(&next);
        let got: Vec<(&str, u8)> = c.iter().map(|c| (c.card_id.as_str(), c.slot)).collect();
        assert_eq!(
            got,
            [
                ("next:push:S91-selfcheck", 5),
                ("next:merge:#860", 5),
                ("next:fix:#861", 2)
            ],
            "reason: row_slot_or_key_wrong"
        );
        let v = c[0].view();
        assert_eq!(v["tag"], "可推進");
        assert_eq!(v["text"], "推 S91-selfcheck(審過未推)");
        assert_eq!(v["card"], "S91-selfcheck");
        assert_eq!(v["src"], "043dd");
        assert_eq!(v["state"], "seat_busy");
        assert_eq!(c[1].state, None, "reason: needs_none_shown");
        let f = c[2].view();
        assert_eq!(f["tag"], "注意");
        assert_eq!(f["text"], "修 #861");
        assert_eq!(f["actions"][0], "fix", "reason: fix_card_actions");
        for k in ["review", "receipt", "collect"] {
            assert_eq!(next_slot(k), None, "reason: {k}_carded");
        }
        assert_eq!(next_slot("install"), Some(5), "reason: install_not_carded");
        let mut other = next.clone();
        other.schema = "aib-drive.next.v2".into();
        assert!(next_cards(&other).is_empty(), "reason: unknown_schema_read");
    }

    #[test]
    fn a_seat_card_takes_the_node_its_status_line_names_by_the_board_s_edges() {
        let ids: Vec<String> = ["N-ws", "N-ws-slot-rest", "S18", "S180"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert_eq!(
            node_in("做 N-ws-slot-rest 第 3 級", &ids).as_deref(),
            Some("N-ws-slot-rest")
        );
        assert_eq!(
            node_in("看 S180 的收據", &ids).as_deref(),
            Some("S180"),
            "reason: prefix_matched"
        );
        assert_eq!(node_in("N-ws:§14", &ids).as_deref(), Some("N-ws"));
        assert_eq!(node_in("XS18", &ids), None, "reason: no_edge_before");
        assert_eq!(node_in("沒有卡號", &ids), None);
        let mut lines = BTreeMap::new();
        lines.insert("a".to_owned(), line("reply", "2026-11-18T01:00:00.000Z"));
        if let Some(n) = lines.get_mut("a") {
            n.item = "等 S18 回覆".into();
        }
        let mut cs = live_cards(&lines, &[], "2026-11-18T09:00:00.000Z");
        assert_eq!(cs.len(), 1);
        attach_nodes(&mut cs, &lines, &ids);
        assert_eq!(
            cs[0].card.as_deref(),
            Some("S18"),
            "reason: seat_card_without_node"
        );
        assert_eq!(cs[0].view()["card"], "S18");
    }

    /// 「先收起」還原 (043dd 11-18 22:34): a dismissed card comes back once; an answered one
    /// never does; the dismissed list holds dismissals only.
    #[test]
    fn a_dismissed_card_is_restored_once_and_an_answered_one_never() {
        let t = Taken::default();
        assert!(t.dismiss("ev_1"));
        assert!(!t.dismiss("ev_1"), "reason: dismissed_twice");
        assert!(t.take("andy:A1"));
        assert_eq!(
            t.dismissed().into_iter().collect::<Vec<_>>(),
            ["ev_1"],
            "reason: answered_in_dismissed_list"
        );
        assert!(!t.restore("andy:A1"), "reason: answered_card_restored");
        assert!(t.restore("ev_1"), "reason: dismissed_not_restored");
        assert!(!t.restore("ev_1"), "reason: restored_twice");
        assert!(!t.all().contains("ev_1"), "reason: restored_still_taken");
        assert!(t.all().contains("andy:A1"));
        let q = |v: &str| PageQuery {
            dismissed: Some(v.into()),
            ..PageQuery::default()
        };
        assert!(Paging::of(&q("1")).unwrap().dismissed);
        assert!(
            Paging::of(&q("yes")).is_err(),
            "reason: bad_dismissed_taken"
        );
    }

    /// a9136 ORDER-autodrive 3 (Andy's run 11-20): what the 1.x system wrote is `history`, apart
    /// from what a 2.0 seat asks now, and `counts_now` counts only the latter.
    #[test]
    fn old_system_cards_are_history_and_not_counted_as_waiting() {
        let mut all = andy_cards(&[andy("A1", false)], &BTreeMap::new());
        let mut ask = all[0].clone();
        ask.card_id = "ask:x".into();
        ask.kind = crate::asks::EVENT.into();
        all.push(ask);
        for k in [
            "dm.hold",
            "dm.task",
            "andy.review_list",
            "idea.consent",
            "andy.open",
        ] {
            assert_eq!(origin_of(k), "history", "{k}");
        }
        for k in [crate::asks::EVENT, "now.decide", "session.failed"] {
            assert_eq!(origin_of(k), "now", "{k}");
        }
        let (_, m) = page_of(&all, &Paging::default());
        assert_eq!(m["counts"]["1"], 2, "{m}");
        assert_eq!(
            m["counts_now"]["1"], 1,
            "reason: history_counted_as_waiting {m}"
        );
        let v: Vec<Value> = all.iter().map(Card::view).collect();
        assert_eq!(v[0]["origin"], "history");
        assert_eq!(v[1]["origin"], "now");
        // a decide line: a 1.x terminal seat's is history, a 2.0 seat's is now (5d33a N2)
        let mut line = all[1].clone();
        line.kind = "seat.now".into();
        line.seat_id = Some("a9136".into());
        assert_eq!(line.origin(), "history", "reason: old_seat_decide_counted");
        line.seat_id = Some("seat_0123abcd".into());
        assert_eq!(line.origin(), "now", "reason: new_seat_decide_history");
    }

    /// SPEC §21 (Andy 11-18 21:0x): counts per class, one class a page of 10, and a page being
    /// read does not move when new cards come in (`as_of`).
    #[test]
    fn a_class_pages_by_ten_and_new_cards_do_not_move_a_page() {
        let mk = |seq: i64, kind: &str, sec: i64| {
            ev(
                seq,
                kind,
                "session",
                &format!("s_{seq}"),
                &format!("2026-11-18T00:00:{sec:02}.000Z"),
                json!({}),
            )
        };
        // 23 broken (slot 2) and 3 queued (slot 4)
        let mut evs: Vec<StoredEvent> = (1..=23).map(|i| mk(i, "session.timeout", i)).collect();
        evs.extend((24..=26).map(|i| mk(i, "session.queued", i)));
        let all = cards(&BTreeMap::new(), &evs, &[], &BTreeSet::new(), rules());
        let q = |slot: Option<&str>, page: Option<&str>, as_of: Option<&str>| {
            Paging::of(&PageQuery {
                slot: slot.map(str::to_owned),
                page: page.map(str::to_owned),
                per: None,
                as_of: as_of.map(str::to_owned),
                dismissed: None,
            })
            .unwrap()
        };
        // no query: every card, with the counts
        let (shown, m) = page_of(&all, &Paging::default());
        assert_eq!(shown.len(), 26);
        assert_eq!(m["counts"]["2"], 23, "reason: count_wrong {m}");
        assert_eq!(m["counts"]["4"], 3);
        assert_eq!(m["counts"]["1"], 0);
        assert_eq!(m["total"], 26);
        assert!(m.get("pages").is_none(), "reason: unpaged_gets_paging {m}");
        // slot 2, page 1 and 3 of 3
        let (p1, m1) = page_of(&all, &q(Some("2"), None, None));
        assert_eq!(p1.len(), PER_PAGE, "reason: page_not_ten");
        assert!(
            p1.iter().all(|c| c.slot == 2),
            "reason: other_class_on_page"
        );
        assert_eq!(p1[0].card_id, "ev_23", "reason: not_newest_first");
        assert_eq!(
            (m1["pages"].as_u64(), m1["listed"].as_u64()),
            (Some(3), Some(23))
        );
        let as_of = m1["as_of"].as_str().unwrap().to_owned();
        assert_eq!(as_of, "2026-11-18T00:00:26.000Z");
        let (p3, _) = page_of(&all, &q(Some("2"), Some("3"), Some(&as_of)));
        assert_eq!(p3.len(), 3, "reason: last_page_wrong");
        // two new broken cards come in: page 2 with the old as_of is the same page
        let (before, _) = page_of(&all, &q(Some("2"), Some("2"), Some(&as_of)));
        let before: Vec<String> = before.iter().map(|c| c.card_id.clone()).collect();
        evs.push(mk(27, "session.timeout", 40));
        evs.push(mk(28, "session.timeout", 41));
        let all2 = cards(&BTreeMap::new(), &evs, &[], &BTreeSet::new(), rules());
        let (after, m2) = page_of(&all2, &q(Some("2"), Some("2"), Some(&as_of)));
        let after: Vec<String> = after.iter().map(|c| c.card_id.clone()).collect();
        assert_eq!(after, before, "reason: page_moved_by_new_cards");
        assert_eq!(m2["newer"], 2, "reason: newer_not_counted {m2}");
        assert_eq!(m2["counts"]["2"], 25, "reason: tab_count_not_live");
        // without as_of the new ones lead page 1
        let (fresh, _) = page_of(&all2, &q(Some("2"), None, None));
        assert_eq!(fresh[0].card_id, "ev_28");
    }

    #[test]
    fn a_bad_slots_query_is_refused() {
        let q = |k: &str, v: &str| {
            let mut p = PageQuery::default();
            match k {
                "slot" => p.slot = Some(v.into()),
                "page" => p.page = Some(v.into()),
                "per" => p.per = Some(v.into()),
                _ => p.as_of = Some(v.into()),
            }
            Paging::of(&p)
        };
        for (k, v) in [
            ("slot", "0"),
            ("slot", "7"),
            ("slot", "x"),
            ("page", "0"),
            ("page", "+2"),
            ("per", "11"),
            ("per", "0"),
            ("as_of", ""),
            ("as_of", "a\nb"),
        ] {
            assert!(q(k, v).is_err(), "reason: bad_query_taken {k}={v}");
        }
        let long = "9".repeat(MAX_AS_OF + 1);
        assert!(q("as_of", &long).is_err(), "reason: long_as_of_taken");
        assert_eq!(q("per", "5").unwrap().per, 5);
        assert_eq!(
            q("slot", "6").unwrap().page,
            Some(1),
            "reason: slot_alone_not_paged"
        );
    }

    /// ⑥ 換站 (SPEC §2): a tree item that moved station is a slot-6 card naming the item and
    /// both stations; an edit that keeps the station and a new item are no card.
    #[test]
    fn a_station_move_is_a_slot_6_card_and_an_edit_in_place_is_none() {
        let at = "2026-11-18T00:00:00.000Z";
        let moved = ev(
            1,
            "node.set",
            "node",
            "ev_a",
            at,
            json!({"node_id": "N-ws-c1", "from": "開發中", "to": "實查通過", "rev": 3, "by": "human:0123456789abcdef"}),
        );
        let t = event_card(&moved, rules()).unwrap().view();
        assert_eq!(t["level"], 6, "reason: station_move_not_slot_6 {t}");
        assert_eq!(t["card"], "N-ws-c1", "reason: station_move_no_item {t}");
        assert_eq!(t["text"], "換站:開發中 → 實查通過");
        assert_eq!(t["tag"], "知道");
        assert!(
            !t.to_string().contains("human:"),
            "reason: page_key_on_card {t}"
        );
        let kept = ev(
            2,
            "node.set",
            "node",
            "ev_b",
            at,
            json!({"node_id": "N-ws-c1", "from": "開發中", "to": null, "rev": 4, "by": "startup"}),
        );
        assert!(
            event_card(&kept, rules()).is_none(),
            "reason: edit_in_place_is_card"
        );
        let added = ev(
            3,
            "node.added",
            "node",
            "ev_c",
            at,
            json!({"node_id": "N-ws-c2", "parent_id": "N-ws", "by": "startup"}),
        );
        assert!(
            event_card(&added, rules()).is_none(),
            "reason: new_item_is_card"
        );
    }

    #[test]
    fn mail_waiting_past_the_cutoff_is_one_slot_4_card_per_seat() {
        let row = |to: &str, at: i64| {
            let mut r =
                crate::seat_send::queued_row("seat_a", to, Some("safe"), "x", "00").unwrap();
            r.created_unix = at;
            r.created = format!("t{at}");
            r
        };
        let mut written = row("seat_c", 100);
        written.item.insert("session_id".into(), json!("s_1"));
        let rows = vec![
            row("seat_b", 200),
            row("seat_b", 100),
            row("seat_d", 900),
            written,
        ];
        let c = mail_cards(&rows, 500);
        assert_eq!(c.len(), 1, "reason: new_or_written_mail_carded {c:?}");
        assert_eq!(c[0].seat_id.as_deref(), Some("seat_b"));
        assert_eq!(c[0].slot, 4);
        assert_eq!(c[0].at, "t100", "reason: not_the_oldest");
        assert_eq!(c[0].item.as_deref(), Some("2 封信排隊沒送到"));
        assert_eq!(c[0].src.as_deref(), Some("seat_a"));
        assert!(c[0].card_id.starts_with("mailq:seat_b:"));
    }

    /// A message written to a seat and not read past the cutoff is a slot-4 card per seat
    /// (043dd 11-18 20:48); a read one, a fresh one and a queued one are not this card.
    #[test]
    fn mail_written_and_unread_past_the_cutoff_is_one_slot_4_card_per_seat() {
        let row = |to: &str, status: &str, delivered: Option<i64>| {
            let mut r =
                crate::seat_send::queued_row("seat_a", to, Some("safe"), "x", "00").unwrap();
            r.item.insert("status".into(), json!(status));
            if let Some(at) = delivered {
                r.item.insert("delivered_at".into(), json!(at));
                r.item.insert("session_id".into(), json!("s_1"));
            }
            r
        };
        let rows = vec![
            row("seat_b", "delivered", Some(300)),
            row("seat_b", "delivered", Some(100)),
            row("seat_c", "read", Some(100)),
            row("seat_d", "delivered", Some(900)),
            row("seat_e", "queued", None),
        ];
        let c = unread_cards(&rows, 500);
        assert_eq!(c.len(), 1, "reason: read_fresh_or_queued_carded {c:?}");
        assert_eq!(c[0].seat_id.as_deref(), Some("seat_b"));
        assert_eq!(c[0].slot, 4);
        assert_eq!(c[0].item.as_deref(), Some("2 封信送到沒讀"));
        assert_eq!(
            c[0].at,
            crate::sessions::rfc3339_of_unix(100),
            "reason: not_the_oldest"
        );
        assert!(c[0].card_id.starts_with("mailunread:seat_b:"));
        // mail_cards gives it too, beside the queued ones
        let all = mail_cards(&rows, 500);
        assert!(
            all.iter().any(|c| c.kind == "message.unread"),
            "reason: unread_not_in_mail_cards"
        );
    }
}
