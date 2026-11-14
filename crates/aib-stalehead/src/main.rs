//! `aib-stalehead dm [--file F]` / `aib-stalehead review <file>`: the
//! stale-head gate (see the library). Prints one JSON line on stdout and the
//! lines a person should see on stderr.
//!
//! Exit: 0 = send (maybe with reminders), 3 = held back (`stale_head`),
//! 2 = bad usage or the text could not be read.
//!
//! - `dm`: the text comes from `--file` or stdin; every `REVIEW-*.md` /
//!   `VERIFY-*.md` path it names is read too (its first line only).
//! - `review`: the file's first line only.
//! - `AIB_DM_ACK_STALE=1`: a stale head is still printed but not held back
//!   (the JSON says `acked`).
//! - `AIB_STALEHEAD_REPO` (default `ez2app/ez2-aib`), `AIB_STALEHEAD_GH`
//!   (default `gh`; tests point it at a fake).

use aib_drive::snapshot::GhRead;
use aib_stalehead::{check, review_first_line, review_paths, GhQuota};
use std::io::Read as _;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

/// One `gh` call; a dm waits for at most a dozen of these.
const CALL_TIMEOUT: Duration = Duration::from_secs(8);

fn usage() -> ExitCode {
    eprintln!("usage: aib-stalehead dm [--file F] | aib-stalehead review <file>");
    ExitCode::from(2)
}

fn expand(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map_or_else(|| PathBuf::from(p), |h| PathBuf::from(h).join(rest)),
        None => PathBuf::from(p),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let text = match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["dm"] => {
            let mut s = String::new();
            if std::io::stdin().read_to_string(&mut s).is_err() {
                eprintln!("aib-stalehead: stdin unreadable");
                return ExitCode::from(2);
            }
            s
        }
        ["dm", "--file", f] | ["review", f] => match std::fs::read_to_string(expand(f)) {
            Ok(s) if args[0] == "review" => review_first_line(&s).to_owned(),
            Ok(s) => s,
            Err(e) => {
                eprintln!("aib-stalehead: {f}: {}", e.kind());
                return ExitCode::from(2);
            }
        },
        _ => return usage(),
    };
    let mut all = text.clone();
    if args[0] == "dm" {
        for p in review_paths(&text) {
            // a named review that cannot be read is left to the reader: the
            // dm still names it, and the gate does not guess its head
            if let Ok(s) = std::fs::read_to_string(expand(&p)) {
                all.push('\n');
                all.push_str(review_first_line(&s));
            }
        }
    }
    let repo = std::env::var("AIB_STALEHEAD_REPO")
        .ok()
        .filter(|r| !r.is_empty())
        .unwrap_or_else(|| "ez2app/ez2-aib".to_owned());
    let program = std::env::var("AIB_STALEHEAD_GH")
        .ok()
        .filter(|p| !p.is_empty())
        .unwrap_or_else(|| "gh".to_owned());
    let gh = GhRead {
        program: program.clone(),
        timeout: CALL_TIMEOUT,
    };
    let quota = GhQuota {
        program,
        timeout: CALL_TIMEOUT,
    };
    let report = check(&gh, &quota, &repo, &all);
    let acked = std::env::var("AIB_DM_ACK_STALE").as_deref() == Ok("1");
    let blocked = report.blocked() && !acked;
    let mut j = report.to_json();
    j["blocked"] = serde_json::json!(blocked);
    if !blocked {
        j["reason"] = serde_json::Value::Null;
    }
    j["acked"] = serde_json::json!(acked && report.blocked());
    println!("{j}");
    for l in report.lines() {
        eprintln!("{l}");
    }
    if blocked {
        ExitCode::from(3)
    } else {
        ExitCode::SUCCESS
    }
}
