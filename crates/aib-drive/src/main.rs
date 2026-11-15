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
use aib_ghread::run_fixed_any;
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
const TOP_USAGE: &str = "usage: aib-drive <observe|next|seats|event|act|switch|refresh> …
  next     what can be done now, best first (read only)
  seats    the seat facts
  event    write one wake-up event (merge / receipt / review / dm_done / seat_free / tick)
  act      the dispatch dry run;  switch  turn one of act's switches on or off
  refresh  update aib-slice's cache for next (--repo DIR or AIB_DRIVE_REPO_DIR; --full)
  observe  the old dry-run rounds";

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
        // layer 7 (C7): tidy-scan's report; no file is layer 7 unsupported, so not passed
        let tidy = drive.join("tidy-scan.json");
        if tidy.is_file() {
            add(&mut args, "--tidy", tidy.display().to_string());
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
const REFRESH_USAGE: &str = "usage: aib-drive refresh [--full] [--repo DIR] | refresh --auto [--repo DIR]  \
(DIR: the local clone aib-slice scans; else AIB_DRIVE_REPO_DIR; --auto also reads drive/refresh-repo; none ⇒ refused)";
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
/// aib-slice could not be run, or another refresh holds drive/refresh.lock.
fn refresh_main(args: &[String]) -> i32 {
    if args.iter().any(|a| a == "--auto") {
        let rest: Vec<String> = args.iter().filter(|a| *a != "--auto").cloned().collect();
        return auto_main(&rest);
    }
    let (full, repo) = match refresh_opts(args, std::env::var(REPO_DIR_ENV).ok()) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("aib-drive refresh: {e}\n{REFRESH_USAGE}");
            return 2;
        }
    };
    refresh_locked(&repo, full, true)
}

/// A manual or watch refresh under the same lock as `refresh --auto` (7b81e B3-1 L3: two
/// scans at once write the same staged files): another run holds it ⇒ exit 3, nothing run.
fn refresh_locked(repo: &std::path::Path, full: bool, say_ok: bool) -> i32 {
    let drive = match state_root() {
        Ok(r) => r.join("drive"),
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            return 3;
        }
    };
    if let Err(e) = std::fs::create_dir_all(&drive) {
        eprintln!("aib-drive refresh: {}: {e}", drive.display());
        return 3;
    }
    under_lock(&drive, now_secs(), || refresh_run(repo, full, say_ok))
}

/// Runs `scan` holding `<drive>/refresh.lock`; another run holding it gives 3 and `scan`
/// is not run (7b81e B3-1 L3).
fn under_lock(drive: &std::path::Path, now: u64, scan: impl FnOnce() -> i32) -> i32 {
    let lock = drive.join(AUTO_LOCK);
    let mine = format!("{} {now}", std::process::id());
    match auto_lock(&lock, &mine, now) {
        Ok(true) => {}
        Ok(false) => {
            eprintln!(
                "aib-drive refresh: another refresh holds {}",
                lock.display()
            );
            return 3;
        }
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            return 3;
        }
    }
    let rc = scan();
    auto_unlock(&lock, &mine);
    rc
}

