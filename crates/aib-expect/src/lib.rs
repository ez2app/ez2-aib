//! `aib-expect` v0: compare the human's expectations with what has been
//! checked, and say what comes next (DECISION-expect-check-a63ab-1111).
//!
//! Andy 11-11 21:5x: at the start of every seat and after every task, look
//! back at the human's expectations, list the comparison, and say the fastest
//! order to go on.
//!
//! - **Expectations** are the `goals` of the project card's constitution
//!   (`<state>/projects/<uid>/card.json`, `layers.constitution`). Only the
//!   human changes them (`aib newproj --approve`, Touch ID). This crate reads
//!   them only if their hash has an approved snapshot
//!   (`approved/<sha256>.json`, the same canonical hash `new_project.py`
//!   writes); otherwise it refuses with `expect_unsigned`. The card is an
//!   unsigned local file, so this shows that someone changed the goals
//!   without approving them; it is not a defence against a same-user AI.
//! - **Evidence** is `<state>/projects/<uid>/evidence.jsonl`, append-only,
//!   written by seats. A row's status comes from the latest evidence line for
//!   that goal: no line, an expired line, a line for a head the PR no longer
//!   has, or a PR head that cannot be read are all **unverified**, never a
//!   pass and never an older value. A line binds to the goal's exact wording
//!   (`goal_sha`, see [`goal_sha`]); a line for other wording, or a line that
//!   cannot be read after the latest readable one, is unverified too.
//! - Every local file is read with a byte limit; over it is an error, never a
//!   silent cut.
//! - **Events** (seat start, PR merged, ...) are each evaluated and recorded
//!   in `<state>/projects/<uid>/expect-evals.jsonl`. Nothing is dropped by
//!   throttling; only the display of two events from one seat within 60
//!   seconds is merged.
//! - Read-only towards everything else: no merge, push, dm or approval. What
//!   it suggests is not an authorisation and is never itself an event.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::io::{Read as _, Seek as _, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// One expectation, as approved. `id` is the leading `A4b`-style token of the
/// text when there is one, else `G<n>` (1-based position).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Goal {
    pub id: String,
    pub text: String,
}

/// Why the goals could not be used. Each has a stable code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GoalsError {
    BadProject(String),
    NoCard(PathBuf),
    Unreadable(String),
    NotApproved,
    BadItem(String),
    /// The goals / red lines on the card hash to `sha`, and no approved
    /// snapshot with that content exists.
    Unsigned {
        sha: String,
    },
}

impl GoalsError {
    pub fn code(&self) -> &'static str {
        match self {
            GoalsError::BadProject(_) => "expect_bad_project",
            GoalsError::NoCard(_) => "expect_no_card",
            GoalsError::Unreadable(_) => "expect_card_unreadable",
            GoalsError::NotApproved => "expect_not_approved",
            GoalsError::BadItem(_) => "expect_bad_goal",
            GoalsError::Unsigned { .. } => "expect_unsigned",
        }
    }
}

impl std::fmt::Display for GoalsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GoalsError::BadProject(p) => write!(f, "{}: bad project id {p:?}", self.code()),
            GoalsError::NoCard(p) => write!(f, "{}: no card at {}", self.code(), p.display()),
            GoalsError::Unreadable(e) => write!(f, "{}: {e}", self.code()),
            GoalsError::NotApproved => write!(
                f,
                "{}: the constitution is not approved (aib newproj --approve)",
                self.code()
            ),
            GoalsError::BadItem(e) => write!(f, "{}: {e}", self.code()),
            GoalsError::Unsigned { sha } => write!(
                f,
                "{}: the goals on the card (sha256 {sha}) have no approved snapshot: changed without the human's approval",
                self.code()
            ),
        }
    }
}

/// `--state-root` > `EZ2_AIB_STATE_ROOT` > `EZ2_AIB_HOME` > `~/.ez2-aib`
/// (the order `new_project.py` uses).
pub fn state_root(explicit: Option<&str>) -> Option<PathBuf> {
    if let Some(p) = explicit.filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(p));
    }
    for name in ["EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"] {
        if let Some(v) = std::env::var_os(name).filter(|v| !v.is_empty()) {
            return Some(PathBuf::from(v));
        }
    }
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    Some(PathBuf::from(home).join(".ez2-aib"))
}

