//! S82 `aib-slice gc`: a seat's own trees under one folder, removed once their head is in
//! main (DEV-S82 rules 1-6). Real git in a temp folder; `now` is three hours ahead so the
//! fresh trees count as idle.

use aib_slice::gc::{apply, plan, receipt_running, render, to_json, Verdict};
use std::path::{Path, PathBuf};
use std::process::Command;

fn git(dir: &Path, args: &[&str]) -> String {
    let o = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
        .args(args)
        .output()
        .expect("git runs");
    assert!(o.status.success(), "git {args:?}: {o:?}");
    String::from_utf8_lossy(&o.stdout).trim().to_owned()
}

fn commit(dir: &Path, file: &str, msg: &str) -> String {
    std::fs::write(dir.join(file), msg).expect("write");
    git(dir, &["add", file]);
    git(dir, &["commit", "-q", "-m", msg]);
    git(dir, &["rev-parse", "HEAD"])
}

struct Fx {
    _t: tempfile::TempDir,
    repo: PathBuf,
    root: PathBuf,
    later: i64,
}

/// repo (main = origin/main) and a root with:
/// - 1ced7-merged: detached at the head of a branch merged into main ⇒ removed
/// - 1ced7-fresh: at main, a new crate written deep inside and not added ⇒ kept (043dd H1)
/// - 1ced7-atmain: at main, nothing done ⇒ kept (not "merged")
/// - 1ced7-clone: a full clone with a local-only branch ⇒ backup (043dd M1)
/// - 1ced7-dirty: same, with an uncommitted change ⇒ kept
/// - 1ced7-rawmerged: same as merged, with a file written deep inside and not added ⇒ kept;
///   only "untracked counts" protects it (043dd r2 L1)
/// - 1ced7-stash: a full clone, every branch pushed, one stash ⇒ backup (043dd r2 L2)
/// - 1ced7-used: a full clone at a merged head, with a linked work tree (outside the root,
///   detached, one commit) hanging from it ⇒ kept (043dd r2 M1)
/// - 1ced7-work: a commit on no remote ⇒ backup
/// - 1ced7-pushed: a commit on a remote branch, not in main, with a tagged target and an
///   untagged target-win ⇒ kept; with --caches only `target` goes
/// - 043dd-other: another seat's merged tree ⇒ not even listed
/// - 1ced7-x-target: a tagged build cache folder ⇒ removed
/// - 1ced7-notes: a plain folder ⇒ kept
/// - 1ced7-link: a link to 043dd-other ⇒ not listed, its target untouched
fn fixture() -> Fx {
    let t = tempfile::tempdir().expect("tmp");
    let repo = t.path().join("repo");
    let root = t.path().join("wt 有空白");
    std::fs::create_dir_all(&root).expect("root");
    git(t.path(), &["init", "-q", "-b", "main", "repo"]);
    commit(&repo, "a.txt", "base");
    // a branch merged into main with a merge commit: `side` is in main, not on main's own line
    git(&repo, &["checkout", "-q", "-b", "side"]);
    let side = commit(&repo, "s.txt", "side work");
    git(&repo, &["checkout", "-q", "main"]);
    git(
        &repo,
        &["merge", "-q", "--no-ff", "-m", "merge side", "side"],
    );
    let base = git(&repo, &["rev-parse", "HEAD"]);
    git(&repo, &["update-ref", "refs/remotes/origin/main", &base]);
    let add = |name: &str, at: &str| {
        let p = root.join(name);
        git(
            &repo,
            &[
                "worktree",
                "add",
                "-q",
                "--detach",
                &p.to_string_lossy(),
                at,
            ],
        );
        p
    };
    add("1ced7-merged", &side);
    let dirty = add("1ced7-dirty", &side);
    std::fs::write(dirty.join("a.txt"), "changed").expect("dirty");
    // 043dd H1: opened from main, a whole new crate written deep inside, nothing added yet
    let fresh = add("1ced7-fresh", &base);
    std::fs::create_dir_all(fresh.join("crates").join("new").join("src")).expect("deep");
    std::fs::write(
        fresh.join("crates").join("new").join("src").join("lib.rs"),
        "//! new",
    )
    .expect("untracked");
    // opened from main and nothing done in it: clean, but not "merged"
    add("1ced7-atmain", &base);
    // 043dd M1: a full clone with a local branch that is on no remote
    let clone = root.join("1ced7-clone");
    git(
        t.path(),
        &[
            "clone",
            "-q",
            &repo.to_string_lossy(),
            &clone.to_string_lossy(),
        ],
    );
    git(&clone, &["checkout", "-q", "-b", "local-only"]);
    commit(&clone, "l.txt", "local only");
    git(&clone, &["checkout", "-q", "main"]);
    // 043dd r2 L1: a merged head, and work nobody added yet
    let raw = add("1ced7-rawmerged", &side);
    std::fs::create_dir_all(raw.join("crates").join("raw")).expect("deep");
    std::fs::write(raw.join("crates").join("raw").join("lib.rs"), "//! raw").expect("untracked");
    let full = |name: &str| {
        let p = root.join(name);
        git(
            t.path(),
            &["clone", "-q", &repo.to_string_lossy(), &p.to_string_lossy()],
        );
        p
    };
    // 043dd r2 L2: nothing unpushed on any branch, one stash
    let stash = full("1ced7-stash");
    std::fs::write(stash.join("a.txt"), "half done").expect("change");
    git(&stash, &["stash", "-q"]);
    // 043dd r2 M1: a merged clone that another work tree uses as its repository
    let used = full("1ced7-used");
    git(&used, &["checkout", "-q", "--detach", &side]);
    let linked = t.path().join("linked-x");
    git(
        &used,
        &[
            "worktree",
            "add",
            "-q",
            "--detach",
            &linked.to_string_lossy(),
            &side,
        ],
    );
    commit(&linked, "x.txt", "only in the linked tree");
    let work = add("1ced7-work", &base);
    commit(&work, "w.txt", "only here");
    let pushed = add("1ced7-pushed", &base);
    let ph = commit(&pushed, "p.txt", "pushed");
    git(&repo, &["update-ref", "refs/remotes/origin/feat/p", &ph]);
    std::fs::create_dir_all(pushed.join("target")).expect("target");
    std::fs::write(pushed.join("target").join("CACHEDIR.TAG"), "Signature: x").expect("tag");
    std::fs::write(pushed.join("target").join("big"), vec![0_u8; 4096]).expect("big");
    std::fs::create_dir_all(pushed.join("target-win")).expect("target-win");
    add("043dd-other", &side);
    let cache = root.join("1ced7-x-target");
    std::fs::create_dir_all(&cache).expect("cache");
    std::fs::write(cache.join("CACHEDIR.TAG"), "Signature: x").expect("tag");
    std::fs::create_dir_all(root.join("1ced7-notes")).expect("notes");
    #[cfg(unix)]
    std::os::unix::fs::symlink(root.join("043dd-other"), root.join("1ced7-link")).expect("link");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs();
    Fx {
        _t: t,
        repo,
        root,
        later: i64::try_from(now).expect("time") + 3 * 3600,
    }
}

