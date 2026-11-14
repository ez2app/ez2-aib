// SPDX-License-Identifier: Apache-2.0
//! aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--gate] [--hours N]
//!   [--gh PROGRAM | --no-gh]
//! aib-slice due (--seat 5碼 | --all) [--max N] [--json] [--repo DIR] [--pending DIR]
//!   [--no-fetch] [--hours N] [--gh PROGRAM | --no-gh]
//!
//! `sweep` prints one `aib-slice-sweep.v1` JSON object (schema/sweep.v1.json). Exit: 0 swept;
//! 2 the sweep is not complete (something could not be read; JSON still printed,
//! `complete=false`); 3 only with `--gate`, when a branch is in class 1, 2 or 3; 64 usage.
//!
//! `due` (S73l) prints 應合未合 / 應修未修 from the same sweep, as text or as
//! `aib-slice-due.v1` JSON (IFACE-s73l). Exit: 0 swept; 2 not complete (both lists are still
//! printed, under a first line that says so); 64 usage. It writes nothing.
//!
//! aib-slice gc --seat 5碼 (--dry-run | --apply) [--root DIR] [--caches] [--json] [--machine NAME]
//! `gc` (S82) removes that seat's own work trees under one folder (default ~/wt.noindex) once
//! their head is in main, and with `--caches` the tagged build caches of the trees that stay
//! (rules: gc.rs). Exit: 0 done; 2 a removal failed or a tree could not be read (kept); 64
//! usage. Each removal is one line in `<root>/gc-<seat>.log`.

use aib_slice::{blocks, classify, due, gc, scan, to_json};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: aib-slice sweep [--repo DIR] [--pending DIR] [--seat SEAT] [--no-fetch] [--gate] [--hours N] [--gh PROGRAM | --no-gh]\n       aib-slice due (--seat SEAT | --all) [--max N] [--json] [--repo DIR] [--pending DIR] [--no-fetch] [--hours N] [--gh PROGRAM | --no-gh]\n       aib-slice gc --seat SEAT (--dry-run | --apply) [--root DIR] [--caches] [--json] [--machine NAME]";

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(rc) => ExitCode::from(rc),
        Err(e) => {
            eprintln!("aib-slice: {e}\n{USAGE}");
            ExitCode::from(64)
        }
    }
}

/// What the flags said, besides the sweep's own options.
struct Flags {
    gate: bool,
    all: bool,
    json: bool,
    max: usize,
}

/// `aib-slice gc`: rc 0 done; 2 a removal failed or a tree could not be read (it was kept).
fn run_gc(args: &[String]) -> Result<u8, String> {
    let mut it = args.iter();
    let (mut seat, mut root) = (None::<String>, home().join("wt.noindex"));
    let (mut dry, mut apply, mut caches, mut json) = (false, false, false, false);
    let mut machine = std::env::var("HOSTNAME")
        .or_else(|_| std::env::var("COMPUTERNAME"))
        .unwrap_or_else(|_| "?".into());
    while let Some(a) = it.next() {
        let mut val = || {
            it.next()
                .filter(|v| !v.starts_with("--"))
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--seat" => {
                let s = val()?;
                if s.len() != 5 || !s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')) {
                    return Err(format!("--seat {s:?}: 5 lowercase hex"));
                }
                seat = Some(s);
            }
            "--root" => root = PathBuf::from(val()?),
            "--machine" => machine = val()?,
            "--dry-run" => dry = true,
            "--apply" => apply = true,
            "--caches" => caches = true,
            "--json" => json = true,
            _ => return Err(format!("unknown argument {a}")),
        }
    }
    let seat = seat.ok_or("gc needs --seat (only that seat's own folders are touched)")?;
    // removing is never the default: say which
    if dry == apply {
        return Err("gc needs exactly one of --dry-run and --apply".into());
    }
    let now = scan::now();
    let mut rows = gc::plan(&root, &seat, caches, now)?;
    if apply {
        gc::apply(&root, &seat, &machine, &mut rows, now);
    }
    if json {
        println!("{}", gc::to_json(&rows, &seat, dry));
    } else {
        print!("{}", gc::render(&rows, dry));
    }
    let unread =
        |r: &gc::Row| matches!(&r.verdict, gc::Verdict::Keep(w) if w == "could not be read");
    Ok(if rows.iter().any(|r| r.failed.is_some() || unread(r)) {
        2
    } else {
        0
    })
}

fn run(args: &[String]) -> Result<u8, String> {
    let mut it = args.iter();
    let cmd = it.next().map(String::as_str);
    if cmd == Some("gc") {
        return run_gc(&args[1..]);
    }
    if !matches!(cmd, Some("sweep" | "due")) {
        return Err("the commands are sweep, due and gc".into());
    }
    let is_due = cmd == Some("due");
    let mut o = scan::Opts {
        repo: PathBuf::from("."),
        pending: home().join(".ez2-aib").join("pending"),
        seat: None,
        fetch: true,
        gh: Some("gh".into()),
        now: scan::now(),
        hours: 72,
    };
    let mut f = Flags {
        gate: false,
        all: false,
        json: false,
        max: 8,
    };
    while let Some(a) = it.next() {
        let mut val = || {
            it.next()
                .filter(|v| !v.starts_with("--"))
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--repo" => o.repo = PathBuf::from(val()?),
            "--pending" => o.pending = PathBuf::from(val()?),
            "--seat" => {
                let s = val()?;
                if s.len() != 5 || !s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')) {
                    return Err(format!("--seat {s:?}: 5 lowercase hex"));
                }
                o.seat = Some(s);
            }
            "--hours" => {
                o.hours = val()?
                    .parse()
                    .ok()
                    .filter(|h| *h > 0)
                    .ok_or("--hours: a positive number")?;
            }
            "--gh" => o.gh = Some(val()?),
            "--no-gh" => o.gh = None,
            "--no-fetch" => o.fetch = false,
            "--gate" if !is_due => f.gate = true,
            "--all" if is_due => f.all = true,
            "--json" if is_due => f.json = true,
            "--max" if is_due => {
                f.max = val()?
                    .parse()
                    .ok()
                    .filter(|n| (1..=50).contains(n))
                    .ok_or("--max: 1 to 50")?;
            }
            _ => return Err(format!("unknown argument {a}")),
        }
    }
    // a reply's tail must not print the whole fleet by accident: say which
    if is_due && f.all == o.seat.is_some() {
        return Err("due needs exactly one of --seat and --all".into());
    }
    let r = scan::sweep(&o);
    if is_due {
        let d = due::due(&r, o.hours, o.now);
        if f.json {
            println!("{}", due::to_json(&d, &r, o.seat.as_deref(), f.max));
        } else {
            print!("{}", due::render(&d, &r.errors, f.max));
        }
        return Ok(if r.errors.is_empty() { 0 } else { 2 });
    }
    println!("{}", to_json(&r, o.hours));
    if !r.errors.is_empty() {
        return Ok(2);
    }
    if f.gate && r.branches.iter().any(|b| blocks(classify(b, o.hours).0)) {
        return Ok(3);
    }
    Ok(0)
}