/// Which file-system rules a project folder name has to follow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    Windows,
    Unix,
}

impl Os {
    /// The platform this binary runs on.
    pub fn current() -> Os {
        if cfg!(windows) {
            Os::Windows
        } else {
            Os::Unix
        }
    }
}

/// The folder names to try for project `uid`, in order. A real id is
/// `proj:<hex>`; on NTFS `a:b` is stream `b` of file `a`, so Windows only
/// ever uses `_` for `:`. mac / Linux try the 1.x name first (bindme.py
/// globs `proj:*`), then the `_` one (a63ab FOLLOWUP X1; the same
/// `replace(":", "_")` as aib_records.py / memory_archive.py).
pub fn project_dir_names(uid: &str, os: Os) -> Vec<String> {
    let safe = uid.replace(':', "_");
    match os {
        Os::Windows => vec![safe],
        Os::Unix if safe != uid => vec![uid.to_owned(), safe],
        Os::Unix => vec![safe],
    }
}

/// `<root>/projects/<uid>`, refusing ids that could leave that folder.
pub fn project_dir(root: &Path, uid: &str) -> Result<PathBuf, GoalsError> {
    project_dir_on(root, uid, Os::current())
}

/// [`project_dir`] for a given platform: the first of
/// [`project_dir_names`] that is a folder, else the first name (whose card
/// is then not found).
pub fn project_dir_on(root: &Path, uid: &str, os: Os) -> Result<PathBuf, GoalsError> {
    let ok = !uid.is_empty()
        && uid.len() <= 64
        && uid != "."
        && uid != ".."
        && uid
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, ':' | '.' | '_' | '-'));
    // one plain path component: on Windows `C:foo` is a drive-relative path
    // whose join would replace the base (a9136 / Codex #693)
    let plain = {
        let mut cs = Path::new(uid).components();
        matches!(
            (cs.next(), cs.next()),
            (Some(std::path::Component::Normal(_)), None)
        )
    };
    if !ok || !plain {
        return Err(GoalsError::BadProject(uid.to_owned()));
    }
    let base = root.join("projects");
    let mut dirs = Vec::new();
    for name in project_dir_names(uid, os) {
        let dir = base.join(&name);
        if dir.parent() != Some(base.as_path()) {
            return Err(GoalsError::BadProject(uid.to_owned()));
        }
        dirs.push(dir);
    }
    let first = dirs[0].clone();
    Ok(dirs.into_iter().find(|d| d.is_dir()).unwrap_or(first))
}

/// The canonical hash of goals + red lines, byte-for-byte the one
/// `new_project.py::_const_sha` writes (`json.dumps(sort_keys=True,
/// separators=(",", ":"), ensure_ascii=False)`).
pub fn const_sha(goals: &[String], red_lines: &[String]) -> String {
    let list = |v: &[String]| serde_json::to_string(v).unwrap_or_default();
    let body = format!(
        "{{\"goals\":{},\"red_lines\":{}}}",
        list(goals),
        list(red_lines)
    );
    hex(&Sha256::digest(body.as_bytes()))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn strings(v: Option<&Value>, what: &str) -> Result<Vec<String>, GoalsError> {
    match v {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(a)) => a
            .iter()
            .map(|i| {
                i.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| GoalsError::BadItem(format!("{what}: an item is not text")))
            })
            .collect(),
        Some(_) => Err(GoalsError::BadItem(format!("{what} is not a list"))),
    }
}

/// Largest card / approved snapshot read (bytes).
pub const MAX_CARD_BYTES: u64 = 1 << 20;
/// Largest `evidence.jsonl` read (bytes).
pub const MAX_EVIDENCE_BYTES: u64 = 16 << 20;
/// How much of the end of `expect-evals.jsonl` the display throttle reads.
pub const EVALS_TAIL_BYTES: u64 = 256 << 10;

