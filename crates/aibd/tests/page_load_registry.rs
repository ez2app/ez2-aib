#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! §19h (Andy 11-18 22:4x): what the opening dialog loads is a registry, not three fixed steps.
//! `apps/web/test/load-registry.test.cjs` lifts the pure section of `ws.js` and checks that
//! registering one fake item adds one row and one to the denominator. The page script is
//! JavaScript, so the check runs under node; a machine without node skips it with a note
//! (the dist files are still checked for the markers below).

use std::path::{Path, PathBuf};
use std::process::Command;

fn web() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web")
}

#[test]
fn the_page_keeps_its_load_registry_section() {
    let js = std::fs::read_to_string(web().join("dist/ws.js")).unwrap();
    assert!(js.contains("// @load-registry-begin"));
    assert!(js.contains("// @load-registry-end"));
    assert!(
        !js.contains("BOOT_STEPS"),
        "the opening steps must come from loadReg, not a fixed list"
    );
}

#[test]
fn registering_a_fake_item_adds_a_row_and_a_step() {
    let script = web().join("test/load-registry.test.cjs");
    let out = match Command::new("node").arg(&script).output() {
        Ok(o) => o,
        Err(e) => {
            eprintln!("skip: node not found ({e}); the page registry test needs it");
            return;
        }
    };
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "load-registry test failed:\n{text}");
    assert!(text.contains("全部通過"), "{text}");
}
