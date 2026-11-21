//! G5 信橋 (DESIGN-g5-bridge-7ebca-1121 r1 §1; 5d33a PREREAD-g5-bridge M1): a 1.x seat moves
//! into a web seat of this aibd with the owner's fingerprint. Moving a seat is the owner's
//! red line (開關席), and the move decides whose name a web seat's letters to 1.x seats carry
//! and where letters to the old code go, so it is never read from a file an AI can edit: it
//! is written here, into the message library's `seat_moves` ([`aib_store::seat_moves`]).
//!
//! `POST /api/v1/act {"type":"seat_move","old_code":"7ebca","seat_id":"seat_…","ticket"?}`
//! from the built-in page in this pairing only (the caller refuses a user folder page, a
//! carried pairing, a seat's token). Without a ticket aibd reads the facts, passes [`gate`]
//! and asks the fingerprint (`202 {ticket}`); with the ticket it reads them again and writes
//! only when the ticket approves this very move ([`recheck`]). Journals `seat.moved
//! {old_code, seat, by}` (the switch record, DECISION-council-cutover §4-2) and answers the
//! steps aibd does not do itself: the 1.x half (stop the old seat's mail, Q1 (c); stop its
//! timers) is done by the cabin's lead with 1.x commands.
//!
//! `{"type":"seat_unmove","old_code":"7ebca"}` (going back, §4-3) needs no fingerprint, like
//! `seat_untrust`: it only takes the web seat's old name away and sends letters back to the
//! old seat's ledger. Journals `seat.unmoved {old_code, seat, by}`.

use std::path::PathBuf;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::guard::request_id;

pub const MOVE: &str = "seat_move";
pub const UNMOVE: &str = "seat_unmove";
/// The operation the owner approves.
pub const OP: &str = "seat.move";

/// The steps that are not aibd's, answered with a move (the page lists them; §4-2 is not
/// passed until they are done).
pub const STEPS: [&str; 3] = [
    "1.x:關掉舊席收信(519d0 用 1.x 指令;Q1 (c))",
    "1.x:舊席停定時",
    "寫切換紀錄已由 aibd 記下(seat.moved);交接檔反向接回 = seat_unmove",
];

/// What aibd reads before it asks, and again before it writes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    pub user_page: bool,
    /// The web seat's row state (`None`: no such seat).
    pub state: Option<String>,
    /// The old code has a folder in this machine's 1.x ledger (a plain folder, not a link).
    pub in_ledger: bool,
    /// The web seat the old code is moved into now.
    pub code_now: Option<String>,
    /// The old code the web seat stands for now.
    pub seat_now: Option<String>,
}

/// This machine's 1.x ledger (`<state>/dm-queue`), as the mirror and legacy_send find it.
pub fn ledger() -> Option<PathBuf> {
    crate::bridge_cfg::queue_root(&crate::bridge_cfg::now(), |k| std::env::var(k).ok())
}

pub fn in_ledger(code: &str) -> bool {
    ledger().is_some_and(|q| {
        std::fs::symlink_metadata(q.join(code)).is_ok_and(|m| m.file_type().is_dir())
    })
}

/// Read now; nothing is kept between two reads.
pub fn read(
    store: &aib_store::Store,
    messages: &aib_store::MessageDb,
    old_code: &str,
    seat_id: &str,
) -> Facts {
    Facts {
        user_page: crate::static_files::web_source() == "user",
        state: store.seat(seat_id).ok().flatten().map(|s| s.state),
        in_ledger: in_ledger(old_code),
        code_now: messages.moved_seat(old_code).ok().flatten(),
        seat_now: messages.moved_code(seat_id).ok().flatten(),
    }
}

/// May `old_code` move into `seat_id`? `Ok(true)`: it already has (nothing to ask or write).
pub fn gate(old_code: &str, seat_id: &str, f: &Facts) -> Result<bool, (StatusCode, &'static str)> {
    if f.user_page {
        return Err((StatusCode::FORBIDDEN, "page_only"));
    }
    match f.state.as_deref() {
        None => return Err((StatusCode::NOT_FOUND, "seat_not_found")),
        Some("ARCHIVED") => return Err((StatusCode::NOT_FOUND, "seat_archived")),
        Some(_) => {}
    }
    if !f.in_ledger {
        return Err((StatusCode::NOT_FOUND, "old_seat_not_found"));
    }
    match (&f.code_now, &f.seat_now) {
        (Some(s), _) if s == seat_id => Ok(true),
        (Some(_), _) => Err((StatusCode::CONFLICT, "old_code_moved")),
        (None, Some(o)) if o != old_code => Err((StatusCode::CONFLICT, "seat_stands_for_another")),
        _ => Ok(false),
    }
}

