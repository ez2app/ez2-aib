// SPDX-License-Identifier: Apache-2.0
//! `aib-consent-run supervise` as the Python half calls it, with a staged program.
#![allow(clippy::disallowed_methods)] // the test changes the copy after a short sleep (ADR 0005 named exception)

use aib_consent::params::sha256_hex;
use serde_json::Value;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_aib-consent-run");

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!(
        "aib-consent-cli-{}-{}-{}",
        name,
        std::process::id(),
        Instant::now().elapsed().as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).expect("scratch");
    std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700)).expect("0700");
    d
}

fn supervise(d: &Path, copy: &Path, sha: &str, argv: &[&str]) -> Command {
    let mut c = Command::new(BIN);
    c.args(["supervise", "--id", "t1", "--log"])
        .arg(d.join("job.log"))
        .arg("--state")
        .arg(d.join("job.state.json"))
        .arg("--stop-request")
        .arg(d.join("job.stop"))
        .arg("--cwd")
        .arg(d)
        .args([
            "--ask-json",
            r#"["/bin/sh","-c","exit 1"]"#,
            "--program-copy",
        ])
        .arg(copy)
        .args(["--program-sha", sha, "--"])
        .args(argv);
    c
}

fn record(out: &std::process::Output) -> Value {
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.lines().last().unwrap_or("")).expect("one JSON record")
}

// knife: the program runs from the bytes that were checked, not from the copy's path
#[test]
fn a_copy_changed_after_the_check_is_not_run() {
    let d = scratch("changed-later");
    let copy = d.join("prog.sh");
    let body = b"sleep 1\necho first\n";
    std::fs::write(&copy, body).expect("copy");
    let sha = sha256_hex(body);
    let copy_s = copy.to_string_lossy().into_owned();
    let child = supervise(&d, &copy, &sha, &["/bin/bash", &copy_s])
        .stdout(std::process::Stdio::piped())
        .spawn()
        .expect("spawn");
    // bash is still in `sleep 1`: add a line to the copy (2240e #661 M2 repro)
    std::thread::sleep(Duration::from_millis(400));
    let mut f = std::fs::OpenOptions::new()
        .append(true)
        .open(&copy)
        .expect("append");
    std::io::Write::write_all(&mut f, b"echo appended\n").expect("append");
    drop(f);
    let out = child.wait_with_output().expect("wait");
    let r = record(&out);
    assert_eq!(r["result"], "exited", "{r}");
    assert_eq!(r["rc"], 0, "{r}");
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("first"), "{log}");
    assert!(
        !log.contains("appended"),
        "the line added after the check ran: {log}"
    );
}

// knife: the copy is checked again right before the run
#[test]
fn a_copy_changed_before_the_run_is_refused() {
    let d = scratch("changed-before");
    let copy = d.join("prog.sh");
    std::fs::write(&copy, b"echo approved\n").expect("copy");
    let sha = sha256_hex(b"echo approved\n");
    std::fs::write(&copy, b"echo something else\n").expect("change");
    let copy_s = copy.to_string_lossy().into_owned();
    let out = supervise(&d, &copy, &sha, &["/bin/bash", &copy_s])
        .output()
        .expect("run");
    let r = record(&out);
    assert_eq!(out.status.code(), Some(1), "{r}");
    assert_eq!(r["result"], "failed", "{r}");
    assert_eq!(r["why"], "staged_hash_changed", "{r}");
    assert!(
        !d.join("job.log").exists()
            || std::fs::read_to_string(d.join("job.log"))
                .unwrap_or_default()
                .is_empty()
    );
}

#[test]
fn the_copy_must_be_what_is_run() {
    let d = scratch("not-run");
    let copy = d.join("prog.sh");
    std::fs::write(&copy, b"echo approved\n").expect("copy");
    let sha = sha256_hex(b"echo approved\n");
    let out = supervise(&d, &copy, &sha, &["/bin/bash", "-c", "echo other"])
        .output()
        .expect("run");
    let r = record(&out);
    assert_eq!(out.status.code(), Some(2), "{r}");
    assert_eq!(r["why"], "program_copy_not_run", "{r}");
}
