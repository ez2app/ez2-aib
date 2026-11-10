#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Static files (ADR 0006 §6). `serve` is tested on a table of fake files, so no front-end
//! build (node) is needed; the router tests run on an aibd built without the `web`
//! feature, which must answer `404 web_not_built` and never leak the token.

use aibd::api::{router, AppState};
use aibd::guard::GuardConfig;
use aibd::static_files::{serve, Asset, BUILD, PAGE_CSP};
use aibd::token::Token;
use axum::body::Body;
use axum::http::{HeaderMap, HeaderValue, Method, Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

const PORT: u16 = 47772;
const TOKEN: &str = "test-token-value-0123456789abcdefghijklmnopq";

static FILES: &[Asset] = &[
    Asset {
        path: "index.html",
        content_type: "text/html; charset=utf-8",
        body: b"<!doctype html><script type=module src=/assets/app-1a2b3c.js></script>",
        gz: Some(b"GZ-INDEX"),
        br: None,
        etag: "0123456789abcdef0123456789abcdef",
    },
    Asset {
        path: "pair.html",
        content_type: "text/html; charset=utf-8",
        body: b"<!doctype html><title>pair</title>",
        gz: None,
        br: None,
        etag: "11111111111111111111111111111111",
    },
    Asset {
        path: "assets/app-1a2b3c.js",
        content_type: "text/javascript; charset=utf-8",
        body: b"console.log('plain')",
        gz: Some(b"GZ-BYTES"),
        br: Some(b"BR-BYTES"),
        etag: "fedcba9876543210fedcba9876543210",
    },
];

async fn body_of(r: axum::response::Response) -> (StatusCode, HeaderMap, Vec<u8>) {
    let s = r.status();
    let h = r.headers().clone();
    let b = r.into_body().collect().await.unwrap().to_bytes().to_vec();
    (s, h, b)
}

fn hdr(pairs: &[(&'static str, &'static str)]) -> HeaderMap {
    let mut h = HeaderMap::new();
    for (k, v) in pairs {
        h.append(*k, HeaderValue::from_static(v));
    }
    h
}

#[tokio::test]
async fn index_has_a_strong_etag_no_cache_and_a_strict_policy() {
    let (s, h, b) = body_of(serve(FILES, &Method::GET, "/", &HeaderMap::new())).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h["etag"], "\"0123456789abcdef0123456789abcdef\"");
    assert_eq!(h["cache-control"], "no-cache");
    assert_eq!(h["content-security-policy"], PAGE_CSP);
    assert!(PAGE_CSP.contains("frame-ancestors 'none'"));
    assert_eq!(h["x-content-type-options"], "nosniff");
    assert_eq!(b, FILES[0].body);
    let (s2, _, b2) = body_of(serve(FILES, &Method::GET, "/index.html", &HeaderMap::new())).await;
    assert_eq!((s2, b2), (StatusCode::OK, b));
}

#[tokio::test]
async fn a_matching_if_none_match_gives_304_with_no_body() {
    let h = hdr(&[("if-none-match", "\"0123456789abcdef0123456789abcdef\"")]);
    let (s, hh, b) = body_of(serve(FILES, &Method::GET, "/", &h)).await;
    assert_eq!(s, StatusCode::NOT_MODIFIED);
    assert!(b.is_empty());
    assert_eq!(hh["etag"], "\"0123456789abcdef0123456789abcdef\"");
    let h = hdr(&[("if-none-match", "\"something-else\"")]);
    let (s, _, _) = body_of(serve(FILES, &Method::GET, "/", &h)).await;
    assert_eq!(s, StatusCode::OK);
}

#[tokio::test]
async fn hashed_assets_are_immutable_for_a_year() {
    let (s, h, _) = body_of(serve(
        FILES,
        &Method::GET,
        "/assets/app-1a2b3c.js",
        &HeaderMap::new(),
    ))
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h["cache-control"], "public, max-age=31536000, immutable");
    assert_eq!(h["content-type"], "text/javascript; charset=utf-8");
}

#[tokio::test]
async fn precompressed_files_follow_accept_encoding() {
    let p = "/assets/app-1a2b3c.js";
    for (ae, want, coding) in [
        ("br, gzip", &b"BR-BYTES"[..], Some("br")),
        ("gzip", &b"GZ-BYTES"[..], Some("gzip")),
        ("br;q=0, gzip", &b"GZ-BYTES"[..], Some("gzip")),
        ("identity", &b"console.log('plain')"[..], None),
    ] {
        let mut h = HeaderMap::new();
        h.insert("accept-encoding", HeaderValue::from_str(ae).unwrap());
        let (s, hh, b) = body_of(serve(FILES, &Method::GET, p, &h)).await;
        assert_eq!(s, StatusCode::OK, "{ae}");
        assert_eq!(b, want, "{ae}");
        assert_eq!(
            hh.get("content-encoding").map(|v| v.to_str().unwrap()),
            coding,
            "{ae}"
        );
        assert_eq!(hh["vary"], "Accept-Encoding", "{ae}");
    }
    let (_, _, b) = body_of(serve(FILES, &Method::GET, p, &HeaderMap::new())).await;
    assert_eq!(b, b"console.log('plain')");
}

