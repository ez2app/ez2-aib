//! `aib-drive observe` (PR-C, M-drive-0): dry-run rounds.
//!
//! Each round takes a snapshot, runs [`decide`], and appends one record to
//! the dry-run journal. Nothing a round decides is done: there is no executor
//! in aib-drive. The only files written are under `<state root>/drive/`:
//! the journal (append only) and the lock.
//!
//! - State root: the same order as the rest of aib (`aib_init.py`):
//!   `EZ2_AIB_STATE_ROOT`, then `EZ2_AIB_HOME`, then `~/.ez2-aib`. One root
//!   means one lock and one journal, wherever observe is started from.
//! - Pause: while `drive/pause` exists no round runs. `--once` refuses;
//!   `--watch` writes one `paused` line, and a `resumed` line when the file is
//!   gone. Removing the file is enough to resume in this dry-run version;
//!   the version with real actions will need a Touch ID receipt for it.
//! - One watcher: `drive/lock` is held with an OS file lock for the whole
//!   run. A second `observe` is refused and told the holder's pid. A killed
//!   holder's lock is released by the OS, so the next one can start; a live,
//!   slow one is never taken over.
//! - Pace: a round every 300 s; touching `drive/event` starts one early.
//!   When the snapshot fails the wait doubles each time, up to 1800 s, and
//!   goes back to the interval after a good round. `--interval` is at least
//!   60 s. A round reads GitHub about 2 + 3–4 × (open PRs) times with the
//!   token every seat shares (5000 an hour), so each round first reads
//!   `gh api rate_limit` (free) and does not run below [`QUOTA_FLOOR`]
//!   calls left (1ced7 PR-C M2). No conditional requests yet.
//! - The holder's pid is kept in `drive/lock.pid`, not in the locked file:
//!   on Windows the lock is mandatory and the locked file cannot be read.
//! - Stopping: `--watch` ends by signal (Ctrl-C, SIGTERM). Every journal
//!   line is one append write, so a line is whole or absent. [`RealClock`]
//!   wakes once a second to look at the event file (no process is started);
//!   two touches within the file system's mtime resolution count once.

use crate::decide::{decide, Intent, Sha, Snapshot};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::{File, OpenOptions, TryLockError};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub const BASE: Duration = Duration::from_secs(300);
pub const CAP: Duration = Duration::from_secs(1800);
/// Fewest GitHub calls left for a round to run.
pub const QUOTA_FLOOR: u64 = 1500;
/// Longest text from outside (a source's error) kept in a record.
pub const MAX_REASON: usize = 300;
/// The shortest `--interval`.
pub const MIN_INTERVAL: Duration = Duration::from_secs(60);
/// How much of the journal's end is read for the last heads at first.
const TAIL: u64 = 64 * 1024;

/// `--interval SECS`, at least [`MIN_INTERVAL`].
pub fn interval(v: &str) -> Result<Duration, String> {
    let secs: u64 = v
        .parse()
        .map_err(|_| format!("--interval {:?}: seconds", clean(v, 20)))?;
    let d = Duration::from_secs(secs);
    if d < MIN_INTERVAL {
        return Err(format!(
            "--interval {secs}: at least {} s (each round reads GitHub)",
            MIN_INTERVAL.as_secs()
        ));
    }
    Ok(d)
}

/// The quota read: `gh api rate_limit` (a GET that does not count against
/// the quota).
pub fn quota_args() -> Vec<String> {
    vec!["api".to_owned(), "rate_limit".to_owned()]
}

/// Calls left this hour, from the `rate_limit` answer.
pub fn quota_left(v: &Value) -> Result<u64, String> {
    v.get("resources")
        .and_then(|r| r.get("core"))
        .and_then(|c| c.get("remaining"))
        .and_then(Value::as_u64)
        .ok_or_else(|| "rate_limit: no resources.core.remaining".to_owned())
}

/// A round runs only with at least `floor` calls left. An unreadable quota
/// is no round either: a guess could spend what other seats need.
pub fn quota_gate(left: Result<u64, String>, floor: u64) -> Result<(), String> {
    match left {
        Ok(n) if n >= floor => Ok(()),
        Ok(n) => Err(format!("配額保留:GitHub 剩 {n} 次,低於 {floor},這輪不讀")),
        Err(e) => Err(format!("配額讀不到,這輪不讀:{e}")),
    }
}

