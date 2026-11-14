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
//!   tree-plain    as `tree-platform`, but the re-parented process is this
//!                 fixture (a regular program: its environment, marker
//!                 included, is readable), and its parent stays 300 ms so the
//!                 fork watcher records it. Unix only.
//!
//!   smoke-tree <marker> <stop>
//!                 portable (macOS, Linux, Windows), no signal tricks: starts one
//!                 child (`smoke-wait <stop>`), writes "<own pid> <child pid>"
//!                 to <marker> atomically, then waits. Both exit by themselves
//!                 once <stop> exists, or after 600 s. The release sessions smoke
//!                 starts this (M1 gate, 043dd 11-09: a shell or interpreter can
//!                 no longer be a session's program, so the Python helper cannot).
//!   smoke-wait <stop>
//!                 wait until <stop> exists (600 s at most).
//!   hold-gc <pidfile> <root_ms> <code>
//!                 portable, no signal tricks: starts one child (`gc-hold`),
//!                 writes the child's pid to <pidfile> atomically (tmp + rename),
//!                 then exits with <code> after <root_ms> ms; <root_ms> 0 = stay
//!                 until AIB_FIXTURE_TTL (then exit <code>). The child stays until
//!                 AIB_FIXTURE_TTL. The Windows sessions tests start this (#745,
//!                 Andy 11-14: no PowerShell; ADR 0003 update 11-09: a shell or
//!                 interpreter is never on the list).
//!   gc-hold       stay until AIB_FIXTURE_TTL, then exit 0.
//!
//!   setuid-ps-burst
//!                 G1 (aib-verify, 1ced7 11-10): run the setuid `/bin/ps -axww`
//!                 30 times in a row (50 ms apart), each waited for, then exit 0.
//!                 A single short `ps` ends before the fork watcher reads it and
//!                 does not reproduce G1 (11-09: the old head passed it), so the
//!                 count and the arguments are fixed here, not taken from argv.
//!   setuid-ps-stuck <pidfile>
//!                 G1 counterexample (043dd's recipe): a child runs the setuid
//!                 `/bin/ps -E -ww` with a 70 000-byte env through
//!                 `sh -c "trap '' TERM HUP INT; exec …"` (a real SIG_IGN, kept
//!                 across exec). Its stdout is a pipe whose read end ps itself
//!                 also holds (as stdin), so it never gets SIGPIPE: it blocks once
//!                 the pipe is full and only SIGKILL ends it, also after this
//!                 fixture is gone. Writes the ps pid to <pidfile> atomically, then
//!                 sleeps until AIB_FIXTURE_TTL. The caller must SIGKILL that pid.
//!
//! With AIB_FIXTURE_GO=<path>, `tree-platform` / `tree-plain` start nothing
//! until that file exists (the test creates it once `spawn_tree` returned, so
//! the fork watcher is on the root before the first fork). No go within
//! 10 s ⇒ print {"type":"error","reason":"no_go"} and exit 3, never fork late.
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

/// Wait for AIB_FIXTURE_GO (when set). false = it never came.
fn wait_go() -> bool {
    let Ok(p) = std::env::var("AIB_FIXTURE_GO") else {
        return true;
    };
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if std::path::Path::new(&p).exists() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    false
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

/// Poll for `stop` every 200 ms; give up after 600 s.
fn wait_for_stop(stop: &std::path::Path) {
    let deadline = Instant::now() + Duration::from_secs(600);
    while !stop.exists() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(200));
    }
}

/// `smoke-tree`: one child, the marker, then wait. rc 1 on an I/O error.
fn smoke_tree(marker: &str, stop: &str) -> std::io::Result<()> {
    let exe = std::env::current_exe()?;
    let mut child = Command::new(exe)
        .args(["smoke-wait", stop])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let tmp = format!("{marker}.tmp");
    std::fs::write(&tmp, format!("{} {}", std::process::id(), child.id()))?;
    std::fs::rename(&tmp, marker)?;
    wait_for_stop(std::path::Path::new(stop));
    // the child polls the same stop file, so this returns within about 200 ms
    child.wait()?;
    Ok(())
}

/// `hold-gc`: one child that outlives this root (see the header).
fn hold_gc(pidfile: &str, root_ms: u64, code: i32) -> std::io::Result<()> {
    let exe = std::env::current_exe()?;
    let child = Command::new(exe)
        .arg("gc-hold")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let tmp = format!("{pidfile}.tmp");
    std::fs::write(&tmp, child.id().to_string())?;
    std::fs::rename(&tmp, pidfile)?;
    // the child is never waited for: it must stay behind when the root exits
    drop(child);
    let end = Instant::now()
        + if root_ms == 0 {
            ttl()
        } else {
            Duration::from_millis(root_ms)
        };
    while Instant::now() < end {
        std::thread::sleep(Duration::from_millis(20));
    }
    std::process::exit(code)
}