#[tokio::test]
async fn unknown_files_and_other_methods_are_refused() {
    for p in [
        "/nope.js",
        "/assets/../index.html",
        "/%2e%2e/etc/passwd",
        "/assets/",
    ] {
        let (s, _, _) = body_of(serve(FILES, &Method::GET, p, &HeaderMap::new())).await;
        assert_eq!(s, StatusCode::NOT_FOUND, "{p}");
    }
    let (s, _, _) = body_of(serve(FILES, &Method::POST, "/", &HeaderMap::new())).await;
    assert_eq!(s, StatusCode::METHOD_NOT_ALLOWED);
    let (s, h, b) = body_of(serve(FILES, &Method::HEAD, "/", &HeaderMap::new())).await;
    assert_eq!(s, StatusCode::OK);
    assert!(b.is_empty());
    assert_eq!(h["content-length"], FILES[0].body.len().to_string());
}

fn app() -> (axum::Router, tempfile::TempDir) {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(aib_store::Store::open(&dir.path().join("db.sqlite")).unwrap());
    let cfg = GuardConfig::new(PORT, false, vec![], Token::from_test_value(TOKEN));
    (
        router(
            AppState {
                sessions: aibd::sessions::Sessions::new(
                    store.clone(),
                    None,
                    aibd::platform::default_spawn(),
                    8,
                    std::env::temp_dir(),
                ),
                store,
                lineage: Arc::new(aibd::lineage::Lineage::disabled("test")),
                session_policy: Arc::new(Default::default()),
            },
            Arc::new(cfg),
        ),
        dir,
    )
}

async fn get(path: &str, bearer: bool) -> (StatusCode, HeaderMap, String) {
    let (a, _d) = app();
    let mut b = Request::builder()
        .uri(path)
        .header("host", format!("127.0.0.1:{PORT}"));
    if bearer {
        b = b.header("authorization", format!("Bearer {TOKEN}"));
    }
    let r = a.oneshot(b.body(Body::empty()).unwrap()).await.unwrap();
    let (s, h, body) = body_of(r).await;
    (s, h, String::from_utf8_lossy(&body).into_owned())
}

#[cfg(not(feature = "web"))]
#[tokio::test]
async fn without_the_web_feature_pages_are_404_web_not_built_and_never_hold_the_token() {
    // this test binary is built without `--features web`
    for p in ["/", "/index.html", "/assets/app.js"] {
        let (s, h, body) = get(p, false).await;
        assert_eq!(s, StatusCode::NOT_FOUND, "{p}");
        assert!(body.contains("web_not_built"), "{p}: {body}");
        assert!(!body.contains(TOKEN), "{p}");
        assert!(h.get("access-control-allow-origin").is_none());
    }
}

/// Built with `--features web` (CI and the build-host run use a small fake dist).
#[cfg(feature = "web")]
#[tokio::test]
async fn the_web_build_serves_the_embedded_index_without_the_token() {
    assert!(aibd::static_files::ASSETS
        .iter()
        .any(|a| a.path == "index.html"));
    let (s, h, body) = get("/", false).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h["cache-control"], "no-cache");
    assert!(!body.contains(TOKEN));
}

#[tokio::test]
async fn api_paths_keep_the_bearer_rule_and_answer_no_store() {
    // an unknown API path is still unauthorized without the token, not a static 404
    let (s, h, body) = get("/api/v1/nope", false).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED);
    assert_eq!(h["cache-control"], "no-store");
    assert!(body.contains("unauthorized"));
    let (s, h, body) = get("/api/v1/nope", true).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert!(body.contains("\"not_found\""));
    assert_eq!(h["cache-control"], "no-store");
    // every API answer, success included
    for p in ["/api/v1/health", "/api/v1/events", "/api/v1/metrics"] {
        let (s, h, _) = get(p, true).await;
        assert_eq!(s, StatusCode::OK, "{p}");
        assert_eq!(h["cache-control"], "no-store", "{p}");
    }
    // static cache rules never reach the API
    let (_, h, _) = get("/", false).await;
    assert_ne!(
        h.get("cache-control").map(|v| v.to_str().unwrap()),
        Some("public, max-age=31536000, immutable")
    );
}

