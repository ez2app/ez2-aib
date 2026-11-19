//! B1dm-notify (519d0 11-18 07:36, Andy 07:4x「開始」): the four notification levels of 1.x
//! `aib/src/messaging/notify_policy.py`, translated to Rust as pure functions with the same logic
//! and the same rule file (`notify-rules.json`, byte for byte). Not redesigned: the 1.x tests
//! (`ez2-ai-b/tests/test-notify.py`, classes `ClassifierTest` and `ActionAndStateTest`) are the
//! acceptance tests in `tests/policy.rs`.
//!
//! - [`classify`]: which level one record is (red > yellow > green > blue, the strictest rule
//!   wins). A routine AI-to-AI message is locked blue; the only way out is a red / yellow
//!   `requested_level` with a non-blank `escalation_reason`, and that is marked for the audit.
//! - [`plan_actions`]: what a level does (desktop notification, sound, flash, how long) under the
//!   total switch and the four level switches.
//!
//! Out of scope here: reading a rule file from disk (owner / mode / symlink checks), the switch
//! file, the audit log and the inbox count (1.x side effects), and sending — that is N-notify.

use regex::Regex;
use serde_json::{Map, Value};
use std::fmt;

/// Strictest first.
pub const LEVELS: [&str; 4] = ["red", "yellow", "green", "blue"];

/// The rule file 1.x ships next to notify_policy.py, embedded.
pub const BUILTIN_RULES: &str = include_str!("../notify-rules.json");

const AI_KINDS: [&str; 4] = ["ai", "agent", "main", "sub_agent"]; // "sub-agent" is "sub_agent" after the dash swap
const RULE_FIELDS: [&str; 6] = [
    "always",
    "signal_type",
    "kind",
    "actor_kind",
    "text",
    "needs_action",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RulesError(pub String);
impl fmt::Display for RulesError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for RulesError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StateError(pub String);
impl fmt::Display for StateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for StateError {}

fn rerr<T>(m: impl Into<String>) -> Result<T, RulesError> {
    Err(RulesError(m.into()))
}

#[derive(Debug, Clone)]
enum Op {
    Exact(Vec<String>),
    Regex(Vec<Regex>),
    Truthy,
}

#[derive(Debug, Clone)]
struct Rule {
    id: String,
    level: usize,
    field: &'static str,
    op: Op,
}

/// What one level does (the `actions` block of the rule file).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Action {
    pub system_notification: bool,
    pub sound: Option<String>,
    pub flash: bool,
    pub duration_ms: u32,
    pub flash_background: Option<u8>,
    pub flash_foreground: Option<u8>,
}

/// A checked rule file.
#[derive(Debug, Clone)]
pub struct Rules {
    rules: Vec<Rule>,
    actions: [Action; 4],
}

impl Rules {
    /// The embedded 1.x rule file. It is checked by the tests, so this cannot fail at run time
    /// unless the file is edited badly — and then every test fails first.
    pub fn builtin() -> Result<Rules, RulesError> {
        parse_rules(BUILTIN_RULES)
    }
}

fn level_index(v: &Value) -> Option<usize> {
    v.as_str().and_then(|s| LEVELS.iter().position(|l| *l == s))
}

fn exact_keys(o: &Map<String, Value>, keys: &[&str]) -> bool {
    o.len() == keys.len() && keys.iter().all(|k| o.contains_key(*k))
}

fn nonempty(v: &Value) -> bool {
    v.as_str().is_some_and(|s| !s.trim().is_empty())
}

/// 1.x writes `(?<![A-Za-z0-9_])X(?![A-Za-z0-9_])`; the `regex` crate has no look-around. For a
/// yes / no search, taking the neighbour (or the text edge) instead of looking at it gives the
/// same answer, so exactly these two forms are rewritten; any other look-around is refused.
fn rust_pattern(p: &str) -> Result<String, String> {
    let s = p
        .replace("(?<![A-Za-z0-9_])", "(?:^|[^A-Za-z0-9_])")
        .replace("(?![A-Za-z0-9_])", "(?:$|[^A-Za-z0-9_])");
    if s.contains("(?<") || s.contains("(?=") || s.contains("(?!") {
        return Err("look-around other than the word-edge form".into());
    }
    Ok(format!("(?i){s}"))
}

