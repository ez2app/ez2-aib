//! B1dm-S4: reading a seat's mail against the frozen 1.x Python.
//!
//! `tests/fixtures/view-v1.json` (in this crate, so it outlives the 1.x tree; 5d33a N2) was made by running the frozen
//! `dm_queue.py` (main 50612a0b7) with its file reads replaced by the listed items: 11 sets
//! (227 items, seeded random over every status, priority, sender kind, kind, bad and
//! missing times, waits, supersedes, expiry and global / IM marks, plus an empty set) with
//! their delivery order and manual waiting order. For each set it holds `tabs_payload`
//! (ids per tab in order, counts, oldest), the badges, `brief_payload`'s oldest unread,
//! `stale_payload` at 0 / 30 / 120 minutes, `list_items` both ways, `list_for_box`, and per
//! item `_touched_at`, `wait_overdue`, `wait_stale`, `_expired`, the row's `expired_now`,
//! `is_update`, `age_min`, `when` and `delivery_text`.

use std::path::Path;

use aib_dmq::view::{self, Badges};
use serde_json::{json, Value};

fn fixture() -> Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/view-v1.json");
    let text = std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {}: {e}", p.display()))
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array()
        .expect("array")
        .iter()
        .map(|s| s.as_str().expect("string").to_string())
        .collect()
}

fn ids(rows: &[&Value]) -> Vec<String> {
    rows.iter()
        .map(|r| r["id"].as_str().expect("id").to_string())
        .collect()
}

fn badges_json(b: &Badges) -> Value {
    json!({
        "unread": b.unread, "expired": b.expired, "update": b.update, "hold": b.hold,
        "im_hold": b.im_hold, "global_hold": b.global_hold, "decide": b.decide, "human": b.human,
        "stale_wait": b.stale_wait, "wait_overdue": b.wait_overdue,
    })
}

#[test]
fn every_fixture_set_reads_like_the_python() {
    let fx = fixture();
    let now = fx["now"].as_i64().expect("now");
    let sets = fx["sets"].as_array().expect("sets");
    assert_eq!(sets.len(), 11);
    let mut items_seen = 0;
    for (n, set) in sets.iter().enumerate() {
        let items: Vec<Value> = set["items"].as_array().expect("items").clone();
        items_seen += items.len();
        let order = strings(&set["order"]);
        let waiting = strings(&set["waiting_order"]);

        let tabs = view::tabs(&items, &order, &waiting, now);
        assert_eq!(tabs.len(), 5);
        for (name, tab) in &tabs {
            let want = &set["tabs"][*name];
            assert_eq!(
                ids(&tab.rows),
                strings(&want["ids"]),
                "set {n} tab {name} order"
            );
            assert_eq!(
                Some(tab.rows.len() as u64),
                want["n"].as_u64(),
                "set {n} tab {name} n"
            );
            assert_eq!(
                tab.oldest_ts,
                want["oldest_ts"].as_i64(),
                "set {n} tab {name} oldest_ts"
            );
            assert_eq!(
                tab.oldest_when.as_deref(),
                want["oldest_when"].as_str(),
                "set {n} tab {name} oldest_when"
            );
        }
        let mut want_badges = set["badges"].clone();
        assert_eq!(
            badges_json(&view::badges(&tabs, now)),
            want_badges.take(),
            "set {n} badges"
        );

        let c = view::counts(&tabs);
        let got = json!({
            "tabs": c.tabs.iter().map(|(t, n)| (t.to_string(), json!(n))).collect::<serde_json::Map<_, _>>(),
            "by_status": c.by_status,
            "read_without_wait": c.read_without_wait,
        });
        assert_eq!(got, set["counts"], "set {n} counts");

        let brief = view::oldest_unread(&tabs, now);
        assert_eq!(
            brief.as_ref().map(|b| b.0),
            set["brief"]["oldest_ts"].as_i64(),
            "set {n} brief ts"
        );
        assert_eq!(
            brief.and_then(|b| b.1).as_deref(),
            set["brief"]["oldest_when"].as_str(),
            "set {n} brief when"
        );

        for (m, want) in set["stale"].as_object().expect("stale") {
            let minutes: i64 = m.parse().expect("minutes");
            let got = view::stale(&items, now, minutes);
            let got = json!({
                "unread": got.unread, "stale": got.stale, "expired": got.expired,
                "minutes": got.minutes, "oldest_ts": got.oldest_ts,
                "oldest_when": got.oldest_when, "ids": got.ids,
            });
            assert_eq!(&got, want, "set {n} stale {m}");
        }

        assert_eq!(
            ids(&view::list(&items, &order, false)),
            strings(&set["list"]),
            "set {n} list"
        );
        assert_eq!(
            ids(&view::list(&items, &order, true)),
            strings(&set["list_newest"]),
            "set {n} newest"
        );
        assert_eq!(
            ids(&view::list_for_box(&items, &order)),
            strings(&set["box"]),
            "set {n} box"
        );

        for (item, want) in items
            .iter()
            .zip(set["per_item"].as_array().expect("per_item"))
        {
            let id = want["id"].as_str().expect("id");
            assert_eq!(
                view::touched_at(item),
                want["touched_at"].as_i64(),
                "{id} touched_at"
            );
            assert_eq!(
                Some(view::wait_overdue(item, now)),
                want["wait_overdue"].as_bool(),
                "{id} overdue"
            );
            assert_eq!(
                Some(view::wait_stale(item, now)),
                want["wait_stale"].as_bool(),
                "{id} stale"
            );
            assert_eq!(
                Some(view::expired(item, now)),
                want["expired"].as_bool(),
                "{id} expired"
            );
            let row = &want["row"];
            assert_eq!(
                Some(view::expired_now(item, now)),
                row["expired_now"].as_bool(),
                "{id} expired_now"
            );
            assert_eq!(
                Some(view::is_update(item)),
                row["is_update"].as_bool(),
                "{id} is_update"
            );
            assert_eq!(
                view::age_min(item, now),
                row["age_min"].as_i64(),
                "{id} age_min"
            );
            assert_eq!(
                Some(view::row_when(item, now).as_str()),
                row["when"].as_str(),
                "{id} when"
            );
            assert_eq!(
                Some(view::delivery_text(item, now).as_str()),
                want["delivery_text"].as_str(),
                "{id} delivery_text"
            );
        }
    }
    assert_eq!(items_seen, 227);
}

