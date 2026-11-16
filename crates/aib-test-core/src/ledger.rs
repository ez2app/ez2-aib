//! The shadow ledger (S66-shared piece 1; IFACE-ledger-side-ebb36-1117 §A §F §G §I,
//! IFACE-ledger-envelope-b433b-1117 §6 §7): what one `aib test` run did, unit by unit, kept
//! as lines a reviewer, auto-advance and the weekly numbers all read the same way.
//!
//! - Every line is an `aib_ledger::envelope` v 2 line: `v`, `ev`, `event_id`, `occurred_at`,
//!   `aggregate_type = "shadow.unit"`, `aggregate_id = "<repo_id>/<invocation_id>"`,
//!   `sequence` from 1 with no gap. This crate writes no envelope of its own.
//! - One run = one stream = one file `<root>/<repo_id>/<invocation_id>.jsonl`, with one
//!   writer, so no lock is taken and no two runs race for a number (b433b §7). The file is
//!   made new: a second writer for the same run is refused, not merged.
//! - The lines of a stream, in order: one `plan` (the head, the tree, the cells and the
//!   units that must have a result), then `unit` lines, then at most one `end`. Only `end`
//!   with `complete = true` says the run reached its normal path; a stream without it is a
//!   run that was killed or is still going, and its `ran` lines are not evidence.
//! - `invocation_id` and `seat` (who ran it: the receipt's OWNER) are this ledger's own
//!   fields on every line (b433b §6).
//!
//! Reading never guesses: a stream with any bad line is reported with `<file>: line <n>: …`
//! and none of its lines are used. What a unit's line says is judged by the same rules as a
//! receipt (`receipt::is_green` with `Use::Evidence`): a pass on a complete run, a reuse that
//! names an outside run with ref / key / tree / at, or a skip harmless where it is used.

use crate::skip::{SkipWhy, Use};
use aib_ledger::envelope::{self, Aggregate};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};

/// `aggregate_type` of every line here.
pub const AGGREGATE_TYPE: &str = "shadow.unit";
/// The longest line written or read (a plan names every unit of a cell).
pub const MAX_LINE_BYTES: usize = 256 * 1024;

/// How a unit came to its result (SPEC u0 §1.4; a9136 r0 `how`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum How {
    Ran,
    Reused,
    Skipped,
    NotRun,
}

impl How {
    pub fn word(self) -> &'static str {
        match self {
            How::Ran => "ran",
            How::Reused => "reused",
            How::Skipped => "skipped",
            How::NotRun => "not_run",
        }
    }
    fn from_word(s: &str) -> Option<How> {
        Some(match s {
            "ran" => How::Ran,
            "reused" => How::Reused,
            "skipped" => How::Skipped,
            "not_run" => How::NotRun,
            _ => return None,
        })
    }
}

/// What the unit showed (IFACE §F): `pass` = what the unit expects (a knife: it bit),
/// `fail`, `invalid` = ran but cannot be judged (for example 0 tests), `none` = did not run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Pass,
    Fail,
    Invalid,
    None,
}

impl Outcome {
    pub fn word(self) -> &'static str {
        match self {
            Outcome::Pass => "pass",
            Outcome::Fail => "fail",
            Outcome::Invalid => "invalid",
            Outcome::None => "none",
        }
    }
    fn from_word(s: &str) -> Option<Outcome> {
        Some(match s {
            "pass" => Outcome::Pass,
            "fail" => Outcome::Fail,
            "invalid" => Outcome::Invalid,
            "none" => Outcome::None,
            _ => return None,
        })
    }
}

/// Where a reused result came from. Only `External` (a GitHub run / attempt or a receipt
/// file, named by the runner when it decided) can be evidence; `Local` is this machine's own
/// ledger (IFACE §A, §F 2).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Origin {
    Local,
    External,
}

/// The four words a reuse must name (r7 §2.5): where from, the key, the tree, when.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reuse {
    pub origin: Origin,
    /// the run / attempt or receipt file (`ref` in the line, IFACE-drive-shadow §1.2; the
    /// receipt line's `from=`)
    pub from: String,
    pub key: String,
    pub tree: String,
    pub at: String,
}

/// The plan of one run: what must have a result (IFACE §F 1). Auto-advance reads this
/// instead of cutting the workflow file itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Plan {
    /// the head commit, 40 hex
    pub head: String,
    /// the tree the units ran on (SPEC u0 §1.6 A); a head with another tree is another run
    pub tree: String,
    pub host: String,
    pub cells: Vec<String>,
    pub units: Vec<String>,
}

