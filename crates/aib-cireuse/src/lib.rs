// SPDX-License-Identifier: Apache-2.0
//! S1 read-only shadow (plan PLAN-release-tooling v3 9.2 item 3, 1ced7 writes / bb7dc reviews).
//!
//! For one push to main: could each CI cell have reused the result the PR already earned on the
//! very same tree? Only records the answer, the reasons and the minutes it would have saved.
//! It never writes a status, never skips a cell, and nothing here is gate evidence.
//!
//! A cell counts as "could reuse" only when every check below holds (anything unreadable = no):
//! - the PR's latest run of the same workflow file, from this repo (not a fork), finished;
//! - the same job name exists there exactly once, on its latest attempt, with conclusion success;
//! - the merge commit that job checked out (read from its log) has the same tree as main's commit;
//! - same runner image name + version (read from both logs);
//! - the same step list with the same per-step outcome (catches event-dependent paths such as a
//!   PR cell that only printed "cell not run on this PR");
//! - the PR job ran at least half as long as main's job (catches empty runs);
//! - it finished at most 24 h before main's run was created.

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
}

#[derive(Debug, Clone, PartialEq)]
pub struct Verdict {
    pub main_sha: String,
    /// reusable | partial | not_reusable | wait | unknown | not_merge
    pub state: String,
    pub note: String,
    pub cells: Vec<Cell>,
    /// measured: main's wall clock (first run start -> last run end) when every cell could reuse
    pub save_minutes: Option<f64>,
    /// predicted reusable but main's own run of that cell was not success
    pub shadow_false_green: Vec<String>,
    pub main_created_at: Option<i64>,
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
        save_minutes: None,
        shadow_false_green: vec![],
        main_created_at: f.main_runs.iter().map(|r| r.created_at).min(),
    }
}

pub fn judge(f: &Facts) -> Verdict {
    if f.parents.len() != 2 {
        return verdict(f, "not_merge", "not a two-parent merge commit: no PR-tested tree to compare");
    }
    let mains = latest_per_path(&f.main_runs);
    if mains.is_empty() {
        return verdict(f, "unknown", "no push run for this commit");
    }
    if let Some(r) = mains.values().find(|r| r.status != "completed" || r.jobs.iter().any(|j| j.status != "completed")) {
        return verdict(f, "wait", &format!("main run {} ({}) has not finished", r.id, r.path));
    }
    let cands = latest_per_path(&f.cand_runs);
    let mut cells = vec![];
    for (path, m) in &mains {
        for mj in &m.jobs {
            cells.push(judge_cell(f, path, m, mj, cands.get(path).copied()));
        }
    }
    let mut v = verdict(f, "not_reusable", "");
    v.shadow_false_green = cells
        .iter()
        .filter(|c| c.reusable && c.main_conclusion.as_deref() != Some("success"))
        .map(|c| format!("{} / {}", c.workflow, c.job))
        .collect();
    let all = !cells.is_empty() && cells.iter().all(|c| c.reusable);
    let any = cells.iter().any(|c| c.reusable);
    v.state = if all { "reusable" } else if any { "partial" } else { "not_reusable" }.into();
    if all {
        let start = mains.values().filter_map(|r| r.run_started_at).min();
        let end = mains.values().filter_map(|r| r.updated_at).max();
        if let (Some(a), Some(b)) = (start, end) {
            if b >= a && mains.values().all(|r| r.run_started_at.is_some() && r.updated_at.is_some()) {
                v.save_minutes = Some(((b - a) as f64 / 60.0 * 10.0).round() / 10.0);
            }
        }
        if v.save_minutes.is_none() {
            v.note = "every cell could reuse, but main's wall clock is unreadable: no minutes shown".into();
        }
    }
    v.cells = cells;
    v
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
    };
    let Some(cr) = cand else {
        c.reasons.push("no_candidate_run: the PR head has no pull_request run of this workflow file".into());
        return c;
    };
    if cr.status != "completed" {
        c.reasons.push(format!("candidate_not_completed: run {} is {}", cr.id, cr.status));
        return c;
    }
    if cr.head_repo != f.repo {
        c.reasons.push(format!("fork: candidate run comes from {}", cr.head_repo));
    }
    let same: Vec<&Job> = cr.jobs.iter().filter(|j| j.name == mj.name).collect();
    let cj = match same.as_slice() {
        [one] => *one,
        [] => {
            c.reasons.push(format!("no_candidate_job: run {} attempt {} has no job named so", cr.id, cr.run_attempt));
            return c;
        }
        _ => {
            c.reasons.push(format!("ambiguous_job: {} jobs share this name", same.len()));
            return c;
        }
    };
    c.cand_job_id = Some(cj.id);
    if cj.conclusion.as_deref() != Some("success") {
        c.reasons.push(format!(
            "candidate_not_success: job {} (run {} attempt {}) is {}",
            cj.id,
            cr.id,
            cr.run_attempt,
            cj.conclusion.as_deref().unwrap_or("none")
        ));
    }
    match cj.completed_at {
        Some(done) if m.created_at - done <= MAX_AGE_S => {}
        Some(done) => c.reasons.push(format!("older_than_24h: finished {} h before main's run", (m.created_at - done) / 3600)),
        None => c.reasons.push("candidate_time_unknown".into()),
    }
    match cj.tested_sha.as_deref() {
        None => c.reasons.push("tested_sha_unknown: no checkout line in the PR job log".into()),
        Some(sha) => match f.tested_trees.get(sha) {
            None => c.reasons.push(format!("tested_tree_unknown: commit {} unreadable", short(sha))),
            Some(t) if *t == f.main_tree => {}
            Some(t) => c.reasons.push(format!("tree_differs: PR tested tree {} != main tree {}", short(t), short(&f.main_tree))),
        },
    }
    match (cj.image.as_deref(), mj.image.as_deref()) {
        (Some(a), Some(b)) if a == b => {}
        (Some(a), Some(b)) => c.reasons.push(format!("image_differs: PR {a} / main {b}")),
        _ => c.reasons.push("image_unknown: runner image unreadable in a log".into()),
    }
    if cj.steps != mj.steps {
        c.reasons.push(format!("steps_differ: {}", first_step_diff(&cj.steps, &mj.steps)));
    }
    match (cj.seconds(), mj.seconds()) {
        (Some(a), Some(b)) if (a as f64) >= MIN_DURATION_RATIO * b as f64 => {}
        (Some(a), Some(b)) => c.reasons.push(format!("too_short: PR job ran {a} s, main ran {b} s")),
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
            return format!("step {}: PR {} / main {}", i + 1, fmt(a.get(i)), fmt(b.get(i)));
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
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let num = |a: usize, z: usize| s.get(a..z)?.parse::<i64>().ok();
    let (y, mo, d, h, mi, se) = (num(0, 4)?, num(5, 7)?, num(8, 10)?, num(11, 13)?, num(14, 16)?, num(17, 19)?);
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
        run_attempt: u32::try_from(v.get("run_attempt").and_then(Value::as_u64).unwrap_or(1)).unwrap_or(1),
        created_at: t(v, "created_at")?,
        run_started_at: t(v, "run_started_at"),
        updated_at: t(v, "updated_at"),
        head_repo: v.get("head_repository").map(|h| s(h, "full_name")).unwrap_or_default(),
        jobs: vec![],
    })
}

