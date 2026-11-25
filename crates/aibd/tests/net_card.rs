#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! The network card at the door (IFACE-netcard r3; f163c REVIEW-netcardact L1 L2; ebab8 11-23
//! 09:11 standing rule): only the built-in page in this pairing lists or answers a card; a yes
//! that needs the owner raises a window and, when none can be raised here, leaves the card to be
//! answered again; a ticket aibd never approved finishes nothing; `net_cards` counts what waits.
//! Its own test binary: the user folder is set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn card(id: &str) -> aibd::net_card::Card {
    aibd::net_card::Card {
        request_id: id.into(),
        seat_id: "a1b2c".into(),
        persona_uid: "p_1".into(),
        persona_name: "頁面工程師".into(),
        dest_name: "db.lan".into(),
        dest: "10.0.0.5:5432".parse().unwrap(),
        source: "proxy",
        why: None,
        expires_at: now() + 100,
        high: false,
        listener: None,
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_card_is_answered_by_the_built_in_page_only_and_a_yes_only_through_the_owner() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);
    let waiting = |r: &Reply| r.json["net_cards"].as_u64();

    aibd::net_card_web::raise(card("rq_it_1"));
    let r = d.call("GET", "/api/v1/seats", Some(&page), None).await;
    assert_eq!((r.code, waiting(&r)), (200, Some(1)), "{}", r.body);
    let r = act(&d, &page, json!({"type": "net_card_list"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["items"][0]["request_id"], "rq_it_1", "{}", r.body);
    assert_eq!(r.json["items"][0]["dest_port"], 5432, "{}", r.body);

    // no owner check wired in this daemon: no window, and the card is not locked by trying
    let yes = json!({"type": "net_card_answer", "request_id": "rq_it_1", "choice": "min15"});
    for _ in 0..2 {
        let r = act(&d, &page, yes.clone()).await;
        assert_eq!(
            (r.code, why(&r).as_deref()),
            (403, Some("owner_check_unavailable")),
            "reason: card_locked_without_window {}",
            r.body
        );
        assert!(r.json["ticket"].is_null(), "{}", r.body);
    }
    // a ticket aibd never gave
    let mut forged = yes.clone();
    forged["ticket"] = json!("ot_forged");
    let r = act(&d, &page, forged).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_not_approved")),
        "reason: forged_ticket_taken {}",
        r.body
    );
    // 拒絕 needs no one
    let r = act(
        &d,
        &page,
        json!({"type": "net_card_answer", "request_id": "rq_it_1", "choice": "deny"}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["done"].as_bool()),
        (200, Some(true)),
        "{}",
        r.body
    );
    let r = d.call("GET", "/api/v1/seats", Some(&page), None).await;
    assert_eq!(
        waiting(&r),
        Some(0),
        "reason: answered_still_counted {}",
        r.body
    );

    // a user folder page serving `/`: even this credential lists and answers nothing
    aibd::net_card_web::raise(card("rq_it_2"));
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let index = web.join("index.html");
    std::fs::write(&index, "<p>mine</p>").unwrap();
    for body in [
        json!({"type": "net_card_list"}),
        json!({"type": "net_card_answer", "request_id": "rq_it_2", "choice": "deny"}),
        json!({"type": "net_allow_list"}),
        json!({"type": "net_allow_revoke", "seat_id": "a1b2c", "ip": "10.0.0.5", "port": 5432}),
    ] {
        let r = act(&d, &page, body.clone()).await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (403, Some("page_only")),
            "reason: user_page_answered {body} {}",
            r.body
        );
    }
    std::fs::remove_file(&index).unwrap();
    let r = act(&d, &page, json!({"type": "net_card_list"})).await;
    assert_eq!(
        r.json["items"][0]["request_id"], "rq_it_2",
        "reason: user_page_finished_the_card {}",
        r.body
    );
    d.shutdown().await;
}
