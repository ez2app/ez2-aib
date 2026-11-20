//! `/api/v1/pair/*` handlers (ADR 0006 §7; state and rules in [`crate::pairing`]).
//!
//! - `POST /pair/codes`, `GET /pair/codes/{id}`, `POST /pair/codes/{id}/confirm`,
//!   `DELETE /pair/codes/{id}`: start-up token only, used by `aib web`. The guard is the
//!   one gate (it answers 401 to a page credential), like every other route.
//! - `POST /pair/redeem`: public, form body with the single field `code`; on success the
//!   exchange page (the only response that carries a page credential).
//! - `GET /pair/status`, `POST /pair/revoke`: the page's own credential, allowed before
//!   confirmation.
//! - `GET /pair/prefs`: public; the code page's box default (SPEC §19b-2), `<data>/pair-prefs.json`.

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
        // answered by the redeem handler itself; never an error page
        PairError::Already => ErrorCode::PairingCodeReused,
        PairError::Expired => ErrorCode::PairingCodeExpired,
    };
    no_store(error_response(code, msg, &request_id()))
}

/// What a pairing step's answer means, for the event journal (no code, no credential).
fn outcome(s: StatusCode) -> &'static str {
    match s.as_u16() {
        200..=299 => "ok",
        401 => "wrong_code",
        404 => "not_found",
        409 => "reused",
        410 => "expired",
        413 => "too_large",
        429 => "rate_limited",
        400 => "bad_request",
        _ => "error",
    }
}

/// SPEC §19d: the outcome also goes to aibd's event journal (the tab may close), before the
/// answer leaves.
async fn journaled(
    p: &Arc<Pairing>,
    kind: &'static str,
    id: Option<String>,
    r: Response,
) -> Response {
    let (p, out) = (p.clone(), outcome(r.status()));
    let payload = serde_json::json!({"outcome": out, "status": r.status().as_u16(), "pair_id": id});
    let _ = tokio::task::spawn_blocking(move || p.journal(kind, payload)).await;
    r
}

/// `POST /api/v1/pair/redeem` (the code page): see the answer's own notes below.
pub async fn redeem(
    Extension(p): Extension<Arc<Pairing>>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let r = redeem_answer(Extension(p.clone()), headers, body).await;
    journaled(&p, "pair.redeem", None, r).await
}

/// `POST /api/v1/pair/preview`, form `pass=<43 base64url>` (Andy 11-19 08:5x: a preview opens
/// already paired): only a preview answers it; a live pass from the parent buys a confirmed
/// credential for the built-in page, as the exchange page hands one over.
pub async fn preview_pass(
    Extension(p): Extension<Arc<Pairing>>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let r = preview_pass_answer(&p, &headers, body).await;
    journaled(&p, "pair.preview_pass", None, r).await
}

async fn preview_pass_answer(p: &Arc<Pairing>, headers: &HeaderMap, body: Body) -> Response {
    if !crate::preview::is_preview() {
        return pair_error(PairError::NotFound, "only a preview takes a pass");
    }
    if !is_form(headers) {
        return no_store(error_response(
            ErrorCode::InvalidRequest,
            REDEEM_FAILED,
            &request_id(),
        ));
    }
    let Some(bytes) = read_limited(body, REDEEM_MAX_BODY).await else {
        return no_store(too_large());
    };
    let Some(pass) = pass_form(&bytes) else {
        return no_store(error_response(
            ErrorCode::InvalidRequest,
            REDEEM_FAILED,
            &request_id(),
        ));
    };
    let (p, pass) = (p.clone(), pass.to_owned());
    match tokio::task::spawn_blocking(move || p.admit_pass(&pass)).await {
        Ok(Ok(credential)) => exchange_page(&credential, false),
        Ok(Err(e)) => pair_error(e, "the preview pass is not good (used, late or wrong)"),
        Err(_) => pair_error(PairError::Failed, REDEEM_FAILED),
    }
}

