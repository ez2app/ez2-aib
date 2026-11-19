//! `/api/v1/pair/*` handlers (ADR 0006 §7; state and rules in [`crate::pairing`]).
//!
//! - `POST /pair/codes`, `GET /pair/codes/{id}`, `POST /pair/codes/{id}/confirm`,
//!   `DELETE /pair/codes/{id}`: start-up token only, used by `aib web`. The guard is the
//!   one gate (it answers 401 to a page credential), like every other route.
//! - `POST /pair/redeem`: public, form body with the single field `code`; on success the
//!   exchange page (the only response that carries a page credential).
//! - `GET /pair/status`, `POST /pair/revoke`: the page's own credential, allowed before
//!   confirmation.

use crate::guard::{error_response, request_id, Caller};
use crate::pairing::{ConfirmChannel, PairError, PairView, Pairing};
use aib_core::ErrorCode;
use axum::body::{Body, Bytes};
use axum::extract::{Extension, Path};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use base64::Engine;
use sha2::{Digest, Sha256};
use std::sync::Arc;

/// Largest redeem form body (ADR 0006 §7).
pub const REDEEM_MAX_BODY: usize = 1024;
/// Largest confirm JSON body.
const CONFIRM_MAX_BODY: usize = 256;
/// Every redeem failure says the same thing.
pub const REDEEM_FAILED: &str = "pairing failed";
/// Where the exchange page keeps the credential (this tab only).
pub const STORAGE_KEY: &str = "aib.page_credential";

fn no_store(mut r: Response) -> Response {
    r.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    r
}

fn json(status: StatusCode, v: serde_json::Value) -> Response {
    no_store(
        (
            status,
            [(header::CONTENT_TYPE, "application/json")],
            v.to_string(),
        )
            .into_response(),
    )
}

fn view_json(v: &PairView, channel: ConfirmChannel) -> serde_json::Value {
    let mut j = serde_json::json!({"state": v.state.as_str(), "expires_in": v.expires_in});
    // a20: the page says how it was confirmed; terminal_y is never shown as the person
    if let Some(m) = channel.method() {
        j["channel"] = serde_json::Value::String(channel.as_str().into());
        j["method"] = serde_json::Value::String(m.into());
        j["verified_person"] = serde_json::Value::Bool(false);
    }
    if let Some(s) = &v.short_code {
        j["short_code"] = serde_json::Value::String(s.clone());
    }
    if v.user_page {
        j["user_page"] = serde_json::Value::Bool(true);
    }
    if v.carried {
        j["carried"] = serde_json::Value::Bool(true);
    }
    j
}

fn pair_error(e: PairError, msg: &'static str) -> Response {
    let code = match e {
        PairError::Failed => ErrorCode::PairingFailed,
        PairError::Reused => ErrorCode::PairingCodeReused,
        PairError::RateLimited | PairError::Full => ErrorCode::PairingRateLimited,
        PairError::NotFound | PairError::Off => ErrorCode::NotFound,
    };
    no_store(error_response(code, msg, &request_id()))
}

/// The body, or `None` when it is larger than `max` (answer with [`too_large`]).
async fn read_limited(body: Body, max: usize) -> Option<Bytes> {
    axum::body::to_bytes(body, max).await.ok()
}

fn too_large() -> Response {
    error_response(
        ErrorCode::PayloadTooLarge,
        "request body too large",
        &request_id(),
    )
}

/// `POST /api/v1/pair/codes`
pub async fn create_code(Extension(p): Extension<Arc<Pairing>>) -> Response {
    match p.create_code() {
        Ok(n) => json(
            StatusCode::CREATED,
            serde_json::json!({"pair_id": n.pair_id, "code": n.code, "expires_in": n.expires_in}),
        ),
        Err(PairError::Off) => pair_error(PairError::Off, "pairing is off (AIBD_PAIRING)"),
        Err(e) => pair_error(e, "too many pairings; try again later"),
    }
}

/// `GET /api/v1/pair/codes/{pair_id}`
pub async fn get_code(Extension(p): Extension<Arc<Pairing>>, Path(id): Path<String>) -> Response {
    match p.view(&id) {
        Some(v) => {
            let mut j = view_json(&v, p.channel());
            j["pair_id"] = serde_json::Value::String(id);
            json(StatusCode::OK, j)
        }
        None => pair_error(PairError::NotFound, "no such pairing"),
    }
}

