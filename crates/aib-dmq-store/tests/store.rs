//! aib-dmq-store on a temp queue root: a whole move end to end, two writers racing the
//! same dm, the lock order enforced across seats, the folder lock really excluding a
//! second opener, order.json and the source rev.

use std::sync::{Arc, Barrier};

use aib_dmq::lock::Level;
use aib_dmq::rev::IfRev;
use aib_dmq::spec::{Reason, Status, ITEM_SCHEMA};
use aib_dmq_store::locks::lock;
use aib_dmq_store::store::{Caller, Move};
use aib_dmq_store::{ItemRead, LockError, Store, StoreError};
use serde_json::{json, Map, Value};

const SEAT: &str = "aaaaa";
const ID: &str = "q_00000000000000a1";

fn store() -> (tempfile::TempDir, Store) {
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    (d, s)
}

fn item(status: &str) -> Map<String, Value> {
    json!({"schema": ITEM_SCHEMA, "id": ID, "seat": SEAT, "msg": "x", "status": status,
           "delivered_at": 1, "rev": 3, "sender": "agent:bbbbb"})
    .as_object()
    .cloned()
    .unwrap_or_default()
}

fn mv<'a>(to: Status, actor: &'a str, why: Option<&'a str>, rev: IfRev) -> Move<'a> {
    Move {
        seat: SEAT,
        id: ID,
        to,
        actor: Some(actor),
        why,
        evidence: None,
        if_rev: rev,
        no_rev_check: false,
        expect_status: None,
        caller_is_seat: false,
        now: 1000,
    }
}

fn status(s: &Store) -> String {
    match s.read_item(SEAT, ID).expect("read") {
        ItemRead::Item { item, .. } => item["status"].as_str().unwrap_or("").to_string(),
        other => format!("{other:?}"),
    }
}

#[test]
fn a_move_writes_the_item_leaves_the_order_and_audits() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("queued")).expect("write");
    s.order_append(SEAT, ID).expect("append");
    assert_eq!(s.order_ids(SEAT).expect("ids"), vec![ID.to_string()]);
    let rev0 = s.source_rev(SEAT);
    let out = s
        .transition(
            &mv(Status::Read, "seat:aaaaa", None, IfRev::Value(3)),
            &Caller::default(),
        )
        .expect("move");
    assert_eq!(out.reason, Reason::Ok);
    assert_eq!(status(&s), "read");
    assert!(
        s.order_ids(SEAT).expect("ids").is_empty(),
        "read leaves order.json"
    );
    assert!(s.source_rev(SEAT) > rev0, "the source rev goes up");
    // a refused move is audited too and changes nothing
    let bad = s
        .transition(
            &mv(Status::Done, "seat:aaaaa", None, IfRev::Value(3)),
            &Caller::default(),
        )
        .expect("move");
    assert_eq!(bad.reason, Reason::Evidence);
    assert_eq!(status(&s), "read");
    let audit =
        std::fs::read_to_string(s.seat_dir(SEAT).expect("dir").join("audit.jsonl")).expect("audit");
    let rows: Vec<Value> = audit
        .lines()
        .map(|l| serde_json::from_str(l).expect("json line"))
        .collect();
    assert_eq!(rows.len(), 2);
    assert_eq!(
        (rows[0]["ok"].clone(), rows[0]["from"].clone()),
        (json!(true), json!("queued"))
    );
    assert_eq!(
        (rows[1]["ok"].clone(), rows[1]["reason"].clone()),
        (json!(false), json!("evidence"))
    );
}

#[test]
fn no_rev_and_a_wrong_rev_are_refused() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    let c = Caller::default();
    assert_eq!(
        s.transition(&mv(Status::Read, "seat:aaaaa", None, IfRev::Absent), &c)
            .expect("m")
            .reason,
        Reason::NoRev
    );
    let r = s
        .transition(&mv(Status::Read, "seat:aaaaa", None, IfRev::Value(4)), &c)
        .expect("m");
    assert_eq!((r.reason, r.rev), (Reason::RevMismatch, Some(3)));
    assert_eq!(status(&s), "delivered");
}

