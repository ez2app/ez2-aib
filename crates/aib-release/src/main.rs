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
//!
//! `aib-release host-build (--candidate-bin FILE | --candidate-info LINE)
//!     (--current-bin FILE | --current-info LINE | --current-unknown)
//!     [--repo-dir DIR] [--allow-downgrade WHY] [--allow-unknown-current WHY]`
//! says whether a build of aib-cloud-host may go onto a host: built clean, on
//! `origin/main` (fixed; fetch first), containing what the host runs (DEV 4.6
//! P1; see `host_build`). Exit: 0 may go on, 2 refused, 3 git could not
//! answer, 64 usage.
//!
//! `aib-release binsync install --name NAME --file FILE --by WHO [--dest DIR] [--repo-dir DIR]
//!     [--allow-downgrade WHY] [--allow-unknown-current WHY]`
//! `aib-release binsync rollback --name NAME --by WHO --why WHY [--dest DIR]`
//! `aib-release binsync status [--dest DIR] [--repo-dir DIR]`
//! puts a local tool (today only aib-title) into `~/.ez2-aib/bin` (or `--dest`) when its build
//! is clean and on the local origin/main, swapping it in with a rename and keeping `.prev` and
//! a ledger (`binsync`). Exit: 0 done, 2 refused, 3 could not answer / busy, 64 usage.

use aib_release::binsync;
use aib_release::gh::GhCli;
use aib_release::host_build;
use aib_release::trial;
use aib_release::yield_integ::{self, Request, DEFAULT_LABEL};

const USAGE: &str =
    "usage: aib-release yield --repo OWNER/NAME --integ N --orig N:FULLSHA[,N:FULLSHA…] \
[--need-workflow NAME]… [--label NAME] [--apply]
       aib-release trial --base REF [--repo-dir DIR] [--expect-tree REF] [--integ-branch NAME] HEAD…
       aib-release host-build (--candidate-bin FILE | --candidate-info LINE) \
(--current-bin FILE | --current-info LINE | --current-unknown) [--repo-dir DIR] \
[--allow-downgrade WHY] [--allow-unknown-current WHY]
       aib-release binsync install --name NAME --file FILE --by WHO [--dest DIR] [--repo-dir DIR] \
[--allow-downgrade WHY] [--allow-unknown-current WHY]
       aib-release binsync rollback --name NAME --by WHO --why WHY [--dest DIR]
       aib-release binsync status [--dest DIR] [--repo-dir DIR]";

