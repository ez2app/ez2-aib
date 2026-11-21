//! The message library (S89-talk-2a; DECISION-council-dmq-519d0-1118, interface r1 in
//! PLAN-S89-talk-2c §3 / §6): a person writes to a seat, reads a thread, marks it read and
//! closes what waited on them.
//!
//! - Rows live in their own file, `<data>/messages.sqlite` ([`aib_store::MessageDb`]); the
//!   main database gets the events only. Every write is: the row, then its event, then the
//!   row marked journaled; [`reconcile`] at start writes `message.recovered` for a row a stop
//!   left without its event.
//! - Each move of a message is decided by the dm queue's pure core ([`aib_dmq::decide`]): the
//!   same status machine and evidence rules as 1.x. The reader / closer is the owner of the
//!   queue the message sits in (`seat:<its key>`), so only the receiver moves it.
//! - Keys: a page is `human:<16 hex>` ([`crate::contacts_api::page_key`]), a seat
//!   `seat:<seat id>`; a thread is the two keys sorted and joined by `~`.
//! - Texts: kept [`aib_store::BODY_KEEP_DAYS`] days; the event has the length and SHA-256
//!   only. A thread's texts are read on the built-in page only: a credential a user folder
//!   page got, or any page while a user page serves `/`, sees `len` and `body_sha256`.
//! - `message_send` types through [`crate::seat_input::type_to_seat`] (the same checks as
//!   `seat_input`: safe seats only from a user page, the mode must take input).
//!
//! Seat tokens do not use these yet (b4297: a seat's own receive / close acts come next);
//! the folder rule and the dgrs rule of the seat they write to are the seat's (seats.rs).

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use crate::pairing::PageAccess;
use aib_dmq::evidence::{self, Probe};
use aib_dmq::rev::{rev_check, IfRev, Presence};
use aib_dmq::{decide, Decision, Reason, Status};
use aib_store::{Inserted, MessageDb, MessageRow, NewEvent, Store, Updated};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::sync::Arc;

pub const SEND: &str = "message_send";
pub const READ: &str = "message_read";
pub const DONE: &str = "message_done";

/// Longest `idem_key`.
pub const IDEM_MAX: usize = 64;
/// The reason a page's close is written with when it gives neither evidence nor a reason:
/// the person pressed the button (the dmq core wants one of the two).
pub const PAGE_DONE_WHY: &str = "decided on the page";

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

fn internal(what: &str) -> Response {
    refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", what)
}

/// `<a>~<b>` with the two keys sorted.
pub fn thread_of(a: &str, b: &str) -> String {
    if a <= b {
        format!("{a}~{b}")
    } else {
        format!("{b}~{a}")
    }
}

/// The two ends of a thread id, when it is one.
pub fn ends(thread_id: &str) -> Option<(&str, &str)> {
    let (a, b) = thread_id.split_once('~')?;
    (key_ok(a) && key_ok(b) && a < b).then_some((a, b))
}

/// `human:<16 hex>` or `seat:<seat id>`.
pub fn key_ok(k: &str) -> bool {
    if let Some(h) = k.strip_prefix("human:") {
        return h.len() == 16
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase());
    }
    k.strip_prefix("seat:").is_some_and(|s| {
        !s.is_empty()
            && s.len() <= 64
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    })
}

fn idem_ok(k: &str) -> bool {
    (1..=IDEM_MAX).contains(&k.len()) && k.bytes().all(|b| b.is_ascii_graphic())
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

/// What the page shows for a row's dmq status (chat_mcp words, interface r1 §2).
pub fn state_of(item: &Map<String, Value>) -> &'static str {
    if item.get("failed").and_then(Value::as_bool) == Some(true) {
        return "failed";
    }
    match item
        .get("status")
        .and_then(Value::as_str)
        .and_then(Status::parse)
    {
        Some(Status::Claimed) => "dispatched",
        Some(Status::Delivered | Status::Read) => "received",
        Some(Status::Done | Status::Archived) => "processed",
        _ => "queued",
    }
}

fn status_of(row: &MessageRow) -> Option<Status> {
    row.item
        .get("status")
        .and_then(Value::as_str)
        .and_then(Status::parse)
}

/// Whether a message still waits to be read by its receiver (queued or delivered); the
/// thread's `unread` flag and the contacts list's counts both use this.
pub fn is_unread(item: &Map<String, Value>) -> bool {
    matches!(
        item.get("status")
            .and_then(Value::as_str)
            .and_then(Status::parse),
        Some(Status::Queued | Status::Delivered)
    )
}

