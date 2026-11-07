//! aib-forkcount — count forks under monitored process trees (DECISION-3,
//! 2026-11-07). A measurement tool for the legacy-AIB before / after
//! comparison; not shipped, not part of aibd.
//!
//! The metric is "forks per second under the monitored trees", reported as a
//! **lower bound**, never as "new programs" (fork and exec are not one to
//! one). Every report carries a coverage table (roots, how they were found,
//! how they are watched, exclusions, known blind spots). `calibrate` measures
//! the capture rate against a generator whose exact number of process
//! creations is known.
//!
//!   aib-forkcount run --seconds 300 [--tmux-socket PATH]... [--all-tmux] [--pid PID]... [--label TEXT]
//!   aib-forkcount calibrate --pattern single|grandchild|mid-exit|parallel|burst --count N
//!
//! Pacing uses plain sleeps: this is a measurement tool, not aibd, so the
//! ADR 0005 timer rule does not apply here.
#![allow(clippy::disallowed_methods)]

use aib_procwatch::{program_name, start_time, ForkWatch, Stats};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const BLIND_SPOTS: &[&str] = &[
    "kqueue coalesces several forks of one parent between two reads into one event: fork_events is a lower bound",
    "a child that forks and exits before it is listed is never watched; its children are not seen: children_adopted is a lower bound",
    "processes that leave the monitored trees through a parent outside them (e.g. launchd-started agents) are not counted",
    "python painters' own children and Claude Code's statusline are only partly visible here; use the D ledger (per-item attribution) for them",
];

struct Root {
    pid: u32,
    source: String,
}

fn arg_values(args: &[String], flag: &str) -> Vec<String> {
    args.windows(2)
        .filter(|w| w[0] == flag)
        .map(|w| w[1].clone())
        .collect()
}

fn has(args: &[String], flag: &str) -> bool {
    args.iter().any(|a| a == flag)
}

