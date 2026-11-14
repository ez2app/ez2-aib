//! bb7dc #549 3bacd239: the selfcheck stops its tree only once the child
//! reported it started; a child that never reports is said, never hidden.
#![cfg(unix)]

use aibd::selfcheck::{check_tree, process_row, CHECK_SCRIPT, CHILD_WAIT};
use std::time::Duration;

#[test]
fn the_real_check_waits_for_the_child_and_says_when_it_never_came() {
    let spawn = aibd::platform::default_spawn();
    // no marker is ever written: the wait runs out, the tree is still stopped
    let c = check_tree(spawn, None, "sleep 30 & wait", Duration::from_millis(300)).unwrap();
    assert!(!c.child_started, "{c:?}");
    assert!(c.stop.survivors.is_empty(), "{c:?}");
    let row = process_row(&Ok(c));
    assert_eq!(row.status.label(), "unverified");
    assert_eq!(row.notes, vec![("reason", "child_not_started".to_string())]);
    // a child that reports late (0.5 s): the check really waits for it
    let late = format!("sleep 0.5; {CHECK_SCRIPT}");
    let c = check_tree(spawn, None, &late, CHILD_WAIT).unwrap();
    assert!(
        c.child_started,
        "the check did not wait for a late child: {c:?}"
    );
    assert!(c.stop.survivors.is_empty(), "{c:?}");
    // the real script: the child reports, and it was in the stopped tree
    let c = check_tree(spawn, None, CHECK_SCRIPT, CHILD_WAIT).unwrap();
    assert!(c.child_started, "{c:?}");
    assert!(c.stop.survivors.is_empty(), "{c:?}");
    assert!(
        c.stop.tree_size >= 2,
        "the child was not in the stopped tree: {c:?}"
    );
}

/// a9136 11-09: a tree that ended on its own before the stop is not
/// reported as stopped. The root exits at once; its background child writes
/// the marker 0.3 s later, so when the handshake sees the marker the root is
/// certainly gone (no race with the 20 ms handshake).
#[test]
fn a_tree_that_ended_before_the_stop_is_said_not_stopped() {
    let spawn = aibd::platform::default_spawn();
    let c = check_tree(
        spawn,
        None,
        "(sleep 0.3; echo $$ > \"$1\") & exit 0",
        CHILD_WAIT,
    )
    .unwrap();
    assert!(c.child_started, "{c:?}");
    assert!(!c.tree_running, "the root had exited: {c:?}");
    let row = process_row(&Ok(c));
    assert_eq!(row.status.label(), "unverified");
    assert_eq!(
        row.notes,
        vec![("reason", "tree_ended_before_stop".to_string())]
    );
}
