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
        Self::start_max(dir, 4).await
    }

    /// `start` with room for `max` running sessions (a test that opens more seats than four and
    /// needs the later ones running, not queued).
    pub async fn start_max(dir: &Path, max: usize) -> Daemon {
        Self::start_inner(dir, max, false).await
    }

    /// `start` with `copilot` listed too: the same fake under the file name `copilot` (it acts
    /// like copilot 1.0.115 for aibd's checks, 3e53b REVIEW-copilot e3d07aff L1).
    pub async fn start_copilot(dir: &Path) -> Daemon {
        Self::start_inner(dir, 4, true).await
    }

    async fn start_inner(dir: &Path, max: usize, with_copilot: bool) -> Daemon {
        // pairing is off unless this is set (server.rs); every test here sets the same value
        std::env::set_var("AIBD_PAIRING", "terminal-unverified");
        let cards = dir.join("cards.json");
        let doc = serde_json::json!({
            "schema_version": "aib-cards/v2",
            "cards": { "un0eba": {"uid": "un0eba", "display_name": "送字測試", "persona_sha": SHA} }
        });
        std::fs::write(&cards, doc.to_string()).unwrap();
        // S89-live-2: the same fake as `codex` (its file name is what makes aibd speak codex)
        let codex = dir
            .join("bin")
            .join(if cfg!(windows) { "codex.exe" } else { "codex" });
        std::fs::create_dir_all(codex.parent().unwrap()).unwrap();
        std::fs::copy(FAKE, &codex).unwrap();
        let mut programs = vec![
            aibd::allowlist::Program {
                name: "claude".into(),
                path: FAKE.into(),
                signer: None,
            },
            aibd::allowlist::Program {
                name: "codex".into(),
                path: codex.clone(),
                signer: None,
            },
        ];
        if with_copilot {
            let copilot = dir.join("bin").join("copilot");
            std::fs::copy(FAKE, &copilot).unwrap();
            programs.push(aibd::allowlist::Program {
                name: "copilot".into(),
                path: copilot,
                signer: None,
            });
        }
        let policy = aibd::allowlist::SessionPolicy {
            programs,
            env: vec![],
            interpreters_ok: false,
        };
        let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
        let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
        let c = Config {
            bind: "127.0.0.1:0".parse().unwrap(),
            data_dir: dir.join("data"),
            allowed_origins: vec![],
            max_sessions: max,
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

    /// The address and the start-up token (a test that reads a stream itself).
    pub fn addr_token(&self) -> (SocketAddr, String) {
        (self.addr, self.token.clone())
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

    /// A form POST with no token, from `origin` (`None`: this aibd's own; `Some("")`: none),
    /// as the code page sends its redeem.
    #[allow(dead_code)] // not every test binary that includes the harness uses it
    pub async fn form(&self, path: &str, body: &str, origin: Option<&str>) -> Reply {
        let addr = self.addr;
        // Some("") sends no Origin at all
        let o = match origin {
            Some("") => String::new(),
            Some(o) => format!("Origin: {o}\r\n"),
            None => format!("Origin: http://127.0.0.1:{}\r\n", addr.port()),
        };
        let headers = format!("{o}Content-Type: application/x-www-form-urlencoded\r\n");
        let (p, b) = (path.to_string(), body.to_string());
        tokio::task::spawn_blocking(move || raw(addr, "POST", &p, &headers, Some(&b)))
            .await
            .unwrap()
    }

    /// Open a seat in `mode` working in `cwd`: `(seat_id, session_id)`.
    pub async fn open(&self, mode: &str, cwd: &Path) -> (String, String) {
        self.open_as("claude", mode, cwd).await
    }

    pub async fn open_as(&self, provider: &str, mode: &str, cwd: &Path) -> (String, String) {
        let body = serde_json::json!({
            "persona_uid": "un0eba", "provider": provider, "mode": mode,
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

/// A test that ends before [`Daemon::shutdown`] (an assertion failed: a knife bit) stops aibd
/// here, so its seats' providers are stopped and the test runtime can end. Without it the
/// providers kept running, a blocking reader of theirs never returned and the runtime's drop
/// waited for it: the failing test hung until the knife's limit and the knife read as not
/// run (a9136 11-18, knives-14-threads: 4 knives at 300 s on andymini). aibd's own stop is
/// bounded (SHUTDOWN_CAP), so this wait is too.
impl Drop for Daemon {
    fn drop(&mut self) {
        let (Some(stop), Some(h)) = (self.stop.take(), self.h.take()) else {
            return;
        };
        // only where another thread can run aibd's stop while this one waits
        let Ok(rt) = tokio::runtime::Handle::try_current()
            .map_err(|_| ())
            .and_then(|rt| {
                (rt.runtime_flavor() == tokio::runtime::RuntimeFlavor::MultiThread)
                    .then_some(rt)
                    .ok_or(())
            })
        else {
            return;
        };
        let _ = stop.send(());
        // b4297 RECEIPT-lin-pvwt3 note: a test failing (a knife biting) while its seats still
        // run must fail, not hang: waiting for aibd's stop under a panic held 12 knives to the
        // 300 s knife limit (INVALID, not counted as bites). The process ends the rest.
        if std::thread::panicking() {
            // b4297 RECEIPT-andymini-a0e-65be8029a: returning was not enough for three knives
            // (TR held_as_user_page, knives-34 own_tree, knives-15 set_data_dir): the test's
            // runtime, dropped after the panic, waits for aibd's blocking threads, and with
            // seats still open those never end, so the binary sat to the 300 s limit again.
            // A failed test ends its binary within 30 s instead (rc 101, as a failed test
            // does); the failure is already printed above this line. The wait is always the
            // full 30 s (nothing sends on the channel): if the binary is gone by then, so is
            // this thread. So a test using this Daemon must not be #[should_panic] (5d33a
            // PREREAD-harnessexit N2): its expected panic would end the binary 101.
            //
            // #869 round 2, crate knives 3/3 (knives-04-seat-input.sh, second knife on
            // a_provider_that_stops_reading_gets_busy_not_a_hang: MISSES rc=101 failed=[]): the
            // exit came before the test runner printed the failed test's line, and
            // scripts/knife.py counts a bite only when it reads `test <name> ... FAILED`. So
            // the line is written here before the exit: the name is this thread's (the test
            // runner names a test's thread after the test), written to stdout itself, which
            // the runner's capture of `print!` does not hold back.
            let name = std::thread::current()
                .name()
                .unwrap_or("unnamed_test")
                .to_owned();
            std::thread::spawn(move || {
                let (_keep, rx) = std::sync::mpsc::channel::<()>();
                if rx.recv_timeout(std::time::Duration::from_secs(30)).is_err() {
                    use std::io::Write;
                    let mut out = std::io::stdout();
                    let _ = writeln!(
                        out,
                        "\nharness: the test binary still runs 30 s after a test failed; \
                         it exits 101\ntest {name} ... FAILED"
                    );
                    let _ = out.flush();
                    std::process::exit(101);
                }
            });
            return;
        }
        let (tx, rx) = std::sync::mpsc::channel();
        rt.spawn(async move {
            let _ = h.await;
            let _ = tx.send(());
        });
        if rx.recv_timeout(std::time::Duration::from_secs(60)).is_err() {
            // 5d33a N: say so, a stop that never ends is worth seeing in the test output
            eprintln!("harness: aibd did not stop within 60 s; the test goes on");
        }
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
    // only whole lines: the fake provider may be writing the last one right now (a9136
    // a9136-imp-ab7c1c0eb: seat_codex parsed half a line, EOF while parsing)
    let whole = text.rfind('\n').map_or("", |i| &text[..=i]);
    whole
        .lines()
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
