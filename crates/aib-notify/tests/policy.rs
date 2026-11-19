//! 1.x `ez2-ai-b/tests/test-notify.py` ClassifierTest + ActionAndStateTest, carried over as the
//! acceptance tests (B1dm-notify: same logic, not redesigned). The file / side-effect tests of
//! 1.x (rule-file owner / mode / symlink, switch file, audit log, adapters) are not here: those
//! parts are not in this crate.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_notify::{classify, parse_rules, parse_state, plan_actions, Rules, State, LEVELS};
use serde_json::{json, Value};

fn rules() -> Rules {
    Rules::builtin().unwrap()
}

fn level(r: Value) -> &'static str {
    classify(&r, &rules()).unwrap().level
}

fn ai_message(text: &str, level: Option<&str>, reason: Option<&str>) -> Value {
    let mut r = json!({
        "kind": "message.inbound",
        "message_id": "m-1",
        "actor": {"kind": "main", "ai_id": "codex:test:00000000-0000-0000-0000-000000000001", "sig": "pm@00001/Codex"},
        "recipient": {"kind": "main", "ai_id": "claude:test:2"},
        "text": text,
    });
    if let Some(l) = level {
        r["requested_level"] = json!(l);
    }
    if let Some(x) = reason {
        r["escalation_reason"] = json!(x);
    }
    r
}

#[test]
fn four_level_fixtures_and_strictest_wins() {
    assert_eq!(
        level(json!({"signal_type": "pane_dead", "text": "pane died"})),
        "red"
    );
    assert_eq!(
        level(json!({"actor": {"kind": "human"}, "text": "請裁決"})),
        "yellow"
    );
    assert_eq!(
        level(json!({"kind": "task.verified", "text": "完成且 CI success"})),
        "green"
    );
    assert_eq!(level(ai_message("例行同步", None, None)), "blue");
    assert_eq!(
        level(
            json!({"signal_type": "pane_dead", "actor": {"kind": "human"}, "text": "完成且全綠"})
        ),
        "red"
    );
    assert_eq!(LEVELS, ["red", "yellow", "green", "blue"]);
}

#[test]
fn yellow_wins_over_green_and_blue() {
    assert_eq!(
        level(json!({"text": "[人類] 驗收請示,先前工作已完成"})),
        "yellow"
    );
}

#[test]
fn ai_to_ai_is_blue_without_structured_escalation() {
    for text in [
        "BLOCKED P1 安全事故",
        "authority failure pane dead",
        "完成全綠",
    ] {
        let r = classify(&ai_message(text, None, None), &rules()).unwrap();
        assert_eq!(r.level, "blue", "{text}");
        assert!(
            r.reason_codes.iter().any(|c| c == "ai_routine_blue_lock"),
            "{text}"
        );
    }
}

#[test]
fn ai_escalation_requires_nonblank_reason() {
    for l in ["red", "yellow"] {
        for reason in [None, Some(""), Some("   ")] {
            let r = classify(&ai_message("例行同步", Some(l), reason), &rules()).unwrap();
            assert_eq!(r.level, "blue", "{l} {reason:?}");
            assert!(r.audit_required);
            assert_eq!(r.audit_code, Some("missing_escalation_reason"));
        }
    }
    let red = classify(
        &ai_message("請立即處理", Some("red"), Some("活體 pane 已死亡")),
        &rules(),
    )
    .unwrap();
    let yellow = classify(
        &ai_message("請裁決", Some("yellow"), Some("等待真人選 A/B")),
        &rules(),
    )
    .unwrap();
    assert_eq!(red.level, "red");
    assert_eq!(yellow.level, "yellow");
    assert!(red.audit_required && yellow.audit_required);
}

#[test]
fn requested_level_can_raise_but_not_lower_detected_severity() {
    let r = json!({"signal_type": "authority_failure", "requested_level": "green", "text": "authority failed"});
    assert_eq!(level(r), "red");
}

