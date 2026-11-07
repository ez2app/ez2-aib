//! Request guard applied to every route (ADR 0003). Order matters:
//! Host (DNS rebinding) -> Origin -> token-in-URL -> bearer token.

use crate::token::Token;
use aib_core::ErrorCode;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use std::sync::Arc;

/// Paths reachable without the bearer token (Host/Origin still apply).
const PUBLIC_PATHS: &[&str] = &["/api/v1/health"];

/// Routes whose path may appear in logs. Anything else is logged as
/// `UNMATCHED_PATH`, so a path like `/api/v1/<token>` never reaches the log.
pub const LOGGED_ROUTES: &[&str] = &["/api/v1/health", "/api/v1/events"];
pub const UNMATCHED_PATH: &str = "<unmatched>";

/// Query parameter names that look like credentials; any of them is refused.
const CREDENTIAL_PARAMS: &[&str] = &[
    "token",
    "access_token",
    "auth",
    "authorization",
    "bearer",
    "key",
    "api_key",
    "apikey",
];

#[derive(Debug)]
pub struct GuardConfig {
    pub allowed_hosts: Vec<String>,
    pub allowed_origins: Vec<String>,
    pub token: Token,
}

impl GuardConfig {
    pub fn new(port: u16, ipv6: bool, allowed_origins: Vec<String>, token: Token) -> Self {
        let mut allowed_hosts = vec![format!("127.0.0.1:{port}"), format!("localhost:{port}")];
        if ipv6 {
            allowed_hosts.push(format!("[::1]:{port}"));
        }
        GuardConfig {
            allowed_hosts,
            allowed_origins,
            token,
        }
    }
}

