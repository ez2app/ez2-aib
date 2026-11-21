#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace threads of many end to end (SPEC-workspace-multiseat-519d0-1118 §1.3 / §7 /
//! §8): a real aibd, two seats on the fake provider, a confirmed built-in page. The page opens
//! a thread on a tree item with both seats, posts calling one of them; the called seat gets a
//! delivery row of its own, the other is only told; the list by item and the timeline show it.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn assign(d: &Daemon, cred: &str, kind: &str, node: &str, seat: &str, role: &str) -> Reply {
    act(
        d,
        cred,
        json!({"type": kind, "node_id": node, "seat_id": seat, "role": role}),
    )
    .await
}

/// TH1–TH3: open on an item, `@` one member, the rest is cc; the same idem_key gives the same
/// post; a seat that is not a member cannot be called; the item's list and the timeline (with
/// the text, on the built-in page) show the post and each call's own state.
#[tokio::test(flavor = "multi_thread")]
async fn a_page_opens_a_thread_on_an_item_and_calls_one_member_in() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, _) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let cred = d.page(true).await;
    // a tree item's thread members are its assignments (5d33a wave-2 N1)
    for (seat, role) in [(&a, "author"), (&b, "reviewer")] {
        let r = assign(&d, &cred, "assign_add", "N-workspace", seat, role).await;
        assert_eq!(r.code, 200, "{}", r.body);
    }

    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "node": "N-workspace", "title": "多人對話",
               "members": [{"seat": a, "role": "author"}, {"seat": format!("seat:{b}"), "role": "reviewer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    assert!(t.starts_with("t_") && t.len() == 18, "{t}");
    assert_eq!(r.json["thread"]["members"].as_array().unwrap().len(), 2);
    assert_eq!(r.json["existing"], false, "reason: new_not_said");

    // 043dd 02:42: the page's free 「共同對話」 by title gains the seat it calls later
    let shared = |m: &str| {
        json!({"type": "thread_open", "title": "共同對話", "reuse": "title",
               "members": [{"seat": m, "role": "observer"}]})
    };
    let first = act(&d, &cred, shared(&a)).await;
    assert_eq!(first.code, 201, "{}", first.body);
    assert_eq!(first.json["existing"], false);
    let next = act(&d, &cred, shared(&b)).await;
    assert_eq!(next.code, 200, "reason: free_not_reused {}", next.body);
    assert_eq!(next.json["existing"], true);
    assert_eq!(
        next.json["thread"]["thread_id"],
        first.json["thread"]["thread_id"]
    );
    assert_eq!(next.json["thread"]["members"].as_array().unwrap().len(), 2);

    // clicking the item again gives its one thread back
    let again = act(
        &d,
        &cred,
        json!({"type": "thread_open", "node": "N-workspace", "title": "再點一次"}),
    )
    .await;
    assert_eq!(again.code, 200, "{}", again.body);
    assert_eq!(again.json["thread"]["thread_id"], t.as_str());
    assert_eq!(again.json["existing"], true);
    assert_eq!(again.json["thread"]["members"].as_array().unwrap().len(), 2);

    let post = json!({"type": "thread_send", "thread_id": t, "body": "請 @a 先看",
                      "to": [a], "idem_key": "p1"});
    let r = act(&d, &cred, post.clone()).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let p = &r.json["post"];
    assert_eq!(p["to"], json!([format!("seat:{a}")]), "{}", r.body);
    assert_eq!(p["cc"], json!([format!("seat:{b}")]));
    assert_eq!(p["calls"].as_array().unwrap().len(), 1, "{}", r.body);
    assert_eq!(p["calls"][0]["seat"], format!("seat:{a}"));
    assert_eq!(p["sender_kind"], "human");
    let pid = p["message_id"].as_str().unwrap().to_string();

    let again = act(&d, &cred, post).await;
    assert_eq!(again.code, 200, "{}", again.body);
    assert_eq!(again.json["post"]["message_id"], pid.as_str());

    let stranger = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "x", "to": ["seat_nobody"]}),
    )
    .await;
    assert_eq!(stranger.code, 400, "{}", stranger.body);
    assert_eq!(stranger.json["code"], "to_not_a_member");
    // FINDING-andy-realrun 1 / 5d33a PREREAD f36f26a1 N4: the refusal says the next step
    assert!(
        stranger.json["message"]
            .as_str()
            .unwrap_or("")
            .contains("ask the person in this thread to @ it in"),
        "reason: no_next_step {}",
        stranger.body
    );

    let l = d
        .call("GET", "/api/v1/threads?node=N-workspace", Some(&cred), None)
        .await;
    assert_eq!(l.code, 200, "{}", l.body);
    assert_eq!(l.json["threads"].as_array().unwrap().len(), 1, "{}", l.body);
    assert_eq!(l.json["threads"][0]["thread_id"], t.as_str());
    let none = d
        .call("GET", "/api/v1/threads?node=other", Some(&cred), None)
        .await;
    assert_eq!(none.json["threads"], json!([]), "{}", none.body);

    let v = d
        .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
        .await;
    assert_eq!(v.code, 200, "{}", v.body);
    let posts = v.json["posts"].as_array().unwrap();
    assert_eq!(posts.len(), 1, "{}", v.body);
    assert_eq!(posts[0]["body"], "請 @a 先看");
    assert_eq!(posts[0]["calls"][0]["seat"], format!("seat:{a}"));
    assert_eq!(v.json["node"], "N-workspace");

    // TH8: the person marks the thread read; the post shows who read it
    let me = posts[0]["from_seat"].as_str().unwrap().to_string();
    assert_eq!(posts[0]["read_by"], json!([]), "{}", v.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_read", "thread_id": t, "message_id": pid}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["read_upto"], pid.as_str());
    let v = d
        .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
        .await;
    assert_eq!(v.json["posts"][0]["read_by"], json!([me]), "{}", v.body);
    assert_eq!(v.json["read_upto"], pid.as_str());

    let missing = d
        .call(
            "GET",
            "/api/v1/threads/t_0000000000000000",
            Some(&cred),
            None,
        )
        .await;
    assert_eq!(missing.code, 404, "{}", missing.body);
    d.shutdown().await;
}

