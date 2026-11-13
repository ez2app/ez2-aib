//! `aib-accept`: see the crate docs (lib.rs) for what each step proves.
use aib_accept::{
    events_about, output_text, parse_marker, parse_response, stop_done, Answer, Live, Receipt,
};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitCode, Stdio};
use std::time::{Duration, Instant};

const USAGE: &str = "aib-accept --aibd PATH --fixture PATH [--claude PATH] [--keep]\n  \
    --aibd     the aibd binary to accept (a release or CI build)\n  \
    --fixture  the aib-fixture-tree binary of the same build (the session's program)\n  \
    --claude   a real Claude Code CLI: also run one `claude -p` session (T79)\n  \
    --keep     keep the temp directory (logs, data) and print where it is";

const WINDOWS: bool = cfg!(windows);

struct Args {
    aibd: PathBuf,
    fixture: PathBuf,
    claude: Option<PathBuf>,
    keep: bool,
}

fn parse_args(a: &[String]) -> Result<Args, String> {
    let (mut aibd, mut fixture, mut claude, mut keep) = (None, None, None, false);
    let mut it = a.iter();
    while let Some(x) = it.next() {
        match x.as_str() {
            "--aibd" => aibd = it.next().map(PathBuf::from),
            "--fixture" => fixture = it.next().map(PathBuf::from),
            "--claude" => claude = it.next().map(PathBuf::from),
            "--keep" => keep = true,
            _ => return Err(format!("unknown argument {x}")),
        }
    }
    let abs = |p: PathBuf, what: &str| {
        std::fs::canonicalize(&p)
            .map(aib_accept::plain_path)
            .map_err(|e| format!("{what} {}: {e}", p.display()))
    };
    Ok(Args {
        aibd: abs(aibd.ok_or("--aibd is required")?, "--aibd")?,
        fixture: abs(fixture.ok_or("--fixture is required")?, "--fixture")?,
        claude: claude.map(|c| abs(c, "--claude")).transpose()?,
        keep,
    })
}

// ADR 0005 names one exception for one-shot command-line tools that have no timer
// registry of their own (same as aib-cloud-host probe / opstop): this program's only clock
// is "poll again in a moment, until a deadline". Every wait goes through here.
#[allow(clippy::disallowed_methods)]
fn pause(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

/// Poll `f` every 100 ms until it gives `Some` or `secs` pass.
fn wait_for<T>(secs: u64, mut f: impl FnMut() -> Option<T>) -> Option<T> {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        if let Some(v) = f() {
            return Some(v);
        }
        if Instant::now() >= end {
            return None;
        }
        pause(100);
    }
}

#[cfg(windows)]
fn live(pid: u32) -> Live {
    match Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
    {
        Ok(o) => aib_accept::live_from_tasklist(
            o.status.success(),
            &String::from_utf8_lossy(&o.stdout),
            pid,
        ),
        Err(_) => Live::Unknown,
    }
}

#[cfg(not(windows))]
fn live(pid: u32) -> Live {
    match Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
    {
        Ok(o) => aib_accept::live_from_ps(o.status.success(), &String::from_utf8_lossy(&o.stdout)),
        Err(_) => Live::Unknown,
    }
}

/// Wait up to `secs` for every pid to be gone; the ones not proven gone, with their state.
fn not_gone_after(pids: &[u32], secs: u64) -> Vec<(u32, Live)> {
    let left = |ps: &[u32]| -> Vec<(u32, Live)> {
        ps.iter()
            .map(|&p| (p, live(p)))
            .filter(|(_, l)| *l != Live::Gone)
            .collect()
    };
    wait_for(secs, || {
        let l = left(pids);
        l.is_empty().then_some(())
    });
    left(pids)
}

/// One running aibd, started by us.
struct Daemon {
    child: Child,
    addr: SocketAddr,
    token: String,
}

