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

use aib_procwatch::{
    program_name, start_time, uptime_ns, ForkWatch, ListFault, NotAdoptedWhy, Stats, Trace,
    TraceKind,
};
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

/// Most forking pids listed in `forkers`.
const FORKERS_TOP: usize = 50;

/// The busiest forking pids, most fork events first (pid breaks ties), each
/// with its command line from `parent_argv` (`?` when none was kept): goal 2
/// prerequisite 1, telling which `bash` / `sh` / `tmux` did the forking.
fn forkers(stats: &Stats) -> Vec<Value> {
    let mut v: Vec<(u32, u64)> = stats
        .fork_events_by_pid
        .iter()
        .map(|(p, n)| (*p, *n))
        .collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    v.into_iter()
        .take(FORKERS_TOP)
        .map(|(pid, n)| {
            let argv = stats.parent_argv.get(&pid).map_or("?", String::as_str);
            json!({ "pid": pid, "fork_events": n, "argv": argv })
        })
        .collect()
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
        "forkers": forkers(stats),
        "parent_argv_dropped": stats.parent_argv_dropped,
        "register_failures": stats.register_failures,
        "list_failures": stats.list_failures,
        "list_failures_by_tag": stats.list_failures_by_tag,
        "empty_lists": stats.empty_lists,
        "fork_without_child": stats.fork_without_child,
        "fork_without_child_definition": "NOTE_FORK events after which the parent's listing adopted no new child; report only (coalesced events, early exits), no threshold",
        "list_faults_injected": stats.injected,
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

/// gen-one's own clock stamps (`CLOCK_UPTIME_RAW` ns, the watcher's clock):
/// just before it started its child, when the start returned, and just before
/// it returned from main. It was alive at least until `exiting`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Stamps {
    before: u64,
    spawned: u64,
    exiting: u64,
}