/// 0eed7 VERIFY-turnfail-0eed7-8680f0dbc L1: a called seat that is not running gets the post
/// when it starts, and the thread says so at once (from aibd, calling nobody).
#[tokio::test(flavor = "multi_thread")]
async fn calling_a_seat_that_is_not_running_says_so_in_the_thread() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let (b, sb) = d.open("safe", &seat_dir(dir.path(), "b")).await;
    let cred = d.page(true).await;
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_open", "title": "叫已關的席",
               "members": [{"seat": a, "role": "observer"}, {"seat": b, "role": "observer"}]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"].as_str().unwrap().to_string();
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sb}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let end = std::time::Instant::now() + std::time::Duration::from_secs(15);
    loop {
        let s = d
            .call("GET", &format!("/api/v1/sessions/{sb}"), None, None)
            .await;
        if s.json["state"] != "running" {
            break;
        }
        assert!(
            std::time::Instant::now() < end,
            "reason: b_never_stopped {}",
            s.body
        );
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "@b 看一下", "to": [b]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let v = d
        .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
        .await;
    let posts = v.json["posts"].as_array().unwrap();
    let note = posts
        .iter()
        .find(|p| p["auto"] == "not_running")
        .unwrap_or_else(|| panic!("reason: no_not_running_note {}", v.body));
    assert_eq!(note["not_running"], json!([b]), "{}", v.body);
    assert_eq!(
        note["body"].as_str().unwrap_or(""),
        format!("{} 沒在跑,開了才會收到", aibd::seats::seat_code(&b)),
        "reason: note_text {}",
        v.body
    );
    // the same post sent again (same idem_key) gives no second note
    let again = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "@b 看一下", "to": [b],
               "idem_key": "nr1"}),
    )
    .await;
    assert_eq!(again.code, 201, "{}", again.body);
    let resend = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "@b 看一下", "to": [b],
               "idem_key": "nr1"}),
    )
    .await;
    assert_eq!(resend.code, 200, "{}", resend.body);
    // a running seat called later gets no such note
    let r = act(
        &d,
        &cred,
        json!({"type": "thread_send", "thread_id": t, "body": "@a 看", "to": [a]}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let v = d
        .call("GET", &format!("/api/v1/threads/{t}"), Some(&cred), None)
        .await;
    let notes = v.json["posts"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|p| p["auto"] == "not_running")
        .count();
    assert_eq!(
        notes, 2,
        "reason: note_for_a_running_seat_or_resend {}",
        v.body
    );
    d.shutdown().await;
}
