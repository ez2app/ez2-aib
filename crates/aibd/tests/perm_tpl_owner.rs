#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! aibd §7 permission templates (f163c REVIEW-permtpl-code pre d698a7ef L1): the gate itself.
//! A raise with no ticket is asked and changes nothing; a ticket the owner gave for other words
//! is not approved and changes nothing; the one it was given for goes in; a user folder page
//! keeps no template at all. Its own binary: the owner (a fake presence that says yes and
//! counts) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
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
async fn a_raise_goes_in_only_with_the_owners_ticket_for_it() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let p = &post;
    let rev_now = move || async move {
        let r = p(json!({"type": "perm_template_list"})).await;
        r.json["templates"][0]["rev"].as_i64().unwrap()
    };

    // a template that raises nothing goes in with no window
    let r = post(json!({"type": "perm_template_add", "name": "整理用",
                        "base": "safe", "items": items(&["chat"])}))
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let uid = r.json["tpl_uid"].as_str().unwrap().to_owned();
    let rev = rev_now().await;
    let set = |on: &[&str], ticket: Option<&str>| {
        let mut b = json!({"type": "perm_template_set", "tpl_uid": uid, "rev": rev,
                           "name": "整理用", "base": "safe", "items": items(on)});
        if let Some(t) = ticket {
            b["ticket"] = json!(t);
        }
        b
    };

    // (a) turning push on with no ticket: asked, nothing changed
    let r = post(set(&["chat", "push"], None)).await;
    assert_eq!(r.code, 202, "reason: raise_not_asked {}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    assert_eq!(rev_now().await, rev, "reason: raise_written_unasked");
    answered(&n, 1).await;

    // (b) that ticket with other words (host instead of push): not approved, nothing changed
    let r = post(set(&["chat", "host"], Some(&t))).await;
    assert_eq!(
        r.code, 403,
        "reason: ticket_for_other_words_passed {}",
        r.body
    );
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(rev_now().await, rev, "reason: other_words_written");

    // the words it was given for go in (a ticket is taken once: ask again)
    let r = post(set(&["chat", "push"], None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, 2).await;
    let r = post(set(&["chat", "push"], Some(&t))).await;
    assert_eq!(r.code, 200, "reason: approved_raise_refused {}", r.body);
    assert_ne!(rev_now().await, rev, "reason: approved_raise_not_written");

    // (c) a user folder page keeps no template, not even one that raises nothing
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(
                &json!({"type": "perm_template_add", "name": "別的",
                        "base": "safe", "items": items(&[])})
                .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 403, "reason: user_page_kept_a_template {}", r.body);
    assert!(r.body.contains("page_only"), "{}", r.body);
    d.shutdown().await;
}