/// The whole file, or an error when it is larger than `max` bytes (never a
/// silent cut: a cut file could drop the newest evidence).
fn read_capped(p: &Path, max: u64) -> std::io::Result<String> {
    let f = fs::File::open(p)?;
    let mut t = String::new();
    f.take(max + 1).read_to_string(&mut t)?;
    if t.len() as u64 > max {
        return Err(std::io::Error::other(format!("larger than {max} bytes")));
    }
    Ok(t)
}

/// The last `n` bytes, from the first whole line in them ("" when unreadable).
fn read_tail(p: &Path, n: u64) -> String {
    let Ok(mut f) = fs::File::open(p) else {
        return String::new();
    };
    let len = f.metadata().map_or(0, |m| m.len());
    let from = len.saturating_sub(n);
    if f.seek(SeekFrom::Start(from)).is_err() {
        return String::new();
    }
    let mut b = Vec::new();
    if f.take(n).read_to_end(&mut b).is_err() {
        return String::new();
    }
    let t = String::from_utf8_lossy(&b).into_owned();
    if from == 0 {
        t
    } else {
        t.split_once('\n')
            .map(|(_, rest)| rest.to_owned())
            .unwrap_or_default()
    }
}

fn read_json(p: &Path) -> Result<Value, String> {
    let t = read_capped(p, MAX_CARD_BYTES).map_err(|e| format!("{}: {e}", p.display()))?;
    serde_json::from_str(&t).map_err(|e| format!("{}: {e}", p.display()))
}

/// `A1`, `A4b`, `C10`: an upper-case letter, digits, an optional lower-case
/// letter. A trailing `:` or `.` is dropped.
fn goal_id(text: &str) -> Option<String> {
    let first = text.split_whitespace().next()?;
    let t = first.trim_end_matches([':', '.', '：']);
    let mut cs = t.chars();
    let head = cs.next()?;
    if !head.is_ascii_uppercase() {
        return None;
    }
    let rest: Vec<char> = cs.collect();
    let digits = rest.iter().take_while(|c| c.is_ascii_digit()).count();
    let tail = &rest[digits..];
    let ok = digits > 0 && (tail.is_empty() || (tail.len() == 1 && tail[0].is_ascii_lowercase()));
    ok.then(|| t.to_owned())
}

/// The approved goals of one project, or why they cannot be used.
pub fn load_goals(root: &Path, uid: &str) -> Result<Vec<Goal>, GoalsError> {
    let dir = project_dir(root, uid)?;
    let card_path = dir.join("card.json");
    if !card_path.is_file() {
        return Err(GoalsError::NoCard(card_path));
    }
    let card = read_json(&card_path).map_err(GoalsError::Unreadable)?;
    let c = card
        .get("layers")
        .and_then(|l| l.get("constitution"))
        .ok_or_else(|| GoalsError::Unreadable("the card has no constitution".to_owned()))?;
    if c.get("approved") != Some(&Value::Bool(true)) {
        return Err(GoalsError::NotApproved);
    }
    let goals = strings(c.get("goals"), "goals")?;
    let red = strings(c.get("red_lines"), "red_lines")?;
    let sha = const_sha(&goals, &red);
    // the snapshot must exist and still hash to its own name
    let snap_ok = read_json(&dir.join("approved").join(format!("{sha}.json")))
        .ok()
        .and_then(|s| {
            let g = strings(s.get("goals"), "goals").ok()?;
            let r = strings(s.get("red_lines"), "red_lines").ok()?;
            Some(const_sha(&g, &r) == sha)
        })
        .unwrap_or(false);
    if !snap_ok {
        return Err(GoalsError::Unsigned { sha });
    }
    let mut out: Vec<Goal> = Vec::new();
    for (i, text) in goals.into_iter().enumerate() {
        let id = goal_id(&text).unwrap_or_else(|| format!("G{}", i + 1));
        if out.iter().any(|g| g.id == id) {
            return Err(GoalsError::BadItem(format!("two goals are both {id}")));
        }
        out.push(Goal { id, text });
    }
    Ok(out)
}