/// One unit's line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unit {
    pub unit: String,
    /// the crate the unit belongs to (`owner.crate`), if any
    pub krate: Option<String>,
    pub how: How,
    pub result: Outcome,
    pub reuse: Option<Reuse>,
    /// `how = not_run`: cancelled / timeout / killed / queued / launch_failed /
    /// upstream_failed / no_knife_table / reuse_without_source / unreadable
    pub why_not: Option<String>,
    /// cancelled: head_changed / by_seat / concurrency; timeout: job / budget
    pub why_detail: Option<String>,
    /// `how = skipped`: 043dd's reason
    pub skip: Option<SkipWhy>,
    pub secs: Option<u64>,
    /// the author's seat (from the branch name), `None` when it cannot be read
    pub seat: Option<String>,
}

/// A name that is one path part and one aggregate part: no `/`, nothing a path reads.
fn plain_id(s: &str) -> bool {
    !s.is_empty()
        && s != "."
        && s != ".."
        && s.len() <= 128
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.')
}

fn head40(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The stream's file.
pub fn stream_path(root: &Path, repo_id: &str, invocation_id: &str) -> PathBuf {
    root.join(repo_id).join(format!("{invocation_id}.jsonl"))
}

/// `event_id` of a line: the first 128 bits of sha256(aggregate_id, sequence) in uuid
/// shape, version 8. One stream has one writer and `invocation_id` names it, so the pair is
/// unique and needs no random source; written twice it is the same id.
pub fn event_id(aggregate_id: &str, sequence: u64) -> String {
    let mut h = Sha256::new();
    h.update(aggregate_id.as_bytes());
    h.update(b"\n");
    h.update(sequence.to_string().as_bytes());
    let mut b: [u8; 16] = h.finalize()[..16].try_into().unwrap_or([0; 16]);
    b[6] = (b[6] & 0x0f) | 0x80; // version 8
    b[8] = (b[8] & 0x3f) | 0x80; // variant 10
    let mut x = String::with_capacity(32);
    for v in b {
        x.push_str(&format!("{v:02x}"));
    }
    format!(
        "{}-{}-{}-{}-{}",
        &x[0..8],
        &x[8..12],
        &x[12..16],
        &x[16..20],
        &x[20..32]
    )
}

/// The one writer of one run's stream.
#[derive(Debug)]
pub struct Writer {
    path: PathBuf,
    aggregate_id: String,
    invocation_id: String,
    seat: String,
    next: u64,
    ended: bool,
}

impl Writer {
    /// Make the stream's file (refused if it is there: one run, one writer) and write its
    /// plan as line 1.
    pub fn start(
        root: &Path,
        repo_id: &str,
        invocation_id: &str,
        seat: &str,
        at_ms: i64,
        plan: &Plan,
    ) -> Result<Writer, String> {
        if !plain_id(repo_id) {
            return Err(format!("bad repo_id {repo_id:?}"));
        }
        if !plain_id(invocation_id) {
            return Err(format!("bad invocation_id {invocation_id:?}"));
        }
        if !head40(&plan.head) {
            return Err(format!("bad head {:?}", plan.head));
        }
        if plan.tree.trim().is_empty() {
            return Err("plan without a tree".into());
        }
        let path = stream_path(root, repo_id, invocation_id);
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("mkdir: {e}"))?;
        }
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| format!("new stream {}: {e}", path.display()))?;
        let mut w = Writer {
            path,
            aggregate_id: format!("{repo_id}/{invocation_id}"),
            invocation_id: invocation_id.to_owned(),
            seat: seat.to_owned(),
            next: 1,
            ended: false,
        };
        let mut f = Map::new();
        f.insert("head".into(), plan.head.clone().into());
        f.insert("tree".into(), plan.tree.clone().into());
        f.insert("host".into(), plan.host.clone().into());
        f.insert("cells".into(), strings(&plan.cells));
        f.insert("units".into(), strings(&plan.units));
        w.line("plan", at_ms, f)?;
        Ok(w)
    }

    pub fn unit(&mut self, at_ms: i64, u: &Unit) -> Result<(), String> {
        if u.unit.trim().is_empty() {
            return Err("unit without a name".into());
        }
        let mut f = Map::new();
        f.insert("unit".into(), u.unit.clone().into());
        opt(&mut f, "crate", &u.krate);
        f.insert("how".into(), u.how.word().into());
        f.insert("result".into(), u.result.word().into());
        if let Some(r) = &u.reuse {
            let mut m = Map::new();
            let origin = match r.origin {
                Origin::Local => "local",
                Origin::External => "external",
            };
            m.insert("origin".into(), origin.into());
            m.insert("ref".into(), r.from.clone().into());
            m.insert("key".into(), r.key.clone().into());
            m.insert("tree".into(), r.tree.clone().into());
            m.insert("at".into(), r.at.clone().into());
            f.insert("reuse".into(), Value::Object(m));
        }
        opt(&mut f, "why_not", &u.why_not);
        opt(&mut f, "why_detail", &u.why_detail);
        if let Some(s) = &u.skip {
            f.insert("skip_why".into(), s.word().into());
            match s {
                SkipWhy::NotThisOs { os } => {
                    f.insert("os".into(), os.clone().into());
                }
                SkipWhy::NotThisEvent { event } => {
                    f.insert("event".into(), event.clone().into());
                }
                SkipWhy::NamedCannotBiteHere { why, bites_on } => {
                    f.insert("reason".into(), why.clone().into());
                    f.insert("bites_on".into(), strings(bites_on));
                }
                _ => {}
            }
        }
        if let Some(s) = u.secs {
            f.insert("secs".into(), s.into());
        }
        opt(&mut f, "author_seat", &u.seat);
        self.line("unit", at_ms, f)
    }

    /// The last line. `complete = true` only on the normal path.
    pub fn end(&mut self, at_ms: i64, complete: bool) -> Result<(), String> {
        let mut f = Map::new();
        f.insert("complete".into(), complete.into());
        self.line("end", at_ms, f)?;
        self.ended = true;
        Ok(())
    }

    fn line(&mut self, ev: &str, at_ms: i64, fields: Map<String, Value>) -> Result<(), String> {
        if self.ended {
            return Err("the stream has ended".into());
        }
        let agg = Aggregate {
            kind: AGGREGATE_TYPE,
            id: &self.aggregate_id,
        };
        let id = event_id(&self.aggregate_id, self.next);
        let mut o = envelope::write(ev, &id, at_ms, agg, self.next);
        o.insert("invocation_id".into(), self.invocation_id.clone().into());
        o.insert("seat".into(), self.seat.clone().into());
        o.extend(fields);
        let text = Value::Object(o).to_string();
        aib_ledger::append(&self.path, &text, MAX_LINE_BYTES).map_err(|e| format!("{e:?}"))?;
        self.next += 1;
        Ok(())
    }
}

