// SPDX-License-Identifier: Apache-2.0
//! Read-only GETs through aib-cireuse's `Source` (gh api, or recorded files offline).
//! A failed list read or a short page is an error for the whole run; a log or a tree that
//! cannot be read becomes None, which `judge` turns into `unclassified`.

use crate::{parse_log, rust_map, test_file_of, Changed, Facts, JobAttempt, RustMap, Step};
use aib_cireuse::gh::{paged, Source};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// GitHub caps a PR's file list at 3000 entries.
const PR_FILES_CAP: u64 = 3000;

fn json(src: &dyn Source, path: &str) -> Result<Value, String> {
    let body = src.get(path)?;
    serde_json::from_str(&body).map_err(|e| format!("{path}: bad json: {e}"))
}

fn str_at<'a>(v: &'a Value, ptr: &str) -> Result<&'a str, String> {
    v.pointer(ptr)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing {ptr}"))
}

/// Every changed file of the PR; the count must equal the PR's `changed_files`.
fn pr_files(src: &dyn Source, repo: &str, pr: u64, expected: u64) -> Result<Vec<Changed>, String> {
    if expected > PR_FILES_CAP {
        return Err(format!(
            "PR #{pr}: {expected} files, over the API cap of {PR_FILES_CAP}"
        ));
    }
    let mut out = vec![];
    for page in 1..=PR_FILES_CAP / 100 {
        let v = json(
            src,
            &format!("repos/{repo}/pulls/{pr}/files?per_page=100&page={page}"),
        )?;
        let arr = v.as_array().ok_or("pull files: not a list")?;
        for f in arr {
            out.push(Changed {
                path: str_at(f, "/filename")?.to_string(),
                status: str_at(f, "/status")?.to_string(),
            });
        }
        if arr.len() < 100 {
            break;
        }
    }
    if out.len() as u64 != expected {
        return Err(format!(
            "missing_page: PR #{pr} files gave {} of {expected}",
            out.len()
        ));
    }
    Ok(out)
}

fn steps(j: &Value) -> Vec<Step> {
    j.get("steps")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|s| Step {
                    name: s
                        .get("name")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    conclusion: s
                        .get("conclusion")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Whose CI a run of this head is (bb7dc #589 M1 / M2). The same SHA can be built by a
/// main push, another branch's push and any number of PRs; only the runs of the context
/// being judged count, and a run that does not state its context is left out (fail-closed).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Context {
    /// main push mode: `event` push on `head_branch` main (M1: a PR-only or other-branch
    /// SHA must not be recorded as a main precedent).
    MainPush,
    /// PR mode: a `pull_request` run from the PR's head branch whose `pull_requests` names
    /// the PR (M2: another PR's green run of the same head is not this PR's re-run).
    Pr { number: u64, head_ref: String },
}

/// Does this workflow run belong to `ctx`? Missing fields = no.
pub fn run_counts(r: &Value, ctx: &Context) -> bool {
    let event = r.get("event").and_then(Value::as_str);
    let branch = r.get("head_branch").and_then(Value::as_str);
    match ctx {
        Context::MainPush => event == Some("push") && branch == Some("main"),
        Context::Pr { number, head_ref } => {
            event == Some("pull_request")
                && branch == Some(head_ref.as_str())
                && r.get("pull_requests")
                    .and_then(Value::as_array)
                    .is_some_and(|a| {
                        a.iter()
                            .any(|p| p.get("number").and_then(Value::as_u64) == Some(*number))
                    })
        }
    }
}

fn attempts(
    src: &dyn Source,
    repo: &str,
    head: &str,
    ctx: &Context,
) -> Result<Vec<JobAttempt>, String> {
    let runs = paged(
        src,
        &format!("repos/{repo}/actions/runs?head_sha={head}"),
        "workflow_runs",
    )?;
    let mut out: Vec<JobAttempt> = vec![];
    for r in &runs {
        if r.get("head_sha").and_then(Value::as_str) != Some(head) || !run_counts(r, ctx) {
            continue;
        }
        let id = r.get("id").and_then(Value::as_u64).ok_or("run: no id")?;
        let path = str_at(r, "/path")?.to_string();
        let last = r
            .get("run_attempt")
            .and_then(Value::as_u64)
            .ok_or("run: no run_attempt")?;
        for att in 1..=last {
            let jobs = paged(
                src,
                &format!("repos/{repo}/actions/runs/{id}/attempts/{att}/jobs"),
                "jobs",
            )?;
            for j in &jobs {
                let job_id = j.get("id").and_then(Value::as_u64).ok_or("job: no id")?;
                if out.iter().any(|o| o.job_id == job_id) {
                    continue;
                }
                let conclusion = j
                    .get("conclusion")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                let is_red = matches!(conclusion.as_deref(), Some("failure") | Some("timed_out"));
                let log = if is_red {
                    src.get(&format!("repos/{repo}/actions/jobs/{job_id}/logs"))
                        .ok()
                } else {
                    None
                };
                out.push(JobAttempt {
                    run_id: id,
                    attempt: u32::try_from(att).map_err(|_| "attempt overflow")?,
                    workflow: path.clone(),
                    job_id,
                    job: str_at(j, "/name")?.to_string(),
                    conclusion,
                    steps: steps(j),
                    log,
                });
            }
        }
    }
    Ok(out)
}

/// Standard base64 (GitHub wraps it in newlines). None = not base64.
pub fn b64(s: &str) -> Option<Vec<u8>> {
    let mut buf = 0u32;
    let mut bits = 0;
    let mut out = vec![];
    for c in s.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' | b'\n' | b'\r' => continue,
            _ => return None,
        };
        buf = (buf << 6) | u32::from(v);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((buf >> bits) & 0xff) as u8);
        }
    }
    Some(out)
}

