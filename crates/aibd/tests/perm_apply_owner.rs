#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! aibd §7 7b applying a template to an open seat (f163c REVIEW-papply pre 9f6265af L2, an
//! integration condition of ebab8 11-23 09:03): the gate itself. A raise with no ticket is
//! asked and changes nothing; a ticket the owner gave for another template is not approved and
//! changes nothing; the one it was given for goes in (waiting for the restart); a stale rev is
//! refused; a user folder page changes no seat's permissions. Its own binary: the owner (a
//! fake presence that says yes and counts) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::{json, Value};
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
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    panic!("reason: window_never_asked");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let wd = seat_dir(dir.path(), "sf");
    let (seat, _sid) = d.open("safe", &wd).await;
    let post = |b: Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };

    // two safe templates that raise (kept the way the page keeps one: a raise is asked first)
    let keep = |name: &'static str, on: &'static [&'static str]| {
        let (p, n) = (&post, &n);
        async move {
            let add = |t: Option<&str>| {
                let mut b = json!({"type": "perm_template_add", "name": name, "base": "safe",
                                   "items": items(on)});
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
    let (a, a_rev) = keep("寫碼", &["chat", "code"]).await;
    let (b, b_rev) = keep("指令", &["chat", "cmd"]).await;
    let apply = |uid: &str, rev: i64, ticket: Option<&str>| {
        let mut v = json!({"type": "perm_template_apply", "seat_id": seat, "tpl_uid": uid,
                           "rev": rev});
        if let Some(t) = ticket {
            v["ticket"] = json!(t);
        }
        v
    };
    let p = &post;
    let s = seat.clone();
    let pending = move || {
        let s = s.clone();
        async move {
            let r = p(json!({"type": "seat_perm_log", "seat_id": s})).await;
            assert_eq!(r.code, 200, "{}", r.body);
            r.json["pending"]["tpl_uid"].as_str().map(str::to_owned)
        }
    };

    // (a) a raise with no ticket: asked, nothing changed
    let was = n.load(Ordering::SeqCst);
    let r = post(apply(&a, a_rev, None)).await;
    assert_eq!(r.code, 202, "reason: apply_raise_not_asked {}", r.body);
    let ta = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    assert_eq!(pending().await, None, "reason: raise_written_unasked");

    // (b) the ticket the owner gave for the other template: not approved, nothing changed
    let r = post(apply(&b, b_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let tb = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 2).await;
    let r = post(apply(&a, a_rev, Some(&tb))).await;
    assert_eq!(r.code, 403, "reason: other_ticket_applied {}", r.body);
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(pending().await, None, "reason: other_ticket_written");

    // (c) a stale rev: refused before anything is asked
    let asked = n.load(Ordering::SeqCst);
    let r = post(apply(&a, a_rev + 5, Some(&ta))).await;
    assert_eq!(r.code, 409, "reason: stale_rev_applied {}", r.body);
    assert!(r.body.contains("template_changed"), "{}", r.body);
    assert_eq!(n.load(Ordering::SeqCst), asked);

    // (d) a user folder page changes no seat's permissions
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(&apply(&a, a_rev, Some(&ta)).to_string()),
        )
        .await;
    assert_eq!(r.code, 403, "reason: user_page_applied {}", r.body);
    assert!(r.body.contains("page_only"), "{}", r.body);
    assert_eq!(pending().await, None);

    // (e) its own ticket: in, waiting for the seat's restart
    let r = post(apply(&a, a_rev, Some(&ta))).await;
    assert_eq!(r.code, 200, "reason: approved_apply_refused {}", r.body);
    assert_eq!(r.json["pending_restart"], true, "{}", r.body);
    assert_eq!(pending().await.as_deref(), Some(a.as_str()));

    // (f) U8-3: replaying a used, expired, or revoked ticket is refused and leaves pending untouched.
    // Apply b first so pending moves off a and replaying ta reaches approved_for instead of Same.
    let was = n.load(Ordering::SeqCst);
    let r = post(apply(&b, b_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let tb = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    let r = post(apply(&b, b_rev, Some(&tb))).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(pending().await.as_deref(), Some(b.as_str()));

    let r = post(apply(&a, a_rev, Some(&ta))).await;
    assert_eq!(r.code, 403, "reason: used_ticket_replayed {}", r.body);
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(pending().await.as_deref(), Some(b.as_str()));

    let was = n.load(Ordering::SeqCst);
    let r = post(apply(&a, a_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let texp = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    aibd::owner_ok::test_expire_ticket(&texp);
    let r = post(apply(&a, a_rev, Some(&texp))).await;
    assert_eq!(r.code, 403, "reason: expired_ticket_accepted {}", r.body);
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(pending().await.as_deref(), Some(b.as_str()));

    let was = n.load(Ordering::SeqCst);
    let r = post(apply(&a, a_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let trev = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    aibd::owner_ok::test_revoke_ticket(&trev);
    let r = post(apply(&a, a_rev, Some(&trev))).await;
    assert_eq!(r.code, 403, "reason: revoked_ticket_accepted {}", r.body);
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(pending().await.as_deref(), Some(b.as_str()));

    // Positive control (ebab8 15:28): a fresh approved ticket for a still succeeds (proving the
    // three 403s above came from ticket state, not rev or anything else).
    let was = n.load(Ordering::SeqCst);
    let r = post(apply(&a, a_rev, None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let tfresh = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, was + 1).await;
    let r = post(apply(&a, a_rev, Some(&tfresh))).await;
    assert_eq!(
        r.code, 200,
        "reason: fresh_ticket_control_failed {}",
        r.body
    );
    assert_eq!(pending().await.as_deref(), Some(a.as_str()));
    d.shutdown().await;
}
