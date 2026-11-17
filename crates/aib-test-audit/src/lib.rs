// SPDX-License-Identifier: Apache-2.0
//! Shadow rerun U4 (S69-u4, SPEC-shadow-u4-1ced7-1116 r1): once U1 has judged a unit reusable,
//! should it still run for real (a random audit, a forced audit, the defense round), what to do
//! when the real run disagrees with the result it would have reused (revoke, and suspect the
//! sources written beside it), and which units are never reused at all.
//!
//! Pure functions over plain data: no clock, no files, no randomness of its own (the caller
//! passes `now` and a fresh `seed`; the same seed redraws the same audits, so a ledger row can be
//! checked later). Field and code names follow piece 1 r3 §1.7 (`audit_kind`, `decision_code`,
//! `inclusion_probability`, `reference_event_id`). This crate moves into aib-test-core once that
//! skeleton is in main (043dd 11-16 23:30).

pub mod engine;

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const DAY: i64 = 86_400;
/// Random audits run at 1/5 for the first 14 days after the repo's first reuse, then 1/10 (r7 §2.3).
pub const EARLY_DAYS: i64 = 14;
pub const EARLY_RATE: f64 = 0.2;
pub const LATER_RATE: f64 = 0.1;
/// A unit reused this many times in a row since its last real run is audited (r7 §2.3).
pub const STREAK_LIMIT: u32 = 10;
/// A unit whose last real run is this old is audited (r7 §2.3: 滿一週必抽).
pub const AGE_LIMIT_DAYS: i64 = 7;

// ---------- reproducible draws ----------

/// One splitmix64 step.
pub fn mix(z: u64) -> u64 {
    let mut z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn fnv(s: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in s.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h
}

/// A number in [0, 1) fixed by (seed, unit): the same seed draws the same units again.
pub fn draw(seed: u64, unit: &str) -> f64 {
    let x = mix(seed ^ fnv(unit));
    (x >> 11) as f64 / (1u64 << 53) as f64
}

/// The units in an order fixed by the seed (the defense round shuffles and records the seed, so a
/// red that depends on order can be replayed).
pub fn shuffled(units: &[String], seed: u64) -> Vec<String> {
    let mut v = units.to_vec();
    let mut i = v.len();
    while i > 1 {
        let j = (mix(seed.wrapping_add(i as u64)) % (i as u64)) as usize;
        i -= 1;
        v.swap(i, j);
    }
    v
}

/// The random-audit rate at `now`. No reuse yet ⇒ the early rate.
pub fn rate(first_reuse_at: Option<i64>, now: i64) -> f64 {
    match first_reuse_at {
        Some(t) if now - t >= EARLY_DAYS * DAY => LATER_RATE,
        _ => EARLY_RATE,
    }
}

// ---------- choosing an audit ----------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuditKind {
    None,
    Random,
    Forced,
    Defense,
}

impl AuditKind {
    pub fn word(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Random => "random",
            Self::Forced => "forced",
            Self::Defense => "defense",
        }
    }
}

/// What the ledger says about one unit, summed by the caller from piece 1 §1.3 rows.
#[derive(Debug, Clone, Default)]
pub struct History {
    /// the unit's kind (test / lint / knife …), to keep audits off only the cheap kinds
    pub kind: String,
    /// reuses since its last real run
    pub reuse_streak: u32,
    pub last_real_run_at: Option<i64>,
    pub ever_audited: bool,
    /// its last audit did not finish (timeout / cancelled / incomplete)
    pub last_audit_incomparable: bool,
}

/// Audits so far per kind of unit: (audited, candidates).
pub type KindCounts = BTreeMap<String, (u64, u64)>;