/// One evidence line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Evidence {
    pub goal: String,
    pub at: u64,
    pub seat: String,
    pub pr: Option<u64>,
    pub head: Option<String>,
    pub check: String,
    pub rc: i64,
    pub summary: String,
    pub valid_until: Option<u64>,
    /// `kind: "inferred"`: a reasoned guess, shown as such, never a pass.
    pub inferred: bool,
    /// [`goal_sha`] of the wording this line checked.
    pub goal_sha: Option<String>,
    /// 0-based line number in the file.
    pub pos: usize,
}

/// Evidence lines, and how many lines could not be read (shown, not hidden).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EvidenceLog {
    pub lines: Vec<Evidence>,
    pub unreadable: usize,
    /// Each unreadable line: its line number and the goal it names, when
    /// that much can be read (`None`: it could be about any goal).
    pub bad: Vec<(usize, Option<String>)>,
}

/// The first 16 hex digits of sha256 of a goal's text. An evidence line
/// carries it as `goal_sha`, so evidence for an earlier wording of the same
/// goal id (or for a goal whose `G<n>` position moved) does not count.
pub fn goal_sha(text: &str) -> String {
    let d = Sha256::digest(text.as_bytes());
    d.iter().take(8).map(|b| format!("{b:02x}")).collect()
}

/// One line, or the goal id it names when it cannot be used. A field of the
/// wrong type is an unreadable line, not a missing field; `pr` and `head`
/// come together or not at all (a pr without a head could never go stale).
fn evidence_line(v: &Value, pos: usize) -> Result<Evidence, Option<String>> {
    let goal = v
        .get("goal_id")
        .and_then(Value::as_str)
        .filter(|g| !g.is_empty())
        .map(str::to_owned);
    let text = |k: &str| match v.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(t)) => Ok(Some(t.clone())),
        Some(_) => Err(goal.clone()),
    };
    let num = |k: &str| match v.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(x) => x.as_u64().map(Some).ok_or_else(|| goal.clone()),
    };
    if !v.is_object() {
        return Err(None);
    }
    let kind = text("kind")?;
    let inferred = kind.as_deref() == Some("inferred");
    let head = text("head")?;
    if head
        .as_deref()
        .is_some_and(|h| h.len() != 40 || !h.chars().all(|c| c.is_ascii_hexdigit()))
    {
        return Err(goal.clone());
    }
    let pr = num("pr")?;
    if pr.is_some() != head.is_some() {
        return Err(goal.clone());
    }
    let rc = match v.get("rc") {
        None | Some(Value::Null) if inferred => 0,
        // a check line must have an rc
        None | Some(Value::Null) => return Err(goal.clone()),
        Some(x) => x.as_i64().ok_or_else(|| goal.clone())?,
    };
    Ok(Evidence {
        goal: goal.clone().ok_or(None)?,
        at: num("at")?.ok_or_else(|| goal.clone())?,
        seat: text("seat")?.unwrap_or_default(),
        pr,
        head,
        check: text("check_id")?.unwrap_or_default(),
        rc,
        summary: text("summary")?.unwrap_or_default(),
        valid_until: num("valid_until")?,
        inferred,
        goal_sha: text("goal_sha")?,
        pos,
    })
}

/// Parse `evidence.jsonl` text.
pub fn parse_evidence(text: &str) -> EvidenceLog {
    let mut log = EvidenceLog::default();
    for (pos, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let parsed = serde_json::from_str::<Value>(line)
            .map_err(|_| None)
            .and_then(|v| evidence_line(&v, pos));
        match parsed {
            Ok(e) => log.lines.push(e),
            Err(goal) => {
                log.unreadable += 1;
                log.bad.push((pos, goal));
            }
        }
    }
    log
}

/// Read `<project>/evidence.jsonl`; a missing file is no evidence.
pub fn load_evidence(root: &Path, uid: &str) -> Result<EvidenceLog, GoalsError> {
    let p = project_dir(root, uid)?.join("evidence.jsonl");
    match read_capped(&p, MAX_EVIDENCE_BYTES) {
        Ok(t) => Ok(parse_evidence(&t)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(EvidenceLog::default()),
        Err(e) => Err(GoalsError::Unreadable(format!("{}: {e}", p.display()))),
    }
}

/// What is known about the world right now: each PR's head, `None` when it
/// could not be read.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Snapshot {
    pub pr_heads: BTreeMap<u64, Option<String>>,
}

