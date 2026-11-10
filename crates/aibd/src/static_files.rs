//! Static files of the web workbench (ADR 0006 §6; PLAN-web-workbench A3).
//!
//! The files are compiled into aibd behind the cargo feature `web` (a9136 Q4, 11-10):
//! `build.rs` embeds `apps/web/dist` (or `AIBD_WEB_DIST`). Without the feature the table is
//! empty and every non-API path answers `404 web_not_built`, so `cargo test` never needs
//! node. Nothing is read from disk at run time, so a path can only name a file that is in
//! the table (no traversal, no symlinks).
//!
//! Rules (§6):
//! - `index.html` (also `/`): strong `ETag` (content hash), `Cache-Control: no-cache`, a
//!   matching `If-None-Match` gives `304` with no body.
//! - files under `assets/` carry a content hash in their name: one year, `immutable`.
//! - a `.br` / `.gz` file next to the original is sent with `Content-Encoding` when the
//!   client accepts it; every response has `Vary: Accept-Encoding`.
//! - no file contains a credential: the table is identical for every caller.
//! - W1 ships one bundle: a hashed file of the previous version is gone after an upgrade,
//!   and the page reloads on its 404 (ADR 0006 §6 W1 note).

use crate::guard::{error_response, request_id};
use aib_core::ErrorCode;
use axum::body::Body;
use axum::extract::Request;
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode};
use axum::response::{IntoResponse, Response};

/// One embedded file.
#[derive(Debug)]
pub struct Asset {
    /// Path relative to the dist root, `/`-separated, no leading `/` (`index.html`).
    pub path: &'static str,
    pub content_type: &'static str,
    pub body: &'static [u8],
    pub gz: Option<&'static [u8]>,
    pub br: Option<&'static [u8]>,
    /// Content hash (hex), used as the strong ETag.
    pub etag: &'static str,
}

#[cfg(feature = "web")]
include!(concat!(env!("OUT_DIR"), "/web_assets.rs"));

/// Built without the `web` feature: no files.
#[cfg(not(feature = "web"))]
pub static ASSETS: &[Asset] = &[];

/// Page policy for `index.html`: only our own scripts, styles and API; the pairing form
/// posts to our own origin; never framed.
pub const INDEX_CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; \
frame-ancestors 'none'";

const IMMUTABLE: &str = "public, max-age=31536000, immutable";

/// The router fallback: unknown `/api/` paths stay JSON `not_found`; everything else is a
/// static file.
pub async fn fallback(req: Request) -> Response {
    let path = req.uri().path().to_string();
    if crate::guard::is_api(&path) {
        return error_response(ErrorCode::NotFound, "no such route", &request_id());
    }
    serve(ASSETS, req.method(), &path, req.headers())
}

/// Does the client accept this content coding (`q=0` means no)?
fn accepts(h: &HeaderMap, coding: &str) -> bool {
    h.get_all(header::ACCEPT_ENCODING)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(','))
        .any(|item| {
            let mut parts = item.split(';');
            let name = parts.next().unwrap_or("").trim();
            let zero = parts.any(|p| {
                let p = p.trim();
                p.strip_prefix("q=")
                    .is_some_and(|q| q.trim().parse::<f32>().is_ok_and(|q| q <= 0.0))
            });
            name.eq_ignore_ascii_case(coding) && !zero
        })
}

fn etag_matches(h: &HeaderMap, etag: &str) -> bool {
    let quoted = format!("\"{etag}\"");
    h.get_all(header::IF_NONE_MATCH)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(','))
        .any(|t| {
            let t = t.trim();
            t == quoted || t == "*"
        })
}

/// Serve `path` from `assets` (tests pass their own table).
pub fn serve(assets: &[Asset], method: &Method, path: &str, h: &HeaderMap) -> Response {
    if method != Method::GET && method != Method::HEAD {
        return error_response(
            ErrorCode::MethodNotAllowed,
            "method not allowed",
            &request_id(),
        );
    }
    if assets.is_empty() {
        return error_response(
            ErrorCode::WebNotBuilt,
            "this aibd was built without the web workbench (cargo feature `web`)",
            &request_id(),
        );
    }
    let rel = match path.strip_prefix('/') {
        Some("") | None => "index.html",
        Some(r) => r,
    };
    let Some(a) = assets.iter().find(|a| a.path == rel) else {
        return error_response(ErrorCode::NotFound, "no such file", &request_id());
    };
    let is_index = a.path == "index.html";
    let hashed = a.path.starts_with("assets/");

    let mut out = Response::builder()
        .header(header::VARY, "Accept-Encoding")
        .header(header::X_CONTENT_TYPE_OPTIONS, "nosniff")
        .header(header::REFERRER_POLICY, "no-referrer");
    out = if hashed {
        out.header(header::CACHE_CONTROL, IMMUTABLE)
    } else {
        out.header(header::CACHE_CONTROL, "no-cache")
            .header(header::ETAG, format!("\"{}\"", a.etag))
    };
    if is_index {
        out = out
            .header(header::CONTENT_SECURITY_POLICY, INDEX_CSP)
            .header(header::X_FRAME_OPTIONS, "DENY");
    }
    if !hashed && etag_matches(h, a.etag) {
        return out
            .status(StatusCode::NOT_MODIFIED)
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response());
    }
    let (body, coding) = match (a.br, a.gz) {
        (Some(b), _) if accepts(h, "br") => (b, Some("br")),
        (_, Some(g)) if accepts(h, "gzip") => (g, Some("gzip")),
        _ => (a.body, None),
    };
    out = out
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, a.content_type)
        .header(header::CONTENT_LENGTH, body.len());
    if let Some(c) = coding {
        out = out.header(header::CONTENT_ENCODING, c);
    }
    let body = if method == Method::HEAD {
        Body::empty()
    } else {
        Body::from(body)
    };
    out.body(body)
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

/// `Cache-Control: no-store` on every API answer that set nothing itself (§6: static cache
/// rules never apply to `/api/`). Called by the guard.
pub fn api_no_store(path: &str, resp: &mut Response) {
    if crate::guard::is_api(path) && !resp.headers().contains_key(header::CACHE_CONTROL) {
        resp.headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    }
}
