//! The real [`Github`]: `gh api` (the token stays with `gh`; nothing here
//! sees, logs or passes it). Every non-zero exit or unparsable answer is a
//! [`GhError`], so the caller reports UNKNOWN instead of guessing.

use crate::yield_integ::{Cancelled, GhError, Github, Pull, Run};
use serde_json::Value;
use std::process::Command;

/// Largest page GitHub gives; a longer list is refused as incomplete.
const PER_PAGE: u64 = 100;

pub struct GhCli {
    /// `owner/name`
    pub repo: String,
    /// The `gh` program (tests point it at a fake).
    pub program: String,
}

impl GhCli {
    pub fn new(repo: &str) -> Self {
        Self {
            repo: repo.to_owned(),
            program: "gh".to_owned(),
        }
    }

    fn api(&self, args: &[&str]) -> Result<(bool, String, String), GhError> {
        let out = Command::new(&self.program)
            .arg("api")
            .args(args)
            .output()
            .map_err(|e| GhError(format!("gh_not_run:{}", e.kind())))?;
        Ok((
            out.status.success(),
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        ))
    }

    fn get(&self, path: &str) -> Result<Value, GhError> {
        let (ok, stdout, stderr) = self.api(&[path])?;
        if !ok {
            return Err(GhError(format!("http:{}", http_code(&stderr))));
        }
        serde_json::from_str(&stdout).map_err(|_| GhError("bad_json".into()))
    }
}

/// `gh api` prints `gh: <message> (HTTP 409)` on failure: the status, or `?`.
fn http_code(stderr: &str) -> String {
    stderr
        .rsplit_once("(HTTP ")
        .and_then(|(_, rest)| rest.split(')').next())
        .filter(|c| c.len() == 3 && c.bytes().all(|b| b.is_ascii_digit()))
        .unwrap_or("?")
        .to_owned()
}

fn s(v: &Value, k: &str) -> Result<String, GhError> {
    v.get(k)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| GhError(format!("missing:{k}")))
}

fn repo_of(v: &Value, k: &str) -> Option<String> {
    v.get(k)
        .and_then(|r| r.get("full_name"))
        .and_then(Value::as_str)
        .map(str::to_owned)
}

/// `path` may come as `.github/workflows/ci.yml@refs/...`: keep the file.
fn workflow_file(path: &str) -> String {
    path.split('@').next().unwrap_or(path).to_owned()
}

fn run_of(v: &Value) -> Result<Run, GhError> {
    let prs = v
        .get("pull_requests")
        .and_then(Value::as_array)
        .ok_or_else(|| GhError("missing:pull_requests".into()))?
        .iter()
        .map(|p| {
            p.get("number")
                .and_then(Value::as_u64)
                .ok_or_else(|| GhError("missing:pull_requests.number".into()))
        })
        .collect::<Result<Vec<u64>, GhError>>()?;
    Ok(Run {
        id: v
            .get("id")
            .and_then(Value::as_u64)
            .ok_or_else(|| GhError("missing:id".into()))?,
        path: workflow_file(&s(v, "path")?),
        event: s(v, "event")?,
        status: s(v, "status")?,
        conclusion: v
            .get("conclusion")
            .and_then(Value::as_str)
            .map(str::to_owned),
        head_sha: s(v, "head_sha")?,
        head_branch: s(v, "head_branch")?,
        head_repo: repo_of(v, "head_repository"),
        prs,
    })
}

impl Github for GhCli {
    fn pull(&self, number: u64) -> Result<Pull, GhError> {
        let v = self.get(&format!("repos/{}/pulls/{number}", self.repo))?;
        let head = v
            .get("head")
            .ok_or_else(|| GhError("missing:head".into()))?;
        let labels = v
            .get("labels")
            .and_then(Value::as_array)
            .ok_or_else(|| GhError("missing:labels".into()))?
            .iter()
            .filter_map(|l| l.get("name").and_then(Value::as_str).map(str::to_owned))
            .collect();
        Ok(Pull {
            number,
            state: s(&v, "state")?,
            head_sha: s(head, "sha")?,
            head_ref: s(head, "ref")?,
            head_repo: repo_of(head, "repo"),
            labels,
        })
    }

