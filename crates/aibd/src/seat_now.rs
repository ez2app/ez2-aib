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
/// Slice 2: the seat's Stop hook asks whether its own line was written since the turn began
/// (`{"type":"seat_now_check","since":<UTC RFC 3339>}`; [`crate::now_gate`]).
pub const CHECK: &str = "seat_now_check";
/// Slice 3: when a seat opens, or its conversation is cleared or compacted, its SessionStart
/// hook reads its own line back (`{"type":"seat_now_mine"}`; [`crate::now_gate::session_start`]).
pub const MINE: &str = "seat_now_mine";
/// 1a93b GAP-web-backend §6 (519d0 11-20 22:34): the seat says its handoff note is written
/// (`{"type":"seat_handoff","path":"<absolute path of the .md>"}`), so a page shows "written,
/// here" without reading the seat's words. aibd never opens the path (no file probe for a seat).
pub const HANDOFF: &str = "seat_handoff";
/// The longest handoff path taken, in characters.
pub const MAX_PATH: usize = 512;
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
    inner: Mutex<Lines>,
}

#[derive(Default)]
struct Lines {
    lines: BTreeMap<String, Now>,
    /// When each seat last wrote (a `clear` too): what the Stop hook checks.
    written: BTreeMap<String, String>,
    /// Each seat's last handoff note ([`HANDOFF`]).
    handoffs: BTreeMap<String, Handoff>,
}

/// One seat's last handoff note: where it says it wrote it, and when it said so.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Handoff {
    pub path: String,
    pub at: String,
}

impl Board {
    fn lock(&self) -> MutexGuard<'_, Lines> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `None` removes the seat's line (`state: clear`); either way the seat wrote `at`.
    pub fn set(&self, seat_id: &str, now: Option<Now>, at: &str) {
        let mut g = self.lock();
        g.written.insert(seat_id.to_owned(), at.to_owned());
        match now {
            Some(n) => {
                g.lines.insert(seat_id.to_owned(), n);
            }
            None => {
                g.lines.remove(seat_id);
            }
        }
    }

    /// The seat stopped (its token was revoked): its line and its last write go, so the board
    /// never shows a stopped or archived seat, and a seat opened again starts with no line
    /// (0eed7 PRECHECK-chain4 L1).
    pub fn forget(&self, seat_id: &str) {
        let mut g = self.lock();
        g.lines.retain(|k, _| k != seat_id);
        g.written.retain(|k, _| k != seat_id);
        g.handoffs.retain(|k, _| k != seat_id);
    }

    pub fn get(&self, seat_id: &str) -> Option<Now> {
        self.lock().lines.get(seat_id).cloned()
    }

    pub fn written(&self, seat_id: &str) -> Option<String> {
        self.lock().written.get(seat_id).cloned()
    }

    pub fn all(&self) -> BTreeMap<String, Now> {
        self.lock().lines.clone()
    }

    pub fn set_handoff(&self, seat_id: &str, h: Handoff) {
        self.lock().handoffs.insert(seat_id.to_owned(), h);
    }

    pub fn handoffs(&self) -> BTreeMap<String, Handoff> {
        self.lock().handoffs.clone()
    }
}

/// The body of a [`HANDOFF`] ⇒ the path: `type` and `path` only; the path one line of at most
/// [`MAX_PATH`] characters, absolute (`/…`, `C:\…`, `C:/…` or `\\…`) and ending in `.md`.
/// Refused, never trimmed. Nothing is read from disk.
pub fn parse_handoff(body: &Value) -> Result<String, Refused> {
    let obj = body.as_object().ok_or(Refused("body"))?;
    if obj.keys().any(|k| k != "type" && k != "path") {
        return Err(Refused("unknown_handoff_field"));
    }
    let p = one_line(obj.get("path"), "path", MAX_PATH)?.ok_or(Refused("path"))?;
    let b = p.as_bytes();
    let drive = b.len() > 2
        && b[0].is_ascii_alphabetic()
        && b[1] == b':'
        && (b[2] == b'\\' || b[2] == b'/');
    let absolute = p.starts_with('/') || p.starts_with("\\\\") || drive;
    if !absolute || !p.to_ascii_lowercase().ends_with(".md") {
        return Err(Refused("path"));
    }
    Ok(p)
}

/// One handoff as a page sees it; a user folder page gets the path as `{len, sha256}` (the
/// same rule as [`view`]).
pub fn handoff_view(seat_id: &str, h: &Handoff, user_page: bool) -> Value {
    let path = if user_page {
        json!({"len": h.path.chars().count(), "sha256": sha(&h.path)})
    } else {
        json!(h.path)
    };
    json!({"seat_id": seat_id, "path": path, "at": h.at})
}

