//! `aib-drive observe` (PR-C): the journal, pause, the single-watcher lock,
//! backoff, the state root and cleaning outside text (1ced7's acceptance
//! plan V1 / V4–V9). The snapshot is a stand-in: these tests are about what
//! observe does around it, not about GitHub.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::decide::{Known, Roles, Sha, Snapshot};
use aib_drive::observe::{
    append, backoff, clean, interval, lock, once, prev_heads, quota_gate, quota_left, record_of,
    state_root, summary, watch, Clock, Kind, Paths, Record, Refused, Wake, BASE, CAP, MAX_REASON,
    QUOTA_FLOOR,
};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
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
    assert!(
        matches!(&e, Refused::Paused(m) if m.contains("暫停中") && m.contains("pause")),
        "{e}"
    );
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

/// V7: failures double the wait up to the cap; a good round goes back to
/// the interval. Defaults: 300 s, capped at 1800 s (1ced7 PR-C M2).
#[test]
fn failed_rounds_back_off_to_the_cap_and_a_good_one_goes_back() {
    let got: Vec<u64> = (0..6).map(|f| backoff(BASE, CAP, f).as_secs()).collect();
    assert_eq!(got, vec![300, 600, 1200, 1800, 1800, 1800]);
    let sixty = Duration::from_secs(60);
    let got: Vec<u64> = (0..7)
        .map(|f| backoff(sixty, Duration::from_secs(600), f).as_secs())
        .collect();
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
    watch(&p, REPO, sixty, &mut c, &mut r, &mut print, Some(7)).unwrap();
    assert_eq!(c.waits, vec![120, 240, 480, 960, 1800, 60]);
}

/// V6: one watcher. A live holder is never taken over and is named; once
/// it is gone (a killed process drops its lock too) the next one starts.
#[test]
fn a_second_observe_is_refused_with_the_holders_pid() {
    let p = Paths::under(&root("lock"));
    let held = lock(&p.lock, &p.pid).unwrap();
    // the pid is read from its own file, never from the locked one (on
    // Windows the locked file cannot be read: 1ced7 PR-C M1)
    assert_eq!(std::fs::metadata(&p.lock).unwrap().len(), 0);
    let e = lock(&p.lock, &p.pid).unwrap_err();
    // "pid <n>" itself: the temp folder's name holds the pid too
    assert!(e.contains(&format!("(pid {})", std::process::id())), "{e}");
    let mut r = |_: &BTreeMap<u64, Sha>| Ok(snap());
    assert!(matches!(once(&p, REPO, 1, &mut r), Err(Refused::State(_))));
    drop(held);
    assert!(lock(&p.lock, &p.pid).is_ok());
}

