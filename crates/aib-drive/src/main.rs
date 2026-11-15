//! `aib-drive observe`: dry-run rounds (see `aib_drive::observe`). It never
//! acts: there is no executor in this program.
//!
//! `--expect-project UID` (off unless given) wires aib-expect: each round
//! also compares the project's goals with its evidence, the gaps weight
//! decide, and a completion event (`--event ID` with `--once`, or the id
//! written into `drive/event` with `--watch`) gets one report.
//!
//! A round without a snapshot answers no event: `--watch` keeps the event for
//! the next round, `--once` exits 3 and the caller sends `--event` again.
//!
//! Exit codes: 0 done; 2 usage; 3 a round had no snapshot (`--once`);
//! 4 paused; 5 another observe holds the lock, or a state file failed.

use aib_drive::decide::{Roles, Seat, Sha};
use aib_drive::observe::{self, Built, Paths, RealClock, Record, Refused, BASE};
use aib_drive::snapshot::{build, GhRead, Inputs};
use aib_drive::source::{run_fixed, AibQueue};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

const USAGE: &str = "usage: aib-drive observe --repo OWNER/NAME --pending DIR \
--captain SEAT --collector SEAT [--seats A,B,…] [--expect-project UID] \
(--once [--event ID] | --watch [--interval SECS] [--clone DIR])";

