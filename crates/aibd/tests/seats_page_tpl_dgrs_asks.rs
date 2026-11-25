#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! A11 × 7b (ebab8 11-23 09:00 condition on seats.rs page_open): a seat that would open with
//! full permission (完全權限, mode dgrs) from a template is asked of the owner at least once on
//! every path. The template's own ask stands in for A11's (one window, one ticket); a template
//! of another base, an unknown, removed or garbled uid, or a uid with no rev opens nothing; a
//! ticket the owner gave for a plain dgrs open does not open one from a template. Its own
//! binary: the owner (a fake presence that says yes and counts) and the dgrs switch are set
//! once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// The words of every window asked, in order.
static SEEN: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

/// Says yes to every window, and counts them (and keeps their words).
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

fn seats(v: &Value) -> usize {
    v["seats"].as_array().map_or(0, Vec::len)
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
async fn a_full_permission_seat_from_a_template_is_always_asked() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let list = || async { seats(&d.call("GET", "/api/v1/seats", None, None).await.json) };

    // a template kept the way the page keeps one (a raise is asked first)
    let keep = |name: &'static str, base: &'static str| {
        let (p, n) = (&post, &n);
        async move {
            let add = |t: Option<&str>| {
                let mut b = json!({"type": "perm_template_add", "name": name, "base": base,
                                   "items": items(aibd::perm_tpl::gives_now(base))});
                if let Some(t) = t {
                    b["ticket"] = json!(t);
                }
                b
            };
            let was = n.load(Ordering::SeqCst);
            let mut r = p(add(None)).await;
            if r.code == 202 {
                let t = r.json["ticket"].as_str().unwrap().to_owned();
                answered(n, was + 1).await;
                r = p(add(Some(&t))).await;
            }
            assert_eq!(r.code, 200, "{}", r.body);
            let uid = r.json["tpl_uid"].as_str().unwrap().to_owned();
            let l = p(json!({"type": "perm_template_list"})).await;
            let rev = l.json["templates"]
                .as_array()
                .unwrap()
                .iter()
                .find(|t| t["tpl_uid"] == uid.as_str())
                .map(|t| t["rev"].as_i64().unwrap())
                .unwrap();
            (uid, rev)
        }
    };
    let (dg, dg_rev) = keep("完全", "dgrs").await;
    let (wk, wk_rev) = keep("沙盒", "work").await;
    let open = |key: &str, tpl: Option<(&str, Option<i64>)>, ticket: Option<&str>| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "dgrs",
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some((uid, rev)) = tpl {
            b["tpl_uid"] = json!(uid);
            if let Some(rev) = rev {
                b["rev"] = json!(rev);
            }
        }
        if let Some(t) = ticket {
            b["ticket"] = json!(t);
        }
        b
    };
    let before = list().await;

    // (a) a dgrs template, no ticket: asked once, nothing opened
    let asked = n.load(Ordering::SeqCst);
    let r = post(open("t-1", Some((&dg, Some(dg_rev))), None)).await;
    assert_eq!(r.code, 202, "reason: tpl_dgrs_opened_unasked {}", r.body);
    let tt = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, asked + 1).await;
    assert_eq!(
        n.load(Ordering::SeqCst),
        asked + 1,
        "reason: two_windows_for_one_open"
    );
    assert_eq!(list().await, before, "reason: opened_before_the_owner");
    // pa2 L2 (c2f0b REVIEW-pa2-postmerge L2): the window says who (no card: the uid) and which
    // brand, first
    let w = SEEN.lock().unwrap().last().cloned().unwrap_or_default();
    assert!(
        w.contains("un0eba(un0eba)· claude · "),
        "reason: open_window_without_who {w}"
    );

    // (b) a ticket the owner gave for a plain dgrs open does not open one from a template
    let r = post(open("t-2", None, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let plain = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, asked + 2).await;
    let r = post(open("t-3", Some((&dg, Some(dg_rev))), Some(&plain))).await;
    assert_eq!(r.code, 403, "reason: plain_ticket_opened_tpl {}", r.body);
    assert_eq!(list().await, before);

    // (c) a template of another base with mode dgrs: refused, nothing opened, no window
    let asked = n.load(Ordering::SeqCst);
    for (key, tpl) in [
        ("t-4", (wk.as_str(), Some(wk_rev))),
        ("t-5", ("tpl_nosuch0001", Some(1))),
        ("t-6", ("../x", Some(1))),
        ("t-7", (dg.as_str(), None)),
        ("t-8", (dg.as_str(), Some(dg_rev + 7))),
    ] {
        let r = post(open(key, Some(tpl), Some(&tt))).await;
        assert!(
            (400..500).contains(&r.code),
            "reason: tpl_path_opened {key} {} {}",
            r.code,
            r.body
        );
        assert_eq!(list().await, before, "reason: tpl_path_opened_a_seat {key}");
    }
    assert_eq!(
        n.load(Ordering::SeqCst),
        asked,
        "reason: window_for_a_refused_tpl"
    );

    // (d) the template's own ticket: opened with full permission, once
    let r = post(open("t-9", Some((&dg, Some(dg_rev))), Some(&tt))).await;
    assert_eq!(r.code, 201, "reason: approved_tpl_open_refused {}", r.body);
    assert_eq!(r.json["seat"]["mode"], "dgrs", "{}", r.body);
    assert_eq!(list().await, before + 1);
    d.shutdown().await;
}
