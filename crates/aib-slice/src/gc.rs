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
    /// anything `git status` shows: changed, staged or untracked (not ignored) files
    pub dirty: Option<bool>,
    /// the tree had commits of its own: false = it was opened from main and nothing was ever
    /// committed in it (HEAD is on main's first-parent line and `git cherry` is empty), so
    /// "its head is in main" says nothing about work being merged (043dd S82 review H1)
    pub own_work: Option<bool>,
    /// `.git` is a folder: a full clone, where other local branches and stashes live too
    pub clone: bool,
    /// commits on no remote ref: of HEAD in a linked work tree; of every local branch, plus
    /// the stash entries, in a full clone (043dd M1)
    pub only_here: Option<usize>,
    /// in a full clone: the other work trees `git worktree list` names. They keep their
    /// index, their branch and any detached commit inside this clone's `.git`, so removing
    /// the clone breaks them and loses those commits (043dd S82 review r2 M1)
    pub others: Option<usize>,
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

/// Rule 1: the name starts or ends with the seat code as a whole part (`<seat>-…`,
/// `…-<seat>`, `…-<seat>-<mmdd>`). A seat code in the middle names someone else's work that
/// this seat only touched (`rv-757-043dd-x`: 043dd S82 review L1).
pub fn owned(name: &str, seat: &str) -> bool {
    if seat.is_empty() {
        return false;
    }
    let parts: Vec<&str> = name.split('-').collect();
    let date = |d: &str| d.len() == 4 && d.bytes().all(|b| b.is_ascii_digit());
    match parts.as_slice() {
        [first, ..] if *first == seat => true,
        [.., last] if *last == seat => true,
        [.., s, d] if *s == seat && date(d) => true,
        _ => false,
    }
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
    let (Some(in_main), Some(dirty), Some(only_here), Some(own_work)) =
        (t.in_main, t.dirty, t.only_here, t.own_work)
    else {
        return keep("could not be read");
    };
    if dirty {
        return keep("uncommitted or untracked changes");
    }
    // a full clone holds more than its HEAD: other local branches, stashes
    if t.clone && only_here > 0 {
        return Verdict::Backup(format!("{only_here} commit(s) only here: bundle first"));
    }
    if t.clone && t.others.is_none() {
        return keep("could not be read");
    }
    if t.clone && t.others > Some(0) {
        return keep("other work trees use this repository");
    }
    if in_main && !own_work {
        return keep("at main with no commit of its own");
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

/// The smallest age under `p`, the whole tree deep; `.git` and the build caches are not
/// walked (a build touches them, work does not).
fn newest(p: &Path, now: i64) -> Option<i64> {
    let own = age_s(p, now);
    let Ok(rd) = std::fs::read_dir(p) else {
        return own;
    };
    rd.filter_map(Result::ok)
        .filter(|e| {
            let n = e.file_name();
            n != ".git" && !CACHE_DIRS.iter().any(|c| n == *c)
        })
        .filter_map(|e| {
            let path = e.path();
            if e.file_type().is_ok_and(|t| t.is_dir()) {
                newest(&path, now)
            } else {
                age_s(&path, now)
            }
        })
        .chain(own)
        .min()
}

/// Seconds since anything in the tree was last changed (043dd H1: a file written deep in the
/// tree does not touch the top folder's time).
fn idle_s(p: &Path, now: i64) -> i64 {
    newest(p, now).unwrap_or(0)
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
        t.clone = t.path.join(".git").is_dir();
        // rule 5: without origin/main nothing is known to be in main
        let cherry = git(&t.path, &["rev-parse", "--verify", "-q", "origin/main"])
            .and_then(|_| git(&t.path, &["cherry", "origin/main", "HEAD"]));
        t.in_main = cherry
            .as_ref()
            .ok()
            .map(|s| !s.lines().any(|l| l.starts_with('+')));
        // commits of its own: `git cherry` lists some (already in main by patch), or HEAD is
        // not on main's own line (it came in through a merge)
        let line = git(&t.path, &["rev-list", "--first-parent", "origin/main"]);
        t.own_work = match (&cherry, &line, &t.head) {
            (Ok(c), Ok(l), Some(h)) => Some(!c.is_empty() || !l.lines().any(|x| x == h)),
            _ => None,
        };
        // untracked files count (the default --untracked-files=normal; ignored ones do not)
        // a build cache folder git does not ignore (target-win.noindex is not in .gitignore)
        // is not work
        let cache_line = |l: &str| {
            l.strip_prefix("?? ")
                .is_some_and(|p| CACHE_DIRS.contains(&p.trim_end_matches('/')))
        };
        t.dirty = git(&t.path, &["status", "--porcelain"])
            .ok()
            .map(|s| s.lines().any(|l| !l.trim().is_empty() && !cache_line(l)));
        let count = |args: &[&str]| {
            git(&t.path, args)
                .ok()
                .and_then(|s| s.parse::<usize>().ok())
        };
        t.only_here = if t.clone {
            let stashes = git(&t.path, &["stash", "list"])
                .ok()
                .map(|s| s.lines().count());
            count(&[
                "rev-list",
                "--count",
                "--branches",
                "HEAD",
                "--not",
                "--remotes",
            ])
            .zip(stashes)
            .map(|(c, s)| c + s)
        } else {
            count(&["rev-list", "--count", "HEAD", "--not", "--remotes"])
        };
        if t.clone {
            // the first entry is this clone itself
            t.others = git(&t.path, &["worktree", "list", "--porcelain"])
                .ok()
                .map(|s| s.lines().filter(|l| l.starts_with("worktree ")).count())
                .and_then(|n| n.checked_sub(1));
        }
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
/// Remove the folder itself. On Windows git keeps its pack files read-only and
/// `remove_dir_all` stops at the first one, leaving half a tree: the read-only bit is cleared
/// first (043dd S82 review L3). A failure says the folder may be partly removed.
fn remove_folder(p: &Path) -> Result<(), String> {
    #[cfg(windows)]
    make_writable(p);
    std::fs::remove_dir_all(p)
        .map_err(|e| format!("remove: {e} (the folder may be partly removed)"))
}

#[cfg(windows)]
// on Windows this only clears the read-only attribute, which is the point (the lint warns
// about Unix, where it would make the file world-writable; this is not compiled there)
#[allow(clippy::permissions_set_readonly_false)]
fn make_writable(p: &Path) {
    let Ok(m) = std::fs::symlink_metadata(p) else {
        return;
    };
    let mut perm = m.permissions();
    if perm.readonly() {
        perm.set_readonly(false);
        let _ = std::fs::set_permissions(p, perm);
    }
    if m.is_dir() {
        if let Ok(rd) = std::fs::read_dir(p) {
            for e in rd.filter_map(Result::ok) {
                make_writable(&e.path());
            }
        }
    }
}

fn remove_tree(t: &Tree) -> Result<(), String> {
    // a full clone is not a linked work tree: git has nothing to remove it with (043dd M1;
    // it only gets here with no local-only branch and no stash)
    if t.git && !t.clone {
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
            remove_folder(&t.path)?;
            let prune = [
                format!("--git-dir={common}"),
                "worktree".to_owned(),
                "prune".to_owned(),
            ];
            let _ = run_fixed("git", &prune, GIT_TIMEOUT);
            return Ok(());
        }
    }
    remove_folder(&t.path)
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
            own_work: Some(true),
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
        for name in ["aib-expect-1ced7", "aib-x-1ced7-1114", "1ced7-due-tree"] {
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
            "rv-757-1ced7-x",
            "slice-1ced7-old",
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
        assert_eq!(kept(&t), "uncommitted or untracked changes");
        // 043dd H1: opened from main, nothing ever committed in it: not "merged"
        let mut t = merged();
        t.own_work = Some(false);
        assert_eq!(kept(&t), "at main with no commit of its own");
        let mut t = merged();
        t.own_work = None;
        assert_eq!(kept(&t), "could not be read");
        // 043dd M1: a full clone with a local-only branch or a stash is a backup, in main or not
        let mut t = merged();
        t.clone = true;
        t.only_here = Some(2);
        t.others = Some(0);
        assert!(matches!(
            decide(&t, "1ced7", false, true),
            Verdict::Backup(_)
        ));
        // 043dd r2 M1: a merged full clone other work trees hang from stays; alone it goes
        t.only_here = Some(0);
        assert_eq!(
            decide(&t, "1ced7", false, true),
            Verdict::Remove("its head is in main".into())
        );
        t.others = Some(1);
        assert_eq!(kept(&t), "other work trees use this repository");
        t.others = None;
        assert_eq!(kept(&t), "could not be read");
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
