//! Shared by the aibd integration tests: the test-only `aib-fixture-tree` program
//! (S55: the session tests start it instead of `/bin/sh -c …`, so their policy lists
//! one native program and keeps `interpreters_ok` false, the policy aibd really uses).
// each test file uses a part of this module
#![allow(dead_code)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aibd::allowlist::{Program, SessionPolicy};
use std::path::PathBuf;

/// Path of the fixture binary, built fresh on first use. `cargo test` does not
/// rebuild another package's binary, so a stale fixture from an earlier build
/// would otherwise be run.
pub fn fixture() -> PathBuf {
    static BUILD: std::sync::Once = std::sync::Once::new();
    // target/<profile>/deps/<test-bin>
    let exe = std::env::current_exe().unwrap();
    let profile_dir = exe.parent().unwrap().parent().unwrap().to_path_buf();
    let target_dir = profile_dir.parent().unwrap().to_path_buf();
    BUILD.call_once(|| {
        let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
        let mut c = std::process::Command::new(cargo);
        c.args([
            "build",
            "-q",
            "--locked",
            "-p",
            "aib-fixture-tree",
            "--target-dir",
        ])
        .arg(&target_dir)
        .current_dir(env!("CARGO_MANIFEST_DIR"));
        if profile_dir.file_name().is_some_and(|n| n == "release") {
            c.arg("--release");
        }
        let st = c.status().unwrap();
        assert!(st.success(), "building aib-fixture-tree failed: {st}");
    });
    let p = profile_dir.join(format!("aib-fixture-tree{}", std::env::consts::EXE_SUFFIX));
    assert!(p.exists(), "{} missing after build", p.display());
    p
}

/// The fixture's path as a string (argv[0] of a session).
pub fn fx() -> String {
    fixture().display().to_string()
}

/// The fixture's argv: `[fixture, args…]`.
pub fn fx_argv(args: &[&str]) -> Vec<String> {
    std::iter::once(fx())
        .chain(args.iter().map(|a| (*a).to_string()))
        .collect()
}

/// A `POST /sessions` body `{"argv": [fixture, args…]}`.
pub fn fx_body(args: &[&str]) -> String {
    serde_json::json!({ "argv": fx_argv(args) }).to_string()
}

/// The one-program policy: only the fixture, and no interpreter.
pub fn fx_policy() -> SessionPolicy {
    SessionPolicy {
        programs: vec![Program {
            name: "aib-fixture-tree".into(),
            path: fixture(),
            signer: None,
        }],
        env: vec![],
        interpreters_ok: false,
    }
}
