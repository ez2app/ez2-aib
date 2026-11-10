// SPDX-License-Identifier: Apache-2.0
//! Host provisioning as data (4.4.1 §22 / §24-4: `host spec lint` and the plan half of
//! `host provision --spec F [--from <step>]`).
//!
//! A provisioning spec is the machine-readable description of how a bare host becomes a
//! host that can carry sites: steps, each with its approval level, what it runs, how the
//! result is checked and what the way back is. This module reads such a spec (as a JSON
//! value; the caller owns the file and the file system), refuses anything a one-command
//! provision could not act on, and turns an accepted spec into an ordered [`Plan`] with an
//! identifier. No I/O here: nothing is run.
//!
//! Spec, version 2:
//!
//! ```json
//! { "version": 2,
//!   "vars": { "hostname": "host1", "public_ipv4": "203.0.113.10" },
//!   "steps": [
//!     { "id": "B0.precheck", "level": "L0", "kind": "check", "title": "…",
//!       "verify": [ { "run": ["hostname", "-f"], "expect": { "eq": "{{hostname}}" } } ] },
//!     { "id": "B2.repo", "level": "L1", "kind": "run", "title": "…",
//!       "requires": ["B0.precheck"],
//!       "apply": [ ["apt-get", "update"] ],
//!       "verify": [ { "run": ["apt-cache", "policy", "proxmox-ve"], "expect": { "rc": 0 } } ],
//!       "recovery": { "kind": "rollback",
//!                     "run": [ ["rm", "/etc/apt/sources.list.d/pve.sources"] ] } }
//!   ] }
//! ```
//!
//! A command is an **argument list**, never a shell line: `["pvesm", "status"]`. A variable
//! fills in part of one argument and cannot add arguments. A secret is a reference,
//! `{"secret": "name"}`, resolved outside this crate at run time; its value never enters a
//! spec or a plan. When a shell is really needed the command says so and says why:
//! `{"sh": "a | b", "why": "…"}` — and variables used inside it must hold plain values.
//! For a `manual` step, `apply` is the instructions for a person, as text.
//!
//! A spec holds no progress: which steps a given host has done is that host's state, kept
//! elsewhere and re-verified, never trusted. The `level` a step states is a floor that
//! this module raises where it must (`P03`); what a level *allows* is the caller's policy.
//! [`Plan::id`] identifies a plan (same spec, same variables, same starting point ⇒ same
//! id); it is not a cryptographic hash and must not be the only thing an approval is
//! bound to — the caller hashes [`Plan::to_json`] together with the target host.
//!
//! Two things are the reader's job, because a parsed JSON value no longer shows them: an
//! object with the same key twice must be refused when the file is read (the second would
//! silently win), and digests in a spec are written in hex — a base64 digest is long
//! enough to be taken for key material by `P11`.
//!
//! What [`lint`] refuses (every finding carries a stable code):
//! * `P01` wrong shape or version; `P10` a key this version does not know (typos, and the
//!   `status` lines of a hand-kept ledger).
//! * `P02` step id missing, malformed or used twice.
//! * `P03` level unknown, or too low: a step that destroys something or reboots is `L2`.
//! * `P04` kind unknown, or a pair that makes no sense (`check` is `L0`, runs nothing and
//!   disturbs nothing; `run` / `manual` write, so they are `L1` or `L2` and say what to do).
//! * `P05` a step without a decidable verify (a command plus exactly one expectation).
//! * `P06` a writing step without an explicit way back: `rollback` with commands, `rescue`
//!   (a person and the rescue system) or `unsupported`, both with the path spelled out.
//! * `P07` a `{{name}}` that `vars` does not define, an unclosed `{{`, or a variable used
//!   inside a shell command whose value is not plain (`A-Z a-z 0-9 . _ : / @ , = + -`).
//! * `P08` a host-specific value written out in a step instead of `{{name}}`.
//! * `P09` `requires` naming an unknown step, itself, or forming a cycle.
//! * `P11` a malformed command, a shell command without `why`, or text that looks like a
//!   secret written out.
//! * `P12` a step that cuts the connection without saying how long to wait; a network
//!   change without a timed automatic revert; an `L2` step without the exact sentence the
//!   approver confirms (`consent`).

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
/// Longest wait for a host to come back, and longest timed revert, in seconds.
const WAIT_MAX: u64 = 3600;

/// One reason a spec was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// Stable rule code, `P01` … `P12`.
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
    /// Something only a person can do (a rescue boot, a password, a hardware token): the
    /// tool shows the instructions and stops there until the verify passes.
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

/// One argument of a command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Arg {
    /// Literal text (variables filled in once the step is in a [`Plan`]).
    Text(String),
    /// A secret, by name. Resolved outside this crate when the command runs.
    Secret(String),
}

/// One command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// A program and its arguments; no shell is involved.
    Argv(Vec<Arg>),
    /// A shell line, with the reason a shell is needed.
    Shell {
        /// The line.
        sh: String,
        /// Why an argument list would not do.
        why: String,
    },
}

/// What a step does to the connection it is run over.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Disruption {
    /// Nothing.
    None,
    /// The network path may drop: a timed revert is armed on the host first and disarmed
    /// only after a new connection verifies.
    Network {
        /// Seconds to wait for the host to answer again.
        reconnect_timeout_s: u32,
        /// Seconds after which the host reverts the change by itself.
        auto_revert_s: u32,
    },
    /// The host reboots.
    Reboot {
        /// Seconds to wait for the host to answer again.
        reconnect_timeout_s: u32,
    },
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
    pub run: Command,
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
    Rollback(Vec<Command>),
    /// A person has to repair it from the rescue system; this is how, in words.
    Rescue(String),
    /// No rollback exists; this is the recovery path, in words.
    Unsupported(String),
}

/// One accepted step. In a [`Spec`] variables are still `{{name}}`; in a [`Plan`] they
/// are filled in.
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
    /// Commands of a `run` step; empty otherwise.
    pub apply: Vec<Command>,
    /// Instructions of a `manual` step, for a person; empty otherwise.
    pub instructions: Vec<String>,
    /// At least one.
    pub verify: Vec<Verify>,
    /// The way back.
    pub recovery: Recovery,
    /// What the step destroys, if anything, in words.
    pub destroys: Option<String>,
    /// What the step does to the connection.
    pub disruption: Disruption,
    /// `true`: started as a unit on the host and polled, so a dropped connection does not
    /// stop it half way.
    pub detach: bool,
    /// The exact sentence the approver confirms; present on every `L2` step.
    pub consent: Option<String>,
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
    "id",
    "level",
    "kind",
    "title",
    "requires",
    "apply",
    "verify",
    "recovery",
    "destroys",
    "note",
    "disruption",
    "detach",
    "consent",
];
const VERIFY_KEYS: &[&str] = &["run", "expect", "active"];
const EXPECT_KEYS: &[&str] = &["rc", "eq", "contains"];
const RECOVERY_KEYS: &[&str] = &["kind", "run", "path"];
const DISRUPTION_KEYS: &[&str] = &["kind", "reconnect_timeout_s", "auto_revert_s"];
const SHELL_KEYS: &[&str] = &["sh", "why"];

