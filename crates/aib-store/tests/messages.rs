#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! The message library file (S89-talk-2a): its own `user_version`, pages of a thread.

use aib_store::{Inserted, MessageDb, MessageRow, Updated, MESSAGES_SCHEMA};
use serde_json::json;

fn row(n: i64, thread: &str) -> MessageRow {
    let mut item = serde_json::Map::new();
    item.insert("status".into(), json!("delivered"));
    MessageRow {
        message_id: format!("q_{n:016x}"),
        thread_id: thread.into(),
        from_key: "seat:a".into(),
        to_key: "human:0123456789abcdef".into(),
        created: format!("t{n}"),
        created_unix: 1_000 + n,
        updated: format!("t{n}"),
        rev: 1,
        item,
        body: Some(format!("text {n}")),
        body_sha256: "0".repeat(64),
        len: 6,
        idem_owner: None,
        idem_key: None,
        journaled: false,
    }
}

/// A file a newer aibd wrote is refused; this build's file opens again with its rows.
#[test]
fn a_newer_library_is_refused_and_this_builds_opens_again() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("messages.sqlite");
    {
        let db = MessageDb::open(&p).unwrap();
        assert_eq!(db.insert(row(1, "x~y")).unwrap(), Inserted::New);
    }
    let db = MessageDb::open(&p).unwrap();
    assert!(db.get("q_0000000000000001").unwrap().is_some());
    drop(db);
    let c = rusqlite::Connection::open(&p).unwrap();
    c.pragma_update(None, "user_version", MESSAGES_SCHEMA + 1)
        .unwrap();
    drop(c);
    assert!(MessageDb::open(&p).is_err(), "a newer file is not read");
}

/// A test_pagination: the newest page first, `more` says older rows exist, the next page
/// starts before the oldest row of this one and the two pages meet without a gap.
#[test]
fn an_inbox_page_continues_where_the_last_one_ended() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    for n in 1..=5 {
        db.insert(row(n, "x~y")).unwrap();
    }
    db.insert(row(9, "other")).unwrap();
    let (page, more) = db.thread_page("x~y", None, 2).unwrap();
    let ids: Vec<i64> = page.iter().map(|r| r.created_unix - 1_000).collect();
    assert_eq!(ids, [4, 5]);
    assert!(more);
    let (page, more) = db.thread_page("x~y", Some(&page[0].message_id), 2).unwrap();
    let ids: Vec<i64> = page.iter().map(|r| r.created_unix - 1_000).collect();
    assert_eq!(ids, [2, 3]);
    assert!(more);
    let (page, more) = db.thread_page("x~y", Some(&page[0].message_id), 2).unwrap();
    assert_eq!(page.len(), 1);
    assert!(!more);
}

/// A write names the revision it read; an old one changes nothing and gets the row back.
/// The same idempotency key of the same owner is the first row.
#[test]
fn a_stale_revision_writes_nothing_and_a_key_answers_once() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let mut r = row(1, "x~y");
    r.idem_owner = Some("human:0123456789abcdef".into());
    r.idem_key = Some("k".into());
    db.insert(r.clone()).unwrap();
    let mut again = row(2, "x~y");
    again.idem_owner = r.idem_owner.clone();
    again.idem_key = r.idem_key.clone();
    match db.insert(again).unwrap() {
        Inserted::Existing(e) => assert_eq!(e.message_id, r.message_id),
        other => panic!("{other:?}"),
    }
    let mut item = r.item.clone();
    item.insert("status".into(), json!("read"));
    match db.update(&r.message_id, 1, item.clone(), "t2").unwrap() {
        Updated::Done(a) => assert_eq!((a.rev, a.journaled), (2, false)),
        other => panic!("{other:?}"),
    }
    match db.update(&r.message_id, 1, item, "t3").unwrap() {
        Updated::Stale(now) => assert_eq!(now.rev, 2),
        other => panic!("{other:?}"),
    }
}

/// A seat's inbox: what is open to it (queued / delivered / read), oldest first; done, failed
/// and other receivers' rows are left out (b4297 B1dm-S3c).
#[test]
fn the_open_rows_to_a_receiver_are_its_inbox_oldest_first() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let to = "human:0123456789abcdef";
    for (n, status, failed) in [
        (3, "read", false),
        (1, "queued", false),
        (2, "delivered", false),
        (4, "done", false),
        (5, "queued", true),
    ] {
        let mut r = row(n, "x~y");
        r.item.insert("status".into(), json!(status));
        if failed {
            r.item.insert("failed".into(), json!(true));
        }
        db.insert(r).unwrap();
    }
    let mut other = row(6, "x~z");
    other.to_key = "seat:b".into();
    db.insert(other).unwrap();
    let ids: Vec<i64> = db
        .open_to(to, 100)
        .unwrap()
        .iter()
        .map(|r| r.created_unix - 1_000)
        .collect();
    assert_eq!(ids, vec![1, 2, 3], "reason: inbox_rows_wrong");
    assert_eq!(db.open_to(to, 1).unwrap().len(), 1, "reason: limit_ignored");
}

