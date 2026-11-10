// SPDX-License-Identifier: Apache-2.0
//! Host provisioning as data (4.4.1 §22 / §24-4: `host spec lint` and the plan half of
//! `host provision --spec F [--from <step>]`).
//!
//! A provisioning spec is the machine-readable ledger of how a bare host becomes a host
//! that can carry sites: ordered steps, each with its approval level, what it runs, how
//! the result is checked and what the way back is. This module reads such a spec (as a
//! JSON value; the caller owns the file format and the file system), refuses anything a
//! one-command provision could not act on, and turns an accepted spec into an ordered
//! [`Plan`] with a hash an approval can be bound to. No I/O here: nothing is run.
//!
//! Spec, version 2:
//!
//! ```json
//! { "version": 2,
//!   "vars": { "hostname": "host1", "public_ipv4": "203.0.113.10" },
//!   "steps": [
//!     { "id": "B0.precheck", "level": "L0", "kind": "check", "title": "…",
//!       "verify": [ { "run": "hostname -f", "expect": { "eq": "{{hostname}}" } } ] },
//!     { "id": "B2.repo", "level": "L1", "kind": "run", "title": "…",
//!       "requires": ["B0.precheck"],
//!       "apply": ["apt-get update"],
//!       "verify": [ { "run": "apt-cache policy proxmox-ve", "expect": { "rc": 0 } } ],
//!       "recovery": { "kind": "rollback", "run": ["rm /etc/apt/sources.list.d/pve.sources"] } }
//!   ] }
//! ```
//!
//! What [`lint`] refuses (every finding carries a stable code):
//! * `P01` wrong shape or version; `P10` a key this version does not know (typos).
//! * `P02` step id missing, malformed or used twice.
//! * `P03` / `P04` level or kind unknown, or a pair that makes no sense (`check` is `L0`
//!   and runs nothing; `run` / `manual` write, so they are `L1` or `L2` and say what to do).
//! * `P05` a step without a decidable verify (a command plus exactly one expectation).
//! * `P06` a writing step without an explicit way back: `rollback` with commands, or
//!   `unsupported` with the recovery path spelled out (§1.1: never pretend).
//! * `P07` a `{{name}}` that `vars` does not define, or an unclosed `{{`.
//! * `P08` a host-specific value written out in a step instead of `{{name}}` — the spec
//!   must provision the next host too, and must be publishable as a fixture.
//! * `P09` `requires` naming an unknown step, itself, or forming a cycle.

use alloc::borrow::ToOwned;
use alloc::collections::BTreeSet;
use alloc::string::{String, ToString};
use alloc::vec::Vec;
use core::fmt;

use serde_json::{json, Map, Value};

/// The spec version this module reads.
pub const VERSION: u64 = 2;

/// Shortest variable value that [`lint`] treats as host-specific (`P08`). Shorter values
/// ("2", "vg0", "bios") appear in ordinary command text too often to be told apart.
const LITERAL_MIN: usize = 6;

/// One reason a spec was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// Stable rule code, `P01` … `P10`.
    pub code: &'static str,
    /// The step the finding is about, when there is one.
    pub step: Option<String>,
    /// What is wrong, for a person.
    pub msg: String,
}

impl fmt::Display for Finding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.step {
            Some(s) => write!(f, "{} [{}] {}", self.code, s, self.msg),
            None => write!(f, "{} {}", self.code, self.msg),
        }
    }
}

/// Approval level of a step (4.4.1 §1.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    /// Read only.
    L0,
    /// A write inside a standing authorisation.
    L1,
    /// Irreversible, customer-facing, or costs money: a person approves this run.
    L2,
}

impl Level {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "L0" => Some(Level::L0),
            "L1" => Some(Level::L1),
            "L2" => Some(Level::L2),
            _ => None,
        }
    }

    /// The spelling used in specs and plans.
    pub fn as_str(self) -> &'static str {
        match self {
            Level::L0 => "L0",
            Level::L1 => "L1",
            Level::L2 => "L2",
        }
    }
}

/// What a step is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// Only verifies; runs nothing that writes.
    Check,
    /// Commands the tool runs.
    Run,
    /// Something only a person can do (a rescue boot, a password, a hardware token);
    /// `apply` holds the instructions and the tool stops there until the verify passes.
    Manual,
}

impl Kind {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "check" => Some(Kind::Check),
            "run" => Some(Kind::Run),
            "manual" => Some(Kind::Manual),
            _ => None,
        }
    }

    /// The spelling used in specs and plans.
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Check => "check",
            Kind::Run => "run",
            Kind::Manual => "manual",
        }
    }
}

