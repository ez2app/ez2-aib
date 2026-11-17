//! `aib-mergewatch plan --sweep FILE|- [--labels FILE] [--json]`
//!
//! Read only (slice 1, DEV-mergewatch-rust s15): prints the merge queue order
//! and what blocks each open PR. `--sweep` is the JSON of `aib-slice sweep`;
//! `--labels` is the JSON of `gh pr list --json number,labels` (without it,
//! no PR has `ready-merge`, so every PR is held). It never merges, pushes,
//! labels or comments.
//!
//! Exit codes: 0 plan printed from a complete sweep; 2 the sweep or labels
//! could not be read, or the sweep says it is incomplete (the plan is still
//! printed: do not merge on it); 64 usage.

use std::collections::BTreeMap;
use std::io::Read;
use std::process::ExitCode;

const USAGE: &str = "usage: aib-mergewatch plan --sweep FILE|- [--labels FILE] [--json]";

fn read(path: &str) -> Result<serde_json::Value, String> {
    let mut text = String::new();
    if path == "-" {
        std::io::stdin()
            .read_to_string(&mut text)
            .map_err(|e| format!("stdin: {e}"))?;
    } else {
        text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    }
    serde_json::from_str(&text).map_err(|e| format!("{path}: not JSON: {e}"))
}

fn run(args: &[String]) -> Result<ExitCode, (u8, String)> {
    let usage = |m: &str| (64u8, format!("{m}\n{USAGE}"));
    let mut it = args.iter();
    if it.next().map(String::as_str) != Some("plan") {
        return Err(usage("the only subcommand is plan"));
    }
    let (mut sweep, mut labels, mut json) = (None, None, false);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--sweep" => {
                sweep = Some(
                    it.next()
                        .ok_or_else(|| usage("--sweep needs a value"))?
                        .clone(),
                )
            }
            "--labels" => {
                labels = Some(
                    it.next()
                        .ok_or_else(|| usage("--labels needs a value"))?
                        .clone(),
                )
            }
            "--json" => json = true,
            other => return Err(usage(&format!("unknown argument {other}"))),
        }
    }
    let sweep = sweep.ok_or_else(|| usage("--sweep is required"))?;
    if labels.as_deref() == Some("-") && sweep == "-" {
        return Err(usage("only one of --sweep / --labels can be -"));
    }
    let sv = read(&sweep).map_err(|e| (2, e))?;
    let lv = match labels {
        Some(p) => {
            aib_mergewatch::labels_from_gh(&read(&p).map_err(|e| (2, e))?).map_err(|e| (2, e))?
        }
        None => BTreeMap::new(),
    };
    let p = aib_mergewatch::plan(&sv, &lv).map_err(|e| (2, e))?;
    if json {
        println!("{}", aib_mergewatch::to_json(&p));
    } else {
        print!("{}", aib_mergewatch::to_text(&p));
    }
    Ok(if p.complete {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    })
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(c) => c,
        Err((rc, msg)) => {
            eprintln!("aib-mergewatch: {msg}");
            ExitCode::from(rc)
        }
    }
}