/// Check a rule file the way 1.x `_validate_rules` does.
pub fn parse_rules(text: &str) -> Result<Rules, RulesError> {
    let data: Value = serde_json::from_str(text)
        .map_err(|_| RulesError("notification rules are not valid JSON".into()))?;
    let Some(top) = data.as_object() else {
        return rerr("notification rules have an invalid top-level shape");
    };
    if !exact_keys(top, &["schema_version", "level_order", "rules", "actions"]) {
        return rerr("notification rules have an invalid top-level shape");
    }
    if top["schema_version"] != "1" {
        return rerr(format!(
            "unsupported notification rules schema: {}",
            top["schema_version"]
        ));
    }
    let order_ok = top["level_order"]
        .as_array()
        .is_some_and(|a| a.len() == 4 && a.iter().zip(LEVELS).all(|(x, l)| x == l));
    if !order_ok {
        return rerr("notification level_order must be red,yellow,green,blue");
    }
    let Some(list) = top["rules"].as_array().filter(|a| !a.is_empty()) else {
        return rerr("notification rules list must be non-empty");
    };
    let mut rules = Vec::new();
    let mut seen_levels = [false; 4];
    for (i, r) in list.iter().enumerate() {
        let w = format!("rules[{i}]");
        let Some(r) = r
            .as_object()
            .filter(|o| exact_keys(o, &["id", "level", "field", "match", "values"]))
        else {
            return rerr(format!("{w} has an invalid shape"));
        };
        let id = r["id"].as_str().unwrap_or("");
        let id_ok = nonempty(&r["id"])
            && id.len() <= 64
            && id.bytes().enumerate().all(|(n, b)| {
                b.is_ascii_lowercase()
                    || b.is_ascii_digit()
                    || (n > 0 && matches!(b, b'.' | b'_' | b'-'))
            });
        if !id_ok || rules.iter().any(|x: &Rule| x.id == id) {
            return rerr(format!("{w} has an invalid or duplicate id"));
        }
        let Some(level) = level_index(&r["level"]) else {
            return rerr(format!("{w} has an invalid level"));
        };
        seen_levels[level] = true;
        let Some(field) = r["field"]
            .as_str()
            .and_then(|f| RULE_FIELDS.iter().find(|x| **x == f).copied())
        else {
            return rerr(format!("{w} has an invalid field"));
        };
        let Some(m) = r["match"]
            .as_str()
            .filter(|m| matches!(*m, "exact" | "regex" | "truthy"))
        else {
            return rerr(format!("{w} has an invalid match operator"));
        };
        let values: Option<Vec<String>> = r["values"].as_array().and_then(|a| {
            a.iter()
                .map(|v| nonempty(v).then(|| v.as_str().unwrap_or("").to_string()))
                .collect()
        });
        let Some(values) = values.filter(|v| {
            let mut s = v.clone();
            s.sort();
            s.dedup();
            s.len() == v.len()
        }) else {
            return rerr(format!("{w} values must be unique non-empty strings"));
        };
        let op = match m {
            "truthy" if !values.is_empty() => {
                return rerr(format!("{w} truthy rules must not have values"))
            }
            "truthy" => Op::Truthy,
            _ if values.is_empty() => {
                return rerr(format!("{w} requires at least one match value"))
            }
            "exact" => Op::Exact(values),
            _ => {
                let mut res = Vec::new();
                for p in &values {
                    let re =
                        rust_pattern(p).and_then(|q| Regex::new(&q).map_err(|e| e.to_string()));
                    match re {
                        Ok(re) => res.push(re),
                        Err(e) => return rerr(format!("{w} has invalid regex {p:?} ({e})")),
                    }
                }
                Op::Regex(res)
            }
        };
        rules.push(Rule {
            id: id.to_string(),
            level,
            field,
            op,
        });
    }
    if seen_levels.iter().any(|x| !x) {
        return rerr("notification rules must cover all four levels");
    }
    let Some(acts) = top["actions"]
        .as_object()
        .filter(|o| exact_keys(o, &LEVELS))
    else {
        return rerr("notification actions must define all four levels");
    };
    let mut actions = Vec::new();
    for level in LEVELS {
        actions.push(parse_action(&acts[level], level)?);
    }
    let actions: [Action; 4] = actions
        .try_into()
        .map_err(|_| RulesError("notification actions must define all four levels".into()))?;
    Ok(Rules { rules, actions })
}

