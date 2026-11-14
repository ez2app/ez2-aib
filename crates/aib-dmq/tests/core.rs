//! Rules the transitions fixture does not reach: commit and reply-id evidence, evidence
//! inside a sentence, the rev parser, actor shapes, normalisation, exit codes, the lock
//! order. Expected values follow the frozen 1.x Python (dm_queue.py), cited per test.

use std::cell::RefCell;

use aib_dmq::evidence::{classify, is_pr_url, kind_of, pr_url_in, Kind, Probe};
use aib_dmq::lock::{Enter, Held, Level, OrderError};
use aib_dmq::norm;
use aib_dmq::rev::{item_rev, rev_check, IfRev, Presence};
use aib_dmq::spec::{Reason, Status};
use aib_dmq::transition::{decide, Decision, Request};
use aib_dmq::{parse_actor, ActorClass};
use serde_json::{json, Map, Value};

/// A probe with fixed answers that records every question asked.
#[derive(Default)]
struct Fake {
    files: Vec<&'static str>,
    commits: Vec<&'static str>,
    replies: Vec<(&'static str, &'static str)>,
    asked: RefCell<Vec<String>>,
}

impl Probe for Fake {
    fn is_file(&self, t: &str) -> bool {
        self.asked.borrow_mut().push(format!("file {t}"));
        self.files.contains(&t)
    }
    fn commit_exists(&self, s: &str) -> bool {
        self.asked.borrow_mut().push(format!("commit {s}"));
        self.commits.contains(&s)
    }
    fn reply_sent_by(&self, seat: &str, id: &str) -> bool {
        self.asked.borrow_mut().push(format!("dm {id}"));
        self.replies.contains(&(seat, id))
    }
}

const SHA: &str = "0ea4432b";
const ID: &str = "q_0123456789abcdef";

#[test]
fn whole_string_evidence_kinds() {
    let p = Fake {
        files: vec!["/tmp/x.md"],
        commits: vec![SHA],
        replies: vec![("aaaaa", ID)],
        ..Fake::default()
    };
    assert_eq!(kind_of("aaaaa", "/tmp/x.md", &p), Some(Kind::File));
    assert_eq!(kind_of("aaaaa", SHA, &p), Some(Kind::Commit));
    assert_eq!(kind_of("aaaaa", ID, &p), Some(Kind::Dm));
    // a reply id another seat sent is not this seat's evidence, and does not fall through to a file
    assert_eq!(kind_of("bbbbb", ID, &p), None);
    assert!(!p.asked.borrow().iter().any(|q| q == &format!("file {ID}")));
    // an unknown sha still gets the file check (a file may be named like a sha)
    assert_eq!(kind_of("aaaaa", "deadbee", &p), None);
    assert!(p.asked.borrow().iter().any(|q| q == "file deadbee"));
    assert_eq!(kind_of("aaaaa", "  ", &p), None);
}

#[test]
fn pr_urls_take_only_the_ez2app_shapes() {
    for ok in [
        "https://github.com/ez2app/ez2-aib/pull/1",
        "https://github.com/ez2app/ez2-aib/pull/123456789/",
        "https://github.com/ez2app/a.b_c-d/commit/0ea4432",
    ] {
        assert!(is_pr_url(ok), "{ok}");
    }
    for no in [
        "https://github.com/ez2app/ez2-aib/pull/1234567890",
        "https://github.com/ez2app/ez2-aib/pull/",
        "https://github.com/ez2app/./pull/1",
        "https://github.com/ez2app/a..b/pull/1",
        "https://github.com/ez2app/ez2-aib/commit/0EA4432",
        "https://github.com/ez2app/ez2-aib/commit/0ea443",
        "https://github.com/other/ez2-aib/pull/1",
        "http://github.com/ez2app/ez2-aib/pull/1",
        "https://github.com/ez2app/ez2-aib/pull/1#x",
        "#741",
    ] {
        assert!(!is_pr_url(no), "{no}");
    }
}

#[test]
fn a_pr_url_inside_a_sentence() {
    let u = "https://github.com/ez2app/ez2-aib/pull/741";
    assert_eq!(pr_url_in(&format!("合了 {u} 見審查")), Some(u));
    assert_eq!(pr_url_in(&format!("({u})")), Some(u));
    assert_eq!(pr_url_in(&format!("{u}。")), Some(u));
    // glued to a word, a slash or "=" before it: another URL's tail, not taken
    assert_eq!(pr_url_in(&format!("x{u}")), None);
    assert_eq!(pr_url_in(&format!("/{u}")), None);
    assert_eq!(pr_url_in(&format!("?u={u}")), None);
    // followed by a letter or more digits
    assert_eq!(pr_url_in(&format!("{u}a")), None);
    assert_eq!(pr_url_in(&format!("{u}_")), None);
    // the first good one wins even after a bad one
    assert_eq!(pr_url_in(&format!("x{u} 然後 {u}")), Some(u));
    // a commit URL counts as well
    let c = "https://github.com/ez2app/ez2-aib/commit/0ea4432b";
    assert_eq!(pr_url_in(&format!("見 {c}")), Some(c));
}