/// What a verify command must show.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Expect {
    /// Exit status.
    Rc(i64),
    /// Trimmed standard output equals this text.
    Eq(String),
    /// Standard output contains this text.
    Contains(String),
}

/// One decidable check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verify {
    /// The command.
    pub run: String,
    /// What it must show.
    pub expect: Expect,
    /// `true` when the check probes actively (opens connections, sends traffic) rather
    /// than only reading state (§1.1).
    pub active: bool,
}

/// The way back from a step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Recovery {
    /// Nothing to undo (checks only).
    None,
    /// Commands that undo the step.
    Rollback(Vec<String>),
    /// No rollback exists; this is the recovery path, in words.
    Unsupported(String),
}

/// One accepted step. Variables are still `{{name}}` here; [`plan`] fills them in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    /// Unique id.
    pub id: String,
    /// Approval level.
    pub level: Level,
    /// Check, run or manual.
    pub kind: Kind,
    /// One line for a person.
    pub title: String,
    /// Steps that must be done first.
    pub requires: Vec<String>,
    /// Commands (`run`) or instructions (`manual`); empty for `check`.
    pub apply: Vec<String>,
    /// At least one.
    pub verify: Vec<Verify>,
    /// The way back.
    pub recovery: Recovery,
    /// What the step destroys, if anything, in words.
    pub destroys: Option<String>,
}

/// An accepted spec. Only [`lint`] builds one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    vars: Vec<(String, String)>,
    steps: Vec<Step>,
    order: Vec<usize>,
}

impl Spec {
    /// Steps in the order the spec lists them.
    pub fn steps(&self) -> &[Step] {
        &self.steps
    }

    /// Variable names and values, sorted by name.
    pub fn vars(&self) -> &[(String, String)] {
        &self.vars
    }
}

const TOP_KEYS: &[&str] = &["version", "vars", "steps"];
const STEP_KEYS: &[&str] = &[
    "id", "level", "kind", "title", "requires", "apply", "verify", "recovery", "destroys", "note",
];
const VERIFY_KEYS: &[&str] = &["run", "expect", "active"];
const EXPECT_KEYS: &[&str] = &["rc", "eq", "contains"];
const RECOVERY_KEYS: &[&str] = &["kind", "run", "path"];

fn finding(code: &'static str, step: Option<&str>, msg: String) -> Finding {
    Finding {
        code,
        step: step.map(ToOwned::to_owned),
        msg,
    }
}

fn valid_id(id: &str) -> bool {
    let b = id.as_bytes();
    !b.is_empty()
        && b.len() <= 64
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|&c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

fn valid_var_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 40
        && name
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
}

fn unknown_keys(
    obj: &Map<String, Value>,
    known: &[&str],
    step: Option<&str>,
    what: &str,
    out: &mut Vec<Finding>,
) {
    for k in obj.keys() {
        if !known.contains(&k.as_str()) {
            out.push(finding("P10", step, format!("{what}: unknown key {k:?}")));
        }
    }
}

/// `{{name}}` occurrences in `text`. `Err` when a `{{` is not closed or the name is not a
/// variable name.
fn placeholders(text: &str) -> Result<Vec<&str>, String> {
    let mut names = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find("{{") {
        let after = &rest[start + 2..];
        let Some(end) = after.find("}}") else {
            return Err("unclosed {{".to_owned());
        };
        let name = &after[..end];
        if !valid_var_name(name) {
            return Err(format!("{{{{{name}}}}} is not a variable name"));
        }
        names.push(name);
        rest = &after[end + 2..];
    }
    Ok(names)
}

fn render(text: &str, vars: &[(String, String)]) -> String {
    let mut out = text.to_owned();
    for (name, value) in vars {
        let mut needle = String::from("{{");
        needle.push_str(name);
        needle.push_str("}}");
        out = out.replace(&needle, value);
    }
    out
}

fn string_list(v: Option<&Value>, step: &str, what: &str, out: &mut Vec<Finding>) -> Vec<String> {
    let Some(v) = v else {
        return Vec::new();
    };
    let Some(arr) = v.as_array() else {
        out.push(finding(
            "P01",
            Some(step),
            format!("{what} must be a list of strings"),
        ));
        return Vec::new();
    };
    let mut items = Vec::new();
    for item in arr {
        match item.as_str() {
            Some(s) if !s.trim().is_empty() => items.push(s.to_owned()),
            _ => out.push(finding(
                "P01",
                Some(step),
                format!("{what}: every entry must be a non-empty string"),
            )),
        }
    }
    items
}

