#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! `Idempotency-Key` on the session writes on a real aibd (T81, ADR 0003): a retry
//! with the same key starts or stops nothing again, another request with it is a
//! 409, a refused request gives the key back, and an answer is replayed after a
//! restart. POSIX only; the sessions run the test-only `aib-fixture-tree`.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::allowlist::SessionPolicy;
use aibd::config::Config;
use aibd::server::{self, Started};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::{Duration, Instant};

mod common;

/// One test at a time, as in `seat_sessions.rs` (DECISION-614-held-lock: a held
/// spawn of another test can keep a copy of `aibd.lock` and a restart here would
/// see AlreadyRunning).
static SERIAL: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// The program these tests start: only the fixture, no interpreter (S55).
fn policy() -> SessionPolicy {
    common::fx_policy()
}

struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

async fn start(dir: &Path) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: 2,
        session_policy: policy(),
        persona_cards: None,
    };
    let h = tokio::spawn(async move {
        server::run(
            c,
            async {
                let _ = stop_rx.await;
            },
            move |s| {
                let _ = st_tx.send(s);
            },
        )
        .await
    });
    let st = match tokio::time::timeout(Duration::from_secs(20), st_rx)
        .await
        .unwrap()
    {
        Ok(st) => st,
        // the server ended before it said it started: show why instead of RecvError
        // bounded: a server that dropped the sender but still runs must not hang the test (ebb36 #683 N1)
        Err(_) => panic!(
            "aibd did not start: {:?}",
            tokio::time::timeout(Duration::from_secs(5), h).await
        ),
    };
    Daemon {
        addr: st.addr,
        token: std::fs::read_to_string(&st.token_path).unwrap(),
        stop: Some(stop_tx),
        h: Some(h),
    }
}

/// What came back: status, response head, JSON body.
struct Reply {
    code: u16,
    head: String,
    json: Value,
}

impl Reply {
    fn replayed(&self) -> bool {
        self.head
            .to_ascii_lowercase()
            .contains("idempotent-replayed: true")
    }
}

/// One raw HTTP/1.1 request (blocking); `key` = an `Idempotency-Key` header.
fn raw(
    addr: SocketAddr,
    token: &str,
    method: &str,
    path: &str,
    body: Option<&str>,
    key: Option<&str>,
) -> Reply {
    let port = addr.port();
    let mut req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\
         Authorization: Bearer {token}\r\n"
    );
    if let Some(k) = key {
        req += &format!("Idempotency-Key: {k}\r\n");
    }
    match body {
        Some(b) => {
            req += &format!(
                "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{b}",
                b.len()
            )
        }
        None => req += "Content-Length: 0\r\n\r\n",
    }
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    s.write_all(req.as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
    let (head, body) = out.split_once("\r\n\r\n").unwrap_or((out.as_str(), ""));
    Reply {
        code,
        head: head.to_string(),
        json: serde_json::from_str(body).unwrap_or(Value::Null),
    }
}

impl Daemon {
    async fn keyed(
        &self,
        method: &str,
        path: &str,
        body: Option<&str>,
        key: Option<&str>,
    ) -> Reply {
        let (addr, token) = (self.addr, self.token.clone());
        let (m, p, b, k) = (
            method.to_string(),
            path.to_string(),
            body.map(str::to_string),
            key.map(str::to_string),
        );
        tokio::task::spawn_blocking(move || raw(addr, &token, &m, &p, b.as_deref(), k.as_deref()))
            .await
            .unwrap()
    }

    async fn call(&self, method: &str, path: &str, body: Option<&str>) -> (u16, Value) {
        let r = self.keyed(method, path, body, None).await;
        (r.code, r.json)
    }

    /// How many sessions this aibd knows (`GET /api/v1/sessions`).
    async fn count(&self) -> usize {
        let (_, v) = self.call("GET", "/api/v1/sessions", None).await;
        v["sessions"].as_array().unwrap().len()
    }

    async fn seat(&self, name: &str) -> String {
        let (code, v) = self
            .call(
                "POST",
                "/api/v1/seats",
                Some(&json!({"name": name}).to_string()),
            )
            .await;
        assert_eq!(code, 201, "{v}");
        v["seat_id"].as_str().unwrap().to_string()
    }

