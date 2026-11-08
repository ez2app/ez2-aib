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
//!   aib-forkcount calibrate --pattern single|grandchild|mid-exit|parallel|burst --count N [--inject-unlisted]
//!
//! Pacing uses plain sleeps: this is a measurement tool, not aibd, so the
//! ADR 0005 timer rule does not apply here.
#![allow(clippy::disallowed_methods)]

use aib_procwatch::{program_name, start_time, ForkWatch, Stats};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
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

/// Identity coverage needed per class (DECISION-4 C).
const CAPTURE_THRESHOLD: f64 = 0.99;

/// How many process creations a pattern makes for `--count n`, fixed in
/// advance (3f312 11-08): the expected count must not come from what the
/// generator reports, or a creation that silently failed would vanish from
/// both sides and still read 100 %.
fn planned_creations(pattern: &str, n: u64) -> Option<u64> {
    let per_step = match pattern {
        "single" | "burst" => 1,
        // gen-one, then its child
        "grandchild" | "mid-exit" => 2,
        // 4 threads of "single"
        "parallel" => 4,
        _ => return None,
    };
    n.checked_mul(per_step)
}

/// One process creation, as the generator records it: (parent pid, child pid).
type Creation = (u32, u32);

/// Start `gen-one <mode>`, which prints the pid of the child it creates.
/// Returns both creations (us -> gen-one, gen-one -> its child).
fn spawn_gen_one(mode: &str) -> Vec<Creation> {
    let me_pid = std::process::id();
    let Ok(c) = Command::new(me())
        .args(["gen-one", mode])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return Vec::new();
    };
    let one = c.id();
    let mut v = vec![(me_pid, one)];
    if let Ok(out) = c.wait_with_output() {
        if let Ok(k) = String::from_utf8_lossy(&out.stdout).trim().parse::<u32>() {
            v.push((one, k));
        }
    }
    v
}

fn spawn_true() -> Option<Creation> {
    let mut c = spawn_quiet(&mut Command::new("/usr/bin/true"))?;
    let k = c.id();
    let _ = c.wait();
    Some((std::process::id(), k))
}

/// Generator (child of `calibrate`). Prints one line `C <parent> <child>` for
/// every process creation it caused (its own and its helpers'), then exits.
/// `unlisted` adds one extra child that is deliberately not printed: the
/// miscount knife (a fork that is not part of the fixture must not raise the
/// capture rate).
fn cmd_gen(args: &[String]) -> ExitCode {
    let pattern = args.first().map(String::as_str).unwrap_or("");
    let n: u64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(20);
    let knives: Vec<&str> = args.iter().skip(2).map(String::as_str).collect();
    let unlisted = knives.contains(&"unlisted");
    // knife: one step is never started and never recorded (a failed spawn)
    let n_first = if knives.contains(&"short") {
        n.saturating_sub(1)
    } else {
        n
    };
    let gap = Duration::from_millis(50);
    let mut created: Vec<Creation> = Vec::new();
    match pattern {
        // one direct child per step
        "single" => {
            for _ in 0..n_first {
                created.extend(spawn_true());
                std::thread::sleep(gap);
            }
        }
        // child that starts one grandchild and waits for it
        "grandchild" => {
            for _ in 0..n_first {
                created.extend(spawn_gen_one("wait"));
                std::thread::sleep(gap);
            }
        }
        // child that starts /bin/sleep in its own group and exits at once
        "mid-exit" => {
            for _ in 0..n_first {
                created.extend(spawn_gen_one("detach"));
                std::thread::sleep(gap);
            }
        }
        // 4 threads of "single" at once
        "parallel" => {
            let hs: Vec<_> = (0..4)
                .map(|t| {
                    let steps = if t == 0 { n_first } else { n };
                    std::thread::spawn(move || {
                        let mut v = Vec::new();
                        for _ in 0..steps {
                            v.extend(spawn_true());
                            std::thread::sleep(gap);
                        }
                        v
                    })
                })
                .collect();
            for h in hs {
                created.extend(h.join().unwrap_or_default());
            }
        }
        // n children started back to back, waited for afterwards
        "burst" => {
            let kids: Vec<_> = (0..n_first)
                .filter_map(|_| spawn_quiet(&mut Command::new("/usr/bin/true")))
                .collect();
            let me_pid = std::process::id();
            for mut k in kids {
                created.push((me_pid, k.id()));
                let _ = k.wait();
            }
        }
        _ => {
            eprintln!("unknown pattern {pattern}");
            return ExitCode::from(2);
        }
    }
    if unlisted {
        // Lives 1 s, so the watcher always sees it: the knife is about
        // counting it wrongly, not about missing it (a /usr/bin/true here
        // was sometimes missed under load and the test went red, 11-08).
        if let Some(mut c) = spawn_quiet(Command::new("/bin/sleep").arg("1")) {
            let _ = c.wait();
        }
    }
    for (p, c) in created {
        println!("C {p} {c}");
    }
    ExitCode::SUCCESS
}

