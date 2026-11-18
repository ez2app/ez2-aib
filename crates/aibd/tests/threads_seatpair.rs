#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace (519d0 11-18 裁定 2): the built-in page reads two seats talking. Its own test
//! binary: a user folder page is set up here, and the folder aibd serves pages from is one
//! per process (static_files::USER_DIR), so it would turn every page in the threads binary
//! into a user page.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

/// TH7 (519d0 11-18 裁定 2): what two seats of one cabin said to each other is read on the
/// built-in page, with the text; a user folder page does not get that thread.
#[tokio::test(flavor = "multi_thread")]
async fn the_built_in_page_reads_two_seats_talking_and_a_user_page_does_not() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (ka, kb) = (format!("seat:{a}"), format!("seat:{b}"));
    let tid = if ka < kb {
        format!("{ka}~{kb}")
    } else {
        format!("{kb}~{ka}")
    };
    {
        let lib =
            aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();
        let mut item = serde_json::Map::new();
        for (k, v) in [
            ("id", json!("q_00000000000000a1")),
            ("status", json!("delivered")),
            ("talk_kind", json!("say")),
            ("sender_seat", json!(ka)),
            ("sender_kind", json!("seat")),
        ] {
            item.insert(k.into(), v);
        }
        lib.insert(aib_store::MessageRow {
            message_id: "q_00000000000000a1".into(),
            thread_id: tid.clone(),
            from_key: ka.clone(),
            to_key: kb.clone(),
            created: "2027-01-01T00:00:01Z".into(),
            created_unix: 1_800_000_001,
            updated: "2027-01-01T00:00:01Z".into(),
            rev: 1,
            item,
            body: Some("席對席".into()),
            body_sha256: "0".repeat(64),
            len: 9,
            idem_owner: None,
            idem_key: None,
            journaled: true,
        })
        .unwrap();
    }
    let path = format!("/api/v1/threads/{}", tid.replace(':', "%3A"));
    let built_in = d.page(true).await;
    let r = d.call("GET", &path, Some(&built_in), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["messages"][0]["body"], "席對席", "{}", r.body);
    // a user folder page serves `/`: any credential is held as a user page
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    std::fs::write(web.join("index.html"), "<p>mine</p>").unwrap();
    let r = d.call("GET", &path, Some(&built_in), None).await;
    assert_eq!(r.code, 404, "{}", r.body);
    d.shutdown().await;
}