pub fn job_from_json(v: &Value) -> Option<Job> {
    let steps = v
        .get("steps")
        .and_then(Value::as_array)
        .map(|a| a.iter().map(|st| (s(st, "name"), os(st, "conclusion"))).collect())
        .unwrap_or_default();
    Some(Job {
        id: v.get("id")?.as_u64()?,
        name: s(v, "name"),
        status: s(v, "status"),
        conclusion: os(v, "conclusion"),
        started_at: t(v, "started_at"),
        completed_at: t(v, "completed_at"),
        steps,
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
                if plus == b'+' && cand.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) && !found.iter().any(|x| x == cand) {
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

pub fn verdict_json(v: &Verdict) -> Value {
    serde_json::json!({
        "main_sha": v.main_sha,
        "state": v.state,
        "note": v.note,
        "save_minutes": v.save_minutes,
        "save_minutes_kind": "measured: main wall clock of this push (only when every cell could reuse)",
        "shadow_false_green": v.shadow_false_green,
        "main_created_at": v.main_created_at,
        "cells": v.cells.iter().map(|c| serde_json::json!({
            "workflow": c.workflow, "job": c.job, "reusable": c.reusable, "reasons": c.reasons,
            "main_seconds": c.main_seconds, "main_conclusion": c.main_conclusion, "cand_job_id": c.cand_job_id,
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
    let mut minutes = 0.0;
    let mut fg = 0;
    for v in by.values() {
        *count.entry(s(v, "state")).or_default() += 1;
        if s(v, "state") == "reusable" {
            minutes += v.get("save_minutes").and_then(Value::as_f64).unwrap_or(0.0);
        }
        fg += v.get("shadow_false_green").and_then(Value::as_array).map_or(0, Vec::len);
    }
    let g = |k: &str| count.get(k).copied().unwrap_or(0);
    let mut out = format!(
        "今天 {} 次可沿用、估少等 {:.0} 分(判了 {} 次：部分可沿用 {}、不可 {}、等跑完 {}、查不到 {}、非合併 {};影子假綠 {})",
        g("reusable"),
        minutes,
        by.len(),
        g("partial"),
        g("not_reusable"),
        g("wait"),
        g("unknown"),
        g("not_merge"),
        fg
    );
    if bad > 0 {
        out.push_str(&format!(";帳本壞行 {bad}"));
    }
    out
}
