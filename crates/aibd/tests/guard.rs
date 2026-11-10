#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Router-level security tests (no network). Each test names the attack.

use aibd::api::{router, AppState};
use aibd::guard::GuardConfig;
use aibd::token::Token;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

const PORT: u16 = 47770;
const TOKEN: &str = "test-token-value-0123456789abcdefghijklmnopq";

fn app(origins: &[&str]) -> (axum::Router, tempfile::TempDir) {
    let dir = tempfile::tempdir().unwrap();
    let store = aib_store::Store::open(&dir.path().join("db.sqlite")).unwrap();
    let cfg = GuardConfig::new(
        PORT,
        false,
        origins.iter().map(|s| s.to_string()).collect(),
        Token::from_test_value(TOKEN),
    );
    (
        router(
            {
                let store = Arc::new(store);
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
                }
            },
            Arc::new(cfg),
        ),
        dir,
    )
}

fn req(path: &str) -> axum::http::request::Builder {
    Request::builder()
        .uri(path)
        .header("host", format!("127.0.0.1:{PORT}"))
}

async fn send(
    app: axum::Router,
    r: Request<Body>,
) -> (StatusCode, serde_json::Value, axum::http::HeaderMap) {
    let resp = app.oneshot(r).await.unwrap();
    let status = resp.status();
    let headers = resp.headers().clone();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let json = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (status, json, headers)
}

#[tokio::test]
async fn health_is_public_and_minimal() {
    let (a, _d) = app(&[]);
    let (s, j, h) = send(a, req("/api/v1/health").body(Body::empty()).unwrap()).await;
    assert_eq!(s, StatusCode::OK);
    // only these fields, and no path (043dd S3: the data directory stays behind the token)
    let mut keys: Vec<&str> = j.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        ["api_version", "instance_id", "started_at", "status", "version"]
    );
    assert_eq!(j["status"], "ok");
    assert_eq!(j["api_version"], "v1");
    assert_eq!(j["version"], env!("CARGO_PKG_VERSION"));
    assert!(j["started_at"].as_u64().unwrap() > 1_700_000_000);
    let id = j["instance_id"].as_str().unwrap();
    assert_eq!(id.len(), 16);
    assert!(id.bytes().all(|b| b.is_ascii_hexdigit()));
    assert!(!j.to_string().contains('/'), "{j}");
    assert!(h.get("access-control-allow-origin").is_none());
}

#[tokio::test]
async fn dns_rebinding_host_is_refused_even_with_token() {
    for host in [
        "evil.example:47770",
        "127.0.0.1:1",
        "localhost",
        "0.0.0.0:47770",
        "127.0.0.1.nip.io:47770",
    ] {
        let (a, _d) = app(&[]);
        let r = Request::builder()
            .uri("/api/v1/events")
            .header("host", host)
            .header("authorization", format!("Bearer {TOKEN}"))
            .body(Body::empty())
            .unwrap();
        let (s, j, _) = send(a, r).await;
        assert_eq!(s, StatusCode::FORBIDDEN, "{host}");
        assert_eq!(j["code"], "host_not_allowed", "{host}");
        assert!(j["request_id"].as_str().unwrap().len() == 16);
    }
}

#[tokio::test]
async fn localhost_name_is_accepted() {
    let (a, _d) = app(&[]);
    let r = Request::builder()
        .uri("/api/v1/health")
        .header("host", format!("LOCALHOST:{PORT}"))
        .body(Body::empty())
        .unwrap();
    assert_eq!(send(a, r).await.0, StatusCode::OK);
}

#[tokio::test]
async fn missing_or_duplicate_host_is_refused() {
    let (a, _d) = app(&[]);
    let r = Request::builder()
        .uri("/api/v1/health")
        .body(Body::empty())
        .unwrap();
    assert_eq!(send(a, r).await.1["code"], "host_not_allowed");
    let (a, _d) = app(&[]);
    let r = req("/api/v1/health")
        .header("host", "evil.example")
        .body(Body::empty())
        .unwrap();
    assert_eq!(send(a, r).await.1["code"], "host_not_allowed");
}

