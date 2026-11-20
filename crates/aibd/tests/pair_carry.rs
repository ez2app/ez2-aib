#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S89-pair-carry end to end (DECISION-council-pair-persist-519d0-1118 §4; 5d33a CHECKLIST
//! wave 3 F): a real aibd in this process, stopped and started again on the same data folder.
//! aibd here runs as this test binary, which no test changes, so a clean stop is a plain
//! restart and writes nothing; the update is played by putting the file a stop over a new
//! build would write, for the build that is running.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{read_log, seat_dir, Daemon};
use sha2::{Digest, Sha256};
use std::path::Path;

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn carry_file(dir: &Path) -> std::path::PathBuf {
    aibd::pair_carry::path(&dir.join("data"))
}

/// The carry file an update to this build would leave, for `cred`, written `ago` seconds ago.
fn put_carry(dir: &Path, cred: &str, user_page: bool, ago: u64) {
    let running = aibd::pair_carry::exe().unwrap().1;
    let list = [aibd::pairing::CarriedCred {
        digest: Sha256::digest(cred.as_bytes()).into(),
        user_page,
        left: 3600,
    }];
    let doc = aibd::pair_carry::document(
        &list,
        &"0".repeat(64),
        &running,
        aibd::pair_carry::now_unix() - ago,
    );
    aibd::platform::write_private_file(&carry_file(dir), doc.to_string().as_bytes()).unwrap();
}

/// PC5 (DECISION §4 "手動重啟 401", 5d33a 04:16 "手動 kickstart 後 401"): a clean stop with
/// the same build writes nothing, and the page is refused after the restart.
#[tokio::test(flavor = "multi_thread")]
async fn a_restart_without_an_update_carries_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    assert_eq!(d.act(Some(&cred), &seat, "before").await.code, 200);
    d.shutdown().await;
    assert!(
        !carry_file(dir.path()).exists(),
        "same build: no carry file"
    );
    let mut d = Daemon::start(dir.path()).await;
    let r = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.code, 401, "{}", r.body);
    d.shutdown().await;
}

/// PC6: the file an update leaves is taken once: the page types to a safe seat, its status
/// says carried, a dgrs seat refuses it (DECISION §2), the file is gone, and the next restart
/// (no new update) refuses the page (DECISION §4 "第二次用 401").
#[tokio::test(flavor = "multi_thread")]
async fn an_update_carries_the_page_once_and_for_safe_seats_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    d.shutdown().await;
    put_carry(dir.path(), &cred, false, 5);
    let mut d = Daemon::start(dir.path()).await;
    assert!(
        !carry_file(dir.path()).exists(),
        "read once, removed at start"
    );
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(st.code, 200, "{}", st.body);
    assert_eq!(st.json["state"], "confirmed");
    assert_eq!(st.json["carried"], true);
    // not the fingerprint channel: the page is not offered a re-confirm (519d0 01:41 probe)
    assert!(st.json.get("reconfirm").is_none(), "{}", st.body);
    let safe = seat_dir(dir.path(), "safe");
    let (seat, _) = d.open("safe", &safe).await;
    let r = d.act(Some(&cred), &seat, "after the update").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let dgrs = seat_dir(dir.path(), "dgrs");
    let (seat, _) = d.open("dgrs", &dgrs).await;
    let r = d.act(Some(&cred), &seat, "hi").await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "carried_pair_safe_seats_only");
    // BOUNDARY r3 §3: the refusal names the action a re-confirm would be for
    assert_eq!(r.json["reconfirm_act"], "type_dgrs", "{}", r.body);
    // and the re-confirm itself answers off here (no fingerprint channel), with no window
    let rc = d
        .call(
            "POST",
            "/api/v1/pair/reconfirm",
            Some(&cred),
            Some(r#"{"act":"type_dgrs"}"#),
        )
        .await;
    assert_eq!(rc.code, 409, "{}", rc.body);
    assert_eq!(rc.json["code"], "pair_reconfirm_off");
    assert!(read_log(&dgrs).is_empty());
    // nor opens one (b4297 seat_open; 5d33a CHECKLIST-wave3 E1)
    let open = serde_json::json!({"type": "seat_open", "provider": "claude", "mode": "dgrs",
                                  "persona_uid": "un0eba", "idem_key": "carried-1"});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&open.to_string()))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(
        r.json["code"], "carried_pair_read_and_safe_only",
        "reason: carried_pair_opened_dgrs {}",
        r.body
    );
    assert_eq!(r.json["reconfirm_act"], "seat_open", "{}", r.body);
    // nor a safe one, nor sets an assignment (519d0 r16 裁定 3, MATRIX P2)
    for body in [
        serde_json::json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                           "persona_uid": "un0eba", "idem_key": "carried-2"}),
        serde_json::json!({"type": "assign_add", "node_id": "N-c", "seat_id": seat,
                           "role": "author"}),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&cred), Some(&body.to_string()))
            .await;
        assert_eq!(r.code, 403, "reason: carried_pair_not_read_only {}", r.body);
        assert_eq!(r.json["code"], "carried_pair_read_and_safe_only");
    }
    // nor reaches it by calling it in a conversation (5d33a REVIEW-pair-carry-6f21b9a1 N1)
    let open = serde_json::json!({"type": "thread_open", "title": "延續",
                                  "members": [{"seat": seat, "role": "author"}]});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&open.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let send = serde_json::json!({"type": "thread_send", "thread_id": t, "to": "all",
                                  "body": "延續配對的話"});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert!(
        r.body.contains("safe_cannot_send_to_dgrs"),
        "reason: carried_pair_called_dgrs {}",
        r.body
    );
    assert!(read_log(&dgrs).is_empty());
    // nor writes the tree (519d0 r16 ruling 3; MATRIX P2; 7b81e)
    let node = serde_json::json!({"type": "node_add", "parent_id": "N-ws", "title": "x"});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&node.to_string()))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(
        r.json["code"], "carried_pair_read_and_safe_only",
        "reason: carried_pair_wrote_tree {}",
        r.body
    );
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(ev.body.contains("pair.carried"), "{}", ev.body);
    d.shutdown().await;
    assert!(
        !carry_file(dir.path()).exists(),
        "carried once: not written again"
    );
    let mut d = Daemon::start(dir.path()).await;
    let r = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.code, 401, "{}", r.body);
    d.shutdown().await;
}

