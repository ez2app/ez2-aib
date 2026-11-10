// SPDX-License-Identifier: Apache-2.0
//! aib-flaky — read-only suspected-flaky register.
//!
//!   aib-flaky judge --repo OWNER/NAME --pr N [--register FILE] [--offline DIR]
//!   aib-flaky judge --repo OWNER/NAME --sha MAIN_SHA [--register FILE] [--offline DIR]
//!   aib-flaky suggest --register FILE
//!
//! judge prints one JSON line per red job attempt on the PR's current head (every run, every
//! attempt) and, with --register, appends the ones not already there. --sha records a main push
//! as precedent only. suggest prints fingerprints that keep going red (≥3 reds or ≥2 platforms,
//! from ≥2 heads) for dispatch. Exit: 0 done (whatever the verdicts; there is no "pass"),
//! 2 usage, 3 a read failed or the register is unreadable. Never writes to GitHub.

use aib_cireuse::gh::{FileSource, GhCli, Source};
use aib_flaky::gh::gather;
use aib_flaky::{entry_json, judge, new_entries, read_register, suggest, Entry};
use serde_json::json;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Default)]
struct Args {
    cmd: String,
    repo: String,
    pr: Option<u64>,
    sha: String,
    register: Option<PathBuf>,
    offline: Option<PathBuf>,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args::default();
    let mut it = argv.iter();
    a.cmd = it
        .next()
        .cloned()
        .ok_or("missing command (judge | suggest)")?;
    while let Some(k) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{k} needs a value"))
        };
        match k.as_str() {
            "--repo" => a.repo = val()?,
            "--pr" => a.pr = Some(val()?.parse().map_err(|_| "--pr: integer")?),
            "--sha" => a.sha = val()?,
            "--register" => a.register = Some(PathBuf::from(val()?)),
            "--offline" => a.offline = Some(PathBuf::from(val()?)),
            _ => return Err(format!("unknown argument {k}")),
        }
    }
    Ok(a)
}

/// A missing register is empty; one that exists but does not parse is an error.
fn load(p: Option<&Path>) -> Result<Vec<Entry>, String> {
    let Some(p) = p else {
        return Ok(vec![]);
    };
    match std::fs::read_to_string(p) {
        Ok(t) => read_register(&t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(vec![]),
        Err(e) => Err(format!("register: {e}")),
    }
}

/// UTC "YYYY-MM-DDTHH:MM:SSZ".
fn now_iso() -> String {
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (days, rem) = (t.div_euclid(86400), t.rem_euclid(86400));
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

/// Append under an exclusive lock, re-reading the register inside the lock so two writers
/// never interleave a line or add the same attempt twice.
fn append(p: &Path, entries: Vec<Entry>, at: &str) -> Result<(), String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut fh = std::fs::OpenOptions::new()
        .create(true)
        .read(true)
        .append(true)
        .open(p)
        .map_err(|e| e.to_string())?;
    fh.lock().map_err(|e| format!("lock: {e}"))?;
    let mut text = String::new();
    fh.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    fh.read_to_string(&mut text).map_err(|e| e.to_string())?;
    let current = read_register(&text)?;
    let mut out = String::new();
    for e in new_entries(&current, entries) {
        out.push_str(&format!("{}\n", entry_json(&e, at)));
    }
    fh.write_all(out.as_bytes()).map_err(|e| e.to_string())?;
    fh.unlock().map_err(|e| format!("unlock: {e}"))
}

fn cmd_judge(a: &Args, src: &dyn Source) -> i32 {
    if a.repo.is_empty() || a.pr.is_some() == !a.sha.is_empty() {
        eprintln!("aib-flaky: judge needs --repo and exactly one of --pr / --sha");
        return 2;
    }
    let prior = match load(a.register.as_deref()) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("aib-flaky: {e}");
            return 3;
        }
    };
    let f = match gather(src, &a.repo, a.pr, &a.sha) {
        Ok(f) => f,
        Err(e) => {
            println!("{}", json!({"state": "unknown", "note": e}));
            eprintln!("aib-flaky: {e}");
            return 3;
        }
    };
    let entries = judge(&f, &prior);
    let at = now_iso();
    for e in &entries {
        println!("{}", entry_json(e, &at));
    }
    let mut counts = std::collections::BTreeMap::<&str, usize>::new();
    for e in &entries {
        *counts.entry(e.verdict.as_str()).or_default() += 1;
    }
    println!(
        "{}",
        json!({"summary": {"head": f.head, "job_attempts": f.attempts.len(), "red": entries.len(), "verdicts": counts}})
    );
    if let Some(p) = &a.register {
        if let Err(e) = append(p, entries, &at) {
            eprintln!("aib-flaky: register: {e}");
            return 3;
        }
    }
    0
}

fn cmd_suggest(a: &Args) -> i32 {
    if a.register.is_none() {
        eprintln!("aib-flaky: suggest needs --register");
        return 2;
    }
    let entries = match load(a.register.as_deref()) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("aib-flaky: {e}");
            return 3;
        }
    };
    for s in suggest(&entries) {
        println!(
            "{}",
            json!({"fingerprint": s.fingerprint, "reds": s.reds, "platforms": s.platforms, "heads": s.heads, "kind": "recurring_red"})
        );
    }
    0
}

fn run(argv: &[String]) -> i32 {
    let a = match parse(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-flaky: {e}");
            return 2;
        }
    };
    let file_src;
    let src: &dyn Source = match &a.offline {
        Some(dir) => match FileSource::open(dir) {
            Ok(f) => {
                file_src = f;
                &file_src
            }
            Err(e) => {
                eprintln!("aib-flaky: {e}");
                return 2;
            }
        },
        None => &GhCli,
    };
    match a.cmd.as_str() {
        "judge" => cmd_judge(&a, src),
        "suggest" => cmd_suggest(&a),
        _ => {
            eprintln!("usage: aib-flaky judge --repo R (--pr N | --sha S) [--register F] | suggest --register F");
            2
        }
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(run(&argv));
}
