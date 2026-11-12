//! `aib-expect`: the human's expectations against the evidence.
//!
//! ```text
//! aib-expect show  --project <uid> [--short | --json] [--ai-order A5,A4b --why "<reason>"]
//! aib-expect event --project <uid> --kind <session-start|pr-merged|release|order-closed|now-done>
//!                  --seat <seat> [--pr <n>]
//! aib-expect check-goal --project <uid> --seat <seat> --now-dir <dir> [--json]
//!                  (always offline; see goalcheck.rs: rc 0 ok, 4 missing/unknown, 5 reminder)
//! common:          [--state-root <dir>] [--repo <owner/name>] [--offline] [--now <epoch>]
//! ```
//!
//! `show` prints; `event` evaluates and appends one line to
//! `expect-evals.jsonl` (every event, also when its display is merged).
//! PR heads are read with GET only through aib-drive's reader; `--offline`
//! reads none (every PR-bound row is then unverified).
//!
//! Exit: 0 done; 2 bad arguments; 3 the goals cannot be used (the code is
//! printed; `event` still records it); `check-goal` also 4 / 5.

use aib_expect as ex;
use std::collections::BTreeMap;
use std::process::ExitCode;

struct Args {
    cmd: String,
    flags: BTreeMap<String, String>,
    switches: Vec<String>,
}

const VALUED: &[&str] = &[
    "--project",
    "--state-root",
    "--repo",
    "--now",
    "--ai-order",
    "--why",
    "--kind",
    "--seat",
    "--pr",
    "--now-dir",
];
const SWITCHES: &[&str] = &["--short", "--json", "--offline"];

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut it = argv.iter();
    let cmd = it.next().cloned().unwrap_or_default();
    if !["show", "event", "check-goal"].contains(&cmd.as_str()) {
        return Err(
            "usage: aib-expect show|event|check-goal --project <uid> … (see the source header)"
                .to_owned(),
        );
    }
    let mut flags = BTreeMap::new();
    let mut switches = Vec::new();
    while let Some(a) = it.next() {
        if VALUED.contains(&a.as_str()) {
            let v = it.next().ok_or(format!("{a} needs a value"))?;
            if flags.insert(a.clone(), v.clone()).is_some() {
                return Err(format!("{a} given twice"));
            }
        } else if SWITCHES.contains(&a.as_str()) {
            switches.push(a.clone());
        } else {
            return Err(format!("unknown argument {a}"));
        }
    }
    Ok(Args {
        cmd,
        flags,
        switches,
    })
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

fn run(a: &Args) -> Result<u8, String> {
    let get = |k: &str| a.flags.get(k).map(String::as_str);
    let on = |k: &str| a.switches.iter().any(|s| s == k);
    let uid = get("--project").ok_or("--project <uid> is required")?;
    let root = ex::state_root(get("--state-root")).ok_or("no state root (set --state-root)")?;
    let now = match get("--now") {
        Some(n) => n.parse().map_err(|_| "--now needs epoch seconds")?,
        None => now_secs(),
    };
    let repo = get("--repo").unwrap_or("ez2app/ez2-aib");
    // an unreadable evidence file still lets `event` record (it says why)
    let evidence = ex::load_evidence(&root, uid);
    // goals first: only their rows read PR heads (FOLLOWUP X2)
    let goals = ex::load_goals(&root, uid);
    let prs = match (&goals, &evidence) {
        (Ok(g), Ok(e)) => ex::prs_of(g, e, now),
        _ => Default::default(),
    };
    // check-goal runs in the Stop hook: never a network call
    let snap = if !ex::goalcheck::reads_pr_heads(&a.cmd, on("--offline")) {
        ex::Snapshot {
            pr_heads: prs.into_iter().map(|n| (n, None)).collect(),
        }
    } else {
        ex::read_heads(&aib_drive::snapshot::GhRead::new(), repo, &prs)
    };
    if a.cmd == "event" {
        let ev = ex::Event {
            kind: get("--kind").ok_or("--kind is required")?.to_owned(),
            seat: get("--seat").ok_or("--seat is required")?.to_owned(),
            pr: get("--pr")
                .map(|p| p.parse().map_err(|_| "--pr needs a number"))
                .transpose()?,
            at: now,
        };
        let rec = ex::record_event(&root, uid, &ev, &snap)?;
        return Ok(match rec.result {
            Err(code) => {
                println!("{code} (recorded)");
                3
            }
            Ok(_) if rec.merged_display => {
                println!("recorded; shown with the evaluation under 60 s ago");
                0
            }
            Ok(r) => {
                print!("{}", ex::render_short(&r, None));
                0
            }
        });
    }
    let goals = match goals {
        Ok(g) => g,
        Err(e) => {
            println!("{e}");
            return Ok(3);
        }
    };
    if a.cmd == "check-goal" {
        let seat = get("--seat").ok_or("--seat is required")?;
        let dir = get("--now-dir").ok_or("--now-dir is required")?;
        let evidence = match evidence {
            Ok(e) => e,
            Err(e) => {
                println!("{e}");
                return Ok(3);
            }
        };
        let (stage, history) = match ex::goalcheck::read_seat(std::path::Path::new(dir), seat) {
            Ok(x) => x,
            Err(e) if e.starts_with("bad seat code") => return Err(e),
            Err(e) => {
                println!("expect_now_unreadable: {e}");
                return Ok(3);
            }
        };
        let report = ex::compare(&goals, &evidence, &snap, now);
        let v = ex::goalcheck::check(&evidence, &report, stage.as_ref(), &history);
        if on("--json") {
            println!("{}", v.to_json());
        } else if v.codes.is_empty() {
            println!("ok {}", v.goal.as_deref().unwrap_or(""));
        } else {
            for c in &v.codes {
                println!("{c} {}", v.goal.as_deref().unwrap_or("-"));
            }
        }
        return Ok(v.rc());
    }
    let evidence = evidence.map_err(|e| e.to_string())?;
    let report = ex::compare(&goals, &evidence, &snap, now);
    let ai = match get("--ai-order") {
        Some(ids) => Some(ex::AiOrder::new(
            ids.split(',').map(|s| s.trim().to_owned()).collect(),
            get("--why").unwrap_or(""),
            &report,
        )?),
        None if get("--why").is_some() => return Err("--why goes with --ai-order".to_owned()),
        None => None,
    };
    if on("--json") {
        println!("{}", ex::to_json(&report));
    } else if on("--short") {
        print!("{}", ex::render_short(&report, ai.as_ref()));
    } else {
        print!("{}", ex::render(&report, ai.as_ref()));
    }
    Ok(0)
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    match parse(&argv).and_then(|a| run(&a)) {
        Ok(rc) => ExitCode::from(rc),
        Err(e) => {
            eprintln!("aib-expect: {e}");
            ExitCode::from(2)
        }
    }
}
