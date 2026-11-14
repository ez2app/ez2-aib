#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! M1 gate, reverse tests (1ced7, TESTPLAN-sessions-allowlist-1ced7-1109): what must be
//! refused is refused on BOTH start routes (`POST /api/v1/sessions` and
//! `POST /api/v1/seats/{id}/sessions`), nothing starts, no slot is used, and the audit
//! record carries no value from the request. Every test also has a positive case on the
//! same daemon, so a red one means the gate refused, not that nothing could start.
//!
//! The policy is the one aibd really runs (`interpreters_ok: false`); the allowed
//! programs are native system tools (`/bin/sleep`, `/usr/bin/true`), not shells.
//! POSIX only (symlinks, `/bin/sh`); the Windows cells are UNVERIFIED-desktop.
#![cfg(unix)]
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::allowlist::{self, Program, SessionPolicy};
use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

fn fx(name: &str, path: impl Into<PathBuf>) -> Program {
    Program {
        name: name.into(),
        path: path.into(),
        signer: None,
    }
}

/// `/bin/sleep` and `/usr/bin/true`, one env name, interpreters refused (the real rule).
fn policy() -> SessionPolicy {
    SessionPolicy {
        programs: vec![fx("sleep", "/bin/sleep"), fx("true", "/usr/bin/true")],
        env: vec!["MY_VAR".into()],
        interpreters_ok: false,
    }
}

