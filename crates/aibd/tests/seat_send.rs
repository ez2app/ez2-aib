#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! S89-talk-1 end to end through `POST /api/v1/act` (7b81e MAP-tests-dmq-7b81e-1118, 519d0
//! 06:15, 5d33a 06:15): a seat token sends to a running seat of its cabin; the line the other
//! seat's provider reads starts with aibd's marker naming the token's seat, whatever the body
//! says; a seat of another cabin is answered as no seat; the pace answers 429 with retry_after.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, Daemon};

fn code_of(seat: &str) -> &str {
    &seat["seat_".len().."seat_".len() + 5]
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_token_sends_through_act_to_its_cabin_only_and_is_paced() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let b_dir = seat_dir(dir.path(), "b");
    let (b, _) = d.open("safe", &b_dir).await;
    let c_dir = seat_dir(dir.path(), "c");
    let body = serde_json::json!({
        "persona_uid": "un0eba", "provider": "claude", "mode": "safe",
        "cwd": c_dir.to_str().unwrap(), "cabin": "other-cabin",
    })
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let c = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let tok = aibd::seat_token::tokens().issue(&a).unwrap();

    // the body names another sender: the token's seat is still the sender
    let send = serde_json::json!({"type": "seat_send", "to": b, "text": "你好", "from": c});
    let r = d
        .call("POST", "/api/v1/act", Some(&tok), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["state"], "delivered", "{}", r.body);
    let got = log_with(&b_dir, 1);
    assert_eq!(got.len(), 1, "reason: not_written_to_the_seat");
    let v: serde_json::Value = serde_json::from_slice(&got[0].1).unwrap();
    let blocks = v["message"]["content"].as_array().unwrap();
    let marker = blocks[0]["text"].as_str().unwrap();
    assert!(
        marker.starts_with("[aibd] 來自 ") && marker.contains(&format!("@{}", code_of(&a))),
        "reason: marker_not_the_token_seat {marker}"
    );
    assert!(
        !marker.contains(code_of(&c)),
        "reason: body_from_taken {marker}"
    );
    assert_eq!(blocks[1]["text"], "你好");

    // another cabin's seat is no seat at all; nothing reaches it
    let other = serde_json::json!({"type": "seat_send", "to": c, "text": "越艙"});
    let r = d
        .call("POST", "/api/v1/act", Some(&tok), Some(&other.to_string()))
        .await;
    assert_eq!(r.code, 404, "reason: other_cabin_reached {}", r.body);
    assert_eq!(r.json["code"], "seat_not_found", "{}", r.body);
    assert!(read_log(&c_dir).is_empty(), "reason: other_cabin_written");

    // six a minute; the seventh is 429 and says when to try again. A send while b is still in
    // the turn the last one started waits for its end (202 queued, N-workspace): both count
    for i in 0..5 {
        let m = serde_json::json!({"type": "seat_send", "to": b, "text": format!("第{i}則")});
        let r = d
            .call("POST", "/api/v1/act", Some(&tok), Some(&m.to_string()))
            .await;
        assert!(r.code == 200 || r.code == 202, "{}", r.body);
    }
    let r = d
        .call("POST", "/api/v1/act", Some(&tok), Some(&send.to_string()))
        .await;
    assert_eq!(r.code, 429, "{}", r.body);
    assert_eq!(r.json["code"], "talk_rate_limited");
    let wait = r.json["retry_after"].as_u64().unwrap_or(0);
    assert!(
        (1..=60).contains(&wait),
        "reason: no_retry_after {}",
        r.body
    );

    aibd::seat_token::tokens().revoke(&a);
    d.shutdown().await;
}

async fn act(d: &mut Daemon, tok: &str, body: serde_json::Value) -> harness::Reply {
    d.call("POST", "/api/v1/act", Some(tok), Some(&body.to_string()))
        .await
}