#[tokio::test]
async fn a_static_path_still_refuses_a_token_in_the_url_and_a_foreign_host() {
    let (s, _, _) = get(&format!("/?token={TOKEN}"), false).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (a, _d) = app();
    let r = a
        .oneshot(
            Request::builder()
                .uri("/")
                .header("host", "evil.example:47772")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
}

/// a9136 A3 L1: paths outside `/api/` skip the bearer check, so every mounted route must
/// sit under `/api/`. `ROUTES` is the only list the router mounts from.
#[test]
fn every_route_sits_under_api_and_has_handlers() {
    for p in aibd::guard::ROUTES {
        assert!(
            aibd::guard::is_api(p),
            "{p} is outside /api/, so it would answer without the token"
        );
        assert!(aibd::api::handlers(p).is_some(), "{p} has no handlers");
    }
    for p in aibd::guard::LOGGED_ROUTES {
        assert!(
            aibd::guard::ROUTES.contains(p),
            "{p} is logged but not a route"
        );
    }
}

#[tokio::test]
async fn every_route_but_the_public_ones_wants_the_token() {
    for p in aibd::guard::ROUTES {
        if *p == aibd::guard::HEALTH || *p == aibd::guard::PAIR_REDEEM {
            continue;
        }
        let path = p.replace("{id}", "x");
        let (s, _, body) = get(&path, false).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED, "{path}: {body}");
    }
}

/// 1ced7 A3 L2: each content coding is its own representation with its own strong ETag;
/// If-None-Match naming any of them gives 304.
#[tokio::test]
async fn each_encoding_has_its_own_etag_and_any_of_them_gives_304() {
    let gz = hdr(&[("accept-encoding", "gzip")]);
    let (s, h, b) = body_of(serve(FILES, &Method::GET, "/", &gz)).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(b, b"GZ-INDEX");
    assert_eq!(h["content-encoding"], "gzip");
    assert_eq!(h["etag"], "\"0123456789abcdef0123456789abcdef-gz\"");
    let (_, h, _) = body_of(serve(FILES, &Method::GET, "/", &HeaderMap::new())).await;
    assert_eq!(h["etag"], "\"0123456789abcdef0123456789abcdef\"");
    // a cached plain copy, now asking with gzip
    let h = hdr(&[
        ("accept-encoding", "gzip"),
        ("if-none-match", "\"0123456789abcdef0123456789abcdef\""),
    ]);
    let (s, hh, b) = body_of(serve(FILES, &Method::GET, "/", &h)).await;
    assert_eq!(s, StatusCode::NOT_MODIFIED);
    assert!(b.is_empty());
    assert_eq!(hh["etag"], "\"0123456789abcdef0123456789abcdef-gz\"");
    // a cached gzip copy, now asking without
    let h = hdr(&[("if-none-match", "\"0123456789abcdef0123456789abcdef-gz\"")]);
    let (s, _, _) = body_of(serve(FILES, &Method::GET, "/", &h)).await;
    assert_eq!(s, StatusCode::NOT_MODIFIED);
}

/// 1ced7 A3 L1 / L3: every HTML file (not just index.html) gets the page policy, which
/// takes Vite's inlined `data:` fonts.
#[tokio::test]
async fn every_html_file_gets_the_page_policy() {
    assert!(PAGE_CSP.contains("font-src 'self' data:"), "{PAGE_CSP}");
    let (s, h, _) = body_of(serve(FILES, &Method::GET, "/pair.html", &HeaderMap::new())).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(h["content-security-policy"], PAGE_CSP);
    assert_eq!(h["x-frame-options"], "DENY");
    let (_, h, _) = body_of(serve(
        FILES,
        &Method::GET,
        "/assets/app-1a2b3c.js",
        &HeaderMap::new(),
    ))
    .await;
    assert!(h.get("content-security-policy").is_none());
}

/// 1ced7 A3 M2: every API answer (refusals included) tells the page which aibd it talks
/// to, so an open page reloads after an upgrade.
#[tokio::test]
async fn api_answers_carry_the_build() {
    assert!(
        BUILD.starts_with(concat!(env!("CARGO_PKG_VERSION"), "+")),
        "{BUILD}"
    );
    for (p, bearer) in [
        ("/api/v1/health", true),
        ("/api/v1/events", true),
        ("/api/v1/events", false),
        ("/api/v1/nope", true),
    ] {
        let (_, h, _) = get(p, bearer).await;
        assert_eq!(h["x-aibd-build"], BUILD, "{p} {bearer}");
    }
}
