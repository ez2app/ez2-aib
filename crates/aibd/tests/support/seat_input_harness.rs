//! Shared by tests/seat_input.rs and tests/seat_input_dgrs.rs (one process each, because the
//! daemon reads AIBD_PAIRING / AIBD_SEAT_INPUT_DGRS from the process environment).
#![allow(dead_code)]

use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const FAKE: &str = env!("CARGO_BIN_EXE_aibd-fake-provider");
const SHA: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

pub struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

pub struct Reply {
    pub code: u16,
    pub body: String,
    pub json: serde_json::Value,
}

fn raw(addr: SocketAddr, method: &str, path: &str, headers: &str, body: Option<&str>) -> Reply {
    let port = addr.port();
    let mut req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n{headers}"
    );
    match body {
        Some(b) => req += &format!("Content-Length: {}\r\n\r\n{b}", b.len()),
        None => req += "Content-Length: 0\r\n\r\n",
    }
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(req.as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
    let body = out.split_once("\r\n\r\n").map_or("", |x| x.1).to_string();
    Reply {
        code,
        json: serde_json::from_str(&body).unwrap_or(serde_json::Value::Null),
        body,
    }
}

impl Daemon {
    pub async fn start(dir: &Path) -> Daemon {
        // pairing is off unless this is set (server.rs); every test here sets the same value
        std::env::set_var("AIBD_PAIRING", "terminal-unverified");
        let cards = dir.join("cards.json");
        let doc = serde_json::json!({
            "schema_version": "aib-cards/v2",
            "cards": { "un0eba": {"uid": "un0eba", "display_name": "送字測試", "persona_sha": SHA} }
        });
        std::fs::write(&cards, doc.to_string()).unwrap();
        let policy = aibd::allowlist::SessionPolicy {
            programs: vec![aibd::allowlist::Program {
                name: "claude".into(),
                path: FAKE.into(),
                signer: None,
            }],
            env: vec![],
            interpreters_ok: false,
        };
        let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
        let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
        let c = Config {
            bind: "127.0.0.1:0".parse().unwrap(),
            data_dir: dir.join("data"),
            allowed_origins: vec![],
            max_sessions: 4,
            session_policy: policy,
            persona_cards: Some(cards),
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
        let st = tokio::time::timeout(Duration::from_secs(20), st_rx)
            .await
            .unwrap()
            .unwrap();
        Daemon {
            addr: st.addr,
            token: std::fs::read_to_string(&st.token_path).unwrap(),
            stop: Some(stop_tx),
            h: Some(h),
        }
    }

    /// `bearer: None` = the start-up token.
    pub async fn call(
        &self,
        method: &str,
        path: &str,
        bearer: Option<&str>,
        body: Option<&str>,
    ) -> Reply {
        let t = bearer.unwrap_or(&self.token);
        let mut headers = format!(
            "Authorization: Bearer {t}\r\nIdempotency-Key: k-{}\r\n",
            unique()
        );
        if body.is_some() {
            headers += "Content-Type: application/json\r\n";
        }
        let (addr, m, p, b) = (
            self.addr,
            method.to_string(),
            path.to_string(),
            body.map(str::to_string),
        );
        tokio::task::spawn_blocking(move || raw(addr, &m, &p, &headers, b.as_deref()))
            .await
            .unwrap()
    }

    /// A page credential, redeemed and (when `confirm`) confirmed with the terminal's short code.
    pub async fn page(&self, confirm: bool) -> String {
        self.page_form(confirm, "").await
    }

    /// [`Self::page`] with more of the redeem form (`&to=user`: a user folder page pairing
    /// for itself, S107-userhtml).
    #[allow(dead_code)] // not every test binary that includes the harness uses it
    pub async fn page_form(&self, confirm: bool, more: &str) -> String {
        let r = self.call("POST", "/api/v1/pair/codes", None, None).await;
        assert_eq!(r.code, 201, "{}", r.body);
        let id = r.json["pair_id"].as_str().unwrap().to_string();
        let code = r.json["code"].as_str().unwrap().to_string();
        let addr = self.addr;
        let headers = format!(
            "Origin: http://127.0.0.1:{}\r\nContent-Type: application/x-www-form-urlencoded\r\n",
            addr.port()
        );
        let body = format!("code={code}{more}");
        let r = tokio::task::spawn_blocking(move || {
            raw(addr, "POST", "/api/v1/pair/redeem", &headers, Some(&body))
        })
        .await
        .unwrap();
        assert_eq!(r.code, 200, "{}", r.body);
        let key = "setItem('aib.page_credential','";
        let i = r.body.find(key).unwrap() + key.len();
        let cred = r.body[i..].split('\'').next().unwrap().to_string();
        if confirm {
            let r = self
                .call("GET", &format!("/api/v1/pair/codes/{id}"), None, None)
                .await;
            let short = r.json["short_code"].as_str().unwrap().to_string();
            let r = self
                .call(
                    "POST",
                    &format!("/api/v1/pair/codes/{id}/confirm"),
                    None,
                    Some(&format!("{{\"short_code\":\"{short}\"}}")),
                )
                .await;
            assert_eq!(r.code, 200, "{}", r.body);
        }
        cred
    }

    /// Open a seat in `mode` working in `cwd`: `(seat_id, session_id)`.
    pub async fn open(&self, mode: &str, cwd: &Path) -> (String, String) {
        let body = serde_json::json!({
            "persona_uid": "un0eba", "provider": "claude", "mode": mode,
            "cwd": cwd.to_str().unwrap(),
        })
        .to_string();
        let r = self
            .call("POST", "/api/v1/seats/open", None, Some(&body))
            .await;
        assert_eq!(r.code, 201, "{}", r.body);
        assert_eq!(r.json["session"]["state"], "running", "{}", r.body);
        (
            r.json["seat"]["seat_id"].as_str().unwrap().to_string(),
            r.json["session"]["id"].as_str().unwrap().to_string(),
        )
    }

    pub async fn act(&self, cred: Option<&str>, seat: &str, text: &str) -> Reply {
        let body = serde_json::json!({"type": "seat_input", "seat_id": seat, "text": text});
        self.call("POST", "/api/v1/act", cred, Some(&body.to_string()))
            .await
    }

    pub async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        let _ = self.h.take().unwrap().await;
    }
}

fn unique() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos()
}

/// The lines the fake provider read so far: `(pid, the raw line)`.
pub fn read_log(cwd: &Path) -> Vec<(String, Vec<u8>)> {
    let text = std::fs::read_to_string(cwd.join("fake-provider.log")).unwrap_or_default();
    text.lines()
        .map(|l| {
            let (pid, hex) = l.split_once(' ').unwrap();
            let bytes = (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                .collect();
            (pid.to_string(), bytes)
        })
        .collect()
}

/// Wait until the log has `n` lines (or 10 s pass), then return it.
pub fn log_with(cwd: &Path, n: usize) -> Vec<(String, Vec<u8>)> {
    let end = Instant::now() + Duration::from_secs(10);
    loop {
        let l = read_log(cwd);
        if l.len() >= n || Instant::now() > end {
            return l;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

pub fn text_of(line: &[u8]) -> String {
    let v: serde_json::Value = serde_json::from_slice(line).unwrap();
    assert_eq!(v["type"], "user");
    v["message"]["content"][0]["text"]
        .as_str()
        .unwrap()
        .to_string()
}

pub fn seat_dir(root: &Path, name: &str) -> PathBuf {
    let p = root.join(name);
    std::fs::create_dir_all(&p).unwrap();
    p.canonicalize().unwrap()
}