fn parse_verify(v: &Value, step: &str, out: &mut Vec<Finding>) -> Option<Verify> {
    let Some(obj) = v.as_object() else {
        out.push(finding(
            "P05",
            Some(step),
            "verify entry must be an object".to_owned(),
        ));
        return None;
    };
    unknown_keys(obj, VERIFY_KEYS, Some(step), "verify", out);
    let run = match obj.get("run").and_then(Value::as_str) {
        Some(s) if !s.trim().is_empty() => s.to_owned(),
        _ => {
            out.push(finding(
                "P05",
                Some(step),
                "verify needs a command in `run`".to_owned(),
            ));
            return None;
        }
    };
    let active = match obj.get("active") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => {
            out.push(finding(
                "P05",
                Some(step),
                "verify `active` must be true or false".to_owned(),
            ));
            return None;
        }
    };
    let Some(exp) = obj.get("expect").and_then(Value::as_object) else {
        out.push(finding(
            "P05",
            Some(step),
            "verify needs `expect` with exactly one of rc / eq / contains".to_owned(),
        ));
        return None;
    };
    unknown_keys(exp, EXPECT_KEYS, Some(step), "expect", out);
    if exp.len() != 1 {
        out.push(finding(
            "P05",
            Some(step),
            "expect must hold exactly one of rc / eq / contains".to_owned(),
        ));
        return None;
    }
    let expect = if let Some(rc) = exp.get("rc") {
        rc.as_i64().map(Expect::Rc)
    } else if let Some(eq) = exp.get("eq") {
        eq.as_str().map(|s| Expect::Eq(s.to_owned()))
    } else {
        exp.get("contains")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(|s| Expect::Contains(s.to_owned()))
    };
    match expect {
        Some(expect) => Some(Verify {
            run,
            expect,
            active,
        }),
        None => {
            out.push(finding(
                "P05",
                Some(step),
                "expect: rc is an integer, eq a string, contains a non-empty string".to_owned(),
            ));
            None
        }
    }
}

fn parse_recovery(v: Option<&Value>, step: &str, out: &mut Vec<Finding>) -> Option<Recovery> {
    let Some(v) = v else {
        return Some(Recovery::None);
    };
    let Some(obj) = v.as_object() else {
        out.push(finding(
            "P06",
            Some(step),
            "recovery must be an object".to_owned(),
        ));
        return None;
    };
    unknown_keys(obj, RECOVERY_KEYS, Some(step), "recovery", out);
    match obj.get("kind").and_then(Value::as_str) {
        Some("rollback") => {
            let run = string_list(obj.get("run"), step, "recovery.run", out);
            if run.is_empty() || obj.contains_key("path") {
                out.push(finding(
                    "P06",
                    Some(step),
                    "rollback needs its commands in `run` (and no `path`)".to_owned(),
                ));
                return None;
            }
            Some(Recovery::Rollback(run))
        }
        Some("unsupported") => match obj.get("path").and_then(Value::as_str) {
            Some(p) if !p.trim().is_empty() && !obj.contains_key("run") => {
                Some(Recovery::Unsupported(p.to_owned()))
            }
            _ => {
                out.push(finding(
                    "P06",
                    Some(step),
                    "unsupported needs the recovery path in `path` (and no `run`)".to_owned(),
                ));
                None
            }
        },
        _ => {
            out.push(finding(
                "P06",
                Some(step),
                "recovery.kind must be rollback or unsupported".to_owned(),
            ));
            None
        }
    }
}