/// Start `gen-one <mode>`, which prints the pid of the child it creates and
/// its stamps. Returns both creations (us -> gen-one, gen-one -> its child)
/// and the stamps line `T <gen-one> <child> <before> <spawned> <exiting>`
/// when gen-one gave them.
fn spawn_gen_one(mode: &str) -> (Vec<Creation>, Option<String>) {
    let me_pid = std::process::id();
    let Ok(c) = Command::new(me())
        .args(["gen-one", mode])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return (Vec::new(), None);
    };
    let one = c.id();
    let mut v = vec![(me_pid, one)];
    let mut stamps = None;
    if let Ok(out) = c.wait_with_output() {
        let line = String::from_utf8_lossy(&out.stdout).into_owned();
        let mut it = line.split_whitespace();
        if let Some(k) = it.next().and_then(|t| t.parse::<u32>().ok()) {
            v.push((one, k));
            let t: Vec<&str> = it.collect();
            if t.len() == 3 && t.iter().all(|x| x.parse::<u64>().is_ok()) {
                stamps = Some(format!("T {one} {k} {} {} {}", t[0], t[1], t[2]));
            }
        }
    }
    (v, stamps)
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
    let mut stamps: Vec<String> = Vec::new();
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
                let (c, t) = spawn_gen_one("wait");
                created.extend(c);
                stamps.extend(t);
                std::thread::sleep(gap);
            }
        }
        // child that starts /bin/sleep in its own group and exits at once
        "mid-exit" => {
            for _ in 0..n_first {
                let (c, t) = spawn_gen_one("detach");
                created.extend(c);
                stamps.extend(t);
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
    for t in stamps {
        println!("{t}");
    }
    ExitCode::SUCCESS
}

fn cmd_gen_one(args: &[String]) -> ExitCode {
    let before = uptime_ns();
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
    let spawned = uptime_ns();
    // Stamps only for `detach`: its child (sleep 2) outlives gen-one, so a
    // listing that ends before `exiting` had the child under gen-one. A
    // `wait` child may already be gone, so no stamps there (miss_cause then
    // never says listed_but_absent for it).
    let detach = args.first().map(String::as_str) == Some("detach");
    if let Some(k) = child {
        match (before, spawned) {
            // `exiting` is taken last, after the line is built: nothing of
            // gen-one's own runs after it but the print and the return
            (Some(b), Some(s)) if detach => {
                let head = format!("{k} {b} {s}");
                match uptime_ns() {
                    Some(e) => println!("{head} {e}"),
                    None => println!("{k}"),
                }
            }
            _ => println!("{k}"),
        }
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

/// Every child listed under a parent: adopted ones, and (a63ab 11-10) the
/// ones listed whose start time could not be read because they had already
/// exited. Adoption wins where both name a child.
fn listed_under(
    adopted: &BTreeMap<u32, u32>,
    start_unknown: &BTreeMap<u32, u32>,
) -> BTreeMap<u32, u32> {
    let mut m = start_unknown.clone();
    m.extend(adopted.iter().map(|(c, p)| (*c, *p)));
    m
}

/// Every recorded creation whose identity was not confirmed, one record each
/// (DECISION-10 3: a missing identity is listed, never explained away by a
/// count). `adopted_under` is the parent the child was listed under, if it
/// was adopted at all; `parent_watched` says whether the recorded parent was
/// itself a root or an adopted process (if not, its children could not be
/// seen by events).
fn missing_identities(
    created: &[Creation],
    adopted: &BTreeMap<u32, u32>,
    forks_by_pid: &BTreeMap<u32, u64>,
    roots: &[u32],
    stamps: &BTreeMap<Creation, Stamps>,
    trace: &Trace,
) -> Vec<Value> {
    created
        .iter()
        .filter(|(p, c)| adopted.get(c) != Some(p))
        .map(|(p, c)| {
            let st = stamps.get(&(*p, *c)).copied();
            json!({
                "parent": p,
                "child": c,
                "adopted_under": adopted.get(c),
                "parent_watched": roots.contains(p) || adopted.contains_key(p),
                "parent_fork_events": forks_by_pid.get(p).copied().unwrap_or(0),
                "cause": miss_cause(*p, *c, adopted.get(c).copied(), st, trace),
                "timeline": timeline(&[*p, *c], *c, st, trace, 40),
            })
        })
        .collect()
}

/// `T <parent> <child> <before> <spawned> <exiting>` lines, by creation.
fn parse_stamps(stdout: &str) -> BTreeMap<Creation, Stamps> {
    stdout
        .lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            (it.next() == Some("T")).then_some(())?;
            let p = it.next()?.parse().ok()?;
            let c = it.next()?.parse().ok()?;
            let before = it.next()?.parse().ok()?;
            let spawned = it.next()?.parse().ok()?;
            let exiting = it.next()?.parse().ok()?;
            Some((
                (p, c),
                Stamps {
                    before,
                    spawned,
                    exiting,
                },
            ))
        })
        .collect()
}

/// One listing of a parent: start, end, result.
type Listing<'a> = (u64, u64, &'a Result<Vec<u32>, String>);

