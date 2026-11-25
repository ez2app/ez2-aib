//! Step 2b: the shop's Cloudflare zone keeps the engine's own cache time for `/e2/*`.
//! Without it the zone's default (four hours in the browser) holds an old site script
//! after any change: on 11-24 the AI loader reached visitors hours late.
//!
//! The rule is the one the first shop has had since 11-01 (PREREAD L1): cache on, the
//! origin's time both at the edge and in the browser. Found by its expression, never by
//! its name (a person renames it; L2). The zone's cache rules are read first: none at all
//! ⇒ the rule set is created (PUT); some ⇒ this rule is **added** (POST), never put over
//! the others. A rule with the same expression but other settings is shown, not changed.

use crate::plan::State;
use serde_json::{json, Value};

/// The rule's expression.
pub const EXPRESSION: &str = r#"starts_with(http.request.uri.path, "/e2/")"#;
/// The rule's name, as the first shop has it (shown only; matching is by expression).
pub const DESCRIPTION: &str = "Ez2 足跡 /e2/* 照來源";

/// The rule as it is written.
pub fn standard_rule() -> Value {
    json!({
        "expression": EXPRESSION,
        "description": DESCRIPTION,
        "action": "set_cache_settings",
        "action_parameters": {
            "cache": true,
            "edge_ttl": {"mode": "respect_origin"},
            "browser_ttl": {"mode": "respect_origin"}
        },
        "enabled": true
    })
}

/// An expression with the spaces outside quoted text removed and the words outside
/// quoted text lower-cased; quoted text is kept as it is (a path is case-sensitive).
pub fn normalize(expr: &str) -> String {
    let mut out = String::with_capacity(expr.len());
    let mut quoted = false;
    let mut escaped = false;
    for c in expr.chars() {
        if quoted {
            out.push(c);
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == '"' {
                quoted = false;
            }
        } else if c == '"' {
            quoted = true;
            out.push(c);
        } else if !c.is_whitespace() {
            out.extend(c.to_lowercase());
        }
    }
    out
}

fn is_e2(rule: &Value) -> bool {
    rule["expression"]
        .as_str()
        .is_some_and(|e| normalize(e) == normalize(EXPRESSION))
}

fn is_standard(rule: &Value) -> bool {
    let p = &rule["action_parameters"];
    rule["action"] == "set_cache_settings"
        && rule["enabled"] != false
        && p["cache"] == true
        && p["edge_ttl"]["mode"] == "respect_origin"
        && p["browser_ttl"]["mode"] == "respect_origin"
}

/// How the rule is written when it is to do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Write {
    /// The zone has no cache rules: create the rule set with this one rule.
    Put,
    /// The zone has cache rules: add this one after them.
    Post,
}

/// What reading the zone's cache rule set said.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Zone {
    pub state: State,
    /// Set when the state is to do.
    pub write: Option<Write>,
    /// Some other rule caches pages (step 4b then also clears Cloudflare; PREREAD M4).
    /// Counted when unsure: clearing one more layer harms nothing.
    pub html_cache: bool,
}

/// Judges the zone's cache rule set as read (`GET …/rulesets/phases/
/// http_request_cache_settings/entrypoint`): `None` = the API said there is none (404);
/// `Some(answer)` = the answer's JSON. Anything unreadable stops: a rule set that was not
/// read is never put over.
pub fn judge(entry: Option<&Value>) -> Zone {
    let Some(v) = entry else {
        return Zone {
            state: State::Todo,
            write: Some(Write::Put),
            html_cache: false,
        };
    };
    let Some(rules) = v["result"]["rules"]
        .as_array()
        .or_else(|| (v["success"] == true && v["result"].is_object()).then_some(&EMPTY))
    else {
        return Zone {
            state: State::Stop("cf_rules_unreadable".into()),
            write: None,
            html_cache: true,
        };
    };
    let html_cache = rules.iter().any(|r| {
        !is_e2(r)
            && r["enabled"] != false
            && r["action"] == "set_cache_settings"
            && r["action_parameters"]["cache"] != false
    });
    let e2: Vec<&Value> = rules.iter().filter(|r| is_e2(r)).collect();
    let (state, write) = match (e2.len(), e2.iter().any(|r| is_standard(r))) {
        (0, _) if rules.is_empty() => (State::Todo, Some(Write::Put)),
        (0, _) => (State::Todo, Some(Write::Post)),
        (_, true) => (State::Done, None),
        (_, false) => (State::Warn("cf_rule_differs_from_standard"), None),
    };
    Zone {
        state,
        write,
        html_cache,
    }
}

