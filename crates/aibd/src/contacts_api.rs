//! S89-talk-2b HTTP side (IFACE-S89-talk-2b-7b81e-1118): `GET /api/v1/contacts`,
//! `GET /api/v1/flows`, `GET /api/v1/prefs/{name}`, and the two writes, which come in as
//! `POST /api/v1/act` types (`contact_set`, `prefs_set`; only a confirmed page acts).

use crate::api::AppState;
use crate::contacts::{self, BookError, Query, Refused, SeatFacts};
use aib_store::NewEvent;
use axum::extract::{Path, Query as Q, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;

/// The act types this module answers.
pub const CONTACT_SET: &str = "contact_set";
pub const PREFS_SET: &str = "prefs_set";

fn refuse(status: u16, code: &str, message: &str) -> Response {
    (
        StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST),
        Json(json!({
            "code": code,
            "message": message,
            "request_id": crate::guard::request_id(),
        })),
    )
        .into_response()
}

fn refused(r: Refused) -> Response {
    refuse(r.0, r.1, &r.2)
}

fn book_error(e: &BookError) -> Response {
    match e {
        BookError::Unreadable(why) => {
            tracing::error!(%why, "contacts file unreadable; left as it is");
            refuse(
                503,
                "store_unreadable",
                "the contacts file cannot be read; it was left as it is",
            )
        }
        BookError::Io(e) => {
            tracing::error!(error = %e, "contacts file io");
            refuse(500, "internal", "internal error")
        }
    }
}

fn internal() -> Response {
    refuse(500, "internal", "internal error")
}

/// Why a blocking part did not answer; turned into a response by [`Fail::answer`]
/// (a `Response` itself is too large to carry in an `Err`).
enum Fail {
    Internal,
    Book(BookError),
    Refused(Refused),
}

