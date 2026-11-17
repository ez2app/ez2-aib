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
//! The deletion conditions of DECISION-council-autoclean-519d0-1115 §二 (S82-a), on top:
//! - 1 `git fetch origin main` first, once per repository; a failed fetch keeps the tree;
//! - 2 ignored files that are not a build cache keep the tree; submodule changes count as
//!   dirty (`--ignore-submodules=none`). A stash is counted where it would be lost: in a full
//!   clone (a linked tree's stash lives in the repository and survives its removal);
//! - 3 a `git worktree lock`ed tree, or one whose head an open PR has, is kept; the open
//!   heads are given (`--open-heads`), and without them no tree is removed;
//! - 4 the tree's own index and `logs/HEAD` count as touching it;
//! - 5 a process whose working folder or command line names the folder, a lease that names
//!   it, or any seat's receipt `.out` without `.done` that names it, keeps it (caches too);
//! - 6 a cache is cleared only inside the tree, with its tag, and not a link;
//! - 7 only this seat's own (rule 1);
//! - 8 `--apply` takes `<root>/gc.lock`, measures and judges each folder again, removes it
//!   only when the answer is the same, and prunes the repository after each removal.
//!
//! Every run writes one line per folder to `cleanup.jsonl`, a dry run too: the three
//! read-only days are dry runs, and `--apply` waits until their lines show no wrong call.
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
/// Ignored files that are never anyone's work (5d33a S82-a L2: ten of forty trees kept for
/// these alone).
pub const JUNK_NAMES: [&str; 2] = ["__pycache__", ".DS_Store"];

/// An ignored path from `git ls-files --others --ignored --directory` that is a cache or
/// junk, not work: a build cache folder at the top (`target`, `target-<anything>`) or a
/// [`JUNK_NAMES`] file or folder anywhere.
pub fn junk_ignored(line: &str) -> bool {
    let p = line.trim_end_matches('/');
    let name = p.rsplit('/').next().unwrap_or(p);
    CACHE_DIRS.contains(&p)
        || (!p.contains('/') && (p == "target" || p.starts_with("target-")))
        || JUNK_NAMES.contains(&name)
}

/// The first ignored path that is work, from `git ls-files --others --ignored
/// --exclude-standard --directory` output. A folder listed with a deeper line under it
/// (`tools/` above `tools/__pycache__/`: git lists both when `tools` itself is untracked)
/// is only the way to that line and is judged by it.
pub fn first_work_ignored(listing: &str) -> Option<String> {
    let lines: Vec<&str> = listing
        .lines()
        .map(|l| l.trim_end_matches('/'))
        .filter(|l| !l.is_empty())
        .collect();
    let above = |l: &str| {
        lines
            .iter()
            .any(|o| o.len() > l.len() && o.starts_with(l) && o[l.len()..].starts_with('/'))
    };
    lines
        .iter()
        .find(|l| !junk_ignored(l) && !above(l))
        .map(|l| (*l).to_owned())
}
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
    /// council rule 1: `None` = not asked (`--no-fetch`); `Some(false)` = `git fetch` failed
    pub fetched: Option<bool>,
    /// council rule 2: the first ignored file that is not a build cache (`Some(None)` = none)
    pub ignored: Option<Option<String>>,
    /// council rule 3: `git worktree lock` was used on it
    pub locked: Option<bool>,
    /// council rule 3: an open PR has this head (`None` = the open heads were not given)
    pub open_pr: Option<bool>,
    /// council rule 5: what uses it (a process, a lease, a receipt not done); `Some(None)` =
    /// nothing, `None` = could not be checked
    pub busy: Option<Option<String>>,
}

/// What `plan` asks the machine once, not per tree (council rules 1, 3, 5). Each `None` is
/// "could not be read", and a tree that needs it is kept.
#[derive(Debug, Clone, Default)]
pub struct Probe {
    /// `git fetch origin main` once per repository before judging "in main"
    pub fetch: bool,
    /// one line per process: its working folder, then its command line
    pub procs: Option<Vec<String>>,
    /// `aib lease status` as printed
    pub leases: Option<String>,
    /// heads (40 hex) of the open PRs
    pub open_heads: Option<Vec<String>>,
    /// the start of every `*.out` under the root that has no `.done` (filled by `plan`)
    pub receipts: Vec<String>,
    /// the fetch answers so far, one per repository (filled by `plan`; start it empty)
    pub fetched: Vec<(String, bool)>,
}

