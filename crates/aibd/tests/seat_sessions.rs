#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Sessions of a seat on a real aibd: `POST /api/v1/seats/{id}/sessions`,
//! the stored rows (`GET /api/v1/seats/{id}/sessions`) read back after a
//! restart, and a session left open by a crashed aibd shown `interrupted`.
//! POSIX only (`/bin/sh`); the processes are tiny and end by themselves.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::allowlist::{Program, SessionPolicy};
use aibd::config::Config;
use aibd::server::{self, Started};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::{Duration, Instant};

/// The programs these tests start (`/bin/sh` is an interpreter, so
/// `interpreters_ok`, which only Rust code can set).
/// DECISION-614-held-lock (043dd + Codex): the tests in this file run one at a
/// time. On macOS a held spawn keeps copies of the whole process's fds between
/// fork and exec, so another test's held child can hold the crash test's
/// `aibd.lock` and its restart sees AlreadyRunning (Pro 13: 3/10 in parallel,
/// 0/10 one at a time). Fixed for real in a follow-up (close the lock and the
/// listener before holding; one held spawn at a time).
static SERIAL: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn policy() -> SessionPolicy {
    SessionPolicy {
        programs: vec![Program {
            name: "sh".into(),
            path: "/bin/sh".into(),
            signer: None,
        }],
        env: vec![],
        interpreters_ok: true,
    }
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

/// One raw HTTP/1.1 request (blocking).
fn raw(
    addr: SocketAddr,
    token: &str,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> (u16, Value) {
    let port = addr.port();
    let mut req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\
         Authorization: Bearer {token}\r\n"
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
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    s.write_all(req.as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
    let json = out
        .split_once("\r\n\r\n")
        .and_then(|(_, b)| serde_json::from_str(b).ok())
        .unwrap_or(Value::Null);
    (code, json)
}

impl Daemon {
    async fn call(&self, method: &str, path: &str, body: Option<&str>) -> (u16, Value) {
        let (addr, token) = (self.addr, self.token.clone());
        let (m, p, b) = (
            method.to_string(),
            path.to_string(),
            body.map(str::to_string),
        );
        tokio::task::spawn_blocking(move || raw(addr, &token, &m, &p, b.as_deref()))
            .await
            .unwrap()
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

const QUICK: &str = r#"{"argv": ["/bin/sh", "-c", "exit 0"]}"#;

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_session_is_stored_and_read_back_after_a_restart() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let seat = d.seat("dev").await;
    let (code, s) = d
        .call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(QUICK),
        )
        .await;
    assert_eq!(code, 201, "{s}");
    assert_eq!(s["seat_id"], seat.as_str());
    let id = s["id"].as_str().unwrap().to_string();
    let done = d.ended(&id).await;
    let (code, list) = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None)
        .await;
    assert_eq!(code, 200);
    let rows = list["sessions"].as_array().unwrap();
    assert_eq!(rows.len(), 1, "{list}");
    let r = &rows[0];
    assert_eq!(r["id"], id.as_str());
    assert_eq!(r["state"], done["state"], "the row follows the session");
    assert_eq!(r["argv0"], "sh");
    assert_eq!(r["argc"], 3);
    assert!(r.get("argv").is_none() && r.get("env").is_none(), "{r}");
    // the preview.2 route still works and has no seat
    let (code, p) = d.call("POST", "/api/v1/sessions", Some(QUICK)).await;
    assert_eq!(code, 201, "{p}");
    assert_eq!(p["seat_id"], Value::Null);
    d.ended(p["id"].as_str().unwrap()).await;
    d.shutdown().await;
    // what this aibd left in the store when it stopped (a slot still held by
    // a stop that could not show the tree gone is the restart's case)
    let before = {
        let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
        s.session_row(&id).unwrap().unwrap()
    };

    let mut d = start(dir.path()).await;
    let (_, list) = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None)
        .await;
    let rows = list["sessions"].as_array().unwrap();
    assert_eq!(rows.len(), 1, "{list}");
    if before.holds_slot {
        assert_eq!(rows[0]["state"], "interrupted", "{list}");
    } else {
        assert_eq!(
            rows[0]["state"],
            before.state.as_str(),
            "an ended session stays as it ended"
        );
    }
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_session_left_open_by_a_crash_is_interrupted_at_the_next_start() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let seat = d.seat("qa").await;
    d.shutdown().await;
    // what a crash leaves: a row still `running` (written as aibd writes it)
    {
        let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
        s.session_event(
            aib_store::SessionRow {
                session_id: "s_crashed".into(),
                seat_id: Some(seat.clone()),
                pid: 999_999,
                argv0: Some("claude".into()),
                argc: 2,
                state: "running".into(),
                holds_slot: true,
                started_at: "2026-11-09T00:00:01.000Z".into(),
                ended_at: None,
                ended_by: None,
                exit_code: None,
                signal: None,
                stop_status: None,
                stop_reason: None,
                updated_at: "2026-11-09T00:00:01.000Z".into(),
            },
            aib_store::NewEvent {
                event_id: "ev_crashed_1".into(),
                aggregate_type: "session".into(),
                aggregate_id: "s_crashed".into(),
                sequence: 1,
                event_type: "session.started".into(),
                correlation_id: None,
                occurred_at: "2026-11-09T00:00:01.000Z".into(),
                payload: json!({"id": "s_crashed"}),
            },
        )
        .unwrap();
    }
    let mut d = start(dir.path()).await;
    let (_, list) = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None)
        .await;
    let r = &list["sessions"][0];
    assert_eq!(r["id"], "s_crashed", "{list}");
    assert_eq!(r["state"], "interrupted");
    assert_eq!(r["ended_by"], "aibd_restart");
    assert_eq!(r["holds_slot"], false);
    // never re-attached: it is not a live session of this aibd
    let (code, _) = d.call("GET", "/api/v1/sessions/s_crashed", None).await;
    assert_eq!(code, 404);
    let (_, ev) = d
        .call("GET", "/api/v1/events?cursor=0&limit=500", None)
        .await;
    assert!(
        ev["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["aggregate_id"] == "s_crashed"
                && e["type"] == "session.interrupted"
                && e["payload"]["processes_may_remain"] == true),
        "{ev}"
    );
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn an_unknown_seat_starts_nothing() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let (code, v) = d
        .call("POST", "/api/v1/seats/seat_nope/sessions", Some(QUICK))
        .await;
    assert_eq!(
        (code, v["code"].as_str()),
        (404, Some("seat_not_found")),
        "{v}"
    );
    let (code, v) = d
        .call("GET", "/api/v1/seats/seat_nope/sessions", None)
        .await;
    assert_eq!(
        (code, v["code"].as_str()),
        (404, Some("seat_not_found")),
        "{v}"
    );
    let (_, all) = d.call("GET", "/api/v1/sessions", None).await;
    assert_eq!(all["sessions"], json!([]), "nothing was started");
    // a bad body is refused before the seat is looked up
    let seat = d.seat("x").await;
    let (code, v) = d
        .call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(r#"{"argv": []}"#),
        )
        .await;
    assert_eq!(
        (code, v["code"].as_str()),
        (400, Some("invalid_request")),
        "{v}"
    );
    d.shutdown().await;
}

