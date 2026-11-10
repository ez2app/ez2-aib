//! `aib-release yield --repo OWNER/NAME --integ N --orig N:SHA[,N:SHA…]
//!     [--need-workflow ci]… [--label in-integ] [--apply]`
//!
//! Without `--apply` it only reads and prints the plan (JSON). With it, the
//! originals that pass every check get the label and their queued / running
//! runs cancelled. Exit: 0 done / nothing to do, 2 gate refused, 3 unknown or
//! failed, 64 usage.
//!
//! `aib-release trial --base REF [--repo-dir DIR] [--expect-tree REF]
//!     [--integ-branch NAME] HEAD…` trial-merges the heads with
//! `git merge-tree` (nothing written to the repository) and prints the
//! conflicts and a stacking order (JSON). Exit: 0 clean, 2 not ready, 3 git
//! could not answer, 64 usage.

use aib_release::gh::GhCli;
use aib_release::trial;
use aib_release::yield_integ::{self, Request, DEFAULT_LABEL};

const USAGE: &str =
    "usage: aib-release yield --repo OWNER/NAME --integ N --orig N:FULLSHA[,N:FULLSHA…] \
[--need-workflow NAME]… [--label NAME] [--apply]
       aib-release trial --base REF [--repo-dir DIR] [--expect-tree REF] [--integ-branch NAME] HEAD…";

struct Args {
    repo: String,
    req: Request,
    apply: bool,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut it = argv.iter();
    match it.next().map(String::as_str) {
        Some("yield") => {}
        _ => return Err("the commands are `yield` and `trial`".into()),
    }
    let (mut repo, mut integ, mut origs, mut need, mut label, mut apply) =
        (None, None, Vec::new(), Vec::new(), None, false);
    while let Some(a) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--repo" => repo = Some(val()?),
            "--integ" => {
                integ = Some(
                    val()?
                        .parse::<u64>()
                        .map_err(|_| "--integ is a PR number")?,
                )
            }
            "--orig" => {
                for part in val()?.split(',').filter(|p| !p.is_empty()) {
                    let (n, sha) = part
                        .split_once(':')
                        .ok_or_else(|| format!("--orig {part}: want N:FULLSHA"))?;
                    let n = n
                        .parse::<u64>()
                        .map_err(|_| format!("--orig {part}: bad PR number"))?;
                    if !yield_integ::is_full_sha(sha) {
                        return Err(format!(
                            "--orig {part}: the head must be the full 40-hex SHA"
                        ));
                    }
                    origs.push((n, sha.to_owned()));
                }
            }
            "--need-workflow" => need.push(val()?),
            "--label" => label = Some(val()?),
            "--apply" => apply = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let repo = repo.ok_or("--repo is required")?;
    if repo.split('/').count() != 2 || repo.contains("..") {
        return Err("--repo is OWNER/NAME".into());
    }
    if origs.is_empty() {
        return Err("--orig is required".into());
    }
    if need.is_empty() {
        need.push("ci".to_owned());
    }
    Ok(Args {
        repo,
        req: Request {
            integ: integ.ok_or("--integ is required")?,
            originals: origs,
            need_workflows: need,
            label: label.unwrap_or_else(|| DEFAULT_LABEL.to_owned()),
        },
        apply,
    })
}

/// Time GitHub gets to create the run that adding the label starts.
const SETTLE: std::time::Duration = std::time::Duration::from_secs(15);

fn settle() {
    // ADR 0005 named exception: a one-shot CLI pause, at most once per
    // `--apply` (only after a label was added); not a loop, not in aibd.
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(SETTLE);
}

fn parse_trial(argv: &[String]) -> Result<(std::path::PathBuf, trial::Request), String> {
    let mut it = argv.iter().skip(1);
    let (mut dir, mut base, mut expect, mut integ, mut heads) =
        (None, None, None, None, Vec::new());
    while let Some(a) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--repo-dir" => dir = Some(std::path::PathBuf::from(val()?)),
            "--base" => base = Some(val()?),
            "--expect-tree" => expect = Some(val()?),
            "--integ-branch" => integ = Some(val()?),
            s if s.starts_with('-') => return Err(format!("unknown argument {s}")),
            s => heads.push(s.to_owned()),
        }
    }
    if heads.is_empty() {
        return Err("trial needs at least one HEAD".into());
    }
    Ok((
        dir.unwrap_or_else(|| ".".into()),
        trial::Request {
            base: base.ok_or("--base is required")?,
            heads,
            expect_tree: expect,
            integ_branch: integ,
        },
    ))
}

fn main_trial(argv: &[String]) -> ! {
    let (dir, req) = match parse_trial(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            std::process::exit(64);
        }
    };
    let rep = match trial::GitCli::new(&dir) {
        Ok(git) => trial::run(&git, &req),
        Err(e) => {
            println!("{}", serde_json::json!({"verdict": "error", "error": e}));
            std::process::exit(3);
        }
    };
    println!("{}", trial::to_json(&rep));
    std::process::exit(trial::exit_code(&rep));
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.first().map(String::as_str) == Some("trial") {
        main_trial(&argv);
    }
    let args = match parse(&argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            std::process::exit(64);
        }
    };
    let gh = GhCli::new(&args.repo);
    let plan = yield_integ::plan(&gh, &args.req);
    let applied = args
        .apply
        .then(|| yield_integ::apply(&gh, &args.req, &plan, &settle));
    println!("{}", yield_integ::to_json(&plan, applied.as_deref()));
    std::process::exit(yield_integ::exit_code(
        &plan,
        applied.as_deref().unwrap_or(&[]),
    ));
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    fn v(s: &str) -> Vec<String> {
        s.split_whitespace().map(str::to_owned).collect()
    }

    const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

    #[test]
    fn a_short_or_empty_head_is_refused_at_the_door() {
        for bad in [
            "537:",
            "537:781fadd5",
            "537:0123456789ABCDEF0123456789ABCDEF01234567",
        ] {
            let e = parse(&v(&format!("yield --repo o/r --integ 1 --orig {bad}"))).err();
            assert!(e.is_some_and(|e| e.contains("full 40-hex")), "{bad}");
        }
    }

    #[test]
    fn trial_needs_a_base_and_a_head_and_knows_its_flags() {
        assert!(parse_trial(&v("trial a b")).is_err_and(|e| e.contains("--base")));
        assert!(parse_trial(&v("trial --base main")).is_err_and(|e| e.contains("HEAD")));
        assert!(parse_trial(&v("trial --base main --bogus a")).is_err());
        let (dir, r) = parse_trial(&v(
            "trial --base origin/main --expect-tree x --integ-branch int/m1 a b",
        ))
        .unwrap();
        assert_eq!(dir, std::path::PathBuf::from("."));
        assert_eq!(r.heads, vec!["a", "b"]);
        assert_eq!(r.expect_tree.as_deref(), Some("x"));
        assert_eq!(r.integ_branch.as_deref(), Some("int/m1"));
    }

    #[test]
    fn defaults_are_ci_and_in_integ_and_dry() {
        let a = parse(&v(&format!(
            "yield --repo o/r --integ 567 --orig 537:{SHA},557:{SHA}"
        )))
        .unwrap();
        assert_eq!(a.req.need_workflows, vec!["ci"]);
        assert_eq!(a.req.label, "in-integ");
        assert_eq!(a.req.originals.len(), 2);
        assert!(!a.apply);
    }
}