#[test]
fn evidence_inside_a_sentence() {
    let p = Fake {
        files: vec!["/tmp/r.md"],
        commits: vec![SHA],
        replies: vec![("aaaaa", ID)],
        ..Fake::default()
    };
    let c = |t: &str| classify("aaaaa", Some(t), &p);
    // the whole string first
    assert_eq!(
        c(" /tmp/r.md "),
        (Some(Kind::File), Some("/tmp/r.md".into()))
    );
    // a PR URL is taken without asking anything
    let u = "https://github.com/ez2app/ez2-aib/pull/9";
    assert_eq!(c(&format!("看 {u} 了")), (Some(Kind::Pr), Some(u.into())));
    // then reply ids, then shas, then paths; trailing . and : stripped
    assert_eq!(c(&format!("已回 {ID}.")), (Some(Kind::Dm), Some(ID.into())));
    assert_eq!(
        c(&format!("修在 {SHA}: 見")),
        (Some(Kind::Commit), Some(SHA.into()))
    );
    assert_eq!(
        c("寫在 /tmp/r.md, 請看"),
        (Some(Kind::File), Some("/tmp/r.md".into()))
    );
    assert_eq!(
        c("寫在「/tmp/r.md」"),
        (Some(Kind::File), Some("/tmp/r.md".into()))
    );
    // "#N" and plain text are never evidence
    assert_eq!(c("見 #741"), (None, None));
    assert_eq!(c("看過了"), (None, None));
    assert_eq!(classify("aaaaa", None, &p), (None, None));
}

#[test]
fn at_most_four_candidates_are_checked() {
    let p = Fake {
        files: vec!["/tmp/r.md"],
        ..Fake::default()
    };
    let t = "aaaaaaa bbbbbbb ccccccc ddddddd /tmp/r.md";
    assert_eq!(classify("aaaaa", Some(t), &p), (None, None));
    let t = "aaaaaaa bbbbbbb ccccccc /tmp/r.md";
    assert_eq!(
        classify("aaaaa", Some(t), &p),
        (Some(Kind::File), Some("/tmp/r.md".into()))
    );
}

#[test]
fn rev_parsing_and_checks() {
    assert_eq!(IfRev::parse(None), IfRev::Absent);
    assert_eq!(IfRev::parse(Some(" 3 ")), IfRev::Value(3));
    assert_eq!(IfRev::parse(Some("+3")), IfRev::Value(3));
    assert_eq!(IfRev::parse(Some("1_0")), IfRev::Value(10));
    for g in ["", "x", "1__0", "_1", "1_", "3.0", "３"] {
        assert_eq!(IfRev::parse(Some(g)), IfRev::Garbage, "{g:?}");
    }
    assert_eq!(item_rev(&json!({})), 0);
    assert_eq!(item_rev(&json!({"rev": null})), 0);
    assert_eq!(item_rev(&json!({"rev": 4})), 4);
    assert_eq!(item_rev(&json!({"rev": "5"})), 5);
    assert_eq!(item_rev(&json!({"rev": "x"})), 0);
    assert_eq!(item_rev(&json!({"rev": 2.9})), 2);
    let live = Presence::Live { rev: 4 };
    assert_eq!(
        rev_check(live, IfRev::Absent, false),
        (false, Reason::NoRev, Some(4))
    );
    assert_eq!(
        rev_check(live, IfRev::Absent, true),
        (true, Reason::Ok, None)
    );
    assert_eq!(
        rev_check(Presence::Missing, IfRev::Absent, true),
        (true, Reason::Ok, None)
    );
    assert_eq!(
        rev_check(Presence::Tombstone, IfRev::Value(1), false),
        (false, Reason::NotFound, None)
    );
    assert_eq!(
        rev_check(live, IfRev::Garbage, false),
        (false, Reason::RevMismatch, Some(4))
    );
}