/// Why a recorded creation's identity was not confirmed, worked out from the
/// order of observations only (a63ab 11-10; PLAN-goal2-fixture §1.2). Never
/// counts a miss as captured: the capture rate does not use it.
///
/// - `no_fork_event`: the watcher read no `NOTE_FORK` of the parent;
/// - `listed_but_absent`: the parent's children were listed after its fork
///   event, the listing ended before the parent's own `exiting` stamp (so the
///   parent was alive and the child was its child), and the child was not in
///   the list. Not a known blind spot: a tool problem;
/// - `listed_start_unreadable`: the child was in a listing of the parent but
///   its start time could not be read (it had already exited). Since a63ab
///   11-10 such a child is kept (`start_unknown`) and counts as listed, so
///   this cause only shows if that keeping breaks;
/// - `listed_older_than_parent`: listed, refused as older than the parent
///   (the pid-reuse rule);
/// - `parent_exited_before_list`: every listing of the parent after its fork
///   event started after its exit was observed (`NOTE_EXIT`, possibly in the
///   same event as the fork) or failed with `gone`. The registered blind
///   spot: the child was re-parented before it could be listed;
/// - `exit_race_unresolved`: (stamped parents) a listing without the child
///   started after the parent's last own stamp but before its exit was read:
///   the trace cannot tell whether the parent had exited by then;
/// - `unknown`: anything else (no trace, a dropped trace event, the child
///   adopted under another parent, a listing that cannot be placed). Stays
///   unverified.
fn miss_cause(
    p: u32,
    c: u32,
    adopted_under: Option<u32>,
    stamps: Option<Stamps>,
    trace: &Trace,
) -> &'static str {
    if !trace.enabled || trace.dropped > 0 || adopted_under.is_some() {
        return "unknown";
    }
    let of_p = || trace.events.iter().filter(move |e| e.pid == p);
    let refused = of_p().find_map(|e| match e.kind {
        TraceKind::NotAdopted { child, why } if child == c => Some(why),
        _ => None,
    });
    match refused {
        Some(NotAdoptedWhy::StartUnreadable) => return "listed_start_unreadable",
        Some(NotAdoptedWhy::StartDenied) => return "listed_start_denied",
        Some(NotAdoptedWhy::OlderThanParent) => return "listed_older_than_parent",
        None => {}
    }
    let Some(first_fork) = of_p()
        .filter(|e| matches!(e.kind, TraceKind::Fork { .. }))
        .map(|e| e.t_ns)
        .min()
    else {
        return "no_fork_event";
    };
    let exit_seen = of_p()
        .filter(|e| matches!(e.kind, TraceKind::Exit | TraceKind::Fork { exited: true }))
        .map(|e| e.t_ns)
        .min();
    let lists: Vec<Listing<'_>> = of_p()
        .filter(|e| e.t_ns >= first_fork)
        .filter_map(|e| match &e.kind {
            TraceKind::List { end_ns, result } => Some((e.t_ns, *end_ns, result)),
            _ => None,
        })
        .collect();
    if let Some(s) = stamps {
        if lists
            .iter()
            .any(|(_, end, r)| *end <= s.exiting && matches!(r, Ok(k) if !k.contains(&c)))
        {
            return "listed_but_absent";
        }
    }
    let after_exit = |t: u64, r: &Result<Vec<u32>, String>| {
        matches!(r, Err(tag) if tag == "gone") || exit_seen.is_some_and(|x| x <= t)
    };
    if !lists.is_empty() && lists.iter().all(|(t, _, r)| after_exit(*t, r)) {
        return "parent_exited_before_list";
    }
    if let Some(s) = stamps {
        // every listing is either after the exit was read, or in the window
        // between the parent's last stamp and that read
        let in_race = |t: u64, r: &Result<Vec<u32>, String>| {
            after_exit(t, r) || (t >= s.exiting && exit_seen.is_some_and(|x| t < x))
        };
        if !lists.is_empty() && lists.iter().all(|(t, _, r)| in_race(*t, r)) {
            return "exit_race_unresolved";
        }
    }
    "unknown"
}

