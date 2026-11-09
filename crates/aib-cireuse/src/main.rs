// SPDX-License-Identifier: Apache-2.0
//! aib-cireuse — read-only shadow: could a push to main have reused its PR's CI result?
//!
//!   aib-cireuse judge --repo OWNER/NAME --sha SHA [--ledger DIR] [--tz-hours 8]
//!   aib-cireuse recent --repo OWNER/NAME [--limit 10] [--ledger DIR] [--tz-hours 8]
//!   aib-cireuse day --ledger DIR [--date YYYY-MM-DD] [--tz-hours 8]
//!   --offline DIR (judge / recent): read recorded responses (DIR/index.json: API path -> file) instead of gh
//!
//! judge / recent print one JSON verdict per main commit and, with --ledger, append it to
//! DIR/<day>.jsonl. day prints the board line. Exit: 0 done (whatever the verdict), 1 a read
//! failed (recorded as state unknown), 2 usage. Never writes to GitHub.

use aib_cireuse::gh::{facts, FileSource, GhCli, Source};
use aib_cireuse::{day_key, day_line, judge, verdict_json, Verdict};
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

struct Args {
    cmd: String,
    repo: String,
    sha: String,
    ledger: Option<PathBuf>,
    tz: i64,
    limit: usize,
    date: Option<String>,
    offline: Option<PathBuf>,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args { cmd: String::new(), repo: String::new(), sha: String::new(), ledger: None, tz: 8, limit: 10, date: None, offline: None };
    let mut it = argv.iter();
    a.cmd = it.next().cloned().ok_or("missing command (judge | recent | day)")?;
    while let Some(k) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| format!("{k} needs a value"));
        match k.as_str() {
            "--repo" => a.repo = val()?,
            "--sha" => a.sha = val()?,
            "--ledger" => a.ledger = Some(PathBuf::from(val()?)),
            "--tz-hours" => a.tz = val()?.parse().map_err(|_| "--tz-hours: integer")?,
            "--limit" => a.limit = val()?.parse().map_err(|_| "--limit: integer")?,
            "--date" => a.date = Some(val()?),
            "--offline" => a.offline = Some(PathBuf::from(val()?)),
            _ => return Err(format!("unknown argument {k}")),
        }
    }
    Ok(a)
}

fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

fn record(a: &Args, v: &Verdict) -> Result<(), String> {
    let line = verdict_json(v).to_string();
    println!("{line}");
    if let Some(dir) = &a.ledger {
        std::fs::create_dir_all(dir).map_err(|e| format!("ledger: {e}"))?;
        let day = day_key(v.main_created_at.unwrap_or_else(now), a.tz);
        let p = dir.join(format!("{day}.jsonl"));
        let mut fh = std::fs::OpenOptions::new().create(true).append(true).open(&p).map_err(|e| format!("ledger: {e}"))?;
        writeln!(fh, "{line}").map_err(|e| format!("ledger: {e}"))?;
    }
    Ok(())
}

fn judge_one(a: &Args, src: &dyn Source, sha: &str) -> i32 {
    match facts(src, &a.repo, sha) {
        Ok(f) => match record(a, &judge(&f)) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("aib-cireuse: {e}");
                1
            }
        },
        Err(e) => {
            let v = Verdict {
                main_sha: sha.into(),
                state: "unknown".into(),
                note: e.clone(),
                cells: vec![],
                save_minutes: None,
                shadow_false_green: vec![],
                main_created_at: None,
            };
            let _ = record(a, &v);
            eprintln!("aib-cireuse: {e}");
            1
        }
    }
}

fn run(argv: &[String]) -> i32 {
    let a = match parse(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-cireuse: {e}");
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
                eprintln!("aib-cireuse: {e}");
                return 2;
            }
        },
        None => &GhCli,
    };
    match a.cmd.as_str() {
        "judge" if !a.repo.is_empty() && !a.sha.is_empty() => judge_one(&a, src, &a.sha.clone()),
        "recent" if !a.repo.is_empty() => {
            let body = match src.get(&format!("repos/{}/commits?sha=main&per_page={}", a.repo, a.limit.clamp(1, 100))) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("aib-cireuse: {e}");
                    return 1;
                }
            };
            let shas: Vec<String> = serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| v.as_array().map(|a| a.iter().filter_map(|c| c.get("sha")?.as_str().map(str::to_string)).collect()))
                .unwrap_or_default();
            if shas.is_empty() {
                eprintln!("aib-cireuse: no commits read");
                return 1;
            }
            shas.iter().map(|s| judge_one(&a, src, s)).max().unwrap_or(0)
        }
        "day" => {
            let Some(dir) = &a.ledger else {
                eprintln!("aib-cireuse: day needs --ledger");
                return 2;
            };
            let day = a.date.clone().unwrap_or_else(|| day_key(now(), a.tz));
            let lines: Vec<String> = std::fs::read_to_string(dir.join(format!("{day}.jsonl")))
                .map(|t| t.lines().map(str::to_string).collect())
                .unwrap_or_default();
            println!("{day} {}", day_line(&lines));
            0
        }
        _ => {
            eprintln!("usage: aib-cireuse judge --repo R --sha S | recent --repo R | day --ledger DIR");
            2
        }
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(run(&argv));
}