fn uid() -> String {
    Command::new("/usr/bin/id")
        .arg("-u")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Sockets of every tmux server of this user in the default places.
fn all_tmux_sockets() -> Vec<PathBuf> {
    let u = uid();
    let mut dirs = vec![PathBuf::from(format!("/private/tmp/tmux-{u}"))];
    if let Some(d) = std::env::var_os("TMUX_TMPDIR") {
        dirs.insert(0, PathBuf::from(d).join(format!("tmux-{u}")));
    }
    let mut out = Vec::new();
    for d in dirs {
        if let Ok(rd) = std::fs::read_dir(&d) {
            for e in rd.flatten() {
                out.push(e.path());
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

/// Read-only tmux queries: server pid and every pane's pid. Never changes
/// the server.
fn tmux_roots(sock: &Path) -> Vec<Root> {
    let q = |fmt: &[&str]| -> Vec<u32> {
        Command::new("tmux")
            .arg("-S")
            .arg(sock)
            .args(fmt)
            .stderr(Stdio::null())
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| {
                String::from_utf8_lossy(&o.stdout)
                    .lines()
                    .filter_map(|l| l.trim().parse().ok())
                    .collect()
            })
            .unwrap_or_default()
    };
    let s = sock.display().to_string();
    let mut roots: Vec<Root> = q(&["display-message", "-p", "#{pid}"])
        .into_iter()
        .map(|pid| Root {
            pid,
            source: format!("tmux server {s}"),
        })
        .collect();
    roots.extend(
        q(&["list-panes", "-a", "-F", "#{pane_pid}"])
            .into_iter()
            .map(|pid| Root {
                pid,
                source: format!("pane_pid {s}"),
            }),
    );
    roots
}

fn cpu_ms() -> Value {
    // ps(1) on ourselves; forked outside the monitored trees
    let o = Command::new("/bin/ps")
        .args(["-o", "utime=,stime=", "-p", &std::process::id().to_string()])
        .output()
        .ok();
    let s = o
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    json!(s)
}

fn report(stats: &Stats, seconds: f64, tracked: usize) -> Value {
    let per_second: Vec<u64> = {
        let last = stats.per_second.keys().max().copied().unwrap_or(0);
        (0..=last)
            .map(|s| stats.per_second.get(&s).copied().unwrap_or(0))
            .collect()
    };
    json!({
        "metric": "forks per second under the monitored trees (lower bound)",
        "fork_events": stats.fork_events,
        "children_adopted": stats.children_adopted,
        "forks_per_sec_lower_bound": if seconds > 0.0 { stats.fork_events.max(stats.children_adopted) as f64 / seconds } else { 0.0 },
        "per_second": per_second,
        "by_parent": stats.by_parent,
        "register_failures": stats.register_failures,
        "list_failures": stats.list_failures,
        "tracked_processes": tracked,
    })
}

fn cmd_run(args: &[String]) -> ExitCode {
    let seconds: u64 = arg_values(args, "--seconds")
        .first()
        .and_then(|s| s.parse().ok())
        .unwrap_or(300);
    let mut sockets: Vec<PathBuf> = arg_values(args, "--tmux-socket")
        .into_iter()
        .map(PathBuf::from)
        .collect();
    if has(args, "--all-tmux") {
        sockets.extend(all_tmux_sockets());
    }
    let mut roots: Vec<Root> = sockets.iter().flat_map(|s| tmux_roots(s)).collect();
    roots.extend(
        arg_values(args, "--pid")
            .into_iter()
            .filter_map(|p| p.parse().ok())
            .map(|pid| Root {
                pid,
                source: "--pid".into(),
            }),
    );
    if roots.is_empty() {
        eprintln!("aib-forkcount: no roots (give --tmux-socket, --all-tmux or --pid)");
        return ExitCode::from(2);
    }
    let w = match ForkWatch::start(&[]) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("aib-forkcount: {e}");
            return ExitCode::from(3);
        }
    };
    let mut root_rows = Vec::new();
    for r in &roots {
        let ok = w.add_root(r.pid);
        root_rows.push(json!({
            "pid": r.pid,
            "start_time": start_time(r.pid),
            "name": program_name(r.pid),
            "source": r.source,
            "watched": ok,
        }));
    }
    let t0 = Instant::now();
    let started = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    std::thread::sleep(Duration::from_secs(seconds));
    let elapsed = t0.elapsed().as_secs_f64();
    let stats = w.stats();
    let tracked = w.snapshot().len();
    w.stop();
    let out = json!({
        "tool": "aib-forkcount",
        "version": env!("CARGO_PKG_VERSION"),
        "label": arg_values(args, "--label").first(),
        "started_unix": started,
        "seconds": elapsed,
        "coverage": {
            "roots": root_rows,
            "watch_method": "kqueue EVFILT_PROC NOTE_FORK|NOTE_EXIT on every recorded pid; children listed by parent pid on each fork, recursively",
            "excluded": ["aib-forkcount itself and its ps / tmux queries (not under the roots)"],
            "blind_spots": BLIND_SPOTS,
        },
        "result": report(&stats, elapsed, tracked),
        "tool_cpu_utime_stime": cpu_ms(),
    });
    println!("{out}");
    ExitCode::SUCCESS
}

// ---- calibration -------------------------------------------------------

fn me() -> PathBuf {
    std::env::current_exe().unwrap_or_else(|_| PathBuf::from("aib-forkcount"))
}

fn spawn_quiet(c: &mut Command) -> Option<std::process::Child> {
    c.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
}

/// Generator (child of `calibrate`). Prints the exact number of process
/// creations it caused, then exits.
fn cmd_gen(args: &[String]) -> ExitCode {
    let pattern = args.first().map(String::as_str).unwrap_or("");
    let n: u64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(20);
    let gap = Duration::from_millis(50);
    let mut created = 0u64;
    let true_bin = "/usr/bin/true";
    match pattern {
        // one direct child per step
        "single" => {
            for _ in 0..n {
                if let Some(mut c) = spawn_quiet(&mut Command::new(true_bin)) {
                    created += 1;
                    let _ = c.wait();
                }
                std::thread::sleep(gap);
            }
        }
        // child that starts one grandchild and waits for it
        "grandchild" => {
            for _ in 0..n {
                if let Some(mut c) = spawn_quiet(Command::new(me()).args(["gen-one", "wait"])) {
                    created += 2;
                    let _ = c.wait();
                }
                std::thread::sleep(gap);
            }
        }
        // child that starts /bin/sleep in its own group and exits at once
        "mid-exit" => {
            for _ in 0..n {
                if let Some(mut c) = spawn_quiet(Command::new(me()).args(["gen-one", "detach"])) {
                    created += 2;
                    let _ = c.wait();
                }
                std::thread::sleep(gap);
            }
        }
        // 4 threads of "single" at once
        "parallel" => {
            let hs: Vec<_> = (0..4)
                .map(|_| {
                    std::thread::spawn(move || {
                        let mut k = 0u64;
                        for _ in 0..n {
                            if let Some(mut c) = spawn_quiet(&mut Command::new(true_bin)) {
                                k += 1;
                                let _ = c.wait();
                            }
                            std::thread::sleep(gap);
                        }
                        k
                    })
                })
                .collect();
            created = hs.into_iter().map(|h| h.join().unwrap_or(0)).sum();
        }
        // n children started back to back, waited for afterwards
        "burst" => {
            let kids: Vec<_> = (0..n)
                .filter_map(|_| spawn_quiet(&mut Command::new(true_bin)))
                .collect();
            created = kids.len() as u64;
            for mut k in kids {
                let _ = k.wait();
            }
        }
        _ => {
            eprintln!("unknown pattern {pattern}");
            return ExitCode::from(2);
        }
    }
    println!("{created}");
    ExitCode::SUCCESS
}

fn cmd_gen_one(args: &[String]) -> ExitCode {
    match args.first().map(String::as_str) {
        Some("wait") => {
            if let Some(mut c) = spawn_quiet(&mut Command::new("/usr/bin/true")) {
                let _ = c.wait();
            }
        }
        Some("detach") => {
            let mut c = Command::new("/bin/sleep");
            c.arg("2");
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                c.process_group(0);
            }
            let _ = spawn_quiet(&mut c);
        }
        _ => return ExitCode::from(2),
    }
    ExitCode::SUCCESS
}

fn cmd_calibrate(args: &[String]) -> ExitCode {
    let pattern = arg_values(args, "--pattern")
        .first()
        .cloned()
        .unwrap_or_else(|| "single".into());
    let count = arg_values(args, "--count")
        .first()
        .cloned()
        .unwrap_or_else(|| "40".into());
    let w = match ForkWatch::start(&[]) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("aib-forkcount: {e}");
            return ExitCode::from(3);
        }
    };
    // The generator waits on stdin so the watch is in place before it forks.
    let mut gen = match Command::new(me())
        .args(["gen-wait", &pattern, &count])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(g) => g,
        Err(e) => {
            eprintln!("aib-forkcount: cannot start generator: {e}");
            return ExitCode::from(3);
        }
    };
    let gen_pid = gen.id();
    let watched = w.add_root(gen_pid);
    let t0 = Instant::now();
    drop(gen.stdin.take()); // EOF = go
    let out = gen.wait_with_output();
    // let the last events land, and detached sleeps finish
    std::thread::sleep(Duration::from_millis(500));
    let elapsed = t0.elapsed().as_secs_f64();
    let stats = w.stats();
    let tracked = w.snapshot().len();
    w.stop();
    let expected: u64 = out
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0);
    let rate = |x: u64| {
        if expected == 0 {
            Value::Null
        } else {
            json!(x as f64 / expected as f64)
        }
    };
    println!(
        "{}",
        json!({
            "tool": "aib-forkcount",
            "mode": "calibrate",
            "pattern": pattern,
            "root_watched": watched,
            "expected_process_creations": expected,
            "capture_rate_fork_events": rate(stats.fork_events),
            "capture_rate_children_adopted": rate(stats.children_adopted),
            "result": report(&stats, elapsed, tracked),
        })
    );
    if expected == 0 || !watched {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let rest = args.get(1..).unwrap_or(&[]);
    match args.first().map(String::as_str) {
        Some("run") => cmd_run(rest),
        Some("calibrate") => cmd_calibrate(rest),
        Some("gen-wait") => {
            // block until the parent closes stdin
            let mut s = String::new();
            let _ = std::io::stdin().read_line(&mut s);
            cmd_gen(rest)
        }
        Some("gen-one") => cmd_gen_one(rest),
        _ => {
            eprintln!(
                "usage: aib-forkcount run --seconds N [--tmux-socket P]... [--all-tmux] [--pid N]... [--label T]\n       aib-forkcount calibrate --pattern single|grandchild|mid-exit|parallel|burst [--count N]"
            );
            ExitCode::from(2)
        }
    }
}
