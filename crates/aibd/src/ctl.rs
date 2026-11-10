//! Local command-line client for the aibd API. Never puts the token in a URL.
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::path::Path;
use std::time::{Duration, Instant};

const USAGE: &str = "aibd ctl seat create <name> | seat open <name> -- <absolute-program> [args...] | session start <seat-id> -- <absolute-program> [args...] | watch <session-id> | stop <session-id> | list [seat-id] | policy init";
const MAX_RESPONSE: u64 = 16 * 1024 * 1024;

struct Client {
    addr: SocketAddr,
    token: String,
}

impl Client {
    fn from_runtime() -> Result<Self, String> {
        let dir = data_dir()?.join("run");
        Self::from_run_dir(&dir)
    }

    fn from_run_dir(dir: &Path) -> Result<Self, String> {
        let info: Value = serde_json::from_slice(
            &std::fs::read(dir.join("aibd.json")).map_err(|e| format!("read run info: {e}"))?,
        )
        .map_err(|e| format!("invalid run info: {e}"))?;
        let ip: IpAddr = info
            .get("ip")
            .and_then(Value::as_str)
            .ok_or("run info missing ip")?
            .parse()
            .map_err(|_| "invalid run info ip")?;
        if ip != IpAddr::V4(std::net::Ipv4Addr::LOCALHOST)
            && ip != IpAddr::V6(std::net::Ipv6Addr::LOCALHOST)
        {
            return Err("run info is not loopback".into());
        }
        let port = info
            .get("port")
            .and_then(Value::as_u64)
            .and_then(|p| u16::try_from(p).ok())
            .filter(|p| *p != 0)
            .ok_or("run info invalid port")?;
        let token = std::fs::read_to_string(dir.join("aibd.token"))
            .map_err(|e| format!("read token file: {e}"))?;
        let token = token.trim();
        if token.is_empty() || !token.bytes().all(|b| b.is_ascii_graphic()) {
            return Err("invalid token file".into());
        }
        Ok(Self {
            addr: SocketAddr::new(ip, port),
            token: token.to_owned(),
        })
    }
    fn request(&self, method: &str, path: &str, body: Option<Value>) -> Result<Value, String> {
        let body = match body {
            Some(v) => serde_json::to_vec(&v).map_err(|e| e.to_string())?,
            None => Vec::new(),
        };
        let mut stream = TcpStream::connect_timeout(&self.addr, Duration::from_secs(5))
            .map_err(|e| format!("connect: {e}"))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(40)))
            .map_err(|e| e.to_string())?;
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .map_err(|e| e.to_string())?;
        write!(stream, "{method} {path} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", self.addr, self.token, body.len()).map_err(|e| format!("request: {e}"))?;
        stream
            .write_all(&body)
            .map_err(|e| format!("request body: {e}"))?;
        let deadline = Instant::now() + Duration::from_secs(40);
        let mut raw = Vec::new();
        let mut buf = [0_u8; 8192];
        loop {
            let remaining = deadline
                .checked_duration_since(Instant::now())
                .ok_or("response deadline exceeded")?;
            if remaining.is_zero() {
                return Err("response deadline exceeded".into());
            }
            stream
                .set_read_timeout(Some(remaining))
                .map_err(|e| e.to_string())?;
            let n = stream
                .read(&mut buf)
                .map_err(|e| format!("response: {e}"))?;
            if n == 0 {
                break;
            }
            raw.extend_from_slice(&buf[..n]);
            if raw.len() as u64 > MAX_RESPONSE {
                return Err("response exceeds limit".into());
            }
        }
        parse_response(&raw)
    }
}

fn data_dir() -> Result<std::path::PathBuf, String> {
    std::env::var_os("AIBD_DATA_DIR")
        .map(std::path::PathBuf::from)
        .or_else(aibd::platform::default_data_dir)
        .ok_or_else(|| "no data directory: set AIBD_DATA_DIR".into())
}