/// What the owner is asked: aibd's words, the two codes only.
pub fn ask_of(old_code: &str, seat_id: &str, by: String) -> crate::owner_ok::Ask {
    let params = json!({"old_code": old_code, "seat_id": seat_id});
    let web: String = seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect();
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!("舊席 {old_code} 搬進網頁席 {web}:寄 {old_code} 的信改送網頁席"),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// After the fingerprint, on facts read again: the gate again, and what the owner approved is
/// still this move.
pub fn recheck(
    old_code: &str,
    seat_id: &str,
    f: &Facts,
    approved: &[u8],
) -> Result<bool, (StatusCode, &'static str)> {
    let already = gate(old_code, seat_id, f)?;
    if ask_of(old_code, seat_id, String::new()).params != approved {
        return Err((StatusCode::CONFLICT, "seat_move_changed"));
    }
    Ok(already)
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "page_only" => "a seat is moved from the built-in page in this pairing only",
        "seat_not_found" | "seat_archived" => "no such web seat now",
        "old_seat_not_found" => "the old code has no folder in this machine's 1.x mail ledger",
        "old_code_moved" => {
            "the old code is already moved into another web seat: move it back first"
        }
        "seat_stands_for_another" => "the web seat already stands for another old code",
        "seat_move_changed" => "the move changed since the fingerprint: ask again",
        "body" => "body must be {type, old_code, seat_id, ticket?}; old_code is five lower hex",
        _ => "the move could not be written",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::INTERNAL_SERVER_ERROR => "internal",
        _ => "forbidden",
    };
    (
        status,
        Json(json!({"code": code, "reason": reason, "message": message,
            "request_id": request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    old_code: String,
    #[serde(default)]
    seat_id: Option<String>,
    #[serde(default)]
    ticket: Option<String>,
}

fn parse(body: Value) -> Option<Raw> {
    serde_json::from_value::<Raw>(body)
        .ok()
        .filter(|r| aib_store::seat_moves::code_ok(&r.old_code))
        .filter(|r| {
            r.seat_id
                .as_deref()
                .is_none_or(crate::seat_trust::seat_id_ok)
        })
}

/// `seat_move`, for the built-in page `by` (`page:<pair>`).
pub async fn moved(st: AppState, body: Value, by: String) -> Response {
    let Some((code, sid, ticket)) =
        parse(body).and_then(|r| Some((r.old_code, r.seat_id?, r.ticket)))
    else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let facts = |st: &AppState| {
        let (store, m, c, s) = (
            st.store.clone(),
            st.messages.clone(),
            code.clone(),
            sid.clone(),
        );
        tokio::task::spawn_blocking(move || read(&store, &m, &c, &s))
    };
    let Ok(f) = facts(&st).await else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store");
    };
    match gate(&code, &sid, &f) {
        Err((status, why)) => return refuse(status, why),
        Ok(true) => {
            return Json(json!({"done": true, "already": true, "old_code": code,
                "seat_id": sid, "steps": STEPS}))
            .into_response()
        }
        Ok(false) => {}
    }
    let ask = ask_of(&code, &sid, by.clone());
    let Some(ticket) = ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let Ok(f) = facts(&st).await else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store");
    };
    match recheck(&code, &sid, &f, &ask.params) {
        Err((status, why)) => return refuse(status, why),
        Ok(true) => {
            return Json(json!({"done": true, "already": true, "old_code": code,
                "seat_id": sid, "steps": STEPS}))
            .into_response()
        }
        Ok(false) => {}
    }
    let (m, c, s, b) = (st.messages.clone(), code.clone(), sid.clone(), by.clone());
    let wrote = tokio::task::spawn_blocking(move || {
        m.seat_move(&c, &s, &b, &crate::sessions::now_rfc3339())
    })
    .await;
    match wrote {
        Ok(Ok(aib_store::seat_moves::Moved::Moved)) => {}
        Ok(Ok(aib_store::seat_moves::Moved::Already)) => {
            return Json(json!({"done": true, "already": true, "old_code": code,
                "seat_id": sid, "steps": STEPS}))
            .into_response()
        }
        Ok(Ok(aib_store::seat_moves::Moved::CodeTaken { .. })) => {
            return refuse(StatusCode::CONFLICT, "old_code_moved")
        }
        Ok(Ok(aib_store::seat_moves::Moved::SeatTaken { .. })) => {
            return refuse(StatusCode::CONFLICT, "seat_stands_for_another")
        }
        _ => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store"),
    }
    // DESIGN-web-inbox-aibd-7ebca-1121 r1 B: the letters still open in the old code's ledger
    // folder go to the web seat now, each once
    let forwarded = match ledger() {
        Some(queue) => {
            let (m, c, s) = (st.messages.clone(), code.clone(), sid.clone());
            tokio::task::spawn_blocking(move || {
                let root = queue
                    .parent()
                    .and_then(std::path::Path::parent)
                    .map(std::path::Path::to_path_buf)?;
                crate::dm_import::forward_open(&m, &root, &queue, &c, &s).ok()
            })
            .await
            .ok()
            .flatten()
            .unwrap_or_default()
        }
        None => Vec::new(),
    };
    if !forwarded.is_empty() {
        crate::dm_import::wake(&sid);
    }
    crate::seats::side_event(
        &st,
        MOVE,
        "seat.moved",
        json!({"old_code": code, "seat": sid, "by": by, "forwarded": forwarded.len()}),
    )
    .await;
    Json(
        json!({"done": true, "old_code": code, "seat_id": sid, "steps": STEPS,
        "forwarded": forwarded.len()}),
    )
    .into_response()
}

/// `seat_unmove`, for the built-in page `by`: no fingerprint.
pub async fn unmoved(st: AppState, body: Value, by: String) -> Response {
    let Some(code) = parse(body)
        .filter(|r| r.ticket.is_none() && r.seat_id.is_none())
        .map(|r| r.old_code)
    else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    if crate::static_files::web_source() == "user" {
        return refuse(StatusCode::FORBIDDEN, "page_only");
    }
    let (m, c) = (st.messages.clone(), code.clone());
    let took =
        tokio::task::spawn_blocking(move || m.seat_unmove(&c, &crate::sessions::now_rfc3339()))
            .await;
    let seat = match took {
        Ok(Ok(s)) => s,
        _ => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store"),
    };
    // 0eed7 VERIFY-integ3 M2: what the move handed to the web seat leaves its inbox
    let mut taken_back = 0;
    if let Some(s) = &seat {
        let (m, sid, c) = (st.messages.clone(), s.clone(), code.clone());
        taken_back = tokio::task::spawn_blocking(move || crate::dm_import::take_back(&m, &sid, &c))
            .await
            .ok()
            .and_then(Result::ok)
            .map_or(0, |v| v.len());
        crate::seats::side_event(
            &st,
            UNMOVE,
            "seat.unmoved",
            json!({"old_code": code, "seat": s, "by": by, "taken_back": taken_back}),
        )
        .await;
    }
    Json(
        json!({"done": true, "old_code": code, "was_moved_into": seat,
        "taken_back": taken_back}),
    )
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SEAT: &str = "seat_33ff9a0b1c2d3e4f";

    fn facts() -> Facts {
        Facts {
            user_page: false,
            state: Some("ACTIVE".into()),
            in_ledger: true,
            code_now: None,
            seat_now: None,
        }
    }

    #[test]
    fn only_a_live_seat_and_a_ledger_seat_not_moved_elsewhere_are_asked_for() {
        assert_eq!(gate("7ebca", SEAT, &facts()), Ok(false));
        let with = |edit: fn(&mut Facts)| {
            let mut f = facts();
            edit(&mut f);
            gate("7ebca", SEAT, &f).map_err(|e| e.1)
        };
        assert_eq!(with(|f| f.user_page = true), Err("page_only"));
        assert_eq!(with(|f| f.state = None), Err("seat_not_found"));
        assert_eq!(
            with(|f| f.state = Some("ARCHIVED".into())),
            Err("seat_archived")
        );
        assert_eq!(with(|f| f.in_ledger = false), Err("old_seat_not_found"));
        assert_eq!(with(|f| f.code_now = Some(SEAT.into())), Ok(true));
        assert_eq!(
            with(|f| f.code_now = Some("seat_other".into())),
            Err("old_code_moved")
        );
        assert_eq!(
            with(|f| f.seat_now = Some("519d0".into())),
            Err("seat_stands_for_another")
        );
    }

    #[test]
    fn the_ticket_is_for_this_move_only() {
        let ask = ask_of("7ebca", SEAT, "page:x".into());
        assert_eq!(ask.operation, OP);
        assert!(ask.resource.contains("7ebca") && ask.resource.contains("33ff9"));
        assert_eq!(recheck("7ebca", SEAT, &facts(), &ask.params), Ok(false));
        let other = ask_of("519d0", SEAT, String::new());
        assert_eq!(
            recheck("7ebca", SEAT, &facts(), &other.params).map_err(|e| e.1),
            Err("seat_move_changed")
        );
    }

    #[test]
    fn the_body_is_two_codes_of_their_shape() {
        let ok = parse(json!({"type": MOVE, "old_code": "7ebca", "seat_id": SEAT}));
        assert!(ok.is_some());
        for bad in [
            json!({"type": MOVE, "old_code": "7EBCA", "seat_id": SEAT}),
            json!({"type": MOVE, "old_code": "../x1", "seat_id": SEAT}),
            json!({"type": MOVE, "old_code": "7ebca", "seat_id": "33ff9"}),
            json!({"type": MOVE, "old_code": "7ebca", "seat_id": SEAT, "to": "x"}),
        ] {
            assert!(parse(bad.clone()).is_none(), "{bad}");
        }
    }
}
