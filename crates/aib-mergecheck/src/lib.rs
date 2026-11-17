// SPDX-License-Identifier: Apache-2.0
//! `aib-mergecheck ready`(S73-ready 片 1;REQ-merge-ready-519d0-1117 r3、IFACE-mergecheck-ready-7b81e-1117
//! r2,5d33a 0/0/0/1):for every open PR, measure each merge condition (pass / fail / unread and
//! why), estimate how long things will take, and say whether the machines can take a merge now.
//!
//! Read-only: it never merges, comments, labels, writes an event or writes the plan. The only
//! file it writes is its own `last-ready.json` (which PRs were mergeable last time), so slice 2
//! can turn "became mergeable" into a `merge.ready` event.
//!
//! The judging here is pure over a [`World`]; `fsworld` is the one that calls `gh` and `git`
//! (fixed argv, a deadline, no shell). "審過" is not judged here: it calls the station judge
//! `aib_plan::card::entry_problems` (SPEC-station-entry-review r5), with the PR's current head.

pub mod fsworld;
pub mod steps;

use aib_core::median::{self, Estimate};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// The `--json` format.
pub const FMT: &str = "aib-mergecheck-ready.v1";

/// One item's verdict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum V {
    Pass,
    Fail(String),
    /// could not be read: what is missing (the PR is never listed mergeable, rc 3)
    Unread(String),
    /// only for checks: a cancelled required cell, neither red nor green for `ready`
    Cancelled(String),
}

impl V {
    pub fn word(&self) -> &'static str {
        match self {
            V::Pass => "pass",
            V::Fail(_) => "fail",
            V::Unread(_) => "unread",
            V::Cancelled(_) => "cancelled",
        }
    }
    pub fn why(&self) -> &str {
        match self {
            V::Pass => "",
            V::Fail(w) | V::Unread(w) | V::Cancelled(w) => w,
        }
    }
}

/// The seven per-PR items, in print order (the pair and bump rows are lists, not verdicts).
pub const ITEMS: [&str; 7] = [
    "base", "draft", "review", "checks", "main", "workflow", "steps",
];

/// `merge-ready.json`.
#[derive(Debug, Clone, PartialEq)]
pub struct Cfg {
    pub main: String,
    pub workflow_cap: u64,
    pub take: usize,
    pub self_hosted: Vec<String>,
    pub receipt_hosts: Vec<String>,
    /// REQ §7.1 never-auto paths as `(pattern, category)`: a pattern is a path prefix, or with
    /// a leading `*` a piece anywhere in the path. Slice 2 never auto-merges a match; `ready`
    /// prints the category and "人按可提前" (5d33a r2 N1, review 9fae2967 L1). Deleted files
    /// and new Cargo.lock packages are found without a pattern.
    pub never_auto_paths: Vec<(String, String)>,
    pub pending_dir: Option<String>,
    pub plan_file: Option<String>,
    /// state/measure (ci-jobs.jsonl, receipt-runs.jsonl)
    pub measure_dir: Option<String>,
    /// where last-ready.json lives
    pub state_dir: Option<String>,
}

impl Default for Cfg {
    fn default() -> Cfg {
        Cfg {
            main: "main".into(),
            workflow_cap: 450_000,
            take: 10,
            self_hosted: Vec::new(),
            receipt_hosts: Vec::new(),
            never_auto_paths: default_never_auto(),
            pending_dir: None,
            plan_file: None,
            measure_dir: None,
            state_dir: None,
        }
    }
}

/// REQ-merge-ready r4 §7.1 as paths (the classes that are not paths: integration heads are
/// unread in `review`; outside or closed authors and release / customer text are slice 2).
pub fn default_never_auto() -> Vec<(String, String)> {
    let mut v: Vec<(String, String)> = vec![(".github/".into(), "新閘".into())];
    for c in [
        "aib-mergecheck",
        "aib-mergewatch",
        "aib-slice",
        "aib-plan",
        "aib-drive",
        "aib-knife",
    ] {
        v.push((format!("crates/{c}/"), "煞車".into()));
    }
    for (pat, cat) in [
        ("*/knives/", "煞車(刀)"),
        ("scripts/knife", "煞車(刀)"),
        ("*RULES", "煞車(規則文件)"),
        ("rust-toolchain", "工具鏈"),
        ("clippy.toml", "工具鏈"),
        ("crates/aib-own-gate/", "核准類"),
        ("crates/aib-approve/", "核准類"),
        ("crates/aib-consent/", "核准類"),
        ("crates/aib-signlint/", "核准類"),
    ] {
        v.push((pat.into(), cat.into()));
    }
    v
}

/// `pattern` (prefix, or `*piece` anywhere) against a `/` path.
pub fn path_matches(pattern: &str, path: &str) -> bool {
    match pattern.strip_prefix('*') {
        Some(piece) => path.contains(piece),
        None => path.starts_with(pattern),
    }
}

/// `[[package]]` names in a Cargo.lock text.
pub fn lock_packages(text: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut in_pkg = false;
    for l in text.lines() {
        let t = l.trim();
        if t.starts_with('[') {
            in_pkg = t == "[[package]]";
        } else if in_pkg {
            if let Some(n) = t.strip_prefix("name = ") {
                out.insert(n.trim_matches('"').to_owned());
            }
        }
    }
    out
}