fn parse_response(raw: &[u8]) -> Result<Value, String> {
    let split = raw
        .windows(4)
        .position(|x| x == b"\r\n\r\n")
        .ok_or("malformed HTTP response")?;
    let headers = std::str::from_utf8(&raw[..split]).map_err(|_| "invalid HTTP headers")?;
    let mut lines = headers.split("\r\n");
    let status = lines
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or("missing HTTP status")?;
    let mut chunked = false;
    let mut length = None;
    for line in lines {
        let (k, v) = line.split_once(':').ok_or("malformed HTTP header")?;
        if k.eq_ignore_ascii_case("transfer-encoding") {
            chunked = v.trim().eq_ignore_ascii_case("chunked");
        }
        if k.eq_ignore_ascii_case("content-length") {
            length = Some(
                v.trim()
                    .parse::<usize>()
                    .map_err(|_| "invalid content length")?,
            );
        }
    }
    let bytes = &raw[split + 4..];
    let payload = if chunked {
        decode_chunks(bytes)?
    } else {
        if let Some(n) = length {
            if n != bytes.len() {
                return Err("incomplete response body".into());
            }
        }
        bytes.to_vec()
    };
    let value: Value =
        serde_json::from_slice(&payload).map_err(|e| format!("invalid JSON response: {e}"))?;
    if !(200..300).contains(&status) {
        return Err(format!("HTTP {status}: {value}"));
    }
    Ok(value)
}
fn decode_chunks(mut data: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let end = data
            .windows(2)
            .position(|x| x == b"\r\n")
            .ok_or("truncated chunk size")?;
        let head = std::str::from_utf8(&data[..end]).map_err(|_| "invalid chunk size")?;
        let size = usize::from_str_radix(head.split(';').next().unwrap_or(""), 16)
            .map_err(|_| "invalid chunk size")?;
        data = &data[end + 2..];
        if size == 0 {
            if !data.starts_with(b"\r\n") {
                return Err("invalid final chunk".into());
            }
            break;
        }
        let end = size.checked_add(2).ok_or("chunk size overflow")?;
        if data.len() < end || &data[size..end] != b"\r\n" {
            return Err("truncated chunk".into());
        }
        out.extend_from_slice(&data[..size]);
        data = &data[end..];
    }
    Ok(out)
}
fn id(s: &str) -> Result<&str, String> {
    if s.is_empty()
        || !s
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err("invalid identifier".into());
    }
    Ok(s)
}

/// Current session GET is in-memory; after a restart find the persisted row
/// through the seat list. Never turn an unreadable history into success.
fn session_state(c: &Client, session: &str) -> Result<Value, String> {
    match c.request("GET", &format!("/api/v1/sessions/{session}"), None) {
        Ok(v) => Ok(v),
        Err(e) if e.starts_with("HTTP 404:") => {
            let seats = c.request("GET", "/api/v1/seats", None)?;
            let seats = seats
                .get("seats")
                .and_then(Value::as_array)
                .ok_or("seat list missing")?;
            let mut found = None;
            for seat in seats {
                let sid = seat
                    .get("seat_id")
                    .and_then(Value::as_str)
                    .ok_or("seat id missing")?;
                let rows =
                    c.request("GET", &format!("/api/v1/seats/{}/sessions", id(sid)?), None)?;
                for row in rows
                    .get("sessions")
                    .and_then(Value::as_array)
                    .ok_or("session list missing")?
                {
                    if row.get("id").and_then(Value::as_str) == Some(session) {
                        if found.is_some() {
                            return Err("duplicate persisted session".into());
                        }
                        found = Some(row.clone());
                    }
                }
            }
            found.ok_or_else(|| "session not found in persisted history".into())
        }
        Err(e) => Err(e),
    }
}

fn watch(c: &Client, session: &str) -> Result<(), String> {
    watch_with_grace(c, session, Duration::from_secs(5))
}