fn verdicts(rows: &[aib_slice::gc::Row]) -> Vec<(String, String)> {
    rows.iter()
        .map(|r| {
            let v = match &r.verdict {
                Verdict::Remove(w) => format!("remove: {w}"),
                Verdict::Caches(n, _) => format!("caches: {}", n.join(",")),
                Verdict::Keep(w) => format!("keep: {w}"),
                Verdict::Backup(w) => format!("backup: {w}"),
            };
            (r.tree.name.clone(), v)
        })
        .collect()
}

fn pairs(v: &[(&str, &str)]) -> Vec<(String, String)> {
    v.iter()
        .map(|(a, b)| ((*a).to_owned(), (*b).to_owned()))
        .collect()
}

#[test]
fn a_dry_run_lists_and_removes_nothing() {
    let f = fixture();
    let rows = plan(&f.root, "1ced7", false, f.later).expect("plan");
    assert_eq!(
        verdicts(&rows),
        pairs(&[
            ("1ced7-atmain", "keep: at main with no commit of its own"),
            ("1ced7-clone", "backup: 1 commit(s) only here: bundle first"),
            ("1ced7-dirty", "keep: uncommitted or untracked changes"),
            ("1ced7-fresh", "keep: uncommitted or untracked changes"),
            ("1ced7-merged", "remove: its head is in main"),
            (
                "1ced7-notes",
                "keep: not a git tree and not a tagged build cache"
            ),
            ("1ced7-pushed", "keep: not in main yet"),
            ("1ced7-rawmerged", "keep: uncommitted or untracked changes"),
            ("1ced7-stash", "backup: 1 commit(s) only here: bundle first"),
            ("1ced7-used", "keep: other work trees use this repository"),
            ("1ced7-work", "backup: 1 commit(s) only here: bundle first"),
            ("1ced7-x-target", "remove: a build cache (CACHEDIR.TAG)"),
        ]),
        "another seat's tree and the link are not listed"
    );
    let text = render(&rows, true);
    assert!(text.contains("would remove\t1ced7-merged\t"), "{text}");
    assert!(text.contains("BACKUP\t1ced7-work\t"), "{text}");
    assert!(
        text.lines()
            .last()
            .is_some_and(|l| l.starts_with("would free ")),
        "{text}"
    );
    let j = to_json(&rows, "1ced7", true);
    assert_eq!(j["schema"], "aib-slice-gc.v1");
    assert_eq!(j["rows"][4]["action"], "remove");
    // nothing was touched and nothing was logged
    for n in [
        "1ced7-merged",
        "1ced7-x-target",
        "1ced7-dirty",
        "043dd-other",
    ] {
        assert!(f.root.join(n).is_dir(), "{n}");
    }
    assert!(!f.root.join("gc-1ced7.log").exists());
    // a fresh tree is not idle yet: at the real time everything of this seat is kept
    let now = f.later - 3 * 3600;
    let fresh = plan(&f.root, "1ced7", true, now).expect("plan");
    assert!(
        fresh
            .iter()
            .all(|r| matches!(&r.verdict, Verdict::Keep(w) if w.starts_with("touched "))),
        "{:?}",
        verdicts(&fresh)
    );
}