/// Findings of one step, collected with the step's id attached.
struct Sink<'a> {
    step: &'a str,
    out: &'a mut Vec<Finding>,
}

impl Sink<'_> {
    fn add(&mut self, code: &'static str, msg: String) {
        self.out.push(Finding {
            code,
            step: Some(self.step.to_owned()),
            msg,
        });
    }

    fn unknown_keys(&mut self, obj: &Map<String, Value>, known: &[&str], what: &str) {
        for k in obj.keys() {
            if !known.contains(&k.as_str()) {
                self.add("P10", format!("{what}: unknown key {k:?}"));
            }
        }
    }
}

fn top(code: &'static str, msg: String) -> Finding {
    Finding {
        code,
        step: None,
        msg,
    }
}

fn name_ok(name: &str, max: usize, extra: &[u8]) -> bool {
    !name.is_empty()
        && name.len() <= max
        && name
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || extra.contains(&c))
}

fn valid_id(id: &str) -> bool {
    name_ok(id, 64, b"._-") && id.as_bytes()[0].is_ascii_alphanumeric()
}

fn valid_var_name(name: &str) -> bool {
    name_ok(name, 40, b"_") && !name.bytes().any(|c| c.is_ascii_uppercase())
}

fn valid_secret_name(name: &str) -> bool {
    name_ok(name, 64, b"._-")
}

/// A value that can sit inside a shell line without changing its meaning.
fn shell_plain(value: &str) -> bool {
    value
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || b"._:/@,=+-".contains(&c))
}

