#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! 5d33a REVIEW-ws-slots ca4a2458 M1 / M2 / N1 (519d0 r16): the 待人決策 list is Andy's decision
//! ledger, part of the plan like the assignments and the tree.
//! - A user folder page reads a 待人決策 card's id, node, kind and level, and of its words only
//!   length and SHA-256; it neither answers nor takes off a card (slot_built_in_page_only).
//! - A pairing carried over an update answers and takes off no card
//!   (carried_pair_read_and_safe_only).
//! - The item stays open on the list after every refusal.
//!
//! Its own binary: it sets the state root for the process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn open_item(root: &Path) {
    let ctx = aib_ledger::events::Ctx::for_run(root, Some("519d0"));
    let args: Vec<String> = [
        "open",
        "--item",
        "A1",
        "--item-kind",
        "決定",
        "--text",
        "要不要先上用量條",
        "--rec",
        "先上",
        "--card",
        "N-workspace",
    ]
    .iter()
    .map(|a| (*a).to_owned())
    .collect();
    let (rc, out) = aib_drive::andy_open::run(&args, root, &ctx, aib_drive::act::now_ms());
    assert_eq!(rc, 0, "{out}");
}

fn still_open(root: &Path) -> bool {
    let (evs, _) = aib_drive::andy_open::read_events(root).unwrap();
    aib_drive::andy_open::replay(&evs)
        .get("A1")
        .is_some_and(|i| !i.done)
}

fn card(v: &Value) -> Value {
    v["slots"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["card_id"] == "andy:A1")
        .cloned()
        .unwrap_or_else(|| panic!("reason: no_andy_card {v}"))
}

fn sha(s: &str) -> String {
    Sha256::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn put_carry(dir: &Path, cred: &str) {
    let running = aibd::pair_carry::exe().unwrap().1;
    let list = [aibd::pairing::CarriedCred {
        digest: Sha256::digest(cred.as_bytes()).into(),
        user_page: false,
        left: 3600,
    }];
    let doc = aibd::pair_carry::document(
        &list,
        &"0".repeat(64),
        &running,
        aibd::pair_carry::now_unix() - 5,
    );
    let path = aibd::pair_carry::path(&dir.join("data"));
    aibd::platform::write_private_file(&path, doc.to_string().as_bytes()).unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn only_a_built_in_page_paired_now_answers_or_reads_a_decision_in_words() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("state-root");
    std::env::set_var("EZ2_AIB_STATE_ROOT", &root);
    open_item(&root);

    let mut d = Daemon::start(dir.path()).await;
    let built_in = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;

    // the built-in page reads the words
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(card(&r.json)["text"], "要不要先上用量條");

    // a user folder page: id, node, kind, level; the words as length and hash only
    let r = d.call("GET", "/api/v1/slots", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let c = card(&r.json);
    assert_eq!(c["card"], "N-workspace");
    assert_eq!(c["kind"], "andy.open");
    assert_eq!(c["level"], 1);
    assert_eq!(
        c["text"],
        json!({"len": 8, "sha256": sha("要不要先上用量條")}),
        "reason: words_to_user_page {c}"
    );
    assert_eq!(c["rec"], json!({"len": 2, "sha256": sha("先上")}));
    assert!(
        !r.body.contains("要不要先上用量條") && !r.body.contains("先上"),
        "reason: words_to_user_page {}",
        r.body
    );

    // a user folder page neither answers nor takes off a card
    for b in [
        json!({"type": "decision_answer", "card_id": "andy:A1", "answer": "x"}),
        json!({"type": "slot_dismiss", "card_id": "andy:A1"}),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&user), Some(&b.to_string()))
            .await;
        assert_eq!(r.code, 403, "reason: user_page_answers {}", r.body);
        assert_eq!(r.json["code"], "slot_built_in_page_only");
    }
    assert!(still_open(&root), "reason: user_page_closed_item");

    // SPEC §21: a class's page and the counts reach the page through the route; a bad query is
    // refused
    let r = d
        .call(
            "GET",
            "/api/v1/slots?slot=1&page=1&per=10",
            Some(&built_in),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["counts"]["1"], 1,
        "reason: counts_missing {}",
        r.body
    );
    assert_eq!(
        (r.json["page"].as_u64(), r.json["pages"].as_u64()),
        (Some(1), Some(1))
    );
    assert_eq!(card(&r.json)["card_id"], "andy:A1");
    let r = d
        .call("GET", "/api/v1/slots?slot=9", Some(&built_in), None)
        .await;
    assert_eq!(r.code, 400, "reason: bad_slot_taken {}", r.body);
    assert_eq!(r.json["code"], "bad_slots_query");

    // 「先收起」還原 (043dd 11-18 22:34): a dismissed card lists under ?dismissed=1 and comes back
    // by slot_restore from the built-in page only, once
    let act = |t: &str| json!({"type": t, "card_id": "andy:A1"}).to_string();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&act("slot_dismiss")),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d
        .call("GET", "/api/v1/slots?dismissed=1", Some(&built_in), None)
        .await;
    assert_eq!(
        r.json["dismissed"], true,
        "reason: not_the_dismissed_list {}",
        r.body
    );
    assert_eq!(card(&r.json)["card_id"], "andy:A1");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(&act("slot_restore")),
        )
        .await;
    assert_eq!(r.code, 403, "reason: user_page_restores {}", r.body);
    assert_eq!(r.json["code"], "slot_built_in_page_only");
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&act("slot_restore")),
        )
        .await;
    assert_eq!(r.code, 200, "reason: not_restored {}", r.body);
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&built_in),
            Some(&act("slot_restore")),
        )
        .await;
    assert_eq!(r.code, 404, "reason: restored_twice {}", r.body);
    assert_eq!(r.json["code"], "slot_not_dismissed");
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    assert_eq!(
        card(&r.json)["card_id"],
        "andy:A1",
        "reason: restored_not_listed"
    );
    d.shutdown().await;

    // a pairing carried over an update: no answer, no dismissal
    put_carry(dir.path(), &built_in);
    let mut d = Daemon::start(dir.path()).await;
    let st = d
        .call("GET", "/api/v1/pair/status", Some(&built_in), None)
        .await;
    assert_eq!(st.json["carried"], true, "{}", st.body);
    for b in [
        json!({"type": "decision_answer", "card_id": "andy:A1", "answer": "x"}),
        json!({"type": "slot_dismiss", "card_id": "andy:A1"}),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&built_in), Some(&b.to_string()))
            .await;
        assert_eq!(r.code, 403, "reason: carried_pair_answers {}", r.body);
        assert_eq!(r.json["code"], "carried_pair_read_and_safe_only");
    }
    assert!(still_open(&root), "reason: carried_pair_closed_item");
    let r = d.call("GET", "/api/v1/slots", Some(&built_in), None).await;
    assert_eq!(
        card(&r.json)["card_id"],
        "andy:A1",
        "reason: carried_dismissed"
    );
    d.shutdown().await;
}
