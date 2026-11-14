// SPDX-License-Identifier: Apache-2.0
//! Collects the facts for one sweep: git (fixed argv, deadline, no shell), open PRs through
//! `gh api` (GET only, aib-ghread), and the review / receipt files of the pending folder.
//! Anything that cannot be read goes into `errors` (the run is then not complete); it is never
//! guessed.

use crate::{
    gate_files, not_gate_reason, parse_review_line, py_files, receipt_complete, receipt_heads,
    receipt_nonzero, receipt_sealed, receipt_steps, reviewer_of, seat_of, Branch, Clash, OpenPr,
    PrInfo, Receipt, Report, Review,
};
use aib_ghread::{run_fixed, GhRead, Read as _};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub struct Opts {
    pub repo: PathBuf,
    pub pending: PathBuf,
    pub seat: Option<String>,
    pub fetch: bool,
    /// `gh` program (tests point it at a fake); None = do not read GitHub (counted as an error)
    pub gh: Option<String>,
    pub now: i64,
    /// class 6 after this many hours without a commit (also used to pick the class 1 / 2 queue)
    pub hours: i64,
}

pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0))
}

struct Git {
    repo: PathBuf,
}

const GIT_TIMEOUT: Duration = Duration::from_secs(60);
const FETCH_TIMEOUT: Duration = Duration::from_secs(180);

impl Git {
    fn run_in(&self, dir: &Path, args: &[&str], t: Duration) -> Result<String, String> {
        let mut a = vec!["-C".to_owned(), dir.to_string_lossy().into_owned()];
        a.extend(args.iter().map(|s| (*s).to_owned()));
        run_fixed("git", &a, t).map(|b| String::from_utf8_lossy(&b).into_owned())
    }
    fn run(&self, args: &[&str]) -> Result<String, String> {
        self.run_in(&self.repo, args, GIT_TIMEOUT)
    }
}

/// Branch names the sweep covers on origin (besides every local branch).
const REMOTE_PREFIXES: [&str; 4] = ["integ-src/", "feat/", "fix/", "integ/"];

/// `owner/name` of a GitHub remote URL (https or ssh form).
pub fn gh_repo(url: &str) -> Option<String> {
    let url = url.trim();
    let rest = url
        .strip_prefix("https://github.com/")
        .or_else(|| url.strip_prefix("git@github.com:"))
        .or_else(|| url.strip_prefix("ssh://git@github.com/"))?;
    let rest = rest.strip_suffix(".git").unwrap_or(rest);
    let mut p = rest.split('/');
    let (o, n) = (p.next()?, p.next()?);
    let ok = |s: &str| {
        !s.is_empty()
            && s.bytes()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.'))
    };
    (ok(o) && ok(n) && p.next().is_none()).then(|| format!("{o}/{n}"))
}