struct Args {
    repo: String,
    pending: PathBuf,
    captain: Seat,
    collector: Seat,
    seats: Vec<Seat>,
    watch: bool,
    interval: Duration,
    /// The clone a watch round refreshes the caches from (`--clone`, or
    /// `AIB_DRIVE_REPO_DIR`); none ⇒ no refresh, said once.
    clone: Option<PathBuf>,
    expect_project: Option<String>,
    event: Option<String>,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut it = argv.iter();
    if it.next().map(String::as_str) != Some("observe") {
        return Err(USAGE.into());
    }
    let mut m: BTreeMap<String, String> = BTreeMap::new();
    let (mut once, mut watch) = (false, false);
    while let Some(k) = it.next() {
        match k.as_str() {
            "--once" => once = true,
            "--watch" => watch = true,
            "--repo" | "--pending" | "--captain" | "--collector" | "--seats" | "--interval"
            | "--expect-project" | "--event" | "--clone" => {
                let v = it.next().ok_or_else(|| format!("{k} needs a value"))?;
                if m.insert(k.clone(), v.clone()).is_some() {
                    return Err(format!("{k} given twice"));
                }
            }
            _ => return Err(format!("unknown argument {k:?}\n{USAGE}")),
        }
    }
    if once == watch {
        return Err(format!("give exactly one of --once / --watch\n{USAGE}"));
    }
    let mut need = |k: &str| m.remove(k).ok_or_else(|| format!("{k} missing\n{USAGE}"));
    let repo = need("--repo")?;
    let pending = need("--pending")?.into();
    let captain = need("--captain")?;
    let collector = need("--collector")?;
    let seats = m
        .remove("--seats")
        .map(|s| {
            s.split(',')
                .filter(|x| !x.is_empty())
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    let interval = match m.remove("--interval") {
        None => BASE,
        Some(v) => observe::interval(&v)?,
    };
    if !watch && interval != BASE {
        return Err("--interval goes with --watch".into());
    }
    let clone = m.remove("--clone").map(PathBuf::from);
    if clone.is_some() && !watch {
        return Err("--clone goes with --watch".into());
    }
    let expect_project = m.remove("--expect-project");
    let event = m.remove("--event");
    if let Some(e) = &event {
        if watch {
            return Err("--event goes with --once (--watch reads drive/event)".into());
        }
        if expect_project.is_none() {
            return Err("--event needs --expect-project".into());
        }
        if !aib_drive::report::is_event(e) {
            return Err(format!("--event {e:?} is not a completion event"));
        }
    }
    Ok(Args {
        repo,
        pending,
        captain,
        collector,
        seats,
        watch,
        interval,
        clone,
        expect_project,
        event,
    })
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// The subcommands (S79 integration, IFACE-s79 §0): each slice's own entry is routed
/// here, so there is one program.
const TOP_USAGE: &str = "usage: aib-drive <observe|next|seats|event|act|switch|refresh|unblock-u3> …
  next     what can be done now, best first (read only)
  seats    the seat facts
  event    write one wake-up event (merge / receipt / review / dm_done / seat_free / tick)
  act      the dispatch dry run;  switch  turn one of act's switches on or off
  refresh  update aib-slice's cache for next (--repo DIR or AIB_DRIVE_REPO_DIR; --full)
  observe  the old dry-run rounds
  unblock-u3  u-3 blockers: machines, swap / long-open seats, receipts with no result, hanging lease, fake knife bites (read only)";

/// `--seats` default: the seat facts read now, in this process (read only: the idle log
/// that `aib-drive seats` appends is not written).
const LIVE_SEATS: &str = "@live-seats";
/// `--ideas` default: `aib-idea list --all --json`, run now.
const LIVE_IDEAS: &str = "@live-ideas";
/// Where `pr:` items of the plan live (`AIB_DRIVE_REPO` overrides).
const DEFAULT_REPO: &str = "ez2app/ez2-aib";
/// aib-slice's cache older than this is `stale` (IFACE r5: 30 minutes).
const SLICE_STALE_SECS: u64 = 30 * 60;
/// The full scan's cache older than this is `stale` (IFACE r8: 24 hours; only layer 4).
const FULL_STALE_SECS: u64 = 24 * 60 * 60;

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let rest: Vec<String> = argv.get(1..).unwrap_or_default().to_vec();
    match argv.first().map(String::as_str) {
        Some("observe") => observe_main(&argv),
        Some("next") => std::process::exit(next_main(&rest)),
        Some("seats") => {
            let (rc, out) = aib_drive::seats::run(&rest, now_secs());
            print!("{out}");
            std::process::exit(rc);
        }
        Some("event") => {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or((0, 0), |d| (d.as_secs(), d.subsec_nanos()));
            let o = aib_drive::wake::cli_event(
                &rest,
                state_root(),
                std::env::var("AIB_DRIVE_BY").ok(),
                now,
                std::process::id(),
            );
            print!("{}", o.stdout);
            eprint!("{}", o.stderr);
            std::process::exit(i32::from(o.code));
        }
        Some("refresh") => std::process::exit(refresh_main(&rest)),
        Some("act") => std::process::exit(i32::from(aib_drive::act::cli_act(&rest))),
        Some("switch") => std::process::exit(i32::from(aib_drive::act::cli_switch(&rest))),
        Some("unblock-u3") => {
            let root = state_root().ok();
            let (rc, out) = aib_drive::unblock_u3::run(&rest, root.as_deref(), now_secs());
            print!("{out}");
            std::process::exit(rc);
        }
        _ => {
            eprintln!("{TOP_USAGE}");
            std::process::exit(2);
        }
    }
}

/// The state root (`EZ2_AIB_STATE_ROOT`, `EZ2_AIB_HOME`, else `~/.ez2-aib`).
fn state_root() -> Result<PathBuf, String> {
    observe::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    )
}

/// The value after flag `k`, if given.
fn flag<'a>(args: &'a [String], k: &str) -> Option<&'a str> {
    args.iter()
        .position(|a| a == k)
        .and_then(|i| args.get(i + 1))
        .map(String::as_str)
}

