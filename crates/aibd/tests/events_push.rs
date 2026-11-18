#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S89-events-push (ADR 0003; ORDER-workspace-519d0-1118 第三波 2): `GET /api/v1/events/stream`
//! gives the stored events, then each new one as it is written, with no asking again; a
//! reconnect with `Last-Event-ID` starts after it; an open stream does not hold up aibd's stop.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

fn open(addr: SocketAddr, token: &str, last: Option<i64>) -> TcpStream {
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let extra = last.map_or(String::new(), |n| format!("Last-Event-ID: {n}\r\n"));
    let req = format!(
        "GET /api/v1/events/stream HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {token}\r\n{extra}\r\n",
        addr.port()
    );
    s.write_all(req.as_bytes()).unwrap();
    s
}

/// Read until `want(text so far)` or 10 s; returns the text.
fn read_until(s: &mut TcpStream, want: impl Fn(&str) -> bool) -> String {
    let end = Instant::now() + Duration::from_secs(10);
    let mut got = Vec::new();
    let mut buf = [0u8; 4096];
    while Instant::now() < end {
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => got.extend_from_slice(&buf[..n]),
            Err(_) => {}
        }
        if want(&String::from_utf8_lossy(&got)) {
            break;
        }
    }
    String::from_utf8_lossy(&got).into_owned()
}

fn ids(text: &str) -> Vec<i64> {
    text.lines()
        .filter_map(|l| l.strip_prefix("id:"))
        .map(|v| v.trim().parse().unwrap())
        .collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_page_gets_new_events_pushed_and_resumes_after_its_last_id() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (addr, token) = d.addr_token();
    let _ = d.open("safe", &seat_dir(dir.path(), "a")).await;

    let t2 = token.clone();
    let first = tokio::task::spawn_blocking(move || {
        let mut s = open(addr, &t2, None);
        let head = read_until(&mut s, |t| t.contains("seat.opened"));
        (s, head)
    })
    .await
    .unwrap();
    let (mut s, head) = first;
    assert!(head.contains("text/event-stream"), "{head}");
    assert!(
        head.contains("seat.opened"),
        "reason: stored_events_not_sent {head}"
    );
    let before = *ids(&head).last().unwrap();

    // a new event is pushed on the same stream, no new request
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let pushed = tokio::task::spawn_blocking(move || {
        let t = read_until(&mut s, |t| t.contains(&b));
        (s, t)
    })
    .await
    .unwrap();
    let (s, more) = pushed;
    assert!(
        more.contains("seat.opened"),
        "reason: new_event_not_pushed {more}"
    );
    assert!(ids(&more).iter().all(|n| *n > before), "{more}");

    // a reconnect with the last id seen gets only what came after it
    let last = *ids(&more).last().unwrap();
    let t3 = token.clone();
    let again = tokio::task::spawn_blocking(move || {
        let mut s = open(addr, &t3, Some(last));
        read_until(&mut s, |t| t.contains("id:"))
    })
    .await
    .unwrap();
    assert!(
        ids(&again).iter().all(|n| *n > last),
        "reason: replay_before_last_id {again}"
    );

    // an open stream does not hold up the stop
    let stop = tokio::time::timeout(Duration::from_secs(15), d.shutdown()).await;
    assert!(stop.is_ok(), "reason: stop_waits_on_stream");
    drop(s);
}