/// Why slice 2 would never auto-merge a PR (REQ §7.1), one line per category, each ending in
/// "人按可提前" (r4 L1: new gates and brakes are merged early by hand). `changed` is
/// `(status, path)` from `git diff --name-status`; `new_pkgs` = new Cargo.lock packages.
pub fn never_auto_reasons(
    cfg: &Cfg,
    changed: &[(String, String)],
    new_pkgs: &[String],
) -> Vec<String> {
    let mut by_cat: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (st, p) in changed {
        let p = slash(p);
        if st.starts_with('D') {
            by_cat.entry("刪檔".into()).or_default().push(p.clone());
        }
        if let Some((_, cat)) = cfg
            .never_auto_paths
            .iter()
            .find(|(pat, _)| path_matches(pat, &p))
        {
            by_cat.entry(cat.clone()).or_default().push(p);
        }
    }
    if !new_pkgs.is_empty() {
        by_cat
            .entry("Cargo.lock 新套件".into())
            .or_default()
            .extend(new_pkgs.iter().cloned());
    }
    by_cat
        .into_iter()
        .map(|(cat, files)| {
            let more = if files.len() > 3 {
                format!(" 等 {} 個", files.len())
            } else {
                String::new()
            };
            let shown: Vec<&str> = files.iter().take(3).map(String::as_str).collect();
            format!("{cat}({}{more}):人按可提前", shown.join("、"))
        })
        .collect()
}

impl Cfg {
    /// Unknown keys are refused (a typo must not silently fall back to a default).
    pub fn parse(text: &str) -> Result<Cfg, String> {
        let v: Value = serde_json::from_str(text).map_err(|e| format!("merge-ready.json: {e}"))?;
        let o = v.as_object().ok_or("merge-ready.json: not an object")?;
        let mut c = Cfg::default();
        let strs = |x: &Value, k: &str| -> Result<Vec<String>, String> {
            x.as_array()
                .ok_or(format!("{k}: not a list"))?
                .iter()
                .map(|s| {
                    s.as_str()
                        .map(str::to_owned)
                        .ok_or(format!("{k}: not text"))
                })
                .collect()
        };
        for (k, x) in o {
            match k.as_str() {
                "main" => c.main = x.as_str().ok_or("main: not text")?.to_owned(),
                "workflow_cap" => {
                    c.workflow_cap = x.as_u64().ok_or("workflow_cap: not a number")?
                }
                "take" => {
                    c.take = x
                        .as_u64()
                        .filter(|n| *n > 0)
                        .ok_or("take: not a number > 0")? as usize
                }
                "self_hosted" => c.self_hosted = strs(x, k)?,
                "receipt_hosts" => c.receipt_hosts = strs(x, k)?,
                "never_auto_paths" => {
                    let o = x.as_object().ok_or("never_auto_paths: not an object")?;
                    c.never_auto_paths = o
                        .iter()
                        .map(|(pat, cat)| {
                            cat.as_str()
                                .map(|c| (pat.clone(), c.to_owned()))
                                .ok_or(format!("never_auto_paths.{pat}: not text"))
                        })
                        .collect::<Result<_, _>>()?;
                }
                "pending_dir" => {
                    c.pending_dir = Some(x.as_str().ok_or("pending_dir: not text")?.into())
                }
                "plan_file" => c.plan_file = Some(x.as_str().ok_or("plan_file: not text")?.into()),
                "measure_dir" => {
                    c.measure_dir = Some(x.as_str().ok_or("measure_dir: not text")?.into())
                }
                "state_dir" => c.state_dir = Some(x.as_str().ok_or("state_dir: not text")?.into()),
                // slice 2 keys, read there
                "never_auto" | "auto_bump_cap" => {}
                _ => return Err(format!("merge-ready.json: unknown key {k}")),
            }
        }
        Ok(c)
    }
}

/// `git merge-tree --write-tree` result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Merge {
    Clean(String),
    Conflict(Vec<String>),
    Err(String),
}

/// One CI job row (state/measure ci-jobs.jsonl).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CiJob {
    /// run id: the only ordering key the rows carry (no timestamp field)
    pub run: i64,
    pub job: String,
    pub event: String,
    pub branch: String,
    pub conclusion: String,
    pub dur_s: u64,
    pub wait_s: u64,
    pub macos: bool,
}

/// One receipt run row (state/measure receipt-runs.jsonl).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReceiptRun {
    pub at_ms: i64,
    pub seat: String,
    pub total_s: u64,
}

/// GitHub macOS right now (runs, not jobs: macOS takes one run at a time).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct MacLoad {
    pub running_main: u32,
    pub running_pr: u32,
    pub queued_main: u32,
    pub queued_pr: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Runner {
    pub name: String,
    pub online: bool,
    pub busy: bool,
}

