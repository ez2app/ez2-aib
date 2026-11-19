//! aib-idea: the suggestion ledger (S76-1, DEV-S76-idea-autoapprove-519d0-1114 r2).
//!
//! Andy 11-14 10:3x:「只要是優化、不會造成重大錯誤的就自動記錄、同意、可查詢，也要在每次列出
//! 來的建議之後，列出真人未審的內容，可以一併後審同意」.
//!
//! A seat adds each suggestion with the classes it touches. `none` (alone) ⇒ classification
//! `auto`: the seat need not wait for Andy's yes to this suggestion (tests, review, PR, receipt
//! and the merge queue still apply). Any red-line class ([`RED`]) ⇒ `needs_human`: it goes the
//! old way (Touch ID / an explicit yes) and this tool never approves it.
//!
//! This file is pure: it parses ledger lines, replays them in file order and renders the
//! lists. The file, the lock and the clock live in `main.rs`.
//!
//! The verdict sorts work between seats; it is **not a defence** (DEV §4): an AI on the same
//! account can edit the ledger or misreport touches, and `--by andy --quote` is a statement on
//! record, not proof. A non-author review checks touches against the real change.

pub mod brief;

use aib_ledger::envelope::{self, Aggregate};
use serde_json::{json, Map, Value};

/// The ledger's one stream (spec V1.1 §8.2, S97): every line, v 1 or v 2, is an event of it.
/// New lines are v 2 ([`aib_ledger::envelope`]); v 1 lines are read as they are.
pub const AGG: Aggregate<'static> = Aggregate {
    kind: "idea_ledger",
    id: "ideas",
};
/// The judgement table below; bump it when [`RED`] changes.
pub const POLICY_VERSION: i64 = 1;
/// One text field, in Unicode characters (DEV §5).
pub const MAX_FIELD_CHARS: usize = 500;
/// One ledger line, in bytes (DEV §5).
pub const MAX_LINE_BYTES: usize = 16 * 1024;
/// The footer lists at most this many approvable suggestions, oldest first (DEV §6).
pub const FOOTER_MAX: usize = 10;

/// Red-line classes (DEV §4): any one ⇒ needs_human. Written into the code on purpose:
/// changing it is a `rule` change and goes through a PR and a review.
pub const RED: [&str; 12] = [
    "money",
    "sign",
    "prod",
    "delete",
    "data",
    "external",
    "customer_text",
    "seat",
    "machine",
    "security",
    "rule",
    "release",
];

/// Only "when" is still asked (S76-when, Andy 11-15 08:5x:「只要有任何的 需人同意 關於時間的,就是
/// 立刻、或最快時間執行的語意,用來取代等待真人」). Alone (or with `none`) ⇒ auto with the answer
/// "now"; next to any red class the red class decides (needs_human): asking for a time never
/// lets a red line through.
pub const WHEN: &str = "when";

/// How much it matters (DEV-S76 §14 r2, Andy 11-14 11:19): S = the mainline stands or falls on
/// it, or it stops a big mistake; A = clearly saves Andy / the fleet time; B = a felt
/// improvement; C = a small one. Sorted S first.
pub const GRADES: [&str; 4] = ["S", "A", "B", "C"];
/// The one main direction (§14): the code value and the word people see.
pub const AXES: [(&str, &str); 4] = [
    ("stable", "穩定"),
    ("fast", "快速"),
    ("simple", "簡化"),
    ("unseen", "沒想到"),
];

pub fn grade_ok(g: &str) -> bool {
    GRADES.contains(&g)
}

pub fn axis_ok(a: &str) -> bool {
    AXES.iter().any(|(k, _)| *k == a)
}

fn grade_rank(g: &str) -> usize {
    GRADES.iter().position(|x| *x == g).unwrap_or(GRADES.len())
}

/// Whether this tool may approve it at all.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    Auto,
    NeedsHuman,
}

impl Class {
    pub fn code(self) -> &'static str {
        match self {
            Class::Auto => "auto",
            Class::NeedsHuman => "needs_human",
        }
    }
    fn parse(s: &str) -> Option<Class> {
        match s {
            "auto" => Some(Class::Auto),
            "needs_human" => Some(Class::NeedsHuman),
            _ => None,
        }
    }
}

/// What Andy said; each suggestion is decided once (no re-judging today).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HumanReview {
    Pending,
    Ok,
    No,
}

impl HumanReview {
    pub fn code(self) -> &'static str {
        match self {
            HumanReview::Pending => "pending",
            HumanReview::Ok => "ok",
            HumanReview::No => "no",
        }
    }
}

/// `--touches`: `none` alone, `when`, or red-line classes. Err = usage (rc 64): empty, an
/// unknown word (a typo must never become an automatic yes), or `none` next to a red class.
pub fn parse_touches(arg: &str) -> Result<Vec<String>, String> {
    let mut out: Vec<String> = Vec::new();
    for w in arg.split(',').map(str::trim) {
        if w.is_empty() {
            return Err("touches_empty_item".into());
        }
        if w != "none" && w != WHEN && !RED.contains(&w) {
            return Err(format!("touches_unknown: {w}"));
        }
        if !out.iter().any(|x| x == w) {
            out.push(w.to_string());
        }
    }
    if out.iter().any(|w| w == "none") && out.iter().any(|w| RED.contains(&w.as_str())) {
        return Err("touches_none_with_others".into());
    }
    Ok(out)
}

/// The classification that follows from valid touches: only `none` / `when` ⇒ auto; any red
/// class ⇒ needs_human (a red class wins over `when`).
pub fn classify(touches: &[String]) -> Class {
    if !touches.is_empty() && touches.iter().all(|t| t == "none" || t == WHEN) {
        Class::Auto
    } else {
        Class::NeedsHuman
    }
}

/// A seat code: 5 lower-case hex.
pub fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// A UUID as this ledger writes it: 8-4-4-4-12 lower-case hex.
pub fn uuid_ok(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    parts.iter().map(|p| p.len()).eq([8, 4, 4, 4, 12])
        && parts
            .iter()
            .all(|p| p.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
}

/// A UUID (version 4 shape) from 128 bits.
pub fn uuid_from(bits: u128) -> String {
    let b = (bits & !(0xf << 76) | (0x4 << 76)) & !(0x3 << 62) | (0x2 << 62);
    let h = format!("{b:032x}");
    format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    )
}

/// `i_` + the first 8 hex of the id: what people see.
pub fn short(id: &str) -> String {
    format!("i_{}", &id[..id.len().min(8)])
}

/// A text field: non-empty after trimming, at most [`MAX_FIELD_CHARS`].
pub fn field_ok(s: &str) -> bool {
    !s.trim().is_empty() && s.chars().count() <= MAX_FIELD_CHARS
}