/// `aib-drive next`: the defaults filled in (aib-slice's cache under `<root>/drive`, the
/// seat facts and the idea ledger read now, the plan of the cockpit), the cache judged
/// stale past [`SLICE_STALE_SECS`] (the full scan past [`FULL_STALE_SECS`]), then
/// [`aib_drive::next::cli_with`].
fn next_main(args: &[String]) -> i32 {
    let root = state_root();
    let mut args = args.to_vec();
    let add = |args: &mut Vec<String>, k: &str, v: String| {
        if flag(args, k).is_none() {
            args.push(k.to_owned());
            args.push(v);
        }
    };
    if let Ok(r) = &root {
        let drive = r.join("drive");
        add(
            &mut args,
            "--due",
            drive.join("slice-due.json").display().to_string(),
        );
        add(
            &mut args,
            "--sweep",
            drive.join("slice-sweep.json").display().to_string(),
        );
        add(
            &mut args,
            "--sweep-full",
            drive.join("slice-full-sweep.json").display().to_string(),
        );
        // act's intents ledger (IFACE r9 3); no file is nothing claimed, so not passed
        let intents = drive.join("intents.jsonl");
        if intents.is_file() {
            add(&mut args, "--intents", intents.display().to_string());
        }
        let plan = r.join("pending").join("aib2-a63ab-1107").join("cabin-plan");
        add(
            &mut args,
            "--plan",
            plan.join("data.json").display().to_string(),
        );
    }
    add(&mut args, "--seats", LIVE_SEATS.to_owned());
    add(&mut args, "--ideas", LIVE_IDEAS.to_owned());
    let caches: Vec<(String, u64)> = [
        ("--due", SLICE_STALE_SECS),
        ("--sweep", SLICE_STALE_SECS),
        ("--sweep-full", FULL_STALE_SECS),
    ]
    .iter()
    .filter_map(|(k, limit)| flag(&args, k).map(|p| (p.to_owned(), *limit)))
    .collect();
    let now = now_secs();
    // where pr: items live, for the seat facts' gh reads and for a pr: the cache does not list
    let repo = std::env::var("AIB_DRIVE_REPO").unwrap_or_else(|_| DEFAULT_REPO.to_owned());
    let read = |p: &str| -> Result<String, String> {
        match p {
            LIVE_SEATS => {
                let mut src = aib_drive::seats::FsSource::new(root.clone()?);
                src.repo = Some(repo.clone());
                let report = aib_drive::seats::report(&src, now);
                // next reads the facts; who is newly free is `aib-drive seats`'s to say
                Ok(aib_drive::seats::to_json(&report, "", &[]).to_string())
            }
            LIVE_IDEAS => {
                let own = root.as_ref().ok().map(|r| r.join("bin").join("aib-idea"));
                let program = own
                    .filter(|p| p.is_file())
                    .map_or_else(|| "aib-idea".to_owned(), |p| p.display().to_string());
                let args = ["list", "--all", "--json"].map(str::to_owned);
                let out = run_fixed(&program, &args, aib_drive::source::CALL_TIMEOUT)?;
                String::from_utf8(out).map_err(|_| "aib-idea: not UTF-8".to_owned())
            }
            _ => {
                if let Some((_, limit)) = caches.iter().find(|(c, _)| c == p) {
                    let age = std::fs::metadata(p)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|t| t.elapsed().ok())
                        .map(|d| d.as_secs());
                    if let Some(a) = age.filter(|a| a > limit) {
                        return Err(format!("stale: {p} 已 {} 分鐘沒更新", a / 60));
                    }
                }
                std::fs::read_to_string(p).map_err(|e| format!("{p}: {e}"))
            }
        }
    };
    // a pr: item aib-slice's cache does not list (--fast: none closed over 72 h ago)
    let gh = GhRead::new();
    let more = aib_drive::next::GhMerged::new(&gh, &repo);
    let (out, rc) = aib_drive::next::cli_with(&args, read, Some(&more), now, &utc(now));
    print!("{out}");
    rc
}

/// One aib-slice scan (1ced7 f20f34e5) that writes both files next reads; aib-slice puts
/// each in place by temp file + rename, so next never reads half a file. `--full` scans
/// every branch (about 15 minutes) instead of `--fast` (open PRs and 72 hours, about 3).
const REFRESH_USAGE: &str = "usage: aib-drive refresh [--full] [--repo DIR]  \
(DIR: the local clone aib-slice scans; else AIB_DRIVE_REPO_DIR; neither ⇒ refused)";
/// Where `refresh` finds the local clone when `--repo` is not given.
const REPO_DIR_ENV: &str = "AIB_DRIVE_REPO_DIR";
/// A scan past this is killed: the old cache stays and goes stale on its own.
const REFRESH_TIMEOUT: Duration = Duration::from_secs(30 * 60);
/// `--max` given to aib-slice for due (519d0 T7 note 1).
const DUE_MAX: &str = "50";
/// `observe --watch` refreshes the caches at a round's start when the last refresh is
/// this old (519d0 T7: no one ran refresh, and at 30 minutes layers 1–3 went stale).
const WATCH_REFRESH_SECS: u64 = 20 * 60;