/// Everything `ready` reads. `fsworld::FsWorld` reads the real thing; tests use fixtures.
pub trait World {
    /// Bring main and every open PR's refs up to date. `ready` calls it first, before any
    /// other read (review 9fae2967 M1: main read before the fetch measured last round's main).
    fn refresh(&self) -> Result<(), String>;
    /// `gh pr list --state open --json …,isDraft,body` text (as of `refresh`)
    fn open_prs(&self) -> Result<String, String>;
    /// required check names from the active rulesets of the main branch
    fn required_checks(&self) -> Result<Vec<String>, String>;
    fn main_head(&self) -> Result<String, String>;
    fn merge_tree(&self, a: &str, b: &str) -> Merge;
    /// `b` merged onto (`base` with `a` merged in): what `b` meets once `a` is in main
    /// (IFACE §2, review 9fae2967 L3); `a` conflicting with `base` is `Err`
    fn merge_after(&self, base: &str, a: &str, b: &str) -> Merge;
    /// `(path, bytes)` of `.github/workflows/*` in a tree
    fn workflows(&self, tree: &str) -> Result<Vec<(String, u64)>, String>;
    /// a file's text at a commit or tree
    fn show(&self, rev: &str, path: &str) -> Option<String>;
    /// `(status, path)` `head` changes against the merge base with `base` (`--name-status`)
    fn changed(&self, base: &str, head: &str) -> Result<Vec<(String, String)>, String>;
    /// merge commits on the first-parent chain of `base..head`
    fn first_parent_merges(&self, base: &str, head: &str) -> Result<usize, String>;
    /// other in-flight heads (`integ*` branches on origin) as `(name, sha)`
    fn side_heads(&self) -> Result<Vec<(String, String)>, String>;
    fn pending(&self, name: &str) -> Option<String>;
    fn ancestor(&self, a: &str, b: &str) -> Option<bool>;
    fn plan(&self) -> Result<String, String>;
    fn ci_jobs(&self) -> Result<Vec<CiJob>, String>;
    fn receipt_runs(&self) -> Result<Vec<ReceiptRun>, String>;
    fn mac_load(&self) -> Result<MacLoad, String>;
    fn runners(&self) -> Result<Vec<Runner>, String>;
    /// a release workflow is running now
    fn releasing(&self) -> Result<bool, String>;
    /// the tree of the merge commit the PR's latest checks ran on (display only)
    fn pr_merge_tree(&self, pr: u32) -> Option<String>;
    fn now_s(&self) -> i64;
}

#[derive(Debug, Clone, PartialEq)]
pub struct PrReport {
    pub pr: u32,
    pub head: String,
    pub branch: String,
    pub card: Option<String>,
    pub items: BTreeMap<&'static str, V>,
    pub merge_tree: Option<String>,
    pub workflow_max: Option<(String, u64)>,
    pub skipped_cells: Vec<String>,
    pub mergeable: bool,
    /// mergeable PRs this one clashes with (not at the same time)
    pub not_with: Vec<u32>,
    /// in-flight heads that are clean against main now and clash with this one
    pub bumps: Vec<String>,
    /// why slice 2 would never auto-merge it (r2 N1 wording), empty = could
    pub never_auto: Vec<String>,
    pub until_green: Option<Estimate>,
    pub rereceipt: Vec<(String, Estimate)>,
    /// main's round after this merge could reuse the PR's result (display only)
    pub reuse: V,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Load {
    pub mac: Result<MacLoad, String>,
    pub mac_wait: Estimate,
    pub self_hosted: Vec<(String, V)>,
    pub receipt: Vec<(String, V)>,
    pub verdict: String,
    pub because: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub main: String,
    pub prs: Vec<PrReport>,
    /// mergeable in the order to merge them
    pub order: Vec<u32>,
    pub main_round: Estimate,
    pub load: Load,
    pub newly_ready: Vec<u32>,
    /// a read that failed for the whole run (gh list, main head)
    pub unread: Vec<String>,
}

impl Report {
    /// 0 measured; 3 something could not be read (REQ 驗收 4)
    pub fn rc(&self) -> i32 {
        let any = !self.unread.is_empty()
            || self
                .prs
                .iter()
                .any(|p| p.items.values().any(|v| matches!(v, V::Unread(_))));
        if any {
            3
        } else {
            0
        }
    }
}

fn short(s: &str) -> &str {
    s.get(..8).unwrap_or(s)
}

/// a path with `\` written as `/` (the same bytes on Mac and Windows, REQ 驗收 7)
pub fn slash(p: &str) -> String {
    p.replace('\\', "/")
}

/// `isDraft` and `body` of each PR (the station parser `aib_plan::prs::parse_gh` reads the rest)
fn drafts_and_bodies(text: &str) -> BTreeMap<u32, (bool, String)> {
    let mut m = BTreeMap::new();
    if let Ok(Value::Array(a)) = serde_json::from_str::<Value>(text) {
        for p in a {
            if let Some(n) = p.get("number").and_then(Value::as_u64) {
                let d = p.get("isDraft").and_then(Value::as_bool);
                let b = p
                    .get("body")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned();
                // isDraft missing ⇒ treat as draft (unknown is never "ready")
                m.insert(n as u32, (d.unwrap_or(true), b));
            }
        }
    }
    m
}

/// The required cells' verdict (r2 L1 L2): per name only the latest start counts; skip counts
/// as passed (GitHub does) but is listed; running / queued fail; cancelled is its own word.
pub fn judge_checks(required: &[String], checks: &[aib_plan::prs::Check]) -> (V, Vec<String>) {
    use aib_plan::prs::Cell;
    let mut latest: BTreeMap<&str, &aib_plan::prs::Check> = BTreeMap::new();
    for c in checks {
        let keep = match latest.get(c.name.as_str()) {
            None => true,
            // the rerun started later wins; an unstarted (queued) one is newer than any start
            Some(old) => match (old.started, c.started) {
                (_, None) => true,
                (None, Some(_)) => false,
                (Some(a), Some(b)) => b >= a,
            },
        };
        if keep {
            latest.insert(c.name.as_str(), c);
        }
    }
    let mut skipped = Vec::new();
    let mut running = Vec::new();
    let mut red = Vec::new();
    let mut cancelled = Vec::new();
    let mut missing = Vec::new();
    for name in required {
        match latest.get(name.as_str()) {
            None => missing.push(name.clone()),
            Some(c) => match c.cell {
                Cell::Pass if c.started.is_some() => {}
                Cell::Pass => red.push(format!("{name}(沒有開始時間，不是實跑)")),
                Cell::Skip => skipped.push(name.clone()),
                Cell::Running => running.push(name.clone()),
                Cell::Fail => red.push(name.clone()),
                Cell::Cancel => cancelled.push(name.clone()),
            },
        }
    }
    let v = if !red.is_empty() {
        V::Fail(format!("紅:{}", red.join("、")))
    } else if !running.is_empty() {
        V::Fail(format!("還在跑:{}", running.join("、")))
    } else if !missing.is_empty() {
        V::Fail(format!("沒有這幾格:{}", missing.join("、")))
    } else if !cancelled.is_empty() {
        V::Cancelled(format!("被取消:{}", cancelled.join("、")))
    } else {
        V::Pass
    };
    (v, skipped)
}

/// The card whose record names this PR (`#N`) or its branch; the last one wins.
fn card_for<'p>(plan: &'p aib_plan::Plan, pr: u32, branch: &str) -> Option<&'p aib_plan::Node> {
    let tag = format!("#{pr}");
    plan.nodes.iter().rfind(|n| {
        let e = n.evidence.as_deref().unwrap_or("");
        aib_plan::card::tokens(e).into_iter().any(|t| {
            t.trim_end_matches([',', ';', ')', '(']) == tag || (!branch.is_empty() && t == branch)
        })
    })
}

