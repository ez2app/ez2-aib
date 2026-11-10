#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Pairing over the router (ADR 0006 §7 required tests that do not involve the P2 file;
//! no network). Each test names the attack or the rule; the knives in aibd.yml each turn
//! exactly one of them red, and `the_whole_pairing_flow_unlocks_only_after_confirm` is the
//! positive path, so "refuse everything" cannot pass.

use aibd::api::{router, AppState};
use aibd::guard::GuardConfig;
use aibd::pairing::{Clock, ConfirmChannel, Pairing, CONFIRM_WINDOW, MAX_FAILURES};
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
            Some("null"),
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

fn code_of(r: &R) -> &str {
    r.json["code"].as_str().unwrap_or("")
}

#[tokio::test]
async fn the_whole_pairing_flow_unlocks_only_after_confirm() {
    let t = app();
    let (id, code, cred) = t.paired().await;
    // the page and the terminal show the same short code
    let page = t
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(page.status, StatusCode::OK);
    assert_eq!(page.json["state"], "awaiting_confirmation");
    let short = t.short_code(&id).await;
    assert_eq!(page.json["short_code"], short.as_str());
    assert!(!code.is_empty());
    let r = t.confirm(&id, &short).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.text);
    let ev = t.call("GET", "/api/v1/events", Some(&cred), None).await;
    assert_eq!(ev.status, StatusCode::OK, "{}", ev.text);
    let st = t
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(st.json["state"], "confirmed");
    assert!(st.json.get("short_code").is_none());
}

#[tokio::test]
async fn reading_the_api_without_pairing_is_refused() {
    let t = app();
    for bearer in [None, Some("made-up-credential")] {
        for p in ["/api/v1/events", "/api/v1/sessions", "/api/v1/pair/status"] {
            let r = t.call("GET", p, bearer, None).await;
            assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{p}");
        }
    }
}

#[tokio::test]
async fn an_unconfirmed_credential_gets_only_the_allow_list() {
    let t = app();
    let (_id, _c, cred) = t.paired().await;
    for (m, p, body) in [
        ("GET", "/api/v1/events", None),
        ("GET", "/api/v1/metrics", None),
        ("GET", "/api/v1/sessions", None),
        ("GET", "/api/v1/seats", None),
        ("POST", "/api/v1/seats", Some("{}")),
        ("POST", "/api/v1/sessions", Some("{}")),
        ("GET", "/api/v1/nope", None),
    ] {
        let r = t.call(m, p, Some(&cred), body).await;
        assert_eq!(r.status, StatusCode::FORBIDDEN, "{m} {p}");
        assert_eq!(code_of(&r), "pairing_unconfirmed", "{m} {p}");
    }
    let h = t.call("GET", "/api/v1/health", Some(&cred), None).await;
    assert_eq!(h.status, StatusCode::OK);
    let s = t
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(s.status, StatusCode::OK);
}