/// The message object of interface r1; `body` only when `with_body`; `unread` for `me`.
pub fn object(row: &MessageRow, me: &str, with_body: bool) -> Value {
    let st = status_of(row);
    let mut o = json!({
        "message_id": row.message_id,
        "thread_id": row.thread_id,
        "from_seat": row.from_key,
        "to_seat": row.to_key,
        "kind": row.item.get("talk_kind").cloned().unwrap_or_else(|| json!("say")),
        "state": state_of(&row.item),
        "status": st.map(Status::as_str),
        "revision": row.rev,
        "hop_count": 0,
        "len": row.len,
        "body_sha256": row.body_sha256,
        "created": row.created,
        "updated": row.updated,
        "unread": row.to_key == me && is_unread(&row.item),
    });
    if with_body {
        if let (Some(b), Some(m)) = (&row.body, o.as_object_mut()) {
            m.insert("body".into(), json!(b));
        }
    }
    if let (Some(item), Some(m)) = (imported_item(row, with_body), o.as_object_mut()) {
        m.insert("item".into(), item);
    }
    o
}

/// The ledger fields an imported or mirrored 1.x letter shows (043dd EVID-sharedthread r3: the
/// page's state filter and card marks read them): an allow-list, never the whole item. The
/// words in it (what a seat waits for, why and with what it closed, the original sender) follow
/// the body rule: a page that reads no bodies gets their length and SHA-256.
/// The rest of the 043dd 05:23 list (a9136 da7bfca2b: 校正 `correction` and 每日 `seat_day`
/// records): a seat's judgement (`track`, `judge`), its counts this goal, the day.
pub const IMPORTED_KEYS: [&str; 16] = [
    "imported",
    "dm_id",
    "dm_status",
    "dm_view",
    "dm_superseded_by",
    "card",
    "card_rule",
    "dm_reply",
    "track",
    "seat",
    "judge",
    "corrections",
    "summaries",
    "handoffs",
    "day",
    // a pull back's score before (a9136 f05f3d088: correction = back on the axis only)
    "from_track",
];
/// The word fields of [`IMPORTED_KEYS`]' list.
pub const IMPORTED_WORDS: [&str; 7] = [
    "dm_wait",
    "dm_done_why",
    "dm_done_evidence",
    "sender_label",
    "why",
    "idea",
    "card_said",
];
/// The record kinds (`item.source.kind`) whose rows carry the list.
pub const IMPORTED_KINDS: [&str; 4] = ["dm", "judgement", "correction", "seat_day"];

/// An imported row's allow-listed item (`None` for any other row).
pub fn imported_item(row: &MessageRow, with_body: bool) -> Option<Value> {
    let imported = row.item.get("imported").and_then(Value::as_bool) == Some(true)
        || row
            .item
            .get("source")
            .and_then(|s| s.get("kind"))
            .and_then(Value::as_str)
            .is_some_and(|k| IMPORTED_KINDS.contains(&k));
    if !imported {
        return None;
    }
    let mut out = serde_json::Map::new();
    // values, not texts: a field the list names as a value never carries an object or a list
    for k in IMPORTED_KEYS {
        if let Some(v) = row
            .item
            .get(k)
            .filter(|v| !v.is_null() && !v.is_object() && !v.is_array())
        {
            out.insert(k.into(), v.clone());
        }
    }
    // a goal: when it was written, and its words
    for k in ["first_goal", "goal"] {
        if let Some(g) = row.item.get(k).filter(|g| g.is_object()) {
            let mut o = serde_json::Map::new();
            if let Some(at) = g.get("at").filter(|v| v.is_string() || v.is_number()) {
                o.insert("at".into(), at.clone());
            }
            if let Some(s) = g.get("item").and_then(Value::as_str) {
                o.insert("item".into(), crate::workspace::words_or_hash(s, with_body));
            }
            out.insert(k.into(), Value::Object(o));
        }
    }
    for k in IMPORTED_WORDS {
        if let Some(s) = row.item.get(k).and_then(Value::as_str) {
            out.insert(k.into(), crate::workspace::words_or_hash(s, with_body));
        }
    }
    if let Some(src) = row.item.get("source").filter(|s| s.is_object()) {
        let mut s = serde_json::Map::new();
        for k in ["kind", "id", "via", "reply"] {
            if let Some(v) = src.get(k).filter(|v| !v.is_null()) {
                s.insert(k.into(), v.clone());
            }
        }
        out.insert("source".into(), Value::Object(s));
    }
    Some(Value::Object(out))
}

/// Whether this page reads texts: the built-in page only (see the module doc).
async fn reads_bodies(page: &PageAccess) -> bool {
    if page.user_page {
        return false;
    }
    tokio::task::spawn_blocking(|| crate::static_files::web_source() != "user")
        .await
        .unwrap_or(false)
}

/// Evidence a page can give: a reply it sent (a message id of the library) or a PR url.
/// Files and commits are not looked up for a page (an HTTP caller must not learn which
/// paths exist on this machine).
struct PageProbe {
    db: Arc<MessageDb>,
}