/// Why a row is unverified. Each has a stable code (043dd knives assert it).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Why {
    /// No evidence line for this goal.
    NoEvidence,
    /// The latest line's `valid_until` has passed.
    Expired,
    /// The latest line is for a head the PR no longer has.
    StaleHead { pr: u64, was: String, now: String },
    /// The PR's current head could not be read (the source said Err).
    SourceUnknown { pr: u64 },
    /// The line is for other wording of this goal, or names none.
    GoalChanged,
    /// A line after the latest readable one for this goal cannot be read
    /// (it names this goal, or no goal at all).
    EvidenceUnreadable,
}

impl Why {
    pub fn code(&self) -> &'static str {
        match self {
            Why::NoEvidence => "no_evidence",
            Why::Expired => "evidence_expired",
            Why::StaleHead { .. } => "evidence_stale_head",
            Why::SourceUnknown { .. } => "source_unknown",
            Why::GoalChanged => "evidence_goal_changed",
            Why::EvidenceUnreadable => "evidence_unreadable",
        }
    }

    pub fn text(&self) -> String {
        match self {
            Why::NoEvidence => "沒有證據".to_owned(),
            Why::Expired => "證據過期".to_owned(),
            Why::StaleHead { pr, was, now } => format!(
                "換頭:證據在 {}、#{pr} 現在 {}",
                was.get(..12).unwrap_or(was),
                now.get(..12).unwrap_or(now)
            ),
            Why::SourceUnknown { pr } => format!("#{pr} 頭碼讀不到"),
            Why::GoalChanged => "證據不是對這版期望(goal_sha 不符或沒帶)".to_owned(),
            Why::EvidenceUnreadable => "最新的證據行讀不懂".to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    /// 實查通過
    Pass,
    /// 實查不通過
    Fail,
    /// 未驗, with the reason
    Unverified(Why),
    /// 推測
    Inferred,
}

impl Status {
    pub fn label(&self) -> &'static str {
        match self {
            Status::Pass => "實查通過",
            Status::Fail => "實查不通過",
            Status::Unverified(_) => "未驗",
            Status::Inferred => "推測",
        }
    }
    fn code(&self) -> &'static str {
        match self {
            Status::Pass => "pass",
            Status::Fail => "fail",
            Status::Unverified(_) => "unverified",
            Status::Inferred => "inferred",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub goal: Goal,
    pub status: Status,
    pub evidence: Option<Evidence>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub rows: Vec<Row>,
    pub unreadable_evidence: usize,
    /// Up to three goal ids, by rule: failing first, then unverified, then
    /// inferred, each in goal order.
    pub next: Vec<String>,
}

impl Report {
    pub fn count(&self, code: &str) -> usize {
        self.rows.iter().filter(|r| r.status.code() == code).count()
    }
}

fn status_of(goal: &Goal, e: Option<&Evidence>, snap: &Snapshot, now: u64) -> Status {
    let Some(e) = e else {
        return Status::Unverified(Why::NoEvidence);
    };
    if e.goal_sha.as_deref() != Some(goal_sha(&goal.text).as_str()) {
        return Status::Unverified(Why::GoalChanged);
    }
    if e.valid_until.is_some_and(|t| t < now) {
        return Status::Unverified(Why::Expired);
    }
    if let (Some(pr), Some(head)) = (e.pr, e.head.as_deref()) {
        match snap.pr_heads.get(&pr) {
            Some(Some(now_head)) if now_head == head => {}
            Some(Some(now_head)) => {
                return Status::Unverified(Why::StaleHead {
                    pr,
                    was: head.to_owned(),
                    now: now_head.clone(),
                })
            }
            _ => return Status::Unverified(Why::SourceUnknown { pr }),
        }
    }
    if e.inferred {
        return Status::Inferred;
    }
    if e.rc == 0 {
        Status::Pass
    } else {
        Status::Fail
    }
}

/// Most PRs one report reads heads for; the rest stay unknown.
pub const MAX_PRS: usize = 30;

/// One goal's deciding line, and whether something that cannot be used (an
/// unreadable line, or a line dated more than [`FUTURE_SECS`] ahead) comes
/// after it in the file. [`compare`] and [`prs_of`] both use this, so they
/// always look at the same line.
fn pick<'a>(evidence: &'a EvidenceLog, goal: &str, now: u64) -> (Option<&'a Evidence>, bool) {
    let limit = now.saturating_add(FUTURE_SECS);
    // the latest line wins, even when it is worse than an older one
    let latest = evidence
        .lines
        .iter()
        .enumerate()
        .filter(|(_, e)| e.goal == goal && e.at <= limit)
        .max_by_key(|(i, e)| (e.at, *i))
        .map(|(_, e)| e);
    // what cannot be used, after the chosen line, could be the newest result:
    // never fall back. Only a line that is itself chosen clears it, not an
    // older one appended later (Codex #693 r2).
    let future = evidence
        .lines
        .iter()
        .filter(|e| e.goal == goal && e.at > limit)
        .map(|e| (e.pos, None));
    let bad_after = evidence.bad.iter().cloned().chain(future).any(|(pos, g)| {
        g.as_deref().is_none_or(|x| x == goal) && latest.is_none_or(|l| pos > l.pos)
    });
    (latest, bad_after)
}