fn parse_step(v: &Value, index: usize, out: &mut Vec<Finding>) -> Option<Step> {
    let label = format!("#{index}");
    let Some(obj) = v.as_object() else {
        out.push(finding(
            "P01",
            Some(label.as_str()),
            "step must be an object".to_owned(),
        ));
        return None;
    };
    let id = match obj.get("id").and_then(Value::as_str) {
        Some(s) if valid_id(s) => s.to_owned(),
        Some(s) => {
            out.push(finding(
                "P02",
                Some(label.as_str()),
                format!(
                    "id {s:?} refused (A-Z a-z 0-9 . _ -, 1..=64, starts with a letter or digit)"
                ),
            ));
            return None;
        }
        None => {
            out.push(finding(
                "P02",
                Some(label.as_str()),
                "step has no id".to_owned(),
            ));
            return None;
        }
    };
    let before = out.len();
    unknown_keys(obj, STEP_KEYS, Some(id.as_str()), "step", out);

    let level = obj
        .get("level")
        .and_then(Value::as_str)
        .and_then(Level::parse);
    if level.is_none() {
        out.push(finding(
            "P03",
            Some(id.as_str()),
            "level must be L0, L1 or L2".to_owned(),
        ));
    }
    let kind = obj
        .get("kind")
        .and_then(Value::as_str)
        .and_then(Kind::parse);
    if kind.is_none() {
        out.push(finding(
            "P04",
            Some(id.as_str()),
            "kind must be check, run or manual".to_owned(),
        ));
    }
    let title = match obj.get("title").and_then(Value::as_str) {
        Some(s) if !s.trim().is_empty() => s.to_owned(),
        _ => {
            out.push(finding(
                "P01",
                Some(id.as_str()),
                "step needs a title".to_owned(),
            ));
            String::new()
        }
    };
    let requires = string_list(obj.get("requires"), &id, "requires", out);
    let apply = string_list(obj.get("apply"), &id, "apply", out);
    let destroys = match obj.get("destroys") {
        None => None,
        Some(Value::String(s)) if !s.trim().is_empty() => Some(s.clone()),
        Some(_) => {
            out.push(finding(
                "P01",
                Some(id.as_str()),
                "destroys must be a non-empty string".to_owned(),
            ));
            None
        }
    };
    if obj.get("note").is_some_and(|n| !n.is_string()) {
        out.push(finding(
            "P01",
            Some(id.as_str()),
            "note must be a string".to_owned(),
        ));
    }

    let mut verify = Vec::new();
    match obj.get("verify").and_then(Value::as_array) {
        Some(arr) if !arr.is_empty() => {
            for item in arr {
                if let Some(one) = parse_verify(item, &id, out) {
                    verify.push(one);
                }
            }
        }
        _ => out.push(finding(
            "P05",
            Some(id.as_str()),
            "step needs at least one verify (a command and what it must show)".to_owned(),
        )),
    }
    let recovery = parse_recovery(obj.get("recovery"), &id, out);

    if let (Some(level), Some(kind)) = (level, kind) {
        match kind {
            Kind::Check => {
                if level != Level::L0 {
                    out.push(finding(
                        "P04",
                        Some(id.as_str()),
                        "a check is L0".to_owned(),
                    ));
                }
                if !apply.is_empty() {
                    out.push(finding(
                        "P04",
                        Some(id.as_str()),
                        "a check has no apply".to_owned(),
                    ));
                }
                if destroys.is_some() {
                    out.push(finding(
                        "P04",
                        Some(id.as_str()),
                        "a check destroys nothing".to_owned(),
                    ));
                }
            }
            Kind::Run | Kind::Manual => {
                if level == Level::L0 {
                    out.push(finding(
                        "P04",
                        Some(id.as_str()),
                        "run / manual steps write: L1 or L2, not L0".to_owned(),
                    ));
                }
                if apply.is_empty() {
                    out.push(finding(
                        "P04",
                        Some(id.as_str()),
                        "run / manual steps need apply (commands, or instructions for a person)"
                            .to_owned(),
                    ));
                }
                if recovery == Some(Recovery::None) {
                    out.push(finding(
                        "P06",
                        Some(id.as_str()),
                        "a writing step needs recovery: rollback commands, or `unsupported` with the recovery path".to_owned(),
                    ));
                }
            }
        }
    }

    if out.len() != before {
        return None;
    }
    Some(Step {
        id,
        level: level?,
        kind: kind?,
        title,
        requires,
        apply,
        verify,
        recovery: recovery?,
        destroys,
    })
}

fn step_texts(step: &Step) -> Vec<&str> {
    let mut texts: Vec<&str> = Vec::new();
    texts.push(&step.title);
    texts.extend(step.apply.iter().map(String::as_str));
    for v in &step.verify {
        texts.push(&v.run);
        match &v.expect {
            Expect::Eq(s) | Expect::Contains(s) => texts.push(s),
            Expect::Rc(_) => {}
        }
    }
    match &step.recovery {
        Recovery::Rollback(run) => texts.extend(run.iter().map(String::as_str)),
        Recovery::Unsupported(path) => texts.push(path),
        Recovery::None => {}
    }
    if let Some(d) = &step.destroys {
        texts.push(d);
    }
    texts
}

/// Declaration-stable topological order: repeatedly take the first step, in the order the
/// spec lists them, whose requirements are all taken. `Err` holds the ids left in a cycle.
fn order(steps: &[Step]) -> Result<Vec<usize>, Vec<String>> {
    let mut done: BTreeSet<&str> = BTreeSet::new();
    let mut taken = vec![false; steps.len()];
    let mut out = Vec::with_capacity(steps.len());
    while out.len() < steps.len() {
        let next = steps
            .iter()
            .enumerate()
            .position(|(i, s)| !taken[i] && s.requires.iter().all(|r| done.contains(r.as_str())));
        match next {
            Some(i) => {
                taken[i] = true;
                done.insert(steps[i].id.as_str());
                out.push(i);
            }
            None => {
                return Err(steps
                    .iter()
                    .enumerate()
                    .filter(|(i, _)| !taken[*i])
                    .map(|(_, s)| s.id.clone())
                    .collect());
            }
        }
    }
    Ok(out)
}