/// The aib state root, in `aib_init.py`'s order: `EZ2_AIB_STATE_ROOT`,
/// then `EZ2_AIB_HOME`, then `~/.ez2-aib` (an empty value counts as unset).
pub fn state_root(
    env_root: Option<&str>,
    aib_home: Option<&str>,
    home: Option<&Path>,
) -> Result<PathBuf, String> {
    if let Some(r) = env_root.filter(|r| !r.is_empty()) {
        return Ok(PathBuf::from(r));
    }
    if let Some(h) = aib_home.filter(|h| !h.is_empty()) {
        return Ok(PathBuf::from(h));
    }
    home.map(|h| h.join(".ez2-aib"))
        .ok_or_else(|| "no EZ2_AIB_STATE_ROOT, no EZ2_AIB_HOME and no home folder".to_owned())
}

/// The files of one state root.
#[derive(Debug, Clone)]
pub struct Paths {
    pub dir: PathBuf,
    pub journal: PathBuf,
    pub pause: PathBuf,
    pub lock: PathBuf,
    /// The lock holder's pid (shown to a refused second observe).
    pub pid: PathBuf,
    pub event: PathBuf,
}

impl Paths {
    pub fn under(root: &Path) -> Self {
        let dir = root.join("drive");
        Self {
            journal: dir.join("dryrun.jsonl"),
            pause: dir.join("pause"),
            lock: dir.join("lock"),
            pid: dir.join("lock.pid"),
            event: dir.join("event"),
            dir,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Round,
    Paused,
    Resumed,
}

/// One journal line.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub at: u64,
    pub kind: Kind,
    /// Always true: nothing in `would` was done.
    pub dry_run: bool,
    pub repo: String,
    /// Why there was no snapshot (cleaned, at most [`MAX_REASON`] chars).
    pub unknown: Option<String>,
    /// PR ⇒ head this round saw (the next round's `prev_head`).
    pub heads: BTreeMap<u64, Sha>,
    /// What decide would do.
    pub would: Vec<Intent>,
}

/// Invisible characters that can reorder or hide text on a terminal (bidi
/// overrides and isolates, zero-width characters, BOM, Hangul fillers, tag
/// characters, …).
fn is_format(c: char) -> bool {
    matches!(
        c,
        '\u{ad}'
            | '\u{61c}'
            | '\u{180e}'
            | '\u{200b}'..='\u{200f}'
            | '\u{202a}'..='\u{202e}'
            | '\u{2060}'..='\u{206f}'
            | '\u{feff}'
            | '\u{fff9}'..='\u{fffb}'
            | '\u{115f}'
            | '\u{1160}'
            | '\u{3164}'
            | '\u{ffa0}'
            | '\u{e0000}'..='\u{e007f}'
    )
}

/// Text from outside, made safe for one line: control and format characters
/// become `?`, and it is cut to `max` characters.
pub fn clean(s: &str, max: usize) -> String {
    let mut out: String = s
        .chars()
        .take(max)
        .map(|c| {
            if c.is_control() || is_format(c) {
                '?'
            } else {
                c
            }
        })
        .collect();
    if s.chars().count() > max {
        out.push('…');
    }
    out
}

/// The record of one round.
pub fn record_of(
    at: u64,
    repo: &str,
    prev: &BTreeMap<u64, Sha>,
    built: Result<Snapshot, String>,
) -> Record {
    let (unknown, heads, would) = match built {
        Ok(s) => (
            None,
            s.prs.iter().map(|p| (p.number, p.head.clone())).collect(),
            decide(&s),
        ),
        Err(e) => (Some(clean(&e, MAX_REASON)), prev.clone(), Vec::new()),
    };
    Record {
        at,
        kind: Kind::Round,
        dry_run: true,
        repo: repo.to_owned(),
        unknown,
        heads,
        would,
    }
}

fn mark(at: u64, repo: &str, kind: Kind, prev: &BTreeMap<u64, Sha>) -> Record {
    Record {
        at,
        kind,
        dry_run: true,
        repo: repo.to_owned(),
        unknown: None,
        heads: prev.clone(),
        would: Vec::new(),
    }
}

/// The one-line summary printed for a record (no outside text but the
/// cleaned reason).
pub fn summary(r: &Record) -> String {
    match r.kind {
        Kind::Paused => "aib-drive 暫停中(dry-run)".to_owned(),
        Kind::Resumed => "aib-drive 恢復(dry-run)".to_owned(),
        Kind::Round => match &r.unknown {
            Some(why) => format!("aib-drive 快照不明,這輪不判:{why}"),
            None => {
                let mut n: BTreeMap<&str, usize> = BTreeMap::new();
                for i in &r.would {
                    *n.entry(kind_of(i)).or_default() += 1;
                }
                let list: Vec<String> = n.iter().map(|(k, c)| format!("{k} {c}")).collect();
                format!(
                    "aib-drive dry-run:{} 張 PR,會做 {} 件({}),一件都沒做",
                    r.heads.len(),
                    r.would.len(),
                    if list.is_empty() {
                        "無".to_owned()
                    } else {
                        list.join("、")
                    }
                )
            }
        },
    }
}

fn kind_of(i: &Intent) -> &'static str {
    match i {
        Intent::Merge { .. } => "合併",
        Intent::Rerun { .. } => "重跑",
        Intent::Cancel { .. } => "取消",
        Intent::AskReview { .. } => "派審",
        Intent::AskVerify { .. } => "派驗",
        Intent::Nudge { .. } => "催",
        Intent::Stale { .. } => "頭換了",
        Intent::Ask { .. } => "問",
        Intent::Note { .. } => "記",
    }
}

