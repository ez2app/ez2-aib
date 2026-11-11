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
//!   pass and never an older value.
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
use std::io::Write;
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

/// `<root>/projects/<uid>`, refusing ids that could leave that folder.
pub fn project_dir(root: &Path, uid: &str) -> Result<PathBuf, GoalsError> {
    let ok = !uid.is_empty()
        && uid.len() <= 64
        && uid != "."
        && uid != ".."
        && uid
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, ':' | '.' | '_' | '-'));
    if !ok {
        return Err(GoalsError::BadProject(uid.to_owned()));
    }
    Ok(root.join("projects").join(uid))
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

fn read_json(p: &Path) -> Result<Value, String> {
    let t = fs::read_to_string(p).map_err(|e| format!("{}: {e}", p.display()))?;
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
}

/// Evidence lines, and how many lines could not be read (shown, not hidden).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EvidenceLog {
    pub lines: Vec<Evidence>,
    pub unreadable: usize,
}

fn evidence_line(v: &Value) -> Option<Evidence> {
    let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_owned);
    let inferred = v.get("kind").and_then(Value::as_str) == Some("inferred");
    let head = s("head");
    if head
        .as_deref()
        .is_some_and(|h| h.len() != 40 || !h.chars().all(|c| c.is_ascii_hexdigit()))
    {
        return None;
    }
    Some(Evidence {
        goal: s("goal_id").filter(|g| !g.is_empty())?,
        at: v.get("at").and_then(Value::as_u64)?,
        seat: s("seat").unwrap_or_default(),
        pr: v.get("pr").and_then(Value::as_u64),
        head,
        check: s("check_id").unwrap_or_default(),
        // an inferred line has no rc; a check line must have one
        rc: match v.get("rc").and_then(Value::as_i64) {
            Some(rc) => rc,
            None if inferred => 0,
            None => return None,
        },
        summary: s("summary").unwrap_or_default(),
        valid_until: v.get("valid_until").and_then(Value::as_u64),
        inferred,
    })
}

/// Parse `evidence.jsonl` text.
pub fn parse_evidence(text: &str) -> EvidenceLog {
    let mut log = EvidenceLog::default();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        match serde_json::from_str::<Value>(line)
            .ok()
            .as_ref()
            .and_then(evidence_line)
        {
            Some(e) => log.lines.push(e),
            None => log.unreadable += 1,
        }
    }
    log
}

/// Read `<project>/evidence.jsonl`; a missing file is no evidence.
pub fn load_evidence(root: &Path, uid: &str) -> Result<EvidenceLog, GoalsError> {
    let p = project_dir(root, uid)?.join("evidence.jsonl");
    match fs::read_to_string(&p) {
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    /// 實查通過
    Pass,
    /// 實查不通過
    Fail,
    /// 未驗, with the reason
    Unverified(String),
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

fn status_of(e: Option<&Evidence>, snap: &Snapshot, now: u64) -> Status {
    let Some(e) = e else {
        return Status::Unverified("沒有證據".to_owned());
    };
    if e.valid_until.is_some_and(|t| t < now) {
        return Status::Unverified("證據過期".to_owned());
    }
    if let (Some(pr), Some(head)) = (e.pr, e.head.as_deref()) {
        match snap.pr_heads.get(&pr) {
            Some(Some(now_head)) if now_head == head => {}
            Some(Some(now_head)) => {
                return Status::Unverified(format!(
                    "換頭:證據在 {}、#{pr} 現在 {}",
                    &head[..12],
                    now_head.get(..12).unwrap_or(now_head)
                ))
            }
            _ => return Status::Unverified(format!("#{pr} 頭碼讀不到")),
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

/// The PRs the evidence names.
pub fn prs_of(evidence: &EvidenceLog) -> std::collections::BTreeSet<u64> {
    evidence.lines.iter().filter_map(|e| e.pr).collect()
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

/// The comparison. Pure: same input, same report.
pub fn compare(goals: &[Goal], evidence: &EvidenceLog, snap: &Snapshot, now: u64) -> Report {
    let rows: Vec<Row> = goals
        .iter()
        .map(|g| {
            // the latest line wins, even when it is worse than an older one
            let latest = evidence
                .lines
                .iter()
                .enumerate()
                .filter(|(_, e)| e.goal == g.id)
                .max_by_key(|(i, e)| (e.at, *i))
                .map(|(_, e)| e.clone());
            Row {
                status: status_of(latest.as_ref(), snap, now),
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
            (Status::Unverified(w), _) => w.clone(),
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
            Status::Unverified(w) => w.clone(),
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
            "status": r.status.code(),
            "why": match &r.status { Status::Unverified(w) => Value::from(w.as_str()), _ => Value::Null },
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
    let merged_display = fs::read_to_string(&log)
        .unwrap_or_default()
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
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
        .map_err(|e| format!("{}: {e}", log.display()))?;
    f.write_all(format!("{line}\n").as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", log.display()))?;
    Ok(Recorded {
        result,
        merged_display,
    })
}