/// Reviews (first-line format) and receipts in the pending folder, keyed by the head they name.
/// A folder or file that cannot be read goes into `errors` (043dd L2): with the folder unreadable
/// every branch would look unreviewed.
fn read_pending(
    dir: &Path,
    errors: &mut Vec<String>,
) -> (
    BTreeMap<String, Vec<Review>>,
    BTreeMap<String, Vec<Receipt>>,
) {
    let mut reviews: BTreeMap<String, Vec<(i64, Review)>> = BTreeMap::new();
    let mut receipts: BTreeMap<String, Vec<(i64, Receipt)>> = BTreeMap::new();
    let rd = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) => {
            errors.push(format!("pending {}: {e}", dir.display()));
            return (BTreeMap::new(), BTreeMap::new());
        }
    };
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let mtime = e
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
        let is_review = name.starts_with("REVIEW-") && name.ends_with(".md");
        let is_receipt = (name.starts_with("TEST-") && name.ends_with(".txt"))
            || (name.starts_with("RECEIPT-") && (name.ends_with(".txt") || name.ends_with(".log")));
        if !is_review && !is_receipt {
            continue;
        }
        // not UTF-8 ⇒ read lossily (a seal over it then does not match), not skipped
        let text = match std::fs::read(e.path()) {
            Ok(b) => String::from_utf8(b)
                .unwrap_or_else(|e| String::from_utf8_lossy(e.as_bytes()).into_owned()),
            Err(e) => {
                errors.push(format!("pending {name}: {e}"));
                continue;
            }
        };
        if is_review {
            let first = text.lines().next().unwrap_or_default();
            if let Some((verdict, head, [h, m, l, n])) = parse_review_line(first) {
                reviews.entry(head.clone()).or_default().push((
                    mtime,
                    Review {
                        head,
                        verdict,
                        h,
                        m,
                        l,
                        n,
                        file: name.clone(),
                        at_head: false,
                        unreviewed_after: Vec::new(),
                        reviewer: reviewer_of(&name),
                        not_gate_reason: not_gate_reason(&text),
                    },
                ));
            }
        } else {
            let nonzero = receipt_nonzero(&text);
            let steps = receipt_steps(&text);
            let sealed = receipt_sealed(&text);
            for head in receipt_heads(&text) {
                let complete = receipt_complete(&text, &head);
                receipts.entry(head.clone()).or_default().push((
                    mtime,
                    Receipt {
                        head,
                        file: name.clone(),
                        nonzero: nonzero.clone(),
                        at_head: false,
                        steps: steps.clone(),
                        sealed,
                        complete,
                    },
                ));
            }
        }
    }
    (
        reviews.into_iter().map(|(k, v)| (k, newest(v))).collect(),
        receipts.into_iter().map(|(k, v)| (k, newest(v))).collect(),
    )
}

/// Newest file first.
fn newest<T>(mut v: Vec<(i64, T)>) -> Vec<T> {
    v.sort_by_key(|x| std::cmp::Reverse(x.0));
    v.into_iter().map(|x| x.1).collect()
}

/// `<pending>/sweep-ack.txt`: `branch<TAB>reason` per line; `#` lines are comments.
fn read_acks(dir: &Path) -> BTreeMap<String, String> {
    std::fs::read_to_string(dir.join("sweep-ack.txt"))
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim_start().starts_with('#'))
        .filter_map(|l| l.split_once('\t'))
        .map(|(b, r)| (b.trim().to_owned(), r.trim().to_owned()))
        .filter(|(b, r)| !b.is_empty() && !r.is_empty())
        .collect()
}

fn text_at(v: &Value, ptr: &str) -> Option<String> {
    v.pointer(ptr).and_then(Value::as_str).map(str::to_owned)
}

/// `<pending>/build-claims.tsv`: `branch<TAB>seat<TAB>since<TAB>note` (Andy 11-14 「建置中」).
/// A line whose time cannot be read, or older than CLAIM_TTL_S, does not count.
fn read_claims(dir: &Path, now: i64) -> BTreeMap<String, (String, i64)> {
    std::fs::read_to_string(dir.join("build-claims.tsv"))
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim_start().starts_with('#'))
        .filter_map(|l| {
            let mut f = l.split('\t');
            let (b, seat, at) = (f.next()?.trim(), f.next()?.trim(), f.next()?);
            let at = crate::parse_time(at)?;
            (!b.is_empty() && !seat.is_empty() && now - at <= crate::CLAIM_TTL_S)
                .then(|| (b.to_owned(), (seat.to_owned(), at)))
        })
        .collect()
}

/// Open PRs, each with what the list gives (base, author).
/// Pages of 100 read for open PRs; a full last page is an error, not a silent cut (043dd L3).
const OPEN_PR_PAGES: u32 = 10;

