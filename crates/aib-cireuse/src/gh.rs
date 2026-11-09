// SPDX-License-Identifier: Apache-2.0
//! Reading GitHub through `gh api` (read-only GETs). Any failure or a short page = an error,
//! so the caller records "unknown" instead of guessing.

use crate::{job_from_json, parse_image, parse_tested_sha, run_from_json, Facts, Run};
use serde_json::Value;
use std::collections::BTreeMap;
use std::process::{Command, Stdio};

pub trait Source {
    /// GET `path` (relative to api.github.com), return the body.
    fn get(&self, path: &str) -> Result<String, String>;
}

pub struct GhCli;

impl Source for GhCli {
    fn get(&self, path: &str) -> Result<String, String> {
        let out = Command::new("gh")
            .args(["api", "--method", "GET", path])
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("gh: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "gh api {path}: rc {:?} {}",
                out.status.code(),
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    }
}

/// Recorded responses for offline runs: DIR/index.json maps each API path to a file in DIR.
/// A path that was not recorded is an error, exactly like a failed read.
pub struct FileSource {
    dir: std::path::PathBuf,
    index: BTreeMap<String, String>,
}

impl FileSource {
    pub fn open(dir: &std::path::Path) -> Result<Self, String> {
        let raw = std::fs::read_to_string(dir.join("index.json"))
            .map_err(|e| format!("offline index: {e}"))?;
        let v: Value = serde_json::from_str(&raw).map_err(|e| format!("offline index: {e}"))?;
        let obj = v.as_object().ok_or("offline index: not an object")?;
        let mut index = BTreeMap::new();
        for (k, f) in obj {
            let f = f
                .as_str()
                .ok_or("offline index: file name must be a string")?;
            if f.contains('/') || f.starts_with('.') {
                return Err(format!("offline index: bad file name {f}"));
            }
            index.insert(k.clone(), f.to_string());
        }
        Ok(Self {
            dir: dir.to_path_buf(),
            index,
        })
    }
}

impl Source for FileSource {
    fn get(&self, path: &str) -> Result<String, String> {
        let f = self
            .index
            .get(path)
            .ok_or_else(|| format!("offline: not recorded: {path}"))?;
        std::fs::read_to_string(self.dir.join(f)).map_err(|e| format!("offline {f}: {e}"))
    }
}

fn json(src: &dyn Source, path: &str) -> Result<Value, String> {
    let body = src.get(path)?;
    serde_json::from_str(&body).map_err(|e| format!("{path}: bad json: {e}"))
}

/// Every item of a paged list (`key` array + `total_count`). Fewer items than total_count = error.
pub fn paged(src: &dyn Source, base: &str, key: &str) -> Result<Vec<Value>, String> {
    let sep = if base.contains('?') { '&' } else { '?' };
    let mut items = vec![];
    let mut total = None;
    for page in 1..=50 {
        let v = json(src, &format!("{base}{sep}per_page=100&page={page}"))?;
        let t = v
            .get("total_count")
            .and_then(Value::as_u64)
            .ok_or_else(|| format!("{base}: no total_count"))?;
        if total.is_some_and(|x| x != t) {
            return Err(format!("{base}: total_count changed while paging"));
        }
        total = Some(t);
        let arr = v
            .get(key)
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{base}: no {key}"))?;
        items.extend(arr.iter().cloned());
        if items.len() as u64 >= t || arr.is_empty() {
            break;
        }
    }
    let t = total.unwrap_or(0);
    if items.len() as u64 != t {
        return Err(format!("missing_page: {base} gave {} of {t}", items.len()));
    }
    Ok(items)
}

fn runs(src: &dyn Source, repo: &str, sha: &str, event: &str) -> Result<Vec<Run>, String> {
    let items = paged(
        src,
        &format!("repos/{repo}/actions/runs?head_sha={sha}&event={event}"),
        "workflow_runs",
    )?;
    let mut out = vec![];
    for it in &items {
        let mut r = run_from_json(it).ok_or_else(|| "unreadable run".to_string())?;
        if it.get("head_sha").and_then(Value::as_str) != Some(sha) {
            continue;
        }
        let jobs = paged(
            src,
            &format!(
                "repos/{repo}/actions/runs/{}/attempts/{}/jobs",
                r.id, r.run_attempt
            ),
            "jobs",
        )?;
        for j in &jobs {
            r.jobs
                .push(job_from_json(j).ok_or_else(|| "unreadable job".to_string())?);
        }
        out.push(r);
    }
    Ok(out)
}

/// Gather everything `judge` needs. Logs and tested trees that cannot be read stay None
/// (judge turns that into a reason); list reads that fail make the whole thing an error.
/// The expected cell list: required status check names of every active branch ruleset that
/// covers main (the same source release-go-p2.sh reads). Anything unreadable = error (fail closed).
pub fn required_checks(src: &dyn Source, repo: &str) -> Result<Vec<String>, String> {
    let list = json(src, &format!("repos/{repo}/rulesets?per_page=100"))?;
    let list = list.as_array().ok_or("rulesets: not a list")?;
    if list.len() >= 100 {
        return Err("rulesets: 100 or more, paging not supported".into());
    }
    let mut out = vec![];
    for r in list {
        let id = r
            .get("id")
            .and_then(Value::as_u64)
            .ok_or("rulesets: no id")?;
        let active = r.get("enforcement").and_then(Value::as_str) == Some("active");
        let branch = r.get("target").and_then(Value::as_str).unwrap_or("branch") == "branch";
        if !active || !branch {
            continue;
        }
        let d = json(src, &format!("repos/{repo}/rulesets/{id}"))?;
        let include = d
            .pointer("/conditions/ref_name/include")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("ruleset {id}: no conditions.ref_name.include"))?;
        let covers_main = include.iter().any(|x| {
            matches!(
                x.as_str(),
                Some("~DEFAULT_BRANCH" | "~ALL" | "refs/heads/main")
            )
        });
        if !covers_main {
            continue;
        }
        let rules = d
            .get("rules")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("ruleset {id}: no rules"))?;
        for rule in rules
            .iter()
            .filter(|x| x.get("type").and_then(Value::as_str) == Some("required_status_checks"))
        {
            let checks = rule
                .pointer("/parameters/required_status_checks")
                .and_then(Value::as_array)
                .ok_or_else(|| format!("ruleset {id}: unreadable required_status_checks"))?;
            for c in checks {
                let ctx = c
                    .get("context")
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("ruleset {id}: a required check has no context"))?;
                out.push(ctx.to_string());
            }
        }
    }
    out.sort();
    out.dedup();
    Ok(out)
}

