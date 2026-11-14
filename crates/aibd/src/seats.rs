//! `/api/v1/seats` (spec 8): create, list and read seats; `/api/v1/seats/open` opens a
//! seat by persona (B4-1b-1, IFACE-b4-1b-seat-open-a9136-1114.md). A seat is a long-lived
//! logical slot, separate from any OS process (spec 4.1); it lives in SQLite,
//! so it is read back after a restart (Phase 1 gate).
//!
//! Writes take an optional `Idempotency-Key` header (spec 8). The same key
//! with the same body on the same route replays the first answer (with
//! `Idempotent-Replayed: true`); with a different body it is
//! `409 idempotency_conflict`. Nothing is written in either case.

use crate::api::AppState;
use crate::personas::{PersonaCard, PersonaSourceError};
use crate::sessions::{json_content_type, new_id, now_rfc3339, ApiError};
use aib_core::state::SeatState;
use aib_core::ErrorCode;
use aib_store::{IdemKey, NewEvent, Seat, SeatCreate, SeatMeta};
use axum::body::Body;
use axum::extract::{Path as UrlPath, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;

/// Request bodies for seats are small.
pub const MAX_BODY: usize = 4 * 1024;
/// Longest seat name, in characters (the column checks the same bound).
pub const MAX_NAME: usize = 128;
/// Longest `Idempotency-Key`.
pub const MAX_KEY: usize = 128;
pub const IDEMPOTENCY_HEADER: &str = "idempotency-key";
pub const REPLAYED_HEADER: &str = "idempotent-replayed";
const CREATE_SCOPE: &str = "POST /api/v1/seats";
const OPEN_SCOPE: &str = "POST /api/v1/seats/open";
/// Longest cwd, in bytes.
pub const MAX_CWD: usize = 4096;
/// Longest cabin / axis id.
pub const MAX_TAG: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCreate {
    name: String,
}

fn bad(msg: &str) -> ApiError {
    ApiError::new(ErrorCode::InvalidRequest, msg)
}

/// The seat name: 1–128 characters, no control characters, not only spaces.
fn valid_name(n: &str) -> bool {
    let len = n.chars().count();
    (1..=MAX_NAME).contains(&len) && !n.chars().any(char::is_control) && !n.trim().is_empty()
}

/// `Idempotency-Key`: absent, or 1–128 of `A-Z a-z 0-9 - _ . :`. A key that
/// is present but unreadable is an error, never "no key" (a silent fallback
/// would turn a retry into a second seat).
pub(crate) fn idempotency_key(h: &HeaderMap) -> Result<Option<String>, ApiError> {
    let mut vals = h.get_all(IDEMPOTENCY_HEADER).iter();
    let Some(v) = vals.next() else {
        return Ok(None);
    };
    if vals.next().is_some() {
        return Err(bad("Idempotency-Key given more than once"));
    }
    let s = v
        .to_str()
        .map_err(|_| bad("Idempotency-Key must be printable ASCII"))?;
    let ok = (1..=MAX_KEY).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b':'));
    if ok {
        Ok(Some(s.to_string()))
    } else {
        Err(bad("Idempotency-Key must be 1-128 of A-Z a-z 0-9 - _ . :"))
    }
}

/// The API form of a seat with nothing else known about it (a seat made with
/// `POST /seats`, or the answer stored with an idempotency key).
pub fn view(s: &Seat) -> serde_json::Value {
    view_with(s, None, None, None)
}

/// The seat's short code: its id without `seat_`, first 5 characters (the 1.x seat code
/// is 5 characters too).
pub fn seat_code(seat_id: &str) -> String {
    seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect()
}

/// What a seat row shows (Andy 11-14 10:44): `<persona> · <provider> · <mode>`, and
/// ` · <seat code>` from the second seat of the same persona on (`dup`). A seat that was
/// not opened by persona shows its name. `name` is the persona's name now, else the
/// seat's name (the persona's name when it opened).
pub fn display(s: &Seat, m: Option<&SeatMeta>, name: Option<&str>, dup: bool) -> String {
    let Some(m) = m.filter(|m| m.persona_uid.is_some()) else {
        return s.name.clone();
    };
    let mut d = format!(
        "{} · {} · {}",
        name.unwrap_or(&s.name),
        m.provider,
        m.mode.as_deref().unwrap_or("?")
    );
    if dup {
        d.push_str(" · ");
        d.push_str(&seat_code(&s.seat_id));
    }
    d
}

