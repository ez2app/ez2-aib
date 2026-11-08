//! TEST FIXTURE ONLY — not a provider and never presented as one.
//!
//! Builds the awkward process trees that real CLIs produced in the provider
//! matrix (ADR 0004), so aibd's cleanup can be tested without a model call:
//!
//!   complete      print {"type":"started"}, {"type":"completed"}; exit 0
//!   no-complete   print {"type":"started"}; exit 0 (Codex-after-SIGTERM shape)
//!   tree          main ignores SIGINT/SIGTERM and starts:
//!                   child    same process group, ignores SIGTERM
//!                   escapee  own process group (like Codex's detached tool child)
//!                   orphan   own group, its parent exits at once (re-parented)
//!                 then prints {"type":"ready","pids":{...}} and sleeps.
//!   tree-stable   as `tree` without the orphan: no intermediate that exits at
//!                 once, so whether lineage is complete does not depend on load
//!   tree-platform main ignores SIGINT/SIGTERM; a short-lived parent starts
//!                 the system `/bin/sleep` in its own group and exits at once
//!                 (the shape real Claude Code's Bash tool leaked, ADR 0004
//!                 update). Apple platform binaries hide their environment,
//!                 so the marker sweep cannot see it; only the fork watcher
//!                 can. Unix only.
//!
//! Every process exits by itself after AIB_FIXTURE_TTL seconds (default 60), so
//! a failing test cannot leave processes behind for long.

// A test fixture sleeps by design; the ADR 0005 timer rule is for aibd.
#![allow(clippy::disallowed_methods)]

use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};

fn ttl() -> Duration {
    let s = std::env::var("AIB_FIXTURE_TTL")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(60);
    Duration::from_secs(s)
}

fn ignore(sigs: &[i32]) {
    let flag = Arc::new(AtomicBool::new(false));
    for s in sigs {
        // Registering a handler replaces the default "terminate" action.
        let _ = signal_hook::flag::register(*s, flag.clone());
    }
}

fn sleep_until_ttl() -> ! {
    let end = Instant::now() + ttl();
    while Instant::now() < end {
        std::thread::sleep(Duration::from_millis(100));
    }
    std::process::exit(0)
}

fn emit(v: serde_json::Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{v}");
    let _ = out.flush();
}

fn me() -> PathBuf {
    std::env::current_exe().unwrap_or_else(|_| PathBuf::from("aib-fixture-tree"))
}

fn spawn_role(role: &str, own_group: bool, piddir: &std::path::Path) {
    let mut c = Command::new(me());
    c.arg(role)
        .env("AIB_FIXTURE_PIDDIR", piddir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    if own_group {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    #[cfg(not(unix))]
    let _ = own_group;
    let _ = c.spawn();
}

fn write_pid(role: &str) {
    if let Ok(d) = std::env::var("AIB_FIXTURE_PIDDIR") {
        let p = PathBuf::from(d).join(role);
        let _ = std::fs::write(&p, std::process::id().to_string());
    }
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    match mode.as_str() {
        "complete" => {
            emit(serde_json::json!({"type": "started"}));
            emit(serde_json::json!({"type": "completed"}));
        }
        "no-complete" => emit(serde_json::json!({"type": "started"})),
        "tree" | "tree-stable" => {
            ignore(&[signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM]);
            let piddir = std::env::temp_dir().join(format!("aib-fixture-{}", std::process::id()));
            let _ = std::fs::create_dir_all(&piddir);
            spawn_role("child", false, &piddir);
            spawn_role("escapee", true, &piddir);
            let stable = mode == "tree-stable";
            if !stable {
                spawn_role("orphan-parent", false, &piddir);
            }
            let roles: &[&str] = if stable {
                &["child", "escapee"]
            } else {
                &["child", "escapee", "orphan"]
            };
            let deadline = Instant::now() + Duration::from_secs(10);
            let mut pids = serde_json::Map::new();
            while Instant::now() < deadline && pids.len() < roles.len() {
                for r in roles.iter().copied() {
                    if let Ok(s) = std::fs::read_to_string(piddir.join(r)) {
                        if let Ok(n) = s.trim().parse::<u32>() {
                            pids.insert(r.into(), n.into());
                        }
                    }
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            let _ = std::fs::remove_dir_all(&piddir);
            pids.insert("main".into(), std::process::id().into());
            emit(serde_json::json!({"type": "ready", "pids": pids}));
            sleep_until_ttl();
        }
        "child" => {
            ignore(&[signal_hook::consts::SIGTERM]);
            write_pid("child");
            sleep_until_ttl();
        }
        "escapee" => {
            ignore(&[signal_hook::consts::SIGTERM]);
            write_pid("escapee");
            sleep_until_ttl();
        }
        "orphan-parent" => {
            if let Ok(d) = std::env::var("AIB_FIXTURE_PIDDIR") {
                spawn_role("orphan", true, std::path::Path::new(&d));
            }
            // exit at once: the orphan is re-parented and drops out of the ppid tree
        }
        "orphan" => {
            write_pid("orphan");
            sleep_until_ttl();
        }
        "tree-platform" => {
            ignore(&[signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM]);
            let piddir = std::env::temp_dir().join(format!("aib-fixture-{}", std::process::id()));
            let _ = std::fs::create_dir_all(&piddir);
            spawn_role("platform-parent", false, &piddir);
            let deadline = Instant::now() + Duration::from_secs(10);
            let mut orphan = None;
            while Instant::now() < deadline && orphan.is_none() {
                orphan = std::fs::read_to_string(piddir.join("platform-orphan"))
                    .ok()
                    .and_then(|s| s.trim().parse::<u32>().ok());
                std::thread::sleep(Duration::from_millis(20));
            }
            let _ = std::fs::remove_dir_all(&piddir);
            emit(serde_json::json!({"type": "ready", "pids": {
                "main": std::process::id(),
                "platform_orphan": orphan,
            }}));
            sleep_until_ttl();
        }
        "platform-parent" => {
            // start /bin/sleep in its own group, record its pid, exit at once
            let mut c = Command::new("/bin/sleep");
            c.arg(ttl().as_secs().to_string())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                c.process_group(0);
            }
            if let Ok(ch) = c.spawn() {
                if let Ok(d) = std::env::var("AIB_FIXTURE_PIDDIR") {
                    let _ = std::fs::write(
                        PathBuf::from(d).join("platform-orphan"),
                        ch.id().to_string(),
                    );
                }
            }
        }
        _ => {
            eprintln!("usage: aib-fixture-tree complete|no-complete|tree|tree-platform");
            std::process::exit(2);
        }
    }
}
