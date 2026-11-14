// SPDX-License-Identifier: Apache-2.0
//! S1 read-only shadow (plan PLAN-release-tooling v3 9.2 item 3, 1ced7 writes / bb7dc reviews).
//!
//! For one push to main: could each CI cell have reused the result the PR already earned on the
//! very same tree? Only records the answer and the reasons. It never writes a status, never skips
//! a cell, and nothing here is gate evidence. It claims no minutes saved: main's observed wall
//! clock is recorded as an observation only (a saving needs a counterfactual measurement).
//!
//! A cell counts as "could reuse" only when every check below holds (anything unreadable = no):
//! - the PR's latest run of the same workflow file, from this repo (not a fork), finished;
//! - the same job name exists there exactly once, on its latest attempt, with conclusion success;
//! - the merge commit that job checked out (read from its log) has the same tree as main's commit;
//! - same runner image name + version (read from both logs);
//! - the same step list with the same per-step outcome (catches event-dependent paths such as a
//!   PR cell that only printed "cell not run on this PR");
//! - the PR job ran at least half as long as main's job (catches empty runs);
//! - it finished before main's run attempt started, and at most 24 h before (a negative gap = no).
//!
//! The whole push counts as "reusable" only when the cell list is also complete: every workflow
//! file the PR ran has a main run, every job name either side ran exists on both sides exactly
//! once, and no main run came back with an empty job list. Missing or unreadable = not complete.
//! What "should be there" comes from an independent source, the required status checks of the
//! active branch rulesets: each required check must be a job exactly once on the PR side and on
//! main's side, or the verdict is unknown (so a cell both sides lack is never "reusable"). An
//! unreadable or empty required list is unknown too. A job whose step list is missing, empty, or
//! has a step without number / name / conclusion is not compared as equal: steps_unknown.
//!
//! Shadow false green is only counted over cells it could be compared on (main's own run of the
//! cell ended success or failure). With nothing comparable it is unknown, not 0.

use serde_json::Value;
use std::collections::BTreeMap;

pub mod gh;

pub const MAX_AGE_S: i64 = 24 * 3600;
pub const MIN_DURATION_RATIO: f64 = 0.5;

#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub id: u64,
    pub name: String,
    pub status: String,
    pub conclusion: Option<String>,
    pub started_at: Option<i64>,
    pub completed_at: Option<i64>,
    /// (step name, conclusion)
    pub steps: Vec<(String, Option<String>)>,
    /// false = the API gave no step list (or an empty one): never compared as equal
    pub steps_known: bool,
    /// "image-name version", from the job log
    pub image: Option<String>,
    /// the commit actions/checkout fetched, from the job log (PR runs only)
    pub tested_sha: Option<String>,
}