async fn start(dir: &Path, max: usize, session_policy: SessionPolicy) -> Daemon {
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

/// What a blocking thread needs to talk to the daemon.
#[derive(Clone)]
struct View {
    addr: SocketAddr,
    token: String,
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

impl View {
    fn call(&self, method: &str, path: &str, body: Option<&str>) -> (u16, serde_json::Value) {
        let port = self.addr.port();
        let mut req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nAuthorization: Bearer {}\r\n",
            self.token
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

    fn new_seat(&self) -> String {
        let (c, s) = self.call("POST", "/api/v1/seats", Some(r#"{"name":"qa-1ced7"}"#));
        assert_eq!(c, 201, "{s}");
        s["seat_id"].as_str().unwrap().to_string()
    }

    /// Start on one of the two routes (`seat` None = `POST /api/v1/sessions`).
    fn start_on(&self, seat: Option<&str>, body: &serde_json::Value) -> (u16, serde_json::Value) {
        let path = match seat {
            None => "/api/v1/sessions".to_string(),
            Some(id) => format!("/api/v1/seats/{id}/sessions"),
        };
        self.call("POST", &path, Some(&body.to_string()))
    }

    fn events(&self) -> Vec<serde_json::Value> {
        let (c, evs) = self.call("GET", "/api/v1/events?limit=500", None);
        assert_eq!(c, 200, "{evs}");
        evs["events"].as_array().unwrap().clone()
    }

    fn count(&self, kind: &str) -> usize {
        self.events().iter().filter(|e| e["type"] == kind).count()
    }

    /// Sessions the daemon knows, live ones included.
    fn sessions(&self) -> usize {
        let (c, s) = self.call("GET", "/api/v1/sessions", None);
        assert_eq!(c, 200, "{s}");
        s["sessions"].as_array().map(Vec::len).unwrap_or(0)
    }

    /// Stop a session we started (only that id; never a wider kill).
    fn stop(&self, id: &str) {
        let _ = self.call("DELETE", &format!("/api/v1/sessions/{id}"), None);
    }

    /// The stdout lines of a session, once its stdout has closed (20 s at most).
    fn stdout_lines(&self, id: &str) -> Vec<String> {
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        loop {
            let (c, o) = self.call("GET", &format!("/api/v1/sessions/{id}/output"), None);
            assert_eq!(c, 200, "{o}");
            let items = o["output"].as_array().cloned().unwrap_or_default();
            let closed = items.iter().any(|e| {
                e["type"] == "session.output_closed" && e["payload"]["stream"] == "stdout"
            });
            if closed {
                return items
                    .iter()
                    .filter(|e| e["type"] == "session.output" && e["payload"]["stream"] == "stdout")
                    .filter_map(|e| e["payload"]["line"].as_str().map(str::to_string))
                    .collect();
            }
            assert!(
                std::time::Instant::now() < deadline,
                "stdout of {id} never closed: {o}"
            );
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}

fn argv(a: &[&str]) -> serde_json::Value {
    serde_json::json!({ "argv": a })
}

/// `(status, code)` of an answer, for one-line asserts.
fn code(r: &(u16, serde_json::Value)) -> (u16, Option<String>) {
    (r.0, r.1["code"].as_str().map(str::to_string))
}

fn refused(r: &(u16, serde_json::Value), status: u16, c: &str) {
    assert_eq!(code(r), (status, Some(c.to_string())), "{}", r.1);
}

/// P1 / P2 / E1 / E2 / P7 on both routes: refused with the right code, nothing starts,
/// one audit record per code; then a listed program with a listed env name starts on
/// both routes (the positive half: the same daemon can start what the gate allows).
#[tokio::test(flavor = "multi_thread")]
async fn both_routes_refuse_what_is_not_listed_and_start_what_is() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8, policy()).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            let at = format!("{route:?}");
            // P1 a shell, not listed
            refused(
                &v.start_on(route, &argv(&["/bin/sh", "-c", "exit 0"])),
                403,
                "program_not_allowed",
            );
            // P1 a native program, not listed
            refused(
                &v.start_on(route, &argv(&["/bin/echo", "x"])),
                403,
                "program_not_allowed",
            );
            // P2 `..` is refused without resolving
            refused(
                &v.start_on(route, &argv(&["/bin/../bin/sleep", "1"])),
                403,
                "program_not_allowed",
            );
            // E1 an env name that today's deny list would let through
            for k in ["NODE_OPTIONS", "BASH_ENV", "PYTHONPATH", "HOME"] {
                let body = serde_json::json!({"argv": ["/usr/bin/true"], "env": {k: "v"}});
                refused(&v.start_on(route, &body), 400, "env_not_allowed");
            }
            // E2 Unix compares env names exactly: `my_var` is not `MY_VAR`
            let body = serde_json::json!({"argv": ["/usr/bin/true"], "env": {"my_var": "v"}});
            refused(&v.start_on(route, &body), 400, "env_not_allowed");
            assert_eq!(v.count("session.started"), 0, "{at}: something started");
        }
        assert_eq!(v.sessions(), 0);
        // the positive half, both routes
        let ok = serde_json::json!({"argv": ["/usr/bin/true"], "env": {"MY_VAR": "1"}});
        let a = v.start_on(None, &ok);
        assert_eq!(a.0, 201, "{}", a.1);
        let b = v.start_on(Some(seat.as_str()), &ok);
        assert_eq!(b.0, 201, "{}", b.1);
        assert_eq!(b.1["seat_id"], seat.as_str(), "{}", b.1);
        // what runs is the real path that was checked
        let real = std::fs::canonicalize("/usr/bin/true").unwrap();
        assert_eq!(a.1["argv"][0], real.to_str().unwrap(), "{}", a.1);
        assert_eq!(v.count("session.started"), 2);
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// P7: no policy (`AIBD_SESSION_POLICY` unset) ⇒ nothing starts, on either route.
#[tokio::test(flavor = "multi_thread")]
async fn no_policy_starts_nothing_on_either_route() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8, SessionPolicy::default()).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            refused(
                &v.start_on(route, &argv(&["/usr/bin/true"])),
                403,
                "program_not_allowed",
            );
        }
        assert_eq!(v.sessions(), 0);
        assert_eq!(v.count("session.started"), 0);
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// I1 / E3 / M (launchers): a policy file that lists a shell, an interpreter, a launcher,
/// or a deny-listed env name never loads (aibd does not start). Names only: the paths do
/// not have to exist, so this runs the same on every OS.
#[test]
fn a_policy_that_lists_a_shell_interpreter_launcher_or_denied_env_does_not_load() {
    for p in [
        "/bin/sh",
        "/bin/zsh",
        "/bin/bash",
        "/usr/bin/python3",
        "/usr/bin/python3.12",
        "/usr/local/bin/node",
        "/usr/bin/osascript",
        "/usr/bin/env",
        "/usr/bin/xargs",
        "/usr/bin/arch",
        "/usr/bin/nohup",
        "/usr/bin/sudo",
        "/usr/bin/find",
        "/usr/bin/awk",
        "/usr/local/bin/bash-5.2",
    ] {
        let text =
            serde_json::json!({"programs": [{"name": "x", "path": p, "signer": null}]}).to_string();
        assert!(allowlist::parse(&text).is_err(), "{p} was accepted");
    }
    for k in [
        "PATH",
        "path",
        "DYLD_INSERT_LIBRARIES",
        "LD_PRELOAD",
        "AIBD_TOKEN",
    ] {
        let text = serde_json::json!({"env": [k]}).to_string();
        assert!(allowlist::parse(&text).is_err(), "env {k} was accepted");
    }
    // the positive half: a native tool and a plain env name load
    let text = serde_json::json!({
        "programs": [{"name": "sleep", "path": "/bin/sleep", "signer": null}],
        "env": ["MY_VAR"]
    })
    .to_string();
    let p = allowlist::parse(&text).unwrap();
    assert_eq!(p.programs.len(), 1);
    assert!(!p.interpreters_ok, "a file can never turn interpreters on");
    // unknown keys are refused (a typo must not silently widen or narrow the list)
    assert!(allowlist::parse(r#"{"programs": [], "interpreters_ok": true}"#).is_err());
}

/// I2 / P4: a listed link whose real file is a shell is refused when the file is read,
/// and, if the list was built some other way, when a session starts.
#[tokio::test(flavor = "multi_thread")]
async fn a_listed_link_to_a_shell_is_refused_at_load_and_at_start() {
    let tmp = tempfile::tempdir().unwrap();
    let link = tmp.path().join("fixture-ok");
    std::os::unix::fs::symlink("/bin/sh", &link).unwrap();
    let text =
        serde_json::json!({"programs": [{"name": "fx", "path": link, "signer": null}]}).to_string();
    assert!(
        allowlist::parse(&text).is_err(),
        "a link to /bin/sh was accepted"
    );
    let mut p = policy();
    p.programs.push(fx("fx", &link));
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, p).await;
    let v = d.view();
    let l = link.to_str().unwrap().to_string();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            refused(
                &v.start_on(route, &argv(&[l.as_str(), "-c", "exit 0"])),
                403,
                "program_not_allowed",
            );
        }
        assert_eq!(v.count("session.started"), 0);
        let ok = v.start_on(None, &argv(&["/usr/bin/true"]));
        assert_eq!(ok.0, 201, "{}", ok.1);
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// N1 / N2: the real path is resolved again on every start (no cache). A listed link
/// moved to another native tool runs that tool; moved to a shell, it is refused.
#[tokio::test(flavor = "multi_thread")]
async fn every_start_resolves_the_real_path_again() {
    let tmp = tempfile::tempdir().unwrap();
    let link = tmp.path().join("provider");
    std::os::unix::fs::symlink("/usr/bin/true", &link).unwrap();
    let mut p = policy();
    p.programs.push(fx("provider", &link));
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, p).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let l = link.to_str().unwrap().to_string();
        let a = v.start_on(None, &argv(&[l.as_str()]));
        assert_eq!(a.0, 201, "{}", a.1);
        let t = std::fs::canonicalize("/usr/bin/true").unwrap();
        assert_eq!(a.1["argv"][0], t.to_str().unwrap(), "{}", a.1);
        // N1: the link now points at sleep (a version update moving the target)
        std::fs::remove_file(&link).unwrap();
        std::os::unix::fs::symlink("/bin/sleep", &link).unwrap();
        let b = v.start_on(None, &argv(&[l.as_str(), "1"]));
        assert_eq!(b.0, 201, "{}", b.1);
        let s = std::fs::canonicalize("/bin/sleep").unwrap();
        assert_eq!(b.1["argv"][0], s.to_str().unwrap(), "{}", b.1);
        if let Some(id) = b.1["id"].as_str() {
            v.stop(id);
        }
        // N2: now at a shell: refused, although the same path started twice before
        std::fs::remove_file(&link).unwrap();
        std::os::unix::fs::symlink("/bin/sh", &link).unwrap();
        refused(
            &v.start_on(None, &argv(&[l.as_str(), "-c", "exit 0"])),
            403,
            "program_not_allowed",
        );
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// N3: a provider that runs through node is `provider_install_unsupported`; another
/// script is `program_not_allowed`; neither starts.
#[tokio::test(flavor = "multi_thread")]
async fn scripts_are_refused_and_node_installs_get_their_own_code() {
    use std::os::unix::fs::PermissionsExt;
    let tmp = tempfile::tempdir().unwrap();
    let write = |name: &str, body: &str| {
        let f = tmp.path().join(name);
        std::fs::write(&f, body).unwrap();
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o755)).unwrap();
        f
    };
    let node_sb = write("claude", "#!/usr/bin/env node\nrequire('./cli.js')\n");
    let node_js = write("cli.js", "console.log('hi')\n");
    let sh_sb = write("codex", "#!/bin/sh\nexit 0\n");
    let mut p = policy();
    for (n, f) in [("claude", &node_sb), ("cli", &node_js), ("codex", &sh_sb)] {
        p.programs.push(fx(n, f));
    }
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, p).await;
    let v = d.view();
    let (a, b, c) = (
        node_sb.to_str().unwrap().to_string(),
        node_js.to_str().unwrap().to_string(),
        sh_sb.to_str().unwrap().to_string(),
    );
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            refused(
                &v.start_on(route, &argv(&[a.as_str()])),
                403,
                "provider_install_unsupported",
            );
            refused(
                &v.start_on(route, &argv(&[b.as_str()])),
                403,
                "provider_install_unsupported",
            );
            refused(
                &v.start_on(route, &argv(&[c.as_str()])),
                403,
                "program_not_allowed",
            );
        }
        assert_eq!(v.count("session.started"), 0);
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// C1: refusals use no slot. With room for one session, three refusals in a row and then
/// a listed program still starts.
#[tokio::test(flavor = "multi_thread")]
async fn refusals_use_no_slot() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 1, policy()).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        refused(
            &v.start_on(None, &argv(&["/bin/sh"])),
            403,
            "program_not_allowed",
        );
        refused(
            &v.start_on(Some(seat.as_str()), &argv(&["/bin/sh"])),
            403,
            "program_not_allowed",
        );
        let e = serde_json::json!({"argv": ["/usr/bin/true"], "env": {"NOPE": "1"}});
        refused(&v.start_on(None, &e), 400, "env_not_allowed");
        let ok = v.start_on(Some(seat.as_str()), &argv(&["/bin/sleep", "1"]));
        assert_eq!(ok.0, 201, "{}", ok.1);
        if let Some(id) = ok.1["id"].as_str() {
            v.stop(id);
        }
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// C2 / A1 / A2 / A3: one `session.spawn_rejected` per code (folded within the minute),
/// in aggregate type `security`, never under a session, with exactly the ruled keys, and
/// no value from the request anywhere in the event table: not env values, not argv
/// after argv[0], not the cwd, not argv[0]'s directories.
#[tokio::test(flavor = "multi_thread")]
async fn the_audit_record_has_the_ruled_keys_and_no_request_value() {
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("CWDSECRET-1ced7");
    let dirsecret = tmp.path().join("DIRSECRET-1ced7");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&dirsecret).unwrap();
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, policy()).await;
    let v = d.view();
    let cwd_s = cwd.to_str().unwrap().to_string();
    let prog = dirsecret.join("x").to_str().unwrap().to_string();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        let env_bad = serde_json::json!({
            "argv": ["/usr/bin/true", "ARGSECRET-1ced7"],
            "cwd": cwd_s,
            "env": {"NOT_LISTED_1CED7": "VALSECRET-1ced7"}
        });
        refused(
            &v.start_on(Some(seat.as_str()), &env_bad),
            400,
            "env_not_allowed",
        );
        let prog_bad = serde_json::json!({"argv": [prog, "ARGSECRET-1ced7"], "cwd": cwd_s});
        refused(&v.start_on(None, &prog_bad), 403, "program_not_allowed");
        // folded: more of the same code within the minute add no record
        for _ in 0..4 {
            refused(&v.start_on(None, &prog_bad), 403, "program_not_allowed");
        }
        let evs = v.events();
        let rejected: Vec<&serde_json::Value> = evs
            .iter()
            .filter(|e| e["type"] == "session.spawn_rejected")
            .collect();
        assert_eq!(rejected.len(), 2, "{evs:?}");
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
        }
        let program_rec = rejected
            .iter()
            .find(|e| e["payload"]["code"] == "program_not_allowed")
            .unwrap();
        assert_eq!(program_rec["payload"]["program"], "x", "{program_rec}");
        assert!(
            !evs.iter().any(|e| e["aggregate_type"] == "session"),
            "a refusal wrote session events: {evs:?}"
        );
        let text = serde_json::Value::Array(evs.clone()).to_string();
        for secret in [
            "VALSECRET-1ced7",
            "NOT_LISTED_1CED7",
            "ARGSECRET-1ced7",
            "CWDSECRET-1ced7",
            "DIRSECRET-1ced7",
            v.token.as_str(),
        ] {
            assert!(!text.contains(secret), "{secret} in the event table");
        }
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// P3 / P5 (043dd 19:21: the real paths are compared, and the real path runs): another
/// spelling of a listed path (`//`, `.`), an unlisted link to a listed program's real
/// file, and that real file itself all start, and what runs is the real path. The list
/// here holds only a link, so a check that compares the request's text instead of the
/// real paths refuses all three (knife K2).
#[tokio::test(flavor = "multi_thread")]
async fn other_spellings_and_links_of_a_listed_program_run_its_real_path() {
    let tmp = tempfile::tempdir().unwrap();
    let listed = tmp.path().join("listed-true");
    let other = tmp.path().join("other-link");
    std::os::unix::fs::symlink("/usr/bin/true", &listed).unwrap();
    std::os::unix::fs::symlink("/usr/bin/true", &other).unwrap();
    let p = SessionPolicy {
        programs: vec![fx("true", &listed)],
        env: vec![],
        interpreters_ok: false,
    };
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, p).await;
    let v = d.view();
    let real = std::fs::canonicalize("/usr/bin/true").unwrap();
    let real = real.to_str().unwrap().to_string();
    let spelled = format!("{}/./{}", tmp.path().display(), "/listed-true");
    let (o, r) = (other.to_str().unwrap().to_string(), real.clone());
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            for a in [spelled.as_str(), o.as_str(), r.as_str()] {
                let s = v.start_on(route, &argv(&[a]));
                assert_eq!(s.0, 201, "{a} via {route:?}: {}", s.1);
                assert_eq!(s.1["argv"][0], real.as_str(), "{a}: {}", s.1);
            }
        }
        // the negative half on the same daemon: a program that is not the listed one
        refused(
            &v.start_on(None, &argv(&["/bin/sleep", "1"])),
            403,
            "program_not_allowed",
        );
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// E5: the child starts from an empty environment: it sees a listed env name the request
/// set and its own `AIBD_SESSION`, but none of aibd's `AIBD_*` variables (knife K7 keeps
/// the inherited environment). `printenv` runs without arguments (the whole environment:
/// BSD printenv prints only its first name argument). The variable is set in this test
/// process, which is the aibd process here; its name is unique, so no other test reads it.
#[tokio::test(flavor = "multi_thread")]
async fn the_child_sees_listed_env_and_its_session_id_but_no_aibd_variable() {
    const LEAK: &str = "AIBD_QA_E5_1CED7";
    std::env::set_var(LEAK, "AIBDLEAK-1ced7");
    let tmp = tempfile::tempdir().unwrap();
    let mut p = policy();
    p.programs.push(fx("printenv", "/usr/bin/printenv"));
    let mut d = start(tmp.path(), 8, p).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [None, Some(seat.as_str())] {
            let body = serde_json::json!({
                "argv": ["/usr/bin/printenv"],
                "env": {"MY_VAR": "MYVALUE-1ced7"}
            });
            let s = v.start_on(route, &body);
            assert_eq!(s.0, 201, "{route:?}: {}", s.1);
            let id = s.1["id"].as_str().unwrap().to_string();
            let lines = v.stdout_lines(&id);
            assert!(
                lines.iter().any(|l| l == "MY_VAR=MYVALUE-1ced7"),
                "{lines:?}"
            );
            let own = format!("AIBD_SESSION={id}");
            assert!(lines.contains(&own), "no {own}: {lines:?}");
            assert!(
                !lines.iter().any(|l| l.contains("AIBDLEAK-1ced7")),
                "aibd's own AIBD_* reached the child: {lines:?}"
            );
        }
    })
    .await
    .unwrap();
    d.shutdown().await;
    std::env::remove_var(LEAK);
}

