//! Time-saved events (Andy 11-22: the two time-saving systems, the precheck gates and shadow
//! rerun, show what they saved from one book; every test run's time and size is kept too).
//!
//! No ledger of its own: these are kinds of [`crate::events`] (same month files, same nine
//! common fields, same lock, Unix and Windows). This module fixes the kinds' own fields, checks
//! them, and adds them up for the page's five boxes (today / this week / this month / this
//! year / since the start). Pure functions and file reads; no program is run.
//!
//! This book is for people and for measuring. A same-account process can edit it, so nothing
//! here is a gate or evidence for one: a gate's own trail (who let what through) is aibd's.
//!
//! | kind | own fields |
//! |---|---|
//! | `time_saved` | `system` (`precheck` / `shadow`), `rule`, `verdict` (`reject` / `warn` / `reuse`), `avoided`, `saved_ms`, `confirmed`, `basis`, `mode` (`observe` / `real`), `head` (may be absent) |
//! | `time_saved_confirm` | `ref`: the id of a `time_saved` that was an estimate and has since been checked |
//! | `time_saved_wrong` | `ref`, `penalty_ms`, `basis`: that saving was a wrong block / a wrong reuse |
//! | `unit_time` | `unit`, `ms`, `runner`, `size` and `load1` (may be absent) |
//!
//! Adding up ([`summarize`]):
//! - `mode = observe` is never in a total: a prediction that did nothing. It is counted beside.
//! - a real saving is **confirmed** when it says so or a `time_saved_confirm` names it; else it
//!   is an **estimate**, kept in its own number and never added to the confirmed one.
//! - a saving a `time_saved_wrong` names is taken out of both, and the penalty is its own number.
//! - a box holds what happened in it: the saving by its own time, the penalty by the time it
//!   was found out. Weeks start on Monday; the caller gives the clock's offset from UTC.
//! - a line that is not understood, an event whose fields do not fit, a `ref` that names
//!   nothing, a second confirm / wrong for one saving, an event dated after the reader's clock
//!   (five minutes are allowed for another machine's clock), a folder that is there and cannot
//!   be read: not counted, and counted in `bad` so the page can say "N more could not be
//!   read". Never a guess. A saving may be checked and later still be found wrong.

use crate::envelope::{civil_from_days, days_from_civil, parse_rfc3339_ms};
use crate::events::{self, clean_text, Ctx};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::Path;

pub const KIND_SAVED: &str = "time_saved";
pub const KIND_CONFIRM: &str = "time_saved_confirm";
pub const KIND_WRONG: &str = "time_saved_wrong";
pub const KIND_UNIT: &str = "unit_time";

/// Which of the two systems saved the time.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum System {
    /// A precheck gate stopped work that was sure to fail.
    Precheck,
    /// Shadow rerun did not run what did not change.
    Shadow,
}

impl System {
    pub fn as_str(self) -> &'static str {
        match self {
            System::Precheck => "precheck",
            System::Shadow => "shadow",
        }
    }
    pub fn parse(s: &str) -> Option<System> {
        match s {
            "precheck" => Some(System::Precheck),
            "shadow" => Some(System::Shadow),
            _ => None,
        }
    }
}

/// The verdicts a saving may carry, per system: a gate rejects or warns, shadow rerun reuses.
pub fn verdict_fits(system: System, verdict: &str) -> bool {
    match system {
        System::Precheck => matches!(verdict, "reject" | "warn"),
        System::Shadow => verdict == "reuse",
    }
}

/// One saving, as its writer knows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Saved<'a> {
    pub system: System,
    /// Which rule: `knife-precheck`, `receipt-verdict`, `reuse`, …
    pub rule: &'a str,
    pub verdict: &'a str,
    /// The step that was not done.
    pub avoided: &'a str,
    pub saved_ms: u64,
    /// true: the number is a fact (the compile did fail, the source run did take that long).
    pub confirmed: bool,
    /// One line: where the number comes from.
    pub basis: &'a str,
    /// false: observe mode, nothing was really stopped or reused.
    pub real: bool,
    pub head: Option<&'a str>,
}

/// [`clean_text`] without the blanks around it: a field of blanks is an empty field.
fn text(s: &str) -> String {
    clean_text(s).trim().to_string()
}

fn word(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
}