/// Read a spec. Every problem is reported, not only the first.
pub fn lint(spec: &Value) -> Result<Spec, Vec<Finding>> {
    let mut out = Vec::new();
    let Some(top) = spec.as_object() else {
        return Err(vec![finding(
            "P01",
            None,
            "spec must be an object".to_owned(),
        )]);
    };
    unknown_keys(top, TOP_KEYS, None, "spec", &mut out);
    if top.get("version").and_then(Value::as_u64) != Some(VERSION) {
        out.push(finding("P01", None, format!("version must be {VERSION}")));
    }

    let mut vars: Vec<(String, String)> = Vec::new();
    match top.get("vars") {
        None => {}
        Some(Value::Object(map)) => {
            for (name, value) in map {
                if !valid_var_name(name) {
                    out.push(finding(
                        "P01",
                        None,
                        format!("variable name {name:?} refused (a-z 0-9 _)"),
                    ));
                    continue;
                }
                let text = match value {
                    Value::String(s) if !s.contains("{{") => s.clone(),
                    Value::Number(n) => n.to_string(),
                    Value::Bool(b) => b.to_string(),
                    _ => {
                        out.push(finding(
                            "P01",
                            None,
                            format!("variable {name}: value must be a string, number or boolean, without {{{{"),
                        ));
                        continue;
                    }
                };
                vars.push((name.clone(), text));
            }
        }
        Some(_) => out.push(finding("P01", None, "vars must be an object".to_owned())),
    }

    let mut steps: Vec<Step> = Vec::new();
    let mut raw_ids: BTreeSet<String> = BTreeSet::new();
    match top.get("steps").and_then(Value::as_array) {
        Some(arr) if !arr.is_empty() => {
            for (i, item) in arr.iter().enumerate() {
                if let Some(id) = item.get("id").and_then(Value::as_str) {
                    if !raw_ids.insert(id.to_owned()) {
                        out.push(finding(
                            "P02",
                            Some(id),
                            "id used more than once".to_owned(),
                        ));
                        continue;
                    }
                }
                if let Some(step) = parse_step(item, i, &mut out) {
                    steps.push(step);
                }
            }
        }
        _ => out.push(finding(
            "P01",
            None,
            "steps must be a non-empty list".to_owned(),
        )),
    }

    for step in &steps {
        for text in step_texts(step) {
            match placeholders(text) {
                Ok(names) => {
                    for name in names {
                        if !vars.iter().any(|(n, _)| n == name) {
                            out.push(finding(
                                "P07",
                                Some(step.id.as_str()),
                                format!("{{{{{name}}}}} is not defined in vars"),
                            ));
                        }
                    }
                }
                Err(why) => out.push(finding("P07", Some(step.id.as_str()), why)),
            }
            for (name, value) in &vars {
                if value.len() >= LITERAL_MIN && text.contains(value.as_str()) {
                    out.push(finding(
                        "P08",
                        Some(step.id.as_str()),
                        format!("the value of {name} is written out; use {{{{{name}}}}}"),
                    ));
                }
            }
        }
        for req in &step.requires {
            if req == &step.id {
                out.push(finding(
                    "P09",
                    Some(step.id.as_str()),
                    "requires itself".to_owned(),
                ));
            } else if !raw_ids.contains(req) {
                out.push(finding(
                    "P09",
                    Some(step.id.as_str()),
                    format!("requires unknown step {req:?}"),
                ));
            }
        }
    }

    if !out.is_empty() {
        return Err(out);
    }
    match order(&steps) {
        Ok(order) => Ok(Spec { vars, steps, order }),
        Err(left) => Err(vec![finding(
            "P09",
            None,
            format!("requires form a cycle among: {}", left.join(", ")),
        )]),
    }
}

/// One step of a plan: variables filled in, ready to show to a person or to run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanStep {
    /// Step id.
    pub id: String,
    /// Approval level.
    pub level: Level,
    /// Check, run or manual.
    pub kind: Kind,
    /// One line for a person.
    pub title: String,
    /// Commands or instructions.
    pub apply: Vec<String>,
    /// Checks.
    pub verify: Vec<Verify>,
    /// The way back.
    pub recovery: Recovery,
    /// What the step destroys.
    pub destroys: Option<String>,
}

/// An ordered plan and the hash an approval is bound to (§1.3: an approval covers this
/// plan and no other; any change to a step, a variable or the starting point changes it).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    steps: Vec<PlanStep>,
    hash: String,
}

impl Plan {
    /// Steps in the order they run.
    pub fn steps(&self) -> &[PlanStep] {
        &self.steps
    }

    /// 16 hex digits over the rendered steps.
    pub fn hash(&self) -> &str {
        &self.hash
    }

