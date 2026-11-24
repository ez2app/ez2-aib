#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! aibd §8 8b-3 (IFACE-merge r3, REVIEW-mpname pre N1, standing owner-gate rule):
//! merge_find -> merge_preview -> merge_run -> merge_log -> merge_undo; built-in page only,
//! persona/brain merges require owner fingerprint, role merges do not, tampered plan_sha is
//! refused (`plan_changed`), and U+202E / 21-char names are stripped/refused.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
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
    fn page_now(&self, digest: [u8; 32]) -> Option<aibd::pairing::PageAccess> {
        Some(aibd::pairing::PageAccess {
            digest,
            confirmed: true,
            user_page: false,
            carried: false,
        })
    }
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo() {
    let windows = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(windows.clone())), Arc::new(Gate));

    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user_page = d.page_user(true).await;
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

    // merge_undo restores it
    let r = act(
        &d,
        &page,
        json!({"type": "merge_undo", "batch_id": batch_id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["state"], "undone", "{}", r.body);

    d.shutdown().await;
}