/// act [`HANDOFF`] with a seat token (dispatched from `seat_input::act` by `type`).
pub async fn handoff_act(st: AppState, key: crate::seat_token::Key, body: Value) -> Response {
    let Some(seat_id) = crate::seat_token::tokens().seat(&key) else {
        return refuse(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "this seat has stopped",
        );
    };
    let path = match parse_handoff(&body) {
        Ok(p) => p,
        Err(Refused(field)) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                &format!("{field}: seat_handoff takes path only: the absolute path of the .md, one line, ≤ {MAX_PATH} characters"),
            )
        }
    };
    let at = crate::sessions::now_rfc3339();
    let h = Handoff {
        path,
        at: at.clone(),
    };
    board().set_handoff(&seat_id, h.clone());
    let Ok(event_id) = crate::sessions::new_id("ev_") else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no event id");
    };
    let event = NewEvent {
        event_id: event_id.clone(),
        aggregate_type: "seat_now".into(),
        aggregate_id: event_id,
        sequence: 1,
        event_type: "seat.handoff_written".into(),
        correlation_id: None,
        occurred_at: at,
        // the path as length + hash only, like the words of seat.now
        payload: json!({"seat_id": seat_id, "path": {"len": h.path.chars().count(), "sha256": sha(&h.path)}}),
    };
    let store = st.store.clone();
    let journaled = matches!(
        tokio::task::spawn_blocking(move || store.append(vec![event])).await,
        Ok(Ok(_))
    );
    Json(json!({"seat_id": seat_id, "handoff": h, "journaled": journaled})).into_response()
}

/// `seat_now_check`: was the seat's line written at or after `since` (second resolution, both
/// UTC RFC 3339), and is a done stage still missing its review (track / why / idea)?
pub fn check(board: &Board, seat_id: &str, body: &Value) -> Result<Value, Refused> {
    let obj = body.as_object().ok_or(Refused("body"))?;
    if obj.keys().any(|k| k != "type" && k != "since") {
        return Err(Refused("unknown_check_field"));
    }
    let since = obj
        .get("since")
        .and_then(Value::as_str)
        .and_then(crate::contacts::utc_time)
        .ok_or(Refused("since"))?;
    let updated = board
        .written(seat_id)
        .is_some_and(|w| w.get(..19) >= since.get(..19));
    let review_due = board.get(seat_id).is_some_and(|n| {
        (matches!(n.state, "reply" | "decide") || n.done == n.total)
            && (n.track.is_none() || n.why.is_none() || n.idea.is_none())
    });
    Ok(json!({"updated": updated, "review_due": review_due}))
}

/// `seat_now_mine`: the seat's own line (`null` when it has none). No field but `type`, and
/// never another seat's line: the seat is the token's.
pub fn mine(board: &Board, seat_id: &str, body: &Value) -> Result<Value, Refused> {
    let obj = body.as_object().ok_or(Refused("body"))?;
    if obj.keys().any(|k| k != "type") {
        return Err(Refused("unknown_mine_field"));
    }
    Ok(json!({ "now": board.get(seat_id) }))
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
        Some(Value::String(s))
            if s.chars().count() <= max
                && !s.chars().any(|c| c.is_control() || is_bidi_control(c)) =>
        {
            Ok(Some(s.clone()))
        }
        Some(_) => Err(Refused(name)),
    }
}

/// A character that reorders the text around it on screen (0eed7 PRECHECK-chain4 N2): the
/// embeddings / overrides U+202A–U+202E, the isolates U+2066–U+2069, and the marks U+200E /
/// U+200F / U+061C. A status line shows as typed or not at all.
fn is_bidi_control(c: char) -> bool {
    matches!(c, '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}' | '\u{200E}' | '\u{200F}' | '\u{061C}')
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
    match body.get("type").and_then(Value::as_str) {
        Some(KIND) => {}
        Some(CHECK) => {
            return match check(board(), &seat_id, &body) {
                Ok(v) => Json(v).into_response(),
                Err(Refused(field)) => refuse(
                    StatusCode::BAD_REQUEST,
                    "invalid_request",
                    &format!("{field}: seat_now_check takes since (UTC RFC 3339) only"),
                ),
            };
        }
        Some(MINE) => {
            return match mine(board(), &seat_id, &body) {
                Ok(v) => Json(v).into_response(),
                Err(Refused(field)) => refuse(
                    StatusCode::BAD_REQUEST,
                    "invalid_request",
                    &format!("{field}: seat_now_mine takes no field"),
                ),
            };
        }
        _ => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "unknown_act_type",
                "type must be seat_now, seat_now_check or seat_now_mine",
            )
        }
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
    board().set(&seat_id, now.clone(), &at);
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