fn parse_action(v: &Value, level: &str) -> Result<Action, RulesError> {
    let Some(a) = v.as_object().filter(|o| {
        exact_keys(
            o,
            &[
                "system_notification",
                "sound",
                "flash",
                "duration_ms",
                "flash_background",
                "flash_foreground",
            ],
        )
    }) else {
        return rerr(format!("notification action has invalid shape: {level}"));
    };
    let Some(system_notification) = a["system_notification"].as_bool() else {
        return rerr(format!("system_notification must be boolean: {level}"));
    };
    let sound = match &a["sound"] {
        Value::Null => None,
        s if nonempty(s) => s.as_str().map(str::to_string),
        _ => return rerr(format!("sound must be null or a non-empty string: {level}")),
    };
    let Some(flash) = a["flash"].as_bool() else {
        return rerr(format!("flash must be boolean: {level}"));
    };
    let Some(duration_ms) = a["duration_ms"]
        .as_u64()
        .filter(|d| *d <= 60_000)
        .and_then(|d| u32::try_from(d).ok())
    else {
        return rerr(format!("duration_ms is invalid: {level}"));
    };
    let colour = |key: &str| -> Result<Option<u8>, RulesError> {
        let c = &a[key];
        if flash {
            c.as_u64()
                .and_then(|n| u8::try_from(n).ok())
                .map(Some)
                .ok_or_else(|| RulesError(format!("{key} must be an xterm colour: {level}")))
        } else if c.is_null() {
            Ok(None)
        } else {
            rerr(format!(
                "{key} must be null when flash is disabled: {level}"
            ))
        }
    };
    Ok(Action {
        system_notification,
        sound,
        flash,
        duration_ms,
        flash_background: colour("flash_background")?,
        flash_foreground: colour("flash_foreground")?,
    })
}

// ---------- the record's facts (1.x `_facts`) ----------

/// Python's truth value of a JSON value.
fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Python's `str()` of a JSON value, for the cases a record can hold.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// `a or b`: the first truthy value, else the last.
fn or<'a>(a: Option<&'a Value>, b: Option<&'a Value>) -> Option<&'a Value> {
    match a {
        Some(x) if py_truthy(x) => Some(x),
        _ => b,
    }
}

fn norm(v: Option<&Value>) -> String {
    v.filter(|x| py_truthy(x))
        .map(py_str)
        .unwrap_or_default()
        .trim()
        .to_lowercase()
}

fn text_parts(r: &Map<String, Value>) -> String {
    let mut parts: Vec<&str> = Vec::new();
    fn add<'a>(parts: &mut Vec<&'a str>, v: Option<&'a Value>) {
        match v {
            Some(Value::String(s)) => parts.push(s),
            Some(Value::Object(o)) => parts.extend(o.values().filter_map(Value::as_str)),
            _ => {}
        }
    }
    add(&mut parts, r.get("text"));
    add(&mut parts, r.get("subject"));
    if let Some(Value::Object(b)) = r.get("body") {
        for k in ["summary", "subject", "text", "message"] {
            add(&mut parts, b.get(k));
        }
    }
    parts.join("\n")
}

fn actor_kind(r: &Map<String, Value>) -> String {
    let from_actor = r
        .get("actor")
        .and_then(Value::as_object)
        .and_then(|a| a.get("kind"));
    norm(or(from_actor, r.get("sender_kind"))).replace('-', "_")
}

