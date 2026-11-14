//! aib-dmq-store recv (take / close / wait) on a temp queue root, against what the frozen
//! dm_take.py does, plus the race 1.x loses: a receiver's note written over a close.

use std::path::PathBuf;
use std::sync::{Arc, Barrier};

use aib_dmq::spec::ITEM_SCHEMA;
use aib_dmq_store::recv::{close, set_wait, take, Ctx};
use aib_dmq_store::store::Caller;
use aib_dmq_store::{ItemRead, Store};
use serde_json::{json, Map, Value};

const SEAT: &str = "aaaaa";
const ID: &str = "q_00000000000000b2";
const ME: &str = "seat:aaaaa";

struct T {
    /// Kept only so the temp folder lives as long as the test (read on no platform).
    _d: tempfile::TempDir,
    s: Store,
    caller: Caller,
    relay: Vec<PathBuf>,
}

impl T {
    fn new() -> T {
        let d = tempfile::tempdir().expect("tempdir");
        let s = Store::new(d.path(), d.path());
        let pending = d.path().join("pending");
        std::fs::create_dir_all(&pending).expect("pending");
        let relay = vec![std::fs::canonicalize(&pending).expect("canon")];
        T {
            _d: d,
            s,
            caller: Caller::default(),
            relay,
        }
    }
    fn ctx(&self) -> Ctx<'_> {
        Ctx {
            caller: &self.caller,
            caller_is_seat: true,
            relay_dirs: &self.relay,
            now: 1_794_578_890,
        }
    }
    fn put(&self, status: &str, extra: Value) {
        let mut m: Map<String, Value> = json!({"schema": ITEM_SCHEMA, "id": ID, "seat": SEAT,
            "msg": "請審 S3", "status": status, "rev": 3, "kind": "report",
            "sender": "agent:bbbbb", "sender_label": "@bbbbb", "ts": 1_794_578_000})
        .as_object()
        .expect("object")
        .clone();
        if let Value::Object(e) = extra {
            m.extend(e);
        }
        self.s.write_item(SEAT, &m).expect("write");
    }
    fn item(&self) -> Map<String, Value> {
        match self.s.read_item(SEAT, ID).expect("read") {
            ItemRead::Item { item, .. } => item,
            other => panic!("{other:?}"),
        }
    }
    fn status(&self) -> String {
        self.item()["status"].as_str().unwrap_or("").to_string()
    }
}

#[test]
fn take_reads_marks_read_and_keeps_the_rev_seen() {
    let t = T::new();
    t.put("delivered", json!({}));
    let out = take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take").out;
    assert_eq!(out["ok"], json!(true), "{out}");
    assert_eq!(out["reason"], json!("ok"));
    assert_eq!(out["text"], json!("請審 S3"));
    assert_eq!(out["from"], json!("@bbbbb"));
    assert_eq!(out["chars"], json!(5));
    assert_eq!(out["when"], json!("11-13 21:53:20"), "ts in Taipei");
    assert!(out["next"]["close"]
        .as_str()
        .is_some_and(|s| s.contains(ID)));
    assert!(out["next"]["reply_and_close"].is_string(), "not a dispatch");
    assert!(out["next"]["wait"].is_string(), "no wait yet");
    let it = t.item();
    assert_eq!(it["status"], json!("read"));
    assert_eq!(
        (it["taken_rev"].clone(), it["touched_at"].clone()),
        (json!(3), json!(1_794_578_890))
    );
    assert_eq!(it["rev"], json!(3), "a note does not bump the sender's rev");
}

#[test]
fn take_with_a_wait_writes_it_cleaned() {
    let t = T::new();
    t.put("read", json!({"kind": "order"}));
    let out = take(
        &t.s,
        &t.ctx(),
        SEAT,
        ID,
        ME,
        Some(" 等 043dd\u{200b}\n回信 "),
    )
    .expect("take")
    .out;
    assert_eq!(out["wait"], json!("等 043dd 回信"));
    assert!(
        out["next"].get("reply_and_close").is_none(),
        "a dispatch is not closed by a reply"
    );
    assert!(out["next"].get("wait").is_none());
    assert_eq!(
        t.item()["wait"],
        json!({"text": "等 043dd 回信", "at": 1_794_578_890})
    );
}