/// One seat's line as a page sees it. A user folder page (0eed7 PRECHECK-chain4 N1; a9136 07:02,
/// the same rule as the 2a message body) gets the numbers, state and track, and `item` / `why` /
/// `idea` only as `{len, sha256}`: the words are read on the built-in page only.
pub fn view(seat_id: &str, n: &Now, user_page: bool) -> Value {
    let mut now = json!(n);
    if user_page {
        for k in ["item", "why", "idea"] {
            if let Some(s) = now[k].as_str() {
                let hidden = json!({"len": s.chars().count(), "sha256": sha(s)});
                now[k] = hidden;
            }
        }
    }
    json!({"seat_id": seat_id, "now": now})
}

/// `GET /api/v1/now`: every seat's line (a page or the start-up token; never a seat token).
/// A credential a user folder page got, or any credential while a user page serves `/`
/// (unknown counts as one), reads it through [`view`]: lengths and hashes, not the words.
pub async fn list(
    State(_st): State<AppState>,
    axum::Extension(c): axum::Extension<crate::guard::Caller>,
) -> Response {
    let user_page = match c {
        crate::guard::Caller::Page(a) => {
            a.user_page
                || tokio::task::spawn_blocking(|| crate::static_files::web_source() == "user")
                    .await
                    .unwrap_or(true)
        }
        _ => false,
    };
    let rows: Vec<Value> = board()
        .all()
        .iter()
        .map(|(seat_id, n)| view(seat_id, n, user_page))
        .collect();
    let handoffs: Vec<Value> = board()
        .handoffs()
        .iter()
        .map(|(seat_id, h)| handoff_view(seat_id, h, user_page))
        .collect();
    Json(json!({ "now": rows, "handoffs": handoffs })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_handoff_is_one_absolute_md_path_and_nothing_else() {
        for ok in [
            "/Users/a/.ez2-aib/pending/HANDOFF-a1.md",
            "C:\\Users\\a\\HANDOFF.MD",
            "C:/Users/a/handoff.md",
            "\\\\host\\share\\h.md",
        ] {
            assert_eq!(
                parse_handoff(&json!({"type": HANDOFF, "path": ok})),
                Ok(ok.to_owned()),
                "reason: refused_{ok}"
            );
        }
        for (bad, why) in [
            (json!({"type": HANDOFF}), "path"),
            (json!({"type": HANDOFF, "path": "HANDOFF.md"}), "path"),
            (json!({"type": HANDOFF, "path": "/a/notes.txt"}), "path"),
            (json!({"type": HANDOFF, "path": "/a/b\n.md"}), "path"),
            (
                json!({"type": HANDOFF, "path": format!("/{}.md", "x".repeat(MAX_PATH))}),
                "path",
            ),
            (
                json!({"type": HANDOFF, "path": "/a.md", "seat_id": "seat_other"}),
                "unknown_handoff_field",
            ),
        ] {
            assert_eq!(parse_handoff(&bad), Err(Refused(why)), "reason: took {bad}");
        }
    }

    #[test]
    fn a_handoff_is_shown_to_the_built_in_page_and_hashed_for_a_user_page_and_forgotten_on_stop() {
        let b = Board::default();
        let h = Handoff {
            path: "/u/HANDOFF-x.md".into(),
            at: AT.into(),
        };
        b.set_handoff("seat_a", h.clone());
        assert_eq!(handoff_view("seat_a", &h, false)["path"], "/u/HANDOFF-x.md");
        let hidden = handoff_view("seat_a", &h, true);
        assert_eq!(hidden["path"]["len"], 15, "reason: user_page_sees_the_path");
        assert!(
            !hidden.to_string().contains("HANDOFF-x"),
            "reason: user_page_sees_the_path"
        );
        assert_eq!(b.handoffs().get("seat_a"), Some(&h));
        b.forget("seat_a");
        assert!(b.handoffs().is_empty(), "reason: handoff_outlives_the_seat");
    }

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
            json!({"type":"seat_now","item":"abc\u{202E}fed"}),
            json!({"type":"seat_now","item":"x","why":"\u{2067}y"}),
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
        b.set("s1", n.clone(), AT);
        b.set("s2", n, AT);
        assert_eq!(b.all().len(), 2);
        b.set("s1", None, AT);
        assert!(b.get("s1").is_none(), "reason: clear_kept_the_line");
        assert!(b.get("s2").is_some());
    }

    #[test]
    fn the_check_says_written_since_the_turn_and_a_done_stage_needs_its_review() {
        let b = Board::default();
        let ask = |since: &str| json!({"type": CHECK, "since": since});
        let v = check(&b, "s1", &ask("2026-11-18T06:00:00.000Z")).unwrap();
        assert_eq!(v["updated"], false, "reason: never_written_counts {v}");
        let n = parse(
            &json!({"type":"seat_now","item":"a","done":1,"total":3}),
            AT,
        )
        .unwrap();
        b.set("s1", n, "2026-11-18T06:00:05.120Z");
        let v = check(&b, "s1", &ask("2026-11-18T06:00:05.900Z")).unwrap();
        assert_eq!(v["updated"], true, "same second counts {v}");
        let v = check(&b, "s1", &ask("2026-11-18T06:00:06.000Z")).unwrap();
        assert_eq!(v["updated"], false, "reason: old_write_counts {v}");
        assert_eq!(v["review_due"], false);
        // a stage handed back without the review
        let n = parse(
            &json!({"type":"seat_now","item":"a","state":"reply","track":80}),
            AT,
        )
        .unwrap();
        b.set("s1", n, "2026-11-18T06:01:00.000Z");
        let v = check(&b, "s1", &ask("2026-11-18T06:00:30.000Z")).unwrap();
        assert_eq!(v["review_due"], true, "reason: review_not_due {v}");
        let n = parse(
            &json!({"type":"seat_now","item":"a","state":"reply","track":80,"why":"w","idea":"i"}),
            AT,
        )
        .unwrap();
        b.set("s1", n, "2026-11-18T06:01:00.000Z");
        assert_eq!(
            check(&b, "s1", &ask("2026-11-18T06:00:30.000Z")).unwrap()["review_due"],
            false
        );
        // a clear is a write too, and the seat's check reads only its own line
        b.set("s2", None, "2026-11-18T06:02:00.000Z");
        assert_eq!(
            check(&b, "s2", &ask("2026-11-18T06:01:00.000Z")).unwrap()["updated"],
            true
        );
        assert!(check(&b, "s1", &json!({"type": CHECK, "since": "yesterday"})).is_err());
        assert!(check(
            &b,
            "s1",
            &json!({"type": CHECK, "since": "2026-11-18T06:00:00Z", "seat_id": "s2"})
        )
        .is_err());
    }

    #[test]
    fn a_seat_reads_back_only_its_own_line() {
        let b = Board::default();
        let mine_of = |seat: &str| mine(&b, seat, &json!({"type": MINE})).unwrap();
        assert_eq!(mine_of("s1")["now"], Value::Null, "no line yet");
        let n = parse(
            &json!({"type":"seat_now","item":"甲","done":1,"total":3,"state":"busy"}),
            AT,
        )
        .unwrap();
        b.set("s1", n, AT);
        let other = parse(&json!({"type":"seat_now","item":"乙"}), AT).unwrap();
        b.set("s2", other, AT);
        let v = mine_of("s1");
        assert_eq!(
            (v["now"]["item"].as_str(), v["now"]["done"].as_u64()),
            (Some("甲"), Some(1))
        );
        assert_eq!(mine_of("s2")["now"]["item"], "乙");
        // naming another seat is refused, not followed
        assert!(mine(&b, "s1", &json!({"type": MINE, "seat_id": "s2"})).is_err());
        b.set("s1", None, AT);
        assert_eq!(mine_of("s1")["now"], Value::Null, "clear removes it");
    }

    #[test]
    fn a_user_page_sees_the_line_without_why_or_idea() {
        let n = parse(
            &json!({"type":"seat_now","item":"項目原文","done":1,"total":2,"state":"reply",
                    "track":70,"why":"原因原文","idea":"建議原文"}),
            AT,
        )
        .unwrap()
        .unwrap();
        let built_in = view("s1", &n, false);
        assert_eq!(
            built_in["now"]["why"], "原因原文",
            "the built-in page reads the words"
        );
        let user = view("s1", &n, true);
        let text = user.to_string();
        assert!(
            !text.contains("項目原文") && !text.contains("原因原文") && !text.contains("建議原文"),
            "reason: user_page_reads_the_words {text}"
        );
        assert_eq!(user["now"]["item"]["len"], 4, "{text}");
        assert_eq!(user["now"]["why"]["sha256"], sha("原因原文"), "{text}");
        assert_eq!(
            (user["now"]["done"].as_u64(), user["now"]["track"].as_u64()),
            (Some(1), Some(70))
        );
    }
}
