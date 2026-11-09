#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Sessions API on a real aibd (127.0.0.1:0, temp data dir) with real
//! processes (contract §7, a63ab / bb7dc H1-H3). POSIX only: the Windows
//! side is 043dd's `sessions_windows.rs`. Every test collects exactly the
//! pids it was told about, never a wider kill.
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

struct Daemon {
    addr: SocketAddr,
    token: String,
    data: PathBuf,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

/// Pids a test learned; SIGKILLed on drop whatever happened.
struct Own(Vec<u32>);

impl Drop for Own {
    fn drop(&mut self) {
        for p in &self.0 {
            let _ = Command::new("kill")
                .args(["-9", &p.to_string()])
                .stderr(std::process::Stdio::null())
                .status();
        }
    }
}

/// A listed native file (a copy of `/usr/bin/true`) without any execute bit: it resolves and
/// passes the gate, then exec fails (the "a failed start gives its slot back" case).
fn not_executable(dir: &Path) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let f = dir.join("not-executable");
    std::fs::copy("/usr/bin/true", &f).unwrap();
    std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o644)).unwrap();
    f
}

fn fx(name: &str, path: impl Into<PathBuf>) -> Program {
    Program {
        name: name.into(),
        path: path.into(),
        signer: None,
    }
}

/// The policy most tests use: exactly the programs / env names they start. `/bin/sh` is
/// a shell, so these in-process tests set `interpreters_ok` (only Rust code can).
fn fixtures(dir: &Path) -> SessionPolicy {
    SessionPolicy {
        programs: vec![
            fx("sh", "/bin/sh"),
            fx("sleep", "/bin/sleep"),
            fx("true", "/usr/bin/true"), // macOS has no /bin/true
            fx("not-executable", not_executable(dir)),
        ],
        env: vec!["MY_VAR".into(), "MY_TOKEN".into()],
        interpreters_ok: true,
    }
}

async fn start(dir: &Path, max: usize) -> Daemon {
    start_policy(dir, max, fixtures(dir)).await
}

async fn start_policy(dir: &Path, max: usize, session_policy: SessionPolicy) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: max,
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
    #[allow(clippy::disallowed_methods)] // test deadline only
    let st = tokio::time::timeout(Duration::from_secs(20), st_rx)
        .await
        .unwrap()
        .unwrap();
    Daemon {
        addr: st.addr,
        token: std::fs::read_to_string(&st.token_path).unwrap(),
        data: dir.to_path_buf(),
        stop: Some(stop_tx),
        h: Some(h),
    }
}

impl Daemon {
    /// One raw HTTP/1.1 request (blocking; call from spawn_blocking or a
    /// plain thread). `auth` false = no Authorization header.
    fn raw(
        &self,
        method: &str,
        path: &str,
        body: Option<&str>,
        extra: &str,
        auth: bool,
    ) -> (u16, serde_json::Value) {
        let port = self.addr.port();
        let mut req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n{extra}"
        );
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
        let json = out
            .split_once("\r\n\r\n")
            .and_then(|(_, b)| serde_json::from_str(b).ok())
            .unwrap_or(serde_json::Value::Null);
        (code, json)
    }

    fn call(&self, method: &str, path: &str, body: Option<&str>) -> (u16, serde_json::Value) {
        self.raw(method, path, body, "", true)
    }

    fn post(&self, argv: &[&str]) -> (u16, serde_json::Value) {
        self.call(
            "POST",
            "/api/v1/sessions",
            Some(&serde_json::json!({ "argv": argv }).to_string()),
        )
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

fn alive(pid: u32) -> bool {
    let o = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let st = String::from_utf8_lossy(&o.stdout).trim().to_string();
    !st.is_empty() && !st.starts_with('Z')
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

/// `sh` that starts `sleep 60` (pid written to `f`), then `tail`.
fn sh_gc(f: &Path, tail: &str) -> Vec<String> {
    vec![
        "/bin/sh".into(),
        "-c".into(),
        format!("sleep 60 & echo $! > {}; {tail}", f.display()),
    ]
}

fn wait_state(d: &Daemon, id: &str, not: &str) -> serde_json::Value {
    let end = Instant::now() + Duration::from_secs(15);
    loop {
        let (c, s) = d.call("GET", &format!("/api/v1/sessions/{id}"), None);
        assert_eq!(c, 200, "{s}");
        if s["state"] != not {
            return s;
        }
        assert!(Instant::now() < end, "still {not}: {s}");
        std::thread::sleep(Duration::from_millis(50));
    }
}

macro_rules! blocking {
    ($d:expr, |$x:ident| $body:expr) => {{
        let $x = $d.clone();
        tokio::task::spawn_blocking(move || $body).await.unwrap()
    }};
}

// Daemon is not Clone (it owns the stop handle); a cheap view for threads.
#[derive(Clone)]
struct View {
    addr: SocketAddr,
    token: String,
}

impl View {
    fn d(&self) -> Daemon {
        Daemon {
            addr: self.addr,
            token: self.token.clone(),
            data: PathBuf::new(),
            stop: None,
            h: None,
        }
    }
}

fn view(d: &Daemon) -> View {
    View {
        addr: d.addr,
        token: d.token.clone(),
    }
}

/// Contract §7 / knife `no-tree-stop`: DELETE stops the grandchild, answers
/// after the stop with an honest macOS result, and frees the slot.
#[tokio::test(flavor = "multi_thread")]
async fn delete_stops_the_grandchild_and_reports_honestly() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let f = tmp.path().join("gc");
    let argv = sh_gc(&f, "wait");
    let v = view(&d);
    let (code, s) = blocking!(v, |v| v.d().call(
        "POST",
        "/api/v1/sessions",
        Some(&serde_json::json!({"argv": argv}).to_string())
    ));
    assert_eq!(code, 201, "{s}");
    assert_eq!(s["state"], "running");
    assert!(s["id"].as_str().unwrap().starts_with("s_"));
    let id = s["id"].as_str().unwrap().to_owned();
    let gc = read_pid(&f);
    let _own = Own(vec![gc, s["pid"].as_u64().unwrap() as u32]);
    let path = format!("/api/v1/sessions/{id}?grace_ms=500");
    let (code, s) = blocking!(v, |v| v.d().call("DELETE", &path, None));
    assert_eq!(code, 200, "{s}");
    assert!(
        !alive(gc),
        "grandchild {gc} still running after DELETE answered: {s}"
    );
    // H2: macOS never claims clean, so never `killed`
    assert_eq!(s["state"], "stop_unconfirmed", "{s}");
    assert_eq!(s["ended_by"], "api");
    assert_eq!(s["stop"]["status"], "unverified", "{s}");
    assert!(!s["stop"]["blind_spots"].as_array().unwrap().is_empty());
    assert_eq!(s["stop"]["survivors"], serde_json::json!([]));
    assert!(s.get("env").is_none());
    // the reason decides the slot (whitelist): observed_clean frees it, a
    // "could not look" reason keeps it until a DELETE re-checks
    assert_eq!(
        s["holds_slot"],
        s["stop"]["reason"] != "observed_clean",
        "{s}"
    );
    // a second DELETE answers with the session again (re-checked or kept)
    let path2 = format!("/api/v1/sessions/{id}?grace_ms=0");
    let (code, again) = blocking!(v, |v| v.d().call("DELETE", &path2, None));
    assert_eq!((code, &again["id"]), (200, &s["id"]));
    assert_eq!(
        again["holds_slot"],
        again["stop"]["reason"] != "observed_clean",
        "{again}"
    );
    d.shutdown().await;
}

/// Contract §1 / knife `no-stop-after-root-exit`: the root exits by itself,
/// its grandchild is stopped by the one stop that follows.
#[tokio::test(flavor = "multi_thread")]
async fn a_root_that_exits_by_itself_gets_its_leftovers_stopped() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let f = tmp.path().join("gc");
    let argv = sh_gc(&f, "sleep 0.3; exit 3");
    let v = view(&d);
    let (code, s) = blocking!(v, |v| v.d().call(
        "POST",
        "/api/v1/sessions",
        Some(&serde_json::json!({"argv": argv}).to_string())
    ));
    assert_eq!(code, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_owned();
    let gc = read_pid(&f);
    let _own = Own(vec![gc]);
    let s = blocking!(v, |v| wait_state(&v.d(), &id, "running"));
    assert_eq!(s["ended_by"], "root_exit", "{s}");
    assert_eq!(s["exit_code"], 3, "{s}");
    assert_eq!(s["state"], "stop_unconfirmed", "{s}");
    assert!(!s["stop"].is_null(), "{s}");
    assert!(
        gone_within(gc, Duration::from_secs(5)),
        "grandchild {gc} left behind: {s}"
    );
    d.shutdown().await;
}

/// Contract §5 / knife `no-shutdown-stop`: aibd ending stops every session's
/// tree before it returns, and says so in the events.
#[tokio::test(flavor = "multi_thread")]
async fn aibd_shutdown_stops_every_session() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    let mut gcs = Vec::new();
    for n in 0..2 {
        let f = tmp.path().join(format!("gc{n}"));
        let argv = sh_gc(&f, "wait");
        let (code, s) = blocking!(v, |v| v.d().call(
            "POST",
            "/api/v1/sessions",
            Some(&serde_json::json!({"argv": argv}).to_string())
        ));
        assert_eq!(code, 201, "{s}");
        gcs.push(read_pid(&f));
    }
    let _own = Own(gcs.clone());
    let data = d.data.clone();
    d.shutdown().await;
    for gc in &gcs {
        assert!(!alive(*gc), "grandchild {gc} survived aibd's shutdown");
    }
    let store = aib_store::Store::open(&data.join("aibd.sqlite")).unwrap();
    let evs = store.events_after(0, 500).unwrap();
    let ends: Vec<_> = evs
        .iter()
        .filter(|e| {
            e.event_type == "session.stop_unconfirmed" && e.payload["ended_by"] == "shutdown"
        })
        .collect();
    assert_eq!(ends.len(), 2, "{evs:?}");
}

