// SPDX-License-Identifier: Apache-2.0
//! S82 合完要刪(Andy 11-14 14:2x: 「把這些也寫成 rust 測跑、合完要刪」): `aib-slice gc` removes a
//! seat's own work trees under one folder once their head is in main, and (asked for) the
//! build caches of the trees that stay. One folder on one machine per run: each test machine
//! runs it on its own `~/wt.noindex` (no GitHub is read, so it works where `gh` is not signed
//! in).
//!
//! The rules of DEV-S82 (each has a knife):
//! 1. only this seat's own: the folder name carries the seat code (`<seat>-…`, `…-<seat>`,
//!    `…-<seat>-…`);
//! 2. safe: a direct child of the folder and not a link; no receipt of this seat is running
//!    there (`<seat>-*.out` without its `.done`); no uncommitted change; not touched for
//!    [`MIN_IDLE_S`];
//! 3. a build cache is removed only with its `CACHEDIR.TAG` (it really is cargo's);
//! 4. every removal is one line in `gc-<seat>.log`; `--dry-run` only lists;
//! 5. what cannot be read is kept (git fails, no `origin/main`): never "nothing to keep";
//! 6. a tree whose commits are only here is never removed: it is listed as `backup` (bundle
//!    it, then the S81 archive takes it).
//!
//! "In main" = `git cherry origin/main HEAD` has no `+` line (an ancestor, or every patch is
//! already there). A branch whose PR was closed without merge is not judged here (that needs
//! GitHub): it is kept and shows in `aib-slice sweep` as 可放棄 for a person to decide.

use aib_ghread::run_fixed;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

/// A tree touched less than this long ago is left alone (the clean-tree rule: 2 hours).
pub const MIN_IDLE_S: i64 = 2 * 3600;
/// Build cache folder names inside a tree.
pub const CACHE_DIRS: [&str; 3] = ["target", "target-win", "target-win.noindex"];
const GIT_TIMEOUT: Duration = Duration::from_secs(60);

/// One build cache folder of a tree.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Cache {
    pub name: String,
    /// it has a `CACHEDIR.TAG`
    pub tagged: bool,
    pub bytes: u64,
}

/// What was measured about one folder. `None` = could not be read.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Tree {
    pub name: String,
    pub path: PathBuf,
    /// a plain directory directly under the root (not a link)
    pub plain_child: bool,
    /// a git work tree
    pub git: bool,
    /// the folder itself is a tagged build cache (`<seat>-…-target`)
    pub tagged: bool,
    pub head: Option<String>,
    /// no `+` line in `git cherry origin/main HEAD`
    pub in_main: Option<bool>,
    pub dirty: Option<bool>,
    /// commits of HEAD on no remote ref
    pub only_here: Option<usize>,
    pub idle_s: i64,
    pub bytes: u64,
    pub caches: Vec<Cache>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    /// remove the whole folder
    Remove(String),
    /// remove these build caches, keep the tree
    Caches(Vec<String>, String),
    Keep(String),
    /// kept, and it must be backed up before anyone removes it (rule 6)
    Backup(String),
}

/// Rule 1: the name carries the seat code as a whole part.
pub fn owned(name: &str, seat: &str) -> bool {
    !seat.is_empty() && name.split(['-', '.']).any(|p| p == seat)
}

