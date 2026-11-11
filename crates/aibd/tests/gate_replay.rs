#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! The preview.3 gate (1ced7 GATE-RESULT-preview3-1ced7-1109, 11 steps on a real Mac
//! with a real `claude -p`) replayed in CI without Claude (Andy 11-09: CI uses recorded
//! data, the real CLI only on the gate machine).
//!
//! The "provider" is `/bin/sh` printing a recorded `claude -p --output-format
//! stream-json` answer (`fixtures/claude-p-haiku-reply-ok.jsonl`, scrubbed: no paths,
//! ids, skills or plugins) with `/bin/cat` and then ending. Like claude, it forks a
//! child that ends at once and then ends itself: the shape that left slots held on
//! macOS (G1). `/bin/sh` is an interpreter, so the policy sets `interpreters_ok`, which
//! only Rust code can; the allow-list itself is covered by `sessions_allowlist.rs`.
//!
//! Gate step ⇒ here:
//! - 1 start, health, a second aibd on the same folder refused ⇒ `the_gate_in_order`
//! - 2 seat, listed, no token 401 ⇒ same
//! - 3 seat route starts the provider, `argv[0]` the real path; unlisted ⇒ 403 + audit
//! - 4 output = the recording line for line, stdout types, sequence 1..n, both streams
//!   closed, reading from a middle cursor neither repeats nor skips
//! - 5 a long provider stopped midway: its child is gone
//! - 6 clean stop + restart: seat and sessions read back, the output is the same events
//! - 8 no token in the data folder's files; folder 0700, database 0600
//! - 9 nothing of ours left running
//!
//! Not here (UNVERIFIED in CI, gate machine only): step 0 / 0b the real CLI and its
//! environment (E5 is `sessions_allowlist.rs`), step 7 `kill -9` of aibd (needs the
//! binary, not an in-process server), Windows.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::allowlist::{Program, SessionPolicy};
use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

const RECORDING: &str = include_str!("fixtures/claude-p-haiku-reply-ok.jsonl");

