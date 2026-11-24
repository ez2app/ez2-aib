#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! The built-in page's 「⏱ 節省時間」 and 「外掛」 blocks (PLAN-skipstop-master F2 / H1b, c5d88
//! 11-23). `apps/web/test/saved-blocks.test.cjs` lifts that section of `ws.js` and runs it
//! under node with made-up answers: nothing is drawn or asked before the acts are known, a
//! state that is not `ok` says its own words and draws no boxes and no zeros, the boxes show
//! time on the clock, what a plugin's file says is escaped, a switch has no button when it
//! cannot be moved, and the switch sends the name and on/off only. Without node it fails
//! unless AIB_PAGE_TESTS_NO_NODE is set, as page_tree does.

use std::path::{Path, PathBuf};
use std::process::Command;

fn web() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web")
}

#[test]
fn the_page_draws_savings_and_plugins_only_from_what_it_was_told() {
    let script = web().join("test/saved-blocks.test.cjs");
    let out = match Command::new("node").arg(&script).output() {
        Ok(o) => o,
        Err(e) if std::env::var_os("AIB_PAGE_TESTS_NO_NODE").is_some() => {
            eprintln!("skip: node not found ({e}); AIB_PAGE_TESTS_NO_NODE is set");
            return;
        }
        Err(e) => panic!(
            "node not found ({e}): the page blocks test needs it; \
             set AIB_PAGE_TESTS_NO_NODE=1 to skip on purpose"
        ),
    };
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "saved-blocks test failed:\n{text}");
    assert!(text.contains("全部通過"), "{text}");
}