#[test]
fn apply_removes_merged_trees_and_tagged_caches_and_logs_each() {
    let f = fixture();
    let mut rows = plan(&f.root, "1ced7", true, f.later).expect("plan");
    assert_eq!(
        verdicts(&rows)[6],
        ("1ced7-pushed".to_owned(), "caches: target".to_owned())
    );
    apply(&f.root, "1ced7", "testbox", &mut rows, f.later);
    assert!(rows.iter().all(|r| r.failed.is_none()), "{:?}", rows);
    // gone: the merged tree and the cache folder; the pushed tree keeps everything but target
    assert!(!f.root.join("1ced7-merged").exists());
    assert!(!f.root.join("1ced7-x-target").exists());
    assert!(!f.root.join("1ced7-pushed").join("target").exists());
    assert!(
        f.root.join("1ced7-pushed").join("target-win").is_dir(),
        "no tag: kept"
    );
    assert!(f.root.join("1ced7-pushed").join("p.txt").is_file());
    // kept: dirty, commits only here, the plain folder, another seat's tree (also behind the link)
    for n in [
        "1ced7-dirty",
        "1ced7-work",
        "1ced7-notes",
        "043dd-other",
        "1ced7-fresh",
        "1ced7-atmain",
        "1ced7-clone",
        "1ced7-rawmerged",
        "1ced7-stash",
        "1ced7-used",
    ] {
        assert!(f.root.join(n).is_dir(), "{n}");
    }
    // 043dd r2 L1: at a merged head, the file nobody added is still there
    assert!(
        f.root
            .join("1ced7-rawmerged")
            .join("crates")
            .join("raw")
            .join("lib.rs")
            .is_file(),
        "untracked work in a merged tree is never removed"
    );
    // 043dd r2 M1: the tree hanging from the clone still works and still has its commit
    let linked = f.root.parent().expect("tmp").join("linked-x");
    assert_eq!(
        git(&linked, &["log", "-1", "--format=%s"]),
        "only in the linked tree"
    );
    // 043dd r2 L2: the stash is still there
    assert_eq!(
        git(&f.root.join("1ced7-stash"), &["stash", "list"])
            .lines()
            .count(),
        1
    );
    assert!(f.root.join("043dd-other").join("a.txt").is_file());
    assert!(
        f.root
            .join("1ced7-fresh")
            .join("crates")
            .join("new")
            .join("src")
            .join("lib.rs")
            .is_file(),
        "untracked work is never removed"
    );
    // the repository no longer lists the removed work tree
    let list = git(&f.repo, &["worktree", "list"]);
    assert!(!list.contains("1ced7-merged"), "{list}");
    assert!(list.contains("1ced7-work"), "{list}");
    // one log line per removal, with the machine, what and why
    let log = std::fs::read_to_string(f.root.join("gc-1ced7.log")).expect("log");
    let lines: Vec<&str> = log.lines().collect();
    assert_eq!(lines.len(), 3, "{log}");
    assert!(
        lines[0].contains("\ttestbox\t")
            && lines[0].contains("\tremoved-tree\tits head is in main\t1ced7"),
        "{log}"
    );
    assert!(lines[1].contains("removed-caches:target"), "{log}");
    assert!(render(&rows, false).contains("removed\t1ced7-merged\t"));
}