/// Appends one record as one line (one write, so a line is whole or absent).
pub fn append(journal: &Path, r: &Record) -> Result<(), String> {
    if let Some(dir) = journal.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let mut line = serde_json::to_string(r).map_err(|e| format!("record: {e}"))?;
    line.push('\n');
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(journal)
        .map_err(|e| format!("{}: {e}", journal.display()))?;
    f.write_all(line.as_bytes())
        .map_err(|e| format!("{}: {e}", journal.display()))
}

/// The heads of the last readable journal line; none if there is none.
/// Reads only the end of the journal: 64 KB, doubled until a whole readable
/// line is found or the whole file was read.
pub fn prev_heads(journal: &Path) -> BTreeMap<u64, Sha> {
    let Ok(mut f) = File::open(journal) else {
        return BTreeMap::new();
    };
    let Ok(len) = f.metadata().map(|m| m.len()) else {
        return BTreeMap::new();
    };
    let mut want = TAIL;
    loop {
        let from = len.saturating_sub(want);
        let mut buf = Vec::new();
        if f.seek(SeekFrom::Start(from)).is_err() || f.read_to_end(&mut buf).is_err() {
            return BTreeMap::new();
        }
        let text = String::from_utf8_lossy(&buf);
        // Unless the read starts at the top, its first line may be cut. (No
        // knife: a cut line's tail is not a whole record, so it never parses.)
        let whole = if from == 0 {
            &text[..]
        } else {
            text.split_once('\n').map_or("", |(_, rest)| rest)
        };
        if let Some(r) = whole
            .lines()
            .rev()
            .find_map(|l| serde_json::from_str::<Record>(l).ok())
        {
            return r.heads;
        }
        if want >= len {
            // the whole file was read
            return BTreeMap::new();
        }
        want = want.saturating_mul(2);
    }
}

/// The wait after `failures` failed rounds in a row: 60, 120, 240, 480, 600…
pub fn backoff(base: Duration, cap: Duration, failures: u32) -> Duration {
    let mut d = base;
    for _ in 0..failures {
        d = d.saturating_mul(2);
        if d >= cap {
            return cap;
        }
    }
    d.min(cap)
}

/// The held lock; dropping it (or the process dying) releases it.
#[derive(Debug)]
pub struct Lock(#[allow(dead_code)] File);

/// Takes `path` with an OS lock, then writes this pid into `pid`. If
/// another process holds the lock, says which pid (from `pid`, when it can
/// be read). The locked file itself is never read: on Windows it cannot be.
pub fn lock(path: &Path, pid: &Path) -> Result<Lock, String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    match f.try_lock() {
        Ok(()) => {}
        Err(TryLockError::WouldBlock) => {
            let who = std::fs::read_to_string(pid).unwrap_or_default();
            let who = clean(who.trim(), 40);
            return Err(format!(
                "另一個 aib-drive observe 在跑(pid {}):{}",
                if who.is_empty() { "?" } else { who.as_str() },
                path.display()
            ));
        }
        Err(TryLockError::Error(e)) => return Err(format!("{}: {e}", path.display())),
    }
    std::fs::write(pid, std::process::id().to_string())
        .map_err(|e| format!("{}: {e}", pid.display()))?;
    Ok(Lock(f))
}

/// Why a wait ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Wake {
    Timeout,
    Event,
    Stop,
}