pub fn request_id() -> String {
    let mut b = [0u8; 8];
    let _ = getrandom::fill(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

pub fn error_response(code: ErrorCode, message: &str, request_id: &str) -> Response {
    let status =
        StatusCode::from_u16(code.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = serde_json::json!({
        "code": code.as_str(),
        "message": message,
        "request_id": request_id,
    });
    (
        status,
        [(header::CONTENT_TYPE, "application/json")],
        body.to_string(),
    )
        .into_response()
}

fn single_header(h: &HeaderMap, name: header::HeaderName) -> Result<Option<&str>, ()> {
    let mut it = h.get_all(name).iter();
    match (it.next(), it.next()) {
        (None, _) => Ok(None),
        (Some(v), None) => v.to_str().map(Some).map_err(|_| ()),
        (Some(_), Some(_)) => Err(()), // duplicated header: refuse rather than guess
    }
}

/// `application/x-www-form-urlencoded` decoding: `+` is a space, `%XX` a byte.
/// Invalid escapes are kept literally (the check below still sees them).
fn form_decode(s: &str) -> String {
    fn hex(c: u8) -> Option<u8> {
        match c {
            b'0'..=b'9' => Some(c - b'0'),
            b'a'..=b'f' => Some(c - b'a' + 10),
            b'A'..=b'F' => Some(c - b'A' + 10),
            _ => None,
        }
    }
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' => {
                match (
                    b.get(i + 1).copied().and_then(hex),
                    b.get(i + 2).copied().and_then(hex),
                ) {
                    (Some(h), Some(l)) => {
                        out.push(h * 16 + l);
                        i += 2;
                    }
                    _ => out.push(b'%'),
                }
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query_has_credential(query: &str, token: &Token) -> bool {
    // Names: each key decoded twice (`%74oken`, `%2574oken`), case-insensitive.
    let names_hit = query.split('&').any(|pair| {
        let (k, _) = pair.split_once('=').unwrap_or((pair, ""));
        let k = form_decode(&form_decode(k)).to_ascii_lowercase();
        CREDENTIAL_PARAMS.contains(&k.trim())
    });
    // Values: the token itself, raw, encoded once or twice, anywhere.
    let once = form_decode(query);
    let twice = form_decode(&once);
    let value_hit = [query, once.as_str(), twice.as_str()]
        .iter()
        .any(|q| q.contains(token.expose()));
    names_hit || value_hit
}

/// The path as it may be logged: a known route, or a fixed placeholder.
pub fn loggable_path(path: &str) -> &'static str {
    LOGGED_ROUTES
        .iter()
        .find(|r| **r == path)
        .copied()
        .unwrap_or(UNMATCHED_PATH)
}

/// Placeholder for any non-standard HTTP method in logs.
pub const OTHER_METHOD: &str = "<other>";

/// The method as it may be logged: a standard method, or a fixed
/// placeholder. A method is an arbitrary token on the wire, so a caller could
/// put a secret there (review by @3f312, 11-07).
pub fn loggable_method(m: &axum::http::Method) -> &'static str {
    use axum::http::Method;
    const STANDARD: [Method; 9] = [
        Method::GET,
        Method::POST,
        Method::PUT,
        Method::DELETE,
        Method::PATCH,
        Method::HEAD,
        Method::OPTIONS,
        Method::CONNECT,
        Method::TRACE,
    ];
    STANDARD
        .iter()
        .find(|s| *s == m)
        .map(|s| s.as_str())
        .unwrap_or(OTHER_METHOD)
}

pub async fn guard(
    State(cfg): State<Arc<GuardConfig>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let rid = request_id();
    // never log the raw method, path or query: any of them may carry a secret
    let method = loggable_method(req.method());
    let path = loggable_path(req.uri().path());
    let verdict = check(&cfg, &req);
    let resp = match verdict {
        Err((code, msg)) => error_response(code, msg, &rid),
        Ok(()) => next.run(req).await,
    };
    tracing::info!(request_id = %rid, %method, %path, status = resp.status().as_u16(), "request");
    resp
}

fn check(cfg: &GuardConfig, req: &Request<Body>) -> Result<(), (ErrorCode, &'static str)> {
    let h = req.headers();

    let host = single_header(h, header::HOST)
        .map_err(|_| (ErrorCode::HostNotAllowed, "bad Host header"))?
        .ok_or((ErrorCode::HostNotAllowed, "missing Host header"))?;
    if !cfg
        .allowed_hosts
        .iter()
        .any(|a| a.eq_ignore_ascii_case(host))
    {
        return Err((
            ErrorCode::HostNotAllowed,
            "Host is not this loopback service",
        ));
    }

    match single_header(h, header::ORIGIN) {
        Err(_) => return Err((ErrorCode::OriginNotAllowed, "bad Origin header")),
        Ok(Some(o)) if !cfg.allowed_origins.iter().any(|a| a == o) => {
            return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed"))
        }
        _ => {}
    }

    let raw_path = req.uri().path();
    if [
        raw_path.to_string(),
        form_decode(raw_path),
        form_decode(&form_decode(raw_path)),
    ]
    .iter()
    .any(|p| p.contains(cfg.token.expose()))
    {
        return Err((
            ErrorCode::TokenInUrl,
            "credentials must not be sent in the URL",
        ));
    }
    if let Some(q) = req.uri().query() {
        if query_has_credential(q, &cfg.token) {
            return Err((
                ErrorCode::TokenInUrl,
                "credentials must not be sent in the URL",
            ));
        }
    }

    if PUBLIC_PATHS.contains(&req.uri().path()) {
        return Ok(());
    }
    let auth = single_header(h, header::AUTHORIZATION)
        .map_err(|_| (ErrorCode::Unauthorized, "bad Authorization header"))?
        .ok_or((ErrorCode::Unauthorized, "missing bearer token"))?;
    let presented = auth
        .strip_prefix("Bearer ")
        .ok_or((ErrorCode::Unauthorized, "expected Bearer token"))?;
    if !cfg.token.matches(presented) {
        return Err((ErrorCode::Unauthorized, "wrong token"));
    }
    Ok(())
}
