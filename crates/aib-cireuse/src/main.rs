// SPDX-License-Identifier: Apache-2.0
//! aib-cireuse — read-only shadow: could a push to main have reused its PR's CI result?
//!
//!   aib-cireuse judge --repo OWNER/NAME --sha SHA [--ledger DIR] [--reuse-ledger DIR] [--tz-hours 8]
//!   aib-cireuse recent --repo OWNER/NAME [--limit 10] [--ledger DIR] [--reuse-ledger DIR] [--tz-hours 8]
//!   aib-cireuse day --ledger DIR [--date YYYY-MM-DD] [--tz-hours 8]
//!   aib-cireuse week --reuse-ledger DIR [--days 7]
//!   aib-cireuse pull --repo OWNER/NAME --reuse-ledger DIR [--limit 30]
//!   aib-cireuse build-info   (BUILD_INFO then TOOL_INFO: what aib-release binsync checks, S68c)
//!   --offline DIR (judge / recent / pull): read recorded responses (DIR/index.json: API path -> file) instead of gh
//!
//! judge / recent print one JSON verdict per main commit; with --ledger they append it to
//! DIR/<day>.jsonl (the board-line ledger `day` reads), with --reuse-ledger they append the shared
//! `ez2-reuse-ledger.v1` rows (one `push` row + one row per cell, `kind: reuse_prediction`).
//! week prints one JSON summary of the v1 ledger for the cockpit (S68). pull downloads the
//! `cireuse-ledger` artifacts the main-push workflow uploaded and appends their new lines to the
//! v1 ledger (DIR/.pulled lists the artifact ids already taken).
//! Exit: 0 done (whatever the verdict), 1 a read failed (recorded as state unknown), 2 usage.
//! Never writes to GitHub.

use aib_cireuse::gh::{facts, paged, FileSource, GhCli, Source};
use aib_cireuse::{
    artifacts_to_pull, day_key, day_line, ingest, judge, ledger_lines, ledger_rows, pulled_ids,
    verdict_json, week_json, week_summary, Artifact, Verdict,
};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

struct Args {
    cmd: String,
    repo: String,
    sha: String,
    ledger: Option<PathBuf>,
    reuse_ledger: Option<PathBuf>,
    tz: i64,
    limit: usize,
    date: Option<String>,
    offline: Option<PathBuf>,
    days: i64,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args {
        cmd: String::new(),
        repo: String::new(),
        sha: String::new(),
        ledger: None,
        reuse_ledger: None,
        tz: 8,
        limit: 10,
        date: None,
        offline: None,
        days: 7,
    };
    let mut it = argv.iter();
    a.cmd = it
        .next()
        .cloned()
        .ok_or("missing command (judge | recent | day | week | pull)")?;
    while let Some(k) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{k} needs a value"))
        };
        match k.as_str() {
            "--repo" => a.repo = val()?,
            "--sha" => a.sha = val()?,
            "--ledger" => a.ledger = Some(PathBuf::from(val()?)),
            "--reuse-ledger" => a.reuse_ledger = Some(PathBuf::from(val()?)),
            "--tz-hours" => a.tz = val()?.parse().map_err(|_| "--tz-hours: integer")?,
            "--limit" => a.limit = val()?.parse().map_err(|_| "--limit: integer")?,
            "--date" => a.date = Some(val()?),
            "--offline" => a.offline = Some(PathBuf::from(val()?)),
            "--days" => {
                a.days = val()?.parse().map_err(|_| "--days: integer")?;
                if !(1..=366).contains(&a.days) {
                    return Err("--days: 1 to 366".into());
                }
            }
            _ => return Err(format!("unknown argument {k}")),
        }
    }
    Ok(a)
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Append lines to DIR/<day>.jsonl (the day of the main push in --tz-hours, else today).
fn append(dir: &Path, day: &str, lines: &[String]) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("ledger: {e}"))?;
    let mut fh = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(format!("{day}.jsonl")))
        .map_err(|e| format!("ledger: {e}"))?;
    for l in lines {
        writeln!(fh, "{l}").map_err(|e| format!("ledger: {e}"))?;
    }
    Ok(())
}