/// PC7: a carry file older than ten minutes, or put back for another build, is removed and
/// not taken (DECISION §4 "逾 10 分 401"; 5d33a F2).
#[tokio::test(flavor = "multi_thread")]
async fn an_old_or_foreign_carry_file_is_removed_and_not_taken() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    d.shutdown().await;
    put_carry(dir.path(), &cred, false, aibd::pair_carry::MAX_AGE + 30);
    let mut d = Daemon::start(dir.path()).await;
    assert!(!carry_file(dir.path()).exists());
    let r = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.code, 401, "too old: {}", r.body);
    d.shutdown().await;
    // written for some other build
    let running = aibd::pair_carry::exe().unwrap().1;
    let list = [aibd::pairing::CarriedCred {
        digest: Sha256::digest(cred.as_bytes()).into(),
        user_page: false,
        left: 3600,
    }];
    let doc = aibd::pair_carry::document(
        &list,
        &running,
        &"1".repeat(64),
        aibd::pair_carry::now_unix(),
    );
    aibd::platform::write_private_file(&carry_file(dir.path()), doc.to_string().as_bytes())
        .unwrap();
    let mut d = Daemon::start(dir.path()).await;
    assert!(!carry_file(dir.path()).exists());
    let r = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.code, 401, "other build: {}", r.body);
    d.shutdown().await;
}

/// PC8 (5d33a F3): a carried user-page credential stays a user-page one: safe seats only, and
/// no stop.
#[tokio::test(flavor = "multi_thread")]
async fn a_carried_user_page_credential_keeps_its_mark() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    d.shutdown().await;
    put_carry(dir.path(), &cred, true, 5);
    let mut d = Daemon::start(dir.path()).await;
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(st.json["user_page"], true, "{}", st.body);
    let cwd = seat_dir(dir.path(), "seat");
    let (_, session) = d.open("safe", &cwd).await;
    let body = serde_json::json!({"type": "session_stop", "session_id": session});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&body.to_string()))
        .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "stop_built_in_page_only");
    d.shutdown().await;
}

/// PC9 (DECISION §4 "grep 憑證原文 0"): what a stop over a new build writes holds digests only.
#[test]
fn the_carry_file_never_holds_the_credential() {
    let dir = tempfile::tempdir().unwrap();
    let exe = dir.path().join("aibd");
    std::fs::write(&exe, b"new build").unwrap();
    let p = aibd::pairing::Pairing::default()
        .with_channel(aibd::pairing::ConfirmChannel::TerminalUnverified);
    let c = p.create_code().unwrap();
    let r = p.redeem(&c.code).unwrap();
    let short = p.view(&c.pair_id).unwrap().short_code.unwrap();
    p.confirm(&c.pair_id, &short).unwrap();
    let old = hex(&Sha256::digest(b"old build"));
    let n =
        aibd::pair_carry::write_at_stop(dir.path(), &p, &exe, &old, aibd::pair_carry::now_unix())
            .unwrap();
    assert_eq!(n, 1);
    let f = aibd::pair_carry::path(dir.path());
    let text = std::fs::read_to_string(&f).unwrap();
    assert!(!text.contains(&r.credential), "{text}");
    assert!(text.contains(&hex(&Sha256::digest(r.credential.as_bytes()))));
    // 5d33a L1: the channel that confirmed it goes with it
    assert!(
        text.contains(r#""channel":"terminal-unverified""#),
        "{text}"
    );
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(
        std::fs::metadata(&f).unwrap().permissions().mode() & 0o777,
        0o600
    );
    // the same build at stop: nothing written
    std::fs::remove_file(&f).unwrap();
    let same = hex(&Sha256::digest(b"new build"));
    assert_eq!(
        aibd::pair_carry::write_at_stop(dir.path(), &p, &exe, &same, 0),
        Err(aibd::pair_carry::Skip::SameBuild)
    );
    assert!(!f.exists());
}

/// PC10 (0eed7 M1 on 73d1acf2; DECISION §4 "第二次用 401", 5d33a F2): a carry file that was
/// taken, copied and put back is not taken again: the page is refused after the second start.
#[tokio::test(flavor = "multi_thread")]
async fn a_used_carry_file_put_back_is_not_taken_again() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    d.shutdown().await;
    put_carry(dir.path(), &cred, false, 5);
    let copy = std::fs::read(carry_file(dir.path())).unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(st.code, 200, "taken once: {}", st.body);
    d.shutdown().await;
    aibd::platform::write_private_file(&carry_file(dir.path()), &copy).unwrap();
    let mut d = Daemon::start(dir.path()).await;
    assert!(!carry_file(dir.path()).exists(), "removed at start");
    let r = d
        .call("GET", "/api/v1/pair/status", Some(&cred), None)
        .await;
    assert_eq!(r.code, 401, "put back: {}", r.body);
    d.shutdown().await;
}