// This is a short-lived interactive client, not a recurring daemon timer (ADR 0005).
#[allow(clippy::disallowed_methods)]
fn watch_with_grace(c: &Client, session: &str, grace: Duration) -> Result<(), String> {
    let session = id(session)?;
    let mut cursor = 0_i64;
    let mut event_cursor = 0_i64;
    let (mut stdout_closed, mut stderr_closed) = (false, false);
    let mut terminal_seen: Option<Instant> = None;
    loop {
        let page = c.request(
            "GET",
            &format!("/api/v1/sessions/{session}/output?after={cursor}&limit=500"),
            None,
        )?;
        let entries = page
            .get("output")
            .and_then(Value::as_array)
            .ok_or("output missing")?;
        let next = page
            .get("next")
            .and_then(Value::as_i64)
            .ok_or("output cursor missing")?;
        if next < cursor || (!entries.is_empty() && next == cursor) {
            return Err("output cursor did not advance".into());
        }
        for event in entries {
            let payload = event.get("payload").ok_or("output payload missing")?;
            match event.get("type").and_then(Value::as_str) {
                Some("session.output") => {
                    let line = payload
                        .get("line")
                        .and_then(Value::as_str)
                        .ok_or("output line missing")?;
                    // stream-json is JSON per line; non-JSON output is shown verbatim.
                    let shown = serde_json::from_str::<Value>(line)
                        .map(|v| v.to_string())
                        .unwrap_or_else(|_| line.to_owned());
                    match payload.get("stream").and_then(Value::as_str) {
                        Some("stderr") => eprintln!("{shown}"),
                        Some("stdout") => println!("{shown}"),
                        _ => return Err("unknown output stream".into()),
                    }
                    if payload.get("cut").and_then(Value::as_bool) == Some(true) {
                        eprintln!("output line was truncated");
                    }
                }
                Some("session.output_closed") => {
                    if payload.get("why").and_then(Value::as_str) != Some("eof") {
                        return Err(format!("output stream closed without EOF: {payload}"));
                    }
                    match payload.get("stream").and_then(Value::as_str) {
                        Some("stdout") => stdout_closed = true,
                        Some("stderr") => stderr_closed = true,
                        _ => return Err("unknown closed stream".into()),
                    }
                }
                Some("session.output_truncated") => eprintln!("output truncated: {payload}"),
                _ => return Err("unknown output event".into()),
            }
        }
        cursor = next;
        let events = c.request(
            "GET",
            &format!("/api/v1/events?cursor={event_cursor}&limit=500&aggregate_id={session}"),
            None,
        )?;
        if events.get("filter_applied").and_then(Value::as_bool) != Some(true) {
            return Err("server does not support filtered session events".into());
        }
        let items = events
            .get("events")
            .and_then(Value::as_array)
            .ok_or("event list missing")?;
        let event_next = events
            .get("next_cursor")
            .and_then(Value::as_i64)
            .ok_or("event cursor missing")?;
        if event_next < event_cursor || (!items.is_empty() && event_next == event_cursor) {
            return Err("event cursor did not advance".into());
        }
        for event in items {
            if event.get("aggregate_id").and_then(Value::as_str) != Some(session) {
                return Err("server did not filter session events".into());
            }
            eprintln!("event: {event}");
        }
        event_cursor = event_next;
        if stdout_closed && stderr_closed && entries.len() < 500 && items.len() < 500 {
            let state = session_state(c, session)?;
            let status = state
                .get("state")
                .and_then(Value::as_str)
                .ok_or("session state missing")?;
            // ADR 0005 §5 queue (bb7dc 11-09): `pending` = queued, keep waiting
            if status != "running" && status != "pending" {
                println!("{state}");
                return match status {
                    "exited" | "killed" => Ok(()),
                    "interrupted" | "stop_unconfirmed" | "cancelled" | "failed" => {
                        Err(format!("session ended as {status}"))
                    }
                    _ => Err(format!("unrecognized session state: {status}")),
                };
            }
        } else if !(stdout_closed && stderr_closed) && !entries.is_empty() {
            terminal_seen = None;
        } else if !(stdout_closed && stderr_closed) {
            let state = session_state(c, session)?;
            let status = state
                .get("state")
                .and_then(Value::as_str)
                .ok_or("session state missing")?;
            if matches!(
                status,
                "interrupted" | "stop_unconfirmed" | "cancelled" | "failed"
            ) {
                println!("{state}");
                return Err(format!(
                    "session ended as {status} before both output streams closed"
                ));
            }
            // a queued session (ADR 0005 §5) has no output yet: no grace runs
            if status == "running" || status == "pending" {
                terminal_seen = None;
            } else if matches!(status, "exited" | "killed") {
                let since = *terminal_seen.get_or_insert_with(Instant::now);
                if since.elapsed() >= grace {
                    return Err("output streams did not close after session ended".into());
                }
            } else {
                return Err(format!("unrecognized session state: {status}"));
            }
        }
        if entries.len() < 500 && items.len() < 500 {
            std::thread::sleep(Duration::from_millis(300));
        }
    }
}

