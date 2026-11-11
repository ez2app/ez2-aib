//! `aib-drive observe` (PR-C): the journal, pause, the single-watcher lock,
//! backoff, the state root and cleaning outside text (1ced7's acceptance
//! plan V1 / V4–V9). The snapshot is a stand-in: these tests are about what
//! observe does around it, not about GitHub.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::{Known, Roles, Sha, Snapshot};
use aib_drive::observe::{
    backoff, clean, lock, once, record_of, state_root, summary, watch, Clock, Kind, Paths, Record,
    Wake, BASE, CAP, MAX_REASON,
};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Duration;

fn root(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aib-drive-obs-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// No PRs, freeze unknown: decide notes the freeze, nothing else.
fn snap() -> Snapshot {
    Snapshot {
        repo: "ez2app/ez2-aib".into(),
        roles: Roles {
            captain: "f1fae".into(),
            collector: "f1fae".into(),
        },
        freeze: Known::Unknown("freeze source not wired".into()),
        required_cells: Known::Unknown("x".into()),
        rules_version: Known::Unknown("x".into()),
        merge_slot_free: Known::Unknown("x".into()),
        busy_seats: BTreeSet::new(),
        prs: vec![],
        rulings: vec![],
        done: BTreeSet::new(),
    }
}

fn lines(p: &Path) -> Vec<Record> {
    std::fs::read_to_string(p)
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

struct FakeClock {
    t: u64,
    waits: Vec<u64>,
    /// Removed at the first wait (the pause is lifted while waiting).
    unpause: Option<PathBuf>,
}

impl Clock for FakeClock {
    fn now(&mut self) -> u64 {
        self.t += 1;
        self.t
    }
    fn wait(&mut self, d: Duration, _event: &Path) -> Wake {
        self.waits.push(d.as_secs());
        if let Some(p) = self.unpause.take() {
            std::fs::remove_file(p).unwrap();
        }
        Wake::Timeout
    }
}

fn clock() -> FakeClock {
    FakeClock {
        t: 0,
        waits: vec![],
        unpause: None,
    }
}

const REPO: &str = "ez2app/ez2-aib";

/// V1 / V6: each run appends one whole line; the decision is the same.
#[test]
fn two_rounds_append_two_lines_and_decide_the_same() {
    let p = Paths::under(&root("twice"));
    let mut r = |_: &BTreeMap<u64, Sha>| Ok(snap());
    let a = once(&p, REPO, 1, &mut r).unwrap();
    let b = once(&p, REPO, 2, &mut r).unwrap();
    assert_eq!(a.would, b.would);
    assert!(!a.would.is_empty());
    let got = lines(&p.journal);
    assert_eq!(got.len(), 2, "the journal is appended to, never rewritten");
    assert!(got.iter().all(|r| r.dry_run && r.kind == Kind::Round));
}

/// V5 a: paused before the run: refused, nothing written.
#[test]
fn a_round_while_paused_is_refused_and_writes_nothing() {
    let p = Paths::under(&root("paused"));
    std::fs::create_dir_all(&p.dir).unwrap();
    std::fs::write(&p.pause, "").unwrap();
    let mut called = false;
    let mut r = |_: &BTreeMap<u64, Sha>| {
        called = true;
        Ok(snap())
    };
    let e = once(&p, REPO, 1, &mut r).unwrap_err();
    assert!(e.contains("暫停中") && e.contains("pause"), "{e}");
    assert!(!called, "no snapshot while paused");
    assert!(!p.journal.exists());
}

/// V5 b: --watch writes one paused line, and a resumed line once the file is gone.
#[test]
fn watch_marks_the_pause_and_the_resume_once_each() {
    let p = Paths::under(&root("resume"));
    std::fs::create_dir_all(&p.dir).unwrap();
    std::fs::write(&p.pause, "").unwrap();
    let mut c = clock();
    c.unpause = Some(p.pause.clone());
    let mut r = |_: &BTreeMap<u64, Sha>| Ok(snap());
    let mut seen = Vec::new();
    let mut print = |x: &Record| seen.push(x.kind);
    watch(&p, REPO, BASE, &mut c, &mut r, &mut print, Some(1)).unwrap();
    let kinds: Vec<Kind> = lines(&p.journal).iter().map(|r| r.kind).collect();
    assert_eq!(kinds, vec![Kind::Paused, Kind::Resumed, Kind::Round]);
    assert_eq!(seen, kinds);
}

/// V4: a failed snapshot is a record with its reason, and no decision.
#[test]
fn a_failed_snapshot_is_recorded_with_its_reason() {
    let p = Paths::under(&root("unknown"));
    let mut ok = |_: &BTreeMap<u64, Sha>| {
        let mut s = snap();
        s.prs = vec![];
        Ok(s)
    };
    once(&p, REPO, 1, &mut ok).unwrap();
    let mut bad =
        |_: &BTreeMap<u64, Sha>| Err("open pulls: 100 items, may have a next page".to_owned());
    let r = once(&p, REPO, 2, &mut bad).unwrap();
    let why = r.unknown.clone().unwrap();
    assert!(why.contains("next page"), "{why}");
    assert!(r.would.is_empty());
    assert!(summary(&r).contains("快照不明"));
    assert_eq!(lines(&p.journal)[1].unknown.as_deref(), Some(why.as_str()));
}

/// V7: failures double the wait up to 600 s; a good round goes back to 60 s.
#[test]
fn failed_rounds_back_off_to_600_and_a_good_one_goes_back_to_60() {
    let got: Vec<u64> = (0..7).map(|f| backoff(BASE, CAP, f).as_secs()).collect();
    assert_eq!(got, vec![60, 120, 240, 480, 600, 600, 600]);
    let p = Paths::under(&root("backoff"));
    let mut n = 0;
    let mut r = |_: &BTreeMap<u64, Sha>| {
        n += 1;
        if n <= 5 {
            Err("gh: timed out".to_owned())
        } else {
            Ok(snap())
        }
    };
    let mut c = clock();
    let mut print = |_: &Record| {};
    watch(&p, REPO, BASE, &mut c, &mut r, &mut print, Some(7)).unwrap();
    assert_eq!(c.waits, vec![120, 240, 480, 600, 600, 60]);
}

/// V6: one watcher. A live holder is never taken over and is named; once
/// it is gone (a killed process drops its lock too) the next one starts.
#[test]
fn a_second_observe_is_refused_with_the_holders_pid() {
    let p = Paths::under(&root("lock"));
    let held = lock(&p.lock).unwrap();
    let e = lock(&p.lock).unwrap_err();
    assert!(e.contains(&std::process::id().to_string()), "{e}");
    let mut r = |_: &BTreeMap<u64, Sha>| Ok(snap());
    assert!(once(&p, REPO, 1, &mut r).is_err());
    drop(held);
    assert!(lock(&p.lock).is_ok());
}

/// K6: the state root comes from EZ2_AIB_STATE_ROOT when set.
#[test]
fn the_state_root_is_the_env_root_or_the_home_default() {
    let home = Path::new("/home/x");
    assert_eq!(
        state_root(Some("/tmp/s"), Some(home)).unwrap(),
        PathBuf::from("/tmp/s")
    );
    assert_eq!(
        state_root(Some(""), Some(home)).unwrap(),
        home.join(".ez2-aib").join("state")
    );
    assert_eq!(
        state_root(None, Some(home)).unwrap(),
        home.join(".ez2-aib").join("state")
    );
    assert!(state_root(None, None).is_err());
    assert_eq!(
        Paths::under(Path::new("/tmp/s")).journal,
        Path::new("/tmp/s").join("drive").join("dryrun.jsonl")
    );
}

/// V9: text from outside is cleaned and cut before it is printed or kept.
#[test]
fn outside_text_is_cleaned_and_cut() {
    let raw = format!("\u{1b}[31mred\u{7}\n{}", "x".repeat(1000));
    let r = record_of(1, REPO, &BTreeMap::new(), Err(raw));
    let why = r.unknown.clone().unwrap();
    assert!(!why.chars().any(char::is_control), "{why:?}");
    assert!(why.chars().count() <= MAX_REASON + 1);
    let line = serde_json::to_string(&r).unwrap();
    assert!(!line.contains('\u{1b}') && !line.contains('\n'));
    assert!(!summary(&r).chars().any(char::is_control));
    assert_eq!(clean("a\tb", 10), "a?b");
}
