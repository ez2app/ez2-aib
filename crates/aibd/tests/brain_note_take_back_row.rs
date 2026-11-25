#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! pa2 L1 (f163c REVIEW-pa2-postmerge L1; c2f0b REVIEW-pa2ls-993c0f9b L-c): the page's 收回
//! (`brain_note_delete`) moves a file only when its row's path is a place aibd writes a note of
//! that row's seat. The message library is a file an AI of the same account can write: a row
//! naming another file of the brain library, with that file's own SHA-256, is answered
//! `note_path_not_aibs` and the file stays; a row of aibd's own shape is taken back as before.
//! Its own binary: the brain library is an environment variable, set once before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;
use sha2::{Digest, Sha256};

fn sha(b: &[u8]) -> String {
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

fn row(id: &str, seat: &str, rel: &str, text: &[u8]) -> aib_store::BrainNote {
    aib_store::BrainNote {
        note_id: id.to_owned(),
        seat_id: seat.to_owned(),
        persona_uid: None,
        mode: "work".to_owned(),
        brain_route: "r1".to_owned(),
        rel_path: rel.to_owned(),
        sha256: sha(text),
        bytes: text.len() as i64,
        written_at: "2026-11-25T12:15:00Z".to_owned(),
        written_unix: 1_795_580_100,
        deleted_at: None,
        deleted_by: None,
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_take_back_moves_only_aibds_own_note_of_the_rows_seat() {
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let lib = dir.path().join("lib");
    std::fs::create_dir_all(lib.join("fld_a").join("AIB-notes").join("2026-11")).unwrap();
    std::env::set_var("AIBD_BRAIN_ROOT", &lib);
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;

    // another file of the library, and aibd's own note of seat ab12c
    let other = lib.join("fld_a").join("重要筆記.md");
    std::fs::write(&other, b"mine").unwrap();
    let own_rel = "fld_a/AIB-notes/2026-11/20261125-121500-ab12c-a1b2c3.md";
    let own = lib.join(own_rel);
    std::fs::write(&own, b"one").unwrap();
    let db = aib_store::MessageDb::open(&dir.path().join("data").join(aib_store::MESSAGES_FILE))
        .unwrap();
    db.brain_note_add(row(
        "bn_forged01",
        "seat_ab12cxyz",
        "fld_a/重要筆記.md",
        b"mine",
    ))
    .unwrap();
    db.brain_note_add(row("bn_own00001", "seat_ab12cxyz", own_rel, b"one"))
        .unwrap();

    let take = |id: &str| {
        let b = json!({"type": "brain_note_delete", "note_id": id}).to_string();
        let (d, cred) = (&d, cred.clone());
        async move { d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await }
    };
    let r = take("bn_forged01").await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["results"][0]["result"], "note_path_not_aibs",
        "reason: other_file_taken_back {}",
        r.body
    );
    assert_eq!(
        std::fs::read(&other).ok().as_deref(),
        Some(&b"mine"[..]),
        "reason: other_file_moved"
    );

    // the control: aibd's own note is taken back as before
    let r = take("bn_own00001").await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["results"][0]["result"], "archived",
        "reason: own_note_not_taken_back {}",
        r.body
    );
    assert!(!own.exists(), "reason: own_note_left");
    d.shutdown().await;
}