#[test]
fn only_the_receiving_seat_may_take() {
    let t = T::new();
    t.put("delivered", json!({}));
    for who in ["seat:bbbbb", "human:andy", "agent:aaaaa/x", ""] {
        let out = take(&t.s, &t.ctx(), SEAT, ID, who, None).expect("take").out;
        assert_eq!(out["reason"], json!("actor"), "{who}: {out}");
    }
    assert_eq!(t.status(), "delivered");
    // a read or closed dm is not moved, so the transition rules never see the actor:
    // take has to check it itself (dm_take.py #380 r1 M3)
    for st in ["read", "done"] {
        t.put(st, json!({}));
        let out = take(&t.s, &t.ctx(), SEAT, ID, "seat:bbbbb", None)
            .expect("take")
            .out;
        assert_eq!(out["reason"], json!("actor"), "{st}: {out}");
        assert!(out.get("text").is_none(), "no text for another seat");
        assert!(t.item().get("taken_rev").is_none(), "no note");
    }
}

/// Unix only: relay pointers are `/`-rooted paths (relay_path_of, known gap on Windows).
#[cfg(unix)]
#[test]
fn a_long_message_is_read_from_the_relay_folder_only() {
    let t = T::new();
    let f = t.relay[0].join("RELAY-dm-dab9-1.txt");
    std::fs::write(&f, "全文第一行\n第二行\n").expect("relay");
    t.put(
        "delivered",
        json!({"msg": format!("長訊已落檔(30 位元組,來自 @bbbbb),請讀:{}", f.display())}),
    );
    let out = take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take").out;
    assert_eq!(out["text"], json!("全文第一行\n第二行\n"));
    assert_eq!(out["relay_path"], json!(f.display().to_string()));
    assert_eq!(t.status(), "read");

    // a pointer to a file outside the relay folder: not read, not marked read
    let t = T::new();
    let root = t.relay[0].parent().expect("temp root");
    let other = root.join("RELAY-dm-x.txt");
    std::fs::write(&other, "secret").expect("other");
    t.put(
        "delivered",
        json!({"msg": format!("長訊已落檔(6 位元組),請讀:{}", other.display())}),
    );
    let out = take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take").out;
    assert_eq!(out["reason"], json!("relay_unreadable"), "{out}");
    assert!(out.get("text").is_none());
    assert_eq!(t.status(), "delivered");
    let out = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("x"))
        .expect("close")
        .out;
    assert_eq!(out["reason"], json!("relay_unreadable"));
    assert_eq!(t.status(), "delivered");
}

#[test]
fn close_after_the_dm_changed_since_take_is_refused() {
    let t = T::new();
    t.put("delivered", json!({}));
    take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take");
    let mut it = t.item();
    it.insert("rev".into(), json!(4));
    it.insert("msg".into(), json!("改過的內容"));
    t.s.write_item(SEAT, &it).expect("edit");
    let out = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("回過了"))
        .expect("close")
        .out;
    assert_eq!(out["reason"], json!("rev_mismatch"), "{out}");
    assert_eq!(
        (out["rev"].clone(), out["taken_rev"].clone()),
        (json!(4), json!(3))
    );
    assert_eq!(t.status(), "read");
}

#[test]
fn close_on_an_unread_dm_reads_closes_and_prints_the_text() {
    let t = T::new();
    t.put("delivered", json!({}));
    let out = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("回過了"))
        .expect("close")
        .out;
    assert_eq!(out["ok"], json!(true), "{out}");
    assert_eq!(out["id"], json!(ID));
    assert_eq!(out["text"], json!("請審 S3"));
    assert_eq!(t.status(), "done");
    // closing again: the dm is no longer open
    let again = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("再一次"))
        .expect("close")
        .out;
    assert_eq!(again["reason"], json!("transition"));
}

#[test]
fn close_needs_evidence_or_a_reason() {
    let t = T::new();
    t.put("read", json!({}));
    for (ev, why) in [(None, None), (Some(""), Some("  ")), (None, Some("\n"))] {
        let out = close(&t.s, &t.ctx(), SEAT, ID, ME, ev, why)
            .expect("close")
            .out;
        assert_eq!(out["reason"], json!("evidence"), "{ev:?} {why:?}");
    }
    assert_eq!(t.status(), "read");
}

#[test]
fn a_notice_is_archived_when_taken_and_gets_no_note() {
    let t = T::new();
    t.put("delivered", json!({"kind": "notice"}));
    let out = take(&t.s, &t.ctx(), SEAT, ID, ME, Some("不會寫"))
        .expect("take")
        .out;
    assert_eq!(out["ok"], json!(true), "{out}");
    assert_eq!(out["status"], json!("archived"));
    assert!(out.get("next").is_none());
    let it = t.item();
    assert_eq!(it["status"], json!("archived"));
    assert!(it.get("taken_rev").is_none() && it.get("wait").is_none());
    let close_out = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("x"))
        .expect("close")
        .out;
    assert_eq!(
        close_out["reason"],
        json!("transition"),
        "archived is closed already"
    );
}

