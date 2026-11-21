#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Round 2 (519d0 11-20 02:27, b4297's row): a reviewer's tree read reaches the page.
//! `apps/web/test/tree.test.cjs` loads `tree.js` under node and checks the diff colouring
//! escapes what a seat wrote, refusals read as words, the seat id is encoded into the URL,
//! the click is taken in the capture phase, nothing inline-styled (the page CSP), and that
//! `index.html` and `ws.js` wire it (card + seat strip). Without node it fails unless
//! AIB_PAGE_TESTS_NO_NODE is set, as page_load_registry does.

use std::path::{Path, PathBuf};
use std::process::Command;

fn web() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web")
}

#[test]
fn the_page_shows_a_seats_tree_read_only() {
    let script = web().join("test/tree.test.cjs");
    let out = match Command::new("node").arg(&script).output() {
        Ok(o) => o,
        Err(e) if std::env::var_os("AIB_PAGE_TESTS_NO_NODE").is_some() => {
            eprintln!("skip: node not found ({e}); AIB_PAGE_TESTS_NO_NODE is set");
            return;
        }
        Err(e) => panic!(
            "node not found ({e}): the page tree test needs it; \
             set AIB_PAGE_TESTS_NO_NODE=1 to skip on purpose"
        ),
    };
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "tree.js test failed:\n{text}");
    assert!(text.contains("全部通過"), "{text}");
}