/// The station judge's view of the world for one run.
struct Facts<'w> {
    w: &'w dyn World,
    prs: &'w [aib_plan::prs::Pr],
    /// the main head read once after `refresh` (every item of a run uses the same one)
    main: &'w str,
}

impl aib_plan::card::Facts for Facts<'_> {
    fn pending(&self, name: &str) -> Option<String> {
        self.w.pending(name)
    }
    fn in_main(&self, rev: &str) -> Option<bool> {
        if self.main.is_empty() {
            return None;
        }
        self.w.ancestor(rev, self.main)
    }
    fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
        self.w.ancestor(a, b)
    }
    fn pr(&self, n: u32) -> Option<(bool, String)> {
        self.prs
            .iter()
            .find(|p| p.number == n)
            .map(|p| (true, p.head.clone()))
    }
    fn pr_of_branch(&self, branch: &str) -> Option<(bool, String)> {
        self.prs
            .iter()
            .find(|p| p.branch == branch)
            .map(|p| (true, p.head.clone()))
    }
}

/// 審過 (IFACE r2 §2 M1): integration PRs are unread; otherwise the station judge, fed the
/// card's record followed by `#N <current head>` (its head is the last 40-hex in the text, so
/// a review of an older head fails there: "審的頭 X 不是 Y").
fn judge_review(
    w: &dyn World,
    main: &str,
    plan: Option<&aib_plan::Plan>,
    pr: &aib_plan::prs::Pr,
    facts: &Facts,
) -> (V, Option<String>) {
    if pr.branch.starts_with("integ/") {
        return (
            V::Unread(format!("整合 PR:看 CHECK-<名>-{}.md", short(&pr.head))),
            None,
        );
    }
    match w.first_parent_merges(main, &pr.head) {
        Ok(n) if n >= 2 => {
            return (
                V::Unread(format!(
                    "整合 PR(第一父鏈上 {n} 個合併):看 CHECK-<名>-{}.md",
                    short(&pr.head)
                )),
                None,
            )
        }
        Ok(_) => {}
        Err(e) => return (V::Unread(format!("數不出合併數:{e}")), None),
    }
    let Some(plan) = plan else {
        return (V::Unread("流程表讀不到".into()), None);
    };
    let Some(node) = card_for(plan, pr.number, &pr.branch) else {
        return (
            V::Unread(format!("沒有卡寫這張 PR(#{} / {})", pr.number, pr.branch)),
            None,
        );
    };
    // the PR's head now, last in the text: the station judge takes the last 40-hex as the head
    let head_now: &str = &pr.head;
    let record = record_for_head(node.evidence.as_deref().unwrap_or(""), head_now);
    if record.is_empty() {
        // the card names no review / receipt of this head (it moved on, or nothing was written)
        return (
            V::Fail(format!(
                "卡上沒有一句寫到現在的頭 {}:審查與收據要對這個頭",
                short(head_now)
            )),
            Some(node.id.clone()),
        );
    }
    let fact = format!("{record} #{} {head_now}", pr.number);
    let problems = aib_plan::card::entry_problems(3, &fact, node, facts);
    let v = match problems.first() {
        None => V::Pass,
        Some(p) => V::Fail(p.clone()),
    };
    (v, Some(node.id.clone()))
}

/// The pieces of a card's record (split at `|` and new lines) that name this head (its first 8
/// characters, as review and receipt file names do). The station judge reads "放行" anywhere in
/// the text it gets; a whole card's record would let any old "請手動放行" count (review 9fae2967 L4).
pub fn record_for_head(record: &str, head: &str) -> String {
    let key = short(head);
    let keep: Vec<&str> = record
        .split(['|', '\n'])
        .map(str::trim)
        .filter(|seg| !key.is_empty() && seg.contains(key))
        .collect();
    keep.join(" | ")
}

/// CI-based estimates are ordered by run id, which is not a time (review 9fae2967 N3).
fn no_time(mut e: Estimate) -> Estimate {
    e.last_at_ms = None;
    e
}

