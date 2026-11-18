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
