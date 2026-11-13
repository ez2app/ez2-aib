//! Real-machine acceptance for a built aibd (T06 Windows, T07 macOS, T79 real Claude).
//!
//! `aib-accept --aibd PATH --fixture PATH [--claude PATH]` starts that aibd in a fresh data
//! directory and walks what a person does with it, printing one `STEP` line per check and a
//! last `ACCEPT_OK` / `ACCEPT_FAIL` line (the receipt):
//!
//! 1. **open** — aibd starts on 127.0.0.1, health answers, a seat is created; the same
//!    `Idempotency-Key` again returns the same seat and creates nothing.
//! 2. **run** — a session of the seat starts the fixture's two-process tree
//!    (`aib-fixture-tree smoke-tree`), which writes both pids to a marker file.
//! 3. **see** — the session is `running`, its `session.started` event is on the cursor, and a
//!    short program's stdout comes back through `GET /sessions/{id}/output`.
//! 4. **stop** — DELETE ends it the way the contract allows on this OS, and every pid of the
//!    tree is gone from outside within 10 s.
//! 5. **recover** — with a tree running, aibd is killed (a crash), started again on the same
//!    data directory: the seat reads back, the session is `session.interrupted`, and on
//!    Windows the tree is gone (the Job is `KILL_ON_JOB_CLOSE`).
//! 6. **claude** (only with `--claude`) — a real `claude -p` run ends `exited` with code 0
//!    and its stream-json output is stored.
//!
//! What it does not prove: anything about a second user, signing, the installer, or the
//! page. "Gone" needs proof: a failed check is `unknown`, never `gone`. It never signals a
//! bare pid: leftovers are released through the fixture's stop file.
//!
//! Exit: 0 every check passed, 1 a check failed, 2 usage or a broken environment.

use serde_json::Value;

/// One HTTP answer: status, the raw header block (lower-cased names), the parsed body.
#[derive(Debug, Clone)]
pub struct Answer {
    pub code: u16,
    pub head: String,
    pub json: Value,
}

impl Answer {
    /// A header's value (`name` lower case), first one only.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.head.lines().find_map(|l| {
            let (k, v) = l.split_once(':')?;
            (k.trim().eq_ignore_ascii_case(name)).then(|| v.trim())
        })
    }
}

/// Parse a whole HTTP/1.1 response read until the server closed it.
pub fn parse_response(raw: &[u8]) -> Result<Answer, String> {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("no end of headers")?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| "headers are not UTF-8")?;
    let code = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|c| c.parse::<u16>().ok())
        .ok_or("no status code")?;
    let mut body = raw[split + 4..].to_vec();
    let chunked = head.lines().any(|l| {
        l.split_once(':').is_some_and(|(k, v)| {
            k.trim().eq_ignore_ascii_case("transfer-encoding")
                && v.to_ascii_lowercase().contains("chunked")
        })
    });
    if chunked {
        body = dechunk(&body)?;
    }
    let json = if body.iter().all(u8::is_ascii_whitespace) {
        Value::Null
    } else {
        serde_json::from_slice(&body).map_err(|e| format!("body is not JSON: {e}"))?
    };
    Ok(Answer {
        code,
        head: head.to_string(),
        json,
    })
}

fn dechunk(mut b: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let end = b
            .windows(2)
            .position(|w| w == b"\r\n")
            .ok_or("chunk size line")?;
        let size_txt = std::str::from_utf8(&b[..end]).map_err(|_| "chunk size")?;
        let size_txt = size_txt.split(';').next().unwrap_or("").trim();
        let size = usize::from_str_radix(size_txt, 16).map_err(|_| "chunk size")?;
        b = &b[end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if b.len() < size + 2 {
            return Err("chunk shorter than its size".into());
        }
        out.extend_from_slice(&b[..size]);
        b = &b[size + 2..];
    }
}

/// The fixture's marker: `"<child pid> <grandchild pid>"`.
pub fn parse_marker(s: &str) -> Option<(u32, u32)> {
    let mut it = s.split_whitespace().map(|x| x.parse::<u32>().ok());
    match (it.next()??, it.next()??, it.next()) {
        (a, b, None) if a > 0 && b > 0 => Some((a, b)),
        _ => None,
    }
}

