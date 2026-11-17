//! S82 `aib-slice gc`: a seat's own trees under one folder, removed once their head is in
//! main (DEV-S82 rules 1-6). Real git in a temp folder; `now` is three hours ahead so the
//! fresh trees count as idle.

use aib_slice::gc::{
    apply, journal, plan, receipt_running, render, to_json, write_events, Probe, Verdict, LOCK,
};
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

/// A machine where nothing uses any tree, no lease is held, no PR is open, and no fetch is
/// asked (the fixture has no remote to fetch from).
fn probe() -> Probe {
    Probe {
        fetch: false,
        procs: Some(vec![]),
        leases: Some(String::new()),
        open_heads: Some(vec![]),
        ..Probe::default()
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
    let rows = plan(&f.root, "1ced7", false, f.later, &mut probe()).expect("plan");
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
    let fresh = plan(&f.root, "1ced7", true, now, &mut probe()).expect("plan");
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
    let mut rows = plan(&f.root, "1ced7", true, f.later, &mut probe()).expect("plan");
    assert_eq!(
        verdicts(&rows)[6],
        ("1ced7-pushed".to_owned(), "caches: target".to_owned())
    );
    apply(
        &f.root,
        "1ced7",
        "testbox",
        &mut rows,
        f.later,
        true,
        &mut probe(),
    );
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
    let rows = plan(&f.root, "1ced7", true, f.later, &mut probe()).expect("plan");
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
    let rows = plan(&f.root, "1ced7", false, f.later, &mut probe()).expect("plan");
    let merged = rows
        .iter()
        .find(|r| r.tree.name == "1ced7-merged")
        .expect("row");
    assert_eq!(merged.verdict, Verdict::Keep("could not be read".into()));
    assert!(plan(
        &f.root.join("missing"),
        "1ced7",
        false,
        f.later,
        &mut probe()
    )
    .is_err());
}

/// The state root the command writes its events under: `sroot` beside the `--root` folder,
/// never this machine's own (S107-product slice 2).
fn state_root_of(args: &[&str]) -> PathBuf {
    let root = args
        .iter()
        .position(|a| *a == "--root")
        .and_then(|i| args.get(i + 1))
        .map_or_else(std::env::temp_dir, PathBuf::from);
    root.parent().unwrap_or(&root).join("sroot")
}

fn run(args: &[&str]) -> (i32, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_aib-slice"))
        .args(args)
        .env("EZ2_AIB_STATE_ROOT", state_root_of(args))
        .env_remove("AIB_HOSTS_FILE")
        .env_remove("AIB_PLAN_DATA")
        .stdin(std::process::Stdio::null())
        .output()
        .expect("run aib-slice");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
    )
}

fn events_of(sroot: &Path) -> Vec<serde_json::Value> {
    let dir = sroot.join("state").join("aib-slice").join("events");
    let mut out = Vec::new();
    for e in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
        let text = std::fs::read_to_string(e.path()).expect("read");
        for l in text.lines() {
            out.push(serde_json::from_str(l).expect("one whole JSON line"));
        }
    }
    out
}

