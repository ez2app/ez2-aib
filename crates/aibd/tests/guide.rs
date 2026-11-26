#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! The new-user guide (6770f IFACE-guide-6770f-1126 r3–r4b) through a real aibd: behind the
//! token, a page not confirmed yet learns only that pairing is next, `guide_done` waits for a
//! seat's first answer and writes once, and opening a seat answers the same before and after
//! the record (f163c PREREAD 9, knife g).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};

async fn guide(d: &Daemon, cred: &str) -> Reply {
    let r = d.call("GET", "/api/v1/guide", Some(cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    r
}

fn step<'a>(v: &'a Value, project: usize, id: &str) -> &'a Value {
    v["projects"][project]["steps"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["id"] == id)
        .unwrap()
}

async fn done(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn open(d: &Daemon, mode: &str, cwd: &std::path::Path) -> (u16, Value) {
    let body = json!({"persona_uid": "un0eba", "provider": "claude", "mode": mode,
                      "cwd": cwd.to_str().unwrap()});
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body.to_string()))
        .await;
    (r.code, r.json["code"].clone())
}

#[tokio::test(flavor = "multi_thread")]
async fn the_guide_reads_the_steps_and_its_record_changes_nothing_else() {
    // the look finds nothing: the claude step does not depend on the test machine
    aibd::session_policy_setup::test_search_dirs(Some(vec![]));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let d = Daemon::start_max(dir.path(), 8).await;
    let record = dir.path().join("data").join("guide").join("done");
    let cwd = dir.path().join("work");
    std::fs::create_dir_all(&cwd).unwrap();

    // no token: 401, as every route but the public ones
    let (addr, _) = d.addr_token();
    let r = tokio::task::spawn_blocking(move || {
        use std::io::{Read, Write};
        let mut s = std::net::TcpStream::connect(addr).unwrap();
        let req = format!(
            "GET /api/v1/guide HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
            addr.port()
        );
        s.write_all(req.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        out
    })
    .await
    .unwrap();
    assert!(
        r.starts_with("HTTP/1.1 401"),
        "reason: guide_without_token {r}"
    );

    // a page not confirmed yet: pairing is next, nothing else is told (knives c / f)
    let unconfirmed = d.page(false).await;
    let r = guide(&d, &unconfirmed).await;
    assert_eq!(r.json, aibd::guide::unpaired_body(), "{}", r.body);

    // a confirmed page, before any seat answered
    let page = d.page(true).await;
    let r = guide(&d, &page).await;
    assert_eq!(step(&r.json, 0, "pair")["done"], true, "{}", r.body);
    assert_eq!(step(&r.json, 0, "claude")["why"], "not_found", "{}", r.body);
    // the harness's start-up policy names claude
    assert_eq!(step(&r.json, 0, "allow")["done"], true, "{}", r.body);
    assert_eq!(step(&r.json, 0, "first")["why"], "none_yet", "{}", r.body);
    assert_eq!(step(&r.json, 0, "tour")["why"], "after_first", "{}", r.body);
    assert_eq!(step(&r.json, 1, "host_add")["why"], "none", "{}", r.body);
    assert_eq!(r.json["panel_lit"], false);
    assert!(
        !r.body.contains(dir.path().to_str().unwrap()),
        "reason: path_told {}",
        r.body
    );

    // knife h: nothing but `type`; knife (409): not before a seat answered
    let r = done(
        &d,
        &page,
        json!({"type": "guide_done", "at": "2020-01-01T00:00:00Z"}),
    )
    .await;
    assert_eq!(r.code, 400, "reason: client_time_taken {}", r.body);
    let r = done(&d, &page, json!({"type": "guide_done"})).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "guide_not_ready", "{}", r.body);
    assert!(!record.exists(), "reason: written_before_first");

    // a seat answers
    let (seat, _) = d.open("safe", &cwd).await;
    // r7 (fc7f9 B1): open and silent ⇒ the chat, not another seat
    let r = guide(&d, &page).await;
    assert_eq!(step(&r.json, 0, "first")["why"], "seat_open", "{}", r.body);
    assert_eq!(
        step(&r.json, 0, "first")["next_act"],
        "focus_chat",
        "{}",
        r.body
    );
    let r = d.act(Some(&page), &seat, "hello").await;
    assert!(r.code < 300, "{}", r.body);
    let mut spoke = false;
    for _ in 0..200 {
        if step(&guide(&d, &page).await.json, 0, "first")["done"] == true {
            spoke = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    assert!(spoke, "reason: first_never_done");
    assert!(!record.exists(), "reason: get_wrote");
    let r = guide(&d, &page).await;
    assert_eq!(step(&r.json, 0, "tour")["why"], "not_yet", "{}", r.body);
    assert_eq!(
        step(&r.json, 0, "tour")["next_act"],
        "open_partner_panel",
        "{}",
        r.body
    );

    // knife g: opening a seat answers the same before and after the record (each mode opened
    // twice before, so the opens after compare with a second open, not a first)
    let mut before = Vec::new();
    for mode in ["dgrs", "work", "dgrs", "work"] {
        before.push(open(&d, mode, &cwd).await);
    }

    let r = done(&d, &page, json!({"type": "guide_done"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "opened", "{}", r.body);
    let at = r.json["at"].clone();
    // knife i: a second press answers the first record
    let r = done(&d, &page, json!({"type": "guide_done"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["at"], at, "reason: second_write {}", r.body);
    assert!(record.is_file());

    let mut after = Vec::new();
    for mode in ["dgrs", "work"] {
        after.push(open(&d, mode, &cwd).await);
    }
    assert_eq!(before[2..], after[..], "reason: open_reads_guide");

    let r = guide(&d, &page).await;
    assert_eq!(r.json["panel_lit"], true, "{}", r.body);
    assert_eq!(r.json["done_at"], at, "{}", r.body);
    assert_eq!(step(&r.json, 0, "tour")["done"], true, "{}", r.body);
    // the unconfirmed page still learns nothing
    assert_eq!(
        guide(&d, &unconfirmed).await.json,
        aibd::guide::unpaired_body()
    );
}