#[test]
fn taipei_stamps_match_the_python() {
    let fx = fixture();
    for row in fx["stamps"].as_array().expect("stamps") {
        let e = row[0].as_i64().expect("epoch");
        assert_eq!(
            Some(view::taipei_stamp(e).as_str()),
            row[1].as_str(),
            "stamp {e}"
        );
    }
}

// --- named cells for the rules the sets exercise -------------------------------------

const NOW: i64 = 1_794_960_000;

#[test]
fn a_status_outside_the_five_tabs_is_on_the_waiting_tab() {
    for s in [
        None,
        Some(""),
        Some("weird"),
        Some("queued"),
        Some("hold_human"),
    ] {
        assert_eq!(view::tab_of_status(s), "waiting", "{s:?}");
    }
    assert_eq!(view::tab_of_status(Some("read")), "read");
}

#[test]
fn the_waiting_tab_puts_undelivered_human_and_decide_first() {
    let items = vec![
        json!({"id": "q_a", "status": "delivered", "sender_kind": "human", "ts": 1}),
        json!({"id": "q_b", "status": "queued", "sender_kind": "seat", "ts": 2}),
        json!({"id": "q_c", "status": "queued", "sender_kind": "human", "ts": 3}),
        json!({"id": "q_d", "status": "queued", "sender_kind": "seat", "kind": "decide", "ts": 4}),
        json!({"id": "q_e", "status": "queued", "sender_kind": "seat", "priority": "urgent", "ts": 5}),
    ];
    let tabs = view::tabs(&items, &[], &[], NOW);
    assert_eq!(ids(&tabs[0].1.rows), ["q_c", "q_d", "q_e", "q_b", "q_a"]);
}

#[test]
fn an_unread_message_older_than_the_minutes_is_stale_and_an_expired_one_is_not_unread() {
    let items = vec![
        json!({"id": "q_1", "status": "delivered", "sent_at": NOW - 31 * 60}),
        json!({"id": "q_2", "status": "delivered", "sent_at": NOW - 29 * 60}),
        json!({"id": "q_3", "status": "delivered", "sent_at": NOW - 99 * 60, "expires": NOW}),
        json!({"id": "q_4", "status": "delivered", "sent_at": NOW - 99 * 60, "read_at": NOW}),
    ];
    let s = view::stale(&items, NOW, 30);
    assert_eq!(
        (s.unread, s.stale, s.expired, s.ids),
        (2, 1, 1, vec!["q_1".to_string()])
    );
}

#[test]
fn a_read_message_untouched_for_four_hours_reminds_by_whether_it_says_what_it_waits_for() {
    let quiet = json!({"status": "read", "read_at": NOW - 4 * 3600 - 1});
    let waits =
        json!({"status": "read", "read_at": NOW - 4 * 3600 - 1, "wait": {"text": "等 a9136"}});
    let fresh = json!({"status": "read", "read_at": NOW - 4 * 3600});
    assert!(view::wait_stale(&quiet, NOW) && !view::wait_overdue(&quiet, NOW));
    assert!(view::wait_overdue(&waits, NOW) && !view::wait_stale(&waits, NOW));
    assert!(!view::wait_stale(&fresh, NOW));
}