    /// Ids of the steps a person must approve.
    pub fn needs_approval(&self) -> Vec<&str> {
        self.steps
            .iter()
            .filter(|s| s.level == Level::L2)
            .map(|s| s.id.as_str())
            .collect()
    }

    /// The plan as JSON: `{"plan_hash", "steps": [...]}`.
    pub fn to_json(&self) -> Value {
        json!({ "plan_hash": self.hash, "steps": steps_json(&self.steps) })
    }
}

fn steps_json(steps: &[PlanStep]) -> Value {
    Value::Array(
        steps
            .iter()
            .map(|s| {
                let verify: Vec<Value> = s
                    .verify
                    .iter()
                    .map(|v| {
                        let expect = match &v.expect {
                            Expect::Rc(n) => json!({ "rc": n }),
                            Expect::Eq(t) => json!({ "eq": t }),
                            Expect::Contains(t) => json!({ "contains": t }),
                        };
                        json!({ "run": v.run, "expect": expect, "active": v.active })
                    })
                    .collect();
                let recovery = match &s.recovery {
                    Recovery::None => json!({ "kind": "none" }),
                    Recovery::Rollback(run) => json!({ "kind": "rollback", "run": run }),
                    Recovery::Unsupported(path) => json!({ "kind": "unsupported", "path": path }),
                };
                json!({
                    "id": s.id,
                    "level": s.level.as_str(),
                    "kind": s.kind.as_str(),
                    "title": s.title,
                    "apply": s.apply,
                    "verify": verify,
                    "recovery": recovery,
                    "destroys": s.destroys,
                })
            })
            .collect(),
    )
}

