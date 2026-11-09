//! 043dd #510 M1: a root that was gone before it could be recorded (a CLI
//! that exits at once) leaves the layer nothing to check the tree against.
//! Such a cancel must say so (`unverified` / `root_unrecorded`), never
//! `clean`. The race cannot be made to happen on demand, so the record step
//! is told to find nothing (`Inject::RootUnrecorded`); everything after it
//! is the real code path (the injected root is in fact alive, so the test
//! kills it itself afterwards). Its own test binary.
#![cfg(unix)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aibd::lineage::Lineage;
use aibd::platform::tree::{spawn_tree, Inject, SpawnSpec, GAP_ROOT_UNRECORDED};
use std::sync::Arc;
use std::time::Duration;

fn running(pid: u32) -> bool {
    std::process::Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .map(|o| {
            let s = String::from_utf8_lossy(&o.stdout);
            let s = s.trim();
            !s.is_empty() && !s.starts_with('Z')
        })
        .unwrap_or(false)
}

#[test]
fn a_cancel_after_an_unrecorded_root_is_unverified_not_clean() {
    let mut h = spawn_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), "exec sleep 60".into()],
        cwd: Some(std::env::temp_dir()),
        env: vec![],
        env_clear: false,
        session_id: format!("s-root-unrecorded-{}", std::process::id()),
        watch_forks: true,
        discard_output: true,
        // as aibd runs it: fork watcher plus M1 lineage
        lineage: Some(Arc::new(Lineage::detect())),
        inject: Some(Inject::RootUnrecorded),
    })
    .unwrap();
    let root = h.root_pid();
    let r = h.cancel(Duration::from_millis(500)).unwrap();
    // the test's own child: never leave it behind, whatever the layer did
    let left = running(root);
    if left {
        let _ = std::process::Command::new("kill")
            .args(["-KILL", &root.to_string()])
            .status();
    }
    eprintln!("left={left} {r:?}");
    assert_eq!(r.known_gaps, vec![GAP_ROOT_UNRECORDED], "{r:?}");
    assert_ne!(r.clean_status(), "clean", "{r:?}");
    // the reason names the gap; only an unavailable lineage (this machine)
    // comes before it, and neither is ever "observed_clean"
    let want = if r.lineage_unavailable {
        "lineage_unavailable"
    } else {
        GAP_ROOT_UNRECORDED
    };
    assert_eq!(r.unverified_reason(), Some(want), "{r:?}");
}
