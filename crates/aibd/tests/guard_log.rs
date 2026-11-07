#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Log-leak test, alone in its own test binary: `tracing` caches callsite
//! interest per process, so another test running first on another thread can
//! make a thread-local subscriber miss events.

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
            AppState {
                store: Arc::new(store),
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

#[derive(Clone, Default)]
struct Buf(std::sync::Arc<std::sync::Mutex<Vec<u8>>>);
impl std::io::Write for Buf {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(b);
        Ok(b.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for Buf {
    type Writer = Buf;
    fn make_writer(&'a self) -> Buf {
        self.clone()
    }
}

#[tokio::test]
async fn logs_never_contain_token_raw_method_path_or_query() {
    let buf = Buf::default();
    let sub = tracing_subscriber::fmt()
        .json()
        .with_writer(buf.clone())
        .with_max_level(tracing::Level::TRACE)
        .finish();
    let _guard = tracing::subscriber::set_default(sub);
    let enc: String = TOKEN.bytes().map(|b| format!("%{b:02x}")).collect();
    let attempts = [
        ("GET", format!("/api/v1/{TOKEN}"), true),
        ("GET", format!("/api/v1/{TOKEN}"), false),
        ("GET", format!("/api/v1/events?token={TOKEN}"), true),
        ("GET", format!("/api/v1/x?y={enc}"), true),
        (
            "GET",
            "/api/v1/secret-looking-unknown-route".to_string(),
            true,
        ),
        ("GET", "/api/v1/events?cursor=0".to_string(), true),
        // a method is an arbitrary token on the wire
        (TOKEN, "/api/v1/health".to_string(), false),
        (TOKEN, "/api/v1/events".to_string(), true),
        ("get", "/api/v1/health".to_string(), false),
    ];
    for (m, u, with_auth) in &attempts {
        let (a, _d) = app(&[]);
        let mut b = req(u).method(*m);
        if *with_auth {
            b = b.header("authorization", format!("Bearer {TOKEN}"));
        }
        let _ = send(a, b.body(Body::empty()).unwrap()).await;
    }
    let log = String::from_utf8(buf.0.lock().unwrap().clone()).unwrap();
    assert!(!log.is_empty(), "no log captured");
    assert!(!log.contains(TOKEN), "token in log:\n{log}");
    assert!(!log.contains(&enc), "encoded token in log:\n{log}");
    assert!(
        !log.contains("secret-looking-unknown-route"),
        "raw unknown path in log:\n{log}"
    );
    assert!(!log.contains("cursor=0"), "query in log:\n{log}");
    assert!(log.contains("<unmatched>"));
    assert!(log.contains("\"method\":\"<other>\""), "{log}");
    assert!(log.contains("\"method\":\"GET\""), "{log}");
    assert!(log.contains("\"path\":\"/api/v1/events\""));
}