/// Does a DELETE answer say "done" the way the sessions contract allows on this OS?
/// Windows: `killed` + `clean` only. Elsewhere also `stop_unconfirmed` + `unverified` +
/// `observed_clean` (H2: macOS is never `clean`). Survivors are never done.
pub fn stop_done(windows: bool, session: &Value) -> bool {
    let state = session["state"].as_str().unwrap_or("");
    let stop = &session["stop"];
    let status = stop["status"].as_str().unwrap_or("");
    let reason = stop["reason"].as_str().unwrap_or("");
    let no_survivors = stop["survivors"].as_array().is_some_and(Vec::is_empty);
    if !no_survivors {
        return false;
    }
    if state == "killed" && status == "clean" {
        return true;
    }
    !windows && state == "stop_unconfirmed" && status == "unverified" && reason == "observed_clean"
}

/// Whether a pid is there. Only clear evidence is `Gone`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Live {
    Alive,
    Gone,
    Unknown,
}

/// `ps -o stat= -p PID` on a POSIX system: (exit status ok?, its stdout).
pub fn live_from_ps(ok: bool, out: &str) -> Live {
    let st = out.trim();
    if !ok && st.is_empty() {
        return Live::Gone; // ps exits 1 with nothing printed for a pid that does not exist
    }
    if ok && st.starts_with('Z') {
        return Live::Gone; // exited, not reaped yet
    }
    if ok && !st.is_empty() {
        return Live::Alive;
    }
    Live::Unknown
}

/// `tasklist /FI "PID eq N" /FO CSV /NH` on Windows: (exit status ok?, its stdout). A CSV
/// row whose second field is `"N"` means the pid is there; any other successful answer
/// (the "no tasks" note is in the user's language) means it is not. A pid reused by another
/// program reads as there: never "gone" by mistake, only a slower pass.
pub fn live_from_tasklist(ok: bool, out: &str, pid: u32) -> Live {
    if !ok {
        return Live::Unknown;
    }
    let want = format!("\"{pid}\"");
    let found = out
        .lines()
        .any(|l| l.split(',').nth(1).map(str::trim) == Some(want.as_str()));
    if found {
        Live::Alive
    } else {
        Live::Gone
    }
}

/// `canonicalize` on Windows answers `\\?\C:\...`; aibd and a policy file want the plain
/// `C:\...` form. Only a drive path loses the prefix (a `\\?\UNC\` one keeps it).
pub fn plain_path(p: std::path::PathBuf) -> std::path::PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => std::path::PathBuf::from(rest),
        _ => p,
    }
}

/// Every event in `events` (a `GET /api/v1/events` page) of `kind` about `id`: its
/// aggregate is `id`, or its payload names it.
pub fn events_about<'a>(page: &'a Value, kind: &str, id: &str) -> Vec<&'a Value> {
    page["events"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|e| e["type"] == kind)
                .filter(|e| e["aggregate_id"] == id || e["payload"]["id"] == id)
                .collect()
        })
        .unwrap_or_default()
}

/// The text of every stored output line (`GET /sessions/{id}/output`), whatever the
/// payload's field is called: every string in each `session.output` payload, joined.
pub fn output_text(page: &Value) -> String {
    fn strings(v: &Value, out: &mut String) {
        match v {
            Value::String(s) => {
                out.push_str(s);
                out.push('\n');
            }
            Value::Array(a) => a.iter().for_each(|x| strings(x, out)),
            Value::Object(o) => o.values().for_each(|x| strings(x, out)),
            _ => {}
        }
    }
    let mut out = String::new();
    if let Some(items) = page["output"].as_array() {
        for e in items.iter().filter(|e| e["type"] == "session.output") {
            strings(&e["payload"], &mut out);
        }
    }
    out
}

/// The receipt: one line per check, failures counted.
#[derive(Debug, Default)]
pub struct Receipt {
    pub lines: Vec<String>,
    pub failed: usize,
}

