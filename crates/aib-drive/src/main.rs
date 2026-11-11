//! `aib-drive observe`: dry-run rounds (see `aib_drive::observe`). It never
//! acts: there is no executor in this program.
//!
//! Exit codes: 0 done; 2 usage; 3 a round had no snapshot (`--once`);
//! 4 paused; 5 another observe holds the lock, or a state file failed.

use aib_drive::decide::{Roles, Seat, Sha, Snapshot};
use aib_drive::observe::{self, Paths, RealClock, Record, Refused, BASE};
use aib_drive::snapshot::{build, GhRead, Inputs};
use aib_drive::source::{run_fixed, AibQueue};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

const USAGE: &str = "usage: aib-drive observe --repo OWNER/NAME --pending DIR \
--captain SEAT --collector SEAT [--seats A,B,…] (--once | --watch [--interval SECS])";

struct Args {
    repo: String,
    pending: PathBuf,
    captain: Seat,
    collector: Seat,
    seats: Vec<Seat>,
    watch: bool,
    interval: Duration,
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
            "--repo" | "--pending" | "--captain" | "--collector" | "--seats" | "--interval" => {
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
    Ok(Args {
        repo,
        pending,
        captain,
        collector,
        seats,
        watch,
        interval,
    })
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
    let mut round = |prev: &BTreeMap<u64, Sha>| -> Result<Snapshot, String> {
        // the quota first: a round must not use up the token every seat shares
        let left = run_fixed(&gh.program, &observe::quota_args(), gh.timeout)
            .map_err(|e| format!("gh api rate_limit: {e}"))
            .and_then(|out| {
                serde_json::from_slice(&out).map_err(|_| "gh api rate_limit: bad json".to_owned())
            })
            .and_then(|v| observe::quota_left(&v));
        observe::quota_gate(left, observe::QUOTA_FLOOR)?;
        build(
            &gh,
            &Inputs {
                repo: &a.repo,
                roles: roles.clone(),
                pending: &a.pending,
                prev_heads: prev,
                queue: &queue,
                seats: &a.seats,
            },
        )
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
        if let Err(e) = observe::watch(
            &paths, &a.repo, a.interval, &mut clock, &mut round, &mut print, None,
        ) {
            eprintln!("aib-drive: {e}");
            std::process::exit(5);
        }
        return;
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    match observe::once(&paths, &a.repo, now, &mut round) {
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