impl Daemon {
    fn start(
        exe: &Path,
        data: &Path,
        policy: &Path,
        logs: &Path,
        n: u32,
    ) -> Result<Daemon, String> {
        let out_path = logs.join(format!("aibd-{n}.out"));
        let out = std::fs::File::create(&out_path).map_err(|e| format!("log file: {e}"))?;
        let err = std::fs::File::create(logs.join(format!("aibd-{n}.err")))
            .map_err(|e| format!("log file: {e}"))?;
        let mut child = Command::new(exe)
            .env("AIBD_BIND", "127.0.0.1:0")
            .env("AIBD_DATA_DIR", data)
            .env("AIBD_SESSION_POLICY", policy)
            .env_remove("AIBD_ALLOWED_ORIGINS")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            .spawn()
            .map_err(|e| format!("start aibd: {e}"))?;
        match Self::read_start(&mut child, &out_path, logs) {
            Ok((addr, token)) => Ok(Daemon { child, addr, token }),
            Err(e) => {
                // never leave a half-started aibd behind
                let _ = child.kill();
                let _ = child.wait();
                Err(e)
            }
        }
    }

    /// Its start-up line (address, token file) and the token.
    fn read_start(
        child: &mut Child,
        out_path: &Path,
        logs: &Path,
    ) -> Result<(SocketAddr, String), String> {
        let line = wait_for(30, || {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return Some(None);
            }
            let text = std::fs::read_to_string(out_path).ok()?;
            text.lines()
                .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                .find(|v| v["listening"].is_string())
                .map(Some)
        });
        let info = match line {
            Some(Some(v)) => v,
            Some(None) => return Err(format!("aibd exited at start (see {})", logs.display())),
            None => return Err("no start-up line within 30 s".into()),
        };
        let addr: SocketAddr = info["listening"]
            .as_str()
            .and_then(|s| s.parse().ok())
            .ok_or("start-up line: bad address")?;
        if !addr.ip().is_loopback() || addr.port() == 0 {
            return Err(format!("aibd listens on {addr}, not a loopback port"));
        }
        let token = info["token_file"]
            .as_str()
            .ok_or("start-up line: no token_file")
            .and_then(|p| std::fs::read_to_string(p).map_err(|_| "token file unreadable"))?
            .trim()
            .to_string();
        if token.is_empty() {
            return Err("empty token".into());
        }
        Ok((addr, token))
    }

    fn call(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        extra: &str,
    ) -> Result<Answer, String> {
        let body = body.map(|b| b.to_string()).unwrap_or_default();
        let mut s = TcpStream::connect_timeout(&self.addr, Duration::from_secs(5))
            .map_err(|e| format!("{method} {path}: connect: {e}"))?;
        s.set_read_timeout(Some(Duration::from_secs(60)))
            .map_err(|e| e.to_string())?;
        // the token goes in a header only, never in the URL
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n{extra}\r\n{body}",
            self.addr,
            self.token,
            body.len()
        );
        s.write_all(req.as_bytes())
            .map_err(|e| format!("{method} {path}: send: {e}"))?;
        let mut raw = Vec::new();
        s.take(16 * 1024 * 1024)
            .read_to_end(&mut raw)
            .map_err(|e| format!("{method} {path}: read: {e}"))?;
        parse_response(&raw).map_err(|e| format!("{method} {path}: {e}"))
    }

    /// A crash: no shutdown, no sweep (TerminateProcess / SIGKILL).
    fn crash(mut self) -> Result<(), String> {
        self.child.kill().map_err(|e| format!("kill aibd: {e}"))?;
        self.child.wait().map_err(|e| format!("wait aibd: {e}"))?;
        Ok(())
    }

    /// The end of the run: on POSIX an interrupt first (aibd's own shutdown), then a kill.
    fn finish(mut self) {
        #[cfg(not(windows))]
        {
            let _ = Command::new("kill")
                .args(["-INT", &self.child.id().to_string()])
                .status();
            if wait_for(10, || self.child.try_wait().ok().flatten()).is_some() {
                return;
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Daemon {
    /// Never leave an aibd behind: an early `?` drops the daemon here.
    fn drop(&mut self) {
        if matches!(self.child.try_wait(), Ok(None)) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

/// Start the fixture's tree in a session of `seat`; (session, root pid, child, grandchild).
fn start_tree(
    d: &Daemon,
    seat: &str,
    fixture: &Path,
    work: &Path,
    tag: &str,
) -> Result<(Value, u32, u32), String> {
    let marker = work.join(format!("marker-{tag}"));
    let stop = work.join("stop");
    let body = json!({"argv": [fixture.to_string_lossy(), "smoke-tree",
        marker.to_string_lossy(), stop.to_string_lossy()], "cwd": work.to_string_lossy()});
    let a = d.call(
        "POST",
        &format!("/api/v1/seats/{seat}/sessions"),
        Some(&body),
        "",
    )?;
    if a.code != 201 || a.json["pid"].as_u64().unwrap_or(0) == 0 {
        return Err(format!("start: HTTP {} {}", a.code, a.json));
    }
    let (child, grand) = wait_for(20, || {
        std::fs::read_to_string(&marker)
            .ok()
            .and_then(|s| parse_marker(&s))
    })
    .ok_or("the program did not write its marker within 20 s")?;
    Ok((a.json, child, grand))
}

fn session_state(d: &Daemon, id: &str) -> Option<Value> {
    d.call("GET", &format!("/api/v1/sessions/{id}"), None, "")
        .ok()
        .filter(|a| a.code == 200)
        .map(|a| a.json)
}

/// Every event from the start of the store (pages of 500).
fn all_events(d: &Daemon) -> Result<Value, String> {
    let mut all = Vec::new();
    let mut cursor = 0i64;
    for _ in 0..200 {
        let a = d.call(
            "GET",
            &format!("/api/v1/events?cursor={cursor}&limit=500"),
            None,
            "",
        )?;
        let page = a.json["events"].as_array().cloned().unwrap_or_default();
        if a.code != 200 {
            return Err(format!("events: HTTP {}", a.code));
        }
        if page.is_empty() {
            break;
        }
        all.extend(page);
        cursor = a.json["next_cursor"].as_i64().unwrap_or(cursor);
    }
    Ok(json!({"events": all}))
}

fn run(args: &Args, work: &Path, r: &mut Receipt) -> Result<(), String> {
    let data = work.join("data");
    let logs = work.join("logs");
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let mut programs = vec![json!({"name": "fixture", "path": args.fixture})];
    if let Some(c) = &args.claude {
        programs.push(json!({"name": "claude", "path": c}));
    }
    let policy = work.join("policy.json");
    std::fs::write(
        &policy,
        json!({"programs": programs, "env": []}).to_string(),
    )
    .map_err(|e| format!("policy: {e}"))?;

    // 1. open
    let d = match Daemon::start(&args.aibd, &data, &policy, &logs, 1) {
        Ok(d) => d,
        Err(e) => {
            r.fail("open.start", e);
            return Ok(());
        }
    };
    r.pass("open.start", format!("listening {}", d.addr));
    let h = d.call("GET", "/api/v1/health", None, "")?;
    r.check(
        "open.health",
        (h.code == 200 && h.json["status"] == "ok")
            .then(|| format!("instance {}", h.json["instance_id"]))
            .ok_or(format!("HTTP {} {}", h.code, h.json)),
    );
    let key = "Idempotency-Key: accept-seat-1\r\n";
    let s1 = d.call(
        "POST",
        "/api/v1/seats",
        Some(&json!({"name": "accept"})),
        key,
    )?;
    let seat = s1.json["seat_id"].as_str().unwrap_or("").to_string();
    if !r.check(
        "open.seat",
        (s1.code == 201 && !seat.is_empty())
            .then(|| seat.clone())
            .ok_or(format!("HTTP {} {}", s1.code, s1.json)),
    ) {
        d.finish();
        return Ok(());
    }
    let s2 = d.call(
        "POST",
        "/api/v1/seats",
        Some(&json!({"name": "accept"})),
        key,
    )?;
    let list = d.call("GET", "/api/v1/seats", None, "")?;
    let n = list.json["seats"].as_array().map_or(0, Vec::len);
    r.check(
        "open.seat_retry",
        (s2.json["seat_id"] == seat.as_str()
            && s2.header("idempotent-replayed") == Some("true")
            && n == 1)
            .then(|| "same seat, replayed, 1 seat".to_string())
            .ok_or(format!("retry {} {} seats={n}", s2.code, s2.json)),
    );

    // 2. run
    let (sess, child, grand) = match start_tree(&d, &seat, &args.fixture, work, "a") {
        Ok(v) => v,
        Err(e) => {
            r.fail("run.start", e);
            d.finish();
            return Ok(());
        }
    };
    let sid = sess["id"].as_str().unwrap_or("").to_string();
    let root = sess["pid"].as_u64().unwrap_or(0) as u32;
    r.pass(
        "run.start",
        format!("{sid} pid {root} tree {child} {grand}"),
    );

    // 3. see
    let st = session_state(&d, &sid);
    r.check(
        "see.state",
        st.as_ref()
            .filter(|s| s["state"] == "running" && s["seat_id"] == seat.as_str())
            .map(|_| "running, under the seat".to_string())
            .ok_or(format!("{st:?}")),
    );
    let ev = all_events(&d)?;
    r.check(
        "see.events",
        (!events_about(&ev, "session.started", &sid).is_empty())
            .then(|| "session.started on the cursor".to_string())
            .ok_or("no session.started event".into()),
    );
    see_output(&d, &seat, &args.fixture, work, r);

    // 4. stop
    let del = d.call("DELETE", &format!("/api/v1/sessions/{sid}"), None, "")?;
    r.check(
        "stop.answer",
        ((200..300).contains(&del.code) && stop_done(WINDOWS, &del.json))
            .then(|| format!("{} / {}", del.json["state"], del.json["stop"]["status"]))
            .ok_or(format!("HTTP {} {}", del.code, del.json)),
    );
    let left = not_gone_after(&[root, child, grand], 10);
    r.check(
        "stop.tree_gone",
        left.is_empty()
            .then(|| "root, child, grandchild gone".to_string())
            .ok_or(format!("not proven gone: {left:?}")),
    );

    // 5. recover
    recover(args, d, &data, &policy, &logs, &seat, work, r);
    if let Some(c) = &args.claude {
        claude(args, c, &data, &policy, &logs, &seat, work, r);
    }
    Ok(())
}

/// A short program's stdout comes back through the output API.
fn see_output(d: &Daemon, seat: &str, fixture: &Path, work: &Path, r: &mut Receipt) {
    let body =
        json!({"argv": [fixture.to_string_lossy(), "complete"], "cwd": work.to_string_lossy()});
    let a = match d.call(
        "POST",
        &format!("/api/v1/seats/{seat}/sessions"),
        Some(&body),
        "",
    ) {
        Ok(a) if a.code == 201 => a,
        Ok(a) => return r.fail("see.output", format!("start: HTTP {} {}", a.code, a.json)),
        Err(e) => return r.fail("see.output", e),
    };
    let id = a.json["id"].as_str().unwrap_or("").to_string();
    let ended = wait_for(20, || {
        session_state(d, &id).filter(|s| s["state"] != "running" && s["state"] != "pending")
    });
    let text = wait_for(10, || {
        d.call("GET", &format!("/api/v1/sessions/{id}/output"), None, "")
            .ok()
            .map(|a| output_text(&a.json))
            .filter(|t| t.contains("completed"))
    });
    match (ended, text) {
        (Some(s), Some(_)) => r.pass(
            "see.output",
            format!("{} code {}, stdout stored", s["state"], s["exit_code"]),
        ),
        (s, None) => r.fail(
            "see.output",
            format!(
                "stdout not stored (end state {:?})",
                s.map(|s| s["state"].clone())
            ),
        ),
        (None, Some(_)) => r.fail("see.output", "output stored but the session never ended"),
    }
}

/// Crash aibd with a tree running, start it again on the same data.
#[allow(clippy::too_many_arguments)]
fn recover(
    args: &Args,
    d: Daemon,
    data: &Path,
    policy: &Path,
    logs: &Path,
    seat: &str,
    work: &Path,
    r: &mut Receipt,
) {
    let started = start_tree(&d, seat, &args.fixture, work, "b");
    let (sid, pids) = match started {
        Ok((s, c, g)) => (
            s["id"].as_str().unwrap_or("").to_string(),
            vec![s["pid"].as_u64().unwrap_or(0) as u32, c, g],
        ),
        Err(e) => {
            r.fail("recover.start", e);
            d.finish();
            return;
        }
    };
    if let Err(e) = d.crash() {
        r.fail("recover.crash", e);
        return;
    }
    r.pass("recover.crash", format!("aibd killed with {sid} running"));
    let d2 = match Daemon::start(&args.aibd, data, policy, logs, 2) {
        Ok(d) => d,
        Err(e) => {
            r.fail("recover.restart", e);
            return;
        }
    };
    r.pass("recover.restart", format!("listening {}", d2.addr));
    let s = d2.call("GET", &format!("/api/v1/seats/{seat}"), None, "");
    let seen = match &s {
        Ok(a) => format!("HTTP {} {}", a.code, a.json),
        Err(e) => e.clone(),
    };
    let back = matches!(&s, Ok(a) if a.code == 200 && a.json["seat_id"] == seat && a.json["name"] == "accept");
    r.check(
        "recover.seat",
        back.then(|| "seat read back".to_string()).ok_or(seen),
    );
    match all_events(&d2) {
        Ok(ev) => r.check(
            "recover.interrupted",
            (!events_about(&ev, "session.interrupted", &sid).is_empty())
                .then(|| format!("{sid} interrupted"))
                .ok_or(format!("no session.interrupted for {sid}")),
        ),
        Err(e) => r.check("recover.interrupted", Err(e)),
    };
    let left = not_gone_after(&pids, 10);
    if left.is_empty() {
        r.pass("recover.tree_gone", "the crashed session's tree is gone");
    } else if WINDOWS {
        r.fail(
            "recover.tree_gone",
            format!("Job close should kill the tree; left {left:?}"),
        );
    } else {
        // no Job on POSIX: what a crash leaves is reported, not judged here (ADR 0004 blind spots)
        r.note(
            "recover.tree_gone",
            format!("left after a crash (no Job on this OS): {left:?}"),
        );
    }
    d2.finish();
}

/// One real `claude -p` run through aibd (T79).
#[allow(clippy::too_many_arguments)]
fn claude(
    args: &Args,
    exe: &Path,
    data: &Path,
    policy: &Path,
    logs: &Path,
    seat: &str,
    work: &Path,
    r: &mut Receipt,
) {
    let d = match Daemon::start(&args.aibd, data, policy, logs, 3) {
        Ok(d) => d,
        Err(e) => return r.fail("claude.start", e),
    };
    let body = json!({"argv": [exe.to_string_lossy(), "-p", "Reply with the single word ok.",
        "--output-format", "stream-json", "--verbose"], "cwd": work.to_string_lossy()});
    let a = match d.call(
        "POST",
        &format!("/api/v1/seats/{seat}/sessions"),
        Some(&body),
        "",
    ) {
        Ok(a) if a.code == 201 => a,
        Ok(a) => {
            r.fail("claude.start", format!("HTTP {} {}", a.code, a.json));
            return d.finish();
        }
        Err(e) => {
            r.fail("claude.start", e);
            return d.finish();
        }
    };
    let id = a.json["id"].as_str().unwrap_or("").to_string();
    let end = wait_for(300, || {
        session_state(&d, &id).filter(|s| s["state"] != "running" && s["state"] != "pending")
    });
    r.check(
        "claude.exit",
        end.as_ref()
            // H2: only a Windows Job proves a clean end; elsewhere a root that exits by
            // itself may end `stop_unconfirmed`, still with its own exit code
            .filter(|s| {
                s["exit_code"] == 0
                    && (s["state"] == "exited" || (!WINDOWS && s["state"] == "stop_unconfirmed"))
            })
            .map(|s| format!("{} 0", s["state"]))
            .ok_or(format!(
                "end {:?}",
                end.as_ref()
                    .map(|s| (s["state"].clone(), s["exit_code"].clone()))
            )),
    );
    let out = d
        .call(
            "GET",
            &format!("/api/v1/sessions/{id}/output?limit=1000"),
            None,
            "",
        )
        .map(|a| output_text(&a.json))
        .unwrap_or_default();
    r.check(
        "claude.output",
        (out.contains("\"type\":\"result\"") || out.contains("\"type\": \"result\""))
            .then(|| format!("stream-json stored, {} bytes", out.len()))
            .ok_or(format!("no result line in {} bytes of output", out.len())),
    );
    d.finish();
}

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    if raw.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    let args = match parse_args(&raw) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let work = std::env::temp_dir().join(format!("aib-accept-{}", std::process::id()));
    if let Err(e) = std::fs::create_dir_all(&work) {
        eprintln!("work dir: {e}");
        return ExitCode::from(2);
    }
    println!(
        "aib-accept {} os={} aibd={}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        args.aibd.display()
    );
    let mut r = Receipt::default();
    let res = run(&args, &work, &mut r);
    // release whatever the fixture still runs: it exits by itself once the stop file is there
    let _ = std::fs::write(work.join("stop"), b"");
    pause(1000);
    let code = match res {
        Err(e) => {
            r.fail("environment", e);
            2
        }
        Ok(()) if r.failed == 0 => 0,
        Ok(()) => 1,
    };
    println!("{}", r.last_line());
    if args.keep {
        println!("kept {}", work.display());
    } else {
        let _ = std::fs::remove_dir_all(&work);
    }
    ExitCode::from(code)
}