#[test]
fn actor_shapes() {
    let p = |s: &str| parse_actor(Some(s)).map(|a| (a.class, a.ident));
    assert_eq!(p(" system "), Some((ActorClass::System, None)));
    assert_eq!(
        p("seat: aaaaa "),
        Some((ActorClass::Seat, Some("aaaaa".into())))
    );
    assert_eq!(
        p("human:andy"),
        Some((ActorClass::Human, Some("andy".into())))
    );
    assert_eq!(
        p("sender:bbbbb"),
        Some((ActorClass::Sender, Some("bbbbb".into())))
    );
    assert_eq!(
        p("agent:aaaaa/sub-1.x_y"),
        Some((ActorClass::Agent, Some("aaaaa/sub-1.x_y".into())))
    );
    for bad in [
        "",
        "andy",
        "seat:",
        "robot:x",
        "agent:AAAAA/x",
        "agent:aaaaa/",
        "agent:aaaa/x",
        "agent:aaaaa/a b",
    ] {
        assert_eq!(p(bad), None, "{bad:?}");
    }
    assert_eq!(parse_actor(None), None);
}

fn delivered() -> Map<String, Value> {
    let (m, _) = norm::upgrade(
        json!({"schema": "aib-dm-queue-item/v2", "id": ID, "status": "delivered", "delivered_at": 1, "rev": 1})
            .as_object().expect("object")
    );
    m
}

fn req<'a>(to: Status, actor: &'a str) -> Request<'a> {
    Request {
        seat: "aaaaa",
        to,
        actor: Some(actor),
        why: None,
        evidence: None,
        evidence_ref: (None, None),
        expect_status: None,
        caller_is_seat: false,
        now: 100,
    }
}

#[test]
fn expected_status_and_actor_hint() {
    let it = delivered();
    let mut r = req(Status::Read, "seat:aaaaa");
    r.expect_status = Some(Status::Queued);
    assert_eq!(decide(&it, &r).reason(), Reason::Transition);
    r.expect_status = Some(Status::Delivered);
    assert_eq!(decide(&it, &r).reason(), Reason::Ok);
    match decide(&it, &req(Status::Read, "andy")) {
        Decision::Refuse {
            reason, message, ..
        } => {
            assert_eq!(reason, Reason::ActorMissing);
            assert!(
                message.contains("收到 andy") && message.contains("seat:aaaaa"),
                "{message}"
            );
        }
        Decision::Allow(_) => panic!("andy is not an actor"),
    }
    match decide(&it, &req(Status::Read, "  ")) {
        Decision::Refuse { message, .. } => assert_eq!(message, Reason::ActorMissing.message()),
        Decision::Allow(_) => panic!("blank is not an actor"),
    }
}

#[test]
fn done_with_a_commit_keeps_the_checked_part() {
    let it = delivered();
    let mut r = req(Status::Done, "seat:aaaaa");
    let ev = format!("修在 {SHA}");
    r.evidence = Some(&ev);
    r.evidence_ref = (Some(Kind::Commit), Some(SHA.into()));
    let Decision::Allow(o) = decide(&it, &r) else {
        panic!("commit evidence closes")
    };
    assert_eq!(o.item["done_kind"], json!("commit"));
    assert_eq!(o.item["done_evidence"], json!(ev));
    assert_eq!(o.item["done_ref"], json!(SHA));
    assert_eq!(o.item["read_at"], json!(100));
    // the same with only a why: closed by reason, why kept, evidence not
    let mut r = req(Status::Done, "seat:aaaaa");
    r.why = Some("  回過了 ");
    let Decision::Allow(o) = decide(&it, &r) else {
        panic!("a why closes")
    };
    assert_eq!(o.item["done_kind"], json!("reason"));
    assert_eq!(o.item["done_why"], json!("回過了"));
    assert_eq!(o.item["done_evidence"], Value::Null);
    assert!(!o.item.contains_key("done_ref"));
}

#[test]
fn an_agent_of_another_seat_is_refused() {
    let it = delivered();
    assert_eq!(
        decide(&it, &req(Status::Read, "agent:bbbbb/x")).reason(),
        Reason::Actor
    );
    assert_eq!(
        decide(&it, &req(Status::Read, "agent:aaaaa/x")).reason(),
        Reason::Ok
    );
}

#[test]
fn an_external_im_sender_counts_as_telegram() {
    let mut it = delivered();
    it.insert("sender_kind".into(), json!("external_im"));
    assert_eq!(
        decide(&it, &req(Status::Read, "seat:aaaaa")).reason(),
        Reason::Actor
    );
}

