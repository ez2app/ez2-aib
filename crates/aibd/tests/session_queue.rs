#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! The session queue on a real aibd (ADR 0005 §5, a63ab 19:13): a full limit
//! queues instead of refusing (202 `pending`), waiting sessions start in
//! arrival order as slots come back, DELETE takes one out of the queue, at
//! most `MAX_QUEUE` wait (then `429 session_queue_full`), and a session left
//! pending by a crash is `interrupted` at the next start. POSIX only
//! (the test-only `aib-fixture-tree`, `/bin/sleep`); every process is collected by aibd's own stop.
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

mod common;

/// The programs these tests start: the fixture and `/bin/sleep`, both native, no
/// interpreter (S55).
fn policy() -> SessionPolicy {
    SessionPolicy {
        programs: vec![
            Program {
                name: "aib-fixture-tree".into(),
                path: common::fixture(),
                signer: None,
            },
            Program {
                name: "sleep".into(),
                path: "/bin/sleep".into(),
                signer: None,
            },
        ],
        env: vec![],
        interpreters_ok: false,
    }
}

struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

async fn start(dir: &Path, max: usize) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: max,
        session_policy: policy(),
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

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

const SLEEP: &str = r#"{"argv": ["/bin/sleep", "60"]}"#;
/// A session that ends at once with 0.
fn quick() -> String {
    common::fx_body(&["exit", "0"])
}

impl Daemon {
    async fn post(&self, body: &str) -> (u16, Value) {
        self.call("POST", "/api/v1/sessions", Some(body)).await
    }

    async fn state(&self, id: &str) -> Value {
        self.call("GET", &format!("/api/v1/sessions/{id}"), None)
            .await
            .1
    }

    /// Wait until the session is in one of `states`.
    async fn until(&self, id: &str, states: &[&str]) -> Value {
        let end = Instant::now() + Duration::from_secs(30);
        loop {
            let v = self.state(id).await;
            if states.iter().any(|s| v["state"] == *s) {
                return v;
            }
            assert!(Instant::now() < end, "{id} never reached {states:?}: {v}");
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    async fn events(&self) -> Vec<Value> {
        let (_, ev) = self
            .call("GET", "/api/v1/events?cursor=0&limit=500", None)
            .await;
        ev["events"].as_array().unwrap().clone()
    }
}

fn started_seq(evs: &[Value], id: &str) -> Option<i64> {
    evs.iter()
        .find(|e| e["aggregate_id"] == id && e["type"] == "session.started")
        .map(|e| e["global_seq"].as_i64().unwrap())
}

#[tokio::test(flavor = "multi_thread")]
async fn a_full_limit_queues_and_the_waiting_start_in_arrival_order() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path(), 1).await;
    let (code, a) = d.post(SLEEP).await;
    assert_eq!((code, a["state"].as_str()), (201, Some("running")), "{a}");
    let a = a["id"].as_str().unwrap().to_string();
    let mut waiting = Vec::new();
    for _ in 0..3 {
        let (code, q) = d.post(&quick()).await;
        assert_eq!((code, q["state"].as_str()), (202, Some("pending")), "{q}");
        assert_eq!(
            (q["pid"].as_u64(), q["holds_slot"].as_bool()),
            (Some(0), Some(false))
        );
        waiting.push(q["id"].as_str().unwrap().to_string());
    }
    let evs = d.events().await;
    let pos: Vec<i64> = waiting
        .iter()
        .map(|id| {
            evs.iter()
                .find(|e| e["aggregate_id"] == id.as_str() && e["type"] == "session.queued")
                .unwrap()["payload"]["position"]
                .as_i64()
                .unwrap()
        })
        .collect();
    assert_eq!(pos, vec![1, 2, 3]);
    // nothing waiting starts while the slot is taken
    tokio::time::sleep(Duration::from_millis(300)).await;
    for id in &waiting {
        assert_eq!(d.state(id).await["state"], "pending");
    }
    // the slot comes back: the three start one after another, oldest first
    let (code, _) = d
        .call("DELETE", &format!("/api/v1/sessions/{a}?grace_ms=0"), None)
        .await;
    assert_eq!(code, 200);
    for id in &waiting {
        let v = d.until(id, &["exited", "stop_unconfirmed", "killed"]).await;
        assert!(v["pid"].as_u64().unwrap() > 0, "{v}");
    }
    let evs = d.events().await;
    let seqs: Vec<i64> = waiting
        .iter()
        .map(|id| started_seq(&evs, id).unwrap())
        .collect();
    let mut sorted = seqs.clone();
    sorted.sort_unstable();
    assert_eq!(seqs, sorted, "FIFO: started in the order they were queued");
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_cancelled_queued_session_never_starts() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path(), 1).await;
    let (_, a) = d.post(SLEEP).await;
    let a = a["id"].as_str().unwrap().to_string();
    let (code, q) = d.post(&quick()).await;
    assert_eq!(code, 202, "{q}");
    let q = q["id"].as_str().unwrap().to_string();
    let (code, c) = d
        .call("DELETE", &format!("/api/v1/sessions/{q}"), None)
        .await;
    assert_eq!((code, c["state"].as_str()), (200, Some("cancelled")), "{c}");
    assert_eq!(c["ended_by"], "api");
    d.call("DELETE", &format!("/api/v1/sessions/{a}?grace_ms=0"), None)
        .await;
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(d.state(&q).await["state"], "cancelled");
    let evs = d.events().await;
    assert_eq!(started_seq(&evs, &q), None, "a cancelled session started");
    assert!(evs
        .iter()
        .any(|e| e["aggregate_id"] == q.as_str() && e["type"] == "session.cancelled"));
    d.shutdown().await;
}

