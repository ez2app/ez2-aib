#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! 519d0 ORDER-webonly r2 item 13 (043dd GAPS-webseat-page): a web seat cannot open a browser
//! to try the page, so what can be checked without one is a test. `apps/web/test/ws-static.test.cjs`
//! parses `ws.js`, checks every rendered `data-*` button has a handler, every act type the page
//! sends is one aibd knows (or is listed as not yet and marked 示意 on the page), every refusal
//! code of `repo_register` / a repo seat has words, and the fingerprint wait resends the same
//! body with the ticket. Without node it fails unless AIB_PAGE_TESTS_NO_NODE is set, as
//! page_tree does.

use std::path::{Path, PathBuf};
use std::process::Command;

fn web() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web")
}

#[test]
fn the_page_is_checked_without_a_browser() {
    let script = web().join("test/ws-static.test.cjs");
    let out = match Command::new("node").arg(&script).output() {
        Ok(o) => o,
        Err(e) if std::env::var_os("AIB_PAGE_TESTS_NO_NODE").is_some() => {
            eprintln!("skip: node not found ({e}); AIB_PAGE_TESTS_NO_NODE is set");
            return;
        }
        Err(e) => panic!(
            "node not found ({e}): the page static test needs it; \
             set AIB_PAGE_TESTS_NO_NODE=1 to skip on purpose"
        ),
    };
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "ws.js static test failed:\n{text}");
    assert!(text.contains("全部通過"), "{text}");
}
