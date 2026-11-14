//! aibd as its own selfcheck tree (a9136 11-09). Windows has no `/bin/sh`,
//! so the check tree there is aibd itself: `aibd __check-tree <marker>`
//! starts one child (`aibd __check-leaf`), writes the child's pid to the
//! marker and waits for it. The session spawn puts both in the session's
//! tree (Job on Windows), so the selfcheck stop really meets a root and a
//! child, the same way a CLI and its subprocess are met.
//!
//! Std only (no platform API); it lives under `platform/` because it starts
//! a process.

use std::path::Path;
use std::process::{Command, Stdio};

/// The hidden argument that makes aibd the check tree's root.
pub const ROOT_ARG: &str = "__check-tree";
/// The hidden argument that makes aibd the check tree's child.
pub const LEAF_ARG: &str = "__check-leaf";

/// `aibd __check-tree <marker>`: start the child, report its pid (the
/// marker is complete or absent: written beside it, then renamed), wait for
/// it. 0 when the child ended; 1 when it could not be started or reported
/// (the selfcheck then says `child_not_started`).
pub fn run_root(marker: &Path) -> u8 {
    let Ok(exe) = std::env::current_exe() else {
        return 1;
    };
    let Ok(mut child) = Command::new(exe)
        .arg(LEAF_ARG)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return 1;
    };
    let tmp = marker.with_extension("tmp");
    let reported =
        std::fs::write(&tmp, child.id().to_string()).and_then(|()| std::fs::rename(&tmp, marker));
    if reported.is_err() {
        let _ = std::fs::remove_file(&tmp);
        let _ = child.kill();
        let _ = child.wait();
        return 1;
    }
    // blocks without a clock until the stop ends the child (or it ends itself)
    let _ = child.wait();
    0
}

/// `aibd __check-leaf`: stay alive for the stop to meet, at most
/// `selfcheck.leaf_life` (30 s, one wake-up), so a leaf a stop missed still
/// ends by itself.
pub fn run_leaf() -> u8 {
    let op = crate::timers::SELFCHECK_LEAF_LIFE.begin(None);
    while op.tick() {}
    0
}
