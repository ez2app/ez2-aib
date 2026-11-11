// SPDX-License-Identifier: Apache-2.0
//! aib-signlint — lint one sign-off script before it is sent for a person's consent.
//!
//!   aib-signlint check <file> [--ack S1,S3] [--json]
//!   aib-signlint rules
//!
//! check prints one line per finding (`code line yes|unknown text`), or with --json one object:
//! `{"file","ok","findings":[…],"acked":[…],"acks":[…],"rehearsal":{"env","receipt"}|null}`.
//! `--ack` names rules the author has looked at and lets through; they stay in the output
//! (as `acked`) so the consent dialog and its record show them. U1 and R1 cannot be acked.
//! Exit: 0 nothing left, 1 findings left (yes or unknown), 2 usage or the file cannot be read.

use aib_signlint::{ackable, lint, rehearsal, split_acked, Finding, RULES};
use serde_json::json;
use std::collections::BTreeSet;
use std::path::Path;

fn usage() -> u8 {
    eprintln!("usage: aib-signlint check <file> [--ack S1,S3] [--json] | aib-signlint rules");
    2
}

fn finding_json(f: &Finding) -> serde_json::Value {
    json!({"rule": f.rule, "line": f.line, "state": f.state.as_str(), "msg": f.msg})
}

fn run(argv: &[String]) -> u8 {
    match argv.first().map(String::as_str) {
        Some("rules") if argv.len() == 1 => {
            for (code, ack, what) in RULES {
                println!("{code}\t{}\t{what}", if ack { "ack" } else { "no-ack" });
            }
            0
        }
        Some("check") => {
            let mut file = None;
            let mut acks = BTreeSet::new();
            let mut as_json = false;
            let mut it = argv[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--json" => as_json = true,
                    "--ack" => {
                        let Some(v) = it.next() else { return usage() };
                        for code in v.split(',').map(str::trim).filter(|c| !c.is_empty()) {
                            if !ackable(code) {
                                eprintln!("aib-signlint: {code} cannot be acked (S1–S5 only)");
                                return 2;
                            }
                            acks.insert(code.to_owned());
                        }
                    }
                    f if !f.starts_with('-') && file.is_none() => file = Some(f.to_owned()),
                    _ => return usage(),
                }
            }
            let Some(file) = file else { return usage() };
            let text = match std::fs::read(&file) {
                Ok(b) => match String::from_utf8(b) {
                    Ok(t) => t,
                    Err(_) => {
                        eprintln!("aib-signlint: {file}: not UTF-8");
                        return 2;
                    }
                },
                Err(e) => {
                    eprintln!("aib-signlint: {file}: {e}");
                    return 2;
                }
            };
            let name = Path::new(&file)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("");
            let (left, acked) = split_acked(lint(&text, name), &acks);
            let ok = left.is_empty();
            if as_json {
                let r = rehearsal(&text);
                println!(
                    "{}",
                    json!({
                        "file": file,
                        "ok": ok,
                        "findings": left.iter().map(finding_json).collect::<Vec<_>>(),
                        "acked": acked.iter().map(finding_json).collect::<Vec<_>>(),
                        "acks": acks,
                        "rehearsal": r.map(|r| json!({"env": r.env, "receipt": r.receipt})),
                    })
                );
            } else {
                for f in &left {
                    println!("{} {} {} {}", f.rule, f.line, f.state.as_str(), f.msg);
                }
                for f in &acked {
                    println!("{} {} acked {}", f.rule, f.line, f.msg);
                }
                println!("aib-signlint: {} left, {} acked", left.len(), acked.len());
            }
            u8::from(!ok)
        }
        _ => usage(),
    }
}

fn main() -> std::process::ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(run(&argv))
}