impl Receipt {
    pub fn pass(&mut self, step: &str, detail: impl AsRef<str>) {
        self.push(format!("STEP {step} ok :: {}", one_line(detail.as_ref())));
    }
    pub fn fail(&mut self, step: &str, detail: impl AsRef<str>) {
        self.failed += 1;
        self.push(format!("STEP {step} FAIL :: {}", one_line(detail.as_ref())));
    }
    /// Something seen that is not a pass / fail on this OS (a documented gap).
    pub fn note(&mut self, step: &str, detail: impl AsRef<str>) {
        self.push(format!("NOTE {step} :: {}", one_line(detail.as_ref())));
    }
    pub fn check(&mut self, step: &str, r: Result<String, String>) -> bool {
        match r {
            Ok(d) => {
                self.pass(step, d);
                true
            }
            Err(e) => {
                self.fail(step, e);
                false
            }
        }
    }
    fn push(&mut self, l: String) {
        println!("{l}");
        self.lines.push(l);
    }
    pub fn last_line(&self) -> String {
        if self.failed == 0 {
            "ACCEPT_OK".into()
        } else {
            format!("ACCEPT_FAIL failed={}", self.failed)
        }
    }
}

/// A detail never spans lines (the receipt is read line by line), and stays short.
fn one_line(s: &str) -> String {
    let mut t: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if t.len() > 400 {
        let mut cut = 400;
        while !t.is_char_boundary(cut) {
            cut -= 1;
        }
        t.truncate(cut);
        t.push('…');
    }
    t
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_plain_and_a_chunked_answer_parse_the_same() {
        let plain = b"HTTP/1.1 201 Created\r\nIdempotent-Replayed: true\r\ncontent-length: 11\r\n\r\n{\"a\": \"b\"}";
        let a = parse_response(plain).unwrap();
        assert_eq!(a.code, 201);
        assert_eq!(a.json, json!({"a": "b"}));
        assert_eq!(a.header("idempotent-replayed"), Some("true"));
        let chunked =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{\"a\"\r\n6\r\n: \"b\"}\r\n0\r\n\r\n";
        let c = parse_response(chunked).unwrap();
        assert_eq!(c.code, 200);
        assert_eq!(c.json, json!({"a": "b"}));
        assert_eq!(c.header("idempotent-replayed"), None);
        let empty = parse_response(b"HTTP/1.1 204 No Content\r\n\r\n").unwrap();
        assert_eq!(empty.json, Value::Null);
        assert!(
            parse_response(b"HTTP/1.1 200 OK\r\n").is_err(),
            "no end of headers"
        );
        assert!(parse_response(b"HTTP/1.1 200 OK\r\n\r\nnot json").is_err());
    }

    #[test]
    fn a_marker_is_exactly_two_positive_pids() {
        assert_eq!(parse_marker("12 34"), Some((12, 34)));
        assert_eq!(parse_marker(" 12  34\n"), Some((12, 34)));
        assert_eq!(parse_marker("12"), None);
        assert_eq!(parse_marker("12 34 56"), None);
        assert_eq!(parse_marker("0 34"), None);
        assert_eq!(parse_marker("x 34"), None);
    }

    #[test]
    fn windows_needs_killed_and_clean_and_survivors_are_never_done() {
        let clean = json!({"state": "killed", "stop": {"status": "clean", "survivors": []}});
        let unconfirmed = json!({"state": "stop_unconfirmed",
            "stop": {"status": "unverified", "reason": "observed_clean", "survivors": []}});
        let other_reason = json!({"state": "stop_unconfirmed",
            "stop": {"status": "unverified", "reason": "lineage_unavailable", "survivors": []}});
        let survivor = json!({"state": "killed", "stop": {"status": "clean", "survivors": [7]}});
        let no_list = json!({"state": "killed", "stop": {"status": "clean"}});
        assert!(stop_done(true, &clean));
        assert!(stop_done(false, &clean));
        assert!(!stop_done(true, &unconfirmed));
        assert!(stop_done(false, &unconfirmed));
        assert!(!stop_done(false, &other_reason));
        assert!(!stop_done(false, &survivor));
        assert!(
            !stop_done(true, &no_list),
            "a missing survivors list is not an empty one"
        );
    }

    #[test]
    fn ps_says_gone_only_when_it_finds_nothing_or_a_zombie() {
        assert_eq!(live_from_ps(false, ""), Live::Gone);
        assert_eq!(live_from_ps(true, "Z+"), Live::Gone);
        assert_eq!(live_from_ps(true, "S"), Live::Alive);
        assert_eq!(live_from_ps(false, "ps: bad"), Live::Unknown);
        assert_eq!(live_from_ps(true, ""), Live::Unknown);
    }

    #[test]
    fn tasklist_finds_the_pid_in_its_own_column_only() {
        let row = "\"aib-fixture-tree.exe\",\"4242\",\"Console\",\"1\",\"5,120 K\"\r\n";
        assert_eq!(live_from_tasklist(true, row, 4242), Live::Alive);
        assert_eq!(
            live_from_tasklist(true, row, 42),
            Live::Gone,
            "not a substring match"
        );
        assert_eq!(
            live_from_tasklist(true, row, 1),
            Live::Gone,
            "not another column"
        );
        assert_eq!(
            live_from_tasklist(true, "INFO: 沒有執行中的工作符合指定的準則。\r\n", 4242),
            Live::Gone
        );
        assert_eq!(live_from_tasklist(false, "", 4242), Live::Unknown);
    }

    #[test]
    fn only_a_drive_path_loses_the_verbatim_prefix() {
        use std::path::PathBuf;
        assert_eq!(
            plain_path(PathBuf::from(r"\\?\C:\a\aibd.exe")),
            PathBuf::from(r"C:\a\aibd.exe")
        );
        assert_eq!(
            plain_path(PathBuf::from(r"\\?\UNC\srv\x")),
            PathBuf::from(r"\\?\UNC\srv\x")
        );
        assert_eq!(
            plain_path(PathBuf::from("/usr/bin/x")),
            PathBuf::from("/usr/bin/x")
        );
    }

    #[test]
    fn events_are_matched_by_aggregate_or_payload_id() {
        let page = json!({"events": [
            {"type": "session.interrupted", "aggregate_id": "s_1", "payload": {}},
            {"type": "session.interrupted", "aggregate_id": "x", "payload": {"id": "s_2"}},
            {"type": "session.started", "aggregate_id": "s_1", "payload": {}},
        ]});
        assert_eq!(events_about(&page, "session.interrupted", "s_1").len(), 1);
        assert_eq!(events_about(&page, "session.interrupted", "s_2").len(), 1);
        assert_eq!(events_about(&page, "session.interrupted", "s_3").len(), 0);
        assert_eq!(events_about(&json!({}), "session.started", "s_1").len(), 0);
    }

    #[test]
    fn output_text_reads_only_output_lines() {
        let page = json!({"output": [
            {"type": "session.output", "payload": {"stream": "stdout", "line": "{\"type\":\"completed\"}"}},
            {"type": "session.output_closed", "payload": {"stream": "stdout", "why": "eof"}},
        ]});
        let t = output_text(&page);
        assert!(t.contains("completed"));
        assert!(!t.contains("eof"));
        assert_eq!(output_text(&json!({})), "");
    }

    #[test]
    fn the_receipt_counts_failures_and_keeps_each_step_on_one_line() {
        let mut r = Receipt::default();
        r.pass("open", "a\nb");
        r.note("recover", "gap");
        assert_eq!(r.last_line(), "ACCEPT_OK");
        assert!(!r.check("stop", Err("survivor 7".into())));
        assert_eq!(r.failed, 1);
        assert_eq!(r.last_line(), "ACCEPT_FAIL failed=1");
        assert_eq!(r.lines[0], "STEP open ok :: a b");
        assert_eq!(r.lines[1], "NOTE recover :: gap");
        assert_eq!(r.lines[2], "STEP stop FAIL :: survivor 7");
        let long = "x".repeat(1000);
        r.pass("long", &long);
        assert!(r.lines[3].chars().count() < 450);
    }
}
