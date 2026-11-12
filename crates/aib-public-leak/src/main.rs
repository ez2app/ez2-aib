// SPDX-License-Identifier: Apache-2.0
//! aib-public-leak — RULES §16 gate (approvals-to-rust C10; becomes `aib-verify public-leak`
//! once aib-verify is on main).
//!
//!   aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist]
//!   aib-public-leak --local-sources (--pre-push | --base REV [--head REV] | --text-file FILE) [--repo DIR]
//!                   [--denylist-file FILE] [--hosts-json FILE]
//!   aib-public-leak --install-hook [--repo DIR]
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
//! Local mode (`--local-sources`, 建議表 #12): the denylist is the private file
//! (default ~/.ez2-aib/public-denylist, same format as the CI secret, read strictly; `word:`
//! lines match on ASCII word boundaries) plus `hosts.json` server names and ssh hosts. A
//! missing, empty or unreadable list is exit 3: the local check never runs on the IPv4 rule
//! alone. `--pre-push` reads a pre-push hook's standard input and checks every pushed ref
//! commit by commit (added lines and message of each commit, so a word added and removed again
//! in the same push is still stopped). `--text-file` checks a PR title / body draft.
//!
//! `--install-hook` writes the repository's pre-push hook (hooks are shared by every worktree
//! of a clone, so once per clone): a short shim that runs this binary, by its absolute path,
//! in `--local-sources --pre-push` mode, and blocks the push when the binary is gone. A
//! pre-push hook that is not ours is left alone (exit 2).
//!
//! Exit: 0 clean, 1 findings, 2 usage or a git / file read failed, 3 denylist required but absent
//! or not understood.

use aib_public_leak::local::{hosts_entries, parse_pre_push, parse_strict, PushRef};
use aib_public_leak::{
    parse_diff, parse_log, pr_text, report_line, scan, Denylist, Finding, Input, DENYLIST_ENV,
};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

struct Args {
    base: String,
    head: String,
    repo: PathBuf,
    event: Option<PathBuf>,
    require_denylist: bool,
    local: bool,
    pre_push: bool,
    text_file: Option<PathBuf>,
    denylist_file: Option<PathBuf>,
    hosts_json: Option<PathBuf>,
    install_hook: bool,
}

const USAGE: &str = "usage: aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist]
       aib-public-leak --install-hook [--repo DIR]
       aib-public-leak --local-sources (--pre-push | --base REV [--head REV] | --text-file FILE) [--repo DIR] [--denylist-file FILE] [--hosts-json FILE]";

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args {
        base: String::new(),
        head: "HEAD".into(),
        repo: PathBuf::from("."),
        event: None,
        require_denylist: false,
        local: false,
        pre_push: false,
        text_file: None,
        denylist_file: None,
        hosts_json: None,
        install_hook: false,
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
            "--local-sources" => a.local = true,
            "--install-hook" => a.install_hook = true,
            "--pre-push" => a.pre_push = true,
            "--text-file" => a.text_file = Some(PathBuf::from(v()?)),
            "--denylist-file" => a.denylist_file = Some(PathBuf::from(v()?)),
            "--hosts-json" => a.hosts_json = Some(PathBuf::from(v()?)),
            _ => return Err(format!("unknown argument {k}")),
        }
    }
    if (a.pre_push || a.text_file.is_some() || a.denylist_file.is_some() || a.hosts_json.is_some())
        && !a.local
    {
        return Err(
            "--pre-push, --text-file, --denylist-file and --hosts-json need --local-sources".into(),
        );
    }
    if a.install_hook {
        if a.local || a.pre_push || !a.base.is_empty() || a.text_file.is_some() || a.event.is_some()
        {
            return Err("--install-hook takes only --repo".into());
        }
        return Ok(a);
    }
    let modes = [a.pre_push, !a.base.is_empty(), a.text_file.is_some()];
    if modes.iter().filter(|m| **m).count() != 1 {
        return Err("give exactly one of --base, --pre-push, --text-file".into());
    }
    if a.local && a.event.is_some() {
        return Err("--github-event is for CI; locally use --text-file".into());
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
            text: None,
        },
        binary,
    ))
}