/// Control characters removed, for printing a field on one line.
pub fn clean(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Review {
    pub ok: bool,
    pub by: String,
    pub quote: String,
    pub why: Option<String>,
    pub at_ms: i64,
    pub seat: String,
}

/// What a seat decided for an idea (519d0 11-19 09:20, RULES §31: only a red line is left to
/// Andy): `done` (already done), `decided` (the coordinator or a council decided it), `dropped`,
/// `council` (sent to a council). It is **not** Andy's review: [`Idea::human_review`] stays
/// pending, so Andy can still say ok / no later, and his word wins (a settled one can still be
/// reviewed; a reviewed one cannot be settled). The footer stops asking Andy for a settled
/// needs_human one.
pub const OUTCOMES: [(&str, &str); 6] = [
    ("done", "已做"),
    ("decided", "已定"),
    ("dropped", "作廢"),
    ("council", "遇事不決"),
    // 遇事不決定稿 DECISION-council-ideas-2 §1: the proposing seat takes it back; it was
    // folded into another idea (`superseded_by`)
    ("withdrawn", "撤回"),
    ("superseded", "已併入"),
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Settle {
    pub outcome: String,
    pub why: String,
    pub at_ms: i64,
    pub seat: String,
    /// `superseded` only: the idea it was folded into
    pub superseded_by: Option<String>,
}

impl Settle {
    /// `已做` …
    pub fn word(&self) -> &'static str {
        OUTCOMES
            .iter()
            .find(|(k, _)| *k == self.outcome)
            .map_or("?", |(_, w)| w)
    }
}

/// A `when` event (S76-when): why it cannot be done now and the earliest moment it can.
/// Both empty ⇒ now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct When {
    pub blocked_by: Option<String>,
    pub earliest: Option<String>,
    pub at_ms: i64,
    pub seat: String,
}

/// What `aib-idea when` prints: 立刻, or 最快 and why not now. Nobody is waited for.
pub fn when_answer(blocked_by: Option<&str>, earliest: Option<&str>) -> String {
    match (blocked_by, earliest) {
        (Some(b), Some(e)) => format!("最快:{}(因為 {})", clean(e), clean(b)),
        _ => "立刻".into(),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Idea {
    pub id: String,
    pub at_ms: i64,
    pub seat: String,
    pub title: String,
    pub now: String,
    pub suggest: String,
    pub benefit: String,
    pub cost: String,
    pub touches: Vec<String>,
    pub class: Class,
    /// S / A / B / C
    pub grade: String,
    /// stable / fast / simple / unseen
    pub axis: String,
    pub node: Option<String>,
    pub source: Option<String>,
    pub review: Option<Review>,
    /// the last `when` event, if any
    pub when: Option<When>,
    /// the last `settle` event, if any
    pub settle: Option<Settle>,
}

impl Idea {
    /// Only the time was asked, and no red class: the answer is now (S76-when).
    pub fn when_now(&self) -> bool {
        self.class == Class::Auto && self.touches.iter().any(|t| t == WHEN)
    }
    /// `時間:立刻` / `時間:最快 …` for a [`Idea::when_now`] one.
    pub fn when_text(&self) -> Option<String> {
        self.when_now().then(|| {
            let w = self.when.as_ref();
            let ans = when_answer(
                w.and_then(|w| w.blocked_by.as_deref()),
                w.and_then(|w| w.earliest.as_deref()),
            );
            format!("時間:{ans}")
        })
    }
    pub fn human_review(&self) -> HumanReview {
        match &self.review {
            None => HumanReview::Pending,
            Some(r) if r.ok => HumanReview::Ok,
            Some(_) => HumanReview::No,
        }
    }
    /// `A | 穩定 | 自動同意優化`: grade, direction, who agrees (§14 r3, Andy 11:39).
    pub fn label(&self) -> String {
        let axis = AXES
            .iter()
            .find(|(k, _)| *k == self.axis)
            .map_or(self.axis.as_str(), |(_, w)| w);
        let who = match self.class {
            Class::Auto => "自動同意優化",
            Class::NeedsHuman => "需人同意",
        };
        format!("{} | {axis} | {who}", self.grade)
    }
    /// One word for lists: auto / needs_human while pending, else ok / no.
    pub fn status(&self) -> &'static str {
        match self.human_review() {
            HumanReview::Pending => self.class.code(),
            h => h.code(),
        }
    }
    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id, "short": short(&self.id), "at_ms": self.at_ms, "seat": self.seat,
            "title": self.title, "now": self.now, "suggest": self.suggest,
            "benefit": self.benefit, "cost": self.cost, "touches": self.touches,
            "classification": self.class.code(), "human_review": self.human_review().code(),
            "status": self.status(), "grade": self.grade, "axis": self.axis,
            "label": self.label(), "node": self.node, "source": self.source,
            "when": self.when_text(),
            "settle": self.settle.as_ref().map(|t| json!({
                "outcome": t.outcome, "word": t.word(), "why": t.why, "at_ms": t.at_ms,
                "recorded_by_seat": t.seat, "superseded_by": t.superseded_by,
            })),
            "review": self.review.as_ref().map(|r| json!({
                "verdict": if r.ok { "ok" } else { "no" }, "by": r.by, "quote": r.quote,
                "why": r.why, "at_ms": r.at_ms, "recorded_by_seat": r.seat,
            })),
        })
    }
}

/// One v 2 line: the envelope, then the event's own fields.
fn line(ev: &str, event_id: &str, at_ms: i64, sequence: u64, fields: Value) -> String {
    let mut o = envelope::write(ev, event_id, at_ms, AGG, sequence);
    if let Value::Object(f) = fields {
        o.extend(f);
    }
    Value::Object(o).to_string()
}

/// The `add` event line (v 2). `sequence` = [`Ledger::next_sequence`] of the ledger it is
/// appended to, read under the lock. A [`Idea::when_now`] one carries `"when": "now"`.
pub fn add_line(event_id: &str, sequence: u64, i: &Idea) -> String {
    let mut f = json!({
        "idea_id": i.id, "recorded_by_seat": i.seat, "policy_version": POLICY_VERSION,
        "title": i.title, "now": i.now, "suggest": i.suggest, "benefit": i.benefit,
        "cost": i.cost, "touches": i.touches, "classification": i.class.code(),
        "grade": i.grade, "axis": i.axis, "node": i.node, "source": i.source,
    });
    if i.when_now() {
        f["when"] = json!("now");
    }
    line("add", event_id, i.at_ms, sequence, f)
}

/// The `when` event line (v 2): the answer for one idea (blocked_by and earliest together or
/// neither).
pub fn when_line(event_id: &str, sequence: u64, idea_id: &str, w: &When) -> String {
    line(
        "when",
        event_id,
        w.at_ms,
        sequence,
        json!({
            "idea_id": idea_id, "recorded_by_seat": w.seat, "blocked_by": w.blocked_by,
            "earliest": w.earliest,
        }),
    )
}

/// Whether a `when` may be written for this idea: only for one that asks only the time.
pub fn check_when(i: &Idea) -> Result<(), String> {
    if i.when_now() {
        Ok(())
    } else if i.class == Class::NeedsHuman {
        Err(format!(
            "{} touches a red class: the red line decides, not the time",
            short(&i.id)
        ))
    } else {
        Err(format!("{} does not touch when", short(&i.id)))
    }
}

/// The `settle` event line (v 2): one outcome for every id, all or nothing.
pub fn settle_line(event_id: &str, sequence: u64, ids: &[String], t: &Settle) -> String {
    line(
        "settle",
        event_id,
        t.at_ms,
        sequence,
        json!({"recorded_by_seat": t.seat, "outcome": t.outcome, "why": t.why, "ids": ids,
            "superseded_by": t.superseded_by}),
    )
}