fn cmd_gen_one(args: &[String]) -> ExitCode {
    let child = match args.first().map(String::as_str) {
        Some("wait") => spawn_quiet(&mut Command::new("/usr/bin/true")).map(|mut c| {
            let k = c.id();
            let _ = c.wait();
            k
        }),
        Some("detach") => {
            let mut c = Command::new("/bin/sleep");
            c.arg("2");
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                c.process_group(0);
            }
            spawn_quiet(&mut c).map(|c| c.id())
        }
        _ => return ExitCode::from(2),
    };
    if let Some(k) = child {
        println!("{k}");
    }
    ExitCode::SUCCESS
}

/// Two metrics, never mixed (DECISION-4 C, bb7dc and 3f312, 2026-11-07):
///
/// - **identity coverage** (the only one called "capture rate", threshold
///   99 %): recorded creations whose child was adopted under the recorded
///   parent, each counted once, ÷ expected;
/// - **count consistency**: fork events from fixture parents ÷ expected. A
///   proxy only: events carry no child identity and kqueue coalesces them.
///
/// Passing does not rule out unknown losses; anything that cannot be checked
/// is marked unverified.
#[derive(Debug, Default, PartialEq, Eq)]
struct Attribution {
    /// fixed in advance from pattern and --count ([`planned_creations`])
    expected: u64,
    /// creations the generator recorded
    recorded: u64,
    /// creations seen with their identity (child pid adopted under the
    /// recorded parent)
    identity_confirmed: u64,
    /// fork events of fixture parents (no identity; proxy)
    fixture_fork_events: u64,
    /// fork events of fixture parents beyond what they created
    fork_events_beyond_created: u64,
    /// adopted children of fixture parents that the fixture did not record
    adopted_not_created: u64,
    /// child pids the fixture recorded more than once (identity ambiguous)
    duplicate_child_pids: u64,
}

impl Attribution {
    fn unverified_reasons(&self) -> Vec<&'static str> {
        let mut r = Vec::new();
        if self.expected == 0 {
            r.push("no_creations_recorded");
        }
        if self.fork_events_beyond_created > 0 {
            r.push("fork_events_beyond_created");
        }
        if self.adopted_not_created > 0 {
            r.push("adopted_not_created");
        }
        if self.duplicate_child_pids > 0 {
            r.push("duplicate_child_pids");
        }
        if self.recorded < self.expected {
            r.push("creations_short");
        }
        if self.recorded > self.expected {
            r.push("creations_over");
        }
        if self.identity_confirmed > self.expected {
            r.push("capture_above_expected");
        }
        r
    }
}

/// Per fixture parent P with C_P recorded children: A_P of them were adopted
/// under P (identity confirmed), F_P fork events came from P. Children and
/// events that are not the fixture's are reported, not counted.
fn attribute(
    planned: u64,
    created: &[Creation],
    adopted: &BTreeMap<u32, u32>,
    forks_by_pid: &BTreeMap<u32, u64>,
) -> Attribution {
    let mut by_parent: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    let mut kids = BTreeSet::new();
    let mut dup = 0;
    for (p, c) in created {
        by_parent.entry(*p).or_default().push(*c);
        if !kids.insert(*c) {
            dup += 1;
        }
    }
    let mut a = Attribution {
        expected: planned,
        recorded: created.len() as u64,
        duplicate_child_pids: dup,
        ..Attribution::default()
    };
    for (p, cs) in &by_parent {
        let c_p = cs.len() as u64;
        let a_p = cs.iter().filter(|c| adopted.get(c) == Some(p)).count() as u64;
        let f_p = forks_by_pid.get(p).copied().unwrap_or(0);
        a.identity_confirmed += a_p;
        a.fixture_fork_events += f_p;
        a.fork_events_beyond_created += f_p.saturating_sub(c_p);
    }
    a.adopted_not_created = adopted
        .iter()
        .filter(|(c, p)| by_parent.contains_key(p) && !kids.contains(c))
        .count() as u64;
    a
}