/// The API form of a seat: the stored fields, then (only added, B4-1b-1) what it was
/// opened as. `card` is its persona card now (`None`: not opened by persona, the card is
/// gone, or the cards cannot be read; the name is then `null`, never an error);
/// `dup` = an older seat has the same persona (`None` = not worked out: the answer to
/// an open, whose `display` has no suffix; the list has it). cwd and the resume id never
/// appear.
pub fn view_with(
    s: &Seat,
    m: Option<&SeatMeta>,
    card: Option<&PersonaCard>,
    dup: Option<bool>,
) -> serde_json::Value {
    let persona = m.and_then(|m| m.persona_uid.as_ref()).map(|uid| {
        serde_json::json!({
            "uid": uid,
            "name": card.map(|c| &c.name),
            "persona_sha": card.map(|c| &c.persona_sha),
        })
    });
    serde_json::json!({
        "seat_id": s.seat_id,
        "name": s.name,
        "state": s.state,
        "revision": s.revision,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "persona": persona,
        "provider": m.map(|m| &m.provider),
        "mode": m.and_then(|m| m.mode.as_ref()),
        "model": m.and_then(|m| m.model.as_ref()),
        "model_pinned": m.map(|m| m.model_pinned),
        "cabin": m.and_then(|m| m.cabin.as_ref()),
        "axis_id": m.and_then(|m| m.axis_id.as_ref()),
        "display": display(s, m, card.map(|c| c.name.as_str()), dup.unwrap_or(false)),
    })
}

/// Every seat as the API shows it, oldest first: its open fields, its persona's name
/// now, and the seat-code suffix from the second seat of a persona on. The cards being
/// unreadable leaves names `null` and the list still answers.
pub fn views(seats: &[Seat], metas: &[SeatMeta], cards: &[PersonaCard]) -> Vec<serde_json::Value> {
    let meta = |id: &str| metas.iter().find(|m| m.seat_id == id);
    let mut seen: std::collections::HashSet<&str> = std::collections::HashSet::new();
    seats
        .iter()
        .map(|s| {
            let m = meta(&s.seat_id);
            let uid = m.and_then(|m| m.persona_uid.as_deref());
            let card = uid.and_then(|u| cards.iter().find(|c| c.uid == u));
            let dup = uid.is_some_and(|u| !seen.insert(u));
            view_with(s, m, card, Some(dup))
        })
        .collect()
}

fn state_str(s: SeatState) -> String {
    serde_json::to_value(s)
        .ok()
        .and_then(|v| v.as_str().map(str::to_string))
        .unwrap_or_default()
}

fn internal() -> Response {
    ApiError::new(ErrorCode::Internal, "internal error").into_response()
}