fn samples_of(jobs: &[CiJob], name: &str, f: impl Fn(&CiJob) -> u64) -> Vec<(i64, u64)> {
    jobs.iter()
        .filter(|j| j.job == name && j.conclusion == "success")
        .map(|j| (j.run, f(j)))
        .collect()
}

/// §3-1: the slowest required cell still to finish; `None` when nothing is left to wait for.
fn until_green(
    required: &[String],
    checks: &[aib_plan::prs::Check],
    jobs: &[CiJob],
    take: usize,
    now: i64,
) -> Option<Estimate> {
    use aib_plan::prs::Cell;
    let mut worst: Option<Estimate> = None;
    for c in checks.iter().filter(|c| c.cell == Cell::Running) {
        if !required.contains(&c.name) {
            continue;
        }
        let e = match c.started {
            Some(t) => {
                let elapsed = u64::try_from(now - t).unwrap_or(0);
                no_time(median::remaining(
                    &samples_of(jobs, &c.name, |j| j.dur_s),
                    take,
                    elapsed,
                ))
            }
            None => {
                let run = median::median_recent(&samples_of(jobs, &c.name, |j| j.dur_s), take);
                let wait = median::median_recent(&samples_of(jobs, &c.name, |j| j.wait_s), take);
                Estimate {
                    secs: run.secs.zip(wait.secs).map(|(a, b)| a + b),
                    n: run.n.min(wait.n),
                    last_at_ms: None,
                }
            }
        };
        worst = Some(match worst {
            None => e,
            // an unknown cell makes the whole wait unknown
            Some(w) => match (w.secs, e.secs) {
                (Some(a), Some(b)) if b > a => e,
                (Some(_), Some(_)) => w,
                (None, _) => w,
                (_, None) => e,
            },
        });
    }
    worst
}

/// §3-2: main's round after a merge = per main push run, its slowest job's wait + run.
fn main_round(jobs: &[CiJob], take: usize) -> Estimate {
    let mut per_run: BTreeMap<i64, u64> = BTreeMap::new();
    for j in jobs
        .iter()
        .filter(|j| j.event == "push" && j.branch == "main" && j.conclusion == "success")
    {
        let e = per_run.entry(j.run).or_default();
        *e = (*e).max(j.dur_s + j.wait_s);
    }
    let s: Vec<(i64, u64)> = per_run.into_iter().collect();
    no_time(median::median_recent(&s, take))
}

/// §3-3: a bumped head's re-receipt; its seat's own runs when it has enough, else the fleet's.
fn rereceipt(branch: &str, runs: &[ReceiptRun], take: usize) -> Estimate {
    let seat = branch
        .split(['-', '/'])
        .find(|t| t.len() == 5 && t.bytes().all(|b| b.is_ascii_hexdigit()));
    if let Some(seat) = seat {
        let s: Vec<(i64, u64)> = runs
            .iter()
            .filter(|r| r.seat == seat)
            .map(|r| (r.at_ms, r.total_s))
            .collect();
        let e = median::median_recent(&s, take);
        if e.secs.is_some() {
            return e;
        }
    }
    let s: Vec<(i64, u64)> = runs.iter().map(|r| (r.at_ms, r.total_s)).collect();
    median::median_recent(&s, take)
}

/// §4 conclusion (r2 M2, DECISION-council-nextstep §4-3): any machine line unread ⇒ never
/// "現在合"; macOS busy ⇒ wait; k ≥ 2 mergeable, no clashes, none never-auto ⇒ chain merge
/// (keep only the last main round); k = 1 ⇒ merge now.
fn conclude(
    load: &Load,
    k: usize,
    clash: bool,
    never_auto_n: usize,
    round: &Estimate,
) -> (String, String) {
    let unread: Vec<&str> = load
        .self_hosted
        .iter()
        .chain(load.receipt.iter())
        .filter(|(_, v)| matches!(v, V::Unread(_)))
        .map(|(n, _)| n.as_str())
        .collect();
    let mins = |e: &Estimate| {
        e.secs
            .map_or("?".to_owned(), |s| s.div_ceil(60).to_string())
    };
    match &load.mac {
        Err(e) => return (format!("不給現在合:macOS 讀不到({e})"), "macos".into()),
        Ok(m) if m.running_main + m.running_pr + m.queued_main + m.queued_pr > 0 => {
            return (
                format!("等 macOS 空(約 {} 分)", mins(&load.mac_wait)),
                "macos".into(),
            )
        }
        Ok(_) => {}
    }
    if k == 0 {
        return ("沒有可合的".into(), "-".into());
    }
    if !unread.is_empty() {
        return (
            format!("不給現在合:{} 讀不到", unread.join("、")),
            unread.join(","),
        );
    }
    if k >= 2 && !clash && never_auto_n == 0 {
        return (
            format!("連合 {k} 張(只留最後一輪,約 {} 分)", mins(round)),
            "mergeable".into(),
        );
    }
    if k >= 2 {
        return ("逐張合(有互撞或要人按的)".into(), "mergeable".into());
    }
    ("現在合".into(), "macos".into())
}

