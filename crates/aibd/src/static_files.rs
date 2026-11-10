//! Static files of the web workbench (ADR 0006 §6; PLAN-web-workbench A3).
//!
//! The files are compiled into aibd behind the cargo feature `web` (a9136 Q4, 11-10):
//! `build.rs` embeds `apps/web/dist` (or `AIBD_WEB_DIST`). Without the feature the table is
//! empty and every non-API path answers `404 web_not_built`, so `cargo test` never needs
//! node. Nothing is read from disk at run time, so a path can only name a file that is in
//! the table (no traversal, no symlinks).
//!
//! Rules (§6):
//! - `index.html` (also `/`) and other files outside `assets/`: strong `ETag` (content hash,
//!   one per encoding: `"<hash>"`, `"<hash>-br"`, `"<hash>-gz"`), `Cache-Control: no-cache`;
//!   a matching `If-None-Match` (any of the three) gives `304` with no body.
//! - files under `assets/` carry a content hash in their name: one year, `immutable`.
//! - a `.br` / `.gz` file next to the original is sent with `Content-Encoding` when the
//!   client accepts it; every response has `Vary: Accept-Encoding`.
//! - every `text/html` file gets the page policy (`PAGE_CSP`) and `X-Frame-Options: DENY`.
//! - no file contains a credential: the table is identical for every caller.
//! - the front end uses hash routes; there is no deep-link fallback (ADR 0006 §6).
//! - every `/api/` answer carries `X-Aibd-Build` (`BUILD`); an open page reloads when it
//!   changes, so old page code never talks to a new API (ADR 0006 §6 W1 note, 1ced7 A3 M2).

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

/// Page policy for every `text/html` file: only our own scripts, styles, fonts and API;
/// `data:` images and fonts (Vite inlines files under 4 KB, 1ced7 A3 L1); the pairing form
/// posts to our own origin; never framed.
pub const PAGE_CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
img-src 'self' data:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; \
form-action 'self'; frame-ancestors 'none'";

/// `X-Aibd-Build`: aibd version + hash of the embedded `dist` (`nodist` without the feature).
pub const BUILD: &str = concat!(env!("CARGO_PKG_VERSION"), "+", env!("AIBD_BUILD_DIST"));
pub const BUILD_HEADER: &str = "x-aibd-build";

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

/// The strong ETag of one encoding: each content coding is its own representation
/// (RFC 9110 §8.8.3; 1ced7 A3 L2).
fn etag_of(hash: &str, coding: Option<&str>) -> String {
    match coding {
        Some("br") => format!("\"{hash}-br\""),
        Some(_) => format!("\"{hash}-gz\""),
        None => format!("\"{hash}\""),
    }
}

/// Does `If-None-Match` name this file in any of its encodings (or `*`)?
fn etag_matches(h: &HeaderMap, hash: &str) -> bool {
    let known = [
        etag_of(hash, None),
        etag_of(hash, Some("br")),
        etag_of(hash, Some("gzip")),
    ];
    h.get_all(header::IF_NONE_MATCH)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(','))
        .any(|t| {
            let t = t.trim();
            t == "*" || known.iter().any(|k| k == t)
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
    let is_html = a.content_type.starts_with("text/html");
    let hashed = a.path.starts_with("assets/");
    let (body, coding) = match (a.br, a.gz) {
        (Some(b), _) if accepts(h, "br") => (b, Some("br")),
        (_, Some(g)) if accepts(h, "gzip") => (g, Some("gzip")),
        _ => (a.body, None),
    };

    let mut out = Response::builder()
        .header(header::VARY, "Accept-Encoding")
        .header(header::X_CONTENT_TYPE_OPTIONS, "nosniff")
        .header(header::REFERRER_POLICY, "no-referrer");
    out = if hashed {
        out.header(header::CACHE_CONTROL, IMMUTABLE)
    } else {
        out.header(header::CACHE_CONTROL, "no-cache")
            .header(header::ETAG, etag_of(a.etag, coding))
    };
    if is_html {
        out = out
            .header(header::CONTENT_SECURITY_POLICY, PAGE_CSP)
            .header(header::X_FRAME_OPTIONS, "DENY");
    }
    if !hashed && etag_matches(h, a.etag) {
        return out
            .status(StatusCode::NOT_MODIFIED)
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response());
    }
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

/// Every API answer: `Cache-Control: no-store` unless it set its own (§6: static cache
/// rules never apply to `/api/`), and `X-Aibd-Build` so an open page notices an upgraded
/// aibd and reloads (1ced7 A3 M2). Called by the guard.
pub fn api_no_store(path: &str, resp: &mut Response) {
    if !crate::guard::is_api(path) {
        return;
    }
    let h = resp.headers_mut();
    if !h.contains_key(header::CACHE_CONTROL) {
        h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    }
    h.insert(BUILD_HEADER, HeaderValue::from_static(BUILD));
}