pub async fn create(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    if !json_content_type(&headers) {
        return bad("Content-Type must be application/json").into_response();
    }
    let key = match idempotency_key(&headers) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    let Ok(bytes) = axum::body::to_bytes(body, MAX_BODY).await else {
        return ApiError::new(ErrorCode::PayloadTooLarge, "request body over 4 KiB")
            .into_response();
    };
    let req: RawCreate = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(_) => return bad("body must be {\"name\": string}").into_response(),
    };
    if !valid_name(&req.name) {
        return bad("name must be 1-128 characters, no control characters").into_response();
    }
    let Ok(seat_id) = new_id("seat_") else {
        return internal();
    };
    let Ok(event_id) = new_id("ev_") else {
        return internal();
    };
    let now = now_rfc3339();
    let seat = Seat {
        seat_id: seat_id.clone(),
        name: req.name.clone(),
        state: state_str(SeatState::Created),
        revision: 1,
        created_at: now.clone(),
        updated_at: now.clone(),
    };
    let event = NewEvent {
        event_id,
        aggregate_type: "seat".into(),
        aggregate_id: seat_id,
        sequence: 1,
        event_type: "seat.created".into(),
        correlation_id: None,
        occurred_at: now,
        payload: serde_json::json!({"name": req.name, "state": seat.state}),
    };
    // the canonical request: what the key is bound to (field order and
    // whitespace of the original body do not matter)
    let idem = key.map(|k| IdemKey {
        scope: CREATE_SCOPE.into(),
        key: k,
        request: serde_json::json!({"name": req.name}).to_string(),
    });
    let response = view(&seat);
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || {
        store.create_seat(seat, event, idem, StatusCode::CREATED.as_u16(), response)
    })
    .await;
    match res {
        Ok(Ok(SeatCreate::Created(s))) => (StatusCode::CREATED, Json(view(&s))).into_response(),
        Ok(Ok(SeatCreate::Replayed { status, response })) => {
            let code = StatusCode::from_u16(status).unwrap_or(StatusCode::OK);
            let mut r = (code, Json(response)).into_response();
            r.headers_mut()
                .insert(REPLAYED_HEADER, HeaderValue::from_static("true"));
            r
        }
        Ok(Ok(SeatCreate::KeyConflict)) => ApiError::new(
            ErrorCode::IdempotencyConflict,
            "this Idempotency-Key was used for a different request",
        )
        .into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat create failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// Every seat, its open fields and every card (names are read when shown; cards that
/// cannot be read give no names, not an error).
fn read_all(st: &AppState) -> aib_store::Result<Vec<serde_json::Value>> {
    let seats = st.store.seats()?;
    let metas = st.store.seat_metas()?;
    let cards = st.personas.cards().unwrap_or_else(|e| {
        tracing::warn!(error = %e, "persona cards unreadable; seat names shown without them");
        Vec::new()
    });
    Ok(views(&seats, &metas, &cards))
}

pub async fn list(State(st): State<AppState>) -> Response {
    match tokio::task::spawn_blocking(move || read_all(&st)).await {
        Ok(Ok(v)) => Json(serde_json::json!({ "seats": v })).into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat list failed");
            internal()
        }
        Err(_) => internal(),
    }
}