/// Time for `--watch`, injectable for tests.
pub trait Clock {
    fn now(&mut self) -> u64;
    /// Waits up to `d`, or until `event` is touched.
    fn wait(&mut self, d: Duration, event: &Path) -> Wake;
}

/// The wall clock. Waits in steps of one second on a channel nobody sends to
/// and looks at the event file between steps.
pub struct RealClock {
    rx: std::sync::mpsc::Receiver<()>,
    _tx: std::sync::mpsc::Sender<()>,
    seen: Option<std::time::SystemTime>,
}

impl RealClock {
    pub fn new() -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        Self {
            rx,
            _tx: tx,
            seen: None,
        }
    }
}

impl Default for RealClock {
    fn default() -> Self {
        Self::new()
    }
}

fn mtime(p: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(p).and_then(|m| m.modified()).ok()
}

impl Clock for RealClock {
    fn now(&mut self) -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }

    // Named exception (clippy.toml, ADR 0005): aib-drive is not aibd and has no
    // timer registry. The wait between dry-run rounds is a channel wait that
    // nothing sends on, one second at a time; nothing else ticks.
    #[allow(clippy::disallowed_methods)]
    fn wait(&mut self, d: Duration, event: &Path) -> Wake {
        if self.seen.is_none() {
            self.seen = mtime(event);
        }
        let step = Duration::from_secs(1);
        let mut left = d;
        while !left.is_zero() {
            let t = left.min(step);
            if let Err(std::sync::mpsc::RecvTimeoutError::Disconnected) = self.rx.recv_timeout(t) {
                return Wake::Stop;
            }
            left = left.saturating_sub(t);
            let now = mtime(event);
            if now.is_some() && now != self.seen {
                self.seen = now;
                return Wake::Event;
            }
        }
        Wake::Timeout
    }
}

/// One snapshot attempt, given the heads of the last record.
pub type Round<'a> = dyn FnMut(&BTreeMap<u64, Sha>) -> Result<Snapshot, String> + 'a;

/// Why `--once` did not run a round.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refused {
    /// The pause file exists.
    Paused(String),
    /// Another observe holds the lock, or a state file failed.
    State(String),
}

impl std::fmt::Display for Refused {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Paused(m) | Self::State(m) => f.write_str(m),
        }
    }
}

/// One round for `--once`. Refused while paused or while another observe
/// holds the lock. The pause file is looked at after the lock is taken, so
/// a pause made while waiting for it still counts.
pub fn once(paths: &Paths, repo: &str, now: u64, round: &mut Round<'_>) -> Result<Record, Refused> {
    let _lock = lock(&paths.lock, &paths.pid).map_err(Refused::State)?;
    if paths.pause.exists() {
        return Err(Refused::Paused(format!("暫停中:{}", paths.pause.display())));
    }
    let prev = prev_heads(&paths.journal);
    let r = record_of(now, repo, &prev, round(&prev));
    append(&paths.journal, &r).map_err(Refused::State)?;
    Ok(r)
}

/// `--watch`: rounds until the clock says stop, or `max_rounds` rounds ran
/// (tests). `print` gets each record.
pub fn watch(
    paths: &Paths,
    repo: &str,
    base: Duration,
    clock: &mut dyn Clock,
    round: &mut Round<'_>,
    print: &mut dyn FnMut(&Record),
    max_rounds: Option<u32>,
) -> Result<(), String> {
    let _lock = lock(&paths.lock, &paths.pid)?;
    let mut failures: u32 = 0;
    let mut paused = false;
    let mut rounds: u32 = 0;
    loop {
        let prev = prev_heads(&paths.journal);
        if paths.pause.exists() {
            if !paused {
                let r = mark(clock.now(), repo, Kind::Paused, &prev);
                append(&paths.journal, &r)?;
                print(&r);
                paused = true;
            }
            if clock.wait(base, &paths.event) == Wake::Stop {
                return Ok(());
            }
            continue;
        }
        if paused {
            let r = mark(clock.now(), repo, Kind::Resumed, &prev);
            append(&paths.journal, &r)?;
            print(&r);
            paused = false;
        }
        let r = record_of(clock.now(), repo, &prev, round(&prev));
        append(&paths.journal, &r)?;
        print(&r);
        failures = if r.unknown.is_some() {
            failures.saturating_add(1)
        } else {
            0
        };
        rounds = rounds.saturating_add(1);
        if max_rounds.is_some_and(|m| rounds >= m) {
            return Ok(());
        }
        if clock.wait(backoff(base, CAP, failures), &paths.event) == Wake::Stop {
            return Ok(());
        }
    }
}
