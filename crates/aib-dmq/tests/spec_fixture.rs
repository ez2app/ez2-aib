//! Every cell of aib/tests/fixtures/dm-spec/transitions-v1.json (made from the frozen 1.x
//! Python queue by aib/tools/verify/dm_spec.py) through the Rust core: same verdict, same
//! reason, same changed fields, same order.json membership, same receipt. The header
//! tables (statuses, active, transitions, reasons, rev cases) must match too.
//!
//! The harness copies how the generator built each cell: a delivered item recorded at T0
//! with the status overwritten, `global` / `im` set by the flag, `no_rev_check`, the
//! caller-is-a-seat gate on only for the `human:from-seat` actor, receipts recorded.

use std::path::{Path, PathBuf};

use aib_dmq::evidence::{classify, Probe};
use aib_dmq::norm::{upgrade, v2_defaults};
use aib_dmq::rev::{rev_check, IfRev, Presence};
use aib_dmq::spec::{allowed, Reason, Status, ITEM_SCHEMA};
use aib_dmq::transition::{decide, Decision, Request};
use aib_dmq::ActorClass;
use serde_json::{json, Map, Value};

const SEAT: &str = "aaaaa";
const NOW: i64 = 1_792_930_000;
const T0: i64 = NOW - 3600;

fn fixture() -> Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../aib/tests/fixtures/dm-spec/transitions-v1.json");
    let text = std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {}: {e}", p.display()))
}

struct Fs;
impl Probe for Fs {
    fn is_file(&self, text: &str) -> bool {
        Path::new(text).is_file()
    }
    fn commit_exists(&self, _sha: &str) -> bool {
        false // not covered by the fixture
    }
    fn reply_sent_by(&self, _seat: &str, _id: &str) -> bool {
        false // not covered by the fixture
    }
}

struct Sandbox {
    _dir: tempfile::TempDir,
    root: String,
    evfile: PathBuf,
}

fn sandbox() -> Sandbox {
    let dir = tempfile::Builder::new()
        .prefix("aib-dm-spec-")
        .tempdir()
        .expect("tempdir");
    let evfile = dir.path().join("evidence.txt");
    std::fs::write(&evfile, "evidence\n").expect("write evidence");
    Sandbox {
        root: dir.path().to_string_lossy().into_owned(),
        evfile,
        _dir: dir,
    }
}

fn base_item(from: &str, flag: &str) -> Map<String, Value> {
    let mut m = v2_defaults();
    for (k, v) in [
        ("schema", json!(ITEM_SCHEMA)),
        ("id", json!("q_00000000000000aa")),
        ("seat", json!(SEAT)),
        ("msg", json!("對照表")),
        ("sender", json!("agent:offhub")),
        ("sender_kind", json!("unknown")),
        ("ts", json!(T0)),
        ("sent_at", json!(T0)),
        ("delivered_at", json!(T0)),
        ("claimed_at", Value::Null),
        ("claim_id", Value::Null),
        ("rev", json!(1)),
        ("status", json!(from)),
    ] {
        m.insert(k.into(), v);
    }
    m.insert(
        "global".into(),
        if flag == "global" {
            json!(true)
        } else {
            Value::Null
        },
    );
    if flag == "im" {
        m.insert("im".into(), json!({"kind": "telegram"}));
    }
    m
}

fn norm(v: &Value, root: &str) -> Value {
    match v {
        Value::String(s) if s.contains(root) => json!("<str>"),
        Value::Number(n) if n.as_i64() == Some(NOW) => json!("<now>"),
        Value::Number(n) if n.as_i64() == Some(T0) => json!("<t0>"),
        Value::Null | Value::Bool(_) => v.clone(),
        Value::Number(n) if n.is_i64() || n.is_u64() => v.clone(),
        Value::String(s) if s.chars().count() <= 40 => v.clone(),
        Value::String(_) => json!("<str>"),
        Value::Number(_) => json!("<float>"),
        Value::Array(_) => json!("<list>"),
        Value::Object(_) => json!("<dict>"),
    }
}

/// _changes: keys whose value differs (after both sides are upgraded), `_` keys skipped.
fn changes(before: &Map<String, Value>, after: &Map<String, Value>, root: &str) -> Value {
    let (b, _) = upgrade(before);
    let (a, _) = upgrade(after);
    let mut keys: Vec<&String> = b.keys().chain(a.keys()).collect();
    keys.sort();
    keys.dedup();
    let mut out = Map::new();
    for k in keys {
        if k.starts_with('_') || b.get(k) == a.get(k) {
            continue;
        }
        out.insert(
            k.clone(),
            a.get(k).map_or(json!("<removed>"), |v| norm(v, root)),
        );
    }
    Value::Object(out)
}

fn s(row: &[Value], i: usize) -> &str {
    row[i]
        .as_str()
        .unwrap_or_else(|| panic!("cell field {i} not a string: {row:?}"))
}

