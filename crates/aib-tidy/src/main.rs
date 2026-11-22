//! `aib-tidy scan [--root R] [--json]`: what may be cleaned after a merge, listed for a person
//! to review (DECISION-council-tidy-519d0-1121 r1). Reads only; changes and removes nothing.
//!
//! Exit: 0 = listed, 2 = bad usage or the root could not be read.

use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some("scan") {
        eprintln!("usage: aib-tidy scan [--root R] [--json]");
        return ExitCode::from(2);
    }
    let (mut root, mut as_json) = (PathBuf::from("."), false);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--root" => match args.next() {
                Some(r) => root = PathBuf::from(r),
                None => return ExitCode::from(2),
            },
            "--json" => as_json = true,
            _ => {
                eprintln!("usage: aib-tidy scan [--root R] [--json]");
                return ExitCode::from(2);
            }
        }
    }
    let items = match aib_tidy::scan(&root) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("aib-tidy: {e}");
            return ExitCode::from(2);
        }
    };
    if as_json {
        println!("{}", aib_tidy::to_json(&items));
    } else {
        for i in &items {
            println!("{}", i.line());
        }
        println!("{}", aib_tidy::summary(&items));
    }
    ExitCode::SUCCESS
}
