//! bb7dc #544 310cf95c D4: the shutdown sweep of leftover trees keeps to
//! one absolute deadline, however many trees there are. Its own test
//! binary: the leftover list is per process.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![cfg(target_os = "macos")]

use aibd::platform::tree::{spawn_tree, SpawnSpec};
use std::process::Command;
use std::time::{Duration, Instant};

const N: usize = 8;

/// A tree whose shell and child both ignore SIGTERM (inherited across exec),
/// so each stop waits its whole grace before SIGKILL.
fn stubborn(i: usize, dir: &std::path::Path) -> aibd::platform::tree::TreeHandle {
    spawn_tree(&SpawnSpec {
        program: "/bin/sh".into(),
        args: vec!["-c".into(), "trap '' TERM; sleep 60 & wait".into()],
        cwd: Some(dir.to_path_buf()),
        env: vec![],
        env_clear: false,
        session_id: format!("s-cap-{i}"),
        watch_forks: true,
        discard_output: true,
        lineage: None,
        inject: None,
    })
    .unwrap()
}

fn running(pid: u32) -> bool {
    let o = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let st = String::from_utf8_lossy(&o.stdout).trim().to_string();
    !st.is_empty() && !st.starts_with('Z')
}

#[test]
fn many_stubborn_leftovers_are_stopped_within_one_deadline() {
    let tmp = tempfile::tempdir().unwrap();
    let mut roots = Vec::new();
    for i in 0..N {
        let h = stubborn(i, tmp.path());
        roots.push(h.root_pid());
        aibd::platform::session_unix::keep_as_leftover_for_test(h);
    }
    // the grace is cut to 1 s here: one at a time this is N x (1 s + KILL)
    // ~ 8 s or more; together it is about 1 s
    let cap = Duration::from_secs(4);
    let t = Instant::now();
    let left = aibd::platform::session_unix::stop_leftovers(t + cap);
    let took = t.elapsed();
    assert!(
        took < cap + Duration::from_millis(500),
        "took {took:?} for {N} trees, cap {cap:?}"
    );
    // lineage off: none can be confirmed, all reported with their identity
    let mut got: Vec<u32> = left.iter().map(|(p, _)| *p).collect();
    got.sort_unstable();
    roots.sort_unstable();
    assert_eq!(got, roots, "{left:?}");
    assert!(left.iter().all(|(_, start)| start.is_some()), "{left:?}");
    // and they really were stopped (SIGKILL after the shared grace)
    for r in &roots {
        assert!(!running(*r), "root {r} still running");
    }
}