#[test]
fn rules_are_data_driven_and_strict() {
    // the shipped file checks; level order and the four actions are fixed
    let text: Value = serde_json::from_str(aib_notify::BUILTIN_RULES).unwrap();
    assert_eq!(text["level_order"], json!(LEVELS));
    assert_eq!(text["actions"].as_object().unwrap().len(), 4);
    // a few broken files are refused, never half-loaded
    let mut bad = text.clone();
    bad["level_order"] = json!(["yellow", "red", "green", "blue"]);
    assert!(parse_rules(&bad.to_string()).is_err());
    let mut bad = text.clone();
    bad["rules"][0]["level"] = json!("purple");
    assert!(parse_rules(&bad.to_string()).is_err());
    let mut bad = text.clone();
    bad["rules"][1]["id"] = json!("red.signal");
    assert!(parse_rules(&bad.to_string()).is_err());
    let mut bad = text.clone();
    bad["actions"]["blue"]["flash_background"] = json!(1);
    assert!(parse_rules(&bad.to_string()).is_err());
    assert!(parse_rules("not json").is_err());
}

const EXPECTED: [(&str, bool, Option<&str>, bool, u32); 4] = [
    ("red", true, Some("Basso"), true, 8000),
    ("yellow", true, Some("Tink"), true, 6000),
    ("green", false, None, true, 4000),
    ("blue", false, None, false, 0),
];

#[test]
fn action_profiles() {
    for (l, sys, sound, flash, ms) in EXPECTED {
        let p = plan_actions(l, &State::default(), &rules()).unwrap();
        let a = &p.action;
        assert_eq!(
            (
                a.system_notification,
                a.sound.as_deref(),
                a.flash,
                a.duration_ms
            ),
            (sys, sound, flash, ms),
            "{l}"
        );
    }
}

#[test]
fn all_32_switch_combinations() {
    for total in [false, true] {
        for mask in 0..16u8 {
            let mut s = State {
                enabled: total,
                ..State::default()
            };
            for bit in 0..4 {
                s.levels[bit] = mask & (1 << bit) != 0;
            }
            for (i, l) in LEVELS.iter().enumerate() {
                let p = plan_actions(l, &s, &rules()).unwrap();
                let expected = s.enabled && s.levels[i];
                assert_eq!(p.enabled, expected, "{total} {mask} {l}");
                if !expected {
                    assert!(!p.action.system_notification && !p.action.flash);
                    assert!(p.action.sound.is_none());
                }
            }
        }
    }
}

#[test]
fn switch_record_fails_closed() {
    let good = json!({"schema_version": "1", "enabled": true, "levels": {"red": true, "yellow": true, "green": true, "blue": true}});
    assert_eq!(parse_state(&good).unwrap(), State::default());
    for bad in [
        json!("not json"),
        json!({}),
        json!({"schema_version": "2", "enabled": true, "levels": good["levels"]}),
        json!({"schema_version": "1", "enabled": "yes", "levels": good["levels"]}),
        json!({"schema_version": "1", "enabled": true, "levels": {"red": true}}),
        json!({"schema_version": "1", "enabled": true, "levels": {"red": 1, "yellow": true, "green": true, "blue": true}}),
    ] {
        assert!(parse_state(&bad).is_err(), "{bad}");
    }
    assert!(plan_actions("purple", &State::default(), &rules()).is_err());
}

#[test]
fn word_edge_patterns_match_like_python() {
    // 1.x look-around (?<![A-Za-z0-9_])BLOCKED(?![A-Za-z0-9_]) — whole word only, any case
    assert_eq!(level(json!({"text": "status: blocked."})), "red");
    assert_eq!(level(json!({"text": "UNBLOCKED now"})), "blue");
    assert_eq!(level(json!({"text": "P1"})), "red");
    assert_eq!(level(json!({"text": "AP12"})), "blue");
}