/// A kind audited at under half the overall share (r7 §2.7: 不是只抽到便宜的). A kind with no
/// candidates yet counts as undersampled once anything at all has been audited.
pub fn undersampled(kinds: &KindCounts, kind: &str) -> bool {
    let (a, c) = kinds
        .values()
        .fold((0u64, 0u64), |(a, c), (x, y)| (a + x, c + y));
    if a == 0 || c == 0 {
        return false;
    }
    let overall = a as f64 / c as f64;
    match kinds.get(kind) {
        Some((x, y)) if *y > 0 => (*x as f64 / *y as f64) < overall / 2.0,
        _ => true,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Choice {
    pub audit_kind: AuditKind,
    /// the main reason (piece 1 r3 §1.7 fixed codes)
    pub decision_code: String,
    /// every reason, the main one first
    pub reason_codes: Vec<String>,
    /// only for a random audit (r3 §1.7 column 10)
    pub inclusion_probability: Option<f64>,
}

fn choice(kind: AuditKind, code: &str, more: &[&str], p: Option<f64>) -> Choice {
    let mut reason_codes = vec![code.to_string()];
    reason_codes.extend(more.iter().map(|s| (*s).to_string()));
    Choice {
        audit_kind: kind,
        decision_code: code.to_string(),
        reason_codes,
        inclusion_probability: p,
    }
}

/// For one unit U1 judged reusable: reuse it, or run it for real as an audit. The three kinds
/// are kept apart: only a random audit estimates the whole (r7 §9).
pub fn choose(
    h: &History,
    kinds: &KindCounts,
    defense_round: bool,
    seed: u64,
    unit: &str,
    first_reuse_at: Option<i64>,
    now: i64,
) -> Choice {
    if defense_round {
        return choice(AuditKind::Defense, "run_defense_round", &[], None);
    }
    let mut forced: Vec<&str> = Vec::new();
    if h.reuse_streak >= STREAK_LIMIT {
        forced.push("forced:streak10");
    }
    match h.last_real_run_at {
        Some(t) if now - t < AGE_LIMIT_DAYS * DAY => {}
        _ => forced.push("forced:age7d"),
    }
    if !h.ever_audited && undersampled(kinds, &h.kind) {
        forced.push("forced:kind_undersampled");
    }
    if h.last_audit_incomparable {
        forced.push("forced:incomparable");
    }
    // the random draw comes before the forced reasons (spec §1 order; 5d33a f6c37623 M1): a unit
    // that is also forced must still be able to land in the random sample, or the sample never
    // holds the units reused longest and the estimate comes out low. Drawn ⇒ a random audit that
    // still names its forced reasons
    let p = rate(first_reuse_at, now);
    if draw(seed, unit) < p {
        return choice(AuditKind::Random, "run_audit_random", &forced, Some(p));
    }
    if !forced.is_empty() {
        return choice(AuditKind::Forced, "run_audit_forced", &forced, None);
    }
    choice(AuditKind::None, "reuse_same_key", &[], None)
}

// ---------- comparing an audit with the result it would have reused ----------

/// piece 1 r3 §1.7 column 11
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Pass,
    Fail,
    Timeout,
    Cancelled,
    Invalid,
    Incomplete,
}

impl Outcome {
    /// ran to its end, so it can be compared
    pub fn finished(self) -> bool {
        matches!(self, Self::Pass | Self::Fail | Self::Invalid)
    }
}

/// What `verdict_digest` is made of (r3 §1.7): the parts are kept so a mismatch can be named.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub outcome: Outcome,
    pub tests_ran: Option<u32>,
    /// failed test names (a knife: the ones it bit)
    pub failed: Vec<String>,
    /// digest of the normalised output (times, addresses, pids, temp paths taken out)
    pub output_digest: String,
    /// the run did not finish (r3 §1.7 `censored`)
    pub censored: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Compare {
    Same,
    /// same outcome, count and failed names; only the normalised output differs (結果同、行為有變)
    BehaviorChanged,
    Mismatch,
    /// the audit did not finish: neither same nor mismatch, audit it again next time
    Incomparable,
}

impl Compare {
    pub fn word(self) -> &'static str {
        match self {
            Self::Same => "same",
            Self::BehaviorChanged => "behavior_changed",
            Self::Mismatch => "mismatch",
            Self::Incomparable => "incomparable",
        }
    }
}

/// The audit's real run against the result that would have been reused.
pub fn compare(audit: &Verdict, source: &Verdict) -> Compare {
    if audit.censored || !audit.outcome.finished() {
        return Compare::Incomparable;
    }
    let names = |v: &Verdict| v.failed.iter().cloned().collect::<BTreeSet<String>>();
    if audit.outcome != source.outcome
        || audit.tests_ran != source.tests_ran
        || names(audit) != names(source)
    {
        return Compare::Mismatch;
    }
    if audit.output_digest != source.output_digest {
        return Compare::BehaviorChanged;
    }
    Compare::Same
}