fn str_truthy(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(true)) => true,
        Some(Value::String(s)) => {
            matches!(
                s.trim().to_lowercase().as_str(),
                "1" | "true" | "yes" | "y" | "是"
            )
        }
        _ => false,
    }
}

struct Facts {
    signal_type: String,
    kind: String,
    actor_kind: String,
    text: String,
    needs_action: bool,
}

fn facts(r: &Map<String, Value>) -> Facts {
    Facts {
        signal_type: norm(or(r.get("signal_type"), r.get("type"))),
        kind: norm(r.get("kind")),
        actor_kind: actor_kind(r),
        text: text_parts(r),
        needs_action: str_truthy(r.get("needs_action")) || str_truthy(r.get("requires_action")),
    }
}

fn rule_matches(rule: &Rule, f: &Facts) -> bool {
    // (the field's value as Python str(), and its truth value)
    let (s, truthy): (String, bool) = match rule.field {
        "always" => ("True".into(), true),
        "needs_action" => (
            if f.needs_action { "True" } else { "False" }.into(),
            f.needs_action,
        ),
        "signal_type" => (f.signal_type.clone(), !f.signal_type.is_empty()),
        "kind" => (f.kind.clone(), !f.kind.is_empty()),
        "actor_kind" => (f.actor_kind.clone(), !f.actor_kind.is_empty()),
        _ => (f.text.clone(), !f.text.is_empty()),
    };
    match &rule.op {
        Op::Truthy => truthy,
        Op::Exact(vs) => {
            let t = s.to_lowercase();
            vs.iter().any(|c| c.to_lowercase() == t)
        }
        Op::Regex(rs) => rs.iter().any(|re| re.is_match(&s)),
    }
}

fn is_ai_kind(k: &str) -> bool {
    AI_KINDS.contains(&k)
}

fn is_ai_message(r: &Map<String, Value>, f: &Facts) -> bool {
    if !is_ai_kind(&f.actor_kind) {
        return false;
    }
    let shape = f.kind.starts_with("message.")
        || r.contains_key("recipient")
        || r.contains_key("sender_kind");
    if !shape {
        return false;
    }
    match r
        .get("recipient")
        .and_then(Value::as_object)
        .and_then(|o| o.get("kind"))
    {
        Some(k) if py_truthy(k) => is_ai_kind(&py_str(k).trim().to_lowercase().replace('-', "_")),
        _ => true,
    }
}

/// `requested_level` (or `notify_level` when that is absent or null); a string is trimmed and
/// lower-cased, anything else is kept as it came.
fn requested(r: &Map<String, Value>) -> Option<Value> {
    let v = match r.get("requested_level") {
        None | Some(Value::Null) => r.get("notify_level"),
        x => x,
    }?;
    match v {
        Value::Null => None,
        Value::String(s) => Some(Value::String(s.trim().to_lowercase())),
        other => Some(other.clone()),
    }
}

fn escalation_reason(r: &Map<String, Value>) -> Option<&Value> {
    match r.get("escalation_reason") {
        None | Some(Value::Null) => r.get("notify_reason"),
        x => x,
    }
}

/// One record's level and why (1.x `classify` return value).
#[derive(Debug, Clone, PartialEq)]
pub struct Classification {
    pub level: &'static str,
    pub reason_codes: Vec<String>,
    pub rule_ids: Vec<String>,
    pub audit_required: bool,
    pub audit_code: Option<&'static str>,
    pub requested_level: Option<Value>,
}

fn blue_lock(
    audit_required: bool,
    audit_code: Option<&'static str>,
    req: Option<Value>,
) -> Classification {
    Classification {
        level: "blue",
        reason_codes: vec!["ai_routine_blue_lock".into()],
        rule_ids: vec!["blue.default".into()],
        audit_required,
        audit_code,
        requested_level: req,
    }
}