/// Contract §7 limits / errors, and the guard on every new route (bb7dc:
/// not only GET).
#[tokio::test(flavor = "multi_thread")]
async fn limits_errors_and_the_guard_on_every_route() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 2).await;
    let v = view(&d);
    let ne = tmp.path().join("not-executable").display().to_string();
    blocking!(v, |v| {
        let d = v.d();
        let (c, s) = d.post(&["/bin/sleep", "60"]);
        assert_eq!(c, 201, "{s}");
        // a failed start gives its reserved slot back: the second still fits
        let (c, s) = d.post(&[ne.as_str()]);
        assert_eq!((c, s["code"].as_str()), (422, Some("spawn_failed")), "{s}");
        assert_eq!(s["message"], "could not start: eacces");
        // a path that does not exist is not a listed program
        let (c, s) = d.post(&["/no/such/program"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("program_not_allowed")),
            "{s}"
        );
        let (c, s) = d.post(&["/bin/sleep", "60"]);
        assert_eq!(c, 201, "slot leaked by the failed spawn: {s}");
        let (c, s) = d.post(&["/bin/sleep", "60"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (429, Some("too_many_sessions")),
            "{s}"
        );
        let env = |k: &str| serde_json::json!({"argv": ["/bin/true"], "env": {k: "x"}}).to_string();
        let (c, s) = d.call("POST", "/api/v1/sessions", Some(&env("PATH")));
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("env_not_allowed")),
            "{s}"
        );
        let (c, s) = d.post(&["sleep", "1"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("invalid_request")),
            "{s}"
        );
        let (c, s) = d.raw(
            "POST",
            "/api/v1/sessions",
            Some(r#"{"argv":["/bin/true"]}"#),
            "",
            false,
        );
        assert_eq!((c, s["code"].as_str()), (401, Some("unauthorized")), "{s}");
        let (c, s) = d.raw("DELETE", "/api/v1/sessions/s_0?token=abc", None, "", true);
        assert_eq!((c, s["code"].as_str()), (400, Some("token_in_url")), "{s}");
        let (c, s) = d.raw(
            "DELETE",
            "/api/v1/sessions/s_0",
            None,
            "Origin: https://evil.example\r\n",
            true,
        );
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("origin_not_allowed")),
            "{s}"
        );
        let (c, s) = d.raw(
            "POST",
            "/api/v1/sessions",
            Some(r#"{"argv":["/bin/true"]}"#),
            "Origin: https://evil.example\r\n",
            true,
        );
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("origin_not_allowed")),
            "{s}"
        );
        let (c, s) = d.call("PUT", "/api/v1/sessions", None);
        assert_eq!(
            (c, s["code"].as_str()),
            (405, Some("method_not_allowed")),
            "{s}"
        );
        let (c, s) = d.call("GET", "/api/v1/sessions/s_nope", None);
        assert_eq!(
            (c, s["code"].as_str()),
            (404, Some("session_not_found")),
            "{s}"
        );
        let (c, s) = d.call("DELETE", "/api/v1/sessions/s_nope?grace_ms=abc", None);
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("invalid_request")),
            "{s}"
        );
        // wrong content type: never parsed as a session
        let port = d.addr.port();
        let raw = format!("POST /api/v1/sessions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nAuthorization: Bearer {}\r\nContent-Type: text/plain\r\nContent-Length: 22\r\n\r\n{{\"argv\":[\"/bin/true\"]}}", d.token);
        let mut s = TcpStream::connect(d.addr).unwrap();
        s.write_all(raw.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        assert!(out.starts_with("HTTP/1.1 400"), "{out}");
        let (c, list) = d.call("GET", "/api/v1/sessions", None);
        assert_eq!(c, 200);
        assert!(list["sessions"].as_array().unwrap().len() >= 2, "{list}");
    });
    d.shutdown().await;
}

