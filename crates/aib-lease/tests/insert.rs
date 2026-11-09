// SPDX-License-Identifier: Apache-2.0
//! D4: the insert slot (DECISION-d4-insert-043dd-1110). Each guard has its own test, so taking
//! one out turns exactly that test red.
#![allow(clippy::unwrap_used)]

use aib_lease::insert::*;
use aib_lease::*;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

const T0: f64 = 1_794_240_000.0;
const LONG_PID: i64 = 4100;
const SHORT_PID: i64 = 4200;

struct Fake {
    procs: RefCell<HashMap<i64, String>>,
}

impl Fake {
    fn new() -> Fake {
        let f = Fake {
            procs: RefCell::new(HashMap::new()),
        };
        f.run(LONG_PID);
        f.run(SHORT_PID);
        f
    }
    fn run(&self, pid: i64) {
        self.procs.borrow_mut().insert(pid, format!("start-{pid}"));
    }
    fn kill(&self, pid: i64) {
        self.procs.borrow_mut().remove(&pid);
    }
}

impl Probe for Fake {
    fn hostname(&self) -> String {
        "pro13".into()
    }
    fn proc_start(&self, pid: i64) -> ProcState {
        match self.procs.borrow().get(&pid) {
            Some(s) => ProcState::Started(s.clone()),
            None => ProcState::Gone,
        }
    }
    fn git_dir(&self, _repo: &str) -> Option<PathBuf> {
        None
    }
    fn iso(&self, epoch: i64) -> String {
        iso_utc(epoch)
    }
}

static N: AtomicUsize = AtomicUsize::new(0);

fn paths(enabled: bool) -> Paths {
    let d = std::env::temp_dir().join(format!(
        "aib-lease-insert-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    let ps = Paths {
        lease: d.join("host-lease.json"),
    };
    if enabled {
        std::fs::write(ps.insert_enabled(), "").unwrap();
    }
    ps
}

fn holder(run_id: &str, pid: i64) -> Holder {
    Holder {
        owner: "043dd".into(),
        what: format!("what {run_id}"),
        run_id: run_id.into(),
        pid: Some(pid),
        ..Holder::default()
    }
}

fn long(ps: &Paths, p: &Fake, exclusive: bool) -> Outcome {
    acquire(
        ps,
        p,
        T0,
        &AcquireArgs {
            holder: holder("long-1", LONG_PID),
            ttl: Some(3600),
            exclusive,
            priority: false,
            cwd: Some("/x".into()),
        },
    )
}

fn ins(ps: &Paths, p: &Fake, now: f64, run_id: &str, pid: i64) -> Outcome {
    insert_acquire(
        ps,
        p,
        now,
        &InsertArgs {
            holder: holder(run_id, pid),
            hard_timeout_s: 600,
        },
    )
}

fn result(o: &Outcome) -> &str {
    o.out["result"].as_str().unwrap_or("")
}

#[test]
fn a_short_run_goes_next_to_a_shared_long_holder_and_gives_the_slot_back() {
    let (ps, p) = (paths(true), Fake::new());
    assert_eq!(long(&ps, &p, false).rc, RC_OK);
    let o = ins(&ps, &p, T0 + 10.0, "short-1", SHORT_PID);
    assert_eq!((o.rc, result(&o)), (RC_OK, "inserted"), "{:?}", o.out);
    assert_eq!(o.out["main_run_id"], "long-1");
    let r = read_insert(&ps).unwrap().unwrap();
    assert_eq!(r["deadline_epoch"].as_i64(), Some(T0 as i64 + 10 + 600));
    assert_eq!(
        insert_release(&ps, &p, T0 + 60.0, "someone-else").rc,
        RC_HELD
    );
    assert_eq!(insert_release(&ps, &p, T0 + 60.0, "short-1").rc, RC_OK);
    assert!(read_insert(&ps).unwrap().is_none());
    let log = std::fs::read_to_string(ps.ledger()).unwrap();
    for e in ["\"event\":\"insert\"", "\"event\":\"insert_release\""] {
        assert!(log.contains(e), "{e} in {log}");
    }
}

#[test]
fn it_is_off_until_the_machine_opts_in() {
    let (ps, p) = (paths(false), Fake::new());
    long(&ps, &p, false);
    let o = ins(&ps, &p, T0, "short-1", SHORT_PID);
    assert_eq!((o.rc, result(&o)), (RC_HELD, "insert_disabled"));
}

#[test]
fn an_exclusive_holder_is_never_shared() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, true);
    let o = ins(&ps, &p, T0, "short-1", SHORT_PID);
    assert_eq!((o.rc, result(&o)), (RC_HELD, "holder_exclusive"));
}

