#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! aibd §8 8b-3 (IFACE-merge r3, REVIEW-mpname pre N1, standing owner-gate rule):
//! merge_find -> merge_preview -> merge_run -> merge_log -> merge_undo; built-in page only,
//! persona/brain merges require owner fingerprint, role merges do not, tampered plan_sha is
//! refused (`plan_changed`), and U+202E / 21-char names are stripped/refused.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

struct Gate;

impl aibd::owner_ok::WindowGate for Gate {
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

/// A batch's journal as merge_log lists it: (op, state) per step, in order.
async fn steps_of(d: &Daemon, page: &str, batch_id: &str) -> Vec<(String, String)> {
    let r = act(d, page, json!({"type": "merge_log", "batch_id": batch_id})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    r.json["batches"][0]["steps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            (
                s["op"].as_str().unwrap().to_owned(),
                s["state"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

/// The roles a seat is assigned, sorted.
async fn roles_of(d: &Daemon, page: &str, seat: &str) -> Vec<String> {
    let r = d
        .call(
            "GET",
            &format!("/api/v1/assignments?seat={seat}"),
            Some(page),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let mut v: Vec<String> = r.json["assignments"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a["role"].as_str().unwrap().to_owned())
        .collect();
    v.sort();
    v
}

#[tokio::test(flavor = "multi_thread")]
async fn merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo() {
    let windows = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(windows.clone())), Arc::new(Gate));

    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user_page = d.page_form(true, "&to=user").await;
    let data = dir.path().join("data");

    // Add two alike roles in registry.json
    let reg = json!({
        "roles": [
            {"uid": "r_one", "name": "核心架構師", "what": "design"},
            {"uid": "r_two", "name": "核心架構師-2", "what": "design"}
        ],
        "brains": []
    });
    std::fs::write(data.join("registry.json"), reg.to_string()).unwrap();

    // User folder page is refused
    let r = act(
        &d,
        &user_page,
        json!({"type": "merge_find", "kind": "role"}),
    )
    .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert!(r.body.contains("registry_built_in_page_only"), "{}", r.body);

    // merge_find lists the group
    let r = act(&d, &page, json!({"type": "merge_find", "kind": "role"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let groups = r.json["groups"].as_array().unwrap();
    assert_eq!(groups.len(), 1, "{}", r.body);
    assert_eq!(groups[0]["keep"], "r_one");
    assert_eq!(groups[0]["sure"], true);

    // M2 (f163c REVIEW-macts): a seat assigned the role that is merged away (after
    // merge_find: an assignment makes r_two the one kept by default)
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let r = act(
        &d,
        &page,
        json!({"type": "assign_add", "node_id": "G7", "seat_id": seat, "role": "r_two"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);

    // REVIEW-mpname N1: 21-char name refused; U+202E stripped in preview
    let r = act(
        &d,
        &page,
        json!({
            "type": "merge_preview",
            "kind": "role",
            "groups": [{"keep": "r_one", "merge": ["r_two"], "name": "一二三四五六七八九十一二三四五六七八九十一"}]
        }),
    )
    .await;
    assert_eq!(r.code, 400, "{}", r.body);

    let r = act(
        &d,
        &page,
        json!({
            "type": "merge_preview",
            "kind": "role",
            "groups": [{"keep": "r_one", "merge": ["r_two"], "name": "架構師\u{202E}ok"}]
        }),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["plan"]["groups"][0]["name"], "架構師ok");
    let sha = r.json["plan_sha"].as_str().unwrap().to_owned();
    let plan = r.json["plan"].clone();

    // Tampered plan is refused
    let mut bad_plan = plan.clone();
    bad_plan["groups"][0]["name"] = json!("被改過");
    let r = act(
        &d,
        &page,
        json!({"type": "merge_run", "plan_sha": sha, "plan": bad_plan}),
    )
    .await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["reason"], "plan_changed", "{}", r.body);

    // Role merge runs without owner window
    let r = act(
        &d,
        &page,
        json!({"type": "merge_run", "plan_sha": sha, "plan": plan}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(windows.load(Ordering::SeqCst), 0);
    let batch_id = r.json["batch_id"].as_str().unwrap().to_owned();
    assert_eq!(
        roles_of(&d, &page, &seat).await,
        vec!["r_one"],
        "reason: assignment_not_moved"
    );

    // N1: a plan runs once
    let r = act(
        &d,
        &page,
        json!({"type": "merge_run", "plan_sha": sha, "plan": plan}),
    )
    .await;
    assert_eq!(r.code, 409, "reason: plan_ran_twice {}", r.body);

    // merge_log lists the batch
    let r = act(
        &d,
        &page,
        json!({"type": "merge_log", "batch_id": batch_id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["batches"][0]["state"], "done", "{}", r.body);
    assert_eq!(r.json["batches"][0]["can_undo"], true, "{}", r.body);
    // M3 (f163c REVIEW-macts): the journal really has the steps (it was empty: seq from 1)
    let steps = steps_of(&d, &page, &batch_id).await;
    assert!(
        steps.iter().any(|(op, st)| op == "archive" && st == "done")
            && steps
                .iter()
                .any(|(op, st)| op == "role_assign" && st == "done"),
        "reason: journal_empty {steps:?}"
    );
    let msgs = aib_store::MessageDb::open(&data.join(aib_store::MESSAGES_FILE)).unwrap();
    assert!(msgs.archived("role", "r_two").unwrap().is_some());

    // merge_undo restores it
    let r = act(
        &d,
        &page,
        json!({"type": "merge_undo", "batch_id": batch_id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["state"], "undone", "{}", r.body);
    assert_eq!(
        roles_of(&d, &page, &seat).await,
        vec!["r_two"],
        "reason: assignment_not_moved_back"
    );
    assert_eq!(r.json["kept_changed"], json!([]), "{}", r.body);
    assert!(
        msgs.archived("role", "r_two").unwrap().is_none(),
        "reason: archive_not_undone"
    );
    let steps = steps_of(&d, &page, &batch_id).await;
    assert!(
        !steps.is_empty() && steps.iter().all(|(_, st)| st == "undone"),
        "reason: steps_not_undone {steps:?}"
    );
    // 08de5 01:08 N: a second undo is refused before any step is touched
    let r = act(
        &d,
        &page,
        json!({"type": "merge_undo", "batch_id": batch_id}),
    )
    .await;
    assert_eq!(r.code, 409, "reason: undone_twice {}", r.body);
    assert_eq!(
        steps_of(&d, &page, &batch_id).await,
        steps,
        "reason: steps_moved_by_second_undo"
    );

    d.shutdown().await;
}
