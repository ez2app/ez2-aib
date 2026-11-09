//! The knife spec: the same JSON fields as knife.py (#525), checked when read.
//!
//! ```json
//! {"cmd": ["cargo", "test", "-q", "-p", "aib-sys", "--locked"],
//!  "timeout": 900,
//!  "noop": true,
//!  "knives": [{"name": "no-kill", "file": "crates/x/src/a.rs", "old": "...", "new": "...",
//!              "expect_fail": "job::tests::the_owner_crashing_kills_the_tree",
//!              "cmd": ["optional", "override"], "timeout": 600}]}
//! ```
//!
//! Rules (each one is a usage error, rc 2):
//! - `name`, `file`, `old`, `expect_fail` are non-empty strings; only `new` may be `""`
//!   (1ced7 #525 H1: an empty `expect_fail` matched every red name);
//! - every knife has a command: its own `cmd` or the top-level one; a command is a
//!   non-empty array of non-empty strings;
//! - `timeout` (seconds) is a positive integer, default 900, a knife may override it;
//! - `noop` is a boolean, default true;
//! - at least one knife (zero knives can never be "all bit"); names are unique;
//! - `file` is relative and stays inside the repo (no absolute path, no `..`);
//! - unknown keys are refused, so a typo such as `expect-fail` is not silently ignored.
//!   The last three are stricter than knife.py (listed in the PR as behaviour changes).

use serde_json::{Map, Value};
use std::fmt;
use std::path::{Component, Path};

pub const DEFAULT_TIMEOUT_S: u64 = 900;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Knife {
    pub name: String,
    pub file: String,
    pub old: String,
    pub new: String,
    pub expect_fail: String,
    /// The command this knife runs: its own, or the spec's top-level one.
    pub cmd: Vec<String>,
    pub timeout_s: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    pub knives: Vec<Knife>,
    pub noop: bool,
}

impl Spec {
    /// Every distinct command the knives actually use, in first-use order. The noop runs
    /// exactly these (knife.py behaviour, Codex M2): a top-level `cmd` that every knife
    /// overrides is not run.
    pub fn noop_cmds(&self) -> Vec<(Vec<String>, u64)> {
        let mut out: Vec<(Vec<String>, u64)> = Vec::new();
        for k in &self.knives {
            match out.iter_mut().find(|(c, _)| *c == k.cmd) {
                Some((_, t)) => *t = (*t).max(k.timeout_s),
                None => out.push((k.cmd.clone(), k.timeout_s)),
            }
        }
        out
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpecError {
    pub at: String,
    pub what: String,
}

impl fmt::Display for SpecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "knife spec: {}: {}", self.at, self.what)
    }
}

impl std::error::Error for SpecError {}

fn err(at: impl Into<String>, what: impl Into<String>) -> SpecError {
    SpecError { at: at.into(), what: what.into() }
}

const TOP_KEYS: &[&str] = &["cmd", "timeout", "noop", "knives"];
const KNIFE_KEYS: &[&str] = &["name", "file", "old", "new", "expect_fail", "cmd", "timeout"];

fn only_keys(o: &Map<String, Value>, allowed: &[&str], at: &str) -> Result<(), SpecError> {
    match o.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(err(at, format!("unknown key {k:?} (allowed: {})", allowed.join(", ")))),
        None => Ok(()),
    }
}

fn non_empty_str(o: &Map<String, Value>, key: &str, at: &str) -> Result<String, SpecError> {
    match o.get(key) {
        Some(Value::String(s)) if !s.is_empty() => Ok(s.clone()),
        Some(Value::String(_)) => Err(err(at, format!("{key} must not be empty"))),
        Some(_) => Err(err(at, format!("{key} must be a string"))),
        None => Err(err(at, format!("{key} is missing"))),
    }
}

fn cmd_of(v: Option<&Value>, at: &str) -> Result<Option<Vec<String>>, SpecError> {
    let Some(v) = v else { return Ok(None) };
    let Value::Array(a) = v else { return Err(err(at, "cmd must be an array of strings")) };
    if a.is_empty() {
        return Err(err(at, "cmd must not be empty"));
    }
    let mut out = Vec::with_capacity(a.len());
    for x in a {
        match x {
            Value::String(s) if !s.is_empty() => out.push(s.clone()),
            _ => return Err(err(at, "cmd items must be non-empty strings")),
        }
    }
    Ok(Some(out))
}