/// Classify one record, red > yellow > green > blue (1.x `classify`).
pub fn classify(record: &Value, rules: &Rules) -> Result<Classification, RulesError> {
    let Some(r) = record.as_object() else {
        return rerr("notification record must be an object");
    };
    let f = facts(r);
    let matches: Vec<&Rule> = rules.rules.iter().filter(|x| rule_matches(x, &f)).collect();
    let detected = matches.iter().map(|x| x.level).min().unwrap_or(3);
    let req = requested(r);
    let req_level = req.as_ref().and_then(level_index);
    let mut codes: Vec<String> = matches.iter().map(|x| x.id.clone()).collect();
    let mut audit_required = false;
    let mut audit_code = None;
    let level;
    if is_ai_message(r, &f) {
        if matches!(req_level, Some(0 | 1)) {
            audit_required = true;
            if !escalation_reason(r).is_some_and(nonempty) {
                return Ok(blue_lock(true, Some("missing_escalation_reason"), req));
            }
            audit_code = Some("ai_structured_escalation");
            level = detected.min(req_level.unwrap_or(3));
            codes.push("ai_structured_escalation".into());
        } else {
            if req.is_some() && req_level.is_none() {
                return Ok(blue_lock(true, Some("invalid_requested_level"), req));
            }
            return Ok(blue_lock(false, None, req));
        }
    } else {
        level = match req_level {
            Some(q) => {
                codes.push("structured_level_request".into());
                detected.min(q)
            }
            None => {
                if req.is_some() {
                    audit_required = true;
                    audit_code = Some("invalid_requested_level");
                }
                detected
            }
        };
    }
    let mut ordered: Vec<String> = Vec::new();
    for c in codes {
        if !ordered.contains(&c) {
            ordered.push(c);
        }
    }
    Ok(Classification {
        level: LEVELS[level],
        reason_codes: ordered,
        rule_ids: matches.iter().map(|x| x.id.clone()).collect(),
        audit_required,
        audit_code,
        requested_level: req,
    })
}

// ---------- switches and actions (1.x `default_state` / `_validate_state` / `plan_actions`) ----------

/// The total switch and the four level switches.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct State {
    pub enabled: bool,
    /// red, yellow, green, blue
    pub levels: [bool; 4],
}

impl Default for State {
    fn default() -> Self {
        State {
            enabled: true,
            levels: [true; 4],
        }
    }
}

/// Read a switch record `{schema_version:"1", enabled, levels:{red,yellow,green,blue}}`.
pub fn parse_state(v: &Value) -> Result<State, StateError> {
    let bad = |m: &str| Err(StateError(m.into()));
    let Some(o) = v
        .as_object()
        .filter(|o| exact_keys(o, &["schema_version", "enabled", "levels"]))
    else {
        return bad("notification state has an invalid top-level shape");
    };
    let (Some("1"), Some(enabled)) = (o["schema_version"].as_str(), o["enabled"].as_bool()) else {
        return bad("notification state has an invalid schema or total switch");
    };
    let levels = o["levels"].as_object().filter(|l| exact_keys(l, &LEVELS));
    let Some(levels) = levels else {
        return bad("notification state must contain four boolean level switches");
    };
    let mut out = [false; 4];
    for (i, l) in LEVELS.iter().enumerate() {
        match levels[*l].as_bool() {
            Some(b) => out[i] = b,
            None => return bad("notification state must contain four boolean level switches"),
        }
    }
    Ok(State {
        enabled,
        levels: out,
    })
}

/// What a level does now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub level: &'static str,
    pub enabled: bool,
    pub action: Action,
}

/// 1.x `plan_actions`: a level switched off (or the total switch off) does nothing — no
/// notification, no sound, no flash, 0 ms; the flash colours stay as the file has them.
pub fn plan_actions(level: &str, state: &State, rules: &Rules) -> Result<Plan, StateError> {
    let Some(i) = LEVELS.iter().position(|l| *l == level) else {
        return Err(StateError(
            "classification has an unknown notification level".into(),
        ));
    };
    let enabled = state.enabled && state.levels[i];
    let mut action = rules.actions[i].clone();
    if !enabled {
        action.system_notification = false;
        action.sound = None;
        action.flash = false;
        action.duration_ms = 0;
    }
    Ok(Plan {
        level: LEVELS[i],
        enabled,
        action,
    })
}