/// `POST /api/v1/pair/codes/{pair_id}/confirm` with `{"short_code": "nnnnnn"}`
pub async fn confirm(
    Extension(p): Extension<Arc<Pairing>>,
    Path(id): Path<String>,
    body: Body,
) -> Response {
    let Some(bytes) = read_limited(body, CONFIRM_MAX_BODY).await else {
        return too_large();
    };
    let short = serde_json::from_slice::<serde_json::Value>(&bytes)
        .ok()
        .and_then(|v| v.get("short_code")?.as_str().map(str::to_string));
    let Some(short) = short else {
        return error_response(
            ErrorCode::InvalidRequest,
            "body must be {\"short_code\": \"...\"}",
            &request_id(),
        );
    };
    match p.confirm(&id, &short) {
        Ok(()) => json(StatusCode::OK, confirmed_body(p.channel())),
        Err(PairError::NotFound) => pair_error(PairError::NotFound, "no such pairing"),
        Err(e) => pair_error(e, "not confirmed; pair again"),
    }
}

/// The answer to a confirmation, and its record (a20 L1, 0eed7 REVIEW-pweak): a weak
/// (terminal_y) credential is logged as such, never with the credential or the short code.
fn confirmed_body(channel: ConfirmChannel) -> serde_json::Value {
    let mut j = serde_json::json!({"state": "confirmed"});
    if let Some(m) = channel.method() {
        tracing::warn!(
            method = m,
            verified_person = false,
            "pairing confirmed: WEAK, someone on this account answered y; not the person proven present"
        );
        j["method"] = m.into();
        j["verified_person"] = false.into();
    }
    j
}

/// `DELETE /api/v1/pair/codes/{pair_id}`: the person said no or closed the prompt.
pub async fn cancel(Extension(p): Extension<Arc<Pairing>>, Path(id): Path<String>) -> Response {
    if p.cancel(&id) {
        no_store(StatusCode::NO_CONTENT.into_response())
    } else {
        pair_error(PairError::NotFound, "no such pairing")
    }
}

fn is_form(h: &HeaderMap) -> bool {
    let mut it = h.get_all(header::CONTENT_TYPE).iter();
    match (it.next(), it.next()) {
        (Some(v), None) => v.to_str().is_ok_and(|s| {
            s.split(';').next().is_some_and(|m| {
                m.trim()
                    .eq_ignore_ascii_case("application/x-www-form-urlencoded")
            })
        }),
        _ => false,
    }
}

/// The single `code` field, or `None` (any other shape is a failure).
fn code_field(body: &[u8]) -> Option<String> {
    let s = std::str::from_utf8(body).ok()?;
    let (k, v) = s.split_once('=')?;
    if s.contains('&') || crate::guard::form_decode(k) != "code" {
        return None;
    }
    Some(crate::guard::form_decode(v))
}

/// The redeem form: `code=…`, or `code=…&to=user` from a user folder page that pairs for
/// itself (S107-userhtml option 2). `(code, to_user)`; any other shape is `None`.
fn redeem_form(body: &[u8]) -> Option<(String, bool)> {
    let s = std::str::from_utf8(body).ok()?;
    match s.split_once('&') {
        None => code_field(body).map(|c| (c, false)),
        Some((code, to)) => {
            let to = to.split_once('=')?;
            if to.0 != "to" || to.1 != "user" || to.1.contains('&') {
                return None;
            }
            code_field(code.as_bytes()).map(|c| (c, true))
        }
    }
}

/// Where the exchange page goes (519d0 11-18 03:5x, option 2): the built-in page under its own
/// prefix, which no user folder file can shadow, unless the user folder page asked for itself.
pub const BUILTIN_HOME: &str = "/__builtin/";

/// The exchange page: stores the credential in this tab's `sessionStorage` and leaves
/// history with `location.replace`, so a reload does not re-send the form. It goes to the
/// built-in page ([`BUILTIN_HOME`]) unless `to_user` (a user folder page paired for itself).
pub fn exchange_page(credential: &str, to_user: bool) -> Response {
    let home = if to_user { "/" } else { BUILTIN_HOME };
    // the credential is base64url: safe inside a JS string literal
    let script = format!(
        "try{{sessionStorage.setItem('{STORAGE_KEY}','{credential}')}}catch(e){{}}location.replace('{home}');"
    );
    let hash = base64::engine::general_purpose::STANDARD.encode(Sha256::digest(script.as_bytes()));
    let csp = format!(
        "default-src 'none'; script-src 'sha256-{hash}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    );
    let html = format!(
        "<!doctype html><meta charset=\"utf-8\"><meta name=\"referrer\" content=\"no-referrer\"><title>aib</title><script>{script}</script>"
    );
    let mut r = (StatusCode::OK, html).into_response();
    let h = r.headers_mut();
    h.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/html; charset=utf-8"),
    );
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    h.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("no-referrer"),
    );
    h.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    h.insert(header::X_FRAME_OPTIONS, HeaderValue::from_static("DENY"));
    if let Ok(v) = HeaderValue::from_str(&csp) {
        h.insert(header::CONTENT_SECURITY_POLICY, v);
    }
    r
}