fn strings(v: &[String]) -> Value {
    Value::Array(v.iter().cloned().map(Value::from).collect())
}

fn opt(f: &mut Map<String, Value>, k: &str, v: &Option<String>) {
    if let Some(s) = v {
        f.insert(k.into(), s.clone().into());
    }
}

/// One run read back whole.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stream {
    pub invocation_id: String,
    /// who ran it (`run_owner`, IFACE §G 2)
    pub seat: String,
    pub plan: Plan,
    /// when the plan was written, ms since 1970 UTC
    pub at_ms: i64,
    /// `(occurred_at ms, unit)` in file order
    pub units: Vec<(i64, Unit)>,
    /// `None` = no end line (killed or still running)
    pub ended: Option<bool>,
}

impl Stream {
    pub fn complete(&self) -> bool {
        self.ended == Some(true)
    }
}

fn s<'a>(o: &'a Map<String, Value>, k: &str) -> Option<&'a str> {
    o.get(k).and_then(Value::as_str)
}

fn need(o: &Map<String, Value>, k: &str) -> Result<String, String> {
    match s(o, k) {
        Some(v) if !v.trim().is_empty() => Ok(v.to_owned()),
        _ => Err(format!("bad {k}")),
    }
}

fn list(o: &Map<String, Value>, k: &str) -> Result<Vec<String>, String> {
    match o.get(k) {
        Some(Value::Array(a)) => a
            .iter()
            .map(|v| v.as_str().map(str::to_owned).ok_or(format!("bad {k}")))
            .collect(),
        _ => Err(format!("bad {k}")),
    }
}

