//! 「要你決定」卡 (519d0 11-19 20:22 派工 (5), PLAN-ask-person-card-7ebca-1119 r1; 519d0 20:24
//! 定案): a seat whose reviewer and person disagree asks the person with the `ask_person` tool,
//! and the person answers with one press, no typing.
//!
//! - act `seat_ask {kind, thread_id, review_msg_id, person_msg_id}`, a seat token only (the
//!   seat is who the token says): the seat is a member of the thread; `review_msg_id` is a post
//!   there from **another** seat; `person_msg_id` is a post there from a person (any person for
//!   now, 519d0 定案 2); `kind` is [`KIND_REVIEW`], the only one. At most one open ask a seat
//!   (the same three ids again give the same ask), at most [`PER_WINDOW`] asks a seat in
//!   [`WINDOW_MIN`] minutes. Journals `seat.asked` (ids only, never words) and gives `{ask_id}`.
//! - Its card ([`cards`]) is slot 1, id [`PREFIX`]`<ask_id>`, in fixed words only (the posts'
//!   words never reach a card); `open` names the thread and both posts for the page to show.
//! - `decision_answer {card_id: "ask:<id>", answer: "review" | "keep"}` from the built-in page
//!   ([`answer`], through [`crate::slots::answer`]): aibd types a fixed line into the seat (it
//!   wakes), posts the same line in the thread as aibd with who pressed and when (519d0 定案 1:
//!   the thread shows a person decided), and journals `decision.answered` (`answer` is one of
//!   the two fixed values). A seat token never reaches it: a seat cannot answer its own card.

use crate::api::AppState;
use crate::guard::request_id;
use crate::pairing::PageAccess;
use crate::slots::Card;
use aib_store::{NewEvent, StoredEvent};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::sync::LazyLock;

/// act type of the seat side.
pub const ASK: &str = "seat_ask";
/// The one kind of ask so far: the reviewer's word and the person's word disagree.
pub const KIND_REVIEW: &str = "review_vs_person";
/// An ask's card id is this and the ask id.
pub const PREFIX: &str = "ask:";
/// The event an ask writes.
pub const EVENT: &str = "seat.asked";
/// At most this many asks a seat in [`WINDOW_MIN`] minutes (a loop stops here).
pub const PER_WINDOW: usize = 3;
pub const WINDOW_MIN: i64 = 10;
/// The two answers: the value the page sends, and the words aibd types and posts.
pub const ANSWERS: [(&str, &str); 2] = [("review", "照審查改"), ("keep", "維持原需求")];
/// The card's fixed line.
pub const CARD_LINE: &str = "審查意見跟你的需求不同,要照哪個?";

/// One check and write at a time, so two asks of one seat at once are one ask.
static WRITE: LazyLock<tokio::sync::Mutex<()>> = LazyLock::new(|| tokio::sync::Mutex::new(()));

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

fn bad(message: &str) -> Response {
    refuse(StatusCode::BAD_REQUEST, "invalid_request", message)
}

/// Whether `body` is the seat side's ask.
pub fn is_seat_act(body: &Value) -> bool {
    body.get("type").and_then(Value::as_str) == Some(ASK)
}

fn text<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(Value::as_str)
}

