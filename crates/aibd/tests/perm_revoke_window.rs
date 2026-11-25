#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! pa2 L3 L4 (c2f0b REVIEW-pa2-postmerge L3 L4; REVIEW-pa2ls-993c0f9b L-c): a take-back (收回)
//! that turns items on again asks the owner, and the window names the template as it is now —
//! here one removed since the seat held it: 「已刪的模板」, never its bare tpl_uid. The seat
//! holds a raise (applied, then started on), is lowered to its mode's own, the raise's template
//! is removed, and the latest change is taken back. Its own binary: the owner (a fake presence
//! that says yes and keeps the windows' words) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// The words of every window asked, in order.
static SEEN: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

/// Says yes to every window, counts them and keeps their words.
struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        SEEN.lock().unwrap().push(reason.to_owned());
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

fn items(on: &[&str]) -> Value {
    let mut m = serde_json::Map::new();
    for (k, _) in aibd::perm_tpl::ITEMS {
        m.insert(k.to_owned(), json!(on.contains(&k)));
    }
    Value::Object(m)
}

/// The window answers on its own thread: wait until `n` windows were answered.
async fn answered(n: &AtomicUsize, want: usize) {
    for _ in 0..100 {
        if n.load(Ordering::SeqCst) >= want {
            // the answer is written just after the presence returns
            tokio::time::sleep(Duration::from_millis(100)).await;
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("reason: window_never_asked");
}

async fn act(d: &Daemon, cred: &str, b: Value) -> harness::Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&b.to_string()))
        .await
}

/// Keep a template the way the page keeps one (a raise is asked first): (tpl_uid, rev).
async fn keep(d: &Daemon, cred: &str, n: &AtomicUsize, name: &str, on: &[&str]) -> (String, i64) {
    let add = |t: Option<&str>| {
        let mut v = json!({"type": "perm_template_add", "name": name, "base": "safe",
                           "items": items(on)});
        if let Some(t) = t {
            v["ticket"] = json!(t);
        }
        v
    };
    let was = n.load(Ordering::SeqCst);
    let mut r = act(d, cred, add(None)).await;
    if r.code == 202 {
        let t = r.json["ticket"].as_str().unwrap().to_owned();
        answered(n, was + 1).await;
        r = act(d, cred, add(Some(&t))).await;
    }
    assert_eq!(r.code, 200, "{}", r.body);
    let uid = r.json["tpl_uid"].as_str().unwrap().to_owned();
    let l = act(d, cred, json!({"type": "perm_template_list"})).await;
    let rev = l.json["templates"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["tpl_uid"] == uid.as_str())
        .map(|t| t["rev"].as_i64().unwrap())
        .unwrap();
    (uid, rev)
}

async fn log(d: &Daemon, cred: &str, seat: &str) -> Value {
    let r = act(d, cred, json!({"type": "seat_perm_log", "seat_id": seat})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    r.json
}

#[tokio::test(flavor = "multi_thread")]
async fn a_take_back_to_a_removed_template_says_so_in_the_window() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let wd = seat_dir(dir.path(), "rv");
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let (seat, sid) = d.open("safe", &wd).await;
    let (raise, raise_rev) = keep(&d, &cred, &n, "寫碼", &["chat", "code"]).await;
    let (own, own_rev) = keep(&d, &cred, &n, "只對話", aibd::perm_tpl::gives_now("safe")).await;

    // the raise, with its ticket, then started on
    let apply = |uid: &str, rev: i64, t: Option<&str>| {
        let mut v = json!({"type": "perm_template_apply", "seat_id": seat, "tpl_uid": uid,
                           "rev": rev});
        if let Some(t) = t {
            v["ticket"] = json!(t);
        }
        v
    };
    let was = n.load(Ordering::SeqCst);
    let r = act(&d, &cred, apply(&raise, raise_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    let r = act(&d, &cred, apply(&raise, raise_rev, Some(&t))).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sid}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let r = act(
        &d,
        &cred,
        json!({"type": "seat_restart", "seat_id": seat, "idem_key": "rv-1"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        log(&d, &cred, &seat).await["now"]["tpl_uid"],
        raise.as_str(),
        "reason: raise_not_started_on"
    );

    // lowered to its mode's own (aibd starts the seat on it now), and the raise's template removed
    let r = act(&d, &cred, apply(&own, own_rev, None)).await;
    assert_eq!(r.code, 200, "reason: lower_refused {}", r.body);
    let end = Instant::now() + Duration::from_secs(10);
    while log(&d, &cred, &seat).await["now"]["tpl_uid"] != own.as_str() {
        assert!(Instant::now() < end, "reason: lower_not_started_on");
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let r = act(
        &d,
        &cred,
        json!({"type": "perm_template_remove", "tpl_uid": raise, "rev": raise_rev}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);

    // the latest change taken back: back to the raise, which turns items on ⇒ asked
    let l = log(&d, &cred, &seat).await;
    let latest = l["log"]
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["revoked_at"].is_null() && x["action"] != "open")
        .cloned()
        .unwrap_or_else(|| panic!("no change to take back {l}"));
    assert_eq!(latest["from"]["tpl_uid"], raise.as_str(), "{latest}");
    let was = n.load(Ordering::SeqCst);
    let r = act(
        &d,
        &cred,
        json!({"type": "seat_perm_revoke", "seat_id": seat, "log_id": latest["log_id"]}),
    )
    .await;
    assert_eq!(r.code, 202, "reason: take_back_raise_unasked {}", r.body);
    answered(&n, was + 1).await;
    let w = SEEN.lock().unwrap().last().cloned().unwrap_or_default();
    assert!(
        w.contains("已刪的模板"),
        "reason: removed_template_unsaid {w}"
    );
    assert!(
        !w.contains(raise.as_str()),
        "reason: bare_uid_in_window {w}"
    );
    d.shutdown().await;
}