/// bb7dc H3: concurrent POSTs never get past the limit (the slot is reserved
/// under the lock before the spawn).
#[tokio::test(flavor = "multi_thread")]
async fn h3_concurrent_posts_never_exceed_the_limit() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 3).await;
    let v = view(&d);
    let codes = blocking!(v, |v| {
        let b = std::sync::Arc::new(std::sync::Barrier::new(10));
        let hs: Vec<_> = (0..10)
            .map(|_| {
                let (v, b) = (v.clone(), b.clone());
                std::thread::spawn(move || {
                    b.wait();
                    v.d().post(&["/bin/sleep", "60"]).0
                })
            })
            .collect();
        hs.into_iter()
            .map(|h| h.join().unwrap())
            .collect::<Vec<u16>>()
    });
    assert_eq!(codes.iter().filter(|c| **c == 201).count(), 3, "{codes:?}");
    assert_eq!(codes.iter().filter(|c| **c == 429).count(), 7, "{codes:?}");
    d.shutdown().await;
}

/// M1 gate (a63ab 11-09): a program the policy does not allow and an env name it does not
/// list are refused before anything starts; nothing is listed and no session event is
/// written for them.
#[tokio::test(flavor = "multi_thread")]
async fn m1_gate_refuses_unlisted_programs_and_env_names_before_starting() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    blocking!(v, |v| {
        let d = v.d();
        let (c, s) = d.post(&["/bin/echo", "should-not-run"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("program_not_allowed")),
            "{s}"
        );
        assert!(
            !s.to_string().contains("/bin/echo"),
            "message echoes the request: {s}"
        );
        // `..` is refused before anything is resolved
        let (c, s) = d.post(&["/bin/../bin/true"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("program_not_allowed")),
            "{s}"
        );
        let body =
            serde_json::json!({"argv": ["/usr/bin/true"], "env": {"NOT_LISTED": "sk-SECRET-ENV"}})
                .to_string();
        let (c, s) = d.call("POST", "/api/v1/sessions", Some(&body));
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("env_not_allowed")),
            "{s}"
        );
        let (c, s) = d.call("GET", "/api/v1/sessions", None);
        assert_eq!(c, 200);
        assert_eq!(s["sessions"].as_array().map(Vec::len), Some(0), "{s}");
        let (c, evs) = d.call("GET", "/api/v1/events?limit=500", None);
        assert_eq!(c, 200);
        let started = evs["events"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|e| e["type"] == "session.started")
            .count();
        assert_eq!(started, 0, "{evs}");
        // a63ab 19:23: each refusal is audited, folded per token + code per minute
        // (two program refusals ⇒ one record), with exactly these payload keys
        let rejected: Vec<&serde_json::Value> = evs["events"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|e| e["type"] == "session.spawn_rejected")
            .collect();
        assert_eq!(rejected.len(), 2, "{evs}");
        let mut codes: Vec<&str> = rejected
            .iter()
            .map(|e| e["payload"]["code"].as_str().unwrap())
            .collect();
        codes.sort_unstable();
        assert_eq!(codes, ["env_not_allowed", "program_not_allowed"]);
        for e in &rejected {
            assert_eq!(e["aggregate_type"], "security", "{e}");
            let mut keys: Vec<&str> = e["payload"]
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect();
            keys.sort_unstable();
            assert_eq!(
                keys,
                ["at", "code", "folded_before", "program", "token_id"],
                "{e}"
            );
            assert_eq!(e["payload"]["token_id"], "startup", "{e}");
            assert_eq!(e["payload"]["folded_before"], 0, "{e}");
        }
        let text = evs.to_string();
        for secret in ["should-not-run", "sk-SECRET-ENV", "NOT_LISTED", "/bin/echo"] {
            assert!(!text.contains(secret), "{secret} in {text}");
        }
    });
    d.shutdown().await;
}

/// M1 gate with the policy aibd really runs (`interpreters_ok` false): a shell is refused
/// even when listed in code, and an npm-style provider gets its own code (I2, N3).
#[tokio::test(flavor = "multi_thread")]
async fn m1_gate_refuses_shells_and_npm_installs_over_http() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let tmp = tempfile::tempdir().unwrap();
    let link = tmp.path().join("fixture-ok");
    symlink("/bin/sh", &link).unwrap();
    let js = tmp.path().join("cli.js");
    std::fs::write(&js, b"#!/usr/bin/env node\n").unwrap();
    std::fs::set_permissions(&js, std::fs::Permissions::from_mode(0o755)).unwrap();
    let claude = tmp.path().join("claude");
    symlink(&js, &claude).unwrap();
    let policy = SessionPolicy {
        programs: vec![
            fx("fixture-ok", link.clone()),
            fx("claude", claude.clone()),
            fx("sleep", "/bin/sleep"),
        ],
        env: vec![],
        interpreters_ok: false,
    };
    let mut d = start_policy(tmp.path(), 8, policy).await;
    let v = view(&d);
    let (link, claude) = (link.display().to_string(), claude.display().to_string());
    blocking!(v, |v| {
        let d = v.d();
        for p in [link.as_str(), "/bin/sh"] {
            let (c, s) = d.post(&[p, "-c", "exit 0"]);
            assert_eq!(
                (c, s["code"].as_str()),
                (403, Some("program_not_allowed")),
                "{p}: {s}"
            );
        }
        let (c, s) = d.post(&[claude.as_str()]);
        assert_eq!(
            (c, s["code"].as_str()),
            (403, Some("provider_install_unsupported")),
            "{s}"
        );
        let (c, s) = d.call("GET", "/api/v1/sessions", None);
        assert_eq!(c, 200);
        assert_eq!(s["sessions"].as_array().map(Vec::len), Some(0), "{s}");
        // 1ced7 #593 M2: the first layer's refusals are audited too
        let body = serde_json::json!({"argv": ["/bin/sleep", "1"], "env": {"DYLD_INSERT_LIBRARIES": "sk-DYLD-SECRET"}})
            .to_string();
        let (c, s) = d.call("POST", "/api/v1/sessions", Some(&body));
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("env_not_allowed")),
            "{s}"
        );
        let (c, s) = d.post(&["sleep", "1"]);
        assert_eq!(
            (c, s["code"].as_str()),
            (400, Some("invalid_request")),
            "{s}"
        );
        let (c, evs) = d.call("GET", "/api/v1/events?limit=500", None);
        assert_eq!(c, 200);
        let mut codes: Vec<&str> = evs["events"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|e| e["type"] == "session.spawn_rejected")
            .map(|e| e["payload"]["code"].as_str().unwrap())
            .collect();
        codes.sort_unstable();
        // program_not_allowed folded twice into one record; provider, env, argv[0] one each
        assert_eq!(
            codes,
            [
                "env_not_allowed",
                "invalid_request",
                "program_not_allowed",
                "provider_install_unsupported"
            ],
            "{evs}"
        );
        assert!(!evs.to_string().contains("sk-DYLD-SECRET"), "{evs}");
        // the gate is what refused them: a listed native program still starts
        let (c, s) = d.post(&["/bin/sleep", "60"]);
        assert_eq!(c, 201, "{s}");
    });
    d.shutdown().await;
}