/// Rows written in one second come back in the order they were written, whatever their ids
/// (ids are random: by id, a later message could jump the line).
#[test]
fn open_rows_of_one_second_keep_the_order_they_were_written_in() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("m.sqlite")).unwrap();
    for (n, id) in [
        (1, "q_ffff000000000000"),
        (2, "q_0000000000000001"),
        (3, "q_8000000000000000"),
    ] {
        let mut r = row(n, "x~y");
        r.message_id = id.into();
        r.created_unix = 2_000;
        r.body = Some(format!("text {n}"));
        db.insert(r).unwrap();
    }
    let got: Vec<String> = db
        .open_to("human:0123456789abcdef", 100)
        .unwrap()
        .into_iter()
        .filter_map(|r| r.body)
        .collect();
    assert_eq!(got, ["text 1", "text 2", "text 3"], "reason: order_by_id");
}

/// Unread and open decides per sender, for the seat list and the contact center: read,
/// done and another reader's rows do not count.
#[test]
fn unread_counts_are_per_sender_and_only_for_the_reader() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let me = "human:0123456789abcdef";
    for (n, from, status, kind, to) in [
        (1, "seat:a", "delivered", "say", me),
        (2, "seat:a", "queued", "decide", me),
        (3, "seat:a", "read", "decide", me),
        (4, "seat:a", "done", "decide", me),
        (5, "seat:b", "read", "say", me),
        (6, "seat:a", "delivered", "say", "seat:c"),
    ] {
        let mut r = row(n, "x~y");
        r.from_key = from.into();
        r.to_key = to.into();
        r.item.insert("status".into(), json!(status));
        r.item.insert("talk_kind".into(), json!(kind));
        db.insert(r).unwrap();
    }
    let got: Vec<(String, i64, i64)> = db
        .unread_by_sender(me)
        .unwrap()
        .into_iter()
        .map(|u| (u.from_key, u.unread, u.decide_open))
        .collect();
    assert_eq!(
        got,
        [("seat:a".to_string(), 2, 2), ("seat:b".to_string(), 0, 0)]
    );
}