pub fn run(args: &[&str]) -> Result<(), String> {
    if args == ["--help"] || args == ["-h"] {
        println!("{USAGE}");
        return Ok(());
    }
    if args == ["policy", "init"] {
        return policy_init();
    }
    let c = Client::from_runtime()?;
    execute(&c, args)
}

fn execute(c: &Client, args: &[&str]) -> Result<(), String> {
    let value = match args {
        ["seat", "create", name] => {
            c.request("POST", "/api/v1/seats", Some(json!({"name":name})))?
        }
        ["list"] => c.request("GET", "/api/v1/seats", None)?,
        ["list", seat] => c.request(
            "GET",
            &format!("/api/v1/seats/{}/sessions", id(seat)?),
            None,
        )?,
        ["stop", session] => {
            let result = c.request(
                "DELETE",
                &format!("/api/v1/sessions/{}", id(session)?),
                None,
            )?;
            if result.get("holds_slot").and_then(Value::as_bool) != Some(false)
                || result.get("state").and_then(Value::as_str) == Some("stop_unconfirmed")
            {
                println!("{result}");
                return Err("session stop could not be confirmed".into());
            }
            result
        }
        ["watch", session] => return watch(c, session),
        ["session", "start", seat, "--", argv @ ..] if !argv.is_empty() => {
            let resolved = resolve_argv(argv)?;
            c.request(
                "POST",
                &format!("/api/v1/seats/{}/sessions", id(seat)?),
                Some(json!({"argv":resolved})),
            )?
        }
        ["seat", "open", name, "--", argv @ ..] if !argv.is_empty() => {
            return seat_open(c, name, argv);
        }
        _ => return Err(USAGE.into()),
    };
    println!("{value}");
    Ok(())
}

/// argv for a session start: the program must be an absolute path; it is
/// sent resolved (symlinks followed), the arguments unchanged.
fn resolve_argv(argv: &[&str]) -> Result<Vec<String>, String> {
    if !Path::new(argv[0]).is_absolute() {
        return Err("program must be an absolute path".into());
    }
    let program = std::fs::canonicalize(argv[0]).map_err(|e| format!("resolve program: {e}"))?;
    let program = program.to_str().ok_or("program path is not valid UTF-8")?;
    let mut resolved: Vec<String> = argv.iter().map(|a| a.to_string()).collect();
    resolved[0] = program.to_string();
    Ok(resolved)
}

/// `seat open` (gameworld 2.0 S1, a63ab 11-10 Q2 b): one step for seat
/// create + session start. The program is checked before anything is sent,
/// so a bad argv leaves no seat behind. A session start that fails after the
/// seat was created prints the seat (so `session start <seat-id>` can be
/// retried) and returns the server's own error unchanged. Opens no window.
fn seat_open(c: &Client, name: &str, argv: &[&str]) -> Result<(), String> {
    let resolved = resolve_argv(argv)?;
    let seat = c.request("POST", "/api/v1/seats", Some(json!({"name":name})))?;
    let seat_id = seat
        .get("seat_id")
        .and_then(Value::as_str)
        .ok_or("seat create answered without a seat_id")?;
    let path = format!("/api/v1/seats/{}/sessions", id(seat_id)?);
    match c.request("POST", &path, Some(json!({"argv":resolved}))) {
        Ok(session) => {
            println!("{}", json!({"seat": seat, "session": session}));
            Ok(())
        }
        Err(e) => {
            println!("{}", json!({"seat": seat, "session": null}));
            Err(format!("session start for {seat_id}: {e}"))
        }
    }
}