/// The PRs whose heads decide a row: the PR of each current goal's deciding
/// line ([`pick`], the same one [`compare`] uses), not every PR the history
/// ever named, so old PRs never push a new one past [`MAX_PRS`] (a9136 #693).
/// Only the goals on the card count: a goal taken off it has no row, so its
/// PRs are never read (a9136 #693 N1, FOLLOWUP X2).
pub fn prs_of(goals: &[Goal], evidence: &EvidenceLog, now: u64) -> std::collections::BTreeSet<u64> {
    goals
        .iter()
        .map(|g| g.id.as_str())
        .filter_map(|g| match pick(evidence, g, now) {
            (Some(e), false) => e.pr,
            _ => None,
        })
        .collect()
}

/// Each PR's current head through aib-drive's GET-only reader (the same one
/// the drive snapshot uses, so both agree on what "could not read" means).
/// A failed call, an odd answer or a PR past [`MAX_PRS`] is `None`: unknown,
/// which makes its rows unverified, never an old pass.
pub fn read_heads(
    gh: &dyn aib_drive::snapshot::Read,
    repo: &str,
    prs: &std::collections::BTreeSet<u64>,
) -> Snapshot {
    let pr_heads = prs
        .iter()
        .enumerate()
        .map(|(i, n)| {
            let head = (i < MAX_PRS)
                .then(|| gh.get(&format!("repos/{repo}/pulls/{n}")).ok())
                .flatten()
                .and_then(|v| {
                    v.pointer("/head/sha")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .filter(|h| h.len() == 40 && h.chars().all(|c| c.is_ascii_hexdigit()));
            (*n, head)
        })
        .collect();
    Snapshot { pr_heads }
}

/// An `at` further than this past `now` is unreadable: a line dated in the
/// future would otherwise win over every later one.
pub const FUTURE_SECS: u64 = 300;

/// The comparison. Pure: same input, same report.
pub fn compare(goals: &[Goal], evidence: &EvidenceLog, snap: &Snapshot, now: u64) -> Report {
    let rows: Vec<Row> = goals
        .iter()
        .map(|g| {
            let (latest, bad_after) = pick(evidence, &g.id, now);
            let latest = latest.cloned();
            Row {
                status: if bad_after {
                    Status::Unverified(Why::EvidenceUnreadable)
                } else {
                    status_of(g, latest.as_ref(), snap, now)
                },
                goal: g.clone(),
                evidence: latest,
            }
        })
        .collect();
    let mut next = Vec::new();
    for code in ["fail", "unverified", "inferred"] {
        for r in rows.iter().filter(|r| r.status.code() == code) {
            if next.len() < 3 {
                next.push(r.goal.id.clone());
            }
        }
    }
    Report {
        rows,
        unreadable_evidence: evidence.unreadable,
        next,
    }
}

/// The order an AI proposes instead of the rule's. It must say why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AiOrder {
    pub ids: Vec<String>,
    pub why: String,
}

impl AiOrder {
    pub fn new(ids: Vec<String>, why: &str, report: &Report) -> Result<Self, String> {
        if why.trim().is_empty() {
            return Err("expect_order_without_reason: an AI order needs a reason".to_owned());
        }
        if let Some(bad) = ids
            .iter()
            .find(|i| !report.rows.iter().any(|r| &r.goal.id == *i))
        {
            return Err(format!("expect_order_unknown_goal: {bad}"));
        }
        Ok(Self {
            ids,
            why: why.to_owned(),
        })
    }
}

/// Text that came from a seat, made safe to print: no control characters,
/// no escape sequences, one line, at most `max` characters.
pub fn clean(s: &str, max: usize) -> String {
    let one: String = s
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .filter(|c| !matches!(c, '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2060}'..='\u{2069}' | '\u{feff}'))
        .collect();
    let one = one.split_whitespace().collect::<Vec<_>>().join(" ");
    if one.chars().count() <= max {
        one
    } else {
        let mut t: String = one.chars().take(max.saturating_sub(1)).collect();
        t.push('…');
        t
    }
}

/// The full table, for a terminal.
pub fn render(report: &Report, ai: Option<&AiOrder>) -> String {
    let mut out = String::new();
    out.push_str(&format!(
        "期望值對比:實查通過 {} / 實查 {}(未驗 {}、推測 {})\n",
        report.count("pass"),
        report.count("pass") + report.count("fail"),
        report.count("unverified"),
        report.count("inferred"),
    ));
    out.push_str("| # | 期望 | 狀態 | 證據 / 差什麼 |\n|---|---|---|---|\n");
    for r in &report.rows {
        let why = match (&r.status, &r.evidence) {
            (Status::Unverified(w), _) => w.text(),
            (_, Some(e)) => format!("{} {} rc {}:{}", e.seat, e.check, e.rc, e.summary),
            (_, None) => String::new(),
        };
        out.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            r.goal.id,
            clean(&r.goal.text, 60).replace('|', "/"),
            r.status.label(),
            clean(&why, 80).replace('|', "/"),
        ));
    }
    if report.unreadable_evidence > 0 {
        out.push_str(&format!(
            "證據檔有 {} 行讀不懂(沒算進任何一列)\n",
            report.unreadable_evidence
        ));
    }
    out.push_str(&format!("接下來(規則順序):{}\n", order_text(&report.next)));
    if let Some(a) = ai {
        out.push_str(&format!(
            "接下來(AI 建議):{};理由:{}\n",
            order_text(&a.ids),
            clean(&a.why, 120)
        ));
    }
    out
}