static EMPTY: Vec<Value> = Vec::new();

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(rules: Value) -> Value {
        json!({"success": true, "result": {"id": "rs1", "phase": "http_request_cache_settings", "rules": rules}})
    }

    /// The first shop's rule, as the API lists it (11-24 read, f163c).
    fn first_shops_rule() -> Value {
        json!({
            "id": "r1",
            "expression": "starts_with(http.request.uri.path, \"/e2/\")",
            "description": "renamed by a person",
            "action": "set_cache_settings",
            "action_parameters": {"cache": true, "edge_ttl": {"mode": "respect_origin"}, "browser_ttl": {"mode": "respect_origin"}},
            "enabled": true
        })
    }

    fn html_rule() -> Value {
        json!({
            "id": "r0",
            "expression": "(http.request.uri.path eq \"/\") or not starts_with(http.request.uri.path, \"/wp-\")",
            "action": "set_cache_settings",
            "action_parameters": {"cache": true, "edge_ttl": {"mode": "override_origin", "default": 7200}},
            "enabled": true
        })
    }

    /// Never PUT over rules the zone already has (SPEC r1 2b).
    #[test]
    fn rules_already_there_are_added_to_never_put_over() {
        let z = judge(Some(&entry(json!([html_rule()]))));
        assert_eq!(z.state, State::Todo);
        assert_eq!(
            z.write,
            Some(Write::Post),
            "reason: put_over_existing_rules"
        );
        assert!(z.html_cache, "reason: page_cache_rule_missed");
        let z = judge(Some(&entry(json!([]))));
        assert_eq!(z.write, Some(Write::Put));
        assert!(!z.html_cache);
        let z = judge(None);
        assert_eq!(z.write, Some(Write::Put));
        let z = judge(Some(
            &json!({"success": false, "errors": [{"code": 10000}]}),
        ));
        assert_eq!(
            z.state,
            State::Stop("cf_rules_unreadable".into()),
            "reason: unread_zone_written"
        );
        assert_eq!(z.write, None);
    }

    /// PREREAD L2: matched by expression, whatever it is called and however it is spaced.
    #[test]
    fn the_rule_is_found_by_its_expression_not_its_name() {
        let z = judge(Some(&entry(json!([html_rule(), first_shops_rule()]))));
        assert_eq!(z.state, State::Done, "reason: renamed_rule_not_found");
        let mut spaced = first_shops_rule();
        spaced["expression"] = json!("STARTS_WITH( http.request.uri.path ,\"/e2/\" )");
        assert_eq!(judge(Some(&entry(json!([spaced])))).state, State::Done);
        let mut other_path = first_shops_rule();
        other_path["expression"] = json!("starts_with(http.request.uri.path, \"/E2/\")");
        assert_eq!(
            judge(Some(&entry(json!([other_path])))).state,
            State::Todo,
            "reason: quoted_path_case_folded"
        );
        assert!(!judge(Some(&entry(json!([first_shops_rule()])))).html_cache);
    }

    /// PREREAD L1: the second shop's rule has only the browser time: shown, not changed.
    #[test]
    fn a_rule_with_other_settings_is_shown_and_not_changed() {
        let mut browser_only = first_shops_rule();
        browser_only["action_parameters"] = json!({"browser_ttl": {"mode": "respect_origin"}});
        let z = judge(Some(&entry(json!([browser_only]))));
        assert_eq!(z.state, State::Warn("cf_rule_differs_from_standard"));
        assert_eq!(z.write, None, "reason: differing_rule_rewritten");
        let mut off = first_shops_rule();
        off["enabled"] = json!(false);
        assert_eq!(
            judge(Some(&entry(json!([off])))).state,
            State::Warn("cf_rule_differs_from_standard"),
            "reason: disabled_rule_counted"
        );
        assert!(is_standard(&standard_rule()));
        assert!(is_e2(&standard_rule()));
    }
}