// ---------- never reuse ----------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NeverReuse {
    /// formats or writes files, regenerates code, updates the lock file, migrates a database
    WritesWorktree,
    /// reads the network, a remote branch or state outside the repo (advisories, leak scan,
    /// push gate, merge watch, push freeze)
    ReadsOutside,
    /// deploys, sends a message, pushes, opens a PR
    SideEffect,
    /// reads the time, randomness or the home folder
    TimeRandomHome,
    /// the same key in the same environment both passed and failed
    Flaky,
}

impl NeverReuse {
    pub const ALL: [NeverReuse; 5] = [
        Self::WritesWorktree,
        Self::ReadsOutside,
        Self::SideEffect,
        Self::TimeRandomHome,
        Self::Flaky,
    ];

    pub fn word(self) -> &'static str {
        match self {
            Self::WritesWorktree => "writes_worktree",
            Self::ReadsOutside => "reads_outside",
            Self::SideEffect => "side_effect",
            Self::TimeRandomHome => "time_random_home",
            Self::Flaky => "flaky",
        }
    }

    pub fn from_word(s: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|n| n.word() == s)
    }
}

/// What a unit declares (adapter or project file), piece 1 §1.1 Outputs / Policy.
#[derive(Debug, Clone, Copy, Default)]
pub struct Declared {
    pub writes_worktree: bool,
    pub reads_outside: bool,
    pub side_effect: bool,
    pub time_random_home: bool,
}

/// The never-reuse class a unit falls in by what it declares; side effects first.
pub fn never_reuse(d: Declared) -> Option<NeverReuse> {
    if d.side_effect {
        Some(NeverReuse::SideEffect)
    } else if d.writes_worktree {
        Some(NeverReuse::WritesWorktree)
    } else if d.reads_outside {
        Some(NeverReuse::ReadsOutside)
    } else if d.time_random_home {
        Some(NeverReuse::TimeRandomHome)
    } else {
        None
    }
}

/// One real run of a unit, for the flaky check.
#[derive(Debug, Clone)]
pub struct RealRun {
    pub key: String,
    pub env_id: String,
    pub outcome: Outcome,
    /// rerun only because the run before it was red: not evidence (r7 §9)
    pub retry_after_red: bool,
}

/// Flaky = the same key in the same environment has both a pass and a fail among runs that were
/// not retries after a red.
pub fn flaky(runs: &[RealRun]) -> bool {
    let mut seen: BTreeMap<(&str, &str), (bool, bool)> = BTreeMap::new();
    for r in runs.iter().filter(|r| !r.retry_after_red) {
        let e = seen
            .entry((r.key.as_str(), r.env_id.as_str()))
            .or_insert((false, false));
        match r.outcome {
            Outcome::Pass => e.0 = true,
            Outcome::Fail => e.1 = true,
            _ => {}
        }
    }
    seen.values().any(|(p, f)| *p && *f)
}

// ---------- revocations ----------

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Scope {
    Unit(String),
    Key(String),
    /// one source row (its event_id)
    Source(String),
    /// every source written under this rule version
    Policy(String),
}

impl Scope {
    fn parts(&self) -> (&'static str, &str) {
        match self {
            Self::Unit(x) => ("unit", x),
            Self::Key(x) => ("key", x),
            Self::Source(x) => ("source", x),
            Self::Policy(x) => ("policy", x),
        }
    }