#[tokio::test]
async fn foreign_origin_is_refused_even_with_token() {
    let (a, _d) = app(&["tauri://localhost"]);
    let r = req("/api/v1/events")
        .header("origin", "https://evil.example")
        .header("authorization", format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (s, j, _) = send(a, r).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert_eq!(j["code"], "origin_not_allowed");

    // "null" origin (sandboxed iframe, file://) is not special-cased.
    let (a, _d) = app(&[]);
    let r = req("/api/v1/health")
        .header("origin", "null")
        .body(Body::empty())
        .unwrap();
    assert_eq!(send(a, r).await.1["code"], "origin_not_allowed");
}

#[tokio::test]
async fn allowed_origin_passes_but_gets_no_cors_headers() {
    let (a, _d) = app(&["tauri://localhost"]);
    let r = req("/api/v1/events")
        .header("origin", "tauri://localhost")
        .header("authorization", format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (s, _, h) = send(a, r).await;
    assert_eq!(s, StatusCode::OK);
    assert!(h.get("access-control-allow-origin").is_none());
}

#[tokio::test]
async fn preflight_is_not_answered_with_cors() {
    let (a, _d) = app(&[]);
    let r = req("/api/v1/events")
        .method("OPTIONS")
        .header("origin", "https://evil.example")
        .header("access-control-request-method", "POST")
        .body(Body::empty())
        .unwrap();
    let (s, _, h) = send(a, r).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    assert!(h.get("access-control-allow-origin").is_none());
    assert!(h.get("access-control-allow-methods").is_none());
}

#[tokio::test]
async fn missing_wrong_or_non_bearer_token_is_unauthorized() {
    for auth in [
        None,
        Some("Bearer nope".to_string()),
        Some(format!("Basic {TOKEN}")),
        Some(format!("bearer{TOKEN}")),
    ] {
        let (a, _d) = app(&[]);
        let mut b = req("/api/v1/events");
        if let Some(v) = &auth {
            b = b.header("authorization", v);
        }
        let (s, j, _) = send(a, b.body(Body::empty()).unwrap()).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED, "{auth:?}");
        assert_eq!(j["code"], "unauthorized");
    }
}

#[tokio::test]
async fn token_in_url_is_refused_even_when_correct() {
    for q in [
        format!("/api/v1/events?token={TOKEN}"),
        format!("/api/v1/events?cursor=0&x={TOKEN}"),
        "/api/v1/events?access_token=whatever".to_string(),
        "/api/v1/health?Token=1".to_string(),
    ] {
        let (a, _d) = app(&[]);
        let r = req(&q)
            .header("authorization", format!("Bearer {TOKEN}"))
            .body(Body::empty())
            .unwrap();
        let (s, j, _) = send(a, r).await;
        assert_eq!(s, StatusCode::BAD_REQUEST, "{q}");
        assert_eq!(j["code"], "token_in_url", "{q}");
    }
}

#[tokio::test]
async fn authenticated_events_cursor_works() {
    let (a, _d) = app(&[]);
    let r = req("/api/v1/events?cursor=0&limit=10")
        .header("authorization", format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (s, j, _) = send(a, r).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(j, serde_json::json!({"events": [], "next_cursor": 0}));
}

#[tokio::test]
async fn unknown_route_still_needs_host_and_token() {
    let (a, _d) = app(&[]);
    let r = req("/api/v1/nope").body(Body::empty()).unwrap();
    assert_eq!(send(a, r).await.1["code"], "unauthorized");
    let (a, _d) = app(&[]);
    let r = req("/api/v1/nope")
        .header("authorization", format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (s, j, _) = send(a, r).await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert_eq!(j["code"], "not_found");
}

#[tokio::test]
async fn encoded_or_mixed_case_credential_names_are_refused() {
    for q in [
        "/api/v1/events?%74oken=x",
        "/api/v1/events?%54OKEN=x",
        "/api/v1/events?ToKeN=x",
        "/api/v1/events?Access_Token=x",
        "/api/v1/events?%2574oken=x",
        "/api/v1/events?api%5Fkey=x",
        "/api/v1/events?cursor=0&+token+=x",
    ] {
        let (a, _d) = app(&[]);
        let r = req(q)
            .header("authorization", format!("Bearer {TOKEN}"))
            .body(Body::empty())
            .unwrap();
        let (s, j, _) = send(a, r).await;
        assert_eq!(s, StatusCode::BAD_REQUEST, "{q}");
        assert_eq!(j["code"], "token_in_url", "{q}");
    }
}

#[tokio::test]
async fn encoded_token_value_in_query_or_path_is_refused() {
    let enc: String = TOKEN.bytes().map(|b| format!("%{b:02X}")).collect();
    let dbl: String = TOKEN.bytes().map(|b| format!("%25{b:02X}")).collect();
    for u in [
        format!("/api/v1/events?x={enc}"),
        format!("/api/v1/events?x={dbl}"),
        format!("/api/v1/{TOKEN}"),
        format!("/api/v1/{enc}"),
        format!("/api/v1/{dbl}"),
        format!("/api/v1/health/{TOKEN}"),
    ] {
        let (a, _d) = app(&[]);
        let r = req(&u)
            .header("authorization", format!("Bearer {TOKEN}"))
            .body(Body::empty())
            .unwrap();
        let (s, j, _) = send(a, r).await;
        assert_eq!(s, StatusCode::BAD_REQUEST, "{u}");
        assert_eq!(j["code"], "token_in_url", "{u}");
    }
}

#[tokio::test]
async fn metrics_needs_the_token_and_reports_lineage() {
    let (a, _d) = app(&[]);
    let (s, j, _) = send(a, req("/api/v1/metrics").body(Body::empty()).unwrap()).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED, "{j}");
    assert!(j.get("lineage").is_none(), "{j}");

    let (a, _d) = app(&[]);
    let r = req("/api/v1/metrics")
        .header("authorization", format!("Bearer {TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (s, j, _) = send(a, r).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        j,
        serde_json::json!({"lineage": {
            "enabled": false, "selfcheck": "test", "failures": 0, "orphan_candidates_extra": 0
        }, "pairing": {
            "channel": "off", "failures_total": 0, "rate_limited_total": 0,
            "confirm_failures_total": 0, "pairs": 0
        }})
    );
}