#[tokio::test]
async fn a_page_credential_never_reaches_the_code_endpoints() {
    let t = app();
    let (id, _c, cred) = t.paired().await;
    let short = t.short_code(&id).await;
    // a page cannot confirm itself, not even with the right short code
    let r = t
        .call(
            "POST",
            &format!("/api/v1/pair/codes/{id}/confirm"),
            Some(&cred),
            Some(&format!("{{\"short_code\":\"{short}\"}}")),
        )
        .await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    // nor read the short code from the terminal side, nor make codes
    for (m, p) in [
        ("GET", format!("/api/v1/pair/codes/{id}")),
        ("POST", "/api/v1/pair/codes".to_string()),
        ("DELETE", format!("/api/v1/pair/codes/{id}")),
    ] {
        let r = t.call(m, &p, Some(&cred), None).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{m} {p}");
    }
    // still the same after confirmation through the right channel
    assert_eq!(t.confirm(&id, &short).await.status, StatusCode::OK);
    let r = t
        .call("POST", "/api/v1/pair/codes", Some(&cred), None)
        .await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_second_redemption_is_409_and_revokes_the_first_credential() {
    let t = app();
    let (_id, code, cred) = t.paired().await;
    // a reload / bfcache / late re-send is the same form again
    let r = t.redeem(&code).await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    assert_eq!(code_of(&r), "pairing_code_reused");
    assert!(!r.text.contains("setItem"));
    let s = t
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(s.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn an_attacker_who_redeems_first_never_unlocks_by_waiting() {
    let t = app();
    let (id, _code, cred) = t.paired().await;
    t.advance(CONFIRM_WINDOW - Duration::from_secs(1));
    let r = t.call("GET", "/api/v1/events", Some(&cred), None).await;
    assert_eq!(code_of(&r), "pairing_unconfirmed");
    t.advance(Duration::from_secs(1));
    // the window ran out: revoked, not confirmed
    let r = t.call("GET", "/api/v1/events", Some(&cred), None).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    let v = t
        .call(
            "GET",
            &format!("/api/v1/pair/codes/{id}"),
            Some(TOKEN),
            None,
        )
        .await;
    assert_eq!(v.json["state"], "revoked");
}

#[tokio::test]
async fn a_wrong_short_code_revokes_and_cannot_be_retried() {
    let t = app();
    let (id, _code, cred) = t.paired().await;
    let short = t.short_code(&id).await;
    let wrong = if short == "000000" {
        "000001"
    } else {
        "000000"
    };
    let r = t.confirm(&id, wrong).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    assert_eq!(code_of(&r), "pairing_failed");
    assert_eq!(
        t.confirm(&id, &short).await.status,
        StatusCode::UNAUTHORIZED
    );
    let r = t
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn cancel_in_the_terminal_and_revoke_on_the_page_both_end_the_credential() {
    let t = app();
    let (id, _code, cred) = t.paired().await;
    let r = t
        .call(
            "DELETE",
            &format!("/api/v1/pair/codes/{id}"),
            Some(TOKEN),
            None,
        )
        .await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    let short = t.short_code(&id).await;
    assert!(short.is_empty() || t.confirm(&id, &short).await.status != StatusCode::OK);
    assert_eq!(
        t.call("GET", "/api/v1/pair/status", Some(&cred), None)
            .await
            .status,
        StatusCode::UNAUTHORIZED
    );
    let (_id2, _c2, cred2) = t.paired().await;
    let r = t
        .call("POST", "/api/v1/pair/revoke", Some(&cred2), None)
        .await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    assert_eq!(
        t.call("GET", "/api/v1/pair/status", Some(&cred2), None)
            .await
            .status,
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn wrong_guesses_hit_the_rate_limit_but_not_an_unrelated_session() {
    let t = app();
    let (id, _code, cred) = t.paired().await;
    let short = t.short_code(&id).await;
    assert_eq!(t.confirm(&id, &short).await.status, StatusCode::OK);
    for _ in 0..MAX_FAILURES {
        let r = t.redeem("00000-00000").await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    }
    let (_id2, code2) = t.new_code().await;
    let r = t.redeem(&code2).await;
    assert_eq!(r.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(code_of(&r), "pairing_rate_limited");
    let ev = t.call("GET", "/api/v1/events", Some(&cred), None).await;
    assert_eq!(ev.status, StatusCode::OK);
}

#[tokio::test]
async fn every_redeem_failure_says_the_same_thing() {
    let t = app();
    let (_id, used, _cred) = t.paired().await;
    let mut seen = std::collections::BTreeSet::new();
    for body in [
        "code=".to_string(),
        "code=00000-00000".to_string(),
        "code=not-a-code!".to_string(),
        format!("code={used}"),
        "x=1".to_string(),
        "code=a&code=b".to_string(),
    ] {
        let r = t
            .redeem_raw(Some("null"), "application/x-www-form-urlencoded", &body)
            .await;
        assert_ne!(r.status, StatusCode::OK, "{body}");
        seen.insert(r.json["message"].as_str().unwrap().to_string());
    }
    assert_eq!(seen.len(), 1, "{seen:?}");
}

#[tokio::test]
async fn redeem_takes_only_a_small_form_from_null_or_its_own_origin() {
    let t = app();
    let (_id, code) = t.new_code().await;
    let form = format!("code={code}");
    let ctype = "application/x-www-form-urlencoded";
    // a hostile page on another local port, a foreign site, no Origin at all
    for o in [
        Some("http://127.0.0.1:1"),
        Some("http://evil.example"),
        Some("http://localhost:1"),
        None,
    ] {
        let r = t.redeem_raw(o, ctype, &form).await;
        assert_eq!(r.status, StatusCode::FORBIDDEN, "{o:?}");
        assert_eq!(code_of(&r), "origin_not_allowed");
    }
    // JSON or another content type
    let r = t
        .redeem_raw(
            Some("null"),
            "application/json",
            &format!("{{\"code\":\"{code}\"}}"),
        )
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    // over 1 KiB
    let big = format!("code={code}&pad={}", "x".repeat(1100));
    let r = t.redeem_raw(Some("null"), ctype, &big).await;
    assert_eq!(r.status, StatusCode::PAYLOAD_TOO_LARGE);
    // a GET, with or without the code in the query
    let get = |uri: String| {
        Request::builder()
            .method("GET")
            .uri(uri)
            .header("host", format!("127.0.0.1:{PORT}"))
            .header("origin", "null")
            .body(Body::empty())
            .unwrap()
    };
    let r = t.send(get("/api/v1/pair/redeem".into())).await;
    assert_eq!(r.status, StatusCode::METHOD_NOT_ALLOWED);
    let r = t
        .send(get(format!("/api/v1/pair/redeem?code={code}")))
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    assert_eq!(code_of(&r), "token_in_url");
    // none of that used the code up: aibd's own origin still redeems it
    let r = t.redeem_raw(Some(OWN), ctype, &form).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.text);
}

#[tokio::test]
async fn null_origin_is_refused_everywhere_but_redeem() {
    // even when someone lists "null" as an allowed origin: it identifies nothing
    let t = app_with(&["null"]);
    for (m, p) in [
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/events"),
        ("POST", "/api/v1/pair/codes"),
        ("GET", "/api/v1/pair/status"),
    ] {
        let r = t
            .send(
                Request::builder()
                    .method(m)
                    .uri(p)
                    .header("host", format!("127.0.0.1:{PORT}"))
                    .header("origin", "null")
                    .header("authorization", format!("Bearer {TOKEN}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(r.status, StatusCode::FORBIDDEN, "{m} {p}");
    }
}

#[tokio::test]
async fn redeem_still_checks_host_against_dns_rebinding() {
    let t = app();
    let (_id, code) = t.new_code().await;
    let r = t
        .send(
            Request::builder()
                .method("POST")
                .uri("/api/v1/pair/redeem")
                .header("host", "evil.example:47771")
                .header("origin", "null")
                .header("content-type", "application/x-www-form-urlencoded")
                .body(Body::from(format!("code={code}")))
                .unwrap(),
        )
        .await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    assert_eq!(code_of(&r), "host_not_allowed");
}

#[tokio::test]
async fn the_exchange_page_is_not_cached_and_loads_nothing() {
    let t = app();
    let (_id, code) = t.new_code().await;
    let r = t.redeem(&code).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.headers["cache-control"], "no-store");
    assert_eq!(r.headers["referrer-policy"], "no-referrer");
    let csp = r.headers["content-security-policy"].to_str().unwrap();
    assert!(csp.contains("default-src 'none'") && csp.contains("frame-ancestors 'none'"));
    assert!(r.text.contains("location.replace('/')"));
    assert!(!r.text.contains("src="));
    // the code response is not cached either
    let c = t
        .call("POST", "/api/v1/pair/codes", Some(TOKEN), None)
        .await;
    assert_eq!(c.headers["cache-control"], "no-store");
}

#[tokio::test]
async fn credentials_and_codes_stay_out_of_urls_and_logs() {
    let t = app();
    let (id, code, cred) = t.paired().await;
    // any query on a pairing path is refused, even a harmless-looking one
    let r = t
        .call("GET", "/api/v1/pair/status?x=1", Some(&cred), None)
        .await;
    assert_eq!(code_of(&r), "token_in_url");
    // a path with an id is logged as the placeholder, never verbatim
    assert_eq!(
        aibd::guard::loggable_path(&format!("/api/v1/pair/codes/{id}")),
        aibd::guard::UNMATCHED_PATH
    );
    assert_eq!(
        aibd::guard::loggable_path("/api/v1/pair/redeem"),
        "/api/v1/pair/redeem"
    );
    // error bodies never echo the code or the credential
    let r = t.redeem(&code).await;
    assert!(!r.text.contains(&code) && !r.text.contains(&cred));
}

#[tokio::test]
async fn pairing_is_off_unless_turned_on() {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(aib_store::Store::open(&dir.path().join("db.sqlite")).unwrap());
    let cfg = GuardConfig::new(PORT, false, vec![], Token::from_test_value(TOKEN));
    let t = T {
        app: router(
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
        clock: Arc::new(AtomicU64::new(0)),
        _dir: dir,
    };
    let r = t
        .call("POST", "/api/v1/pair/codes", Some(TOKEN), None)
        .await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    let m = t.call("GET", "/api/v1/metrics", Some(TOKEN), None).await;
    assert_eq!(m.json["pairing"]["channel"], "off");
}

#[tokio::test]
async fn failures_and_rate_limits_are_counted_in_metrics() {
    let t = app();
    for _ in 0..MAX_FAILURES + 1 {
        t.redeem("00000-00000").await;
    }
    let m = t.call("GET", "/api/v1/metrics", Some(TOKEN), None).await;
    assert_eq!(m.json["pairing"]["failures_total"], MAX_FAILURES as u64);
    assert_eq!(m.json["pairing"]["rate_limited_total"], 1);
}

#[tokio::test]
async fn the_start_up_token_keeps_full_access_and_has_no_page_status() {
    let t = app();
    let r = t.call("GET", "/api/v1/events", Some(TOKEN), None).await;
    assert_eq!(r.status, StatusCode::OK);
    let r = t
        .call("GET", "/api/v1/pair/status", Some(TOKEN), None)
        .await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
}