struct Args {
    repo: String,
    req: Request,
    apply: bool,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut it = argv.iter();
    match it.next().map(String::as_str) {
        Some("yield") => {}
        _ => return Err("the commands are `yield`, `trial` and `host-build`".into()),
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

/// A reason given with an `--allow-…` flag: it goes onto the approval the
/// person reads, so it must say something.
fn why(flag: &str, v: String) -> Result<String, String> {
    let t = v.trim();
    if t.chars().count() < 4 || t.starts_with('-') {
        return Err(format!(
            "{flag} needs the reason (it is shown on the approval)"
        ));
    }
    Ok(t.to_owned())
}

/// The build info, and the file's sha256 when it came from a file (read once: the hash is
/// of the same bytes the build info was read from).
fn info_of(
    flag: &str,
    file: Option<String>,
    line: Option<String>,
) -> Result<(host_build::BuildInfo, Option<String>), String> {
    match (file, line) {
        (Some(f), None) => {
            let bytes = std::fs::read(&f).map_err(|e| format!("{flag}-bin {f}: {e}"))?;
            let info =
                host_build::info_in_binary(&bytes).map_err(|e| format!("{flag}-bin {f}: {e}"))?;
            Ok((info, Some(host_build::sha256_hex(&bytes))))
        }
        (None, Some(l)) => host_build::parse_info(&l)
            .map(|i| (i, None))
            .ok_or_else(|| format!("{flag}-info: not a build info line: {l:?}")),
        _ => Err(format!("give exactly one of {flag}-bin and {flag}-info")),
    }
}

fn parse_host_build(argv: &[String]) -> Result<(std::path::PathBuf, host_build::Request), String> {
    let mut it = argv.iter().skip(1);
    let (mut dir, mut cbin, mut cinfo, mut hbin, mut hinfo, mut hunknown) =
        (None, None, None, None, None, false);
    let (mut down, mut unknown) = (None, None);
    while let Some(a) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{a} needs a value"))
        };
        match a.as_str() {
            "--repo-dir" => dir = Some(std::path::PathBuf::from(val()?)),
            "--candidate-bin" => cbin = Some(val()?),
            "--candidate-info" => cinfo = Some(val()?),
            "--current-bin" => hbin = Some(val()?),
            "--current-info" => hinfo = Some(val()?),
            "--current-unknown" => hunknown = true,
            "--allow-downgrade" => down = Some(why(a, val()?)?),
            "--allow-unknown-current" => unknown = Some(why(a, val()?)?),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let (candidate, candidate_sha256) = info_of("--candidate", cbin, cinfo)?;
    let current = match (hbin, hinfo, hunknown) {
        (None, None, true) => host_build::Current::Unknown,
        (f, l, false) if f.is_some() != l.is_some() => {
            host_build::Current::Known(info_of("--current", f, l)?.0)
        }
        _ => {
            return Err(
                "give exactly one of --current-bin, --current-info and --current-unknown".into(),
            )
        }
    };
    Ok((
        dir.unwrap_or_else(|| ".".into()),
        // the main line is not the caller's to name (2240e, #650 M1)
        host_build::Request {
            candidate_sha256,
            allow_downgrade: down,
            allow_unknown_current: unknown,
            ..host_build::Request::for_host(candidate, current)
        },
    ))
}

fn main_host_build(argv: &[String]) -> ! {
    let (dir, req) = match parse_host_build(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            std::process::exit(64);
        }
    };
    let rep = host_build::check(&host_build::GitCli::new(&dir), &req);
    println!("{}", host_build::to_json(&req, &rep));
    std::process::exit(host_build::exit_code(&rep));
}

#[derive(Debug, Default, PartialEq, Eq)]
struct BinsyncArgs {
    action: String,
    name: Option<String>,
    file: Option<String>,
    by: Option<String>,
    why: Option<String>,
    dest: Option<std::path::PathBuf>,
    repo: Option<std::path::PathBuf>,
    down: Option<String>,
    unknown: Option<String>,
}

/// `who`: a seat or person tag for the ledger, short and plain.
fn who(s: &str) -> Result<String, String> {
    if s.is_empty()
        || s.len() > 40
        || !s
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'@' | b':' | b'-' | b'_' | b'/'))
    {
        return Err(format!(
            "--by {s:?}: letters, digits and @ : - _ / only, at most 40"
        ));
    }
    Ok(s.to_owned())
}

