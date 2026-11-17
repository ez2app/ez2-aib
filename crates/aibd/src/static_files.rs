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
//!
//! S107-userhtml (Andy 11-18: the screens stay adjustable at any time and a user may design
//! their own pages): a file in the user's folder `<data dir>/web/` is served before the built-in
//! table, read at each request (an edit shows on reload, no restart). Only a regular file whose
//! real path stays inside that folder (no `..`, no link leading out), at most
//! [`MAX_USER_FILE`] bytes, with a known type; anything else falls back to the built-in file.
//! The same headers apply (page policy, no-cache + content ETag). `GET /api/v1/health` says
//! which one serves `/` (`web_source`: `user` / `builtin` / `none`). The folder is writable by
//! anything running as the account, like the rest of the data folder: it is not a defense
//! (GUIDE-same-user-ai-threat-model); a page from it still has to be paired to call the API.
//! A user page is served from aibd's own origin, so its scripts can read what the built-in
//! pairing page left in that tab (the page credential in sessionStorage) and call the API with
//! it: whoever can write the folder can use a pairing a person confirmed (0eed7 L2, 5d33a M1 on
//! 3b09b3ba; how far that may go is 519d0's / Andy's call).

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

/// The largest user file served (S107-userhtml); a bigger one falls back to the built-in file.
pub const MAX_USER_FILE: u64 = 4 * 1024 * 1024;

/// The user's page folder (`<data dir>/web`), set once by the server at start.
static USER_DIR: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();

pub fn set_user_dir(dir: std::path::PathBuf) {
    let _ = USER_DIR.set(dir);
}

/// Who serves `/` now: `user` (a usable `index.html` in the user's folder), `builtin` (the
/// embedded table), or `none`.
pub fn web_source() -> &'static str {
    let user = USER_DIR
        .get()
        .is_some_and(|d| user_file(d, "index.html").is_some());
    if user {
        "user"
    } else if ASSETS.is_empty() {
        "none"
    } else {
        "builtin"
    }
}

/// The type of a user file by its extension; `None` = not served from the user's folder.
fn user_type(rel: &str) -> Option<&'static str> {
    let ext = rel.rsplit_once('.')?.1.to_ascii_lowercase();
    Some(match ext.as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff2" => "font/woff2",
        "txt" => "text/plain; charset=utf-8",
        _ => return None,
    })
}

/// The bytes and type of `rel` in the user's folder, or `None` to fall back: a plain relative
/// path of normal parts, a known type, a regular file whose real path (links resolved) is inside
/// the folder's real path, at most [`MAX_USER_FILE`] bytes.
fn user_file(root: &std::path::Path, rel: &str) -> Option<(Vec<u8>, &'static str)> {
    let parts: Vec<&str> = rel.split('/').collect();
    let plain =
        |p: &&str| !p.is_empty() && *p != "." && *p != ".." && !p.contains(['\\', ':', '\0']);
    if !parts.iter().all(plain) {
        return None;
    }
    let ty = user_type(rel)?;
    let root = std::fs::canonicalize(root).ok()?;
    let real = std::fs::canonicalize(parts.iter().fold(root.clone(), |p, x| p.join(x))).ok()?;
    if !real.starts_with(&root) {
        return None;
    }
    if !std::fs::metadata(&real).ok()?.is_file() {
        return None;
    }
    // one size check, on what is read (the file may grow after a metadata look)
    use std::io::Read;
    let mut body = Vec::new();
    std::fs::File::open(&real)
        .ok()?
        .take(MAX_USER_FILE + 1)
        .read_to_end(&mut body)
        .ok()?;
    if body.len() as u64 > MAX_USER_FILE {
        return None;
    }
    Some((body, ty))
}

/// Serve `path` from the user's folder `root`, or `None` to fall back to the built-in table.
pub fn serve_user(
    root: &std::path::Path,
    method: &Method,
    path: &str,
    h: &HeaderMap,
) -> Option<Response> {
    if method != Method::GET && method != Method::HEAD {
        return None;
    }
    let rel = match path.strip_prefix('/') {
        Some("") | None => "index.html",
        Some(r) => r,
    };
    let (body, ty) = user_file(root, rel)?;
    use sha2::Digest;
    let hash: String = sha2::Sha256::digest(&body)[..16]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let mut out = Response::builder()
        .header(header::X_CONTENT_TYPE_OPTIONS, "nosniff")
        .header(header::REFERRER_POLICY, "no-referrer")
        .header(header::CACHE_CONTROL, "no-cache")
        .header(header::ETAG, etag_of(&hash, None));
    // SVG too: opened on its own it runs its scripts (5d33a L1 on 3b09b3ba)
    if ty.starts_with("text/html") || ty.starts_with("image/svg+xml") {
        out = out
            .header(
                header::CONTENT_SECURITY_POLICY,
                HeaderValue::from_static(PAGE_CSP),
            )
            .header(header::X_FRAME_OPTIONS, "DENY");
    }
    if etag_matches(h, &hash) {
        return out
            .status(StatusCode::NOT_MODIFIED)
            .body(Body::empty())
            .ok();
    }
    out = out
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, ty)
        .header(header::CONTENT_LENGTH, body.len());
    let body = if *method == Method::HEAD {
        Body::empty()
    } else {
        Body::from(body)
    };
    out.body(body).ok()
}

