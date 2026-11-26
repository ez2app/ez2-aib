#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! §7-2 寫回人格 at the door, the owner's approve (S2-2 part 2; ebab8 11-23 09:11 常設規則: a
//! built-in-page gate has a test that sends the request): a waiting proposal is written back
//! only from the built-in page in this pairing, and only with the owner's own approved ticket
//! for this very proposal — not by a seat's token, not while a user folder page serves `/`,
//! never with a made-up ticket. Its own test binary: the user folder is set once per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use aibd::persona_propose::{Ask, Card, Stand, BOOK};
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

const OLD: &str = "---\nuid: un0eba\ncode: send-test\n---\n# 人格\n規則一\n";

async fn act(d: &Daemon, cred: &str, body: &Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

#[tokio::test(flavor = "multi_thread")]
async fn approve_is_the_built_in_pages_with_the_owners_own_ticket_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let page = d.page(true).await;

    // one waiting proposal, in this aibd's own book (the same process as the daemon)
    let text = format!("{OLD}規則二\n");
    let card = Card {
        uid: "un0eba",
        name: "送字測試",
        sha: &aibd::persona_propose::sha_hex(OLD.as_bytes()),
        text: OLD,
    };
    let ask = Ask {
        seat_id: &seat,
        card_text: &text,
        why: "door test",
    };
    let (p, _) = BOOK
        .propose(&card, &ask, || Some("pp_door_1".into()), now())
        .unwrap();
    let approve = |ticket: Option<&str>| {
        let mut b = json!({"type": "persona_propose_approve", "request_id": p.id,
            "diff_sha": p.diff_sha});
        if let Some(t) = ticket {
            b["ticket"] = json!(t);
        }
        b
    };
    let still_waiting = || {
        BOOK.get(None, &p.id, now())
            .is_some_and(|q| matches!(q.stand, Stand::Waiting))
    };

    // no ticket: the owner is asked (202 + ticket), or no fingerprint here (403); nothing written
    let r = act(&d, &page, &approve(None)).await;
    assert!(
        (r.code == 202 && r.json["ticket"].is_string())
            || (r.code == 403 && r.json["reason"] == "owner_check_unavailable"),
        "reason: approve_without_ticket {}",
        r.body
    );
    assert!(still_waiting(), "reason: written_without_ticket");

    // a made-up ticket is not the owner's: refused before anything is written
    let r = act(&d, &page, &approve(Some("tk_made_up_1"))).await;
    assert_eq!(
        (r.code, r.json["reason"].as_str()),
        (403, Some("owner_check_not_approved")),
        "reason: made_up_ticket_taken {}",
        r.body
    );
    assert!(still_waiting(), "reason: written_with_made_up_ticket");

    // an id that is not there reaches persona_propose and says so (the page's arm is there)
    let r = act(
        &d,
        &page,
        &json!({"type": "persona_propose_approve", "request_id": "pp_none", "diff_sha": "x"}),
    )
    .await;
    assert_eq!(
        r.json["code"], "request_not_found",
        "reason: approve_arm_gone {}",
        r.body
    );

    // a seat's token never approves
    let r = act(&d, &tok, &approve(Some("tk_made_up_2"))).await;
    assert!(
        r.code >= 400 && r.json["state"].is_null(),
        "reason: a_seat_reached_approve {}",
        r.body
    );
    assert!(still_waiting(), "reason: written_by_a_seat");

    // while a user folder page serves `/`, the same credential is refused
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let user_page = web.join("index.html");
    std::fs::write(&user_page, "<p>mine</p>").unwrap();
    let r = act(&d, &page, &approve(Some("tk_made_up_3"))).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("persona_built_in_page_only")),
        "reason: user_page_reached_approve {}",
        r.body
    );
    std::fs::remove_file(&user_page).unwrap();
    assert!(still_waiting(), "reason: written_from_a_user_page");
    d.shutdown().await;
}
