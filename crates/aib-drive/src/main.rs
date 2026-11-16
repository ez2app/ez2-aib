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
use aib_drive::snapshot::{build, GhRead, GitCarry, Inputs};
use aib_drive::source::{run_fixed, AibQueue, DEFAULT_REPO};
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
const TOP_USAGE: &str =
    "usage: aib-drive <observe|next|own|round|seats|event|act|switch|refresh|unblock> …
  next     what can be done now, best first (read only)
  own      what one seat can push on by itself now, top 3 (read only)
  round    the steps of one round for a seat: its first item, inbox, ledger, throttle (read only)
  seats    the seat facts
  event    write one wake-up event (merge / receipt / review / dm_done / seat_free / tick)
  act      the dispatch dry run;  switch  turn one of act's switches on or off
  refresh  update aib-slice's cache for next (--repo DIR or AIB_DRIVE_REPO_DIR; --full)
  unblock  merge drive/unblock-src/*.json into unblock.json + blocking/<seat>.txt (--write saves)
  observe  the old dry-run rounds";

/// `--seats` default: the seat facts read now, in this process (read only: the idle log
/// that `aib-drive seats` appends is not written).
const LIVE_SEATS: &str = "@live-seats";
/// `--ideas` default: `aib-idea list --all --json`, run now.
const LIVE_IDEAS: &str = "@live-ideas";
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
        Some("own") => std::process::exit(own_main(&rest)),
        Some("round") => std::process::exit(round_main(&rest)),
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
        Some("act") if rest.iter().any(|a| a == "--auto") => {
            std::process::exit(act_auto_main(&rest));
        }
        Some("act") => std::process::exit(i32::from(aib_drive::act::cli_act(&rest))),
        Some("switch") => std::process::exit(i32::from(aib_drive::act::cli_switch(&rest))),
        Some("unblock") => std::process::exit(i32::from(aib_drive::unblock::cli(&rest))),
        _ => {
            eprintln!("{TOP_USAGE}");
            std::process::exit(2);
        }
    }
}

const OWN_USAGE: &str = "usage: aib-drive own --seat <5 hex> [--role dev|review|ops|coordinator] [--json] \
[--plan FILE] [--plan-txt FILE] [--dms FILE]  (defaults: the cockpit plan, <root>/state/drive/plan-<seat>.txt, \
the seat's dm queue waiting + read tabs; rc 0 some, 4 empty, 3 the plan unreadable, 2 usage)\n\
       aib-drive own done --seat <5 hex> --id <id> --evidence <type>:<ref>\n\
       aib-drive own progress --seat <5 hex> --id <id> --evidence <type>:<ref> --next <text>\n\
       aib-drive own none --seat <5 hex> --why <text>\n\
  (one line to <root>/drive/own.jsonl; type = file / commit / pr / receipt / review / node / dm, \
node is progress only; rc 0 written, 1 refused, 3 not writable)";

/// `aib-drive own` (S79-own o-1): the sources read now, then [`aib_drive::own::own_list`].
fn own_main(all_args: &[String]) -> i32 {
    // o-2: `own done | progress | none …` re-runs the list below, then writes one ledger line
    let sub = all_args
        .first()
        .map(String::as_str)
        .filter(|a| aib_drive::own_ledger::KINDS.contains(a));
    let args = if sub.is_some() {
        &all_args[1..]
    } else {
        all_args
    };
    let Some((seat, out, root)) = own_compute(args) else {
        eprintln!("{OWN_USAGE}");
        return 2;
    };
    let seat = seat.as_str();
    if let Some(kind) = sub {
        return own_write(kind, seat, args, &out, root.as_deref());
    }
    if args.iter().any(|a| a == "--json") {
        println!(
            "{}",
            serde_json::to_string_pretty(&out).unwrap_or_else(|_| "{}".to_owned())
        );
    } else {
        print!("{}", aib_drive::own::render(&out));
    }
    out.rc()
}

const ROUND_USAGE: &str =
    "usage: aib-drive round --seat <5 hex> [--role dev|review|ops|coordinator] [--sends] \
[--plan FILE] [--plan-txt FILE] [--dms FILE]  (read only; --sends adds the dispatch throttle, \
default on for the coordinator; rc 0, 2 usage)\n\
       aib-drive round done --seat <5 hex> --recv <N> --sent <N> --own <own id | none --why <text>> \
[--dm-in q_…,…] [--dm-out q_…,…] [--trigger 自動推進|真人]\n\
  (one line to <root>/drive/rounds.jsonl, a round with nothing done too; who started the turn is \
read from the transcript the turn-start hook named unless --trigger is given; rc 0 written, \
1 refused, 3 not writable)\n\
       aib-drive round log --seat <5 hex> [--since 30m|24h|7d|<RFC 3339>] [--json] [--file FILE]\n\
  (one line a round, times in Taipei; rounds whose starter cannot be told are listed as 分不出來)";

/// `aib-drive round` (S79-own o-3): the same sources as `own`, then [`aib_drive::round::steps`];
/// `round done | log` (o5) write and read the round ledger.
fn round_main(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("done") => return round_done(&args[1..]),
        Some("log") => return round_log(&args[1..]),
        _ => {}
    }
    let Some((_, out, _)) = own_compute(args) else {
        eprintln!("{ROUND_USAGE}");
        return 2;
    };
    let sends = args.iter().any(|a| a == "--sends") || out.role == "coordinator";
    print!("{}", aib_drive::round::steps(&out, sends));
    0
}

/// The last [`aib_own_gate::TAIL_BYTES`] of a file as text (a cut first line dropped).
fn read_tail(path: &str) -> Option<String> {
    use std::io::{Read as _, Seek as _, SeekFrom};
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    let cut = len > aib_own_gate::TAIL_BYTES;
    if cut {
        f.seek(SeekFrom::Start(len - aib_own_gate::TAIL_BYTES))
            .ok()?;
    }
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    Some(if cut {
        text.split_once('\n')
            .map(|(_, r)| r.to_owned())
            .unwrap_or_default()
    } else {
        text
    })
}

/// `aib-drive round done` (S79-own o5): one line to `<root>/drive/rounds.jsonl` in one append
/// write, after [`aib_drive::rounds::build_line`] checks it.
fn round_done(args: &[String]) -> i32 {
    use aib_drive::rounds as r;
    use std::io::Write as _;
    let Some(seat) = flag(args, "--seat") else {
        eprintln!("{ROUND_USAGE}");
        return r::RC_USAGE;
    };
    let Ok(root) = state_root() else {
        eprintln!("round done: 狀態根讀不到,沒寫");
        return r::RC_WRITE;
    };
    let dir = root.join("drive");
    let own = std::fs::read_to_string(dir.join(aib_drive::own_ledger::LEDGER)).unwrap_or_default();
    let turn = r::current_turn(&own, seat, &read_tail);
    let path = dir.join(r::LEDGER);
    let rounds = std::fs::read_to_string(&path).unwrap_or_default();
    let req = r::DoneReq {
        seat,
        recv: flag(args, "--recv"),
        sent: flag(args, "--sent"),
        own: flag(args, "--own"),
        why: flag(args, "--why"),
        dm_in: flag(args, "--dm-in"),
        dm_out: flag(args, "--dm-out"),
        trigger: flag(args, "--trigger"),
        // milliseconds: the gate compares this with the turn start, which is in ms
        at_ms: aib_drive::act::now_ms(),
    };
    let line = match r::build_line(&req, turn.as_ref(), &rounds) {
        Ok(v) => v.to_string(),
        Err(why) => {
            eprintln!("round done: 沒寫 —— {why}");
            return r::RC_REFUSED;
        }
    };
    let wrote = std::fs::create_dir_all(&dir).and_then(|()| {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?
            .write_all(format!("{line}\n").as_bytes())
    });
    match wrote {
        Ok(()) => {
            println!("{line}");
            r::RC_OK
        }
        Err(e) => {
            eprintln!("round done: 帳本寫不進 {}:{e}", path.display());
            r::RC_WRITE
        }
    }
}

/// `aib-drive round log` (S79-own o5): the seat's rounds, one line each, or `--json`.
fn round_log(args: &[String]) -> i32 {
    use aib_drive::rounds as r;
    let Some(seat) = flag(args, "--seat").filter(|s| aib_drive::next_local::is_seat(s)) else {
        eprintln!("{ROUND_USAGE}");
        return r::RC_USAGE;
    };
    let now = aib_drive::act::now_ms();
    let since = match flag(args, "--since") {
        Some(v) => match r::parse_since(v, now) {
            Some(ms) => Some(ms),
            None => {
                eprintln!("round log: --since 看不懂:{v}(30m / 24h / 7d / RFC 3339)");
                return r::RC_USAGE;
            }
        },
        None => None,
    };
    let path = match flag(args, "--file") {
        Some(f) => PathBuf::from(f),
        None => match state_root() {
            Ok(root) => root.join("drive").join(r::LEDGER),
            Err(e) => {
                eprintln!("round log: 狀態根讀不到:{e}");
                return r::RC_WRITE;
            }
        },
    };
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => {
            eprintln!("round log: 讀不到 {}:{e}", path.display());
            return r::RC_WRITE;
        }
    };
    let rows = r::rows(&text, seat, since);
    if args.iter().any(|a| a == "--json") {
        println!(
            "{}",
            serde_json::to_string_pretty(&rows).unwrap_or_else(|_| "[]".to_owned())
        );
    } else {
        print!("{}", r::render(seat, &rows));
    }
    r::RC_OK
}

/// The seat, its list read now, and the state root: what `own` and `round` share. `None` =
/// usage (no seat, a bad seat or role).
fn own_compute(args: &[String]) -> Option<(String, aib_drive::own::OwnOut, Option<PathBuf>)> {
    let seat = flag(args, "--seat").filter(|s| aib_drive::next_local::is_seat(s))?;
    let role = flag(args, "--role").unwrap_or_else(|| aib_drive::own::default_role(seat));
    if !aib_drive::own::ROLES.contains(&role) {
        return None;
    }
    let root = state_root().ok();
    let plan_path = flag(args, "--plan").map(PathBuf::from).or_else(|| {
        root.as_ref().map(|r| {
            r.join("pending")
                .join("aib2-a63ab-1107")
                .join("cabin-plan")
                .join("data.json")
        })
    });
    let plan: Option<serde_json::Value> = plan_path
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str(&t).ok());
    let txt_path = flag(args, "--plan-txt").map(PathBuf::from).or_else(|| {
        root.as_ref().map(|r| {
            r.join("state")
                .join("drive")
                .join(format!("plan-{seat}.txt"))
        })
    });
    let plan_txt = txt_path.and_then(|p| std::fs::read_to_string(p).ok());
    let dms: Option<Vec<serde_json::Value>> = match flag(args, "--dms") {
        Some(p) => std::fs::read_to_string(p)
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .and_then(|v| v.as_array().cloned()),
        None => {
            let program = aib_drive::progpath::aib_program();
            let mut all = Some(Vec::new());
            for tab in ["waiting", "read"] {
                let a = ["dm-queue", "list", "--seat", seat, "--tab", tab].map(str::to_owned);
                let rows = run_fixed(&program, &a, aib_drive::source::CALL_TIMEOUT)
                    .ok()
                    .and_then(|o| serde_json::from_slice::<serde_json::Value>(&o).ok())
                    .and_then(|v| v.as_array().cloned());
                match (all.as_mut(), rows) {
                    (Some(acc), Some(r)) => acc.extend(r),
                    _ => all = None,
                }
            }
            all
        }
    };
    // claimed by another seat: act's intents not addressed to this one (key ends :<seat>)
    let others = root
        .as_ref()
        .and_then(|r| std::fs::read_to_string(r.join("drive").join("intents.jsonl")).ok())
        .map(|t| {
            let mine = format!(":{seat}\"");
            t.lines()
                .filter(|l| !l.contains(&mine))
                .collect::<Vec<_>>()
                .join("\n")
        });
    let now = now_secs();
    let (claimed, _) = aib_drive::next_local::claimed_from_intents(
        others.as_deref(),
        i64::try_from(now).unwrap_or(i64::MAX).saturating_mul(1000),
    );
    let as_of = utc(now);
    let out = aib_drive::own::own_list(&aib_drive::own::OwnInput {
        seat,
        role,
        plan: plan.as_ref(),
        plan_txt: plan_txt.as_deref(),
        dms: dms.as_deref(),
        claimed: &claimed,
        as_of: &as_of,
    });
    Some((seat.to_owned(), out, root))
}

/// `aib-drive own done | progress | none` (S79-own o-2): one line to `<root>/drive/own.jsonl`
/// in one append write, after [`aib_drive::own_ledger::build_line`] checks it against the
/// list `out` just made. rc 0 written, 1 refused (why on stderr), 3 the ledger not writable.
fn own_write(
    kind: &str,
    seat: &str,
    args: &[String],
    out: &aib_drive::own::OwnOut,
    root: Option<&std::path::Path>,
) -> i32 {
    use aib_drive::own_ledger as l;
    use std::io::Write as _;
    let Some(dir) = root.map(|r| r.join("drive")) else {
        eprintln!("own {kind}: 狀態根讀不到,沒寫");
        return l::RC_WRITE;
    };
    let path = dir.join(l::LEDGER);
    let ledger = std::fs::read_to_string(&path).unwrap_or_default();
    let at_ms = i64::try_from(now_secs())
        .unwrap_or(i64::MAX)
        .saturating_mul(1000);
    let req = l::WriteReq {
        kind,
        seat,
        id: flag(args, "--id"),
        evidence: flag(args, "--evidence"),
        next: flag(args, "--next"),
        why: flag(args, "--why"),
        at_ms,
    };
    let read = |p: &str| std::fs::read(p).ok();
    let line = match l::build_line(&req, out, &ledger, &read) {
        Ok(v) => v.to_string(),
        Err(why) => {
            eprintln!("own {kind}: 沒寫 —— {why}");
            return l::RC_REFUSED;
        }
    };
    let wrote = std::fs::create_dir_all(&dir).and_then(|()| {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?
            .write_all(format!("{line}\n").as_bytes())
    });
    match wrote {
        Ok(()) => {
            println!("{line}");
            l::RC_OK
        }
        Err(e) => {
            eprintln!("own {kind}: 帳本寫不進 {}:{e}", path.display());
            l::RC_WRITE
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
    let (out, rc) = next_output(args);
    print!("{out}");
    rc
}

/// What `next` prints and its exit code (`next` and `act --auto` share it).
fn next_output(args: &[String]) -> (String, i32) {
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
                    let age = |q: &str| {
                        std::fs::metadata(q)
                            .and_then(|m| m.modified())
                            .ok()
                            .and_then(|t| t.elapsed().ok())
                            .map(|d| d.as_secs())
                    };
                    let part = partial_name(p);
                    match pick_cache(age(p), age(&part), *limit) {
                        Cache::Main | Cache::Missing => {}
                        Cache::Partial => {
                            return std::fs::read_to_string(&part)
                                .map_err(|e| format!("{part}: {e}"));
                        }
                        Cache::Stale(a) => {
                            return Err(format!("stale: {p} 已 {} 分鐘沒更新", a / 60));
                        }
                    }
                }
                std::fs::read_to_string(p).map_err(|e| format!("{p}: {e}"))
            }
        }
    };
    // a pr: item aib-slice's cache does not list (--fast: none closed over 72 h ago)
    let gh = GhRead::new();
    let more = aib_drive::next::GhMerged::new(&gh, &repo);
    aib_drive::next::cli_with(&args, read, Some(&more), now, &utc(now))
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
/// A fast scan (open PRs and 72 hours) takes about 3 minutes; one past this hangs on the
/// network (519d0 11-15 19:54: aibd's refresh logged 3 failures in 93 minutes, each a
/// scan held to the 30-minute limit), so it is killed sooner and tried again.
const FAST_TIMEOUT: Duration = Duration::from_secs(10 * 60);

/// How long one scan may run: [`FAST_TIMEOUT`], or [`REFRESH_TIMEOUT`] for `--full`.
fn scan_limit(full: bool) -> Duration {
    if full {
        REFRESH_TIMEOUT
    } else {
        FAST_TIMEOUT
    }
}
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
/// A scan that was not whole but wrote both files readable (a JSON object with `complete`)
/// is kept beside the caches as [`partial_name`] (519d0 11-15 20:28: next reads it when the
/// cache is stale, using only what its errors do not name); a whole scan removes them.
fn settle(drive: &std::path::Path, files: &[&str], ran_ok: bool) -> Result<bool, String> {
    let staged: Vec<std::path::PathBuf> = files
        .iter()
        .map(|f| drive.join(format!("{STAGE}{f}")))
        .collect();
    let read = |p: &std::path::Path| {
        std::fs::read(p)
            .ok()
            .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
            .and_then(|v| v.get("complete").and_then(serde_json::Value::as_bool))
    };
    if !(ran_ok && staged.iter().all(|p| read(p) == Some(true))) {
        let keep = staged.iter().all(|p| read(p).is_some());
        for (p, f) in staged.iter().zip(files) {
            if !(keep && std::fs::rename(p, drive.join(partial_name(f))).is_ok()) {
                let _ = std::fs::remove_file(p);
            }
        }
        return Ok(false);
    }
    for (p, f) in staged.iter().zip(files) {
        std::fs::rename(p, drive.join(f)).map_err(|e| format!("{}: {e}", p.display()))?;
        // a whole scan: an older partial one is never read again
        let _ = std::fs::remove_file(drive.join(partial_name(f)));
    }
    Ok(true)
}

/// Where [`settle`] keeps a scan that was not whole: `…/slice-due.json` ⇒
/// `…/slice-due.partial.json` (a file name or a whole path).
fn partial_name(file: &str) -> String {
    match file.strip_suffix(".json") {
        Some(stem) => format!("{stem}.partial.json"),
        None => format!("{file}.partial"),
    }
}

/// Which file a cache read takes ([`pick_cache`]).
#[derive(Debug, PartialEq, Eq)]
enum Cache {
    /// The cache, fresh.
    Main,
    /// The partial scan beside it: the cache is stale or missing and the partial one is
    /// fresh and newer (519d0 11-15 20:28; next reads it as a `partial` source).
    Partial,
    /// Stale this many seconds, and no fresh partial scan.
    Stale(u64),
    /// No cache and no fresh partial scan (the read then says the file is missing).
    Missing,
}

/// `main` / `partial`: the cache's and the partial scan's age in seconds (`None` = no file).
fn pick_cache(main: Option<u64>, partial: Option<u64>, limit: u64) -> Cache {
    if main.is_some_and(|a| a <= limit) {
        return Cache::Main;
    }
    if partial.is_some_and(|p| p <= limit && main.is_none_or(|m| p < m)) {
        return Cache::Partial;
    }
    main.map_or(Cache::Missing, Cache::Stale)
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
    refresh_run_why(repo, full, say_ok).rc
}

/// What one refresh did, for `drive/`[`AUTO_LOG`].
struct Ran {
    rc: i32,
    /// Why the caches were not replaced (empty when they were).
    why: String,
    /// Why tidy-scan.json was not written (empty when it was, or no clone to scan).
    tidy: String,
}

/// The scan's own stderr, rewritten each run (519d0 11-15 21:33 a: a scan killed at the limit
/// left only "timed out" in refresh.log, not what it was doing).
const SCAN_ERR: &str = "refresh-scan.err";
/// Lines of the scan's stderr kept in the reason.
const SCAN_ERR_LINES: usize = 3;

/// The last [`SCAN_ERR_LINES`] lines of `err` that are not blank, joined by ` | `.
fn stderr_tail(err: &std::path::Path) -> String {
    let text = std::fs::read_to_string(err).unwrap_or_default();
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    lines[lines.len().saturating_sub(SCAN_ERR_LINES)..].join(" | ")
}

/// Runs the scan like `run_fixed` (the same `<program>: exit <code>: …` and `timed out`
/// words), but its stderr goes to `err`, so a scan killed at `limit` still says what it was
/// doing last (519d0 11-15 21:33 a). A file, not a pipe: nothing the scan starts can hold
/// it open past the kill. On Unix the scan runs in its own process group and the whole group
/// is killed, so the git it starts does not live on (5d33a 1b07fc00 N3); on Windows
/// `taskkill /T` kills the tree.
#[allow(clippy::disallowed_methods)] // named exception (ADR 0005): the scan's one deadline, as aib_ghread's run_core
fn run_scan(
    program: &str,
    args: &[String],
    limit: Duration,
    err: &std::path::Path,
) -> Result<(), String> {
    use std::process::{Command, Stdio};
    use std::sync::mpsc::{self, TryRecvError};
    let file = std::fs::File::create(err).map_err(|e| format!("{}: {e}", err.display()))?;
    let mut cmd = Command::new(program);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(file);
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("{program} not run: {}", e.kind()))?;
    let pid = child.id().to_string();
    #[cfg(not(windows))]
    let group = format!("-{pid}");
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(child.wait());
    });
    match rx.recv_timeout(limit) {
        Ok(Ok(s)) if s.success() => Ok(()),
        Ok(Ok(s)) => {
            let code = s
                .code()
                .map_or_else(|| "signal".to_owned(), |c| c.to_string());
            Err(format!("{program}: exit {code}: {}", stderr_tail(err)))
        }
        Ok(Err(e)) => Err(format!("{program}: wait failed: {}", e.kind())),
        Err(_) => {
            // only a child the waiter has not reaped is killed (its pid is not reused yet)
            if matches!(rx.try_recv(), Err(TryRecvError::Empty)) {
                #[cfg(windows)]
                let kill = ("taskkill", vec!["/F", "/T", "/PID", pid.as_str()]);
                #[cfg(not(windows))]
                let kill = ("kill", vec!["-s", "KILL", "--", group.as_str()]);
                let _ = Command::new(kill.0)
                    .args(kill.1)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
            Err(format!(
                "{program}: timed out after {} ms; last: {}",
                limit.as_millis(),
                stderr_tail(err)
            ))
        }
    }
}

/// [`refresh_run`], with why it did not refresh for `drive/`[`AUTO_LOG`] (519d0 11-15 19:54:
/// aibd drops a refresh's output, so 17:39–19:12 left no reason).
fn refresh_run_why(repo: &std::path::Path, full: bool, say_ok: bool) -> Ran {
    let root = match state_root() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            return Ran {
                rc: 3,
                why: e,
                tidy: String::new(),
            };
        }
    };
    let drive = root.join("drive");
    if let Err(e) = std::fs::create_dir_all(&drive) {
        let e = format!("{}: {e}", drive.display());
        eprintln!("aib-drive refresh: {e}");
        return Ran {
            rc: 3,
            why: e,
            tidy: String::new(),
        };
    }
    let own = root.join("bin").join("aib-slice");
    let program = if own.is_file() {
        own.display().to_string()
    } else {
        "aib-slice".to_owned()
    };
    let pending = root.join("pending");
    let mut why = String::new();
    let rc = match run_scan(
        &program,
        &refresh_args(&drive, repo, &pending, full),
        scan_limit(full),
        &drive.join(SCAN_ERR),
    ) {
        Ok(()) => 0,
        Err(e) => {
            // aib-slice exits 2 when the scan was not complete
            eprintln!("aib-drive refresh: {e}");
            why = e.clone();
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
    let tidy = tidy_refresh(&root, &drive, repo, main);
    match settle(&drive, &refresh_files(full), rc == 0) {
        Ok(true) => {
            if say_ok {
                println!("refreshed {}", drive.display());
            }
            Ran {
                rc: 0,
                why: String::new(),
                tidy,
            }
        }
        Ok(false) => {
            eprintln!("aib-drive refresh: the scan was not whole; the old caches are kept");
            if why.is_empty() {
                why = "the scan was not whole; the old caches are kept".to_owned();
            }
            Ran {
                rc: if rc == 0 { 2 } else { rc },
                why,
                tidy,
            }
        }
        Err(e) => {
            eprintln!("aib-drive refresh: {e}");
            Ran {
                rc: 3,
                why: e,
                tidy,
            }
        }
    }
}

/// What `act --auto` writes for act to read (519d0 11-15).
const ACT_AUTO_FILES: [&str; 2] = ["act-next.json", "act-seats.json"];
/// The last `act --auto` a watch round ran, its output (overwritten each round).
const ACT_AUTO_LOG: &str = "act-auto.log";
/// The longest one `act --auto` from a watch round may run (next reads gh and aib-idea).
const ACT_AUTO_LIMIT: Duration = Duration::from_secs(180);

/// `aib-drive act --auto` (519d0 11-15: nobody ran act, and its `--next` / `--seats` /
/// `--dm` files had no producer): next and the seat facts are worked out now and written to
/// `drive/act-next.json` / `drive/act-seats.json`, act reads the live dm queue itself (no
/// `--dm`), then one `act --once --send`. act's switches decide what goes out: with every
/// switch off (the default) nothing is sent and the round is only journaled, the same as a
/// dry run. No `drive/` folder ⇒ nothing done, exit 0 (as `event`).
fn act_auto_main(args: &[String]) -> i32 {
    if args.iter().any(|a| a != "--auto") {
        eprintln!("aib-drive act --auto: takes no other argument");
        return 2;
    }
    let root = match state_root() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("aib-drive act --auto: {e}");
            return 5;
        }
    };
    let drive = root.join("drive");
    if !drive.is_dir() {
        println!("act --auto: no {} folder, nothing to do", drive.display());
        return 0;
    }
    // the seat facts are read once: written for act and handed to next as its --seats file
    // (5d33a 624d4c96 N1); next prints JSON for act to read (M1: without --json it printed
    // the text for people and every act --auto stopped at reading it)
    let repo = std::env::var("AIB_DRIVE_REPO").unwrap_or_else(|_| DEFAULT_REPO.to_owned());
    let mut src = aib_drive::seats::FsSource::new(root.clone());
    src.repo = Some(repo);
    let seats_out =
        aib_drive::seats::to_json(&aib_drive::seats::report(&src, now_secs()), "", &[]).to_string();
    let [np, sp] = ACT_AUTO_FILES.map(|f| drive.join(f));
    if let Err(e) = std::fs::write(&sp, &seats_out) {
        eprintln!("aib-drive act --auto: {}: {e}", sp.display());
        return 5;
    }
    // next's exit 3 (not complete) still prints the list; act reads `complete` itself
    let seats_file = sp.display().to_string();
    let next_args = ["--json", "--seats", seats_file.as_str()].map(str::to_owned);
    let (next_out, _) = next_output(&next_args);
    if let Err(e) = std::fs::write(&np, &next_out) {
        eprintln!("aib-drive act --auto: {}: {e}", np.display());
        return 5;
    }
    // v-2: unblock.json + blocking/<seat>.txt right after next (DESIGN-S79-unblock §3 1),
    // before act so its unblock_notify reads this round; a failure does not stop act
    if let Err(e) = aib_drive::unblock::run(&drive, aib_drive::act::now_ms(), true) {
        eprintln!("aib-drive act --auto: unblock: {e}; act goes on");
    }
    let act = [
        "--once".to_owned(),
        "--send".to_owned(),
        "--next".to_owned(),
        np.display().to_string(),
        "--seats".to_owned(),
        sp.display().to_string(),
    ];
    i32::from(aib_drive::act::cli_act(&act))
}

/// After each watch round: one `act --auto` as a child (its own lock and output), at most
/// [`ACT_AUTO_LIMIT`]; what it printed goes to `drive/act-auto.log`. A failure is one line on
/// stderr and the watch goes on (519d0 11-15: each round runs act; switches off = dry run).
fn act_after_round(drive: &std::path::Path) {
    let Ok(exe) = std::env::current_exe() else {
        eprintln!("aib-drive: act --auto: no path to this program");
        return;
    };
    let args = ["act".to_owned(), "--auto".to_owned()];
    let text = match run_fixed_any(&exe.display().to_string(), &args, ACT_AUTO_LIMIT) {
        Ok((code, out)) => {
            if code != Some(0) {
                eprintln!("aib-drive: act --auto exit {code:?}; the watch goes on");
            }
            format!(
                "{} exit {code:?}\n{}",
                utc(now_secs()),
                String::from_utf8_lossy(&out)
            )
        }
        Err(e) => {
            eprintln!("aib-drive: act --auto: {e}; the watch goes on");
            format!("{} {e}\n", utc(now_secs()))
        }
    };
    let _ = std::fs::write(drive.join(ACT_AUTO_LOG), text);
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

/// drive's own clone tidy-scan reads (`<drive>/clone`, 519d0 09:5x on 5d33a's C7b r3 L1):
/// the user's clone is never moved, and its working tree is often behind origin/main, which
/// made layer 7 stale every round.
const TIDY_CLONE: &str = "clone";

/// `git <args>` with a deadline; stdout trimmed. Never asks anyone (5d33a N2 on C7b r4: aibd's
/// timer runs refresh with nobody there): no credential prompt, and ssh in batch mode with
/// no agent or forwarding.
fn git(args: &[&str], secs: u64) -> Result<String, String> {
    let mut a: Vec<String> = [
        "-c",
        "credential.interactive=never",
        "-c",
        "core.askPass=",
        "-c",
        "core.sshCommand=ssh -o BatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes",
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    a.extend(args.iter().map(|s| (*s).to_owned()));
    run_fixed("git", &a, Duration::from_secs(secs))
        .map(|o| String::from_utf8_lossy(&o).trim().to_owned())
}

/// Makes [`TIDY_CLONE`] from `repo` (a local clone, then origin set to `repo`'s origin).
fn tidy_clone_make(
    drive: &std::path::Path,
    repo: &std::path::Path,
    clone: &std::path::Path,
) -> Result<(), String> {
    let r = repo.display().to_string();
    let url = git(&["-C", &r, "remote", "get-url", "origin"], 10)?;
    let staged = drive.join(format!("{STAGE}{TIDY_CLONE}"));
    let _ = std::fs::remove_dir_all(&staged);
    let s = staged.display().to_string();
    git(&["clone", "--quiet", "--no-checkout", &r, &s], 600)?;
    git(&["-C", &s, "remote", "set-url", "origin", &url], 10)?;
    std::fs::rename(&staged, clone).map_err(|e| format!("{s}: {e}"))
}

/// Removes drive's own clone folder so it can be made again, only when it is that folder
/// (519d0 10:22): a symbolic link is not removed nor followed, and the path, resolved, must
/// be `<drive>/`[`TIDY_CLONE`] itself. Anything else is an error (the round is skipped).
fn remove_own_clone(drive: &std::path::Path, clone: &std::path::Path) -> Result<(), String> {
    let c = clone.display().to_string();
    if std::fs::symlink_metadata(clone).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(format!("{c} 是連結,不刪、不跟進去"));
    }
    let real = std::fs::canonicalize(clone).map_err(|e| format!("{c}: {e}"))?;
    let home = std::fs::canonicalize(drive).map_err(|e| format!("{}: {e}", drive.display()))?;
    if real.parent() != Some(home.as_path())
        || real.file_name() != Some(std::ffi::OsStr::new(TIDY_CLONE))
    {
        return Err(format!("{c} 不是 <drive>/{TIDY_CLONE},不刪"));
    }
    std::fs::remove_dir_all(&real).map_err(|e| format!("{c}: {e}"))
}

/// Brings [`TIDY_CLONE`] to this round's main and returns it: made the first time from
/// `repo`, then each round `fetch origin` and a forced detached checkout of `main` — the
/// commit the same round's sweep saw (5d33a N1 on C7b r4: a second fetch could land one
/// push later and make the round stale) — or `origin/main` when the sweep gave none. A clone
/// git cannot read, or whose checkout fails, is ours to remake once (5d33a N3); a fetch that
/// fails is the network and is only an error. `repo` itself is only read.
fn tidy_clone(
    drive: &std::path::Path,
    repo: &std::path::Path,
    main: Option<&str>,
) -> Result<std::path::PathBuf, String> {
    let clone = drive.join(TIDY_CLONE);
    let c = clone.display().to_string();
    let to = main.unwrap_or("origin/main");
    // a link is never gone into: git would fetch and check out wherever it points (519d0 10:22)
    if std::fs::symlink_metadata(&clone).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(format!("{c} 是連結,不在裡面跑 git"));
    }
    // ours only when it has its own `.git` and git's top level is the clone itself: without
    // one git walks up and would fetch and force a checkout in whatever repository holds the
    // state root, losing its uncommitted work (5d33a L1 on 1e7270ca)
    let readable = || {
        clone.join(".git").exists()
            && git(&["-C", &c, "rev-parse", "--show-toplevel"], 10)
                .ok()
                .and_then(|t| std::fs::canonicalize(t).ok())
                == std::fs::canonicalize(&clone).ok()
    };
    if clone.exists() && !readable() {
        remove_own_clone(drive, &clone)?;
    }
    if !clone.exists() {
        tidy_clone_make(drive, repo, &clone)?;
    }
    let fetch = || git(&["-C", &c, "fetch", "--quiet", "--prune", "origin"], 180);
    fetch()?;
    let checkout = |to: &str| {
        git(
            &["-C", &c, "checkout", "--quiet", "--force", "--detach", to],
            120,
        )
    };
    // the sweep's sha not here (a force push): origin/main, stale this round but not remade
    // every round (5d33a N2 on 1e7270ca); only a clone that cannot check out either is remade
    let at = checkout(to).is_ok() || (to != "origin/main" && checkout("origin/main").is_ok());
    if !at {
        remove_own_clone(drive, &clone)?;
        tidy_clone_make(drive, repo, &clone)?;
        fetch()?;
        checkout(to)?;
    }
    Ok(clone)
}

/// The clone could not be brought to origin/main: the kept report's `head` is cleared, so
/// layer 7 reads it as stale this round (skipped, 519d0 09:5x) instead of trusting it for up
/// to an hour. No report ⇒ nothing to do.
fn tidy_unset_head(drive: &std::path::Path) -> Result<(), String> {
    let file = drive.join(TIDY_FILE);
    let Some(mut report) = std::fs::read(&file)
        .ok()
        .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
    else {
        return Ok(());
    };
    report["head"] = serde_json::Value::Null;
    let staged = drive.join(format!("{STAGE}{TIDY_FILE}"));
    std::fs::write(&staged, format!("{report}\n"))
        .and_then(|()| std::fs::rename(&staged, &file))
        .map_err(|e| format!("{}: {e}", staged.display()))
}

/// tidy-scan's program: `<root>/bin/aib-xtask` first, then each `PATH` folder in order.
/// None found is said with where it looked (a9136 11-16 01:41: observe's refresh logged
/// only "entity not found" for hours and tidy-scan.json stayed old).
fn xtask_program(
    root: &std::path::Path,
    path: Option<&std::ffi::OsStr>,
) -> Result<std::path::PathBuf, String> {
    aib_drive::progpath::find("aib-xtask", &[root.join("bin")], path)
}

/// The `tidy` field for a tidy-scan that did not land: the failure, tidy-scan's own last
/// stderr line when it gave one, and that the old file stays.
fn tidy_why(e: &str, last: &str) -> String {
    if last.is_empty() {
        format!("{e}; the old {TIDY_FILE} is kept")
    } else {
        format!("{e}: {last}; the old {TIDY_FILE} is kept")
    }
}

/// One tidy-scan for the round, on [`TIDY_CLONE`]; a failure is said on stderr and never
/// changes the refresh's exit code (519d0 11-15 08:00). It is returned (empty = it went
/// well) for the `tidy` field of `drive/`[`AUTO_LOG`] (5d33a a704784a L1: tidy-scan.json was
/// never written and nothing said why).
fn tidy_refresh(
    root: &std::path::Path,
    drive: &std::path::Path,
    repo: &std::path::Path,
    main: Option<String>,
) -> String {
    let program = match xtask_program(root, std::env::var_os("PATH").as_deref()) {
        Ok(p) => p.display().to_string(),
        Err(e) => {
            let why = format!("{e}; the old {TIDY_FILE} is kept");
            eprintln!("aib-drive refresh: {why}");
            return why;
        }
    };
    let clone = match tidy_clone(drive, repo, main.as_deref()) {
        Ok(c) => c,
        Err(e) => {
            let why = format!("tidy clone: {e}; {TIDY_FILE} is marked stale");
            eprintln!("aib-drive refresh: {why}");
            if let Err(e) = tidy_unset_head(drive) {
                eprintln!("aib-drive refresh: {e}");
            }
            return why;
        }
    };
    // the commit tidy-scan reads; layer 7 compares it with the main this round wrote
    let head = git(
        &["-C", &clone.display().to_string(), "rev-parse", "HEAD"],
        10,
    )
    .ok()
    .filter(|h| !h.is_empty());
    let ran = aib_ghread::run_fixed_any_why(&program, &tidy_args(drive, &clone), TIDY_TIMEOUT);
    // a9136 11-16 04:45: "tidy-scan exit 2" alone hid that the installed aib-xtask had no
    // tidy-scan; its own last stderr line goes with the exit
    let last = ran.as_ref().map(|r| r.2.clone()).unwrap_or_default();
    let ran = ran.map(|(code, out, _)| (code, out));
    match tidy_settle(drive, ran, now_secs().saturating_mul(1000), head, main) {
        Ok(()) => String::new(),
        Err(e) => {
            let why = tidy_why(&e, &last);
            eprintln!("aib-drive refresh: {why}");
            why
        }
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
    let start = now_secs();
    let mut tidy = String::new();
    let (rc, what, why) = auto_run(&drive, args, start, &mut tidy);
    eprintln!("aib-drive refresh --auto: {why}");
    // no drive folder (no clone named, nothing ever run): nothing to keep a log in
    if drive.is_dir() {
        auto_log(&drive, start, now_secs(), rc, what, &why, &tidy);
    }
    rc
}

/// One `refresh --auto` decision: (exit code, outcome word for [`AUTO_LOG`], what to say);
/// `tidy` gets why tidy-scan.json was not written, when a scan ran.
fn auto_run(
    drive: &std::path::Path,
    args: &[String],
    now: u64,
    tidy: &mut String,
) -> (i32, &'static str, String) {
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
            return (
                2,
                "usage",
                format!("{e} (or the one line of drive/{AUTO_REPO_FILE})"),
            );
        }
    };
    if full {
        return (2, "usage", "--full is never automatic".to_owned());
    }
    let age = std::fs::metadata(drive.join(refresh_files(false)[0]))
        .and_then(|m| m.modified())
        .ok();
    let age = age_of(age, std::time::SystemTime::now());
    let tried = std::fs::read_to_string(drive.join(AUTO_TRIED))
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok());
    match auto_due(age, tried, now) {
        Auto::Fresh => {
            return (0, "fresh", "the caches are fresh".to_owned());
        }
        Auto::TriedLately => {
            return (
                0,
                "tried-lately",
                "tried within 20 minutes; next time".to_owned(),
            );
        }
        Auto::Run => {}
    }
    if let Err(e) = std::fs::create_dir_all(drive) {
        return (3, "error", format!("{}: {e}", drive.display()));
    }
    let lock = drive.join(AUTO_LOCK);
    let mine = format!("{} {now}", std::process::id());
    match auto_lock(&lock, &mine, now) {
        Ok(true) => {}
        Ok(false) => {
            let who = std::fs::read_to_string(&lock).unwrap_or_default();
            return (
                0,
                "held",
                format!("another refresh holds {} ({})", lock.display(), who.trim()),
            );
        }
        Err(e) => return (3, "error", e),
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
        return (if unreadable { 3 } else { 0 }, "quota", e);
    }
    let _ = std::fs::write(drive.join(AUTO_TRIED), now.to_string());
    let ran = refresh_run_why(&repo, false, false);
    auto_unlock(&lock, &mine);
    *tidy = ran.tidy;
    if ran.rc == 0 {
        (0, "refreshed", "refreshed".to_owned())
    } else {
        (ran.rc, "scan-failed", ran.why)
    }
}

/// One line per `refresh --auto` (aibd drops its output): `drive/refresh.log`, JSON lines.
const AUTO_LOG: &str = "refresh.log";
/// Past this the log is moved to `refresh.log.1` (one older file kept), so a line every
/// 5 minutes never grows without end.
const AUTO_LOG_MAX: u64 = 1024 * 1024;

/// Appends one line to `<drive>/`[`AUTO_LOG`]: when it started, how long it took, the exit
/// code, the outcome and why, and why tidy-scan.json was not written (`tidy`, empty when it
/// was or no scan ran). A log that cannot be written is said and the run is not failed for
/// it.
fn auto_log(
    drive: &std::path::Path,
    start: u64,
    end: u64,
    rc: i32,
    what: &str,
    why: &str,
    tidy: &str,
) {
    use std::io::Write as _;
    let path = drive.join(AUTO_LOG);
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > AUTO_LOG_MAX) {
        let _ = std::fs::rename(&path, drive.join(format!("{AUTO_LOG}.1")));
    }
    let line = serde_json::json!({
        "at": utc(start), "secs": end.saturating_sub(start), "rc": rc, "what": what,
        "why": observe::clean(why, 400), "tidy": observe::clean(tidy, 400),
    });
    let w = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut f| writeln!(f, "{line}"));
    if let Err(e) = w {
        eprintln!("aib-drive refresh --auto: {}: {e}", path.display());
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
    // F9 p05: the clone also proves RULES §20 carried heads for the review gate
    let carry = clone.clone().map(|clone| GitCarry { clone });
    let mut refreshed: Option<u64> = None;
    let mut round = |prev: &BTreeMap<u64, Sha>| -> Built {
        // 519d0 T7: the caches next reads are refreshed at a round's start; a failed
        // refresh is said and the round goes on
        if let Some(c) = clone.as_deref().filter(|_| a.watch) {
            let now = now_secs();
            if refresh_due(refreshed, now) {
                refreshed = Some(now);
                // the same path as aibd's `refresh --auto` (519d0 11-15 19:54: two refreshers):
                // a fresh cache, a recent try or a scan already running is not a second scan
                match auto_main(&["--repo".to_owned(), c.display().to_string()]) {
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
                carry: carry.as_ref().map(|c| c as &dyn aib_drive::snapshot::Carry),
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
        let mut clock = RealClock::new().with_dm_done(&root);
        let drive = root.join("drive");
        let mut print = |r: &Record| {
            show(r);
            act_after_round(&drive);
        };
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

    /// 519d0 11-15 19:54: a fast scan that hangs on the network is killed at 10 minutes, not
    /// held to the full scan's 30
    #[test]
    fn a_fast_scan_is_killed_sooner_than_a_full_one() {
        assert_eq!(
            super::scan_limit(false),
            std::time::Duration::from_secs(600)
        );
        assert_eq!(
            super::scan_limit(true),
            std::time::Duration::from_secs(1800)
        );
    }

    /// 5d33a 1b07fc00 N3: a scan killed at the limit takes what it started with it (aib-slice's
    /// git), not only itself.
    #[cfg(unix)]
    #[test]
    fn a_killed_scan_takes_what_it_started_with_it() {
        let d = std::env::temp_dir().join(format!("aib-drive-scangrp-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let err = d.join(super::SCAN_ERR);
        let pid_file = d.join("grandchild.pid");
        let script = format!("sleep 30 & echo $! > '{}'; wait", pid_file.display());
        let e = super::run_scan(
            "/bin/sh",
            &["-c".to_owned(), script],
            std::time::Duration::from_secs(1),
            &err,
        )
        .unwrap_err();
        assert!(e.contains("timed out after 1000 ms"), "{e}");
        let pid = std::fs::read_to_string(&pid_file)
            .unwrap()
            .trim()
            .to_owned();
        // gone or a zombie waiting for launchd / init; asked again a few times, no sleep
        let alive = || {
            let o = std::process::Command::new("ps")
                .args(["-o", "stat=", "-p", &pid])
                .output()
                .unwrap();
            let st = String::from_utf8_lossy(&o.stdout).trim().to_owned();
            !st.is_empty() && !st.starts_with('Z')
        };
        let gone = (0..50).any(|_| !alive());
        let _ = std::fs::remove_dir_all(&d);
        assert!(gone, "the scan's child {pid} lived on after the kill");
    }

    /// 519d0 11-15 21:33 a: a scan killed at the limit keeps the last lines it wrote to
    /// stderr in the reason; a scan that exits non-zero keeps them after its exit code.
    #[cfg(unix)]
    #[test]
    fn a_killed_scan_keeps_its_last_stderr_lines() {
        let d = std::env::temp_dir().join(format!("aib-drive-scanerr-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let err = d.join(super::SCAN_ERR);
        let sh = |script: &str| vec!["-c".to_owned(), script.to_owned()];
        let hang = sh("for s in one two three four; do echo \"step $s\" >&2; done; exec sleep 30");
        let started = std::time::Instant::now();
        let e =
            super::run_scan("/bin/sh", &hang, std::time::Duration::from_secs(1), &err).unwrap_err();
        assert!(
            started.elapsed() < std::time::Duration::from_secs(20),
            "{e}"
        );
        assert!(e.contains("timed out after 1000 ms"), "{e}");
        assert!(
            e.ends_with("last: step two | step three | step four"),
            "{e}"
        );
        let two = sh("echo 'partial: PR #9 not readable' >&2; exit 2");
        let e =
            super::run_scan("/bin/sh", &two, std::time::Duration::from_secs(20), &err).unwrap_err();
        assert_eq!(e, "/bin/sh: exit 2: partial: PR #9 not readable");
        assert!(super::run_scan(
            "/bin/sh",
            &sh("true"),
            std::time::Duration::from_secs(20),
            &err
        )
        .is_ok());
        let _ = std::fs::remove_dir_all(&d);
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
        // 519d0 11-15 20:28: both readable ⇒ kept beside the caches as the partial pair
        assert!(d.join(super::partial_name(files[0])).is_file());
        assert!(d.join(super::partial_name(files[1])).is_file());
        assert_eq!(
            super::partial_name("slice-due.json"),
            "slice-due.partial.json"
        );
        // exit 0 but one file says not complete: kept as well
        put(&stage(files[0]), true);
        put(&stage(files[1]), false);
        assert_eq!(super::settle(&d, &files, true), Ok(false));
        assert_eq!(files.iter().map(|f| read(f)).collect::<Vec<_>>(), old);
        // whole: both replaced, and the partial pair removed
        put(&stage(files[0]), true);
        put(&stage(files[1]), true);
        assert_eq!(super::settle(&d, &files, true), Ok(true));
        assert!(read(files[0]).contains(".new-"), "{}", read(files[0]));
        assert!(read(files[1]).contains(".new-"));
        assert!(!d.join(super::partial_name(files[0])).exists());
        assert!(!d.join(super::partial_name(files[1])).exists());
        // a staged file that cannot be read is not kept as partial
        std::fs::write(d.join(stage(files[0])), "{not json").unwrap();
        put(&stage(files[1]), false);
        assert_eq!(super::settle(&d, &files, false), Ok(false));
        assert!(!d.join(super::partial_name(files[0])).exists());
        assert!(!d.join(super::partial_name(files[1])).exists());
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 519d0 11-15 20:28: a stale or missing cache gives way to a fresh partial scan that
    /// is newer; a fresh cache is always taken, a stale partial one never.
    #[test]
    fn a_stale_cache_gives_way_to_a_fresh_newer_partial_scan() {
        use super::{pick_cache, Cache};
        assert_eq!(pick_cache(Some(60), Some(10), 1800), Cache::Main);
        assert_eq!(pick_cache(Some(4000), Some(100), 1800), Cache::Partial);
        assert_eq!(pick_cache(None, Some(100), 1800), Cache::Partial);
        assert_eq!(pick_cache(Some(4000), Some(2000), 1800), Cache::Stale(4000));
        assert_eq!(pick_cache(Some(4000), None, 1800), Cache::Stale(4000));
        assert_eq!(pick_cache(None, None, 1800), Cache::Missing);
    }

    /// 519d0 09:5x (5d33a C7b r3 L1): tidy-scan reads drive's own clone, brought to
    /// origin/main every round; the user's clone (behind origin/main here) is not moved.
    #[test]
    fn tidy_scan_reads_its_own_clone_at_origin_main() {
        let d = std::env::temp_dir().join(format!("aib-drive-tclone-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let p = |n: &str| d.join(n).display().to_string();
        let g = |a: &[&str]| super::git(a, 60).unwrap();
        let commit = |dir: &str, msg: &str| {
            std::fs::write(format!("{dir}/f.txt"), msg).unwrap();
            g(&["-C", dir, "add", "f.txt"]);
            g(&[
                "-C",
                dir,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "--quiet",
                "-m",
                msg,
            ]);
            g(&["-C", dir, "rev-parse", "HEAD"])
        };
        let (origin, user, drive) = (p("origin"), p("user"), d.join("drive"));
        std::fs::create_dir_all(&drive).unwrap();
        g(&["init", "--quiet", "-b", "main", &origin]);
        let first = commit(&origin, "one");
        g(&["clone", "--quiet", &origin, &user]);
        let second = commit(&origin, "two");
        // the user's clone is still at the first commit; the drive clone goes to the second
        let u = std::path::Path::new(&user);
        let clone = super::tidy_clone(&drive, u, None).unwrap();
        let head = |dir: &str| g(&["-C", dir, "rev-parse", "HEAD"]);
        let c = clone.display().to_string();
        assert_eq!(head(&c), second);
        assert_eq!(head(&user), first, "the user's clone is only read");
        // a push after the sweep: the clone goes to the main the sweep saw (5d33a N1)
        let third = commit(&origin, "three");
        super::tidy_clone(&drive, u, Some(second.as_str())).unwrap();
        assert_eq!(head(&c), second, "the sweep's main, not the later push");
        // no main from the sweep: origin/main
        super::tidy_clone(&drive, u, None).unwrap();
        assert_eq!(head(&c), third);
        // a clone git cannot read is remade (5d33a N3)
        std::fs::remove_dir_all(clone.join(".git")).unwrap();
        std::fs::write(clone.join(".git"), "gitdir: /nonexistent-aib-drive\n").unwrap();
        super::tidy_clone(&drive, u, None).unwrap();
        assert_eq!(head(&c), third, "remade and brought up");
        // the sweep's sha not in the clone: origin/main, and the clone is not remade (N2)
        std::fs::write(clone.join(".git").join("aib-mark"), "x").unwrap();
        super::tidy_clone(&drive, u, Some("0123456789012345678901234567890123456789")).unwrap();
        assert_eq!(head(&c), third);
        assert!(clone.join(".git").join("aib-mark").exists(), "not remade");
        // a clone folder with no .git of its own inside another work tree: git must not walk
        // up into that repository (5d33a L1): the user's tree keeps its HEAD and its change
        let inner = d.join("user").join("drive");
        std::fs::create_dir_all(inner.join(super::TIDY_CLONE)).unwrap();
        std::fs::write(format!("{user}/f.txt"), "uncommitted").unwrap();
        super::tidy_clone(&inner, u, None).unwrap();
        assert_eq!(
            head(&user),
            first,
            "the user's repository was not checked out"
        );
        assert_eq!(
            std::fs::read_to_string(format!("{user}/f.txt")).unwrap(),
            "uncommitted",
            "its uncommitted change is kept"
        );
        assert_eq!(
            head(&inner.join(super::TIDY_CLONE).display().to_string()),
            third
        );
        // 519d0 10:22: only <drive>/clone itself is removed, never another path
        assert!(super::remove_own_clone(&drive, std::path::Path::new(&user)).is_err());
        assert!(
            std::path::Path::new(&user).join(".git").exists(),
            "the user's clone stays"
        );
        // a folder right under <drive> but not named clone is not ours either (5d33a N1 on r3)
        let beside = drive.join("other");
        std::fs::create_dir_all(&beside).unwrap();
        std::fs::write(beside.join("keep.txt"), "kept").unwrap();
        assert!(super::remove_own_clone(&drive, &beside).is_err());
        assert!(beside.join("keep.txt").exists(), "<drive>/other stays");
        // and a clone that is a link is neither removed nor gone into
        #[cfg(unix)]
        {
            let other = p("other");
            g(&["clone", "--quiet", &origin, &other]);
            g(&["-C", &other, "checkout", "--quiet", "--detach", &first]);
            std::fs::write(format!("{other}/f.txt"), "theirs").unwrap();
            let linked = d.join("linked");
            std::fs::create_dir_all(&linked).unwrap();
            std::os::unix::fs::symlink(&other, linked.join(super::TIDY_CLONE)).unwrap();
            assert!(super::tidy_clone(&linked, u, None).is_err());
            assert!(super::remove_own_clone(&linked, &linked.join(super::TIDY_CLONE)).is_err());
            assert_eq!(
                head(&other),
                first,
                "a linked repository is not checked out"
            );
            assert_eq!(
                std::fs::read_to_string(format!("{other}/f.txt")).unwrap(),
                "theirs"
            );
        }
        // not brought up (origin gone): the kept report's head is cleared ⇒ stale
        std::fs::write(
            drive.join(super::TIDY_FILE),
            "{\"head\":\"x\",\"main\":\"x\"}",
        )
        .unwrap();
        std::fs::remove_dir_all(&origin).unwrap();
        assert!(super::tidy_clone(&drive, u, None).is_err());
        super::tidy_unset_head(&drive).unwrap();
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(drive.join(super::TIDY_FILE)).unwrap())
                .unwrap();
        assert!(v["head"].is_null() && v["main"] == "x", "{v}");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn aib_xtask_is_looked_for_in_the_state_bin_then_on_path_and_a_miss_says_where() {
        let d = std::env::temp_dir().join(format!("aib-drive-xtask-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let (root, a, b) = (d.join("root"), d.join("a"), d.join("b"));
        for dir in [root.join("bin"), a.clone(), b.clone()] {
            std::fs::create_dir_all(dir).unwrap();
        }
        let name = if cfg!(windows) {
            "aib-xtask.exe"
        } else {
            "aib-xtask"
        };
        let path = std::env::join_paths([&a, &b]).unwrap();
        // none anywhere: the miss names the state bin and how many PATH folders it read
        let miss = super::xtask_program(&root, Some(&path)).unwrap_err();
        assert!(
            miss.contains(&root.join("bin").join(name).display().to_string()),
            "{miss}"
        );
        assert!(miss.contains("2 PATH folders"), "{miss}");
        // on PATH only: the first folder that has it, in PATH order
        std::fs::write(b.join(name), "").unwrap();
        assert_eq!(super::xtask_program(&root, Some(&path)), Ok(b.join(name)));
        std::fs::write(a.join(name), "").unwrap();
        assert_eq!(super::xtask_program(&root, Some(&path)), Ok(a.join(name)));
        // the state bin's own copy comes before PATH
        std::fs::write(root.join("bin").join(name), "").unwrap();
        assert_eq!(
            super::xtask_program(&root, Some(&path)),
            Ok(root.join("bin").join(name))
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// a9136 11-16 04:45: an aib-xtask with no tidy-scan exits 2 with its usage on stderr;
    /// the tidy field carries that last line, not only "exit 2".
    #[cfg(unix)]
    #[test]
    fn a_failed_tidy_scan_keeps_its_last_stderr_line() {
        let args = |s: &str| vec!["-c".to_owned(), s.to_owned()];
        let t = std::time::Duration::from_secs(20);
        let (code, out, last) = aib_ghread::run_fixed_any_why(
            "sh",
            &args("echo first >&2; echo 'usage: aib-xtask purity' >&2; exit 2"),
            t,
        )
        .unwrap();
        assert_eq!((code, out.is_empty()), (Some(2), true));
        assert_eq!(last, "usage: aib-xtask purity");
        // a clean exit reads no stderr
        let (code, _, last) =
            aib_ghread::run_fixed_any_why("sh", &args("echo note >&2; echo '{}'"), t).unwrap();
        assert_eq!((code, last.as_str()), (Some(0), ""));
        let why = super::tidy_why("tidy-scan exit 2", "usage: aib-xtask purity");
        assert!(
            why.starts_with("tidy-scan exit 2: usage: aib-xtask purity; "),
            "{why}"
        );
        assert!(super::tidy_why("tidy-scan exit 2", "").starts_with("tidy-scan exit 2; "));
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