impl Probe for PageProbe {
    fn is_file(&self, _text: &str) -> bool {
        false
    }
    fn commit_exists(&self, _sha: &str) -> bool {
        false
    }
    fn reply_sent_by(&self, seat: &str, id: &str) -> bool {
        matches!(self.db.get(id), Ok(Some(r)) if r.from_key == seat)
    }
}

fn event(kind: &str, payload: Value) -> Option<NewEvent> {
    let event_id = crate::sessions::new_id("ev_").ok()?;
    Some(NewEvent {
        event_id: event_id.clone(),
        aggregate_type: "message".into(),
        aggregate_id: event_id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    })
}

fn row_facts(r: &MessageRow) -> Value {
    json!({
        "message_id": r.message_id,
        "thread_id": r.thread_id,
        "from": r.from_key,
        "to": r.to_key,
        "revision": r.rev,
        "status": r.item.get("status"),
        "kind": r.item.get("talk_kind"),
        "len": r.len,
        "sha256": r.body_sha256,
    })
}

/// Journal `kind` for these rows (already written), then mark them journaled. False when the
/// event did not go in: the rows stay unjournaled and [`reconcile`] writes them at start.
pub(crate) async fn journal(
    store: Arc<Store>,
    db: Arc<MessageDb>,
    kind: &'static str,
    extra: Value,
    rows: Vec<MessageRow>,
) -> bool {
    tokio::task::spawn_blocking(move || {
        let mut payload = json!({ "messages": rows.iter().map(row_facts).collect::<Vec<_>>() });
        if let (Some(p), Value::Object(x)) = (payload.as_object_mut(), extra) {
            p.extend(x);
        }
        let Some(ev) = event(kind, payload) else {
            return false;
        };
        if store.append(vec![ev]).is_err() {
            return false;
        }
        rows.iter()
            .all(|r| db.mark_journaled(&r.message_id, r.rev).is_ok())
    })
    .await
    .unwrap_or(false)
}

/// At start: a `message.recovered` event for every row a stop left without its event, and
/// the texts past [`aib_store::BODY_KEEP_DAYS`] dropped. Returns how many rows were recovered.
pub fn reconcile(db: &MessageDb, store: &Store) -> aib_store::Result<usize> {
    let rows = db.unjournaled()?;
    for r in &rows {
        let Some(ev) = event("message.recovered", row_facts(r)) else {
            continue;
        };
        store.append(vec![ev])?;
        db.mark_journaled(&r.message_id, r.rev)?;
    }
    let _ = db.drop_bodies_before(now_unix() - aib_store::BODY_KEEP_DAYS * 86_400);
    Ok(rows.len())
}

fn page_key(page: &PageAccess) -> String {
    crate::contacts_api::page_key(&Caller::Page(*page)).unwrap_or_default()
}

fn str_field<'a>(body: &'a Value, k: &str) -> Option<&'a str> {
    body.get(k).and_then(Value::as_str)
}