/// The router fallback: unknown `/api/` paths stay JSON `not_found`; everything else is a
/// static file.
pub async fn fallback(req: Request) -> Response {
    let path = req.uri().path().to_string();
    if crate::guard::is_api(&path) {
        return error_response(ErrorCode::NotFound, "no such route", &request_id());
    }
    if let Some(root) = USER_DIR.get() {
        let (m, h) = (req.method().clone(), req.headers().clone());
        let root = root.clone();
        let p = path.clone();
        // a file read: off the async threads
        if let Ok(Some(r)) =
            tokio::task::spawn_blocking(move || serve_user(&root, &m, &p, &h)).await
        {
            return r;
        }
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

#[cfg(test)]
mod user_tests {
    use super::*;

    fn get(root: &std::path::Path, path: &str) -> Option<(StatusCode, HeaderMap, Vec<u8>)> {
        let r = serve_user(root, &Method::GET, path, &HeaderMap::new())?;
        let (parts, body) = r.into_parts();
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        let b = rt.block_on(axum::body::to_bytes(body, usize::MAX)).unwrap();
        Some((parts.status, parts.headers, b.to_vec()))
    }

    #[test]
    fn a_user_page_is_served_with_the_page_policy_and_read_each_time() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("index.html"), "<p>mine</p>").unwrap();
        let (s, h, b) = get(d.path(), "/").unwrap();
        assert_eq!(s, StatusCode::OK);
        assert_eq!(b, b"<p>mine</p>");
        assert_eq!(h[header::CONTENT_SECURITY_POLICY], PAGE_CSP);
        assert_eq!(h[header::X_FRAME_OPTIONS], "DENY");
        assert_eq!(h[header::CACHE_CONTROL], "no-cache");
        // edited: the next request shows it, with a new ETag
        let tag = h[header::ETAG].clone();
        std::fs::write(d.path().join("index.html"), "<p>edited</p>").unwrap();
        let (_, h2, b2) = get(d.path(), "/index.html").unwrap();
        assert_eq!(b2, b"<p>edited</p>");
        assert_ne!(h2[header::ETAG], tag);
        // the same ETag back: 304, no body
        let mut inm = HeaderMap::new();
        inm.insert(header::IF_NONE_MATCH, h2[header::ETAG].clone());
        let r = serve_user(d.path(), &Method::GET, "/", &inm).unwrap();
        assert_eq!(r.status(), StatusCode::NOT_MODIFIED);
    }

    /// 5d33a L1 on 3b09b3ba: an SVG opened on its own runs its scripts, so it gets the policy too.
    #[test]
    fn a_user_svg_gets_the_page_policy() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("x.svg"), "<svg/>").unwrap();
        let (s, h, _) = get(d.path(), "/x.svg").unwrap();
        assert_eq!(s, StatusCode::OK);
        assert_eq!(h[header::CONTENT_SECURITY_POLICY], PAGE_CSP);
        std::fs::write(d.path().join("x.css"), "p{}").unwrap();
        let (_, h, _) = get(d.path(), "/x.css").unwrap();
        assert!(!h.contains_key(header::CONTENT_SECURITY_POLICY));
    }

    #[test]
    fn paths_outside_the_folder_unknown_types_and_big_files_fall_back() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("web");
        std::fs::create_dir(&root).unwrap();
        std::fs::write(d.path().join("secret.html"), "out").unwrap();
        std::fs::write(root.join("a.exe"), "x").unwrap();
        std::fs::write(root.join("ok.css"), "p{}").unwrap();
        let big = vec![b'x'; usize::try_from(MAX_USER_FILE).unwrap() + 1];
        std::fs::write(root.join("big.html"), big).unwrap();
        for p in [
            "/../secret.html",
            "/./ok.css",
            "//ok.css",
            "/a.exe",
            "/big.html",
            "/missing.html",
            "/ok.css\\..",
        ] {
            assert!(get(&root, p).is_none(), "{p}");
        }
        assert!(get(&root, "/ok.css").is_some());
        assert!(
            serve_user(&root, &Method::POST, "/ok.css", &HeaderMap::new()).is_none(),
            "only GET / HEAD"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_link_that_leads_out_of_the_folder_falls_back() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("web");
        std::fs::create_dir(&root).unwrap();
        std::fs::write(d.path().join("secret.html"), "out").unwrap();
        std::os::unix::fs::symlink(d.path().join("secret.html"), root.join("index.html")).unwrap();
        assert!(get(&root, "/").is_none());
        // a link that stays inside is fine
        std::fs::write(root.join("real.html"), "in").unwrap();
        std::os::unix::fs::symlink(root.join("real.html"), root.join("alias.html")).unwrap();
        assert_eq!(get(&root, "/alias.html").unwrap().2, b"in");
    }
}