/// One file at `head` through the contents API. None = unreadable or not base64 text.
fn read_file(src: &dyn Source, repo: &str, head: &str, path: &str) -> Option<String> {
    let v = json(src, &format!("repos/{repo}/contents/{path}?ref={head}")).ok()?;
    if v.get("encoding").and_then(Value::as_str) != Some("base64") {
        return None;
    }
    String::from_utf8(b64(v.get("content")?.as_str()?)?).ok()
}

/// Head tree -> RustMap. None = truncated tree or an unreadable manifest.
fn head_rust(src: &dyn Source, repo: &str, head: &str) -> Option<RustMap> {
    let t = json(src, &format!("repos/{repo}/git/trees/{head}?recursive=1")).ok()?;
    if t.get("truncated").and_then(Value::as_bool) != Some(false) {
        return None;
    }
    let files: Vec<String> = t
        .get("tree")?
        .as_array()?
        .iter()
        .filter(|e| e.get("type").and_then(Value::as_str) == Some("blob"))
        .filter_map(|e| e.get("path").and_then(Value::as_str).map(str::to_string))
        .collect();
    rust_map(&files, &|p| read_file(src, repo, head, p)).ok()
}

/// PR mode (`pr` = Some) or main push mode (`sha` given, `pr` = None).
pub fn gather(src: &dyn Source, repo: &str, pr: Option<u64>, sha: &str) -> Result<Facts, String> {
    let (head, base, changed, ctx) = match pr {
        Some(n) => {
            let p = json(src, &format!("repos/{repo}/pulls/{n}"))?;
            let head = str_at(&p, "/head/sha")?.to_string();
            let base = str_at(&p, "/base/sha")?.to_string();
            let head_ref = str_at(&p, "/head/ref")?.to_string();
            let count = p
                .get("changed_files")
                .and_then(Value::as_u64)
                .ok_or("pull: no changed_files")?;
            let files = pr_files(src, repo, n, count)?;
            (
                head,
                base,
                files,
                Context::Pr {
                    number: n,
                    head_ref,
                },
            )
        }
        None => {
            let c = json(src, &format!("repos/{repo}/commits/{sha}"))?;
            let head = str_at(&c, "/sha")?.to_string();
            let base = str_at(&c, "/parents/0/sha").unwrap_or("").to_string();
            (head, base, vec![], Context::MainPush)
        }
    };
    let attempts = attempts(src, repo, &head, &ctx)?;
    let fps: BTreeSet<String> = attempts
        .iter()
        .filter_map(|a| a.log.as_deref())
        .flat_map(|l| parse_log(l).fingerprints)
        .collect();
    let needs_rust = fps.iter().any(|f| f.starts_with("rs:"));
    // the failing Python test files' own source at head (tests load each other as fixtures)
    let mut py_sources = BTreeMap::new();
    if pr.is_some() {
        for t in fps.iter().filter_map(|f| test_file_of(f)) {
            if !py_sources.contains_key(t) {
                py_sources.insert(t.to_string(), read_file(src, repo, &head, t));
            }
        }
    }
    let rust = if needs_rust {
        head_rust(src, repo, &head)
    } else {
        None
    };
    Ok(Facts {
        repo: repo.into(),
        pr,
        head,
        base,
        changed,
        attempts,
        rust,
        py_sources,
    })
}
