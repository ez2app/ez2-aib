//! N-workspace-c4 A (DESIGN-tree-plan-via-aibd-7b81e-1118.md, 5d33a CHECK 0 H/M): while aibd
//! holds the tree it leaves `<data.json>.aibd` next to the file, and `aib-plan set / note / add`
//! send the op to aibd (`POST /api/v1/nodes/plan`) instead of writing the file. aibd applies it
//! with this crate's own writer, so every rule is the same.
//!
//! - The seat's own token (`AIB_SEAT_TOKEN`) when there is one: aibd knows the seat (5d33a L1).
//!   Otherwise aibd's start-up token, with the seat as `--by` says it (as today).
//! - aibd not answering: nothing is written (rc 3), and the line says whether the aibd that left
//!   the marker is still alive (5d33a N1). Never a quiet write to the file.
//! - Loopback only; one HTTP/1.1 exchange with std's TcpStream; no new crate.

use crate::card::Op;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// The marker aibd leaves while it holds the tree.
pub fn mark_path(data: &Path) -> PathBuf {
    let mut s = data.as_os_str().to_os_string();
    s.push(".aibd");
    PathBuf::from(s)
}

/// What the marker says: aibd's pid and its run folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mark {
    pub pid: i64,
    pub run: PathBuf,
}

/// The marker next to `data`, if aibd left one (an unreadable one counts: aibd holds the tree).
pub fn mark_of(data: &Path) -> Option<Mark> {
    let b = std::fs::read(mark_path(data)).ok()?;
    let v: Value = serde_json::from_slice(&b).unwrap_or(Value::Null);
    Some(Mark {
        pid: v["pid"].as_i64().unwrap_or(0),
        run: v["run"].as_str().map(PathBuf::from).unwrap_or_default(),
    })
}

/// Why a send did not write.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Fail {
    /// aibd is not answering: nothing written (rc 3).
    Down(String),
    /// aibd answered no: its code and words (aib-plan's own, as it would print them).
    Refused(String, String),
}

/// The body for `op` (the seat goes in only with the start-up token).
pub fn body_of(op: &Op, seat: Option<&str>) -> Value {
    let mut v = match op {
        Op::Set {
            id,
            status,
            fact,
            head,
            wait,
            reviewer,
            need,
        } => json!({
            "op": "set", "id": id, "status": status, "fact": fact, "head": head,
            "wait": wait, "reviewer": reviewer, "need": need,
        }),
        Op::Note { id, fact } => json!({"op": "note", "id": id, "fact": fact}),
        Op::Add {
            parent,
            id,
            title,
            owner,
            why,
            reviewer,
            need,
        } => json!({
            "op": "add", "parent": parent, "id": id, "title": title, "owner": owner,
            "why": why, "reviewer": reviewer, "need": need,
        }),
    };
    if let Some(o) = v.as_object_mut() {
        o.retain(|_, x| !x.is_null());
        if let Some(s) = seat {
            o.insert("seat".into(), json!(s));
        }
    }
    v
}

fn run_info(run: &Path) -> Result<SocketAddr, String> {
    let info: Value = serde_json::from_slice(
        &std::fs::read(run.join("aibd.json")).map_err(|e| format!("run info: {e}"))?,
    )
    .map_err(|e| format!("run info: {e}"))?;
    let ip: IpAddr = info["ip"]
        .as_str()
        .unwrap_or("")
        .parse()
        .map_err(|_| "run info ip")?;
    if !ip.is_loopback() {
        return Err("run info is not loopback".into());
    }
    let port = info["port"]
        .as_u64()
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p != 0)
        .ok_or("run info port")?;
    Ok(SocketAddr::new(ip, port))
}

fn token_file(run: &Path) -> Result<String, String> {
    let t = std::fs::read_to_string(run.join("aibd.token")).map_err(|e| format!("token: {e}"))?;
    let t = t.trim().to_owned();
    if t.is_empty() || !t.bytes().all(|b| b.is_ascii_graphic()) {
        return Err("token file".into());
    }
    Ok(t)
}

/// Split a raw HTTP/1.1 answer into (status, body).
pub fn parse_answer(raw: &[u8]) -> Option<(u16, Value)> {
    let s = String::from_utf8_lossy(raw);
    let (head, body) = s.split_once("\r\n\r\n")?;
    let status: u16 = head.split_whitespace().nth(1)?.parse().ok()?;
    Some((
        status,
        serde_json::from_str(body.trim()).unwrap_or(Value::Null),
    ))
}

