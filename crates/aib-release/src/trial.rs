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
//! It is a trial, not the merge itself: `.gitattributes` merge attributes
//! (`merge=union`, `-merge`, `binary`) and merge drivers are not read, so a
//! repository that uses them can get a different result from a real
//! `git merge` (1ced7 68028506 N1). Needs git >= 2.38 (`merge-tree
//! --write-tree`); an older git is an error, never a pass.
//!
//! Nothing is written to the repository and none of its config runs: the
//! repository is only asked to resolve names (`rev-parse`); every merge runs
//! in a throw-away bare repository that borrows the repository's objects as
//! an alternate, reads no config, and is deleted when the trial ends (see
//! [`GitCli`]). Names are resolved once to full SHAs; every later git call
//! gets only those SHAs.

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

/// One merge result: the tree git wrote, whether git called it a conflict
/// (its exit code; some conflicts, e.g. directory renames, name no file),
/// and the files it could not merge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Merge {
    pub tree: String,
    pub conflicted: bool,
    pub conflicts: Vec<String>,
}

impl Merge {
    pub fn clean(&self) -> bool {
        !self.conflicted
    }
}

/// The git operations the trial needs. [`GitCli`] is the real one.
pub trait Git {
    /// `REF^{commit}` as a full SHA.
    fn commit(&self, r: &str) -> Result<String, String>;
    /// `REF^{tree}` as a full SHA (a name in the repository, or a commit
    /// the repository has).
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
    /// The tree ids are equal. The diff is only for the reader: a diff can
    /// come out empty for trees that differ (an ignored submodule).
    pub matches: bool,
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
    /// Pairs git could not merge at all (e.g. no common ancestor). Only the
    /// stacking hint loses them; the stack itself is still tried.
    pub pair_errors: Vec<(String, String, String)>,
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
        pair_errors: Vec::new(),
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
        && rep.expect.as_ref().is_none_or(|e| e.matches);
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
            match git.merge(&a.sha, &b.sha) {
                Ok(m) if !m.clean() => rep.pairs.push(Pair {
                    a: a.name.clone(),
                    b: b.name.clone(),
                    conflicts: m.conflicts,
                }),
                Ok(_) => {}
                Err(e) => rep.pair_errors.push((a.name.clone(), b.name.clone(), e)),
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
            let matches = tree == stacked;
            let diff = if matches {
                Vec::new()
            } else {
                git.diff_trees(&stacked, &tree)?
            };
            rep.expect = Some(ExpectTree {
                name: want.clone(),
                tree,
                matches,
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
        "pair_errors": rep.pair_errors.iter().map(|(a, b, e)| json!({
            "a": a, "b": b, "error": e,
        })).collect::<Vec<_>>(),
        "order": rep.order,
        "blocked": rep.blocked.iter().map(|b| {
            // 1ced7 68028506 N2: say why when there is nobody to stack on
            let why = if !b.stack_on.is_empty() {
                None
            } else if rep.heads.iter().any(|h| h.name == b.name && !h.onto_base.clean()) {
                Some("needs_the_base_merged_first")
            } else {
                Some("conflicts_only_with_the_whole_stack")
            };
            json!({
                "ref": b.name, "conflicts": b.conflicts, "stack_on": b.stack_on,
                "stack_on_reason": why,
            })
        }).collect::<Vec<_>>(),
        "stacked_tree": rep.stacked_tree,
        "expect_tree": rep.expect.as_ref().map(|e| json!({
            "ref": e.name, "tree": e.tree, "match": e.matches, "diff": e.diff,
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

/// The real git.
///
/// Names are resolved in the repository (`rev-parse` only). Every merge,
/// commit and diff runs in a throw-away bare repository that borrows the
/// repository's objects through `objects/info/alternates` and reads no
/// system / global / repository config: no custom merge driver, no
/// `merge.*` setting, no worktree, and git's own `.merge_file_*` scratch
/// files land there too. The bare repository is removed on drop.
pub struct GitCli {
    src: PathBuf,
    scratch: Scratch,
}

/// Removed with everything in it when dropped.
struct Scratch(tempfile::TempDir);

impl Scratch {
    /// A new directory under `base` whose name is made and taken in one step, then
    /// `fill`ed; when `fill` fails the directory goes with everything put in it so far
    /// (2240e #678 N1: a half-made scratch repository is not left in the temp dir).
    fn made_in(
        base: &Path,
        fill: impl FnOnce(&Path) -> Result<(), String>,
    ) -> Result<Self, String> {
        // pid + nanoseconds named two trials of one process alike on macOS, whose clock
        // moves in microseconds (#644, #675 run 43000539701: "File exists")
        let made = tempfile::Builder::new()
            .prefix("aib-release-trial-")
            .suffix(".git")
            .tempdir_in(base)
            .map_err(|e| format!("scratch repository: {e}"))?;
        fill(made.path())?;
        Ok(Self(made))
    }
}

/// The config file that means "none".
const NO_CONFIG: &str = if cfg!(windows) { "NUL" } else { "/dev/null" };

/// A git command with no repository chosen by the caller's environment.
fn bare_cmd() -> Command {
    let mut c = Command::new("git");
    for v in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_ATTR_SOURCE",
    ] {
        c.env_remove(v);
    }
    c
}

/// A git command that reads no config at all (for the scratch repository).
fn isolated_cmd() -> Command {
    let mut c = bare_cmd();
    c.env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", NO_CONFIG)
        .env("GIT_AUTHOR_NAME", "aib-release trial")
        .env("GIT_AUTHOR_EMAIL", "trial@aib.invalid")
        .env("GIT_AUTHOR_DATE", "1700000000 +0000")
        .env("GIT_COMMITTER_NAME", "aib-release trial")
        .env("GIT_COMMITTER_EMAIL", "trial@aib.invalid")
        .env("GIT_COMMITTER_DATE", "1700000000 +0000");
    c
}

fn run_ok(mut c: Command, what: &str) -> Result<Vec<u8>, String> {
    let out = c.output().map_err(|e| format!("git {what}: {e}"))?;
    if out.status.success() {
        Ok(out.stdout)
    } else {
        Err(format!(
            "git {what} failed ({}): {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

/// NUL-separated git output (`-z`) ⇒ the non-empty entries.
fn nul_list(b: &[u8]) -> Vec<String> {
    b.split(|c| *c == 0)
        .filter(|s| !s.is_empty())
        .map(|s| String::from_utf8_lossy(s).into_owned())
        .collect()
}

impl GitCli {
    pub fn new(dir: &Path) -> Result<Self, String> {
        let mut probe = bare_cmd();
        probe.arg("-C").arg(dir).args([
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects",
        ]);
        let objects = run_ok(probe, "rev-parse --git-path objects")
            .map_err(|e| format!("{} is not a git repository: {e}", dir.display()))?;
        let objects = String::from_utf8_lossy(&objects).trim().to_owned();
        if objects.is_empty() || objects.contains('\n') || objects.starts_with('"') {
            return Err(format!("objects path {objects:?} cannot be an alternate"));
        }
        let scratch = Scratch::made_in(&std::env::temp_dir(), |at| {
            let mut init = isolated_cmd();
            init.args(["init", "-q", "--bare", "--template="]).arg(at);
            run_ok(init, "init --bare")?;
            let info = at.join("objects").join("info");
            std::fs::create_dir_all(&info).map_err(|e| format!("{}: {e}", info.display()))?;
            std::fs::write(info.join("alternates"), format!("{objects}\n"))
                .map_err(|e| format!("alternates: {e}"))
        })?;
        Ok(Self {
            src: dir.to_owned(),
            scratch,
        })
    }

    /// A read-only call in the repository itself.
    fn src_git(&self, args: &[&str]) -> Command {
        let mut c = bare_cmd();
        c.arg("-C").arg(&self.src).args(args);
        c
    }

    /// A call in the scratch repository.
    fn scratch_git(&self, args: &[&str]) -> Command {
        let mut c = isolated_cmd();
        c.arg("--git-dir").arg(self.scratch.0.path()).args(args);
        c
    }

    fn resolve(&self, r: &str, kind: &str) -> Result<String, String> {
        if r.is_empty() || r.starts_with('-') {
            return Err(format!("bad ref {r:?}"));
        }
        let spec = format!("{r}^{{{kind}}}");
        let out = run_ok(
            self.src_git(&[
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                &spec,
            ]),
            "rev-parse",
        )
        .map_err(|_| format!("{r} is not a {kind} here"))?;
        let sha = String::from_utf8_lossy(&out).trim().to_owned();
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
            "-z",
            "--name-only",
            "--no-messages",
            a,
            b,
        ];
        let out = self
            .scratch_git(&args)
            .output()
            .map_err(|e| format!("git merge-tree: {e}"))?;
        let mut parts = out.stdout.split(|c| *c == 0);
        let tree = String::from_utf8_lossy(parts.next().unwrap_or_default()).into_owned();
        let conflicted = match out.status.code() {
            Some(0) if crate::yield_integ::is_full_sha(&tree) => false,
            Some(1) if crate::yield_integ::is_full_sha(&tree) => true,
            _ => {
                return Err(format!(
                    "git merge-tree {a} {b} failed ({}): {}",
                    out.status,
                    String::from_utf8_lossy(&out.stderr).trim()
                ))
            }
        };
        let mut conflicts: Vec<String> = parts
            .take_while(|s| !s.is_empty())
            .map(|s| String::from_utf8_lossy(s).into_owned())
            .collect();
        conflicts.dedup();
        Ok(Merge {
            tree,
            conflicted,
            conflicts,
        })
    }

    fn commit_tree(&self, tree: &str, parents: &[&str]) -> Result<String, String> {
        let mut args = vec!["commit-tree", tree];
        for p in parents {
            args.extend(["-p", p]);
        }
        args.extend(["-m", "aib-release trial"]);
        let out = run_ok(self.scratch_git(&args), "commit-tree")?;
        let sha = String::from_utf8_lossy(&out).trim().to_owned();
        if crate::yield_integ::is_full_sha(&sha) {
            Ok(sha)
        } else {
            Err(format!("git commit-tree answered {sha:?}"))
        }
    }

    fn diff_trees(&self, a: &str, b: &str) -> Result<Vec<String>, String> {
        let args = [
            "diff-tree",
            "-r",
            "-z",
            "--name-only",
            "--no-renames",
            "--ignore-submodules=none",
            a,
            b,
        ];
        Ok(nul_list(&run_ok(self.scratch_git(&args), "diff-tree")?))
    }
}

#[cfg(test)]
mod tests {
    use super::Scratch;
    use std::path::Path;

    fn left_in(base: &Path) -> Vec<String> {
        std::fs::read_dir(base)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn a_scratch_whose_filling_fails_is_removed_with_what_was_put_in_it() {
        let base = tempfile::tempdir().unwrap();
        let mut seen = None;
        let r = Scratch::made_in(base.path(), |at| {
            std::fs::create_dir_all(at.join("objects").join("info")).unwrap();
            std::fs::write(at.join("HEAD"), "ref: refs/heads/main\n").unwrap();
            seen = Some(at.to_owned());
            Err("init --bare failed".into())
        });
        assert_eq!(r.err().as_deref(), Some("init --bare failed"));
        let seen = seen.expect("fill ran");
        assert!(
            seen.starts_with(base.path()),
            "{seen:?} is not under the base"
        );
        assert!(!seen.exists(), "{seen:?} was left behind");
        assert_eq!(left_in(base.path()), Vec::<String>::new());
    }

    #[test]
    fn a_scratch_that_is_filled_stays_until_it_is_dropped() {
        let base = tempfile::tempdir().unwrap();
        let s = Scratch::made_in(base.path(), |at| {
            std::fs::write(at.join("HEAD"), "x").map_err(|e| e.to_string())
        })
        .unwrap();
        let at = s.0.path().to_owned();
        let name = at.file_name().unwrap().to_string_lossy().into_owned();
        assert!(
            name.starts_with("aib-release-trial-") && name.ends_with(".git"),
            "{name}"
        );
        assert!(at.join("HEAD").is_file());
        drop(s);
        assert!(!at.exists(), "{at:?} was left behind");
        assert_eq!(left_in(base.path()), Vec::<String>::new());
    }
}