/// Up to `max` trace events of `pids`, oldest first, for a human to read.
fn timeline(pids: &[u32], child: u32, stamps: Option<Stamps>, trace: &Trace, max: usize) -> Value {
    let mine: Vec<&aib_procwatch::TraceEvent> = trace
        .events
        .iter()
        .filter(|e| pids.contains(&e.pid))
        .collect();
    // centre on the first event that names the child, else on the stamp,
    // else show the last events
    let names_child = |e: &&aib_procwatch::TraceEvent| match &e.kind {
        TraceKind::List { result: Ok(k), .. } => k.contains(&child),
        TraceKind::NotAdopted { child: k, .. } => *k == child,
        _ => false,
    };
    let centre = mine
        .iter()
        .position(names_child)
        .or_else(|| stamps.and_then(|s| mine.iter().position(|e| e.t_ns >= s.before)))
        .unwrap_or(mine.len());
    let from = centre.saturating_sub(max / 2);
    let mut ev: Vec<Value> = mine
        .iter()
        .skip(from)
        .take(max)
        .map(|e| match &e.kind {
            TraceKind::Fork { exited } => {
                json!({"t_ns": e.t_ns, "pid": e.pid, "event": "fork", "exited": exited})
            }
            TraceKind::Exit => json!({"t_ns": e.t_ns, "pid": e.pid, "event": "exit"}),
            TraceKind::List { end_ns, result } => match result {
                Ok(k) => json!({"t_ns": e.t_ns, "end_ns": end_ns, "pid": e.pid, "event": "list", "children": k}),
                Err(tag) => json!({"t_ns": e.t_ns, "end_ns": end_ns, "pid": e.pid, "event": "list", "error": tag}),
            },
            TraceKind::NotAdopted { child, why } => json!({
                "t_ns": e.t_ns, "pid": e.pid, "event": "not_adopted", "child": child,
                "why": match why {
                    NotAdoptedWhy::StartUnreadable => "start_unreadable",
                    NotAdoptedWhy::StartDenied => "start_denied",
                    NotAdoptedWhy::OlderThanParent => "older_than_parent",
                },
            }),
        })
        .collect();
    if let Some(s) = stamps {
        ev.push(json!({"gen_one": {"before_ns": s.before, "spawned_ns": s.spawned, "exiting_ns": s.exiting}}));
    }
    Value::Array(ev)
}

