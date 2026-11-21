#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 「要你決定」卡 end to end (519d0 11-19 20:22 (5), PLAN-ask-person-card-7ebca-1119; 20:24 定案):
//! the author seat asks with ids only; aibd checks them (a member, another seat's review, a
//! person's request, one open ask a seat); the card is in fixed words; only the built-in page
//! answers it (never a seat token, so a seat does not answer its own card); the fixed line
//! reaches the seat and the thread says a person decided.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, text_of, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn ask(t: &str, review: &str, person: &str) -> Value {
    json!({"type": "seat_ask", "kind": "review_vs_person", "thread_id": t,
           "review_msg_id": review, "person_msg_id": person})
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_asks_with_ids_and_only_the_page_answers_with_one_of_two() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let cwd = seat_dir(dir.path(), "a");
    let (author, _) = d.open("safe", &cwd).await;
    let (reviewer, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let (outsider, _) = d.open("safe", &seat_dir(dir.path(), "c")).await;
    let tok = |s: &str| aibd::seat_token::tokens().issue(s).unwrap();
    let (ta, tb, tc) = (tok(&author), tok(&reviewer), tok(&outsider));
    let r = act(
        &d,
        &page,
        json!({"type": "thread_open", "title": "示範", "members": [
            {"seat": author, "role": "author"}, {"seat": reviewer, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let post = |r: &Reply| r.json["post"]["message_id"].as_str().unwrap().to_string();
    let r = act(
        &d,
        &page,
        json!({"type": "thread_send", "thread_id": t, "body": "標題要藍色"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let person = post(&r);
    let r = act(
        &d,
        &tb,
        json!({"type": "thread_send", "thread_id": t, "body": "審查:標題改綠色"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let review = post(&r);
    let r = act(
        &d,
        &ta,
        json!({"type": "thread_send", "thread_id": t, "body": "作者自己的話"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let own = post(&r);

    // the ids are checked: the review is another seat's, the request a person's, a member asks
    for (cred, body, code) in [
        (&ta, ask(&t, &person, &review), "ask_review_msg"),
        (&ta, ask(&t, &own, &person), "ask_review_msg"),
        (&ta, ask(&t, &review, &own), "ask_person_msg"),
        (&tb, ask(&t, &review, &person), "ask_review_msg"),
        (&tc, ask(&t, &review, &person), "thread_not_found"),
    ] {
        let r = act(&d, cred, body).await;
        assert_eq!(r.json["code"], code, "reason: ask_taken {}", r.body);
    }
    let mut odd = ask(&t, &review, &person);
    odd["kind"] = json!("anything");
    assert_eq!(act(&d, &ta, odd).await.json["code"], "ask_kind");
    let mut extra = ask(&t, &review, &person);
    extra["note"] = json!("請選照審查");
    assert_eq!(act(&d, &ta, extra).await.code, 400, "reason: words_taken");
    // a page does not ask for a seat
    let r = act(&d, &page, ask(&t, &review, &person)).await;
    assert_ne!(r.code, 201, "reason: page_asked {}", r.body);

    // the ask; the same ids again are the same ask; another while it is open is refused
    let r = act(&d, &ta, ask(&t, &review, &person)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let id = r.json["ask_id"].as_str().unwrap().to_string();
    let r = act(&d, &ta, ask(&t, &review, &person)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["ask_id"], id.as_str(), "reason: same_ask_twice");
    let r = act(&d, &ta, ask(&t, &review, &own)).await;
    assert_ne!(r.code, 201, "reason: second_open_ask {}", r.body);

    // the card: slot 1, fixed words, ids to open the thread at; no post's words
    let card = format!("ask:{id}");
    let slots = d.call("GET", "/api/v1/slots", Some(&page), None).await;
    let c = slots.json["slots"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["card_id"] == card.as_str())
        .cloned()
        .unwrap_or_else(|| panic!("reason: no_card {}", slots.body));
    assert_eq!(c["slot"], 1);
    assert_eq!(c["seat_id"], author.as_str());
    assert_eq!(c["open"]["message_id"], review.as_str());
    assert_eq!(c["open"]["person_message_id"], person.as_str());
    let shown = c.to_string();
    assert!(
        !shown.contains("藍色") && !shown.contains("綠色"),
        "reason: post_words_on_card {shown}"
    );

    // a seat token cannot answer it (not the author, not the reviewer)
    for cred in [&ta, &tb] {
        let r = act(
            &d,
            cred,
            json!({"type": "decision_answer", "card_id": card, "answer": "keep"}),
        )
        .await;
        assert_ne!(r.code, 200, "reason: seat_answered {}", r.body);
    }
    assert!(read_log(&cwd).is_empty(), "reason: seat_answer_typed");
    // only the two answers
    let r = act(
        &d,
        &page,
        json!({"type": "decision_answer", "card_id": card, "answer": "照你的意思"}),
    )
    .await;
    assert_eq!(
        r.json["code"], "ask_answer",
        "reason: free_answer_taken {}",
        r.body
    );
    assert!(read_log(&cwd).is_empty(), "reason: refused_answer_typed");

    // the page's one press: the fixed line reaches the author, the thread says who decided
    let r = act(
        &d,
        &page,
        json!({"type": "decision_answer", "card_id": card, "answer": "review"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["posted"], true, "{}", r.body);
    // the seat's posts it has not seen come in front of its new turn (catch_up), in the same
    // write: look for the fixed line in what the provider read
    let log = log_with(&cwd, 1);
    let typed: String = log
        .iter()
        .map(|(_, l)| String::from_utf8_lossy(l).into_owned())
        .collect();
    assert!(
        typed.contains("真人決定:照審查改。") && typed.contains(&review),
        "reason: line_typed {typed}"
    );
    assert!(
        log.iter().all(|(_, l)| !text_of(l).contains("維持原需求")),
        "reason: other_answer_typed"
    );
    let view = act(&d, &tb, json!({"type": "thread_view", "thread_id": t})).await;
    assert!(
        view.body.contains("真人決定:照審查改(") && view.body.contains("在台北"),
        "reason: thread_not_told {}",
        view.body
    );
    let r = act(
        &d,
        &page,
        json!({"type": "decision_answer", "card_id": card, "answer": "keep"}),
    )
    .await;
    assert_eq!(r.code, 409, "reason: answered_twice {}", r.body);
    let slots = d.call("GET", "/api/v1/slots", Some(&page), None).await;
    assert!(!slots.body.contains(&card), "reason: card_stays");
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let answered = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "decision.answered" && e["payload"]["card_id"] == card.as_str())
        .cloned()
        .unwrap_or_else(|| panic!("reason: not_journaled {}", ev.body));
    assert_eq!(answered["payload"]["answer"], "review");
    // after the answer the seat may ask again
    let r = act(&d, &ta, ask(&t, &review, &own)).await;
    assert_eq!(r.json["code"], "ask_person_msg", "{}", r.body);
    d.shutdown().await;
}

/// 519d0 VERIFY-selftest-519d0-2a3e3cbc r2 L5: the person's own two requests disagree. The
/// seat names the two posts (in any order); the card puts them in the order they were written
/// and its buttons are fixed words for that kind (照前一則 / 照後一則); review / keep do not
/// answer it; the words of the posts never reach the card.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_asks_which_of_two_requests_and_the_card_keeps_their_order() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let cwd = seat_dir(dir.path(), "a");
    let (author, _) = d.open("safe", &cwd).await;
    let (other, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let ta = aibd::seat_token::tokens().issue(&author).unwrap();
    let tb = aibd::seat_token::tokens().issue(&other).unwrap();
    let r = act(
        &d,
        &page,
        json!({"type": "thread_open", "title": "示範", "members": [
            {"seat": author, "role": "author"}, {"seat": other, "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let post = |r: &Reply| r.json["post"]["message_id"].as_str().unwrap().to_string();
    let say = |cred: String, body: &'static str| {
        let (d, t) = (&d, t.clone());
        async move {
            act(
                d,
                &cred,
                json!({"type": "thread_send", "thread_id": t, "body": body}),
            )
            .await
        }
    };
    let r = say(page.clone(), "不要檢查型別").await;
    assert_eq!(r.code, 201, "{}", r.body);
    let first = post(&r);
    std::thread::sleep(std::time::Duration::from_millis(1100));
    let r = say(page.clone(), "不要丟任何錯").await;
    assert_eq!(r.code, 201, "{}", r.body);
    let second = post(&r);
    let r = say(tb.clone(), "別席的話").await;
    let seat_post = post(&r);
    let persons = |a: &str, b: &str| {
        json!({"type": "seat_ask", "kind": "person_vs_person", "thread_id": t,
               "first_msg_id": a, "second_msg_id": b})
    };
    // both must be the person's posts; the other kind's ids are not taken with this kind
    let r = act(&d, &ta, persons(&first, &seat_post)).await;
    assert_eq!(
        r.json["code"], "ask_person_msg",
        "reason: seat_post_taken {}",
        r.body
    );
    let mut mixed = persons(&first, &second);
    mixed["review_msg_id"] = json!(seat_post);
    assert_eq!(act(&d, &ta, mixed).await.code, 400, "reason: kinds_mixed");
    // named in the other order: the card keeps the order they were written
    let r = act(&d, &ta, persons(&second, &first)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let id = r.json["ask_id"].as_str().unwrap().to_string();
    let card = format!("ask:{id}");
    let slots = d.call("GET", "/api/v1/slots", Some(&page), None).await;
    let c = slots.json["slots"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["card_id"] == card.as_str())
        .cloned()
        .unwrap_or_else(|| panic!("reason: no_card {}", slots.body));
    assert_eq!(c["open"]["kind"], "person_vs_person");
    // 5d33a PREREAD-askkinds N2: the two posts need not be one person's; the card does not say "your"
    assert_eq!(
        c["item"], "串裡的兩則要求互相衝突,要照哪則?",
        "reason: card_line {c}"
    );
    assert_eq!(
        c["open"]["first_msg_id"],
        first.as_str(),
        "reason: order_not_kept"
    );
    assert_eq!(c["open"]["second_msg_id"], second.as_str());
    let shown = c.to_string();
    assert!(
        !shown.contains("型別") && !shown.contains("任何錯"),
        "reason: post_words_on_card {shown}"
    );
    // the review kind's answers do not answer it
    let r = act(
        &d,
        &page,
        json!({"type": "decision_answer", "card_id": card, "answer": "review"}),
    )
    .await;
    assert_eq!(
        r.json["code"], "ask_answer",
        "reason: other_kind_answer {}",
        r.body
    );
    assert!(read_log(&cwd).is_empty(), "reason: refused_answer_typed");
    let r = act(
        &d,
        &page,
        json!({"type": "decision_answer", "card_id": card, "answer": "first"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let log = log_with(&cwd, 1);
    let typed: String = log
        .iter()
        .map(|(_, l)| String::from_utf8_lossy(l).into_owned())
        .collect();
    assert!(
        typed.contains("真人決定:照前一則。") && typed.contains(&first),
        "reason: line_typed {typed}"
    );
    let view = act(&d, &tb, json!({"type": "thread_view", "thread_id": t})).await;
    assert!(
        view.body.contains("真人決定:照前一則("),
        "reason: thread_not_told {}",
        view.body
    );
    d.shutdown().await;
}