#[test]
fn an_exclusive_acquire_waits_for_a_running_insert() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, false);
    assert_eq!(ins(&ps, &p, T0, "short-1", SHORT_PID).rc, RC_OK);
    // the long holder ends; an exclusive one comes next while the insert still runs
    p.kill(LONG_PID);
    let o = acquire(
        &ps,
        &p,
        T0 + 30.0,
        &AcquireArgs {
            holder: holder("exclusive-2", 4300),
            ttl: Some(600),
            exclusive: true,
            ..AcquireArgs::default()
        },
    );
    assert_eq!((o.rc, result(&o)), (RC_HELD, "insert_running"), "{:?}", o.out);
    assert_eq!(o.out["insert"]["run_id"], "short-1");
    // a shared one may come next to it
    let o = acquire(
        &ps,
        &p,
        T0 + 30.0,
        &AcquireArgs {
            holder: holder("shared-2", 4300),
            ttl: Some(600),
            ..AcquireArgs::default()
        },
    );
    assert_eq!(o.rc, RC_OK, "{:?}", o.out);
    // the insert's shell is gone: the exclusive one gets its turn
    p.kill(SHORT_PID);
    p.kill(4300);
    let o = acquire(
        &ps,
        &p,
        T0 + 40.0,
        &AcquireArgs {
            holder: holder("exclusive-2", 4400),
            ttl: Some(600),
            exclusive: true,
            ..AcquireArgs::default()
        },
    );
    assert_eq!(o.rc, RC_OK, "{:?}", o.out);
}

#[test]
fn a_long_waiter_of_thirty_minutes_keeps_the_next_turn() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, false);
    p.run(4500);
    let q = queue_add(
        &ps,
        &p,
        T0,
        &QueueAddArgs {
            holder: holder("waiter", 4500),
            ttl: Some(7200),
            ..QueueAddArgs::default()
        },
    );
    assert_eq!(q.rc, RC_OK, "{:?}", q.out);
    assert_eq!(
        ins(&ps, &p, T0 + 1799.0, "short-1", SHORT_PID).rc,
        RC_OK,
        "29:59 waited: may go"
    );
    insert_release(&ps, &p, T0 + 1799.0, "short-1");
    let o = ins(&ps, &p, T0 + 1800.0, "short-2", SHORT_PID);
    assert_eq!((o.rc, result(&o)), (RC_HELD, "long_waiter"), "{:?}", o.out);
}

#[test]
fn one_insert_at_a_time_and_a_dead_one_frees_the_slot() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, false);
    assert_eq!(ins(&ps, &p, T0, "short-1", SHORT_PID).rc, RC_OK);
    p.run(4600);
    let o = ins(&ps, &p, T0 + 5.0, "short-2", 4600);
    assert_eq!((o.rc, result(&o)), (RC_HELD, "insert_taken"));
    // past its deadline but its shell is still stopping the tree: still taken
    let o = ins(&ps, &p, T0 + 2000.0, "short-2", 4600);
    assert_eq!(result(&o), "insert_taken");
    p.kill(SHORT_PID);
    assert_eq!(ins(&ps, &p, T0 + 2000.0, "short-2", 4600).rc, RC_OK);
}

#[test]
fn no_holder_means_take_the_lease_itself() {
    let (ps, p) = (paths(true), Fake::new());
    let o = ins(&ps, &p, T0, "short-1", SHORT_PID);
    assert_eq!((o.rc, result(&o)), (RC_HELD, "no_long_holder"));
    long(&ps, &p, false);
    p.kill(LONG_PID);
    assert_eq!(result(&ins(&ps, &p, T0, "short-1", SHORT_PID)), "no_long_holder");
}

#[test]
fn an_unreadable_insert_record_fails_closed() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, false);
    std::fs::write(ps.insert(), "{not json").unwrap();
    assert_eq!(ins(&ps, &p, T0, "short-1", SHORT_PID).rc, RC_UNREADABLE);
    let o = acquire(
        &ps,
        &p,
        T0,
        &AcquireArgs {
            holder: holder("exclusive-2", 4300),
            exclusive: true,
            ..AcquireArgs::default()
        },
    );
    assert_eq!(o.rc, RC_UNREADABLE, "{:?}", o.out);
}

#[test]
fn usage_insert_runs_here_with_a_bounded_hard_timeout() {
    let (ps, p) = (paths(true), Fake::new());
    long(&ps, &p, false);
    for (h, t) in [
        (holder("a", SHORT_PID), 299),
        (holder("a", SHORT_PID), 901),
        (
            Holder {
                pid: None,
                ..holder("a", 0)
            },
            600,
        ),
        (
            Holder {
                host: Some("mac".into()),
                heartbeat_s: Some(30),
                ..holder("a", SHORT_PID)
            },
            600,
        ),
    ] {
        let o = insert_acquire(
            &ps,
            &p,
            T0,
            &InsertArgs {
                holder: h,
                hard_timeout_s: t,
            },
        );
        assert_eq!(o.rc, RC_USAGE, "{:?}", o.out);
    }
}
