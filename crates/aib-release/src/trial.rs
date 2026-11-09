//! `aib-release trial` (approvals-to-Rust A8 / A9, RULES §13): before work
//! is handed out or an integration head is reported, trial-merge the
//! branches with `git merge-tree` and say, from git alone:
//!
//! - which heads do not merge cleanly onto the base (they need the base
//!   merged in first);
//! - which pairs of heads touch the same lines (conflicting files);
//! - a stacking order: every head that goes on cleanly, in the order given,
//!   and for each head that does not, the stacked heads it collides with
//!   ("merge those first, open the PR stacked on them");
//! - with `--expect-tree REF`, whether the fully stacked result has exactly
//!   REF's tree (A9: "merged into the real integ, the tree diff is 0");
//! - with `--integ-branch NAME` (and for the base itself), that an
//!   integration branch is named `int/…`: `integ/…` skips the macOS 3.9 /
//!   3.14 cells unless `full-ci` is set (PLAN-release-tooling 9.1, DECIDED
//!   #24), which looks like a pass that never ran.
//!
//! Nothing is written to the repository: every object the trial makes goes
//! to a throw-away object directory that borrows the repository's objects
//! as an alternate, and is deleted when the trial ends. Refs are resolved
//! once to full SHAs; every later git call gets only those SHAs.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

/// What the trial was asked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub base: String,
    pub heads: Vec<String>,
    pub expect_tree: Option<String>,
    pub integ_branch: Option<String>,
}

/// One merge result: the tree git wrote and the files it could not merge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Merge {
    pub tree: String,
    pub conflicts: Vec<String>,
}

impl Merge {
    pub fn clean(&self) -> bool {
        self.conflicts.is_empty()
    }
}