/// Contract §4 / bb7dc: neither events nor GET carry env values or the full
/// argv's secret parts; events name argv[0]'s file name and the count.
#[tokio::test(flavor = "multi_thread")]
async fn events_never_carry_env_or_the_full_argv() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    let body = serde_json::json!({
        "argv": ["/bin/sh", "-c", "exit 0", "--password=hunter2-SECRET-ARG"],
        "env": {"MY_TOKEN": "sk-SECRET-ENV-xyz"},
    })
    .to_string();
    let (c, s) = blocking!(v, |v| v.d().call("POST", "/api/v1/sessions", Some(&body)));
    assert_eq!(c, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_owned();
    let id2 = id.clone();
    let got = blocking!(v, |v| wait_state(&v.d(), &id2, "running"));
    assert!(!got.to_string().contains("sk-SECRET-ENV-xyz"), "{got}");
    let (c, evs) = blocking!(v, |v| v.d().call("GET", "/api/v1/events?limit=500", None));
    assert_eq!(c, 200);
    let text = evs.to_string();
    assert!(!text.contains("sk-SECRET-ENV-xyz"), "{text}");
    assert!(!text.contains("hunter2-SECRET-ARG"), "{text}");
    let started = evs["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "session.started" && e["aggregate_id"] == id.as_str())
        .unwrap();
    // argv[0] is replaced by the real path (Linux: /bin/sh is dash)
    let sh = std::fs::canonicalize("/bin/sh").unwrap();
    assert_eq!(
        started["payload"]["argv0"],
        sh.file_name().unwrap().to_str().unwrap()
    );
    assert_eq!(started["payload"]["argc"], 4);
    d.shutdown().await;
}

/// a63ab ruling 3: nothing is inherited from aibd. stdin is the null
/// device; since preview.3 C stdout / stderr are pipes that aibd reads into
/// `session.output` events (so what the child prints lands in its own output,
/// never on aibd's stdout / stderr); the request env is applied and
/// AIBD_SESSION set. Windows still starts with three null handles
/// (UNVERIFIED, see the Windows tests).
#[tokio::test(flavor = "multi_thread")]
async fn the_child_gets_null_stdio_its_env_and_the_marker() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    let f = tmp.path().join("out");
    let script = format!(
        "r=; for n in 0 1 2; do if [ -c /dev/fd/$n ]; then r=\"$r $n chr\"; elif [ -p /dev/fd/$n ]; then r=\"$r $n pipe\"; else r=\"$r $n other\"; fi; done; echo \"$r\" > {f}; env >> {f}; echo to-stdout; echo to-stderr >&2",
        f = f.display()
    );
    let body = serde_json::json!({"argv": ["/bin/sh", "-c", script], "env": {"MY_VAR": "one"}})
        .to_string();
    let (c, s) = blocking!(v, |v| v.d().call("POST", "/api/v1/sessions", Some(&body)));
    assert_eq!(c, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_owned();
    let id2 = id.clone();
    blocking!(v, |v| wait_state(&v.d(), &id2, "running"));
    let out = std::fs::read_to_string(&f).unwrap();
    assert!(
        out.contains(" 0 chr"),
        "stdin is not the null device: {out}"
    );
    for n in 1..3 {
        assert!(
            out.contains(&format!(" {n} pipe")),
            "fd {n} is not aibd's output pipe: {out}"
        );
    }
    assert!(out.contains("MY_VAR=one"), "{out}");
    assert!(out.contains(&format!("AIBD_SESSION={id}")), "{out}");
    // what the child printed is in its own output, one line per stream
    let id3 = id.clone();
    let items = blocking!(v, |v| {
        let end = Instant::now() + Duration::from_secs(15);
        loop {
            let (c, s) = v
                .d()
                .call("GET", &format!("/api/v1/sessions/{id3}/output"), None);
            assert_eq!(c, 200, "{s}");
            let items = s["output"].as_array().unwrap().clone();
            let closed = items
                .iter()
                .filter(|e| e["type"] == "session.output_closed")
                .count();
            if closed == 2 {
                break items;
            }
            assert!(Instant::now() < end, "streams not closed: {s}");
            std::thread::sleep(Duration::from_millis(50));
        }
    });
    for (stream, line) in [("stdout", "to-stdout"), ("stderr", "to-stderr")] {
        assert!(
            items.iter().any(|e| e["type"] == "session.output"
                && e["payload"]["stream"] == stream
                && e["payload"]["line"] == line),
            "{stream} line missing: {items:?}"
        );
    }
    d.shutdown().await;
}

/// a63ab 20:32: an empty argument after argv[0] (claude `--setting-sources ""`) is
/// accepted and reaches the child as an empty argument, in its place.
#[tokio::test(flavor = "multi_thread")]
async fn empty_arguments_reach_the_child_as_they_are() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    let f = tmp.path().join("args");
    let script = format!(
        "printf '[%s]' \"$#\" \"$@\" > {}.tmp && mv {0}.tmp {0}",
        f.display()
    );
    let body = serde_json::json!({
        "argv": ["/bin/sh", "-c", script, "sh", "--setting-sources", "", "x", ""]
    })
    .to_string();
    let (c, s) = blocking!(v, |v| v.d().call("POST", "/api/v1/sessions", Some(&body)));
    assert_eq!(c, 201, "{s}");
    let f2 = f.clone();
    let got = blocking!(v, |_v| {
        let end = Instant::now() + Duration::from_secs(15);
        loop {
            if let Ok(x) = std::fs::read_to_string(&f2) {
                break x;
            }
            assert!(Instant::now() < end, "the child never wrote its arguments");
            std::thread::sleep(Duration::from_millis(50));
        }
    });
    assert_eq!(got, "[4][--setting-sources][][x][]");
    d.shutdown().await;
}

static ENTERED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// A start that is slow (it signals it began, waits, then really starts).
fn slow_spawn(
    spec: &aibd::platform::session::Spec,
    l: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    ENTERED.store(true, std::sync::atomic::Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(600));
    aibd::platform::session_unix::spawn(spec, l)
}

