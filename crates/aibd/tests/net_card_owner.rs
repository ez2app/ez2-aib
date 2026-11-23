#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! The network card's yes through the owner (f163c REVIEW-netcardact incremental, L2's owner
//! half; ebab8 11-23 09:11 standing rule): a yes that needs the owner raises one window (202); a
//! ticket aibd did not approve, or one for another choice, finishes nothing; the card's own
//! ticket finishes it and `net_cards` drops; 「一直允許」 writes the seat's row, and taking it
//! back removes it. Its own binary: the owner (a fake presence that says yes and counts, after
//! 08de5's tests/perm_apply_owner.rs) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
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

/// The window answers on its own thread: wait until `want` windows were answered.
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

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn card(id: &str) -> aibd::net_card::Card {
    aibd::net_card::Card {
        request_id: id.into(),
        seat_id: "a1b2c".into(),
        persona_uid: "p_1".into(),
        persona_name: "頁面工程師".into(),
        dest_name: "db.lan".into(),
        dest: "10.0.0.5:5432".parse().unwrap(),
        source: "proxy",
        why: None,
        expires_at: now() + 100,
        high: false,
        listener: None,
    }
}

fn answer(id: &str, choice: &str, ticket: Option<&str>) -> Value {
    let mut b = json!({"type": "net_card_answer", "request_id": id, "choice": choice});
    if let Some(t) = ticket {
        b["ticket"] = json!(t);
    }
    b
}

#[tokio::test(flavor = "multi_thread")]
async fn a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let act = |b: Value| {
        let (d, page) = (&d, page.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&page), Some(&b.to_string()))
                .await
        }
    };
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);
    let waiting = |r: &Reply| r.json["net_cards"].as_u64();

    aibd::net_card_web::raise(card("rq_ow_1"));
    let r = act(answer("rq_ow_1", "min15", None)).await;
    assert_eq!(r.code, 202, "reason: no_window {}", r.body);
    let t1 = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, 1).await;
    assert_eq!(n.load(Ordering::SeqCst), 1, "reason: windows_counted");

    // a ticket aibd never gave, on the window's own choice
    let r = act(answer("rq_ow_1", "min15", Some("ot_forged"))).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_not_approved")),
        "reason: forged_ticket_taken {}",
        r.body
    );
    // the owner's ticket for 15 minutes does not open an hour
    let r = act(answer("rq_ow_1", "hour1", Some(&t1))).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_not_approved")),
        "reason: other_choice_taken {}",
        r.body
    );
    let r = d.call("GET", "/api/v1/seats", Some(&page), None).await;
    assert_eq!(waiting(&r), Some(1), "{}", r.body);
    // its own ticket
    let r = act(answer("rq_ow_1", "min15", Some(&t1))).await;
    assert_eq!(
        (r.code, r.json["done"].as_bool()),
        (200, Some(true)),
        "reason: own_ticket_refused {}",
        r.body
    );
    let r = d.call("GET", "/api/v1/seats", Some(&page), None).await;
    assert_eq!(
        waiting(&r),
        Some(0),
        "reason: finished_still_counted {}",
        r.body
    );
    // and only once
    let r = act(answer("rq_ow_1", "min15", Some(&t1))).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (409, Some("net_card_used")),
        "{}",
        r.body
    );

    // 一直允許: the seat's row, then taken back
    aibd::net_card_web::raise(card("rq_ow_2"));
    let r = act(answer("rq_ow_2", "always", None)).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t2 = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, 2).await;
    let r = act(json!({"type": "net_allow_list", "seat_id": "a1b2c"})).await;
    assert_eq!(
        r.json["items"].as_array().map(Vec::len),
        Some(0),
        "reason: listed_before_the_yes {}",
        r.body
    );
    let r = act(answer("rq_ow_2", "always", Some(&t2))).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = act(json!({"type": "net_allow_list", "seat_id": "a1b2c"})).await;
    assert_eq!(
        r.json["items"],
        json!([{"seat_id": "a1b2c", "ip": "10.0.0.5", "port": 5432}]),
        "reason: always_not_listed {}",
        r.body
    );
    let r = act(
        json!({"type": "net_allow_revoke", "seat_id": "a1b2c", "ip": "10.0.0.5", "port": 5432}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["closed"].as_u64()),
        (200, Some(0)),
        "{}",
        r.body
    );
    let r = act(json!({"type": "net_allow_list", "seat_id": "a1b2c"})).await;
    assert_eq!(
        r.json["items"].as_array().map(Vec::len),
        Some(0),
        "{}",
        r.body
    );
    d.shutdown().await;
}