/// The `time_saved` event for `s`; an error says which field does not fit.
pub fn saved(ctx: &Ctx, s: &Saved<'_>, at_ms: i64) -> Result<Value, String> {
    if !word(s.rule) {
        return Err(format!("rule {:?}: not a-z 0-9 - _ (1 to 64)", s.rule));
    }
    if !verdict_fits(s.system, s.verdict) {
        return Err(format!(
            "verdict {:?} is not one of system {}",
            s.verdict,
            s.system.as_str()
        ));
    }
    let (avoided, basis) = (text(s.avoided), text(s.basis));
    if avoided.is_empty() {
        return Err("avoided is empty".into());
    }
    if basis.is_empty() {
        return Err("basis is empty: a saving says where its number comes from".into());
    }
    let mut m = Map::new();
    m.insert("system".into(), json!(s.system.as_str()));
    m.insert("rule".into(), json!(s.rule));
    m.insert("verdict".into(), json!(s.verdict));
    m.insert("avoided".into(), json!(avoided));
    m.insert("saved_ms".into(), json!(s.saved_ms));
    m.insert("confirmed".into(), json!(s.confirmed));
    m.insert("basis".into(), json!(basis));
    m.insert(
        "mode".into(),
        json!(if s.real { "real" } else { "observe" }),
    );
    if let Some(h) = s.head {
        m.insert("head".into(), json!(clean_text(h)));
    }
    Ok(events::build(ctx, KIND_SAVED, "saved", at_ms, m))
}

/// The `time_saved_confirm` event: the estimate `of` (a `time_saved` id) has been checked.
pub fn confirm(ctx: &Ctx, of: &str, at_ms: i64) -> Result<Value, String> {
    if of.trim().is_empty() {
        return Err("ref is empty".into());
    }
    let mut m = Map::new();
    m.insert("ref".into(), json!(of));
    Ok(events::build(ctx, KIND_CONFIRM, "confirmed", at_ms, m))
}

/// The `time_saved_wrong` event: the saving `of` was a wrong block or a wrong reuse.
pub fn wrong(
    ctx: &Ctx,
    of: &str,
    penalty_ms: u64,
    basis: &str,
    at_ms: i64,
) -> Result<Value, String> {
    if of.trim().is_empty() {
        return Err("ref is empty".into());
    }
    let basis = text(basis);
    if basis.is_empty() {
        return Err("basis is empty: a penalty says where its number comes from".into());
    }
    let mut m = Map::new();
    m.insert("ref".into(), json!(of));
    m.insert("penalty_ms".into(), json!(penalty_ms));
    m.insert("basis".into(), json!(basis));
    Ok(events::build(ctx, KIND_WRONG, "wrong", at_ms, m))
}

/// One unit of one test run, as its runner knows it.
#[derive(Debug, Clone, PartialEq)]
pub struct Unit<'a> {
    /// The script or test unit.
    pub unit: &'a str,
    pub ms: u64,
    /// The kind of machine: seconds of different machines are not compared.
    pub runner: &'a str,
    /// How big it is (knives, cells), when the runner knows.
    pub size: Option<u64>,
    pub load1: Option<f64>,
    /// How it ended, in the runner's own word (`pass`, `fail`, `timeout`, …).
    pub outcome: &'a str,
}

/// The `unit_time` event: one unit of one test run, how long it took and how big it is.
pub fn unit_time(ctx: &Ctx, u: &Unit<'_>, at_ms: i64) -> Result<Value, String> {
    let (unit, runner) = (text(u.unit), text(u.runner));
    if unit.is_empty() {
        return Err("unit is empty".into());
    }
    if runner.is_empty() {
        return Err("runner is empty: seconds of different machines are not compared".into());
    }
    if !word(u.outcome) {
        return Err(format!(
            "outcome {:?}: not a-z 0-9 - _ (1 to 64)",
            u.outcome
        ));
    }
    let mut m = Map::new();
    m.insert("unit".into(), json!(unit));
    m.insert("ms".into(), json!(u.ms));
    m.insert("runner".into(), json!(runner));
    if let Some(n) = u.size {
        m.insert("size".into(), json!(n));
    }
    if let Some(l) = u.load1.filter(|l| l.is_finite() && *l >= 0.0) {
        m.insert("load1".into(), json!(l));
    }
    Ok(events::build(ctx, KIND_UNIT, u.outcome, at_ms, m))
}

/// One box's numbers for one system.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Sum {
    /// Savings that are facts.
    pub confirmed_ms: u64,
    /// Savings that are estimates nobody has checked yet. Never added to `confirmed_ms`.
    pub estimated_ms: u64,
    /// What wrong blocks / wrong reuses found out in this box cost.
    pub penalty_ms: u64,
    /// How many savings (confirmed and estimated) are in the two numbers above.
    pub count: u64,
}

