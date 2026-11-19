#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! N-web-dmbridge batch 2 (519d0 11-19 02:08, 0eed7 VERIFY-dmbridge): a letter into the 1.x
//! ledger is written only by a built-in page paired this run, and only with legacy_cabin in
//! <data>/bridge.json; a pairing carried over an update writes none.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::path::Path;

fn put_carry(dir: &Path, cred: &str) {
    let running = aibd::pair_carry::exe().unwrap().1;
    let list = [aibd::pairing::CarriedCred {
        digest: Sha256::digest(cred.as_bytes()).into(),
        user_page: false,
        left: 3600,
    }];
    let doc = aibd::pair_carry::document(
        &list,
        &"0".repeat(64),
        &running,
        aibd::pair_carry::now_unix() - 5,
    );
    let path = aibd::pair_carry::path(&dir.join("data"));
    aibd::platform::write_private_file(&path, doc.to_string().as_bytes()).unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn only_a_page_paired_this_run_with_the_switch_writes_a_letter() {
    let dir = tempfile::tempdir().unwrap();
    let body = json!({"type": "legacy_send", "seat": "7ebca", "text": "請看"}).to_string();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    // no bridge.json: off
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&body))
        .await;
    assert_eq!(r.code, 403, "reason: written_without_switch {}", r.body);
    assert_eq!(r.json["code"], "legacy_off", "{}", r.body);
    d.shutdown().await;

    // a pairing carried over an update: refused before anything is read
    put_carry(dir.path(), &page);
    std::fs::write(
        dir.path().join("data").join(aibd::bridge_cfg::FILE),
        json!({"legacy_cabin": "c1"}).to_string(),
    )
    .unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&page), None)
        .await;
    assert_eq!(st.json["carried"], true, "{}", st.body);
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&body))
        .await;
    assert_eq!(r.code, 403, "reason: carried_pair_writes {}", r.body);
    assert_eq!(r.json["code"], "legacy_send_pair_this_run", "{}", r.body);
    d.shutdown().await;
}