/// `ask_` and 16 lowercase hex (as [`crate::sessions::new_id`] makes them).
pub fn ask_id_ok(a: &str) -> bool {
    a.strip_prefix("ask_").is_some_and(|h| {
        !h.is_empty()
            && h.len() <= 32
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}

/// The ask ids a person answered, from the events.
fn answered(events: &[StoredEvent]) -> BTreeSet<String> {
    events
        .iter()
        .filter(|e| e.event_type == "decision.answered")
        .filter_map(|e| text(&e.payload, "card_id")?.strip_prefix(PREFIX))
        .map(str::to_owned)
        .collect()
}

/// The asks not answered yet, oldest first.
pub fn open_asks(events: &[StoredEvent]) -> Vec<&StoredEvent> {
    let done = answered(events);
    let mut v: Vec<&StoredEvent> = events
        .iter()
        .filter(|e| e.event_type == EVENT)
        .filter(|e| text(&e.payload, "ask_id").is_some_and(|a| !done.contains(a)))
        .collect();
    v.sort_by_key(|e| e.global_seq);
    v
}

/// What a seat may ask now, from the last events: `Ok(Some(id))` = this same ask is open
/// already; `Ok(None)` = a new one may be written; `Err` = the refusal.
pub fn may_ask(
    events: &[StoredEvent],
    seat_id: &str,
    ids: (&str, &str, &str),
    window_start: &str,
) -> Result<Option<String>, (&'static str, &'static str)> {
    let mine = |e: &&StoredEvent| text(&e.payload, "seat_id") == Some(seat_id);
    if let Some(open) = open_asks(events).into_iter().find(mine) {
        let p = &open.payload;
        let same = (
            text(p, "thread_id"),
            text(p, "review_msg_id"),
            text(p, "person_msg_id"),
        ) == (Some(ids.0), Some(ids.1), Some(ids.2));
        return if same {
            Ok(text(p, "ask_id").map(str::to_owned))
        } else {
            Err((
                "ask_open",
                "this seat has an ask the person has not answered; wait for it",
            ))
        };
    }
    let recent = events
        .iter()
        .filter(|e| e.event_type == EVENT)
        .filter(mine)
        .filter(|e| e.occurred_at.as_str() >= window_start)
        .count();
    if recent >= PER_WINDOW {
        return Err(("ask_too_often", "at most 3 asks a seat in 10 minutes"));
    }
    Ok(None)
}

/// The slot-1 cards of the open asks: fixed words; `open` names the thread and the two posts.
pub fn cards(events: &[StoredEvent]) -> Vec<Card> {
    open_asks(events)
        .into_iter()
        .filter_map(|e| {
            let p = &e.payload;
            let seat = text(p, "seat_id")?.to_owned();
            Some(Card {
                card_id: format!("{PREFIX}{}", text(p, "ask_id")?),
                slot: 1,
                name: crate::slots::NAMES[0],
                kind: EVENT.into(),
                seat_id: Some(seat.clone()),
                session_id: None,
                at: e.occurred_at.clone(),
                global_seq: Some(e.global_seq),
                item: Some(CARD_LINE.into()),
                why: None,
                pct: None,
                card: None,
                rec: None,
                state: None,
                src: Some(seat),
                tag: None,
                open: Some(json!({
                    "thread_id": text(p, "thread_id")?,
                    "message_id": text(p, "review_msg_id")?,
                    "person_message_id": text(p, "person_msg_id")?,
                })),
                actions: &["ask_review", "ask_keep", "later"],
            })
        })
        .collect()
}

/// The line typed into the seat for `answer` (`review` / `keep`), fixed words and ids only.
pub fn seat_line(answer: &str, thread_id: &str, review: &str, person: &str) -> Option<String> {
    let (_, words) = ANSWERS.iter().find(|(a, _)| *a == answer)?;
    Some(match answer {
        "review" => format!(
            "真人決定:{words}。串 {thread_id} 的審查 {review} 照改;需求 {person} 依審查意見調整。改完照常回報。"
        ),
        _ => format!(
            "真人決定:{words}。串 {thread_id} 的需求 {person} 不變;審查 {review} 這幾點不改。照原需求做完回報。"
        ),
    })
}

/// The line aibd posts in the thread: what was pressed, by whom, when (Taipei time).
pub fn thread_line(answer: &str, by: &str, taipei_hhmm: &str) -> Option<String> {
    let (_, words) = ANSWERS.iter().find(|(a, _)| *a == answer)?;
    Some(format!("真人決定:{words}({by} 在台北 {taipei_hhmm} 按的)"))
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
}

async fn last_events(st: &AppState) -> Option<Vec<StoredEvent>> {
    let store = st.store.clone();
    crate::threads::blocking(move || store.events_last(crate::slots::SCAN)).await
}

/// act `seat_ask` (a seat token; `seat_id` is the token's seat).
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    let fields = [
        "type",
        "kind",
        "thread_id",
        "review_msg_id",
        "person_msg_id",
    ];
    let shape = "body must be {type, kind, thread_id, review_msg_id, person_msg_id}";
    if !body
        .as_object()
        .is_some_and(|o| o.keys().all(|k| fields.contains(&k.as_str())))
    {
        return bad(shape);
    }
    if text(&body, "kind") != Some(KIND_REVIEW) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "ask_kind",
            "kind must be review_vs_person",
        );
    }
    let (Some(t), Some(r), Some(p)) = (
        text(&body, "thread_id"),
        text(&body, "review_msg_id"),
        text(&body, "person_msg_id"),
    ) else {
        return bad(shape);
    };
    if !crate::threads::thread_id_ok(t) || r == p {
        return bad(shape);
    }
    let me = format!("seat:{seat_id}");
    let (db, tid, rid, pid, key) = (
        st.messages.clone(),
        t.to_owned(),
        r.to_owned(),
        p.to_owned(),
        me.clone(),
    );
    let got = crate::threads::blocking(move || {
        let member = crate::threads::is_member_now(&db, &tid, &key)?;
        Ok((member, db.get(&rid)?, db.get(&pid)?))
    })
    .await;
    let Some((member, review, person)) = got else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "thread not read",
        );
    };
    if !member {
        return refuse(StatusCode::NOT_FOUND, "thread_not_found", "no such thread");
    }
    fn in_thread<'a>(
        m: &'a Option<aib_store::MessageRow>,
        t: &str,
    ) -> Option<&'a aib_store::MessageRow> {
        m.as_ref().filter(|m| m.thread_id == t)
    }
    match in_thread(&review, t) {
        Some(m) if m.from_key.starts_with("seat:") && m.from_key != me => {}
        _ => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "ask_review_msg",
                "review_msg_id must be a post in this thread from another seat; a review sent by seat_send is not in it: ask the reviewer to post it here with thread_send, or, when it is not a member, ask the person in this thread to @ it in",
            )
        }
    }
    match in_thread(&person, t) {
        Some(m) if m.from_key.starts_with("human:") => {}
        _ => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "ask_person_msg",
                "person_msg_id must be a post in this thread from a person: the post where the person wrote what they want",
            )
        }
    }
    let _one = WRITE.lock().await;
    let Some(events) = last_events(&st).await else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "events not read",
        );
    };
    let start = crate::sessions::rfc3339_of_unix(now_unix().saturating_sub(WINDOW_MIN * 60));
    match may_ask(&events, seat_id, (t, r, p), &start) {
        Ok(Some(same)) => return Json(json!({ "ask_id": same, "again": true })).into_response(),
        Ok(None) => {}
        Err((code, why)) => return refuse(StatusCode::CONFLICT, code, why),
    }
    let (Ok(ask_id), Ok(event_id)) = (
        crate::sessions::new_id("ask_"),
        crate::sessions::new_id("ev_"),
    ) else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no id");
    };
    let ev = NewEvent {
        event_id,
        aggregate_type: "seat_ask".into(),
        aggregate_id: ask_id.clone(),
        sequence: 1,
        event_type: EVENT.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload: json!({
            "ask_id": ask_id,
            "seat_id": seat_id,
            "kind": KIND_REVIEW,
            "thread_id": t,
            "review_msg_id": r,
            "person_msg_id": p,
        }),
    };
    let store = st.store.clone();
    if crate::threads::blocking(move || store.append(vec![ev]))
        .await
        .is_none()
    {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "ask not written",
        );
    }
    (StatusCode::CREATED, Json(json!({ "ask_id": ask_id }))).into_response()
}