/// The pass form: exactly `pass=` and 43 base64url characters (a 32-byte pass), nothing else.
pub fn pass_form(body: &[u8]) -> Option<&str> {
    let v = std::str::from_utf8(body).ok()?.strip_prefix("pass=")?;
    (v.len() == 43
        && v.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
    .then_some(v)
}

/// `POST /api/v1/pair/codes/{id}/confirm` (the terminal's y).
pub async fn confirm(
    Extension(p): Extension<Arc<Pairing>>,
    Path(id): Path<String>,
    body: Body,
) -> Response {
    let r = confirm_answer(Extension(p.clone()), Path(id.clone()), body).await;
    journaled(&p, "pair.confirm", Some(id), r).await
}

/// `POST /api/v1/pair/next` (§19f: the page asks for the code that replaced its own).
pub async fn next(
    Extension(p): Extension<Arc<Pairing>>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let r = next_answer(Extension(p.clone()), headers, body).await;
    journaled(&p, "pair.next", None, r).await
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

/// Largest `POST /pair/codes` body (`{"terminal": true}`).
const CODES_MAX_BODY: usize = 128;

/// `POST /api/v1/pair/codes`; an empty body, or `{"terminal": true|false}` (SPEC §19b-3:
/// `aibd ctl web` pairs its terminal in the same step).
pub async fn create_code(Extension(p): Extension<Arc<Pairing>>, body: Body) -> Response {
    let Some(bytes) = read_limited(body, CODES_MAX_BODY).await else {
        return no_store(too_large());
    };
    let Some((terminal, replaces)) = codes_body(&bytes) else {
        return no_store(error_response(
            ErrorCode::InvalidRequest,
            "body is empty or {\"terminal\": true|false} (and \"replaces\": \"<pair id>\" with terminal true)",
            &request_id(),
        ));
    };
    match p.create_code_next(terminal, replaces.as_deref()) {
        Ok(n) => json(StatusCode::CREATED, code_json(&n)),
        Err(PairError::NotFound) => pair_error(
            PairError::NotFound,
            "no expired code of this terminal to replace",
        ),
        Err(PairError::Off) => pair_error(PairError::Off, "pairing is off (AIBD_PAIRING)"),
        Err(e) => pair_error(e, "too many pairings; try again later"),
    }
}

/// The `POST pair/codes` body: empty, `{}`, `{"terminal": bool}`, or SPEC §19f
/// `{"terminal": true, "replaces": "<16 hex>"}`. `(terminal, replaces)`; any other shape `None`.
pub fn codes_body(bytes: &[u8]) -> Option<(bool, Option<String>)> {
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Some((false, None));
    }
    let serde_json::Value::Object(m) = serde_json::from_slice::<serde_json::Value>(bytes).ok()?
    else {
        return None;
    };
    if m.keys().any(|k| k != "terminal" && k != "replaces") {
        return None;
    }
    let terminal = match m.get("terminal") {
        None => false,
        Some(serde_json::Value::Bool(b)) => *b,
        Some(_) => return None,
    };
    let replaces = match m.get("replaces") {
        None => None,
        Some(serde_json::Value::String(s))
            if terminal && s.len() == 16 && s.bytes().all(|b| b.is_ascii_hexdigit()) =>
        {
            Some(s.clone())
        }
        Some(_) => return None,
    };
    Some((terminal, replaces))
}

/// A new code as JSON: id, code, seconds left and when it runs out (SPEC §19f).
fn code_json(n: &crate::pairing::NewCode) -> serde_json::Value {
    serde_json::json!({
        "pair_id": n.pair_id,
        "code": n.code,
        "expires_in": n.expires_in,
        "expires_at_ms": n.expires_at_ms,
    })
}

/// `POST /api/v1/pair/next` (SPEC §19f), form `code=…`: the page shows the code it has. 200
/// `{code, expires_in, expires_at_ms}`: that code while it lives, or the one `aibd ctl web`
/// put in its place after it ran out; 204: ran out, none yet. A code nobody made: 401, counted
/// as in redeem.
async fn next_answer(
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
    let code = code_field(&bytes).unwrap_or_default();
    match p.next_code(&code) {
        Ok(Some(n)) => no_store(json(
            StatusCode::OK,
            serde_json::json!({"code": n.code, "expires_in": n.expires_in, "expires_at_ms": n.expires_at_ms}),
        )),
        Ok(None) => no_store(StatusCode::NO_CONTENT.into_response()),
        // (not `e`: knives-pairing P10 anchors on redeem's own line)
        Err(why) => pair_error(why, REDEEM_FAILED),
    }
}

/// `GET /api/v1/pair/codes/{pair_id}`
pub async fn get_code(Extension(p): Extension<Arc<Pairing>>, Path(id): Path<String>) -> Response {
    match p.view(&id) {
        Some(v) => {
            let mut j = view_json(&v, p.channel());
            // TouchId: the terminal waits for the fingerprint instead of asking y
            if p.channel() == crate::pairing::ConfirmChannel::TouchId {
                j["touch"] = serde_json::Value::Bool(true);
            }
            // the terminal's own credential, once, when the code was made for it (§19b-3)
            if let Ok(Some(c)) = p.take_terminal(&id) {
                j["terminal_credential"] = serde_json::Value::String(c);
            }
            j["pair_id"] = serde_json::Value::String(id);
            json(StatusCode::OK, j)
        }
        None => pair_error(PairError::NotFound, "no such pairing"),
    }
}

/// `POST /api/v1/pair/codes/{pair_id}/confirm` with `{"short_code": "nnnnnn"}`
async fn confirm_answer(
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
        Ok(()) => {
            let mut j = confirmed_body(p.channel());
            // the terminal's own credential, once, when the code was made for it (§19b-3)
            if let Ok(Some(c)) = p.take_terminal(&id) {
                j["terminal_credential"] = serde_json::Value::String(c);
            }
            json(StatusCode::OK, j)
        }
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

/// What the redeem form asked.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RedeemForm {
    pub code: String,
    /// `to=user`: a user folder page pairs for itself (S107-userhtml option 2).
    pub to_user: bool,
    /// `confirm_in_page=1`: the box under the code was ticked (SPEC §19b-2); any other value
    /// is "not ticked", so the terminal's y is still asked.
    pub confirm_in_page: bool,
    /// `n=…`: the page's nonce for this load (16–64 of `A-Z a-z 0-9 - _`, in its memory only,
    /// never in the URL); the same code sent again with it soon after is not a reuse.
    pub nonce: Option<String>,
}

/// A page nonce: 16–64 of `A-Z a-z 0-9 - _`.
fn nonce_ok(v: &str) -> bool {
    (16..=64).contains(&v.len())
        && v.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// The redeem form: `code=…` first, then at most one `to=user` and at most one
/// `confirm_in_page=…`, in either order. Any other shape is `None`.
fn redeem_form(body: &[u8]) -> Option<RedeemForm> {
    let s = std::str::from_utf8(body).ok()?;
    let mut parts = s.split('&');
    let mut f = RedeemForm {
        code: code_field(parts.next()?.as_bytes())?,
        ..RedeemForm::default()
    };
    let (mut to, mut box_, mut n) = (false, false, false);
    for p in parts {
        let (k, v) = p.split_once('=')?;
        match k {
            "to" if !to && v == "user" => {
                to = true;
                f.to_user = true;
            }
            "confirm_in_page" if !box_ => {
                box_ = true;
                f.confirm_in_page = v == "1";
            }
            "n" if !n && nonce_ok(v) => {
                n = true;
                f.nonce = Some(v.to_owned());
            }
            _ => return None,
        }
    }
    Some(f)
}

/// Where the code page's box default lives (SPEC §19b-2: "記在這台").
pub const PREFS_FILE: &str = "pair-prefs.json";

/// The box default: the last choice a right code was sent with; ticked when none.
pub fn confirm_in_page_default() -> bool {
    crate::workspace::data_file(PREFS_FILE)
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
        .and_then(|v| {
            v.get("confirm_in_page")
                .and_then(serde_json::Value::as_bool)
        })
        .unwrap_or(true)
}

/// Keep the box choice (only after a right code: a wrong guess changes nothing here).
fn keep_confirm_in_page(on: bool) {
    let Some(path) = crate::workspace::data_file(PREFS_FILE) else {
        return;
    };
    let tmp = path.with_extension("json.tmp");
    let body = serde_json::json!({ "confirm_in_page": on }).to_string();
    let written = {
        use std::io::Write;
        let mut o = std::fs::OpenOptions::new();
        o.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        o.open(&tmp).and_then(|mut f| f.write_all(body.as_bytes()))
    };
    if written.is_ok() && std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// `GET /api/v1/pair/prefs`: `{"confirm_in_page": bool}` for the code page, before pairing.
pub async fn prefs() -> Response {
    json(
        StatusCode::OK,
        serde_json::json!({ "confirm_in_page": confirm_in_page_default() }),
    )
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
    script_page(&script)
}

/// A page that only runs `script` (allowed by its hash), with no-store and no referrer.
fn script_page(script: &str) -> Response {
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

/// The answer to the same page sending its code again: no credential, only "go on" (the page
/// already stored the one it got). Same headers as [`exchange_page`].
pub fn already_page(to_user: bool) -> Response {
    // (not the same line as exchange_page's: knives-05 anchors on that one)
    let go = if to_user { "/" } else { BUILTIN_HOME };
    script_page(&format!("location.replace('{go}');"))
}

/// `(user_page, confirm_in_page)` for a redeem: a user page when the form says so or a user
/// folder serves `/` now; a user page never confirms in the page (the terminal's y stays).
pub fn redeem_flags(f: &RedeemForm, served_by_user: bool) -> (bool, bool) {
    let user_page = f.to_user || served_by_user;
    (user_page, f.confirm_in_page && !user_page)
}

/// `POST /api/v1/pair/redeem`
async fn redeem_answer(
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
    let f = redeem_form(&bytes).unwrap_or_default();
    // 5d33a PREREAD-oneweb M1: whoever serves `/` now counts, not only what the form says (a
    // user folder page that leaves out to=user is still a user page); unknown ⇒ a user page
    let served_by_user =
        tokio::task::spawn_blocking(|| crate::static_files::web_source() == "user")
            .await
            .unwrap_or(true);
    let (user_page, confirm_in_page) = redeem_flags(&f, served_by_user);
    match p.redeem_with(&f.code, user_page, confirm_in_page, f.nonce.as_deref()) {
        Ok(r) => {
            if !user_page {
                keep_confirm_in_page(f.confirm_in_page);
            }
            if p.channel() == crate::pairing::ConfirmChannel::TouchId {
                // aibd asks the owner now; the page and the terminal see confirmed / revoked
                let (p, id) = (p.clone(), r.pair_id.clone());
                tokio::task::spawn_blocking(move || {
                    let out = p.ask_touch(&id, &crate::touch::Who::Terminal);
                    let why = out.err().unwrap_or("ok");
                    p.journal(
                        "pair.touch",
                        serde_json::json!({"outcome": why, "pair_id": id}),
                    );
                });
            }
            exchange_page(&r.credential, f.to_user)
        }
        Err(PairError::Full) => pair_error(PairError::Full, "too many paired pages; revoke one"),
        // the same page sent the code twice: it keeps the credential it has, and goes on
        Err(PairError::Already) => already_page(f.to_user),
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
        Some(v) => {
            let mut j = view_json(&v, p.channel());
            // the page's probe (519d0 01:41): offer the window only when aibd has it
            if v.carried && !a.carried {
                j["reconfirmed"] = serde_json::Value::Bool(true);
            } else if a.carried && p.can_reconfirm() {
                j["reconfirm"] = serde_json::Value::Bool(true);
            }
            json(StatusCode::OK, j)
        }
        None => error_response(ErrorCode::Unauthorized, "wrong token", &request_id()),
    }
}

/// `POST /api/v1/pair/reconfirm` `{act, seat?}` (BOUNDARY r3 §3; 5d33a CHECK-pair-touchid
/// §6-2): a pairing carried over an update asks aibd for the owner's finger before `act`; aibd
/// shows the window with its own words for `act` and the seat only once it is a seat code.
/// 204 = the same credential now works as one paired in this run; the page sends the action
/// again without clearing anything.
pub async fn reconfirm(
    Extension(c): Extension<Caller>,
    Extension(p): Extension<Arc<Pairing>>,
    body: Bytes,
) -> Response {
    let Caller::Page(a) = c else {
        return error_response(
            ErrorCode::InvalidRequest,
            "send the page credential",
            &request_id(),
        );
    };
    let v: serde_json::Value = match serde_json::from_slice(&body) {
        Ok(v @ serde_json::Value::Object(_)) => v,
        _ => return refused(StatusCode::BAD_REQUEST, "reconfirm_act_unknown"),
    };
    let act = v
        .get("act")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_owned();
    // nothing else the page sends reaches the window (5d33a REVIEW-touchid-8ec571dc L1)
    let (p2, digest, act2) = (p.clone(), a.digest, act.clone());
    let r = tokio::task::spawn_blocking(move || p2.reconfirm(&digest, &act2))
        .await
        .unwrap_or(Err("pair_touch_failed"));
    let outcome = match r {
        Ok(()) => "confirmed",
        Err(e) => e,
    };
    // 5d33a PREREAD-integws2 L4: an act aibd does not know is the page's own text: only its
    // length goes in the journal
    let act3 = if outcome == "reconfirm_act_unknown" {
        serde_json::json!({"unknown_len": act.len()})
    } else {
        serde_json::json!(act)
    };
    let p3 = p.clone();
    let _ = tokio::task::spawn_blocking(move || {
        p3.journal(
            "pair.reconfirm",
            serde_json::json!({"outcome": outcome, "act": act3}),
        )
    })
    .await;
    match r {
        Ok(()) => no_store(StatusCode::NO_CONTENT.into_response()),
        Err(e @ "reconfirm_act_unknown") => refused(StatusCode::BAD_REQUEST, e),
        Err(e @ "pair_touch_limited") => {
            // when a window fits again, as the owner's windows say it (0eed7 pv7 L)
            let at = p.touch_wait().map(|w| {
                crate::owner_ok::unix_now() + w.as_secs() + u64::from(w.subsec_nanos() > 0)
            });
            let mut v = crate::owner_ok::refusal_at(e, at);
            v["code"] = serde_json::json!(e);
            v["request_id"] = serde_json::json!(request_id());
            json(StatusCode::TOO_MANY_REQUESTS, v)
        }
        Err(e @ ("pair_reconfirm_limited" | "pair_touch_busy" | "pair_touch_cooling")) => {
            refused(StatusCode::TOO_MANY_REQUESTS, e)
        }
        Err(e @ ("pair_not_carried" | "pair_reconfirm_off")) => refused(StatusCode::CONFLICT, e),
        Err(e) => refused(StatusCode::FORBIDDEN, e),
    }
}

fn refused(status: StatusCode, code: &str) -> Response {
    json(
        status,
        serde_json::json!({"code": code, "request_id": request_id()}),
    )
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

    /// SPEC §19f: `replaces` only with terminal true and a 16-hex pair id; nothing else taken.
    #[test]
    fn a_code_body_replaces_only_the_terminals_own_pair_id() {
        assert_eq!(codes_body(b""), Some((false, None)));
        assert_eq!(codes_body(b"{}"), Some((false, None)));
        assert_eq!(codes_body(br#"{"terminal":true}"#), Some((true, None)));
        assert_eq!(
            codes_body(br#"{"terminal":true,"replaces":"0123456789abcdef"}"#),
            Some((true, Some("0123456789abcdef".into())))
        );
        for bad in [
            &br#"{"replaces":"0123456789abcdef"}"#[..],
            br#"{"terminal":false,"replaces":"0123456789abcdef"}"#,
            br#"{"terminal":true,"replaces":"0123"}"#,
            br#"{"terminal":true,"replaces":"../../etc/passwd"}"#,
            br#"{"terminal":"yes"}"#,
            br#"{"terminal":true,"x":1}"#,
            b"[]",
        ] {
            assert_eq!(codes_body(bad), None, "reason: body_taken {bad:?}");
        }
    }

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
        let f = |code: &str, to_user, confirm_in_page| RedeemForm {
            code: code.into(),
            to_user,
            confirm_in_page,
            nonce: None,
        };
        assert_eq!(
            redeem_form(b"code=ABCDE-FGHJK"),
            Some(f("ABCDE-FGHJK", false, false))
        );
        assert_eq!(
            redeem_form(b"code=ABCDE-FGHJK&to=user"),
            Some(f("ABCDE-FGHJK", true, false))
        );
        assert_eq!(
            redeem_form(b"code=ABCDE-FGHJK&confirm_in_page=1"),
            Some(f("ABCDE-FGHJK", false, true))
        );
        // only "1" ticks the box: anything else still asks the terminal's y
        for v in ["0", "true", "on", "yes", "", "11"] {
            let b = format!("code=A&confirm_in_page={v}");
            assert_eq!(
                redeem_form(b.as_bytes()).map(|x| x.confirm_in_page),
                Some(false),
                "reason: not_one_ticks {v}"
            );
        }
        for bad in [
            &b"code=A&to=other"[..],
            b"code=A&to=user&x=1",
            b"to=user&code=A",
            b"code=A&code=B",
            b"code=A&confirm_in_page=1&confirm_in_page=1",
            b"confirm_in_page=1&code=A",
            // a page nonce: 16-64 of A-Z a-z 0-9 - _, at most once
            b"code=A&n=short",
            b"code=A&n=0123456789abcdef&n=0123456789abcdef",
            b"code=A&n=0123456789abcdef%2F",
        ] {
            assert_eq!(redeem_form(bad), None, "{bad:?}");
        }
        // 5d33a M1: a user folder serving `/` makes it a user page, with or without to=user
        let ticked = f("A", false, true);
        assert_eq!(redeem_flags(&ticked, false), (false, true));
        assert_eq!(
            redeem_flags(&ticked, true),
            (true, false),
            "reason: user_folder_confirms_in_page"
        );
        assert_eq!(redeem_flags(&f("A", true, true), false), (true, false));
        assert_eq!(
            redeem_form(b"code=A&confirm_in_page=1&n=0123456789abcdef-_").and_then(|x| x.nonce),
            Some("0123456789abcdef-_".to_owned()),
            "reason: nonce_dropped"
        );
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