/// One `ready` run. `prev`: last run's `(pr, head)` that were mergeable.
pub fn ready(w: &dyn World, cfg: &Cfg, prev: &BTreeSet<(u32, String)>) -> Report {
    let mut unread = Vec::new();
    let now = w.now_s();
    let refreshed = w.refresh();
    // no refresh ⇒ no main: every PR's "main" item is unread (never measured on an old main)
    let main = match refreshed.and_then(|()| w.main_head()) {
        Ok(h) => h,
        Err(e) => {
            unread.push(format!("主線沒更新或讀不到:{e}"));
            String::new()
        }
    };
    let (prs, extra) = match w.open_prs() {
        Ok(t) if !t.trim().is_empty() => match aib_plan::prs::parse_gh(&t) {
            Ok(p) => (p, drafts_and_bodies(&t)),
            Err(e) => {
                unread.push(format!("gh PR 清單讀不懂:{e}"));
                (Vec::new(), BTreeMap::new())
            }
        },
        Ok(_) => {
            unread.push("gh PR 清單是空的回覆".into());
            (Vec::new(), BTreeMap::new())
        }
        Err(e) => {
            unread.push(format!("gh PR 清單讀不到:{e}"));
            (Vec::new(), BTreeMap::new())
        }
    };
    let required = w.required_checks();
    let plan = w.plan().ok().and_then(|t| aib_plan::parse(&t).ok());
    let jobs = w.ci_jobs().unwrap_or_default();
    let runs = w.receipt_runs().unwrap_or_default();
    let facts = Facts {
        w,
        prs: &prs,
        main: &main,
    };
    let main_wf: BTreeMap<String, String> = if main.is_empty() {
        BTreeMap::new()
    } else {
        w.workflows(&main)
            .unwrap_or_default()
            .into_iter()
            .filter_map(|(p, _)| w.show(&main, &p).map(|t| (slash(&p), t)))
            .collect()
    };

    let mut out: Vec<PrReport> = Vec::new();
    for pr in &prs {
        let mut items: BTreeMap<&'static str, V> = BTreeMap::new();
        let (draft, body) = extra
            .get(&pr.number)
            .cloned()
            .unwrap_or((true, String::new()));
        items.insert(
            "base",
            if pr.base == cfg.main {
                V::Pass
            } else {
                V::Fail(format!("基底是 {}", pr.base))
            },
        );
        items.insert(
            "draft",
            if draft {
                V::Fail("還是草稿".into())
            } else {
                V::Pass
            },
        );
        let (rv, card) = judge_review(w, &main, plan.as_ref(), pr, &facts);
        items.insert("review", rv);
        let (cv, skipped) = match &required {
            Ok(r) if !r.is_empty() => judge_checks(r, &pr.checks),
            Ok(_) => (V::Unread("必要格名單是空的".into()), Vec::new()),
            Err(e) => (V::Unread(format!("必要格讀不到:{e}")), Vec::new()),
        };
        items.insert("checks", cv);
        let (mv, tree) = if main.is_empty() {
            (V::Unread("主線頭讀不到".into()), None)
        } else {
            match w.merge_tree(&main, &pr.head) {
                Merge::Clean(t) => (V::Pass, Some(t)),
                Merge::Conflict(f) => (V::Fail(format!("跟主線衝突:{}", f.join("、"))), None),
                Merge::Err(e) => (V::Unread(format!("merge-tree:{e}")), None),
            }
        };
        items.insert("main", mv);
        let mut workflow_max = None;
        let (wv, sv) = match &tree {
            None => (
                V::Unread("沒有合後樹".into()),
                V::Unread("沒有合後樹".into()),
            ),
            Some(t) => match w.workflows(t) {
                Err(e) => (V::Unread(format!("流程檔讀不到:{e}")), V::Unread(e)),
                Ok(list) => {
                    let mut list: Vec<(String, u64)> =
                        list.into_iter().map(|(p, n)| (slash(&p), n)).collect();
                    list.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
                    workflow_max = list.first().cloned();
                    let over: Vec<String> = list
                        .iter()
                        .filter(|(_, n)| *n >= cfg.workflow_cap)
                        .map(|(p, n)| format!("{p} {n}"))
                        .collect();
                    let wv = if over.is_empty() {
                        V::Pass
                    } else {
                        V::Fail(format!("超過 {}:{}", cfg.workflow_cap, over.join("、")))
                    };
                    let allow = steps::allow_lines(&body);
                    let mut lost = Vec::new();
                    let mut sv = V::Pass;
                    for (p, before) in &main_wf {
                        let Some(after) = w.show(t, p) else {
                            lost.push(format!("{p} 整支不見"));
                            continue;
                        };
                        match steps::compare(before, &after, &allow) {
                            Err(e) => sv = V::Unread(format!("{p}:{e}")),
                            Ok(r) if !r.ok() => {
                                for m in r.missing {
                                    lost.push(format!("{p} 少了 {m}"));
                                }
                                for d in r.duplicate {
                                    lost.push(format!("{p} 重名 {d}"));
                                }
                                for a in r.stale_allow {
                                    lost.push(format!("{p} 對不上的 step-removed {a}"));
                                }
                            }
                            Ok(_) => {}
                        }
                    }
                    if !lost.is_empty() {
                        sv = V::Fail(lost.join("、"));
                    }
                    (wv, sv)
                }
            },
        };
        items.insert("workflow", wv);
        items.insert("steps", sv);
        let mergeable = items.values().all(|v| *v == V::Pass);
        let never_auto = match w.changed(&main, &pr.head) {
            Ok(paths) => {
                let new_pkgs: Vec<String> = match &tree {
                    Some(t) => {
                        let before =
                            lock_packages(&w.show(&main, "Cargo.lock").unwrap_or_default());
                        let after = lock_packages(&w.show(t, "Cargo.lock").unwrap_or_default());
                        after.difference(&before).cloned().collect()
                    }
                    None => Vec::new(),
                };
                never_auto_reasons(cfg, &paths, &new_pkgs)
            }
            Err(e) => vec![format!("改了哪些檔讀不到({e}):照班次")],
        };
        let until = match &required {
            Ok(r) => until_green(r, &pr.checks, &jobs, cfg.take, now),
            Err(_) => None,
        };
        let reuse = match (&tree, w.pr_merge_tree(pr.number)) {
            (Some(t), Some(p)) if *t == p => V::Pass,
            (Some(_), Some(p)) => V::Fail(format!("PR 那輪測的樹 {} 不是合後樹", short(&p))),
            (_, None) => V::Unread("讀不到 PR 那輪的合併樹".into()),
            (None, _) => V::Unread("沒有合後樹".into()),
        };
        out.push(PrReport {
            pr: pr.number,
            head: pr.head.clone(),
            branch: pr.branch.clone(),
            card,
            items,
            merge_tree: tree,
            workflow_max,
            skipped_cells: skipped,
            mergeable,
            not_with: Vec::new(),
            bumps: Vec::new(),
            never_auto,
            until_green: until,
            rereceipt: Vec::new(),
            reuse,
        });
    }

    // pairs among the mergeable ones, and who each would bump (§2 last two rows)
    let ok: Vec<(u32, String)> = out
        .iter()
        .filter(|p| p.mergeable)
        .map(|p| (p.pr, p.head.clone()))
        .collect();
    let mut clash = false;
    for i in 0..ok.len() {
        for j in i + 1..ok.len() {
            if let Merge::Conflict(_) | Merge::Err(_) = w.merge_after(&main, &ok[i].1, &ok[j].1) {
                clash = true;
                for (a, b) in [(i, j), (j, i)] {
                    if let Some(p) = out.iter_mut().find(|p| p.pr == ok[a].0) {
                        p.not_with.push(ok[b].0);
                        p.never_auto.push(format!("跟 #{} 互撞：照班次", ok[b].0));
                    }
                }
            }
        }
    }
    let mut others: Vec<(String, String)> = out
        .iter()
        .map(|p| (format!("#{} {}", p.pr, p.branch), p.head.clone()))
        .collect();
    others.extend(w.side_heads().unwrap_or_default());
    for p in out.iter_mut().filter(|p| p.mergeable) {
        for (name, h) in &others {
            if *h == p.head {
                continue;
            }
            let clean_now = matches!(w.merge_tree(&main, h), Merge::Clean(_));
            if clean_now && matches!(w.merge_after(&main, &p.head, h), Merge::Conflict(_)) {
                p.bumps.push(name.clone());
                let branch = name.rsplit(' ').next().unwrap_or(name);
                p.rereceipt
                    .push((name.clone(), rereceipt(branch, &runs, cfg.take)));
            }
        }
    }
    // order: fewest bumps first, then PR number (REQ-merge-check §2 order not written yet, Q4)
    let mut order: Vec<&PrReport> = out.iter().filter(|p| p.mergeable).collect();
    order.sort_by_key(|p| (p.bumps.len(), p.pr));
    let order: Vec<u32> = order.into_iter().map(|p| p.pr).collect();

    let round = main_round(&jobs, cfg.take);
    let mac_jobs: Vec<(i64, u64)> = jobs
        .iter()
        .filter(|j| j.macos)
        .map(|j| (j.run, j.wait_s))
        .collect();
    let runners = w.runners();
    let releasing = w.releasing();
    let self_hosted: Vec<(String, V)> = cfg
        .self_hosted
        .iter()
        .map(|name| {
            let v = match (&runners, &releasing) {
                (Err(e), _) | (_, Err(e)) => V::Unread(e.clone()),
                (Ok(rs), Ok(rel)) => match rs.iter().find(|r| r.name == *name) {
                    None => V::Unread(format!("{name} 不在 runner 清單")),
                    Some(r) if !r.online => V::Fail("離線".into()),
                    Some(r) if r.busy || *rel => {
                        V::Fail(if *rel { "發版中" } else { "手上有工作" }.into())
                    }
                    Some(_) => V::Pass,
                },
            };
            (name.clone(), v)
        })
        .collect();
    // Q3 乙 (5d33a 同意): slice 1 does not read the receipt machines' leases
    let receipt: Vec<(String, V)> = cfg
        .receipt_hosts
        .iter()
        .map(|h| (h.clone(), V::Unread("片 1 不讀收據機租約(Q3 乙)".into())))
        .collect();
    let mut load = Load {
        mac: w.mac_load(),
        mac_wait: no_time(median::median_recent(&mac_jobs, cfg.take)),
        self_hosted,
        receipt,
        verdict: String::new(),
        because: String::new(),
    };
    let never_auto_n = out
        .iter()
        .filter(|p| p.mergeable && !p.never_auto.is_empty())
        .count();
    let (verdict, because) = conclude(&load, order.len(), clash, never_auto_n, &round);
    load.verdict = verdict;
    load.because = because;

    let newly_ready = out
        .iter()
        .filter(|p| p.mergeable && !prev.contains(&(p.pr, p.head.clone())))
        .map(|p| p.pr)
        .collect();
    Report {
        main,
        prs: out,
        order,
        main_round: round,
        load,
        newly_ready,
        unread,
    }
}