/// What to do with one folder. `running`: a receipt of this seat is running under the root.
/// `caches`: also remove the build caches of the trees that stay.
pub fn decide(t: &Tree, seat: &str, running: bool, caches: bool) -> Verdict {
    let keep = |s: &str| Verdict::Keep(s.to_owned());
    if !owned(&t.name, seat) {
        return keep("not this seat's");
    }
    if !t.plain_child {
        return keep("not a plain folder directly under the root");
    }
    if running {
        return keep("a receipt of this seat is running here");
    }
    if t.idle_s < MIN_IDLE_S {
        return Verdict::Keep(format!("touched {} min ago", t.idle_s.max(0) / 60));
    }
    if !t.git {
        return if t.tagged {
            Verdict::Remove("a build cache (CACHEDIR.TAG)".into())
        } else {
            keep("not a git tree and not a tagged build cache")
        };
    }
    let (Some(in_main), Some(dirty), Some(only_here)) = (t.in_main, t.dirty, t.only_here) else {
        return keep("could not be read");
    };
    if dirty {
        return keep("uncommitted changes");
    }
    if in_main {
        return Verdict::Remove("its head is in main".into());
    }
    if only_here > 0 {
        return Verdict::Backup(format!("{only_here} commit(s) only here: bundle first"));
    }
    let tagged: Vec<String> = t
        .caches
        .iter()
        .filter(|c| c.tagged)
        .map(|c| c.name.clone())
        .collect();
    if caches && !tagged.is_empty() {
        return Verdict::Caches(tagged, "not in main yet: build caches only".into());
    }
    keep("not in main yet")
}

fn git(dir: &Path, args: &[&str]) -> Result<String, String> {
    let mut a = vec!["-C".to_owned(), dir.to_string_lossy().into_owned()];
    a.extend(args.iter().map(|s| (*s).to_owned()));
    run_fixed("git", &a, GIT_TIMEOUT).map(|b| String::from_utf8_lossy(&b).trim().to_owned())
}

/// Bytes under `p` (links are not followed; what cannot be read counts 0).
pub fn size(p: &Path) -> u64 {
    let Ok(m) = std::fs::symlink_metadata(p) else {
        return 0;
    };
    if !m.is_dir() {
        return m.len();
    }
    std::fs::read_dir(p)
        .map(|rd| rd.filter_map(Result::ok).map(|e| size(&e.path())).sum())
        .unwrap_or(0)
}

fn age_s(p: &Path, now: i64) -> Option<i64> {
    let t = std::fs::symlink_metadata(p).ok()?.modified().ok()?;
    let s = t.duration_since(SystemTime::UNIX_EPOCH).ok()?.as_secs();
    Some(now - i64::try_from(s).ok()?)
}

/// Seconds since the folder or anything directly in it was last changed.
fn idle_s(p: &Path, now: i64) -> i64 {
    let inside = std::fs::read_dir(p)
        .map(|rd| {
            rd.filter_map(Result::ok)
                .filter_map(|e| age_s(&e.path(), now))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    age_s(p, now).into_iter().chain(inside).min().unwrap_or(0)
}

/// Measure one folder directly under `root`.
pub fn measure(root: &Path, name: &str, now: i64) -> Tree {
    let path = root.join(name);
    let plain_child = std::fs::symlink_metadata(&path).is_ok_and(|m| m.is_dir());
    let mut t = Tree {
        name: name.to_owned(),
        plain_child,
        git: path.join(".git").exists(),
        tagged: path.join("CACHEDIR.TAG").is_file(),
        idle_s: idle_s(&path, now),
        path,
        ..Tree::default()
    };
    if !t.plain_child {
        return t;
    }
    t.bytes = size(&t.path);
    for c in CACHE_DIRS {
        let p = t.path.join(c);
        if std::fs::symlink_metadata(&p).is_ok_and(|m| m.is_dir()) {
            t.caches.push(Cache {
                name: c.to_owned(),
                tagged: p.join("CACHEDIR.TAG").is_file(),
                bytes: size(&p),
            });
        }
    }
    if t.git {
        t.head = git(&t.path, &["rev-parse", "HEAD"]).ok();
        // rule 5: without origin/main nothing is known to be in main
        t.in_main = git(&t.path, &["rev-parse", "--verify", "-q", "origin/main"])
            .and_then(|_| git(&t.path, &["cherry", "origin/main", "HEAD"]))
            .ok()
            .map(|s| !s.lines().any(|l| l.starts_with('+')));
        t.dirty = git(&t.path, &["status", "--porcelain", "--untracked-files=no"])
            .ok()
            .map(|s| !s.is_empty());
        t.only_here = git(
            &t.path,
            &["rev-list", "--count", "HEAD", "--not", "--remotes"],
        )
        .ok()
        .and_then(|s| s.parse().ok());
    }
    t
}

/// Rule 2: a receipt of this seat is running under `root` (`<seat>-*.out` with no `.done`).
pub fn receipt_running(root: &Path, seat: &str) -> bool {
    let Ok(rd) = std::fs::read_dir(root) else {
        return false;
    };
    rd.filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| n.starts_with(&format!("{seat}-")))
        .filter_map(|n| n.strip_suffix(".out").map(str::to_owned))
        .any(|run| !root.join(format!("{run}.done")).exists())
}

/// One folder and what was decided (and done) about it.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub tree: Tree,
    pub verdict: Verdict,
    /// bytes this row frees (or would free)
    pub frees: u64,
    /// `Some(error)` when a removal was tried and failed
    pub failed: Option<String>,
}

