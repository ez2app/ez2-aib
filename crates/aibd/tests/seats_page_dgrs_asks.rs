#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! A11 (DESIGN-a11-dgrs-open-asks-08de5-1123 r1; SPEC-ai-partner-open-ebab8-1123-r2 §3.1
//! 「完全權限:每次開都要驗證」; f163c REVIEW-a11 r0 M2 / r1 L1 L2): every act of the built-in
//! page that starts a dgrs seat's provider asks the owner first — an open with no repo, 重開,
//! 換模型, 清對話 — and nothing is opened, stopped or started until the right ticket comes back.
//! A safe seat is not asked. Its own binary: the owner (a fake presence that says yes and
//! counts) and the dgrs switch are set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::json;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// Says yes to every window, and counts them.
struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

/// Lets every window open.
struct Open;

impl aibd::owner_ok::WindowGate for Open {
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

fn owner() -> Arc<AtomicUsize> {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    n
}

fn seats(v: &serde_json::Value) -> usize {
    v["seats"].as_array().map_or(0, Vec::len)
}

fn starts(cwd: &std::path::Path) -> usize {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .lines()
        .count()
}

/// The window answers on its own thread: wait until `n` windows were answered.
async fn answered(n: &AtomicUsize, want: usize) {
    for _ in 0..100 {
        if n.load(Ordering::SeqCst) >= want {
            // the answer is written just after the presence returns
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    panic!("reason: window_never_asked");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_dgrs_seat_starts_from_the_page_only_through_the_owner() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let n = owner();
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let list = || async { seats(&d.call("GET", "/api/v1/seats", None, None).await.json) };
    let open = |persona: &str, key: &str, ticket: Option<&str>| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "dgrs",
                           "persona_uid": persona, "idem_key": key});
        if let Some(t) = ticket {
            b["ticket"] = json!(t);
        }
        b
    };

    // (a) no ticket: 202, the window asked once, no seat
    let before = list().await;
    let r = post(open("un0eba", "a11-1", None)).await;
    assert_eq!(r.code, 202, "reason: dgrs_opened_unasked {}", r.body);
    assert_eq!(r.json["reason"], "owner_check_pending", "{}", r.body);
    let ticket = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, 1).await;
    assert_eq!(list().await, before, "reason: opened_before_the_owner");

    // (b) that ticket for another persona: not approved, nothing opened (and not used up)
    let r = post(open("mnnftg", "a11-2", Some(&ticket))).await;
    assert_eq!(r.code, 403, "reason: ticket_crossed_personas {}", r.body);
    assert_eq!(r.json["reason"], "owner_check_not_approved", "{}", r.body);
    assert_eq!(list().await, before);

    // (c) the right ticket: opened, once
    let r = post(open("un0eba", "a11-3", Some(&ticket))).await;
    assert_eq!(r.code, 201, "reason: approved_open_refused {}", r.body);
    assert_eq!(r.json["seat"]["mode"], "dgrs", "{}", r.body);
    assert_eq!(list().await, before + 1);
    let r = post(open("un0eba", "a11-4", Some(&ticket))).await;
    assert_eq!(r.code, 403, "reason: ticket_used_twice {}", r.body);

    // a brand with no dgrs mode is refused as before, with no window
    let asked = n.load(Ordering::SeqCst);
    let r = post(
        json!({"type": "seat_open", "provider": "codex", "mode": "dgrs",
                        "persona_uid": "un0eba", "idem_key": "a11-5"}),
    )
    .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(
        n.load(Ordering::SeqCst),
        asked,
        "reason: window_for_a_refused_brand"
    );

    // (e) 重開 a stopped dgrs seat: asked, nothing started until the right ticket; a ticket of
    // an open does not pass for a restart (L1)
    let wd = seat_dir(dir.path(), "dg");
    let (seat, sid) = d.open("dgrs", &wd).await;
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sid}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let started = starts(&wd);
    let asked = n.load(Ordering::SeqCst);
    let restart = |key: &str, ticket: Option<&str>| {
        let mut b = json!({"type": "seat_restart", "seat_id": seat, "idem_key": key});
        if let Some(t) = ticket {
            b["ticket"] = json!(t);
        }
        b
    };
    let r = post(restart("a11-r1", None)).await;
    assert_eq!(r.code, 202, "reason: dgrs_restarted_unasked {}", r.body);
    let rt = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, asked + 1).await;
    assert_eq!(starts(&wd), started, "reason: started_before_the_owner");
    let r = post(open("un0eba", "a11-6", None)).await;
    let ot = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, asked + 2).await;
    let r = post(restart("a11-r2", Some(&ot))).await;
    assert_eq!(r.code, 403, "reason: open_ticket_restarted {}", r.body);
    assert_eq!(starts(&wd), started);
    let r = post(restart("a11-r3", Some(&rt))).await;
    assert_eq!(r.code, 200, "reason: approved_restart_refused {}", r.body);

    // (L2) 換模型 on a running dgrs seat: asked before the model is written or the seat stopped
    let asked = n.load(Ordering::SeqCst);
    let r = post(
        json!({"type": "seat_model", "seat_id": seat, "model": "sonnet",
                        "idem_key": "a11-m1"}),
    )
    .await;
    assert_eq!(r.code, 202, "reason: dgrs_model_unasked {}", r.body);
    answered(&n, asked + 1).await;
    let r = d.call("GET", "/api/v1/seats", None, None).await;
    let me = r.json["seats"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["seat_id"] == seat.as_str())
        .cloned()
        .unwrap_or_default();
    assert_ne!(me["model"], "sonnet", "reason: model_written_unasked {me}");

    // (L2) 清對話 on a dgrs seat: asked before anything is stopped
    let asked = n.load(Ordering::SeqCst);
    let r = post(json!({"type": "seat_clear", "seat_id": seat,
                        "handoff_at": "2026-11-23T03:00:00Z", "idem_key": "a11-c1"}))
    .await;
    assert_eq!(r.code, 202, "reason: dgrs_clear_unasked {}", r.body);

    // a safe seat restarts with no window
    let ws = seat_dir(dir.path(), "sf");
    let (safe, ss) = d.open("safe", &ws).await;
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{ss}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    answered(&n, asked + 1).await;
    let asked = n.load(Ordering::SeqCst);
    let r = post(json!({"type": "seat_restart", "seat_id": safe, "idem_key": "a11-s1"})).await;
    assert_eq!(r.code, 200, "reason: safe_restart_refused {}", r.body);
    assert_eq!(
        n.load(Ordering::SeqCst),
        asked,
        "reason: safe_restart_asked"
    );
    d.shutdown().await;
}
