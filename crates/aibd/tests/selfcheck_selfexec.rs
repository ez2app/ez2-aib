//! aibd as its own selfcheck tree (a9136 11-09): the tree Windows uses
//! (no /bin/sh), run here through this platform's real session spawn, so
//! macOS checks the same code path Windows CI does.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aibd::platform::check_tree::ROOT_ARG;
use aibd::selfcheck::{check_argv, process_row, CHILD_WAIT};

#[test]
fn aibd_is_its_own_check_tree_and_the_stop_meets_root_and_child() {
    let c = check_argv(
        aibd::platform::default_spawn(),
        None,
        vec![env!("CARGO_BIN_EXE_aibd").into(), ROOT_ARG.into()],
        CHILD_WAIT,
    )
    .unwrap();
    assert!(c.child_started, "the leaf never reported: {c:?}");
    assert!(c.stop.survivors.is_empty(), "{c:?}");
    assert!(c.stop.tree_size >= 2, "the leaf was not in the stopped tree: {c:?}");
    assert!(
        !process_row(&Ok(c)).status.label().starts_with("failed"),
        "the row failed"
    );
}

/// A root that cannot report (the marker's folder does not exist) exits 1
/// at once and kills its child: never a leaf left behind unreported.
#[test]
fn a_root_that_cannot_report_ends_with_1() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aibd"))
        .args([ROOT_ARG, "/nonexistent-aibd-dir/x.child"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1), "{out:?}");
}
