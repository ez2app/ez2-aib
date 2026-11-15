// SPDX-License-Identifier: Apache-2.0
//! Collects the facts for one sweep: git (fixed argv, deadline, no shell), open PRs through
//! `gh api` (GET only, aib-ghread), and the review / receipt files of the pending folder.
//! Anything that cannot be read goes into `errors` (the run is then not complete); it is never
//! guessed.

use crate::{
    diff_names, gate_files, not_gate_reason, parse_review_line, py_files, receipt_complete,
    receipt_heads, receipt_nonzero, receipt_sealed, receipt_steps, reviewer_of, seat_of, Branch,
    Clash, OpenPr, PrInfo, Receipt, Report, Resolved, Review,
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
    /// read only branches with an open PR or a commit in the last `hours`; closed PRs come
    /// from one list of the recently changed ones instead of one question per branch
    pub fast: bool,
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
        let is_receipt = crate::is_receipt_name(&name);
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
                let scope = crate::review_scope(first, &head, &name);
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
                        resolved_after_review: None,
                        reviewer: reviewer_of(&name),
                        not_gate_reason: not_gate_reason(&text),
                        scope,
                    },
                ));
            }
        } else {
            let mut nonzero = receipt_nonzero(&text);
            // 519d0: receipt-run.sh touches `<run-id>.red` beside a cut-short `<run-id>.out`
            let red_file = name
                .rsplit_once('.')
                .is_some_and(|(stem, _)| dir.join(format!("{stem}.red")).exists());
            if red_file {
                nonzero.push("red_file".to_owned());
            }
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
                        stacked_at: None,
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

/// Rows per page of a PR list. Small on purpose (519d0 11-15): a PR row is about 20 KB, and
/// on some networks a large GitHub answer is cut off and the call times out; five rows get
/// through where a hundred do not.
const PR_PAGE: usize = 5;

/// One read, and once more when it failed (a timeout on a slow line is the usual cause).
/// The second answer stands, error or not.
fn get_again(gh: &GhRead, path: &str) -> Result<Value, String> {
    gh.get(path).or_else(|_| gh.get(path))
}

/// Open PRs, each with what the list gives (base, author).
/// Pages read for open PRs (300 PRs); a full last page is an error, not a silent cut
/// (043dd L3).
const OPEN_PR_PAGES: u32 = 60;

fn open_prs(gh: &GhRead, repo: &str) -> Result<Vec<(OpenPr, PrInfo)>, String> {
    let mut a: Vec<Value> = Vec::new();
    for page in 1..=OPEN_PR_PAGES {
        let v = get_again(
            gh,
            &format!("repos/{repo}/pulls?state=open&per_page={PR_PAGE}&page={page}"),
        )?;
        let rows = v.as_array().ok_or("open PRs: not a list")?;
        let full = rows.len() >= PR_PAGE;
        a.extend(rows.iter().cloned());
        if !full {
            break;
        }
        if page == OPEN_PR_PAGES {
            return Err(format!("open PRs: more than {} pages", OPEN_PR_PAGES));
        }
    }
    // a PR opened while the pages were read pushes a row onto the next page: read once
    // (5d33a L1). A row without a number is kept and refused below.
    let mut seen = BTreeSet::new();
    a.retain(|p| {
        p.get("number")
            .and_then(Value::as_u64)
            .is_none_or(|n| seen.insert(n))
    });
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

/// The newest check run of each name (by `started_at`, then `id`): a commit keeps the runs of
/// every attempt, so a cell that was cancelled and then ran again shows twice (1ced7 r3:
/// #710 had 24 green cells and 6 cancelled ones of the run the label event replaced).
/// Premise: a check run name is one cell of the whole repository. Two workflows with a job
/// of the same name would hide each other here (043dd S82 review L4; FOLLOWUP L10: group by
/// workflow too, which needs the jobs API).
pub fn latest_runs(v: &Value) -> Option<Vec<&Value>> {
    let runs = v.get("check_runs")?.as_array()?;
    let key = |r: &Value| {
        (
            r.get("started_at")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            r.get("id").and_then(Value::as_u64).unwrap_or(0),
        )
    };
    let mut by: BTreeMap<&str, &Value> = BTreeMap::new();
    for r in runs {
        let name = r.get("name").and_then(Value::as_str).unwrap_or("?");
        if by.get(name).is_none_or(|cur| key(r) >= key(cur)) {
            by.insert(name, r);
        }
    }
    Some(by.into_values().collect())
}

/// Check runs of one commit rolled up: (SUCCESS | FAILURE | PENDING, failed names); None when
/// there are none. Only the newest run of each name counts ([`latest_runs`]). failure /
/// timed_out / action_required / startup_failure = red. A cancelled cell is not red: someone
/// (the merge queue, a newer event) stopped it and it has to run again, so it counts as
/// pending (1ced7 r3; S79: 「人工取消 / 被新版取代」 are not red).
pub fn rollup(v: &Value) -> Option<(String, Vec<String>)> {
    let runs = latest_runs(v)?;
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
        match r.get("conclusion").and_then(Value::as_str) {
            Some("failure" | "timed_out" | "action_required" | "startup_failure") => {
                red.push(name.to_owned());
            }
            Some("cancelled") => pending = true,
            _ => {}
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

/// The names of a commit's check runs (one per name, [`latest_runs`]); None when the page
/// does not hold them all (`total_count` larger than the list, 5d33a L3): a name on a later
/// page is never called missing.
pub fn check_names(v: &Value) -> Option<Vec<String>> {
    let listed = v.get("check_runs").and_then(Value::as_array)?.len();
    let listed = u64::try_from(listed).unwrap_or(u64::MAX);
    let total = v.get("total_count").and_then(Value::as_u64);
    if total.is_some_and(|t| t > listed) {
        return None;
    }
    Some(
        latest_runs(v)
            .unwrap_or_default()
            .into_iter()
            .filter_map(|r| r.get("name").and_then(Value::as_str).map(str::to_owned))
            .collect(),
    )
}

/// A PR head whose runs are all green but lacks a required cell is MISSING, not SUCCESS
/// (#755: aibd.yml over GitHub's limit, no aibd cell at all, the rest green). Still running or
/// red stays as it is; the missing list is kept either way.
pub fn judge_required(p: &mut PrInfo, required: &[String]) {
    let Some(have) = p.check_names.as_ref() else {
        return;
    };
    let missing: Vec<String> = required
        .iter()
        .filter(|c| !have.contains(c))
        .cloned()
        .collect();
    if !missing.is_empty() && p.checks.as_deref() == Some("SUCCESS") {
        p.checks = Some("MISSING".to_owned());
    }
    p.missing_checks = Some(missing);
}

/// Fills mergeable / merge_state (one PR GET) and checks / red_checks (check runs of the head).
/// The red check runs of [`rollup`], with their ids (for GitHub Actions the check run id is
/// the job id).
pub fn red_runs(v: &Value) -> Vec<(String, u64)> {
    latest_runs(v)
        .unwrap_or_default()
        .into_iter()
        .filter(|r| {
            r.get("status").and_then(Value::as_str) == Some("completed")
                && matches!(
                    r.get("conclusion").and_then(Value::as_str),
                    Some("failure" | "timed_out" | "action_required" | "startup_failure")
                )
        })
        .filter_map(|r| {
            Some((
                r.get("name").and_then(Value::as_str)?.to_owned(),
                r.get("id").and_then(Value::as_u64)?,
            ))
        })
        .collect()
}

/// IFACE-s79 r11 4: a job that died while its runner was being set up never ran the code.
/// The first failed step is a `Set up …` step (`Set up job`, `Set up runner`) and nothing
/// after it ran (skipped, or the closing `Complete …` step). Anything else, and a job
/// without steps, is a real failure: when it cannot be told, it is the author's to look at.
pub fn infra_failure(job: &Value) -> bool {
    let Some(steps) = job.get("steps").and_then(Value::as_array) else {
        return false;
    };
    let text = |s: &Value, k: &str| {
        s.get(k)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase()
    };
    let Some(at) = steps
        .iter()
        .position(|s| text(s, "conclusion") == "failure")
    else {
        return false;
    };
    let setup = text(&steps[at], "name").starts_with("set up ");
    let rest_idle = steps[at + 1..]
        .iter()
        .all(|s| text(s, "conclusion") == "skipped" || text(s, "name").starts_with("complete "));
    setup && rest_idle
}

fn pr_detail(gh: &GhRead, repo: &str, pr: &OpenPr, info: &mut PrInfo) -> Vec<String> {
    let mut errs = Vec::new();
    match get_again(gh, &format!("repos/{repo}/pulls/{}", pr.number)) {
        Ok(v) => {
            info.mergeable =
                Some(mergeable_word(v.get("mergeable").unwrap_or(&Value::Null)).to_owned());
            info.merge_state = text_at(&v, "/mergeable_state");
        }
        Err(e) => errs.push(format!("PR #{}: {e}", pr.number)),
    }
    match get_again(
        gh,
        &format!("repos/{repo}/commits/{}/check-runs?per_page=100", pr.head),
    ) {
        Ok(v) => {
            if let Some((q, r, c)) = crate::due::waiting(&v) {
                info.queued = Some(q);
                info.running = Some(r);
                info.cancelled = Some(c);
            }
            info.check_names = check_names(&v);
            if let Some((w, red)) = rollup(&v) {
                // which of the red ones died in the runner's set-up (one read per red run;
                // a job that cannot be read stays a real red)
                let infra: Vec<String> = red_runs(&v)
                    .into_iter()
                    .filter(|(_, id)| {
                        get_again(gh, &format!("repos/{repo}/actions/jobs/{id}"))
                            .is_ok_and(|job| infra_failure(&job))
                    })
                    .map(|(name, _)| name)
                    .collect();
                info.checks = Some(w);
                if !red.is_empty() && infra.len() == red.len() {
                    info.checks = Some("INFRA".to_owned());
                }
                info.infra_checks = Some(infra);
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

/// Pages read for the recently changed closed PRs in `--fast`; more than this inside
/// the window is an error, not a silent cut.
const FAST_CLOSED_PAGES: u32 = 60;

/// `--fast`: the closed PRs GitHub changed since `cutoff`, newest per head ref, from one
/// list (newest change first) instead of one question per branch. A PR closed before the
/// window is not seen: the branch then reads as having no PR.
fn closed_recent(gh: &GhRead, repo: &str, cutoff: i64) -> Result<BTreeMap<String, PrInfo>, String> {
    let mut out: BTreeMap<String, PrInfo> = BTreeMap::new();
    for page in 1..=FAST_CLOSED_PAGES {
        let v = get_again(
            gh,
            &format!(
                "repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page={PR_PAGE}&page={page}"
            ),
        )?;
        let rows = v.as_array().ok_or("closed PRs: not a list")?;
        let mut older = false;
        for p in rows {
            let at = text_at(p, "/updated_at").and_then(|t| crate::parse_time(&t));
            if at.is_some_and(|t| t < cutoff) {
                older = true;
                break;
            }
            let (Some(number), Some(head_ref)) = (
                p.get("number").and_then(Value::as_u64),
                text_at(p, "/head/ref"),
            ) else {
                continue;
            };
            let info = PrInfo {
                number,
                open: false,
                merged: !p.get("merged_at").is_none_or(Value::is_null),
                base: text_at(p, "/base/ref"),
                author: text_at(p, "/user/login"),
                labels: labels(p),
                head: text_at(p, "/head/sha"),
                ..PrInfo::default()
            };
            match out.get(&head_ref) {
                Some(old) if old.number >= number => {}
                _ => {
                    out.insert(head_ref, info);
                }
            }
        }
        if older || rows.len() < PR_PAGE {
            return Ok(out);
        }
    }
    Err(format!(
        "closed PRs: more than {FAST_CLOSED_PAGES} pages inside the window"
    ))
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

/// S73k: judge the gate files by content (merge base vs head). Err = git could not be read
/// (the sweep is then not complete); a file missing on one side is a refusal, not an error.
fn gate_content(
    git: &Git,
    main: &str,
    head: &str,
    gate: &[String],
    files: &[String],
) -> Result<Result<String, String>, String> {
    let own = crate::gatecontent::own_crates(files);
    let [f] = gate else {
        return Ok(crate::gatecontent::judge(gate, None, None, &own));
    };
    let base = git.run(&["merge-base", main, head])?;
    let at = |rev: &str| -> Result<Option<String>, String> {
        if git
            .run(&["ls-tree", "--name-only", rev, "--", f])?
            .trim()
            .is_empty()
        {
            return Ok(None);
        }
        git.run(&["show", &format!("{rev}:{f}")]).map(Some)
    };
    let (b, h) = (at(base.trim())?, at(head)?);
    Ok(crate::gatecontent::judge(
        gate,
        b.as_deref(),
        h.as_deref(),
        &own,
    ))
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

/// Does `taker` carry every commit of `head` (5d33a L2): `git cherry <taker> <head>` lists no
/// `+` (a commit of head whose patch taker lacks); an ancestor lists nothing. A git error
/// counts as no: the branch stays a to-do.
fn carries(git: &Git, taker: &str, head: &str) -> bool {
    git.run(&["cherry", taker, head])
        .is_ok_and(|s| !s.lines().any(|l| l.starts_with('+')))
}

/// The tree `git merge-tree --write-tree a b` writes: the first line of its stdout. Exit 1 (a
/// conflict) writes a tree too, with the conflict markers in it, so a merge that needed a
/// person never equals it.
fn auto_tree(git: &Git, a: &str, b: &str) -> Result<String, String> {
    let o = std::process::Command::new("git")
        .arg("-C")
        .arg(&git.repo)
        .args(["merge-tree", "--write-tree", "--no-messages", a, b])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("git merge-tree not run: {}", e.kind()))?;
    let out = String::from_utf8_lossy(&o.stdout);
    match (o.status.code(), out.lines().next().map(str::trim)) {
        (Some(0) | Some(1), Some(t))
            if t.len() >= 40 && t.bytes().all(|c| c.is_ascii_hexdigit()) =>
        {
            Ok(t.to_owned())
        }
        (c, _) => Err(format!(
            "git merge-tree: exit {c:?}: {}",
            String::from_utf8_lossy(&o.stderr).trim()
        )),
    }
}

/// For each changed workflow file that main has: the steps of main's file that are missing
/// from the file in the automatic merge of `head` into `main`, as `file: step`.
fn workflow_steps_lost(
    git: &Git,
    main: &str,
    head: &str,
    files: &[&String],
) -> Result<Vec<String>, String> {
    let tree = auto_tree(git, main, head)?;
    let at = |rev: &str, f: &str| -> Result<Option<String>, String> {
        if git
            .run(&["ls-tree", "--name-only", rev, "--", f])?
            .trim()
            .is_empty()
        {
            return Ok(None);
        }
        git.run(&["show", &format!("{rev}:{f}")]).map(Some)
    };
    let mut out = Vec::new();
    for f in files {
        let Some(was) = at(main, f)? else {
            continue;
        };
        let now = at(&tree, f)?;
        for s in crate::steps_lost(&was, now.as_deref()) {
            out.push(format!("{f}: {s}"));
        }
    }
    Ok(out)
}

/// S78 r-2a (RULES §20): the merge commits of `main..head` that are not the plain automatic
/// merge of their two parents, each with the files that differ from it. A merge of three or
/// more parents cannot be redone by merge-tree: it is listed with every file it changed
/// against its first parent (too many, never too few).
/// A green, finished receipt whose head is a merge commit with commits outside main: it may
/// have run on a stack (main + the heads of the queue, merged one after another).
struct Stack {
    head: String,
    /// the commits of main..head
    reach: BTreeSet<String>,
    receipt: Receipt,
}

fn stacks(
    git: &Git,
    main: &str,
    receipts: &BTreeMap<String, Vec<Receipt>>,
    errors: &mut Vec<String>,
) -> Vec<Stack> {
    let green: Vec<(&String, Receipt)> = receipts
        .iter()
        .filter_map(|(h, v)| {
            crate::receipts_together(v)
                .filter(Receipt::green)
                .map(|r| (h, r))
        })
        .collect();
    if green.is_empty() {
        return Vec::new();
    }
    // which of those heads are merge commits this repository has (one question for all)
    let mut args = vec!["rev-list", "--no-walk", "--merges", "--ignore-missing"];
    args.extend(green.iter().map(|(h, _)| h.as_str()));
    let merges: BTreeSet<String> = match git.run(&args) {
        Ok(s) => s.lines().map(|l| l.trim().to_owned()).collect(),
        Err(e) => {
            errors.push(format!("stacked receipts: {e}"));
            return Vec::new();
        }
    };
    let mut out = Vec::new();
    for (h, receipt) in green {
        if !merges.contains(h) {
            continue;
        }
        match git.run(&["rev-list", h, &format!("^{main}")]) {
            Ok(s) => {
                let reach: BTreeSet<String> = s
                    .lines()
                    .map(|l| l.trim().to_owned())
                    .filter(|l| !l.is_empty())
                    .collect();
                if !reach.is_empty() {
                    out.push(Stack {
                        head: h.clone(),
                        reach,
                        receipt,
                    });
                }
            }
            Err(e) => errors.push(format!("stacked receipt {h}: {e}")),
        }
    }
    out
}

/// Is the stack nothing but open PR heads and plain merges: every commit of it that no open
/// PR head inside it reaches is a merge of two parents whose tree is the automatic merge
/// (RULES §20). A commit of its own, or a hand-resolved merge, and the receipt ran on code
/// that is in no PR: it does not count.
fn stack_plain(
    git: &Git,
    main: &str,
    s: &Stack,
    pr_heads: &[(u64, String)],
) -> Result<bool, String> {
    let mut rest: BTreeSet<String> = s.reach.clone();
    for (_, h) in pr_heads {
        if s.reach.contains(h) {
            for c in git.run(&["rev-list", h, &format!("^{main}")])?.lines() {
                rest.remove(c.trim());
            }
        }
    }
    for c in &rest {
        let line = git.run(&["rev-list", "--parents", "-n", "1", c])?;
        let p: Vec<&str> = line.split_whitespace().collect();
        if p.len() != 3 {
            return Ok(false);
        }
        let auto = auto_tree(git, p[1], p[2])?;
        let tree = git.run(&["rev-parse", &format!("{c}^{{tree}}")])?;
        if auto != tree.trim() {
            return Ok(false);
        }
    }
    Ok(true)
}

/// IFACE-s79 r10 3: the passed reviews of the commits under `head` (the newest review of
/// each commit), and what they cover together ([`crate::review_parts`]).
fn parts_of(
    git: &Git,
    main: &str,
    head: &str,
    commits: &[String],
    reviews: &BTreeMap<String, Vec<Review>>,
    resolved: &[Resolved],
) -> Result<crate::ReviewParts, String> {
    let list = |args: &[&str]| -> Result<Vec<String>, String> {
        Ok(git
            .run(args)?
            .lines()
            .map(|l| l.trim().to_owned())
            .filter(|l| !l.is_empty())
            .collect())
    };
    let non_merge = list(&["rev-list", "--no-merges", &format!("{main}..{head}")])?;
    let merges: BTreeSet<String> = list(&["rev-list", "--merges", &format!("{main}..{head}")])?
        .into_iter()
        .collect();
    let mut parts = Vec::new();
    for c in commits {
        let Some(r) = reviews
            .get(c)
            .and_then(|v| v.first())
            .filter(|r| r.passed())
        else {
            continue;
        };
        let not_main = format!("^{main}");
        let from = match &r.scope {
            // a start that cannot be read (unknown, ambiguous) covers the commit alone
            crate::Scope::From(a) => list(&["rev-list", &format!("{a}..{c}"), &not_main])
                .ok()
                .map(|v| v.into_iter().collect()),
            _ => None,
        };
        parts.push(crate::Part {
            head: c.clone(),
            file: r.file.clone(),
            scope: r.scope.clone(),
            merge: merges.contains(c),
            reach: list(&["rev-list", c, &not_main])?.into_iter().collect(),
            from,
        });
    }
    Ok(crate::review_parts(&non_merge, &parts, resolved))
}

fn resolved_merges(git: &Git, main: &str, head: &str) -> Result<Vec<Resolved>, String> {
    let list = git.run(&[
        "rev-list",
        "--merges",
        "--parents",
        &format!("{main}..{head}"),
    ])?;
    let names = |a: &str, b: &str| -> Result<Vec<String>, String> {
        Ok(git
            .run(&["diff", "--name-only", "--no-renames", a, b])?
            .lines()
            .map(|l| l.trim().to_owned())
            .filter(|l| !l.is_empty())
            .collect())
    };
    let mut out = Vec::new();
    for l in list.lines() {
        let p: Vec<&str> = l.split_whitespace().collect();
        if p.len() < 3 {
            continue;
        }
        let (c, a, b) = (p[0], p[1], p[2]);
        let files = if p.len() == 3 {
            let auto = auto_tree(git, a, b)?;
            let tree = git.run(&["rev-parse", &format!("{c}^{{tree}}")])?;
            if auto == tree.trim() {
                continue;
            }
            names(&auto, c)?
        } else {
            names(a, c)?
        };
        let ahead = git.run(&["rev-list", "--count", b, &format!("^{main}")])?;
        out.push(Resolved {
            commit: c.to_owned(),
            parent: b.to_owned(),
            from_main: ahead.trim() == "0",
            files,
        });
    }
    Ok(out)
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
                fast: o.fast,
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
    let stacks = stacks(&git, &main, &receipts, &mut errors);

    // --fast: closed PRs from one list; None = the full way, one question per branch
    let window = o.now - o.hours * 3600;
    let recent_closed = match (o.fast, &gh, &repo) {
        (true, Some(gh), Some(repo)) => match closed_recent(gh, repo, window) {
            Ok(m) => Some(m),
            Err(e) => {
                errors.push(format!("gh: {e}"));
                Some(BTreeMap::new())
            }
        },
        _ => None,
    };

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
        // --fast: only what a merge could be waiting on, or what somebody touched lately
        if o.fast && r.at < window && !prs.iter().any(|p| p.head_ref == *name || p.head == r.head) {
            continue;
        }
        // 5d33a M1: an open PR of this name is judged at the PR's head, also when the local
        // branch sits on another commit (ahead: not pushed yet; behind: pushed from elsewhere)
        let pr_head = prs
            .iter()
            .find(|p| p.head_ref == *name && pr_heads.iter().any(|(n, _)| *n == p.number))
            .map(|p| p.head.clone());
        let (mut head, mut at) = (r.head.clone(), r.at);
        let mut local_head = None;
        if let Some(ph) = pr_head.filter(|ph| *ph != r.head) {
            match git.run(&["show", "-s", "--format=%ct", &ph]) {
                Ok(s) => {
                    at = s.trim().parse().unwrap_or(at);
                    local_head = local.map(|l| l.head.clone());
                    head = ph;
                }
                Err(e) => errors.push(format!("{name}: PR head: {e}")),
            }
        }
        let mut b = Branch {
            branch: name.clone(),
            seat,
            head,
            local_head,
            local: local.is_some(),
            remote: remote.map(|_| format!("origin/{name}")),
            worktree: trees.get(name).cloned(),
            last_commit_at: at,
            age_h: (o.now - at).max(0) / 3600,
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
            // the PR is judged on the row whose head is the PR's head (IFACE-s79 r11 5): a
            // local branch of the PR's name on another commit is not the PR
            p.head == b.head && (p.head_ref == *name || pushed_names.contains(&p.head_ref.as_str()))
        }) {
            b.pr = infos.get(&p.number).cloned();
        } else if let Some(m) = &recent_closed {
            b.pr = m.get(name.as_str()).cloned();
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
                &format!("^{main}"),
            ]) {
                Ok(s) => r.unreviewed_after = s.lines().map(str::to_owned).collect(),
                Err(e) => errors.push(format!("{name}: rev-list after review: {e}")),
            }
        }
        b.receipt = commits.iter().find_map(|c| {
            // every receipt file of that head, read as one (IFACE-s79 r11 5)
            receipts
                .get(c)
                .and_then(|v| crate::receipts_together(v))
                .map(|r| Receipt {
                    at_head: *c == b.head,
                    ..r
                })
        });
        // f1fae COMPARE r2 2: an open PR without a receipt at its head, and a green receipt
        // that ran on a stack of plain merges holding this head: that one counts
        if !b.receipt.as_ref().is_some_and(|r| r.at_head) && prs.iter().any(|p| p.head == b.head) {
            for s in &stacks {
                if s.head != b.head && s.reach.contains(&b.head) {
                    match stack_plain(&git, &main, s, &pr_heads) {
                        Ok(true) => {
                            b.receipt = Some(Receipt {
                                at_head: true,
                                stacked_at: Some(s.head.clone()),
                                ..s.receipt.clone()
                            });
                            break;
                        }
                        Ok(false) => {}
                        Err(e) => errors.push(format!("{name}: stacked receipt: {e}")),
                    }
                }
            }
        }
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
            "--name-status",
            "--no-renames",
            &format!("{main}...{}", b.head),
        ]) {
            Ok(s) => {
                let (files, kept) = diff_names(&s);
                // #755: the cells the merge queue requires, read against the PR's runs
                if let Some(p) = b.pr.as_mut().filter(|p| p.open) {
                    let aibd = if crate::touches_rust(&files) {
                        let at = format!("{}:.github/workflows/aibd.yml", b.head);
                        git.run(&["show", &at]).ok()
                    } else {
                        None
                    };
                    judge_required(p, &crate::required_cells(&files, aibd.as_deref()));
                }
                // #755: a workflow over GitHub's size limit, at the head or once merged
                if files.iter().any(|f| f.starts_with(".github/workflows/")) {
                    let wf = ".github/workflows";
                    match git.run(&["ls-tree", "-l", "-r", &b.head, "--", wf]) {
                        Ok(s) => b.workflow_too_big = crate::too_big(&s, "head"),
                        Err(e) => errors.push(format!("{name}: workflow sizes: {e}")),
                    }
                    match auto_tree(&git, &main, &b.head)
                        .and_then(|t| git.run(&["ls-tree", "-l", "-r", &t, "--", wf]))
                    {
                        Ok(s) => {
                            for big in crate::too_big(&s, "merged") {
                                if !b.workflow_too_big.iter().any(|x| x.0 == big.0) {
                                    b.workflow_too_big.push(big);
                                }
                            }
                        }
                        Err(e) => errors.push(format!("{name}: workflow sizes merged: {e}")),
                    }
                }
                b.gate_files = gate_files(&files);
                b.py_files = py_files(&kept);
                if !b.gate_files.is_empty() {
                    match gate_content(&git, &main, &b.head, &b.gate_files, &files) {
                        Ok(c) => b.gate_content = Some(c),
                        Err(e) => errors.push(format!("{name}: gate content: {e}")),
                    }
                }
            }
            Err(e) => errors.push(format!("{name}: diff: {e}")),
        }
        match resolved_merges(&git, &main, &b.head) {
            Ok(m) => b.resolved_merges = Some(m),
            Err(e) => errors.push(format!("{name}: merges: {e}")),
        }
        // which of them came after the review: a review at the head has none
        if let (Some(r), Some(m)) = (b.review.as_mut(), b.resolved_merges.as_ref()) {
            if r.at_head {
                r.resolved_after_review = Some(Vec::new());
            } else {
                match git.run(&["rev-list", "--merges", &format!("{}..{}", r.head, b.head)]) {
                    Ok(s) => {
                        let after: BTreeSet<&str> = s.lines().map(str::trim).collect();
                        r.resolved_after_review = Some(
                            m.iter()
                                .filter(|x| after.contains(x.commit.as_str()))
                                .cloned()
                                .collect(),
                        );
                    }
                    Err(e) => errors.push(format!("{name}: merges after review: {e}")),
                }
            }
        }
        // slices stacked into one head: is every commit covered by a passed review
        if let (Some(r), Some(res)) = (&b.review, &b.resolved_merges) {
            if !r.at_head && r.passed() {
                match parts_of(&git, &main, &b.head, &commits, &reviews, res) {
                    Ok(p) => b.review_parts = Some(p),
                    Err(e) => errors.push(format!("{name}: review parts: {e}")),
                }
            }
        }
        if b.acked.is_none() {
            match conflicts(&git, &main, &b.head) {
                Ok(c) => b.conflicts_with_main = Some(c),
                Err(e) => errors.push(format!("{name}: merge-tree vs main: {e}")),
            }
        }
        // RULES §25: a merge that drops a step main has (a stale hand resolution) must not go
        let flows: Vec<&String> = b
            .gate_files
            .iter()
            .filter(|f| f.starts_with(".github/workflows/"))
            .collect();
        if !flows.is_empty() && b.conflicts_with_main.as_ref().is_some_and(|c| !c.conflict) {
            match workflow_steps_lost(&git, &main, &b.head, &flows) {
                Ok(l) => b.steps_lost = Some(l),
                Err(e) => errors.push(format!("{name}: workflow steps: {e}")),
            }
        }
        out.push(b);
    }

    // stack top: nobody listed is stacked on it
    let below: BTreeSet<String> = out.iter().flat_map(|b| b.stacked_on.clone()).collect();
    for b in out.iter_mut() {
        b.stack_top = !below.contains(&b.branch);
    }

    // S79 (519d0 11-15): a seat went quiet and another seat carries its work on
    // integ-src/<key>-<seat>; the newest such branch of another seat takes this one over
    let takers: Vec<(String, String, String, Option<String>, i64)> = out
        .iter()
        .filter(|t| t.branch.starts_with("integ-src/") && t.seat.is_some())
        .map(|t| {
            let key = crate::name_core(&t.branch);
            (
                t.branch.clone(),
                t.head.clone(),
                key,
                t.seat.clone(),
                t.last_commit_at,
            )
        })
        .collect();
    // 5d33a L2: a name alone is not enough; the taker must carry every commit of this head
    // (its head, or each patch: `git cherry` prints no `+`), or the branch stays a to-do
    for b in out.iter_mut() {
        let core = crate::name_core(&b.branch);
        b.taken_over_by = takers
            .iter()
            .filter(|(tb, th, key, ts, at)| {
                *tb != b.branch
                    && *th != b.head
                    && *ts != b.seat
                    && *at > b.last_commit_at
                    && crate::takes_over(&core, key)
                    && carries(&git, th, &b.head)
            })
            .max_by_key(|t| t.4)
            .map(|(tb, th, ..)| (tb.clone(), th.clone()));
    }

    // S79 (519d0 11-15): a red receipt at the head of a branch without an open PR, while a
    // green finished receipt ran on a merge above it (an integration head the branch was
    // merged into). Only merges count: a plain commit stacked on a red head is the same
    // seat's next try, and the red head stays a fix.
    let green: Vec<(&String, Receipt)> = receipts
        .iter()
        .filter_map(|(h, v)| {
            crate::receipts_together(v)
                .filter(Receipt::green)
                .map(|r| (h, r))
        })
        .collect();
    for b in out.iter_mut() {
        let red_here = b.receipt.as_ref().is_some_and(|r| r.at_head && !r.green());
        if !red_here || b.pr.as_ref().is_some_and(|p| p.open) {
            continue;
        }
        let not_here = format!("^{}", b.head);
        let mut args = vec!["rev-list", "--ignore-missing", "--ancestry-path", &not_here];
        args.push("--merges");
        let tips: Vec<&str> = green
            .iter()
            .map(|(h, _)| h.as_str())
            .filter(|h| *h != b.head)
            .collect();
        if tips.is_empty() {
            continue;
        }
        args.extend(tips);
        match git.run(&args) {
            Ok(s) => {
                b.absorbed_into = s.lines().map(str::trim).find_map(|c| {
                    green
                        .iter()
                        .find(|(h, _)| h.as_str() == c)
                        .map(|(h, r)| ((*h).clone(), r.file.clone()))
                });
            }
            Err(e) => errors.push(format!("{}: receipts above the head: {e}", b.branch)),
        }
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
        .filter(|(_, b)| matches!(crate::classify(b, o.hours).0, 1 | 2 | 8))
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
        fast: o.fast,
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
    /// IFACE-s79 r11 4: only a job that died in its runner's set-up is infra.
    #[test]
    fn a_job_that_died_in_set_up_is_infra_and_a_failed_test_is_not() {
        let job = |steps: &[(&str, &str)]| {
            serde_json::json!({"steps": steps.iter()
                .map(|(n, c)| serde_json::json!({"name": n, "conclusion": c}))
                .collect::<Vec<_>>()})
        };
        assert!(infra_failure(&job(&[
            ("Set up runner", "failure"),
            ("Run actions/checkout@v4", "skipped"),
            ("test", "skipped"),
            ("Complete job", "success"),
        ])));
        assert!(infra_failure(&job(&[("Set up job", "failure")])));
        // the code ran and failed
        assert!(!infra_failure(&job(&[
            ("Set up job", "success"),
            ("test", "failure"),
            ("Complete job", "success"),
        ])));
        // set-up failed but a later step still ran: not told apart, so a real red
        assert!(!infra_failure(&job(&[
            ("Set up job", "failure"),
            ("test", "failure"),
        ])));
        // no steps, or not a job: a real red
        assert!(!infra_failure(&job(&[])));
        assert!(!infra_failure(&serde_json::json!({})));
        let runs = serde_json::json!({"check_runs": [
            {"id": 55, "name": "rust", "status": "completed", "conclusion": "failure"},
            {"id": 56, "name": "fmt", "status": "completed", "conclusion": "success"},
        ]});
        assert_eq!(red_runs(&runs), vec![("rust".to_owned(), 55)]);
    }

    /// 5d33a L3: a check-runs page that does not hold every run gives no names, so a cell on
    /// a later page is never called missing.
    #[test]
    fn check_names_are_none_when_the_page_does_not_hold_every_run() {
        let one = |n: usize| serde_json::json!({"name": format!("c{n}"), "status": "completed", "conclusion": "success"});
        let runs: Vec<Value> = (0..100).map(one).collect();
        let full = serde_json::json!({"total_count": 100, "check_runs": runs});
        assert_eq!(check_names(&full).map(|v| v.len()), Some(100));
        let cut = serde_json::json!({"total_count": 101, "check_runs": runs});
        assert_eq!(check_names(&cut), None);
        let bare = serde_json::json!({"check_runs": [one(0)]});
        assert_eq!(check_names(&bare), Some(vec!["c0".to_owned()]));
        assert_eq!(check_names(&serde_json::json!({})), None);
    }

    #[test]
    fn a_receipt_with_a_red_file_beside_it_is_red() {
        let d = tempfile::tempdir().expect("tmp");
        let h = "e".repeat(40);
        let text = format!("== HEAD={h}\nRC fmt=0\nKNIVES-RC 0\nMISSES-COUNT 0\nHEAD-END {h}\n");
        for n in ["RECEIPT-r1.out", "RECEIPT-r2.out"] {
            std::fs::write(d.path().join(n), &text).expect("write");
        }
        std::fs::write(d.path().join("RECEIPT-r1.red"), "").expect("write");
        let mut errors = Vec::new();
        let (_, receipts) = read_pending(d.path(), &mut errors);
        assert!(errors.is_empty(), "{errors:?}");
        let mut got: Vec<(String, Vec<String>)> = receipts[&h]
            .iter()
            .map(|r| (r.file.clone(), r.nonzero.clone()))
            .collect();
        got.sort();
        assert_eq!(
            got,
            vec![
                ("RECEIPT-r1.out".to_owned(), vec!["red_file".to_owned()]),
                ("RECEIPT-r2.out".to_owned(), Vec::new()),
            ]
        );
    }
}