#[test]
fn two_closers_race_one_wins() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("read")).expect("write");
    let s = Arc::new(s);
    let gate = Arc::new(Barrier::new(8));
    let handles: Vec<_> = (0..8)
        .map(|_| {
            let (s, gate) = (Arc::clone(&s), Arc::clone(&gate));
            std::thread::spawn(move || {
                gate.wait();
                s.transition(
                    &mv(Status::Done, "seat:aaaaa", Some("回過了"), IfRev::Value(3)),
                    &Caller::default(),
                )
                .map(|m| m.reason)
            })
        })
        .collect();
    let got: Vec<Reason> = handles
        .into_iter()
        .map(|h| h.join().expect("join").expect("move"))
        .collect();
    assert_eq!(
        got.iter().filter(|r| **r == Reason::Ok).count(),
        1,
        "{got:?}"
    );
    assert!(
        got.iter()
            .all(|r| matches!(r, Reason::Ok | Reason::Transition)),
        "{got:?}"
    );
    assert_eq!(status(&s), "done");
}

#[test]
fn holding_one_seat_may_not_lock_another() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    s.write_item("bbbbb", &{
        let mut m = item("delivered");
        m.insert("seat".into(), json!("bbbbb"));
        m
    })
    .expect("write b");
    let dir_b = s.seat_dir("bbbbb").expect("dir");
    let _held = lock(
        &dir_b,
        "bbbbb",
        Level::Transition,
        std::time::Duration::from_secs(1),
    )
    .expect("lock b");
    let r = s.transition(
        &mv(Status::Read, "seat:aaaaa", None, IfRev::Value(3)),
        &Caller::default(),
    );
    assert!(
        matches!(r, Err(StoreError::Lock(LockError::Order(_)))),
        "{r:?}"
    );
    assert_eq!(status(&s), "delivered", "nothing changed");
}

#[test]
fn a_move_inside_the_same_seats_transition_lock_reenters() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    let dir = s.seat_dir(SEAT).expect("dir");
    let _held = lock(
        &dir,
        SEAT,
        Level::Transition,
        std::time::Duration::from_secs(1),
    )
    .expect("lock");
    let r = s
        .transition(
            &mv(Status::Read, "seat:aaaaa", None, IfRev::Value(3)),
            &Caller::default(),
        )
        .expect("move");
    assert_eq!(r.reason, Reason::Ok);
}

#[test]
fn the_folder_lock_keeps_a_second_opener_out() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    let dir = s.seat_dir(SEAT).expect("dir");
    let _held = lock(
        &dir,
        SEAT,
        Level::Directory,
        std::time::Duration::from_secs(1),
    )
    .expect("lock");
    // another thread (its own order stack) must time out on the same folder
    let dir2 = dir.clone();
    let r = std::thread::spawn(move || {
        lock(
            &dir2,
            SEAT,
            Level::Directory,
            std::time::Duration::from_millis(200),
        )
        .map(|_| ())
    })
    .join()
    .expect("join");
    assert!(matches!(r, Err(LockError::Timeout { .. })), "{r:?}");
    // the lock target: the folder itself on Unix (Python atomic_json flocks it), a fixed file on Windows
    if cfg!(windows) {
        assert!(dir.join(aib_dmq_store::locks::DIR_LOCK_FILE).is_file());
    } else {
        assert!(!dir.join(aib_dmq_store::locks::DIR_LOCK_FILE).exists());
    }
}

#[test]
fn a_timed_out_transition_lock_is_lock_timeout_and_changes_nothing() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    let dir = s.seat_dir(SEAT).expect("dir");
    let (tx, rx) = std::sync::mpsc::channel();
    let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
    let holder = std::thread::spawn(move || {
        let _g = lock(
            &dir,
            SEAT,
            Level::Transition,
            std::time::Duration::from_secs(1),
        )
        .expect("lock");
        tx.send(()).expect("send");
        let _ = done_rx.recv();
    });
    rx.recv().expect("held");
    // the store waits TRANSITION_LOCK_TIMEOUT_MS (10 s) like 1.x
    let r = s
        .transition(
            &mv(Status::Read, "seat:aaaaa", None, IfRev::Value(3)),
            &Caller::default(),
        )
        .expect("move");
    done_tx.send(()).expect("release");
    holder.join().expect("join");
    assert_eq!(r.reason, Reason::LockTimeout);
    assert_eq!(r.reason.exit_code(), 10);
    assert_eq!(status(&s), "delivered");
}