/// Text that looks like key material written out: a PEM header, or a run of 40 or more
/// key-alphabet characters that mixes letters and digits and is not plain hex (checksums
/// are hex and belong in specs).
fn looks_secret(text: &str) -> bool {
    text.contains("-----BEGIN ")
        || text
            .split(|c: char| !(c.is_ascii_alphanumeric() || "+=_".contains(c)))
            .any(|run| {
                run.len() >= 40
                    && run.bytes().any(|c| c.is_ascii_digit())
                    && run.bytes().any(|c| c.is_ascii_alphabetic())
                    && !run.bytes().all(|c| c.is_ascii_hexdigit())
            })
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

/// Fill in the variables in one pass over `text`: what a value contains is never read
/// again, so a value cannot combine with the text after it into a second placeholder.
/// Anything [`placeholders`] would refuse is copied as it stands (lint has refused it).
fn render(text: &str, vars: &[(String, String)]) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find("{{") {
        let after = &rest[start + 2..];
        let known = after.find("}}").and_then(|end| {
            let name = &after[..end];
            vars.iter()
                .find(|(n, _)| n == name)
                .map(|(_, value)| (end, value))
        });
        match known {
            Some((end, value)) => {
                out.push_str(&rest[..start]);
                out.push_str(value);
                rest = &after[end + 2..];
            }
            None => {
                out.push_str(&rest[..start + 2]);
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

fn string_list(v: Option<&Value>, what: &str, sink: &mut Sink<'_>) -> Vec<String> {
    let Some(v) = v else {
        return Vec::new();
    };
    let Some(arr) = v.as_array() else {
        sink.add("P01", format!("{what} must be a list of strings"));
        return Vec::new();
    };
    let mut items = Vec::new();
    for item in arr {
        match item.as_str() {
            Some(s) if !s.trim().is_empty() => items.push(s.to_owned()),
            _ => sink.add(
                "P01",
                format!("{what}: every entry must be a non-empty string"),
            ),
        }
    }
    items
}

fn parse_arg(item: &Value) -> Option<Arg> {
    match item {
        Value::String(s) => Some(Arg::Text(s.clone())),
        Value::Object(o) if o.len() == 1 => o
            .get("secret")
            .and_then(Value::as_str)
            .filter(|n| valid_secret_name(n))
            .map(|n| Arg::Secret(n.to_owned())),
        _ => None,
    }
}

fn parse_command(v: &Value, what: &str, sink: &mut Sink<'_>) -> Option<Command> {
    match v {
        Value::Array(items) if !items.is_empty() => {
            let args: Option<Vec<Arg>> = items.iter().map(parse_arg).collect();
            match args {
                Some(args) if matches!(args.first(), Some(Arg::Text(p)) if !p.trim().is_empty()) => {
                    Some(Command::Argv(args))
                }
                _ => {
                    sink.add(
                        "P11",
                        format!(
                            "{what}: arguments are strings or {{\"secret\": \"name\"}}, and the first is the program"
                        ),
                    );
                    None
                }
            }
        }
        Value::Object(o) => {
            sink.unknown_keys(o, SHELL_KEYS, what);
            let text = |k: &str| {
                o.get(k)
                    .and_then(Value::as_str)
                    .filter(|s| !s.trim().is_empty())
            };
            match (text("sh"), text("why")) {
                (Some(sh), Some(why)) => Some(Command::Shell {
                    sh: sh.to_owned(),
                    why: why.to_owned(),
                }),
                _ => {
                    sink.add(
                        "P11",
                        format!("{what}: a shell command needs both `sh` and `why`"),
                    );
                    None
                }
            }
        }
        _ => {
            sink.add(
                "P11",
                format!("{what}: a command is a non-empty argument list, or {{\"sh\", \"why\"}}"),
            );
            None
        }
    }
}

fn command_list(v: Option<&Value>, what: &str, sink: &mut Sink<'_>) -> Vec<Command> {
    let Some(v) = v else {
        return Vec::new();
    };
    let Some(arr) = v.as_array() else {
        sink.add("P11", format!("{what} must be a list of commands"));
        return Vec::new();
    };
    let mut out = Vec::new();
    for item in arr {
        if let Some(c) = parse_command(item, what, sink) {
            out.push(c);
        }
    }
    out
}

fn seconds(obj: &Map<String, Value>, key: &str) -> Option<u32> {
    obj.get(key)
        .and_then(Value::as_u64)
        .filter(|n| (1..=WAIT_MAX).contains(n))
        .and_then(|n| u32::try_from(n).ok())
}

fn parse_disruption(v: Option<&Value>, sink: &mut Sink<'_>) -> Option<Disruption> {
    let Some(v) = v else {
        return Some(Disruption::None);
    };
    let parsed = v.as_object().and_then(|obj| {
        sink.unknown_keys(obj, DISRUPTION_KEYS, "disruption");
        let wait = seconds(obj, "reconnect_timeout_s");
        let revert = seconds(obj, "auto_revert_s");
        match obj.get("kind").and_then(Value::as_str) {
            Some("none") if obj.len() == 1 => Some(Disruption::None),
            Some("network") => Some(Disruption::Network {
                reconnect_timeout_s: wait?,
                auto_revert_s: revert?,
            }),
            Some("reboot") if !obj.contains_key("auto_revert_s") => Some(Disruption::Reboot {
                reconnect_timeout_s: wait?,
            }),
            _ => None,
        }
    });
    if parsed.is_none() {
        sink.add(
            "P12",
            format!(
                "disruption: none | network (reconnect_timeout_s and auto_revert_s) | reboot (reconnect_timeout_s); seconds 1..={WAIT_MAX}"
            ),
        );
    }
    parsed
}

fn parse_verify(v: &Value, sink: &mut Sink<'_>) -> Option<Verify> {
    let Some(obj) = v.as_object() else {
        sink.add("P05", "verify entry must be an object".to_owned());
        return None;
    };
    sink.unknown_keys(obj, VERIFY_KEYS, "verify");
    let Some(run) = obj.get("run") else {
        sink.add("P05", "verify needs a command in `run`".to_owned());
        return None;
    };
    let run = parse_command(run, "verify.run", sink)?;
    let active = match obj.get("active") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => {
            sink.add("P05", "verify `active` must be true or false".to_owned());
            return None;
        }
    };
    let expect = obj
        .get("expect")
        .and_then(Value::as_object)
        .filter(|exp| exp.len() == 1)
        .and_then(|exp| {
            sink.unknown_keys(exp, EXPECT_KEYS, "expect");
            if let Some(rc) = exp.get("rc") {
                rc.as_i64().map(Expect::Rc)
            } else if let Some(eq) = exp.get("eq") {
                eq.as_str().map(|s| Expect::Eq(s.to_owned()))
            } else {
                exp.get("contains")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(|s| Expect::Contains(s.to_owned()))
            }
        });
    match expect {
        Some(expect) => Some(Verify {
            run,
            expect,
            active,
        }),
        None => {
            sink.add(
                "P05",
                "verify needs `expect` with exactly one of rc (integer) / eq (string) / contains (non-empty string)"
                    .to_owned(),
            );
            None
        }
    }
}

fn parse_recovery(v: Option<&Value>, sink: &mut Sink<'_>) -> Option<Recovery> {
    let Some(v) = v else {
        return Some(Recovery::None);
    };
    let Some(obj) = v.as_object() else {
        sink.add("P06", "recovery must be an object".to_owned());
        return None;
    };
    sink.unknown_keys(obj, RECOVERY_KEYS, "recovery");
    let path = obj
        .get("path")
        .and_then(Value::as_str)
        .filter(|p| !p.trim().is_empty() && !obj.contains_key("run"));
    let parsed = match obj.get("kind").and_then(Value::as_str) {
        Some("rollback") if !obj.contains_key("path") => {
            let run = command_list(obj.get("run"), "recovery.run", sink);
            (!run.is_empty()).then_some(Recovery::Rollback(run))
        }
        Some("rescue") => path.map(|p| Recovery::Rescue(p.to_owned())),
        Some("unsupported") => path.map(|p| Recovery::Unsupported(p.to_owned())),
        _ => None,
    };
    if parsed.is_none() {
        sink.add(
            "P06",
            "recovery: rollback with commands in `run`, or rescue / unsupported with the path in `path`"
                .to_owned(),
        );
    }
    parsed
}

fn parse_step(v: &Value, index: usize, out: &mut Vec<Finding>) -> Option<Step> {
    let label = format!("#{index}");
    let Some(obj) = v.as_object() else {
        out.push(Finding {
            code: "P01",
            step: Some(label),
            msg: "step must be an object".to_owned(),
        });
        return None;
    };
    let id = match obj.get("id").and_then(Value::as_str) {
        Some(s) if valid_id(s) => s.to_owned(),
        other => {
            out.push(Finding {
                code: "P02",
                step: Some(label),
                msg: match other {
                    Some(s) => format!(
                        "id {s:?} refused (A-Z a-z 0-9 . _ -, 1..=64, starts with a letter or digit)"
                    ),
                    None => "step has no id".to_owned(),
                },
            });
            return None;
        }
    };
    let before = out.len();
    let mut sink = Sink {
        step: id.as_str(),
        out,
    };
    sink.unknown_keys(obj, STEP_KEYS, "step");

    let level = obj
        .get("level")
        .and_then(Value::as_str)
        .and_then(Level::parse);
    if level.is_none() {
        sink.add("P03", "level must be L0, L1 or L2".to_owned());
    }
    let kind = obj
        .get("kind")
        .and_then(Value::as_str)
        .and_then(Kind::parse);
    if kind.is_none() {
        sink.add("P04", "kind must be check, run or manual".to_owned());
    }
    let title = match obj.get("title").and_then(Value::as_str) {
        Some(s) if !s.trim().is_empty() => s.to_owned(),
        _ => {
            sink.add("P01", "step needs a title".to_owned());
            String::new()
        }
    };
    let requires = string_list(obj.get("requires"), "requires", &mut sink);
    let (apply, instructions) = if kind == Some(Kind::Manual) {
        (
            Vec::new(),
            string_list(obj.get("apply"), "apply", &mut sink),
        )
    } else {
        (
            command_list(obj.get("apply"), "apply", &mut sink),
            Vec::new(),
        )
    };
    let has_apply = obj.contains_key("apply");
    let text_field = |key: &str, code: &'static str, sink: &mut Sink<'_>| match obj.get(key) {
        None => None,
        Some(Value::String(s)) if !s.trim().is_empty() => Some(s.clone()),
        Some(_) => {
            sink.add(code, format!("{key} must be a non-empty string"));
            None
        }
    };
    let destroys = text_field("destroys", "P01", &mut sink);
    let consent = text_field("consent", "P12", &mut sink);
    if obj.get("note").is_some_and(|n| !n.is_string()) {
        sink.add("P01", "note must be a string".to_owned());
    }
    let detach = match obj.get("detach") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => {
            sink.add("P01", "detach must be true or false".to_owned());
            false
        }
    };
    let disruption = parse_disruption(obj.get("disruption"), &mut sink);

    let mut verify = Vec::new();
    match obj.get("verify").and_then(Value::as_array) {
        Some(arr) if !arr.is_empty() => {
            for item in arr {
                if let Some(one) = parse_verify(item, &mut sink) {
                    verify.push(one);
                }
            }
        }
        _ => sink.add(
            "P05",
            "step needs at least one verify (a command and what it must show)".to_owned(),
        ),
    }
    let recovery = parse_recovery(obj.get("recovery"), &mut sink);

    if let (Some(level), Some(kind)) = (level, kind) {
        let disturbs = disruption.is_some_and(|d| d != Disruption::None);
        match kind {
            Kind::Check => {
                if level != Level::L0 {
                    sink.add("P04", "a check is L0".to_owned());
                }
                if has_apply || destroys.is_some() || disturbs || detach {
                    sink.add(
                        "P04",
                        "a check has no apply, destroys nothing and disturbs nothing".to_owned(),
                    );
                }
            }
            Kind::Run | Kind::Manual => {
                if level == Level::L0 {
                    sink.add(
                        "P04",
                        "run / manual steps write: L1 or L2, not L0".to_owned(),
                    );
                }
                if apply.is_empty() && instructions.is_empty() {
                    sink.add(
                        "P04",
                        "run steps need commands in apply; manual steps need instructions"
                            .to_owned(),
                    );
                }
                if recovery == Some(Recovery::None) {
                    sink.add(
                        "P06",
                        "a writing step needs recovery: rollback, rescue or unsupported".to_owned(),
                    );
                }
            }
        }
        let reboots = matches!(disruption, Some(Disruption::Reboot { .. }));
        if level != Level::L2 && kind != Kind::Check && (destroys.is_some() || reboots) {
            sink.add(
                "P03",
                "a step that destroys something or reboots is L2".to_owned(),
            );
        }
        if level == Level::L2 && consent.is_none() {
            sink.add(
                "P12",
                "an L2 step needs `consent`: the exact sentence the approver confirms".to_owned(),
            );
        }
        if level != Level::L2 && consent.is_some() {
            sink.add("P12", "consent belongs to L2 steps only".to_owned());
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
        instructions,
        verify,
        recovery: recovery?,
        destroys,
        disruption: disruption?,
        detach,
        consent,
    })
}

/// How a piece of a step's text is used.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Use {
    /// Read by a person (title, instructions, expectations, paths in words).
    Prose,
    /// One argument of a command.
    Argv,
    /// A shell line.
    Shell,
}

/// A value that names the root directory however it is spelled: `/`, `//`, `/.`, `/./`.
/// Inside a shell line `chown -R x {{dir}}/*` with such a value reaches everything.
fn is_root(value: &str) -> bool {
    value.starts_with('/') && value.bytes().all(|c| c == b'/' || c == b'.')
}

/// Every piece of text in a step, with how it is used.
fn step_texts(step: &Step) -> Vec<(&str, Use)> {
    fn command<'a>(c: &'a Command, texts: &mut Vec<(&'a str, Use)>) {
        match c {
            Command::Argv(args) => {
                for a in args {
                    if let Arg::Text(t) = a {
                        texts.push((t, Use::Argv));
                    }
                }
            }
            Command::Shell { sh, why } => {
                texts.push((sh, Use::Shell));
                texts.push((why, Use::Prose));
            }
        }
    }
    let mut texts: Vec<(&str, Use)> = Vec::new();
    texts.push((&step.title, Use::Prose));
    for c in &step.apply {
        command(c, &mut texts);
    }
    texts.extend(step.instructions.iter().map(|t| (t.as_str(), Use::Prose)));
    for v in &step.verify {
        command(&v.run, &mut texts);
        if let Expect::Eq(s) | Expect::Contains(s) = &v.expect {
            texts.push((s, Use::Prose));
        }
    }
    match &step.recovery {
        Recovery::Rollback(run) => {
            for c in run {
                command(c, &mut texts);
            }
        }
        Recovery::Rescue(path) | Recovery::Unsupported(path) => texts.push((path, Use::Prose)),
        Recovery::None => {}
    }
    for t in [&step.destroys, &step.consent].into_iter().flatten() {
        texts.push((t, Use::Prose));
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

fn parse_vars(v: Option<&Value>, out: &mut Vec<Finding>) -> Vec<(String, String)> {
    let mut vars = Vec::new();
    match v {
        None => {}
        Some(Value::Object(map)) => {
            for (name, value) in map {
                if !valid_var_name(name) {
                    out.push(top(
                        "P01",
                        format!("variable name {name:?} refused (a-z 0-9 _)"),
                    ));
                    continue;
                }
                let text = match value {
                    // an empty value is a variable somebody forgot to fill in: `{{dir}}/cache`
                    // would quietly become `/cache`
                    Value::String(s) if !s.is_empty() && !s.contains("{{") => s.clone(),
                    Value::Number(n) => n.to_string(),
                    Value::Bool(b) => b.to_string(),
                    _ => {
                        out.push(top(
                            "P01",
                            format!(
                                "variable {name}: value must be a string, number or boolean, without {{{{"
                            ),
                        ));
                        continue;
                    }
                };
                if looks_secret(&text) {
                    out.push(top(
                        "P11",
                        format!("variable {name} looks like a secret; use a secret reference"),
                    ));
                }
                vars.push((name.clone(), text));
            }
        }
        Some(_) => out.push(top("P01", "vars must be an object".to_owned())),
    }
    vars
}

fn check_texts(
    step: &Step,
    vars: &[(String, String)],
    ids: &BTreeSet<String>,
    out: &mut Vec<Finding>,
) {
    let mut sink = Sink {
        step: step.id.as_str(),
        out,
    };
    for (text, used) in step_texts(step) {
        match placeholders(text) {
            Ok(names) => {
                for name in names {
                    match vars.iter().find(|(n, _)| n == name) {
                        None => sink.add("P07", format!("{{{{{name}}}}} is not defined in vars")),
                        // a value that starts with '-' is read as an option by whatever
                        // program receives it
                        Some((_, value)) if used != Use::Prose && value.starts_with('-') => sink.add(
                            "P07",
                            format!("{{{{{name}}}}}: a value that starts with '-' would be read as an option"),
                        ),
                        Some((_, value)) if used == Use::Shell && (!shell_plain(value) || is_root(value)) => sink.add(
                            "P07",
                            format!("{{{{{name}}}}} is used in a shell command but its value is not plain"),
                        ),
                        Some(_) => {}
                    }
                }
            }
            Err(why) => sink.add("P07", why),
        }
        for (name, value) in vars {
            if value.len() >= LITERAL_MIN && text.contains(value.as_str()) {
                sink.add(
                    "P08",
                    format!("the value of {name} is written out; use {{{{{name}}}}}"),
                );
            }
        }
        if looks_secret(text) {
            sink.add(
                "P11",
                "text looks like a secret written out; use {\"secret\": \"name\"}".to_owned(),
            );
        }
    }
    for req in &step.requires {
        if req == &step.id {
            sink.add("P09", "requires itself".to_owned());
        } else if !ids.contains(req) {
            sink.add("P09", format!("requires unknown step {req:?}"));
        }
    }
}

/// Read a spec. Every problem is reported, not only the first.
pub fn lint(spec: &Value) -> Result<Spec, Vec<Finding>> {
    let mut out = Vec::new();
    let Some(obj) = spec.as_object() else {
        return Err(vec![top("P01", "spec must be an object".to_owned())]);
    };
    for k in obj.keys() {
        if !TOP_KEYS.contains(&k.as_str()) {
            out.push(top("P10", format!("spec: unknown key {k:?}")));
        }
    }
    if obj.get("version").and_then(Value::as_u64) != Some(VERSION) {
        out.push(top("P01", format!("version must be {VERSION}")));
    }
    let vars = parse_vars(obj.get("vars"), &mut out);

    let mut steps: Vec<Step> = Vec::new();
    let mut ids: BTreeSet<String> = BTreeSet::new();
    match obj.get("steps").and_then(Value::as_array) {
        Some(arr) if !arr.is_empty() => {
            for (i, item) in arr.iter().enumerate() {
                if let Some(id) = item.get("id").and_then(Value::as_str) {
                    if !ids.insert(id.to_owned()) {
                        out.push(Finding {
                            code: "P02",
                            step: Some(id.to_owned()),
                            msg: "id used more than once".to_owned(),
                        });
                        continue;
                    }
                }
                if let Some(step) = parse_step(item, i, &mut out) {
                    steps.push(step);
                }
            }
        }
        _ => out.push(top("P01", "steps must be a non-empty list".to_owned())),
    }
    for step in &steps {
        check_texts(step, &vars, &ids, &mut out);
    }
    if !out.is_empty() {
        return Err(out);
    }
    match order(&steps) {
        Ok(order) => Ok(Spec { vars, steps, order }),
        Err(left) => Err(vec![top(
            "P09",
            format!("requires form a cycle among: {}", left.join(", ")),
        )]),
    }
}

/// An ordered plan: steps in run order with the variables filled in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    steps: Vec<Step>,
    assumed: Vec<Step>,
    id: String,
}

impl Plan {
    /// Steps in the order they run.
    pub fn steps(&self) -> &[Step] {
        &self.steps
    }

    /// Steps left out by `--from` that a step in the plan depends on, directly or
    /// through another left-out step, in run order. A plan that starts later does not
    /// trust them: whoever runs it runs their `verify` first and stops if one fails.
    pub fn assumed(&self) -> &[Step] {
        &self.assumed
    }

    /// 16 hex digits over the plan as [`Plan::to_json`] prints it (steps, their
    /// `requires`, and the assumed steps). An identifier, not a security hash.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Ids of the steps a person must approve.
    pub fn needs_approval(&self) -> Vec<&str> {
        self.steps
            .iter()
            .filter(|s| s.level == Level::L2)
            .map(|s| s.id.as_str())
            .collect()
    }

    /// The plan as JSON: `{"plan_id", "steps": [...]}`. Secrets appear as references.
    pub fn to_json(&self) -> Value {
        json!({
            "plan_id": self.id,
            "assumed": steps_json(&self.assumed),
            "steps": steps_json(&self.steps),
        })
    }
}

fn command_json(c: &Command) -> Value {
    match c {
        Command::Argv(args) => Value::Array(
            args.iter()
                .map(|a| match a {
                    Arg::Text(t) => Value::String(t.clone()),
                    Arg::Secret(n) => json!({ "secret": n }),
                })
                .collect(),
        ),
        Command::Shell { sh, why } => json!({ "sh": sh, "why": why }),
    }
}

fn steps_json(steps: &[Step]) -> Value {
    let commands = |cs: &[Command]| Value::Array(cs.iter().map(command_json).collect());
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
                        json!({ "run": command_json(&v.run), "expect": expect, "active": v.active })
                    })
                    .collect();
                let recovery = match &s.recovery {
                    Recovery::None => json!({ "kind": "none" }),
                    Recovery::Rollback(run) => json!({ "kind": "rollback", "run": commands(run) }),
                    Recovery::Rescue(path) => json!({ "kind": "rescue", "path": path }),
                    Recovery::Unsupported(path) => json!({ "kind": "unsupported", "path": path }),
                };
                let disruption = match s.disruption {
                    Disruption::None => json!({ "kind": "none" }),
                    Disruption::Network {
                        reconnect_timeout_s,
                        auto_revert_s,
                    } => json!({ "kind": "network", "reconnect_timeout_s": reconnect_timeout_s,
                                 "auto_revert_s": auto_revert_s }),
                    Disruption::Reboot {
                        reconnect_timeout_s,
                    } => json!({ "kind": "reboot", "reconnect_timeout_s": reconnect_timeout_s }),
                };
                json!({
                    "id": s.id,
                    "level": s.level.as_str(),
                    "kind": s.kind.as_str(),
                    "title": s.title,
                    "requires": s.requires,
                    "apply": commands(&s.apply),
                    "instructions": s.instructions,
                    "verify": verify,
                    "recovery": recovery,
                    "destroys": s.destroys,
                    "disruption": disruption,
                    "detach": s.detach,
                    "consent": s.consent,
                })
            })
            .collect(),
    )
}

