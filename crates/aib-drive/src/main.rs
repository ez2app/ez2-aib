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
(--once [--event ID] | --watch [--interval SECS])";

struct Args {
    repo: String,
    pending: PathBuf,
    captain: Seat,
    collector: Seat,
    seats: Vec<Seat>,
    watch: bool,
    interval: Duration,
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
            | "--expect-project" | "--event" => {
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
const TOP_USAGE: &str = "usage: aib-drive <observe|next|seats|event|act|switch> …
  next     what can be done now, best first (read only)
  seats    the seat facts
  event    write one wake-up event (merge / receipt / review / dm_done / seat_free / tick)
  act      the dispatch dry run;  switch  turn one of act's switches on or off
  observe  the old dry-run rounds";

/// `--seats` default: the seat facts read now, in this process (read only: the idle log
/// that `aib-drive seats` appends is not written).
const LIVE_SEATS: &str = "@live-seats";
/// `--ideas` default: `aib-idea list --all --json`, run now.
const LIVE_IDEAS: &str = "@live-ideas";
/// aib-slice's cache older than this is `stale` (IFACE r5: 30 minutes).
const SLICE_STALE_SECS: u64 = 30 * 60;

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
/// stale past [`SLICE_STALE_SECS`], then [`aib_drive::next::cli`].
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
        let plan = r.join("pending").join("aib2-a63ab-1107").join("cabin-plan");
        add(
            &mut args,
            "--plan",
            plan.join("data.json").display().to_string(),
        );
    }
    add(&mut args, "--seats", LIVE_SEATS.to_owned());
    add(&mut args, "--ideas", LIVE_IDEAS.to_owned());
    let caches: Vec<String> = ["--due", "--sweep"]
        .iter()
        .filter_map(|k| flag(&args, k).map(str::to_owned))
        .collect();
    let now = now_secs();
    let read = |p: &str| -> Result<String, String> {
        match p {
            LIVE_SEATS => {
                let r = root.clone()?;
                let report = aib_drive::seats::report(&aib_drive::seats::FsSource { root: r }, now);
                Ok(aib_drive::seats::to_json(&report, "").to_string())
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
                if caches.iter().any(|c| c == p) {
                    let age = std::fs::metadata(p)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|t| t.elapsed().ok())
                        .map(|d| d.as_secs());
                    if let Some(a) = age.filter(|a| *a > SLICE_STALE_SECS) {
                        return Err(format!("stale: {p} 已 {} 分鐘沒更新", a / 60));
                    }
                }
                std::fs::read_to_string(p).map_err(|e| format!("{p}: {e}"))
            }
        }
    };
    let (out, rc) = aib_drive::next::cli(&args, read, now, &utc(now));
    print!("{out}");
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
    let mut round = |prev: &BTreeMap<u64, Sha>| -> Built {
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
    fn a_flag_value_is_the_word_after_it() {
        let a: Vec<String> = ["--due", "d", "--json"].map(str::to_owned).to_vec();
        assert_eq!(super::flag(&a, "--due"), Some("d"));
        assert_eq!(super::flag(&a, "--json"), None);
        assert_eq!(super::flag(&a, "--sweep"), None);
    }
}