#[test]
fn exit_codes() {
    let rc: Vec<(Reason, i32)> = [
        Reason::Ok,
        Reason::NotFound,
        Reason::RevMismatch,
        Reason::NoRev,
        Reason::Actor,
        Reason::ActorMissing,
        Reason::Transition,
        Reason::ImRoomOnly,
        Reason::Evidence,
        Reason::LockTimeout,
    ]
    .iter()
    .map(|r| (*r, r.exit_code()))
    .collect();
    assert_eq!(
        rc,
        vec![
            (Reason::Ok, 0),
            (Reason::NotFound, 4),
            (Reason::RevMismatch, 5),
            (Reason::NoRev, 6),
            (Reason::Actor, 7),
            (Reason::ActorMissing, 7),
            (Reason::Transition, 8),
            (Reason::ImRoomOnly, 8),
            (Reason::Evidence, 9),
            (Reason::LockTimeout, 10),
        ]
    );
}

#[test]
fn normalisation() {
    assert_eq!(norm::kind(Some(&json!(" ORDER "))), "order");
    assert_eq!(norm::kind(Some(&json!("ack"))), "other");
    assert_eq!(norm::kind(None), "other");
    assert_eq!(norm::priority(Some(&json!("Urgent")), "normal"), "urgent");
    assert_eq!(norm::priority(Some(&json!(3)), "normal"), "normal");
    assert_eq!(norm::sender_kind(Some(&json!("ai"))), "seat");
    assert_eq!(norm::sender_kind(Some(&json!("boss"))), "unknown");
    assert_eq!(
        norm::sender_source(Some(&json!("PANE_PID_CHAIN"))),
        Some("pane_pid_chain")
    );
    assert_eq!(norm::origin(None, Some(" TEST "), "live"), "test");
    assert_eq!(
        norm::origin(Some(&json!("live")), Some("test"), "live"),
        "live"
    );
    assert_eq!(
        norm::thread(Some(&json!(" 1ced7/pr-604 "))),
        Some("1ced7/pr-604".into())
    );
    for bad in ["/x", "a/", "-a/x", "a/b/c", "a/b c"] {
        assert_eq!(norm::thread(Some(&json!(bad))), None, "{bad}");
    }
    assert!(norm::is_dispatch(
        &json!({"kind": "report", "kind_orig": "order"})
    ));
    assert!(!norm::is_dispatch(&json!({"kind": "report"})));
    // v1 upgrade: ai -> seat, sent_at from ts, unknown status reads queued
    let (u, legacy) = norm::upgrade(
        json!({"schema": "aib-dm-queue-item/v1", "sender_kind": "ai", "ts": 7, "status": "weird"})
            .as_object()
            .expect("object"),
    );
    assert!(legacy);
    assert_eq!(
        (
            u["sender_kind"].clone(),
            u["sent_at"].clone(),
            u["status"].clone()
        ),
        (json!("seat"), json!(7), json!("queued"))
    );
    assert_eq!(u["attempts"], json!(0));
}

#[test]
fn lock_order() {
    let mut h = Held::new();
    assert_eq!(h.enter("aaaaa", Level::Deliver), Ok(Enter::Acquire));
    assert_eq!(h.enter("aaaaa", Level::Transition), Ok(Enter::Acquire));
    // the same thread re-enters the transition lock: count, do not lock
    assert_eq!(h.enter("aaaaa", Level::Transition), Ok(Enter::Reentered));
    assert_eq!(h.enter("aaaaa", Level::Directory), Ok(Enter::Acquire));
    assert_eq!(
        h.enter("aaaaa", Level::Directory),
        Err(OrderError::NotReentrant {
            seat: "aaaaa".into(),
            level: Level::Directory
        })
    );
    // another seat while holding one: refused (finish, release, then lock the other)
    assert!(matches!(
        h.enter("bbbbb", Level::Transition),
        Err(OrderError::CrossSeat { .. })
    ));
    assert!(h.exit("aaaaa", Level::Directory));
    assert!(matches!(
        h.enter("aaaaa", Level::Deliver),
        Err(OrderError::Backwards { .. })
    ));
    assert!(
        !h.exit("aaaaa", Level::Deliver),
        "only the innermost may be left"
    );
    assert!(
        !h.exit("aaaaa", Level::Transition),
        "depth 2 -> 1: still held"
    );
    assert!(h.holds("aaaaa", Level::Transition));
    assert!(h.exit("aaaaa", Level::Transition));
    assert!(h.exit("aaaaa", Level::Deliver));
    assert!(h.is_empty());
    assert_eq!(h.enter("bbbbb", Level::Transition), Ok(Enter::Acquire));
}
