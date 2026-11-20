#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(dead_code)] // the harness is tests/pairing.rs's, not every helper is used here

//! SPEC §19b / §19c (one line into the workbench). Its own test binary: it sets aibd's data
//! folder (a process-wide once) where the box choice is kept, and the other pairing tests
//! redeem codes too, which would write it. Harness copied from tests/pairing.rs.

use aibd::api::{router, AppState};
use aibd::guard::GuardConfig;
use aibd::pairing::{Clock, ConfirmChannel, Pairing};
use aibd::token::Token;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tower::ServiceExt;

const PORT: u16 = 47771;
const TOKEN: &str = "test-token-value-0123456789abcdefghijklmnopq";
const OWN: &str = "http://127.0.0.1:47771";

struct T {
    app: axum::Router,
    clock: Arc<AtomicU64>,
    _dir: tempfile::TempDir,
}

fn app() -> T {
    app_with(&[])
}

fn app_with(origins: &[&str]) -> T {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(aib_store::Store::open(&dir.path().join("db.sqlite")).unwrap());
    let base = Instant::now();
    let off = Arc::new(AtomicU64::new(0));
    let o = off.clone();
    let clock: Clock = Arc::new(move || base + Duration::from_millis(o.load(Ordering::SeqCst)));
    let cfg = GuardConfig::new(
        PORT,
        false,
        origins.iter().map(|s| s.to_string()).collect(),
        Token::from_test_value(TOKEN),
    )
    .with_pairing(Arc::new(
        Pairing::with_clock(clock).with_channel(ConfirmChannel::TerminalUnverified),
    ));
    let app = router(
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
            thermal: None,
            personas: std::sync::Arc::new(aibd::personas::FixedCards::default()),
            contacts: std::sync::Arc::new(aibd::contacts::Book::new(
                &std::env::temp_dir().join("aibd-test-no-contacts"),
            )),
            messages: aibd::messages::scratch_db(),
        },
        Arc::new(cfg),
    );
    T {
        app,
        clock: off,
        _dir: dir,
    }
}

struct R {
    status: StatusCode,
    json: serde_json::Value,
    text: String,
    headers: axum::http::HeaderMap,
}

impl T {
    fn advance(&self, d: Duration) {
        self.clock.fetch_add(d.as_millis() as u64, Ordering::SeqCst);
    }

    async fn send(&self, r: Request<Body>) -> R {
        let resp = self.app.clone().oneshot(r).await.unwrap();
        let status = resp.status();
        let headers = resp.headers().clone();
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        R {
            status,
            json: serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null),
            text: String::from_utf8_lossy(&bytes).into_owned(),
            headers,
        }
    }

    async fn call(&self, method: &str, path: &str, bearer: Option<&str>, body: Option<&str>) -> R {
        let mut b = Request::builder()
            .method(method)
            .uri(path)
            .header("host", format!("127.0.0.1:{PORT}"));
        if let Some(t) = bearer {
            b = b.header("authorization", format!("Bearer {t}"));
        }
        let body = match body {
            Some(s) => {
                b = b.header("content-type", "application/json");
                Body::from(s.to_string())
            }
            None => Body::empty(),
        };
        self.send(b.body(body).unwrap()).await
    }

    async fn redeem_raw(&self, origin: Option<&str>, ctype: &str, body: &str) -> R {
        let mut b = Request::builder()
            .method("POST")
            .uri("/api/v1/pair/redeem")
            .header("host", format!("127.0.0.1:{PORT}"))
            .header("content-type", ctype);
        if let Some(o) = origin {
            b = b.header("origin", o);
        }
        self.send(b.body(Body::from(body.to_string())).unwrap())
            .await
    }

    async fn redeem(&self, code: &str) -> R {
        self.redeem_raw(
            Some(OWN),
            "application/x-www-form-urlencoded",
            &format!("code={code}"),
        )
        .await
    }

    /// `(pair_id, code)`
    async fn new_code(&self) -> (String, String) {
        let r = self
            .call("POST", "/api/v1/pair/codes", Some(TOKEN), None)
            .await;
        assert_eq!(r.status, StatusCode::CREATED, "{}", r.text);
        (
            r.json["pair_id"].as_str().unwrap().to_string(),
            r.json["code"].as_str().unwrap().to_string(),
        )
    }

    /// `(pair_id, code, credential)` of a redeemed, unconfirmed pairing.
    async fn paired(&self) -> (String, String, String) {
        let (id, code) = self.new_code().await;
        let r = self.redeem(&code).await;
        assert_eq!(r.status, StatusCode::OK, "{}", r.text);
        (id, code, credential_of(&r.text))
    }

    async fn short_code(&self, id: &str) -> String {
        let r = self
            .call(
                "GET",
                &format!("/api/v1/pair/codes/{id}"),
                Some(TOKEN),
                None,
            )
            .await;
        r.json["short_code"].as_str().unwrap_or("").to_string()
    }

    async fn confirm(&self, id: &str, short: &str) -> R {
        self.call(
            "POST",
            &format!("/api/v1/pair/codes/{id}/confirm"),
            Some(TOKEN),
            Some(&format!("{{\"short_code\":\"{short}\"}}")),
        )
        .await
    }
}

