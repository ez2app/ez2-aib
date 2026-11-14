//! `/api/v1/seats` (spec 8): create, list and read seats. A seat is a long-lived
//! logical slot, separate from any OS process (spec 4.1); it lives in SQLite,
//! so it is read back after a restart (Phase 1 gate).
//!
//! Writes take an optional `Idempotency-Key` header (spec 8). The same key
//! with the same body on the same route replays the first answer (with
//! `Idempotent-Replayed: true`); with a different body it is
//! `409 idempotency_conflict`. Nothing is written in either case.

use crate::api::AppState;
use crate::sessions::{json_content_type, new_id, now_rfc3339, ApiError};
use aib_core::state::SeatState;
use aib_core::ErrorCode;
use aib_store::{IdemKey, NewEvent, Seat, SeatCreate};
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

/// The API form of a seat.
pub fn view(s: &Seat) -> serde_json::Value {
    serde_json::json!({
        "seat_id": s.seat_id,
        "name": s.name,
        "state": s.state,
        "revision": s.revision,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    })
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

pub async fn list(State(st): State<AppState>) -> Response {
    let store = st.store.clone();
    match tokio::task::spawn_blocking(move || store.seats()).await {
        Ok(Ok(v)) => Json(serde_json::json!({"seats": v.iter().map(view).collect::<Vec<_>>()}))
            .into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat list failed");
            internal()
        }
        Err(_) => internal(),
    }
}

pub async fn get_one(State(st): State<AppState>, UrlPath(id): UrlPath<String>) -> Response {
    let store = st.store.clone();
    match tokio::task::spawn_blocking(move || store.seat(&id)).await {
        Ok(Ok(Some(s))) => Json(view(&s)).into_response(),
        Ok(Ok(None)) => ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat read failed");
            internal()
        }
        Err(_) => internal(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
