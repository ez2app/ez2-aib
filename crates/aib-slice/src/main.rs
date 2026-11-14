// SPDX-License-Identifier: Apache-2.0
//! aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--gate] [--hours N]
//!   [--gh PROGRAM | --no-gh]
//!
//! Prints one `aib-slice-sweep.v1` JSON object (schema/sweep.v1.json). Exit: 0 swept; 2 the
//! sweep is not complete (something could not be read; JSON still printed, `complete=false`);
//! 3 only with `--gate`, when a branch is in class 1, 2 or 3; 64 usage.

use aib_slice::{blocks, classify, scan, to_json};
use std::path::PathBuf;
use std::process::ExitCode;

const USAGE: &str = "usage: aib-slice sweep [--repo DIR] [--pending DIR] [--seat SEAT] [--no-fetch] [--gate] [--hours N] [--gh PROGRAM | --no-gh]";

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

fn run(args: &[String]) -> Result<u8, String> {
    let mut it = args.iter();
    if it.next().map(String::as_str) != Some("sweep") {
        return Err("the only command is sweep".into());
    }
    let mut o = scan::Opts {
        repo: PathBuf::from("."),
        pending: home().join(".ez2-aib").join("pending"),
        seat: None,
        fetch: true,
        gh: Some("gh".into()),
        now: scan::now(),
    };
    let (mut gate, mut hours) = (false, 72_i64);
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
                hours = val()?
                    .parse()
                    .ok()
                    .filter(|h| *h > 0)
                    .ok_or("--hours: a positive number")?;
            }
            "--gh" => o.gh = Some(val()?),
            "--no-gh" => o.gh = None,
            "--no-fetch" => o.fetch = false,
            "--gate" => gate = true,
            _ => return Err(format!("unknown argument {a}")),
        }
    }
    let r = scan::sweep(&o);
    let v = to_json(&r, hours);
    println!("{v}");
    if !r.errors.is_empty() {
        return Ok(2);
    }
    if gate && r.branches.iter().any(|b| blocks(classify(b, hours).0)) {
        return Ok(3);
    }
    Ok(0)
}