/// B1dm-S5 + S3c: a seat_send is a library row (its id is the answer's id); the receiver's own
/// token sees it in its inbox, reads it, and closes it with a reason; the sender cannot close
/// it; to a seat that is not running it is answered 202 queued.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_reads_and_closes_what_another_seat_sent_it() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, b_session) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (ta, tb) = (
        aibd::seat_token::tokens().issue(&a).unwrap(),
        aibd::seat_token::tokens().issue(&b).unwrap(),
    );
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": b, "text": "請看"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let id = r.json["message_id"].as_str().unwrap().to_string();
    assert!(id.starts_with("q_"), "reason: id_not_the_row {}", r.body);

    let r = act(&mut d, &tb, serde_json::json!({"type": "message_inbox"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let m = &r.json["messages"][0];
    assert_eq!(
        m["message_id"],
        id.as_str(),
        "reason: not_in_inbox {}",
        r.body
    );
    assert_eq!(m["from_seat"], format!("seat:{a}"));
    assert_eq!(m["status"], "delivered");
    assert_eq!(m["body"], "請看");
    let thread = m["thread_id"].as_str().unwrap().to_string();
    // the sender's inbox does not hold it
    let r = act(&mut d, &ta, serde_json::json!({"type": "message_inbox"})).await;
    assert_eq!(
        r.json["messages"].as_array().unwrap().len(),
        0,
        "{}",
        r.body
    );

    let r = act(
        &mut d,
        &tb,
        serde_json::json!({"type": "message_read",
        "thread_id": thread, "message_id": id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["read"], 1, "reason: not_read {}", r.body);
    let rev = r.json["message"]["revision"].as_i64().unwrap();

    // only the receiver closes; a seat says why (no page button to stand for it)
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "message_done",
        "message_id": id, "expected_revision": rev, "why": "我寄的"}),
    )
    .await;
    assert_eq!(r.code, 404, "reason: sender_closed {}", r.body);
    let r = act(
        &mut d,
        &tb,
        serde_json::json!({"type": "message_done",
        "message_id": id, "expected_revision": rev}),
    )
    .await;
    assert_eq!(r.code, 400, "reason: seat_closed_without_why {}", r.body);
    let r = act(
        &mut d,
        &tb,
        serde_json::json!({"type": "message_done",
        "message_id": id, "expected_revision": rev, "why": "看完了"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["message"]["status"], "done");
    let r = act(&mut d, &tb, serde_json::json!({"type": "message_inbox"})).await;
    assert_eq!(
        r.json["messages"].as_array().unwrap().len(),
        0,
        "reason: done_still_open"
    );

    // a seat that is not running: the message stays queued for its next start (B1dm-S5)
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{b_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{}", r.body);
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": b, "text": "再一則"}),
    )
    .await;
    assert_eq!(r.code, 202, "reason: not_running_refused {}", r.body);
    assert_eq!(r.json["state"], "queued", "{}", r.body);

    aibd::seat_token::tokens().revoke(&a);
    aibd::seat_token::tokens().revoke(&b);
    d.shutdown().await;
}

/// B1dm-S5: a message to a seat waiting for a slot is kept queued (202); when a slot frees and
/// the seat starts, the line arrives, marked as from the sender, and the row is delivered.
#[tokio::test(flavor = "multi_thread")]
async fn a_message_to_a_waiting_seat_arrives_when_it_starts() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (_, b_session) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    for i in 0..2 {
        let _ = d
            .open("safe", &seat_dir(dir.path(), &format!("run{i}")))
            .await;
    }
    let e_dir = seat_dir(dir.path(), "e");
    let body = serde_json::json!({
        "persona_uid": "un0eba", "provider": "claude", "mode": "safe",
        "cwd": e_dir.to_str().unwrap(),
    })
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.json["session"]["state"], "pending", "{}", r.body);
    let e = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let ta = aibd::seat_token::tokens().issue(&a).unwrap();
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": e, "text": "等你開"}),
    )
    .await;
    assert_eq!(r.code, 202, "reason: waiting_seat_refused {}", r.body);
    assert_eq!(r.json["state"], "queued");
    assert!(read_log(&e_dir).is_empty(), "reason: written_before_start");

    // a slot frees: the waiting seat starts and gets the line
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{b_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{}", r.body);
    let got = log_with(&e_dir, 1);
    assert_eq!(got.len(), 1, "reason: not_redelivered");
    let v: serde_json::Value = serde_json::from_slice(&got[0].1).unwrap();
    let blocks = v["message"]["content"].as_array().unwrap();
    assert!(
        blocks[0]["text"]
            .as_str()
            .unwrap()
            .contains(&format!("@{}", code_of(&a))),
        "reason: redelivered_without_sender {v}"
    );
    assert_eq!(blocks[1]["text"], "等你開");
    aibd::seat_token::tokens().revoke(&a);
    d.shutdown().await;
}

/// N-workspace (ORDER-workspace-519d0-1118, b4297): a message one seat sends another while that
/// seat is in a turn waits for the turn to end (202 queued, waits_for seat_in_turn, its place in
/// the line); when the provider prints the result that ends the turn, the waiting messages are
/// written, in order. A person's own typing is not held back.
#[tokio::test(flavor = "multi_thread")]
async fn a_message_to_a_seat_in_a_turn_waits_for_its_end() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let b_dir = seat_dir(dir.path(), "b");
    let (b, _) = d.open("safe", &b_dir).await;
    let cred = d.page(true).await;
    let ta = aibd::seat_token::tokens().issue(&a).unwrap();

    // the person starts a turn that does not end yet (the fake prints no result for `hold`)
    let r = d.act(Some(&cred), &b, "hold").await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(log_with(&b_dir, 1).len(), 1);

    let mut ids = Vec::new();
    for (n, text) in [(1, "接手這片"), (2, "第二則")] {
        let r = act(
            &mut d,
            &ta,
            serde_json::json!({"type": "seat_send", "to": b, "text": text}),
        )
        .await;
        assert_eq!(r.code, 202, "reason: written_in_a_turn {}", r.body);
        assert_eq!(r.json["state"], "queued", "{}", r.body);
        assert_eq!(r.json["waits_for"], "seat_in_turn", "{}", r.body);
        assert_eq!(r.json["position"], n, "reason: position_wrong {}", r.body);
        ids.push(r.json["message_id"].as_str().unwrap().to_string());
    }
    assert_eq!(read_log(&b_dir).len(), 1, "reason: written_in_a_turn");

    // the person is not held back; the result of this text ends the turn
    let r = d.act(Some(&cred), &b, "go").await;
    assert_eq!(r.code, 200, "reason: person_held_back {}", r.body);
    let got = log_with(&b_dir, 4);
    assert_eq!(got.len(), 4, "reason: not_written_after_the_turn");
    let text = |i: usize| -> String {
        let v: serde_json::Value = serde_json::from_slice(&got[i].1).unwrap();
        v["message"]["content"][1]["text"]
            .as_str()
            .unwrap_or_default()
            .to_owned()
    };
    assert_eq!(text(2), "接手這片", "reason: order_lost");
    assert_eq!(text(3), "第二則", "reason: order_lost");

    let tb = aibd::seat_token::tokens().issue(&b).unwrap();
    let r = act(&mut d, &tb, serde_json::json!({"type": "message_inbox"})).await;
    for id in &ids {
        let m = r.json["messages"]
            .as_array()
            .unwrap()
            .iter()
            .find(|m| m["message_id"] == id.as_str())
            .cloned()
            .unwrap_or_default();
        assert_eq!(
            m["status"], "delivered",
            "reason: row_not_settled {}",
            r.body
        );
    }
    aibd::seat_token::tokens().revoke(&a);
    aibd::seat_token::tokens().revoke(&b);
    d.shutdown().await;
}