fn read_skip(o: &Map<String, Value>) -> Result<SkipWhy, String> {
    let word = need(o, "skip_why")?;
    Ok(match word.as_str() {
        "not_this_os" => SkipWhy::NotThisOs {
            os: s(o, "os").unwrap_or_default().to_owned(),
        },
        "not_this_event" => SkipWhy::NotThisEvent {
            event: s(o, "event").unwrap_or_default().to_owned(),
        },
        "user_filter" => SkipWhy::UserFilter,
        "named_cannot_bite_here" => SkipWhy::NamedCannotBiteHere {
            why: s(o, "reason").unwrap_or_default().to_owned(),
            bites_on: list(o, "bites_on").unwrap_or_default(),
        },
        "upstream_failed" => SkipWhy::UpstreamFailed,
        "timeout_budget" => SkipWhy::TimeoutBudget,
        "engine_skip" => SkipWhy::EngineSkip,
        // a word we do not know is never harmless
        _ => SkipWhy::Unknown,
    })
}

fn read_unit(o: &Map<String, Value>) -> Result<Unit, String> {
    let how = How::from_word(&need(o, "how")?).ok_or("bad how")?;
    let result = Outcome::from_word(&need(o, "result")?).ok_or("bad result")?;
    let reuse = match o.get("reuse") {
        None => None,
        Some(Value::Object(m)) => Some(Reuse {
            origin: match s(m, "origin") {
                Some("local") => Origin::Local,
                Some("external") => Origin::External,
                _ => return Err("bad reuse.origin".into()),
            },
            from: s(m, "ref").unwrap_or_default().to_owned(),
            key: s(m, "key").unwrap_or_default().to_owned(),
            tree: s(m, "tree").unwrap_or_default().to_owned(),
            at: s(m, "at").unwrap_or_default().to_owned(),
        }),
        Some(_) => return Err("bad reuse".into()),
    };
    let skip = if o.contains_key("skip_why") {
        Some(read_skip(o)?)
    } else {
        None
    };
    if how == How::Skipped && skip.is_none() {
        return Err("skipped without skip_why".into());
    }
    if how == How::Reused && reuse.is_none() {
        return Err("reused without reuse".into());
    }
    Ok(Unit {
        unit: need(o, "unit")?,
        krate: s(o, "crate").map(str::to_owned),
        how,
        result,
        reuse,
        why_not: s(o, "why_not").map(str::to_owned),
        why_detail: s(o, "why_detail").map(str::to_owned),
        skip,
        secs: o.get("secs").and_then(Value::as_u64),
        seat: s(o, "author_seat").map(str::to_owned),
    })
}

/// Read one stream's text. Any problem (`line <n>: …`) means none of it is used.
pub fn parse_stream(text: &str, repo_id: &str, invocation_id: &str) -> Result<Stream, Vec<String>> {
    let aggregate_id = format!("{repo_id}/{invocation_id}");
    let agg = Aggregate {
        kind: AGGREGATE_TYPE,
        id: &aggregate_id,
    };
    let mut next = 1u64;
    let mut seat: Option<String> = None;
    let mut plan: Option<(i64, Plan)> = None;
    let mut units = Vec::new();
    let mut ended: Option<bool> = None;
    let mut errors = aib_ledger::replay(text, MAX_LINE_BYTES, |o| {
        let head = envelope::read(o, agg, next)?;
        next += 1;
        if s(o, "invocation_id") != Some(invocation_id) {
            return Err("invocation_id is not the stream's".into());
        }
        let who = need(o, "seat")?;
        match &seat {
            Some(first) if *first != who => return Err("seat changed inside a stream".into()),
            _ => seat = Some(who),
        }
        if ended.is_some() {
            return Err("a line after end".into());
        }
        match (head.ev.as_str(), plan.is_some()) {
            ("plan", false) => {
                let p = Plan {
                    head: need(o, "head")?,
                    tree: need(o, "tree")?,
                    host: s(o, "host").unwrap_or_default().to_owned(),
                    cells: list(o, "cells")?,
                    units: list(o, "units")?,
                };
                if !head40(&p.head) {
                    return Err("bad head".into());
                }
                plan = Some((head.at_ms, p));
            }
            ("plan", true) => return Err("a second plan".into()),
            (_, false) => return Err("the first line is not a plan".into()),
            ("unit", true) => units.push((head.at_ms, read_unit(o)?)),
            ("end", true) => {
                ended = Some(
                    o.get("complete")
                        .and_then(Value::as_bool)
                        .ok_or("bad complete")?,
                );
            }
            (ev, true) => return Err(format!("unknown ev {ev}")),
        }
        Ok(())
    });
    match (plan, seat) {
        (Some((at_ms, plan)), Some(seat)) if errors.is_empty() => Ok(Stream {
            invocation_id: invocation_id.to_owned(),
            seat,
            plan,
            at_ms,
            units,
            ended,
        }),
        _ => {
            if errors.is_empty() {
                errors.push("empty stream".into());
            }
            Err(errors)
        }
    }
}

