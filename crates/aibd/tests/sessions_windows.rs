#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Sessions API on a real aibd (127.0.0.1:0, temp data dir) with real
//! Windows processes: `default_spawn` must be `session_windows::spawn`
//! (a63ab 11-09: before this wiring every POST answered 422
//! `unsupported_platform`). The POSIX side is `sessions.rs`. Every test
//! collects exactly the pids it was told about, never a wider kill.
#![cfg(windows)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aib_sys::job::Process;
use aibd::allowlist::{Program, SessionPolicy};
use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

struct Daemon {
    addr: SocketAddr,
    token: String,
    data: PathBuf,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

/// Pids a test learned (handles opened while they ran, so a reused pid is
/// never touched); terminated on drop whatever happened.
struct Own(Vec<Process>);

impl Drop for Own {
    fn drop(&mut self) {
        for p in &self.0 {
            if let Ok(t) = Process::open_terminable(p.pid()) {
                if t.created_100ns().ok() == p.created_100ns().ok() {
                    let _ = t.terminate(1);
                }
            }
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
        // M1 gate: exactly the programs these tests start. PowerShell is an interpreter,
        // so these in-process tests set `interpreters_ok` (only Rust code can).
        session_policy: SessionPolicy {
            programs: [
                ("ping", system32("PING.EXE")),
                (
                    "powershell",
                    system32(r"WindowsPowerShell\v1.0\powershell.exe"),
                ),
            ]
            .into_iter()
            .map(|(name, p)| Program {
                name: name.into(),
                path: PathBuf::from(p),
                signer: None,
            })
            .collect(),
            env: vec![],
            interpreters_ok: true,
        },
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
        data: dir.to_path_buf(),
        stop: Some(stop_tx),
        h: Some(h),
    }
}

/// One raw HTTP/1.1 request (blocking; call from spawn_blocking).
fn call(
    addr: SocketAddr,
    token: &str,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> (u16, serde_json::Value) {
    let port = addr.port();
    let mut req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nAuthorization: Bearer {token}\r\n"
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
        .unwrap_or(serde_json::Value::Null);
    (code, json)
}

impl Daemon {
    async fn call(
        &self,
        method: &str,
        path: &str,
        body: Option<String>,
    ) -> (u16, serde_json::Value) {
        let (a, t, m, p) = (
            self.addr,
            self.token.clone(),
            method.to_owned(),
            path.to_owned(),
        );
        tokio::task::spawn_blocking(move || call(a, &t, &m, &p, body.as_deref()))
            .await
            .unwrap()
    }

    async fn post(&self, argv: &[String]) -> (u16, serde_json::Value) {
        let b = serde_json::json!({ "argv": argv }).to_string();
        self.call("POST", "/api/v1/sessions", Some(b)).await
    }

    async fn wait_state(&self, id: &str, not: &str) -> serde_json::Value {
        let end = Instant::now() + Duration::from_secs(20);
        loop {
            let (c, s) = self
                .call("GET", &format!("/api/v1/sessions/{id}"), None)
                .await;
            assert_eq!(c, 200, "{s}");
            if s["state"] != not {
                return s;
            }
            assert!(Instant::now() < end, "still {not}: {s}");
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

fn system32(exe: &str) -> String {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    format!(r"{root}\System32\{exe}")
}

/// PowerShell that starts `ping -n 60` as its own child (pid written to `f`,
/// atomically), then runs `tail`.
fn ps_gc(f: &Path, tail: &str) -> Vec<String> {
    let f = f.display().to_string().replace('\'', "''");
    let ping = system32("PING.EXE");
    vec![
        system32(r"WindowsPowerShell\v1.0\powershell.exe"),
        "-NoProfile".into(),
        "-NonInteractive".into(),
        "-Command".into(),
        format!(
            "$p = Start-Process -FilePath '{ping}' -ArgumentList '-n','60','127.0.0.1' -PassThru -WindowStyle Hidden; \
             Set-Content -LiteralPath '{f}.tmp' -Value $p.Id; Move-Item -LiteralPath '{f}.tmp' -Destination '{f}'; {tail}"
        ),
    ]
}

/// The grandchild's pid from `f`, with a handle opened while it still runs.
async fn grandchild(f: &Path) -> Process {
    let end = Instant::now() + Duration::from_secs(30);
    loop {
        if let Some(p) = std::fs::read_to_string(f)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
        {
            return Process::open(p).unwrap_or_else(|e| panic!("open grandchild {p}: {e:?}"));
        }
        assert!(Instant::now() < end, "{} never written", f.display());
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

fn gone_within(p: &Process, d: Duration) -> bool {
    matches!(p.wait(d), Ok(true))
}

fn pid_of(s: &serde_json::Value) -> u32 {
    s["pid"].as_u64().unwrap() as u32
}

/// The wiring itself (knife `windows-unsupported-spawn`): a start is no
/// longer refused, and DELETE stops the grandchild and says `clean` — the
/// Job confirmed every member gone (definition #11), so the slot is freed.
#[tokio::test(flavor = "multi_thread")]
async fn delete_stops_the_grandchild_and_confirms_the_tree() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let f = tmp.path().join("gc");
    let (code, s) = d.post(&ps_gc(&f, "Start-Sleep -Seconds 60")).await;
    assert_eq!(code, 201, "{s}");
    assert_eq!(s["state"], "running", "{s}");
    let id = s["id"].as_str().unwrap().to_owned();
    let root = Process::open(pid_of(&s)).unwrap();
    let gc = grandchild(&f).await;
    let _own = Own(vec![
        Process::open(gc.pid()).unwrap(),
        Process::open(root.pid()).unwrap(),
    ]);
    assert!(!gone_within(&gc, Duration::ZERO), "grandchild ended early");
    let (code, s) = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{id}?grace_ms=500"),
            None,
        )
        .await;
    assert_eq!(code, 200, "{s}");
    assert!(
        gone_within(&gc, Duration::ZERO),
        "grandchild {} still running after DELETE answered: {s}",
        gc.pid()
    );
    assert!(
        gone_within(&root, Duration::ZERO),
        "root still running: {s}"
    );
    assert_eq!(s["state"], "killed", "{s}");
    assert_eq!(s["ended_by"], "api");
    assert_eq!(s["stop"]["status"], "clean", "{s}");
    assert_eq!(s["stop"]["survivors"], serde_json::json!([]));
    assert_eq!(s["holds_slot"], false, "{s}");
    d.shutdown().await;
}

/// The root exits by itself (code 3); its grandchild is stopped by the one
/// stop that follows (definition #5).
#[tokio::test(flavor = "multi_thread")]
async fn a_root_that_exits_by_itself_gets_its_leftovers_stopped() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let f = tmp.path().join("gc");
    let (code, s) = d
        .post(&ps_gc(&f, "Start-Sleep -Milliseconds 300; exit 3"))
        .await;
    assert_eq!(code, 201, "{s}");
    let id = s["id"].as_str().unwrap().to_owned();
    let gc = grandchild(&f).await;
    let _own = Own(vec![Process::open(gc.pid()).unwrap()]);
    let s = d.wait_state(&id, "running").await;
    assert_eq!(s["ended_by"], "root_exit", "{s}");
    assert_eq!(s["exit_code"], 3, "{s}");
    assert!(!s["stop"].is_null(), "{s}");
    assert!(
        gone_within(&gc, Duration::from_secs(5)),
        "grandchild {} left behind: {s}",
        gc.pid()
    );
    d.shutdown().await;
}

/// aibd ending stops every session's tree before it returns, and records it.
#[tokio::test(flavor = "multi_thread")]
async fn aibd_shutdown_stops_every_session() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8).await;
    let mut gcs = Vec::new();
    for n in 0..2 {
        let f = tmp.path().join(format!("gc{n}"));
        let (code, s) = d.post(&ps_gc(&f, "Start-Sleep -Seconds 60")).await;
        assert_eq!(code, 201, "{s}");
        gcs.push(grandchild(&f).await);
    }
    let _own = Own(gcs
        .iter()
        .map(|p| Process::open(p.pid()).unwrap())
        .collect());
    let data = d.data.clone();
    d.shutdown().await;
    for gc in &gcs {
        assert!(
            gone_within(gc, Duration::ZERO),
            "grandchild {} survived aibd's shutdown",
            gc.pid()
        );
    }
    let store = aib_store::Store::open(&data.join("aibd.sqlite")).unwrap();
    let evs = store.events_after(0, 500).unwrap();
    let ends = evs
        .iter()
        .filter(|e| e.event_type == "session.killed" && e.payload["ended_by"] == "shutdown")
        .count();
    assert_eq!(ends, 2, "{evs:?}");
}
