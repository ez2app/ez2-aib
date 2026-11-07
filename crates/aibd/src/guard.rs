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

fn query_has_credential(query: &str, token: &Token) -> bool {
    query.split('&').any(|pair| {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let k = k.to_ascii_lowercase();
        CREDENTIAL_PARAMS.contains(&k.as_str()) || (!v.is_empty() && token.matches(v))
    }) || query.contains(token.expose())
}

pub async fn guard(
    State(cfg): State<Arc<GuardConfig>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let rid = request_id();
    let method = req.method().clone();
    let path = req.uri().path().to_string(); // never log the query
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