/// The git operations the trial needs. [`GitCli`] is the real one.
pub trait Git {
    /// `REF^{commit}` as a full SHA.
    fn commit(&self, r: &str) -> Result<String, String>;
    /// `REF^{tree}` as a full SHA.
    fn tree(&self, r: &str) -> Result<String, String>;
    /// Merge two commits (full SHAs) without touching any ref or worktree.
    fn merge(&self, a: &str, b: &str) -> Result<Merge, String>;
    /// A commit object for `tree` with these parents (only in the trial's
    /// own object directory).
    fn commit_tree(&self, tree: &str, parents: &[&str]) -> Result<String, String>;
    /// Paths that differ between two trees.
    fn diff_trees(&self, a: &str, b: &str) -> Result<Vec<String>, String>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Head {
    pub name: String,
    pub sha: String,
    /// Merging this head onto the base alone.
    pub onto_base: Merge,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pair {
    pub a: String,
    pub b: String,
    pub conflicts: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Blocked {
    pub name: String,
    /// Files that conflict against everything stacked so far.
    pub conflicts: Vec<String>,
    /// Stacked heads that conflict with this one pairwise: merge these first.
    pub stack_on: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExpectTree {
    pub name: String,
    pub tree: String,
    pub diff: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prefix {
    pub name: String,
    /// `None` = fine; otherwise why it is refused.
    pub refused: Option<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Everything stacks cleanly (and the tree matches, if asked).
    Clean,
    /// Something needs a merge first, a tree differs, or a name is wrong.
    NotReady,
    /// git could not answer.
    Error(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub base: Option<(String, String)>,
    pub heads: Vec<Head>,
    pub pairs: Vec<Pair>,
    pub order: Vec<String>,
    pub blocked: Vec<Blocked>,
    /// The fully stacked tree; `None` when something is blocked.
    pub stacked_tree: Option<String>,
    pub expect: Option<ExpectTree>,
    pub prefixes: Vec<Prefix>,
    pub verdict: Verdict,
}

/// The name an integration branch may have. `None` = fine.
pub fn integ_prefix_refusal(name: &str) -> Option<&'static str> {
    let short = strip_ref(name);
    if short.starts_with("int/") && short.len() > "int/".len() {
        None
    } else if short.starts_with("integ/") {
        Some("integ_prefix_skips_macos_cells")
    } else {
        Some("not_int_prefix")
    }
}

/// `refs/heads/x`, `refs/remotes/origin/x`, `origin/x` ⇒ `x`.
fn strip_ref(name: &str) -> &str {
    let n = name.strip_prefix("refs/heads/").unwrap_or(name);
    let n = n.strip_prefix("refs/remotes/").unwrap_or(n);
    n.strip_prefix("origin/").unwrap_or(n)
}

/// Does this base name look like an integration branch (so the prefix rule
/// applies to it as well)?
fn looks_integ(name: &str) -> bool {
    let s = strip_ref(name);
    s.starts_with("int/") || s.starts_with("integ/")
}

pub fn run(git: &dyn Git, req: &Request) -> Report {
    let mut rep = Report {
        base: None,
        heads: Vec::new(),
        pairs: Vec::new(),
        order: Vec::new(),
        blocked: Vec::new(),
        stacked_tree: None,
        expect: None,
        prefixes: Vec::new(),
        verdict: Verdict::Clean,
    };
    for name in req.integ_branch.iter().chain(
        std::iter::once(&req.base)
            .filter(|b| looks_integ(b) && req.integ_branch.as_ref() != Some(b)),
    ) {
        rep.prefixes.push(Prefix {
            name: name.clone(),
            refused: integ_prefix_refusal(name),
        });
    }
    if let Err(e) = fill(git, req, &mut rep) {
        rep.verdict = Verdict::Error(e);
        return rep;
    }
    let ready = rep.blocked.is_empty()
        && rep.prefixes.iter().all(|p| p.refused.is_none())
        && rep.expect.as_ref().is_none_or(|e| e.diff.is_empty());
    rep.verdict = if ready {
        Verdict::Clean
    } else {
        Verdict::NotReady
    };
    rep
}

fn fill(git: &dyn Git, req: &Request, rep: &mut Report) -> Result<(), String> {
    let base = git.commit(&req.base)?;
    rep.base = Some((req.base.clone(), base.clone()));
    for name in &req.heads {
        let sha = git.commit(name)?;
        if let Some(dup) = rep.heads.iter().find(|h| h.sha == sha) {
            return Err(format!("{name} and {} are the same commit {sha}", dup.name));
        }
        let onto_base = git.merge(&base, &sha)?;
        rep.heads.push(Head {
            name: name.clone(),
            sha,
            onto_base,
        });
    }
    for (i, a) in rep.heads.iter().enumerate() {
        for b in &rep.heads[i + 1..] {
            let m = git.merge(&a.sha, &b.sha)?;
            if !m.clean() {
                rep.pairs.push(Pair {
                    a: a.name.clone(),
                    b: b.name.clone(),
                    conflicts: m.conflicts,
                });
            }
        }
    }
    // Greedy stack: keep taking the first remaining head that merges
    // cleanly onto everything stacked so far.
    let mut cur = base;
    let mut cur_tree = None;
    let mut left: Vec<&Head> = rep.heads.iter().collect();
    loop {
        let mut took = None;
        for (i, h) in left.iter().enumerate() {
            let m = git.merge(&cur, &h.sha)?;
            if m.clean() {
                took = Some((i, m.tree));
                break;
            }
        }
        let Some((i, tree)) = took else { break };
        let h = left.remove(i);
        cur = git.commit_tree(&tree, &[&cur, &h.sha])?;
        cur_tree = Some(tree);
        rep.order.push(h.name.clone());
    }
    for h in left {
        let conflicts = git.merge(&cur, &h.sha)?.conflicts;
        let stack_on = rep
            .order
            .iter()
            .filter(|o| {
                rep.pairs
                    .iter()
                    .any(|p| (p.a == **o && p.b == h.name) || (p.b == **o && p.a == h.name))
            })
            .cloned()
            .collect();
        rep.blocked.push(Blocked {
            name: h.name.clone(),
            conflicts,
            stack_on,
        });
    }
    if rep.blocked.is_empty() {
        let stacked = match cur_tree {
            Some(t) => t,
            None => git.tree(&cur)?,
        };
        if let Some(want) = &req.expect_tree {
            let tree = git.tree(want)?;
            let diff = if tree == stacked {
                Vec::new()
            } else {
                git.diff_trees(&stacked, &tree)?
            };
            rep.expect = Some(ExpectTree {
                name: want.clone(),
                tree,
                diff,
            });
        }
        rep.stacked_tree = Some(stacked);
    }
    Ok(())
}

/// 0 clean, 2 not ready (conflicts / tree differs / wrong prefix), 3 git
/// could not answer.
pub fn exit_code(rep: &Report) -> i32 {
    match rep.verdict {
        Verdict::Clean => 0,
        Verdict::NotReady => 2,
        Verdict::Error(_) => 3,
    }
}

pub fn to_json(rep: &Report) -> Value {
    let verdict = match &rep.verdict {
        Verdict::Clean => "clean",
        Verdict::NotReady => "not_ready",
        Verdict::Error(_) => "error",
    };
    let mut v = json!({
        "verdict": verdict,
        "base": rep.base.as_ref().map(|(n, s)| json!({"ref": n, "sha": s})),
        "heads": rep.heads.iter().map(|h| json!({
            "ref": h.name, "sha": h.sha,
            "onto_base": {"clean": h.onto_base.clean(), "conflicts": h.onto_base.conflicts},
        })).collect::<Vec<_>>(),
        "conflicting_pairs": rep.pairs.iter().map(|p| json!({
            "a": p.a, "b": p.b, "conflicts": p.conflicts,
        })).collect::<Vec<_>>(),
        "order": rep.order,
        "blocked": rep.blocked.iter().map(|b| json!({
            "ref": b.name, "conflicts": b.conflicts, "stack_on": b.stack_on,
        })).collect::<Vec<_>>(),
        "stacked_tree": rep.stacked_tree,
        "expect_tree": rep.expect.as_ref().map(|e| json!({
            "ref": e.name, "tree": e.tree, "match": e.diff.is_empty(), "diff": e.diff,
        })),
        "integ_prefix": rep.prefixes.iter().map(|p| json!({
            "name": p.name, "ok": p.refused.is_none(), "reason": p.refused,
        })).collect::<Vec<_>>(),
    });
    if let Verdict::Error(e) = &rep.verdict {
        v["error"] = json!(e);
    }
    v
}

/// The real git, run in `dir`, writing objects only to a private object
/// directory removed on drop.
pub struct GitCli {
    dir: PathBuf,
    objects: PathBuf,
    scratch: Scratch,
}

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

impl GitCli {
    pub fn new(dir: &Path) -> Result<Self, String> {
        let out = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args([
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "objects",
            ])
            .output()
            .map_err(|e| format!("git: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "{} is not a git repository: {}",
                dir.display(),
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        let objects = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let path =
            std::env::temp_dir().join(format!("aib-release-trial-{}-{nanos}", std::process::id()));
        std::fs::create_dir(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(Self {
            dir: dir.to_owned(),
            objects,
            scratch: Scratch(path),
        })
    }

    fn git(&self, args: &[&str]) -> Command {
        let mut c = Command::new("git");
        c.arg("-C")
            .arg(&self.dir)
            .args(args)
            .env("GIT_OBJECT_DIRECTORY", &self.scratch.0)
            .env("GIT_ALTERNATE_OBJECT_DIRECTORIES", &self.objects)
            .env("GIT_AUTHOR_NAME", "aib-release trial")
            .env("GIT_AUTHOR_EMAIL", "trial@aib.invalid")
            .env("GIT_AUTHOR_DATE", "1700000000 +0000")
            .env("GIT_COMMITTER_NAME", "aib-release trial")
            .env("GIT_COMMITTER_EMAIL", "trial@aib.invalid")
            .env("GIT_COMMITTER_DATE", "1700000000 +0000")
            .env_remove("GIT_DIR")
            .env_remove("GIT_WORK_TREE")
            .env_remove("GIT_INDEX_FILE");
        c
    }

    /// stdout of a git call that must succeed.
    fn ok(&self, args: &[&str]) -> Result<String, String> {
        let out = self
            .git(args)
            .output()
            .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
        if out.status.success() {
            Ok(String::from_utf8_lossy(&out.stdout).into_owned())
        } else {
            Err(format!(
                "git {} failed ({}): {}",
                args.join(" "),
                out.status,
                String::from_utf8_lossy(&out.stderr).trim()
            ))
        }
    }

    fn resolve(&self, r: &str, kind: &str) -> Result<String, String> {
        if r.is_empty() || r.starts_with('-') {
            return Err(format!("bad ref {r:?}"));
        }
        let spec = format!("{r}^{{{kind}}}");
        let sha = self
            .ok(&[
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                &spec,
            ])
            .map_err(|_| format!("{r} is not a {kind} here"))?;
        let sha = sha.trim().to_owned();
        if crate::yield_integ::is_full_sha(&sha) {
            Ok(sha)
        } else {
            Err(format!("{r}: git answered {sha:?}"))
        }
    }
}

impl Git for GitCli {
    fn commit(&self, r: &str) -> Result<String, String> {
        self.resolve(r, "commit")
    }

    fn tree(&self, r: &str) -> Result<String, String> {
        self.resolve(r, "tree")
    }

    fn merge(&self, a: &str, b: &str) -> Result<Merge, String> {
        let args = [
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            a,
            b,
        ];
        let out = self
            .git(&args)
            .output()
            .map_err(|e| format!("git merge-tree: {e}"))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let mut lines = text.lines();
        let tree = lines.next().unwrap_or("").trim().to_owned();
        match out.status.code() {
            Some(0 | 1) if crate::yield_integ::is_full_sha(&tree) => {}
            _ => {
                return Err(format!(
                    "git merge-tree {a} {b} failed ({}): {}",
                    out.status,
                    String::from_utf8_lossy(&out.stderr).trim()
                ));
            }
        }
        let mut conflicts: Vec<String> = lines
            .take_while(|l| !l.is_empty())
            .map(str::to_owned)
            .collect();
        conflicts.dedup();
        if out.status.code() == Some(1) && conflicts.is_empty() {
            return Err(format!("git merge-tree {a} {b}: conflict without a file"));
        }
        Ok(Merge { tree, conflicts })
    }

    fn commit_tree(&self, tree: &str, parents: &[&str]) -> Result<String, String> {
        let mut args = vec!["commit-tree", tree];
        for p in parents {
            args.extend(["-p", p]);
        }
        args.extend(["-m", "aib-release trial"]);
        let sha = self.ok(&args)?.trim().to_owned();
        if crate::yield_integ::is_full_sha(&sha) {
            Ok(sha)
        } else {
            Err(format!("git commit-tree answered {sha:?}"))
        }
    }

    fn diff_trees(&self, a: &str, b: &str) -> Result<Vec<String>, String> {
        Ok(self
            .ok(&["diff-tree", "-r", "--name-only", "--no-renames", a, b])?
            .lines()
            .map(str::to_owned)
            .collect())
    }
}