/// Seconds since `modified`; a time in the future (clock gone back) is no age, so the
/// cache counts as due, not fresh (7b81e B3-1 N2).
fn age_of(modified: Option<std::time::SystemTime>, now: std::time::SystemTime) -> Option<u64> {
    modified
        .and_then(|t| now.duration_since(t).ok())
        .map(|d| d.as_secs())
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
    // the main this round's sweep saw (its staged file, before settle moves it); a failed
    // sweep ⇒ none ⇒ layer 7 reads the tidy report as stale (519d0 09:3x on C7b M1)
    let main = if rc == 0 {
        staged_main(&drive, full)
    } else {
        None
    };
    tidy_refresh(&root, &drive, repo, main);
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

/// tidy-scan's report, read by next's layer 7 (C7); written by every refresh round.
const TIDY_FILE: &str = "tidy-scan.json";
/// Path prefix → plan node, kept by 519d0; given to tidy-scan only when it is there.
const TIDY_NODES: &str = "tidy-nodes.json";
/// tidy-scan reads files and `cargo metadata --no-deps`; past this it is killed.
const TIDY_TIMEOUT: Duration = Duration::from_secs(120);

/// The `aib-xtask` argument list for one tidy-scan of the clone.
fn tidy_args(drive: &std::path::Path, repo: &std::path::Path) -> Vec<String> {
    let mut a = vec![
        "tidy-scan".to_owned(),
        "--json".to_owned(),
        "--root".to_owned(),
        repo.display().to_string(),
    ];
    let nodes = drive.join(TIDY_NODES);
    if nodes.is_file() {
        a.push("--nodes".to_owned());
        a.push(nodes.display().to_string());
    }
    a
}

/// Puts one tidy-scan run in place by temp file + rename (519d0 11-15 08:00). tidy-scan
/// exits 0 complete or 1 partial with its report on stdout; a partial report is written
/// too (next's layer 7 reads its status). Anything else, a report that is not tidy-scan's,
/// or a run that failed or timed out ⇒ the old file stays (none ⇒ layer 7 unsupported).
/// The report gets `at_ms` (when), `head` (the commit scanned, null when not read) and
/// `main` (the main this same refresh's sweep wrote, null when the sweep failed), so layer 7
/// tells an old file kept after a failure by the file's own fields (519d0 09:17, 09:3x on
/// C7b M1). `Ok` = replaced.
fn tidy_settle(
    drive: &std::path::Path,
    ran: Result<(Option<i32>, Vec<u8>), String>,
    at_ms: u64,
    head: Option<String>,
    main: Option<String>,
) -> Result<(), String> {
    let (code, out) = ran?;
    if !matches!(code, Some(0 | 1)) {
        let code = code.map_or_else(|| "signal".to_owned(), |c| c.to_string());
        return Err(format!("tidy-scan exit {code}"));
    }
    let Some(mut report) = serde_json::from_slice::<serde_json::Value>(&out)
        .ok()
        .filter(|v| {
            v.get("schema").and_then(serde_json::Value::as_str)
                == Some(aib_drive::next_local::TIDY_SCHEMA)
        })
    else {
        return Err("tidy-scan printed no report".to_owned());
    };
    report["at_ms"] = serde_json::json!(at_ms);
    report["head"] = serde_json::json!(head);
    report["main"] = serde_json::json!(main);
    let staged = drive.join(format!("{STAGE}{TIDY_FILE}"));
    let put = std::fs::write(&staged, format!("{report}\n"))
        .and_then(|()| std::fs::rename(&staged, drive.join(TIDY_FILE)));
    if let Err(e) = put {
        let _ = std::fs::remove_file(&staged);
        return Err(format!("{}: {e}", staged.display()));
    }
    Ok(())
}

/// `main` of the sweep this refresh just staged (`<drive>/.new-<sweep file>`), if any.
fn staged_main(drive: &std::path::Path, full: bool) -> Option<String> {
    let b = std::fs::read(drive.join(format!("{STAGE}{}", refresh_files(full)[0]))).ok()?;
    serde_json::from_slice::<serde_json::Value>(&b)
        .ok()?
        .get("main")?
        .as_str()
        .filter(|m| !m.is_empty())
        .map(str::to_owned)
}

/// One tidy-scan for the round; a failure is only said on stderr and never changes the
/// refresh's exit code (519d0 11-15 08:00).
fn tidy_refresh(
    root: &std::path::Path,
    drive: &std::path::Path,
    repo: &std::path::Path,
    main: Option<String>,
) {
    let own = root.join("bin").join("aib-xtask");
    let program = if own.is_file() {
        own.display().to_string()
    } else {
        "aib-xtask".to_owned()
    };
    // the commit tidy-scan reads (its working tree); layer 7 compares it with main
    let head = run_fixed(
        "git",
        &[
            "-C".to_owned(),
            repo.display().to_string(),
            "rev-parse".to_owned(),
            "HEAD".to_owned(),
        ],
        Duration::from_secs(10),
    )
    .ok()
    .map(|o| String::from_utf8_lossy(&o).trim().to_owned())
    .filter(|h| !h.is_empty());
    let ran = run_fixed_any(&program, &tidy_args(drive, repo), TIDY_TIMEOUT);
    if let Err(e) = tidy_settle(drive, ran, now_secs().saturating_mul(1000), head, main) {
        eprintln!("aib-drive refresh: {e}; the old {TIDY_FILE} is kept");
    }
}

/// `refresh --auto` (519d0 11-15 07:26, Andy「最快可上線」): aibd's `drive.refresh` timer runs
/// it every few minutes, so the caches stay fresh with no window open. It refreshes only
/// when the fast cache is [`WATCH_REFRESH_SECS`] old (or missing) and the last try is as
/// old too (a scan the network cuts short is not retried every few minutes), nobody else
/// holds [`AUTO_LOCK`], and GitHub has at least [`observe::QUOTA_FLOOR`] calls left.
/// The clone: `--repo`, else `AIB_DRIVE_REPO_DIR`, else the one line of
/// `<drive>/`[`AUTO_REPO_FILE`] (aibd's environment is not a login shell's).
const AUTO_LOCK: &str = "refresh.lock";
/// When the last automatic try started (unix seconds), written before the scan.
const AUTO_TRIED: &str = "refresh-tried";
/// The clone's path for `refresh --auto` when neither `--repo` nor the env names one.
const AUTO_REPO_FILE: &str = "refresh-repo";
/// A lock older than the longest scan is left by a killed run and is taken over.
const AUTO_LOCK_STALE_SECS: u64 = REFRESH_TIMEOUT.as_secs() + 5 * 60;

/// What `refresh --auto` does this time.
#[derive(Debug, PartialEq, Eq)]
enum Auto {
    /// The fast cache is newer than [`WATCH_REFRESH_SECS`].
    Fresh,
    /// The cache is old but the last try is newer than [`WATCH_REFRESH_SECS`].
    TriedLately,
    Run,
}

/// `cache_age`: seconds since the fast sweep file changed (`None` = no file);
/// `tried`: when the last automatic try started.
fn auto_due(cache_age: Option<u64>, tried: Option<u64>, now: u64) -> Auto {
    if cache_age.is_some_and(|a| a < WATCH_REFRESH_SECS) {
        return Auto::Fresh;
    }
    if !refresh_due(tried, now) {
        return Auto::TriedLately;
    }
    Auto::Run
}

/// Takes `<drive>/refresh.lock` for this run: `Ok(true)` taken, `Ok(false)` another run
/// holds it. A lock past [`AUTO_LOCK_STALE_SECS`], unreadable, or from a clock gone back
/// was left by a killed run: it is removed and taken once more.
fn auto_lock(path: &std::path::Path, mine: &str, now: u64) -> Result<bool, String> {
    use std::io::Write as _;
    for first in [true, false] {
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
        {
            Ok(mut f) => {
                f.write_all(mine.as_bytes())
                    .map_err(|e| format!("{}: {e}", path.display()))?;
                return Ok(true);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                // a lock made but not yet written reads empty: its file time is its age,
                // so it is not taken for a stale one (5d33a B3-1 L1)
                let at = std::fs::read_to_string(path)
                    .ok()
                    .and_then(|s| {
                        s.split_whitespace()
                            .nth(1)
                            .and_then(|t| t.parse::<u64>().ok())
                    })
                    .or_else(|| {
                        std::fs::metadata(path)
                            .and_then(|m| m.modified())
                            .ok()
                            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                            .map(|d| d.as_secs())
                    });
                let held = at.is_some_and(|t| now >= t && now - t < AUTO_LOCK_STALE_SECS);
                if held || !first {
                    return Ok(false);
                }
                let _ = std::fs::remove_file(path);
            }
            Err(e) => return Err(format!("{}: {e}", path.display())),
        }
    }
    Ok(false)
}

/// Removes the lock only while it is still this run's (a run that took over a stale lock
/// is not unlocked by the old one).
fn auto_unlock(path: &std::path::Path, mine: &str) {
    if std::fs::read_to_string(path).ok().as_deref() == Some(mine) {
        let _ = std::fs::remove_file(path);
    }
}

/// `aib-drive refresh --auto [--repo DIR]`: exit 0 refreshed, fresh, tried lately, held by
/// another run, or the quota kept for the seats (each said on stderr); 2 no clone named or
/// the scan not whole; 3 could not run (no state root, the quota unreadable, aib-slice).
fn auto_main(args: &[String]) -> i32 {
    let root = match state_root() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("aib-drive refresh --auto: {e}");
            return 3;
        }
    };
    let drive = root.join("drive");
    let from_file = std::fs::read_to_string(drive.join(AUTO_REPO_FILE))
        .ok()
        .map(|s| s.trim().to_owned());
    let env_repo = std::env::var(REPO_DIR_ENV)
        .ok()
        .filter(|r| !r.is_empty())
        .or(from_file);
    let (full, repo) = match refresh_opts(args, env_repo) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("aib-drive refresh --auto: {e} (or the one line of drive/{AUTO_REPO_FILE})");
            return 2;
        }
    };
    if full {
        eprintln!("aib-drive refresh --auto: --full is never automatic");
        return 2;
    }
    let now = now_secs();
    let age = std::fs::metadata(drive.join(refresh_files(false)[0]))
        .and_then(|m| m.modified())
        .ok();
    let age = age_of(age, std::time::SystemTime::now());
    let tried = std::fs::read_to_string(drive.join(AUTO_TRIED))
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok());
    match auto_due(age, tried, now) {
        Auto::Fresh => {
            eprintln!("aib-drive refresh --auto: the caches are fresh");
            return 0;
        }
        Auto::TriedLately => {
            eprintln!("aib-drive refresh --auto: tried within 20 minutes; next time");
            return 0;
        }
        Auto::Run => {}
    }
    if let Err(e) = std::fs::create_dir_all(&drive) {
        eprintln!("aib-drive refresh --auto: {}: {e}", drive.display());
        return 3;
    }
    let lock = drive.join(AUTO_LOCK);
    let mine = format!("{} {now}", std::process::id());
    match auto_lock(&lock, &mine, now) {
        Ok(true) => {}
        Ok(false) => {
            eprintln!(
                "aib-drive refresh --auto: another refresh holds {}",
                lock.display()
            );
            return 0;
        }
        Err(e) => {
            eprintln!("aib-drive refresh --auto: {e}");
            return 3;
        }
    }
    // the quota after the lock: one read per try, and none while another run scans
    let gh = GhRead::new();
    let left = run_fixed(&gh.program, &observe::quota_args(), gh.timeout)
        .map_err(|e| format!("gh api rate_limit: {e}"))
        .and_then(|out| {
            serde_json::from_slice(&out).map_err(|_| "gh api rate_limit: bad json".to_owned())
        })
        .and_then(|v| observe::quota_left(&v));
    let unreadable = left.is_err();
    if let Err(e) = observe::quota_gate(left, observe::QUOTA_FLOOR) {
        auto_unlock(&lock, &mine);
        eprintln!("aib-drive refresh --auto: {e}");
        return if unreadable { 3 } else { 0 };
    }
    let _ = std::fs::write(drive.join(AUTO_TRIED), now.to_string());
    let rc = refresh_run(&repo, false, false);
    auto_unlock(&lock, &mine);
    rc
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
                match refresh_locked(c, false, false) {
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

    /// 519d0 11-15 07:26: aibd runs `refresh --auto` every few minutes; it scans only when
    /// the cache is 20 minutes old and the last try is too.
    #[test]
    fn auto_refresh_runs_only_on_a_stale_cache_not_tried_lately() {
        use super::{auto_due, Auto};
        let now = 10_000;
        assert_eq!(auto_due(Some(60), None, now), Auto::Fresh);
        assert_eq!(auto_due(Some(1_199), None, now), Auto::Fresh);
        assert_eq!(auto_due(Some(1_200), None, now), Auto::Run);
        assert_eq!(auto_due(None, None, now), Auto::Run, "no cache yet is due");
        // a scan the network cut short left the old cache: not again within 20 minutes
        assert_eq!(
            auto_due(Some(5_000), Some(now - 300), now),
            Auto::TriedLately
        );
        assert_eq!(auto_due(None, Some(now - 1_199), now), Auto::TriedLately);
        assert_eq!(auto_due(Some(5_000), Some(now - 1_200), now), Auto::Run);
        // a clock gone back is due, not stuck
        assert_eq!(auto_due(Some(5_000), Some(now + 60), now), Auto::Run);
    }

    #[test]
    fn auto_refresh_lock_is_one_holder_and_a_stale_lock_is_taken_over() {
        let d = std::env::temp_dir().join(format!("aib-drive-autolock-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join(super::AUTO_LOCK);
        let now = 100_000;
        let a = format!("11 {now}");
        assert_eq!(super::auto_lock(&p, &a, now), Ok(true));
        // a second run while the first holds it
        assert_eq!(super::auto_lock(&p, "22 x", now + 60), Ok(false));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), a);
        // past the stale age the lock was left by a killed run
        let late = now + super::AUTO_LOCK_STALE_SECS;
        let b = format!("33 {late}");
        assert_eq!(super::auto_lock(&p, &b, late), Ok(true));
        // the old holder does not remove the new holder's lock
        super::auto_unlock(&p, &a);
        assert_eq!(std::fs::read_to_string(&p).unwrap(), b);
        super::auto_unlock(&p, &b);
        assert!(!p.exists());
        // a lock with no time is aged by its file time (5d33a L1): one just made and not yet
        // written is held, not stale
        std::fs::write(&p, "").unwrap();
        let real = super::now_secs();
        assert_eq!(super::auto_lock(&p, "44 y", real), Ok(false));
        // the same lock long after its file time was left by a killed run
        let later = real + super::AUTO_LOCK_STALE_SECS;
        assert_eq!(super::auto_lock(&p, &a, later), Ok(true));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 7b81e B3-1 L3 (5d33a L3: test and knife): a manual or watch refresh takes the same
    /// lock; while another run holds it nothing is scanned and the exit is 3.
    #[test]
    fn a_manual_refresh_does_not_scan_while_another_holds_the_lock() {
        let d = std::env::temp_dir().join(format!("aib-drive-underlock-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let now = super::now_secs();
        let p = d.join(super::AUTO_LOCK);
        std::fs::write(&p, format!("99 {now}")).unwrap();
        let mut ran = false;
        assert_eq!(
            super::under_lock(&d, now, || {
                ran = true;
                0
            }),
            3
        );
        assert!(!ran, "no scan while another run holds the lock");
        std::fs::remove_file(&p).unwrap();
        assert_eq!(
            super::under_lock(&d, now, || {
                ran = true;
                7
            }),
            7
        );
        assert!(ran);
        assert!(!p.exists(), "the run unlocks its own lock");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 7b81e B3-1 N2 (5d33a L3): a cache time in the future is no age, so it is due.
    #[test]
    fn a_cache_time_in_the_future_is_due_not_fresh() {
        use std::time::{Duration, SystemTime};
        let now = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000);
        assert_eq!(
            super::age_of(Some(now - Duration::from_secs(90)), now),
            Some(90)
        );
        assert_eq!(
            super::age_of(Some(now + Duration::from_secs(3_600)), now),
            None
        );
        assert_eq!(super::age_of(None, now), None);
        assert_eq!(
            super::auto_due(
                super::age_of(Some(now + Duration::from_secs(3_600)), now),
                None,
                1_000_000
            ),
            super::Auto::Run
        );
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
    fn tidy_scan_is_put_in_place_only_from_a_report() {
        let d = std::env::temp_dir().join(format!("aib-drive-tidy-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let file = d.join(super::TIDY_FILE);
        let read = || std::fs::read_to_string(&file).ok();
        let report = |status: &str| {
            format!(
                "{{\"schema\":\"{}\",\"status\":\"{status}\",\"items\":[]}}",
                aib_drive::next_local::TIDY_SCHEMA
            )
            .into_bytes()
        };
        // nodes file only when it is there
        assert!(!super::tidy_args(&d, &d).contains(&"--nodes".to_owned()));
        std::fs::write(d.join(super::TIDY_NODES), "{\"*\":\"S76d\"}").unwrap();
        assert!(super::tidy_args(&d, &d).contains(&"--nodes".to_owned()));
        // could not run / timed out: nothing written, layer 7 stays unsupported
        assert!(super::tidy_settle(&d, Err("aib-xtask: timed out".into()), 7, None, None).is_err());
        assert_eq!(read(), None);
        // exit 0 complete: written
        assert_eq!(
            super::tidy_settle(
                &d,
                Ok((Some(0), report("complete"))),
                1_000,
                Some("abc".into()),
                Some("m9".into())
            ),
            Ok(())
        );
        let good = read().unwrap();
        assert!(good.contains("complete"));
        // stamped with when and what was scanned (519d0 09:17)
        let v: serde_json::Value = serde_json::from_str(&good).unwrap();
        assert_eq!(
            (v["at_ms"].as_u64(), v["head"].as_str(), v["main"].as_str()),
            (Some(1_000), Some("abc"), Some("m9")),
            "and with the main of the same refresh (519d0 09:3x on C7b M1)"
        );
        // that main is read from the sweep the same refresh staged
        assert_eq!(super::staged_main(&d, false), None);
        let staged = d.join(format!(
            "{}{}",
            super::STAGE,
            super::refresh_files(false)[0]
        ));
        std::fs::write(&staged, "{\"complete\":true,\"main\":\"m9\"}").unwrap();
        assert_eq!(super::staged_main(&d, false).as_deref(), Some("m9"));
        assert_eq!(
            super::staged_main(&d, true),
            None,
            "the full sweep is another file"
        );
        std::fs::remove_file(&staged).unwrap();
        // exit 1 partial: written too, layer 7 reads the status
        assert_eq!(
            super::tidy_settle(&d, Ok((Some(1), report("partial"))), 2_000, None, None),
            Ok(())
        );
        assert!(read().unwrap().contains("partial"));
        std::fs::write(&file, &good).unwrap();
        // exit 2, a signal, or stdout that is not a report: the old file stays
        for ran in [
            Ok((Some(2), report("failed"))),
            Ok((None, report("complete"))),
            Ok((Some(0), b"{\"schema\":\"other\"}".to_vec())),
            Ok((Some(0), b"TIDY dead=0".to_vec())),
        ] {
            assert!(super::tidy_settle(&d, ran, 3_000, None, None).is_err());
            assert_eq!(read().as_deref(), Some(good.as_str()));
        }
        assert!(!d
            .join(format!("{}{}", super::STAGE, super::TIDY_FILE))
            .exists());
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