fn frees(t: &Tree, v: &Verdict) -> u64 {
    match v {
        Verdict::Remove(_) => t.bytes,
        Verdict::Caches(names, _) => t
            .caches
            .iter()
            .filter(|c| names.contains(&c.name))
            .map(|c| c.bytes)
            .sum(),
        _ => 0,
    }
}

/// Every folder of this seat under `root`, measured and judged; nothing is removed.
pub fn plan(root: &Path, seat: &str, caches: bool, now: i64) -> Result<Vec<Row>, String> {
    let rd = std::fs::read_dir(root).map_err(|e| format!("{}: {e}", root.display()))?;
    let mut names: Vec<String> = rd
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| owned(n, seat))
        .collect();
    names.sort();
    let running = receipt_running(root, seat);
    Ok(names
        .iter()
        .map(|n| {
            let tree = measure(root, n, now);
            let verdict = decide(&tree, seat, running, caches);
            let frees = frees(&tree, &verdict);
            Row {
                tree,
                verdict,
                frees,
                failed: None,
            }
        })
        .collect())
}

/// Remove a whole tree: as a git work tree when it is one (so the repository forgets it),
/// else the folder.
fn remove_tree(t: &Tree) -> Result<(), String> {
    if t.git {
        let path = t.path.to_string_lossy().into_owned();
        // from the repository the tree belongs to, not from inside the tree being removed
        if let Ok(common) = git(
            &t.path,
            &["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ) {
            let args = [
                format!("--git-dir={common}"),
                "worktree".to_owned(),
                "remove".to_owned(),
                "--force".to_owned(),
                path,
            ];
            if run_fixed("git", &args, GIT_TIMEOUT).is_ok() && !t.path.exists() {
                return Ok(());
            }
            // git would not: remove the folder, then let the repository drop its record
            std::fs::remove_dir_all(&t.path).map_err(|e| format!("remove: {e}"))?;
            let prune = [
                format!("--git-dir={common}"),
                "worktree".to_owned(),
                "prune".to_owned(),
            ];
            let _ = run_fixed("git", &prune, GIT_TIMEOUT);
            return Ok(());
        }
    }
    std::fs::remove_dir_all(&t.path).map_err(|e| format!("remove: {e}"))
}

/// Rule 3 again at the moment of removal: only a cache that still has its tag.
fn remove_caches(t: &Tree, names: &[String]) -> Result<(), String> {
    for n in names {
        let p = t.path.join(n);
        if !CACHE_DIRS.contains(&n.as_str()) || !p.join("CACHEDIR.TAG").is_file() {
            return Err(format!("{n}: no CACHEDIR.TAG"));
        }
        std::fs::remove_dir_all(&p).map_err(|e| format!("remove {n}: {e}"))?;
    }
    Ok(())
}

fn log_line(root: &Path, seat: &str, machine: &str, r: &Row, now: i64) -> Result<(), String> {
    use std::io::Write as _;
    let (what, why) = match &r.verdict {
        Verdict::Remove(w) => ("removed-tree".to_owned(), w),
        Verdict::Caches(n, w) => (format!("removed-caches:{}", n.join(",")), w),
        Verdict::Keep(w) | Verdict::Backup(w) => ("kept".to_owned(), w),
    };
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join(format!("gc-{seat}.log")))
        .map_err(|e| format!("log: {e}"))?;
    writeln!(
        f,
        "{now}\t{machine}\t{}\t{}\t{what}\t{why}\t{seat}",
        r.tree.path.display(),
        r.frees
    )
    .map_err(|e| format!("log: {e}"))
}