    fn from_parts(kind: &str, id: &str) -> Option<Self> {
        let id = id.to_string();
        match kind {
            "unit" => Some(Self::Unit(id)),
            "key" => Some(Self::Key(id)),
            "source" => Some(Self::Source(id)),
            "policy" => Some(Self::Policy(id)),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Why {
    AuditMismatch,
    /// written in the same invocation under the same rule version as a source that mismatched;
    /// lifted when its own real run confirms it
    Suspect,
    NeverReuse(NeverReuse),
    Manual,
}

impl Why {
    pub fn word(&self) -> String {
        match self {
            Self::AuditMismatch => "audit_mismatch".into(),
            Self::Suspect => "suspect".into(),
            Self::NeverReuse(n) => format!("never_reuse:{}", n.word()),
            Self::Manual => "manual".into(),
        }
    }

    pub fn from_word(s: &str) -> Option<Self> {
        match s {
            "audit_mismatch" => Some(Self::AuditMismatch),
            "suspect" => Some(Self::Suspect),
            "manual" => Some(Self::Manual),
            _ => s
                .strip_prefix("never_reuse:")
                .and_then(NeverReuse::from_word)
                .map(Self::NeverReuse),
        }
    }
}

/// One line of the revocation table: a revoke, or the lifting of an earlier one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Entry {
    Revoke {
        scope: Scope,
        why: Why,
        at: i64,
        by: String,
        /// ledger event_ids
        evidence: Vec<String>,
    },
    /// lifting needs a reason; it goes in the table like any other line
    Lift {
        scope: Scope,
        at: i64,
        by: String,
        reason: String,
    },
}

/// The table as JSON Lines.
pub fn line(e: &Entry) -> String {
    let v = match e {
        Entry::Revoke {
            scope,
            why,
            at,
            by,
            evidence,
        } => {
            let (k, id) = scope.parts();
            json!({"op": "revoke", "scope": k, "id": id, "why": why.word(), "at": at, "by": by,
                   "evidence": evidence})
        }
        Entry::Lift {
            scope,
            at,
            by,
            reason,
        } => {
            let (k, id) = scope.parts();
            json!({"op": "lift", "scope": k, "id": id, "at": at, "by": by, "reason": reason})
        }
    };
    v.to_string()
}

fn text(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

fn one(raw: &str) -> Result<Entry, String> {
    let v: Value = serde_json::from_str(raw).map_err(|e| format!("not JSON: {e}"))?;
    let scope = match (text(&v, "scope"), text(&v, "id")) {
        (Some(k), Some(id)) if !id.is_empty() => {
            Scope::from_parts(&k, &id).ok_or_else(|| format!("unknown scope {k}"))?
        }
        _ => return Err("scope and id are required".into()),
    };
    let at = v
        .get("at")
        .and_then(Value::as_i64)
        .ok_or("at is required")?;
    let by = text(&v, "by")
        .filter(|b| !b.is_empty())
        .ok_or("by is required")?;
    match text(&v, "op").as_deref() {
        Some("revoke") => {
            let why = text(&v, "why")
                .and_then(|w| Why::from_word(&w))
                .ok_or("unknown why")?;
            let evidence = match v.get("evidence").and_then(Value::as_array) {
                Some(a) => a
                    .iter()
                    .map(|x| {
                        x.as_str()
                            .map(str::to_string)
                            .ok_or("evidence must be text")
                    })
                    .collect::<Result<Vec<_>, _>>()?,
                None => return Err("evidence is required".into()),
            };
            Ok(Entry::Revoke {
                scope,
                why,
                at,
                by,
                evidence,
            })
        }
        Some("lift") => {
            let reason = text(&v, "reason")
                .filter(|r| !r.trim().is_empty())
                .ok_or("a lift needs a reason")?;
            Ok(Entry::Lift {
                scope,
                at,
                by,
                reason,
            })
        }
        _ => Err("op must be revoke or lift".into()),
    }
}

/// Read the table. Any bad line makes the whole table unreadable: the caller then reuses nothing
/// (a broken table refuses everything, it never allows everything).
pub fn parse(table: &str) -> Result<Vec<Entry>, String> {
    table
        .lines()
        .enumerate()
        .filter(|(_, l)| !l.trim().is_empty())
        .map(|(n, l)| one(l).map_err(|e| format!("line {}: {e}", n + 1)))
        .collect()
}

/// The reuse being asked about.
#[derive(Debug, Clone, Copy)]
pub struct Ask<'a> {
    pub unit: &'a str,
    pub key: &'a str,
    /// the source row's event_id
    pub source: &'a str,
    /// the rule version the source was written under
    pub rule_version: &'a str,
}

/// The scopes in force after every revoke and lift, in table order.
pub fn in_force(entries: &[Entry]) -> BTreeMap<(String, String), Why> {
    let mut on: BTreeMap<(String, String), Why> = BTreeMap::new();
    for e in entries {
        match e {
            Entry::Revoke { scope, why, .. } => {
                let (k, id) = scope.parts();
                on.insert((k.to_string(), id.to_string()), why.clone());
            }
            Entry::Lift { scope, .. } => {
                let (k, id) = scope.parts();
                on.remove(&(k.to_string(), id.to_string()));
            }
        }
    }
    on
}

/// Why the table refuses this reuse: `Some("run_source_revoked:<scope>:<why>")`, or `None` when it
/// allows it. An unreadable table refuses every reuse.
pub fn refused(table: &Result<Vec<Entry>, String>, ask: &Ask) -> Option<String> {
    let entries = match table {
        Ok(e) => e,
        Err(why) => return Some(format!("run_source_revoked:table_unreadable:{why}")),
    };
    let on = in_force(entries);
    for (k, id) in [
        ("unit", ask.unit),
        ("key", ask.key),
        ("source", ask.source),
        ("policy", ask.rule_version),
    ] {
        if let Some(why) = on.get(&(k.to_string(), id.to_string())) {
            return Some(format!("run_source_revoked:{k}:{}", why.word()));
        }
    }
    None
}

/// Who wrote a source row (piece 1 r3 §1.7 `invocation_id`) and under which rule version.
#[derive(Debug, Clone)]
pub struct SourceMeta {
    pub event_id: String,
    pub invocation_id: String,
    pub rule_version: String,
}

/// After `bad` mismatched: the other sources written in the same invocation under the same rule
/// version. They are not reused until each one's own real run confirms it (同源污染).
pub fn suspects(bad: &SourceMeta, all: &[SourceMeta]) -> Vec<String> {
    all.iter()
        .filter(|s| {
            s.event_id != bad.event_id
                && s.invocation_id == bad.invocation_id
                && s.rule_version == bad.rule_version
        })
        .map(|s| s.event_id.clone())
        .collect()
}

/// The table lines one mismatch writes: the unit revoked, its sibling sources suspected.
pub fn on_mismatch(
    unit: &str,
    bad: &SourceMeta,
    all: &[SourceMeta],
    audit_event: &str,
    now: i64,
) -> Vec<Entry> {
    let evidence = vec![audit_event.to_string(), bad.event_id.clone()];
    let mut out = vec![Entry::Revoke {
        scope: Scope::Unit(unit.to_string()),
        why: Why::AuditMismatch,
        at: now,
        by: "engine".into(),
        evidence: evidence.clone(),
    }];
    out.extend(suspects(bad, all).into_iter().map(|s| Entry::Revoke {
        scope: Scope::Source(s),
        why: Why::Suspect,
        at: now,
        by: "engine".into(),
        evidence: evidence.clone(),
    }));
    out
}

// ---------- the defense round ----------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Defense {
    /// run every unit for real this time
    Due {
        /// days past the period (None = never ran one, or something it hangs on changed)
        overdue_days: Option<i64>,
        why: &'static str,
    },
    NotDue,
}

/// It is not a scheduler: each run asks whether the round is due, and if it is, this run is the
/// round. `env_changed` = the lock file, toolchain, OS or AIB itself changed since the last one.
pub fn defense_due(last_at: Option<i64>, period_days: i64, env_changed: bool, now: i64) -> Defense {
    let Some(last) = last_at else {
        return Defense::Due {
            overdue_days: None,
            why: "first",
        };
    };
    if env_changed {
        return Defense::Due {
            overdue_days: None,
            why: "env_changed",
        };
    }
    let age = now - last;
    if age >= period_days * DAY {
        Defense::Due {
            overdue_days: Some(age / DAY - period_days),
            why: "overdue",
        }
    } else {
        Defense::NotDue
    }
}

/// The defense round against its snapshot: for each unit that would have been reused, the real
/// run compared with the result it would have reused. Only what is not `Same` is returned; a
/// `Mismatch` here is an input the unit never listed (抽查抓不到的那種).
pub fn defense_findings(
    would_reuse: &BTreeMap<String, Verdict>,
    actual: &BTreeMap<String, Verdict>,
) -> Vec<(String, Compare)> {
    would_reuse
        .iter()
        .filter_map(|(unit, source)| {
            let got = actual.get(unit)?;
            let c = compare(got, source);
            (c != Compare::Same).then(|| (unit.clone(), c))
        })
        .collect()
}