/// Send `op` to the aibd the marker names. `seat_token`: the seat's own token, if any.
pub fn send(m: &Mark, op: &Op, seat: &str, seat_token: Option<&str>) -> Result<Value, Fail> {
    let addr = run_info(&m.run).map_err(Fail::Down)?;
    let (token, body) = match seat_token.filter(|t| !t.is_empty()) {
        Some(t) => (t.to_owned(), body_of(op, None)),
        None => (
            token_file(&m.run).map_err(Fail::Down)?,
            body_of(op, Some(seat)),
        ),
    };
    let body = body.to_string();
    let req = format!(
        "POST /api/v1/nodes/plan HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\
         Authorization: Bearer {token}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\n\r\n{body}",
        body.len()
    );
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(3))
        .map_err(|e| Fail::Down(format!("connect: {e}")))?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(30)));
    let _ = s.set_write_timeout(Some(Duration::from_secs(10)));
    s.write_all(req.as_bytes())
        .map_err(|e| Fail::Down(format!("send: {e}")))?;
    let mut raw = Vec::new();
    s.take(1 << 20)
        .read_to_end(&mut raw)
        .map_err(|e| Fail::Down(format!("read: {e}")))?;
    let (status, v) = parse_answer(&raw).ok_or_else(|| Fail::Down("no answer".into()))?;
    if (200..300).contains(&status) {
        return Ok(v);
    }
    let code = v["code"].as_str().unwrap_or("refused").to_owned();
    let msg = v["message"].as_str().unwrap_or("").to_owned();
    Err(Fail::Refused(code, msg))
}

/// N1: whether the aibd that left the marker is alive (`ps`, through aib-lease's probe).
pub fn pid_words(pid: i64) -> &'static str {
    if pid <= 0 {
        return "標記裡沒有 pid";
    }
    use aib_lease::Probe;
    match aib_lease::SysProbe.proc_start(pid) {
        aib_lease::ProcState::Gone => "留標記的 aibd 已經不在(殘留標記:開 aibd 就會重寫)",
        aib_lease::ProcState::Started(_) => "留標記的 aibd 還在跑,但連不上",
        aib_lease::ProcState::Unknown => "看不出留標記的 aibd 還在不在",
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    #[test]
    fn the_body_carries_the_op_and_the_seat_only_with_the_start_up_token() {
        let op = Op::Note {
            id: "N".into(),
            fact: "看過".into(),
        };
        assert_eq!(
            body_of(&op, Some("7b81e")),
            json!({"op": "note", "id": "N", "fact": "看過", "seat": "7b81e"})
        );
        assert!(
            body_of(&op, None).get("seat").is_none(),
            "reason: seat_with_token"
        );
        let set = Op::Set {
            id: "N".into(),
            status: Some("開發中".into()),
            fact: "f".into(),
            head: None,
            wait: None,
            reviewer: None,
            need: None,
        };
        let b = body_of(&set, None);
        assert_eq!(b["status"], "開發中");
        assert!(b.get("head").is_none());
    }

    /// A marker with an aibd that does not answer: nothing written, the reason is Down.
    #[test]
    fn a_marker_with_no_aibd_writes_nothing() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data.json");
        std::fs::write(&data, "{}").unwrap();
        assert!(mark_of(&data).is_none());
        let run = d.path().join("run");
        std::fs::create_dir(&run).unwrap();
        // a loopback port nothing listens on: bind, read the port, close
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        std::fs::write(
            run.join("aibd.json"),
            json!({"ip": "127.0.0.1", "port": port}).to_string(),
        )
        .unwrap();
        std::fs::write(run.join("aibd.token"), "tok").unwrap();
        std::fs::write(mark_path(&data), json!({"pid": 0, "run": run}).to_string()).unwrap();
        let m = mark_of(&data).unwrap();
        let op = Op::Note {
            id: "N".into(),
            fact: "x".into(),
        };
        assert!(matches!(send(&m, &op, "7b81e", None), Err(Fail::Down(_))));
        assert_eq!(std::fs::read_to_string(&data).unwrap(), "{}");
        // a run info that is not loopback is never called
        std::fs::write(
            run.join("aibd.json"),
            json!({"ip": "192.0.2.1", "port": port}).to_string(),
        )
        .unwrap();
        assert!(
            matches!(send(&m, &op, "7b81e", None), Err(Fail::Down(e)) if e.contains("loopback"))
        );
    }

    #[test]
    fn an_answer_is_read_by_status() {
        let raw = b"HTTP/1.1 400 Bad Request\r\ncontent-type: application/json\r\n\r\n{\"code\":\"entry\",\"message\":\"entry: x\"}";
        assert_eq!(parse_answer(raw).unwrap().0, 400);
        assert!(parse_answer(b"garbage").is_none());
    }
}