/// bb7dc H3: shutdown begins while a POST is still spawning. Shutdown waits
/// for that reservation; the POST sees the shutdown, stops its own tree and
/// answers 503: nothing it started is left outside the sweep.
#[test]
fn h3_a_post_racing_shutdown_leaves_nothing_behind() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(store, None, slow_spawn, 8, tmp.path().to_path_buf());
    let f = tmp.path().join("gc");
    let req = aibd::sessions::Request {
        argv: sh_gc(&f, "wait"),
        cwd: None,
        env: vec![],
    };
    let s2 = s.clone();
    let post = std::thread::spawn(move || s2.start(req));
    while !ENTERED.load(std::sync::atomic::Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(
        s.slots_in_use(),
        1,
        "the slot must be reserved before the spawn"
    );
    let busy = s.shutdown_all();
    // at the moment shutdown returns (aibd may exit right after), the racing
    // session is already stopped and recorded
    let all = s.list();
    assert_eq!(busy, 0);
    assert_eq!(all.len(), 1, "{all:?}");
    assert_eq!(all[0].ended_by, Some("shutdown"), "{all:?}");
    assert!(!alive(all[0].pid), "leader {} still running", all[0].pid);
    let res = post.join().unwrap();
    let err = res.unwrap_err();
    assert_eq!(err.0, aib_core::ErrorCode::ShuttingDown, "{err:?}");
    // the grandchild, if sh got to start it before the stop
    if let Some(gc) = std::fs::read_to_string(&f)
        .ok()
        .and_then(|x| x.trim().parse::<u32>().ok())
    {
        let _own = Own(vec![gc]);
        assert!(
            gone_within(gc, Duration::from_secs(5)),
            "grandchild {gc} escaped the shutdown"
        );
    }
    // and nothing new after the shutdown
    let late = s.start(aibd::sessions::Request {
        argv: vec!["/bin/true".into()],
        cwd: None,
        env: vec![],
    });
    assert_eq!(late.unwrap_err().0, aib_core::ErrorCode::ShuttingDown);
}

static FAKE_STOPS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

/// A tree whose first stop leaves a survivor (pid 999999, never signalled:
/// nothing here touches the OS) and whose second stop finds nothing.
struct Stubborn;

struct NeverExits;

impl aibd::platform::session::ExitWait for NeverExits {
    fn wait(&self, t: Duration) -> Option<aibd::platform::session::Exit> {
        std::thread::sleep(t.min(Duration::from_millis(50)));
        None
    }
}

impl aibd::platform::session::Running for Stubborn {
    fn pid(&self) -> u32 {
        999_999
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        let n = FAKE_STOPS.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Stop {
            status: if n == 0 {
                StopStatus::TreeNotClean
            } else {
                StopStatus::Unverified("observed_clean".into())
            },
            survivors: if n == 0 { vec![999_998] } else { vec![] },
            tree_size: 2,
            signals: vec![],
            blind_spots: vec!["env_cleared"],
        }
    }
}

fn stubborn_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Stubborn))
}

/// bb7dc / a63ab H2: a stop that leaves a survivor is `stop_unconfirmed`,
/// keeps its slot (the next POST is refused), and a second DELETE stops it
/// again instead of answering "already ended".
#[test]
fn h2_survivors_keep_the_slot_and_a_second_delete_stops_again() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        stubborn_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let req = || aibd::sessions::Request {
        argv: vec!["/bin/true".into()],
        cwd: None,
        env: vec![],
    };
    let rec = s.start(req()).unwrap();
    let first = s.stop(&rec.id, Duration::ZERO).unwrap();
    assert_eq!(first.state, "stop_unconfirmed");
    assert_eq!(first.stop.as_ref().unwrap().status, "tree_not_clean");
    assert_eq!(first.stop.as_ref().unwrap().survivors, vec![999_998]);
    assert_eq!(s.slots_in_use(), 1, "a survivor must keep the slot");
    assert!(first.holds_slot, "{first:?}");
    assert_eq!(
        s.start(req()).unwrap_err().0,
        aib_core::ErrorCode::TooManySessions
    );
    let second = s.stop(&rec.id, Duration::ZERO).unwrap();
    assert_eq!(
        FAKE_STOPS.load(std::sync::atomic::Ordering::SeqCst),
        2,
        "the second DELETE must stop again"
    );
    assert_eq!(
        second.state, "stop_unconfirmed",
        "observed_clean is still not clean"
    );
    assert_eq!(
        second.stop.as_ref().unwrap().reason.as_deref(),
        Some("observed_clean")
    );
    assert_eq!(s.slots_in_use(), 0);
    assert!(!second.holds_slot, "{second:?}");
    let kinds: Vec<String> = store
        .events_after(0, 100)
        .unwrap()
        .into_iter()
        .map(|e| e.event_type)
        .collect();
    assert!(!kinds.iter().any(|k| k == "session.killed"), "{kinds:?}");
    assert_eq!(
        kinds
            .iter()
            .filter(|k| *k == "session.stop_unconfirmed")
            .count(),
        2,
        "{kinds:?}"
    );
}

fn half_started(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Err(aibd::platform::session::SpawnFail {
        tag: "not_reaped".into(),
        left_running: Some(424_242),
    })
}

/// 043dd #539 M2: a start that failed but may have left a process keeps its
/// slot and says so (422 with the pid, a `session.spawn_failed` event).
#[test]
fn a_start_that_may_have_left_a_process_keeps_its_slot() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        half_started,
        1,
        tmp.path().to_path_buf(),
    );
    let req = aibd::sessions::Request {
        argv: vec!["/bin/true".into()],
        cwd: None,
        env: vec![],
    };
    let e = s.start(req.clone()).unwrap_err();
    assert_eq!(e.0, aib_core::ErrorCode::SpawnFailed);
    assert!(e.1.contains("pid 424242"), "{}", e.1);
    assert_eq!(s.slots_in_use(), 1);
    assert_eq!(
        s.start(req).unwrap_err().0,
        aib_core::ErrorCode::TooManySessions
    );
    let evs = store.events_after(0, 10).unwrap();
    assert_eq!(evs.len(), 1, "{evs:?}");
    assert_eq!(evs[0].event_type, "session.spawn_failed");
    assert_eq!(evs[0].payload["left_running"], 424_242);
}

/// A tree whose stop "could not look" (`candidates_unreadable`) the first
/// `UNREADABLE_FOR` times, then saw nothing (`observed_clean`). Touches no
/// process.
struct Unreadable(&'static std::sync::atomic::AtomicUsize, usize);

impl aibd::platform::session::Running for Unreadable {
    fn pid(&self) -> u32 {
        999_997
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        let n = self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let reason = if n < self.1 {
            "candidates_unreadable"
        } else {
            "observed_clean"
        };
        Stop {
            status: StopStatus::Unverified(reason.into()),
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec!["env_cleared"],
        }
    }
}

static FLAKY: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
static NEVER: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

fn flaky_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Unreadable(&FLAKY, 2)))
}

fn never_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Unreadable(&NEVER, usize::MAX)))
}

fn wait_until(d: Duration, mut f: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + d;
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    f()
}

fn one_req() -> aibd::sessions::Request {
    aibd::sessions::Request {
        argv: vec!["/bin/true".into()],
        cwd: None,
        env: vec![],
    }
}

