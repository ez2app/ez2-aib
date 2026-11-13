//! `Idempotency-Key` on the session writes (ADR 0003: "an `Idempotency-Key` header on
//! every write"; T81): `POST /api/v1/sessions`, `POST /api/v1/seats/{id}/sessions` and
//! `DELETE /api/v1/sessions/{id}`. Seats keep theirs in [`crate::seats`] (one
//! transaction); a session write starts or stops processes first, so the key is claimed
//! before and finished after ([`aib_store::IdemClaim`]):
//!
//! - same key, same request, answered before ⇒ that status again with the session **as
//!   it is now** and `Idempotent-Replayed: true`; nothing is started or stopped;
//! - same key, another request ⇒ `409 idempotency_conflict`;
//! - same key while the first request still runs, or after aibd stopped in the middle of
//!   it ⇒ `409 idempotency_conflict` that says so: the key never starts a second session;
//! - an answer other than 2xx gives the key back (nothing was created), so a retry may
//!   try again.
//!
//! Spec 9.4 keeps argv (beyond `argv[0]`) and env out of the store, so the key is bound
//! to a SHA-256 of the canonical request and the stored answer is only the session id.
//!
//! Not covered: the pairing writes. Their answers carry one-time codes and credentials,
//! which must not be kept for a replay.

use crate::api::AppState;
use crate::seats::{idempotency_key, REPLAYED_HEADER};
use crate::sessions::{now_rfc3339, ApiError};
use aib_core::ErrorCode;
use aib_store::{IdemClaim, IdemKey};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use sha2::{Digest, Sha256};

/// `sha256:<hex>` of a canonical request (a `serde_json::Value` prints its object keys
/// sorted, so field order in the body does not matter).
pub fn request_digest(canonical: &serde_json::Value) -> String {
    let d = Sha256::digest(canonical.to_string().as_bytes());
    let hex: String = d.iter().map(|b| format!("{b:02x}")).collect();
    format!("sha256:{hex}")
}

/// The key of this write, if the request has one (`scope` = method + concrete path).
pub fn key_of(
    headers: &HeaderMap,
    scope: String,
    canonical: &serde_json::Value,
) -> Result<Option<IdemKey>, ApiError> {
    Ok(idempotency_key(headers)?.map(|key| IdemKey {
        scope,
        key,
        request: request_digest(canonical),
    }))
}

fn conflict(msg: &str) -> Response {
    ApiError::new(ErrorCode::IdempotencyConflict, msg).into_response()
}

/// Claim `key` before the work. `None` = do the work; `Some(answer)` = answer this and
/// do nothing.
pub async fn begin(st: &AppState, key: &Option<IdemKey>) -> Option<Response> {
    let Some(k) = key.clone() else {
        return None;
    };
    let store = st.store.clone();
    let claim = tokio::task::spawn_blocking(move || store.idem_claim(&k, &now_rfc3339())).await;
    match claim {
        Ok(Ok(IdemClaim::Fresh)) => None,
        Ok(Ok(IdemClaim::Replay { status, response })) => Some(replay(st, status, &response)),
        Ok(Ok(IdemClaim::Conflict)) => Some(conflict(
            "this Idempotency-Key was used for a different request",
        )),
        Ok(Ok(IdemClaim::InProgress)) => Some(conflict(
            "a request with this Idempotency-Key is still running, or aibd stopped in the \
             middle of it: list the sessions, then use a new key",
        )),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "idempotency claim failed");
            Some(ApiError::new(ErrorCode::Internal, "internal error").into_response())
        }
        Err(_) => Some(ApiError::new(ErrorCode::Internal, "internal error").into_response()),
    }
}

/// After the work: a 2xx keeps the key with the session id, anything else gives it back.
pub async fn end(
    st: &AppState,
    key: Option<IdemKey>,
    status: StatusCode,
    session_id: Option<&str>,
) {
    let Some(k) = key else {
        return;
    };
    let store = st.store.clone();
    let id = session_id.map(str::to_string);
    let res = tokio::task::spawn_blocking(move || match id {
        Some(id) if status.is_success() => store
            .idem_finish(&k, status.as_u16(), &serde_json::json!({ "id": id }))
            .map(|_| ()),
        _ => store.idem_release(&k).map(|_| ()),
    })
    .await;
    if !matches!(res, Ok(Ok(()))) {
        // the claim stays in progress: a retry with this key gets 409, never a second start
        tracing::error!("idempotency key could not be finished");
    }
}

/// A replayed answer: the first status, the session as it is now.
fn replay(st: &AppState, status: u16, stored: &serde_json::Value) -> Response {
    let code = StatusCode::from_u16(status).unwrap_or(StatusCode::OK);
    let body = match stored["id"].as_str() {
        Some(id) => match st.sessions.get(id) {
            Some(s) => serde_json::to_value(s).unwrap_or_else(|_| stored.clone()),
            None => stored.clone(),
        },
        None => stored.clone(),
    };
    let mut r = (code, Json(body)).into_response();
    r.headers_mut()
        .insert(REPLAYED_HEADER, HeaderValue::from_static("true"));
    r
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_digest_ignores_field_order_and_changes_with_any_value() {
        let a = request_digest(&json!({"argv": ["/x"], "cwd": "/", "env": [["K", "v"]]}));
        let b = request_digest(&json!({"env": [["K", "v"]], "cwd": "/", "argv": ["/x"]}));
        let c = request_digest(&json!({"argv": ["/x"], "cwd": "/", "env": [["K", "w"]]}));
        assert_eq!(a, b);
        assert_ne!(a, c, "a different env value is a different request");
        assert!(a.starts_with("sha256:") && a.len() == 7 + 64);
        assert!(!a.contains('v'), "no value of the request in the key");
    }

    #[test]
    fn no_header_is_no_key_and_a_bad_header_is_an_error() {
        let canon = json!({});
        assert_eq!(
            key_of(&HeaderMap::new(), "S".into(), &canon).ok(),
            Some(None)
        );
        let mut h = HeaderMap::new();
        h.insert("idempotency-key", HeaderValue::from_static("a b"));
        assert!(key_of(&h, "S".into(), &canon).is_err());
        let mut h = HeaderMap::new();
        h.insert("idempotency-key", HeaderValue::from_static("retry-1"));
        let k = key_of(&h, "POST /api/v1/sessions".into(), &canon)
            .ok()
            .flatten();
        assert_eq!(
            k.map(|k| (k.scope, k.key)),
            Some(("POST /api/v1/sessions".into(), "retry-1".into()))
        );
    }
}