fn est_json(e: &Estimate) -> Value {
    json!({"secs": e.secs, "n": e.n, "last": e.last_at_ms})
}

fn v_json(v: &V) -> Value {
    if v.why().is_empty() {
        json!({"v": v.word()})
    } else {
        json!({"v": v.word(), "why": v.why()})
    }
}

/// `aib-mergecheck-ready.v1`; `at` is given so the output is a pure function of the report.
pub fn to_json(r: &Report, at: &str) -> Value {
    let prs: Vec<Value> = r
        .prs
        .iter()
        .map(|p| {
            let mut items = Map::new();
            for k in ITEMS {
                if let Some(v) = p.items.get(k) {
                    items.insert(k.to_owned(), v_json(v));
                }
            }
            json!({
                "pr": p.pr, "head": p.head, "branch": p.branch, "card": p.card,
                "mergeable": p.mergeable, "items": items,
                "merge_tree": p.merge_tree,
                "workflow_max": p.workflow_max.as_ref().map(|(f, n)| json!({"file": f, "bytes": n})),
                "skipped_cells": p.skipped_cells,
                "not_with": p.not_with, "bumps": p.bumps, "never_auto": p.never_auto,
                "eta": {
                    "until_green": p.until_green.as_ref().map(est_json),
                    "rereceipt": p.rereceipt.iter().map(|(h, e)| json!({"head": h, "eta": est_json(e)})).collect::<Vec<_>>(),
                },
                "reuse": v_json(&p.reuse),
            })
        })
        .collect();
    let mac = match &r.load.mac {
        Ok(m) => json!({"running_main": m.running_main, "running_pr": m.running_pr,
                        "queued_main": m.queued_main, "queued_pr": m.queued_pr}),
        Err(e) => json!({"unread": e}),
    };
    let pairs = |l: &[(String, V)]| -> Vec<Value> {
        l.iter()
            .map(|(n, v)| json!({"name": n, "state": v_json(v)}))
            .collect()
    };
    json!({
        "fmt": FMT, "at": at, "main": r.main, "rc": r.rc(), "unread": r.unread,
        "prs": prs, "order": r.order, "newly_ready": r.newly_ready,
        "main_round": est_json(&r.main_round),
        "load": {"macos": mac, "macos_wait": est_json(&r.load.mac_wait),
                 "self_hosted": pairs(&r.load.self_hosted), "receipt": pairs(&r.load.receipt),
                 "verdict": r.load.verdict, "because": r.load.because},
    })
}