#[test]
fn bad_seats_and_ids_never_touch_the_disk() {
    let (d, s) = store();
    assert!(matches!(s.seat_dir("../x"), Err(StoreError::BadSeat(_))));
    assert!(matches!(
        s.item_path(SEAT, "../../etc"),
        Err(StoreError::BadId(_))
    ));
    assert!(!d.path().join("state").exists());
}

/// The audit line in the frozen Python's bytes: power_tasks.py:173 looks for
/// `"to": "done"` in audit.jsonl, so separators and key order are part of the contract.
#[test]
fn a_done_audit_line_is_byte_for_byte_the_python_line() {
    let (_d, s) = store();
    s.write_item(SEAT, &item("read")).expect("write");
    let m = mv(Status::Done, "seat:aaaaa", Some("回過了"), IfRev::Value(3));
    let out = s.transition(&m, &Caller::default()).expect("move");
    assert_eq!(out.reason, Reason::Ok, "{}", out.message);
    let audit =
        std::fs::read_to_string(s.seat_dir(SEAT).expect("dir").join("audit.jsonl")).expect("audit");
    let line = audit.lines().last().expect("a line");
    let row: Value = serde_json::from_str(line).expect("json");
    let ppid = row["caller"]["ppid"].clone();
    let ev = row["evidence"].clone();
    assert!(
        row.as_object().expect("obj").contains_key("evidence"),
        "{line}"
    );
    // json.dumps(rec, ensure_ascii=False, sort_keys=True) for the same record
    let want = format!(
        "{{\"actor\": \"seat:aaaaa\", \"caller\": {{\"instance\": null, \"ppid\": {ppid}, \"tmux_pane\": null}}, \
         \"evidence\": {ev}, \"from\": \"read\", \"id\": \"{ID}\", \"ok\": true, \"reason\": \"ok\", \"rev\": 3, \
         \"to\": \"done\", \"ts\": 1000, \"why\": \"回過了\"}}"
    );
    assert_eq!(line, want);
    assert!(line.contains("\"to\": \"done\""));
    // the item file is in the same form
    let raw = std::fs::read_to_string(s.item_path(SEAT, ID).expect("path")).expect("item");
    assert!(raw.contains("\"status\": \"done\""), "{raw}");
    assert!(raw.ends_with("}\n"));
}

/// A write on Unix ends with the folder synced: Durable, not NotSynced.
#[cfg(unix)]
#[test]
fn a_plain_write_is_durable() {
    let (_d, s) = store();
    assert!(matches!(
        s.write_item(SEAT, &item("queued")).expect("write"),
        aib_dmq_store::Written::Durable
    ));
}

/// The item is written, then order.json cannot be: the move still counts and is audited;
/// the order failure comes back beside it instead of "nothing changed".
#[cfg(target_os = "macos")]
#[test]
fn an_order_failure_after_the_item_write_still_reports_the_move() {
    use std::process::Command;
    let (_d, s) = store();
    s.write_item(SEAT, &item("delivered")).expect("write");
    s.order_append(SEAT, ID).expect("append");
    let order = s.seat_dir(SEAT).expect("dir").join("order.json");
    // an immutable order.json: readable, but rename over it is refused (EPERM)
    let st = Command::new("chflags")
        .arg("uchg")
        .arg(&order)
        .status()
        .expect("chflags");
    assert!(st.success());
    let out = s.transition(
        &mv(Status::Read, "seat:aaaaa", None, IfRev::Value(3)),
        &Caller::default(),
    );
    let _ = Command::new("chflags").arg("nouchg").arg(&order).status();
    let out = out.expect("the move is reported, not an error");
    assert_eq!(out.reason, Reason::Ok);
    assert!(out.order_error.is_some(), "the order failure is kept");
    assert_eq!(status(&s), "read");
    let audit =
        std::fs::read_to_string(s.seat_dir(SEAT).expect("dir").join("audit.jsonl")).expect("audit");
    assert!(audit.lines().last().expect("line").contains("\"ok\": true"));
}