    fn runs_for(&self, sha: &str) -> Result<Vec<Run>, GhError> {
        let v = self.get(&format!(
            "repos/{}/actions/runs?head_sha={sha}&per_page={PER_PAGE}",
            self.repo
        ))?;
        let total = v
            .get("total_count")
            .and_then(Value::as_u64)
            .ok_or_else(|| GhError("missing:total_count".into()))?;
        let runs = v
            .get("workflow_runs")
            .and_then(Value::as_array)
            .ok_or_else(|| GhError("missing:workflow_runs".into()))?;
        if total > runs.len() as u64 {
            // a second page exists: a short list would hide runs to cancel
            return Err(GhError(format!("incomplete:{}/{total}", runs.len())));
        }
        runs.iter().map(run_of).collect()
    }

    fn contains(&self, ancestor: &str, head: &str) -> Result<bool, GhError> {
        let v = self.get(&format!("repos/{}/compare/{ancestor}...{head}", self.repo))?;
        match s(&v, "status")?.as_str() {
            "identical" | "ahead" => Ok(true),
            "behind" | "diverged" => Ok(false),
            other => Err(GhError(format!("compare_status:{other}"))),
        }
    }

    fn add_label(&self, number: u64, label: &str) -> Result<(), GhError> {
        let path = format!("repos/{}/issues/{number}/labels", self.repo);
        let field = format!("labels[]={label}");
        let (ok, _, stderr) = self.api(&["-X", "POST", &path, "-f", &field])?;
        if ok {
            Ok(())
        } else {
            Err(GhError(format!("http:{}", http_code(&stderr))))
        }
    }

    fn cancel_run(&self, id: u64) -> Result<Cancelled, GhError> {
        let path = format!("repos/{}/actions/runs/{id}/cancel", self.repo);
        let (ok, _, stderr) = self.api(&["-X", "POST", &path])?;
        match (ok, http_code(&stderr).as_str()) {
            (true, _) => Ok(Cancelled::Cancelled),
            // "Cannot cancel a workflow run that is completed."
            (false, "409") => Ok(Cancelled::AlreadyDone),
            (false, c) => Err(GhError(format!("http:{c}"))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_http_status_is_read_from_gh_stderr() {
        assert_eq!(http_code("gh: Not Found (HTTP 404)\n"), "404");
        assert_eq!(
            http_code("gh: Cannot cancel a workflow run that is completed. (HTTP 409)"),
            "409"
        );
        assert_eq!(http_code("error connecting to api.github.com"), "?");
        assert_eq!(http_code("(HTTP abc)"), "?");
    }

    #[test]
    fn a_run_carries_its_pr_branch_repo_and_workflow_file() {
        let v: Value = serde_json::from_str(
            r#"{"id": 7, "name": "ci", "path": ".github/workflows/ci.yml@refs/pull/9/merge",
                "event": "pull_request", "status": "queued", "conclusion": null,
                "head_sha": "abc", "head_branch": "feat/x",
                "head_repository": {"full_name": "o/r"},
                "pull_requests": [{"number": 9}, {"number": 10}]}"#,
        )
        .unwrap_or_default();
        let r = run_of(&v).map_err(|e| e.0);
        assert_eq!(
            r.as_ref().map(|r| r.path.as_str()),
            Ok(".github/workflows/ci.yml")
        );
        assert_eq!(r.as_ref().map(|r| r.prs.clone()), Ok(vec![9, 10]));
        assert_eq!(
            r.as_ref().map(|r| r.head_repo.clone()),
            Ok(Some("o/r".into()))
        );
        // no pull_requests field at all: refused, not "no PR"
        let mut v = v;
        if let Some(o) = v.as_object_mut() {
            o.remove("pull_requests");
        }
        assert!(run_of(&v).is_err());
    }
}