/// T80: of aibd's own environment the child gets only the base set and the names the
/// policy lists; a token aibd happens to have (here a unique name set in this test
/// process, which is the aibd process) never reaches it. Knife: inherit everything again.
#[tokio::test(flavor = "multi_thread")]
async fn the_child_inherits_only_the_base_set_and_the_listed_names() {
    const SECRET: &str = "QA_T80_TOKEN_A9136";
    const LISTED: &str = "QA_T80_LISTED_A9136";
    std::env::set_var(SECRET, "T80SECRET-a9136");
    std::env::set_var(LISTED, "T80LISTED-a9136");
    let tmp = tempfile::tempdir().unwrap();
    let mut p = policy();
    p.programs.push(fx("printenv", "/usr/bin/printenv"));
    p.env.push(LISTED.into());
    let mut d = start(tmp.path(), 8, p).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let s = v.start_on(None, &serde_json::json!({"argv": ["/usr/bin/printenv"]}));
        assert_eq!(s.0, 201, "{}", s.1);
        let id = s.1["id"].as_str().unwrap().to_string();
        let lines = v.stdout_lines(&id);
        assert!(
            !lines.iter().any(|l| l.contains("T80SECRET-a9136")),
            "an unlisted variable of aibd reached the child: {lines:?}"
        );
        let listed = format!("{LISTED}=T80LISTED-a9136");
        assert!(lines.contains(&listed), "no {listed}: {lines:?}");
        assert!(
            lines.iter().any(|l| l.starts_with("PATH=")),
            "the base set passes: {lines:?}"
        );
    })
    .await
    .unwrap();
    d.shutdown().await;
    std::env::remove_var(SECRET);
    std::env::remove_var(LISTED);
}