pub fn facts(src: &dyn Source, repo: &str, sha: &str) -> Result<Facts, String> {
    let c = json(src, &format!("repos/{repo}/commits/{sha}"))?;
    let main_sha = c
        .get("sha")
        .and_then(Value::as_str)
        .ok_or("commit: no sha")?
        .to_string();
    let main_tree = c
        .pointer("/commit/tree/sha")
        .and_then(Value::as_str)
        .ok_or("commit: no tree")?
        .to_string();
    let parents: Vec<String> = c
        .get("parents")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|p| p.get("sha").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let mut f = Facts {
        repo: repo.into(),
        main_sha: main_sha.clone(),
        main_tree,
        parents: parents.clone(),
        main_runs: runs(src, repo, &main_sha, "push")?,
        cand_runs: vec![],
        tested_trees: BTreeMap::new(),
        required: required_checks(src, repo)?,
    };
    if parents.len() != 2 {
        return Ok(f);
    }
    f.cand_runs = runs(src, repo, &parents[1], "pull_request")?;
    let wanted = |r: &Run| f.main_runs.iter().any(|m| m.path == r.path);
    let latest: Vec<u64> = crate::latest_per_path(&f.cand_runs)
        .values()
        .filter(|r| wanted(r))
        .map(|r| r.id)
        .collect();
    for r in f.main_runs.iter_mut() {
        for j in r.jobs.iter_mut() {
            if let Ok(log) = src.get(&format!("repos/{repo}/actions/jobs/{}/logs", j.id)) {
                j.image = parse_image(&log);
            }
        }
    }
    let mut shas = vec![];
    for r in f.cand_runs.iter_mut().filter(|r| latest.contains(&r.id)) {
        for j in r.jobs.iter_mut() {
            if let Ok(log) = src.get(&format!("repos/{repo}/actions/jobs/{}/logs", j.id)) {
                j.image = parse_image(&log);
                j.tested_sha = parse_tested_sha(&log);
                if let Some(s) = &j.tested_sha {
                    shas.push(s.clone());
                }
            }
        }
    }
    shas.sort();
    shas.dedup();
    for s in shas {
        if let Ok(v) = json(src, &format!("repos/{repo}/git/commits/{s}")) {
            if let Some(t) = v.pointer("/tree/sha").and_then(Value::as_str) {
                f.tested_trees.insert(s, t.to_string());
            }
        }
    }
    Ok(f)
}