/// A watch round refreshes when it never did, or the last one is [`WATCH_REFRESH_SECS`]
/// old (a clock gone back counts as due).
fn refresh_due(last: Option<u64>, now: u64) -> bool {
    last.is_none_or(|t| now < t || now - t >= WATCH_REFRESH_SECS)
}

/// The aib-slice argument list for `refresh`. A full scan writes its own pair of files
/// (IFACE r8): next takes layer 4 from it alone, and the fast pair is never swapped for
/// one of another round.
/// The clone and the pending folder are always named (1ced7 M1, IFACE r9 4): aib-slice's
/// own defaults are the folder it was started in and `~/.ez2-aib/pending`, so a refresh
/// started elsewhere, or under another state root, would scan the wrong place.
fn refresh_args(
    drive: &std::path::Path,
    repo: &std::path::Path,
    pending: &std::path::Path,
    full: bool,
) -> Vec<String> {
    let mut a = vec!["sweep".to_owned()];
    if !full {
        a.push("--fast".to_owned());
    }
    // aib-slice writes the staged pair; [`settle`] puts it in place only when whole (T9)
    for (k, f) in ["--out", "--due-out"].into_iter().zip(refresh_files(full)) {
        a.push(k.to_owned());
        a.push(drive.join(format!("{STAGE}{f}")).display().to_string());
    }
    // due keeps 8 items a part by default, which hid #748 (519d0 T7 note 1, until
    // 1ced7's 40f08fed is installed); written after --due-out
    a.push("--max".to_owned());
    a.push(DUE_MAX.to_owned());
    for (k, v) in [("--repo", repo), ("--pending", pending)] {
        a.push(k.to_owned());
        a.push(v.display().to_string());
    }
    a
}

/// What aib-slice writes first: `<drive>/.new-<file>`, renamed over `<file>` by [`settle`].
const STAGE: &str = ".new-";

/// The cache pair a refresh writes: sweep, then due.
fn refresh_files(full: bool) -> [&'static str; 2] {
    if full {
        ["slice-full-sweep.json", "slice-full-due.json"]
    } else {
        ["slice-sweep.json", "slice-due.json"]
    }
}

/// Puts the staged pair over the caches only when aib-slice exited 0 and both files say
/// `complete: true`; otherwise the staged files go and the old caches stay (519d0 T9: a
/// refresh cut short by the network replaced a good 05:00 cache with an incomplete one).
/// `Ok(true)` = replaced, `Ok(false)` = old kept.
fn settle(drive: &std::path::Path, files: &[&str], ran_ok: bool) -> Result<bool, String> {
    let staged: Vec<std::path::PathBuf> = files
        .iter()
        .map(|f| drive.join(format!("{STAGE}{f}")))
        .collect();
    let whole = |p: &std::path::Path| {
        std::fs::read(p)
            .ok()
            .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
            .and_then(|v| v.get("complete").and_then(serde_json::Value::as_bool))
            == Some(true)
    };
    if !(ran_ok && staged.iter().all(|p| whole(p))) {
        for p in &staged {
            let _ = std::fs::remove_file(p);
        }
        return Ok(false);
    }
    for (p, f) in staged.iter().zip(files) {
        std::fs::rename(p, drive.join(f)).map_err(|e| format!("{}: {e}", p.display()))?;
    }
    Ok(true)
}

/// `refresh`'s own arguments: `--full`, and the clone from `--repo` or [`REPO_DIR_ENV`].
/// No clone named, or not a folder ⇒ `Err` (nothing is run and nothing written).
fn refresh_opts(
    args: &[String],
    env_repo: Option<String>,
) -> Result<(bool, std::path::PathBuf), String> {
    let (mut full, mut repo) = (false, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--full" => full = true,
            "--repo" => repo = Some(it.next().ok_or("--repo needs a folder")?.clone()),
            _ => return Err(format!("unknown argument {a}")),
        }
    }
    let repo = repo
        .or(env_repo.filter(|r| !r.is_empty()))
        .ok_or_else(|| format!("no clone named: give --repo DIR or {REPO_DIR_ENV}"))?;
    let repo = std::path::PathBuf::from(repo);
    if !repo.is_dir() {
        return Err(format!("{}: not a folder", repo.display()));
    }
    Ok((full, repo))
}