#[test]
fn a_running_receipt_or_an_unreadable_main_keeps_everything() {
    let f = fixture();
    // a receipt of this seat started and has not finished
    std::fs::write(f.root.join("1ced7-run-abc.out"), "START").expect("out");
    assert!(receipt_running(&f.root, "1ced7"));
    assert!(!receipt_running(&f.root, "043dd"));
    let rows = plan(&f.root, "1ced7", true, f.later).expect("plan");
    assert!(
        rows.iter()
            .all(|r| r.verdict == Verdict::Keep("a receipt of this seat is running here".into())),
        "{:?}",
        verdicts(&rows)
    );
    std::fs::write(f.root.join("1ced7-run-abc.done"), "").expect("done");
    assert!(!receipt_running(&f.root, "1ced7"));
    // origin/main cannot be read: a merged tree is not known to be merged, so it stays
    git(&f.repo, &["update-ref", "-d", "refs/remotes/origin/main"]);
    let rows = plan(&f.root, "1ced7", false, f.later).expect("plan");
    let merged = rows
        .iter()
        .find(|r| r.tree.name == "1ced7-merged")
        .expect("row");
    assert_eq!(merged.verdict, Verdict::Keep("could not be read".into()));
    assert!(plan(&f.root.join("missing"), "1ced7", false, f.later).is_err());
}

fn run(args: &[&str]) -> (i32, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_aib-slice"))
        .args(args)
        .stdin(std::process::Stdio::null())
        .output()
        .expect("run aib-slice");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
    )
}

#[test]
fn the_command_removes_nothing_unless_told_which_seat_and_to_apply() {
    let f = fixture();
    let root = f.root.to_string_lossy().into_owned();
    for bad in [
        vec!["gc", "--root", &root, "--dry-run"],
        vec!["gc", "--root", &root, "--seat", "1ced7"],
        vec![
            "gc",
            "--root",
            &root,
            "--seat",
            "1ced7",
            "--dry-run",
            "--apply",
        ],
        vec!["gc", "--root", &root, "--seat", "1CED7", "--apply"],
    ] {
        let (rc, out) = run(&bad);
        assert_eq!(rc, 64, "{bad:?}");
        assert!(out.is_empty(), "{bad:?}");
    }
    // the real clock: every tree is fresh, so even --apply keeps them all
    let (rc, out) = run(&["gc", "--root", &root, "--seat", "1ced7", "--apply"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("kept\t1ced7-merged\t"), "{out}");
    assert!(f.root.join("1ced7-merged").is_dir());
}