/// `message_send {to_seat, body, thread_id?, idem_key?, kind?}`: a person writes to a seat.
pub async fn send(st: AppState, page: PageAccess, body: Value) -> Response {
    let me = page_key(&page);
    let (Some(to), Some(text)) = (str_field(&body, "to_seat"), str_field(&body, "body")) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, to_seat, body}",
        );
    };
    let Some(seat_id) = to.strip_prefix("seat:").filter(|_| key_ok(to)) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "to_seat must be seat:<seat id>",
        );
    };
    let thread_id = thread_of(&me, to);
    if str_field(&body, "thread_id").is_some_and(|t| t != thread_id) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "thread_mismatch",
            "thread_id is the two keys sorted and joined by ~",
        );
    }
    match str_field(&body, "kind") {
        None | Some("say") => {}
        Some(_) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "a person sends kind say",
            )
        }
    }
    let idem = match body.get("idem_key") {
        None | Some(Value::Null) => None,
        Some(Value::String(k)) if idem_ok(k) => Some(k.clone()),
        Some(_) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "idem_key is 1–64 printable characters",
            )
        }
    };
    let with_body = reads_bodies(&page).await;
    let digest_of = |t: &str| {
        use sha2::Digest;
        sha2::Sha256::digest(t.as_bytes())
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect::<String>()
    };
    // the same key from the same page: the first answer, nothing typed again
    if let Some(k) = &idem {
        let (db, me2, k2) = (st.messages.clone(), me.clone(), k.clone());
        match tokio::task::spawn_blocking(move || db.by_idem(&me2, &k2)).await {
            Ok(Ok(Some(r))) if r.to_key == to && r.body_sha256 == digest_of(text) => {
                return Json(json!({ "message": object(&r, &me, with_body) })).into_response()
            }
            Ok(Ok(Some(_))) => {
                return refuse(
                    StatusCode::CONFLICT,
                    "idem_conflict",
                    "this idem_key was used for another text",
                )
            }
            Ok(Ok(None)) => {}
            _ => return internal("messages not read"),
        }
    }
    let typed = match crate::seat_input::type_to_seat(&st, &page, seat_id, text).await {
        Ok(t) => t,
        Err(r) => return *r,
    };
    let Ok(message_id) = crate::sessions::new_id("q_") else {
        return internal("no message id");
    };
    let (now, at) = (crate::sessions::now_rfc3339(), now_unix());
    let mut item = aib_dmq::norm::v2_defaults();
    for (k, v) in [
        ("id", json!(message_id)),
        (
            "status",
            json!(if typed.late { "queued" } else { "delivered" }),
        ),
        ("talk_kind", json!("say")),
        ("sender_seat", json!(me)),
        ("sender_kind", json!("human")),
        ("thread", json!(thread_id)),
        ("sent_at", json!(at)),
        ("session_id", json!(typed.session_id)),
        ("pairing", json!(me.trim_start_matches("human:"))),
        // r3b: confirmed by a "y" in the terminal, which a same-account AI can also type
        ("method", json!("terminal_y")),
        ("verified_person", json!(false)),
        ("mode", json!(typed.mode)),
        (
            "delivery",
            json!(if typed.late { "pending" } else { "written" }),
        ),
    ] {
        item.insert(k.into(), v);
    }
    if !typed.late {
        item.insert("delivered_at".into(), json!(at));
    }
    let row = MessageRow {
        message_id,
        thread_id,
        from_key: me.clone(),
        to_key: to.to_string(),
        created: now.clone(),
        created_unix: at,
        updated: now,
        rev: 1,
        item,
        body: Some(text.to_string()),
        body_sha256: typed.digest.clone(),
        len: i64::try_from(typed.len).unwrap_or(i64::MAX),
        idem_owner: idem.as_ref().map(|_| me.clone()),
        idem_key: idem,
        journaled: false,
    };
    let (db, r2) = (st.messages.clone(), row.clone());
    let row = match tokio::task::spawn_blocking(move || db.insert(r2)).await {
        Ok(Ok(Inserted::New)) => row,
        // typed already; a second request with the key won the race: answer with its row
        Ok(Ok(Inserted::Existing(r))) => *r,
        _ => {
            // typed already: say so, never type it again (§6)
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({
                    "code": "message_not_stored",
                    "message": "the text was typed to the seat but not kept in the library",
                    "request_id": request_id(),
                    "session_id": typed.session_id,
                })),
            )
                .into_response();
        }
    };
    let journaled = journal(
        st.store.clone(),
        st.messages.clone(),
        "message.sent",
        json!({ "seat_id": seat_id, "session_id": typed.session_id }),
        vec![row.clone()],
    )
    .await;
    let status = if typed.late {
        StatusCode::ACCEPTED
    } else {
        StatusCode::OK
    };
    (
        status,
        Json(json!({ "message": object(&row, &me, with_body), "journaled": journaled })),
    )
        .into_response()
}

/// A move of one row by its receiver, decided by the dmq core; `Err` is the answer to send.
fn move_row(
    row: &MessageRow,
    me: &str,
    to: Status,
    why: Option<&str>,
    evidence: Option<&str>,
    evidence_ref: (Option<evidence::Kind>, Option<String>),
) -> Result<Map<String, Value>, Box<Response>> {
    let actor = format!("seat:{me}");
    let req = aib_dmq::Request {
        seat: me,
        to,
        actor: Some(&actor),
        why,
        evidence,
        evidence_ref,
        expect_status: None,
        caller_is_seat: false,
        now: now_unix(),
    };
    match decide(&row.item, &req) {
        Decision::Allow(o) => Ok(o.item),
        Decision::Refuse { reason, .. } => {
            let (status, code) = match reason {
                Reason::Evidence => (StatusCode::BAD_REQUEST, "evidence_needed"),
                Reason::Actor | Reason::NotFound => (StatusCode::NOT_FOUND, "message_not_found"),
                _ => (StatusCode::CONFLICT, "message_state"),
            };
            Err(Box::new(refuse(status, code, reason.as_str())))
        }
    }
}

/// `message_read {thread_id, message_id}`: every message to this page in the thread up to
/// that one moves to read; the thread's read mark moves there.
pub async fn read(st: AppState, page: PageAccess, body: Value) -> Response {
    // 5d33a 2a 預讀 L1 (a9136 08:51): a user folder page reads counts and state only; marking
    // the person's messages read would hide them from the person, so it is the built-in page's
    let held = crate::seat_input::held_as_user_page(&page).await;
    if held {
        return refuse(
            StatusCode::FORBIDDEN,
            "read_built_in_page_only",
            "a page from the user folder does not mark messages read; open them on the built-in page",
        );
    }
    let me = page_key(&page);
    let with_body = reads_bodies(&page).await;
    read_as(st, me, with_body, body).await
}

