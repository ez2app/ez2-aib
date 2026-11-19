//! N-now slice 1 (519d0 05:24, QUEUE-need-519d0-1118 #2): each seat says what it is doing now,
//! the way 1.x `aib now` fills the status line. A seat writes its own line with its seat token:
//! `POST /api/v1/act {"type":"seat_now","item":…,"done":n,"total":m,"state":…,"track"?,"why"?,"idea"?}`.
//!
//! - **Only its own seat**: the seat is the one the token was made for
//!   ([`crate::seat_token::SeatTokens::seat`]); the body has no seat field, and one is refused.
//! - **Short, one line**: `item` ≤ [`MAX_ITEM`] characters, `why` / `idea` ≤ [`MAX_NOTE`], no
//!   control characters; `done` ≤ `total` ≤ 99; `track` 0–100. `state`: `busy` (working),
//!   `reply` (waiting for another seat), `decide` (waiting for a person), `clear` (nothing on
//!   hand: the line is removed). Not an approval of anything: no field here is read as one.
//! - **Kept in memory** (gone on restart, like the seat tokens and sessions) and journaled as
//!   `seat.now` with the numbers, the state and the lengths + SHA-256 of the three texts, never
//!   the texts (a seat's words stay out of the journal, as for `seat.message`).
//! - **Who reads it**: `GET /api/v1/now` for a page or the start-up token. A seat token cannot
//!   (the guard lets it list seats and act only), so a seat never reads another seat's line.

use crate::api::AppState;
use crate::guard::request_id;
use aib_store::NewEvent;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

pub const KIND: &str = "seat_now";
pub const MAX_ITEM: usize = 120;
pub const MAX_NOTE: usize = 300;
pub const MAX_STEPS: u16 = 99;
pub const STATES: [&str; 4] = ["busy", "reply", "decide", "clear"];

/// One seat's line.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Now {
    pub item: String,
    pub done: u16,
    pub total: u16,
    pub state: &'static str,
    pub track: Option<u8>,
    pub why: Option<String>,
    pub idea: Option<String>,
    pub updated_at: String,
}

#[derive(Default)]
pub struct Board {
    inner: Mutex<BTreeMap<String, Now>>,
}

impl Board {
    fn lock(&self) -> MutexGuard<'_, BTreeMap<String, Now>> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `None` removes the seat's line (`state: clear`).
    pub fn set(&self, seat_id: &str, now: Option<Now>) {
        let mut g = self.lock();
        match now {
            Some(n) => {
                g.insert(seat_id.to_owned(), n);
            }
            None => {
                g.remove(seat_id);
            }
        }
    }

    pub fn get(&self, seat_id: &str) -> Option<Now> {
        self.lock().get(seat_id).cloned()
    }

    pub fn all(&self) -> BTreeMap<String, Now> {
        self.lock().clone()
    }
}

static BOARD: std::sync::LazyLock<Board> = std::sync::LazyLock::new(Board::default);

/// This aibd's lines (one process, one board; gone on restart).
pub fn board() -> &'static Board {
    &BOARD
}

/// Why a `seat_now` body was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused(pub &'static str);

fn one_line(v: Option<&Value>, name: &'static str, max: usize) -> Result<Option<String>, Refused> {
    match v {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if s.trim().is_empty() => Ok(None),
        Some(Value::String(s)) if s.chars().count() <= max && !s.chars().any(char::is_control) => {
            Ok(Some(s.clone()))
        }
        Some(_) => Err(Refused(name)),
    }
}

fn small(v: Option<&Value>, name: &'static str, max: u64) -> Result<Option<u64>, Refused> {
    match v {
        None | Some(Value::Null) => Ok(None),
        Some(x) => x
            .as_u64()
            .filter(|n| *n <= max)
            .map(Some)
            .ok_or(Refused(name)),
    }
}

/// The body ⇒ the line (`None` for `state: clear`). Unknown fields, a seat field, a step past
/// the total or text over its limit are refused, never trimmed.
pub fn parse(body: &Value, updated_at: &str) -> Result<Option<Now>, Refused> {
    let obj = body.as_object().ok_or(Refused("body"))?;
    for k in obj.keys() {
        if !matches!(
            k.as_str(),
            "type" | "item" | "done" | "total" | "state" | "track" | "why" | "idea"
        ) {
            return Err(Refused("unknown_field"));
        }
    }
    let state = obj
        .get("state")
        .map_or(Some("busy"), Value::as_str)
        .and_then(|s| STATES.iter().copied().find(|x| *x == s))
        .ok_or(Refused("state"))?;
    if state == "clear" {
        return Ok(None);
    }
    let item = one_line(obj.get("item"), "item", MAX_ITEM)?.ok_or(Refused("item"))?;
    let total = small(obj.get("total"), "total", u64::from(MAX_STEPS))?.unwrap_or(1);
    let done = small(obj.get("done"), "done", total)?.unwrap_or(0);
    if total == 0 {
        return Err(Refused("total"));
    }
    let track = small(obj.get("track"), "track", 100)?;
    Ok(Some(Now {
        item,
        // both checked against MAX_STEPS / total above
        done: u16::try_from(done).map_err(|_| Refused("done"))?,
        total: u16::try_from(total).map_err(|_| Refused("total"))?,
        state,
        track: track.and_then(|t| u8::try_from(t).ok()),
        why: one_line(obj.get("why"), "why", MAX_NOTE)?,
        idea: one_line(obj.get("idea"), "idea", MAX_NOTE)?,
        updated_at: updated_at.to_owned(),
    }))
}