/// Lines that look like records (`C …`) but do not parse.
fn malformed_records(stdout: &str) -> u64 {
    stdout
        .lines()
        .filter(|l| l.split_whitespace().next() == Some("C"))
        .filter(|l| parse_creations(l).is_empty())
        .count() as u64
}

fn parse_creations(stdout: &str) -> Vec<Creation> {
    stdout
        .lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            (it.next() == Some("C")).then_some(())?;
            Some((it.next()?.parse().ok()?, it.next()?.parse().ok()?))
        })
        .collect()
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
    let Some(planned) = count
        .parse::<u64>()
        .ok()
        .and_then(|n| planned_creations(&pattern, n))
    else {
        eprintln!("aib-forkcount: unknown --pattern {pattern} or bad --count {count}");
        return ExitCode::from(2);
    };
    let w = match ForkWatch::start(&[]) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("aib-forkcount: {e}");
            return ExitCode::from(3);
        }
    };
    let mut gen_args = vec!["gen-wait".to_string(), pattern.clone(), count];
    if has(args, "--inject-unlisted") {
        gen_args.push("unlisted".into());
    }
    if has(args, "--inject-short") {
        gen_args.push("short".into());
    }
    // The generator waits on stdin so the watch is in place before it forks.
    let mut gen = match Command::new(me())
        .args(&gen_args)
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
    let adopted = w.adopted_parents();
    w.stop();
    let (gen_rc, stdout) = match out {
        Ok(o) => (
            o.status.code(),
            String::from_utf8_lossy(&o.stdout).into_owned(),
        ),
        Err(_) => (None, String::new()),
    };
    let created = parse_creations(&stdout);
    let malformed = malformed_records(&stdout);
    let att = attribute(planned, &created, &adopted, &stats.fork_events_by_pid);
    let v = verdict(
        &att,
        Observed {
            watched,
            generator_ok: gen_rc == Some(0),
            malformed,
            list_failures: stats.list_failures,
            register_failures: stats.register_failures,
        },
    );
    let reasons = v.reasons;
    let expected = att.expected;
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
            "expected_definition": "fixed in advance: --count x creations per step of the pattern",
            "recorded_process_creations": att.recorded,
            "generator_rc": gen_rc,
            "malformed_records": malformed,
            "status": if reasons.is_empty() { "ok" } else { "unverified" },
            "unverified_reasons": reasons,
            "capture_rate": rate(att.identity_confirmed),
            "capture_rate_definition": "identity coverage: recorded creations whose child was adopted under the recorded parent / expected",
            "capture_threshold": CAPTURE_THRESHOLD,
            "meets_threshold": v.meets_threshold,
            "identity_confirmed": att.identity_confirmed,
            "count_consistency": rate(att.fixture_fork_events),
            "count_consistency_definition": "fork events from fixture parents / expected (proxy: no identity, coalesced)",
            "limits": "passing does not rule out unknown losses",
            "raw": {
                "fork_events": stats.fork_events,
                "children_adopted": stats.children_adopted,
                "register_failures": stats.register_failures,
                "list_failures": stats.list_failures,
                "capture_rate_fork_events": rate(stats.fork_events),
                "capture_rate_children_adopted": rate(stats.children_adopted),
            },
            "miscount_checks": {
                "fork_events_beyond_created": att.fork_events_beyond_created,
                "adopted_not_created": att.adopted_not_created,
                "duplicate_child_pids": att.duplicate_child_pids,
            },
            "result": report(&stats, elapsed, tracked),
        })
    );
    ExitCode::from(v.rc)
}

/// Outcome of one calibration run (DECISION-4 C).
#[derive(Debug, PartialEq, Eq)]
struct Verdict {
    reasons: Vec<&'static str>,
    meets_threshold: bool,
    /// 0 = ok and above threshold, 1 = unverified, 4 = verified but below
    /// the threshold. Never 0 unless both hold.
    rc: u8,
}

/// What else the run observed, besides the attribution.
#[derive(Debug, Clone, Copy)]
struct Observed {
    watched: bool,
    /// the generator exited 0
    generator_ok: bool,
    malformed: u64,
    list_failures: u64,
    register_failures: u64,
}