/// a63ab 11-09: a held slot with nothing known alive is re-checked in the
/// background; once a re-check sees nothing (`observed_clean`) the slot comes
/// back and `session.slot_released` says so.
#[test]
fn a_held_slot_is_rechecked_in_the_background_and_released() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        flaky_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let rec = s.start(one_req()).unwrap();
    let first = s.stop(&rec.id, Duration::ZERO).unwrap();
    assert!(first.holds_slot, "{first:?}");
    assert_eq!(
        first.stop.as_ref().unwrap().reason.as_deref(),
        Some("candidates_unreadable")
    );
    // 1 stop + 1 unreadable re-check + 1 clean re-check, 2 s apart
    assert!(
        wait_until(Duration::from_secs(8), || s.slots_in_use() == 0),
        "slot never came back"
    );
    let got = s.get(&rec.id).unwrap();
    assert!(!got.holds_slot, "{got:?}");
    assert_eq!(
        got.stop.as_ref().unwrap().reason.as_deref(),
        Some("observed_clean")
    );
    let evs = store.events_after(0, 100).unwrap();
    let rel = evs
        .iter()
        .find(|e| e.event_type == "session.slot_released")
        .expect("no slot_released");
    assert_eq!(rel.payload["tries"], 2, "{evs:?}");
    assert!(s.start(one_req()).is_ok(), "the released slot is usable");
}

/// ... and when every re-check in the window still cannot look, the slot
/// stays held and `session.slot_held` records the reason.
#[test]
fn a_slot_that_stays_unreadable_stays_held_with_the_reason() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        never_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    s.set_rescan_window_for_test(Duration::from_secs(3), Duration::from_millis(500));
    let rec = s.start(one_req()).unwrap();
    assert!(s.stop(&rec.id, Duration::ZERO).unwrap().holds_slot);
    let held = wait_until(Duration::from_secs(8), || {
        store
            .events_after(0, 100)
            .unwrap()
            .iter()
            .any(|e| e.event_type == "session.slot_held")
    });
    assert!(held, "no slot_held event");
    assert_eq!(s.slots_in_use(), 1);
    let evs = store.events_after(0, 100).unwrap();
    let h = evs
        .iter()
        .find(|e| e.event_type == "session.slot_held")
        .unwrap();
    assert_eq!(h.payload["reason"], "candidates_unreadable", "{evs:?}");
    assert!(!evs.iter().any(|e| e.event_type == "session.slot_released"));
    assert_eq!(
        s.start(one_req()).unwrap_err().0,
        aib_core::ErrorCode::TooManySessions
    );
}

/// Measurement, not a gate (a63ab 11-09: how often a real macOS stop ends
/// `candidates_unreadable`). `AIBD_MEASURE_N` sessions (default 50) over
/// `AIBD_MEASURE_PAR` threads (default 1 = one at a time); prints one JSON
/// line `MEASURE {...}` with the first-DELETE reason counts. Run with
/// `cargo test -p aibd --test sessions measure -- --ignored --nocapture`.
#[tokio::test(flavor = "multi_thread")]
#[ignore]
async fn measure_stop_reasons() {
    let n: usize = std::env::var("AIBD_MEASURE_N")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(50);
    let par: usize = std::env::var("AIBD_MEASURE_PAR")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1);
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 64).await;
    let v = view(&d);
    let dir = tmp.path().to_path_buf();
    let reasons = blocking!(v, |v| {
        let hs: Vec<_> = (0..par)
            .map(|t| {
                let (v, dir) = (v.clone(), dir.clone());
                std::thread::spawn(move || {
                    let d = v.d();
                    let mut out = Vec::new();
                    for i in (t..n).step_by(par) {
                        let f = dir.join(format!("m{i}"));
                        let argv = sh_gc(&f, "wait");
                        let (c, s) = d.call(
                            "POST",
                            "/api/v1/sessions",
                            Some(&serde_json::json!({"argv": argv}).to_string()),
                        );
                        assert_eq!(c, 201, "{s}");
                        let id = s["id"].as_str().unwrap().to_owned();
                        let gc = read_pid(&f);
                        let _own = Own(vec![gc]);
                        let (c, s) = d.call(
                            "DELETE",
                            &format!("/api/v1/sessions/{id}?grace_ms=500"),
                            None,
                        );
                        assert_eq!(c, 200, "{s}");
                        out.push(s["stop"]["reason"].as_str().unwrap_or("none").to_owned());
                    }
                    out
                })
            })
            .collect();
        hs.into_iter()
            .flat_map(|h| h.join().unwrap())
            .collect::<Vec<String>>()
    });
    let mut counts = std::collections::BTreeMap::new();
    for r in &reasons {
        *counts.entry(r.clone()).or_insert(0usize) += 1;
    }
    println!(
        "MEASURE {}",
        serde_json::json!({"n": reasons.len(), "par": par, "reasons": counts})
    );
    d.shutdown().await;
}

/// bb7dc #546 a76df098 (1): the shutdown starts while a background re-check
/// is stopping the session (so `stopping` is set and the shutdown skips it);
/// that pass then finds a survivor. The shutdown must not end as if nothing
/// was there: the session gets its shutdown stop on record.
struct Gated;

static GATED_CALLS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
static GATE_IN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static GATE_OPEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

impl aibd::platform::session::Running for Gated {
    fn pid(&self) -> u32 {
        999_996
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        use std::sync::atomic::Ordering::SeqCst;
        let n = GATED_CALLS.fetch_add(1, SeqCst);
        let survivors = if n == 1 {
            // the re-check's pass: hold it until the test has begun the shutdown
            GATE_IN.store(true, SeqCst);
            while !GATE_OPEN.load(SeqCst) {
                std::thread::sleep(Duration::from_millis(10));
            }
            vec![4242]
        } else {
            vec![]
        };
        Stop {
            status: StopStatus::Unverified("candidates_unreadable".into()),
            survivors,
            tree_size: 1,
            signals: vec![],
            blind_spots: vec!["env_cleared"],
        }
    }
}

fn gated_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Gated))
}

#[test]
fn a_shutdown_during_a_recheck_still_records_the_shutdown_stop() {
    use std::sync::atomic::Ordering::SeqCst;
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        gated_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let rec = s.start(one_req()).unwrap();
    assert!(s.stop(&rec.id, Duration::ZERO).unwrap().holds_slot);
    assert!(
        wait_until(Duration::from_secs(8), || GATE_IN.load(SeqCst)),
        "no re-check pass"
    );
    let sd = {
        let s = s.clone();
        std::thread::spawn(move || s.shutdown_all())
    };
    std::thread::sleep(Duration::from_millis(300));
    GATE_OPEN.store(true, SeqCst);
    let busy = sd.join().unwrap();
    let evs = store.events_after(0, 100).unwrap();
    let by_shutdown = evs.iter().any(|e| {
        e.aggregate_id == rec.id
            && e.event_type == "session.stop_unconfirmed"
            && e.payload["ended_by"] == "shutdown"
    });
    assert!(
        by_shutdown || busy > 0,
        "shutdown returned {busy} with no shutdown stop on record: {:?}",
        evs.iter()
            .map(|e| (&e.event_type, &e.payload))
            .collect::<Vec<_>>()
    );
    let got = s.get(&rec.id).unwrap();
    assert_eq!(got.ended_by, Some("shutdown"), "{got:?}");
    assert_eq!(got.stop.as_ref().unwrap().survivors, vec![4242], "{got:?}");
}

