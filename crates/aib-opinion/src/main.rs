//! `aib-opinion`: check council opinion files and list where the final cites each item
//! (S71, DEV-S71-opinion-format-043dd-1116 r0).
//!
//! ```text
//! aib-opinion check <opinion.md>...
//! aib-opinion table --final <final.md> <opinion.md>... [--json] [--strict]
//! ```
//!
//! rc: 0 fine; 2 a format problem, or (with --strict) an item the final never cites or a citation
//! that matches no item; 4 a file could not be read; 64 usage.

use aib_opinion::{parse, render_md, table, to_json, Opinion};
use std::process::ExitCode;

const USAGE: &str = "usage: aib-opinion check <opinion.md>...\n       aib-opinion table --final <final.md> <opinion.md>... [--json] [--strict]\n\nitem line: - O<n> [H|M|L|N] <section> | <opinion> → <fix>   (→ <fix> required for H and M)\nfirst line: 結論: <同意|有條件|反對> H/M/L: a/b/c   (counts must match the items)\nthe final cites an item as <seat>-O<n>; a citing line that says 不採 lands it as not taken\n";

fn usage(msg: &str) -> ExitCode {
    eprintln!("aib-opinion: {msg}\n{USAGE}");
    ExitCode::from(64)
}

fn read(path: &str) -> Result<String, ExitCode> {
    std::fs::read_to_string(path).map_err(|e| {
        eprintln!("aib-opinion: {path}: {e}");
        ExitCode::from(4)
    })
}

/// Parse every opinion; print each problem as `file:line: message`. Err = a file was unreadable.
fn load(paths: &[String]) -> Result<(Vec<Opinion>, usize), ExitCode> {
    let mut out = Vec::new();
    let mut problems = 0;
    for p in paths {
        let o = parse(p, &read(p)?);
        for pr in &o.problems {
            eprintln!("{p}:{}: {}", pr.line, pr.msg);
        }
        problems += o.problems.len();
        out.push(o);
    }
    Ok((out, problems))
}

fn check(args: &[String]) -> ExitCode {
    if args.is_empty() {
        return usage("check needs at least one opinion file");
    }
    if let Some(a) = args.iter().find(|a| a.starts_with("--")) {
        return usage(&format!("check takes no option {a}"));
    }
    let (ops, problems) = match load(args) {
        Ok(x) => x,
        Err(rc) => return rc,
    };
    if problems > 0 {
        eprintln!("aib-opinion: {problems} problem(s)");
        return ExitCode::from(2);
    }
    for (p, o) in args.iter().zip(&ops) {
        println!("ok {p}: {} item(s), seat {}", o.items.len(), o.seat);
    }
    ExitCode::SUCCESS
}

fn table_cmd(args: &[String]) -> ExitCode {
    let mut final_path: Option<&String> = None;
    let mut json = false;
    let mut strict = false;
    let mut files: Vec<String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--final" => match it.next() {
                Some(v) if final_path.is_none() => final_path = Some(v),
                Some(_) => return usage("--final given twice"),
                None => return usage("--final needs a file"),
            },
            "--json" => json = true,
            "--strict" => strict = true,
            s if s.starts_with("--") => return usage(&format!("unknown option {s}")),
            _ => files.push(a.clone()),
        }
    }
    let Some(final_path) = final_path else {
        return usage("table needs --final <final.md>");
    };
    if files.is_empty() {
        return usage("table needs at least one opinion file");
    }
    let final_text = match read(final_path) {
        Ok(t) => t,
        Err(rc) => return rc,
    };
    let (ops, problems) = match load(&files) {
        Ok(x) => x,
        Err(rc) => return rc,
    };
    if problems > 0 {
        eprintln!(
            "aib-opinion: {problems} problem(s); fix the opinion files first (aib-opinion check)"
        );
        return ExitCode::from(2);
    }
    let mut seats: Vec<&str> = ops.iter().map(|o| o.seat.as_str()).collect();
    seats.sort_unstable();
    if let Some(w) = seats.windows(2).find(|w| w[0] == w[1]) {
        eprintln!(
            "aib-opinion: two opinion files are both from seat {}; give one round's files only",
            w[0]
        );
        return ExitCode::from(2);
    }
    let t = table(&final_text, &ops);
    if json {
        println!("{}", to_json(&t));
    } else {
        print!("{}", render_md(&t));
    }
    if strict && (!t.unlanded().is_empty() || !t.unknown.is_empty()) {
        return ExitCode::from(2);
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.split_first() {
        Some((c, rest)) if c == "check" => check(rest),
        Some((c, rest)) if c == "table" => table_cmd(rest),
        Some((c, _)) if c == "--help" || c == "-h" || c == "help" => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some((c, _)) => usage(&format!("unknown command {c}")),
        None => usage("no command"),
    }
}
