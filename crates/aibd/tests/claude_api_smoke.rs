#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! REAL Claude Code through the aibd API (preview.3 E gate, VERIFIED-mac
//! evidence, not a CI test): a policy file that lists only `claude`, a seat,
//! `POST /api/v1/seats/{id}/sessions` with `-p … --output-format
//! stream-json --verbose`, the stream-json lines read back as
//! `session.output` events, then the same output after an aibd restart.
//! One real model call on the user's own login; a failure never prints
//! the output text (1ced7 L1). Run explicitly:
//!
//!   AIBD_REAL_CLAUDE=1 cargo test -p aibd --test claude_api_smoke -- --ignored --nocapture
//!
//! `AIBD_CLAUDE_PATH` overrides the program (default `~/.local/bin/claude`,
//! the standalone build's stable link; never the npm one).
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::allowlist::SessionPolicy;
use aibd::config::Config;
use aibd::server::{self, Started};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

async fn start(dir: &Path, session_policy: SessionPolicy) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: 2,
        session_policy,
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
    /// Poll the output route until both streams are closed.
    async fn output(&self, id: &str) -> Vec<Value> {
        let end = Instant::now() + Duration::from_secs(240);
        loop {
            let (code, v) = self
                .call(
                    "GET",
                    &format!("/api/v1/sessions/{id}/output?limit=1000"),
                    None,
                )
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
            assert!(
                Instant::now() < end,
                "streams not closed after {} events (text not printed)",
                items.len()
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
}

fn claude_path() -> PathBuf {
    match std::env::var("AIBD_CLAUDE_PATH") {
        Ok(p) => PathBuf::from(p),
        Err(_) => PathBuf::from(std::env::var("HOME").unwrap()).join(".local/bin/claude"),
    }
}

#[tokio::test(flavor = "multi_thread")]
#[ignore]
async fn a_real_claude_session_streams_its_output_and_reads_back_after_a_restart() {
    if std::env::var("AIBD_REAL_CLAUDE").as_deref() != Ok("1") {
        eprintln!("skipped: set AIBD_REAL_CLAUDE=1");
        return;
    }
    let claude = claude_path();
    assert!(
        claude.is_absolute() && claude.exists(),
        "no claude at {claude:?}"
    );
    let dir = tempfile::tempdir().unwrap();
    let work = tempfile::tempdir().unwrap();
    // the file AIBD_SESSION_POLICY would name, read by the same code
    let pf = dir.path().join("policy.json");
    std::fs::write(
        &pf,
        json!({"programs": [{"name": "claude", "path": claude}], "env": []}).to_string(),
    )
    .unwrap();
    let policy = aibd::allowlist::from_path(pf.to_str().unwrap()).unwrap();
    let data = dir.path().join("data");
    std::fs::create_dir(&data).unwrap();

    let mut d = start(&data, policy.clone()).await;
    let (code, seat) = d
        .call("POST", "/api/v1/seats", Some(r#"{"name": "smoke"}"#))
        .await;
    assert_eq!(code, 201, "{seat}");
    let seat = seat["seat_id"].as_str().unwrap().to_string();
    let body = json!({
        // aibd refuses an empty argv item (1..=4096 bytes each), so not
        // `--setting-sources ""`: `project` in the empty temp work dir loads
        // no settings either (no user hooks / MCP from this Mac)
        "argv": [claude, "-p", "--setting-sources", "project", "--strict-mcp-config",
                 "--model", "haiku", "--output-format", "stream-json", "--verbose",
                 "--", "Reply with the single word DONE."],
        "cwd": work.path(),
    })
    .to_string();
    let (code, s) = d
        .call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(&body),
        )
        .await;
    assert_eq!(code, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_string();
    let items = d.output(&id).await;
    let stdout: Vec<Value> = items
        .iter()
        .filter(|e| e["type"] == "session.output" && e["payload"]["stream"] == "stdout")
        .map(|e| {
            serde_json::from_str(e["payload"]["line"].as_str().unwrap()).unwrap_or(Value::Null)
        })
        .collect();
    eprintln!(
        "{} stdout lines; types: {:?}",
        stdout.len(),
        stdout
            .iter()
            .map(|v| v["type"].as_str().unwrap_or("?"))
            .collect::<Vec<_>>()
    );
    let result = stdout
        .iter()
        .find(|v| v["type"] == "result")
        .unwrap_or_else(|| {
            panic!(
                "no result event in {} output events (text not printed)",
                items.len()
            )
        });
    assert_eq!(
        result["is_error"], false,
        "result subtype {} (text not printed)",
        result["subtype"]
    );
    // the session ends by itself
    let end = Instant::now() + Duration::from_secs(30);
    let ended = loop {
        let (_, v) = d.call("GET", &format!("/api/v1/sessions/{id}"), None).await;
        if v["state"] != "running" {
            break v;
        }
        assert!(Instant::now() < end, "still running: {v}");
        tokio::time::sleep(Duration::from_millis(200)).await;
    };
    assert_eq!(ended["exit_code"], 0, "{ended}");
    d.shutdown().await;

    let mut d = start(&data, policy).await;
    let (code, v) = d
        .call(
            "GET",
            &format!("/api/v1/sessions/{id}/output?limit=1000"),
            None,
        )
        .await;
    assert_eq!(code, 200, "{v}");
    // compared, never printed: the output is the model's text
    assert!(
        v["output"].as_array().unwrap() == &items,
        "the output after a restart differs ({} vs {} events)",
        v["output"].as_array().unwrap().len(),
        items.len()
    );
    let (_, list) = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None)
        .await;
    assert_eq!(list["sessions"][0]["id"], id.as_str(), "{list}");
    assert_eq!(list["sessions"][0]["argv0"], "claude", "{list}");
    d.shutdown().await;
}