impl Job {
    pub fn seconds(&self) -> Option<i64> {
        match (self.started_at, self.completed_at) {
            (Some(a), Some(b)) if b >= a => Some(b - a),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Run {
    pub id: u64,
    pub path: String,
    pub event: String,
    pub status: String,
    pub conclusion: Option<String>,
    pub run_attempt: u32,
    pub created_at: i64,
    pub run_started_at: Option<i64>,
    pub updated_at: Option<i64>,
    pub head_repo: String,
    pub jobs: Vec<Job>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Facts {
    pub repo: String,
    pub main_sha: String,
    pub main_tree: String,
    pub parents: Vec<String>,
    pub main_runs: Vec<Run>,
    pub cand_runs: Vec<Run>,
    /// tested merge sha -> its tree (absent = unreadable)
    pub tested_trees: BTreeMap<String, String>,
    /// required status check names of the active branch rulesets on main (the expected cell list)
    pub required: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Cell {
    pub workflow: String,
    pub job: String,
    pub reusable: bool,
    pub reasons: Vec<String>,
    pub main_seconds: Option<i64>,
    pub main_conclusion: Option<String>,
    pub cand_job_id: Option<u64>,
    pub cand_run_attempt: Option<u32>,
    pub cand_completed_at: Option<i64>,
    /// main's run of this cell (ledger v1 `run_id`)
    pub main_run_id: Option<u64>,
    /// main's run attempt (ledger v1 `run_attempt`)
    pub main_run_attempt: Option<u32>,
    /// main's job id (ledger v1 `job_id`)
    pub main_job_id: Option<u64>,
    /// main job's runner image name + version (ledger v1 `runner`)
    pub main_image: Option<String>,
    /// the merge commit the PR job checked out (ledger v1 `reused_from.sha`)
    pub cand_tested_sha: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Verdict {
    pub main_sha: String,
    /// reusable | partial | not_reusable | wait | unknown | not_merge
    pub state: String,
    pub note: String,
    pub cells: Vec<Cell>,
    /// observed only, never a saving: main's wall clock (first run start -> last run end)
    pub observed_main_wall_minutes: Option<f64>,
    /// predicted reusable and main's own run of that cell failed. None = nothing comparable (unknown)
    pub shadow_false_green: Option<Vec<String>>,
    /// predicted-reusable cells whose main result was success or failure (the false-green denominator)
    pub compared_cells: usize,
    /// predicted-reusable cells main cancelled / skipped / left without a conclusion
    pub uncomparable_cells: Vec<String>,
    pub main_created_at: Option<i64>,
    /// main commit's tree (None when the commit itself could not be read)
    pub main_tree: Option<String>,
}

/// The latest run per workflow path (by created_at, then id).
pub fn latest_per_path(runs: &[Run]) -> BTreeMap<String, &Run> {
    let mut out: BTreeMap<String, &Run> = BTreeMap::new();
    for r in runs {
        let newer = match out.get(&r.path) {
            Some(cur) => (r.created_at, r.id) > (cur.created_at, cur.id),
            None => true,
        };
        if newer {
            out.insert(r.path.clone(), r);
        }
    }
    out
}

fn verdict(f: &Facts, state: &str, note: &str) -> Verdict {
    Verdict {
        main_sha: f.main_sha.clone(),
        state: state.into(),
        note: note.into(),
        cells: vec![],
        observed_main_wall_minutes: None,
        shadow_false_green: None,
        compared_cells: 0,
        uncomparable_cells: vec![],
        main_created_at: f.main_runs.iter().map(|r| r.created_at).min(),
        main_tree: Some(f.main_tree.clone()).filter(|t| !t.is_empty()),
    }
}

pub fn judge(f: &Facts) -> Verdict {
    if f.parents.len() != 2 {
        return verdict(
            f,
            "not_merge",
            "not a two-parent merge commit: no PR-tested tree to compare",
        );
    }
    let mains = latest_per_path(&f.main_runs);
    if mains.is_empty() {
        return verdict(f, "unknown", "no push run for this commit");
    }
    if let Some(r) = mains
        .values()
        .find(|r| r.status != "completed" || r.jobs.iter().any(|j| j.status != "completed"))
    {
        return verdict(
            f,
            "wait",
            &format!("main run {} ({}) has not finished", r.id, r.path),
        );
    }
    if let Some(r) = mains.values().find(|r| r.jobs.is_empty()) {
        return verdict(
            f,
            "unknown",
            &format!("main run {} ({}) came back with no jobs", r.id, r.path),
        );
    }
    let cands = latest_per_path(&f.cand_runs);
    let mut cells = vec![];
    for (path, m) in &mains {
        for mj in &m.jobs {
            let mut c = judge_cell(f, path, m, mj, cands.get(path).copied());
            if m.jobs.iter().filter(|j| j.name == mj.name).count() > 1 {
                c.reasons
                    .push("ambiguous_main_job: main's run has this job name more than once".into());
                c.reusable = false;
            }
            cells.push(c);
        }
        if let Some(cr) = cands.get(path) {
            for cj in &cr.jobs {
                if !m.jobs.iter().any(|j| j.name == cj.name) {
                    cells.push(missing_cell(
                        path,
                        &cj.name,
                        "main_job_missing: the PR ran this job, main's run has no job named so",
                    ));
                }
            }
        }
    }
    for path in cands.keys() {
        if !mains.contains_key(path) {
            cells.push(missing_cell(
                path,
                "(whole workflow)",
                "main_workflow_missing: the PR ran this workflow file, main's push has no run of it",
            ));
        }
    }
    let mut v = verdict(f, "not_reusable", "");
    let mut false_green = vec![];
    for c in cells.iter().filter(|c| c.reusable) {
        let name = format!("{} / {}", c.workflow, c.job);
        match c.main_conclusion.as_deref() {
            Some("success") => v.compared_cells += 1,
            Some("failure") => {
                v.compared_cells += 1;
                false_green.push(name);
            }
            _ => v.uncomparable_cells.push(name),
        }
    }
    v.shadow_false_green = (v.compared_cells > 0).then_some(false_green);
    let missing: Vec<String> = f
        .required
        .iter()
        .filter_map(|name| {
            let (pr, mn) = (jobs_named(&cands, name), jobs_named(&mains, name));
            (pr != 1 || mn != 1).then(|| format!("{name} (PR {pr}, main {mn})"))
        })
        .collect();
    let all = !cells.is_empty() && cells.iter().all(|c| c.reusable);
    let any = cells.iter().any(|c| c.reusable);
    v.state = if f.required.is_empty() {
        v.note = "required_list_empty: no required status checks read, completeness unknown".into();
        "unknown"
    } else if !missing.is_empty() {
        v.note = format!(
            "required_check_missing: each required check must be one job on both sides: {}",
            missing.join("; ")
        );
        "unknown"
    } else if all {
        "reusable"
    } else if any {
        "partial"
    } else {
        "not_reusable"
    }
    .into();
    let start = mains.values().filter_map(|r| r.run_started_at).min();
    let end = mains.values().filter_map(|r| r.updated_at).max();
    if let (Some(a), Some(b)) = (start, end) {
        if b >= a
            && mains
                .values()
                .all(|r| r.run_started_at.is_some() && r.updated_at.is_some())
        {
            v.observed_main_wall_minutes = Some(((b - a) as f64 / 60.0 * 10.0).round() / 10.0);
        }
    }
    v.cells = cells;
    v
}

fn jobs_named(runs: &BTreeMap<String, &Run>, name: &str) -> usize {
    runs.values()
        .flat_map(|r| r.jobs.iter())
        .filter(|j| j.name == name)
        .count()
}

fn missing_cell(path: &str, job: &str, why: &str) -> Cell {
    Cell {
        workflow: path.into(),
        job: job.into(),
        reusable: false,
        reasons: vec![why.into()],
        main_seconds: None,
        main_conclusion: None,
        cand_job_id: None,
        cand_run_attempt: None,
        cand_completed_at: None,
        main_run_id: None,
        main_run_attempt: None,
        main_job_id: None,
        main_image: None,
        cand_tested_sha: None,
    }
}

fn judge_cell(f: &Facts, path: &str, m: &Run, mj: &Job, cand: Option<&Run>) -> Cell {
    let mut c = Cell {
        workflow: path.into(),
        job: mj.name.clone(),
        reusable: false,
        reasons: vec![],
        main_seconds: mj.seconds(),
        main_conclusion: mj.conclusion.clone(),
        cand_job_id: None,
        cand_run_attempt: None,
        cand_completed_at: None,
        main_run_id: Some(m.id),
        main_run_attempt: Some(m.run_attempt),
        main_job_id: Some(mj.id),
        main_image: mj.image.clone(),
        cand_tested_sha: None,
    };
    let Some(cr) = cand else {
        c.reasons.push(
            "no_candidate_run: the PR head has no pull_request run of this workflow file".into(),
        );
        return c;
    };
    if cr.status != "completed" {
        c.reasons.push(format!(
            "candidate_not_completed: run {} is {}",
            cr.id, cr.status
        ));
        return c;
    }
    if cr.head_repo != f.repo {
        c.reasons
            .push(format!("fork: candidate run comes from {}", cr.head_repo));
    }
    let same: Vec<&Job> = cr.jobs.iter().filter(|j| j.name == mj.name).collect();
    let cj = match same.as_slice() {
        [one] => *one,
        [] => {
            c.reasons.push(format!(
                "no_candidate_job: run {} attempt {} has no job named so",
                cr.id, cr.run_attempt
            ));
            return c;
        }
        _ => {
            c.reasons.push(format!(
                "ambiguous_job: {} jobs share this name",
                same.len()
            ));
            return c;
        }
    };
    c.cand_job_id = Some(cj.id);
    c.cand_run_attempt = Some(cr.run_attempt);
    c.cand_completed_at = cj.completed_at;
    c.cand_tested_sha = cj.tested_sha.clone();
    if cj.conclusion.as_deref() != Some("success") {
        c.reasons.push(format!(
            "candidate_not_success: job {} (run {} attempt {}) is {}",
            cj.id,
            cr.id,
            cr.run_attempt,
            cj.conclusion.as_deref().unwrap_or("none")
        ));
    }
    // main's attempt start (run_started_at moves with a re-run attempt; created_at does not)
    match (cj.completed_at, m.run_started_at) {
        (Some(done), Some(start)) if done > start => c.reasons.push(format!(
            "finished_after_main: PR job finished {} s after main's run attempt {} started",
            done - start,
            m.run_attempt
        )),
        (Some(done), Some(start)) if start - done <= MAX_AGE_S => {}
        (Some(done), Some(start)) => c.reasons.push(format!(
            "older_than_24h: finished {} h before main's run attempt {} started",
            (start - done) / 3600,
            m.run_attempt
        )),
        _ => c.reasons.push("candidate_time_unknown".into()),
    }
    match cj.tested_sha.as_deref() {
        None => c
            .reasons
            .push("tested_sha_unknown: no checkout line in the PR job log".into()),
        Some(sha) => match f.tested_trees.get(sha) {
            None => c.reasons.push(format!(
                "tested_tree_unknown: commit {} unreadable",
                short(sha)
            )),
            Some(t) if *t == f.main_tree => {}
            Some(t) => c.reasons.push(format!(
                "tree_differs: PR tested tree {} != main tree {}",
                short(t),
                short(&f.main_tree)
            )),
        },
    }
    match (cj.image.as_deref(), mj.image.as_deref()) {
        (Some(a), Some(b)) if a == b => {}
        (Some(a), Some(b)) => c.reasons.push(format!("image_differs: PR {a} / main {b}")),
        _ => c
            .reasons
            .push("image_unknown: runner image unreadable in a log".into()),
    }
    if !cj.steps_known || !mj.steps_known {
        c.reasons.push(format!(
            "steps_unknown: step list missing or incomplete on {}",
            match (cj.steps_known, mj.steps_known) {
                (false, false) => "both sides",
                (false, true) => "the PR side",
                _ => "main's side",
            }
        ));
    } else if cj.steps != mj.steps {
        c.reasons.push(format!(
            "steps_differ: {}",
            first_step_diff(&cj.steps, &mj.steps)
        ));
    }
    match (cj.seconds(), mj.seconds()) {
        (Some(a), Some(b)) if (a as f64) >= MIN_DURATION_RATIO * b as f64 => {}
        (Some(a), Some(b)) => c
            .reasons
            .push(format!("too_short: PR job ran {a} s, main ran {b} s")),
        _ => c.reasons.push("duration_unknown".into()),
    }
    c.reusable = c.reasons.is_empty();
    c
}

fn first_step_diff(a: &[(String, Option<String>)], b: &[(String, Option<String>)]) -> String {
    let fmt = |s: Option<&(String, Option<String>)>| match s {
        Some((n, c)) => format!("{n}={}", c.as_deref().unwrap_or("none")),
        None => "(none)".into(),
    };
    let n = a.len().max(b.len());
    for i in 0..n {
        if a.get(i) != b.get(i) {
            return format!(
                "step {}: PR {} / main {}",
                i + 1,
                fmt(a.get(i)),
                fmt(b.get(i))
            );
        }
    }
    "same".into()
}

fn short(s: &str) -> &str {
    s.get(..8).unwrap_or(s)
}

// ---------- parsing GitHub JSON and logs ----------

/// "2026-11-09T04:20:16Z" (or with fractional seconds / +00:00) -> unix seconds.
pub fn parse_time(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 19
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b'T'
        || b[13] != b':'
        || b[16] != b':'
    {
        return None;
    }
    let num = |a: usize, z: usize| s.get(a..z)?.parse::<i64>().ok();
    let (y, mo, d, h, mi, se) = (
        num(0, 4)?,
        num(5, 7)?,
        num(8, 10)?,
        num(11, 13)?,
        num(14, 16)?,
        num(17, 19)?,
    );
    let rest = &s[19..];
    let rest = rest.trim_start_matches(|ch: char| ch == '.' || ch.is_ascii_digit());
    if !(rest == "Z" || rest == "+00:00") {
        return None;
    }
    // days from civil (Howard Hinnant)
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = (mo + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86400 + h * 3600 + mi * 60 + se)
}

/// unix seconds -> "YYYY-MM-DD" after shifting by `offset_h` hours.
pub fn day_key(t: i64, offset_h: i64) -> String {
    let z = (t + offset_h * 3600).div_euclid(86400) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn os(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn t(v: &Value, k: &str) -> Option<i64> {
    v.get(k).and_then(Value::as_str).and_then(parse_time)
}

pub fn run_from_json(v: &Value) -> Option<Run> {
    Some(Run {
        id: v.get("id")?.as_u64()?,
        path: s(v, "path"),
        event: s(v, "event"),
        status: s(v, "status"),
        conclusion: os(v, "conclusion"),
        run_attempt: u32::try_from(v.get("run_attempt").and_then(Value::as_u64).unwrap_or(1))
            .unwrap_or(1),
        created_at: t(v, "created_at")?,
        run_started_at: t(v, "run_started_at"),
        updated_at: t(v, "updated_at"),
        head_repo: v
            .get("head_repository")
            .map(|h| s(h, "full_name"))
            .unwrap_or_default(),
        jobs: vec![],
    })
}

pub fn job_from_json(v: &Value) -> Option<Job> {
    let raw = v.get("steps").and_then(Value::as_array);
    let steps: Vec<(String, Option<String>)> = raw
        .map(|a| {
            a.iter()
                .map(|st| (s(st, "name"), os(st, "conclusion")))
                .collect()
        })
        .unwrap_or_default();
    // every step must carry a number, a non-empty name and a conclusion; anything less = unknown
    let steps_known = raw.is_some_and(|a| {
        !a.is_empty()
            && a.iter().all(|st| {
                st.get("number").and_then(Value::as_u64).is_some()
                    && st
                        .get("name")
                        .and_then(Value::as_str)
                        .is_some_and(|n| !n.is_empty())
                    && st.get("conclusion").and_then(Value::as_str).is_some()
            })
    });
    Some(Job {
        id: v.get("id")?.as_u64()?,
        name: s(v, "name"),
        status: s(v, "status"),
        conclusion: os(v, "conclusion"),
        started_at: t(v, "started_at"),
        completed_at: t(v, "completed_at"),
        steps,
        steps_known,
        image: None,
        tested_sha: None,
    })
}

fn after_stamp(line: &str) -> &str {
    // log lines start with "2026-11-09T02:53:17.6272667Z "
    match line.split_once(' ') {
        Some((stamp, rest)) if parse_time(stamp).is_some() => rest,
        _ => line,
    }
}

/// "Image: X" + "Version: Y" inside the "##[group]Runner Image" group (not "...Provisioner").
pub fn parse_image(log: &str) -> Option<String> {
    let mut inside = false;
    let (mut name, mut ver) = (None, None);
    for raw in log.lines() {
        let line = after_stamp(raw).trim_end();
        if line.starts_with("##[group]") {
            inside = line == "##[group]Runner Image";
            continue;
        }
        if line.starts_with("##[endgroup]") {
            if inside {
                break;
            }
            continue;
        }
        if inside {
            if let Some(x) = line.strip_prefix("Image: ") {
                name = Some(x.trim().to_string());
            } else if let Some(x) = line.strip_prefix("Version: ") {
                ver = Some(x.trim().to_string());
            }
        }
    }
    Some(format!("{} {}", name?, ver?))
}

/// actions/checkout fetches "+<40 hex>:refs/remotes/pull/<N>/merge". Exactly one distinct sha, or None.
pub fn parse_tested_sha(log: &str) -> Option<String> {
    let mut found: Vec<String> = vec![];
    for line in log.lines() {
        let mut rest = line;
        while let Some(i) = rest.find(":refs/remotes/pull/") {
            let before = &rest[..i];
            if before.len() >= 41 {
                let cand = &before[before.len() - 40..];
                let plus = before.as_bytes()[before.len() - 41];
                if plus == b'+'
                    && cand
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                    && !found.iter().any(|x| x == cand)
                {
                    found.push(cand.to_string());
                }
            }
            rest = &rest[i + 1..];
        }
    }
    match found.as_slice() {
        [one] => Some(one.clone()),
        _ => None,
    }
}

// ---------- ledger and the daily line ----------

/// The commit this binary was built from (40 hex, or `unknown`) and whether the sources had
/// uncommitted changes (`build.rs`), in the shape `aib_release::host_build::parse_info` reads
/// (S68c: `aib-release binsync` checks it before installing). One literal, so the bytes sit
/// together in the file.
pub const BUILD_INFO: &str = concat!(
    "aib-build-info version=",
    env!("CARGO_PKG_VERSION"),
    " commit=",
    env!("AIB_CIREUSE_COMMIT"),
    " dirty=",
    env!("AIB_CIREUSE_DIRTY"),
    ";"
);

/// Which tool and target the file is: the build-info mark alone is the same in every tool.
pub const TOOL_INFO: &str = concat!(
    "aib-tool-info name=aib-cireuse target=",
    env!("AIB_CIREUSE_TARGET"),
    ";"
);

/// Shared reuse ledger row schema (S66 / S68 agreed shape, PLAN-s66 §8.2–8.3).
pub const LEDGER_SCHEMA: &str = "ez2-reuse-ledger.v1";
/// The `kind` aib-cireuse writes. A prediction: shown on the cockpit, never a reuse source.
pub const KIND_PREDICTION: &str = "reuse_prediction";

/// What `could_save_s` is, written into every row so nobody reads it as a saving.
pub const COULD_SAVE_KIND: &str = "upper bound: runner seconds main spent on cells predicted reusable; not wall clock, not a measured saving";

/// Runner seconds main spent on this cell when it is predicted reusable, 0 when it is not.
/// `None` when it is reusable but main's duration is unknown.
pub fn cell_could_save_s(c: &Cell) -> Option<i64> {
    if c.reusable {
        c.main_seconds
    } else {
        Some(0)
    }
}

/// The push's `could_save_s`: the sum over its cells for a judged push (`reusable`, `partial`,
/// `not_reusable`, `not_merge`); `None` while it is `wait` / `unknown` or when a reusable
/// cell's duration is unknown (a partial sum would read as the whole).
pub fn could_save_s(v: &Verdict) -> Option<i64> {
    match v.state.as_str() {
        "reusable" | "partial" | "not_reusable" | "not_merge" => {
            v.cells.iter().map(cell_could_save_s).sum::<Option<i64>>()
        }
        _ => None,
    }
}

/// Ledger v1 `verdict` of a whole push: only `reusable` is `could_reuse`.
pub fn push_verdict(state: &str) -> &'static str {
    match state {
        "reusable" => "could_reuse",
        "partial" | "not_reusable" | "not_merge" => "cannot_reuse",
        _ => "unknown",
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` for a unix time.
pub fn iso_utc(t: i64) -> String {
    let s = t.rem_euclid(86_400);
    format!(
        "{}T{:02}:{:02}:{:02}Z",
        day_key(t, 0),
        s / 3600,
        s / 60 % 60,
        s % 60
    )
}

fn result_of(conclusion: Option<&str>) -> Value {
    match conclusion {
        Some("success") => "pass".into(),
        Some("failure") => "fail".into(),
        _ => Value::Null,
    }
}

/// Ledger v1 rows for one judged push: one `unit:"push"` row (the cockpit counts these), then
/// one `unit:"cell:<workflow> / <job>"` row per cell. `judged_at` is when the verdict was made.
pub fn ledger_rows(v: &Verdict, repo: &str, judged_at: i64) -> Vec<Value> {
    let common = |unit: String| {
        serde_json::json!({
            "schema": LEDGER_SCHEMA,
            "kind": KIND_PREDICTION,
            "producer": "cireuse",
            "mode": "shadow",
            "repo": repo,
            "head_sha": v.main_sha,
            "tree": v.main_tree,
            "at": iso_utc(judged_at),
            "main_created_at": v.main_created_at,
            "unit": unit,
            "key": Value::Null,
            "could_save_kind": COULD_SAVE_KIND,
            "check": Value::Null,
        })
    };
    let mut push = common("push".into());
    let why: Vec<Value> = if v.note.is_empty() {
        vec![]
    } else {
        vec![serde_json::json!({"input": "note", "new": v.note})]
    };
    for (k, x) in [
        ("verdict", Value::from(push_verdict(&v.state))),
        ("state", v.state.clone().into()),
        ("result", Value::Null),
        ("rerun_why", why.into()),
        ("reused_from", Value::Null),
        ("could_save_s", could_save_s(v).into()),
        ("spent_s", Value::Null),
        ("runner", Value::Null),
        ("run_id", Value::Null),
        (
            "shadow_false_green",
            serde_json::to_value(&v.shadow_false_green).unwrap_or(Value::Null),
        ),
        ("compared_cells", v.compared_cells.into()),
        // the verdict layer the cockpit shows (DEV-shadow r1 §4.2 / §5 S68):
        // "CI 可沿用 k/n 格(觀察中，未生效;main 這輪 N 分)"
        ("cells_total", v.cells.len().into()),
        (
            "cells_reusable",
            v.cells.iter().filter(|c| c.reusable).count().into(),
        ),
        (
            "observed_main_wall_minutes",
            v.observed_main_wall_minutes.into(),
        ),
        (
            "uncomparable_cells",
            serde_json::to_value(&v.uncomparable_cells).unwrap_or(Value::Null),
        ),
    ] {
        push[k] = x;
    }
    let mut out = vec![push];
    for c in &v.cells {
        let mut r = common(format!("cell:{} / {}", c.workflow, c.job));
        let reused_from = c.cand_job_id.map(|id| {
            serde_json::json!({
                "job_id": id, "run_id": Value::Null, "sha": c.cand_tested_sha,
                "host": "github",
                "at": c.cand_completed_at.map(iso_utc),
            })
        });
        for (k, x) in [
            (
                "verdict",
                Value::from(if c.reusable {
                    "could_reuse"
                } else {
                    "cannot_reuse"
                }),
            ),
            ("result", result_of(c.main_conclusion.as_deref())),
            (
                "rerun_why",
                c.reasons
                    .iter()
                    .map(|r| serde_json::json!({"input": "reason", "new": r}))
                    .collect::<Vec<_>>()
                    .into(),
            ),
            ("reused_from", reused_from.unwrap_or(Value::Null)),
            ("could_save_s", cell_could_save_s(c).into()),
            ("spent_s", c.main_seconds.into()),
            ("runner", c.main_image.clone().into()),
            ("run_id", c.main_run_id.into()),
            ("run_attempt", c.main_run_attempt.into()),
            ("job_id", c.main_job_id.into()),
            ("workflow", c.workflow.clone().into()),
        ] {
            r[k] = x;
        }
        out.push(r);
    }
    out
}

pub fn verdict_json(v: &Verdict) -> Value {
    serde_json::json!({
        "main_sha": v.main_sha,
        "state": v.state,
        "note": v.note,
        "saved_minutes": Value::Null,
        "saved_minutes_kind": "not measured: a saving needs a counterfactual measurement; the observed wall clock is not one",
        "observed_main_wall_minutes": v.observed_main_wall_minutes,
        "shadow_false_green": v.shadow_false_green,
        "compared_cells": v.compared_cells,
        "uncomparable_cells": v.uncomparable_cells,
        "main_created_at": v.main_created_at,
        "cells": v.cells.iter().map(|c| serde_json::json!({
            "workflow": c.workflow, "job": c.job, "reusable": c.reusable, "reasons": c.reasons,
            "main_seconds": c.main_seconds, "main_conclusion": c.main_conclusion, "cand_job_id": c.cand_job_id,
            "cand_run_attempt": c.cand_run_attempt, "cand_completed_at": c.cand_completed_at,
        })).collect::<Vec<_>>(),
        "writes_status": false,
        "skips_cells": false,
    })
}

/// One line per day from ledger lines (JSON). The last line per main_sha wins.
pub fn day_line(lines: &[String]) -> String {
    let mut by: BTreeMap<String, Value> = BTreeMap::new();
    let mut bad = 0;
    for l in lines {
        match serde_json::from_str::<Value>(l) {
            Ok(v) if v.get("main_sha").and_then(Value::as_str).is_some() => {
                by.insert(s(&v, "main_sha"), v);
            }
            _ if l.trim().is_empty() => {}
            _ => bad += 1,
        }
    }
    let mut count: BTreeMap<String, usize> = BTreeMap::new();
    let (mut compared, mut fg, mut unc, mut wall) = (0u64, 0usize, 0usize, 0.0);
    for v in by.values() {
        *count.entry(s(v, "state")).or_default() += 1;
        compared += v.get("compared_cells").and_then(Value::as_u64).unwrap_or(0);
        fg += v
            .get("shadow_false_green")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        unc += v
            .get("uncomparable_cells")
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        wall += v
            .get("observed_main_wall_minutes")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
    }
    let g = |k: &str| count.get(k).copied().unwrap_or(0);
    let fg_text = if compared == 0 {
        "影子假綠 UNKNOWN(沒有可比較的格)".to_string()
    } else {
        format!("影子假綠 {fg} / 可比較 {compared} 格")
    };
    let mut out = format!(
        "今天 {} 次全可沿用(判了 {} 次:部分可沿用 {}、不可 {}、等跑完 {}、查不到 {}、非合併 {});{fg_text}、未可比較 {unc} 格;main 執行牆鐘合計 {:.0} 分(觀察值,不是省下的時間)",
        g("reusable"),
        by.len(),
        g("partial"),
        g("not_reusable"),
        g("wait"),
        g("unknown"),
        g("not_merge"),
        wall
    );
    if bad > 0 {
        out.push_str(&format!(";帳本壞行 {bad}"));
    }
    out
}

/// The newest judged main push in the span, as the cockpit shows it (DEV-shadow r1 §5 S68:
/// "CI 可沿用 k/n 格(觀察中，未生效;main 這輪 N 分)"; never a "can save" figure).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Latest {
    pub head_sha: String,
    /// raw cireuse state: reusable / partial / not_reusable / not_merge
    pub state: String,
    pub cells_reusable: u64,
    pub cells_total: u64,
    /// main's own observed wall clock for this push (an observation, not a saving)
    pub main_wall_minutes: Option<f64>,
    pub main_created_at: i64,
}

/// One week (or any span) of the ledger, for the cockpit line (S68).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Week {
    /// main pushes with a final verdict (`could_reuse` / `cannot_reuse`) in the span
    pub judged: usize,
    /// of those, `could_reuse` (every cell could have reused the PR's result)
    pub reusable_pushes: usize,
    /// the newest of those (by `main_created_at`)
    pub latest: Option<Latest>,
}

/// Summarise ledger v1 lines: only `schema` = [`LEDGER_SCHEMA`], `kind` = [`KIND_PREDICTION`],
/// `unit` = `push` rows whose `main_created_at` is within `[now - days, now]`. Per `head_sha` the
/// row judged last wins (largest `at`; equal `at` ⇒ the later line), so a re-judge after another
/// workflow finished replaces a `wait` whatever order the artifacts were pulled in. Rows without
/// a time are left out.
pub fn week_summary(lines: &[String], now: i64, days: i64) -> Week {
    let from = now - days * 86_400;
    let mut by: BTreeMap<String, Value> = BTreeMap::new();
    for l in lines {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        if s(&v, "schema") != LEDGER_SCHEMA
            || s(&v, "kind") != KIND_PREDICTION
            || s(&v, "unit") != "push"
            || s(&v, "head_sha").is_empty()
        {
            continue;
        }
        let newer = by
            .get(&s(&v, "head_sha"))
            .is_none_or(|cur| s(&v, "at") >= s(cur, "at"));
        if newer {
            by.insert(s(&v, "head_sha"), v);
        }
    }
    let mut w = Week::default();
    for v in by.values() {
        let Some(at) = v.get("main_created_at").and_then(Value::as_i64) else {
            continue;
        };
        if at < from || at > now {
            continue;
        }
        let verdict = s(v, "verdict");
        if !matches!(verdict.as_str(), "could_reuse" | "cannot_reuse") {
            continue;
        }
        w.judged += 1;
        if verdict == "could_reuse" {
            w.reusable_pushes += 1;
        }
        if w.latest.as_ref().is_none_or(|l| at > l.main_created_at) {
            let n = |k: &str| v.get(k).and_then(Value::as_u64).unwrap_or(0);
            w.latest = Some(Latest {
                head_sha: s(v, "head_sha"),
                state: s(v, "state"),
                cells_reusable: n("cells_reusable"),
                cells_total: n("cells_total"),
                main_wall_minutes: v.get("observed_main_wall_minutes").and_then(Value::as_f64),
                main_created_at: at,
            });
        }
    }
    w
}

/// Lines of `incoming` not already in `existing` (exact line match), in order. Appending them
/// keeps the ledger append-only; readers take the last line per commit.
pub fn new_lines(existing: &str, incoming: &str) -> Vec<String> {
    let have: std::collections::BTreeSet<&str> = existing.lines().map(str::trim_end).collect();
    let mut seen = std::collections::BTreeSet::new();
    incoming
        .lines()
        .map(str::trim_end)
        .filter(|l| !l.is_empty() && !have.contains(l) && seen.insert(*l))
        .map(str::to_string)
        .collect()
}

/// An uploaded ledger artifact that can be downloaded.
#[derive(Debug, Clone, PartialEq)]
pub struct Artifact {
    pub id: u64,
    pub run_id: u64,
    pub name: String,
}

/// From `GET repos/R/actions/artifacts` items: the ones named `name`, not expired, made by a
/// run on `main` of this repo (not a fork), and not in `pulled`: the newest `limit` of them,
/// returned oldest first so they are appended in the order they were made.
/// The ledger is an observation, not gate evidence: this keeps out a fork's look-alike, nothing
/// more (anyone who can push to this repo can write a ledger line).
pub fn artifacts_to_pull(
    items: &[Value],
    name: &str,
    pulled: &std::collections::BTreeSet<u64>,
    limit: usize,
) -> Vec<Artifact> {
    let mut out: Vec<(String, Artifact)> = items
        .iter()
        .filter_map(|a| {
            let id = a.get("id")?.as_u64()?;
            let run_id = a.pointer("/workflow_run/id")?.as_u64()?;
            // a fork's pull_request run can upload an artifact with the same name from a branch it
            // calls `main`: only this repository's own runs count
            let own = a.pointer("/workflow_run/head_repository_id")?.as_u64()?
                == a.pointer("/workflow_run/repository_id")?.as_u64()?;
            let ok = a.get("name")?.as_str()? == name
                && !a.get("expired")?.as_bool()?
                && a.pointer("/workflow_run/head_branch")?.as_str()? == "main"
                && own
                && !pulled.contains(&id);
            ok.then(|| {
                (
                    s(a, "created_at"),
                    Artifact {
                        id,
                        run_id,
                        name: name.to_string(),
                    },
                )
            })
        })
        .collect();
    out.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.id.cmp(&a.1.id)));
    out.truncate(limit);
    out.into_iter().rev().map(|(_, a)| a).collect()
}

/// A ledger file name this tool writes: `YYYY-MM-DD.jsonl` and nothing else (a downloaded
/// artifact may not name a path outside the ledger directory).
pub fn is_ledger_file_name(n: &str) -> bool {
    let b = n.as_bytes();
    n.len() == 16
        && n.ends_with(".jsonl")
        && b[..10].iter().enumerate().all(|(i, c)| {
            if i == 4 || i == 7 {
                *c == b'-'
            } else {
                c.is_ascii_digit()
            }
        })
}

/// The file under the ledger directory that lists artifact ids already pulled, one per line.
pub const PULLED_FILE: &str = ".pulled";

/// Artifact ids already pulled into `dir` (unreadable lines are skipped; no file = none).
pub fn pulled_ids(dir: &std::path::Path) -> std::collections::BTreeSet<u64> {
    std::fs::read_to_string(dir.join(PULLED_FILE))
        .map(|t| t.lines().filter_map(|l| l.trim().parse().ok()).collect())
        .unwrap_or_default()
}

/// Take one downloaded artifact: every top-level `YYYY-MM-DD.jsonl` in `from` gets its new lines
/// appended to the same name in `dir` (other names, folders and links are ignored), then the
/// artifact id is recorded in [`PULLED_FILE`]. Returns how many lines were added. The id is
/// recorded last, so a failure part-way is retried next time (lines already appended are not
/// added twice: [`new_lines`] skips them).
pub fn ingest(
    dir: &std::path::Path,
    art: &Artifact,
    from: &std::path::Path,
) -> Result<usize, String> {
    use std::io::Write as _;
    std::fs::create_dir_all(dir).map_err(|e| format!("ledger: {e}"))?;
    let mut names: Vec<String> = std::fs::read_dir(from)
        .map_err(|e| format!("artifact {}: {e}", art.id))?
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| is_ledger_file_name(n))
        .collect();
    names.sort();
    let mut added = 0;
    for n in names {
        let incoming = std::fs::read_to_string(from.join(&n))
            .map_err(|e| format!("artifact {} {n}: {e}", art.id))?;
        let target = dir.join(&n);
        let existing = std::fs::read_to_string(&target).unwrap_or_default();
        let lines = new_lines(&existing, &incoming);
        if lines.is_empty() {
            continue;
        }
        let mut fh = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&target)
            .map_err(|e| format!("ledger {n}: {e}"))?;
        // a file that does not end in a newline would glue the first new line onto its last one
        if !existing.is_empty() && !existing.ends_with('\n') {
            writeln!(fh).map_err(|e| format!("ledger {n}: {e}"))?;
        }
        for l in &lines {
            writeln!(fh, "{l}").map_err(|e| format!("ledger {n}: {e}"))?;
        }
        added += lines.len();
    }
    let mut fh = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join(PULLED_FILE))
        .map_err(|e| format!("ledger {PULLED_FILE}: {e}"))?;
    writeln!(fh, "{}", art.id).map_err(|e| format!("ledger {PULLED_FILE}: {e}"))?;
    Ok(added)
}

/// Every ledger line under `dir` (all `YYYY-MM-DD.jsonl` files, in name order).
pub fn ledger_lines(dir: &std::path::Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(Result::ok)
                .filter_map(|e| e.file_name().into_string().ok())
                .filter(|n| is_ledger_file_name(n))
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    names
        .iter()
        .filter_map(|n| std::fs::read_to_string(dir.join(n)).ok())
        .flat_map(|t| t.lines().map(str::to_string).collect::<Vec<_>>())
        .collect()
}

/// The `week` command's JSON line (no "can save" figure: DEV-shadow r1 §0 / §6).
pub fn week_json(w: &Week, days: i64) -> Value {
    serde_json::json!({
        "days": days,
        "judged": w.judged,
        "reusable_pushes": w.reusable_pushes,
        "latest": w.latest.as_ref().map(|l| serde_json::json!({
            "head_sha": l.head_sha, "state": l.state,
            "cells_reusable": l.cells_reusable, "cells_total": l.cells_total,
            "main_wall_minutes": l.main_wall_minutes, "main_created_at": l.main_created_at,
        })),
        "mode": "shadow: observation only, not in effect",
    })
}