/// A stop that takes `.1` ms and always ends with reason `.0`, no survivor.
struct Slow(&'static str, u64);

impl aibd::platform::session::Running for Slow {
    fn pid(&self) -> u32 {
        999_995
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        std::thread::sleep(Duration::from_millis(self.1));
        Stop {
            status: StopStatus::Unverified(self.0.into()),
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
        }
    }
}

fn slow_unreadable_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Slow("candidates_unreadable", 900)))
}

fn lineage_off_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Slow("lineage_unavailable", 0)))
}

fn rescan_events(store: &aib_store::Store, id: &str) -> Vec<(String, serde_json::Value)> {
    store
        .events_after(0, 100)
        .unwrap()
        .into_iter()
        .filter(|e| e.aggregate_id == id && e.event_type.starts_with("session.slot_"))
        .map(|e| (e.event_type, e.payload))
        .collect()
}

/// bb7dc #546 (2): no pass starts unless it fits in the window, so the
/// whole re-check (slow passes included) ends within it.
#[test]
fn a_recheck_with_slow_passes_still_ends_within_its_window() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        slow_unreadable_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    // ticks sleep first: a 900 ms pass at 2 s, then 4 s - 1 s left < pass_min
    let window = Duration::from_secs(4);
    s.set_rescan_window_for_test(window, Duration::from_secs(1));
    let rec = s.start(one_req()).unwrap();
    assert!(s.stop(&rec.id, Duration::ZERO).unwrap().holds_slot);
    let t = Instant::now(); // the re-check starts as the DELETE answers
    assert!(wait_until(Duration::from_secs(10), || !rescan_events(
        &store, &rec.id
    )
    .is_empty()));
    let took = t.elapsed();
    assert!(
        took <= window + Duration::from_millis(400),
        "re-check took {took:?}, window {window:?}"
    );
    let evs = rescan_events(&store, &rec.id);
    assert_eq!(evs[0].0, "session.slot_held", "{evs:?}");
    // the window ended between passes, not by cutting one short: a pass is
    // never started without pass_min left (else rescan_pass_timeout here)
    assert_eq!(evs[0].1["reason"], "candidates_unreadable", "{evs:?}");
    assert_eq!(evs[0].1["tries"], 1, "{evs:?}");
}

/// bb7dc #546 (3) / DECIDED #9: only candidates_unreadable is re-checked; a
/// reason another pass cannot change keeps the slot without a re-check.
#[test]
fn a_reason_another_pass_cannot_change_is_not_rechecked() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        lineage_off_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    s.set_rescan_window_for_test(Duration::from_secs(3), Duration::from_millis(500));
    let rec = s.start(one_req()).unwrap();
    assert!(s.stop(&rec.id, Duration::ZERO).unwrap().holds_slot);
    // past the whole window: no re-check ever ran
    assert!(!wait_until(Duration::from_secs(4), || !rescan_events(
        &store, &rec.id
    )
    .is_empty()));
    assert!(s.get(&rec.id).unwrap().holds_slot);
}

/// bb7dc #546 7ebde8d1: a re-check pass whose stop hangs past the window.
/// The first stop (the DELETE) answers at once with `candidates_unreadable`;
/// every later one blocks until the test opens the gate, then answers
/// `observed_clean` with no survivor (a result that WOULD release the slot).
struct Stuck;

static STUCK_CALLS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
static STUCK_OPEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

impl aibd::platform::session::Running for Stuck {
    fn pid(&self) -> u32 {
        999_994
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        use std::sync::atomic::Ordering::SeqCst;
        let reason = if STUCK_CALLS.fetch_add(1, SeqCst) == 0 {
            "candidates_unreadable"
        } else {
            while !STUCK_OPEN.load(SeqCst) {
                std::thread::sleep(Duration::from_millis(10));
            }
            "observed_clean"
        };
        Stop {
            status: StopStatus::Unverified(reason.into()),
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
        }
    }
}

fn stuck_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(Stuck))
}

/// The window bounds the re-check even when its stop never returns: it ends
/// within the window with `slot_held {reason: rescan_pass_timeout}`, the slot
/// kept. The late answer (`observed_clean`) is recorded but releases nothing,
/// and the session is not left `stopping` (a DELETE afterwards answers).
#[test]
fn a_recheck_whose_stop_hangs_still_ends_within_its_window_and_keeps_the_slot() {
    use std::sync::atomic::Ordering::SeqCst;
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        stuck_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let window = Duration::from_secs(3);
    s.set_rescan_window_for_test(window, Duration::from_millis(500));
    let rec = s.start(one_req()).unwrap();
    assert!(s.stop(&rec.id, Duration::ZERO).unwrap().holds_slot);
    let t = Instant::now(); // the re-check starts as the DELETE answers
    assert!(wait_until(Duration::from_secs(10), || !rescan_events(
        &store, &rec.id
    )
    .is_empty()));
    let took = t.elapsed();
    assert!(STUCK_CALLS.load(SeqCst) >= 2, "no re-check pass ran");
    assert!(
        took <= window + Duration::from_millis(400),
        "re-check took {took:?}, window {window:?}"
    );
    let evs = rescan_events(&store, &rec.id);
    assert_eq!(evs[0].0, "session.slot_held", "{evs:?}");
    assert_eq!(
        evs[0].1["reason"],
        aibd::sessions::RESCAN_PASS_TIMEOUT,
        "{evs:?}"
    );
    assert!(s.get(&rec.id).unwrap().holds_slot);
    STUCK_OPEN.store(true, SeqCst);
    assert!(
        wait_until(Duration::from_secs(5), || s
            .get(&rec.id)
            .and_then(|g| g.stop)
            .is_some_and(|v| v.reason.as_deref() == Some("observed_clean"))),
        "late answer not recorded: {:?}",
        s.get(&rec.id)
    );
    let got = s.get(&rec.id).unwrap();
    assert!(got.holds_slot, "a late pass released the slot: {got:?}");
    assert_eq!(
        rescan_events(&store, &rec.id).len(),
        1,
        "{:?}",
        rescan_events(&store, &rec.id)
    );
    // not left `stopping`: a DELETE runs its own stop and answers
    let t = Instant::now();
    assert!(s.stop(&rec.id, Duration::ZERO).is_ok());
    assert!(
        t.elapsed() < Duration::from_secs(3),
        "DELETE waited {:?}",
        t.elapsed()
    );
}

/// 043dd #544 Medium: the root's identity was lost (M5 could not read its
/// status). The root-exit stop still runs, but the record and the event carry
/// `exit_code: null`, never a made-up -1, and the stop says why.
struct LostRoot;

struct LostAtOnce;

