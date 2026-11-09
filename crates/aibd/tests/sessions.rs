#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Sessions API on a real aibd (127.0.0.1:0, temp data dir) with real
//! processes (contract §7, a63ab / bb7dc H1-H3). POSIX only: the Windows
//! side is 043dd's `sessions_windows.rs`. Every test collects exactly the
//! pids it was told about, never a wider kill.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

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

async fn start(dir: &Path, max: usize) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: max,
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
    blocking!(v, |v| {
        let d = v.d();
        let (c, s) = d.post(&["/bin/sleep", "60"]);
        assert_eq!(c, 201, "{s}");
        // a failed start gives its reserved slot back: the second still fits
        let (c, s) = d.post(&["/no/such/program"]);
        assert_eq!((c, s["code"].as_str()), (422, Some("spawn_failed")), "{s}");
        assert_eq!(s["message"], "could not start: enoent");
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
    assert_eq!(started["payload"]["argv0"], "sh");
    assert_eq!(started["payload"]["argc"], 4);
    d.shutdown().await;
}

/// a63ab ruling 3: stdin / stdout / stderr are the null device, never
/// inherited from aibd; the request env is applied and AIBD_SESSION set.
#[tokio::test(flavor = "multi_thread")]
async fn the_child_gets_null_stdio_its_env_and_the_marker() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let v = view(&d);
    let f = tmp.path().join("out");
    let script = format!(
        "r=; for n in 0 1 2; do if [ -c /dev/fd/$n ]; then r=\"$r $n chr\"; else r=\"$r $n other\"; fi; done; echo \"$r\" > {f}; env >> {f}",
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
    for n in 0..3 {
        assert!(
            out.contains(&format!("{n} chr")),
            "fd {n} is not the null device: {out}"
        );
    }
    assert!(out.contains("MY_VAR=one"), "{out}");
    assert!(out.contains(&format!("AIBD_SESSION={id}")), "{out}");
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