/// Whether these ids may be settled: each known, once, and not yet reviewed by Andy; only the
/// proposing seat withdraws its own; a superseded one names another known idea, not one of
/// these.
pub fn check_settle(l: &Ledger, ids: &[String], t: &Settle) -> Result<(), String> {
    match (t.outcome.as_str(), t.superseded_by.as_deref()) {
        ("superseded", Some(by)) => {
            if ids.iter().any(|i| i == by) || l.get(by).is_none() {
                return Err(format!("superseded_by {} is not another idea", short(by)));
            }
        }
        ("superseded", None) => return Err("superseded needs superseded_by".into()),
        (_, Some(_)) => return Err("superseded_by goes with superseded only".into()),
        _ => {}
    }
    for (n, id) in ids.iter().enumerate() {
        if ids[..n].contains(id) {
            return Err(format!("{} twice in the batch", short(id)));
        }
        let Some(i) = l.get(id) else {
            return Err(format!("no idea {}", short(id)));
        };
        if i.review.is_some() {
            return Err(format!(
                "{} already {} by Andy: his word stands",
                short(id),
                i.human_review().code()
            ));
        }
        if t.outcome == "withdrawn" && t.seat != i.seat {
            return Err(format!("{} is {}'s to withdraw", short(id), i.seat));
        }
    }
    Ok(())
}

/// The evidence a 已做 claim gives (contract §5 c, DECISION-council-ideas-2 §1: done needs
/// evidence, else it is 「自稱已做」): the first word of `text` that is a commit (7–40 hex)
/// `exists` knows, or an absolute or `~/` path `exists` finds. `exists` does the looking (git,
/// the file system), so this stays pure; both aibd and aib-idea call it.
pub fn done_evidence(text: &str, exists: impl Fn(&str) -> bool) -> Option<String> {
    text.split(|c: char| c.is_whitespace() || "()(),,、;;「」".contains(c))
        .map(|w| w.trim_end_matches(['.', '。', ':', ':']))
        .filter(|w| {
            let hex = (7..=40).contains(&w.len()) && w.bytes().all(|b| b.is_ascii_hexdigit());
            hex || w.starts_with('/') || w.starts_with("~/")
        })
        .find(|w| exists(w))
        .map(str::to_owned)
}

/// The `review_batch` event line (v 2): one review for every id, all or nothing.
pub fn review_line(event_id: &str, sequence: u64, ids: &[String], r: &Review) -> String {
    line(
        "review_batch",
        event_id,
        r.at_ms,
        sequence,
        json!({
            "recorded_by_seat": r.seat, "by": r.by, "quote": r.quote,
            "verdict": if r.ok { "ok" } else { "no" }, "why": r.why, "ids": ids,
        }),
    )
}

/// Everything the ledger says, replayed in file order (time is shown, never used to order).
#[derive(Debug, Default)]
pub struct Ledger {
    pub ideas: Vec<Idea>,
    /// `line <n>: <why>`; any entry makes the ledger incomplete (rc 2, every write refused)
    pub errors: Vec<String>,
    pub event_ids: Vec<String>,
}

impl Ledger {
    pub fn complete(&self) -> bool {
        self.errors.is_empty()
    }
    /// The sequence of the next line written: the events read so far, plus one (only asked
    /// of a complete ledger, so every line was read).
    pub fn next_sequence(&self) -> u64 {
        u64::try_from(self.event_ids.len()).map_or(u64::MAX, |n| n.saturating_add(1))
    }
    pub fn get(&self, id: &str) -> Option<&Idea> {
        self.ideas.iter().find(|i| i.id == id)
    }
    pub fn by_source(&self, source: &str) -> Option<&Idea> {
        self.ideas
            .iter()
            .find(|i| i.source.as_deref() == Some(source))
    }
    /// The ideas whose id starts with this: `i_3fa9c201`, a full id, or any prefix of its hex
    /// at least 8 long (dashes ignored).
    pub fn resolve(&self, word: &str) -> Vec<&Idea> {
        let want: String = word
            .strip_prefix("i_")
            .unwrap_or(word)
            .chars()
            .filter(|c| *c != '-')
            .collect();
        if want.len() < 8 || !want.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
            return Vec::new();
        }
        self.ideas
            .iter()
            .filter(|i| i.id.replace('-', "").starts_with(&want))
            .collect()
    }
}

fn text(o: &Map<String, Value>, k: &str) -> Result<String, String> {
    match o.get(k).and_then(Value::as_str) {
        Some(v) if field_ok(v) => Ok(v.to_string()),
        _ => Err(format!("bad {k}")),
    }
}

fn opt_text(o: &Map<String, Value>, k: &str) -> Result<Option<String>, String> {
    match o.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if field_ok(s) => Ok(Some(s.clone())),
        Some(_) => Err(format!("bad {k}")),
    }
}

fn uuid_field(o: &Map<String, Value>, k: &str) -> Result<String, String> {
    match o.get(k).and_then(Value::as_str) {
        Some(v) if uuid_ok(v) => Ok(v.to_string()),
        _ => Err(format!("bad {k}")),
    }
}

fn seat_field(o: &Map<String, Value>) -> Result<String, String> {
    match o.get("recorded_by_seat").and_then(Value::as_str) {
        Some(s) if seat_ok(s) => Ok(s.to_string()),
        _ => Err("bad recorded_by_seat".into()),
    }
}

fn parse_add(l: &Ledger, o: &Map<String, Value>, at_ms: i64) -> Result<Idea, String> {
    let touches = match o.get("touches") {
        Some(Value::Array(a)) if !a.is_empty() => {
            let w: Option<Vec<&str>> = a.iter().map(Value::as_str).collect();
            parse_touches(&w.ok_or("bad touches")?.join(","))?
        }
        _ => return Err("bad touches".into()),
    };
    // the classification follows from touches; a line saying auto next to a red class is
    // wrong, never a way in
    let class = classify(&touches);
    if o.get("classification")
        .and_then(Value::as_str)
        .and_then(Class::parse)
        != Some(class)
    {
        return Err("classification does not follow from touches".into());
    }
    if o.get("policy_version").and_then(Value::as_i64) != Some(POLICY_VERSION) {
        return Err("unknown policy_version".into());
    }
    let id = uuid_field(o, "idea_id")?;
    if l.get(&id).is_some() {
        return Err(format!("idea {id} added twice"));
    }
    let source = opt_text(o, "source")?;
    if let Some(s) = &source {
        if l.by_source(s).is_some() {
            return Err(format!("source {s} added twice"));
        }
    }
    Ok(Idea {
        id,
        at_ms,
        seat: seat_field(o)?,
        title: text(o, "title")?,
        now: text(o, "now")?,
        suggest: text(o, "suggest")?,
        benefit: text(o, "benefit")?,
        cost: text(o, "cost")?,
        touches,
        class,
        grade: match o.get("grade").and_then(Value::as_str) {
            Some(g) if grade_ok(g) => g.to_string(),
            _ => return Err("bad grade".into()),
        },
        axis: match o.get("axis").and_then(Value::as_str) {
            Some(a) if axis_ok(a) => a.to_string(),
            _ => return Err("bad axis".into()),
        },
        node: opt_text(o, "node")?,
        source,
        review: None,
        when: None,
        settle: None,
    })
    .and_then(|i| {
        let says = o.get("when").and_then(Value::as_str);
        match (i.when_now(), says) {
            (true, Some("now")) | (false, None) => Ok(i),
            _ => Err("when does not follow from touches".into()),
        }
    })
}

