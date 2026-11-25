#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! W-10 (IFACE-w10-session-policy-c2f0b-1125 r5): the page reads the session policy's state,
//! then writes one with the native programs found, after a fresh owner check each time; aibd
//! reads it back and uses it without a restart. A file already there is never replaced, a
//! body that names anything but `type` / `ticket` is refused, a launcher is never written.

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
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn state(d: &Daemon, cred: &str) -> Reply {
    let r = d
        .call("GET", "/api/v1/session-policy", Some(cred), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    r
}

#[tokio::test(flavor = "multi_thread")]
async fn the_page_writes_the_policy_once_after_a_fresh_owner_check() {
    let windows = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(windows.clone())), Arc::new(Gate));
    std::env::remove_var("AIBD_SESSION_POLICY");

    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    // not the harness's own `bin` (its fake claude / codex are on the start-up policy)
    let bin = dir.path().join("w10-look");
    std::fs::create_dir_all(&bin).unwrap();
    // claude: a native executable; codex: only an npm launcher
    std::fs::write(bin.join("claude"), b"\x7fELF\x02\x01\x01\0").unwrap();
    std::fs::write(bin.join("codex.cmd"), b"@node codex.js %*").unwrap();
    aibd::session_policy_setup::test_search_dirs(Some(vec![bin.clone()]));

    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user_page = d.page_form(true, "&to=user").await;
    let file = dir.path().join("data").join("session-policy.json");

    // the state: nothing written yet, claude found native, codex only as a launcher
    let r = state(&d, &page).await;
    assert_eq!(r.json["policy"], "missing", "{}", r.body);
    // the harness starts with its own policy (claude and codex); the page's file replaces it
    assert_eq!(r.json["programs"], json!(["claude", "codex"]), "{}", r.body);
    assert_eq!(r.json["can_write"], true, "{}", r.body);
    assert_eq!(r.json["policy_path"], file.to_str().unwrap(), "{}", r.body);
    let kinds: Vec<(String, String)> = r.json["found"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| {
            (
                f["name"].as_str().unwrap().to_owned(),
                f["kind"].as_str().unwrap().to_owned(),
            )
        })
        .collect();
    assert_eq!(
        kinds,
        [
            ("claude".to_owned(), "native".to_owned()),
            ("codex".to_owned(), "npm".to_owned())
        ],
        "{}",
        r.body
    );

    // r4: the request names nothing; a path (or any other field) is not this act
    for extra in [
        json!({"type": "session_policy_init", "path": "/bin/sh"}),
        json!({"type": "session_policy_init", "programs": [{"name": "claude", "path": "/bin/sh"}]}),
        json!({"type": "session_policy_init", "ticket": 7}),
    ] {
        let r = act(&d, &page, extra).await;
        assert_eq!(r.code, 400, "reason: body_named_more {}", r.body);
        assert_eq!(r.json["code"], "policy_bad_request", "{}", r.body);
    }
    assert_eq!(
        windows.load(Ordering::SeqCst),
        0,
        "reason: window_for_a_bad_body"
    );

    // a page from the user's folder cannot ask
    let r = act(&d, &user_page, json!({"type": "session_policy_init"})).await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert!(!file.exists());

    // first: a ticket and one window, nothing written
    let r = act(&d, &page, json!({"type": "session_policy_init"})).await;
    assert_eq!(r.code, 202, "reason: written_unasked {}", r.body);
    let ticket = r.json["ticket"].as_str().unwrap().to_owned();
    assert_eq!(windows.load(Ordering::SeqCst), 1);
    assert!(!file.exists(), "reason: written_before_the_owner");

    // 3e53b L2: what the owner approved is the text found then; a look that now finds more (a
    // native codex put down after the window) is another ask, so the ticket does not write it
    std::fs::write(bin.join("codex"), b"\x7fELF\x02\x01\x01\0").unwrap();
    let r = act(
        &d,
        &page,
        json!({"type": "session_policy_init", "ticket": ticket}),
    )
    .await;
    assert_eq!(r.code, 403, "reason: approved_other_text {}", r.body);
    assert!(!file.exists(), "reason: written_with_another_look");
    std::fs::remove_file(bin.join("codex")).unwrap();

    // a ticket that is not this one: refused, nothing written
    let r = act(
        &d,
        &page,
        json!({"type": "session_policy_init", "ticket": "t_not_it"}),
    )
    .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert!(!file.exists());

    // the approved ticket: written, read back, in force at once
    let r = act(
        &d,
        &page,
        json!({"type": "session_policy_init", "ticket": ticket}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["programs"], json!(["claude"]), "{}", r.body);
    let written: Value = serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
    let names: Vec<&str> = written["programs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["claude"], "reason: launcher_written {written}");
    assert_eq!(
        written["programs"][0]["path"],
        bin.join("claude").to_str().unwrap()
    );
    let r = state(&d, &page).await;
    assert_eq!(r.json["policy"], "loaded", "{}", r.body);
    assert_eq!(
        r.json["programs"],
        json!(["claude"]),
        "reason: not_in_force_without_restart {}",
        r.body
    );

    // r3: a file already there is never replaced, and no window is raised for it
    let r = act(&d, &page, json!({"type": "session_policy_init"})).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "policy_exists", "{}", r.body);
    assert_eq!(windows.load(Ordering::SeqCst), 1);
    // the used ticket again, with the file gone: a fresh check every time (no hold)
    std::fs::remove_file(&file).unwrap();
    let r = act(
        &d,
        &page,
        json!({"type": "session_policy_init", "ticket": ticket}),
    )
    .await;
    assert_eq!(r.code, 403, "reason: ticket_used_twice {}", r.body);
    assert!(!file.exists());
    // an unreadable file is not replaced either
    std::fs::write(&file, b"{ not json").unwrap();
    let r = state(&d, &page).await;
    assert_eq!(r.json["policy"], "invalid", "{}", r.body);
    let r = act(&d, &page, json!({"type": "session_policy_init"})).await;
    assert_eq!(r.json["code"], "policy_exists", "{}", r.body);
    assert_eq!(std::fs::read(&file).unwrap(), b"{ not json");
    std::fs::remove_file(&file).unwrap();

    // no native program: nothing to approve, no window
    std::fs::remove_file(bin.join("claude")).unwrap();
    let r = act(&d, &page, json!({"type": "session_policy_init"})).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_eq!(r.json["code"], "policy_no_native", "{}", r.body);
    assert_eq!(windows.load(Ordering::SeqCst), 1);
    assert_eq!(state(&d, &page).await.json["can_write"], false);

    aibd::session_policy_setup::test_search_dirs(None);
    d.shutdown().await;
}