/// `aib-drive refresh [--full] [--repo DIR]`: exit 0 both files written whole, 2 written
/// but the scan not complete (aib-slice's 2) or usage / no clone named (nothing run), 3
/// aib-slice could not be run.
fn refresh_main(args: &[String]) -> i32 {
    let (full, repo) = match refresh_opts(args, std::env::var(REPO_DIR_ENV).ok()) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("aib-drive refresh: {e}\n{REFRESH_USAGE}");
            return 2;
        }
    };
    refresh_run(&repo, full, true)
}

/// One refresh of the caches from the clone `repo` (exit codes as [`refresh_main`]).
/// `say_ok` false (a watch round) keeps stdout for the round's records.
fn refresh_run(repo: &std::path::Path, full: bool, say_ok: bool) -> i32 {
    let root = match state_root() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            return 3;
        }
    };
    let drive = root.join("drive");
    if let Err(e) = std::fs::create_dir_all(&drive) {
        eprintln!("aib-drive refresh: {}: {e}", drive.display());
        return 3;
    }
    let own = root.join("bin").join("aib-slice");
    let program = if own.is_file() {
        own.display().to_string()
    } else {
        "aib-slice".to_owned()
    };
    let pending = root.join("pending");
    let rc = match run_fixed(
        &program,
        &refresh_args(&drive, repo, &pending, full),
        REFRESH_TIMEOUT,
    ) {
        Ok(_) => 0,
        Err(e) => {
            // aib-slice exits 2 when the scan was not complete
            eprintln!("aib-drive refresh: {e}");
            // run_fixed writes `<program>: exit <code>: <last stderr line>`: only the head
            // is the code; the stderr after it is not read (Codex L1)
            if e.starts_with(&format!("{program}: exit 2: ")) {
                2
            } else {
                3
            }
        }
    };
    match settle(&drive, &refresh_files(full), rc == 0) {
        Ok(true) => {
            if say_ok {
                println!("refreshed {}", drive.display());
            }
            0
        }
        Ok(false) => {
            eprintln!("aib-drive refresh: the scan was not whole; the old caches are kept");
            if rc == 0 {
                2
            } else {
                rc
            }
        }
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            3
        }
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` of unix seconds (UTC), without a date crate.
fn utc(secs: u64) -> String {
    let (days, rest) = (secs / 86_400, secs % 86_400);
    // civil from days (H. Hinnant), days since 1970-01-01
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rest / 3_600,
        rest % 3_600 / 60,
        rest % 60
    )
}

fn observe_main(argv: &[String]) {
    let a = match parse(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-drive: {e}");
            std::process::exit(2);
        }
    };
    let root = match observe::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    ) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("aib-drive: {e}");
            std::process::exit(5);
        }
    };
    let paths = Paths::under(&root);
    let gh = GhRead::new();
    let queue = AibQueue::new();
    let roles = Roles {
        captain: a.captain.clone(),
        collector: a.collector.clone(),
    };
    let clone = a.clone.clone().or_else(|| {
        std::env::var_os(REPO_DIR_ENV)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    });
    if a.watch && clone.is_none() {
        eprintln!("aib-drive: no --clone or {REPO_DIR_ENV}: the caches are not refreshed");
    }
    let mut refreshed: Option<u64> = None;
    let mut round = |prev: &BTreeMap<u64, Sha>| -> Built {
        // 519d0 T7: the caches next reads are refreshed at a round's start; a failed
        // refresh is said and the round goes on
        if let Some(c) = clone.as_deref().filter(|_| a.watch) {
            let now = now_secs();
            if refresh_due(refreshed, now) {
                refreshed = Some(now);
                match refresh_run(c, false, false) {
                    0 => {}
                    rc => eprintln!("aib-drive: refresh exit {rc}; this round goes on"),
                }
            }
        }
        // the quota first: a round must not use up the token every seat shares.
        // The one call outside GhRead's repos/ list: fixed argv `api rate_limit`, a GET.
        let left = run_fixed(&gh.program, &observe::quota_args(), gh.timeout)
            .map_err(|e| format!("gh api rate_limit: {e}"))
            .and_then(|out| {
                serde_json::from_slice(&out).map_err(|_| "gh api rate_limit: bad json".to_owned())
            })
            .and_then(|v| observe::quota_left(&v));
        observe::quota_gate(left, observe::QUOTA_FLOOR)?;
        let s = build(
            &gh,
            &Inputs {
                repo: &a.repo,
                roles: roles.clone(),
                pending: &a.pending,
                prev_heads: prev,
                queue: &queue,
                seats: &a.seats,
            },
        )?;
        let side = a.expect_project.as_deref().map(|uid| {
            observe::expect_side(
                &gh,
                &a.repo,
                aib_expect::load_goals(&root, uid),
                aib_expect::load_evidence(&root, uid),
                &s,
                now_secs(),
            )
        });
        Ok((s, side))
    };
    let show = |r: &Record| {
        println!("{}", observe::summary(r));
        if let Ok(line) = serde_json::to_string(r) {
            println!("{line}");
        }
    };
    if a.watch {
        let mut clock = RealClock::new();
        let mut print = |r: &Record| show(r);
        if let Err(e) = observe::watch_with(
            &paths, &a.repo, a.interval, &mut clock, &mut round, &mut print, None,
        ) {
            eprintln!("aib-drive: {e}");
            std::process::exit(5);
        }
        return;
    }
    let now = now_secs();
    match observe::once_with(&paths, &a.repo, now, &mut round, a.event.as_deref()) {
        Ok(r) => {
            show(&r);
            std::process::exit(if r.unknown.is_some() { 3 } else { 0 });
        }
        Err(Refused::Paused(e)) => {
            eprintln!("aib-drive: {e}");
            std::process::exit(4);
        }
        Err(Refused::State(e)) => {
            eprintln!("aib-drive: {e}");
            std::process::exit(5);
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn utc_formats_known_instants() {
        assert_eq!(super::utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(super::utc(1_794_672_000), "2026-11-14T16:00:00Z");
        assert_eq!(super::utc(951_782_400), "2000-02-29T00:00:00Z");
    }

    #[test]
    fn refresh_asks_one_fast_scan_for_both_files() {
        let d = std::path::Path::new("r").join("drive");
        let (repo, pending) = (
            std::path::Path::new("/c"),
            std::path::Path::new("r/pending"),
        );
        let a = super::refresh_args(&d, repo, pending, false);
        assert_eq!(a[..2], ["sweep", "--fast"]);
        assert_eq!(a[2], "--out");
        assert!(
            a[3].ends_with("/.new-slice-sweep.json") || a[3].ends_with("\\.new-slice-sweep.json"),
            "{a:?}"
        );
        assert_eq!(a[4], "--due-out");
        assert!(a[5].ends_with("slice-due.json"), "{a:?}");
        // --max right after --due-out (519d0 T7 note 1)
        assert_eq!(a[6..8], ["--max", "50"]);
        // the clone and pending are always named (1ced7 M1)
        let at = |k: &str| a.iter().position(|x| x == k).map(|i| a[i + 1].clone());
        assert_eq!(at("--repo").as_deref(), Some("/c"));
        assert_eq!(at("--pending"), Some(pending.display().to_string()));
        let f = super::refresh_args(&d, repo, pending, true);
        assert!(!f.contains(&"--fast".to_owned()));
        assert!(f[2].ends_with("slice-full-sweep.json"), "{f:?}");
        assert!(f[4].ends_with("slice-full-due.json"), "{f:?}");
    }

    #[test]
    fn a_watch_refreshes_at_most_every_twenty_minutes() {
        assert!(super::refresh_due(None, 1_000));
        assert!(!super::refresh_due(Some(1_000), 1_000 + 300));
        assert!(!super::refresh_due(Some(1_000), 1_000 + 1_199));
        assert!(super::refresh_due(Some(1_000), 1_000 + 1_200));
        // a clock gone back is due, not stuck
        assert!(super::refresh_due(Some(5_000), 1_000));
        const { assert!(super::WATCH_REFRESH_SECS <= 20 * 60) };
    }

    #[test]
    fn a_clone_goes_with_watch() {
        let base = [
            "observe",
            "--repo",
            "o/n",
            "--pending",
            "p",
            "--captain",
            "c",
            "--collector",
            "k",
        ];
        let with = |more: &[&str]| -> Vec<String> {
            base.iter()
                .chain(more.iter())
                .map(|x| (*x).to_owned())
                .collect()
        };
        let a = super::parse(&with(&["--watch", "--clone", "/c"])).unwrap();
        assert_eq!(a.clone, Some(std::path::PathBuf::from("/c")));
        assert!(super::parse(&with(&["--once", "--clone", "/c"])).is_err());
        assert_eq!(super::parse(&with(&["--watch"])).unwrap().clone, None);
    }

    #[test]
    fn an_incomplete_refresh_keeps_the_old_caches() {
        let d = std::env::temp_dir().join(format!("aib-drive-settle-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let files = super::refresh_files(false);
        let put = |name: &str, complete: bool| {
            std::fs::write(
                d.join(name),
                format!("{{\"complete\": {complete}, \"n\": \"{name}\"}}"),
            )
            .unwrap();
        };
        let read = |name: &str| std::fs::read_to_string(d.join(name)).unwrap();
        let stage = |f: &str| format!("{}{f}", super::STAGE);
        for f in files {
            put(f, true);
        }
        let old: Vec<String> = files.iter().map(|f| read(f)).collect();
        // aib-slice exit 2: nothing replaced, the staged files gone
        put(&stage(files[0]), true);
        put(&stage(files[1]), true);
        assert_eq!(super::settle(&d, &files, false), Ok(false));
        assert_eq!(files.iter().map(|f| read(f)).collect::<Vec<_>>(), old);
        assert!(!d.join(stage(files[0])).exists());
        // exit 0 but one file says not complete: kept as well
        put(&stage(files[0]), true);
        put(&stage(files[1]), false);
        assert_eq!(super::settle(&d, &files, true), Ok(false));
        assert_eq!(files.iter().map(|f| read(f)).collect::<Vec<_>>(), old);
        // whole: both replaced
        put(&stage(files[0]), true);
        put(&stage(files[1]), true);
        assert_eq!(super::settle(&d, &files, true), Ok(true));
        assert!(read(files[0]).contains(".new-"), "{}", read(files[0]));
        assert!(read(files[1]).contains(".new-"));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn refresh_without_a_clone_is_refused() {
        let none: Vec<String> = Vec::new();
        assert!(super::refresh_opts(&none, None).is_err());
        assert!(super::refresh_opts(&none, Some(String::new())).is_err());
        let dir = std::env::temp_dir().display().to_string();
        assert_eq!(
            super::refresh_opts(&none, Some(dir.clone())),
            Ok((false, std::path::PathBuf::from(&dir)))
        );
        let a: Vec<String> = ["--full", "--repo", &dir].map(str::to_owned).to_vec();
        assert_eq!(
            super::refresh_opts(&a, None),
            Ok((true, std::path::PathBuf::from(&dir)))
        );
        let gone: Vec<String> = ["--repo", "/no/such/clone"].map(str::to_owned).to_vec();
        assert!(super::refresh_opts(&gone, None).is_err());
        let bad: Vec<String> = vec!["--fast".to_owned()];
        assert!(super::refresh_opts(&bad, Some(dir)).is_err());
    }

    #[test]
    fn a_flag_value_is_the_word_after_it() {
        let a: Vec<String> = ["--due", "d", "--json"].map(str::to_owned).to_vec();
        assert_eq!(super::flag(&a, "--due"), Some("d"));
        assert_eq!(super::flag(&a, "--json"), None);
        assert_eq!(super::flag(&a, "--sweep"), None);
    }
}