impl aibd::platform::session::ExitWait for LostAtOnce {
    fn wait(&self, _: Duration) -> Option<aibd::platform::session::Exit> {
        Some(aibd::platform::session::Exit::Unknown)
    }
}

impl aibd::platform::session::Running for LostRoot {
    fn pid(&self) -> u32 {
        999_993
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(Some(aibd::platform::session::Exit::Unknown))
    }
    fn exit_wait(&self) -> std::sync::Arc<dyn aibd::platform::session::ExitWait> {
        std::sync::Arc::new(LostAtOnce)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{Stop, StopStatus};
        Stop {
            status: StopStatus::Unverified("root_identity_lost".into()),
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
        }
    }
}

fn lost_root_spawn(
    _: &aibd::platform::session::Spec,
    _: Option<std::sync::Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    Ok(Box::new(LostRoot))
}

#[test]
fn a_root_whose_identity_was_lost_has_no_exit_code() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        lost_root_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let rec = s.start(one_req()).unwrap();
    assert!(
        wait_until(Duration::from_secs(5), || s
            .get(&rec.id)
            .is_some_and(|g| g.ended_by.is_some())),
        "the root-exit stop never ran: {:?}",
        s.get(&rec.id)
    );
    let got = s.get(&rec.id).unwrap();
    assert_eq!(got.ended_by, Some("root_exit"), "{got:?}");
    assert_eq!(got.exit_code, None, "a made-up exit code: {got:?}");
    assert_eq!(got.signal, None, "{got:?}");
    assert_eq!(got.state, "stop_unconfirmed", "{got:?}");
    assert_eq!(
        got.stop.as_ref().and_then(|v| v.reason.as_deref()),
        Some("root_identity_lost"),
        "{got:?}"
    );
    let ev = store
        .events_after(0, 100)
        .unwrap()
        .into_iter()
        .find(|e| e.aggregate_id == rec.id && e.event_type == "session.stop_unconfirmed")
        .expect("no stop event");
    assert!(ev.payload["exit_code"].is_null(), "{}", ev.payload);
}

/// 1ced7 #593 L2: a folded refusal arms one flush thread that waits up to a
/// minute for its window to close. While it waits it must not hold the
/// sessions (and with them the store) alive: after a shutdown the store is
/// released at once, not up to 61 s later.
#[test]
fn a_waiting_reject_flush_does_not_keep_the_sessions_alive() {
    let tmp = tempfile::tempdir().unwrap();
    let store = std::sync::Arc::new(aib_store::Store::open(&tmp.path().join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(
        store.clone(),
        None,
        never_spawn,
        1,
        tmp.path().to_path_buf(),
    );
    let before = std::sync::Arc::strong_count(&s);
    let t0 = Instant::now();
    let code = aib_core::ErrorCode::ProgramNotAllowed;
    // the first is written, the second is folded and arms the flush thread
    s.record_rejection(t0, "startup", code, "x");
    s.record_rejection(t0, "startup", code, "x");
    // let the flush thread start and reach its wait
    std::thread::sleep(Duration::from_millis(200));
    assert_eq!(
        std::sync::Arc::strong_count(&s),
        before,
        "the waiting flush thread holds the sessions"
    );
    let w = std::sync::Arc::downgrade(&s);
    drop(s);
    assert!(
        w.upgrade().is_none(),
        "sessions still alive after the last owner dropped them"
    );
}

/// G1 (1ced7 gate, 11-09): a program that ends at once gives its slot back.
/// With one slot, twenty in a row must all start (201) and each end
/// `observed_clean` without holding the slot. printenv / `sleep 0` used to
/// hit `fork_watch_list_failed` (libproc left a stale errno) or
/// `fork_watch_child_unreadable` (a child gone before it was read).
/// The real `claude` race is checked by 1ced7 on a CI-built aibd (10 runs).
#[cfg(target_os = "macos")]
#[tokio::test(flavor = "multi_thread")]
async fn g1_instant_programs_give_their_slot_back() {
    let tmp = tempfile::tempdir().unwrap();
    let mut policy = fixtures(tmp.path());
    policy.programs.push(fx("printenv", "/usr/bin/printenv"));
    let mut d = start_policy(tmp.path(), 1, policy).await;
    let v = view(&d);
    blocking!(v, |v| {
        let d = v.d();
        let runs: [&[&str]; 2] = [&["/usr/bin/printenv", "HOME"], &["/bin/sleep", "0"]];
        for (i, argv) in runs.iter().cycle().take(20).enumerate() {
            let (c, s) = d.post(argv);
            assert_eq!(c, 201, "run {i}: {s}");
            let id = s["id"].as_str().unwrap().to_owned();
            let s = wait_state(&d, &id, "running");
            assert_eq!(s["stop"]["reason"], "observed_clean", "run {i}: {s}");
            assert_eq!(s["holds_slot"], false, "run {i}: {s}");
        }
    });
    d.shutdown().await;
}

/// G1 / 043dd 20:42: the middle process forks and exits before it may be
/// read; its child is re-parented to launchd. Whichever layer finds it (fork
/// watcher, lineage, or the widened orphan check), the slot is never given
/// back while that child still runs: either it was stopped, or the slot is
/// held (a survivor, or `candidates_unreadable`). Never `clean` on macOS, and
/// the report names `exec_after_creator_exit` (043dd 01318a34).
#[cfg(target_os = "macos")]
#[tokio::test(flavor = "multi_thread")]
async fn g1_a_child_of_an_unread_middle_is_never_let_go() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 1).await;
    let v = view(&d);
    let f = tmp.path().join("gc");
    // root sh -> subshell (middle) starts sh in the background and exits at
    // once; that sh writes its pid and execs sleep; the root exits
    let script = format!(
        "( /bin/sh -c 'echo $$ > {f}; exec /bin/sleep 30' & ) ; exit 0",
        f = f.display()
    );
    let (id, gc) = blocking!(v, |v| {
        let d = v.d();
        let (c, s) = d.post(&["/bin/sh", "-c", &script]);
        assert_eq!(c, 201, "{s}");
        (s["id"].as_str().unwrap().to_owned(), read_pid(&f))
    });
    let _own = Own(vec![gc]);
    let s = blocking!(v, |v| wait_state(&v.d(), &id, "running"));
    assert_ne!(s["stop"]["status"], "clean", "{s}");
    assert!(
        s["stop"]["blind_spots"]
            .as_array()
            .unwrap()
            .iter()
            .any(|b| b == "exec_after_creator_exit"),
        "{s}"
    );
    if alive(gc) {
        assert_eq!(
            s["holds_slot"], true,
            "slot given back, {gc} still runs: {s}"
        );
        let surv: Vec<u64> = s["stop"]["survivors"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(serde_json::Value::as_u64)
            .collect();
        assert!(
            surv.contains(&u64::from(gc)) || s["stop"]["reason"] == "candidates_unreadable",
            "{gc} neither a survivor nor an unreadable candidate: {s}"
        );
    }
    d.shutdown().await;
}