/// E3 on both routes, the seat route included: the fixed deny list (`DYLD_*`, `LD_*`,
/// `AIBD_*`, `PATH`, any case) is refused with `env_not_allowed` before the allow-list,
/// nothing starts, and the refusal is audited like the others (one folded
/// `session.spawn_rejected`, the program's file name only, no name or value from the
/// request). Knife K18 drops the audit of these early refusals.
#[tokio::test(flavor = "multi_thread")]
async fn deny_listed_env_is_refused_and_audited_on_both_routes() {
    let tmp = tempfile::tempdir().unwrap();
    let mut d = start(tmp.path(), 8, policy()).await;
    let v = d.view();
    tokio::task::spawn_blocking(move || {
        let seat = v.new_seat();
        for route in [Some(seat.as_str()), None] {
            for k in [
                "DYLD_INSERT_LIBRARIES",
                "dyld_library_path",
                "LD_PRELOAD",
                "AIBD_TOKEN",
                "PATH",
            ] {
                let body = serde_json::json!({
                    "argv": ["/usr/bin/true", "ARGSECRET-1ced7"],
                    "env": {k: "DENYSECRET-1ced7"}
                });
                refused(&v.start_on(route, &body), 400, "env_not_allowed");
            }
        }
        assert_eq!(v.count("session.started"), 0);
        assert_eq!(v.sessions(), 0);
        let evs = v.events();
        let rejected: Vec<&serde_json::Value> = evs
            .iter()
            .filter(|e| e["type"] == "session.spawn_rejected")
            .collect();
        assert_eq!(rejected.len(), 1, "{evs:?}");
        let e = rejected[0];
        assert_eq!(e["aggregate_type"], "security", "{e}");
        assert_eq!(e["payload"]["code"], "env_not_allowed", "{e}");
        assert_eq!(e["payload"]["program"], "true", "{e}");
        let text = serde_json::Value::Array(evs.clone()).to_string();
        for secret in [
            "DENYSECRET-1ced7",
            "ARGSECRET-1ced7",
            "DYLD_INSERT_LIBRARIES",
            "LD_PRELOAD",
            "AIBD_TOKEN",
        ] {
            assert!(!text.contains(secret), "{secret} in the event table");
        }
        // the positive half: a listed name on the same daemon starts
        let ok = serde_json::json!({"argv": ["/usr/bin/true"], "env": {"MY_VAR": "1"}});
        let s = v.start_on(Some(seat.as_str()), &ok);
        assert_eq!(s.0, 201, "{}", s.1);
    })
    .await
    .unwrap();
    d.shutdown().await;
}

/// I4, expected gap (a63ab 19:14): a COPY of a shell under another name is not caught by
/// path or name. It runs today; the M2 signer check must turn this into a refusal, and
/// then this test goes red on purpose and is rewritten.
#[tokio::test(flavor = "multi_thread")]
async fn expected_gap_a_renamed_copy_of_a_shell_still_runs() {
    let tmp = tempfile::tempdir().unwrap();
    let copy = tmp.path().join("fixture-ok");
    std::fs::copy("/bin/sh", &copy).unwrap();
    let mut p = policy();
    p.programs.push(fx("fixture-ok", &copy));
    let data = tmp.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let mut d = start(&data, 8, p).await;
    let v = d.view();
    let c = copy.to_str().unwrap().to_string();
    tokio::task::spawn_blocking(move || {
        let r = v.start_on(None, &argv(&[c.as_str(), "-c", "exit 0"]));
        assert_eq!(
            r.0, 201,
            "expected gap I4 closed? rewrite this test: {}",
            r.1
        );
    })
    .await
    .unwrap();
    d.shutdown().await;
}
