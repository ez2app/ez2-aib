#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-workspace third wave, 7b81e item 1: once the tree is in aibd (`AIBD_TREE_IN_DB=1`, its own
//! process), the built-in page adds an item and moves it through aib-plan's entry conditions;
//! a second writer on an old rev is refused; a user folder page and a seat token never write.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;

const TREE: &str = r#"{"nodes": [
  {"id": "V", "parent": null, "kind": "vision", "title": "總綱"},
  {"id": "N-ws", "parent": "V", "kind": "item", "title": "工作台", "status": "開發中"}
]}"#;

#[tokio::test(flavor = "multi_thread")]
async fn the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions() {
    std::env::set_var("AIBD_TREE_IN_DB", "1");
    let dir = tempfile::tempdir().unwrap();
    // aib-plan's facts read this folder, never the machine's own pending
    std::env::set_var("AIBD_PLAN_PENDING", dir.path().join("pending"));
    let data = dir.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    std::fs::write(data.join("tree.json"), TREE).unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let act = |v: serde_json::Value| v.to_string();

    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&act(
                json!({"type": "node_add", "parent_id": "N-ws", "title": "樹的寫入",
                             "owner": "7b81e", "why": "全部都上", "need": "全部都上"}),
            )),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["node_id"], "N-ws-c1");
    assert_eq!(r.json["station"], "未開始");
    assert_eq!(r.json["journaled"], true);

    // no branch in the fact: aib-plan's rule for 開發中 refuses it
    let mv = |rev: i64, fact: &str| {
        act(
            json!({"type": "node_set", "node_id": "N-ws-c1", "if_rev": rev,
                   "station": "開發中", "fact": fact}),
        )
    };
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&mv(1, "開始")))
        .await;
    assert_eq!(r.code, 409, "reason: entry_skipped {}", r.body);
    assert_eq!(r.json["code"], "entry_refused");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&mv(1, "分支 feat/ws-nodew-7b81e-1118")),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (r.json["rev"].as_i64(), r.json["moved"].as_bool()),
        (Some(2), Some(true))
    );
    // a second writer that read rev 1 is told, not written over
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&mv(1, "分支 feat/other-7b81e-1118")),
        )
        .await;
    assert_eq!(r.code, 409, "reason: lost_update {}", r.body);
    assert_eq!(r.json["code"], "node_rev_changed");

    let r = d.call("GET", "/api/v1/tree?root=N-ws", None, None).await;
    let item = r.json["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["id"] == "N-ws-c1")
        .cloned()
        .unwrap_or_else(|| panic!("reason: item_not_in_tree {}", r.body));
    assert_eq!(item["station"], "開發中");
    assert_eq!(item["owner"], "7b81e");
    assert_eq!(item["rev"], 2, "reason: rev_not_shown {}", r.body);

    // one item, with the rev the next node_set names
    let r = d
        .call("GET", "/api/v1/nodes/N-ws-c1", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (r.json["source"].as_str(), r.json["node"]["rev"].as_i64()),
        (Some("aibd"), Some(2))
    );
    assert_eq!(r.json["node"]["need"], "全部都上");
    // the card window's fields (SPEC §15): why, records, the items right under
    assert_eq!(
        r.json["node"]["why"], "全部都上",
        "reason: card_fields_missing {}",
        r.body
    );
    assert!(r.json["node"]["records"].is_array(), "{}", r.body);
    let r = d.call("GET", "/api/v1/nodes/N-ws", Some(&page), None).await;
    assert_eq!(
        r.json["node"]["children"][0]["id"], "N-ws-c1",
        "reason: children_missing {}",
        r.body
    );
    // a user folder page gets the length and hash of the person's words, not the words
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call("GET", "/api/v1/nodes/N-ws-c1", Some(&user), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        r.json["node"]["need"]["sha256"].is_string(),
        "reason: words_to_user_page {}",
        r.body
    );
    let r = d
        .call("GET", "/api/v1/nodes/nobody", Some(&page), None)
        .await;
    assert_eq!(r.code, 404, "{}", r.body);

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let set: Vec<&serde_json::Value> = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "node.set")
        .collect();
    assert_eq!(set.len(), 1, "reason: one_event_per_write {}", r.body);
    assert_eq!(set[0]["payload"]["to"], "開發中");
    assert!(
        set[0]["payload"].get("fact").is_none(),
        "reason: words_in_event"
    );

    // a user folder page and a seat token do not write the tree
    let add = act(json!({"type": "node_add", "parent_id": "V", "title": "x"}));
    let r = d.call("POST", "/api/v1/act", Some(&user), Some(&add)).await;
    assert_eq!(r.code, 403, "reason: user_page_writes {}", r.body);
    assert_eq!(r.json["code"], "node_built_in_page_only");
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&add)).await;
    assert_ne!(r.code, 200, "reason: seat_token_writes {}", r.body);

    // a seat on the item above sees the new item under it
    let up =
        act(json!({"type": "assign_add", "node_id": "N-ws", "seat_id": seat, "role": "reviewer"}));
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&up)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d
        .call(
            "GET",
            &format!("/api/v1/assignments?seat={seat}&below=1"),
            Some(&page),
            None,
        )
        .await;
    assert_eq!(
        r.json["sees"],
        json!(["N-ws", "N-ws-c1"]),
        "reason: below_not_seen {}",
        r.body
    );

    // 1a93b GAP §1/§2: one click sent twice is one item; the key with another body is refused
    let once = act(
        json!({"type": "node_add", "parent_id": "V", "title": "重送一次", "idempotency_key": "pg-once-1"}),
    );
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&once))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let first = r.json["node_id"].clone();
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&once))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["node_id"], first,
        "reason: resend_wrote_again {}",
        r.body
    );
    assert_eq!(
        r.json["replayed"], true,
        "reason: replay_not_said {}",
        r.body
    );
    let other = act(
        json!({"type": "node_add", "parent_id": "V", "title": "另一件", "idempotency_key": "pg-once-1"}),
    );
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&other))
        .await;
    assert_eq!(
        r.code, 409,
        "reason: key_reused_for_another_write {}",
        r.body
    );
    assert_eq!(r.json["code"], "idempotency_conflict");
    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let added = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "node.added" && e["payload"]["node_id"] == first)
        .count();
    assert_eq!(added, 1, "reason: resend_wrote_again {}", r.body);
    // a refused write gives its key back: the retry is judged again, not told "still running"
    let lost = act(
        json!({"type": "node_add", "parent_id": "N-nope", "title": "x", "idempotency_key": "pg-once-2"}),
    );
    for _ in 0..2 {
        let r = d
            .call("POST", "/api/v1/act", Some(&page), Some(&lost))
            .await;
        assert_eq!(r.code, 404, "reason: refused_key_kept {}", r.body);
        assert_eq!(r.json["code"], "node_not_found");
    }

    // flag off: the tree is aib-plan's file again and the page does not write it
    std::env::remove_var("AIBD_TREE_IN_DB");
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&add)).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "tree_not_in_aibd");
    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(
        r.json["nodes"].as_array().unwrap().len(),
        2,
        "reason: add_reached_the_file"
    );
    d.shutdown().await;
}