fn timeout_of(v: Option<&Value>, at: &str) -> Result<Option<u64>, SpecError> {
    match v {
        None => Ok(None),
        Some(x) => match x.as_u64() {
            Some(t) if t > 0 => Ok(Some(t)),
            _ => Err(err(at, "timeout must be a positive integer (seconds)")),
        },
    }
}

fn file_inside_repo(file: &str, at: &str) -> Result<(), SpecError> {
    let p = Path::new(file);
    let escapes = p.components().any(|c| {
        matches!(c, Component::ParentDir | Component::RootDir | Component::Prefix(_))
    });
    if p.is_absolute() || escapes || file.starts_with('/') || file.starts_with('\\') {
        return Err(err(at, format!("file {file:?} must be relative and inside the repo")));
    }
    Ok(())
}

pub fn parse(text: &str) -> Result<Spec, SpecError> {
    let v: Value = serde_json::from_str(text).map_err(|e| err("spec", format!("not JSON: {e}")))?;
    let Value::Object(top) = v else { return Err(err("spec", "must be a JSON object")) };
    only_keys(&top, TOP_KEYS, "spec")?;
    let top_cmd = cmd_of(top.get("cmd"), "spec.cmd")?;
    let top_timeout = timeout_of(top.get("timeout"), "spec.timeout")?.unwrap_or(DEFAULT_TIMEOUT_S);
    let noop = match top.get("noop") {
        None => true,
        Some(Value::Bool(b)) => *b,
        Some(_) => return Err(err("spec.noop", "must be true or false")),
    };
    let Some(Value::Array(ks)) = top.get("knives") else {
        return Err(err("spec.knives", "must be an array"));
    };
    if ks.is_empty() {
        return Err(err("spec.knives", "no knives (zero knives can never all bite)"));
    }
    let mut knives: Vec<Knife> = Vec::with_capacity(ks.len());
    for (i, k) in ks.iter().enumerate() {
        let at = format!("knives[{i}]");
        let Value::Object(o) = k else { return Err(err(at, "must be an object")) };
        only_keys(o, KNIFE_KEYS, &at)?;
        let name = non_empty_str(o, "name", &at)?;
        if knives.iter().any(|x| x.name == name) {
            return Err(err(at, format!("duplicate name {name:?}")));
        }
        let file = non_empty_str(o, "file", &at)?;
        file_inside_repo(&file, &at)?;
        let old = non_empty_str(o, "old", &at)?;
        let expect_fail = non_empty_str(o, "expect_fail", &at)?;
        let new = match o.get("new") {
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err(err(&at, "new must be a string")),
            None => return Err(err(&at, "new is missing (\"\" deletes the anchor)")),
        };
        if new == old {
            return Err(err(&at, "new equals old: the knife changes nothing"));
        }
        let cmd = match cmd_of(o.get("cmd"), &format!("{at}.cmd"))? {
            Some(c) => c,
            None => top_cmd.clone().ok_or_else(|| err(&at, "no cmd here and no top-level cmd"))?,
        };
        let timeout_s = timeout_of(o.get("timeout"), &format!("{at}.timeout"))?.unwrap_or(top_timeout);
        knives.push(Knife { name, file, old, new, expect_fail, cmd, timeout_s });
    }
    Ok(Spec { knives, noop })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn one(knife: &str) -> String {
        format!(r#"{{"cmd": ["cargo", "test"], "knives": [{knife}]}}"#)
    }

    const OK: &str = r#"{"name": "k1", "file": "src/a.rs", "old": "a", "new": "", "expect_fail": "t::x"}"#;

    #[test]
    fn a_minimal_spec_reads_with_defaults() {
        let s = parse(&one(OK)).expect("valid");
        assert!(s.noop);
        assert_eq!(s.knives.len(), 1);
        let k = &s.knives[0];
        assert_eq!(k.cmd, vec!["cargo".to_string(), "test".to_string()]);
        assert_eq!(k.timeout_s, DEFAULT_TIMEOUT_S);
        assert_eq!(k.new, "");
    }

    #[test]
    fn h1_empty_expect_fail_is_a_usage_error() {
        let e = parse(&one(r#"{"name": "k", "file": "a", "old": "a", "new": "b", "expect_fail": ""}"#))
            .expect_err("empty expect_fail");
        assert!(e.what.contains("expect_fail must not be empty"), "{e}");
    }

    #[test]
    fn h1_every_required_string_must_be_non_empty() {
        for key in ["name", "file", "old"] {
            let mut o: Map<String, Value> = serde_json::from_str(OK).expect("json");
            o.insert(key.to_string(), Value::String(String::new()));
            let e = parse(&one(&Value::Object(o).to_string())).expect_err(key);
            assert!(e.what.contains(&format!("{key} must not be empty")), "{key}: {e}");
        }
    }

    #[test]
    fn a_knife_without_any_cmd_is_refused() {
        let e = parse(&format!(r#"{{"knives": [{OK}]}}"#)).expect_err("no cmd");
        assert!(e.what.contains("no cmd"), "{e}");
    }

    #[test]
    fn zero_knives_is_refused() {
        let e = parse(r#"{"cmd": ["x"], "knives": []}"#).expect_err("zero");
        assert!(e.what.contains("no knives"), "{e}");
    }

    #[test]
    fn unknown_keys_are_refused() {
        let e = parse(&one(r#"{"name": "k", "file": "a", "old": "a", "new": "", "expect-fail": "t"}"#))
            .expect_err("typo");
        assert!(e.what.contains("unknown key \"expect-fail\""), "{e}");
        let e = parse(r#"{"cmd": ["x"], "knifes": []}"#).expect_err("typo top");
        assert!(e.what.contains("unknown key"), "{e}");
    }

    #[test]
    fn file_must_stay_inside_the_repo() {
        for f in ["../x.rs", "/etc/passwd", "a/../../b"] {
            let k = OK.replace("src/a.rs", f);
            let e = parse(&one(&k)).expect_err(f);
            assert!(e.what.contains("inside the repo"), "{f}: {e}");
        }
    }

    #[test]
    fn duplicate_names_and_no_op_knives_are_refused() {
        let e = parse(&one(&format!("{OK}, {OK}"))).expect_err("dup");
        assert!(e.what.contains("duplicate name"), "{e}");
        let e = parse(&one(r#"{"name": "k", "file": "a", "old": "x", "new": "x", "expect_fail": "t"}"#))
            .expect_err("same");
        assert!(e.what.contains("changes nothing"), "{e}");
    }

    #[test]
    fn per_knife_cmd_and_timeout_override() {
        let k2 = r#"{"name": "k2", "file": "b", "old": "b", "new": "c", "expect_fail": "t", "cmd": ["py"], "timeout": 60}"#;
        let s = parse(&format!(r#"{{"cmd": ["cargo"], "timeout": 120, "knives": [{OK}, {k2}]}}"#)).expect("ok");
        assert_eq!(s.knives[0].timeout_s, 120);
        assert_eq!(s.knives[1].timeout_s, 60);
        assert_eq!(s.knives[1].cmd, vec!["py".to_string()]);
    }

    #[test]
    fn bad_timeout_and_noop_types() {
        for bad in [r#""timeout": 0"#, r#""timeout": -5"#, r#""timeout": "9""#, r#""noop": "no""#] {
            let e = parse(&format!(r#"{{"cmd": ["x"], {bad}, "knives": [{OK}]}}"#)).expect_err(bad);
            assert!(e.what.contains("timeout") || e.what.contains("true or false"), "{bad}: {e}");
        }
    }

    #[test]
    fn m2_noop_runs_only_the_commands_knives_use_deduplicated() {
        let k2 = r#"{"name": "k2", "file": "b", "old": "b", "new": "c", "expect_fail": "t", "cmd": ["py"], "timeout": 60}"#;
        let k3 = r#"{"name": "k3", "file": "c", "old": "c", "new": "d", "expect_fail": "t", "cmd": ["py"], "timeout": 90}"#;
        // every knife overrides cmd ⇒ the top-level one is never run
        let s = parse(&format!(r#"{{"cmd": ["unused"], "knives": [{k2}, {k3}]}}"#)).expect("ok");
        assert_eq!(s.noop_cmds(), vec![(vec!["py".to_string()], 90)]);
        let s = parse(&format!(r#"{{"cmd": ["cargo"], "knives": [{OK}, {k2}, {k3}]}}"#)).expect("ok");
        let c: Vec<Vec<String>> = s.noop_cmds().into_iter().map(|(c, _)| c).collect();
        assert_eq!(c, vec![vec!["cargo".to_string()], vec!["py".to_string()]]);
    }

    #[test]
    fn noop_false_is_kept() {
        let s = parse(&format!(r#"{{"cmd": ["x"], "noop": false, "knives": [{OK}]}}"#)).expect("ok");
        assert!(!s.noop);
    }
}