/// N-workspace (a9136 thread_send, 09:24): a shared conversation's post that names (`@`) a seat
/// is a queued row with `post_id` from the person; like a seat's message it waits for the
/// seat's turn to end, and its line says it is the person's, not another seat's.
#[tokio::test(flavor = "multi_thread")]
async fn a_post_naming_a_seat_is_written_after_its_turn_as_the_persons() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let b_dir = seat_dir(dir.path(), "b");
    let (b, _) = d.open("safe", &b_dir).await;
    let cred = d.page(true).await;
    // a real conversation b is in (a call is written only to a member now: a9136 b1a39a86)
    let r = act(
        &mut d,
        &cred,
        serde_json::json!({"type": "thread_open", "title": "點名",
                           "members": [{"seat": b, "role": "author"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let r = d.act(Some(&cred), &b, "hold").await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(log_with(&b_dir, 1).len(), 1);

    let lib = aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();
    let mut item = serde_json::Map::new();
    for (k, v) in [
        ("id", serde_json::json!("q_00000000000000a1")),
        ("status", serde_json::json!("queued")),
        ("sender_kind", serde_json::json!("human")),
        ("post_id", serde_json::json!("p_0001")),
        ("thread", serde_json::json!(t)),
    ] {
        item.insert(k.into(), v);
    }
    lib.insert(aib_store::MessageRow {
        message_id: "q_00000000000000a1".into(),
        thread_id: t.clone(),
        from_key: "human:0123456789abcdef".into(),
        to_key: format!("seat:{b}"),
        created: "2027-01-01T00:00:01Z".into(),
        created_unix: 1_800_000_001,
        updated: "2027-01-01T00:00:01Z".into(),
        rev: 1,
        item,
        body: Some("@b 看一下這項".into()),
        body_sha256: "0".repeat(64),
        len: 1,
        idem_owner: None,
        idem_key: None,
        journaled: true,
    })
    .unwrap();

    let r = d.act(Some(&cred), &b, "go").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&b_dir, 3);
    assert_eq!(got.len(), 3, "reason: post_not_written_after_the_turn");
    let v: serde_json::Value = serde_json::from_slice(&got[2].1).unwrap();
    let blocks = v["message"]["content"].as_array().unwrap();
    assert!(
        blocks[0]["text"].as_str().unwrap().contains("來自 真人"),
        "reason: post_not_marked_as_the_persons {v}"
    );
    assert_eq!(blocks[1]["text"], "@b 看一下這項");
    d.shutdown().await;
}

/// 0eed7 VERIFY-pv6 M2 (預覽真資料 10 次 404): a post in a shared conversation that names a
/// seat is a row to that seat; the seat reads and closes it like any message (seat_done sends
/// message_read with the t_ thread first). Only the seat it was sent to reads it, and the
/// conversation's read mark (the timeline's) is not moved by it.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_closes_a_post_that_named_it_in_a_shared_conversation() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (ta, tb) = (
        aibd::seat_token::tokens().issue(&a).unwrap(),
        aibd::seat_token::tokens().issue(&b).unwrap(),
    );
    let cred = d.page(true).await;
    let r = act(
        &mut d,
        &cred,
        serde_json::json!({"type": "thread_open", "title": "共同串",
            "members": [{"seat": a, "role": "author"}, {"seat": b, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    assert!(t.starts_with("t_"), "{}", r.body);
    let r = act(
        &mut d,
        &cred,
        serde_json::json!({"type": "thread_send", "thread_id": t, "body": "請 @b 看", "to": [b]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);

    let r = act(&mut d, &tb, serde_json::json!({"type": "message_inbox"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let m = r.json["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["thread_id"] == t.as_str())
        .unwrap_or_else(|| panic!("reason: post_not_in_inbox {}", r.body))
        .clone();
    let id = m["message_id"].as_str().unwrap().to_string();

    // another member is not who it was sent to: it does not read it
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "message_read", "thread_id": t, "message_id": id}),
    )
    .await;
    assert_eq!(r.code, 404, "reason: other_member_read {}", r.body);

    let r = act(
        &mut d,
        &tb,
        serde_json::json!({"type": "message_read", "thread_id": t, "message_id": id}),
    )
    .await;
    assert_eq!(r.code, 200, "reason: shared_thread_read_refused {}", r.body);
    assert_eq!(r.json["read"], 1, "reason: not_read {}", r.body);
    let rev = r.json["message"]["revision"].as_i64().unwrap();
    let lib = aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();
    assert_ne!(
        lib.read_mark(&t, &format!("seat:{b}")).unwrap().as_deref(),
        Some(id.as_str()),
        "reason: timeline_mark_moved_to_a_delivery_row"
    );

    let r = act(
        &mut d,
        &tb,
        serde_json::json!({"type": "message_done",
        "message_id": id, "expected_revision": rev, "why": "看完了"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["message"]["status"], "done");
    aibd::seat_token::tokens().revoke(&a);
    aibd::seat_token::tokens().revoke(&b);
    d.shutdown().await;
}

/// 待審 1.5 期 (DESIGN-web-inbox-aibd-7ebca-1121 r3 §F): a seat asks urgent; with no grant it
/// goes plain and the answer says why; once the owner granted it a lead role it goes urgent and
/// the receiver's inbox says so and by which role; a level that is not one is refused.
#[tokio::test(flavor = "multi_thread")]
async fn an_urgent_letter_needs_a_granted_lead_role() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (ta, tb) = (
        aibd::seat_token::tokens().issue(&a).unwrap(),
        aibd::seat_token::tokens().issue(&b).unwrap(),
    );
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": b, "text": "急1", "level": "high"}),
    )
    .await;
    assert_eq!(r.code, 400, "reason: bad_level_taken {}", r.body);
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": b, "text": "急1", "level": "urgent"}),
    )
    .await;
    assert!(r.code == 200 || r.code == 202, "{}", r.body);
    assert_eq!(
        r.json["level"], "normal",
        "reason: ungranted_urgent {}",
        r.body
    );
    assert_eq!(r.json["level_downgraded"], "rank_too_low", "{}", r.body);

    let db = aib_store::MessageDb::open(&dir.path().join("data").join("messages.sqlite")).unwrap();
    let at = "2026-11-21T13:00:00+08:00";
    db.role_rank_set("role_lead", aib_store::ranks::Rank::Lead, "test", at)
        .unwrap();
    db.rank_grant(&a, "role_lead", "test", at).unwrap();
    let r = act(
        &mut d,
        &ta,
        serde_json::json!({"type": "seat_send", "to": b, "text": "急2", "level": "urgent"}),
    )
    .await;
    assert!(
        r.code == 200 || r.code == 202,
        "reason: b may be in a turn {}",
        r.body
    );
    assert_eq!(
        r.json["level"], "urgent",
        "reason: granted_not_urgent {}",
        r.body
    );
    assert!(r.json.get("level_downgraded").is_none(), "{}", r.body);

    let r = act(&mut d, &tb, serde_json::json!({"type": "message_inbox"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ms = r.json["messages"].as_array().unwrap();
    let by_body = |t: &str| {
        ms.iter()
            .find(|m| m["body"] == t)
            .unwrap_or_else(|| panic!("{t} not in {}", r.body))
            .clone()
    };
    assert!(
        by_body("急1").get("level").is_none(),
        "reason: plain_shown_urgent"
    );
    let m = by_body("急2");
    assert_eq!(m["level"], "urgent", "reason: urgent_not_in_inbox");
    assert_eq!(m["level_by"], "role_lead");

    aibd::seat_token::tokens().revoke(&a);
    aibd::seat_token::tokens().revoke(&b);
    d.shutdown().await;
}
