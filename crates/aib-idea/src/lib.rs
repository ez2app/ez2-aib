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

use serde_json::{json, Map, Value};

/// The ledger format this code reads and writes.
pub const V: i64 = 1;
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

/// `--touches`: `none` alone, or red-line classes. Err = usage (rc 64): empty, an unknown
/// word (a typo must never become an automatic yes), or `none` next to anything.
pub fn parse_touches(arg: &str) -> Result<Vec<String>, String> {
    let mut out: Vec<String> = Vec::new();
    for w in arg.split(',').map(str::trim) {
        if w.is_empty() {
            return Err("touches_empty_item".into());
        }
        if w != "none" && !RED.contains(&w) {
            return Err(format!("touches_unknown: {w}"));
        }
        if !out.iter().any(|x| x == w) {
            out.push(w.to_string());
        }
    }
    if out.iter().any(|w| w == "none") && out.len() > 1 {
        return Err("touches_none_with_others".into());
    }
    Ok(out)
}

/// The classification that follows from valid touches.
pub fn classify(touches: &[String]) -> Class {
    if touches.len() == 1 && touches[0] == "none" {
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
}

impl Idea {
    pub fn human_review(&self) -> HumanReview {
        match &self.review {
            None => HumanReview::Pending,
            Some(r) if r.ok => HumanReview::Ok,
            Some(_) => HumanReview::No,
        }
    }
    /// `A | 穩定 | 自動同意`: grade, direction, who agrees (§14 r2).
    pub fn label(&self) -> String {
        let axis = AXES
            .iter()
            .find(|(k, _)| *k == self.axis)
            .map_or(self.axis.as_str(), |(_, w)| w);
        let who = match self.class {
            Class::Auto => "自動同意",
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
            "review": self.review.as_ref().map(|r| json!({
                "verdict": if r.ok { "ok" } else { "no" }, "by": r.by, "quote": r.quote,
                "why": r.why, "at_ms": r.at_ms, "recorded_by_seat": r.seat,
            })),
        })
    }
}

/// The `add` event line.
pub fn add_line(event_id: &str, i: &Idea) -> String {
    json!({
        "v": V, "ev": "add", "event_id": event_id, "idea_id": i.id, "at_ms": i.at_ms,
        "recorded_by_seat": i.seat, "policy_version": POLICY_VERSION, "title": i.title,
        "now": i.now, "suggest": i.suggest, "benefit": i.benefit, "cost": i.cost,
        "touches": i.touches, "classification": i.class.code(), "grade": i.grade, "axis": i.axis, "node": i.node,
        "source": i.source,
    })
    .to_string()
}

/// The `review_batch` event line: one review for every id, all or nothing.
pub fn review_line(event_id: &str, ids: &[String], r: &Review) -> String {
    json!({
        "v": V, "ev": "review_batch", "event_id": event_id, "at_ms": r.at_ms,
        "recorded_by_seat": r.seat, "by": r.by, "quote": r.quote,
        "verdict": if r.ok { "ok" } else { "no" }, "why": r.why, "ids": ids,
    })
    .to_string()
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

fn int(o: &Map<String, Value>, k: &str) -> Result<i64, String> {
    o.get(k)
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("bad {k}"))
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

fn parse_add(l: &Ledger, o: &Map<String, Value>) -> Result<Idea, String> {
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
        at_ms: int(o, "at_ms")?,
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
    })
}

fn parse_batch(l: &Ledger, o: &Map<String, Value>) -> Result<(Vec<String>, Review), String> {
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
        at_ms: int(o, "at_ms")?,
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
        if o.get("v").and_then(Value::as_i64) != Some(V) {
            return Err("unknown v".into());
        }
        let event_id = uuid_field(o, "event_id")?;
        if self.event_ids.contains(&event_id) {
            return Err(format!("event {event_id} twice"));
        }
        match o.get("ev").and_then(Value::as_str) {
            Some("add") => {
                let i = parse_add(self, o)?;
                self.ideas.push(i);
            }
            Some("review_batch") => {
                let (ids, r) = parse_batch(self, o)?;
                for i in self.ideas.iter_mut().filter(|i| ids.contains(&i.id)) {
                    i.review = Some(r.clone());
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
    let mut lines: Vec<&str> = text.split('\n').collect();
    // split leaves "" after the final newline; anything else there was cut short
    let tail = lines.pop().unwrap_or_default();
    for (n, line) in lines.iter().enumerate() {
        let n = n + 1;
        if line.trim().is_empty() {
            continue;
        }
        if line.len() > MAX_LINE_BYTES {
            l.errors
                .push(format!("line {n}: over {MAX_LINE_BYTES} bytes"));
            continue;
        }
        let r = serde_json::from_str::<Value>(line)
            .map_err(|e| format!("bad json: {e}"))
            .and_then(|v| match v {
                Value::Object(o) => l.apply(&o),
                _ => Err("not an object".into()),
            });
        if let Err(e) = r {
            l.errors.push(format!("line {n}: {e}"));
        }
    }
    if !tail.is_empty() {
        l.errors
            .push(format!("line {}: cut short (no newline)", lines.len() + 1));
    }
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
            String::new()
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
        }
    }

    fn add(i: &Idea) -> String {
        add_line(&uid(1_000_000 + i.at_ms as u128), i)
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
        review_line(&uid(5000 + n), &ids, &rv(ok))
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
        let line = review_line(&uid(77), std::slice::from_ref(&b.id), &early);
        let l = ledger(&[add(&a), add(&b), line]);
        assert!(l.complete(), "reason: clean_ledger_errors {:?}", l.errors);
        assert_eq!(
            l.ideas[1].human_review(),
            HumanReview::Ok,
            "reason: replay_by_time"
        );
        // a review written before its add (in file order) is a broken rule
        let line = review_line(&uid(78), std::slice::from_ref(&b.id), &rv(true));
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
            "- A | 穩定 | 自動同意 | {} S72 建議 2(10:35)\n",
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
            "- S | 沒想到 | 自動同意 | {} S72 建議 3",
            short(&ideas[2].id)
        );
        assert!(f.contains(&s3), "{f}");
        let c1 = format!("- C | 快速 | 自動同意 | {} S72 建議 1", short(&ideas[0].id));
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
}