impl Sum {
    fn add(&mut self, o: Sum) {
        self.confirmed_ms = self.confirmed_ms.saturating_add(o.confirmed_ms);
        self.estimated_ms = self.estimated_ms.saturating_add(o.estimated_ms);
        self.penalty_ms = self.penalty_ms.saturating_add(o.penalty_ms);
        self.count = self.count.saturating_add(o.count);
    }
}

/// One box: the two systems apart.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Period {
    pub precheck: Sum,
    pub shadow: Sum,
}

impl Period {
    pub fn total(&self) -> Sum {
        let mut t = self.precheck;
        t.add(self.shadow);
        t
    }
    fn of(&mut self, s: System) -> &mut Sum {
        match s {
            System::Precheck => &mut self.precheck,
            System::Shadow => &mut self.shadow,
        }
    }
}

/// The page's five boxes and what stands beside them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Summary {
    pub today: Period,
    pub week: Period,
    pub month: Period,
    pub year: Period,
    pub all: Period,
    /// The time of the earliest saving counted in `all` ("since …"); None with none.
    pub since_ms: Option<i64>,
    /// Observe-mode savings: predictions that did nothing, in no total.
    pub observed: u64,
    /// Lines and events that were not counted because they could not be read as what they claim.
    pub bad: u64,
}

/// The starts (UTC milliseconds) of the day, week (Monday), month and year `now_ms` is in, on a
/// clock `offset_min` minutes ahead of UTC.
pub fn starts(now_ms: i64, offset_min: i32) -> [i64; 4] {
    let off = i64::from(offset_min) * 60_000;
    let local = now_ms.saturating_add(off);
    let day = local.div_euclid(86_400_000);
    let (y, m, _) = civil_from_days(day);
    // 1970-01-01 was a Thursday: days since Monday = (day + 3) mod 7
    let week = day - (day + 3).rem_euclid(7);
    let at = |d: i64| d.saturating_mul(86_400_000).saturating_sub(off);
    [
        at(day),
        at(week),
        at(days_from_civil(y, m, 1)),
        at(days_from_civil(y, 1, 1)),
    ]
}

struct One {
    at: i64,
    system: System,
    ms: u64,
    confirmed: bool,
    real: bool,
    wrong: bool,
}

fn str_of<'a>(o: &'a Map<String, Value>, k: &str) -> Option<&'a str> {
    o.get(k).and_then(Value::as_str)
}

fn read_saved(o: &Map<String, Value>) -> Option<(String, One)> {
    let id = str_of(o, "id").filter(|s| !s.is_empty())?;
    let at = parse_rfc3339_ms(str_of(o, "at")?)?;
    let system = System::parse(str_of(o, "system")?)?;
    if !verdict_fits(system, str_of(o, "verdict")?) {
        return None;
    }
    let ms = o.get("saved_ms").and_then(Value::as_u64)?;
    let confirmed = o.get("confirmed").and_then(Value::as_bool)?;
    let real = match str_of(o, "mode")? {
        "real" => true,
        "observe" => false,
        _ => return None,
    };
    str_of(o, "basis").filter(|s| !s.trim().is_empty())?;
    str_of(o, "avoided").filter(|s| !s.trim().is_empty())?;
    str_of(o, "rule").filter(|s| word(s))?;
    Some((
        id.to_string(),
        One {
            at,
            system,
            ms,
            confirmed,
            real,
            wrong: false,
        },
    ))
}

/// A confirm's or a wrong's own id, time and `ref`, all three or nothing: (ref, at).
fn read_mark(o: &Map<String, Value>) -> Option<(String, i64)> {
    str_of(o, "id").filter(|s| !s.is_empty())?;
    let at = parse_rfc3339_ms(str_of(o, "at")?)?;
    let r = str_of(o, "ref").filter(|s| !s.trim().is_empty())?;
    Some((r.to_string(), at))
}

/// How far ahead of the reader's clock an event may be and still be read (another machine's
/// clock); an event later than that is counted in `bad`, in no box.
pub const FUTURE_SLACK_MS: i64 = 5 * 60_000;