/// K6: the state root in aib_init.py's order: EZ2_AIB_STATE_ROOT, then
/// EZ2_AIB_HOME, then ~/.ez2-aib (one root, so one lock and one journal).
#[test]
fn the_state_root_is_the_env_root_or_the_home_default() {
    let home = Path::new("/home/x");
    let aib = home.join(".ez2-aib");
    assert_eq!(
        state_root(Some("/tmp/s"), Some("/tmp/h"), Some(home)).unwrap(),
        PathBuf::from("/tmp/s")
    );
    assert_eq!(
        state_root(Some(""), Some("/tmp/h"), Some(home)).unwrap(),
        PathBuf::from("/tmp/h")
    );
    assert_eq!(
        state_root(None, Some("/tmp/h"), Some(home)).unwrap(),
        PathBuf::from("/tmp/h")
    );
    assert_eq!(state_root(None, Some(""), Some(home)).unwrap(), aib);
    assert_eq!(state_root(None, None, Some(home)).unwrap(), aib);
    assert_eq!(
        Paths::under(&state_root(None, None, Some(home)).unwrap()).journal,
        aib.join("drive").join("dryrun.jsonl")
    );
    assert!(state_root(None, None, None).is_err());
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

/// L2: invisible format characters (right-to-left override, zero width, BOM)
/// cannot reorder or hide the printed line.
#[test]
fn format_characters_are_cleaned_too() {
    assert_eq!(clean("ok\u{202e}gnp.exe", 20), "ok?gnp.exe");
    assert_eq!(clean("a\u{200b}b\u{2066}c\u{feff}", 20), "a?b?c?");
    assert_eq!(clean("繁中 é", 20), "繁中 é");
    assert_eq!(clean("x\u{e0041}\u{3164}y\u{115f}", 20), "x??y?");
}

/// L3: --interval is at least 60 s; every round reads GitHub.
#[test]
fn an_interval_under_60_seconds_is_refused() {
    assert!(interval("1").is_err());
    assert!(interval("59").is_err());
    assert!(interval("x").is_err());
    assert_eq!(interval("60").unwrap(), Duration::from_secs(60));
    assert_eq!(interval("600").unwrap(), Duration::from_secs(600));
}

fn heads(n: u64, sha: &str) -> BTreeMap<u64, Sha> {
    BTreeMap::from([(n, sha.repeat(40))])
}

/// L1: the last heads come from the journal's end, even when the last line
/// is longer than the first read (64 KB), and a cut line is never taken.
#[test]
fn the_last_heads_are_read_from_the_end_of_a_long_journal() {
    let p = Paths::under(&root("tail"));
    assert!(prev_heads(&p.journal).is_empty());
    let mut r = record_of(1, REPO, &heads(1, "a"), Err("x".into()));
    for _ in 0..2000 {
        append(&p.journal, &r).unwrap();
    }
    r.heads = heads(2, "b");
    append(&p.journal, &r).unwrap();
    assert_eq!(prev_heads(&p.journal), heads(2, "b"));
    // A last line of 200 KB: more than one doubling of the first read.
    let mut big = record_of(2, REPO, &heads(3, "c"), Err("x".into()));
    big.repo = "r".repeat(200 * 1024);
    append(&p.journal, &big).unwrap();
    assert_eq!(prev_heads(&p.journal), heads(3, "c"));
    // A half-written last line is skipped; the one before it counts.
    std::fs::OpenOptions::new()
        .append(true)
        .open(&p.journal)
        .unwrap()
        .write_all(b"{\"at\":9,\"kind\":\"ro")
        .unwrap();
    assert_eq!(prev_heads(&p.journal), heads(3, "c"));
}

/// 1ced7 PR-C M2: no round below the quota floor, and none when the quota
/// cannot be read.
#[test]
fn a_round_needs_the_quota_floor_left() {
    let v = |n: u64| serde_json::json!({"resources": {"core": {"remaining": n}}});
    assert_eq!(quota_left(&v(4321)).unwrap(), 4321);
    assert!(quota_left(&serde_json::json!({"resources": {}})).is_err());
    assert!(quota_gate(Ok(QUOTA_FLOOR), QUOTA_FLOOR).is_ok());
    assert!(quota_gate(Ok(5000), QUOTA_FLOOR).is_ok());
    let e = quota_gate(Ok(QUOTA_FLOOR - 1), QUOTA_FLOOR).unwrap_err();
    assert!(e.contains("配額保留"), "{e}");
    assert!(quota_gate(Err("timed out".into()), QUOTA_FLOOR).is_err());
}

// --- drive × aib-expect wiring: one report per event, a report never wakes a round ---

const EV: &str = "pr/7@766fa8f663847a5ae78111f6005a1b4a8f41bd7a";

fn wired() -> aib_drive::observe::ExpectSide {
    aib_drive::observe::ExpectSide {
        table: vec![serde_json::json!({"goal_id": "A1", "state": "unverified", "pr": 7})],
        unknown: None,
        ..Default::default()
    }
}

#[test]
fn the_same_event_twice_gives_one_report() {
    use aib_drive::observe::once_with;
    let d = root("ev-once");
    let p = Paths::under(&d);
    let mut r =
        |_: &BTreeMap<u64, Sha>| -> aib_drive::observe::Built { Ok((snap(), Some(wired()))) };
    let a = once_with(&p, REPO, 1, &mut r, Some(EV)).unwrap();
    let b = once_with(&p, REPO, 2, &mut r, Some(EV)).unwrap();
    assert_eq!(a.report.as_ref().map(|x| x.event.as_str()), Some(EV));
    assert!(b.report.is_none(), "second report for the same event");
    let j = lines(&p.journal);
    assert_eq!(j.iter().filter(|l| l.report.is_some()).count(), 1);
    // writing the report touched no event file: it starts no round
    assert!(!p.event.exists());
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn only_a_completion_event_gets_a_report() {
    use aib_drive::observe::{read_event, record_with};
    use aib_drive::report::is_event;
    for bad in [
        "",
        "report/1",
        "pr/7",
        "pr/7@766fa8f6",
        "pr/x@766fa8f663847a5ae78111f6005a1b4a8f41bd7a",
        "dm/a b",
        "now/",
        "dm//",
        "now///",
        "now/2240e/",
    ] {
        assert!(!is_event(bad), "{bad:?}");
        let r = record_with(
            1,
            REPO,
            &BTreeMap::new(),
            Ok((snap(), Some(wired()))),
            Some(bad),
        );
        assert!(r.report.is_none(), "{bad:?}");
    }
    for good in [EV, "dm/q_c192cd5d7397331c", "now/2240e/3"] {
        assert!(is_event(good), "{good:?}");
    }
    let d = root("ev-file");
    let f = d.join("event");
    std::fs::write(&f, "").unwrap();
    assert_eq!(
        read_event(&f),
        None,
        "a bare touch is a wake-up, not an event"
    );
    std::fs::write(&f, format!("{EV}\n")).unwrap();
    assert_eq!(read_event(&f).as_deref(), Some(EV));
    std::fs::write(&f, "report/x").unwrap();
    assert_eq!(read_event(&f), None);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn an_old_journal_line_without_a_report_still_reads() {
    let r = record_of(1, REPO, &BTreeMap::new(), Ok(snap()));
    let line = serde_json::to_string(&r).unwrap();
    assert!(
        !line.contains("\"report\""),
        "unwired records keep their old shape"
    );
    let back: Record = serde_json::from_str(&line).unwrap();
    assert!(back.report.is_none());
}

#[test]
fn an_ai_order_must_be_a_permutation_of_next() {
    use aib_drive::report::ai_order;
    assert!(ai_order(3, vec![2, 0, 1], "x").is_some());
    assert!(ai_order(3, vec![0, 0, 1], "x").is_none());
    assert!(ai_order(3, vec![0, 1], "x").is_none());
    assert!(ai_order(2, vec![0, 1, 2], "x").is_none());
    assert_eq!(ai_order(1, vec![0], "a\u{202e}b").unwrap().why, "a?b");
}

/// Counts GETs; every PR's head is [`H9`].
struct CountGh(std::cell::RefCell<Vec<String>>);

const H9: &str = "766fa8f663847a5ae78111f6005a1b4a8f41bd7a";

impl aib_ghread::Read for CountGh {
    fn get(&self, path: &str) -> Result<serde_json::Value, String> {
        self.0.borrow_mut().push(path.to_owned());
        Ok(serde_json::json!({ "head": { "sha": H9 } }))
    }
}

#[test]
fn one_now_for_the_prs_to_read_and_the_comparison() {
    use aib_drive::observe::expect_side;
    // 1ced7 wiring L1: a line right at the future limit is read and judged,
    // one second past it is neither (both sides use the same `now`)
    let now = 1_000_000;
    let goal = aib_expect::Goal {
        id: "A1".into(),
        text: "t".into(),
    };
    let edge = now + aib_expect::FUTURE_SECS;
    for (at, inside) in [(edge, true), (edge + 1, false)] {
        let line = serde_json::json!({
            "goal_id": "A1", "goal_sha": aib_expect::goal_sha("t"), "at": at, "seat": "1ced7",
            "check_id": "c", "rc": 0, "summary": "s", "pr": 9, "head": H9
        });
        let ev = aib_expect::parse_evidence(&format!("{line}\n"));
        let gh = CountGh(std::cell::RefCell::new(vec![]));
        let side = expect_side(&gh, REPO, Ok(vec![goal.clone()]), Ok(ev), &snap(), now);
        let row = &side.table[0];
        if inside {
            assert_eq!(*gh.0.borrow(), vec![format!("repos/{REPO}/pulls/9")]);
            assert_eq!(row["state"], "pass", "{row}");
            assert_eq!(row["pr"], 9);
        } else {
            assert!(
                gh.0.borrow().is_empty(),
                "a line past the limit names no PR to read"
            );
            assert_eq!(row["state"], "unverified", "{row}");
        }
    }
}

#[test]
fn goals_or_evidence_that_cannot_be_read_give_an_unknown_side_and_no_reads() {
    use aib_drive::observe::expect_side;
    let gh = CountGh(std::cell::RefCell::new(vec![]));
    let goal = aib_expect::Goal {
        id: "A1".into(),
        text: "t".into(),
    };
    let bad = || aib_expect::GoalsError::Unreadable("x".into());
    let a = expect_side(&gh, REPO, Err(bad()), Ok(Default::default()), &snap(), 1);
    let b = expect_side(&gh, REPO, Ok(vec![goal]), Err(bad()), &snap(), 1);
    for s in [a, b] {
        assert!(s.table.is_empty());
        assert_eq!(s.unknown.as_deref(), Some(bad().code()));
    }
    assert!(gh.0.borrow().is_empty());
}

/// A clock that wakes as scripted (then stops), writing `event` into the
/// event file before each `Wake::Event`, and lifting the pause at the first wait.
struct ScriptClock {
    t: u64,
    script: Vec<Wake>,
    event: String,
    unpause: Option<PathBuf>,
}

impl Clock for ScriptClock {
    fn now(&mut self) -> u64 {
        self.t += 1;
        self.t
    }
    fn wait(&mut self, _d: Duration, event: &Path) -> Wake {
        if let Some(p) = self.unpause.take() {
            std::fs::remove_file(p).unwrap();
        }
        if self.script.is_empty() {
            return Wake::Stop;
        }
        let w = self.script.remove(0);
        if w == Wake::Event {
            std::fs::write(event, &self.event).unwrap();
        }
        w
    }
}

fn reports(p: &Path) -> Vec<(u64, String)> {
    lines(p)
        .into_iter()
        .filter_map(|r| r.report.map(|x| (r.at, x.event)))
        .collect()
}

#[test]
fn an_event_met_by_a_failed_round_is_answered_by_the_next_good_one() {
    use aib_drive::observe::watch_with;
    let d = root("ev-retry");
    let p = Paths::under(&d);
    let mut n = 0;
    let mut r = |_: &BTreeMap<u64, Sha>| -> aib_drive::observe::Built {
        n += 1;
        if n == 2 {
            Err("gh api rate_limit: timed out".into())
        } else {
            Ok((snap(), Some(wired())))
        }
    };
    let mut c = ScriptClock {
        t: 0,
        script: vec![Wake::Event, Wake::Timeout],
        event: EV.into(),
        unpause: None,
    };
    let mut print = |_: &Record| {};
    watch_with(&p, REPO, BASE, &mut c, &mut r, &mut print, Some(3)).unwrap();
    // round 1 had no event, round 2 failed, round 3 answers it
    assert_eq!(reports(&p.journal), vec![(3, EV.to_owned())]);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn an_event_written_while_paused_is_answered_after_the_pause() {
    use aib_drive::observe::watch_with;
    let d = root("ev-pause");
    let p = Paths::under(&d);
    std::fs::create_dir_all(p.pause.parent().unwrap()).unwrap();
    std::fs::write(&p.pause, "").unwrap();
    let mut r =
        |_: &BTreeMap<u64, Sha>| -> aib_drive::observe::Built { Ok((snap(), Some(wired()))) };
    let mut c = ScriptClock {
        t: 0,
        script: vec![Wake::Event],
        event: EV.into(),
        unpause: Some(p.pause.clone()),
    };
    let mut print = |_: &Record| {};
    watch_with(&p, REPO, BASE, &mut c, &mut r, &mut print, Some(1)).unwrap();
    assert_eq!(reports(&p.journal).len(), 1, "{:?}", lines(&p.journal));
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn watch_reports_the_same_event_once() {
    use aib_drive::observe::watch_with;
    let d = root("ev-watch-twice");
    let p = Paths::under(&d);
    let mut r =
        |_: &BTreeMap<u64, Sha>| -> aib_drive::observe::Built { Ok((snap(), Some(wired()))) };
    let mut c = ScriptClock {
        t: 0,
        script: vec![Wake::Event, Wake::Event],
        event: EV.into(),
        unpause: None,
    };
    let mut print = |_: &Record| {};
    watch_with(&p, REPO, BASE, &mut c, &mut r, &mut print, Some(3)).unwrap();
    assert_eq!(reports(&p.journal), vec![(2, EV.to_owned())]);
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn expect_rows_reach_drive_without_an_order_of_their_own() {
    // 043dd wiring L1: aib-expect gives gaps, never a rank or a next list
    use aib_drive::observe::expect_side;
    let gh = CountGh(std::cell::RefCell::new(vec![]));
    let goals = vec![
        aib_expect::Goal {
            id: "A1".into(),
            text: "a".into(),
        },
        aib_expect::Goal {
            id: "A2".into(),
            text: "b".into(),
        },
    ];
    let side = expect_side(&gh, REPO, Ok(goals), Ok(Default::default()), &snap(), 1);
    assert_eq!(side.table.len(), 2);
    for row in &side.table {
        for k in row.as_object().unwrap().keys() {
            assert!(
                !k.contains("order") && !k.contains("next") && !k.contains("rank"),
                "{k}"
            );
        }
    }
}

#[test]
fn the_report_says_how_many_evidence_lines_were_unreadable() {
    // 1ced7 wiring N1: skipped evidence is counted in the report, not only
    // visible as a thinner table
    use aib_drive::observe::{expect_side, record_with};
    let gh = CountGh(std::cell::RefCell::new(vec![]));
    let goals = vec![aib_expect::Goal {
        id: "A1".into(),
        text: "a".into(),
    }];
    let log = aib_expect::EvidenceLog {
        unreadable: 2,
        ..Default::default()
    };
    let side = expect_side(&gh, REPO, Ok(goals), Ok(log), &snap(), 1);
    assert_eq!(side.unreadable_evidence, 2);
    let rec = record_with(
        1,
        REPO,
        &BTreeMap::new(),
        Ok((snap(), Some(side))),
        Some(EV),
    );
    assert_eq!(rec.report.unwrap().unreadable_evidence, 2);
    // a report written before the field still reads, as 0
    let old = serde_json::json!({
        "event": EV, "at": 1, "table": [], "expect_unknown": null,
        "next": [], "ai_order": null, "dry_run": true
    });
    let r: aib_drive::report::Report = serde_json::from_value(old).unwrap();
    assert_eq!(r.unreadable_evidence, 0);
}
