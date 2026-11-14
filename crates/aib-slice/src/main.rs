// SPDX-License-Identifier: Apache-2.0
//! aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--fast] [--out FILE] [--due-out FILE [--max N]] [--gate] [--hours N]
//!   [--gh PROGRAM | --no-gh]
//! aib-slice due (--seat 5碼 | --all) [--max N] [--json] [--repo DIR] [--pending DIR]
//!   [--no-fetch] [--fast] [--hours N] [--gh PROGRAM | --no-gh]
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
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "usage: aib-slice sweep [--repo DIR] [--pending DIR] [--seat SEAT] [--no-fetch] [--fast] [--out FILE] [--due-out FILE [--max N]] [--gate] [--hours N] [--gh PROGRAM | --no-gh]\n       aib-slice due (--seat SEAT | --all) [--max N] [--json] [--repo DIR] [--pending DIR] [--no-fetch] [--fast] [--hours N] [--gh PROGRAM | --no-gh]\n       aib-slice gc --seat SEAT (--dry-run | --apply) [--root DIR] [--caches] [--json] [--machine NAME]";

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
    /// sweep: write the JSON here (whole file or nothing) instead of printing it
    out: Option<PathBuf>,
    /// sweep: also write the due lists of the same run here (`aib-slice-due.v1`)
    due_out: Option<PathBuf>,
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

/// Writes `text` to `path` through a file beside it and a rename: a reader of the cache sees
/// the old file or the new one, never half of one.
fn write_whole(path: &Path, text: &str) -> Result<(), String> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(format!(".tmp{}", std::process::id()));
    let tmp = PathBuf::from(tmp);
    std::fs::write(&tmp, text)
        .and_then(|()| std::fs::rename(&tmp, path))
        .map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            format!("{}: {}", path.display(), e.kind())
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
        fast: false,
    };
    let mut f = Flags {
        gate: false,
        all: false,
        json: false,
        max: 8,
        out: None,
        due_out: None,
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
            "--fast" => o.fast = true,
            "--gate" if !is_due => f.gate = true,
            "--all" if is_due => f.all = true,
            "--json" if is_due => f.json = true,
            "--out" if !is_due => f.out = Some(PathBuf::from(val()?)),
            "--due-out" if !is_due => f.due_out = Some(PathBuf::from(val()?)),
            "--max" if is_due || f.due_out.is_some() => {
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
    // one run, both files: whoever keeps a cache does not sweep twice (S79, 519d0 11-15)
    if let Some(p) = &f.due_out {
        let d = due::due(&r, o.hours, o.now);
        let text = format!("{}\n", due::to_json(&d, &r, o.seat.as_deref(), f.max));
        if let Err(e) = write_whole(p, &text) {
            eprintln!("aib-slice: {e}");
            return Ok(2);
        }
    }
    let json = to_json(&r, o.hours);
    match &f.out {
        Some(p) => {
            if let Err(e) = write_whole(p, &format!("{json}\n")) {
                eprintln!("aib-slice: {e}");
                return Ok(2);
            }
        }
        None => println!("{json}"),
    }
    if !r.errors.is_empty() {
        return Ok(2);
    }
    if f.gate && r.branches.iter().any(|b| blocks(classify(b, o.hours).0)) {
        return Ok(3);
    }
    Ok(0)
}