/// 3–5 lines for the end of a reply: the rows that are not passing, then
/// what comes next.
pub fn render_short(report: &Report, ai: Option<&AiOrder>) -> String {
    let mut out = Vec::new();
    for r in report
        .rows
        .iter()
        .filter(|r| r.status != Status::Pass)
        .take(3)
    {
        let why = match &r.status {
            Status::Unverified(w) => w.text(),
            _ => r
                .evidence
                .as_ref()
                .map(|e| e.summary.clone())
                .unwrap_or_default(),
        };
        out.push(format!(
            "{} {}:{}",
            r.goal.id,
            r.status.label(),
            clean(&why, 60)
        ));
    }
    out.push(format!("接下來:{}", order_text(&report.next)));
    if let Some(a) = ai {
        out.push(format!(
            "AI 改排:{}({})",
            order_text(&a.ids),
            clean(&a.why, 60)
        ));
    }
    out.join("\n") + "\n"
}

fn order_text(ids: &[String]) -> String {
    if ids.is_empty() {
        "(全部實查通過)".to_owned()
    } else {
        ids.join(" → ")
    }
}

/// The report as JSON (the same facts as the text).
pub fn to_json(report: &Report) -> Value {
    json!({
        "pass": report.count("pass"),
        "fail": report.count("fail"),
        "unverified": report.count("unverified"),
        "inferred": report.count("inferred"),
        "unreadable_evidence": report.unreadable_evidence,
        "next": report.next,
        "rows": report.rows.iter().map(|r| json!({
            "id": r.goal.id,
            "goal_sha": goal_sha(&r.goal.text),
            "status": r.status.code(),
            "why": match &r.status { Status::Unverified(w) => Value::from(w.code()), _ => Value::Null },
            "evidence_at": r.evidence.as_ref().map(|e| e.at),
        })).collect::<Vec<_>>(),
    })
}