#[test]
fn show_adds_the_age_line_only_when_asked_and_ends_with_a_newline() {
    let item = json!({"msg": "hi", "sent_at": NOW - 5});
    assert_eq!(view::show_text(&item, NOW, false), "hi\n");
    assert_eq!(
        view::show_text(&item, NOW, true),
        "本則發出於 07:59:55,現在 08:00:00,已過 5 秒\nhi\n"
    );
    assert_eq!(view::show_text(&json!({"msg": ""}), NOW, true), "");
}

#[test]
fn a_bad_sent_at_is_not_stale_where_the_python_crashed() {
    // 1.x int("bad") raised inside stale_payload; reading mail must not stop on one bad file.
    let items = vec![json!({"id": "q_x", "status": "delivered", "sent_at": "bad"})];
    let s = view::stale(&items, NOW, 30);
    assert_eq!((s.unread, s.stale), (1, 0));
}

// --- moved from test-dm-evidence-display-cfd04-1025.py E3 / E5 ----------------------

#[test]
fn the_counts_give_rows_per_tab_and_status_and_read_rows_without_a_wait() {
    let items = vec![
        json!({"id": "q_a", "status": "queued"}),
        json!({"id": "q_b", "status": "read"}),
        json!({"id": "q_c", "status": "done"}),
    ];
    let c = view::counts(&view::tabs(&items, &[], &[], NOW));
    let per_tab: Vec<usize> = c.tabs.iter().map(|(_, n)| *n).collect();
    assert_eq!(per_tab, [1, 1, 1, 0, 0]);
    let by: Vec<(&str, usize)> = c.by_status.iter().map(|(k, v)| (k.as_str(), *v)).collect();
    assert_eq!(by, [("done", 1), ("queued", 1), ("read", 1)]);
    assert_eq!(c.read_without_wait, 1);
}

#[test]
fn a_wait_counts_only_when_it_is_an_object_with_text() {
    let waits = [
        json!({"text": "等 @abb89"}),
        json!({"text": "   "}),
        json!({"text": ""}),
        json!("字串不是 dict"),
        json!({"x": 1}),
        Value::Null,
        json!({"text": " 收 \n#366 "}),
    ];
    let items: Vec<Value> = waits
        .iter()
        .enumerate()
        .map(|(n, w)| json!({"id": format!("q_{n}"), "status": "read", "wait": w}))
        .collect();
    let c = view::counts(&view::tabs(&items, &[], &[], NOW));
    assert_eq!(c.read_without_wait, 5);
}

// --- moved from test-dm-take-close-044e0-1025.py T3 (the queue half) ----------------

#[test]
fn a_taken_message_left_four_hours_without_a_wait_is_stale_and_writing_one_turns_it_overdue() {
    let read_at = NOW;
    let later = NOW + view::STALE_WAIT_S + 60;
    let quiet = vec![json!({"id": "q_t", "status": "read", "read_at": read_at})];
    let tabs = view::tabs(&quiet, &[], &[], later);
    assert_eq!(view::badges(&tabs, later).stale_wait, 1);
    let waits = vec![json!({"id": "q_t", "status": "read", "read_at": read_at,
                            "wait": {"text": "等 @abb89 合 #363", "at": later}})];
    let much_later = later + view::STALE_WAIT_S * 3;
    let tabs = view::tabs(&waits, &[], &[], much_later);
    let b = view::badges(&tabs, much_later);
    assert_eq!((b.stale_wait, b.wait_overdue), (0, 1));
}

/// 5d33a N1: times come from the message files a seat writes; an extreme one is no panic and
/// no wrapped-around age (saturating, never overflowing).
#[test]
fn an_extreme_time_neither_panics_nor_wraps_around() {
    let now = 1_794_960_000_i64;
    for t in [i64::MIN, -9_000_000_000_000_000_000_i64, i64::MAX] {
        let item = json!({"id": "q_x", "status": "read", "sent_at": t, "ts": t, "read_at": t,
            "touched_at": t, "wait": {"text": "w", "at": t}, "msg": "m"});
        let _ = view::wait_overdue(&item, now);
        let _ = view::wait_stale(&item, now);
        if let Some(a) = view::age_min(&item, now) {
            assert!(a >= 0, "reason: age_wrapped_negative {t} {a}");
        }
        let _ = view::delivery_text(&item, now);
        let _ = view::show_text(&item, now, true);
        let _ = view::taipei_stamp(t);
        let _ = view::stale(std::slice::from_ref(&item), now, i64::MAX);
        let _ = view::stale(std::slice::from_ref(&item), now, i64::MIN);
    }
    let old = json!({"id": "q_y", "status": "queued", "sent_at": -9_000_000_000_000_000_000_i64});
    assert_eq!(
        view::age_min(&old, i64::MAX),
        Some(i64::MAX.div_euclid(60)),
        "reason: age_not_saturated"
    );
}
