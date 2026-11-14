// SPDX-License-Identifier: Apache-2.0
//! aib-public-leak — RULES §16 gate (approvals-to-rust C10; becomes `aib-verify public-leak`
//! once aib-verify is on main).
//!
//!   aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist] [--full-read]
//!   aib-public-leak --local-sources (--pre-push [--remote NAME] | --base REV [--head REV] | --text-file FILE) [--repo DIR]
//!                   [--denylist-file FILE] [--hosts-json FILE] [--full-read]
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
//! `--attr-source` needs git 2.41; with an older git (Apple Git 2.39 on macOS 15), an unknown
//! version, or `--full-read`, every file is read as text instead (`full_read` in the summary):
//! stricter, never looser (DECISION-690-old-git).
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

use aib_public_leak::local::{
    added_against_all, hosts_entries, parse_pre_push, parse_strict, PushRef,
};
use aib_public_leak::{
    diff_args, git_version, has_attr_source, parse_diff, parse_log, pr_text, report_line, scan,
    Denylist, Finding, Input, DENYLIST_ENV,
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
    /// pre-push `$1`: the remote name, or the URL when the push names no remote
    remote: Option<String>,
    text_file: Option<PathBuf>,
    denylist_file: Option<PathBuf>,
    hosts_json: Option<PathBuf>,
    install_hook: bool,
    full_read: bool,
}

const USAGE: &str = "usage: aib-public-leak --base REV [--head REV] [--repo DIR] [--github-event FILE] [--require-denylist] [--full-read]
       aib-public-leak --install-hook [--repo DIR]
       aib-public-leak --local-sources (--pre-push [--remote NAME] | --base REV [--head REV] | --text-file FILE) [--repo DIR] [--denylist-file FILE] [--hosts-json FILE] [--full-read]";

fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args {
        base: String::new(),
        head: "HEAD".into(),
        repo: PathBuf::from("."),
        event: None,
        require_denylist: false,
        local: false,
        pre_push: false,
        remote: None,
        text_file: None,
        denylist_file: None,
        hosts_json: None,
        install_hook: false,
        full_read: false,
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
            "--remote" => a.remote = Some(v()?),
            "--text-file" => a.text_file = Some(PathBuf::from(v()?)),
            "--denylist-file" => a.denylist_file = Some(PathBuf::from(v()?)),
            "--hosts-json" => a.hosts_json = Some(PathBuf::from(v()?)),
            "--full-read" => a.full_read = true,
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
    if a.remote.is_some() && !a.pre_push {
        return Err("--remote goes with --pre-push".into());
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
    // a file read as text may hold any bytes: lossy, never a failure (DECISION-690-old-git)
    Ok(String::from_utf8_lossy(&o.stdout).into_owned())
}

/// What was read, how many files the base's attributes kept as binary (not read), and whether
/// attributes came from BASE (false = every file read as text).
fn gather(a: &Args) -> Result<(Input, usize, bool), String> {
    let range3 = format!("{}...{}", a.base, a.head);
    let range2 = format!("{}..{}", a.base, a.head);
    // attributes from BASE, never from the PR's own tree; old git ⇒ every file as text
    let attr_source = attr_source_usable(a);
    let args = diff_args(&a.base, &range3, attr_source);
    let argv: Vec<&str> = args.iter().map(String::as_str).collect();
    let diff = git(&a.repo, &argv)?;
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
        attr_source,
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

/// Whether this git can take attributes from a base (`--attr-source`, git 2.41+) and
/// `--full-read` was not asked for; same test as `gather`.
fn attr_source_usable(a: &Args) -> bool {
    let version = git(&a.repo, &["version"]).ok();
    !a.full_read && has_attr_source(version.as_deref().and_then(git_version))
}

/// The remote a pre-push goes to: `$1` when it is a configured remote's name. A URL (no
/// name), a name with glob characters or none at all ⇒ `None`: no remote refs are taken as
/// already public.
fn push_remote(a: &Args) -> Option<String> {
    let r = a
        .remote
        .as_deref()
        .filter(|r| !r.is_empty() && !r.contains(['*', '?', '[', '\\']))?;
    let names = git(&a.repo, &["remote"]).ok()?;
    names.lines().any(|l| l.trim() == r).then(|| r.to_string())
}

const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

/// Every commit a pre-push sends, each with its own added lines and message.
/// Returns (commit sha, its input, binary files not read).
fn pre_push_inputs(
    a: &Args,
    refs: &[PushRef],
    attr_source: bool,
) -> Result<Vec<(String, Input, usize)>, String> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let remote = push_remote(a);
    // only the refs of the remote pushed to count as already public (043dd review of
    // 173f0651 M1): other refs/remotes/** may hold a seat's bundle that never reached it
    let remotes = remote.as_ref().map(|n| format!("--remotes={n}"));
    let main = remote
        .as_ref()
        .map_or_else(|| "origin/main".to_string(), |n| format!("{n}/main"));
    for r in refs.iter().filter(|r| !r.is_delete()) {
        if r.is_new() && remote.is_none() {
            // a URL push has no remote refs to stand on: everything would be read from the root
            return Err(
                "a new branch pushed to a URL (no remote name): push to a named remote".into(),
            );
        }
        // attributes from what the remote already has, never from the pushed commits
        let attr_base = if r.is_new() {
            let b = git(&a.repo, &["merge-base", &r.local_sha, &main])
                .map_err(|e| format!("{e}: a new branch needs {main} to read attributes from"))?;
            b.trim().to_string()
        } else {
            r.remote_sha.clone()
        };
        // only commits that remote has not got yet (7b81e, review of 86ea06b9 M1):
        // `<remote>..<local>` after merging main also lists every main commit merged in,
        // already public and already checked by CI. An update also leaves out what that remote
        // ref already has (it is the attribute base, so it must exist here anyway)
        let mut rl = vec!["rev-list", r.local_sha.as_str(), "--not"];
        if let Some(x) = &remotes {
            rl.push(x.as_str());
        }
        if !r.is_new() {
            rl.push(r.remote_sha.as_str());
        }
        let list = git(&a.repo, &rl)?;
        for c in list.lines().map(str::trim).filter(|c| !c.is_empty()) {
            if !seen.insert(c.to_string()) {
                continue;
            }
            let parents = git(&a.repo, &["rev-list", "--parents", "-n", "1", c])?;
            let mut parents: Vec<&str> = parents.split_whitespace().skip(1).collect();
            if parents.is_empty() {
                parents.push(EMPTY_TREE);
            }
            // one diff per parent; a merge keeps only the lines new against all (M2)
            let (mut diffs, mut binary) = (Vec::new(), usize::MAX);
            for p in &parents {
                // same diff as CI (a9136 DECISION-690-old-git): old git ⇒ every file as text
                let args = diff_args(&attr_base, &format!("{p}..{c}"), attr_source);
                let argv: Vec<&str> = args.iter().map(String::as_str).collect();
                let diff = git(&a.repo, &argv)?;
                let b = diff
                    .lines()
                    .filter(|l| l.starts_with("Binary files "))
                    .count();
                binary = binary.min(b);
                diffs.push(parse_diff(&diff));
            }
            let log = git(
                &a.repo,
                &["log", "-n", "1", "--format=%H%x00%B%x1e", c, "--"],
            )?;
            out.push((
                c.to_string(),
                Input {
                    files: added_against_all(diffs),
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
        let attr_source = attr_source_usable(a);
        let inputs = match pre_push_inputs(a, &refs, attr_source) {
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
                "checked {} ref(s), {} commit(s) one by one, {lines} added line(s), denylist {}, {binary} binary file(s) not read{}",
                refs.len(),
                inputs.len(),
                entries(deny.len()),
                if attr_source { "" } else { ", full_read" }
            ),
            &deny,
            true,
        );
    }
    let (input, binary, attr_source) = match gather(a) {
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
            "checked {} file(s), {lines} added line(s), {} commit(s), denylist {}, {binary} binary file(s) not read{}",
            input.files.len(),
            input.commits.len(),
            entries(deny.len()),
            if attr_source { "" } else { ", full_read" }
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
         exec \"$B\" --local-sources --pre-push --remote \"$1\" --repo \"$(git rev-parse --show-toplevel)\"\n"
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
    let (input, binary, attr_source) = match gather(&a) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("aib-public-leak: {e}");
            return ExitCode::from(2);
        }
    };
    if !attr_source {
        let why = if a.full_read {
            "--full-read"
        } else {
            "attr_source_unsupported: git < 2.41 or version unknown"
        };
        println!("public-leak: full_read ({why}): every file read as text");
    }
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
