#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace wave 2 (b4297; 5d33a CHECKLIST B1): a user folder page serving `/` opens no seat,
//! not even a safe one. Its own binary: who serves `/` is held process-wide, so another test
//! of the same binary would see this one's user page (they raced in 527eed7a).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn a_user_folder_page_opens_no_seat() {
    std::env::remove_var("AIBD_SEAT_INPUT_DGRS");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    std::fs::write(web.join("index.html"), "<p>mine</p>").unwrap();
    let user = d.page_form(true, "&to=user").await;
    let open = json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                      "persona_uid": "un0eba", "idem_key": "k5"});
    let r = d
        .call("POST", "/api/v1/act", Some(&user), Some(&open.to_string()))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "open_built_in_page_only", "{}", r.body);
    let all = d.call("GET", "/api/v1/seats", None, None).await;
    assert_eq!(
        all.json["seats"].as_array().map_or(0, Vec::len),
        0,
        "reason: user_page_opened_a_seat"
    );
    d.shutdown().await;
}