/// The five boxes from event objects in the order they were written (several programs'
/// files may be given one after another: a `ref` is looked up over all of them).
pub fn summarize(evs: &[Value], now_ms: i64, offset_min: i32) -> Summary {
    let mut out = Summary::default();
    let mut saved: BTreeMap<String, One> = BTreeMap::new();
    // confirms and wrongs in the order they were given: (the saving's id, None = a confirm,
    // Some = a wrong with when it was found out and what it cost)
    let mut marks: Vec<(String, Option<(i64, u64)>)> = Vec::new();
    // a clock a little ahead on another machine is not a future event; anything later is unread
    let latest = now_ms.saturating_add(FUTURE_SLACK_MS);
    for v in evs {
        let Some(o) = v.as_object() else {
            out.bad += 1;
            continue;
        };
        match str_of(o, "kind") {
            Some(KIND_SAVED) => match read_saved(o) {
                // the same id twice: the second is not a second saving
                Some((id, one)) if one.at <= latest && !saved.contains_key(&id) => {
                    saved.insert(id, one);
                }
                _ => out.bad += 1,
            },
            Some(KIND_CONFIRM) => match read_mark(o) {
                Some((r, at)) if at <= latest => marks.push((r, None)),
                _ => out.bad += 1,
            },
            Some(KIND_WRONG) => {
                let p = o.get("penalty_ms").and_then(Value::as_u64);
                let basis = str_of(o, "basis").filter(|s| !s.trim().is_empty());
                match (read_mark(o), p, basis) {
                    (Some((r, at)), Some(p), Some(_)) if at <= latest => {
                        marks.push((r, Some((at, p))));
                    }
                    _ => out.bad += 1,
                }
            }
            // other kinds share the files (unit_time, a program's own events): not this sum's
            Some(_) => {}
            None => out.bad += 1,
        }
    }
    let [day, week, month, year] = starts(now_ms, offset_min);
    let boxes = |at: i64| [at >= day, at >= week, at >= month, at >= year, true];
    let mut periods = [Period::default(); 5];
    for (r, mark) in marks {
        // an observe-mode saving stopped nothing: it is neither checked nor a wrong block
        let Some(one) = saved.get_mut(&r).filter(|one| one.real) else {
            out.bad += 1;
            continue;
        };
        match mark {
            // checked once; a saving that was a fact from the start has nothing to check
            None if !one.confirmed => one.confirmed = true,
            // found wrong once, whether or not it had been checked before
            Some((at, p)) if !one.wrong => {
                one.wrong = true;
                for (i, hit) in boxes(at).into_iter().enumerate() {
                    if hit {
                        let s = periods[i].of(one.system);
                        s.penalty_ms = s.penalty_ms.saturating_add(p);
                    }
                }
            }
            _ => out.bad += 1,
        }
    }
    for one in saved.values() {
        if !one.real {
            out.observed += 1;
            continue;
        }
        if one.wrong {
            continue;
        }
        out.since_ms = Some(out.since_ms.map_or(one.at, |s| s.min(one.at)));
        for (i, hit) in boxes(one.at).into_iter().enumerate() {
            if hit {
                let s = periods[i].of(one.system);
                if one.confirmed {
                    s.confirmed_ms = s.confirmed_ms.saturating_add(one.ms);
                } else {
                    s.estimated_ms = s.estimated_ms.saturating_add(one.ms);
                }
                s.count += 1;
            }
        }
    }
    let [today, week, month, year, all] = periods;
    out.today = today;
    out.week = week;
    out.month = month;
    out.year = year;
    out.all = all;
    out
}

/// Every event object of `progs`' month files under `root`, oldest file first within a
/// program, and how many lines were not a JSON object (a file, a folder or a folder entry that
/// is there and cannot be read counts one; a folder that is not there is a program with no events).
pub fn read_all(root: &Path, progs: &[&str]) -> (Vec<Value>, u64) {
    let (mut evs, mut bad) = (Vec::new(), 0u64);
    for prog in progs {
        let rd = match std::fs::read_dir(events::dir(root, prog)) {
            Ok(rd) => rd,
            // a program that has written nothing yet has no folder: that is empty, not unread
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                bad += 1;
                continue;
            }
        };
        let mut files = Vec::new();
        for e in rd {
            match e {
                Ok(e) => files.push(e.path()),
                Err(_) => bad += 1,
            }
        }
        files.retain(|p| p.extension().is_some_and(|x| x == "jsonl"));
        files.sort();
        for f in files {
            let Ok(text) = std::fs::read_to_string(&f) else {
                bad += 1;
                continue;
            };
            for line in text.lines().filter(|l| !l.trim().is_empty()) {
                match serde_json::from_str::<Value>(line) {
                    Ok(v) if v.is_object() => evs.push(v),
                    _ => bad += 1,
                }
            }
        }
    }
    (evs, bad)
}

/// [`read_all`] then [`summarize`]: what the page's read-only act returns.
pub fn summary_of(root: &Path, progs: &[&str], now_ms: i64, offset_min: i32) -> Summary {
    let (evs, bad) = read_all(root, progs);
    let mut s = summarize(&evs, now_ms, offset_min);
    s.bad += bad;
    s
}