/// A failed child listing or watch registration means the watcher may have
/// missed processes; a generator that failed, recorded too few or too many,
/// or wrote a bad record means the fixture is not what was planned. Either
/// way the run cannot be "ok" whatever the counts say.
fn verdict(att: &Attribution, o: Observed) -> Verdict {
    let mut reasons = att.unverified_reasons();
    if !o.watched {
        reasons.push("root_not_watched");
    }
    if !o.generator_ok {
        reasons.push("generator_failed");
    }
    if o.malformed > 0 {
        reasons.push("malformed_records");
    }
    if o.list_failures > 0 {
        reasons.push("list_failures");
    }
    if o.register_failures > 0 {
        reasons.push("register_failures");
    }
    let meets_threshold = reasons.is_empty()
        && att.expected > 0
        && att.identity_confirmed as f64 >= CAPTURE_THRESHOLD * att.expected as f64;
    let rc = if !reasons.is_empty() {
        1
    } else if !meets_threshold {
        4
    } else {
        0
    };
    Verdict {
        reasons,
        meets_threshold,
        rc,
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
                "usage: aib-forkcount run --seconds N [--tmux-socket P]... [--all-tmux] [--pid N]... [--label T]\n       aib-forkcount calibrate --pattern single|grandchild|mid-exit|parallel|burst [--count N] [--inject-unlisted] [--inject-short]"
            );
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn att(expected: u64, confirmed: u64) -> Attribution {
        Attribution {
            expected,
            recorded: expected,
            identity_confirmed: confirmed,
            ..Attribution::default()
        }
    }

    fn obs(watched: bool, list_failures: u64, register_failures: u64) -> Observed {
        Observed {
            watched,
            generator_ok: true,
            malformed: 0,
            list_failures,
            register_failures,
        }
    }

    #[test]
    fn the_expected_count_is_fixed_in_advance() {
        assert_eq!(planned_creations("single", 20), Some(20));
        assert_eq!(planned_creations("burst", 20), Some(20));
        assert_eq!(planned_creations("grandchild", 20), Some(40));
        assert_eq!(planned_creations("mid-exit", 20), Some(40));
        assert_eq!(planned_creations("parallel", 20), Some(80));
        assert_eq!(planned_creations("nope", 20), None);
        assert_eq!(planned_creations("parallel", u64::MAX), None);
    }

    /// 3f312 11-08: 20 asked, 19 created and recorded, all 19 seen.
    #[test]
    fn a_creation_that_never_happened_is_not_hidden() {
        let created: Vec<Creation> = (0..19).map(|i| (10, 100 + i)).collect();
        let adopted = created.iter().map(|(p, c)| (*c, *p)).collect();
        let a = attribute(20, &created, &adopted, &m(&[(10, 19)]));
        assert_eq!((a.expected, a.recorded, a.identity_confirmed), (20, 19, 19));
        let v = verdict(&a, obs(true, 0, 0));
        assert_eq!(
            (v.reasons, v.meets_threshold, v.rc),
            (vec!["creations_short"], false, 1)
        );
        // one recorded more than planned
        let created: Vec<Creation> = (0..21).map(|i| (10, 100 + i)).collect();
        let a = attribute(20, &created, &BTreeMap::new(), &BTreeMap::new());
        assert!(a.unverified_reasons().contains(&"creations_over"));
    }

    #[test]
    fn a_failed_generator_or_a_bad_record_is_unverified() {
        let mut o = obs(true, 0, 0);
        o.generator_ok = false;
        let v = verdict(&att(20, 20), o);
        assert_eq!((v.reasons, v.rc), (vec!["generator_failed"], 1));
        let mut o = obs(true, 0, 0);
        o.malformed = 1;
        assert_eq!(verdict(&att(20, 20), o).reasons, vec!["malformed_records"]);
        assert_eq!(malformed_records("C 1 2\nC x 3\nC 4\nnoise\n"), 2);
        assert_eq!(malformed_records("C 1 2\n"), 0);
    }

    #[test]
    fn list_or_register_failures_make_the_run_unverified() {
        // bb7dc 11-08 mid-exit: 185/200 with 197 list failures came out "ok"
        let v = verdict(&att(200, 200), obs(true, 197, 0));
        assert_eq!(
            (v.reasons.clone(), v.meets_threshold, v.rc),
            (vec!["list_failures"], false, 1)
        );
        let v = verdict(&att(200, 200), obs(true, 0, 1));
        assert_eq!(
            (v.reasons.clone(), v.meets_threshold, v.rc),
            (vec!["register_failures"], false, 1)
        );
        let v = verdict(&att(200, 200), obs(true, 1, 1));
        assert_eq!(v.reasons, vec!["list_failures", "register_failures"]);
    }

    #[test]
    fn rc_is_zero_only_when_verified_and_above_threshold() {
        assert_eq!(verdict(&att(200, 200), obs(true, 0, 0)).rc, 0);
        assert_eq!(verdict(&att(200, 198), obs(true, 0, 0)).rc, 0); // exactly 99 %
        let v = verdict(&att(200, 197), obs(true, 0, 0)); // one short
        assert_eq!((v.meets_threshold, v.rc), (false, 4));
        let v = verdict(&att(200, 185), obs(true, 0, 0));
        assert_eq!((v.meets_threshold, v.rc), (false, 4));
        assert_eq!(verdict(&att(200, 200), obs(false, 0, 0)).rc, 1);
        assert_eq!(verdict(&att(0, 0), obs(true, 0, 0)).rc, 1);
    }

    fn m(v: &[(u32, u64)]) -> BTreeMap<u32, u64> {
        v.iter().copied().collect()
    }

    #[test]
    fn coalesced_events_lower_only_the_proxy() {
        let created = [(10, 11), (10, 12), (10, 13)];
        let adopted = [(11, 10), (12, 10), (13, 10)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &m(&[(10, 1)]));
        assert_eq!(
            (a.expected, a.identity_confirmed, a.fixture_fork_events),
            (3, 3, 1)
        );
        assert!(a.unverified_reasons().is_empty());
    }

    #[test]
    fn events_never_stand_in_for_identity() {
        // every fork event arrived, no child was adopted: coverage is 0
        let created = [(10, 11), (10, 12)];
        let a = attribute(
            created.len() as u64,
            &created,
            &BTreeMap::new(),
            &m(&[(10, 2)]),
        );
        assert_eq!((a.identity_confirmed, a.fixture_fork_events), (0, 2));
    }

    #[test]
    fn one_missed_and_one_foreign_is_caught_although_totals_match() {
        let created = [(10, 11), (10, 12)];
        let adopted = [(11, 10), (99, 10)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &m(&[(10, 2)]));
        assert_eq!(a.identity_confirmed, 1);
        assert_eq!(a.adopted_not_created, 1);
        assert!(a.unverified_reasons().contains(&"adopted_not_created"));
    }

    #[test]
    fn a_failed_registration_shows_as_missing_grandchildren() {
        // 11's watch failed, so its child 12 was never adopted
        let created = [(10, 11), (11, 12)];
        let adopted = [(11, 10)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &m(&[(10, 1)]));
        assert_eq!((a.expected, a.identity_confirmed), (2, 1));
    }

    #[test]
    fn a_miss_and_an_extra_fork_do_not_cancel_out() {
        // 3 created, one missed by both counters, one extra fork event:
        // the raw max would say 3/3.
        let created = [(10, 11), (10, 12), (10, 13)];
        let adopted = [(11, 10), (12, 10)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &m(&[(10, 4)]));
        assert_eq!(a.fork_events_beyond_created, 1);
        assert!(a
            .unverified_reasons()
            .contains(&"fork_events_beyond_created"));
    }

    #[test]
    fn unrecorded_children_are_reported_not_counted() {
        let created = [(10, 11)];
        let adopted = [(11, 10), (99, 10), (500, 7)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &BTreeMap::new());
        assert_eq!((a.identity_confirmed, a.adopted_not_created), (1, 1));
        assert!(a.unverified_reasons().contains(&"adopted_not_created"));
    }

    #[test]
    fn a_child_adopted_under_another_parent_is_not_the_fixtures() {
        // pid 11 was recorded as 10's child but adopted under 77 (pid reuse)
        let created = [(10, 11)];
        let adopted = [(11, 77)].into_iter().collect();
        let a = attribute(created.len() as u64, &created, &adopted, &BTreeMap::new());
        assert_eq!(a.identity_confirmed, 0);
    }

    #[test]
    fn duplicate_pids_make_identity_ambiguous() {
        let created = [(10, 11), (10, 11)];
        let a = attribute(
            created.len() as u64,
            &created,
            &BTreeMap::new(),
            &m(&[(10, 2)]),
        );
        assert_eq!(a.duplicate_child_pids, 1);
        assert!(a.unverified_reasons().contains(&"duplicate_child_pids"));
    }

    #[test]
    fn creation_lines_parse() {
        assert_eq!(
            parse_creations("C 1 2\nnoise\nC 3 x\nC 4 5\n"),
            vec![(1, 2), (4, 5)]
        );
    }
}