fn record(a: &Args, v: &Verdict) -> Result<(), String> {
    let line = verdict_json(v).to_string();
    println!("{line}");
    let t = now();
    let day = day_key(v.main_created_at.unwrap_or(t), a.tz);
    if let Some(dir) = &a.ledger {
        append(dir, &day, std::slice::from_ref(&line))?;
    }
    if let Some(dir) = &a.reuse_ledger {
        let rows: Vec<String> = ledger_rows(v, &a.repo, t)
            .iter()
            .map(ToString::to_string)
            .collect();
        append(dir, &day, &rows)?;
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
                observed_main_wall_minutes: None,
                shadow_false_green: None,
                compared_cells: 0,
                uncomparable_cells: vec![],
                main_created_at: None,
                main_tree: None,
            };
            let _ = record(a, &v);
            eprintln!("aib-cireuse: {e}");
            1
        }
    }
}

fn run(argv: &[String]) -> i32 {
    if argv.len() == 1 && argv[0] == "build-info" {
        println!("{}\n{}", aib_cireuse::BUILD_INFO, aib_cireuse::TOOL_INFO);
        return 0;
    }
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
            let body = match src.get(&format!(
                "repos/{}/commits?sha=main&per_page={}",
                a.repo,
                a.limit.clamp(1, 100)
            )) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("aib-cireuse: {e}");
                    return 1;
                }
            };
            let shas: Vec<String> = serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| {
                    v.as_array().map(|a| {
                        a.iter()
                            .filter_map(|c| c.get("sha")?.as_str().map(str::to_string))
                            .collect()
                    })
                })
                .unwrap_or_default();
            if shas.is_empty() {
                eprintln!("aib-cireuse: no commits read");
                return 1;
            }
            shas.iter()
                .map(|s| judge_one(&a, src, s))
                .max()
                .unwrap_or(0)
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
        "week" => {
            let Some(dir) = &a.reuse_ledger else {
                eprintln!("aib-cireuse: week needs --reuse-ledger");
                return 2;
            };
            let w = week_summary(&ledger_lines(dir), now(), a.days);
            println!("{}", week_json(&w, a.days));
            0
        }
        "pull" if !a.repo.is_empty() => {
            let Some(dir) = &a.reuse_ledger else {
                eprintln!("aib-cireuse: pull needs --reuse-ledger");
                return 2;
            };
            pull(&a, src, dir)
        }
        _ => {
            eprintln!(
                "usage: aib-cireuse judge --repo R --sha S | recent --repo R | day --ledger DIR \
                 | week --reuse-ledger DIR | pull --repo R --reuse-ledger DIR"
            );
            2
        }
    }
}

/// The artifact name the main-push workflow uploads (.github/workflows/cireuse-shadow.yml).
const ARTIFACT: &str = "cireuse-ledger";

/// Download each new ledger artifact with `gh run download` into a fresh folder, take its lines,
/// then remove the folder. Exit 1 if listing or any download failed (the rest still count).
fn pull(a: &Args, src: &dyn Source, dir: &Path) -> i32 {
    let items = match paged(
        src,
        &format!("repos/{}/actions/artifacts?name={ARTIFACT}", a.repo),
        "artifacts",
    ) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("aib-cireuse: {e}");
            return 1;
        }
    };
    let todo = artifacts_to_pull(&items, ARTIFACT, &pulled_ids(dir), a.limit.clamp(1, 100));
    let (mut rc, mut added) = (0, 0);
    for art in &todo {
        match download(&a.repo, art).and_then(|tmp| {
            let r = ingest(dir, art, &tmp);
            let _ = std::fs::remove_dir_all(&tmp);
            r
        }) {
            Ok(n) => added += n,
            Err(e) => {
                eprintln!("aib-cireuse: {e}");
                rc = 1;
            }
        }
    }
    println!(
        "{}",
        serde_json::json!({"artifacts": todo.len(), "lines_added": added})
    );
    rc
}

/// `gh run download RUN -R REPO -n NAME -D <new temp folder>`; the folder is returned.
fn download(repo: &str, art: &Artifact) -> Result<PathBuf, String> {
    let tmp = std::env::temp_dir().join(format!(
        "aib-cireuse-{}-{}-{}",
        std::process::id(),
        art.id,
        now()
    ));
    std::fs::create_dir(&tmp).map_err(|e| format!("artifact {}: {e}", art.id))?;
    let st = std::process::Command::new("gh")
        .args(["run", "download", &art.run_id.to_string(), "-R", repo, "-n"])
        .arg(&art.name)
        .arg("-D")
        .arg(&tmp)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .status();
    match st {
        Ok(s) if s.success() => Ok(tmp),
        other => {
            let _ = std::fs::remove_dir_all(&tmp);
            Err(format!("artifact {}: gh run download {other:?}", art.id))
        }
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(run(&argv));
}