/// Every stream of one repo, and every problem as `<file>: …` (a stream with a problem is
/// left out; the caller must say the ledger is incomplete).
pub fn streams(root: &Path, repo_id: &str) -> (Vec<Stream>, Vec<String>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    if !plain_id(repo_id) {
        problems.push(format!("bad repo_id {repo_id:?}"));
        return (out, problems);
    }
    let dir = root.join(repo_id);
    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (out, problems),
        Err(e) => {
            problems.push(format!("{}: {e}", dir.display()));
            return (out, problems);
        }
    };
    let mut names: Vec<String> = entries
        .filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .collect();
    names.sort();
    for name in names {
        let Some(inv) = name.strip_suffix(".jsonl") else {
            continue;
        };
        if !plain_id(inv) {
            problems.push(format!("{name}: bad invocation_id"));
            continue;
        }
        let path = dir.join(&name);
        let text = match aib_ledger::read(&path) {
            Ok(aib_ledger::Read::Text(t)) => t,
            Ok(aib_ledger::Read::Missing) => continue,
            Ok(aib_ledger::Read::NotUtf8) => {
                problems.push(format!("{name}: not UTF-8"));
                continue;
            }
            Err(e) => {
                problems.push(format!("{name}: {e}"));
                continue;
            }
        };
        match parse_stream(&text, repo_id, inv) {
            Ok(st) => out.push(st),
            Err(es) => problems.extend(es.into_iter().map(|e| format!("{name}: {e}"))),
        }
    }
    (out, problems)
}

/// One row of [`plans`] (IFACE §G 1).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlanRow {
    pub tree: String,
    pub invocation_id: String,
    /// RFC 3339 UTC ms
    pub at: String,
    pub host: String,
    pub cells: Vec<String>,
    pub units: usize,
    pub run_owner: String,
    pub complete: bool,
}

/// The plans of `head`, newest first. No row = not known.
pub fn plans(streams: &[Stream], head: &str) -> Vec<PlanRow> {
    let mut v: Vec<&Stream> = streams.iter().filter(|s| s.plan.head == head).collect();
    v.sort_by(|a, b| {
        b.at_ms
            .cmp(&a.at_ms)
            .then(b.invocation_id.cmp(&a.invocation_id))
    });
    v.into_iter()
        .map(|s| PlanRow {
            tree: s.plan.tree.clone(),
            invocation_id: s.invocation_id.clone(),
            at: envelope::rfc3339_ms(s.at_ms),
            host: s.plan.host.clone(),
            cells: s.plan.cells.clone(),
            units: s.plan.units.len(),
            run_owner: s.seat.clone(),
            complete: s.complete(),
        })
        .collect()
}

/// What one unit's latest line says, judged as evidence (IFACE §A `evidence`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Evidence {
    pub unit: Unit,
    pub invocation_id: String,
    pub run_owner: String,
    /// the run reached its normal path
    pub complete: bool,
    pub green: bool,
}

/// Whether one unit line is evidence (the receipt's rules, `Use::Evidence`).
pub fn unit_green(u: &Unit, complete: bool, bite_shown: impl FnOnce(&[String]) -> bool) -> bool {
    match u.how {
        How::Ran => complete && u.result == Outcome::Pass,
        How::Reused => {
            u.result == Outcome::Pass
                && u.reuse.as_ref().is_some_and(|r| {
                    r.origin == Origin::External
                        && [&r.from, &r.key, &r.tree, &r.at]
                            .iter()
                            .all(|x| !x.trim().is_empty())
                })
        }
        How::Skipped => u
            .skip
            .as_ref()
            .is_some_and(|s| s.harmless(Use::Evidence, bite_shown)),
        How::NotRun => false,
    }
}

/// For `head` on `tree`: each unit's latest line across that head's runs (by occurred_at,
/// a later line in the same stream winning a tie), sorted by unit name.
pub fn evidence(
    streams: &[Stream],
    head: &str,
    tree: &str,
    bite_shown: impl Fn(&[String]) -> bool,
) -> Vec<Evidence> {
    let mut latest: std::collections::BTreeMap<&str, (i64, &Stream, &Unit)> = Default::default();
    for st in streams
        .iter()
        .filter(|s| s.plan.head == head && s.plan.tree == tree)
    {
        for (at, u) in &st.units {
            let newer = latest
                .get(u.unit.as_str())
                .is_none_or(|(t, prev, _)| *at > *t || (*at == *t && std::ptr::eq(*prev, st)));
            if newer {
                latest.insert(u.unit.as_str(), (*at, st, u));
            }
        }
    }
    latest
        .into_values()
        .map(|(_, st, u)| Evidence {
            unit: u.clone(),
            invocation_id: st.invocation_id.clone(),
            run_owner: st.seat.clone(),
            complete: st.complete(),
            green: unit_green(u, st.complete(), &bite_shown),
        })
        .collect()
}