/// 043dd M2-1 (ADR 0003: every spawn resolves the real path again): the M1
/// gate runs again when a session leaves the queue. A listed program that was
/// swapped for an unlisted one while the session waited is refused then:
/// `failed` / `policy_refused`, never started, and the slot goes on.
#[tokio::test(flavor = "multi_thread")]
async fn a_queued_session_is_gated_again_when_it_leaves_the_queue() {
    let dir = tempfile::tempdir().unwrap();
    let bin = tempfile::tempdir().unwrap();
    let link = bin.path().join("prog");
    std::os::unix::fs::symlink("/bin/sleep", &link).unwrap();
    let mut d = start(dir.path(), 1).await;
    let (_, a) = d.post(SLEEP).await;
    let a = a["id"].as_str().unwrap().to_string();
    let body = json!({"argv": [link.to_str().unwrap(), "60"]}).to_string();
    let (code, q) = d.post(&body).await;
    assert_eq!(code, 202, "the gate passed it while /bin/sleep: {q}");
    let q = q["id"].as_str().unwrap().to_string();
    // while it waits, the path now leads to a program that is not listed
    std::fs::remove_file(&link).unwrap();
    std::os::unix::fs::symlink("/bin/cat", &link).unwrap();
    d.call("DELETE", &format!("/api/v1/sessions/{a}?grace_ms=0"), None)
        .await;
    let v = d.until(&q, &["failed", "running"]).await;
    assert_eq!(v["state"], "failed", "{v}");
    assert_eq!(v["ended_by"], "policy_refused", "{v}");
    let evs = d.events().await;
    assert_eq!(started_seq(&evs, &q), None, "an unlisted program started");
    assert!(
        evs.iter().any(|e| e["type"] == "session.spawn_rejected"
            && e["payload"]["code"] == "program_not_allowed"),
        "the refusal is audited: {evs:?}"
    );
    // the slot went on: the next start runs
    let (code, n) = d.post(&quick()).await;
    assert_eq!(code, 201, "{n}");
    d.shutdown().await;
}

/// 043dd M2-2: a pending session says where it stands and what holds the
/// slots (a stop that kept its slot shows its reason), not only `pending`.
#[tokio::test(flavor = "multi_thread")]
async fn a_pending_session_shows_its_place_and_who_holds_the_slots() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path(), 1).await;
    let (_, a) = d.post(SLEEP).await;
    let a = a["id"].as_str().unwrap().to_string();
    let (_, q1) = d.post(&quick()).await;
    let (_, q2) = d.post(&quick()).await;
    let (q1, q2) = (q1["id"].as_str().unwrap(), q2["id"].as_str().unwrap());
    let v = d.state(q2).await;
    assert_eq!(v["queue"]["position"], 2, "{v}");
    assert_eq!(v["queue"]["slots"], 1, "{v}");
    assert_eq!(v["queue"]["held_by"][0]["id"], a.as_str(), "{v}");
    assert_eq!(v["queue"]["held_by"][0]["state"], "running", "{v}");
    // a cancel ahead of it moves it up
    d.call("DELETE", &format!("/api/v1/sessions/{q1}"), None)
        .await;
    assert_eq!(d.state(q2).await["queue"]["position"], 1);
    // a session that is not pending has no queue view
    assert!(d.state(&a).await.get("queue").is_none());
    d.call("DELETE", &format!("/api/v1/sessions/{a}?grace_ms=0"), None)
        .await;
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_queue_is_bounded_and_the_waiting_end_interrupted_at_shutdown() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path(), 1).await;
    let (code, _) = d.post(SLEEP).await;
    assert_eq!(code, 201);
    let mut first = None;
    for _ in 0..aibd::sessions::MAX_QUEUE {
        let (code, q) = d.post(&quick()).await;
        assert_eq!(code, 202, "{q}");
        first.get_or_insert_with(|| q["id"].as_str().unwrap().to_string());
    }
    let (code, v) = d.post(&quick()).await;
    assert_eq!(
        (code, v["code"].as_str()),
        (429, Some("session_queue_full")),
        "{v}"
    );
    d.shutdown().await;
    let first = first.unwrap();
    let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let r = s.session_row(&first).unwrap().unwrap();
    assert_eq!(
        (r.state.as_str(), r.ended_by.as_deref()),
        ("interrupted", Some("shutdown"))
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_session_left_pending_by_a_crash_is_interrupted_and_never_started() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path(), 1).await;
    d.shutdown().await;
    {
        let s = aib_store::Store::open(&dir.path().join("aibd.sqlite")).unwrap();
        s.session_event(
            aib_store::SessionRow {
                session_id: "s_waiting".into(),
                seat_id: None,
                pid: 0,
                argv0: Some("sh".into()),
                argc: 3,
                state: "pending".into(),
                holds_slot: false,
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
                event_id: "ev_waiting_1".into(),
                aggregate_type: "session".into(),
                aggregate_id: "s_waiting".into(),
                sequence: 1,
                event_type: "session.queued".into(),
                correlation_id: None,
                occurred_at: "2026-11-09T00:00:01.000Z".into(),
                payload: json!({"id": "s_waiting"}),
            },
        )
        .unwrap();
    }
    let mut d = start(dir.path(), 1).await;
    let evs = d.events().await;
    let i = evs
        .iter()
        .find(|e| e["aggregate_id"] == "s_waiting" && e["type"] == "session.interrupted")
        .unwrap_or_else(|| panic!("{evs:?}"));
    assert_eq!(i["payload"]["previous_state"], "pending");
    assert_eq!(i["payload"]["processes_may_remain"], false);
    assert_eq!(started_seq(&evs, "s_waiting"), None);
    let (code, _) = d.call("GET", "/api/v1/sessions/s_waiting", None).await;
    assert_eq!(code, 404, "not a session of this aibd");
    d.shutdown().await;
}