/// `decision_answer` on an ask card (a confirmed page; [`crate::slots::answer`] sends it here).
pub async fn answer(
    st: AppState,
    page: PageAccess,
    card_id: &str,
    seat_named: Option<&str>,
    answer: &str,
) -> Response {
    let Some(ask_id) = card_id.strip_prefix(PREFIX).filter(|a| ask_id_ok(a)) else {
        return bad("card_id must be ask:<ask id>");
    };
    if !ANSWERS.iter().any(|(a, _)| *a == answer) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "ask_answer",
            "answer must be review or keep",
        );
    }
    let Some(events) = last_events(&st).await else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "events not read",
        );
    };
    let Some(ask) = open_asks(&events)
        .into_iter()
        .find(|e| text(&e.payload, "ask_id") == Some(ask_id))
        .map(|e| e.payload.clone())
    else {
        return refuse(
            StatusCode::CONFLICT,
            "no_decision",
            "this ask is not open (answered, or no such ask)",
        );
    };
    let (Some(seat), Some(thread), Some(review), Some(person)) = (
        text(&ask, "seat_id"),
        text(&ask, "thread_id"),
        text(&ask, "review_msg_id"),
        text(&ask, "person_msg_id"),
    ) else {
        return refuse(StatusCode::CONFLICT, "no_decision", "this ask is not whole");
    };
    if seat_named.is_some_and(|s| s != seat) {
        return refuse(
            StatusCode::CONFLICT,
            "decision_changed",
            "this card is another seat's",
        );
    }
    let card = format!("{PREFIX}{ask_id}");
    if !crate::slots::taken().take(&card) {
        return refuse(
            StatusCode::CONFLICT,
            "already_answered",
            "this decision was answered or put aside already",
        );
    }
    let Some(line) = seat_line(answer, thread, review, person) else {
        crate::slots::taken().give_back(&card);
        return bad("answer must be review or keep");
    };
    let typed = match crate::seat_input::type_to_seat(&st, &page, seat, &line).await {
        Ok(t) => t,
        Err(r) => {
            crate::slots::taken().give_back(&card);
            return *r;
        }
    };
    let by = crate::contacts_api::page_key(&crate::guard::Caller::Page(page))
        .unwrap_or_else(|| "真人".into());
    let taipei = crate::sessions::rfc3339_of_unix(now_unix().saturating_add(8 * 3600));
    let hhmm = taipei.get(11..16).unwrap_or("?");
    let posted = match thread_line(answer, &by, hhmm) {
        Some(l) => {
            crate::threads::system_post(
                &st,
                thread,
                review,
                &l,
                "decided",
                &[("ask_id", json!(ask_id)), ("answer", json!(answer))],
            )
            .await
        }
        None => false,
    };
    let journaled = crate::slots::journal(
        &st,
        "decision.answered",
        json!({
            "seat_id": seat,
            "session_id": typed.session_id,
            "card_id": card,
            "answer": answer,
            "by": by,
            "len": typed.len,
            "sha256": typed.digest,
            "mode": typed.mode,
            "delivery": if typed.late { "pending" } else { "written" },
            "posted": posted,
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
            "posted": posted,
            "journaled": journaled,
        })),
    )
        .into_response()
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    fn ev(seq: i64, kind: &str, at: &str, payload: Value) -> StoredEvent {
        StoredEvent {
            global_seq: seq,
            event_id: format!("ev_{seq}"),
            aggregate_type: "seat_ask".into(),
            aggregate_id: format!("a{seq}"),
            sequence: 1,
            event_type: kind.into(),
            schema_version: 1,
            correlation_id: None,
            occurred_at: at.into(),
            payload,
        }
    }

    fn asked(seq: i64, ask: &str, seat: &str, at: &str) -> StoredEvent {
        ev(
            seq,
            EVENT,
            at,
            json!({"ask_id": ask, "seat_id": seat, "kind": KIND_REVIEW,
                   "thread_id": "t_0123456789abcdef", "review_msg_id": "q_r", "person_msg_id": "q_p"}),
        )
    }

    const IDS: (&str, &str, &str) = ("t_0123456789abcdef", "q_r", "q_p");

    #[test]
    fn one_open_ask_a_seat_and_the_same_ask_is_the_same() {
        let e = vec![asked(1, "ask_1", "seat_a", "2026-11-19T12:00:00.000Z")];
        assert_eq!(
            may_ask(&e, "seat_a", IDS, "2026-11-19T11:50:00.000Z"),
            Ok(Some("ask_1".into())),
            "reason: same_ask_twice"
        );
        assert_eq!(
            may_ask(
                &e,
                "seat_a",
                (IDS.0, "q_r2", IDS.2),
                "2026-11-19T11:50:00.000Z"
            ),
            Err((
                "ask_open",
                "this seat has an ask the person has not answered; wait for it"
            )),
            "reason: second_open_ask"
        );
        assert_eq!(
            may_ask(&e, "seat_b", IDS, "2026-11-19T11:50:00.000Z"),
            Ok(None)
        );
    }

    #[test]
    fn an_answered_ask_has_no_card_and_three_in_ten_minutes_is_the_most() {
        let answered = |seq: i64, ask: &str| {
            ev(
                seq,
                "decision.answered",
                "2026-11-19T12:01:00.000Z",
                json!({"card_id": format!("ask:{ask}")}),
            )
        };
        let mut e = Vec::new();
        for (i, a) in ["ask_1", "ask_2", "ask_3"].iter().enumerate() {
            let i = i64::try_from(i).unwrap_or(0);
            e.push(asked(i * 2 + 1, a, "seat_a", "2026-11-19T12:00:00.000Z"));
            e.push(answered(i * 2 + 2, a));
        }
        assert!(cards(&e).is_empty(), "reason: answered_ask_card");
        assert_eq!(
            may_ask(&e, "seat_a", IDS, "2026-11-19T11:55:00.000Z").map_err(|x| x.0),
            Err("ask_too_often"),
            "reason: no_pace"
        );
        assert_eq!(
            may_ask(&e, "seat_a", IDS, "2026-11-19T12:00:00.001Z"),
            Ok(None)
        );
        e.push(asked(9, "ask_4", "seat_a", "2026-11-19T12:02:00.000Z"));
        let c = cards(&e);
        assert_eq!(c.len(), 1);
        assert_eq!(c[0].card_id, "ask:ask_4");
        assert_eq!(c[0].slot, 1);
        assert_eq!(c[0].item.as_deref(), Some(CARD_LINE), "reason: card_words");
        assert_eq!(c[0].open.as_ref().unwrap()["person_message_id"], "q_p");
    }

    #[test]
    fn the_lines_are_fixed_words_and_ids() {
        let l = seat_line("review", "t_x", "q_r", "q_p").unwrap();
        assert!(l.starts_with("真人決定:照審查改。"), "{l}");
        assert!(l.contains("q_r") && l.contains("q_p") && l.contains("t_x"));
        let k = seat_line("keep", "t_x", "q_r", "q_p").unwrap();
        assert!(k.starts_with("真人決定:維持原需求。"), "{k}");
        assert_eq!(seat_line("both", "t_x", "q_r", "q_p"), None);
        assert_eq!(
            thread_line("keep", "human:ab", "20:31").as_deref(),
            Some("真人決定:維持原需求(human:ab 在台北 20:31 按的)")
        );
        assert!(ask_id_ok("ask_0123456789abcdef"));
        assert!(!ask_id_ok("ask_../x") && !ask_id_ok("ask_") && !ask_id_ok("q_1"));
    }
}