fn credential_of(html: &str) -> String {
    let key = "setItem('aib.page_credential','";
    let i = html.find(key).unwrap() + key.len();
    html[i..].split('\'').next().unwrap().to_string()
}

/// SPEC §19b / §19c (Andy 11-18 20:1x / 20:3x): the box under the code ticked ⇒ typing the code
/// confirms, no y at the terminal; unticked ⇒ the y is still asked. The terminal that made the
/// code gets its own credential once; the same code again revokes both; the box choice is kept
/// only after a right code.
#[tokio::test]
async fn a_ticked_box_confirms_on_the_page_and_the_terminal_is_paired_once() {
    let t = app();
    let dir = tempfile::tempdir().unwrap();
    aibd::workspace::set_data_dir(dir.path().to_path_buf());
    let prefs = || t.call("GET", "/api/v1/pair/prefs", None, None);
    let form = "application/x-www-form-urlencoded";
    assert_eq!(
        prefs().await.json["confirm_in_page"],
        true,
        "ticked when none kept"
    );

    let r = t
        .call(
            "POST",
            "/api/v1/pair/codes",
            Some(TOKEN),
            Some(r#"{"terminal": true}"#),
        )
        .await;
    assert_eq!(r.status, StatusCode::CREATED, "{}", r.text);
    let id = r.json["pair_id"].as_str().unwrap().to_string();
    let code = r.json["code"].as_str().unwrap().to_string();
    let r = t
        .redeem_raw(Some(OWN), form, &format!("code={code}&confirm_in_page=1"))
        .await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.text);
    let page = credential_of(&r.text);
    let s = t
        .call("GET", "/api/v1/pair/status", Some(&page), None)
        .await;
    assert_eq!(s.json["state"], "confirmed", "reason: box_ignored");
    let codes = format!("/api/v1/pair/codes/{id}");
    let v = t.call("GET", &codes, Some(TOKEN), None).await;
    let term = v.json["terminal_credential"].as_str().unwrap().to_string();
    assert_ne!(term, page);
    let again = t.call("GET", &codes, Some(TOKEN), None).await;
    assert!(
        again.json.get("terminal_credential").is_none(),
        "reason: terminal_twice"
    );
    let s = t
        .call("GET", "/api/v1/pair/status", Some(&term), None)
        .await;
    assert_eq!(s.json["state"], "confirmed");
    // the same code a second time: refused, and both credentials end
    let r = t
        .redeem_raw(Some(OWN), form, &format!("code={code}&confirm_in_page=1"))
        .await;
    assert_ne!(r.status, StatusCode::OK);
    for c in [&page, &term] {
        let s = t.call("GET", "/api/v1/pair/status", Some(c), None).await;
        assert_eq!(
            s.status,
            StatusCode::UNAUTHORIZED,
            "reason: reuse_kept_cred"
        );
    }

    // unticked: the terminal's y is still asked, and the choice is kept
    let (_, code2) = t.new_code().await;
    let r = t
        .redeem_raw(Some(OWN), form, &format!("code={code2}&confirm_in_page=0"))
        .await;
    let p2 = credential_of(&r.text);
    let s = t.call("GET", "/api/v1/pair/status", Some(&p2), None).await;
    assert_eq!(
        s.json["state"], "awaiting_confirmation",
        "reason: unticked_confirmed"
    );
    assert_eq!(
        prefs().await.json["confirm_in_page"],
        false,
        "reason: box_not_kept"
    );
    // a wrong code changes nothing kept
    let r = t
        .redeem_raw(Some(OWN), form, "code=ZZZZZ-ZZZZZ&confirm_in_page=1")
        .await;
    assert_ne!(r.status, StatusCode::OK);
    assert_eq!(
        prefs().await.json["confirm_in_page"],
        false,
        "reason: wrong_code_kept"
    );
    // the box is taken only from aibd's own page
    let (_, code3) = t.new_code().await;
    let r = t
        .redeem_raw(
            Some("http://evil.example"),
            form,
            &format!("code={code3}&confirm_in_page=1"),
        )
        .await;
    assert_eq!(r.status, StatusCode::FORBIDDEN, "{}", r.text);
}