fn policy() -> SessionPolicy {
    SessionPolicy {
        programs: vec![Program {
            name: "fake-claude".into(),
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

fn config(dir: &Path, max: usize) -> Config {
    Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: max,
        session_policy: policy(),
    }
}

async fn start(dir: &Path, max: usize) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = config(dir, max);
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

impl Daemon {
    fn view(&self) -> View {
        View {
            addr: self.addr,
            token: self.token.clone(),
        }
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

#[derive(Clone)]
struct View {
    addr: SocketAddr,
    token: String,
}

impl View {
    fn raw(&self, method: &str, path: &str, body: Option<&str>, auth: bool) -> (u16, String) {
        let port = self.addr.port();
        let mut req =
            format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n");
        if auth {
            req += &format!("Authorization: Bearer {}\r\n", self.token);
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
        let mut s = TcpStream::connect(self.addr).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
        s.write_all(req.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
        let body = out
            .split_once("\r\n\r\n")
            .map(|(_, b)| b.to_string())
            .unwrap_or_default();
        (code, body)
    }

    fn call(&self, method: &str, path: &str, body: Option<&str>) -> (u16, serde_json::Value) {
        let (c, b) = self.raw(method, path, body, true);
        (
            c,
            serde_json::from_str(&b).unwrap_or(serde_json::Value::Null),
        )
    }

    fn start_on(&self, seat: &str, argv: &[String]) -> (u16, serde_json::Value) {
        let body = serde_json::json!({ "argv": argv }).to_string();
        self.call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(&body),
        )
    }

    fn session(&self, id: &str) -> serde_json::Value {
        let (c, s) = self.call("GET", &format!("/api/v1/sessions/{id}"), None);
        assert_eq!(c, 200, "{s}");
        s
    }

    /// The session once it is no longer `running` (20 s at most).
    fn ended(&self, id: &str) -> serde_json::Value {
        let end = Instant::now() + Duration::from_secs(20);
        loop {
            let s = self.session(id);
            if s["state"] != "running" {
                return s;
            }
            assert!(Instant::now() < end, "still running: {s}");
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    /// Every output item, once both streams closed (20 s at most).
    fn output(&self, id: &str) -> Vec<serde_json::Value> {
        let end = Instant::now() + Duration::from_secs(20);
        loop {
            let (c, o) = self.call("GET", &format!("/api/v1/sessions/{id}/output"), None);
            assert_eq!(c, 200, "{o}");
            let items = o["output"].as_array().cloned().unwrap_or_default();
            let closed = items
                .iter()
                .filter(|e| e["type"] == "session.output_closed")
                .count();
            if closed == 2 {
                return items;
            }
            assert!(Instant::now() < end, "output of {id} never closed: {o}");
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    fn events(&self) -> Vec<serde_json::Value> {
        let (c, evs) = self.call("GET", "/api/v1/events?limit=500", None);
        assert_eq!(c, 200, "{evs}");
        evs["events"].as_array().unwrap().clone()
    }
}

fn stdout(items: &[serde_json::Value]) -> Vec<String> {
    items
        .iter()
        .filter(|e| e["type"] == "session.output" && e["payload"]["stream"] == "stdout")
        .map(|e| e["payload"]["line"].as_str().unwrap().to_string())
        .collect()
}

/// The fake provider: print the recording with a child (`cat`), then end. The trailing
/// `exit 0` keeps `sh` from exec-ing `cat`, so there really is a fork.
fn fake_claude(recording: &Path, tail: &str) -> Vec<String> {
    vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("/bin/cat '{}'; {tail}", recording.display()),
    ]
}

fn alive(pid: u32) -> bool {
    let o = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let st = String::from_utf8_lossy(&o.stdout).trim().to_string();
    !st.is_empty() && !st.starts_with('Z')
}

fn read_pid(f: &Path) -> u32 {
    let end = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(p) = std::fs::read_to_string(f)
            .ok()
            .and_then(|s| s.trim().parse().ok())
        {
            return p;
        }
        assert!(Instant::now() < end, "{} never written", f.display());
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn gone_within(pid: u32, d: Duration) -> bool {
    let end = Instant::now() + d;
    while Instant::now() < end {
        if !alive(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    !alive(pid)
}

fn write_recording(dir: &Path) -> PathBuf {
    let f = dir.join("recording.jsonl");
    std::fs::write(&f, RECORDING).unwrap();
    f
}

/// Gate steps 1-6, 8, 9 in the gate's order on one data folder.
#[tokio::test(flavor = "multi_thread")]
async fn the_gate_in_order() {
    let tmp = tempfile::tempdir().unwrap();
    let rec = write_recording(tmp.path());
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let want: Vec<String> = RECORDING.lines().map(str::to_string).collect();
    assert_eq!(want.len(), 4, "the recording has 4 lines");

    // 1: start, health; a second aibd on the same folder does not start
    let mut d = start(&data, 8).await;
    let second = server::run(config(&data, 8), async {}, |_| {}).await;
    assert!(
        matches!(second, Err(server::StartError::AlreadyRunning)),
        "{second:?}"
    );
    let v = d.view();
    let token1 = v.token.clone();
    let rec2 = rec.clone();
    let pidf = tmp.path().join("long.pid");
    let pidf2 = pidf.clone();
    let (seat, first, first_out) = tokio::task::spawn_blocking(move || {
        let (c, h) = v.call("GET", "/api/v1/health", None);
        assert_eq!((c, h["status"].as_str()), (200, Some("ok")), "{h}");

        // 2: a seat; it is listed; no token ⇒ 401
        let (c, s) = v.call("POST", "/api/v1/seats", Some(r#"{"name":"gate-1ced7"}"#));
        assert_eq!(c, 201, "{s}");
        let seat = s["seat_id"].as_str().unwrap().to_string();
        let (c, l) = v.call("GET", "/api/v1/seats", None);
        assert_eq!(c, 200, "{l}");
        assert!(
            l["seats"]
                .as_array()
                .unwrap()
                .iter()
                .any(|x| x["seat_id"] == seat.as_str()),
            "{l}"
        );
        let (c, _) = v.raw("GET", "/api/v1/seats", None, false);
        assert_eq!(c, 401);

        // 3: the seat route starts the provider; what runs is the real path
        let (c, s) = v.start_on(&seat, &fake_claude(&rec2, "exit 0"));
        assert_eq!(c, 201, "{s}");
        let real = std::fs::canonicalize("/bin/sh").unwrap();
        assert_eq!(s["argv"][0], real.to_str().unwrap(), "{s}");
        assert_eq!(s["seat_id"], seat.as_str(), "{s}");
        let first = s["id"].as_str().unwrap().to_string();
        //    an unlisted program ⇒ 403 + one audit record, nothing started
        let (c, r) = v.start_on(&seat, &["/bin/echo".to_string(), "x".to_string()]);
        assert_eq!(
            (c, r["code"].as_str()),
            (403, Some("program_not_allowed")),
            "{r}"
        );
        let evs = v.events();
        let rejected = evs
            .iter()
            .filter(|e| e["type"] == "session.spawn_rejected")
            .count();
        assert_eq!(rejected, 1, "{evs:?}");

        // 4: the output is the recording, line for line, in one gap-free sequence
        let items = v.output(&first);
        assert_eq!(stdout(&items), want, "{items:?}");
        let types: Vec<String> = stdout(&items)
            .iter()
            .map(|l| {
                let j: serde_json::Value = serde_json::from_str(l).unwrap();
                j["type"].as_str().unwrap().to_string()
            })
            .collect();
        assert_eq!(
            types,
            ["system", "assistant", "rate_limit_event", "result"],
            "{types:?}"
        );
        let seqs: Vec<i64> = items
            .iter()
            .map(|e| e["sequence"].as_i64().unwrap())
            .collect();
        assert_eq!(seqs, (1..=seqs.len() as i64).collect::<Vec<_>>());
        //    from a middle cursor: the rest, no repeat, no skip
        let (c, page) = v.call(
            "GET",
            &format!("/api/v1/sessions/{first}/output?after=2"),
            None,
        );
        assert_eq!(c, 200, "{page}");
        assert_eq!(
            page["output"].as_array().unwrap().as_slice(),
            &items[2..],
            "{page}"
        );
        let s = v.ended(&first);
        assert_ne!(s["state"], "running", "{s}");

        // 5: a long provider (its child sleeps) stopped midway: the child is gone
        let long = fake_claude(
            &rec2,
            &format!("/bin/sleep 60 & echo $! > '{}'; wait", pidf2.display()),
        );
        let (c, s) = v.start_on(&seat, &long);
        assert_eq!(c, 201, "{s}");
        let lid = s["id"].as_str().unwrap().to_string();
        let child = read_pid(&pidf2);
        assert!(alive(child), "the long provider's child is not running");
        let (c, st) = v.call("DELETE", &format!("/api/v1/sessions/{lid}"), None);
        assert_eq!(c, 200, "{st}");
        assert!(
            gone_within(child, Duration::from_secs(10)),
            "pid {child} outlived the stop"
        );
        let s = v.ended(&lid);
        assert_ne!(s["state"], "running", "{s}");
        (seat, first, items)
    })
    .await
    .unwrap();

    // 6: a clean stop, then a restart on the same folder: everything reads back
    d.shutdown().await;
    let mut d = start(&data, 8).await;
    let v = d.view();
    let token2 = v.token.clone();
    tokio::task::spawn_blocking(move || {
        let (c, l) = v.call("GET", "/api/v1/seats", None);
        assert_eq!(c, 200, "{l}");
        assert!(
            l["seats"]
                .as_array()
                .unwrap()
                .iter()
                .any(|x| x["seat_id"] == seat.as_str()),
            "the seat did not come back: {l}"
        );
        let (c, s) = v.call("GET", &format!("/api/v1/seats/{seat}/sessions"), None);
        assert_eq!(c, 200, "{s}");
        assert_eq!(s["sessions"].as_array().map(Vec::len), Some(2), "{s}");
        let (c, o) = v.call("GET", &format!("/api/v1/sessions/{first}/output"), None);
        assert_eq!(c, 200, "{o}");
        assert_eq!(
            o["output"].as_array().unwrap(),
            &first_out,
            "the same events"
        );
    })
    .await
    .unwrap();
    d.shutdown().await;

    // 8: neither run's token is in any file under the data folder but `aibd.token`
    //    itself; folder 0700, database 0600
    use std::os::unix::fs::PermissionsExt;
    let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode(&data), 0o700, "data dir");
    assert_eq!(mode(&data.join("aibd.sqlite")), 0o600, "database");
    let mut todo = vec![data.clone()];
    let mut scanned = 0;
    while let Some(dir) = todo.pop() {
        for e in std::fs::read_dir(&dir).unwrap() {
            let p = e.unwrap().path();
            if p.is_dir() {
                todo.push(p);
                continue;
            }
            if p.file_name().is_some_and(|n| n == "aibd.token") {
                continue;
            }
            let hay = String::from_utf8_lossy(&std::fs::read(&p).unwrap()).into_owned();
            for t in [&token1, &token2] {
                assert!(!hay.contains(t.trim()), "a token is in {}", p.display());
            }
            scanned += 1;
        }
    }
    assert!(scanned > 0, "nothing scanned under {}", data.display());

    // 9: nothing of ours left: the long provider's child is gone (checked in 5)
    let child = read_pid(&pidf);
    assert!(!alive(child), "pid {child} still running after the gate");
}

/// G1 regression (gate finding, a63ab: "連開 10 次 claude 都開得了"): with ONE slot, ten
/// fake providers in a row (each forks a child that ends at once, then ends) all start,
/// and each gives its slot back. Before G1 this failed on macOS (the stop was
/// `stop_unconfirmed` / `fork_watch_child_unreadable`, the slot stayed held, the second
/// start was `too_many_sessions`). G1 is on main since #602 (preview.4), so it runs on
/// macOS too (043dd #601 L1: with the ignore it ran nowhere, aibd.yml has no Linux cell).
#[tokio::test(flavor = "multi_thread")]
async fn ten_providers_in_a_row_on_one_slot() {
    let tmp = tempfile::tempdir().unwrap();
    let rec = write_recording(tmp.path());
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 1).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let (c, s) = v.call("POST", "/api/v1/seats", Some(r#"{"name":"g1-1ced7"}"#));
        assert_eq!(c, 201, "{s}");
        let seat = s["seat_id"].as_str().unwrap().to_string();
        for i in 0..10 {
            let (c, s) = v.start_on(&seat, &fake_claude(&rec, "exit 0"));
            assert_eq!(c, 201, "run {i}: {s}");
            let id = s["id"].as_str().unwrap().to_string();
            assert_eq!(v.output(&id).len(), 6, "run {i}: 4 lines + 2 closed");
            let end = Instant::now() + Duration::from_secs(20);
            loop {
                let s = v.session(&id);
                if s["state"] != "running" && s["holds_slot"] == false {
                    break;
                }
                assert!(Instant::now() < end, "run {i} keeps its slot: {s}");
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    })
    .await
    .unwrap();
    d.shutdown().await;
}
