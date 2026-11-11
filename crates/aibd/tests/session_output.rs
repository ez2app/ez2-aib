#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! A session's stdout / stderr as `session.output` events on a real aibd:
//! one event per line, both streams closed at the end, read back from the
//! store after a restart; an over-long line is cut, not split. POSIX only
//! (`/bin/sh`); the processes are tiny and end by themselves.
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

impl Daemon {
    async fn run(&self, script: &str) -> String {
        let body = json!({"argv": ["/bin/sh", "-c", script]}).to_string();
        let (code, s) = self.call("POST", "/api/v1/sessions", Some(&body)).await;
        assert_eq!(code, 201, "{s}");
        s["id"].as_str().unwrap().to_string()
    }

    /// Poll the output route until both streams are closed.
    async fn output(&self, id: &str) -> Vec<Value> {
        let end = Instant::now() + Duration::from_secs(30);
        loop {
            let (code, v) = self
                .call("GET", &format!("/api/v1/sessions/{id}/output"), None)
                .await;
            assert_eq!(code, 200, "{v}");
            let items = v["output"].as_array().unwrap().clone();
            let closed = items
                .iter()
                .filter(|e| e["type"] == "session.output_closed")
                .count();
            if closed == 2 {
                return items;
            }
            assert!(Instant::now() < end, "streams not closed: {v}");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
}

fn lines(items: &[Value], stream: &str) -> Vec<String> {
    items
        .iter()
        .filter(|e| e["type"] == "session.output" && e["payload"]["stream"] == stream)
        .map(|e| e["payload"]["line"].as_str().unwrap().to_string())
        .collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn output_lines_are_events_and_read_back_after_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let id = d
        .run(r#"printf 'one\n{"type":"result","n":2}\n'; printf 'oops\n' >&2; printf 'no newline'"#)
        .await;
    let items = d.output(&id).await;
    assert_eq!(
        lines(&items, "stdout"),
        vec!["one", r#"{"type":"result","n":2}"#, "no newline"],
        "{items:?}"
    );
    assert_eq!(lines(&items, "stderr"), vec!["oops"], "{items:?}");
    // one gap-free sequence across both streams
    let seqs: Vec<i64> = items
        .iter()
        .map(|e| e["sequence"].as_i64().unwrap())
        .collect();
    assert_eq!(
        seqs,
        (1..=seqs.len() as i64).collect::<Vec<_>>(),
        "{items:?}"
    );
    assert!(
        items.iter().all(|e| e["payload"]["cut"] != true),
        "{items:?}"
    );
    // paging: after= skips what was read
    let (_, page) = d
        .call(
            "GET",
            &format!("/api/v1/sessions/{id}/output?after=2&limit=1"),
            None,
        )
        .await;
    assert_eq!(page["output"].as_array().unwrap().len(), 1, "{page}");
    assert_eq!(page["output"][0]["sequence"], 3, "{page}");
    assert_eq!(page["next"], 3, "{page}");
    // the output is on the global cursor too
    let (_, ev) = d
        .call("GET", "/api/v1/events?cursor=0&limit=500", None)
        .await;
    assert!(
        ev["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "session.output"
                && e["aggregate_id"] == format!("{id}/output").as_str()),
        "{ev}"
    );
    // the output lives in the store: folder 0700, database (and its WAL /
    // shared-memory files) 0600 (a63ab preview.3)
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(dir.path()), 0o700, "data dir");
        let db = dir.path().join("aibd.sqlite");
        assert_eq!(mode(&db), 0o600, "database");
        for ext in ["aibd.sqlite-wal", "aibd.sqlite-shm"] {
            let p = dir.path().join(ext);
            if p.exists() {
                assert_eq!(mode(&p), 0o600, "{ext}");
            }
        }
    }
    d.shutdown().await;

    let mut d = start(dir.path()).await;
    let (code, v) = d
        .call("GET", &format!("/api/v1/sessions/{id}/output"), None)
        .await;
    assert_eq!(code, 200, "a session of the previous run reads back: {v}");
    assert_eq!(v["output"].as_array().unwrap(), &items, "the same events");
    let (code, v) = d.call("GET", "/api/v1/sessions/s_nope/output", None).await;
    assert_eq!(
        (code, v["code"].as_str()),
        (404, Some("session_not_found")),
        "{v}"
    );
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn an_over_long_line_is_cut_and_the_next_line_still_arrives() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = start(dir.path()).await;
    let max = aibd::session_output::MAX_LINE;
    let id = d
        .run(&format!(
            "head -c {} /dev/zero | tr '\\000' a; printf '\\nafter\\n'",
            max + 1000
        ))
        .await;
    let items = d.output(&id).await;
    let out: Vec<&Value> = items
        .iter()
        .filter(|e| e["type"] == "session.output")
        .collect();
    assert_eq!(out.len(), 2, "cut, not split into two events");
    assert_eq!(out[0]["payload"]["cut"], true);
    assert_eq!(out[0]["payload"]["line"].as_str().unwrap().len(), max);
    assert_eq!(out[1]["payload"]["line"], "after");
    assert_eq!(out[1]["payload"]["cut"], false);
    d.shutdown().await;
}