/// Do what [`plan`] decided. Rule 4: the log line is written first; a removal whose line
/// cannot be written does not happen.
pub fn apply(root: &Path, seat: &str, machine: &str, rows: &mut [Row], now: i64) {
    for r in rows.iter_mut() {
        let done = match &r.verdict {
            Verdict::Remove(_) => {
                log_line(root, seat, machine, r, now).and_then(|()| remove_tree(&r.tree))
            }
            Verdict::Caches(names, _) => {
                log_line(root, seat, machine, r, now).and_then(|()| remove_caches(&r.tree, names))
            }
            _ => Ok(()),
        };
        r.failed = done.err();
    }
}

fn mb(b: u64) -> String {
    format!("{} MB", b / 1_000_000)
}

/// The list as text: one line per folder, then the total.
pub fn render(rows: &[Row], dry_run: bool) -> String {
    let mut out = String::new();
    for r in rows {
        let (mark, why) = match &r.verdict {
            Verdict::Remove(w) => (if dry_run { "would remove" } else { "removed" }, w.clone()),
            Verdict::Caches(n, w) => (
                if dry_run { "would clear" } else { "cleared" },
                format!("{} ({w})", n.join(", ")),
            ),
            Verdict::Keep(w) => ("kept", w.clone()),
            Verdict::Backup(w) => ("BACKUP", w.clone()),
        };
        let failed = r
            .failed
            .as_ref()
            .map_or(String::new(), |e| format!(" FAILED: {e}"));
        out.push_str(&format!(
            "{mark}\t{}\t{}\t{why}{failed}\n",
            r.tree.name,
            mb(r.frees)
        ));
    }
    let total: u64 = rows
        .iter()
        .filter(|r| r.failed.is_none())
        .map(|r| r.frees)
        .sum();
    out.push_str(&format!(
        "{} {} in {} folder(s)\n",
        if dry_run { "would free" } else { "freed" },
        mb(total),
        rows.iter().filter(|r| r.frees > 0).count()
    ));
    out
}