/// [`read`] for the receiver `me` (a page key or `seat:<seat id>`).
pub async fn read_as(st: AppState, me: String, with_body: bool, body: Value) -> Response {
    let (Some(thread_id), Some(upto)) = (
        str_field(&body, "thread_id"),
        str_field(&body, "message_id"),
    ) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, thread_id, message_id}",
        );
    };
    // a shared conversation (t_…) has no two ends: what a reader reads there are the rows sent
    // to it (0eed7 VERIFY-pv6 M2: seat_done on a post that named the seat was 404 every time);
    // the thread's own read mark is the timeline's (thread_read, catch-up) and is not moved here
    let shared = thread_id.starts_with("t_");
    if !shared && !ends(thread_id).is_some_and(|(a, b)| a == me || b == me) {
        return refuse(StatusCode::NOT_FOUND, "thread_not_found", "no such thread");
    }
    let (db, t, u, m) = (
        st.messages.clone(),
        thread_id.to_string(),
        upto.to_string(),
        me.clone(),
    );
    let found = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        let Some(last) = db
            .get(&u)?
            .filter(|r| r.thread_id == t && (!shared || r.to_key == m))
        else {
            return Ok(None);
        };
        let rows = db.to_reader_upto(&t, &m, &last)?;
        Ok(Some((last, rows)))
    })
    .await;
    let (last, rows) = match found {
        Ok(Ok(Some(x))) => x,
        Ok(Ok(None)) => {
            return refuse(
                StatusCode::NOT_FOUND,
                "message_not_found",
                "no such message in this thread",
            )
        }
        _ => return internal("messages not read"),
    };
    let mut moved = Vec::new();
    for r in rows {
        if !matches!(status_of(&r), Some(Status::Queued | Status::Delivered)) {
            continue;
        }
        let Ok(item) = move_row(&r, &me, Status::Read, None, None, (None, None)) else {
            continue;
        };
        let (db, id, rev, now) = (
            st.messages.clone(),
            r.message_id.clone(),
            r.rev,
            crate::sessions::now_rfc3339(),
        );
        // a row another write moved since it was read stays as that write left it
        if let Ok(Ok(Updated::Done(after))) =
            tokio::task::spawn_blocking(move || db.update(&id, rev, item, &now)).await
        {
            moved.push(*after);
        }
    }
    // a shared conversation's mark stays the timeline's
    let keep_mark = !shared;
    let (db, t, m, u) = (
        st.messages.clone(),
        thread_id.to_string(),
        me.clone(),
        upto.to_string(),
    );
    if keep_mark
        && !matches!(
            tokio::task::spawn_blocking(move || db.set_read_mark(&t, &m, &u)).await,
            Ok(Ok(()))
        )
    {
        return internal("read mark not kept");
    }
    let n = moved.len();
    let journaled = n == 0
        || journal(
            st.store.clone(),
            st.messages.clone(),
            "message.read",
            json!({ "thread_id": thread_id, "reader": me, "read_upto": upto }),
            moved.clone(),
        )
        .await;
    let last = moved
        .into_iter()
        .find(|r| r.message_id == last.message_id)
        .unwrap_or(last);
    Json(json!({
        "message": object(&last, &me, with_body),
        "read": n,
        "read_upto": upto,
        "journaled": journaled,
    }))
    .into_response()
}

/// `message_done {message_id, expected_revision, evidence?, why?}`: the receiver closes a
/// message (what waited on the person). A wrong revision is 409 with the message as it is.
pub async fn done(st: AppState, page: PageAccess, body: Value) -> Response {
    // 5d33a 2a 預讀 L1: what waits on the person ("等你決定") is closed by the person on the
    // built-in page; a user folder page (or any credential while one serves `/`) reads and
    // marks read, it does not decide. Same judgment as typing and notes.
    if crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "done_built_in_page_only",
            "a page from the user folder does not close messages; close it on the built-in page",
        );
    }
    let me = page_key(&page);
    let with_body = reads_bodies(&page).await;
    done_as(st, me, with_body, body).await
}

