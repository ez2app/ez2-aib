//! Issue #514 (043dd #510 M4): a cancel waits for exits on events, not by
//! re-reading the whole process table every 50 ms. Measured with the
//! registered timer's wake-up counter (`tree.cancel_wait`): each wake-up is
//! one process-table read. Its own test binary, so the counter only sees
//! this test.
#![cfg(target_os = "macos")]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aibd::platform::tree::{spawn_tree, SpawnSpec};
use std::time::{Duration, Instant};

fn tree(script: &str, session: &str) -> aibd::platform::tree::TreeHandle {
    let h = spawn_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), script.into()],
        cwd: Some(std::env::temp_dir()),
        env: vec![],
        env_clear: false,
        session_id: format!("{session}-{}", std::process::id()),
        watch_forks: true,
        discard_output: true,
        lineage: None,
        inject: None,
    })
    .unwrap();
    // let the shell start its child (the fork watch records it)
    let end = Instant::now() + Duration::from_secs(3);
    let op = aibd::timers::SELFCHECK_CHILD_WAIT.begin(Some(end));
    while op.tick() {}
    h
}

/// A tree that ignores SIGINT and SIGTERM waits each grace out before the
/// next signal: polling read the table ~40 times per 2 s grace; on events
/// the wait sleeps through the grace (one wake-up at its end).
#[test]
fn a_stubborn_tree_costs_a_handful_of_wake_ups_not_one_per_50_ms() {
    let mut h = tree("trap '' INT TERM; sleep 60 & wait", "cost-stubborn");
    let before = aibd::timers::TREE_CANCEL_WAIT.wakeups();
    let t = Instant::now();
    let r = h.cancel(Duration::from_secs(2)).unwrap();
    let took = t.elapsed();
    let woke = aibd::timers::TREE_CANCEL_WAIT.wakeups() - before;
    eprintln!(
        "stubborn: wake-ups {woke}, took {took:?}, survivors {:?}",
        r.survivors
    );
    assert!(r.survivors.is_empty(), "{r:?}");
    assert!(
        woke <= 8,
        "{woke} wake-ups for one cancel (polling would be ~80)"
    );
    // the graces are still waited out: events change when we look, not how long
    assert!(took >= Duration::from_secs(3), "{took:?}");
}

/// A tree that ends on the first signal: the wait wakes on its exit at
/// once, it does not sit out the grace. (A foreground child: a
/// non-interactive sh starts background jobs with SIGINT ignored, and that
/// tree would sit out the grace on polling too.)
#[test]
fn a_tree_that_ends_at_once_is_seen_gone_at_once() {
    let mut h = tree("sleep 60; true", "cost-quick");
    let before = aibd::timers::TREE_CANCEL_WAIT.wakeups();
    let t = Instant::now();
    let r = h.cancel(Duration::from_secs(10)).unwrap();
    let took = t.elapsed();
    let woke = aibd::timers::TREE_CANCEL_WAIT.wakeups() - before;
    eprintln!(
        "quick: wake-ups {woke}, took {took:?}, survivors {:?}",
        r.survivors
    );
    assert!(r.survivors.is_empty(), "{r:?}");
    assert!(took < Duration::from_secs(5), "sat out the grace: {took:?}");
}