/// S107-product slice 2 (IFACE §8 item 1): each target gc deletes is one clean event with
/// its size; the files a dry run lists are still there and its events say dry_run.
#[test]
fn each_deleted_target_is_one_clean_event_and_a_dry_run_deletes_nothing() {
    let f = fixture();
    let sroot = f.root.parent().expect("tmp").join("sroot");
    let mut rows = plan(&f.root, "1ced7", true, f.later, &mut probe()).expect("plan");
    assert_eq!(write_events(&sroot, "1ced7", &f.root, &rows, true, 1000), 3);
    assert!(
        f.root.join("1ced7-merged").is_dir(),
        "a dry run deletes nothing"
    );
    let dry = events_of(&sroot);
    assert_eq!(dry.len(), 3);
    assert!(dry
        .iter()
        .all(|e| e["dry_run"] == true && e["outcome"] == "skipped"));
    apply(
        &f.root,
        "1ced7",
        "testbox",
        &mut rows,
        f.later,
        true,
        &mut probe(),
    );
    assert_eq!(
        write_events(&sroot, "1ced7", &f.root, &rows, false, 2000),
        3
    );
    let all = events_of(&sroot);
    let real: Vec<&serde_json::Value> = all.iter().filter(|e| e["dry_run"] == false).collect();
    let paths: Vec<&str> = real.iter().filter_map(|e| e["path"].as_str()).collect();
    assert_eq!(
        paths,
        [
            "wt:1ced7-merged",
            "wt:1ced7-pushed/target",
            "wt:1ced7-x-target"
        ]
    );
    for e in &real {
        assert_eq!(
            (e["kind"].as_str(), e["outcome"].as_str()),
            (Some("clean"), Some("deleted"))
        );
        assert!(e["bytes"].as_u64().is_some_and(|b| b > 0), "{e}");
        assert!(e["frees"].as_u64().is_some(), "{e}");
        assert_eq!(e["seat"], "1ced7");
        assert!(!e["path"]
            .as_str()
            .unwrap_or("")
            .contains(&*f.root.to_string_lossy()));
    }
    assert_eq!(real[0]["what"], "tree");
    assert_eq!(real[0]["why"], "merged");
}

/// The command itself writes its events under the state root it is given, and a real run
/// drops event months past the keep period, recording each as a clean event.
#[test]
fn the_command_expires_old_event_months_and_records_it() {
    let f = fixture();
    let root = f.root.to_string_lossy().into_owned();
    let args = [
        "gc",
        "--root",
        &root,
        "--seat",
        "1ced7",
        "--apply",
        "--no-fetch",
    ];
    let sroot = state_root_of(&args);
    let dir = sroot.join("state").join("aib-slice").join("events");
    std::fs::create_dir_all(&dir).expect("mkdir");
    std::fs::write(dir.join("2020-01.jsonl"), "{}\n").expect("old month");
    let jpath = f.root.parent().expect("tmp").join("j.jsonl");
    let j = jpath.to_string_lossy().into_owned();
    let mut with_journal = args.to_vec();
    with_journal.extend(["--journal", j.as_str()]);
    let (rc, out) = run(&with_journal);
    assert_eq!(rc, 0, "{out}");
    assert!(!dir.join("2020-01.jsonl").exists());
    let ev = events_of(&sroot);
    assert_eq!(
        ev.len(),
        1,
        "fresh trees are all kept: only the expiry ({ev:?})"
    );
    assert_eq!(
        (
            ev[0]["what"].as_str(),
            ev[0]["why"].as_str(),
            ev[0]["path"].as_str()
        ),
        (
            Some("events"),
            Some("events_expired"),
            Some("state/aib-slice/events/2020-01.jsonl")
        )
    );
    assert_eq!(
        (ev[0]["bytes"].as_u64(), ev[0]["seat"].as_str()),
        (Some(3), Some("1ced7"))
    );
    // a dry run expires nothing
    std::fs::write(dir.join("2020-02.jsonl"), "{}\n").expect("old month");
    let dry: Vec<&str> = with_journal
        .iter()
        .map(|a| if *a == "--apply" { "--dry-run" } else { *a })
        .collect();
    assert_eq!(run(&dry).0, 0);
    assert!(dir.join("2020-02.jsonl").exists());
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
    let jpath = f
        .root
        .parent()
        .expect("tmp")
        .join("state")
        .join("cleanup.jsonl");
    let jarg = jpath.to_string_lossy().into_owned();
    let (rc, out) = run(&[
        "gc",
        "--root",
        &root,
        "--seat",
        "1ced7",
        "--apply",
        "--no-fetch",
        "--journal",
        &jarg,
    ]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("kept\t1ced7-merged\t"), "{out}");
    assert!(f.root.join("1ced7-merged").is_dir());
    // one journal line per folder listed, the kept ones too
    let lines = std::fs::read_to_string(&jpath).expect("journal");
    assert_eq!(lines.lines().count(), 12, "{lines}");
    assert!(
        !f.root.join(LOCK).exists(),
        "the cleanup lock is given back"
    );
}