/// `name` appears in `text` as a whole word: the characters around it are not part of a
/// folder name (`1ced7-a` does not match inside `1ced7-ab` or `x1ced7-a`).
pub fn mentions(text: &str, name: &str) -> bool {
    let part = |c: char| c.is_alphanumeric() || matches!(c, '-' | '_' | '.');
    !name.is_empty()
        && text.match_indices(name).any(|(i, _)| {
            let before = text[..i].chars().next_back();
            let after = text[i + name.len()..].chars().next();
            !before.is_some_and(part) && !after.is_some_and(part)
        })
}

impl Probe {
    /// Council rule 5: the first thing that uses the folder `name`.
    pub fn busy(&self, name: &str) -> Option<Option<String>> {
        let (procs, leases) = (self.procs.as_ref()?, self.leases.as_ref()?);
        if let Some(p) = procs.iter().find(|l| mentions(l, name)) {
            let p: String = p.chars().take(120).collect();
            return Some(Some(format!("a process uses it: {p}")));
        }
        if mentions(leases, name) {
            return Some(Some("a lease names it".into()));
        }
        if self.receipts.iter().any(|r| mentions(r, name)) {
            return Some(Some("a receipt that is not done names it".into()));
        }
        Some(None)
    }
}

/// The leases as `aib lease status` printed them, whatever its exit code (it exits 4 while
/// a machine is held, the usual state of a test machine: 5d33a S82-a L1). Not run or no
/// output = leases unread (trees kept).
pub fn read_leases(program: &str) -> Option<String> {
    let (_, out) = aib_ghread::run_fixed_any(
        program,
        &["lease".to_owned(), "status".to_owned()],
        Duration::from_secs(30),
    )
    .ok()?;
    let text = String::from_utf8_lossy(&out).into_owned();
    (!text.trim().is_empty()).then_some(text)
}