fn sha(s: &str) -> String {
    Sha256::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The `seat.now` payload: numbers, state, lengths and hashes; never the texts.
pub fn event_payload(seat_id: &str, now: Option<&Now>) -> Value {
    let text = |s: Option<&str>| s.map(|s| json!({"len": s.chars().count(), "sha256": sha(s)}));
    match now {
        None => json!({"seat_id": seat_id, "state": "clear"}),
        Some(n) => json!({
            "seat_id": seat_id,
            "state": n.state,
            "done": n.done,
            "total": n.total,
            "track": n.track,
            "item": text(Some(&n.item)),
            "why": text(n.why.as_deref()),
            "idea": text(n.idea.as_deref()),
        }),
    }
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// act `seat_now` with a seat token (dispatched from `seat_input::act` by `type`).
pub async fn act(st: AppState, key: crate::seat_token::Key, body: Value) -> Response {
    let Some(seat_id) = crate::seat_token::tokens().seat(&key) else {
        return refuse(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "this seat has stopped",
        );
    };
    if body.get("type").and_then(Value::as_str) != Some(KIND) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_act_type",
            "type must be seat_now",
        );
    }
    let at = crate::sessions::now_rfc3339();
    let now = match parse(&body, &at) {
        Ok(n) => n,
        Err(Refused(field)) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                &format!(
                "{field}: item ≤ {MAX_ITEM} and why / idea ≤ {MAX_NOTE} characters on one line, \
                     done ≤ total ≤ {MAX_STEPS}, track 0-100, state busy|reply|decide|clear, \
                     no other field (the seat is the token's own)"
            ),
            )
        }
    };
    board().set(&seat_id, now.clone());
    let Ok(event_id) = crate::sessions::new_id("ev_") else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no event id");
    };
    let event = NewEvent {
        event_id: event_id.clone(),
        aggregate_type: "seat_now".into(),
        aggregate_id: event_id,
        sequence: 1,
        event_type: "seat.now".into(),
        correlation_id: None,
        occurred_at: at,
        payload: event_payload(&seat_id, now.as_ref()),
    };
    let store = st.store.clone();
    let journaled = matches!(
        tokio::task::spawn_blocking(move || store.append(vec![event])).await,
        Ok(Ok(_))
    );
    Json(json!({"seat_id": seat_id, "now": now, "journaled": journaled})).into_response()
}

/// `GET /api/v1/now`: every seat's line (a page or the start-up token; never a seat token).
pub async fn list(State(_st): State<AppState>) -> Response {
    let rows: Vec<Value> = board()
        .all()
        .into_iter()
        .map(|(seat_id, n)| json!({"seat_id": seat_id, "now": n}))
        .collect();
    Json(json!({ "now": rows })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    const AT: &str = "2026-11-18T07:00:00.000Z";

    #[test]
    fn a_line_is_short_one_line_and_has_no_seat_field() {
        let ok = parse(
            &json!({"type":"seat_now","item":"S89-talk-2b 收據","done":3,"total":4,"state":"reply",
                    "track":85,"why":"等收據","idea":"合併兩支"}),
            AT,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            (ok.done, ok.total, ok.state, ok.track),
            (3, 4, "reply", Some(85))
        );
        for bad in [
            json!({"type":"seat_now","item":"x","seat_id":"seat_other"}),
            json!({"type":"seat_now","item":"x".repeat(MAX_ITEM + 1)}),
            json!({"type":"seat_now","item":"兩行\n第二行"}),
            json!({"type":"seat_now","item":"x","done":5,"total":4}),
            json!({"type":"seat_now","item":"x","total":100}),
            json!({"type":"seat_now","item":"x","total":0}),
            json!({"type":"seat_now","item":"x","track":101}),
            json!({"type":"seat_now","item":"x","state":"approved"}),
            json!({"type":"seat_now","item":"x","why":"y".repeat(MAX_NOTE + 1)}),
            json!({"type":"seat_now"}),
        ] {
            assert!(parse(&bad, AT).is_err(), "reason: bad_line_taken {bad}");
        }
        assert_eq!(
            parse(&json!({"type":"seat_now","state":"clear"}), AT).unwrap(),
            None
        );
    }

    #[test]
    fn the_journal_has_numbers_and_hashes_never_the_words() {
        let n = parse(
            &json!({"type":"seat_now","item":"秘密的項目名","why":"理由原文","total":2,"done":1}),
            AT,
        )
        .unwrap();
        let p = event_payload("seat_1", n.as_ref()).to_string();
        assert!(
            !p.contains("秘密的項目名") && !p.contains("理由原文"),
            "reason: text_in_journal {p}"
        );
        assert!(p.contains("\"len\":6"), "{p}");
        assert!(p.contains("\"done\":1"));
    }

    #[test]
    fn the_board_keeps_one_line_per_seat_and_clear_removes_it() {
        let b = Board::default();
        let n = parse(&json!({"type":"seat_now","item":"a"}), AT).unwrap();
        b.set("s1", n.clone());
        b.set("s2", n);
        assert_eq!(b.all().len(), 2);
        b.set("s1", None);
        assert!(b.get("s1").is_none(), "reason: clear_kept_the_line");
        assert!(b.get("s2").is_some());
    }
}