/// Threads of many: the tables come with the file and are only added (an older build that
/// opens it sees user_version 1); a post and its deliveries go in together, the same key
/// twice at the same time writes one post, and the timeline holds posts only.
#[test]
fn a_post_with_one_key_is_written_once_even_when_two_arrive_together() {
    use aib_store::{Member, ThreadRow};
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("messages.sqlite");
    let db = std::sync::Arc::new(MessageDb::open(&path).unwrap());
    let t = "t_00000000000000aa";
    assert_eq!(
        db.thread_open(
            ThreadRow {
                thread_id: t.into(),
                node: Some("N1".into()),
                title: "x".into(),
                opened_by: "human:0123456789abcdef".into(),
                created: "2027-01-01T00:00:00Z".into(),
                created_unix: 1,
                last_unix: 1,
            },
            vec![Member {
                member: "seat:a".into(),
                role: "author".into()
            }],
        )
        .unwrap(),
        aib_store::Opened::New
    );
    let post = |n: i64| {
        let mut p = row(n, t);
        p.to_key = t.into();
        p.idem_owner = Some("human:0123456789abcdef".into());
        p.idem_key = Some("k".into());
        let mut d = row(n + 100, t);
        d.to_key = "seat:a".into();
        d.item.insert("post_id".into(), json!(p.message_id));
        (p, d)
    };
    let hs: Vec<_> = [1, 2]
        .into_iter()
        .map(|n| {
            let db = db.clone();
            std::thread::spawn(move || {
                let (p, d) = post(n);
                db.post_insert(p, vec![d]).unwrap()
            })
        })
        .collect();
    let got: Vec<_> = hs.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(
        got.iter()
            .filter(|g| matches!(g, aib_store::Inserted::New))
            .count(),
        1
    );
    let (posts, more) = db.posts_page(t, None, 10).unwrap();
    assert_eq!((posts.len(), more), (1, false));
    let ds = db.deliveries_of(t, &[posts[0].message_id.clone()]).unwrap();
    assert_eq!(ds.len(), 1);
    assert_eq!(ds[0].to_key, "seat:a");
    assert_eq!(
        db.threads_list(Some("N1"), Some("seat:a"), 10)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(db.threads_list(None, Some("seat:b"), 10).unwrap().len(), 0);
    drop(db);
    let v: i64 = rusqlite::Connection::open(&path)
        .unwrap()
        .query_row("PRAGMA user_version", [], |r| r.get(0))
        .unwrap();
    assert_eq!(v, aib_store::MESSAGES_SCHEMA);
    assert_eq!(
        MessageDb::open(&path)
            .unwrap()
            .thread_members(t)
            .unwrap()
            .len(),
        1
    );
}

/// The person's keys made one (519d0 11-18): rows, two-key threads (sorted again), the read
/// marks and the threads opened move from old page keys to the person's key; kept keys and
/// seats stay; two old keys that used one idempotency key keep both rows.
#[test]
fn old_page_keys_merge_into_the_persons_key() {
    use aib_store::{Member, ThreadRow};
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let (old1, old2, me, user) = (
        "human:00000000000000a1",
        "human:00000000000000a2",
        "human:ffffffffffffff00",
        "human:00000000000000b0",
    );
    for (n, from, k) in [(1, old1, Some("k")), (2, old2, Some("k")), (3, user, None)] {
        let mut r = row(n, &format!("{from}~seat:s"));
        r.from_key = from.into();
        r.to_key = "seat:s".into();
        r.item.insert("sender_seat".into(), json!(from));
        r.idem_owner = k.map(|_| from.to_string());
        r.idem_key = k.map(str::to_string);
        db.insert(r).unwrap();
    }
    db.set_read_mark(&format!("{old1}~seat:s"), old1, "q_1")
        .unwrap();
    db.thread_open(
        ThreadRow {
            thread_id: "t_00000000000000cc".into(),
            node: None,
            title: "x".into(),
            opened_by: old2.into(),
            created: "2027-01-01T00:00:00Z".into(),
            created_unix: 1,
            last_unix: 1,
        },
        vec![Member {
            member: "seat:s".into(),
            role: "author".into(),
        }],
    )
    .unwrap();
    let moved = db
        .merge_humans(vec![me.to_string(), user.to_string()], me.to_string())
        .unwrap();
    assert_eq!(moved, 2);
    let t = format!("{me}~seat:s");
    let (rows, _) = db.thread_page(&t, None, 10).unwrap();
    assert_eq!(
        rows.len(),
        2,
        "both old keys' rows are the person's thread now"
    );
    assert!(rows
        .iter()
        .all(|r| r.from_key == me && r.item["sender_seat"] == me));
    assert_eq!(
        rows.iter().filter(|r| r.idem_key.is_some()).count(),
        1,
        "the second row with the same key keeps its text, not the key"
    );
    assert_eq!(db.read_mark(&t, me).unwrap().as_deref(), Some("q_1"));
    assert_eq!(
        db.thread_page(&format!("{user}~seat:s"), None, 10)
            .unwrap()
            .0
            .len(),
        1
    );
    assert_eq!(
        db.thread_get("t_00000000000000cc")
            .unwrap()
            .unwrap()
            .opened_by,
        me
    );
}

/// An item of the tree has one thread: opening it again gives that thread back with the new
/// members added (an old member keeps its role); a free thread is always new.
#[test]
fn a_tree_item_has_one_thread_and_opening_it_again_adds_members() {
    use aib_store::{Member, Opened, ThreadRow};
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let t = |id: &str, node: Option<&str>| ThreadRow {
        thread_id: id.into(),
        node: node.map(str::to_string),
        title: "x".into(),
        opened_by: "human:0123456789abcdef".into(),
        created: "2027-01-01T00:00:00Z".into(),
        created_unix: 1,
        last_unix: 1,
    };
    let m = |k: &str, r: &str| Member {
        member: k.into(),
        role: r.into(),
    };
    assert_eq!(
        db.thread_open(
            t("t_0000000000000001", Some("N1")),
            vec![m("seat:a", "author")]
        )
        .unwrap(),
        Opened::New
    );
    match db
        .thread_open(
            t("t_0000000000000002", Some("N1")),
            vec![m("seat:a", "observer"), m("seat:b", "reviewer")],
        )
        .unwrap()
    {
        Opened::Existing(r) => assert_eq!(r.thread_id, "t_0000000000000001"),
        other => panic!("{other:?}"),
    }
    assert_eq!(
        db.thread_members("t_0000000000000001").unwrap(),
        vec![m("seat:a", "author"), m("seat:b", "reviewer")]
    );
    assert!(db.thread_get("t_0000000000000002").unwrap().is_none());
    for id in ["t_0000000000000003", "t_0000000000000004"] {
        assert_eq!(db.thread_open(t(id, None), vec![]).unwrap(), Opened::New);
    }
}

/// Posts written in one second keep the order they were written in (not their random ids),
/// on a page and across pages.
#[test]
fn posts_in_one_second_keep_the_order_written() {
    let dir = tempfile::tempdir().unwrap();
    let db = MessageDb::open(&dir.path().join("messages.sqlite")).unwrap();
    let t = "t_00000000000000dd";
    // ids that sort the other way round from the order written
    for n in [9, 3, 7, 1] {
        let mut p = row(n, t);
        p.to_key = t.into();
        p.created_unix = 5_000;
        db.post_insert(p, vec![]).unwrap();
    }
    let ids = |v: Vec<MessageRow>| v.into_iter().map(|r| r.message_id).collect::<Vec<_>>();
    let (all, _) = db.posts_page(t, None, 10).unwrap();
    let want: Vec<String> = [9, 3, 7, 1].iter().map(|n| format!("q_{n:016x}")).collect();
    assert_eq!(ids(all), want);
    let (last2, more) = db.posts_page(t, None, 2).unwrap();
    assert!(more);
    assert_eq!(ids(last2), want[2..]);
    let (first2, more) = db.posts_page(t, Some(&want[2]), 2).unwrap();
    assert!(!more);
    assert_eq!(ids(first2), want[..2]);
}