/// Local mode's denylist: the private file (strict) plus hosts.json. Err((exit code, why)).
fn local_denylist(a: &Args) -> Result<Denylist, (u8, String)> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    let ez = home.map(|h| h.join(".ez2-aib"));
    let file = a
        .denylist_file
        .clone()
        .or_else(|| ez.as_ref().map(|e| e.join("public-denylist")))
        .ok_or((3, "no home directory: denylist file unknown".to_string()))?;
    let text = std::fs::read_to_string(&file)
        .map_err(|e| (3, format!("denylist file {}: {e}", file.display())))?;
    let mut deny = parse_strict(&text).map_err(|e| (3, e))?;
    if deny.is_empty() {
        return Err((
            3,
            format!("denylist file {} has no entries", file.display()),
        ));
    }
    let hosts = a
        .hosts_json
        .clone()
        .or_else(|| ez.as_ref().map(|e| e.join("hosts.json")));
    if let Some(h) = hosts {
        match std::fs::read_to_string(&h) {
            Ok(j) => {
                let extra = hosts_entries(&j).map_err(|e| (3, e))?;
                deny =
                    parse_strict(&format!("{text}\n{}", extra.join("\n"))).map_err(|e| (3, e))?;
            }
            // only an addition: an absent file is said, not an error
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                println!("public-leak: hosts.json not found: private denylist only");
            }
            Err(e) => return Err((2, format!("{}: {e}", h.display()))),
        }
    }
    Ok(deny)
}

const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

/// Every commit a pre-push sends, each with its own added lines and message.
/// Returns (commit sha, its input, binary files not read).
fn pre_push_inputs(a: &Args, refs: &[PushRef]) -> Result<Vec<(String, Input, usize)>, String> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for r in refs.iter().filter(|r| !r.is_delete()) {
        // attributes from what the remote already has, never from the pushed commits
        let (list, attr_base) = if r.is_new() {
            let b = git(&a.repo, &["merge-base", &r.local_sha, "origin/main"]).map_err(|e| {
                format!("{e}: a new branch needs origin/main to read attributes from")
            })?;
            (
                git(&a.repo, &["rev-list", &r.local_sha, "--not", "--remotes"])?,
                b.trim().to_string(),
            )
        } else {
            (
                git(
                    &a.repo,
                    &["rev-list", &format!("{}..{}", r.remote_sha, r.local_sha)],
                )?,
                r.remote_sha.clone(),
            )
        };
        let attr = format!("--attr-source={attr_base}");
        for c in list.lines().map(str::trim).filter(|c| !c.is_empty()) {
            if !seen.insert(c.to_string()) {
                continue;
            }
            let parents = git(&a.repo, &["rev-list", "--parents", "-n", "1", c])?;
            let parent = parents
                .split_whitespace()
                .nth(1)
                .unwrap_or(EMPTY_TREE)
                .to_string();
            let diff = git(
                &a.repo,
                &[
                    &attr,
                    "diff",
                    "--no-color",
                    "--no-ext-diff",
                    "--no-renames",
                    "-U0",
                    &parent,
                    c,
                    "--",
                ],
            )?;
            let log = git(
                &a.repo,
                &["log", "-n", "1", "--format=%H%x00%B%x1e", c, "--"],
            )?;
            let binary = diff
                .lines()
                .filter(|l| l.starts_with("Binary files "))
                .count();
            out.push((
                c.to_string(),
                Input {
                    files: parse_diff(&diff),
                    commits: parse_log(&log),
                    ..Input::default()
                },
                binary,
            ));
        }
    }
    Ok(out)
}

/// Prints the findings, then the summary; returns the exit code.
fn finish(
    found: &[(Option<String>, Finding)],
    summary: String,
    deny: &Denylist,
    require: bool,
) -> ExitCode {
    for (c, f) in found {
        match c {
            Some(c) => println!("{} (in commit {})", report_line(f), &c[..c.len().min(12)]),
            None => println!("{}", report_line(f)),
        }
    }
    println!("public-leak: {} finding(s); {summary}", found.len());
    if !found.is_empty() {
        ExitCode::from(1)
    } else if deny.is_empty() && require {
        // the denylist rule did not run: not checked is not clean
        println!("public-leak: denylist required (--require-denylist) but absent: not passed");
        ExitCode::from(3)
    } else {
        ExitCode::SUCCESS
    }
}

fn entries(n: usize) -> String {
    format!("{n} entr{}", if n == 1 { "y" } else { "ies" })
}