fn parse_binsync(argv: &[String]) -> Result<BinsyncArgs, String> {
    let mut a = BinsyncArgs {
        action: argv.get(1).cloned().unwrap_or_default(),
        ..BinsyncArgs::default()
    };
    if !matches!(a.action.as_str(), "install" | "rollback" | "status") {
        return Err("binsync needs install, rollback or status".into());
    }
    let mut it = argv.iter().skip(2);
    let mut seen: Vec<&str> = Vec::new();
    while let Some(k) = it.next() {
        // a flag given twice is refused, not silently replaced (§18 review N3)
        if seen.contains(&k.as_str()) {
            return Err(format!("{k} given twice"));
        }
        seen.push(k.as_str());
        // the next flag is not a value: `--name --file x` is a usage error (N3)
        let mut val = || match it.next() {
            Some(v) if !v.starts_with("--") => Ok(v.clone()),
            _ => Err(format!("{k} needs a value")),
        };
        match k.as_str() {
            "--name" => a.name = Some(val()?),
            "--file" => a.file = Some(val()?),
            "--by" => a.by = Some(who(&val()?)?),
            "--why" => a.why = Some(why(k, val()?)?),
            "--dest" => a.dest = Some(val()?.into()),
            "--repo-dir" => a.repo = Some(val()?.into()),
            "--allow-downgrade" => a.down = Some(why(k, val()?)?),
            "--allow-unknown-current" => a.unknown = Some(why(k, val()?)?),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let need = |v: &Option<String>, k: &str| {
        v.is_some()
            .then_some(())
            .ok_or_else(|| format!("binsync {} needs {k}", a.action))
    };
    let none = |set: bool, k: &str| {
        (!set)
            .then_some(())
            .ok_or_else(|| format!("binsync {} does not take {k}", a.action))
    };
    match a.action.as_str() {
        "install" => {
            need(&a.name, "--name")?;
            need(&a.file, "--file")?;
            need(&a.by, "--by")?;
            none(a.why.is_some(), "--why")?;
        }
        "rollback" => {
            need(&a.name, "--name")?;
            need(&a.by, "--by")?;
            need(&a.why, "--why")?;
            none(a.file.is_some(), "--file")?;
            none(a.repo.is_some(), "--repo-dir")?;
            none(a.down.is_some() || a.unknown.is_some(), "--allow-*")?;
        }
        _ => {
            none(
                a.name.is_some() || a.file.is_some() || a.by.is_some() || a.why.is_some(),
                "--name / --file / --by / --why",
            )?;
            none(a.down.is_some() || a.unknown.is_some(), "--allow-*")?;
        }
    }
    Ok(a)
}

fn main_binsync(argv: &[String]) -> ! {
    let a = match parse_binsync(argv) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}\n{USAGE}");
            std::process::exit(64);
        }
    };
    let dest = match a.dest.clone().or_else(|| {
        std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".ez2-aib/bin"))
    }) {
        Some(d) => d,
        None => {
            eprintln!("no --dest and no HOME\n{USAGE}");
            std::process::exit(64);
        }
    };
    let repo = a.repo.clone().unwrap_or_else(|| ".".into());
    let git = host_build::GitCli::new(&repo);
    let (os, arch) = (std::env::consts::OS, std::env::consts::ARCH);
    let out = match a.action.as_str() {
        "install" => {
            let file = a.file.clone().unwrap_or_default();
            // read once: the bytes checked are the bytes written
            match std::fs::read(&file) {
                Err(e) => binsync::Outcome {
                    verdict: host_build::Verdict::Error,
                    code: "io",
                    detail: format!("{file}: {e}"),
                    facts: Default::default(),
                },
                Ok(bytes) => binsync::install(
                    &git,
                    &binsync::Install {
                        dest,
                        name: a.name.clone().unwrap_or_default(),
                        bytes,
                        by: a.by.clone().unwrap_or_default(),
                        allow_downgrade: a.down.clone(),
                        allow_unknown_current: a.unknown.clone(),
                        main_moved_at: git.main_moved_at(),
                        os: os.into(),
                        arch: arch.into(),
                        now: binsync::now_secs(),
                    },
                ),
            }
        }
        "rollback" => binsync::rollback(
            &dest,
            a.name.as_deref().unwrap_or_default(),
            a.by.as_deref().unwrap_or_default(),
            a.why.as_deref().unwrap_or_default(),
            os,
            arch,
            binsync::now_secs(),
        ),
        _ => binsync::status(Some(&git), &dest),
    };
    println!("{}", out.to_json());
    std::process::exit(out.exit_code());
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.first().map(String::as_str) == Some("binsync") {
        main_binsync(&argv);
    }
    if argv.first().map(String::as_str) == Some("trial") {
        main_trial(&argv);
    }
    if argv.first().map(String::as_str) == Some("host-build") {
        main_host_build(&argv);
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

    fn bs(s: &str) -> Result<BinsyncArgs, String> {
        parse_binsync(&v(s))
    }

    #[test]
    fn binsync_args_each_action_takes_its_own() {
        let a = bs("binsync install --name aib-title --file f --by @043dd --dest d").unwrap();
        assert_eq!(
            (a.name.as_deref(), a.file.as_deref(), a.by.as_deref()),
            (Some("aib-title"), Some("f"), Some("@043dd"))
        );
        assert!(bs("binsync rollback --name aib-title --by x --why because").is_ok());
        assert!(bs("binsync status --dest d --repo-dir r").is_ok());
        for (bad, what) in [
            ("binsync", "no_action"),
            ("binsync remove --name aib-title", "unknown_action"),
            (
                "binsync install --name aib-title --by x",
                "install_needs_file",
            ),
            (
                "binsync install --name aib-title --file f",
                "install_needs_by",
            ),
            (
                "binsync install --name aib-title --file f --by x --why because",
                "install_no_why",
            ),
            (
                "binsync rollback --name aib-title --by x",
                "rollback_needs_why",
            ),
            (
                "binsync rollback --name aib-title --by x --why because --file f",
                "rollback_no_file",
            ),
            (
                "binsync rollback --name aib-title --by x --why because --allow-downgrade xxxx",
                "rollback_no_allow",
            ),
            ("binsync status --name aib-title", "status_no_name"),
            ("binsync status --allow-downgrade xxxx", "status_no_allow"),
            (
                "binsync install --name --file f --by x",
                "flag_is_not_a_value",
            ),
            (
                "binsync install --name a --name aib-title --file f --by x",
                "twice",
            ),
            (
                "binsync install --name aib-title --file f --by a;b",
                "by_plain",
            ),
            (
                "binsync install --name aib-title --file f --by x --allow-downgrade no",
                "why_too_short",
            ),
            (
                "binsync install --name aib-title --file f --by x --what y",
                "unknown_flag",
            ),
        ] {
            assert!(bs(bad).is_err(), "{what}: {bad}");
        }
    }

    fn v(s: &str) -> Vec<String> {
        s.split_whitespace().map(str::to_owned).collect()
    }

    const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

    /// DEV 4.6 P1: an `--allow-…` flag carries its reason (it goes onto the approval), and
    /// what the host runs is said exactly one way.
    #[test]
    fn host_build_flags_need_their_reason_and_one_current() {
        let info = format!("version=1.0.0 commit={SHA} dirty=0");
        let base = |extra: &[&str]| {
            let mut a = vec![
                "host-build".to_owned(),
                "--candidate-info".to_owned(),
                info.clone(),
            ];
            a.extend(extra.iter().map(|s| (*s).to_owned()));
            a
        };
        let (_, req) = parse_host_build(&base(&["--current-unknown"])).unwrap();
        assert_eq!(req.main, "refs/remotes/origin/main");
        assert_eq!(req.current, host_build::Current::Unknown);
        let (_, req) = parse_host_build(&base(&[
            "--current-info",
            &info,
            "--allow-downgrade",
            "  roll back the 11-11 build ",
        ]))
        .unwrap();
        assert_eq!(
            req.allow_downgrade.as_deref(),
            Some("roll back the 11-11 build")
        );
        for bad in [
            base(&[]),
            base(&["--current-unknown", "--current-info", &info]),
            base(&["--current-unknown", "--allow-downgrade", ""]),
            base(&["--current-unknown", "--allow-downgrade", "ok"]),
            base(&["--current-unknown", "--allow-downgrade", "--x"]),
            // the main line cannot be named: a PR head given as main passed it (#650 M1)
            base(&["--current-unknown", "--main", "HEAD"]),
            base(&["--current-unknown", "--main", "refs/remotes/origin/main"]),
            base(&["--current-unknown", "--allow-unknown-current"]),
            base(&["--current-unknown", "--candidate-bin", "/x"]),
            base(&["--current-info", "commit=abc"]),
        ] {
            assert!(parse_host_build(&bad).is_err(), "{bad:?}");
        }
    }

    /// #650 L1 (4542e): the answer names the file it checked, by the sha256 of the bytes the
    /// build info was read from, so the swap step can install only that file.
    #[test]
    fn a_candidate_file_is_named_by_its_sha256() {
        let bytes = format!("\x7fELF\0aib-build-info version=1.0.0 commit={SHA} dirty=0;\0");
        let path = std::env::temp_dir().join(format!(
            "aib-release-cand-{}-{}",
            std::process::id(),
            line!()
        ));
        std::fs::write(&path, bytes.as_bytes()).unwrap();
        let argv: Vec<String> = [
            "host-build",
            "--candidate-bin",
            path.to_str().unwrap(),
            "--current-unknown",
        ]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
        let got = parse_host_build(&argv);
        let _ = std::fs::remove_file(&path);
        let (_, req) = got.unwrap();
        assert_eq!(
            req.candidate_sha256.as_deref(),
            Some(host_build::sha256_hex(bytes.as_bytes()).as_str())
        );
        let line = format!("version=1.0.0 commit={SHA} dirty=0");
        let argv: Vec<String> = ["host-build", "--candidate-info", &line, "--current-unknown"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert_eq!(parse_host_build(&argv).unwrap().1.candidate_sha256, None);
    }

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