/// Initialize an advisory user-owned allowlist. This is not a defense against
/// another process running as the same user; aibd's policy is read at startup.
#[cfg(target_os = "macos")]
fn policy_init() -> Result<(), String> {
    use std::process::Command;
    let path = match std::env::var_os("AIBD_SESSION_POLICY") {
        Some(path) => std::path::PathBuf::from(path),
        None => data_dir()?.join("session-policy.json"),
    };
    if !path.is_absolute() {
        return Err("policy path must be absolute".into());
    }
    let shown = path
        .to_str()
        .ok_or("policy path is not valid UTF-8")?
        .replace('\'', "'\\''");
    let mut programs = Vec::new();
    let search = std::env::var_os("PATH").ok_or("PATH is missing")?;
    for name in ["claude", "codex"] {
        let candidate = std::env::split_paths(&search)
            .map(|d| d.join(name))
            .find(|p| p.is_file());
        let Some(candidate) = candidate else {
            eprintln!("{name}: not found");
            continue;
        };
        if !candidate.is_absolute() {
            eprintln!("{name}: PATH entry is not absolute");
            continue;
        }
        let real = match std::fs::canonicalize(&candidate) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("{name}: cannot resolve: {e}");
                continue;
            }
        };
        let mut file = match std::fs::File::open(&real) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("{name}: cannot read: {e}");
                continue;
            }
        };
        let mut prefix = [0_u8; 2];
        if file.read(&mut prefix).is_err()
            || prefix == *b"#!"
            || real.extension().is_some_and(|s| s == "js")
        {
            eprintln!("{name}: script or unreadable; standalone signed executable required");
            continue;
        }
        let verify = Command::new("/usr/bin/codesign")
            .args(["--verify", "--strict"])
            .arg(&real)
            .output()
            .map_err(|e| format!("codesign unavailable: {e}"))?;
        if !verify.status.success() {
            eprintln!("{name}: signature verification failed");
            continue;
        }
        let details = Command::new("/usr/bin/codesign")
            .args(["-dv", "--verbose=4"])
            .arg(&real)
            .output()
            .map_err(|e| format!("codesign unavailable: {e}"))?;
        if !details.status.success() {
            eprintln!("{name}: signature details unavailable");
            continue;
        }
        let text = String::from_utf8_lossy(&details.stderr);
        let team = text.lines().find_map(|l| l.strip_prefix("TeamIdentifier="));
        let Some(team) = team.filter(|s| {
            s.len() == 10
                && s.bytes()
                    .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
        }) else {
            eprintln!("{name}: Team ID unavailable");
            continue;
        };
        let expected = if name == "claude" {
            "Q6L2SF6YDW"
        } else {
            "2DC432GLL2"
        };
        if team != expected {
            eprintln!("{name}: observed Team ID {team} differs from previously observed {expected}; verify the executable before using it");
        }
        programs.push(json!({"name": name, "path": candidate.to_string_lossy(), "signer": team}));
    }
    if programs.is_empty() {
        return Err("no verified standalone provider found; policy not written".into());
    }
    let policy = json!({"programs": programs, "env": []});
    write_policy_new(&path, policy.to_string().as_bytes())?;
    println!("export AIBD_SESSION_POLICY='{shown}'");
    println!("Policy is user-writable and is not a same-user security boundary. Restart aibd to load it.");
    Ok(())
}