/// [`done`] for the receiver `me` (a page key or `seat:<seat id>`). A seat gives the same
/// evidence a page does (a reply it sent, a PR url) or a reason; files and commits are not
/// looked up yet (B1dm-S3c r1).
pub async fn done_as(st: AppState, me: String, with_body: bool, body: Value) -> Response {
    let Some(id) = str_field(&body, "message_id") else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, message_id, expected_revision}",
        );
    };
    let if_rev = match body.get("expected_revision") {
        None | Some(Value::Null) => IfRev::Absent,
        Some(v) => v.as_i64().map_or(IfRev::Garbage, IfRev::Value),
    };
    let (db, i) = (st.messages.clone(), id.to_string());
    let row = match tokio::task::spawn_blocking(move || db.get(&i)).await {
        Ok(Ok(Some(r))) if r.to_key == me => r,
        Ok(Ok(_)) => {
            return refuse(
                StatusCode::NOT_FOUND,
                "message_not_found",
                "no such message to you",
            )
        }
        _ => return internal("messages not read"),
    };
    let stale = |r: &MessageRow| {
        (
            StatusCode::CONFLICT,
            Json(json!({
                "code": "revision_mismatch",
                "message": Reason::RevMismatch.as_str(),
                "request_id": request_id(),
                "message_now": object(r, &me, with_body),
            })),
        )
            .into_response()
    };
    match rev_check(Presence::Live { rev: row.rev }, if_rev, false) {
        (true, _, _) => {}
        (false, Reason::NoRev, _) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "expected_revision is the revision you saw",
            )
        }
        _ => return stale(&row),
    }
    let evidence = str_field(&body, "evidence");
    // a page's button is the person's decision; a seat says why or gives evidence
    let fallback = me.starts_with("human:").then_some(PAGE_DONE_WHY);
    let why = str_field(&body, "why").or(evidence.is_none().then_some(fallback).flatten());
    let probe = PageProbe {
        db: st.messages.clone(),
    };
    let (me2, ev2) = (me.clone(), evidence.map(str::to_owned));
    let evidence_ref =
        tokio::task::spawn_blocking(move || evidence::classify(&me2, ev2.as_deref(), &probe))
            .await
            .unwrap_or((None, None));
    let item = match move_row(&row, &me, Status::Done, why, evidence, evidence_ref) {
        Ok(i) => i,
        Err(r) => return *r,
    };
    let (db, i, rev, now) = (
        st.messages.clone(),
        row.message_id.clone(),
        row.rev,
        crate::sessions::now_rfc3339(),
    );
    let after = match tokio::task::spawn_blocking(move || db.update(&i, rev, item, &now)).await {
        Ok(Ok(Updated::Done(a))) => *a,
        Ok(Ok(Updated::Stale(now))) => return stale(&now),
        Ok(Ok(Updated::Missing)) => {
            return refuse(
                StatusCode::NOT_FOUND,
                "message_not_found",
                "no such message",
            )
        }
        _ => return internal("message not written"),
    };
    let journaled = journal(
        st.store.clone(),
        st.messages.clone(),
        "message.done",
        json!({ "by": me }),
        vec![after.clone()],
    )
    .await;
    // DESIGN-web-inbox-aibd-7ebca-1121 r1 C: a letter from a moved code's ledger is closed
    // there too, one way; a failure is said, the close here stands
    let ledger_closed = match crate::seat_move::ledger().and_then(|q| {
        q.parent()
            .and_then(std::path::Path::parent)
            .map(std::path::Path::to_path_buf)
    }) {
        Some(root) => {
            let (db, row) = (st.messages.clone(), after.clone());
            tokio::task::spawn_blocking(move || crate::dm_import::close_back(&db, &root, &row))
                .await
                .ok()
                .and_then(Result::ok)
                .flatten()
        }
        None => None,
    };
    if ledger_closed == Some(false) {
        tracing::warn!(message_id = %after.message_id, "dm mirror: the ledger copy was not closed");
    }
    let mut out = json!({ "message": object(&after, &me, with_body), "journaled": journaled });
    if let Some(c) = ledger_closed {
        out["ledger_closed"] = json!(c);
    }
    Json(out).into_response()
}

/// The seat side's acts (B1dm-S3c): what a seat token may do with its own messages.
pub const INBOX: &str = "message_inbox";

/// `message_inbox {limit?}`: the messages still open to this seat (queued, delivered, read),
/// oldest first, with their texts (they are the seat's own mail).
pub async fn inbox_as(st: AppState, me: String, body: Value) -> Response {
    let limit = body
        .get("limit")
        .and_then(Value::as_u64)
        .map_or(aib_store::PAGE_MAX, |n| {
            u32::try_from(n).unwrap_or(u32::MAX)
        });
    let (db, m) = (st.messages.clone(), me.clone());
    match tokio::task::spawn_blocking(move || db.open_to(&m, limit)).await {
        Ok(Ok(rows)) => Json(json!({
            "messages": rows.iter().map(|r| object(r, &me, true)).collect::<Vec<_>>(),
        }))
        .into_response(),
        _ => internal("messages not read"),
    }
}