pub async fn get_one(State(st): State<AppState>, UrlPath(id): UrlPath<String>) -> Response {
    // the whole list, so the seat-code suffix is the same as in the list
    let one = move || -> aib_store::Result<Option<serde_json::Value>> {
        Ok(read_all(&st)?
            .into_iter()
            .find(|v| v["seat_id"].as_str() == Some(id.as_str())))
    };
    match tokio::task::spawn_blocking(one).await {
        Ok(Ok(Some(v))) => Json(v).into_response(),
        Ok(Ok(None)) => ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat read failed");
            internal()
        }
        Err(_) => internal(),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawOpen {
    persona_uid: String,
    provider: String,
    mode: String,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    cabin: Option<String>,
    #[serde(default)]
    axis_id: Option<String>,
    /// Required (IFACE r5): the CLI always sends its current folder, and aibd has no
    /// working folder of its own to fall back on; a guessed one would start a provider
    /// in the wrong place once B4-1b-2 starts it.
    cwd: String,
    /// Only `false`: a new cabin is not opened by 2.0 yet (the IFACE §2 example has it).
    #[serde(default)]
    new: bool,
    #[serde(default)]
    dry_run: bool,
}

/// A refused open: `invalid_request` with a stable `reason` a screen picks its words by
/// (043dd B4-1b-1 M1; IFACE r5): `body`, `key_missing`, `provider`, `mode`,
/// `persona_shape`, `persona_missing`, `model`, `cabin`, `axis_id`, `cwd`, `new`.
#[derive(Debug)]
struct Refusal(&'static str, &'static str);

impl Refusal {
    fn into_response(self) -> Response {
        let body = serde_json::json!({
            "code": ErrorCode::InvalidRequest.as_str(),
            "message": self.1,
            "request_id": crate::guard::request_id(),
            "reason": self.0,
        });
        (StatusCode::BAD_REQUEST, Json(body)).into_response()
    }
}

/// `a-z 0-9 -`, 1–64, not starting with `-` (a cabin name, an axis id).
fn tag_ok(s: &str) -> bool {
    (1..=MAX_TAG).contains(&s.len())
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// The shape of a seat's cwd (519d0 IFACE r5): an absolute path for this platform, at
/// most `MAX_CWD` bytes, no control characters. On Windows that is `C:\…` or a UNC path
/// `\\server\share\…`; `C:x` (relative to that drive's folder) and `\x` (the current
/// drive's root) are not. Whether it is an existing folder is checked after.
fn cwd_shape(s: &str) -> bool {
    s.len() <= MAX_CWD && !s.chars().any(char::is_control) && std::path::Path::new(s).is_absolute()
}

/// The fields of an open request, checked in the order the seat-open words are
/// (aib-seatopen: AI, mode, persona, model, cabin), then axis, cwd and `new`. The
/// persona is only its uid here: names are resolved by the CLI (7b81e 11-14 §2-2).
fn check_open(r: &RawOpen) -> Result<(), Refusal> {
    if !aib_seatopen::PROVIDERS.contains(&r.provider.as_str()) {
        return Err(Refusal("provider", "provider is not one aib knows"));
    }
    if !aib_seatopen::MODES.contains(&r.mode.as_str()) {
        return Err(Refusal("mode", "mode must be dgrs or safe"));
    }
    if !crate::personas::uid_shape(&r.persona_uid) {
        return Err(Refusal(
            "persona_shape",
            "persona_uid must be a persona card's 6-character uid",
        ));
    }
    if r.model
        .as_deref()
        .is_some_and(|m| !aib_seatopen::model_shape(m))
    {
        return Err(Refusal("model", "model has the wrong shape"));
    }
    if r.cabin.as_deref().is_some_and(|c| !tag_ok(c)) {
        return Err(Refusal("cabin", "cabin must be 1-64 of a-z 0-9 -"));
    }
    if r.axis_id.as_deref().is_some_and(|a| !tag_ok(a)) {
        return Err(Refusal("axis_id", "axis_id must be 1-64 of a-z 0-9 -"));
    }
    if !cwd_shape(&r.cwd) || !std::path::Path::new(&r.cwd).is_dir() {
        return Err(Refusal(
            "cwd",
            "cwd must be an existing folder, written as an absolute path",
        ));
    }
    if r.new {
        return Err(Refusal("new", "2.0 does not open a new cabin yet"));
    }
    Ok(())
}

fn unavailable(e: &PersonaSourceError) -> Response {
    tracing::warn!(error = %e, "persona cards unreadable; seat open refused");
    ApiError::new(
        ErrorCode::PersonaSourceUnavailable,
        "the persona cards cannot be read",
    )
    .into_response()
}

fn replayed(status: u16, response: serde_json::Value) -> Response {
    let code = StatusCode::from_u16(status).unwrap_or(StatusCode::OK);
    let mut r = (code, Json(response)).into_response();
    r.headers_mut()
        .insert(REPLAYED_HEADER, HeaderValue::from_static("true"));
    r
}

fn key_conflict() -> Response {
    ApiError::new(
        ErrorCode::IdempotencyConflict,
        "this Idempotency-Key was used for a different request",
    )
    .into_response()
}

/// `POST /api/v1/seats/open` (B4-1b-1): a seat by persona. Writes the seat, its open
/// fields (seat_meta) and `seat.opened` in one transaction; starts nothing (B4-1b-2).
/// The `Idempotency-Key` is required: a retry must never open a second seat, and a
/// retry is answered before the cards are read (a card renamed or the file broken since
/// the first answer does not turn a replay into an error; 043dd L1). `dry_run` answers
/// the plan and writes nothing, not even an event.
pub async fn open(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    if !json_content_type(&headers) {
        return bad("Content-Type must be application/json").into_response();
    }
    let key = match idempotency_key(&headers) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    let Ok(bytes) = axum::body::to_bytes(body, MAX_BODY).await else {
        return ApiError::new(ErrorCode::PayloadTooLarge, "request body over 4 KiB")
            .into_response();
    };
    let req: RawOpen = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(_) => return Refusal(
            "body",
            "body must be {persona_uid, provider, mode, cwd, model?, cabin?, axis_id?, new?, dry_run?}",
        )
        .into_response(),
    };
    if let Err(e) = check_open(&req) {
        return e.into_response();
    }
    if key.is_none() && !req.dry_run {
        return Refusal("key_missing", "Idempotency-Key is required to open a seat")
            .into_response();
    }
    // the key is bound to the whole request, cwd included
    let idem = key.map(|k| IdemKey {
        scope: OPEN_SCOPE.into(),
        key: k,
        request: serde_json::json!({
            "persona_uid": req.persona_uid, "provider": req.provider, "mode": req.mode,
            "model": req.model, "cabin": req.cabin, "axis_id": req.axis_id, "cwd": req.cwd,
        })
        .to_string(),
    });
    if let Some(k) = idem.clone() {
        let store = st.store.clone();
        match tokio::task::spawn_blocking(move || store.idem_peek(&k.scope, &k.key)).await {
            Ok(Ok(Some((request, status, response)))) => {
                let same = idem.as_ref().is_some_and(|k| k.request == request);
                return if same {
                    replayed(status, response)
                } else {
                    key_conflict()
                };
            }
            Ok(Ok(None)) => {}
            Ok(Err(e)) => {
                tracing::error!(error = %e, "idempotency key read failed");
                return internal();
            }
            Err(_) => return internal(),
        }
    }
    let personas = st.personas.clone();
    let cards = match tokio::task::spawn_blocking(move || personas.cards()).await {
        Ok(Ok(c)) => c,
        Ok(Err(e)) => return unavailable(&e),
        Err(_) => return internal(),
    };
    let Some(card) = cards.into_iter().find(|c| c.uid == req.persona_uid) else {
        return Refusal(
            "persona_missing",
            "persona_uid: no persona card has this uid",
        )
        .into_response();
    };
    let plan = serde_json::json!({
        "persona": {"uid": card.uid, "name": card.name, "persona_sha": card.persona_sha},
        "provider": req.provider,
        "mode": req.mode,
        "model": req.model,
        "cabin": req.cabin,
        "axis_id": req.axis_id,
    });
    if req.dry_run {
        return Json(serde_json::json!({ "plan": plan })).into_response();
    }
    let (Ok(seat_id), Ok(event_id)) = (new_id("seat_"), new_id("ev_")) else {
        return internal();
    };
    let now = now_rfc3339();
    let seat = Seat {
        seat_id: seat_id.clone(),
        name: card.name.clone(),
        state: state_str(SeatState::Created),
        revision: 1,
        created_at: now.clone(),
        updated_at: now.clone(),
    };
    let meta = SeatMeta {
        seat_id: seat_id.clone(),
        provider: req.provider.clone(),
        role: None,
        cwd: req.cwd.clone(),
        model: req.model.clone(),
        model_pinned: req.model.is_some(),
        resume_id: None,
        project_uid: None,
        persona_uid: Some(card.uid.clone()),
        brain_route: None,
        updated_at: now.clone(),
        mode: Some(req.mode.clone()),
        axis_id: req.axis_id.clone(),
        cabin: req.cabin.clone(),
    };
    // what was opened (IFACE §2: the persona by uid and card version): never where (no
    // cwd in the journal) and never the name, which is read when shown (043dd M4)
    let payload = serde_json::json!({
        "seat_id": seat_id,
        "persona_uid": card.uid,
        "persona_sha": card.persona_sha,
        "provider": req.provider,
        "mode": req.mode,
        "model": req.model,
        "cabin": req.cabin,
        "axis_id": req.axis_id,
    });
    let event = NewEvent {
        event_id,
        aggregate_type: "seat".into(),
        aggregate_id: seat_id,
        sequence: 1,
        event_type: "seat.opened".into(),
        correlation_id: None,
        occurred_at: now,
        payload,
    };
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || {
        // the answer shows the seat as the list will: the seat code when an older seat
        // has this persona (043dd M3). Two opens of one persona at the same instant may
        // both answer without it; the list is the one that counts.
        let dup = store
            .seat_metas()?
            .iter()
            .any(|m| m.persona_uid.as_deref() == Some(card.uid.as_str()));
        let response = serde_json::json!({
            "seat": view_with(&seat, Some(&meta), Some(&card), Some(dup)),
            "session": null,
        });
        store
            .create_seat_with_meta(
                seat,
                meta,
                event,
                idem,
                StatusCode::CREATED.as_u16(),
                response.clone(),
            )
            .map(|r| (r, response))
    })
    .await;
    match res {
        Ok(Ok((SeatCreate::Created(_), response))) => {
            (StatusCode::CREATED, Json(response)).into_response()
        }
        Ok(Ok((SeatCreate::Replayed { status, response }, _))) => replayed(status, response),
        Ok(Ok((SeatCreate::KeyConflict, _))) => key_conflict(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat open failed");
            internal()
        }
        Err(_) => internal(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 519d0 IFACE r5: cwd is absolute on both platforms (Windows drive letter and UNC).
    #[test]
    fn a_cwd_is_an_absolute_path_on_this_platform() {
        assert!(!cwd_shape("rel/dir"));
        assert!(!cwd_shape(""));
        assert!(!cwd_shape("/tmp/a\nb"), "no control characters");
        #[cfg(unix)]
        {
            assert!(cwd_shape("/home/u/work"));
            assert!(
                !cwd_shape(r"C:\Users\u"),
                "a drive path is relative on unix"
            );
        }
        #[cfg(windows)]
        {
            assert!(cwd_shape(r"C:\Users\u\work"), "drive letter");
            assert!(cwd_shape(r"\\server\share\work"), "UNC");
            assert!(!cwd_shape(r"C:work"), "relative to the drive's folder");
            assert!(!cwd_shape(r"\work"), "the current drive's root");
        }
        let long = format!("{}{}", std::env::temp_dir().display(), "a".repeat(MAX_CWD));
        assert!(!cwd_shape(&long), "over MAX_CWD");
    }

    fn raw(cwd: &str) -> RawOpen {
        RawOpen {
            persona_uid: "mnnftg".into(),
            provider: "claude".into(),
            mode: "dgrs".into(),
            model: None,
            cabin: None,
            axis_id: None,
            cwd: cwd.into(),
            new: false,
            dry_run: false,
        }
    }

    /// 519d0 IFACE r5: a cwd that is missing or is a file is refused with reason `cwd`.
    #[test]
    #[allow(clippy::unwrap_used)] // test code: a panic is the failure report
    fn a_cwd_must_be_an_existing_folder() {
        let d = tempfile::tempdir().unwrap();
        let file = d.path().join("a-file");
        std::fs::write(&file, "x").unwrap();
        let reason = |p: &std::path::Path| check_open(&raw(p.to_str().unwrap())).err().map(|e| e.0);
        assert_eq!(reason(d.path()), None);
        assert_eq!(reason(&d.path().join("missing")), Some("cwd"));
        assert_eq!(reason(&file), Some("cwd"));
    }

    fn h(vals: &[&str]) -> HeaderMap {
        let mut m = HeaderMap::new();
        for v in vals {
            m.append(
                IDEMPOTENCY_HEADER,
                HeaderValue::from_str(v).unwrap_or(HeaderValue::from_static("x")),
            );
        }
        m
    }

    #[test]
    fn names() {
        assert!(valid_name("dev"));
        assert!(valid_name("主線核心架構師"));
        assert!(valid_name(&"字".repeat(MAX_NAME)));
        assert!(!valid_name(&"字".repeat(MAX_NAME + 1)));
        assert!(!valid_name(""));
        assert!(!valid_name("   "));
        assert!(!valid_name("a\nb"));
        assert!(!valid_name("a\u{7}"));
    }

    #[test]
    fn keys() {
        assert_eq!(idempotency_key(&HeaderMap::new()).ok(), Some(None));
        assert_eq!(
            idempotency_key(&h(&["abc-1_2.3:4"])).ok(),
            Some(Some("abc-1_2.3:4".into()))
        );
        assert!(idempotency_key(&h(&["a", "b"])).is_err(), "twice");
        assert!(idempotency_key(&h(&["a b"])).is_err(), "space");
        assert!(idempotency_key(&h(&["a/b"])).is_err(), "slash");
        assert!(
            idempotency_key(&h(&[&"k".repeat(MAX_KEY + 1)])).is_err(),
            "long"
        );
        assert!(idempotency_key(&h(&[&"k".repeat(MAX_KEY)])).is_ok());
    }

    #[test]
    fn a_new_seat_is_created() {
        assert_eq!(state_str(SeatState::Created), "CREATED");
    }
}