fn parse_when(l: &Ledger, o: &Map<String, Value>, at_ms: i64) -> Result<(String, When), String> {
    let id = uuid_field(o, "idea_id")?;
    let i = l
        .get(&id)
        .ok_or_else(|| format!("when for unknown idea {id}"))?;
    check_when(i)?;
    let w = When {
        blocked_by: opt_text(o, "blocked_by")?,
        earliest: opt_text(o, "earliest")?,
        at_ms,
        seat: seat_field(o)?,
    };
    if w.blocked_by.is_some() != w.earliest.is_some() {
        return Err("blocked_by and earliest go together".into());
    }
    Ok((id, w))
}

fn parse_settle(
    l: &Ledger,
    o: &Map<String, Value>,
    at_ms: i64,
) -> Result<(Vec<String>, Settle), String> {
    let outcome = text(o, "outcome")?;
    if !OUTCOMES.iter().any(|(k, _)| *k == outcome) {
        return Err("bad outcome".into());
    }
    let superseded_by = match o.get("superseded_by") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if uuid_ok(s) => Some(s.clone()),
        _ => return Err("bad superseded_by".into()),
    };
    let t = Settle {
        outcome,
        why: text(o, "why")?,
        at_ms,
        seat: seat_field(o)?,
        superseded_by,
    };
    let ids: Vec<String> = match o.get("ids") {
        Some(Value::Array(a)) if !a.is_empty() => a
            .iter()
            .map(|v| v.as_str().filter(|s| uuid_ok(s)).map(str::to_string))
            .collect::<Option<Vec<_>>>()
            .ok_or("bad ids")?,
        _ => return Err("bad ids".into()),
    };
    check_settle(l, &ids, &t)?;
    Ok((ids, t))
}

fn parse_batch(
    l: &Ledger,
    o: &Map<String, Value>,
    at_ms: i64,
) -> Result<(Vec<String>, Review), String> {
    let ok = match o.get("verdict").and_then(Value::as_str) {
        Some("ok") => true,
        Some("no") => false,
        _ => return Err("bad verdict".into()),
    };
    let r = Review {
        ok,
        by: match o.get("by").and_then(Value::as_str) {
            Some("andy") => "andy".into(),
            _ => return Err("bad by".into()),
        },
        quote: text(o, "quote")?,
        why: opt_text(o, "why")?,
        at_ms,
        seat: seat_field(o)?,
    };
    if !ok && r.why.is_none() {
        return Err("a no without why".into());
    }
    let ids: Vec<String> = match o.get("ids") {
        Some(Value::Array(a)) if !a.is_empty() => a
            .iter()
            .map(|v| v.as_str().filter(|s| uuid_ok(s)).map(str::to_string))
            .collect::<Option<Vec<_>>>()
            .ok_or("bad ids")?,
        _ => return Err("bad ids".into()),
    };
    // the whole batch is checked before any of it counts, as when it was written
    check_batch(l, &ids, ok)?;
    Ok((ids, r))
}

/// Whether a batch may be written: every id known and pending, no id twice, an ok only for
/// auto ones. Err = the rule broken (rc 3, nothing written).
pub fn check_batch(l: &Ledger, ids: &[String], ok: bool) -> Result<(), String> {
    for (n, id) in ids.iter().enumerate() {
        if ids[..n].contains(id) {
            return Err(format!("{} twice in the batch", short(id)));
        }
        let Some(i) = l.get(id) else {
            return Err(format!("no idea {}", short(id)));
        };
        if i.human_review() != HumanReview::Pending {
            return Err(format!("{} already {}", short(id), i.human_review().code()));
        }
        if ok && i.class != Class::Auto {
            return Err(format!(
                "{} is needs_human: approve it the red-line way, not with this tool",
                short(id)
            ));
        }
    }
    Ok(())
}

impl Ledger {
    fn apply(&mut self, o: &Map<String, Value>) -> Result<(), String> {
        // v 1 or v 2: the time and the place in the stream (S97); the rest of the line is the
        // same in both
        let head = envelope::read(o, AGG, self.next_sequence())?;
        let event_id = uuid_field(o, "event_id")?;
        if self.event_ids.contains(&event_id) {
            return Err(format!("event {event_id} twice"));
        }
        match head.ev.as_str() {
            "add" => {
                let i = parse_add(self, o, head.at_ms)?;
                self.ideas.push(i);
            }
            "review_batch" => {
                let (ids, r) = parse_batch(self, o, head.at_ms)?;
                for i in self.ideas.iter_mut().filter(|i| ids.contains(&i.id)) {
                    i.review = Some(r.clone());
                }
            }
            "settle" => {
                let (ids, t) = parse_settle(self, o, head.at_ms)?;
                for i in self.ideas.iter_mut().filter(|i| ids.contains(&i.id)) {
                    i.settle = Some(t.clone());
                }
            }
            "when" => {
                let (id, w) = parse_when(self, o, head.at_ms)?;
                if let Some(i) = self.ideas.iter_mut().find(|i| i.id == id) {
                    i.when = Some(w);
                }
            }
            _ => return Err("unknown ev".into()),
        }
        self.event_ids.push(event_id);
        Ok(())
    }
}

/// Replay the ledger text in file order. A bad line, an unknown version, a broken rule or a
/// last line without its newline makes the ledger incomplete; the lines that could be read
/// still show. Blank lines are skipped.
pub fn replay(text: &str) -> Ledger {
    let mut l = Ledger::default();
    let errors = aib_ledger::replay(text, MAX_LINE_BYTES, |o| l.apply(o));
    l.errors.extend(errors);
    l
}

/// `HH:MM` (or `MM-DD HH:MM` when not today) in the cabin's time zone.
pub fn clock(at_ms: i64, now_ms: i64, tz_offset_min: i32) -> String {
    let off = i64::from(tz_offset_min) * 60;
    let lt = at_ms.div_euclid(1000).saturating_add(off);
    let ln = now_ms.div_euclid(1000).saturating_add(off);
    let (day, sec) = (lt.div_euclid(86_400), lt.rem_euclid(86_400));
    let hm = format!("{:02}:{:02}", sec / 3600, sec % 3600 / 60);
    if day == ln.div_euclid(86_400) {
        return hm;
    }
    let (m, d) = month_day(day);
    format!("{m:02}-{d:02} {hm}")
}

/// Days since 1970-01-01 to (month, day), proleptic Gregorian (H. Hinnant's civil_from_days).
fn month_day(z: i64) -> (i64, i64) {
    let doe = (z + 719_468).rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (m, d)
}

fn pending(i: &&Idea) -> bool {
    i.human_review() == HumanReview::Pending
}

/// The approvable ones the footer lists: the seat's pending auto ones, S → A → B → C, the same
/// grade in the order added, at most [`FOOTER_MAX`]. Empty when the ledger is incomplete.
pub fn footer_auto<'a>(l: &'a Ledger, seat: &str) -> Vec<&'a Idea> {
    if !l.complete() {
        return Vec::new();
    }
    let mut v: Vec<&Idea> = l
        .ideas
        .iter()
        .filter(pending)
        .filter(|i| i.seat == seat && i.class == Class::Auto)
        .collect();
    by_grade(&mut v);
    v.truncate(FOOTER_MAX);
    v
}

/// S → A → B → C; a stable sort, so the same grade stays in the order added.
pub fn by_grade(v: &mut [&Idea]) {
    v.sort_by_key(|i| grade_rank(&i.grade));
}