/// Trace completeness: fork events of a watched pid with no listing of that
/// pid at or after them (the listing step was not recorded). Must be 0.
fn forks_without_listing(trace: &Trace) -> u64 {
    trace
        .events
        .iter()
        .filter(|f| matches!(f.kind, TraceKind::Fork { .. }))
        .filter(|f| {
            !trace.events.iter().any(|l| {
                l.pid == f.pid && l.t_ns >= f.t_ns && matches!(l.kind, TraceKind::List { .. })
            })
        })
        .count() as u64
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
    let inject_list = arg_values(args, "--inject-list").first().cloned();
    let fault = match inject_list.as_deref() {
        None => None,
        Some(f) => match list_fault(f) {
            Some(x) => Some(x),
            None => {
                eprintln!("aib-forkcount: unknown --inject-list {f} (error|incomplete|hide-one)");
                return ExitCode::from(2);
            }
        },
    };
    let w = match ForkWatch::start_traced(&[], fault) {
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
    let start_unknown = w.start_unknown_parents();
    let adopted = listed_under(&w.adopted_parents(), &start_unknown);
    let trace = w.trace();
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
    let confirmed_start_unknown = created
        .iter()
        .filter(|(p, c)| start_unknown.get(c) == Some(p))
        .count() as u64;
    let stamps = parse_stamps(&stdout);
    let missing = missing_identities(
        &created,
        &adopted,
        &stats.fork_events_by_pid,
        &[gen_pid],
        &stamps,
        &trace,
    );
    let mut causes: BTreeMap<&str, u64> = BTreeMap::new();
    for m in &missing {
        if let Some(c) = m["cause"].as_str() {
            *causes.entry(c).or_default() += 1;
        }
    }
    let v = verdict(
        &att,
        Observed {
            watched,
            generator_ok: gen_rc == Some(0),
            malformed,
            list_failures: stats.list_failures,
            register_failures: stats.register_failures,
            start_denied: stats.children_start_denied,
            faults_injected: stats.injected,
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
            "injected_list_fault": inject_list,
            "status": if reasons.is_empty() { "ok" } else { "unverified" },
            "unverified_reasons": reasons,
            "capture_rate": rate(att.identity_confirmed),
            "capture_rate_definition": "identity coverage: recorded creations whose child was listed under the recorded parent (adopted, or start_unknown: listed but exited before its start time could be read) / expected",
            "capture_threshold": CAPTURE_THRESHOLD,
            "meets_threshold": v.meets_threshold,
            "identity_confirmed": att.identity_confirmed,
            "identity_confirmed_start_unknown": confirmed_start_unknown,
            "missing_identities": missing,
            "missing_causes": causes,
            "missing_causes_definition": "order of observations only (watcher trace + gen-one stamps, CLOCK_UPTIME_RAW); never counted as captured",
            "trace": {
                "enabled": trace.enabled,
                "events": trace.events.len(),
                "dropped": trace.dropped,
                "forks_without_listing": forks_without_listing(&trace),
                "gen_one_stamps": stamps.len(),
            },
            "count_consistency": rate(att.fixture_fork_events),
            "count_consistency_definition": "fork events from fixture parents / expected (proxy: no identity, coalesced)",
            "limits": "passing does not rule out unknown losses",
            "raw": {
                "fork_events": stats.fork_events,
                "children_adopted": stats.children_adopted,
                "children_start_unknown": stats.children_start_unknown,
                "children_start_denied": stats.children_start_denied,
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
    /// listed children alive but unreadable (EPERM): unnamed forks
    start_denied: u64,
    /// listings where `--inject-list` changed the result
    faults_injected: u64,
}

/// `--inject-list` value: the first listing fails (`error`), does not fit
/// (`incomplete`), or the first listing with children drops one (`hide-one`).
fn list_fault(name: &str) -> Option<ListFault> {
    match name {
        "error" => Some(ListFault::Error { nth: 1 }),
        "incomplete" => Some(ListFault::Incomplete { nth: 1 }),
        "hide-one" => Some(ListFault::HideOne),
        _ => None,
    }
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
    if o.start_denied > 0 {
        reasons.push("start_denied");
    }
    if o.faults_injected > 0 {
        reasons.push("fault_injected");
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
                "usage: aib-forkcount run --seconds N [--tmux-socket P]... [--all-tmux] [--pid N]... [--label T]\n       aib-forkcount calibrate --pattern single|grandchild|mid-exit|parallel|burst [--count N] [--inject-unlisted] [--inject-short] [--inject-list error|incomplete|hide-one]"
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
            start_denied: 0,
            faults_injected: 0,
        }
    }

    #[test]
    fn a_child_whose_start_time_was_refused_never_lets_a_run_pass() {
        let a = Attribution::default();
        let mut o = obs(true, 0, 0);
        o.start_denied = 1;
        assert!(verdict(&a, o).reasons.contains(&"start_denied"));
    }

    #[test]
    fn an_injected_fault_never_lets_a_run_pass() {
        let mut o = obs(true, 0, 0);
        o.faults_injected = 1;
        let v = verdict(&att(10, 10), o);
        assert_eq!(
            (v.reasons, v.meets_threshold, v.rc),
            (vec!["fault_injected"], false, 1)
        );
    }

    #[test]
    fn every_unconfirmed_creation_is_listed_with_its_parent() {
        // 11 confirmed; 12 never adopted (its parent 10 is the root); 13 was
        // adopted under another parent; 21's parent 20 was never watched
        let created = [(10, 11), (10, 12), (10, 13), (20, 21)];
        let adopted: BTreeMap<u32, u32> = [(11, 10), (13, 77)].into_iter().collect();
        let v = missing_identities(
            &created,
            &adopted,
            &m(&[(10, 3)]),
            &[10],
            &BTreeMap::new(),
            &Trace::default(),
        );
        assert_eq!(v.len(), 3, "{v:?}");
        assert_eq!(v[0]["child"], 12);
        assert_eq!(v[0]["adopted_under"], Value::Null);
        assert_eq!(v[0]["parent_watched"], true);
        assert_eq!(v[0]["parent_fork_events"], 3);
        assert_eq!(v[1]["child"], 13);
        assert_eq!(v[1]["adopted_under"], 77);
        assert_eq!(v[2]["parent"], 20);
        assert_eq!(v[2]["parent_watched"], false);
        // no trace: nothing is explained
        assert!(v.iter().all(|x| x["cause"] == "unknown"), "{v:?}");
    }

    // ---- miss causes (a63ab 11-10, PLAN-goal2-fixture §1.2) ----

    use aib_procwatch::TraceEvent;

    fn ev(t_ns: u64, pid: u32, kind: TraceKind) -> TraceEvent {
        TraceEvent { t_ns, pid, kind }
    }

    fn list(t_ns: u64, end_ns: u64, pid: u32, kids: &[u32]) -> TraceEvent {
        ev(
            t_ns,
            pid,
            TraceKind::List {
                end_ns,
                result: Ok(kids.to_vec()),
            },
        )
    }

    fn tr(events: Vec<TraceEvent>) -> Trace {
        Trace {
            events,
            dropped: 0,
            enabled: true,
        }
    }

    fn st(exiting: u64) -> Option<Stamps> {
        Some(Stamps {
            before: 1,
            spawned: 2,
            exiting,
        })
    }

    #[test]
    fn exit_read_with_the_fork_is_the_registered_blind_spot() {
        let t = tr(vec![
            ev(100, 7, TraceKind::Fork { exited: true }),
            list(101, 102, 7, &[]),
        ]);
        assert_eq!(
            miss_cause(7, 8, None, st(90), &t),
            "parent_exited_before_list"
        );
    }

    #[test]
    fn a_listing_while_the_parent_was_alive_is_a_tool_problem() {
        // knife 2 of the plan: the parent's own stamp says it was alive until
        // 120, the listing ended at 102 without the child; the exit comes
        // later. This must never read as the blind spot.
        let t = tr(vec![
            ev(100, 7, TraceKind::Fork { exited: false }),
            list(101, 102, 7, &[]),
            ev(150, 7, TraceKind::Exit),
            list(151, 152, 7, &[]),
        ]);
        assert_eq!(miss_cause(7, 8, None, st(120), &t), "listed_but_absent");
    }

    #[test]
    fn a_listing_that_cannot_be_placed_is_unknown() {
        // ended after the parent's last stamp, started before its exit was seen
        let t = tr(vec![
            ev(100, 7, TraceKind::Fork { exited: false }),
            list(101, 130, 7, &[]),
            ev(140, 7, TraceKind::Exit),
        ]);
        assert_eq!(miss_cause(7, 8, None, st(120), &t), "unknown");
        // without stamps an early listing is never called a tool problem
        assert_eq!(miss_cause(7, 8, None, None, &t), "unknown");
    }

    #[test]
    fn a_gone_listing_counts_as_after_the_exit_other_errors_do_not() {
        let gone = |tag: &str| {
            tr(vec![
                ev(100, 7, TraceKind::Fork { exited: false }),
                ev(
                    101,
                    7,
                    TraceKind::List {
                        end_ns: 102,
                        result: Err(tag.into()),
                    },
                ),
            ])
        };
        assert_eq!(
            miss_cause(7, 8, None, st(99), &gone("gone")),
            "parent_exited_before_list"
        );
        assert_eq!(miss_cause(7, 8, None, st(99), &gone("os_error")), "unknown");
    }

    #[test]
    fn no_fork_event_no_trace_or_a_dropped_trace() {
        let t = tr(vec![list(101, 102, 7, &[])]);
        assert_eq!(miss_cause(7, 8, None, st(120), &t), "no_fork_event");
        let mut d = tr(vec![ev(100, 7, TraceKind::Fork { exited: true })]);
        d.dropped = 1;
        assert_eq!(miss_cause(7, 8, None, st(90), &d), "unknown");
        assert_eq!(miss_cause(7, 8, None, st(90), &Trace::default()), "unknown");
        // adopted under another parent: not explained here
        let t = tr(vec![
            ev(100, 7, TraceKind::Fork { exited: true }),
            list(101, 102, 7, &[]),
        ]);
        assert_eq!(miss_cause(7, 8, Some(77), st(90), &t), "unknown");
    }

    #[test]
    fn listings_before_the_fork_event_do_not_decide() {
        // a listing at adoption time, before the child existed, is ignored
        let t = tr(vec![
            list(50, 51, 7, &[]),
            ev(100, 7, TraceKind::Fork { exited: true }),
            list(101, 102, 7, &[]),
        ]);
        assert_eq!(
            miss_cause(7, 8, None, st(90), &t),
            "parent_exited_before_list"
        );
    }

    #[test]
    fn a_listing_between_the_last_stamp_and_the_exit_read_is_a_race() {
        // Mac B 11-10 mid-exit #2: stamp 79, fork read 80, empty listing
        // 81-82, exit read 83. Whether the parent had exited is not known.
        let t = tr(vec![
            list(10, 11, 7, &[]),
            ev(80, 7, TraceKind::Fork { exited: false }),
            list(81, 82, 7, &[]),
            ev(83, 7, TraceKind::Exit),
        ]);
        assert_eq!(miss_cause(7, 8, None, st(79), &t), "exit_race_unresolved");
        // without the parent's stamp the same trace is unknown
        assert_eq!(miss_cause(7, 8, None, None, &t), "unknown");
    }

    #[test]
    fn a_listed_child_that_was_not_recorded_says_why() {
        // Mac B 11-10 parallel #1: the child was in the list, its start
        // time could not be read
        let na = |why| {
            tr(vec![
                ev(100, 7, TraceKind::Fork { exited: false }),
                list(101, 102, 7, &[8, 9]),
                ev(103, 7, TraceKind::NotAdopted { child: 8, why }),
            ])
        };
        assert_eq!(
            miss_cause(7, 8, None, None, &na(NotAdoptedWhy::StartUnreadable)),
            "listed_start_unreadable"
        );
        assert_eq!(
            miss_cause(7, 8, None, None, &na(NotAdoptedWhy::StartDenied)),
            "listed_start_denied"
        );
        assert_eq!(
            miss_cause(7, 8, None, None, &na(NotAdoptedWhy::OlderThanParent)),
            "listed_older_than_parent"
        );
        // another child's refusal says nothing about this one
        assert_eq!(
            miss_cause(7, 9, None, None, &na(NotAdoptedWhy::StartUnreadable)),
            "unknown"
        );
    }

    #[test]
    fn a_listed_child_with_no_start_time_counts_and_adoption_wins() {
        // a63ab 11-10: parallel's 25 misses were children the watcher listed
        let adopted: BTreeMap<u32, u32> = [(11, 10)].into_iter().collect();
        let unknown: BTreeMap<u32, u32> = [(12, 10), (11, 99)].into_iter().collect();
        let l = listed_under(&adopted, &unknown);
        assert_eq!(l.get(&11), Some(&10));
        assert_eq!(l.get(&12), Some(&10));
        let a = attribute(3, &[(10, 11), (10, 12), (10, 13)], &l, &m(&[(10, 3)]));
        assert_eq!(a.identity_confirmed, 2);
    }

    #[test]
    fn a_fork_with_no_listing_after_it_is_counted() {
        let t = tr(vec![
            list(50, 51, 7, &[]),
            ev(100, 7, TraceKind::Fork { exited: false }),
            ev(110, 9, TraceKind::Fork { exited: false }),
            list(111, 112, 9, &[]),
        ]);
        assert_eq!(forks_without_listing(&t), 1);
    }

    #[test]
    fn stamp_lines_parse_and_bad_ones_are_skipped() {
        let s = parse_stamps("C 1 2\nT 2 3 10 20 30\nT 2 x 1 2 3\nT 4 5 1 2\n");
        assert_eq!(s.len(), 1);
        assert_eq!(
            s.get(&(2, 3)),
            Some(&Stamps {
                before: 10,
                spawned: 20,
                exiting: 30
            })
        );
        // a T line is not a creation record
        assert_eq!(parse_creations("T 2 3 10 20 30\n"), vec![]);
        assert_eq!(malformed_records("T 2 3 10 20 30\n"), 0);
    }

    #[test]
    fn list_fault_names() {
        assert_eq!(list_fault("error"), Some(ListFault::Error { nth: 1 }));
        assert_eq!(
            list_fault("incomplete"),
            Some(ListFault::Incomplete { nth: 1 })
        );
        assert_eq!(list_fault("hide-one"), Some(ListFault::HideOne));
        assert_eq!(list_fault("nope"), None);
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