fn open_prs(gh: &GhRead, repo: &str) -> Result<Vec<(OpenPr, PrInfo)>, String> {
    let mut a: Vec<Value> = Vec::new();
    for page in 1..=OPEN_PR_PAGES {
        let v = gh.get(&format!(
            "repos/{repo}/pulls?state=open&per_page=100&page={page}"
        ))?;
        let rows = v.as_array().ok_or("open PRs: not a list")?;
        let full = rows.len() >= 100;
        a.extend(rows.iter().cloned());
        if !full {
            break;
        }
        if page == OPEN_PR_PAGES {
            return Err(format!("open PRs: more than {} pages", OPEN_PR_PAGES));
        }
    }
    a.iter()
        .map(|p| {
            let number = p.get("number")?.as_u64()?;
            Some((
                OpenPr {
                    number,
                    head_ref: text_at(p, "/head/ref")?,
                    head: text_at(p, "/head/sha")?,
                },
                PrInfo {
                    number,
                    open: true,
                    base: text_at(p, "/base/ref"),
                    author: text_at(p, "/user/login"),
                    labels: labels(p),
                    ..PrInfo::default()
                },
            ))
        })
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| "open PRs: a row without number / head".to_owned())
}

/// Label names of a PR row.
fn labels(p: &Value) -> Vec<String> {
    p.get("labels")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|l| text_at(l, "/name")).collect())
        .unwrap_or_default()
}

/// `mergeable` (true / false / null) as GitHub's GraphQL words.
pub fn mergeable_word(v: &Value) -> &'static str {
    match v.as_bool() {
        Some(true) => "MERGEABLE",
        Some(false) => "CONFLICTING",
        None => "UNKNOWN",
    }
}

/// Check runs of one commit rolled up: (SUCCESS | FAILURE | PENDING, failed names); None when
/// there are none. failure / timed_out / action_required / startup_failure / cancelled = red.
pub fn rollup(v: &Value) -> Option<(String, Vec<String>)> {
    let runs = v.get("check_runs")?.as_array()?;
    if runs.is_empty() {
        return None;
    }
    let mut red = Vec::new();
    let mut pending = false;
    for r in runs {
        let name = r.get("name").and_then(Value::as_str).unwrap_or("?");
        if r.get("status").and_then(Value::as_str) != Some("completed") {
            pending = true;
            continue;
        }
        if matches!(
            r.get("conclusion").and_then(Value::as_str),
            Some("failure" | "timed_out" | "action_required" | "startup_failure" | "cancelled")
        ) && !red.iter().any(|x| x == name)
        {
            red.push(name.to_owned());
        }
    }
    let word = if !red.is_empty() {
        "FAILURE"
    } else if pending {
        "PENDING"
    } else {
        "SUCCESS"
    };
    Some((word.to_owned(), red))
}

/// Fills mergeable / merge_state (one PR GET) and checks / red_checks (check runs of the head).
fn pr_detail(gh: &GhRead, repo: &str, pr: &OpenPr, info: &mut PrInfo) -> Vec<String> {
    let mut errs = Vec::new();
    match gh.get(&format!("repos/{repo}/pulls/{}", pr.number)) {
        Ok(v) => {
            info.mergeable =
                Some(mergeable_word(v.get("mergeable").unwrap_or(&Value::Null)).to_owned());
            info.merge_state = text_at(&v, "/mergeable_state");
        }
        Err(e) => errs.push(format!("PR #{}: {e}", pr.number)),
    }
    match gh.get(&format!(
        "repos/{repo}/commits/{}/check-runs?per_page=100",
        pr.head
    )) {
        Ok(v) => {
            if let Some((w, red)) = rollup(&v) {
                info.checks = Some(w);
                info.red_checks = Some(red);
            } else if v.get("check_runs").is_some() {
                info.red_checks = Some(Vec::new());
            }
        }
        Err(e) => errs.push(format!("PR #{} checks: {e}", pr.number)),
    }
    errs
}