#[test]
fn the_council_conditions_keep_trees_a_dry_run_would_remove() {
    let f = fixture();
    let side = git(&f.repo, &["rev-parse", "side"]);
    let add = |name: &str| {
        let p = f.root.join(name);
        git(
            &f.repo,
            &[
                "worktree",
                "add",
                "-q",
                "--detach",
                &p.to_string_lossy(),
                &side,
            ],
        );
        p
    };
    // 3: locked; and the head an open PR has (every merged tree here has `side`)
    let locked = add("1ced7-locked");
    git(&f.repo, &["worktree", "lock", &locked.to_string_lossy()]);
    // 2: an ignored file that is not a build cache
    let ignored = add("1ced7-ignored");
    std::fs::write(
        f.repo.join(".git").join("info").join("exclude"),
        "secret.local\n__pycache__/\n.DS_Store\n",
    )
    .expect("exclude");
    std::fs::write(ignored.join("secret.local"), "k").expect("ignored");
    // ... but junk is not work (5d33a S82-a L2)
    let junk = add("1ced7-junk");
    std::fs::create_dir_all(junk.join("tools").join("__pycache__")).expect("pycache");
    std::fs::write(junk.join("tools").join("__pycache__").join("x.pyc"), "c").expect("pyc");
    std::fs::write(junk.join(".DS_Store"), "d").expect("ds");
    // 5: a process works in it; a receipt of another seat, not done, names it
    add("1ced7-busy");
    let rcpt = add("1ced7-rcpt");
    std::fs::write(
        f.root.join("043dd-r9.out"),
        format!("RECEIPT tree={}\n", rcpt.display()),
    )
    .expect("out");
    let mut p = probe();
    p.procs = Some(vec![format!("{} zsh", f.root.join("1ced7-busy").display())]);
    let rows = plan(&f.root, "1ced7", false, f.later, &mut p).expect("plan");
    let v = verdicts(&rows);
    let got = |n: &str| {
        v.iter()
            .find(|(name, _)| name == n)
            .map(|(_, w)| w.clone())
            .expect(n)
    };
    assert_eq!(got("1ced7-merged"), "remove: its head is in main");
    assert_eq!(got("1ced7-junk"), "remove: its head is in main");
    assert_eq!(got("1ced7-locked"), "keep: locked (git worktree lock)");
    assert_eq!(
        got("1ced7-ignored"),
        "keep: ignored files (first: secret.local)"
    );
    assert!(got("1ced7-busy").starts_with("keep: a process uses it: "));
    assert_eq!(
        got("1ced7-rcpt"),
        "keep: a receipt that is not done names it"
    );
    // an open PR has `side`: no merged tree goes
    p.open_heads = Some(vec![side.clone()]);
    let rows = plan(&f.root, "1ced7", false, f.later, &mut p).expect("plan");
    assert_eq!(
        verdicts(&rows)
            .iter()
            .find(|(n, _)| n == "1ced7-merged")
            .map(|(_, w)| w.as_str()),
        Some("keep: an open PR has this head")
    );
    // 1: a fetch was asked and there is no remote to fetch from
    let mut fetching = probe();
    fetching.fetch = true;
    let rows = plan(&f.root, "1ced7", false, f.later, &mut fetching).expect("plan");
    assert!(rows.iter().any(|r| r.tree.name == "1ced7-merged"
        && r.verdict == Verdict::Keep("could not fetch origin main".into())));
    // 8: another gc holds the lock: nothing is removed
    let mut rows = plan(&f.root, "1ced7", false, f.later, &mut probe()).expect("plan");
    std::fs::write(f.root.join(LOCK), "1 043dd 0").expect("lock");
    apply(
        &f.root,
        "1ced7",
        "testbox",
        &mut rows,
        f.later,
        false,
        &mut probe(),
    );
    assert!(f.root.join("1ced7-merged").is_dir());
    let merged = rows
        .iter()
        .find(|r| r.tree.name == "1ced7-merged")
        .expect("row");
    assert!(
        merged
            .failed
            .as_ref()
            .is_some_and(|e| e.contains("cleanup lock")),
        "{merged:?}"
    );
    std::fs::remove_file(f.root.join(LOCK)).expect("unlock");
    // 8: judged again under the lock: a change since the plan keeps it
    std::fs::write(f.root.join("1ced7-merged").join("a.txt"), "changed").expect("dirty");
    apply(
        &f.root,
        "1ced7",
        "testbox",
        &mut rows,
        f.later,
        false,
        &mut probe(),
    );
    assert!(f.root.join("1ced7-merged").is_dir());
    let merged = rows
        .iter()
        .find(|r| r.tree.name == "1ced7-merged")
        .expect("row");
    assert!(
        merged
            .failed
            .as_ref()
            .is_some_and(|e| e.starts_with("changed when checked again")),
        "{merged:?}"
    );
    assert!(!f.root.join(LOCK).exists());
    // the journal: one line per folder, with what was decided and why
    let jpath = f.root.parent().expect("tmp").join("cleanup.jsonl");
    journal(&jpath, &rows, "1ced7", "testbox", true, f.later).expect("journal");
    let text = std::fs::read_to_string(&jpath).expect("read");
    assert_eq!(text.lines().count(), rows.len());
    let first: serde_json::Value =
        serde_json::from_str(text.lines().next().expect("line")).expect("json");
    assert_eq!(first["hook"], "gc");
    assert_eq!(first["dry_run"], true);
    assert_eq!(first["machine"], "testbox");
}

