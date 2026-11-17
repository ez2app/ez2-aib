//! The real [`World`](crate::World): `gh` and `git` with fixed argv and a deadline
//! (`aib_ghread::run_fixed_any_why`, no shell), files under the configured folders.
//!
//! `--repo` should be a refresh clone, not a seat's canonical tree: `open_prs` fetches main and
//! each open PR's head and merge refs into `refs/remotes/origin/pr/<N>/…` there (a local cache
//! update; nothing is written to GitHub).

use crate::{CiJob, MacLoad, Merge, ReceiptRun, Runner};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;

const GIT_T: Duration = Duration::from_secs(60);
const GH_T: Duration = Duration::from_secs(60);
const PR_FIELDS: &str = "number,title,headRefName,headRefOid,baseRefName,mergeable,statusCheckRollup,commits,isDraft,body";

pub struct FsWorld {
    pub repo: PathBuf,
    /// `owner/name` on GitHub
    pub slug: String,
    pub main: String,
    pub pending_dir: PathBuf,
    pub plan_file: PathBuf,
    pub measure_dir: PathBuf,
}

/// `owner/name` from an origin URL (`git@github.com:o/n.git`, `https://github.com/o/n`).
pub fn slug_of(url: &str) -> Option<String> {
    let u = url.trim().trim_end_matches('/').trim_end_matches(".git");
    let rest = u
        .split_once("github.com:")
        .or_else(|| u.split_once("github.com/"))?
        .1;
    let mut it = rest.split('/');
    let (o, n) = (it.next()?, it.next()?);
    (it.next().is_none() && !o.is_empty() && !n.is_empty()).then(|| format!("{o}/{n}"))
}

/// A pending file name: one plain name, no folder, no `..` (pending is read by name only).
pub fn plain_name(name: &str) -> bool {
    !name.is_empty()
        && !name.contains(['/', '\\'])
        && name != "."
        && name != ".."
        && !name.starts_with('.')
}

fn args(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| (*s).to_owned()).collect()
}

impl FsWorld {
    fn git(&self, a: &[&str]) -> Result<(Option<i32>, String), String> {
        let mut v = vec!["-C".to_owned(), self.repo.to_string_lossy().into_owned()];
        v.extend(args(a));
        let (rc, out, why) = aib_ghread::run_fixed_any_why("git", &v, GIT_T)?;
        let out = String::from_utf8_lossy(&out).into_owned();
        match rc {
            Some(0) | Some(1) => Ok((rc, out)),
            _ => Err(format!("git {}: {why}", a.first().unwrap_or(&""))),
        }
    }
    fn git_ok(&self, a: &[&str]) -> Result<String, String> {
        match self.git(a)? {
            (Some(0), o) => Ok(o),
            _ => Err(format!("git {} 回 1", a.first().unwrap_or(&""))),
        }
    }
    fn gh_json(&self, a: &[&str]) -> Result<Value, String> {
        let (rc, out, why) = aib_ghread::run_fixed_any_why("gh", &args(a), GH_T)?;
        if rc != Some(0) {
            return Err(format!("gh {}: {why}", a.get(1).unwrap_or(&"")));
        }
        serde_json::from_slice(&out).map_err(|e| format!("gh 回的不是 JSON:{e}"))
    }
    fn api(&self, path: &str) -> Result<Value, String> {
        self.gh_json(&["api", &format!("repos/{}/{path}", self.slug)])
    }
    fn lines(&self, file: &str) -> Result<Vec<Value>, String> {
        let p = self.measure_dir.join(file);
        let t = std::fs::read_to_string(&p).map_err(|e| format!("{}:{e}", p.display()))?;
        Ok(t.lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .collect())
    }
    fn runs(&self, status: &str) -> Result<Vec<Value>, String> {
        let v = self.api(&format!("actions/runs?status={status}&per_page=30"))?;
        Ok(v.get("workflow_runs")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default())
    }
}

