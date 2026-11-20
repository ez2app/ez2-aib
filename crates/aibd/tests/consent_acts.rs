#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! 要你決定卡 + 真人未審清單卡 (519d0 11-19 09:11; DECISION-council-ideas-2-519d0-1119 §2 §3):
//! an idea that needs a person is a 1 號 card with 同意 / 不同意, the after-review decisions one
//! list card with 看過; every press asks the owner's fingerprint, and while that cannot be asked
//! a press is only recorded unproved: nothing is answered or seen, also after a restart. A user
//! folder page sees no words of either.
//!
//! Its own binary: it sets the state root for the process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};

const IDEA: &str = "11111111-2222-4333-8444-555555555555";
const ADD: &str = r#"{"at_ms":1794645000000,"axis":"stable","benefit":"省時間","classification":"needs_human","cost":"回復:revert","ev":"add","event_id":"0d1e2f30-4152-4637-8899-aabbccddeeff","grade":"A","idea_id":"11111111-2222-4333-8444-555555555555","node":"S76","now":"現況","policy_version":1,"recorded_by_seat":"043dd","source":null,"suggest":"做法","title":"要人同意的一條","touches":["prod","data"],"v":1}"#;
const LEDGER: &str = concat!(
    r#"{"id":"aaaaaaaaaaaa","line":"1","at":"11-19 08:25","kind":"decision","by":"seat:519d0","text":"11-19 08:25 519d0 裁:預覽不退|S1","src":"andy-519d0.txt"}"#,
    "\n",
    r#"{"id":"bbbbbbbbbbbb","line":"2","at":null,"kind":"pending_review","by":"seat:519d0","text":"真人未審(後審):合了 #818","src":"andy-519d0.txt"}"#,
    "\n",
);

fn card<'a>(v: &'a Value, id: &str) -> Option<&'a Value> {
    v["slots"].as_array()?.iter().find(|c| c["card_id"] == id)
}

fn list_ids(v: &Value) -> Vec<String> {
    card(v, "review:list")
        .and_then(|c| c["open"]["list"].as_array())
        .map(|l| {
            l.iter()
                .filter_map(|r| r["id"].as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("state-root");
    std::env::set_var("EZ2_AIB_STATE_ROOT", &root);
    let ideas = root.join("state").join("ideas");
    std::fs::create_dir_all(&ideas).unwrap();
    std::fs::write(ideas.join("ideas.jsonl"), format!("{ADD}\n")).unwrap();
    let drive = root.join("state").join("drive");
    std::fs::create_dir_all(&drive).unwrap();
    std::fs::write(drive.join("andy-519d0.txt"), "old\n").unwrap();
    std::fs::write(drive.join("andy-review-519d0.jsonl"), LEDGER).unwrap();

    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    let idea_card = format!("idea:{IDEA}");

    // the built-in page sees both cards with their words
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let c = card(&r.json, &idea_card).unwrap_or_else(|| panic!("reason: no_idea_card {}", r.body));
    assert_eq!(c["slot"], 1);
    assert_eq!(c["actions"], json!(["agree", "disagree"]));
    assert_eq!(c["item"], "要人同意的一條");
    assert_eq!(
        list_ids(&r.json),
        ["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
        "reason: list_not_the_ledgers {}",
        r.body
    );

    // a user folder page: the idea's words hashed, the list only counted
    let r = d.call("GET", "/api/v1/slots", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let c = card(&r.json, &idea_card).unwrap_or_else(|| panic!("reason: no_idea_card {}", r.body));
    assert_ne!(
        c["item"], "要人同意的一條",
        "reason: idea_words_to_user_page"
    );
    let l = card(&r.json, "review:list").unwrap_or_else(|| panic!("reason: no_list {}", r.body));
    assert_eq!(
        l["open"],
        json!({"count": 2}),
        "reason: list_words_to_user_page"
    );

    // a user page cannot press
    let answer =
        |a: &str| json!({"type": "idea_answer", "card_id": idea_card, "answer": a}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&user), Some(&answer("agree")))
        .await;
    assert_eq!(r.code, 403, "reason: user_page_answers {}", r.body);

    // the built-in page's press is only recorded unproved, both ways
    for a in ["agree", "disagree"] {
        let r = d
            .call("POST", "/api/v1/act", Some(&built_in), Some(&answer(a)))
            .await;
        assert_eq!(r.code, 200, "{}", r.body);
        assert_eq!(
            r.json["verified"], false,
            "reason: press_verified {}",
            r.body
        );
        assert_eq!(r.json["code"], "owner_check_unavailable", "{}", r.body);
        assert_eq!(r.json["journaled"], true, "{}", r.body);
    }
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(
                &json!({"type": "idea_answer", "card_id": "idea:99999999-2222-4333-8444-555555555555", "answer": "agree"})
                    .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 404, "reason: unknown_idea_answered {}", r.body);
    assert_eq!(r.json["code"], "idea_not_waiting");

    // 看過: one press for several, no repeats, only lines on the list
    let seen = |ids: Value| json!({"type": "review_seen", "line_ids": ids}).to_string();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&seen(json!(["aaaaaaaaaaaa", "aaaaaaaaaaaa"]))),
        )
        .await;
    assert_eq!(r.code, 400, "reason: repeat_taken {}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&seen(json!(["aaaaaaaaaaaa", "cccccccccccc"]))),
        )
        .await;
    assert_eq!(r.code, 404, "reason: off_list_taken {}", r.body);
    assert_eq!(r.json["code"], "line_not_waiting");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&seen(json!(["aaaaaaaaaaaa", "bbbbbbbbbbbb"]))),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["verified"], false,
        "reason: seen_verified {}",
        r.body
    );
    assert_eq!(r.json["count"], 2);
    assert_eq!(
        r.json["hash"].as_str().map(str::len),
        Some(12),
        "{}",
        r.body
    );

    // nothing answered, nothing seen: still so after a restart
    for restart in [false, true] {
        if restart {
            d.shutdown().await;
            d = Daemon::start(dir.path()).await;
        }
        let built_in = d.page(true).await;
        let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
        assert!(
            card(&r.json, &idea_card).is_some(),
            "reason: unproved_press_answered restart={restart} {}",
            r.body
        );
        assert_eq!(
            list_ids(&r.json).len(),
            2,
            "reason: unproved_press_seen restart={restart} {}",
            r.body
        );
    }
    d.shutdown().await;
}