/// The seat route has the same M1 gate as `POST /api/v1/sessions` (f1fae
/// integ preview.3: #592's route and #593's gate had not seen each other).
#[tokio::test(flavor = "multi_thread")]
async fn the_seat_route_refuses_an_unlisted_program_and_records_it() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let seat = d.seat("dev").await;
    let (code, v) = d
        .call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(r#"{"argv": ["/bin/echo", "should-not-run"]}"#),
        )
        .await;
    assert_eq!(
        (code, v["code"].as_str()),
        (403, Some("program_not_allowed")),
        "{v}"
    );
    assert!(
        !v.to_string().contains("/bin/echo"),
        "echoes the request: {v}"
    );
    let (_, list) = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None)
        .await;
    assert_eq!(list["sessions"], json!([]), "nothing was started: {list}");
    let (_, ev) = d
        .call("GET", "/api/v1/events?cursor=0&limit=500", None)
        .await;
    let evs = ev["events"].as_array().unwrap();
    assert!(!evs.iter().any(|e| e["type"] == "session.started"), "{ev}");
    assert!(
        evs.iter().any(|e| e["type"] == "session.spawn_rejected"
            && e["payload"]["code"] == "program_not_allowed"),
        "{ev}"
    );
    d.shutdown().await;
}

/// A11-2: a seat session's end reaches who-is-alive. Exit 3 = down (a row
/// waiting for its threshold); exit 0 = closed on purpose (no row).
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_session_end_is_recorded_for_who_is_alive() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let down = d.seat("down").await;
    let clean = d.seat("clean").await;
    for (seat, argv) in [
        (&down, r#"{"argv": ["/bin/sh", "-c", "exit 3"]}"#),
        (&clean, QUICK),
    ] {
        let (code, s) = d
            .call(
                "POST",
                &format!("/api/v1/seats/{seat}/sessions"),
                Some(argv),
            )
            .await;
        assert_eq!(code, 201, "{s}");
        d.ended(s["id"].as_str().unwrap()).await;
    }
    d.shutdown().await;
    let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let rows = s.alive_rows().unwrap();
    assert_eq!(
        rows.iter()
            .map(|r| (r.cabin.as_str(), r.alerted_at))
            .collect::<Vec<_>>(),
        [(down.as_str(), None)],
        "{rows:?}"
    );
}

/// A11-2: a revive-policy.json that cannot be used stops the start with the
/// reason instead of running on defaults (a9136 11-13).
#[tokio::test(flavor = "multi_thread")]
async fn a_bad_revive_policy_stops_the_start() {
    let _serial = SERIAL.lock().await;
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("revive-policy.json"),
        r#"{"alive_after_s": 5}"#,
    )
    .unwrap();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.path().to_path_buf(),
        allowed_origins: vec![],
        max_sessions: 2,
        session_policy: policy(),
        persona_cards: None,
    };
    let r = tokio::time::timeout(
        Duration::from_secs(20),
        server::run(c, std::future::pending(), |_| {}),
    )
    .await
    .unwrap();
    match r {
        Err(server::StartError::RevivePolicy(why)) => {
            assert!(why.contains("alive_after_s"), "{why}")
        }
        other => panic!("expected a revive policy error, got {other:?}"),
    }
}