fn render_command(c: &Command, vars: &[(String, String)]) -> Command {
    match c {
        Command::Argv(args) => Command::Argv(
            args.iter()
                .map(|a| match a {
                    Arg::Text(t) => Arg::Text(render(t, vars)),
                    Arg::Secret(n) => Arg::Secret(n.clone()),
                })
                .collect(),
        ),
        Command::Shell { sh, why } => Command::Shell {
            sh: render(sh, vars),
            why: why.clone(),
        },
    }
}

fn render_step(s: &Step, vars: &[(String, String)]) -> Step {
    let r = |t: &str| render(t, vars);
    let rc = |c: &Command| render_command(c, vars);
    Step {
        id: s.id.clone(),
        level: s.level,
        kind: s.kind,
        title: r(&s.title),
        requires: s.requires.clone(),
        apply: s.apply.iter().map(rc).collect(),
        instructions: s.instructions.iter().map(|t| r(t)).collect(),
        verify: s
            .verify
            .iter()
            .map(|v| Verify {
                run: rc(&v.run),
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
            Recovery::Rollback(run) => Recovery::Rollback(run.iter().map(rc).collect()),
            Recovery::Rescue(path) => Recovery::Rescue(r(path)),
            Recovery::Unsupported(path) => Recovery::Unsupported(r(path)),
        },
        destroys: s.destroys.as_deref().map(r),
        disruption: s.disruption,
        detach: s.detach,
        consent: s.consent.as_deref().map(r),
    }
}

/// Order the steps and fill in the variables. With `from`, the plan starts at that step
/// (in run order); an unknown id is refused. Starting later does **not** mean the earlier
/// steps are trusted: the skipped steps the plan depends on are listed in
/// [`Plan::assumed`], to be verified before the first step runs.
pub fn plan(spec: &Spec, from: Option<&str>) -> Result<Plan, Finding> {
    let mut start = 0;
    if let Some(from) = from {
        start = spec
            .order
            .iter()
            .position(|&i| spec.steps[i].id == from)
            .ok_or_else(|| top("P09", format!("--from names unknown step {from:?}")))?;
    }
    let (skipped, included) = spec.order.split_at(start);
    // what the included steps need from the skipped ones, through any chain of requires:
    // walk the skipped steps backwards so that a needed step marks its own needs
    let mut needed: BTreeSet<&str> = included
        .iter()
        .flat_map(|&i| spec.steps[i].requires.iter().map(String::as_str))
        .collect();
    let mut assumed: Vec<Step> = Vec::new();
    for &i in skipped.iter().rev() {
        let step = &spec.steps[i];
        if needed.contains(step.id.as_str()) {
            needed.extend(step.requires.iter().map(String::as_str));
            assumed.push(render_step(step, &spec.vars));
        }
    }
    assumed.reverse();
    let steps: Vec<Step> = included
        .iter()
        .map(|&i| render_step(&spec.steps[i], &spec.vars))
        .collect();
    let body = json!({ "assumed": steps_json(&assumed), "steps": steps_json(&steps) });
    let id = crate::isolation::fnv1a64(body.to_string().as_bytes());
    Ok(Plan { steps, assumed, id })
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
                  "verify": [ { "run": ["hostname", "-f"], "expect": { "eq": "{{hostname}}" } } ] },
                { "id": "B2.repo", "level": "L1", "kind": "run", "title": "package source",
                  "requires": ["B0.precheck"],
                  "apply": [ ["apt-get", "update"] ],
                  "verify": [ { "run": ["apt-cache", "policy", "proxmox-ve"], "expect": { "rc": 0 } } ],
                  "recovery": { "kind": "rollback", "run": [ ["rm", "/etc/apt/sources.list.d/pve.sources"] ] } },
                { "id": "A.reinstall", "level": "L2", "kind": "manual", "title": "reinstall",
                  "requires": ["B2.repo"],
                  "apply": ["boot the rescue system and run installimage"],
                  "verify": [ { "run": ["ping", "-c1", "{{public_ipv4}}"], "expect": { "rc": 0 }, "active": true } ],
                  "recovery": { "kind": "unsupported", "path": "install again" },
                  "destroys": "everything on both disks",
                  "consent": "reinstall {{hostname}}" }
            ]
        })
    }

    fn codes(spec: &Value) -> Vec<&'static str> {
        lint(spec).unwrap_err().iter().map(|f| f.code).collect()
    }

    /// The good spec with one key of one step replaced (or removed with `Value::Null`).
    fn with(i: usize, key: &str, value: Value) -> Value {
        let mut v = good();
        let step = v["steps"][i].as_object_mut().unwrap();
        if value.is_null() {
            step.remove(key);
        } else {
            step.insert(key.to_owned(), value);
        }
        v
    }

    #[test]
    fn good_spec_is_accepted_and_ordered() {
        let spec = lint(&good()).unwrap();
        assert_eq!(spec.steps().len(), 3);
        let plan = plan(&spec, None).unwrap();
        let ids: Vec<&str> = plan.steps().iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["B0.precheck", "B2.repo", "A.reinstall"]);
        assert_eq!(plan.needs_approval(), ["A.reinstall"]);
        assert_eq!(plan.id().len(), 16);
    }

    #[test]
    fn plan_fills_variables_everywhere() {
        let plan = plan(&lint(&good()).unwrap(), None).unwrap();
        assert_eq!(plan.steps()[0].title, "host is host1");
        assert_eq!(
            plan.steps()[0].verify[0].expect,
            Expect::Eq("host1".to_owned())
        );
        assert_eq!(
            plan.steps()[2].verify[0].run,
            Command::Argv(vec![
                Arg::Text("ping".to_owned()),
                Arg::Text("-c1".to_owned()),
                Arg::Text("203.0.113.10".to_owned())
            ])
        );
        assert!(plan.steps()[2].verify[0].active);
        assert_eq!(plan.steps()[2].consent.as_deref(), Some("reinstall host1"));
        assert_eq!(plan.steps()[2].instructions.len(), 1);
        assert!(!plan.to_json().to_string().contains("{{"));
    }

    #[test]
    fn a_variable_cannot_add_arguments() {
        let mut v = good();
        v["vars"]["public_ipv4"] = json!("203.0.113.10 --force; reboot");
        let plan = plan(&lint(&v).unwrap(), None).unwrap();
        let Command::Argv(args) = &plan.steps()[2].verify[0].run else {
            panic!("argv expected");
        };
        assert_eq!(args.len(), 3);
    }

    /// Values that would change what a command means (review of #619, M1): empty, an
    /// option, and the root directory inside a shell line.
    #[test]
    fn variable_values_that_change_the_command_are_refused() {
        let argv = json!([["rm", "-r", "{{dir}}"]]);
        let sh = json!([{ "sh": "rm -rf {{dir}}/cache", "why": "glob" }]);
        let with_dir = |apply: &Value, dir: &str| {
            let mut v = with(1, "apply", apply.clone());
            v["vars"]["dir"] = json!(dir);
            v
        };
        for apply in [&argv, &sh] {
            assert!(lint(&with_dir(apply, "/var/lib/x")).is_ok());
            assert_eq!(codes(&with_dir(apply, "")), ["P01", "P07"]);
            assert_eq!(codes(&with_dir(apply, "--no-preserve-root")), ["P07"]);
        }
        assert_eq!(codes(&with_dir(&sh, "/")), ["P07"]);
        // as one whole argument the root directory is what the spec says, not an accident
        assert!(lint(&with_dir(&argv, "/")).is_ok());
    }

    /// Review of the #619 stack (ebb36): L1 the root directory has more than one
    /// spelling; N1 a value starting with '-' matters where a program reads it, not in a
    /// title or a consent sentence.
    #[test]
    fn root_spellings_and_dashes_in_prose() {
        let sh = json!([{ "sh": "chown -R www-data {{dir}}/*", "why": "glob" }]);
        for dir in ["/", "//", "/.", "/./", "/..", "///"] {
            let mut v = with(1, "apply", sh.clone());
            v["vars"]["dir"] = json!(dir);
            assert_eq!(codes(&v), ["P07"], "{dir:?}");
        }
        for dir in ["/srv", "/srv/.", "/.well-known", "./x"] {
            let mut v = with(1, "apply", sh.clone());
            v["vars"]["dir"] = json!(dir);
            assert!(lint(&v).is_ok(), "{dir:?}");
        }
        let mut v = with(1, "title", json!("tier {{tier}}"));
        v["vars"]["tier"] = json!("-pro");
        assert!(lint(&v).is_ok());
        let mut v = with(1, "apply", json!([["echo", "{{tier}}"]]));
        v["vars"]["tier"] = json!("-pro");
        assert_eq!(codes(&v), ["P07"]);
    }

    /// A value is never read again after it is filled in (review of #619, L2).
    #[test]
    fn render_is_one_pass() {
        let vars = [
            ("a".to_owned(), "x{".to_owned()),
            ("b".to_owned(), "SECOND".to_owned()),
        ];
        assert_eq!(render("{{a}}{b}}", &vars), "x{{b}}");
        assert_eq!(render("{{b}}-{{a}}-{{b}}", &vars), "SECOND-x{-SECOND");
        assert_eq!(render("{{nope}} {{a", &vars), "{{nope}} {{a");
        assert_eq!(render("plain", &vars), "plain");
    }

    #[test]
    fn order_follows_requires_not_position() {
        let mut v = good();
        v["steps"].as_array_mut().unwrap().reverse();
        let plan = plan(&lint(&v).unwrap(), None).unwrap();
        let ids: Vec<&str> = plan.steps().iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["B0.precheck", "B2.repo", "A.reinstall"]);
    }

    /// `--from` does not trust what it skips (review of #619, ebb36 M1): every skipped
    /// step that a planned step depends on, however indirectly, is listed to be verified.
    #[test]
    fn from_lists_the_skipped_steps_it_depends_on() {
        // A.reinstall requires B2.repo requires B0.precheck; add D, which nothing needs
        let mut v = good();
        let mut extra = v["steps"][0].clone();
        extra["id"] = json!("D.unrelated");
        v["steps"].as_array_mut().unwrap().insert(1, extra);
        let spec = lint(&v).unwrap();
        let ids = |steps: &[Step]| steps.iter().map(|s| s.id.clone()).collect::<Vec<_>>();

        let all = plan(&spec, None).unwrap();
        assert!(all.assumed().is_empty());
        let last = plan(&spec, Some("A.reinstall")).unwrap();
        assert_eq!(ids(last.steps()), ["A.reinstall"]);
        // B2.repo directly, B0.precheck through it; D.unrelated is not needed
        assert_eq!(ids(last.assumed()), ["B0.precheck", "B2.repo"]);
        let json = last.to_json();
        assert_eq!(json["assumed"][1]["id"], "B2.repo");
        assert_eq!(
            json["assumed"][0]["verify"][0]["run"],
            json!(["hostname", "-f"])
        );
        assert_eq!(json["steps"][0]["requires"], json!(["B2.repo"]));
        // the id covers requires: same steps, same order, another dependency
        let mut w = v.clone();
        w["steps"][3]["requires"] = json!(["B2.repo", "D.unrelated"]);
        let other = plan(&lint(&w).unwrap(), None).unwrap();
        assert_eq!(ids(other.steps()), ids(all.steps()));
        assert_ne!(other.id(), all.id());
    }

    #[test]
    fn from_starts_there_and_changes_the_id() {
        let spec = lint(&good()).unwrap();
        let all = plan(&spec, None).unwrap();
        let tail = plan(&spec, Some("B2.repo")).unwrap();
        assert_eq!(tail.steps().len(), 2);
        assert_ne!(all.id(), tail.id());
        assert_eq!(plan(&spec, Some("nope")).unwrap_err().code, "P09");
    }

    #[test]
    fn id_moves_with_a_variable_and_with_a_command() {
        let base = plan(&lint(&good()).unwrap(), None).unwrap();
        let mut v = good();
        v["vars"]["hostname"] = json!("host2");
        assert_ne!(plan(&lint(&v).unwrap(), None).unwrap().id(), base.id());
        let v = with(1, "apply", json!([["apt-get", "update", "-q"]]));
        assert_ne!(plan(&lint(&v).unwrap(), None).unwrap().id(), base.id());
        assert_eq!(plan(&lint(&good()).unwrap(), None).unwrap().id(), base.id());
    }

    #[test]
    fn secrets_stay_references() {
        let v = with(
            1,
            "apply",
            json!([["tailscale", "up", "--authkey", { "secret": "tailscale-authkey" }]]),
        );
        let plan = plan(&lint(&v).unwrap(), None).unwrap();
        assert_eq!(
            plan.to_json()["steps"][1]["apply"][0][3],
            json!({ "secret": "tailscale-authkey" })
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
        assert!(codes(&with(1, "id", json!("B0.precheck"))).contains(&"P02"));
        assert!(codes(&with(0, "id", json!("has space"))).contains(&"P02"));
        assert!(codes(&with(0, "id", Value::Null)).contains(&"P02"));
    }

    #[test]
    fn p03_level_unknown_or_too_low() {
        assert_eq!(codes(&with(0, "level", json!("L3"))), ["P03"]);
        // destroying something as L1
        let mut v = with(2, "level", json!("L1"));
        v["steps"][2].as_object_mut().unwrap().remove("consent");
        assert_eq!(codes(&v), ["P03"]);
        // rebooting as L1
        let v = with(
            1,
            "disruption",
            json!({ "kind": "reboot", "reconnect_timeout_s": 300 }),
        );
        assert_eq!(codes(&v), ["P03"]);
    }

    #[test]
    fn p04_kind_and_level_must_agree() {
        assert_eq!(codes(&with(0, "kind", json!("cmd"))), ["P04"]);
        assert_eq!(codes(&with(0, "level", json!("L1"))), ["P04"]);
        assert_eq!(
            codes(&with(0, "apply", json!([["rm", "-rf", "/tmp/x"]]))),
            ["P04"]
        );
        assert_eq!(codes(&with(0, "detach", json!(true))), ["P04"]);
        assert_eq!(codes(&with(1, "level", json!("L0"))), ["P04"]);
        assert_eq!(codes(&with(1, "apply", Value::Null)), ["P04"]);
    }

    #[test]
    fn p05_verify_must_be_decidable() {
        assert_eq!(codes(&with(1, "verify", Value::Null)), ["P05"]);
        assert_eq!(codes(&with(1, "verify", json!(["rc 0"]))), ["P05"]);
        let two = json!([{ "run": ["true"], "expect": { "rc": 0, "eq": "x" } }]);
        assert_eq!(codes(&with(1, "verify", two)), ["P05"]);
        let wrong = json!([{ "run": ["true"], "expect": { "rc": "zero" } }]);
        assert_eq!(codes(&with(1, "verify", wrong)), ["P05"]);
        let none = json!([{ "run": ["true"] }]);
        assert_eq!(codes(&with(1, "verify", none)), ["P05"]);
    }

    #[test]
    fn p06_writes_need_an_explicit_way_back() {
        assert_eq!(codes(&with(1, "recovery", Value::Null)), ["P06"]);
        assert_eq!(
            codes(&with(1, "recovery", json!({ "kind": "rollback" }))),
            ["P06"]
        );
        assert_eq!(
            codes(&with(2, "recovery", json!({ "kind": "unsupported" }))),
            ["P06"]
        );
        assert_eq!(
            codes(&with(2, "recovery", json!({ "kind": "none" }))),
            ["P06"]
        );
        let rescue = json!({ "kind": "rescue", "path": "boot rescue, chroot, reinstall grub" });
        assert!(lint(&with(2, "recovery", rescue)).is_ok());
    }

    #[test]
    fn p07_placeholders_and_shell_values() {
        assert_eq!(
            codes(&with(1, "apply", json!([["echo", "{{nope}}"]]))),
            ["P07"]
        );
        assert_eq!(
            codes(&with(1, "apply", json!([["echo", "{{hostname"]]))),
            ["P07"]
        );
        // a plain value may be used inside a shell line; a value with a space may not
        let sh = json!([{ "sh": "echo {{hostname}} | tee /etc/hostname", "why": "pipe" }]);
        assert!(lint(&with(1, "apply", sh.clone())).is_ok());
        let mut v = with(1, "apply", sh);
        v["vars"]["hostname"] = json!("host1; reboot");
        assert_eq!(codes(&v), ["P07"]);
    }

    #[test]
    fn p08_host_values_are_not_written_out() {
        let v = with(1, "apply", json!([["curl", "http://203.0.113.10/"]]));
        assert_eq!(codes(&v), ["P08"]);
        // short values are ordinary words: "vg0" in a command is not a finding
        assert!(lint(&with(1, "apply", json!([["vgs", "vg0"]]))).is_ok());
    }

    #[test]
    fn p09_requires_unknown_self_and_cycle() {
        assert_eq!(codes(&with(1, "requires", json!(["nope"]))), ["P09"]);
        assert_eq!(codes(&with(1, "requires", json!(["B2.repo"]))), ["P09"]);
        let found = lint(&with(0, "requires", json!(["A.reinstall"]))).unwrap_err();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "P09");
        assert!(found[0].msg.contains("cycle"));
    }

    #[test]
    fn p10_unknown_keys_and_ledger_status() {
        assert_eq!(codes(&with(1, "verfy", json!([]))), ["P10"]);
        assert_eq!(codes(&with(1, "status", json!("done@2026-11-09"))), ["P10"]);
        let mut v = good();
        v["stages"] = json!([]);
        assert_eq!(codes(&v), ["P10"]);
    }

    #[test]
    fn p11_commands_are_argument_lists() {
        assert_eq!(
            codes(&with(1, "apply", json!(["apt-get update"]))),
            ["P11", "P04"]
        );
        assert_eq!(codes(&with(1, "apply", json!([[]]))), ["P11", "P04"]);
        assert_eq!(
            codes(&with(1, "apply", json!([["apt-get", 3]]))),
            ["P11", "P04"]
        );
        assert_eq!(
            codes(&with(1, "apply", json!([[{ "secret": "k" }, "x"]]))),
            ["P11", "P04"]
        );
        assert_eq!(
            codes(&with(1, "apply", json!([{ "sh": "a | b" }]))),
            ["P11", "P04"]
        );
        let verify = json!([{ "run": "apt-cache policy", "expect": { "rc": 0 } }]);
        assert_eq!(codes(&with(1, "verify", verify)), ["P11"]);
    }

    #[test]
    fn p11_secrets_are_not_written_out() {
        let key = "tskey-kQ9x7Lm2aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zAb4Cd6Ef8";
        let v = with(1, "apply", json!([["tailscale", "up", "--authkey", key]]));
        assert_eq!(codes(&v), ["P11"]);
        let pem = with(
            1,
            "apply",
            json!([["tee", "-----BEGIN OPENSSH PRIVATE KEY-----"]]),
        );
        assert_eq!(codes(&pem), ["P11"]);
        // a sha256 checksum is hex and is fine
        let sum = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
        assert!(lint(&with(1, "apply", json!([["sha256sum", "-c", sum]]))).is_ok());
    }

    #[test]
    fn p12_disruption_and_consent() {
        // a network change must say how long to wait and when the host reverts by itself
        let v = with(
            1,
            "disruption",
            json!({ "kind": "network", "reconnect_timeout_s": 60 }),
        );
        assert_eq!(codes(&v), ["P12"]);
        let v = with(1, "disruption", json!({ "kind": "reboot" }));
        assert_eq!(codes(&v), ["P12"]);
        let v = with(
            1,
            "disruption",
            json!({ "kind": "network", "reconnect_timeout_s": 60, "auto_revert_s": 99999 }),
        );
        assert_eq!(codes(&v), ["P12"]);
        let ok = json!({ "kind": "network", "reconnect_timeout_s": 60, "auto_revert_s": 180 });
        let spec = lint(&with(1, "disruption", ok)).unwrap();
        assert_eq!(
            spec.steps()[1].disruption,
            Disruption::Network {
                reconnect_timeout_s: 60,
                auto_revert_s: 180
            }
        );
        // L2 without the sentence; the sentence on a step that is not L2
        assert_eq!(codes(&with(2, "consent", Value::Null)), ["P12"]);
        assert_eq!(codes(&with(1, "consent", json!("yes"))), ["P12"]);
    }

    #[test]
    fn every_problem_is_reported_at_once() {
        let mut v = with(0, "level", json!("L9"));
        v["steps"][1].as_object_mut().unwrap().remove("recovery");
        v["steps"][2]["apply"] = json!(["say {{nope}}"]);
        let got = codes(&v);
        assert!(
            got.contains(&"P03") && got.contains(&"P06") && got.contains(&"P07"),
            "{got:?}"
        );
    }

    #[test]
    fn finding_prints_code_step_and_reason() {
        let found = lint(&with(1, "recovery", Value::Null)).unwrap_err();
        let line = found[0].to_string();
        assert!(line.starts_with("P06 [B2.repo] "), "{line}");
    }
}
