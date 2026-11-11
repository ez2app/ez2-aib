// SPDX-License-Identifier: Apache-2.0
//! aib-public-leak — RULES §16 gate (approvals-to-rust C10; becomes `aib-verify public-leak`
//! once aib-verify is on main).
//!
//!   aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist]
//!
//! Checks the added lines of `git diff BASE...HEAD`, the messages of `git log BASE..HEAD` and,
//! with --github-event (a pull_request event payload, e.g. $GITHUB_EVENT_PATH), the PR title
//! and body. The denylist is read from $AIB_PUBLIC_DENYLIST; unset or empty ⇒ only the IPv4
//! rule runs and `denylist_absent` is printed; with --require-denylist that is exit 3, never a
//! silent pass (a63ab, after #636). Findings name the place and the rule only.
//!
//! The diff reads .gitattributes from BASE (`git --attr-source=BASE`, 043dd #636 L1): a PR that
//! adds `*.rs binary` or `-diff` cannot turn its own added lines into "Binary files differ".
//! Files BASE's attributes call binary are counted as not read in the summary.
//!
//! Exit: 0 clean, 1 findings, 2 usage or a git / file read failed, 3 denylist required but absent.

use aib_public_leak::{
    parse_diff, parse_log, pr_text, report_line, scan, Denylist, Input, DENYLIST_ENV,
};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

struct Args {
    base: String,
    head: String,
    repo: PathBuf,
    event: Option<PathBuf>,
    require_denylist: bool,
}

const USAGE: &str =
    "usage: aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist]";

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args {
        base: String::new(),
        head: "HEAD".into(),
        repo: PathBuf::from("."),
        event: None,
        require_denylist: false,
    };
    let mut it = argv.iter().skip(1);
    while let Some(k) = it.next() {
        let mut v = || it.next().cloned().ok_or(format!("{k} needs a value"));
        match k.as_str() {
            "--base" => a.base = v()?,
            "--head" => a.head = v()?,
            "--repo" => a.repo = PathBuf::from(v()?),
            "--github-event" => a.event = Some(PathBuf::from(v()?)),
            "--require-denylist" => a.require_denylist = true,
            _ => return Err(format!("unknown argument {k}")),
        }
    }
    if a.base.is_empty() {
        return Err("--base is required".into());
    }
    for r in [&a.base, &a.head] {
        if r.starts_with('-') {
            return Err(format!("a revision may not start with '-': {r}"));
        }
    }
    Ok(a)
}

fn git(repo: &Path, args: &[&str]) -> Result<String, String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["-c", "core.quotePath=false"])
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if !o.status.success() {
        // stderr may quote file contents; only the subcommand and the exit status are printed.
        let sub = args.iter().find(|a| !a.starts_with('-')).unwrap_or(&"");
        return Err(format!("git {sub} failed ({})", o.status));
    }
    String::from_utf8(o.stdout).map_err(|_| "git output is not UTF-8".to_string())
}

/// What was read, and how many files the base's attributes kept as binary (not read).
fn gather(a: &Args) -> Result<(Input, usize), String> {
    let range3 = format!("{}...{}", a.base, a.head);
    let range2 = format!("{}..{}", a.base, a.head);
    // attributes from BASE, never from the PR's own tree
    let attr = format!("--attr-source={}", a.base);
    let diff = git(
        &a.repo,
        &[
            &attr,
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-renames",
            "-U0",
            &range3,
            "--",
        ],
    )?;
    let log = git(&a.repo, &["log", "--format=%H%x00%B%x1e", &range2, "--"])?;
    let (title, body) = match &a.event {
        None => (None, None),
        Some(p) => {
            let t = std::fs::read_to_string(p).map_err(|e| format!("{}: {e}", p.display()))?;
            let v: serde_json::Value =
                serde_json::from_str(&t).map_err(|e| format!("{}: not JSON ({e})", p.display()))?;
            pr_text(&v)
        }
    };
    let binary = diff
        .lines()
        .filter(|l| l.starts_with("Binary files "))
        .count();
    Ok((
        Input {
            files: parse_diff(&diff),
            commits: parse_log(&log),
            title,
            body,
        },
        binary,
    ))
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().collect();
    let a = match parse_args(&argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-public-leak: {e}\n{USAGE}");
            return ExitCode::from(2);
        }
    };
    let deny = Denylist::parse(&std::env::var(DENYLIST_ENV).unwrap_or_default());
    let (input, binary) = match gather(&a) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("aib-public-leak: {e}");
            return ExitCode::from(2);
        }
    };
    if deny.is_empty() {
        println!("public-leak: denylist_absent ({DENYLIST_ENV} not set or empty): only the IPv4 rule ran");
    }
    let found = scan(&input, &deny);
    for f in &found {
        println!("{}", report_line(f));
    }
    let lines: usize = input.files.iter().map(|f| f.lines.len()).sum();
    println!(
        "public-leak: {} finding(s); checked {} file(s), {} added line(s), {} commit(s), PR text {}, denylist {} entr{}, {} binary file(s) not read",
        found.len(),
        input.files.len(),
        lines,
        input.commits.len(),
        if a.event.is_some() { "yes" } else { "no" },
        deny.len(),
        if deny.len() == 1 { "y" } else { "ies" },
        binary,
    );
    if !found.is_empty() {
        ExitCode::from(1)
    } else if deny.is_empty() && a.require_denylist {
        // the denylist rule did not run: not checked is not clean
        println!("public-leak: denylist required (--require-denylist) but absent: not passed");
        ExitCode::from(3)
    } else {
        ExitCode::SUCCESS
    }
}