/// The events that count as a finished task (DECISION item 4). Anything else
/// (`aib now --clear`, an ordinary dm, a push, a step in the middle) is not
/// one, and the tool's own reports are never one.
pub const EVENTS: &[&str] = &[
    "session-start",
    "pr-merged",
    "release",
    "order-closed",
    "now-done",
];

/// One recorded evaluation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub kind: String,
    pub seat: String,
    pub pr: Option<u64>,
    pub at: u64,
}

/// What `record_event` did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Recorded {
    /// The report, or the goals error code.
    pub result: Result<Report, String>,
    /// Another evaluation for this seat was shown under 60 seconds ago:
    /// this one is recorded but its display merges into that one.
    pub merged_display: bool,
}

const MERGE_SECS: u64 = 60;

/// Evaluate one event and append it to `expect-evals.jsonl`. Every event is
/// recorded, also when the goals cannot be read (the line says why) and also
/// when the display is merged.
pub fn record_event(
    root: &Path,
    uid: &str,
    ev: &Event,
    snap: &Snapshot,
) -> Result<Recorded, String> {
    if !EVENTS.contains(&ev.kind.as_str()) {
        return Err(format!(
            "expect_not_a_completion: {:?} is not a finished-task event ({})",
            ev.kind,
            EVENTS.join(" / ")
        ));
    }
    let dir = project_dir(root, uid).map_err(|e| e.to_string())?;
    let log = dir.join("expect-evals.jsonl");
    let merged_display = read_tail(&log, EVALS_TAIL_BYTES)
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|v| v.get("seat").and_then(Value::as_str) == Some(ev.seat.as_str()))
        .filter(|v| v.get("shown") == Some(&Value::Bool(true)))
        .filter_map(|v| v.get("at").and_then(Value::as_u64))
        .any(|t| t <= ev.at && ev.at - t < MERGE_SECS);
    let result = load_goals(root, uid)
        .and_then(|g| Ok((g, load_evidence(root, uid)?)))
        .map(|(g, e)| compare(&g, &e, snap, ev.at))
        .map_err(|e| e.code().to_owned());
    let mut line = json!({
        "at": ev.at,
        "kind": ev.kind,
        "seat": ev.seat,
        "pr": ev.pr,
        "shown": !merged_display,
    });
    match &result {
        Ok(r) => {
            line["ok"] = Value::Bool(true);
            line["report"] = to_json(r);
        }
        Err(code) => {
            line["ok"] = Value::Bool(false);
            line["code"] = Value::from(code.as_str());
        }
    }
    if !dir.is_dir() {
        return Err(format!("expect_no_card: {} is not a folder", dir.display()));
    }
    // the log is written only where it is: never through a link
    if fs::symlink_metadata(&log).is_ok_and(|m| !m.is_file()) {
        return Err(format!("expect_evals_not_a_file: {}", log.display()));
    }
    let mut f = fs::OpenOptions::new()
        .create(true)
        .read(true)
        .append(true)
        .open(&log)
        .map_err(|e| format!("{}: {e}", log.display()))?;
    // an earlier write cut short left half a line: end it, so this line is
    // not glued to it
    let mut last = [0u8; 1];
    let half = f.metadata().is_ok_and(|m| m.len() > 0)
        && f.seek(SeekFrom::End(-1)).is_ok()
        && f.read_exact(&mut last).is_ok()
        && last[0] != b'\n';
    let text = if half {
        format!("\n{line}\n")
    } else {
        format!("{line}\n")
    };
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", log.display()))?;
    Ok(Recorded {
        result,
        merged_display,
    })
}