/// Whether `body` is one of a seat's own message acts (see [`seat_act`]).
pub fn is_seat_act(body: &Value) -> bool {
    matches!(
        body.get("type").and_then(Value::as_str),
        Some(INBOX | READ | DONE)
    )
}

/// A seat token's message act: `message_inbox`, `message_read`, `message_done` on its own
/// messages (`seat:<seat id>`); a seat never sends `message_send` (it uses `seat_send`).
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    let me = format!("seat:{seat_id}");
    match body.get("type").and_then(Value::as_str) {
        Some(READ) => read_as(st, me, true, body).await,
        Some(DONE) => done_as(st, me, true, body).await,
        _ => inbox_as(st, me, body).await,
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct ThreadQuery {
    cursor: Option<String>,
}

/// `GET /api/v1/threads/{thread_id}?cursor=`: `{thread_id, read_upto?, messages, next?}`,
/// oldest first, at most [`aib_store::PAGE_MAX`]; a page reads only its own threads.
pub async fn thread(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(thread_id): Path<String>,
    Query(q): Query<ThreadQuery>,
) -> Response {
    let Caller::Page(page) = c else {
        return refuse(
            StatusCode::FORBIDDEN,
            "threads_need_a_page",
            "threads are read by a paired page",
        );
    };
    if thread_id.starts_with("t_") {
        let s = crate::threads::page_sender(&page).await;
        return crate::threads::view_as(st, s, &thread_id, q.cursor).await;
    }
    let me = page_key(&page);
    let mine = ends(&thread_id).is_some_and(|(a, b)| a == me || b == me);
    // 519d0 11-18 裁定 2: two seats of one cabin talk where the person sees it, on the
    // built-in page only (a user folder page still gets lengths only)
    if !mine && !crate::threads::page_reads_seat_pair(&st, &page, &thread_id).await {
        return refuse(StatusCode::NOT_FOUND, "thread_not_found", "no such thread");
    }
    let (db, t, m, cur) = (st.messages.clone(), thread_id.clone(), me.clone(), q.cursor);
    let got = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        let (rows, more) = db.thread_page(&t, cur.as_deref(), aib_store::PAGE_MAX)?;
        let mark = db.read_mark(&t, &m)?;
        Ok((rows, more, mark, cur.is_some()))
    })
    .await;
    let (rows, more, mark, paged) = match got {
        Ok(Ok(x)) => x,
        _ => return internal("thread not read"),
    };
    if rows.is_empty() && !paged {
        return refuse(StatusCode::NOT_FOUND, "thread_not_found", "no such thread");
    }
    let with_body = reads_bodies(&page).await;
    let mut out = json!({
        "thread_id": thread_id,
        "messages": rows.iter().map(|r| object(r, &me, with_body)).collect::<Vec<_>>(),
    });
    if let Some(o) = out.as_object_mut() {
        if let Some(m) = mark {
            o.insert("read_upto".into(), json!(m));
        }
        if more {
            if let Some(first) = rows.first() {
                o.insert("next".into(), json!(first.message_id));
            }
        }
    }
    Json(out).into_response()
}