#[test]
fn wait_by_the_seat_or_its_agent_and_clearing_it() {
    let t = T::new();
    t.put("read", json!({}));
    let out = set_wait(
        &t.s,
        &t.ctx(),
        SEAT,
        ID,
        "agent:aaaaa/tidy-1",
        Some("等 519d0"),
    )
    .expect("wait")
    .out;
    assert_eq!(
        out,
        json!({"ok": true, "reason": "ok", "id": ID, "wait": "等 519d0"})
    );
    assert_eq!(t.item()["wait"]["text"], json!("等 519d0"));
    let out = set_wait(&t.s, &t.ctx(), SEAT, ID, ME, Some(" \u{200b} "))
        .expect("clear")
        .out;
    assert_eq!(out["wait"], Value::Null);
    assert_eq!(t.item()["wait"], Value::Null);
    let bad = set_wait(&t.s, &t.ctx(), SEAT, ID, "agent:bbbbb/x", Some("x"))
        .expect("other agent")
        .out;
    assert_eq!(bad["reason"], json!("actor"));
    let audit = std::fs::read_to_string(t.s.seat_dir(SEAT).expect("dir").join("audit.jsonl"))
        .expect("audit");
    assert_eq!(audit.matches("\"to\": \"wait\"").count(), 2, "{audit}");
    // only an in-progress dm takes a wait
    t.put("delivered", json!({}));
    let out = set_wait(&t.s, &t.ctx(), SEAT, ID, ME, Some("x"))
        .expect("wait")
        .out;
    assert_eq!(out["reason"], json!("transition"));
}

/// 1.x writes the note from the copy it read before the lock; a close in between comes
/// back as `read`. Here notes are written from a fresh read under the transition lock.
#[test]
fn a_note_never_undoes_a_close() {
    for _ in 0..20 {
        let t = Arc::new(T::new());
        t.put("read", json!({}));
        let gate = Arc::new(Barrier::new(2));
        let (t1, g1) = (Arc::clone(&t), Arc::clone(&gate));
        let waiter = std::thread::spawn(move || {
            g1.wait();
            for i in 0..40 {
                let _ = set_wait(&t1.s, &t1.ctx(), SEAT, ID, ME, Some(&format!("等 {i}")));
            }
        });
        let (t2, g2) = (Arc::clone(&t), Arc::clone(&gate));
        let closer = std::thread::spawn(move || {
            g2.wait();
            close(&t2.s, &t2.ctx(), SEAT, ID, ME, None, Some("回過了"))
                .expect("close")
                .out
        });
        waiter.join().expect("join");
        let out = closer.join().expect("join");
        assert_eq!(out["ok"], json!(true), "{out}");
        assert_eq!(t.status(), "done", "a note wrote the close away");
    }
}

/// A global dm read by take makes a receipt for its outside sender; it comes back to the
/// caller to send after the lock (1.x _global_delivery_for_transition).
#[test]
fn taking_a_global_dm_hands_back_its_read_receipt() {
    let t = T::new();
    t.put("delivered", json!({"global": true}));
    let a = take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take");
    assert_eq!(a.out["ok"], json!(true), "{}", a.out);
    assert_eq!(a.receipts.len(), 1);
    assert!(a.receipts[0].receipt.is_some());
    // a plain dm makes none
    let t = T::new();
    t.put("delivered", json!({}));
    let a = take(&t.s, &t.ctx(), SEAT, ID, ME, None).expect("take");
    assert!(a.receipts.is_empty());
    // closing a global dm straight from delivered: one receipt for the read
    let t = T::new();
    t.put("delivered", json!({"global": true}));
    let a = close(&t.s, &t.ctx(), SEAT, ID, ME, None, Some("回過了")).expect("close");
    assert_eq!(a.out["ok"], json!(true), "{}", a.out);
    assert!(!a.receipts.is_empty());
}

/// A seat with no queue folder: not_found, and no empty folder is left behind for
/// `seats()` (or 1.x iter_queue_seats) to count.
#[test]
fn a_seat_without_a_queue_folder_is_not_found_and_not_made() {
    let t = T::new();
    for out in [
        take(&t.s, &t.ctx(), "ccccc", ID, "seat:ccccc", None)
            .expect("take")
            .out,
        close(&t.s, &t.ctx(), "ccccc", ID, "seat:ccccc", None, Some("x"))
            .expect("close")
            .out,
        set_wait(&t.s, &t.ctx(), "ccccc", ID, "seat:ccccc", Some("x"))
            .expect("wait")
            .out,
    ] {
        assert_eq!(out["reason"], json!("not_found"), "{out}");
    }
    assert!(!t.s.seat_dir("ccccc").expect("dir").exists());
    assert!(t.s.seats().is_empty());
}