fn run_local(a: &Args) -> ExitCode {
    let deny = match local_denylist(a) {
        Ok(d) => d,
        Err((rc, e)) => {
            eprintln!("aib-public-leak: {e}");
            if rc == 3 {
                println!("public-leak: local denylist missing or not understood: not passed");
            }
            return ExitCode::from(rc);
        }
    };
    if let Some(p) = &a.text_file {
        let text = match std::fs::read_to_string(p) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("aib-public-leak: {}: {e}", p.display());
                return ExitCode::from(2);
            }
        };
        let input = Input {
            text: Some(text),
            ..Input::default()
        };
        let lines = input.text.as_deref().map_or(0, |t| t.lines().count());
        let found: Vec<_> = scan(&input, &deny).into_iter().map(|f| (None, f)).collect();
        return finish(
            &found,
            format!(
                "checked text file, {lines} line(s), denylist {}",
                entries(deny.len())
            ),
            &deny,
            true,
        );
    }
    if a.pre_push {
        let mut stdin = String::new();
        if let Err(e) = std::io::stdin().read_to_string(&mut stdin) {
            eprintln!("aib-public-leak: stdin: {e}");
            return ExitCode::from(2);
        }
        let refs = match parse_pre_push(&stdin) {
            Ok(r) => r,
            Err(e) => {
                eprintln!("aib-public-leak: {e}");
                return ExitCode::from(2);
            }
        };
        let inputs = match pre_push_inputs(a, &refs) {
            Ok(i) => i,
            Err(e) => {
                eprintln!("aib-public-leak: {e}");
                return ExitCode::from(2);
            }
        };
        let mut found = Vec::new();
        let (mut lines, mut binary) = (0, 0);
        for (c, input, b) in &inputs {
            binary += b;
            lines += input.files.iter().map(|f| f.lines.len()).sum::<usize>();
            found.extend(scan(input, &deny).into_iter().map(|f| (Some(c.clone()), f)));
        }
        return finish(
            &found,
            format!(
                "checked {} ref(s), {} commit(s) one by one, {lines} added line(s), denylist {}, {binary} binary file(s) not read",
                refs.len(),
                inputs.len(),
                entries(deny.len())
            ),
            &deny,
            true,
        );
    }
    let (input, binary) = match gather(a) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("aib-public-leak: {e}");
            return ExitCode::from(2);
        }
    };
    let lines: usize = input.files.iter().map(|f| f.lines.len()).sum();
    let found: Vec<_> = scan(&input, &deny).into_iter().map(|f| (None, f)).collect();
    finish(
        &found,
        format!(
            "checked {} file(s), {lines} added line(s), {} commit(s), denylist {}, {binary} binary file(s) not read",
            input.files.len(),
            input.commits.len(),
            entries(deny.len())
        ),
        &deny,
        true,
    )
}

/// Marks a pre-push hook written by `--install-hook`; only such a hook is replaced.
const HOOK_MARK: &str = "# aib-public-leak pre-push (RULES §16, #12)";

/// The pre-push shim for the binary at `exe`.
fn hook_text(exe: &str) -> String {
    let q = exe.replace('\'', r"'\''");
    format!(
        "#!/bin/sh\n{HOOK_MARK}\n# written by aib-public-leak --install-hook; a missing binary blocks the push.\n\
         B='{q}'\n\
         [ -x \"$B\" ] || {{ echo \"pre-push: $B not found: push blocked (RULES §16)\" >&2; exit 1; }}\n\
         exec \"$B\" --local-sources --pre-push --repo \"$(git rev-parse --show-toplevel)\"\n"
    )
}

fn install_hook(a: &Args) -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("own path: {e}"))?;
    let exe = exe.to_str().ok_or("own path is not UTF-8")?.to_string();
    let dir = git(
        &a.repo,
        &["rev-parse", "--path-format=absolute", "--git-path", "hooks"],
    )?;
    let dir = PathBuf::from(dir.trim());
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let hook = dir.join("pre-push");
    match std::fs::read_to_string(&hook) {
        Ok(old) if !old.contains(HOOK_MARK) => {
            return Err(format!(
                "{} exists and is not ours: left alone; call this binary from it instead",
                hook.display()
            ))
        }
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("{}: {e}", hook.display())),
    }
    std::fs::write(&hook, hook_text(&exe)).map_err(|e| format!("{}: {e}", hook.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&hook, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("{}: {e}", hook.display()))?;
    }
    Ok(hook)
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
    if a.install_hook {
        return match install_hook(&a) {
            Ok(h) => {
                println!("public-leak: pre-push hook written: {}", h.display());
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("aib-public-leak: {e}");
                ExitCode::from(2)
            }
        };
    }
    if a.local {
        return run_local(&a);
    }
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