#[cfg(target_os = "macos")]
fn write_policy_new(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::fs::OpenOptions;
    use std::os::unix::fs::OpenOptionsExt;
    let parent = path.parent().ok_or("policy has no parent")?;
    if !parent.exists() {
        aibd::platform::create_private_dir(parent).map_err(|e| format!("policy directory: {e}"))?;
    }
    let file_name = path
        .file_name()
        .ok_or("policy has no file name")?
        .to_string_lossy();
    let temporary = parent.join(format!(".{file_name}.{}.tmp", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)
        .map_err(|e| format!("cannot create temporary policy: {e}"))?;
    let written = file
        .write_all(contents)
        .and_then(|_| file.sync_all())
        .and_then(|_| std::fs::hard_link(&temporary, path));
    drop(file);
    let removed = std::fs::remove_file(&temporary);
    written.map_err(|e| format!("cannot create policy without overwriting: {e}"))?;
    removed.map_err(|e| format!("policy created but temporary cleanup failed: {e}"))?;
    Ok(())
}
#[cfg(not(target_os = "macos"))]
fn policy_init() -> Result<(), String> {
    Err("policy init has not been verified on this platform".into())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[cfg(target_os = "macos")]
    #[test]
    fn policy_creation_does_not_overwrite_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("policy.json");
        std::fs::write(&path, b"original").unwrap();
        assert!(write_policy_new(&path, b"replacement").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"original");
        let fresh = dir.path().join("fresh.json");
        write_policy_new(&fresh, b"new").unwrap();
        assert_eq!(std::fs::read(&fresh).unwrap(), b"new");
    }

    fn fake(responses: Vec<&'static str>) -> (Client, std::thread::JoinHandle<Vec<String>>) {
        fake_status(responses.into_iter().map(|r| (200, r)).collect())
    }

    fn fake_status(
        responses: Vec<(u16, &'static str)>,
    ) -> (Client, std::thread::JoinHandle<Vec<String>>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let thread = std::thread::spawn(move || {
            let mut requests = Vec::new();
            for (status, response) in responses {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut header = Vec::new();
                let mut byte = [0_u8; 1];
                while !header.ends_with(b"\r\n\r\n") {
                    socket.read_exact(&mut byte).unwrap();
                    header.push(byte[0]);
                }
                let header = String::from_utf8(header).unwrap();
                let length = header
                    .lines()
                    .find_map(|l| l.strip_prefix("Content-Length: "))
                    .unwrap()
                    .parse::<usize>()
                    .unwrap();
                let mut body = vec![0_u8; length];
                socket.read_exact(&mut body).unwrap();
                requests.push(format!("{header}{}", String::from_utf8(body).unwrap()));
                write!(
                    socket,
                    "HTTP/1.1 {status} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",
                    response.len()
                )
                .unwrap();
            }
            requests
        });
        (
            Client {
                addr,
                token: "test-secret".into(),
            },
            thread,
        )
    }

    #[test]
    fn create_start_stop_use_expected_routes_and_report_unconfirmed() {
        let (c, server) = fake(vec![
            r#"{"seat_id":"seat_1"}"#,
            r#"{"id":"session_1"}"#,
            r#"{"holds_slot":true,"state":"stop_unconfirmed"}"#,
        ]);
        execute(&c, &["seat", "create", "One"]).unwrap();
        let program = std::env::current_exe().unwrap();
        execute(
            &c,
            &[
                "session",
                "start",
                "seat_1",
                "--",
                program.to_str().unwrap(),
            ],
        )
        .unwrap();
        assert!(execute(&c, &["stop", "session_1"]).is_err());
        let requests = server.join().unwrap();
        assert!(requests[0].starts_with("POST /api/v1/seats HTTP/1.1"));
        assert!(requests[1].starts_with("POST /api/v1/seats/seat_1/sessions HTTP/1.1"));
        let body = requests[1].split_once("\r\n\r\n").unwrap().1;
        let body: Value = serde_json::from_str(body).unwrap();
        assert_eq!(
            body["argv"][0],
            std::fs::canonicalize(program).unwrap().to_str().unwrap()
        );
        assert!(body.get("env").is_none());
        assert!(requests[2].starts_with("DELETE /api/v1/sessions/session_1 HTTP/1.1"));
    }

    fn body_of(request: &str) -> Value {
        serde_json::from_str(request.split_once("\r\n\r\n").unwrap().1).unwrap()
    }

    #[test]
    fn seat_open_creates_the_seat_then_starts_its_session() {
        let (c, server) = fake(vec![
            r#"{"seat_id":"seat_9","name":"One","state":"created"}"#,
            r#"{"session_id":"session_9","state":"starting"}"#,
        ]);
        let program = std::env::current_exe().unwrap();
        execute(
            &c,
            &["seat", "open", "One", "--", program.to_str().unwrap(), "x"],
        )
        .unwrap();
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert!(requests[0].starts_with("POST /api/v1/seats HTTP/1.1"));
        assert_eq!(body_of(&requests[0]), json!({"name":"One"}));
        assert!(requests[1].starts_with("POST /api/v1/seats/seat_9/sessions HTTP/1.1"));
        let canon = std::fs::canonicalize(&program).unwrap();
        assert_eq!(
            body_of(&requests[1]),
            json!({"argv":[canon.to_str().unwrap(), "x"]})
        );
    }

    #[test]
    fn seat_open_with_a_relative_program_sends_nothing() {
        let (c, server) = fake(vec![]);
        let err = execute(&c, &["seat", "open", "One", "--", "sleep", "1"]).unwrap_err();
        assert_eq!(err, "program must be an absolute path");
        assert!(server.join().unwrap().is_empty());
    }

    #[test]
    fn seat_open_keeps_the_session_error_and_names_the_seat() {
        let (c, server) = fake_status(vec![
            (
                201,
                r#"{"seat_id":"seat_9","name":"One","state":"created"}"#,
            ),
            (
                403,
                r#"{"error":{"code":"forbidden_program","message":"not on the allowlist"}}"#,
            ),
        ]);
        let program = std::env::current_exe().unwrap();
        let err = execute(
            &c,
            &["seat", "open", "One", "--", program.to_str().unwrap()],
        )
        .unwrap_err();
        assert!(
            err.starts_with("session start for seat_9: HTTP 403: "),
            "{err}"
        );
        assert!(err.contains("forbidden_program"), "{err}");
        assert_eq!(server.join().unwrap().len(), 2);
    }

    #[test]
    fn seat_open_stops_when_the_seat_is_refused() {
        let (c, server) = fake_status(vec![(
            400,
            r#"{"error":{"code":"bad_request","message":"name must be 1-128 characters"}}"#,
        )]);
        let program = std::env::current_exe().unwrap();
        let err = execute(&c, &["seat", "open", "", "--", program.to_str().unwrap()]).unwrap_err();
        assert!(err.starts_with("HTTP 400: "), "{err}");
        assert_eq!(server.join().unwrap().len(), 1);
    }

    #[test]
    fn seat_open_needs_an_id_shaped_seat_id() {
        let (c, server) = fake(vec![r#"{"seat_id":"../x"}"#]);
        let program = std::env::current_exe().unwrap();
        assert!(execute(
            &c,
            &["seat", "open", "One", "--", program.to_str().unwrap()]
        )
        .is_err());
        assert_eq!(server.join().unwrap().len(), 1);
    }

    #[test]
    fn watch_waits_for_both_streams_and_terminal_state() {
        let page = r#"{"output":[{"type":"session.output_closed","payload":{"stream":"stdout","why":"eof"}},{"type":"session.output_closed","payload":{"stream":"stderr","why":"eof"}}],"next":2}"#;
        let (c, server) = fake(vec![
            page,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
        ]);
        execute(&c, &["watch", "session_1"]).unwrap();
        let requests = server.join().unwrap();
        assert!(requests[0]
            .starts_with("GET /api/v1/sessions/session_1/output?after=0&limit=500 HTTP/1.1"));
        assert!(requests[1]
            .starts_with("GET /api/v1/events?cursor=0&limit=500&aggregate_id=session_1 HTTP/1.1"));
        assert!(requests[2].starts_with("GET /api/v1/sessions/session_1 HTTP/1.1"));
    }
    /// ADR 0005 §5 queue (bb7dc 11-09): a queued session is waited for
    /// with no grace, then watched as usual once it runs.
    #[test]
    fn watch_keeps_waiting_while_the_session_is_queued() {
        let closed = r#"{"output":[{"type":"session.output_closed","payload":{"stream":"stdout","why":"eof"}},{"type":"session.output_closed","payload":{"stream":"stderr","why":"eof"}}],"next":2}"#;
        let (c, server) = fake(vec![
            r#"{"output":[],"next":0}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"pending"}"#,
            closed,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
        ]);
        watch_with_grace(&c, "session_1", Duration::ZERO).unwrap();
        assert_eq!(server.join().unwrap().len(), 6);
    }

    /// ... and a queued session that was cancelled or failed to start is
    /// an error, never success.
    #[test]
    fn a_cancelled_or_failed_queued_session_is_not_success() {
        for end in ["cancelled", "failed"] {
            let st: &'static str = if end == "cancelled" {
                r#"{"state":"cancelled"}"#
            } else {
                r#"{"state":"failed"}"#
            };
            let (c, server) = fake(vec![
                r#"{"output":[],"next":0}"#,
                r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
                st,
            ]);
            let e = watch(&c, "session_1").unwrap_err();
            assert!(e.contains(end), "{e}");
            assert_eq!(server.join().unwrap().len(), 3);
        }
    }

    #[test]
    fn interrupted_history_with_unclosed_streams_is_not_success() {
        let (c, server) = fake(vec![
            r#"{"output":[],"next":0}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"interrupted"}"#,
        ]);
        assert!(execute(&c, &["watch", "session_1"]).is_err());
        assert_eq!(server.join().unwrap().len(), 3);
    }
    #[test]
    fn watch_reports_unclosed_streams_after_terminal_grace() {
        let (c, server) = fake(vec![
            r#"{"output":[],"next":0}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
        ]);
        let error = watch_with_grace(&c, "session_1", Duration::ZERO).unwrap_err();
        assert!(error.contains("output streams did not close"));
        assert_eq!(server.join().unwrap().len(), 3);
    }
    #[test]
    fn watch_drains_output_before_checking_the_terminal_grace() {
        let (c, server) = fake(vec![
            r#"{"output":[{"type":"session.output","payload":{"stream":"stdout","line":"first"}}],"next":1}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"output":[{"type":"session.output_closed","payload":{"stream":"stdout","why":"eof"}},{"type":"session.output_closed","payload":{"stream":"stderr","why":"eof"}}],"next":3}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
        ]);
        watch_with_grace(&c, "session_1", Duration::ZERO).unwrap();
        assert_eq!(server.join().unwrap().len(), 5);
    }
    #[test]
    fn output_after_terminal_observed_resets_idle_grace() {
        let (c, server) = fake(vec![
            r#"{"output":[],"next":0}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
            r#"{"output":[{"type":"session.output","payload":{"stream":"stdout","line":"late"}}],"next":1}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"output":[{"type":"session.output_closed","payload":{"stream":"stdout","why":"eof"}},{"type":"session.output_closed","payload":{"stream":"stderr","why":"eof"}}],"next":3}"#,
            r#"{"events":[],"next_cursor":0,"filter_applied":true}"#,
            r#"{"state":"exited"}"#,
        ]);
        watch_with_grace(&c, "session_1", Duration::from_millis(1)).unwrap();
        assert_eq!(server.join().unwrap().len(), 8);
    }
    #[test]
    fn watch_rejects_an_old_server_without_filter_confirmation() {
        let (c, server) = fake(vec![
            r#"{"output":[],"next":0}"#,
            r#"{"events":[],"next_cursor":0}"#,
        ]);
        assert!(watch_with_grace(&c, "session_1", Duration::ZERO)
            .unwrap_err()
            .contains("filtered session events"));
        assert_eq!(server.join().unwrap().len(), 2);
    }
    #[test]
    fn rejects_nonlocal_id() {
        assert!(id("../x").is_err());
        assert!(id("seat_1").is_ok());
    }
    #[test]
    fn parses_and_rejects_truncated_http() {
        assert_eq!(
            parse_response(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}").unwrap(),
            json!({})
        );
        assert!(parse_response(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{}").is_err());
        assert_eq!(
            parse_response(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n"
            )
            .unwrap(),
            json!({})
        );
        assert!(
            parse_response(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 2\r\n\r\n{}").is_err()
        );
        assert!(
            parse_response(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{\r\n")
                .is_err()
        );
    }
    #[test]
    fn runtime_info_refuses_non_loopback() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("aibd.json"),
            br#"{"ip":"192.0.2.1","port":1234}"#,
        )
        .unwrap();
        std::fs::write(dir.path().join("aibd.token"), "secret").unwrap();
        assert!(Client::from_run_dir(dir.path()).is_err());
        std::fs::write(
            dir.path().join("aibd.json"),
            br#"{"ip":"127.0.0.1","port":1234}"#,
        )
        .unwrap();
        assert!(Client::from_run_dir(dir.path()).is_ok());
    }
    #[test]
    fn client_sends_token_as_header_not_in_path() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut byte = [0_u8; 1];
            while !request.ends_with(b"\r\n\r\n") {
                socket.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .unwrap();
            String::from_utf8(request).unwrap()
        });
        let client = Client {
            addr,
            token: "test-secret".into(),
        };
        assert_eq!(
            client.request("GET", "/api/v1/seats", None).unwrap(),
            json!({})
        );
        let request = server.join().unwrap();
        assert!(request.starts_with("GET /api/v1/seats HTTP/1.1\r\n"));
        assert!(request.contains("Authorization: Bearer test-secret\r\n"));
    }
}
