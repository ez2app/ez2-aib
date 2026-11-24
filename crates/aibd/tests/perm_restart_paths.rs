#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! aibd §7 7b (c2f0b P9 / f163c REVIEW-papply M2, an integration condition of ebab8 11-23
//! 09:10): a change waiting for a seat's restart takes effect on every start of a seat that was
//! opened, whatever its brand — the page's 重開 of a claude seat, of a codex seat, and aibd's own
//! rejoin at its start (清對話 and 換模型 start through the same function: revive's
//! `every_start_takes_the_waiting_change` reads that). Its own binary: the owner (a fake
//! presence that says yes and counts) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

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

fn args_of(cwd: &std::path::Path) -> Vec<Vec<String>> {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l.split_once(' ').unwrap().1).unwrap())
        .collect()
}

async fn act(d: &Daemon, cred: &str, b: Value) -> harness::Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&b.to_string()))
        .await
}

/// The seat's row: (now's tpl_uid, the waiting change's tpl_uid).
async fn row(d: &Daemon, cred: &str, seat: &str) -> (Option<String>, Option<String>) {
    let r = act(d, cred, json!({"type": "seat_perm_log", "seat_id": seat})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let s = |k: &str| r.json[k]["tpl_uid"].as_str().map(str::to_owned);
    (s("now"), s("pending"))
}

/// Apply the raise `uid` to `seat` with the owner's ticket: it waits for the restart.
async fn raise(d: &Daemon, cred: &str, n: &AtomicUsize, seat: &str, uid: &str, rev: i64) {
    let b = |t: Option<&str>| {
        let mut v = json!({"type": "perm_template_apply", "seat_id": seat, "tpl_uid": uid,
                           "rev": rev});
        if let Some(t) = t {
            v["ticket"] = json!(t);
        }
        v
    };
    let was = n.load(Ordering::SeqCst);
    let r = act(d, cred, b(None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    answered(n, was + 1).await;
    let r = act(d, cred, b(Some(&t))).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["pending_restart"], true, "{}", r.body);
    assert_eq!(row(d, cred, seat).await.1.as_deref(), Some(uid));
}

/// Stop the seat's session, then the page's 重開.
async fn restart(d: &Daemon, cred: &str, seat: &str, sid: &str, key: &str) {
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sid}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let r = act(
        d,
        cred,
        json!({"type": "seat_restart", "seat_id": seat, "idem_key": key}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_waiting_change_takes_effect_on_every_restart_of_every_brand() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let claude = dir.path().join("claude-config");
    std::env::set_var("CLAUDE_CONFIG_DIR", &claude);
    let (wa, wb, wc) = (
        seat_dir(dir.path(), "a"),
        seat_dir(dir.path(), "b"),
        seat_dir(dir.path(), "c"),
    );
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let (a, _) = d.open("safe", &wa).await;
    let (b, sb) = d.open("safe", &wb).await;
    let (c, sc) = d.open_as("codex", "safe", &wc).await;

    // a safe template that raises (kept the way the page keeps one: a raise is asked first)
    let add = |t: Option<&str>| {
        let mut v = json!({"type": "perm_template_add", "name": "寫碼", "base": "safe",
                           "items": items(&["chat", "code"])});
        if let Some(t) = t {
            v["ticket"] = json!(t);
        }
        v
    };
    let was = n.load(Ordering::SeqCst);
    let mut r = act(&d, &cred, add(None)).await;
    if r.code == 202 {
        let t = r.json["ticket"].as_str().unwrap().to_owned();
        answered(&n, was + 1).await;
        r = act(&d, &cred, add(Some(&t))).await;
    }
    assert_eq!(r.code, 200, "{}", r.body);
    let uid = r.json["tpl_uid"].as_str().unwrap().to_owned();
    let l = act(&d, &cred, json!({"type": "perm_template_list"})).await;
    let rev = l.json["templates"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["tpl_uid"] == uid.as_str())
        .map(|t| t["rev"].as_i64().unwrap())
        .unwrap();

    // (1) the page's 重開 of a codex seat takes it (it had no step of its own: c2f0b P9)
    raise(&d, &cred, &n, &c, &uid, rev).await;
    restart(&d, &cred, &c, &sc, "rp-c").await;
    assert_eq!(
        row(&d, &cred, &c).await,
        (Some(uid.clone()), None),
        "reason: codex_restart_skipped_the_change"
    );

    // (2) the page's 重開 of a claude seat takes it
    raise(&d, &cred, &n, &b, &uid, rev).await;
    restart(&d, &cred, &b, &sb, "rp-b").await;
    assert_eq!(
        row(&d, &cred, &b).await,
        (Some(uid.clone()), None),
        "reason: claude_restart_skipped_the_change"
    );

    // (3) aibd's rejoin at its start takes it (a running seat, its conversation kept)
    raise(&d, &cred, &n, &a, &uid, rev).await;
    d.shutdown().await;
    let first = args_of(&wa);
    let conv = first[0]
        .iter()
        .position(|w| w == "--session-id")
        .map(|i| first[0][i + 1].clone())
        .unwrap();
    let kept = aibd::revive::claude_dir_for(&claude, wa.to_str().unwrap());
    std::fs::create_dir_all(&kept).unwrap();
    std::fs::write(kept.join(format!("{conv}.jsonl")), "{}\n").unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let end = Instant::now() + Duration::from_secs(15);
    loop {
        let r = d
            .call("GET", &format!("/api/v1/seats/{a}/sessions"), None, None)
            .await;
        let list = r.json["sessions"].as_array().cloned().unwrap_or_default();
        if list.iter().any(|s| s["state"] == "running") {
            break;
        }
        assert!(
            Instant::now() < end,
            "reason: seat_not_picked_up {}",
            r.body
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let cred = d.page(true).await;
    assert_eq!(
        row(&d, &cred, &a).await,
        (Some(uid.clone()), None),
        "reason: rejoin_skipped_the_change"
    );
    d.shutdown().await;
}
