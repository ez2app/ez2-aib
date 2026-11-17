// SPDX-License-Identifier: Apache-2.0
//! `aib-mergecheck ready --repo DIR [--json] [--config FILE] [--state-dir DIR]`
//! (S73-ready 片 1, IFACE-mergecheck-ready-7b81e-1117 r2). Read-only on GitHub; rc 0 measured,
//! 3 something could not be read, 2 wrong arguments or config.
//!
//! `--repo` is required and should be a refresh clone: `ready` fetches main and the open PRs'
//! refs into it (review 9fae2967 N2: a default of `.` would move a seat tree's tracking refs).

use aib_mergecheck::fsworld::{home, slug_of, state_root, FsWorld};
use aib_mergecheck::{parse_prev, prev_json, ready, to_json, to_text, Cfg};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str =
    "usage: aib-mergecheck ready --repo DIR [--json] [--config FILE] [--state-dir DIR]";

struct Args {
    json: bool,
    repo: Option<PathBuf>,
    config: Option<PathBuf>,
    state_dir: Option<PathBuf>,
}

fn parse_args(a: &[String]) -> Result<Args, String> {
    let mut it = a.iter();
    if it.next().map(String::as_str) != Some("ready") {
        return Err(USAGE.into());
    }
    let mut r = Args {
        json: false,
        repo: None,
        config: None,
        state_dir: None,
    };
    while let Some(x) = it.next() {
        let mut val = || {
            it.next()
                .filter(|v| !v.starts_with('-'))
                .map(PathBuf::from)
                .ok_or(format!("{x} 要一個值"))
        };
        match x.as_str() {
            "--json" => r.json = true,
            "--repo" => r.repo = Some(val()?),
            "--config" => r.config = Some(val()?),
            "--state-dir" => r.state_dir = Some(val()?),
            _ => return Err(format!("不認得 {x}\n{USAGE}")),
        }
    }
    if r.repo.is_none() {
        return Err(format!("--repo 要寫(一份 refresh clone)\n{USAGE}"));
    }
    Ok(r)
}

fn utc_now() -> String {
    let s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    aib_cireuse::iso_utc(s)
}

fn run() -> Result<i32, String> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let a = parse_args(&argv)?;
    let repo = a.repo.clone().ok_or(USAGE)?;
    let env = |k: &str| std::env::var(k).ok();
    let root = state_root(
        env("EZ2_AIB_STATE_ROOT").as_deref(),
        env("EZ2_AIB_HOME").as_deref(),
        home().as_deref(),
    )
    .ok_or("找不到 aib 狀態根(EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME / 家目錄)")?;
    let cfg = match &a.config {
        Some(p) => {
            let t = std::fs::read_to_string(p).map_err(|e| format!("{}:{e}", p.display()))?;
            Cfg::parse(&t)?
        }
        None => Cfg::default(),
    };
    let pending = cfg
        .pending_dir
        .as_ref()
        .map_or_else(|| root.join("pending"), PathBuf::from);
    let plan_file = cfg.plan_file.as_ref().map_or_else(
        || {
            pending
                .join("aib2-a63ab-1107")
                .join("cabin-plan")
                .join("data.json")
        },
        PathBuf::from,
    );
    let state_root = root.join("state");
    let measure_dir = cfg
        .measure_dir
        .as_ref()
        .map_or_else(|| state_root.join("measure"), PathBuf::from);
    let state_dir = a
        .state_dir
        .clone()
        .or_else(|| cfg.state_dir.as_ref().map(PathBuf::from))
        .unwrap_or_else(|| state_root.join("mergecheck"));
    let url = aib_ghread::run_fixed(
        "git",
        &[
            "-C".to_owned(),
            repo.to_string_lossy().into_owned(),
            "remote".to_owned(),
            "get-url".to_owned(),
            "origin".to_owned(),
        ],
        std::time::Duration::from_secs(20),
    )
    .map_err(|e| format!("--repo {}:{e}", repo.display()))?;
    let slug = slug_of(&String::from_utf8_lossy(&url)).ok_or("origin 不是 GitHub 網址")?;
    let w = FsWorld::new(
        repo,
        slug,
        cfg.main.clone(),
        pending,
        plan_file,
        measure_dir,
    );
    let last = state_dir.join("last-ready.json");
    let prev = std::fs::read_to_string(&last)
        .map(|t| parse_prev(&t))
        .unwrap_or_default();
    let r = ready(&w, &cfg, &prev);
    if a.json {
        println!("{}", to_json(&r, &utc_now()));
    } else {
        print!("{}", to_text(&r));
    }
    // only after a full read: a run that could not read must not forget who was mergeable
    if r.unread.is_empty() {
        let tmp = state_dir.join("last-ready.json.tmp");
        let wrote = std::fs::create_dir_all(&state_dir)
            .and_then(|()| std::fs::write(&tmp, prev_json(&r)))
            .and_then(|()| std::fs::rename(&tmp, &last));
        if let Err(e) = wrote {
            eprintln!("last-ready.json 寫不進去:{e}");
        }
    }
    Ok(r.rc())
}

fn main() -> ExitCode {
    match run() {
        Ok(rc) => ExitCode::from(rc as u8),
        Err(e) => {
            eprintln!("{e}");
            ExitCode::from(2)
        }
    }
}