fn est_text(e: &Estimate) -> String {
    match e.secs {
        Some(s) => format!("約 {} 分(樣本 {} 次)", s.div_ceil(60), e.n),
        None => format!("未知(樣本 {} 次)", e.n),
    }
}

/// For people; not an interface (use `--json`).
pub fn to_text(r: &Report) -> String {
    let mut s = String::new();
    for u in &r.unread {
        s.push_str(&format!("讀不到:{u}\n"));
    }
    s.push_str(&format!("主線 {}\n", short(&r.main)));
    for p in &r.prs {
        s.push_str(&format!(
            "#{} {} {} {}\n",
            p.pr,
            short(&p.head),
            p.branch,
            if p.mergeable { "可合" } else { "不可合" }
        ));
        for k in ITEMS {
            if let Some(v) = p.items.get(k) {
                s.push_str(&format!("  {k:<8} {} {}\n", v.word(), v.why()));
            }
        }
        if !p.skipped_cells.is_empty() {
            s.push_str(&format!(
                "  skip {} 格:{}\n",
                p.skipped_cells.len(),
                p.skipped_cells.join("、")
            ));
        }
        if !p.not_with.is_empty() {
            let n: Vec<String> = p.not_with.iter().map(|x| format!("#{x}")).collect();
            s.push_str(&format!("  不同時合:{}\n", n.join(" ")));
        }
        if !p.bumps.is_empty() {
            s.push_str(&format!("  合了會撞:{}\n", p.bumps.join("、")));
        }
        for n in &p.never_auto {
            s.push_str(&format!("  {n}\n"));
        }
        if let Some(e) = &p.until_green {
            s.push_str(&format!("  還要等 {}\n", est_text(e)));
        }
        for (h, e) in &p.rereceipt {
            s.push_str(&format!("  {h} 重收據 {}\n", est_text(e)));
        }
        s.push_str(&format!(
            "  主線那輪可沿用 PR 的結果:{} {}\n",
            p.reuse.word(),
            p.reuse.why()
        ));
    }
    let k = r.order.len() as u64;
    s.push_str(&format!(
        "主線那輪:逐張等 {}× {};連合 {}\n",
        k,
        est_text(&r.main_round),
        est_text(&r.main_round)
    ));
    s.push_str(&format!("結論:{}(看 {})\n", r.load.verdict, r.load.because));
    s
}

/// `last-ready.json` ⇒ the `(pr, head)` that were mergeable.
pub fn parse_prev(text: &str) -> BTreeSet<(u32, String)> {
    let mut m = BTreeSet::new();
    if let Ok(Value::Array(a)) = serde_json::from_str::<Value>(text) {
        for x in a {
            if let (Some(n), Some(h)) = (
                x.get("pr").and_then(Value::as_u64),
                x.get("head").and_then(Value::as_str),
            ) {
                m.insert((n as u32, h.to_owned()));
            }
        }
    }
    m
}

pub fn prev_json(r: &Report) -> String {
    let v: Vec<Value> = r
        .prs
        .iter()
        .filter(|p| p.mergeable)
        .map(|p| json!({"pr": p.pr, "head": p.head}))
        .collect();
    Value::Array(v).to_string()
}
