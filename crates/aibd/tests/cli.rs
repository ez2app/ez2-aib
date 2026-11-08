//! The real binary: `--version`, `selfcheck`, bad arguments (A1, preview.1).
use std::process::Command;

fn aibd(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_aibd"))
        .args(args)
        .env(
            "AIBD_DATA_DIR",
            std::env::temp_dir().join("aibd-cli-test-never-written"),
        )
        .env_remove("AIBD_BIND")
        .env_remove("AIBD_ALLOWED_ORIGINS")
        .output()
        .expect("run aibd")
}

#[test]
fn version_prints_the_workspace_version_and_platform() {
    let o = aibd(&["--version"]);
    assert!(o.status.success());
    let s = String::from_utf8_lossy(&o.stdout);
    assert_eq!(
        s.trim(),
        format!(
            "aibd {} {}-{}",
            env!("CARGO_PKG_VERSION"),
            std::env::consts::OS,
            std::env::consts::ARCH
        )
    );
}

#[test]
fn selfcheck_json_lists_every_row_and_never_claims_process_management() {
    let o = aibd(&["selfcheck", "--json"]);
    let v: serde_json::Value = serde_json::from_slice(&o.stdout).expect("json");
    let rows = v.as_array().expect("array");
    let status = |name: &str| {
        rows.iter()
            .find(|r| r["name"] == name)
            .map(|r| r["status"].as_str().unwrap_or("").to_string())
            .unwrap_or_default()
    };
    for name in [
        "version",
        "config",
        "lineage_m1",
        "sigchld",
        "process_management",
        "windows_job",
    ] {
        assert!(!status(name).is_empty(), "missing row {name}");
    }
    assert_eq!(status("process_management"), "not_wired");
    assert_eq!(status("config"), "ok");
    if cfg!(target_os = "macos") {
        // DECISION-23: never "ok" (clean) on macOS
        assert_ne!(status("lineage_m1"), "ok");
    }
    if cfg!(target_os = "macos") {
        // the test runner keeps zombies (aib-sys tests/sigchld.rs pins the other cases)
        assert_eq!(status("sigchld"), "ok");
    }
    if cfg!(windows) {
        assert_eq!(status("windows_job"), "unverified");
    }
    let failed = rows
        .iter()
        .any(|r| r["status"].as_str().unwrap_or("").starts_with("failed:"));
    assert_eq!(o.status.code(), Some(i32::from(failed)));
}

#[test]
fn selfcheck_text_ends_with_a_summary() {
    let o = aibd(&["selfcheck"]);
    let s = String::from_utf8_lossy(&o.stdout);
    assert!(
        s.lines().last().unwrap_or("").starts_with("summary: "),
        "{s}"
    );
}

#[test]
fn an_unknown_argument_is_rc_2_and_starts_nothing() {
    let o = aibd(&["--port", "1"]);
    assert_eq!(o.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&o.stderr).contains("usage: aibd"));
    assert!(o.stdout.is_empty());
}