#[test]
fn every_cell_matches_the_python_queue() {
    let spec = fixture();
    let bx = sandbox();
    let actors = spec["actors"].as_object().expect("actors");
    let whys = spec["whys"].as_object().expect("whys");
    let cells = spec["cells"].as_array().expect("cells");
    assert_eq!(
        cells.len(),
        7910,
        "the fixture changed size: regenerate the contract on purpose"
    );
    let mut bad = Vec::new();
    for c in cells {
        let row = c.as_array().expect("cell row");
        let (frm, to, ak, wk, ek, flag, want) = (
            s(row, 0),
            s(row, 1),
            s(row, 2),
            s(row, 3),
            s(row, 4),
            s(row, 5),
            s(row, 6),
        );
        let item = base_item(frm, flag);
        let found = if frm == "deleted" {
            Presence::Tombstone
        } else {
            Presence::Live { rev: 1 }
        };
        let evidence: Option<String> = match ek {
            "none" => None,
            "file" => Some(bx.evfile.to_string_lossy().into_owned()),
            "pr" => Some("https://github.com/ez2app/ez2-aib/pull/1".into()),
            "text" => Some("看過了".into()),
            other => panic!("evidence kind {other}"),
        };
        let to_st = Status::parse(to).expect("to status");
        let (ok, reason, _) = rev_check(found, IfRev::Absent, true);
        let got: Value = if !ok {
            json!([reason.as_str()])
        } else {
            let ev_ref = if to_st == Status::Done {
                classify(SEAT, evidence.as_deref(), &Fs)
            } else {
                (None, None)
            };
            let req = Request {
                seat: SEAT,
                to: to_st,
                actor: actors[ak].as_str(),
                why: whys[wk].as_str(),
                evidence: evidence.as_deref(),
                evidence_ref: ev_ref,
                expect_status: None,
                caller_is_seat: ak == "human:from-seat",
                now: NOW,
            };
            match decide(&item, &req) {
                Decision::Refuse { reason, .. } => json!([reason.as_str()]),
                Decision::Allow(o) => {
                    let mut extra = json!({
                        "in_order": o.from.is_active() && !o.leaves_order,
                        "set": changes(&item, &o.item, &bx.root),
                    });
                    if let Some(r) = o.receipt {
                        extra["receipt"] = json!([[
                            r.state.as_str(),
                            if r.by_human {
                                json!("human")
                            } else {
                                Value::Null
                            }
                        ]]);
                    }
                    json!(["ok", extra])
                }
            }
        };
        let expect: Value = if row.len() > 7 {
            json!([want, row[7]])
        } else {
            json!([want])
        };
        if got != expect {
            bad.push(format!(
                "{frm}/{to}/{ak}/{wk}/{ek}/{flag}: want {expect} got {got}"
            ));
        }
    }
    assert!(
        bad.is_empty(),
        "{} of {} cells differ, first:\n{}",
        bad.len(),
        cells.len(),
        bad[..bad.len().min(12)].join("\n")
    );
}

#[test]
fn the_header_tables_match() {
    let spec = fixture();
    assert_eq!(spec["schema"], json!("aib-dm-spec-transitions/v1"));
    let statuses: Vec<&str> = Status::ALL.iter().map(|x| x.as_str()).collect();
    assert_eq!(spec["statuses"], json!(statuses));
    let active: Vec<&str> = Status::ALL
        .iter()
        .filter(|x| x.is_active())
        .map(|x| x.as_str())
        .collect();
    assert_eq!(spec["active"], json!(active));
    let mut table = Vec::new();
    for f in Status::ALL {
        for t in Status::ALL {
            if let Some(who) = allowed(f, t) {
                let who: Vec<&str> = who.iter().map(|c: &ActorClass| c.as_str()).collect();
                table.push(json!([f.as_str(), t.as_str(), who]));
            }
        }
    }
    table.sort_by_key(|v| v.to_string());
    let mut want = spec["transitions"].as_array().expect("transitions").clone();
    want.sort_by_key(|v| v.to_string());
    assert_eq!(Value::Array(table), Value::Array(want));
    let reasons: Vec<&str> = Reason::TRANSITION.iter().map(|r| r.as_str()).collect();
    assert_eq!(spec["reasons"], json!(reasons));
    let rev: Vec<&str> = Reason::REV.iter().map(|r| r.as_str()).collect();
    assert_eq!(spec["rev_reasons"], json!(rev));
}

#[test]
fn rev_cases_match() {
    let spec = fixture();
    let live = Presence::Live { rev: 1 };
    let got = [
        ("no_rev", rev_check(live, IfRev::Absent, false)),
        ("match", rev_check(live, IfRev::Value(1), false)),
        ("mismatch", rev_check(live, IfRev::Value(2), false)),
        ("garbage", rev_check(live, IfRev::parse(Some("x")), false)),
        (
            "missing",
            rev_check(Presence::Missing, IfRev::Value(1), false),
        ),
        (
            "tombstone-no-rev-check",
            rev_check(Presence::Tombstone, IfRev::Absent, true),
        ),
    ];
    let got: Vec<Value> = got
        .iter()
        .map(|(n, (ok, r, _))| json!({"case": n, "ok": ok, "reason": r.as_str()}))
        .collect();
    assert_eq!(spec["rev_cases"], json!(got));
}