/// Sets the time of everything under `p` (children first), `.git` left alone.
#[cfg(unix)]
fn age_all(p: &Path, t: std::time::SystemTime) {
    if p.is_dir() {
        for e in std::fs::read_dir(p).expect("read").filter_map(Result::ok) {
            if e.file_name() != ".git" {
                age_all(&e.path(), t);
            }
        }
    }
    std::fs::File::open(p)
        .and_then(|f| f.set_modified(t))
        .expect("set time");
}

#[cfg(unix)]
#[test]
fn a_fresh_index_counts_as_touched_when_the_files_are_old() {
    let f = fixture();
    let tree = f.root.join("1ced7-merged");
    let day = std::time::SystemTime::now() - std::time::Duration::from_secs(86_400);
    age_all(&tree, day);
    // the real clock: every file says a day, the work tree's own index says just now
    let now = f.later - 3 * 3600;
    let rows = plan(&f.root, "1ced7", false, now, &mut probe()).expect("plan");
    let merged = rows
        .iter()
        .find(|r| r.tree.name == "1ced7-merged")
        .expect("row");
    assert_eq!(merged.verdict, Verdict::Keep("touched 0 min ago".into()));
}

/// 5d33a S82-a L1: `aib lease status` exits 4 while a machine is held; its text is still the
/// leases. No output (or a program that does not run) is unread.
#[cfg(unix)]
#[test]
fn the_leases_are_read_whatever_the_exit_code() {
    use std::os::unix::fs::PermissionsExt as _;
    let t = tempfile::tempdir().expect("tmp");
    let fake = |name: &str, body: &str| {
        let p = t.path().join(name);
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).expect("write");
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).expect("chmod");
        p.to_string_lossy().into_owned()
    };
    let held = fake(
        "held",
        "echo 'lease pro13 held by 043dd what=1ced7-c'; exit 4",
    );
    let leases = aib_slice::gc::read_leases(&held).expect("read at exit 4");
    assert!(leases.contains("1ced7-c"), "{leases}");
    let free = fake("free", "echo 'no lease'");
    assert!(aib_slice::gc::read_leases(&free).is_some());
    let silent = fake("silent", "exit 4");
    assert_eq!(aib_slice::gc::read_leases(&silent), None);
    assert_eq!(
        aib_slice::gc::read_leases(&t.path().join("none").to_string_lossy()),
        None
    );
}