/// `setuid-ps-burst`: 30 setuid `ps` runs in a row (see the header).
fn setuid_ps_burst() -> std::io::Result<()> {
    for _ in 0..30 {
        Command::new("/bin/ps")
            .args(["-axww", "-o", "pid,uid,command"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()?;
        std::thread::sleep(Duration::from_millis(50));
    }
    Ok(())
}

/// `setuid-ps-stuck <pidfile>`: leave a setuid `ps` blocked on a full pipe (see the header).
// Never waited on by design: the ps must outlive this fixture; the caller SIGKILLs it.
#[allow(clippy::zombie_processes)]
fn setuid_ps_stuck(pidfile: &str) -> std::io::Result<()> {
    let (read_end, write_end) = std::io::pipe()?;
    let child = Command::new("/bin/sh")
        .args([
            "-c",
            "trap '' TERM HUP INT; exec /bin/ps -E -ww -o command -U \"$(/usr/bin/id -u)\"",
            "sh",
        ])
        .env_clear()
        .env("BIG", "x".repeat(70_000))
        .stdin(read_end)
        .stdout(write_end)
        .stderr(Stdio::null())
        .spawn()?;
    let tmp = format!("{pidfile}.tmp");
    std::fs::write(&tmp, child.id().to_string())?;
    std::fs::rename(&tmp, pidfile)?;
    sleep_until_ttl()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match (args.get(1).map(String::as_str), args.len()) {
        (Some("smoke-tree"), 4) => {
            if let Err(e) = smoke_tree(&args[2], &args[3]) {
                eprintln!("aib-fixture-tree smoke-tree: {e}");
                std::process::exit(1);
            }
            return;
        }
        (Some("smoke-wait"), 3) => {
            wait_for_stop(std::path::Path::new(&args[2]));
            return;
        }
        (Some("hold-gc"), 5) => {
            let (Ok(ms), Ok(code)) = (args[3].parse::<u64>(), args[4].parse::<i32>()) else {
                eprintln!("aib-fixture-tree hold-gc: <root_ms> and <code> must be integers");
                std::process::exit(2);
            };
            if let Err(e) = hold_gc(&args[2], ms, code) {
                eprintln!("aib-fixture-tree hold-gc: {e}");
                std::process::exit(1);
            }
            return;
        }
        (Some("gc-hold"), 2) => sleep_until_ttl(),
        (Some("setuid-ps-burst"), 2) => {
            if let Err(e) = setuid_ps_burst() {
                eprintln!("aib-fixture-tree setuid-ps-burst: {e}");
                std::process::exit(1);
            }
            return;
        }
        (Some("setuid-ps-stuck"), 3) => {
            if let Err(e) = setuid_ps_stuck(&args[2]) {
                eprintln!("aib-fixture-tree setuid-ps-stuck: {e}");
                std::process::exit(1);
            }
            return;
        }
        _ => {}
    }
    let mode = args.get(1).cloned().unwrap_or_default();
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
        "tree-platform" | "tree-plain" => {
            ignore(&[signal_hook::consts::SIGINT, signal_hook::consts::SIGTERM]);
            let piddir = std::env::temp_dir().join(format!("aib-fixture-{}", std::process::id()));
            let _ = std::fs::create_dir_all(&piddir);
            let (role, file) = if mode == "tree-plain" {
                ("plain-parent", "orphan")
            } else {
                ("platform-parent", "platform-orphan")
            };
            if !wait_go() {
                let _ = std::fs::remove_dir_all(&piddir);
                emit(serde_json::json!({"type": "error", "reason": "no_go"}));
                std::process::exit(3);
            }
            spawn_role(role, false, &piddir);
            let deadline = Instant::now() + Duration::from_secs(10);
            let mut orphan = None;
            while Instant::now() < deadline && orphan.is_none() {
                orphan = std::fs::read_to_string(piddir.join(file))
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
        "plain-parent" => {
            if let Ok(d) = std::env::var("AIB_FIXTURE_PIDDIR") {
                spawn_role("orphan", true, std::path::Path::new(&d));
            }
            // stay long enough for the fork watcher to list this child
            std::thread::sleep(Duration::from_millis(300));
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
            eprintln!("usage: aib-fixture-tree complete|no-complete|tree|tree-stable|tree-platform|tree-plain");
            std::process::exit(2);
        }
    }
}
