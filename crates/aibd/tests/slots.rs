#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace event slots end to end (SPEC-workspace-multiseat-519d0-1118 §2, §3): a seat
//! waiting for a person is the top card; a page answers it through act `decision_answer` (the
//! answer reaches the seat's provider as one line, the card leaves, the journal keeps the
//! length and hash only); a card is put aside with `slot_dismiss`; a seat token reads no slots.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, text_of, Daemon};
use serde_json::{json, Value};

fn cards(v: &Value) -> Vec<Value> {
    v["slots"].as_array().cloned().unwrap_or_default()
}

fn card_of<'a>(all: &'a [Value], seat: &str, slot: u64) -> Option<&'a Value> {
    all.iter()
        .find(|c| c["seat_id"] == seat && c["slot"].as_u64() == Some(slot))
}

#[tokio::test(flavor = "multi_thread")]
async fn a_page_answers_the_seat_waiting_for_it_and_the_card_leaves() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, session) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();

    // the seat says it waits for a person
    let line = json!({"type":"seat_now","item":"要不要先合 #860","total":2,"done":1,
        "state":"decide","why":"兩個做法"})
    .to_string();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&line)).await;
    assert_eq!(r.code, 200, "{}", r.body);

    // a seat token reads no slots; a page does, and the decision is on top
    let r = d.call("GET", "/api/v1/slots", Some(&tok), None).await;
    assert_eq!(r.code, 403, "reason: seat_reads_slots {}", r.body);
    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["names"][0], "decide");
    let all = cards(&r.json);
    assert_eq!(all[0]["slot"], 1, "reason: decision_not_on_top {}", r.body);
    let top = card_of(&all, &seat, 1).unwrap();
    assert_eq!(top["item"], "要不要先合 #860");
    let card = top["card_id"].as_str().unwrap().to_string();
    assert!(card.starts_with(&format!("now:{seat}:")), "{card}");
    assert!(
        card_of(&all, &seat, 6).is_some_and(|c| c["kind"] == "seat.opened"),
        "reason: opened_notice_missing {}",
        r.body
    );

    // another card id (the seat wrote again since) is refused, nothing typed
    let stale = json!({"type":"decision_answer","seat_id":seat,"answer":"先合",
        "card_id":"now:x:y"})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&stale))
        .await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "decision_changed");
    let extra =
        json!({"type":"decision_answer","seat_id":seat,"answer":"先合","approve":true}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&extra))
        .await;
    assert_eq!(r.code, 400, "reason: unknown_field_taken {}", r.body);
    assert!(read_log(&cwd).is_empty(), "reason: refused_answer_typed");

    // the answer reaches the seat as one line
    let answer = json!({"type":"decision_answer","seat_id":seat,"answer":"先合 #860 的答案",
        "card_id":card})
    .to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&answer))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["session_id"], session.as_str());
    assert_eq!(r.json["card_id"], card.as_str());
    let log = log_with(&cwd, 1);
    assert_eq!(log.len(), 1);
    assert_eq!(text_of(&log[0].1), "先合 #860 的答案");

    // a second answer to the same card is refused; the card has left
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&answer))
        .await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "already_answered");
    assert_eq!(read_log(&cwd).len(), 1, "reason: answered_twice");
    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert!(
        card_of(&cards(&r.json), &seat, 1).is_none(),
        "reason: answered_card_kept {}",
        r.body
    );

    // the journal has the length and hash, never the answer (the provider's own output, which
    // echoes it, is another aggregate and not looked at here)
    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let answered: Vec<&Value> = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "decision.answered")
        .collect();
    assert_eq!(answered.len(), 1, "reason: not_journaled {}", r.body);
    let p = answered[0]["payload"].to_string();
    assert!(!p.contains("的答案"), "reason: answer_in_journal {p}");
    assert_eq!(answered[0]["payload"]["len"], "先合 #860 的答案".len());

    // a new line is a new card; put aside with slot_dismiss it leaves
    let again = json!({"type":"seat_now","item":"第二題","state":"decide"}).to_string();
    let r = d
        .call("POST", "/api/v1/act", Some(&tok), Some(&again))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    let all = cards(&r.json);
    let second = card_of(&all, &seat, 1).unwrap()["card_id"]
        .as_str()
        .unwrap()
        .to_string();
    assert_ne!(second, card, "reason: new_line_same_card");
    let bad = json!({"type":"slot_dismiss","card_id":"a\nb"}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&bad)).await;
    assert_eq!(r.code, 400, "{}", r.body);
    let put = json!({"type":"slot_dismiss","card_id":second}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&put)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["already"], false);
    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert!(
        card_of(&cards(&r.json), &seat, 1).is_none(),
        "reason: dismissed_card_kept {}",
        r.body
    );
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&json!({"type":"decision_answer","seat_id":seat,"answer":"x"}).to_string()),
        )
        .await;
    assert_eq!(r.code, 409, "reason: dismissed_card_answered {}", r.body);

    // a seat not waiting for a person has nothing to answer
    let busy = json!({"type":"seat_now","item":"做事中"}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&busy)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&json!({"type":"decision_answer","seat_id":seat,"answer":"x"}).to_string()),
        )
        .await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "no_decision");
    assert_eq!(read_log(&cwd).len(), 1, "reason: typed_without_a_decision");
    d.shutdown().await;
}
