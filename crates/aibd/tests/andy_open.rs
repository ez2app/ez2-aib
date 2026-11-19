#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! N-workspace 第三波 1 + SPEC §14 end to end: an open item of the 待人決策 list (aib-drive
//! `andy_open` events, #847, written here by aib-drive's own `andy-open open`) is a card with the
//! board's fields (tag / text / card / since / rec); a page answers it with `decision_answer`
//! into the seat it names (one line reaches that seat, the card leaves, the item is closed on
//! the same list, the journal keeps length and hash); with no seat named the item is only
//! closed; a closed or unknown item is not answered. aib-drive's next list (act-next.json) gives
//! its merge / push rows as slot-5 cards (第三波 2).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, text_of, Daemon};
use serde_json::{json, Value};
use std::path::Path;

fn drive(root: &Path, args: &[&str]) {
    let ctx = aib_ledger::events::Ctx::for_run(root, Some("519d0"));
    let args: Vec<String> = args.iter().map(|a| (*a).to_owned()).collect();
    let (rc, out) = aib_drive::andy_open::run(&args, root, &ctx, aib_drive::act::now_ms());
    assert_eq!(rc, 0, "{out}");
}

fn open(root: &Path, item: &str, kind: &str, text: &str, more: &[&str]) {
    let mut a = vec!["open", "--item", item, "--item-kind", kind, "--text", text];
    a.extend_from_slice(more);
    drive(root, &a);
}

fn done(root: &Path, item: &str) -> bool {
    let (evs, _) = aib_drive::andy_open::read_events(root).unwrap();
    aib_drive::andy_open::replay(&evs)
        .get(item)
        .is_some_and(|i| i.done)
}

fn andy_card(v: &Value, id: &str) -> Option<Value> {
    v["slots"]
        .as_array()?
        .iter()
        .find(|c| c["card_id"] == id)
        .cloned()
}

fn answer(seat: Option<&str>, text: &str, card: &str) -> String {
    let mut b = json!({"type": "decision_answer", "answer": text, "card_id": card});
    if let Some(s) = seat {
        b["seat_id"] = json!(s);
    }
    b.to_string()
}

#[tokio::test(flavor = "multi_thread")]
async fn an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("state-root");
    std::env::set_var("EZ2_AIB_STATE_ROOT", &root);
    open(
        &root,
        "A7",
        "決定",
        "要不要先上用量條",
        &["--rec", "先上", "--card", "N-workspace"],
    );
    open(&root, "A8", "決定", "已決的題", &[]);
    drive(&root, &["close", "--item", "A8"]);
    open(&root, "B2", "出手", "請按指紋", &[]);
    // the shape of a real act-next.json row (drive/act-next.json 11-18 04:15)
    std::fs::create_dir_all(root.join("drive")).unwrap();
    let next = json!({"schema": "aib-drive.next.v1", "as_of": "2026-11-18T04:15:32Z",
        "complete": false, "sources": [], "unknown": [], "skipped": [], "abandonable": [],
        "for_andy": [],
        "rows": [{"rank": 1, "layer": 1, "kind": "push", "what": "推 S91-selfcheck(審過未推)",
            "node": "S91-selfcheck", "to": "043dd", "why_him": "043dd 是作者",
            "why_now": "進度樹上審過、名下沒有開著的 PR", "unblocks": 0, "waited_min": 0,
            "starving": false, "needs": "seat_busy", "size": "review",
            "data_at": "2026-11-18T04:15:32Z"}]});
    std::fs::write(root.join("drive").join("act-next.json"), next.to_string()).unwrap();

    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "lead");
    let (seat, session) = d.open("safe", &cwd).await;
    let cred = d.page(true).await;

    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let c =
        andy_card(&r.json, "andy:A7").unwrap_or_else(|| panic!("reason: no_andy_card {}", r.body));
    assert_eq!(c["level"], 1);
    assert_eq!(c["tag"], "決定");
    assert_eq!(c["kind"], "andy.open");
    assert_eq!(c["text"], "要不要先上用量條");
    assert_eq!(c["rec"], "先上");
    assert_eq!(c["card"], "N-workspace");
    // 0eed7 VERIFY-slots d9e6d5133 M1: the time the item was opened, not month-day only
    assert!(
        c["since"]
            .as_str()
            .is_some_and(|t| t.len() > 10 && t.contains('T')),
        "reason: since_month_day_only {c}"
    );
    assert!(
        andy_card(&r.json, "andy:A8").is_none(),
        "reason: closed_item_carded"
    );
    let n = andy_card(&r.json, "next:push:S91-selfcheck")
        .unwrap_or_else(|| panic!("reason: no_next_card {}", r.body));
    assert_eq!(
        (n["level"].as_u64(), n["tag"].as_str()),
        (Some(5), Some("可推進"))
    );
    assert_eq!(n["card"], "S91-selfcheck");

    // a closed or unknown item is not answered, nothing typed
    for item in ["andy:A8", "andy:Z9"] {
        let b = answer(Some(&seat), "x", item);
        let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await;
        assert_eq!(r.code, 409, "reason: closed_item_answered {}", r.body);
        assert_eq!(r.json["code"], "no_decision");
    }
    assert!(read_log(&cwd).is_empty(), "reason: refused_answer_typed");

    // the answer reaches the seat the page names, once, and closes the item on the list
    let b = answer(Some(&seat), "A7 先上 的答案", "andy:A7");
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["session_id"], session.as_str());
    assert_eq!(r.json["closed"], true);
    let log = log_with(&cwd, 1);
    assert_eq!(text_of(&log[0].1), "A7 先上 的答案");
    assert!(done(&root, "A7"), "reason: list_not_closed");
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await;
    assert_eq!(r.code, 409, "{}", r.body);
    let r = d.call("GET", "/api/v1/slots", Some(&cred), None).await;
    assert!(
        andy_card(&r.json, "andy:A7").is_none(),
        "reason: answered_card_kept"
    );

    // no seat named: the item is only closed, nothing typed
    let b = answer(None, "已按", "andy:B2");
    let r = d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["delivery"], "none");
    assert!(done(&root, "B2"), "reason: seatless_answer_not_closed");
    assert_eq!(read_log(&cwd).len(), 1, "reason: seatless_answer_typed");

    // journaled with the card id and length, never the words
    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let e = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "decision.answered")
        .cloned()
        .unwrap_or_else(|| panic!("reason: not_journaled {}", r.body));
    assert_eq!(e["payload"]["card_id"], "andy:A7");
    assert!(
        !e["payload"].to_string().contains("的答案"),
        "reason: answer_in_journal"
    );
    d.shutdown().await;
}