/// Every process the machine shows: working folder and command line (council rule 5).
/// `None` when none could be read.
pub fn read_procs() -> Option<Vec<String>> {
    use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};
    // sysinfo 0.40: `System::new` can fail; no reading = processes unread (trees kept)
    let mut sys = System::new().ok()?;
    sys.refresh_processes_specifics(
        ProcessesToUpdate::All,
        true,
        ProcessRefreshKind::nothing()
            .with_cmd(UpdateKind::Always)
            .with_cwd(UpdateKind::Always),
    );
    let lines: Vec<String> = sys
        .processes()
        .values()
        .map(|p| {
            let cwd = p.cwd().map(|c| c.to_string_lossy().into_owned());
            let cmd: Vec<String> = p
                .cmd()
                .iter()
                .map(|a| a.to_string_lossy().into_owned())
                .collect();
            format!("{} {}", cwd.unwrap_or_default(), cmd.join(" "))
        })
        .collect();
    (!lines.is_empty()).then_some(lines)
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
    match &t.busy {
        None => return keep("processes, leases or receipts could not be read"),
        Some(Some(w)) => return Verdict::Keep(w.clone()),
        Some(None) => {}
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
        // council rules 1-3, asked only before a whole tree goes
        if t.fetched == Some(false) {
            return keep("could not fetch origin main");
        }
        let Some(open_pr) = t.open_pr else {
            return keep("open PR heads not given (--open-heads FILE)");
        };
        let (Some(ignored), Some(locked)) = (&t.ignored, t.locked) else {
            return keep("could not be read");
        };
        if let Some(p) = ignored {
            return Verdict::Keep(format!("ignored files (first: {p})"));
        }
        if locked {
            return keep("locked (git worktree lock)");
        }
        if open_pr {
            return keep("an open PR has this head");
        }
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
pub fn measure(root: &Path, name: &str, now: i64, probe: &mut Probe) -> Tree {
    let mut t = measure_tree(root, name, now, probe);
    t.busy = probe.busy(name);
    if t.git && t.plain_child {
        t.open_pr = match (&probe.open_heads, &t.head) {
            (Some(open), Some(h)) => Some(open.iter().any(|o| o == h)),
            _ => None,
        };
    }
    t
}

/// Council rule 1: `git fetch origin main` once per repository; the answer is kept.
fn fetch_once(dir: &Path, probe: &mut Probe) -> bool {
    let Ok(common) = git(
        dir,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    ) else {
        return false;
    };
    if let Some((_, ok)) = probe.fetched.iter().find(|(c, _)| *c == common) {
        return *ok;
    }
    let ok = git(dir, &["fetch", "-q", "origin", "main"]).is_ok();
    probe.fetched.push((common, ok));
    ok
}

fn measure_tree(root: &Path, name: &str, now: i64, probe: &mut Probe) -> Tree {
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
        if probe.fetch {
            t.fetched = Some(fetch_once(&t.path, probe));
        }
        // council rule 4: the index and HEAD's log in the tree's own git folder count as
        // touching it (a checkout or commit writes them, not the folder's own time)
        match git(&t.path, &["rev-parse", "--absolute-git-dir"]) {
            Ok(gd) => {
                let gd = PathBuf::from(gd);
                for f in [gd.join("index"), gd.join("logs").join("HEAD")] {
                    if let Some(a) = age_s(&f, now) {
                        t.idle_s = t.idle_s.min(a);
                    }
                }
                // council rule 3: `git worktree lock` leaves `locked` in a linked tree's folder
                t.locked = Some(gd.join("locked").exists());
            }
            Err(_) => t.locked = None,
        }
        // council rule 2: ignored files are work too, except the build caches (and a
        // submodule with changes shows in `git status` below: --ignore-submodules=none)
        t.ignored = git(
            &t.path,
            &[
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
            ],
        )
        .ok()
        .map(|s| first_work_ignored(&s));
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
        t.dirty = git(
            &t.path,
            &["status", "--porcelain", "--ignore-submodules=none"],
        )
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

/// Council rule 5: the first 64 KB of every `*.out` under `root` (any seat's) that has no
/// `.done` yet: a receipt still running names the tree it runs in.
pub fn unfinished_receipts(root: &Path) -> Vec<String> {
    use std::io::Read as _;
    let Ok(rd) = std::fs::read_dir(root) else {
        return vec![];
    };
    rd.filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .filter_map(|n| n.strip_suffix(".out").map(str::to_owned))
        .filter(|run| !root.join(format!("{run}.done")).exists())
        .filter_map(|run| {
            let f = std::fs::File::open(root.join(format!("{run}.out"))).ok()?;
            let mut head = Vec::new();
            f.take(64 * 1024).read_to_end(&mut head).ok()?;
            Some(format!("{run} {}", String::from_utf8_lossy(&head)))
        })
        .collect()
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
pub fn plan(
    root: &Path,
    seat: &str,
    caches: bool,
    now: i64,
    probe: &mut Probe,
) -> Result<Vec<Row>, String> {
    probe.receipts = unfinished_receipts(root);
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
            let tree = measure(root, n, now, probe);
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
            let prune = [
                format!("--git-dir={common}"),
                "worktree".to_owned(),
                "prune".to_owned(),
            ];
            // council rule 8: prune after every removal, not only after the fallback
            if run_fixed("git", &args, GIT_TIMEOUT).is_ok() && !t.path.exists() {
                let _ = run_fixed("git", &prune, GIT_TIMEOUT);
                return Ok(());
            }
            // git would not: remove the folder, then let the repository drop its record
            remove_folder(&t.path)?;
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

/// The cleanup lock (council rule 8): one gc at a time under one folder.
pub const LOCK: &str = "gc.lock";

/// Do what [`plan`] decided. Council rule 8: take the cleanup lock, then measure and judge
/// each folder again with `probe` (read again by the caller) and remove it only when the
/// answer is the same. Rule 4: the log line is written first; a removal whose line cannot be
/// written does not happen.
pub fn apply(
    root: &Path,
    seat: &str,
    machine: &str,
    rows: &mut [Row],
    now: i64,
    caches: bool,
    probe: &mut Probe,
) {
    let lock = root.join(LOCK);
    let taken = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock)
        .map(|mut f| {
            use std::io::Write as _;
            let _ = writeln!(f, "{} {seat} {now}", std::process::id());
        });
    if let Err(e) = taken {
        for r in rows.iter_mut() {
            if matches!(r.verdict, Verdict::Remove(_) | Verdict::Caches(..)) {
                r.failed = Some(format!("cleanup lock {}: {e}", lock.display()));
            }
        }
        return;
    }
    probe.receipts = unfinished_receipts(root);
    let running = receipt_running(root, seat);
    for r in rows.iter_mut() {
        if !matches!(r.verdict, Verdict::Remove(_) | Verdict::Caches(..)) {
            continue;
        }
        let again = decide(
            &measure(root, &r.tree.name, now, probe),
            seat,
            running,
            caches,
        );
        if again != r.verdict {
            r.failed = Some(format!("changed when checked again: {again:?}"));
            continue;
        }
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
    let _ = std::fs::remove_file(&lock);
}

/// The program name events of aib-slice are written under (`<state root>/state/aib-slice/events`).
pub const EVENTS_PROG: &str = "aib-slice";

/// The reason word a clean event carries (IFACE-s107-product §2.1: gc's own words, never free
/// text; the sentence stays in the journal and the log).
pub fn why_word(v: &Verdict) -> &'static str {
    match v {
        Verdict::Remove(w) if w.starts_with("a build cache") => "build_cache",
        Verdict::Remove(_) => "merged",
        Verdict::Caches(..) => "caches_asked",
        Verdict::Backup(_) => "backup_first",
        Verdict::Keep(_) => "kept",
    }
}

/// `wt:<tree>` or `wt:<tree>/<sub>`: relative to the cleanup root, never the home folder, with
/// `/` whatever the platform wrote (IFACE §7 item 1).
pub fn rel_path(tree: &str, sub: Option<&str>) -> String {
    match sub {
        Some(s) => format!("wt:{tree}/{s}"),
        None => format!("wt:{tree}"),
    }
    .replace('\\', "/")
}

/// What one row deletes: the whole tree, or each cache it names: (path, what, bytes).
fn targets(r: &Row) -> Vec<(String, &'static str, u64)> {
    match &r.verdict {
        Verdict::Remove(_) => {
            let what = if r.tree.git { "tree" } else { "target" };
            vec![(rel_path(&r.tree.name, None), what, r.tree.bytes)]
        }
        Verdict::Caches(names, _) => names
            .iter()
            .map(|n| {
                let bytes = r
                    .tree
                    .caches
                    .iter()
                    .find(|c| &c.name == n)
                    .map_or(0, |c| c.bytes);
                (rel_path(&r.tree.name, Some(n)), "target", bytes)
            })
            .collect(),
        Verdict::Keep(_) | Verdict::Backup(_) => Vec::new(),
    }
}

/// The fixed word a failed removal's event carries; the whole sentence (which can hold an
/// absolute path, the home folder in it) stays in the journal and the log (5d33a slice 2 L1).
pub fn err_word(e: &str) -> &'static str {
    if e.starts_with("cleanup lock") {
        "cleanup_lock"
    } else if e.starts_with("log:") {
        "log_write"
    } else if e.ends_with("no CACHEDIR.TAG") {
        "no_cachedir_tag"
    } else {
        "remove_failed"
    }
}

/// One `clean` event per target a row deletes (or, in a dry run, would delete). `free_after`:
/// the free bytes of the disk after the run, None when it could not be read. Outcome:
/// `deleted`; `failed` with `err` ([`err_word`]); `skipped` with `skipped`: `dry_run`, or
/// `changed` when the answer changed when checked again (fixed words only: 5d33a slice 2 L2).
pub fn clean_events(
    ctx: &aib_ledger::events::Ctx,
    rows: &[Row],
    dry_run: bool,
    free_after: Option<u64>,
    at_ms: i64,
) -> Vec<Value> {
    let mut out = Vec::new();
    for r in rows {
        for (path, what, bytes) in targets(r) {
            let mut extra = serde_json::Map::new();
            extra.insert("path".into(), json!(path));
            extra.insert("what".into(), json!(what));
            extra.insert("bytes".into(), json!(bytes));
            extra.insert("frees".into(), json!(free_after));
            extra.insert("why".into(), json!(why_word(&r.verdict)));
            extra.insert("dry_run".into(), json!(dry_run));
            let outcome = match (&r.failed, dry_run) {
                (_, true) => {
                    extra.insert("skipped".into(), json!("dry_run"));
                    "skipped"
                }
                (None, false) => "deleted",
                (Some(e), false) if e.starts_with("changed when checked again") => {
                    extra.insert("skipped".into(), json!("changed"));
                    "skipped"
                }
                (Some(e), false) => {
                    extra.insert("err".into(), json!(err_word(e)));
                    "failed"
                }
            };
            out.push(aib_ledger::events::build(
                ctx, "clean", outcome, at_ms, extra,
            ));
        }
    }
    out
}

/// One `clean` event per old event file [`aib_ledger::events::expire`] removed (`what:
/// events`, `why: events_expired`), the path relative to the state root with `/`.
pub fn expired_events(
    ctx: &aib_ledger::events::Ctx,
    state_root: &Path,
    removed: &[(PathBuf, u64)],
    free_after: Option<u64>,
    at_ms: i64,
) -> Vec<Value> {
    removed
        .iter()
        .map(|(p, bytes)| {
            let rel = p.strip_prefix(state_root).unwrap_or(p);
            let path = rel.to_string_lossy().replace('\\', "/");
            let extra = json!({"path": path, "what": "events", "bytes": bytes,
                "frees": free_after, "why": "events_expired", "dry_run": false});
            let extra = extra.as_object().cloned().unwrap_or_default();
            aib_ledger::events::build(ctx, "clean", "deleted", at_ms, extra)
        })
        .collect()
}

/// The free bytes of the disk `p` is on: the mounted disk whose mount point is the longest
/// prefix of `p`. None when no disk could be read.
pub fn free_bytes(p: &Path) -> Option<u64> {
    let p = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    // Windows canonical paths start `\\?\`, which no mount point (`C:\`) is a prefix of; a UNC
    // share (`\\?\UNC\…`) still matches none, and its free space is null (5d33a slice 2 N1)
    #[cfg(windows)]
    let p = match p.to_str().and_then(|s| s.strip_prefix(r"\\?\")) {
        Some(s) => PathBuf::from(s),
        None => p,
    };
    let disks = sysinfo::Disks::new_with_refreshed_list().ok()?;
    disks
        .iter()
        .filter(|d| p.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().as_os_str().len())
        .map(sysinfo::Disk::available_space)
}

/// Write this run's clean events under `state_root` (IFACE §2.1): one per target deleted or,
/// with `dry_run`, that would be; on a real run also expire old aib-slice event files and
/// record each. A write that fails says one stderr line and changes nothing else (§1).
/// Returns how many events were made.
pub fn write_events(
    state_root: &Path,
    seat: &str,
    root: &Path,
    rows: &[Row],
    dry_run: bool,
    at_ms: i64,
) -> usize {
    use aib_ledger::events;
    let ctx = events::Ctx::for_run(state_root, Some(seat));
    let mut evs = clean_events(&ctx, rows, dry_run, free_bytes(root), at_ms);
    if !dry_run {
        let gone = events::expire(state_root, EVENTS_PROG, events::KEEP_DAYS_DEFAULT, at_ms);
        evs.extend(expired_events(
            &ctx,
            state_root,
            &gone,
            free_bytes(state_root),
            at_ms,
        ));
    }
    for ev in &evs {
        events::append_or_warn(state_root, EVENTS_PROG, ev, at_ms);
    }
    evs.len()
}

/// Council 刪前看、刪後記: one JSON line per folder in `path` (default
/// `~/.ez2-aib/state/cleanup.jsonl`), for a dry run too: three days of these lines are the
/// read-only period that decides whether `--apply` may be used.
pub fn journal(
    path: &Path,
    rows: &[Row],
    seat: &str,
    machine: &str,
    dry_run: bool,
    now: i64,
) -> Result<(), String> {
    use std::io::Write as _;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let mut text = String::new();
    for r in rows {
        let (action, why) = match &r.verdict {
            Verdict::Remove(w) => ("remove", w.clone()),
            Verdict::Caches(n, w) => ("caches", format!("{} ({w})", n.join(","))),
            Verdict::Keep(w) => ("keep", w.clone()),
            Verdict::Backup(w) => ("backup", w.clone()),
        };
        let line = json!({
            "ts": now, "hook": "gc", "machine": machine, "seat": seat, "dry_run": dry_run,
            "path": r.tree.path.to_string_lossy(), "head": r.tree.head, "bytes": r.tree.bytes,
            "frees": r.frees, "action": action, "why": why, "failed": r.failed,
            "fetched": r.tree.fetched,
        });
        text.push_str(&format!("{line}\n"));
    }
    std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .and_then(|mut f| f.write_all(text.as_bytes()))
        .map_err(|e| format!("{}: {e}", path.display()))
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
            ignored: Some(None),
            locked: Some(false),
            open_pr: Some(false),
            busy: Some(None),
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
            busy: Some(None),
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

    #[test]
    fn each_council_condition_keeps_a_merged_tree() {
        // 1: the fetch failed (not asked = None is not a failure)
        let mut t = merged();
        t.fetched = Some(false);
        assert_eq!(kept(&t), "could not fetch origin main");
        t.fetched = Some(true);
        assert!(matches!(
            decide(&t, "1ced7", false, true),
            Verdict::Remove(_)
        ));
        // 2: an ignored file that is not a build cache
        let mut t = merged();
        t.ignored = Some(Some(".env.local".into()));
        assert_eq!(kept(&t), "ignored files (first: .env.local)");
        t.ignored = None;
        assert_eq!(kept(&t), "could not be read");
        // 3: locked, or the head of an open PR, or the open heads not given
        let mut t = merged();
        t.locked = Some(true);
        assert_eq!(kept(&t), "locked (git worktree lock)");
        t.locked = None;
        assert_eq!(kept(&t), "could not be read");
        let mut t = merged();
        t.open_pr = Some(true);
        assert_eq!(kept(&t), "an open PR has this head");
        t.open_pr = None;
        assert_eq!(kept(&t), "open PR heads not given (--open-heads FILE)");
        // 5: something uses it, or what uses it could not be read: caches stay too
        for (busy, why) in [
            (
                Some(Some("a lease names it".to_owned())),
                "a lease names it",
            ),
            (None, "processes, leases or receipts could not be read"),
        ] {
            let mut t = merged();
            t.busy = busy;
            assert_eq!(kept(&t), why);
            t.in_main = Some(false);
            assert_eq!(kept(&t), why, "not in main: no cache is cleared either");
        }
        // the open heads, the fetch, the lock and the ignored files are asked only before a
        // whole tree goes: a tree not in main still has its tagged caches cleared
        let mut t = merged();
        t.in_main = Some(false);
        t.open_pr = None;
        t.locked = Some(true);
        t.ignored = Some(Some("x".into()));
        assert!(matches!(
            decide(&t, "1ced7", false, true),
            Verdict::Caches(..)
        ));
    }

    #[test]
    fn caches_and_junk_are_not_ignored_work() {
        for l in [
            "target/",
            "target-rel/",
            "target-win.noindex/",
            "__pycache__/",
            "tools/x/__pycache__/",
            ".DS_Store",
            "docs/.DS_Store",
        ] {
            assert!(junk_ignored(l), "{l}");
        }
        for l in [
            "notes.local",
            "targets.json",
            "src/target-x/",
            ".env",
            "x.DS_Store",
        ] {
            assert!(!junk_ignored(l), "{l}");
        }
        // what git printed for an untracked tools/ holding only __pycache__ (11-15)
        assert_eq!(
            first_work_ignored(".DS_Store\ntools/\ntools/__pycache__/\n"),
            None
        );
        assert_eq!(
            first_work_ignored("tools/\ntools/__pycache__/\ntools/secret.local\n").as_deref(),
            Some("tools/secret.local")
        );
        assert_eq!(
            first_work_ignored("toolsx/\ntools/__pycache__/\n").as_deref(),
            Some("toolsx")
        );
        // 5d33a 273bdce9 N3, what git printed (11-16): an untracked tools/ holding __pycache__
        // and a real file lists only the cache here (the file is in `git status`: dirty keeps
        // the tree); an ignored work/ holding both is one line, and it is work
        assert_eq!(first_work_ignored("tools/__pycache__/\n"), None);
        assert_eq!(
            first_work_ignored("tools/\ntools/__pycache__/\nwork/\n").as_deref(),
            Some("work")
        );
    }

    #[test]
    fn a_folder_is_named_only_as_a_whole_word() {
        assert!(mentions("/u/wt.noindex/1ced7-a/src", "1ced7-a"));
        assert!(mentions("cargo test --manifest-path 1ced7-a", "1ced7-a"));
        assert!(mentions("1ced7-a", "1ced7-a"));
        for other in [
            "/u/wt.noindex/1ced7-ab",
            "x1ced7-a",
            "1ced7-a.bundle",
            "1ced7-a_2",
            "",
        ] {
            assert!(!mentions(other, "1ced7-a"), "{other}");
        }
        assert!(!mentions("anything", ""));
        let mut p = Probe {
            procs: Some(vec!["/u/wt.noindex/1ced7-b zsh".into()]),
            leases: Some("lease pro13 held by 043dd what=1ced7-c".into()),
            receipts: vec!["1ced7-r9 RECEIPT tree=/u/wt.noindex/1ced7-d".into()],
            ..Probe::default()
        };
        assert!(p
            .busy("1ced7-b")
            .is_some_and(|w| w.is_some_and(|w| w.starts_with("a process uses it"))));
        assert_eq!(p.busy("1ced7-c"), Some(Some("a lease names it".into())));
        assert_eq!(
            p.busy("1ced7-d"),
            Some(Some("a receipt that is not done names it".into()))
        );
        assert_eq!(p.busy("1ced7-e"), Some(None));
        p.leases = None;
        assert_eq!(
            p.busy("1ced7-e"),
            None,
            "leases unread: not known to be free"
        );
    }

    fn row(verdict: Verdict, failed: Option<&str>) -> Row {
        Row {
            tree: merged(),
            verdict,
            frees: 0,
            failed: failed.map(str::to_owned),
        }
    }

    fn ctx() -> aib_ledger::events::Ctx {
        aib_ledger::events::Ctx {
            seat: Some("1ced7".into()),
            ..Default::default()
        }
    }

    #[test]
    fn a_clean_event_says_deleted_failed_or_skipped_and_why_in_a_word() {
        let gone = row(Verdict::Remove("its head is in main".into()), None);
        let broke = row(
            Verdict::Remove("its head is in main".into()),
            Some("remove: denied (the folder may be partly removed)"),
        );
        let moved = row(
            Verdict::Remove("its head is in main".into()),
            Some("changed when checked again: Keep(\"touched 0 min ago\")"),
        );
        let kept = row(Verdict::Keep("not in main yet".into()), None);
        let ev = clean_events(&ctx(), &[gone, broke, moved, kept], false, Some(7), 1);
        assert_eq!(ev.len(), 3, "a kept tree deletes nothing: no event");
        let outcome: Vec<&str> = ev.iter().filter_map(|e| e["outcome"].as_str()).collect();
        assert_eq!(outcome, ["deleted", "failed", "skipped"]);
        assert_eq!(ev[0]["kind"], "clean");
        assert_eq!(ev[0]["path"], "wt:1ced7-rx693");
        assert_eq!(ev[0]["what"], "tree");
        assert_eq!(ev[0]["bytes"], 5_000_000_000_u64);
        assert_eq!(ev[0]["frees"], 7);
        assert_eq!(ev[0]["why"], "merged");
        assert_eq!(ev[0]["dry_run"], false);
        assert_eq!(ev[1]["err"], "remove_failed");
        assert_eq!(ev[2]["skipped"], "changed");
        assert_eq!(ev[0]["seat"], "1ced7");
    }

    #[test]
    fn each_cache_is_its_own_event_and_a_dry_run_is_skipped() {
        let caches = row(
            Verdict::Caches(
                vec!["target".into()],
                "not in main yet: build caches only".into(),
            ),
            None,
        );
        let mut cache_dir = merged();
        cache_dir.git = false;
        let tagged = Row {
            tree: cache_dir,
            ..row(Verdict::Remove("a build cache (CACHEDIR.TAG)".into()), None)
        };
        let ev = clean_events(&ctx(), &[caches, tagged], true, None, 1);
        assert_eq!(ev.len(), 2);
        assert_eq!(
            (
                ev[0]["path"].as_str(),
                ev[0]["what"].as_str(),
                ev[0]["why"].as_str()
            ),
            (
                Some("wt:1ced7-rx693/target"),
                Some("target"),
                Some("caches_asked")
            )
        );
        assert_eq!(ev[0]["bytes"], 4_000_000_000_u64, "the cache's own size");
        assert_eq!(
            (ev[1]["what"].as_str(), ev[1]["why"].as_str()),
            (Some("target"), Some("build_cache"))
        );
        for e in &ev {
            assert_eq!(
                (e["outcome"].as_str(), e["skipped"].as_str()),
                (Some("skipped"), Some("dry_run"))
            );
            assert_eq!(e["dry_run"], true);
            assert_eq!(e["frees"], Value::Null, "free space not read: null");
        }
    }

    #[test]
    fn a_failed_removal_says_a_fixed_word_never_a_path() {
        let lock = row(
            Verdict::Remove("its head is in main".into()),
            Some("cleanup lock /Users/someone/wt.noindex/gc.lock: File exists"),
        );
        let ev = clean_events(&ctx(), &[lock], false, None, 1);
        assert_eq!(ev[0]["err"], "cleanup_lock");
        assert!(!ev[0].to_string().contains("/Users/"), "{}", ev[0]);
        assert_eq!(err_word("log: denied"), "log_write");
        assert_eq!(err_word("target: no CACHEDIR.TAG"), "no_cachedir_tag");
        assert_eq!(err_word("remove target: busy"), "remove_failed");
    }

    #[test]
    fn paths_are_relative_with_slashes_on_windows_too() {
        assert_eq!(rel_path("1ced7-a", None), "wt:1ced7-a");
        assert_eq!(rel_path("1ced7-a", Some("target")), "wt:1ced7-a/target");
        assert_eq!(rel_path("x\\y", Some("t\\u")), "wt:x/y/t/u");
        let root = Path::new("sr");
        let gone = [(
            root.join("state")
                .join("aib-slice")
                .join("events")
                .join("2020-01.jsonl"),
            9,
        )];
        let ev = expired_events(&ctx(), root, &gone, None, 1);
        assert_eq!(ev[0]["path"], "state/aib-slice/events/2020-01.jsonl");
        assert_eq!(
            (ev[0]["what"].as_str(), ev[0]["why"].as_str()),
            (Some("events"), Some("events_expired"))
        );
        assert_eq!(ev[0]["bytes"], 9);
        let win = [(
            PathBuf::from("sr\\state\\aib-slice\\events\\2020-01.jsonl"),
            9,
        )];
        let ev = expired_events(&ctx(), Path::new("q"), &win, None, 1);
        assert_eq!(ev[0]["path"], "sr/state/aib-slice/events/2020-01.jsonl");
    }

    #[test]
    fn the_free_space_of_a_real_folder_is_read() {
        let d = std::env::temp_dir();
        assert!(free_bytes(&d).is_some_and(|b| b > 0), "{}", d.display());
    }
}