/// `aib-slice-gc.v1`.
pub fn to_json(rows: &[Row], seat: &str, dry_run: bool) -> Value {
    let list: Vec<Value> = rows
        .iter()
        .map(|r| {
            let (action, why, caches) = match &r.verdict {
                Verdict::Remove(w) => ("remove", w, vec![]),
                Verdict::Caches(n, w) => ("caches", w, n.clone()),
                Verdict::Keep(w) => ("keep", w, vec![]),
                Verdict::Backup(w) => ("backup", w, vec![]),
            };
            json!({
                "name": r.tree.name, "path": r.tree.path.to_string_lossy(), "head": r.tree.head,
                "action": action, "why": why, "caches": caches,
                "bytes": r.frees, "failed": r.failed,
            })
        })
        .collect();
    json!({"schema": "aib-slice-gc.v1", "seat": seat, "dry_run": dry_run, "rows": list})
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A git tree of this seat, clean, idle for a day, its head in main.
    fn merged() -> Tree {
        Tree {
            name: "1ced7-rx693".into(),
            path: PathBuf::from("/x/1ced7-rx693"),
            plain_child: true,
            git: true,
            head: Some("a".repeat(40)),
            in_main: Some(true),
            dirty: Some(false),
            only_here: Some(0),
            idle_s: 86_400,
            bytes: 5_000_000_000,
            caches: vec![
                Cache {
                    name: "target".into(),
                    tagged: true,
                    bytes: 4_000_000_000,
                },
                Cache {
                    name: "target-win".into(),
                    tagged: false,
                    bytes: 500_000_000,
                },
            ],
            ..Tree::default()
        }
    }

    fn kept(t: &Tree) -> String {
        match decide(t, "1ced7", false, true) {
            Verdict::Keep(w) => w,
            other => panic!("not kept: {other:?}"),
        }
    }

    #[test]
    fn a_merged_idle_clean_tree_of_this_seat_is_removed() {
        assert_eq!(
            decide(&merged(), "1ced7", false, false),
            Verdict::Remove("its head is in main".into())
        );
        for name in [
            "aib-expect-1ced7",
            "slice-sweep-1ced7-old",
            "1ced7-due-tree",
        ] {
            assert!(owned(name, "1ced7"), "{name}");
        }
    }

    #[test]
    fn each_rule_keeps_the_tree() {
        // rule 1: another seat's, or the code only inside a longer word
        for name in [
            "043dd-shared-tree",
            "x1ced7y-tree",
            "rb41b2-a9136",
            "1ced77-tree",
        ] {
            let t = Tree {
                name: name.into(),
                ..merged()
            };
            assert_eq!(kept(&t), "not this seat's", "{name}");
        }
        assert!(!owned("anything", ""));
        // rule 2: a link or a deeper path, a running receipt, a fresh tree, a dirty one
        let mut t = merged();
        t.plain_child = false;
        assert!(kept(&t).starts_with("not a plain folder"));
        assert_eq!(
            decide(&merged(), "1ced7", true, true),
            Verdict::Keep("a receipt of this seat is running here".into())
        );
        let mut t = merged();
        t.idle_s = MIN_IDLE_S - 60;
        assert_eq!(kept(&t), "touched 119 min ago");
        let mut t = merged();
        t.dirty = Some(true);
        assert_eq!(kept(&t), "uncommitted changes");
        // rule 5: anything unread
        for unread in [
            Tree {
                in_main: None,
                ..merged()
            },
            Tree {
                dirty: None,
                ..merged()
            },
            Tree {
                only_here: None,
                ..merged()
            },
        ] {
            assert_eq!(kept(&unread), "could not be read");
        }
        // not in main, on a remote: kept (caches only when asked)
        let mut t = merged();
        t.in_main = Some(false);
        assert_eq!(
            decide(&t, "1ced7", false, false),
            Verdict::Keep("not in main yet".into())
        );
    }

    #[test]
    fn commits_only_here_are_a_backup_never_a_removal() {
        let mut t = merged();
        t.in_main = Some(false);
        t.only_here = Some(3);
        assert_eq!(
            decide(&t, "1ced7", false, true),
            Verdict::Backup("3 commit(s) only here: bundle first".into())
        );
    }

    #[test]
    fn only_tagged_caches_are_cleared_and_only_when_asked() {
        let mut t = merged();
        t.in_main = Some(false);
        // target has its tag, target-win does not
        assert_eq!(
            decide(&t, "1ced7", false, true),
            Verdict::Caches(
                vec!["target".into()],
                "not in main yet: build caches only".into()
            )
        );
        assert_eq!(frees(&t, &decide(&t, "1ced7", false, true)), 4_000_000_000);
        t.caches[0].tagged = false;
        assert_eq!(
            decide(&t, "1ced7", false, true),
            Verdict::Keep("not in main yet".into())
        );
        // a folder that is itself a build cache: with its tag it goes, without it stays
        let dir = Tree {
            name: "1ced7-slq-target".into(),
            plain_child: true,
            git: false,
            tagged: true,
            idle_s: 86_400,
            ..Tree::default()
        };
        assert_eq!(
            decide(&dir, "1ced7", false, false),
            Verdict::Remove("a build cache (CACHEDIR.TAG)".into())
        );
        let plain = Tree {
            tagged: false,
            ..dir
        };
        assert!(matches!(
            decide(&plain, "1ced7", false, false),
            Verdict::Keep(_)
        ));
    }
}