impl Fail {
    fn answer(self) -> Response {
        match self {
            Fail::Internal => internal(),
            Fail::Book(e) => book_error(&e),
            Fail::Refused(r) => refused(r),
        }
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct ListQuery {
    q: Option<String>,
    presence: Option<String>,
    group: Option<String>,
    include_blocked: Option<u8>,
    all: Option<u8>,
}

/// Every seat this aibd has, as the list shows it (`presence` measured from the sessions).
fn seat_facts(st: &AppState) -> Result<Vec<SeatFacts>, Fail> {
    let seats = st.store.seats().map_err(|_| Fail::Internal)?;
    let metas: HashMap<String, aib_store::SeatMeta> = st
        .store
        .seat_metas()
        .map_err(|_| Fail::Internal)?
        .into_iter()
        .map(|m| (m.seat_id.clone(), m))
        .collect();
    let names: HashMap<String, String> = st
        .personas
        .cards()
        .map(|c| c.into_iter().map(|c| (c.uid, c.name)).collect())
        .unwrap_or_default();
    // the newest session of each seat says where it stands
    let mut now_of: HashMap<String, (&'static str, Option<usize>)> = HashMap::new();
    for s in st.sessions.list() {
        let Some(seat) = s.seat_id.clone() else {
            continue;
        };
        let presence = match s.state {
            "running" | "stop_unconfirmed" => "running",
            "pending" => "queued",
            _ => continue,
        };
        let position = if presence == "queued" {
            st.sessions
                .get(&s.id)
                .and_then(|r| r.queue.map(|q| q.position))
        } else {
            None
        };
        now_of.insert(seat, (presence, position));
    }
    Ok(seats
        .into_iter()
        .map(|s| {
            let m = metas.get(&s.seat_id);
            let uid = m.and_then(|m| m.persona_uid.clone());
            let (presence, queue_position) =
                now_of.get(&s.seat_id).copied().unwrap_or(("stopped", None));
            SeatFacts {
                name: uid
                    .as_deref()
                    .and_then(|u| names.get(u).cloned())
                    .unwrap_or_else(|| s.name.clone()),
                persona_uid: uid,
                provider: m.map(|m| m.provider.clone()),
                mode: m.and_then(|m| m.mode.clone()),
                presence,
                queue_position,
                seat_id: s.seat_id,
            }
        })
        .collect())
}

/// `GET /api/v1/contacts`: the seats of this aibd with what the person set about each.
/// The human key of a page (see [`contacts::self_row`] and [`crate::person`]); `None` for the
/// start-up token.
pub fn page_key(c: &crate::guard::Caller) -> Option<String> {
    match c {
        // 519d0 11-18: the person's key on this aibd, the same after pairing again
        crate::guard::Caller::Page(a) => crate::person::key_for(a.user_page).or_else(|| {
            Some(format!(
                "human:{}",
                a.digest[..8]
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>()
            ))
        }),
        _ => None,
    }
}

pub async fn list(
    State(st): State<AppState>,
    axum::Extension(c): axum::Extension<crate::guard::Caller>,
    Q(q): Q<ListQuery>,
) -> Response {
    let me = page_key(&c);
    // 5d33a L1 (519d0: a user folder page reads counts and state only): a note is the
    // person's own words, so a user page's rows carry no note, and it cannot search by one
    let user_page = match &c {
        crate::guard::Caller::Page(a) => crate::seat_input::held_as_user_page(a).await,
        _ => false,
    };
    let query = Query {
        q: q.q,
        presence: q.presence,
        group: q.group,
        include_blocked: q.include_blocked == Some(1),
        all: q.all == Some(1),
    };
    let done = tokio::task::spawn_blocking(move || {
        let facts = seat_facts(&st)?;
        let entries = st.contacts.entries().map_err(Fail::Book)?;
        let now = crate::sessions::now_rfc3339();
        // the message library (2a) gives the counts; unreadable ⇒ zeros, the list still shows
        let msgs = st.messages.facts(None).unwrap_or_else(|e| {
            tracing::warn!(error = %e, "messages unreadable; contacts show no counts");
            Vec::new()
        });
        let rows = facts
            .iter()
            .map(|f| {
                let e = entries
                    .get(&format!("seat:{}", f.seat_id))
                    .cloned()
                    .unwrap_or_default();
                let key = format!("seat:{}", f.seat_id);
                let m = contacts::message_counts(&msgs, &key, me.as_deref());
                contacts::row(f, &e, &now, &m)
            })
            .collect::<Vec<_>>();
        let mut rows = rows;
        if let Some(me) = &me {
            let e = entries.get(me).cloned().unwrap_or_default();
            let m = contacts::message_counts(&msgs, me, Some(me));
            rows.push(contacts::self_row(me, &e, &now, &m));
        }
        if user_page {
            for r in rows.iter_mut().filter_map(Value::as_object_mut) {
                r.remove("note");
            }
        }
        Ok::<_, Fail>(contacts::select(rows, &query))
    })
    .await;
    match done {
        Ok(Ok(rows)) => Json(json!({ "contacts": rows })).into_response(),
        Ok(Err(f)) => f.answer(),
        Err(_) => internal(),
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct FlowQuery {
    window: Option<u64>,
}

/// `GET /api/v1/flows?window=600`: messages per pair and kind in the window, read from the
/// message library (S89-talk-2a; [`contacts::flow_pairs`]). The window is clamped to
/// 60–3600 s. A library that cannot be read answers `source: "none"` and no pairs, so the
/// page draws its still version.
pub async fn flows(State(st): State<AppState>, Q(q): Q<FlowQuery>) -> Response {
    let window = q.window.unwrap_or(600).clamp(60, 3600);
    let now_unix = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
        .unwrap_or(0);
    let since = now_unix.saturating_sub(i64::try_from(window).unwrap_or(3600));
    let db = st.messages.clone();
    let facts = tokio::task::spawn_blocking(move || db.facts(Some(since))).await;
    let (source, pairs, truncated) = match facts {
        Ok(Ok(f)) => {
            let (p, t) = contacts::flow_pairs(&f);
            ("messages", p, t)
        }
        _ => ("none", Vec::new(), false),
    };
    Json(json!({
        "window": window,
        "now": crate::sessions::now_rfc3339(),
        "source": source,
        "pairs": pairs,
        "truncated": truncated,
    }))
    .into_response()
}

/// `GET /api/v1/prefs/{name}`: only `contacts_fx` (defaults at revision 0 until set).
pub async fn prefs(State(st): State<AppState>, Path(name): Path<String>) -> Response {
    if name != contacts::FX {
        return refuse(404, "pref_not_found", "the only preference is contacts_fx");
    }
    match tokio::task::spawn_blocking(move || st.contacts.fx()).await {
        Ok(Ok((fx, revision))) => {
            Json(json!({"name": contacts::FX, "value": fx, "revision": revision})).into_response()
        }
        Ok(Err(e)) => book_error(&e),
        Err(_) => internal(),
    }
}

/// act `contact_set` (the caller is a confirmed page; `seat_input::act` checked it).
pub async fn contact_set(st: AppState, body: Value, me: Option<String>) -> Response {
    let (key, expected, patch) = match contacts::parse_set(&body) {
        Ok(p) => p,
        Err(r) => return refused(r),
    };
    let done = tokio::task::spawn_blocking(move || {
        let known = match key.strip_prefix("seat:") {
            Some(id) => st.store.seat(id).map_err(|_| Fail::Internal)?.is_some(),
            // a page may set its own row; other pages' rows come with the pairing list
            None => me.as_deref() == Some(key.as_str()),
        };
        let now = crate::sessions::now_rfc3339();
        let set = st
            .contacts
            .set_contact(&key, expected, &patch, known, &now)
            .map_err(Fail::Book)?;
        let (entry, changed) = match set {
            Ok(x) => x,
            Err(r) => return Err(Fail::Refused(r)),
        };
        // what changed, never what was written (a note is the person's own words)
        let journaled = changed.is_empty() || {
            let ev = crate::sessions::new_id("ev_").ok().map(|id| NewEvent {
                event_id: id.clone(),
                aggregate_type: "contact".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: "contact.set".into(),
                correlation_id: None,
                occurred_at: now.clone(),
                payload: json!({"key": key, "fields": changed, "revision": entry.revision}),
            });
            ev.is_some_and(|ev| st.store.append(vec![ev]).is_ok())
        };
        Ok(json!({"key": key, "entry": entry, "changed": changed, "journaled": journaled}))
    })
    .await;
    match done {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(f)) => f.answer(),
        Err(_) => internal(),
    }
}

/// act `prefs_set` (confirmed page only, as above).
pub async fn prefs_set(st: AppState, body: Value) -> Response {
    let (expected, fx) = match contacts::parse_fx(&body) {
        Ok(p) => p,
        Err(r) => return refused(r),
    };
    match tokio::task::spawn_blocking(move || st.contacts.set_fx(expected, fx)).await {
        Ok(Ok(Ok(revision))) => {
            Json(json!({"name": contacts::FX, "value": fx, "revision": revision})).into_response()
        }
        Ok(Ok(Err(r))) => refused(r),
        Ok(Err(e)) => book_error(&e),
        Err(_) => internal(),
    }
}