/// `POST /api/v1/pair/redeem`
pub async fn redeem(
    Extension(p): Extension<Arc<Pairing>>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    if !is_form(&headers) {
        return no_store(error_response(
            ErrorCode::InvalidRequest,
            REDEEM_FAILED,
            &request_id(),
        ));
    }
    let Some(bytes) = read_limited(body, REDEEM_MAX_BODY).await else {
        return no_store(too_large());
    };
    // a user folder page pairs for itself with to=user; its credential is marked (S107)
    let (code, to_user) = redeem_form(&bytes).unwrap_or_default();
    match p.redeem_marked(&code, to_user) {
        Ok(r) => exchange_page(&r.credential, to_user),
        Err(PairError::Full) => pair_error(PairError::Full, "too many paired pages; revoke one"),
        Err(e) => pair_error(e, REDEEM_FAILED),
    }
}

/// `GET /api/v1/pair/status`: the page's own credential.
pub async fn status(
    Extension(c): Extension<Caller>,
    Extension(p): Extension<Arc<Pairing>>,
) -> Response {
    let Caller::Page(a) = c else {
        return error_response(
            ErrorCode::InvalidRequest,
            "send the page credential",
            &request_id(),
        );
    };
    match p.status(&a.digest) {
        Some(v) => json(StatusCode::OK, view_json(&v, p.channel())),
        None => error_response(ErrorCode::Unauthorized, "wrong token", &request_id()),
    }
}

/// `POST /api/v1/pair/revoke`: the page's own credential.
pub async fn revoke(
    Extension(c): Extension<Caller>,
    Extension(p): Extension<Arc<Pairing>>,
) -> Response {
    let Caller::Page(a) = c else {
        return error_response(
            ErrorCode::InvalidRequest,
            "send the page credential",
            &request_id(),
        );
    };
    p.revoke_credential(&a.digest);
    no_store(StatusCode::NO_CONTENT.into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn code_field_takes_exactly_one_code() {
        assert_eq!(
            code_field(b"code=ABCDE-FGHJK").as_deref(),
            Some("ABCDE-FGHJK")
        );
        assert_eq!(
            code_field(b"code=ABCDE%2DFGHJK").as_deref(),
            Some("ABCDE-FGHJK")
        );
        assert_eq!(code_field(b"code=a&code=b"), None);
        assert_eq!(code_field(b"x=1"), None);
        assert_eq!(code_field(b"code"), None);
        assert_eq!(code_field(b"\xff=1"), None);
    }

    #[test]
    fn form_content_type_only() {
        let mut h = HeaderMap::new();
        assert!(!is_form(&h));
        h.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        assert!(!is_form(&h));
        h.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("Application/X-WWW-Form-Urlencoded; charset=UTF-8"),
        );
        assert!(is_form(&h));
        h.append(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/x-www-form-urlencoded"),
        );
        assert!(!is_form(&h));
    }

    #[test]
    fn exchange_page_headers_and_csp_hash_match_the_script() {
        let r = exchange_page("abc_DEF-123", false);
        let h = r.headers();
        assert_eq!(h[header::CACHE_CONTROL], "no-store");
        assert_eq!(h[header::REFERRER_POLICY], "no-referrer");
        let csp = h[header::CONTENT_SECURITY_POLICY]
            .to_str()
            .unwrap()
            .to_string();
        assert!(csp.contains("frame-ancestors 'none'"));
        assert!(csp.contains("default-src 'none'"));
        let script = format!(
            "try{{sessionStorage.setItem('{STORAGE_KEY}','abc_DEF-123')}}catch(e){{}}location.replace('/__builtin/');"
        );
        let want =
            base64::engine::general_purpose::STANDARD.encode(Sha256::digest(script.as_bytes()));
        assert!(csp.contains(&format!("'sha256-{want}'")));
    }

    /// S107-userhtml option 2: the credential goes to the built-in page under a prefix the user
    /// folder cannot shadow; only a page that asked with to=user gets it at `/`.
    #[test]
    fn the_credential_goes_to_the_built_in_page_unless_the_user_page_asked() {
        assert_eq!(
            redeem_form(b"code=ABCDE-FGHJK"),
            Some(("ABCDE-FGHJK".into(), false))
        );
        assert_eq!(
            redeem_form(b"code=ABCDE-FGHJK&to=user"),
            Some(("ABCDE-FGHJK".into(), true))
        );
        for bad in [
            &b"code=A&to=other"[..],
            b"code=A&to=user&x=1",
            b"to=user&code=A",
            b"code=A&code=B",
        ] {
            assert_eq!(redeem_form(bad), None, "{bad:?}");
        }
        let body = |r: Response| {
            let rt = tokio::runtime::Builder::new_current_thread()
                .build()
                .unwrap();
            let b = rt
                .block_on(axum::body::to_bytes(r.into_body(), usize::MAX))
                .unwrap();
            String::from_utf8(b.to_vec()).unwrap()
        };
        assert!(body(exchange_page("c", false)).contains("location.replace('/__builtin/')"));
        assert!(body(exchange_page("c", true)).contains("location.replace('/')"));
    }
}