/// Which `required` units have a green line for `head` on `tree`, and which do not
/// (IFACE §A `coverage`; a head read with another tree has none).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Coverage {
    pub have: Vec<String>,
    pub missing: Vec<String>,
}

pub fn coverage(
    streams: &[Stream],
    head: &str,
    tree: &str,
    required: &[String],
    bite_shown: impl Fn(&[String]) -> bool,
) -> Coverage {
    let ev = evidence(streams, head, tree, bite_shown);
    let mut c = Coverage::default();
    for r in required {
        if ev.iter().any(|e| e.unit.unit == *r && e.green) {
            c.have.push(r.clone());
        } else {
            c.missing.push(r.clone());
        }
    }
    c
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEAD: &str = "0123456789abcdef0123456789abcdef01234567";
    const AT: i64 = 1_794_855_172_000;

    fn plan(units: &[&str]) -> Plan {
        Plan {
            head: HEAD.into(),
            tree: "t1".into(),
            host: "lin".into(),
            cells: vec!["rust (macos-latest)".into()],
            units: units.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn ran(name: &str, result: Outcome) -> Unit {
        Unit {
            unit: name.into(),
            krate: Some("aib-sys".into()),
            how: How::Ran,
            result,
            reuse: None,
            why_not: None,
            why_detail: None,
            skip: None,
            secs: Some(3),
            seat: Some("ebb36".into()),
        }
    }

    fn no_bite(_: &[String]) -> bool {
        false
    }

    #[test]
    fn a_run_is_written_and_read_back_line_for_line_in_the_envelope() {
        let d = tempfile::tempdir().unwrap();
        let mut w = Writer::start(
            d.path(),
            "ez2-aib",
            "inv-1",
            "ebb36",
            AT,
            &plan(&["a", "b"]),
        )
        .unwrap();
        w.unit(AT + 1, &ran("a", Outcome::Pass)).unwrap();
        let skip = Unit {
            how: How::Skipped,
            result: Outcome::None,
            skip: Some(SkipWhy::NamedCannotBiteHere {
                why: "sysinfo".into(),
                bites_on: vec!["linux".into()],
            }),
            ..ran("b", Outcome::None)
        };
        w.unit(AT + 2, &skip).unwrap();
        w.end(AT + 3, true).unwrap();
        assert!(w.end(AT + 4, true).is_err(), "nothing after end");

        let text = fs::read_to_string(stream_path(d.path(), "ez2-aib", "inv-1")).unwrap();
        let first: Map<String, Value> = serde_json::from_str(text.lines().next().unwrap()).unwrap();
        assert_eq!(first["v"], 2);
        assert_eq!(first["ev"], "plan");
        assert_eq!(first["aggregate_type"], "shadow.unit");
        assert_eq!(first["aggregate_id"], "ez2-aib/inv-1");
        assert_eq!(first["sequence"], 1);
        assert_eq!(first["invocation_id"], "inv-1");
        assert!(!first.contains_key("at_ms"), "one clock per line");

        let (all, problems) = streams(d.path(), "ez2-aib");
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(all.len(), 1);
        let st = &all[0];
        assert_eq!(st.plan, plan(&["a", "b"]));
        assert_eq!(st.units[0].1, ran("a", Outcome::Pass));
        assert_eq!(st.units[1].1, skip);
        assert!(st.complete());
        assert_eq!(st.seat, "ebb36");
    }

    #[test]
    fn a_second_writer_for_one_run_is_refused() {
        let d = tempfile::tempdir().unwrap();
        Writer::start(d.path(), "r", "inv", "ebb36", AT, &plan(&[])).unwrap();
        let e = Writer::start(d.path(), "r", "inv", "043dd", AT, &plan(&[])).unwrap_err();
        assert!(e.starts_with("new stream"), "{e}");
    }

    #[test]
    fn names_that_leave_the_folder_and_short_heads_are_refused() {
        let d = tempfile::tempdir().unwrap();
        for (repo, inv) in [("..", "i"), ("a/b", "i"), ("r", "../x"), ("r", "")] {
            assert!(Writer::start(d.path(), repo, inv, "s", AT, &plan(&[])).is_err());
        }
        let mut p = plan(&[]);
        p.head = "abc".into();
        assert!(Writer::start(d.path(), "r", "i", "s", AT, &p).is_err());
    }

    #[test]
    fn the_event_id_is_a_version_8_uuid_and_the_same_when_written_twice() {
        let a = event_id("r/i", 1);
        assert_eq!(a, event_id("r/i", 1));
        assert_ne!(a, event_id("r/i", 2));
        assert_ne!(a, event_id("r/j", 1));
        assert_eq!(a.len(), 36);
        assert_eq!(&a[14..15], "8");
        assert!(matches!(&a[19..20], "8" | "9" | "a" | "b"));
    }

    fn line(seq: u64, ev: &str, extra: Value) -> String {
        let agg = Aggregate {
            kind: AGGREGATE_TYPE,
            id: "r/i",
        };
        let mut o = envelope::write(ev, &event_id("r/i", seq), AT, agg, seq);
        o.insert("invocation_id".into(), "i".into());
        o.insert("seat".into(), "ebb36".into());
        if let Value::Object(m) = extra {
            o.extend(m);
        }
        format!("{}\n", Value::Object(o))
    }

    fn plan_line() -> String {
        line(
            1,
            "plan",
            serde_json::json!({"head": HEAD, "tree": "t1", "cells": [], "units": ["a"]}),
        )
    }

    #[test]
    fn a_stream_that_is_not_plan_first_or_skips_a_number_is_not_used() {
        let unit = |seq| {
            line(
                seq,
                "unit",
                serde_json::json!({"unit": "a", "how": "ran", "result": "pass"}),
            )
        };
        // no plan first
        assert!(parse_stream(&unit(1), "r", "i").is_err());
        // a gap: 1 then 3
        assert!(parse_stream(&(plan_line() + &unit(3)), "r", "i").is_err());
        // two plans
        let two = plan_line()
            + &line(
                2,
                "plan",
                serde_json::json!({"head": HEAD, "tree": "t", "cells": [], "units": []}),
            );
        assert!(parse_stream(&two, "r", "i").is_err());
        // a line after end
        let after = plan_line() + &line(2, "end", serde_json::json!({"complete": true})) + &unit(3);
        assert!(parse_stream(&after, "r", "i").is_err());
        // an unknown how, a skip without its reason, a reuse without its source
        for bad in [
            serde_json::json!({"unit": "a", "how": "maybe", "result": "pass"}),
            serde_json::json!({"unit": "a", "how": "skipped", "result": "none"}),
            serde_json::json!({"unit": "a", "how": "reused", "result": "pass"}),
        ] {
            assert!(parse_stream(&(plan_line() + &line(2, "unit", bad)), "r", "i").is_err());
        }
        // cut short: no newline at the end
        let cut = plan_line() + unit(2).trim_end();
        assert!(parse_stream(&cut, "r", "i").is_err());
        // the clean one reads
        assert!(parse_stream(&(plan_line() + &unit(2)), "r", "i").is_ok());
    }

    #[test]
    fn a_bad_stream_is_reported_by_file_and_the_others_still_read() {
        let d = tempfile::tempdir().unwrap();
        let mut w = Writer::start(d.path(), "r", "good", "ebb36", AT, &plan(&["a"])).unwrap();
        w.end(AT, true).unwrap();
        fs::write(d.path().join("r").join("bad.jsonl"), "{\"v\":2}\n").unwrap();
        let (all, problems) = streams(d.path(), "r");
        assert_eq!(all.len(), 1);
        assert_eq!(problems.len(), 1);
        assert!(
            problems[0].starts_with("bad.jsonl: line 1:"),
            "{problems:?}"
        );
        // no folder yet = empty and complete
        assert_eq!(streams(d.path(), "other"), (vec![], vec![]));
    }

    fn run(d: &Path, inv: &str, at: i64, tree: &str, units: &[Unit], end: Option<bool>) {
        let mut p = plan(&["a", "b", "c"]);
        p.tree = tree.into();
        let mut w = Writer::start(d, "r", inv, "ebb36", at, &p).unwrap();
        for u in units {
            w.unit(at + 1, u).unwrap();
        }
        if let Some(c) = end {
            w.end(at + 2, c).unwrap();
        }
    }

    #[test]
    fn plans_come_newest_first_and_a_head_never_run_has_none() {
        let d = tempfile::tempdir().unwrap();
        run(d.path(), "old", AT, "t0", &[], Some(true));
        run(d.path(), "new", AT + 10, "t1", &[], None);
        let (all, _) = streams(d.path(), "r");
        let p = plans(&all, HEAD);
        assert_eq!(p.len(), 2);
        assert_eq!(p[0].invocation_id, "new");
        assert_eq!(p[0].tree, "t1");
        assert!(!p[0].complete);
        assert_eq!(p[0].units, 3);
        assert_eq!(p[0].run_owner, "ebb36");
        assert_eq!(p[1].at, envelope::rfc3339_ms(AT));
        assert!(plans(&all, &"f".repeat(40)).is_empty());
    }

    #[test]
    fn a_pass_counts_only_on_a_run_that_ended_on_its_normal_path() {
        let d = tempfile::tempdir().unwrap();
        run(
            d.path(),
            "killed",
            AT,
            "t1",
            &[ran("a", Outcome::Pass)],
            None,
        );
        let (all, _) = streams(d.path(), "r");
        let req = vec!["a".to_string()];
        assert_eq!(coverage(&all, HEAD, "t1", &req, no_bite).missing, req);

        run(
            d.path(),
            "done",
            AT + 10,
            "t1",
            &[ran("a", Outcome::Pass)],
            Some(true),
        );
        let (all, _) = streams(d.path(), "r");
        assert_eq!(coverage(&all, HEAD, "t1", &req, no_bite).have, req);
        // the same head read with another tree has nothing
        assert_eq!(coverage(&all, HEAD, "t2", &req, no_bite).missing, req);
        // ended but not complete
        let d2 = tempfile::tempdir().unwrap();
        run(
            d2.path(),
            "x",
            AT,
            "t1",
            &[ran("a", Outcome::Pass)],
            Some(false),
        );
        let (all, _) = streams(d2.path(), "r");
        assert_eq!(coverage(&all, HEAD, "t1", &req, no_bite).missing, req);
    }

    #[test]
    fn the_latest_line_of_a_unit_wins_across_runs() {
        let d = tempfile::tempdir().unwrap();
        run(
            d.path(),
            "first",
            AT,
            "t1",
            &[ran("a", Outcome::Pass)],
            Some(true),
        );
        run(
            d.path(),
            "second",
            AT + 10,
            "t1",
            &[ran("a", Outcome::Fail)],
            Some(true),
        );
        let (all, _) = streams(d.path(), "r");
        let ev = evidence(&all, HEAD, "t1", no_bite);
        assert_eq!(ev.len(), 1);
        assert_eq!(ev[0].invocation_id, "second");
        assert!(!ev[0].green);
    }

    #[test]
    fn a_reuse_is_evidence_only_from_outside_with_all_four_words() {
        let reused = |origin, key: &str| Unit {
            how: How::Reused,
            reuse: Some(Reuse {
                origin,
                from: "gh:123/1".into(),
                key: key.into(),
                tree: "t1".into(),
                at: "2026-11-17T00:00:00.000Z".into(),
            }),
            ..ran("a", Outcome::Pass)
        };
        assert!(unit_green(&reused(Origin::External, "k"), false, no_bite));
        assert!(!unit_green(&reused(Origin::Local, "k"), true, no_bite));
        assert!(!unit_green(&reused(Origin::External, " "), true, no_bite));
    }

    #[test]
    fn skips_follow_the_receipt_rules_and_not_run_is_never_green() {
        let skipped = |s: SkipWhy| Unit {
            how: How::Skipped,
            result: Outcome::None,
            skip: Some(s),
            ..ran("a", Outcome::None)
        };
        let os = skipped(SkipWhy::NotThisOs {
            os: "windows".into(),
        });
        assert!(unit_green(&os, true, no_bite));
        assert!(!unit_green(&skipped(SkipWhy::UserFilter), true, no_bite));
        let here = skipped(SkipWhy::NamedCannotBiteHere {
            why: "sysinfo".into(),
            bites_on: vec!["linux".into()],
        });
        assert!(!unit_green(&here, true, no_bite));
        assert!(unit_green(&here, true, |on| on == ["linux"]));
        let not_run = Unit {
            how: How::NotRun,
            result: Outcome::None,
            why_not: Some("timeout".into()),
            why_detail: Some("job".into()),
            ..ran("a", Outcome::None)
        };
        assert!(!unit_green(&not_run, true, |_| true));
        assert!(!unit_green(&ran("a", Outcome::Invalid), true, no_bite));
    }
}