    /// Wait until the session is no longer `running` (its root exits at once).
    async fn ended(&self, id: &str) -> Value {
        let end = Instant::now() + Duration::from_secs(30);
        loop {
            let (_, v) = self
                .call("GET", &format!("/api/v1/sessions/{id}"), None)
                .await;
            if v["state"] != "running" {
                return v;
            }
            assert!(Instant::now() < end, "still running: {v}");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

/// Ends at once with 0.
fn quick() -> String {
    common::fx_body(&["exit", "0"])
}
/// Ends at once with 1 (another request than `quick`).
fn other() -> String {
    common::fx_body(&["exit", "1"])
}
/// Stays until it is stopped (or its 60 s TTL).
fn long() -> String {
    common::fx_body(&["gc-hold"])
}
/// Ignores SIGTERM (one process, no child), so a stop takes its whole grace before
/// SIGKILL.
fn stubborn() -> String {
    common::fx_body(&["ignore-term"])
}

#[tokio::test(flavor = "multi_thread")]
async fn a_retry_with_the_same_key_starts_nothing_and_another_request_is_a_conflict() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let seat = d.seat("dev").await;
    let path = format!("/api/v1/seats/{seat}/sessions");
    let a = d
        .keyed("POST", &path, Some(&quick()), Some("start-1"))
        .await;
    assert_eq!(a.code, 201, "{}", a.json);
    assert!(!a.replayed(), "{}", a.head);
    let id = a.json["id"].as_str().unwrap().to_string();
    d.ended(&id).await;

    // the retry: the first status, the same session as it is now, nothing started
    let b = d
        .keyed("POST", &path, Some(&quick()), Some("start-1"))
        .await;
    assert_eq!(b.code, 201, "{}", b.json);
    assert!(b.replayed(), "{}", b.head);
    assert_eq!(b.json["id"], id.as_str());
    assert_ne!(
        b.json["state"], "running",
        "the session as it is now: {}",
        b.json
    );
    assert_eq!(d.count().await, 1);

    // the same key for another request
    let c = d
        .keyed("POST", &path, Some(&other()), Some("start-1"))
        .await;
    assert_eq!(
        (c.code, c.json["code"].as_str()),
        (409, Some("idempotency_conflict")),
        "{}",
        c.json
    );
    assert!(
        !c.json.to_string().contains("exit 1"),
        "echoes the request: {}",
        c.json
    );
    assert_eq!(d.count().await, 1);

    // a key belongs to its route: the same key on the preview.2 route is a new one
    let e = d
        .keyed("POST", "/api/v1/sessions", Some(&quick()), Some("start-1"))
        .await;
    assert_eq!(e.code, 201, "{}", e.json);
    assert!(!e.replayed());
    assert_ne!(e.json["id"], id.as_str());
    d.ended(e.json["id"].as_str().unwrap()).await;

    // no key: every request starts one (preview.2 behaviour unchanged)
    let (code, _) = d.call("POST", &path, Some(&quick())).await;
    assert_eq!(code, 201);
    assert_eq!(d.count().await, 3);
    d.shutdown().await;

    // after a restart the answer is still there
    let mut d = start(dir.path()).await;
    let r = d
        .keyed("POST", &path, Some(&quick()), Some("start-1"))
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    assert!(r.replayed(), "{}", r.head);
    assert_eq!(r.json["id"], id.as_str());
    let (_, list) = d.call("GET", &path, None).await;
    assert_eq!(list["sessions"].as_array().unwrap().len(), 2, "{list}");
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_refused_request_gives_its_key_back() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let seat = d.seat("dev").await;
    let path = format!("/api/v1/seats/{seat}/sessions");
    let no = d
        .keyed(
            "POST",
            &path,
            Some(r#"{"argv": ["/bin/echo", "should-not-run"]}"#),
            Some("start-2"),
        )
        .await;
    assert_eq!(
        (no.code, no.json["code"].as_str()),
        (403, Some("program_not_allowed")),
        "{}",
        no.json
    );
    // nothing was created, so the key is free for the corrected request
    let ok = d
        .keyed("POST", &path, Some(&quick()), Some("start-2"))
        .await;
    assert_eq!(ok.code, 201, "{}", ok.json);
    assert!(!ok.replayed());
    d.ended(ok.json["id"].as_str().unwrap()).await;
    // a malformed key is refused before anything runs
    let bad = d.keyed("POST", &path, Some(&quick()), Some("a b")).await;
    assert_eq!(bad.code, 400, "{}", bad.json);
    assert_eq!(d.count().await, 1);
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_stop_retried_with_the_same_key_is_answered_again() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let (code, s) = d.call("POST", "/api/v1/sessions", Some(&long())).await;
    assert_eq!(code, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_string();
    let del = format!("/api/v1/sessions/{id}?grace_ms=500");
    let a = d.keyed("DELETE", &del, None, Some("stop-1")).await;
    assert_eq!(a.code, 200, "{}", a.json);
    assert!(!a.replayed());
    let b = d.keyed("DELETE", &del, None, Some("stop-1")).await;
    assert_eq!(b.code, 200, "{}", b.json);
    assert!(b.replayed(), "{}", b.head);
    assert_eq!(b.json["id"], id.as_str());
    assert_eq!(b.json["state"], a.json["state"], "{} / {}", a.json, b.json);
    // the same key with another grace is another request
    let c = d
        .keyed(
            "DELETE",
            &format!("/api/v1/sessions/{id}?grace_ms=0"),
            None,
            Some("stop-1"),
        )
        .await;
    assert_eq!(
        (c.code, c.json["code"].as_str()),
        (409, Some("idempotency_conflict")),
        "{}",
        c.json
    );
    // a stop that failed (no such session) gives the key back
    let gone = "/api/v1/sessions/s_nope?grace_ms=500";
    let n = d.keyed("DELETE", gone, None, Some("stop-2")).await;
    assert_eq!(n.code, 404, "{}", n.json);
    let n2 = d.keyed("DELETE", gone, None, Some("stop-2")).await;
    assert_eq!(n2.code, 404, "{}", n2.json);
    assert!(!n2.replayed());
    d.shutdown().await;
}

/// 043dd T81 M1: a client that hangs up while its keyed stop still runs (axum then
/// drops the handler) must not leave the key claimed: once the stop is over, the
/// same key gets the answer again, not a 409 forever.
#[tokio::test(flavor = "multi_thread")]
async fn a_client_that_hangs_up_mid_stop_does_not_leave_its_key_claimed() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let (code, s) = d.call("POST", "/api/v1/sessions", Some(&stubborn())).await;
    assert_eq!(code, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_string();
    // the stop must find SIGTERM already ignored, or it ends at once and aibd closes the
    // connection before this test hangs up (Pro 13 full run, S55): wait for its line
    let end = Instant::now() + Duration::from_secs(15);
    loop {
        let (_, o) = d
            .call("GET", &format!("/api/v1/sessions/{id}/output"), None)
            .await;
        if o["output"]
            .as_array()
            .is_some_and(|a| a.iter().any(|e| e["payload"]["line"] == "ignoring-term"))
        {
            break;
        }
        assert!(Instant::now() < end, "never ignored SIGTERM: {o}");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let del = format!("/api/v1/sessions/{id}?grace_ms=2000");
    {
        let port = d.addr.port();
        let req = format!(
            "DELETE {del} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\
             Authorization: Bearer {}\r\nIdempotency-Key: stop-hangup\r\n\
             Content-Length: 0\r\n\r\n",
            d.token
        );
        let mut c = TcpStream::connect(d.addr).unwrap();
        c.write_all(req.as_bytes()).unwrap();
        // give aibd the request, then hang up without reading the answer
        tokio::time::sleep(Duration::from_millis(300)).await;
        c.shutdown(std::net::Shutdown::Both).unwrap();
    }
    d.ended(&id).await;
    let end = Instant::now() + Duration::from_secs(10);
    let r = loop {
        let r = d.keyed("DELETE", &del, None, Some("stop-hangup")).await;
        if r.code != 409 || Instant::now() > end {
            break r;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    assert_eq!(r.code, 200, "the key is still claimed: {}", r.json);
    assert!(r.replayed(), "{}", r.head);
    assert_eq!(r.json["id"], id.as_str());
    d.shutdown().await;
}

/// 043dd T81 N1: a stop left unfinished by the last run gives its key back at start
/// (stopping again does nothing), a start's key stays claimed (it may have started
/// something).
#[tokio::test(flavor = "multi_thread")]
async fn at_start_an_unfinished_stop_key_is_given_back_and_a_start_key_is_not() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    {
        // what a crash in the middle of both writes leaves
        let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
        let stop = aib_store::IdemKey {
            scope: "DELETE /api/v1/sessions/s_gone".into(),
            key: "left-stop".into(),
            request: aibd::idem::request_digest(&json!({"grace_ms": 500})),
        };
        let start = aib_store::IdemKey {
            scope: "POST /api/v1/sessions".into(),
            key: "left-start".into(),
            // the digest of quick() as the POST routes bind it (argv, cwd, env)
            request: aibd::idem::request_digest(
                &json!({"argv": common::fx_argv(&["exit", "0"]), "cwd": null, "env": []}),
            ),
        };
        let now = "2026-11-13T00:00:00.000Z";
        assert_eq!(
            s.idem_claim(&stop, now).unwrap(),
            aib_store::IdemClaim::Fresh
        );
        assert_eq!(
            s.idem_claim(&start, now).unwrap(),
            aib_store::IdemClaim::Fresh
        );
    }
    let mut d = start(dir.path()).await;
    let r = d
        .keyed(
            "DELETE",
            "/api/v1/sessions/s_gone?grace_ms=500",
            None,
            Some("left-stop"),
        )
        .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (404, Some("session_not_found")),
        "the stop ran again: {}",
        r.json
    );
    let r = d
        .keyed(
            "POST",
            "/api/v1/sessions",
            Some(&quick()),
            Some("left-start"),
        )
        .await;
    assert_eq!(r.code, 409, "a start key stays claimed: {}", r.json);
    assert!(
        r.json["message"]
            .as_str()
            .is_some_and(|m| m.contains("still running")),
        "claimed, not another request: {}",
        r.json
    );
    d.shutdown().await;
}