/// The newest closed PR whose head ref is `branch` (None = there is none).
fn closed_pr(gh: &GhRead, repo: &str, branch: &str) -> Result<Option<PrInfo>, String> {
    let owner = repo.split('/').next().unwrap_or_default();
    let v = gh.get(&format!(
        "repos/{repo}/pulls?state=closed&head={owner}:{branch}&per_page=5"
    ))?;
    let a = v.as_array().ok_or("closed PRs: not a list")?;
    Ok(a.iter()
        .filter_map(|p| {
            Some(PrInfo {
                number: p.get("number")?.as_u64()?,
                open: false,
                merged: !p.get("merged_at").is_none_or(Value::is_null),
                base: text_at(p, "/base/ref"),
                author: text_at(p, "/user/login"),
                labels: labels(p),
                head: text_at(p, "/head/sha"),
                ..PrInfo::default()
            })
        })
        .max_by_key(|p| p.number))
}

/// `+ <sha>` lines of `git cherry`: commits of `head` (after `limit`) with no equal patch in
/// `upstream`.
fn cherry_plus(git: &Git, upstream: &str, head: &str, limit: &str) -> Result<Vec<String>, String> {
    let out = git.run(&["cherry", upstream, head, limit])?;
    Ok(out
        .lines()
        .filter_map(|l| l.strip_prefix("+ "))
        .map(|s| s.trim().to_owned())
        .collect())
}

/// `git merge-tree --write-tree --name-only --no-messages a b`: exit 0 = clean, exit 1 =
/// conflict and its stdout names the files (after the tree id); anything else is an error.
/// Local only (no network), so a plain `output()` instead of run_fixed, which drops the stdout
/// of a non-zero exit.
fn conflicts(git: &Git, a: &str, b: &str) -> Result<Clash, String> {
    let o = std::process::Command::new("git")
        .arg("-C")
        .arg(&git.repo)
        .args([
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            a,
            b,
        ])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("git merge-tree not run: {}", e.kind()))?;
    match o.status.code() {
        Some(0) => Ok(Clash::default()),
        Some(1) => Ok(Clash {
            conflict: true,
            files: conflict_files(&String::from_utf8_lossy(&o.stdout)),
        }),
        c => Err(format!(
            "git merge-tree: exit {c:?}: {}",
            String::from_utf8_lossy(&o.stderr).trim()
        )),
    }
}

/// The file names merge-tree prints after the tree id (first line), deduplicated, in order.
pub fn conflict_files(stdout: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for l in stdout.lines().skip(1) {
        let l = l.trim();
        if l.is_empty() {
            break;
        }
        if !out.iter().any(|x| x == l) {
            out.push(l.to_owned());
        }
    }
    out
}

struct Ref {
    head: String,
    at: i64,
}