impl crate::World for FsWorld {
    fn open_prs(&self) -> Result<String, String> {
        let (rc, out, why) = aib_ghread::run_fixed_any_why(
            "gh",
            &args(&[
                "pr", "list", "-R", &self.slug, "--state", "open", "--limit", "100", "--json",
                PR_FIELDS,
            ]),
            GH_T,
        )?;
        if rc != Some(0) {
            return Err(format!("gh pr list: {why}"));
        }
        let text = String::from_utf8_lossy(&out).into_owned();
        // bring main and every open PR's head / merge commit into the refresh clone
        let mut spec = vec![
            "fetch".to_owned(),
            "-q".to_owned(),
            "origin".to_owned(),
            format!("+refs/heads/{0}:refs/remotes/origin/{0}", self.main),
        ];
        if let Ok(Value::Array(a)) = serde_json::from_str::<Value>(&text) {
            for n in a
                .iter()
                .filter_map(|p| p.get("number").and_then(Value::as_u64))
            {
                spec.push(format!(
                    "+refs/pull/{n}/head:refs/remotes/origin/pr/{n}/head"
                ));
                spec.push(format!(
                    "+refs/pull/{n}/merge:refs/remotes/origin/pr/{n}/merge"
                ));
            }
        }
        let refs: Vec<&str> = spec.iter().map(String::as_str).collect();
        // a PR without a merge ref (conflicting) makes fetch fail; heads are fetched again alone
        if self.git(&refs).map(|r| r.0) != Ok(Some(0)) {
            let heads: Vec<&str> = refs
                .iter()
                .copied()
                .filter(|r| !r.contains("/merge:"))
                .collect();
            self.git_ok(&heads)?;
        }
        Ok(text)
    }
    fn required_checks(&self) -> Result<Vec<String>, String> {
        let v = self.api(&format!("rules/branches/{}", self.main))?;
        let mut out = Vec::new();
        for r in v.as_array().ok_or("rules: 不是清單")? {
            if r.get("type").and_then(Value::as_str) != Some("required_status_checks") {
                continue;
            }
            let list = r
                .pointer("/parameters/required_status_checks")
                .and_then(Value::as_array)
                .ok_or("required_status_checks: 沒有清單")?;
            for c in list {
                if let Some(s) = c.get("context").and_then(Value::as_str) {
                    if !out.iter().any(|x| x == s) {
                        out.push(s.to_owned());
                    }
                }
            }
        }
        Ok(out)
    }
    fn main_head(&self) -> Result<String, String> {
        let o = self.git_ok(&["rev-parse", &format!("refs/remotes/origin/{}", self.main)])?;
        Ok(o.trim().to_owned())
    }
    fn merge_tree(&self, a: &str, b: &str) -> Merge {
        match self.git(&["merge-tree", "--write-tree", "--name-only", a, b]) {
            Err(e) => Merge::Err(e),
            Ok((rc, out)) => {
                let mut l = out.lines();
                let tree = l.next().unwrap_or("").trim().to_owned();
                if rc == Some(0) {
                    Merge::Clean(tree)
                } else {
                    Merge::Conflict(l.take_while(|x| !x.is_empty()).map(str::to_owned).collect())
                }
            }
        }
    }
    fn workflows(&self, tree: &str) -> Result<Vec<(String, u64)>, String> {
        let o = self.git_ok(&["ls-tree", "-r", "-l", tree, "--", ".github/workflows/"])?;
        Ok(o.lines()
            .filter_map(|l| {
                let (meta, path) = l.split_once('\t')?;
                let size = meta.split_whitespace().nth(3)?.parse().ok()?;
                Some((path.to_owned(), size))
            })
            .collect())
    }
    fn show(&self, rev: &str, path: &str) -> Option<String> {
        self.git_ok(&["show", &format!("{rev}:{path}")]).ok()
    }
    fn changed(&self, base: &str, head: &str) -> Result<Vec<String>, String> {
        let o = self.git_ok(&["diff", "--name-only", &format!("{base}...{head}")])?;
        Ok(o.lines().map(str::to_owned).collect())
    }
    fn first_parent_merges(&self, base: &str, head: &str) -> Result<usize, String> {
        let o = self.git_ok(&[
            "rev-list",
            "--count",
            "--first-parent",
            "--merges",
            &format!("{base}..{head}"),
        ])?;
        o.trim().parse().map_err(|e| format!("rev-list:{e}"))
    }
    fn side_heads(&self) -> Result<Vec<(String, String)>, String> {
        let o = self.git_ok(&[
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            "refs/remotes/origin",
        ])?;
        Ok(o.lines()
            .filter_map(|l| l.split_once(' '))
            .filter(|(n, _)| n.starts_with("origin/integ"))
            .map(|(n, s)| (n.trim_start_matches("origin/").to_owned(), s.to_owned()))
            .collect())
    }
    fn pending(&self, name: &str) -> Option<String> {
        if !plain_name(name) {
            return None;
        }
        std::fs::read_to_string(self.pending_dir.join(name)).ok()
    }
    fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
        match self.git(&["merge-base", "--is-ancestor", a, b]).ok()? {
            (Some(0), _) => Some(true),
            (Some(1), _) => Some(false),
            _ => None,
        }
    }
    fn plan(&self) -> Result<String, String> {
        std::fs::read_to_string(&self.plan_file)
            .map_err(|e| format!("{}:{e}", self.plan_file.display()))
    }
    fn ci_jobs(&self) -> Result<Vec<CiJob>, String> {
        Ok(self
            .lines("ci-jobs.jsonl")?
            .iter()
            .filter_map(|v| {
                let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
                let n = |k: &str| v.get(k).and_then(Value::as_u64);
                Some(CiJob {
                    run: v.get("run").and_then(Value::as_i64)?,
                    job: s("job"),
                    event: s("event"),
                    branch: s("branch"),
                    conclusion: s("conclusion"),
                    dur_s: n("dur_s")?,
                    wait_s: n("wait_s").unwrap_or(0),
                    macos: v.get("labels").and_then(Value::as_array).is_some_and(|l| {
                        l.iter()
                            .any(|x| x.as_str().is_some_and(|t| t.starts_with("macos")))
                    }),
                })
            })
            .collect())
    }
    fn receipt_runs(&self) -> Result<Vec<ReceiptRun>, String> {
        Ok(self
            .lines("receipt-runs.jsonl")?
            .iter()
            .filter(|v| v.get("finish").and_then(Value::as_str) == Some("end"))
            .filter_map(|v| {
                Some(ReceiptRun {
                    at_ms: v.get("at_ms").and_then(Value::as_i64)?,
                    seat: v
                        .get("seat")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                    total_s: v.get("total_s").and_then(Value::as_u64)?,
                })
            })
            .collect())
    }
    fn mac_load(&self) -> Result<MacLoad, String> {
        let mut m = MacLoad::default();
        for status in ["in_progress", "queued"] {
            for run in self.runs(status)? {
                let Some(id) = run.get("id").and_then(Value::as_u64) else {
                    continue;
                };
                let jobs = self.api(&format!("actions/runs/{id}/jobs?per_page=100"))?;
                let mac = jobs
                    .get("jobs")
                    .and_then(Value::as_array)
                    .is_some_and(|js| {
                        js.iter().any(|j| {
                            j.get("status").and_then(Value::as_str) != Some("completed")
                                && j.get("labels").and_then(Value::as_array).is_some_and(|l| {
                                    l.iter()
                                        .any(|x| x.as_str().is_some_and(|t| t.starts_with("macos")))
                                })
                        })
                    });
                if !mac {
                    continue;
                }
                let main = run.get("event").and_then(Value::as_str) == Some("push")
                    && run.get("head_branch").and_then(Value::as_str) == Some(self.main.as_str());
                match (status, main) {
                    ("in_progress", true) => m.running_main += 1,
                    ("in_progress", false) => m.running_pr += 1,
                    (_, true) => m.queued_main += 1,
                    (_, false) => m.queued_pr += 1,
                }
            }
        }
        Ok(m)
    }
    fn runners(&self) -> Result<Vec<Runner>, String> {
        let v = self.api("actions/runners?per_page=100")?;
        Ok(v.get("runners")
            .and_then(Value::as_array)
            .ok_or("runners: 沒有清單")?
            .iter()
            .map(|r| Runner {
                name: r
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
                online: r.get("status").and_then(Value::as_str) == Some("online"),
                busy: r.get("busy").and_then(Value::as_bool).unwrap_or(true),
            })
            .collect())
    }
    fn releasing(&self) -> Result<bool, String> {
        Ok(self.runs("in_progress")?.iter().any(|r| {
            r.get("path")
                .and_then(Value::as_str)
                .is_some_and(|p| p.contains("release"))
        }))
    }
    fn pr_merge_tree(&self, pr: u32) -> Option<String> {
        let o = self
            .git_ok(&[
                "rev-parse",
                &format!("refs/remotes/origin/pr/{pr}/merge^{{tree}}"),
            ])
            .ok()?;
        Some(o.trim().to_owned())
    }
    fn now_s(&self) -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs() as i64)
    }
}

/// The home folder (HOME, else USERPROFILE on Windows).
pub fn home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// Where the pending folder is when the config does not say (r2 L3: the config wins).
pub fn default_pending(home: &Path) -> PathBuf {
    home.join(".ez2-aib").join("pending")
}