/// The block after a reply's suggestions (DEV §6). Incomplete ledger ⇒ no approvable list.
pub fn footer(l: &Ledger, seat: &str, now_ms: i64, tz_offset_min: i32) -> String {
    if !l.complete() {
        return "真人未審:帳本不完整，先修(aib-idea list --all 看哪一行)。這一輪沒有可核准清單。\n"
            .into();
    }
    let auto = footer_auto(l, seat);
    let mut human: Vec<&Idea> = l
        .ideas
        .iter()
        .filter(pending)
        .filter(|i| i.seat == seat && i.class == Class::NeedsHuman)
        // a seat settled it (519d0 11-19): no longer asked of Andy, still his to review later
        .filter(|i| i.settle.is_none())
        .collect();
    by_grade(&mut human);
    let rest = l.ideas.iter().filter(pending).count() - auto.len() - human.len();
    let more = if rest > 0 {
        format!("(全艦還有 {rest} 條未審:aib-idea list --unreviewed --all)\n")
    } else {
        String::new()
    };
    if auto.is_empty() && human.is_empty() {
        return format!("真人未審:0 條\n{more}");
    }
    let line = |i: &Idea, tag: bool| {
        let node = i
            .node
            .as_deref()
            .map(|n| format!("{} ", clean(n)))
            .unwrap_or_default();
        let tag = if tag {
            format!(" [{}]", i.touches.join(","))
        } else {
            i.when_text().map(|t| format!(" {t}")).unwrap_or_default()
        };
        format!(
            "- {} | {} {node}{}{tag}({})\n",
            i.label(),
            short(&i.id),
            clean(&i.title),
            clock(i.at_ms, now_ms, tz_offset_min)
        )
    };
    let mut s = format!("真人未審:本次列出 {} 條可後審核准", auto.len());
    if !human.is_empty() {
        s.push_str(&format!(";另有 {} 條需個別授權", human.len()));
    }
    s.push_str(&format!("。回「同意」只核准上列 {} 條。\n", auto.len()));
    for i in auto.iter().copied() {
        s.push_str(&line(i, false));
    }
    if !human.is_empty() {
        s.push_str("需個別授權(不在「同意」範圍):\n");
        for i in human.iter().copied() {
            s.push_str(&line(i, true));
        }
    }
    s.push_str(&more);
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_794_623_580_000; // 2026-11-14 10:33 Taipei, ms

    fn uid(n: u128) -> String {
        uuid_from(n << 96 | n)
    }

    /// The v 1 `add` line every line before S97 is written in (236 of them in the ledger on
    /// 11-14), frozen here: the reader must keep taking it as it is.
    fn v1_add_line(event_id: &str, i: &Idea) -> String {
        json!({
            "v": 1, "ev": "add", "event_id": event_id, "idea_id": i.id, "at_ms": i.at_ms,
            "recorded_by_seat": i.seat, "policy_version": POLICY_VERSION, "title": i.title,
            "now": i.now, "suggest": i.suggest, "benefit": i.benefit, "cost": i.cost,
            "touches": i.touches, "classification": i.class.code(), "grade": i.grade, "axis": i.axis, "node": i.node,
            "source": i.source,
        })
        .to_string()
    }

    /// The v 1 `review_batch` line, frozen as above.
    fn v1_review_line(event_id: &str, ids: &[String], r: &Review) -> String {
        json!({
            "v": 1, "ev": "review_batch", "event_id": event_id, "at_ms": r.at_ms,
            "recorded_by_seat": r.seat, "by": r.by, "quote": r.quote,
            "verdict": if r.ok { "ok" } else { "no" }, "why": r.why, "ids": ids,
        })
        .to_string()
    }

    /// S97 (Andy 11-14 v7): the ledger as it is today, v 1 lines only, in the shape the real
    /// file has (keys sorted as serde_json writes them, `node` null, a manual source), reads
    /// whole: complete, every idea and every review there, times unchanged.
    #[test]
    fn a_v1_ledger_as_it_is_today_reads_whole() {
        let text = concat!(
            r#"{"at_ms":1794644140148,"axis":"simple","benefit":"見手記帳該列","classification":"auto","cost":"見手記帳該列","ev":"add","event_id":"65b8730f-aa15-406d-b62c-de09b45dd8b2","grade":"C","idea_id":"a240cab1-53c8-49c6-b59e-8d73af37c548","node":null,"now":"見手記帳第 m01 列","policy_version":1,"recorded_by_seat":"519d0","source":"manual:m01","suggest":"排進合併隊","title":"[m01 10:31] 排進合併隊","touches":["none"],"v":1}"#,
            "\n",
            r#"{"at_ms":1794644140156,"by":"andy","ev":"review_batch","event_id":"824acc54-d21f-4e4d-b312-b120e0b41199","ids":["a240cab1-53c8-49c6-b59e-8d73af37c548"],"quote":"同意","recorded_by_seat":"519d0","verdict":"ok","v":1,"why":null}"#,
            "\n",
            r#"{"at_ms":1794645000000,"axis":"stable","benefit":"省時間","classification":"needs_human","cost":"回復:revert","ev":"add","event_id":"0d1e2f30-4152-4637-8899-aabbccddeeff","grade":"A","idea_id":"11111111-2222-4333-8444-555555555555","node":"S76","now":"現況","policy_version":1,"recorded_by_seat":"043dd","source":null,"suggest":"做法","title":"要人同意的一條","touches":["prod","data"],"v":1}"#,
            "\n",
        );
        let l = replay(text);
        assert!(l.complete(), "reason: v1_ledger_incomplete {:?}", l.errors);
        assert_eq!(l.ideas.len(), 2, "reason: v1_ideas_lost");
        assert_eq!(
            l.ideas[0].human_review(),
            HumanReview::Ok,
            "reason: v1_review_lost"
        );
        assert_eq!(
            l.ideas[0].at_ms, 1_794_644_140_148,
            "reason: v1_time_changed"
        );
        assert_eq!(l.ideas[1].class, Class::NeedsHuman);
        assert_eq!(l.next_sequence(), 4, "reason: v1_lines_not_counted");
    }

    /// S97: new lines are v 2 (occurred_at, the aggregate, the next sequence, no at_ms) and
    /// follow the v 1 lines in the same file; the whole file reads complete.
    #[test]
    fn new_lines_are_v2_after_the_v1_lines_and_the_ledger_stays_whole() {
        let a = idea(1, "043dd", "none");
        let b = idea(2, "043dd", "none");
        let mut lines = vec![v1_add_line(&uid(11), &a)];
        let l = ledger(&lines);
        let new = add_line(&uid(12), l.next_sequence(), &b);
        let o: Value = serde_json::from_str(&new).expect("json");
        assert_eq!(o["v"], 2);
        assert_eq!(o["sequence"], 2);
        assert_eq!(o["aggregate_type"], "idea_ledger");
        assert_eq!(o["aggregate_id"], "ideas");
        assert_eq!(o["occurred_at"], envelope::rfc3339_ms(b.at_ms));
        assert!(o.get("at_ms").is_none(), "reason: v2_two_clocks");
        lines.push(new);
        let l = ledger(&lines);
        let ids: Vec<String> = vec![a.id.clone(), b.id.clone()];
        lines.push(review_line(&uid(13), l.next_sequence(), &ids, &rv(true)));
        let l = ledger(&lines);
        assert!(
            l.complete(),
            "reason: mixed_ledger_incomplete {:?}",
            l.errors
        );
        assert_eq!(l.ideas[1].at_ms, b.at_ms, "reason: v2_time_lost");
        assert!(l.ideas.iter().all(|i| i.human_review() == HumanReview::Ok));
        // a line lost before a v 2 line: that line's number is no longer its place, and only
        // the number says so (the review alone would still be a legal first decision)
        let lost = vec![lines[0].clone(), lines[2].clone()];
        let l = ledger(&lost);
        assert!(!l.complete(), "reason: v2_out_of_place_accepted");
        assert!(
            l.errors.iter().any(|e| e.contains("sequence is not 2")),
            "{:?}",
            l.errors
        );
    }

    fn idea(n: u128, seat: &str, touches: &str) -> Idea {
        let touches = parse_touches(touches).expect("touches");
        Idea {
            id: uid(n),
            at_ms: T0 + n as i64 * 60_000,
            seat: seat.into(),
            title: format!("建議 {n}"),
            now: "現況".into(),
            suggest: "做法".into(),
            benefit: "好處".into(),
            cost: "代價;回復:revert".into(),
            class: classify(&touches),
            touches,
            grade: "A".into(),
            axis: "stable".into(),
            node: Some("S72".into()),
            source: None,
            review: None,
            when: None,
            settle: None,
        }
    }

    fn add(i: &Idea) -> String {
        v1_add_line(&uid(1_000_000 + i.at_ms as u128), i)
    }

    /// A v 2 `add` line at place `seq` in the ledger: a when one is written only from S97 on.
    fn add2(seq: u64, i: &Idea) -> String {
        add_line(&uid(1_000_000 + i.at_ms as u128), seq, i)
    }

    fn rv(ok: bool) -> Review {
        Review {
            ok,
            by: "andy".into(),
            quote: if ok { "同意".into() } else { "不要".into() },
            why: (!ok).then(|| "先不做".to_string()),
            at_ms: T0 + 3_600_000,
            seat: "043dd".into(),
        }
    }

    fn batch(n: u128, ids: &[&Idea], ok: bool) -> String {
        let ids: Vec<String> = ids.iter().map(|i| i.id.clone()).collect();
        v1_review_line(&uid(5000 + n), &ids, &rv(ok))
    }

    fn ledger(lines: &[String]) -> Ledger {
        let mut t = lines.join("\n");
        t.push('\n');
        replay(&t)
    }

    #[test]
    fn each_red_class_needs_a_human() {
        // the table of DEV-S76 §4 written out here, so a class dropped from RED turns this red
        let table: Vec<&str> =
            "money sign prod delete data external customer_text seat machine security rule release"
                .split(' ')
                .collect();
        assert_eq!(RED.len(), table.len(), "reason: red_table_size");
        for c in table {
            let t = parse_touches(c).expect("parse");
            assert_eq!(
                classify(&t),
                Class::NeedsHuman,
                "reason: red_class_auto {c}"
            );
        }
        assert_eq!(classify(&parse_touches("none").expect("none")), Class::Auto);
    }

    #[test]
    fn none_alone_and_known_classes_only() {
        for bad in [
            "",
            "none,prod",
            "prod,none",
            "code",
            "mony",
            "prod,,data",
            " ",
        ] {
            assert!(
                parse_touches(bad).is_err(),
                "reason: touches_accepted {bad:?}"
            );
        }
        assert_eq!(
            parse_touches(" prod , data,prod"),
            Ok(vec!["prod".to_string(), "data".to_string()]),
            "reason: touches_not_cleaned"
        );
    }

    #[test]
    fn replay_goes_by_file_order_not_time() {
        let a = idea(1, "043dd", "none");
        let b = idea(2, "043dd", "none");
        // b's review was stamped earlier than a's add (a clock behind): file order still wins
        let mut early = rv(true);
        early.at_ms = T0 - 1;
        let line = v1_review_line(&uid(77), std::slice::from_ref(&b.id), &early);
        let l = ledger(&[add(&a), add(&b), line]);
        assert!(l.complete(), "reason: clean_ledger_errors {:?}", l.errors);
        assert_eq!(
            l.ideas[1].human_review(),
            HumanReview::Ok,
            "reason: replay_by_time"
        );
        // a review written before its add (in file order) is a broken rule
        let line = v1_review_line(&uid(78), std::slice::from_ref(&b.id), &rv(true));
        let l = ledger(&[add(&a), line, add(&b)]);
        assert!(!l.complete(), "reason: review_before_add_accepted");
        assert_eq!(l.ideas[1].human_review(), HumanReview::Pending);
        // decided once: a second batch on the same idea breaks the rules
        let l = ledger(&[add(&a), batch(1, &[&a], false), batch(2, &[&a], true)]);
        assert!(!l.complete(), "reason: second_decision_accepted");
        assert_eq!(
            l.ideas[0].human_review(),
            HumanReview::No,
            "reason: first_decision_lost"
        );
    }

    #[test]
    fn a_batch_is_whole_or_nothing() {
        let a = idea(1, "043dd", "none");
        let b = idea(2, "043dd", "none");
        let h = idea(3, "043dd", "prod");
        let l = ledger(&[add(&a), add(&b), add(&h)]);
        assert!(check_batch(&l, &[a.id.clone(), b.id.clone()], true).is_ok());
        assert!(
            check_batch(&l, &[a.id.clone(), h.id.clone()], true).is_err(),
            "reason: ok_took_needs_human"
        );
        assert!(
            check_batch(&l, std::slice::from_ref(&h.id), false).is_ok(),
            "reason: no_on_needs_human"
        );
        assert!(
            check_batch(&l, &[a.id.clone(), uid(99)], true).is_err(),
            "reason: unknown_id_in_batch"
        );
        assert!(
            check_batch(&l, &[a.id.clone(), a.id.clone()], true).is_err(),
            "reason: id_twice_in_batch"
        );
        // a written batch with a needs_human ok is a broken rule: none of it counts
        let l = ledger(&[add(&a), add(&h), batch(3, &[&a, &h], true)]);
        assert!(!l.complete(), "reason: bad_batch_accepted");
        assert_eq!(
            l.ideas[0].human_review(),
            HumanReview::Pending,
            "reason: partial_batch"
        );
    }

    #[test]
    fn damage_makes_the_ledger_incomplete() {
        let a = idea(1, "043dd", "none");
        // cut short: the last line has no newline
        let l = replay(&format!("{}\n{{\"v\":1,\"ev\":\"add\"", add(&a)));
        assert!(
            !l.complete() && l.ideas.len() == 1,
            "reason: cut_line_not_seen"
        );
        assert!(!replay(&add(&a)).complete(), "reason: no_final_newline_ok");
        // an unknown version
        let l = ledger(&[add(&a).replace("\"v\":1", "\"v\":2")]);
        assert!(
            !l.complete() && l.ideas.is_empty(),
            "reason: unknown_v_read"
        );
        // a forged auto next to a red class
        let h = idea(3, "043dd", "prod");
        let l = ledger(&[add(&h).replace("\"needs_human\"", "\"auto\"")]);
        assert!(
            !l.complete() && l.ideas.is_empty(),
            "reason: forged_auto_read"
        );
        // a broken review_batch (it may have been a no): nothing is approvable until fixed
        let l = ledger(&[add(&a), "{\"v\":1,\"ev\":\"review_batch\"".to_string()]);
        assert!(!l.complete(), "reason: broken_batch_ignored");
        assert!(
            footer_auto(&l, "043dd").is_empty(),
            "reason: approvable_on_damage"
        );
        assert!(
            footer(&l, "043dd", T0, 480).contains("帳本不完整"),
            "reason: footer_on_damage"
        );
    }

    #[test]
    fn a_review_needs_andy_a_quote_and_a_no_needs_a_why() {
        let a = idea(1, "043dd", "none");
        for (from, to) in [
            ("\"by\":\"andy\"", "\"by\":\"043dd\""),
            ("\"quote\":\"同意\"", "\"quote\":\" \""),
        ] {
            let l = ledger(&[add(&a), batch(1, &[&a], true).replace(from, to)]);
            assert!(!l.complete(), "reason: review_accepted {to}");
            assert_eq!(l.ideas[0].human_review(), HumanReview::Pending);
        }
        let no = batch(2, &[&a], false).replace("\"why\":\"先不做\"", "\"why\":null");
        assert!(!ledger(&[add(&a), no]).complete(), "reason: no_without_why");
    }

    #[test]
    fn prefixes_resolve_only_when_unique() {
        let a = idea(1, "043dd", "none");
        let l = ledger(&[add(&a)]);
        assert_eq!(l.resolve(&short(&a.id)).len(), 1);
        assert_eq!(l.resolve(&a.id).len(), 1);
        assert!(l.resolve("i_0000").is_empty(), "reason: short_prefix_taken");
        let mut b = idea(2, "043dd", "none");
        b.id = format!("{}{}", &a.id[..8], &uid(2)[8..]);
        let l = ledger(&[add(&a), add(&b)]);
        assert_eq!(
            l.resolve(&short(&a.id)).len(),
            2,
            "reason: ambiguous_prefix_hidden"
        );
    }

    #[test]
    fn the_footer_lists_auto_then_needs_human_and_the_rest() {
        let mut lines = Vec::new();
        let mut ideas = Vec::new();
        for n in 1..=13u128 {
            let seat = if n == 3 { "a9136" } else { "043dd" };
            let touches = if n == 5 { "external" } else { "none" };
            let i = idea(n, seat, touches);
            lines.push(add(&i));
            ideas.push(i);
        }
        lines.push(batch(1, &[&ideas[0]], true));
        let l = ledger(&lines);
        assert!(l.complete(), "{:?}", l.errors);
        let f = footer(&l, "043dd", T0 + 20 * 60_000, 480);
        let want_head =
            "真人未審:本次列出 10 條可後審核准;另有 1 條需個別授權。回「同意」只核准上列 10 條。\n";
        assert!(f.starts_with(want_head), "{f}");
        let a2 = format!(
            "- A | 穩定 | 自動同意優化 | {} S72 建議 2(10:35)\n",
            short(&ideas[1].id)
        );
        assert!(f.contains(&a2), "{f}");
        assert!(!f.contains("建議 1("), "reason: reviewed_in_footer {f}");
        assert!(!f.contains("建議 3("), "reason: other_seat_in_footer {f}");
        let h5 = format!(
            "需個別授權(不在「同意」範圍):\n- A | 穩定 | 需人同意 | {} S72 建議 5 [external](10:38)\n",
            short(&ideas[4].id)
        );
        assert!(f.contains(&h5), "{f}");
        // 12 pending: 10 auto + 1 needs_human shown, a9136's one left
        assert!(
            f.ends_with("(全艦還有 1 條未審:aib-idea list --unreviewed --all)\n"),
            "{f}"
        );
        assert_eq!(footer(&ledger(&[]), "043dd", T0, 480), "真人未審:0 條\n");
    }

    fn settle(n: u128, seq: u64, ids: &[&Idea], outcome: &str) -> String {
        let ids: Vec<String> = ids.iter().map(|i| i.id.clone()).collect();
        let t = Settle {
            outcome: outcome.into(),
            why: "519d0 分流".into(),
            at_ms: T0 + 7_200_000,
            seat: "519d0".into(),
            superseded_by: None,
        };
        settle_line(&uid(7000 + n), seq, &ids, &t)
    }

    /// 519d0 11-19 09:20: a seat settles needs_human ones as itself; the footer stops asking
    /// Andy for them, Andy's review stays pending and still wins; a reviewed one is not
    /// settled; an unknown outcome breaks the ledger.
    #[test]
    fn a_settled_one_leaves_the_footer_but_stays_andys_to_review() {
        let h = idea(1, "043dd", "external");
        let g = idea(2, "043dd", "money");
        let a = idea(3, "043dd", "none");
        let mut lines = vec![add(&h), add(&g), add(&a)];
        lines.push(settle(1, 4, &[&h], "decided"));
        let l = ledger(&lines);
        assert!(l.complete(), "{:?}", l.errors);
        assert_eq!(l.ideas[0].settle.as_ref().map(Settle::word), Some("已定"));
        assert_eq!(
            l.ideas[0].human_review(),
            HumanReview::Pending,
            "reason: settle_is_review"
        );
        assert_eq!(l.ideas[0].to_json()["settle"]["recorded_by_seat"], "519d0");
        let f = footer(&l, "043dd", T0 + 20 * 60_000, 480);
        assert!(
            f.contains("另有 1 條需個別授權"),
            "reason: settled_still_asked {f}"
        );
        assert!(!f.contains("建議 1 ["), "reason: settled_still_asked {f}");
        assert!(f.contains("建議 2 ["), "{f}");
        // Andy can still review a settled one; then it cannot be settled again
        let mut more = lines.clone();
        more.push(batch(1, &[&a], true));
        more.push(settle(2, 6, &[&a], "done"));
        assert!(!ledger(&more).complete(), "reason: reviewed_one_settled");
        let bad = settle(3, 5, &[&g], "decided").replace("\"decided\"", "\"maybe\"");
        let mut more = lines.clone();
        more.push(bad);
        assert!(!ledger(&more).complete(), "reason: unknown_outcome_taken");
        let t = |outcome: &str, seat: &str, by: Option<&Idea>| Settle {
            outcome: outcome.into(),
            why: "w".into(),
            at_ms: T0,
            seat: seat.into(),
            superseded_by: by.map(|i| i.id.clone()),
        };
        assert!(
            check_settle(&l, &[g.id.clone(), g.id.clone()], &t("done", "519d0", None)).is_err()
        );
        // 遇事不決 ideas-2 §1: only the proposing seat withdraws; superseded names another idea
        assert!(
            check_settle(&l, &[g.id.clone()], &t("withdrawn", "519d0", None)).is_err(),
            "reason: other_seat_withdrew"
        );
        assert!(check_settle(&l, &[g.id.clone()], &t("withdrawn", "043dd", None)).is_ok());
        assert!(
            check_settle(&l, &[g.id.clone()], &t("superseded", "519d0", None)).is_err(),
            "reason: superseded_without_target"
        );
        assert!(
            check_settle(&l, &[g.id.clone()], &t("superseded", "519d0", Some(&g))).is_err(),
            "reason: superseded_by_itself"
        );
        assert!(check_settle(&l, &[g.id.clone()], &t("superseded", "519d0", Some(&a))).is_ok());
        assert!(
            check_settle(&l, &[g.id.clone()], &t("done", "519d0", Some(&a))).is_err(),
            "reason: superseded_by_on_done"
        );
    }

    /// Contract §5 c: 已做 counts only with a commit or a file that is really there.
    #[test]
    fn done_evidence_is_a_known_commit_or_an_existing_path() {
        let known = |w: &str| w == "738ca4ac6" || w == "/tmp/x/RECEIPT.out";
        assert_eq!(
            done_evidence("已做(738ca4ac6,收據 /tmp/x/RECEIPT.out)", known).as_deref(),
            Some("738ca4ac6")
        );
        assert_eq!(
            done_evidence("見 /tmp/x/RECEIPT.out。", known).as_deref(),
            Some("/tmp/x/RECEIPT.out"),
            "reason: path_not_found"
        );
        assert_eq!(
            done_evidence("已做 deadbeef0", known),
            None,
            "reason: unknown_commit_taken"
        );
        assert_eq!(
            done_evidence("已做 abc", |_| true),
            None,
            "reason: short_hex_taken"
        );
        assert_eq!(
            done_evidence("照建議做了", |_| true),
            None,
            "reason: words_taken"
        );
    }

    #[test]
    fn the_footer_sorts_s_a_b_c_then_the_order_added() {
        let mut lines = Vec::new();
        let mut ideas = Vec::new();
        for (n, g, ax) in [
            (1u128, "C", "fast"),
            (2, "A", "simple"),
            (3, "S", "unseen"),
            (4, "A", "stable"),
            (5, "B", "fast"),
        ] {
            let mut i = idea(n, "043dd", "none");
            i.grade = g.into();
            i.axis = ax.into();
            lines.push(add(&i));
            ideas.push(i);
        }
        let l = ledger(&lines);
        assert!(l.complete(), "{:?}", l.errors);
        let got: Vec<&str> = footer_auto(&l, "043dd")
            .iter()
            .map(|i| i.title.as_str())
            .collect();
        assert_eq!(
            got,
            ["建議 3", "建議 2", "建議 4", "建議 5", "建議 1"],
            "reason: footer_not_by_grade"
        );
        let f = footer(&l, "043dd", T0 + 20 * 60_000, 480);
        let s3 = format!(
            "- S | 沒想到 | 自動同意優化 | {} S72 建議 3",
            short(&ideas[2].id)
        );
        assert!(f.contains(&s3), "{f}");
        let c1 = format!(
            "- C | 快速 | 自動同意優化 | {} S72 建議 1",
            short(&ideas[0].id)
        );
        assert!(f.contains(&c1), "{f}");
        let mut bad = ideas[0].clone();
        bad.grade = "D".into();
        let r = replay(&format!("{}\n", add(&bad)));
        assert!(!r.complete(), "reason: bad_grade_replayed");
        let mut bad = ideas[0].clone();
        bad.axis = "穩定".into();
        let r = replay(&format!("{}\n", add(&bad)));
        assert!(!r.complete(), "reason: bad_axis_replayed");
    }

    #[test]
    fn clock_is_local_and_dates_other_days() {
        assert_eq!(clock(T0, T0, 480), "10:33");
        assert_eq!(clock(T0 - 86_400_000, T0, 480), "11-13 10:33");
    }

    #[test]
    fn ids_have_their_shape() {
        let u = uuid_from(u128::MAX);
        assert!(uuid_ok(&u), "{u}");
        assert_eq!(&u[14..15], "4", "reason: not_v4 {u}");
        for bad in [
            "",
            "3fa9c201",
            "3FA9C201-0000-4000-8000-000000000000",
            "3fa9c201-0000-4000-8000-00000000000",
        ] {
            assert!(!uuid_ok(bad), "reason: bad_uuid_ok {bad}");
        }
        assert!(seat_ok("043dd") && !seat_ok("043DD") && !seat_ok("43dd"));
        assert_eq!(clean("a\nb\u{1b}[31mc"), "ab[31mc");
    }

    #[test]
    fn only_asking_the_time_is_now_and_a_red_class_still_wins() {
        // S76-when: only when (or with none) ⇒ auto, the answer now
        for t in ["when", "none,when", "when,none"] {
            let i = idea(1, "043dd", t);
            assert_eq!(i.class, Class::Auto, "reason: when_waits_for_a_human {t}");
            assert!(i.when_now(), "reason: when_not_now {t}");
            assert_eq!(i.when_text().as_deref(), Some("時間:立刻"));
        }
        // when next to any red class: the red class decides
        for c in RED {
            let i = idea(2, "043dd", &format!("when,{c}"));
            assert_eq!(
                i.class,
                Class::NeedsHuman,
                "reason: when_lets_red_through {c}"
            );
            assert!(!i.when_now() && i.when_text().is_none());
            assert!(check_when(&i).is_err(), "reason: when_written_for_red {c}");
        }
        assert!(parse_touches("none,money").is_err());
        assert!(check_when(&idea(3, "043dd", "none")).is_err());
        // the add line says when: now, and a line whose when does not follow is refused
        let i = idea(4, "043dd", "when");
        let line = add2(1, &i);
        assert!(line.contains("\"when\":\"now\""), "{line}");
        let l = ledger(std::slice::from_ref(&line));
        assert!(l.complete(), "{:?}", l.errors);
        assert!(l.ideas[0].when_now());
        let forged = add(&idea(5, "043dd", "none")).replace('{', "{\"when\":\"now\",");
        assert!(!ledger(&[forged]).complete(), "reason: when_on_a_none_line");
        let dropped = line.replace(",\"when\":\"now\"", "");
        assert!(!ledger(&[dropped]).complete(), "reason: when_dropped");
    }

    #[test]
    fn a_when_event_gives_now_or_the_earliest_moment() {
        assert_eq!(when_answer(None, None), "立刻");
        assert_eq!(
            when_answer(Some("Windows 機在跑別的"), Some("09:30 租約到期")),
            "最快:09:30 租約到期(因為 Windows 機在跑別的)"
        );
        let i = idea(1, "043dd", "when");
        let w = When {
            blocked_by: Some("機器滿".into()),
            earliest: Some("09:30".into()),
            at_ms: T0 + 1,
            seat: "ebb36".into(),
        };
        let l = ledger(&[add2(1, &i), when_line(&uid(900), 2, &i.id, &w)]);
        assert!(l.complete(), "{:?}", l.errors);
        assert_eq!(
            l.ideas[0].when_text().as_deref(),
            Some("時間:最快:09:30(因為 機器滿)")
        );
        // a when for a red one, or a half-given when, is a broken rule
        let red = idea(2, "043dd", "when,prod");
        let bad = when_line(&uid(901), 2, &red.id, &w);
        assert!(!ledger(&[add2(1, &red), bad]).complete());
        let half = When {
            earliest: None,
            ..w.clone()
        };
        assert!(!ledger(&[add2(1, &i), when_line(&uid(902), 2, &i.id, &half)]).complete());
    }

    #[test]
    fn the_footer_lists_a_when_one_for_review_with_its_time_not_as_needing_a_human() {
        let w = idea(1, "043dd", "when");
        let r = idea(2, "043dd", "when,money");
        let l = ledger(&[add2(1, &w), add2(2, &r)]);
        let f = footer(&l, "043dd", T0 + 600_000, 480);
        let (top, human) = f
            .split_once("需個別授權(不在「同意」範圍):\n")
            .expect("a needs_human section");
        assert!(
            top.contains(&format!("{} S72 建議 1 時間:立刻(", short(&w.id))),
            "reason: when_line_without_its_time {f}"
        );
        assert!(
            !human.contains(&short(&w.id)),
            "reason: when_listed_as_needs_human {f}"
        );
        assert!(human.contains(&short(&r.id)), "{f}");
        assert!(
            f.contains("本次列出 1 條可後審核准;另有 1 條需個別授權"),
            "{f}"
        );
    }
}
