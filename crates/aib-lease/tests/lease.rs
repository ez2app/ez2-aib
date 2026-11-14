// SPDX-License-Identifier: Apache-2.0
//! b127: a holder on another machine keeps its lease / queue place while it heartbeats, and loses
//! it (with the reason logged) when it stops. Each guard has an arm that shows what happens
//! without it (the 1.x way), so a test that stays green for the wrong reason is visible.
#![allow(clippy::unwrap_used)]

use aib_lease::*;
use serde_json::Value;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

const T0: f64 = 1_794_230_000.0;
/// The K10 runner's pid on the Mac (11-09); no such process on the test machine.
const MAC_PID: i64 = 68_643;

struct Fake {
    host: String,
    procs: RefCell<HashMap<i64, String>>,
    git: Option<PathBuf>,
}

impl Fake {
    fn new() -> Fake {
        Fake {
            host: "mac-c".into(),
            procs: RefCell::new(HashMap::new()),
            git: None,
        }
    }
    fn run(&self, pid: i64, start: &str) {
        self.procs.borrow_mut().insert(pid, start.into());
    }
    fn kill(&self, pid: i64) {
        self.procs.borrow_mut().remove(&pid);
    }
}

impl Probe for Fake {
    fn hostname(&self) -> String {
        self.host.clone()
    }
    fn proc_start(&self, pid: i64) -> ProcState {
        match self.procs.borrow().get(&pid) {
            Some(s) => ProcState::Started(s.clone()),
            None => ProcState::Gone,
        }
    }
    fn git_dir(&self, _repo: &str) -> Option<PathBuf> {
        self.git.clone()
    }
    fn iso(&self, epoch: i64) -> String {
        iso_utc(epoch)
    }
}

static N: AtomicUsize = AtomicUsize::new(0);

