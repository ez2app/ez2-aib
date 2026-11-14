// SPDX-License-Identifier: Apache-2.0
//! Collects the facts for one sweep: git (fixed argv, deadline, no shell), open PRs through
//! `gh api` (GET only, aib-ghread), and the review / receipt files of the pending folder.
//! Anything that cannot be read goes into `errors` (the run is then not complete); it is never
//! guessed.

use crate::{
    parse_review_line, receipt_heads, receipt_nonzero, seat_of, Branch, OpenPr, PrInfo, Receipt,
    Report, Review,
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
fn read_pending(
    dir: &Path,
) -> (
    BTreeMap<String, Vec<Review>>,
    BTreeMap<String, Vec<Receipt>>,
) {
    let mut reviews: BTreeMap<String, Vec<(i64, Review)>> = BTreeMap::new();
    let mut receipts: BTreeMap<String, Vec<(i64, Receipt)>> = BTreeMap::new();
    let Ok(rd) = std::fs::read_dir(dir) else {
        return (BTreeMap::new(), BTreeMap::new());
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
        let Ok(text) = std::fs::read_to_string(e.path()) else {
            continue;
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
                    },
                ));
            }
        } else {
            let nonzero = receipt_nonzero(&text);
            for head in receipt_heads(&text) {
                receipts.entry(head.clone()).or_default().push((
                    mtime,
                    Receipt {
                        head,
                        file: name.clone(),
                        nonzero: nonzero.clone(),
                        at_head: false,
                    },
                ));
            }
        }
    }
    // newest file first
    let newest = |mut v: Vec<(i64, _)>| {
        v.sort_by(|a: &(i64, _), b: &(i64, _)| b.0.cmp(&a.0));
        v.into_iter().map(|x| x.1).collect::<Vec<_>>()
    };
    (
        reviews.into_iter().map(|(k, v)| (k, newest(v))).collect(),
        receipts.into_iter().map(|(k, v)| (k, newest(v))).collect(),
    )
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

fn open_prs(gh: &GhRead, repo: &str) -> Result<Vec<OpenPr>, String> {
    let v = gh.get(&format!("repos/{repo}/pulls?state=open&per_page=100"))?;
    let a = v.as_array().ok_or("open PRs: not a list")?;
    a.iter()
        .map(|p| {
            Some(OpenPr {
                number: p.get("number")?.as_u64()?,
                head_ref: p.pointer("/head/ref")?.as_str()?.to_owned(),
                head: p.pointer("/head/sha")?.as_str()?.to_owned(),
            })
        })
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| "open PRs: a row without number / head".to_owned())
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

/// `git merge-tree` exit 1 = conflict; 0 = clean; anything else is an error.
fn conflicts(git: &Git, a: &str, b: &str) -> Result<bool, String> {
    match git.run(&[
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        a,
        b,
    ]) {
        Ok(_) => Ok(false),
        Err(e) if e.contains(": exit 1:") || e.ends_with(": exit 1") => Ok(true),
        Err(e) => Err(e),
    }
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
    let mut prs = Vec::new();
    match (&gh, &repo) {
        (Some(gh), Some(repo)) => match open_prs(gh, repo) {
            Ok(p) => prs = p,
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

    let (reviews, receipts) = read_pending(&o.pending);
    let acks = read_acks(&o.pending);

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
            b.pr = Some(PrInfo {
                number: p.number,
                open: true,
                merged: false,
            });
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
        out.push(b);
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
                Ok(true) => b.conflicts_with.push(*n),
                Ok(false) => {}
                Err(e) => errors.push(format!("{}: merge-tree vs #{n}: {e}", b.branch)),
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