/// A message library in a fresh file under the temp folder (tests that build an
/// [`AppState`] by hand; nothing else uses it).
pub fn scratch_db() -> Arc<MessageDb> {
    static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let id = crate::sessions::new_id("").unwrap_or_default();
    let p = std::env::temp_dir().join(format!(
        "aibd-test-messages-{}-{n}-{id}.sqlite",
        std::process::id()
    ));
    match MessageDb::open(&p) {
        Ok(db) => Arc::new(db),
        Err(e) => panic!("scratch message library {}: {e}", p.display()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 043dd EVID-sharedthread r3: an imported letter carries its ledger fields from an
    /// allow-list; their words are hashed for a page that reads no bodies; any other row has none.
    #[test]
    fn an_imported_letter_shows_its_allow_listed_ledger_fields() {
        let mut item = serde_json::Map::new();
        for (k, v) in [
            ("imported", json!(true)),
            ("dm_id", json!("q_1")),
            ("dm_view", json!("in_progress")),
            ("dm_wait", json!("等 Andy")),
            ("card", json!("N-web-dmbridge")),
            ("human_id", json!("page:0123abcd")),
            (
                "source",
                json!({"kind": "dm", "id": "q_1", "to": "7ebca", "via": "bridge"}),
            ),
        ] {
            item.insert(k.into(), v);
        }
        let row = MessageRow {
            message_id: "m1".into(),
            thread_id: "T".into(),
            from_key: "seat:519d0".into(),
            to_key: "T".into(),
            created: String::new(),
            created_unix: 0,
            updated: String::new(),
            rev: 1,
            item,
            body: Some("x".into()),
            body_sha256: String::new(),
            len: 1,
            idem_owner: None,
            idem_key: None,
            journaled: false,
        };
        let o = object(&row, "seat:7ebca", true);
        assert_eq!(o["item"]["dm_view"], "in_progress");
        assert_eq!(o["item"]["dm_wait"], "等 Andy");
        assert_eq!(o["item"]["card"], "N-web-dmbridge");
        assert_eq!(
            o["item"]["source"],
            json!({"kind": "dm", "id": "q_1", "via": "bridge"})
        );
        assert!(
            o["item"].get("human_id").is_none(),
            "reason: not_allow_listed"
        );
        let hidden = object(&row, "seat:7ebca", false);
        assert_eq!(
            hidden["item"]["dm_wait"]["len"], 6,
            "reason: words_shown_without_bodies"
        );
        assert_eq!(hidden["item"]["dm_view"], "in_progress");
        let mut plain = row.clone();
        plain.item.remove("imported");
        plain.item.remove("source");
        assert!(
            object(&plain, "seat:7ebca", true).get("item").is_none(),
            "reason: item_on_plain_row"
        );
    }

    /// 043dd 05:23: a 校正 / 每日 record (a9136 da7bfca2b) carries its judgement and counts;
    /// its words follow the body rule; a listed value never brings an object.
    #[test]
    fn a_correction_record_shows_its_judgement_and_hides_its_words() {
        let mut item = serde_json::Map::new();
        for (k, v) in [
            ("source", json!({"kind": "correction", "id": "x"})),
            ("track", json!(72)),
            ("from_track", json!(40)),
            ("seat", json!("7ebca")),
            ("corrections", json!(1)),
            ("why", json!("收據綠")),
            ("judge", json!({"secret": "x"})),
            (
                "goal",
                json!({"at": "2026-11-19T05:00:00Z", "item": "能用", "x": 1}),
            ),
        ] {
            item.insert(k.into(), v);
        }
        let row = MessageRow {
            message_id: "m2".into(),
            thread_id: "T".into(),
            from_key: "seat:7ebca".into(),
            to_key: "T".into(),
            created: String::new(),
            created_unix: 0,
            updated: String::new(),
            rev: 1,
            item,
            body: None,
            body_sha256: String::new(),
            len: 0,
            idem_owner: None,
            idem_key: None,
            journaled: false,
        };
        let o = object(&row, "seat:7ebca", true);
        assert_eq!(o["item"]["track"], 72);
        assert_eq!(o["item"]["from_track"], 40);
        let mut j = row.clone();
        j.item.insert("source".into(), json!({"kind": "judgement"}));
        j.item.remove("imported");
        assert_eq!(
            object(&j, "seat:7ebca", true)["item"]["track"],
            72,
            "reason: judgement_hidden"
        );
        assert_eq!(o["item"]["source"]["kind"], "correction");
        assert_eq!(o["item"]["why"], "收據綠");
        assert_eq!(
            o["item"]["goal"],
            json!({"at": "2026-11-19T05:00:00Z", "item": "能用"})
        );
        assert!(
            o["item"].get("judge").is_none(),
            "reason: object_value_shown"
        );
        let h = object(&row, "seat:7ebca", false);
        assert_eq!(
            h["item"]["why"]["len"], 3,
            "reason: words_shown_without_bodies"
        );
        assert_eq!(h["item"]["goal"]["item"]["len"], 2);
        assert_eq!(h["item"]["corrections"], 1);
    }

    #[test]
    fn a_thread_is_the_two_keys_sorted_and_only_real_keys_make_one() {
        let (h, s) = ("human:0123456789abcdef", "seat:seat_1");
        assert_eq!(thread_of(h, s), thread_of(s, h));
        assert_eq!(thread_of(h, s), "human:0123456789abcdef~seat:seat_1");
        assert_eq!(ends(&thread_of(h, s)), Some((h, s)));
        for bad in [
            "seat:seat_1~human:0123456789abcdef",
            "human:0123456789ABCDEF~seat:a",
            "human:0123~seat:a",
            "seat:a",
            "seat:a~seat:a",
            "seat:a/b~seat:c",
        ] {
            assert_eq!(ends(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_page_state_follows_the_dmq_status() {
        let st = |s: &str, failed: bool| {
            let mut m = Map::new();
            m.insert("status".into(), json!(s));
            if failed {
                m.insert("failed".into(), json!(true));
            }
            state_of(&m)
        };
        assert_eq!(st("queued", false), "queued");
        assert_eq!(st("claimed", false), "dispatched");
        assert_eq!(st("delivered", false), "received");
        assert_eq!(st("read", false), "received");
        assert_eq!(st("done", false), "processed");
        assert_eq!(st("delivered", true), "failed");
    }
}