fn tmp(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!(
        "aib-lease-test-{}-{}-{}",
        std::process::id(),
        tag,
        N.fetch_add(1, Ordering::SeqCst)
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn paths(tag: &str) -> Paths {
    Paths {
        lease: tmp(tag).join("host-lease.json"),
    }
}

fn holder(run_id: &str) -> Holder {
    Holder {
        owner: "2240e".into(),
        what: format!("what {run_id}"),
        run_id: run_id.into(),
        ..Holder::default()
    }
}

fn remote(run_id: &str, hb: i64) -> Holder {
    Holder {
        pid: Some(MAC_PID),
        host: Some("mac-a.local".into()),
        heartbeat_s: Some(hb),
        ..holder(run_id)
    }
}

fn acq(ps: &Paths, p: &Fake, now: f64, h: Holder) -> Outcome {
    acquire(
        ps,
        p,
        now,
        &AcquireArgs {
            holder: h,
            ttl: None,
            exclusive: true,
            priority: false,
            cwd: Some("/nonexistent".into()),
            expect: None,
        },
    )
}

fn qadd(ps: &Paths, p: &Fake, now: f64, h: Holder) -> Outcome {
    queue_add(
        ps,
        p,
        now,
        &QueueAddArgs {
            holder: h,
            ..QueueAddArgs::default()
        },
    )
}

fn result(o: &Outcome) -> &str {
    o.out["result"].as_str().unwrap_or("")
}

fn live_ids(ps: &Paths, p: &Fake, now: f64) -> Vec<String> {
    queue_live(ps, now, p)
        .iter()
        .map(|e| e["run_id"].as_str().unwrap().to_string())
        .collect()
}

fn ledger(ps: &Paths) -> Vec<Value> {
    std::fs::read_to_string(ps.ledger())
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

// ── the b127 case ─────────────────────────────────────────────────────────────────────

#[test]
fn a_remote_holder_is_not_judged_by_a_local_pid() {
    let p = Fake::new();
    // 1.x way (no --host): the Mac pid is checked on this machine, looks dead, lease taken at once
    let ps = paths("old-way");
    let old = Holder {
        pid: Some(MAC_PID),
        ..holder("k10p")
    };
    assert_eq!(acq(&ps, &p, T0, old).rc, RC_OK);
    let o = acq(&ps, &p, T0 + 10.0, holder("other"));
    assert_eq!((o.rc, result(&o)), (RC_OK, "took_over"));
    assert_eq!(o.out["why"], "holder_dead");

    // --host: the pid is not probed here; the lease holds
    let ps = paths("remote");
    let o = acq(&ps, &p, T0, remote("k10p", 60));
    assert_eq!((o.rc, result(&o)), (RC_OK, "acquired"));
    let lease = read_lease(&ps).unwrap().unwrap();
    assert_eq!(lease["host"], "mac-a.local");
    assert_eq!(lease["lease_host"], "mac-c");
    assert_eq!(lease["pid"], MAC_PID);
    assert!(lease["pid_start"].is_null());
    assert_eq!(lease["expiry_epoch"], T0 as i64 + 180);
    let o = acq(&ps, &p, T0 + 10.0, holder("other"));
    assert_eq!((o.rc, result(&o)), (RC_HELD, "busy"));
}

#[test]
fn heartbeats_keep_a_remote_lease_and_silence_frees_it() {
    let p = Fake::new();
    let ps = paths("lease-hb");
    acq(&ps, &p, T0, remote("k10p", 60));
    let b = heartbeat(&ps, &p, T0 + 150.0, "k10p");
    assert_eq!((b.rc, result(&b)), (RC_OK, "beat"));
    assert_eq!(b.out["lease_until"], T0 as i64 + 330);
    // past the first deadline (T0+180) but inside the one the beat bought
    assert_eq!(acq(&ps, &p, T0 + 300.0, holder("other")).rc, RC_HELD);
    // no beat since T0+150: free at T0+330, reason named
    let o = acq(&ps, &p, T0 + 330.0, holder("other"));
    assert_eq!((o.rc, result(&o)), (RC_OK, "took_over"));
    assert_eq!(o.out["why"], "heartbeat_stale");
    // the old holder's next beat is told it lost the lease
    let b = heartbeat(&ps, &p, T0 + 340.0, "k10p");
    assert_eq!((b.rc, result(&b)), (RC_HELD, "not_found"));
}

#[test]
fn a_beating_remote_queue_entry_survives_other_seats_cleanup() {
    let p = Fake::new();
    let ps = paths("queue-hb");
    p.run(500, "Mon Nov  9 22:00:00 2026");
    assert_eq!(qadd(&ps, &p, T0, remote("k10p", 60)).rc, RC_OK);
    heartbeat(&ps, &p, T0 + 100.0, "k10p");
    // another seat registers (and cleans the file) at T0+200: k10p still counts (until T0+280)
    let other = Holder {
        pid: Some(500),
        ..holder("33e28-fwt4")
    };
    assert_eq!(qadd(&ps, &p, T0 + 200.0, other.clone()).rc, RC_OK);
    assert_eq!(live_ids(&ps, &p, T0 + 200.0), vec!["k10p", "33e28-fwt4"]);
    // it is ahead: the other seat may not jump it on a free machine
    let o = acq(&ps, &p, T0 + 200.0, other.clone());
    assert_eq!((o.rc, result(&o)), (RC_HELD, "queue_behind"));
    // the holder stops beating; the next cleanup drops it and says why
    qadd(&ps, &p, T0 + 300.0, other);
    assert_eq!(live_ids(&ps, &p, T0 + 300.0), vec!["33e28-fwt4"]);
    let gone: Vec<Value> = ledger(&ps)
        .into_iter()
        .filter(|r| r["event"] == "queue_leave" && r["run_id"] == "k10p")
        .collect();
    assert_eq!(gone.len(), 1);
    assert_eq!(gone[0]["why"], "gone");
    assert_eq!(gone[0]["gone_why"], "heartbeat_stale");
    assert_eq!(gone[0]["holder_host"], "mac-a.local");
}

#[test]
fn without_heartbeats_a_remote_entry_would_have_dropped() {
    // arm for the test above: same timeline, no beat at T0+100 ⇒ gone by T0+200
    let p = Fake::new();
    let ps = paths("queue-nobeat");
    qadd(&ps, &p, T0, remote("k10p", 60));
    assert!(live_ids(&ps, &p, T0 + 200.0).is_empty());
}

#[test]
fn a_beat_after_the_deadline_does_not_revive_the_entry() {
    let p = Fake::new();
    let ps = paths("late-beat");
    qadd(&ps, &p, T0, remote("k10p", 60));
    let b = heartbeat(&ps, &p, T0 + 180.0, "k10p");
    assert_eq!((b.rc, result(&b)), (RC_HELD, "not_found"));
    assert!(queue_read(&ps).is_empty());
    let l = ledger(&ps);
    assert_eq!(l.last().unwrap()["gone_why"], "heartbeat_stale");
}

#[test]
fn a_beat_for_an_entry_without_heartbeat_s_changes_nothing() {
    let p = Fake::new();
    let ps = paths("beat-plain");
    qadd(&ps, &p, T0, holder("plain"));
    let before = queue_read(&ps);
    let b = heartbeat(&ps, &p, T0 + 10.0, "plain");
    assert_eq!(b.rc, RC_HELD);
    assert_eq!(queue_read(&ps), before);
}

// ── usage ─────────────────────────────────────────────────────────────────────────────

#[test]
fn another_host_needs_a_heartbeat_and_the_interval_is_bounded() {
    let p = Fake::new();
    let ps = paths("usage");
    let no_hb = Holder {
        heartbeat_s: None,
        ..remote("x", 60)
    };
    assert_eq!(qadd(&ps, &p, T0, no_hb.clone()).rc, RC_USAGE);
    assert_eq!(acq(&ps, &p, T0, no_hb).rc, RC_USAGE);
    assert_eq!(qadd(&ps, &p, T0, remote("x", 9)).rc, RC_USAGE);
    assert_eq!(qadd(&ps, &p, T0, remote("x", 601)).rc, RC_USAGE);
    let empty = Holder {
        host: Some(String::new()),
        ..remote("x", 60)
    };
    assert_eq!(qadd(&ps, &p, T0, empty).rc, RC_USAGE);
    // --host naming this machine is the local mode: the pid is probed here, no heartbeat needed
    p.run(700, "S");
    let here = Holder {
        pid: Some(700),
        host: Some("mac-c".into()),
        ..holder("here")
    };
    assert_eq!(qadd(&ps, &p, T0, here).rc, RC_OK);
    assert_eq!(queue_read(&ps)[0]["pid_start"], "S");
    assert!(!ps.lease.exists());
}

#[test]
fn a_local_pid_that_is_already_gone_is_not_queued() {
    let p = Fake::new();
    let ps = paths("gone-pid");
    let h = Holder {
        pid: Some(4242),
        ..holder("dead")
    };
    let o = qadd(&ps, &p, T0, h);
    assert_eq!((o.rc, result(&o)), (RC_USAGE, "not_in_queue"));
}

// ── 1.x semantics kept ────────────────────────────────────────────────────────────────

#[test]
fn local_entries_drop_by_pid_and_the_reason_is_named() {
    let p = Fake::new();
    let ps = paths("local-why");
    p.run(600, "A");
    p.run(601, "B");
    for (pid, id) in [(600, "reused"), (601, "dead")] {
        let h = Holder {
            pid: Some(pid),
            ..holder(id)
        };
        assert_eq!(qadd(&ps, &p, T0, h).rc, RC_OK);
    }
    qadd(&ps, &p, T0, holder("nopid"));
    p.run(600, "A2"); // pid 600 now belongs to another process
    p.kill(601);
    let o = queue_list(&ps, &p, T0 + 1.0, true);
    let why: HashMap<String, String> = o.out["gone"]
        .as_array()
        .unwrap()
        .iter()
        .map(|g| {
            (
                g["run_id"].as_str().unwrap().into(),
                g["gone_why"].as_str().unwrap().into(),
            )
        })
        .collect();
    assert_eq!(why["reused"], "pid_reused");
    assert_eq!(why["dead"], "pid_dead");
    assert_eq!(o.out["live"].as_array().unwrap().len(), 1);
    // no pid: capped at 10 minutes whatever --ttl says (QUEUE_NOPID_MAX_S)
    let o = queue_list(&ps, &p, T0 + 600.0, true);
    assert!(o.out["live"].as_array().unwrap().is_empty());
    assert_eq!(o.out["gone"][2]["gone_why"], "until_passed");
    // list is read only
    assert_eq!(queue_read(&ps).len(), 3);
}

#[test]
fn queue_order_is_priority_then_registration_and_the_head_takes_it() {
    let p = Fake::new();
    let ps = paths("order");
    qadd(&ps, &p, T0, holder("a"));
    qadd(&ps, &p, T0 + 1.0, holder("b"));
    queue_add(
        &ps,
        &p,
        T0 + 2.0,
        &QueueAddArgs {
            holder: holder("ship"),
            priority: true,
            ..QueueAddArgs::default()
        },
    );
    assert_eq!(live_ids(&ps, &p, T0 + 3.0), vec!["ship", "a", "b"]);
    // re-adding keeps the original place
    qadd(&ps, &p, T0 + 4.0, holder("a"));
    assert_eq!(live_ids(&ps, &p, T0 + 4.0), vec!["ship", "a", "b"]);
    assert_eq!(acq(&ps, &p, T0 + 5.0, holder("a")).rc, RC_HELD);
    let o = acq(&ps, &p, T0 + 5.0, holder("ship"));
    assert_eq!((o.rc, result(&o)), (RC_OK, "acquired"));
    assert_eq!(live_ids(&ps, &p, T0 + 5.0), vec!["a", "b"]);
    let acquired = ledger(&ps)
        .into_iter()
        .find(|r| r["event"] == "acquire")
        .unwrap();
    assert_eq!(acquired["waited_s"], 3);
}

#[test]
fn a_live_local_holder_is_never_taken_over_even_after_expiry() {
    let p = Fake::new();
    let ps = paths("alive");
    p.run(800, "S");
    let h = Holder {
        pid: Some(800),
        ..holder("long")
    };
    acquire(
        &ps,
        &p,
        T0,
        &AcquireArgs {
            holder: h,
            ttl: Some(60),
            ..AcquireArgs::default()
        },
    );
    assert_eq!(acq(&ps, &p, T0 + 3600.0, holder("other")).rc, RC_HELD);
    p.kill(800);
    let o = acq(&ps, &p, T0 + 3601.0, holder("other"));
    assert_eq!(o.out["why"], "holder_dead");
}

#[test]
fn a_dead_local_holder_with_a_live_ship_under_its_tree_is_not_taken_over() {
    let mut p = Fake::new();
    let git = tmp("git");
    std::fs::create_dir_all(git.join(SHIP_REGISTRY)).unwrap();
    std::fs::write(
        git.join(SHIP_REGISTRY).join("900"),
        r#"{"pid": 900, "pid_start": "S9", "host": "mac-c"}"#,
    )
    .unwrap();
    p.git = Some(git.clone());
    p.run(800, "S");
    p.run(900, "S9");
    let ps = paths("ship");
    let h = Holder {
        pid: Some(800),
        ..holder("shell")
    };
    acquire(
        &ps,
        &p,
        T0,
        &AcquireArgs {
            holder: h,
            cwd: Some("/tree".into()),
            ..AcquireArgs::default()
        },
    );
    p.kill(800);
    let o = acq(&ps, &p, T0 + 10.0, holder("other"));
    assert_eq!((o.rc, result(&o)), (RC_HELD, "busy"));
    assert_eq!(o.out["shipping_pids"][0], 900);
    p.kill(900);
    let o = acq(&ps, &p, T0 + 20.0, holder("other"));
    assert_eq!((o.rc, o.out["why"].as_str()), (RC_OK, Some("holder_dead")));
    assert!(!git.join(SHIP_REGISTRY).join("900").exists());
}

#[test]
fn time_only_records_expire_by_time_and_reentry_keeps_the_start() {
    let p = Fake::new();
    let ps = paths("time");
    acquire(
        &ps,
        &p,
        T0,
        &AcquireArgs {
            holder: holder("t"),
            ttl: Some(100),
            ..AcquireArgs::default()
        },
    );
    assert_eq!(acq(&ps, &p, T0 + 99.0, holder("other")).rc, RC_HELD);
    let o = acq(&ps, &p, T0 + 50.0, holder("t"));
    assert_eq!(result(&o), "reentered");
    let l = read_lease(&ps).unwrap().unwrap();
    assert_eq!(l["acquired_epoch"], T0 as i64);
    let o = acq(&ps, &p, T0 + 50.0 + DEFAULT_TTL_S as f64, holder("other"));
    assert_eq!(o.out["why"], "expired");
}

#[test]
fn an_unreadable_lease_fails_closed() {
    let p = Fake::new();
    let ps = paths("bad");
    std::fs::write(&ps.lease, "{not json").unwrap();
    assert_eq!(acq(&ps, &p, T0, holder("x")).rc, RC_UNREADABLE);
    std::fs::write(&ps.lease, r#"{"schema": "other", "expiry_epoch": 1}"#).unwrap();
    assert_eq!(acq(&ps, &p, T0, holder("x")).rc, RC_UNREADABLE);
    assert_eq!(heartbeat(&ps, &p, T0, "x").rc, RC_UNREADABLE);
}

#[test]
fn iso_utc_matches_known_dates() {
    assert_eq!(iso_utc(0), "1970-01-01T00:00:00+00:00");
    assert_eq!(iso_utc(1_794_235_215), "2026-11-09T14:40:15+00:00");
    assert_eq!(iso_utc(951_782_400), "2000-02-29T00:00:00+00:00");
}

/// A2: `--expect` is shown to whoever waits and never moves the expiry.
#[test]
fn expect_is_recorded_beside_the_expiry_and_never_changes_it() {
    let p = Fake::new();
    let ps = paths("expect");
    let with = |e: Option<i64>| AcquireArgs {
        holder: holder("e"),
        ttl: Some(2700),
        expect: e,
        ..AcquireArgs::default()
    };
    let o = acquire(&ps, &p, T0, &with(Some(900)));
    assert_eq!(o.rc, RC_OK, "{}", o.out);
    let l = read_lease(&ps).unwrap().unwrap();
    assert_eq!(l["expect_s"], 900);
    assert_eq!(l["expect_end_epoch"], T0 as i64 + 900);
    assert!(l["expect_end_at"].is_string());
    assert_eq!(l["expiry_epoch"], T0 as i64 + 2700);
    // past the estimate but inside the ttl: still held
    assert_eq!(acq(&ps, &p, T0 + 1000.0, holder("other")).rc, RC_HELD);

    let q = paths("expect-none");
    assert_eq!(acquire(&q, &p, T0, &with(None)).rc, RC_OK);
    assert!(read_lease(&q).unwrap().unwrap().get("expect_s").is_none());
}

#[test]
fn expect_out_of_range_is_usage_and_takes_nothing() {
    let p = Fake::new();
    for e in [0, -5, EXPECT_MAX_S + 1] {
        let ps = paths(&format!("expect-bad{e}"));
        let o = acquire(
            &ps,
            &p,
            T0,
            &AcquireArgs {
                holder: holder("e"),
                expect: Some(e),
                ..AcquireArgs::default()
            },
        );
        assert_eq!(o.rc, RC_USAGE, "{e}: {}", o.out);
        assert!(read_lease(&ps).unwrap().is_none(), "{e}");
    }
}