/// Order the steps and fill in the variables. With `from`, the plan starts at that step
/// (in run order) and assumes everything before it is done; an unknown id is refused.
pub fn plan(spec: &Spec, from: Option<&str>) -> Result<Plan, Finding> {
    let mut start = 0;
    if let Some(from) = from {
        start = spec
            .order
            .iter()
            .position(|&i| spec.steps[i].id == from)
            .ok_or_else(|| finding("P09", None, format!("--from names unknown step {from:?}")))?;
    }
    let r = |t: &str| render(t, &spec.vars);
    let steps: Vec<PlanStep> = spec.order[start..]
        .iter()
        .map(|&i| {
            let s = &spec.steps[i];
            PlanStep {
                id: s.id.clone(),
                level: s.level,
                kind: s.kind,
                title: r(&s.title),
                apply: s.apply.iter().map(|t| r(t)).collect(),
                verify: s
                    .verify
                    .iter()
                    .map(|v| Verify {
                        run: r(&v.run),
                        expect: match &v.expect {
                            Expect::Rc(n) => Expect::Rc(*n),
                            Expect::Eq(t) => Expect::Eq(r(t)),
                            Expect::Contains(t) => Expect::Contains(r(t)),
                        },
                        active: v.active,
                    })
                    .collect(),
                recovery: match &s.recovery {
                    Recovery::None => Recovery::None,
                    Recovery::Rollback(run) => {
                        Recovery::Rollback(run.iter().map(|t| r(t)).collect())
                    }
                    Recovery::Unsupported(path) => Recovery::Unsupported(r(path)),
                },
                destroys: s.destroys.as_deref().map(&r),
            }
        })
        .collect();
    let hash = crate::isolation::fnv1a64(steps_json(&steps).to_string().as_bytes());
    Ok(Plan { steps, hash })
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn good() -> Value {
        json!({
            "version": 2,
            "vars": { "hostname": "host1", "public_ipv4": "203.0.113.10", "vg": "vg0" },
            "steps": [
                { "id": "B0.precheck", "level": "L0", "kind": "check", "title": "host is {{hostname}}",
                  "verify": [ { "run": "hostname -f", "expect": { "eq": "{{hostname}}" } } ] },
                { "id": "B2.repo", "level": "L1", "kind": "run", "title": "package source",
                  "requires": ["B0.precheck"],
                  "apply": ["apt-get update"],
                  "verify": [ { "run": "apt-cache policy proxmox-ve", "expect": { "rc": 0 } } ],
                  "recovery": { "kind": "rollback", "run": ["rm /etc/apt/sources.list.d/pve.sources"] } },
                { "id": "A.reinstall", "level": "L2", "kind": "manual", "title": "reinstall",
                  "requires": ["B2.repo"],
                  "apply": ["boot the rescue system and run installimage"],
                  "verify": [ { "run": "ping -c1 {{public_ipv4}}", "expect": { "rc": 0 }, "active": true } ],
                  "recovery": { "kind": "unsupported", "path": "install again" },
                  "destroys": "everything on both disks" }
            ]
        })
    }

    fn codes(spec: &Value) -> Vec<&'static str> {
        lint(spec).unwrap_err().iter().map(|f| f.code).collect()
    }

    fn step_mut(spec: &mut Value, i: usize) -> &mut Map<String, Value> {
        spec["steps"][i].as_object_mut().unwrap()
    }

    #[test]
    fn good_spec_is_accepted_and_ordered() {
        let spec = lint(&good()).unwrap();
        assert_eq!(spec.steps().len(), 3);
        let plan = plan(&spec, None).unwrap();
        let ids: Vec<&str> = plan.steps().iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["B0.precheck", "B2.repo", "A.reinstall"]);
        assert_eq!(plan.needs_approval(), ["A.reinstall"]);
        assert_eq!(plan.hash().len(), 16);
    }

    #[test]
    fn plan_fills_variables_everywhere() {
        let spec = lint(&good()).unwrap();
        let plan = plan(&spec, None).unwrap();
        assert_eq!(plan.steps()[0].title, "host is host1");
        assert_eq!(
            plan.steps()[0].verify[0].expect,
            Expect::Eq("host1".to_owned())
        );
        assert_eq!(plan.steps()[2].verify[0].run, "ping -c1 203.0.113.10");
        assert!(plan.steps()[2].verify[0].active);
        assert!(!plan.to_json().to_string().contains("{{"));
    }

    #[test]
    fn order_follows_requires_not_position() {
        let mut v = good();
        let steps = v["steps"].as_array_mut().unwrap();
        steps.reverse();
        let spec = lint(&v).unwrap();
        let plan = plan(&spec, None).unwrap();
        let ids: Vec<&str> = plan.steps().iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["B0.precheck", "B2.repo", "A.reinstall"]);
    }

    #[test]
    fn from_starts_there_and_changes_the_hash() {
        let spec = lint(&good()).unwrap();
        let all = plan(&spec, None).unwrap();
        let tail = plan(&spec, Some("B2.repo")).unwrap();
        assert_eq!(tail.steps().len(), 2);
        assert_ne!(all.hash(), tail.hash());
        assert_eq!(plan(&spec, Some("nope")).unwrap_err().code, "P09");
    }

    #[test]
    fn hash_moves_with_a_variable_and_with_a_command() {
        let base = plan(&lint(&good()).unwrap(), None).unwrap();
        let mut v = good();
        v["vars"]["hostname"] = json!("host2");
        assert_ne!(plan(&lint(&v).unwrap(), None).unwrap().hash(), base.hash());
        let mut v = good();
        step_mut(&mut v, 1).insert("apply".to_owned(), json!(["apt-get update -q"]));
        assert_ne!(plan(&lint(&v).unwrap(), None).unwrap().hash(), base.hash());
        assert_eq!(
            plan(&lint(&good()).unwrap(), None).unwrap().hash(),
            base.hash()
        );
    }

    #[test]
    fn p01_shape_and_version() {
        assert_eq!(codes(&json!([])), ["P01"]);
        let mut v = good();
        v["version"] = json!(1);
        assert_eq!(codes(&v), ["P01"]);
        let mut v = good();
        v["steps"] = json!([]);
        assert_eq!(codes(&v), ["P01"]);
        let mut v = good();
        v["vars"]["hostname"] = json!(["a"]);
        assert!(codes(&v).contains(&"P01"));
    }

    #[test]
    fn p02_duplicate_and_malformed_ids() {
        let mut v = good();
        step_mut(&mut v, 1).insert("id".to_owned(), json!("B0.precheck"));
        assert!(codes(&v).contains(&"P02"));
        let mut v = good();
        step_mut(&mut v, 0).insert("id".to_owned(), json!("has space"));
        assert!(codes(&v).contains(&"P02"));
        let mut v = good();
        step_mut(&mut v, 0).remove("id");
        assert!(codes(&v).contains(&"P02"));
    }

    #[test]
    fn p03_p04_level_and_kind() {
        let mut v = good();
        step_mut(&mut v, 0).insert("level".to_owned(), json!("L3"));
        assert_eq!(codes(&v), ["P03"]);
        let mut v = good();
        step_mut(&mut v, 0).insert("kind".to_owned(), json!("cmd"));
        assert_eq!(codes(&v), ["P04"]);
        // a check that claims to write, and a write that claims to be read-only
        let mut v = good();
        step_mut(&mut v, 0).insert("level".to_owned(), json!("L1"));
        assert_eq!(codes(&v), ["P04"]);
        let mut v = good();
        step_mut(&mut v, 0).insert("apply".to_owned(), json!(["rm -rf /tmp/x"]));
        assert_eq!(codes(&v), ["P04"]);
        let mut v = good();
        step_mut(&mut v, 1).insert("level".to_owned(), json!("L0"));
        assert_eq!(codes(&v), ["P04"]);
        let mut v = good();
        step_mut(&mut v, 1).remove("apply");
        assert_eq!(codes(&v), ["P04"]);
    }

    #[test]
    fn p05_verify_must_be_decidable() {
        let mut v = good();
        step_mut(&mut v, 1).remove("verify");
        assert_eq!(codes(&v), ["P05"]);
        let mut v = good();
        step_mut(&mut v, 1).insert("verify".to_owned(), json!(["rc 0"]));
        assert_eq!(codes(&v), ["P05"]);
        let mut v = good();
        step_mut(&mut v, 1).insert(
            "verify".to_owned(),
            json!([{ "run": "true", "expect": { "rc": 0, "eq": "x" } }]),
        );
        assert_eq!(codes(&v), ["P05"]);
        let mut v = good();
        step_mut(&mut v, 1).insert(
            "verify".to_owned(),
            json!([{ "run": "true", "expect": { "rc": "zero" } }]),
        );
        assert_eq!(codes(&v), ["P05"]);
    }

    #[test]
    fn p06_writes_need_an_explicit_way_back() {
        let mut v = good();
        step_mut(&mut v, 1).remove("recovery");
        assert_eq!(codes(&v), ["P06"]);
        let mut v = good();
        step_mut(&mut v, 1).insert("recovery".to_owned(), json!({ "kind": "rollback" }));
        assert_eq!(codes(&v), ["P06"]);
        let mut v = good();
        step_mut(&mut v, 2).insert("recovery".to_owned(), json!({ "kind": "unsupported" }));
        assert_eq!(codes(&v), ["P06"]);
        let mut v = good();
        step_mut(&mut v, 2).insert("recovery".to_owned(), json!({ "kind": "none" }));
        assert_eq!(codes(&v), ["P06"]);
    }

    #[test]
    fn p07_undefined_and_unclosed_placeholders() {
        let mut v = good();
        step_mut(&mut v, 1).insert("apply".to_owned(), json!(["echo {{nope}}"]));
        assert_eq!(codes(&v), ["P07"]);
        let mut v = good();
        step_mut(&mut v, 1).insert("apply".to_owned(), json!(["echo {{hostname"]));
        assert_eq!(codes(&v), ["P07"]);
    }

    #[test]
    fn p08_host_values_are_not_written_out() {
        let mut v = good();
        step_mut(&mut v, 1).insert("apply".to_owned(), json!(["curl http://203.0.113.10/"]));
        assert_eq!(codes(&v), ["P08"]);
        // short values are ordinary words: "vg0" in a command is not a finding
        let mut v = good();
        step_mut(&mut v, 1).insert("apply".to_owned(), json!(["vgs vg0"]));
        assert!(lint(&v).is_ok());
    }

    #[test]
    fn p09_requires_unknown_self_and_cycle() {
        let mut v = good();
        step_mut(&mut v, 1).insert("requires".to_owned(), json!(["nope"]));
        assert_eq!(codes(&v), ["P09"]);
        let mut v = good();
        step_mut(&mut v, 1).insert("requires".to_owned(), json!(["B2.repo"]));
        assert_eq!(codes(&v), ["P09"]);
        let mut v = good();
        step_mut(&mut v, 0).insert("requires".to_owned(), json!(["A.reinstall"]));
        let found = lint(&v).unwrap_err();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "P09");
        assert!(found[0].msg.contains("cycle"));
    }

    #[test]
    fn p10_unknown_keys_are_typos() {
        let mut v = good();
        step_mut(&mut v, 1).insert("verfy".to_owned(), json!([]));
        assert_eq!(codes(&v), ["P10"]);
        let mut v = good();
        v["stages"] = json!([]);
        assert_eq!(codes(&v), ["P10"]);
    }

    #[test]
    fn every_problem_is_reported_at_once() {
        let mut v = good();
        step_mut(&mut v, 0).insert("level".to_owned(), json!("L9"));
        step_mut(&mut v, 1).remove("recovery");
        step_mut(&mut v, 2).insert("apply".to_owned(), json!(["echo {{nope}}"]));
        let got = codes(&v);
        assert!(
            got.contains(&"P03") && got.contains(&"P06") && got.contains(&"P07"),
            "{got:?}"
        );
    }

    #[test]
    fn finding_prints_code_step_and_reason() {
        let mut v = good();
        step_mut(&mut v, 1).remove("recovery");
        let found = lint(&v).unwrap_err();
        let line = found[0].to_string();
        assert!(line.starts_with("P06 [B2.repo] "), "{line}");
    }
}
