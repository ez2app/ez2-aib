#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! `/api/v1/seats` on a real aibd (127.0.0.1:0, temp data dir): create, read,
//! list, Idempotency-Key, and the Phase 1 gate's "read back after a restart".
//! No processes are started, so this runs on every OS.
// test deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::Duration;

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
        // no session is started here: the default policy allows nothing
        session_policy: Default::default(),
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
        Err(_) => panic!("aibd did not start: {:?}", h.await),
    };
    Daemon {
        addr: st.addr,
        token: std::fs::read_to_string(&st.token_path).unwrap(),
        stop: Some(stop_tx),
        h: Some(h),
    }
}

struct Reply {
    code: u16,
    head: String,
    json: serde_json::Value,
}

impl Daemon {
    /// One raw HTTP/1.1 request (blocking; run it on a blocking thread).
    fn raw(&self, method: &str, path: &str, body: Option<&str>, extra: &str) -> Reply {
        let port = self.addr.port();
        let mut req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\
             Authorization: Bearer {}\r\n{extra}",
            self.token
        );
        match body {
            Some(b) => {
                req += &format!(
                    "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{b}",
                    b.len()
                )
            }
            None => req += "Content-Length: 0\r\n\r\n",
        }
        let mut s = TcpStream::connect(self.addr).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
        s.write_all(req.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
        let (head, body) = out.split_once("\r\n\r\n").unwrap_or((&out, ""));
        Reply {
            code,
            head: head.to_ascii_lowercase(),
            json: serde_json::from_str(body).unwrap_or(serde_json::Value::Null),
        }
    }

    async fn call(&self, method: &str, path: &str, body: Option<&str>, extra: &str) -> Reply {
        let (addr, token) = (self.addr, self.token.clone());
        let (m, p, b, e) = (
            method.to_string(),
            path.to_string(),
            body.map(str::to_string),
            extra.to_string(),
        );
        tokio::task::spawn_blocking(move || {
            let d = Daemon {
                addr,
                token,
                stop: None,
                h: None,
            };
            d.raw(&m, &p, b.as_deref(), &e)
        })
        .await
        .unwrap()
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_is_created_read_listed_and_read_back_after_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let r = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"dev 主線"}"#), "")
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    let id = r.json["seat_id"].as_str().unwrap().to_string();
    assert!(id.starts_with("seat_"), "{id}");
    assert_eq!(r.json["name"], "dev 主線");
    assert_eq!(r.json["state"], "CREATED");
    assert_eq!(r.json["revision"], 1);
    let one = d
        .call("GET", &format!("/api/v1/seats/{id}"), None, "")
        .await;
    assert_eq!(one.code, 200);
    assert_eq!(one.json, r.json);
    // its event is on the global cursor (the events table is the outbox)
    let ev = d.call("GET", "/api/v1/events?cursor=0", None, "").await;
    let evs = ev.json["events"].as_array().unwrap();
    assert!(
        evs.iter()
            .any(|e| e["aggregate_id"] == id.as_str() && e["type"] == "seat.created"),
        "{}",
        ev.json
    );
    d.shutdown().await;

    // Phase 1 gate: a restart reads the seat back
    let mut d = start(dir.path()).await;
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(all.code, 200);
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        1,
        "{}",
        all.json
    );
    assert_eq!(all.json["seats"][0], r.json);
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_retry_with_the_same_key_gets_the_first_seat_and_creates_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let k = "Idempotency-Key: retry-1\r\n";
    let a = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"qa"}"#), k)
        .await;
    assert_eq!(a.code, 201, "{}", a.json);
    assert!(!a.head.contains("idempotent-replayed"), "{}", a.head);
    // same body with other whitespace: same canonical request
    let b = d
        .call("POST", "/api/v1/seats", Some(r#"{ "name" : "qa" }"#), k)
        .await;
    assert_eq!(b.code, 201);
    assert_eq!(b.json, a.json);
    assert!(b.head.contains("idempotent-replayed: true"), "{}", b.head);
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        1,
        "{}",
        all.json
    );
    // the replay also survives a restart
    d.shutdown().await;
    let mut d = start(dir.path()).await;
    let c = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"qa"}"#), k)
        .await;
    assert_eq!(c.json, a.json);
    assert!(c.head.contains("idempotent-replayed: true"), "{}", c.head);
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_same_key_for_another_body_is_a_conflict() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let k = "Idempotency-Key: k-2\r\n";
    let a = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"a"}"#), k)
        .await;
    assert_eq!(a.code, 201);
    let b = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"b"}"#), k)
        .await;
    assert_eq!(b.code, 409, "{}", b.json);
    assert_eq!(b.json["code"], "idempotency_conflict");
    // without a key every POST is a new seat
    let c = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"a"}"#), "")
        .await;
    assert_eq!(c.code, 201);
    assert_ne!(c.json["seat_id"], a.json["seat_id"]);
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        2,
        "{}",
        all.json
    );
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn bad_requests_are_refused_with_stable_codes() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    for (body, extra, code, want) in [
        (Some(r#"{"name":""}"#), "", 400, "invalid_request"),
        (Some(r#"{"name":"a\nb"}"#), "", 400, "invalid_request"),
        (Some(r#"{"name":"a","x":1}"#), "", 400, "invalid_request"),
        (Some(r#"{"nam":"a"}"#), "", 400, "invalid_request"),
        (Some("not json"), "", 400, "invalid_request"),
        (
            Some(r#"{"name":"a"}"#),
            "Idempotency-Key: a b\r\n",
            400,
            "invalid_request",
        ),
        (
            Some(r#"{"name":"a"}"#),
            "Idempotency-Key: a\r\nIdempotency-Key: b\r\n",
            400,
            "invalid_request",
        ),
    ] {
        let r = d.call("POST", "/api/v1/seats", body, extra).await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (code, Some(want)),
            "{body:?} {extra:?}"
        );
    }
    let big = format!(r#"{{"name":"{}"}}"#, "a".repeat(5000));
    let r = d.call("POST", "/api/v1/seats", Some(&big), "").await;
    assert_eq!(r.json["code"], "payload_too_large");
    let r = d.call("GET", "/api/v1/seats/seat_nope", None, "").await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (404, Some("seat_not_found"))
    );
    let r = d.call("DELETE", "/api/v1/seats/seat_nope", None, "").await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (405, Some("method_not_allowed"))
    );
    // nothing above created a seat
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(all.json["seats"], serde_json::json!([]));
    d.shutdown().await;
}
