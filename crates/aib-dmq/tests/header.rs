#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! B1dm-header acceptance: the old tests that read or write the header carried over
//! (test-dm-xhost U5 / U5b, test-codex-tidy-human's native header, test-dm-triage-auto's
//! system / person kinds), and the answers the Python dm_header.py gave for labels, times,
//! headers and parses (fixtures/header-py-vectors.json, made from aib/src/cockpit at this commit).

use aib_dmq::header::{self as h, FromLabels, Party};
use serde_json::Value;

fn vectors() -> Value {
    serde_json::from_str(include_str!("fixtures/header-py-vectors.json")).unwrap()
}

fn party_of(v: &Value) -> Party {
    let s = |k: &str| v[k].as_str().unwrap().to_owned();
    Party {
        addr: s("addr"),
        cabin: s("cabin"),
        name: s("name"),
        code: s("code"),
    }
}

/// test-dm-xhost U5: across machines the sender shows the remote address, its cabin and the
/// note; a reply parses back to the sender's code; on one machine the header is unchanged.
#[test]
fn across_machines_the_sender_shows_where_it_came_from() {
    let o = FromLabels {
        s_socket: "aihub-20261013-931e",
        t_socket: "aihub-20261014-133230-6ddf",
        s_addr: "100.64.0.2",
        s_note: "跨主機·經SSH",
        local_addr: "100.64.0.1",
        ..FromLabels::default()
    };
    let hd = h::build_from_labels("@8f366(第二艦長)", "@cc203(心+指揮官)", &o, 0).unwrap();
    assert!(
        hd.starts_with(
            "IP:100.64.0.2/931e/第二艦長(跨主機·經SSH)@8f366 >> IP:100.64.0.1/6ddf/心+指揮官@cc203 "
        ),
        "{hd}"
    );
    let got = h::parse(&format!("{hd} 你好")).unwrap();
    assert_eq!(
        (got.sender.code.as_str(), got.body.as_str()),
        ("8f366", "你好")
    );
    let same = FromLabels {
        local_addr: "10.0.0.5",
        ..FromLabels::default()
    };
    let hd = h::build_from_labels("@8f366(第二艦長)", "@cc203(心+指揮官)", &same, 0).unwrap();
    assert!(hd.starts_with("IP:10.0.0.5//第二艦長@8f366 >> "), "{hd}");
}

/// test-dm-xhost U5b: a sender cabin that cannot be told is blank, never the receiver's.
#[test]
fn an_unknown_sender_cabin_is_blank_not_the_receivers() {
    for (sock, cabin) in [
        ("", ""),
        ("dmtest", ""),
        ("aihub", "main"),
        ("aihub-20261014-133230-6ddf", "6ddf"),
    ] {
        let o = FromLabels {
            s_socket: sock,
            t_socket: "aihub-20261010-125119-931e",
            s_addr: "100.64.0.1",
            s_note: "跨主機·經SSH",
            local_addr: "100.64.0.2",
            local_cabin: "931e",
            ..FromLabels::default()
        };
        let hd = h::build_from_labels("unattested:unknown", "@8f366(第二艦長)", &o, 0).unwrap();
        assert!(
            hd.starts_with(&format!("IP:100.64.0.1/{cabin}/")),
            "{sock} {hd}"
        );
    }
}

/// test-codex-tidy-human / test-dm-triage-auto: 系統@aib is the system, hub a person, a seat code
/// a seat; the time is Taipei's, no seconds.
#[test]
fn the_sender_kind_comes_from_the_third_part() {
    let p = h::parse(
        "IP:127.0.0.1/abcd/系統@aib >> IP:127.0.0.1/abcd/工程師@abcde 10-26 17:29(台北時間) 機器提醒",
    )
    .unwrap();
    assert_eq!(
        (p.form, p.sender_kind, p.body.as_str()),
        ("v2", "system", "機器提醒")
    );
    let at = |code: &str, name: &str| {
        let s = h::party(code, name, "100.64.0.201", "559f");
        let r = h::party("aaaaa", "工程師", "100.64.0.201", "559f");
        h::parse(&h::build(&s, &r, 1_761_900_000, h::DEFAULT_TZ).unwrap()).unwrap()
    };
    assert_eq!(at(h::HUMAN_CODE, h::HUMAN_NAME).sender_kind, "human");
    assert_eq!(at("b4297", "").sender_kind, "seat");
    assert_eq!(at("b4297", "").sender.name, h::UNBOUND_NAME);
    assert_eq!(at("grokbot", "GrokBot").sender_kind, "other");
    assert_eq!(
        h::format_when(0, "Asia/Taipei").unwrap(),
        "01-01 08:00(台北時間)"
    );
    assert!(
        h::side(&h::party("", "x", "", "")).is_err(),
        "reason: empty_code_built"
    );
}

#[test]
fn the_same_answers_as_the_python_header() {
    let v = vectors();
    let n = |k: &str| v[k].as_array().map_or(0, Vec::len);
    assert_eq!(
        (
            n("labels"),
            n("whens"),
            n("builds"),
            n("parses"),
            n("from_labels")
        ),
        (125, 94, 150, 1356, 119),
        "reason: vectors_lost"
    );
    for r in v["labels"].as_array().unwrap() {
        let got = h::party_from_label(r["label"].as_str().unwrap(), r["origin"].as_str().unwrap());
        assert_eq!(got, party_of(&r["party"]), "reason: label {r}");
    }
    for r in v["whens"].as_array().unwrap() {
        let got = h::format_when(r["epoch"].as_i64().unwrap(), r["tz"].as_str().unwrap());
        assert_eq!(got.as_deref(), r["out"].as_str(), "reason: when {r}");
    }
    for r in v["builds"].as_array().unwrap() {
        let got = h::build(
            &party_of(&r["s"]),
            &party_of(&r["r"]),
            r["epoch"].as_i64().unwrap(),
            r["tz"].as_str().unwrap(),
        )
        .ok();
        assert_eq!(got.as_deref(), r["out"].as_str(), "reason: build {r}");
    }
    for r in v["parses"].as_array().unwrap() {
        let text = r["text"].as_str().unwrap();
        let got = h::parse(text).map(|p| {
            let pj = |p: &Party| {
                serde_json::json!({"addr": p.addr, "cabin": p.cabin, "name": p.name, "code": p.code})
            };
            serde_json::json!({"form": p.form, "sender": pj(&p.sender), "receiver": pj(&p.receiver),
                "when": p.when, "tz": p.tz, "sender_kind": p.sender_kind, "body": p.body})
        });
        assert_eq!(
            got.unwrap_or(Value::Null),
            r["out"],
            "reason: parse {text:?}"
        );
    }
    for r in v["cabins"].as_array().unwrap() {
        let i = r["in"].as_str().unwrap();
        assert_eq!(h::cabin_tail(i), r["tail"].as_str().unwrap(), "{i}");
        assert_eq!(
            h::cabin_of_socket(&format!("aihub-{i}"), "loc1"),
            r["sock"].as_str().unwrap(),
            "{i}"
        );
    }
    for r in v["from_labels"].as_array().unwrap() {
        let s = |k: &str| r[k].as_str().unwrap();
        let o = FromLabels {
            s_origin: s("origin"),
            s_socket: s("ss"),
            t_socket: s("ts"),
            s_addr: s("sa"),
            s_note: s("note"),
            local_addr: s("la"),
            local_cabin: s("local_cabin"),
        };
        let got = h::build_from_labels(s("s"), s("t"), &o, r["epoch"].as_i64().unwrap()).ok();
        assert_eq!(got.as_deref(), r["out"].as_str(), "reason: from_labels {r}");
    }
}
