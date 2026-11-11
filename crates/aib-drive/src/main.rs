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

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let a = match parse(&argv) {
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
