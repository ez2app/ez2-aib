#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S89-talk-2b end to end (IFACE-S89-talk-2b-7b81e-1118): a real aibd with one seat, a
//! confirmed page reads the contact list and the sliders and writes them through act; the
//! start-up token cannot write; a stale revision is 409; nothing the person wrote reaches
//! the event journal.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn a_page_lists_the_seat_and_sets_it_with_the_revision_it_read() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let key = format!("seat:{seat}");

    let r = d.call("GET", "/api/v1/contacts", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    // the page's own row comes first (S89-talk-2c needs its human key), then the seat
    let me = &r.json["contacts"][0];
    assert_eq!(me["self"], true, "reason: no_self_row {}", r.body);
    assert_eq!(me["kind"], "human");
    let me_key = me["key"].as_str().unwrap().to_string();
    assert!(
        me_key.starts_with("human:") && me_key.len() == 22,
        "{me_key}"
    );
    let row = &r.json["contacts"][1];
    assert_eq!(row["key"], key.as_str(), "{}", r.body);
    assert_eq!(row["name"], "送字測試");
    assert_eq!(row["presence"], "running");
    assert_eq!(row["mode"], "safe");
    assert_eq!(row["revision"], 0);

    let set = serde_json::json!({"type":"contact_set","key":key,"expected_revision":0,
        "pinned":true,"note":"只有我看得到的備註"})
    .to_string();
    // the start-up token is not a page: it cannot act
    let r = d.call("POST", "/api/v1/act", None, Some(&set)).await;
    assert_eq!(r.code, 403, "reason: token_acts {}", r.body);
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&set)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["entry"]["revision"], 1);
    assert_eq!(r.json["changed"], serde_json::json!(["pinned", "note"]));
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&set)).await;
    assert_eq!(r.code, 409, "reason: stale_revision_written {}", r.body);
    assert_eq!(r.json["code"], "revision_conflict");

    let r = d.call("GET", "/api/v1/contacts", Some(&cred), None).await;
    assert_eq!(r.json["contacts"][1]["pinned"], true);
    assert_eq!(r.json["contacts"][1]["revision"], 1);
    // an archived seat is hidden unless ?all=1; the page may set its own row
    let arch = serde_json::json!({"type":"contact_set","key":key,"expected_revision":1,
        "archived":true})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&arch))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(r.json["entry"]["archived_at"].is_string());
    let r = d.call("GET", "/api/v1/contacts", Some(&cred), None).await;
    assert_eq!(
        r.json["contacts"].as_array().unwrap().len(),
        1,
        "reason: archived_shown {}",
        r.body
    );
    let r = d
        .call("GET", "/api/v1/contacts?all=1", Some(&cred), None)
        .await;
    assert_eq!(
        r.json["contacts"].as_array().unwrap().len(),
        2,
        "{}",
        r.body
    );
    let mine = serde_json::json!({"type":"contact_set","key":me_key,"expected_revision":0,
        "note":"我自己"})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&mine))
        .await;
    assert_eq!(r.code, 200, "reason: own_row_refused {}", r.body);

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        r.body.contains("contact.set"),
        "reason: contact_set_not_journaled {}",
        r.body
    );
    assert!(
        !r.body.contains("只有我看得到"),
        "reason: note_in_journal {}",
        r.body
    );
    let gone = serde_json::json!({"type":"contact_set","key":"seat:seat_nobody",
        "expected_revision":0,"friend":true})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&gone))
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_sliders_and_the_flows_answer_and_the_file_is_private() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let r = d
        .call("GET", "/api/v1/prefs/contacts_fx", Some(&cred), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["value"]["size"], 70);
    assert_eq!(r.json["revision"], 0);
    let r = d
        .call("GET", "/api/v1/prefs/theme", Some(&cred), None)
        .await;
    assert_eq!(r.code, 404, "{}", r.body);
    let set =
        r#"{"type":"prefs_set","name":"contacts_fx","expected_revision":0,"value":{"speed":0}}"#;
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(set)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["value"]["speed"], 0);
    assert_eq!(r.json["revision"], 1);
    let r = d
        .call("GET", "/api/v1/flows?window=99999", Some(&cred), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["window"], 3600, "reason: window_not_clamped");
    // 2a's library is there (empty): the flows come from it now
    assert_eq!(r.json["source"], "messages");
    assert_eq!(r.json["pairs"], serde_json::json!([]));
    {
        use std::os::unix::fs::PermissionsExt;
        let f = dir.path().join("data").join(aibd::contacts::CONTACTS_FILE);
        let mode = std::fs::metadata(&f).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "reason: contacts_file_not_private");
    }
    d.shutdown().await;
}

/// 5d33a L1 (519d0: a user folder page reads counts and state only): a user page's
/// credential gets the rows without the person's notes and cannot write one.
#[tokio::test(flavor = "multi_thread")]
async fn a_user_page_neither_reads_nor_writes_a_note() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let key = format!("seat:{seat}");
    let built_in = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    let note = serde_json::json!({"type":"contact_set","key":key,"expected_revision":0,
        "note":"內建頁寫的備註"})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&built_in), Some(&note))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);

    let r = d.call("GET", "/api/v1/contacts", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        !r.body.contains("內建頁寫的備註") && !r.body.contains("\"note\""),
        "reason: note_to_user_page {}",
        r.body
    );
    let r = d
        .call(
            "GET",
            "/api/v1/contacts?q=%E5%85%A7%E5%BB%BA%E9%A0%81%E5%AF%AB%E7%9A%84%E5%82%99%E8%A8%BB",
            Some(&user),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["contacts"].as_array().unwrap().len(),
        0,
        "reason: user_page_searches_notes {}",
        r.body
    );
    let r = d
        .call("GET", "/api/v1/contacts", Some(&built_in), None)
        .await;
    assert!(r.body.contains("內建頁寫的備註"), "{}", r.body);

    let set = serde_json::json!({"type":"contact_set","key":key,"expected_revision":1,
        "note":"用戶頁想改"})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&user), Some(&set)).await;
    assert_eq!(r.code, 403, "reason: user_page_wrote_note {}", r.body);
    assert_eq!(r.json["code"], "note_built_in_page_only");
    let pin = serde_json::json!({"type":"contact_set","key":key,"expected_revision":1,
        "pinned":true})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&user), Some(&pin)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    d.shutdown().await;
}