pub fn sweep(o: &Opts) -> Report {
    let git = Git {
        repo: o.repo.clone(),
    };
    let mut errors = Vec::new();
    if o.fetch {
        if let Err(e) = git.run_in(
            &o.repo,
            &["fetch", "--prune", "-q", "origin"],
            FETCH_TIMEOUT,
        ) {
            errors.push(format!("fetch: {e}"));
        }
    } else {
        errors.push("no_fetch".to_owned());
    }
    let main = match git.run(&["rev-parse", "--verify", "-q", "origin/main^{commit}"]) {
        Ok(s) => s.trim().to_owned(),
        Err(e) => {
            errors.push(format!("origin/main: {e}"));
            return Report {
                generated_at: o.now,
                main: String::new(),
                errors,
                open_prs: Vec::new(),
                branches: Vec::new(),
            };
        }
    };

    // local and origin branches
    let mut locals: BTreeMap<String, Ref> = BTreeMap::new();
    let mut remotes: BTreeMap<String, Ref> = BTreeMap::new();
    match git.run(&[
        "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(committerdate:unix)",
        "refs/heads",
        "refs/remotes/origin",
    ]) {
        Ok(out) => {
            for l in out.lines() {
                let mut f = l.split('\t');
                let (Some(r), Some(h), Some(t)) = (f.next(), f.next(), f.next()) else {
                    continue;
                };
                let rf = Ref {
                    head: h.to_owned(),
                    at: t.trim().parse().unwrap_or(0),
                };
                if let Some(n) = r.strip_prefix("refs/heads/") {
                    if n != "main" {
                        locals.insert(n.to_owned(), rf);
                    }
                } else if let Some(n) = r.strip_prefix("refs/remotes/origin/") {
                    if REMOTE_PREFIXES.iter().any(|p| n.starts_with(p)) {
                        remotes.insert(n.to_owned(), rf);
                    }
                }
            }
        }
        Err(e) => errors.push(format!("for-each-ref: {e}")),
    }
    let names: BTreeSet<String> = locals.keys().chain(remotes.keys()).cloned().collect();

    // worktrees
    let mut trees: BTreeMap<String, String> = BTreeMap::new();
    match git.run(&["worktree", "list", "--porcelain"]) {
        Ok(out) => {
            let mut path = None;
            for l in out.lines() {
                if let Some(p) = l.strip_prefix("worktree ") {
                    path = Some(p.to_owned());
                } else if let Some(b) = l.strip_prefix("branch refs/heads/") {
                    if let Some(p) = path.take() {
                        trees.insert(b.to_owned(), p);
                    }
                }
            }
        }
        Err(e) => errors.push(format!("worktree list: {e}")),
    }

    // open PRs
    let gh = o.gh.as_ref().map(|p| GhRead {
        program: p.clone(),
        timeout: aib_ghread::CALL_TIMEOUT,
    });
    let repo = git
        .run(&["remote", "get-url", "origin"])
        .ok()
        .and_then(|u| gh_repo(&u));
    let mut prs: Vec<OpenPr> = Vec::new();
    let mut infos: BTreeMap<u64, PrInfo> = BTreeMap::new();
    match (&gh, &repo) {
        (Some(gh), Some(repo)) => match open_prs(gh, repo) {
            Ok(p) => {
                for (pr, mut info) in p {
                    errors.extend(pr_detail(gh, repo, &pr, &mut info));
                    infos.insert(pr.number, info);
                    prs.push(pr);
                }
            }
            Err(e) => errors.push(format!("gh: {e}")),
        },
        (None, _) => errors.push("no_gh".to_owned()),
        (_, None) => errors.push("origin is not a GitHub remote".to_owned()),
    }
    // PR heads must be readable locally to compare against them
    let mut pr_heads: Vec<(u64, String)> = Vec::new();
    for p in &prs {
        let have = git
            .run(&["cat-file", "-e", &format!("{}^{{commit}}", p.head)])
            .is_ok();
        if !have
            && git
                .run_in(
                    &o.repo,
                    &["fetch", "-q", "origin", &format!("pull/{}/head", p.number)],
                    FETCH_TIMEOUT,
                )
                .is_err()
        {
            errors.push(format!("PR #{}: head {} not readable", p.number, p.head));
            continue;
        }
        pr_heads.push((p.number, p.head.clone()));
    }

    let (reviews, receipts) = read_pending(&o.pending, &mut errors);
    let acks = read_acks(&o.pending);
    let claims = read_claims(&o.pending, o.now);

    let mut out: Vec<Branch> = Vec::new();
    for name in &names {
        let seat = seat_of(name);
        if let Some(want) = &o.seat {
            if seat.as_deref() != Some(want.as_str()) {
                continue;
            }
        }
        let (local, remote) = (locals.get(name), remotes.get(name));
        let Some(r) = local.or(remote) else {
            continue;
        };
        let mut b = Branch {
            branch: name.clone(),
            seat,
            head: r.head.clone(),
            local: local.is_some(),
            remote: remote.map(|_| format!("origin/{name}")),
            worktree: trees.get(name).cloned(),
            last_commit_at: r.at,
            age_h: (o.now - r.at).max(0) / 3600,
            acked: acks.get(name).cloned(),
            claimed_by: claims.get(name).cloned(),
            ..Branch::default()
        };
        let plus = match cherry_plus(&git, &main, &b.head, &main) {
            Ok(p) => p,
            Err(e) => {
                errors.push(format!("{name}: cherry: {e}"));
                continue;
            }
        };
        b.unique = plus.len();
        if plus.is_empty() {
            b.unique_not_in_prs = 0;
            out.push(b);
            continue;
        }
        // which open PRs already hold every one of these patches
        let mut left: BTreeSet<String> = plus.iter().cloned().collect();
        for (n, h) in &pr_heads {
            if h == &b.head {
                continue;
            }
            match cherry_plus(&git, h, &b.head, &main) {
                Ok(p) => {
                    let p: BTreeSet<String> = p.into_iter().collect();
                    let held = left.iter().filter(|c| !p.contains(*c)).count();
                    if held > 0 {
                        left.retain(|c| p.contains(c));
                        b.in_prs.push(*n);
                    }
                }
                Err(e) => errors.push(format!("{name}: cherry vs PR #{n}: {e}")),
            }
        }
        b.unique_not_in_prs = left.len();
        // where the head is pushed
        match git.run(&[
            "for-each-ref",
            "--contains",
            &b.head,
            "--format=%(refname:short)",
            "refs/remotes/origin",
        ]) {
            Ok(s) => {
                b.pushed_to = s
                    .lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty() && *l != "origin/HEAD" && *l != "origin")
                    .map(str::to_owned)
                    .collect();
            }
            Err(e) => errors.push(format!("{name}: --contains: {e}")),
        }
        // PR: an open one whose head ref is this branch or where it was pushed; else the newest
        // closed one with this name
        let pushed_names: Vec<&str> = b
            .pushed_to
            .iter()
            .filter_map(|p| p.strip_prefix("origin/"))
            .collect();
        if let Some(p) = prs.iter().find(|p| {
            p.head_ref == *name || (p.head == b.head && pushed_names.contains(&p.head_ref.as_str()))
        }) {
            b.pr = infos.get(&p.number).cloned();
        } else if let (Some(gh), Some(repo)) = (&gh, &repo) {
            match closed_pr(gh, repo, name) {
                Ok(p) => b.pr = p,
                Err(e) => errors.push(format!("{name}: {e}")),
            }
        }
        // commits on this branch after main, newest first (reviews / receipts / stacking)
        let commits: Vec<String> = match git.run(&["rev-list", &format!("{main}..{}", b.head)]) {
            Ok(s) => s.lines().map(str::to_owned).collect(),
            Err(e) => {
                errors.push(format!("{name}: rev-list: {e}"));
                Vec::new()
            }
        };
        b.review = commits.iter().find_map(|c| {
            reviews.get(c).and_then(|v| v.first()).map(|r| Review {
                at_head: *c == b.head,
                ..r.clone()
            })
        });
        if let Some(r) = b.review.as_mut().filter(|r| !r.at_head) {
            match git.run(&[
                "rev-list",
                "--no-merges",
                &format!("{}..{}", r.head, b.head),
            ]) {
                Ok(s) => r.unreviewed_after = s.lines().map(str::to_owned).collect(),
                Err(e) => errors.push(format!("{name}: rev-list after review: {e}")),
            }
        }
        b.receipt = commits.iter().find_map(|c| {
            receipts.get(c).and_then(|v| v.first()).map(|r| Receipt {
                at_head: *c == b.head,
                ..r.clone()
            })
        });
        let mine: BTreeSet<&str> = commits.iter().map(String::as_str).collect();
        b.stacked_on = names
            .iter()
            .filter(|n| *n != name)
            .filter(|n| {
                locals
                    .get(*n)
                    .or(remotes.get(*n))
                    .is_some_and(|r| r.head != b.head && mine.contains(r.head.as_str()))
            })
            .cloned()
            .collect();
        if let Some(t) = &b.worktree {
            match git.run_in(
                Path::new(t),
                &["status", "--porcelain", "--untracked-files=no"],
                GIT_TIMEOUT,
            ) {
                Ok(s) => b.dirty = Some(!s.trim().is_empty()),
                Err(e) => errors.push(format!("{name}: status: {e}")),
            }
        }
        // --no-renames: a workflow moved out of .github/workflows/ lists its old name too
        match git.run(&[
            "diff",
            "--name-only",
            "--no-renames",
            &format!("{main}...{}", b.head),
        ]) {
            Ok(s) => {
                let files: Vec<String> = s.lines().map(str::to_owned).collect();
                b.gate_files = gate_files(&files);
                b.py_files = py_files(&files);
            }
            Err(e) => errors.push(format!("{name}: diff: {e}")),
        }
        if b.acked.is_none() {
            match conflicts(&git, &main, &b.head) {
                Ok(c) => b.conflicts_with_main = Some(c),
                Err(e) => errors.push(format!("{name}: merge-tree vs main: {e}")),
            }
        }
        out.push(b);
    }

    // stack top: nobody listed is stacked on it
    let below: BTreeSet<String> = out.iter().flat_map(|b| b.stacked_on.clone()).collect();
    for b in out.iter_mut() {
        b.stack_top = !below.contains(&b.branch);
    }

    // conflicts: only for branches a merge could be waiting on (open PR, or a review / receipt
    // at head), against every other open PR
    for b in out.iter_mut() {
        let candidate = b.unique_not_in_prs > 0
            && (b.pr.as_ref().is_some_and(|p| p.open)
                || b.review.as_ref().is_some_and(|r| r.at_head)
                || b.receipt.as_ref().is_some_and(|r| r.at_head));
        if !candidate || b.acked.is_some() {
            continue;
        }
        for (n, h) in &pr_heads {
            if b.pr.as_ref().is_some_and(|p| p.number == *n) || *h == b.head {
                continue;
            }
            match conflicts(&git, h, &b.head) {
                Ok(c) if c.conflict => b.conflicts_with.push((*n, c.files)),
                Ok(_) => {}
                Err(e) => errors.push(format!("{}: merge-tree vs #{n}: {e}", b.branch)),
            }
        }
    }

    // class 1 / 2 branches against each other: what would clash once one of them is merged
    let queue: Vec<(usize, String)> = out
        .iter()
        .enumerate()
        .filter(|(_, b)| matches!(crate::classify(b, o.hours).0, 1 | 2))
        .map(|(i, b)| (i, b.head.clone()))
        .collect();
    for (x, (i, hi)) in queue.iter().enumerate() {
        for (j, hj) in queue.iter().skip(x + 1) {
            if hi == hj {
                continue;
            }
            match conflicts(&git, hi, hj) {
                Ok(c) if c.conflict => {
                    let (ni, nj) = (out[*i].branch.clone(), out[*j].branch.clone());
                    out[*i].conflicts_with_branches.push((nj, c.files.clone()));
                    out[*j].conflicts_with_branches.push((ni, c.files));
                }
                Ok(_) => {}
                Err(e) => errors.push(format!("merge-tree {hi} vs {hj}: {e}")),
            }
        }
    }

    Report {
        generated_at: o.now,
        main,
        errors,
        open_prs: prs,
        branches: out,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn github_remotes_name_their_repo() {
        assert_eq!(
            gh_repo("https://github.com/ez2app/ez2-aib.git").as_deref(),
            Some("ez2app/ez2-aib")
        );
        assert_eq!(
            gh_repo("git@github.com:ez2app/ez2-aib.git\n").as_deref(),
            Some("ez2app/ez2-aib")
        );
        assert_eq!(gh_repo("ssh://git@github.com/o/r").as_deref(), Some("o/r"));
        assert_eq!(gh_repo("https://example.com/o/r.git"), None);
        assert_eq!(gh_repo("https://github.com/o/r/x"), None);
        assert_eq!(gh_repo("https://github.com/o/r&x=1"), None);
    }
}
