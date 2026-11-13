// SPDX-License-Identifier: Apache-2.0
//! `aib_title::beat` against the frozen `title/title_beat.py`, case for case.
//!
//! tests/fixtures/beat-golden.json holds what the Python gave for every case (function,
//! arguments, answer); it was written once from the frozen module, with
//! `seat_address.label_of` replaced by the seat's `_label` field so the label path is
//! driven from the table. Nothing here runs Python. When the Python and this module part,
//! the case says which function, which arguments and both answers.

#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_title::beat::{self, Level, Light, SeatSignals, TitleParts, TrustRecord};
use serde_json::{json, Map, Value};

fn cases() -> Vec<Value> {
    let text = include_str!("fixtures/beat-golden.json");
    serde_json::from_str(text).expect("fixture is JSON")
}

fn s(v: &Value) -> Option<&str> {
    v.as_str()
}

fn f(v: &Value) -> Option<f64> {
    v.as_f64()
}

fn b3(v: &Value) -> Option<bool> {
    v.as_bool()
}

fn light_json(l: &Light) -> Value {
    json!({"level": l.level.as_str(), "code": l.code, "detail": l.detail})
}

fn light_from(v: &Value) -> Light {
    let level = match v["level"].as_str() {
        Some("red") => Level::Red,
        Some("yellow") => Level::Yellow,
        Some("green") => Level::Green,
        other => panic!("level {other:?}"),
    };
    let code: &'static str = Box::leak(v["code"].as_str().unwrap().to_string().into_boxed_str());
    Light {
        level,
        code,
        detail: v["detail"].as_str().unwrap_or("").into(),
    }
}

fn rec_json(r: &beat::SeatRecord) -> Value {
    let mut m = Map::new();
    m.insert("sig".into(), json!(r.sig));
    m.insert(
        "seat_trust".into(),
        r.seat_trust.clone().unwrap_or(Value::Null),
    );
    if let Some(b) = r.beat_epoch {
        m.insert("beat_epoch".into(), json!(b));
    }
    Value::Object(m)
}

fn rec_from(v: &Value) -> Option<beat::SeatRecord> {
    let o = v.as_object()?;
    Some(beat::SeatRecord {
        sig: o.get("sig").and_then(Value::as_str).unwrap_or("").into(),
        seat_trust: o.get("seat_trust").cloned(),
        beat_epoch: o.get("beat_epoch").and_then(Value::as_i64),
    })
}

/// Rust's answer for one case, in the shape the Python gave it.
fn run(c: &Value) -> Value {
    let a = &c["args"];
    match c["fn"].as_str().unwrap() {
        "trust_light" => {
            let rec = a["record"].as_object().map(|r| TrustRecord {
                beat_epoch: r.get("beat_epoch").and_then(Value::as_i64),
                registry_verified: r.get("registry_verified").and_then(Value::as_bool),
            });
            light_json(&beat::trust_light(
                rec.as_ref(),
                s(&a["bind"]),
                a["now_ms"].as_i64().unwrap(),
            ))
        }
        "seat_record" => {
            let sg = SeatSignals {
                label: s(&a["label"]),
                pid_alive: a["pid_alive"].as_bool().unwrap_or(false),
                transcript_age_s: f(&a["transcript_age_s"]),
                now_s: f(&a["now_s"]),
                persona: s(&a["persona"]),
                rank_hint: s(&a["rank_hint"]),
                model: s(&a["model"]),
            };
            let rec = beat::seat_record(&a["seat"], &sg);
            let nows = [
                1_794_576_000_123_i64,
                (1_794_576_000 + 3600) * 1000,
                (1_794_576_000 + 3601) * 1000 + 1,
            ];
            let lights: Vec<Value> = nows
                .iter()
                .map(|n| light_json(&beat::seat_light(rec.as_ref(), *n)))
                .collect();
            json!({"rec": rec.as_ref().map_or(Value::Null, rec_json), "lights": lights})
        }
        "seat_light" => light_json(&beat::seat_light(
            rec_from(&a["rec"]).as_ref(),
            a["now_ms"].as_i64().unwrap(),
        )),
        "cabin_label" => json!(beat::cabin_label(s(&a["instance"]))),
        "uptime_str" => json!(beat::uptime_str(f(&a["started"]), f(&a["now_s"]).unwrap())),
        "cabin_since" => {
            let parts = a["parts"].as_array().map(|p| {
                let n = |i: usize| p[i].as_u64().unwrap() as u32;
                (n(0), n(1), n(2), n(3), n(4))
            });
            let lt = move |_e: i64| parts;
            let inj: Option<&dyn Fn(i64) -> Option<beat::LocalParts>> =
                if a["inject"].as_bool().unwrap() {
                    Some(&lt)
                } else {
                    None
                };
            json!(beat::cabin_since(f(&a["epoch"]), inj))
        }
        "title_string" => {
            let p = TitleParts {
                role: s(&a["role"]),
                color: a["color"].as_bool().unwrap(),
                cabin: s(&a["cabin"]),
                since: s(&a["since"]),
                uptime: s(&a["uptime"]),
                version: s(&a["version"]),
                addr: s(&a["addr"]),
                public: s(&a["public"]),
            };
            json!(beat::title_string(
                s(&a["sig"]),
                &light_from(&a["light"]),
                a["beat"].as_i64().unwrap(),
                &p
            ))
        }
        "model_label" => json!(beat::model_label(s(&a["model"]))),
        "short_version" => json!(beat::short_version(s(&a["version"]))),
        "version_segment" => json!(beat::version_segment(
            s(&a["version"]),
            b3(&a["behind"]),
            b3(&a["changed"])
        )),
        "version_state" => json!(beat::version_state(b3(&a["behind"]), b3(&a["changed"]))),
        other => panic!("no such function in the table: {other}"),
    }
}

#[test]
fn every_case_matches_the_frozen_python() {
    let all = cases();
    assert!(
        all.len() > 1000,
        "the table is the frozen Python's, {} cases",
        all.len()
    );
    let mut bad = Vec::new();
    for c in &all {
        let got = run(c);
        if got != c["want"] {
            bad.push(format!(
                "{} {}\n  python: {}\n  rust:   {}",
                c["fn"], c["args"], c["want"], got
            ));
        }
    }
    assert!(
        bad.is_empty(),
        "{} of {} cases differ:\n{}",
        bad.len(),
        all.len(),
        bad.iter().take(10).cloned().collect::<Vec<_>>().join("\n")
    );
}

#[test]
fn every_function_the_sampler_calls_is_in_the_table() {
    let all = cases();
    for name in [
        "trust_light",
        "seat_record",
        "seat_light",
        "cabin_label",
        "cabin_since",
        "uptime_str",
        "title_string",
        "model_label",
        "short_version",
        "version_segment",
        "version_state",
    ] {
        let n = all.iter().filter(|c| c["fn"] == name).count();
        assert!(n > 0, "{name} has no case");
    }
}

/// Cases the table leaves out on purpose (the sampler never gives them), kept by hand.
#[test]
fn an_empty_sig_prints_a_question_mark_like_none() {
    let l = beat::seat_light(None, 0);
    let p = TitleParts::default();
    assert_eq!(
        beat::title_string(Some(""), &l, 0, &p),
        beat::title_string(None, &l, 0, &p)
    );
    assert!(beat::title_string(None, &l, 0, &p).contains(" ? "));
}
