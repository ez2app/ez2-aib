#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! REAL Claude Code CLI smoke (VERIFIED-mac evidence, not a CI test).
//! Ignored by default; makes one or two real model calls on the user's own
//! login. Run explicitly:
//!
//!   AIBD_REAL_CLAUDE=1 cargo test -p aibd --test claude_smoke -- --ignored --nocapture --test-threads 1
#![cfg(unix)]

use aib_core::outcome::{decide, Observed};
use aib_core::state::SessionState;
use aibd::platform::tree::{spawn_tree, SpawnSpec};
use std::io::{BufRead, BufReader};
use std::process::Command;
use std::time::{Duration, Instant};

const PROMPT: &str = "Use the Bash tool to run exactly this command once: \
python3 -c \"import subprocess; subprocess.Popen(['sleep','47'], start_new_session=True)\" \
-- then reply with the single word DONE.";

fn enabled() -> bool {
    std::env::var("AIBD_REAL_CLAUDE").as_deref() == Ok("1")
}

fn spec(session: &str, dir: &std::path::Path) -> SpawnSpec {
    SpawnSpec {
        program: "claude".into(),
        args: [
            "-p",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--model",
            "haiku",
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools",
            "Bash",
            // --allowedTools takes several values; `--` stops it from eating the prompt
            "--",
            PROMPT,
        ]
        .iter()
        .map(Into::into)
        .collect(),
        cwd: Some(dir.to_path_buf()),
        env: vec![],
        session_id: session.into(),
    }
}

fn comm(pid: u32) -> String {
    let o = Command::new("ps")
        .args(["-o", "comm=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

fn running(pid: u32) -> bool {
    let o = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
    !s.is_empty() && !s.starts_with('Z')
}

/// Reads events until `stop(event)` is true or the stream ends.
fn read_until(
    out: std::process::ChildStdout,
    deadline: Duration,
    mut stop: impl FnMut(&serde_json::Value) -> bool,
) -> Vec<serde_json::Value> {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        for l in BufReader::new(out).lines().map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&l) {
                if tx.send(v).is_err() {
                    break;
                }
            }
        }
    });
    let end = Instant::now() + deadline;
    let mut seen = Vec::new();
    while let Ok(v) = rx.recv_timeout(end.saturating_duration_since(Instant::now())) {
        let done = stop(&v);
        seen.push(v);
        if done {
            break;
        }
    }
    seen
}

fn is_tool_result(v: &serde_json::Value) -> bool {
    v["type"] == "user"
        && v["message"]["content"]
            .as_array()
            .is_some_and(|c| c.iter().any(|x| x["type"] == "tool_result"))
}

fn detached_sleep(known: &[u32]) -> Option<u32> {
    known.iter().copied().find(|p| comm(*p).ends_with("sleep"))
}

#[test]
#[ignore]
fn cancel_after_tool_spawned_a_detached_child() {
    if !enabled() {
        eprintln!("skipped: set AIBD_REAL_CLAUDE=1");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let mut h = spawn_tree(&spec("smoke-cancel", dir.path())).unwrap();
    let out = h.take_stdout().unwrap();
    let evs = read_until(out, Duration::from_secs(120), is_tool_result);
    assert!(
        evs.iter().any(is_tool_result),
        "no tool_result event: {evs:?}"
    );
    h.sweep_marker().unwrap();
    let sleep = detached_sleep(&h.known()).expect("detached sleep not found in tree");
    assert!(running(sleep));
    let rep = h.cancel(Duration::from_secs(3)).unwrap();
    eprintln!("cancel report: {rep:?}");
    assert!(rep.tree_clean(), "{rep:?}");
    assert!(!running(sleep), "detached sleep {sleep} survived");
}

#[test]
#[ignore]
fn natural_finish_is_completed_and_leftovers_are_cleaned() {
    if !enabled() {
        eprintln!("skipped: set AIBD_REAL_CLAUDE=1");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let mut h = spawn_tree(&spec("smoke-finish", dir.path())).unwrap();
    let out = h.take_stdout().unwrap();
    let evs = read_until(out, Duration::from_secs(180), |v| v["type"] == "result");
    let code = h.wait_root(Duration::from_secs(30)).unwrap();
    let completed = evs
        .iter()
        .any(|v| v["type"] == "result" && v["subtype"] == "success");
    h.sweep_marker().unwrap();
    let sleep = detached_sleep(&h.known());
    let rep = h.cleanup(Duration::from_secs(3)).unwrap();
    eprintln!("exit={code:?} completed={completed} leftover_sleep={sleep:?} report={rep:?}");
    let o = decide(Observed {
        completed_event: completed,
        exit_code: code,
        tree_clean: rep.tree_clean(),
        ..Default::default()
    });
    assert_eq!(o.state, SessionState::Completed, "{o:?}");
    if let Some(s) = sleep {
        assert!(!running(s), "leftover sleep {s} survived cleanup");
    }
}
