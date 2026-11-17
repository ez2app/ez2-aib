//! `verify_fix` (DECISION-council-gaps-519d0-1117 R2): act turns the open `fail` lines of the
//! plan's event ledger (`aib-plan fail`, written by the verifier) into 「驗不通過: <卡> <問題>
//! → <該修的人>」 dms to the seat that must fix it now (the event's `fixer`, not the union of
//! authors, Codex).
//!
//! - act only reads the ledger; a `fail` is open until a later line on the same card moves it
//!   to 實查通過 (the verifier passed it).
//! - the dedupe key is `verify_fix:<card>:<head>:<verifier>:<issue>`: the same finding on the
//!   same head is told once; a new head failed again is a new key.
//! - the seat rules of act apply (alive, unread ≤ [`crate::act::UNREAD_MAX`], not on deck);
//!   several findings for one seat in one round go in one dm.
//! - it rides the `notify_fix` switch (the council: 「用已開的通知修開關」) and a pause holds
//!   it back.
//! - dry run first: `verify_fix` is not in [`crate::act::SENDABLE`], so it is only journaled
//!   (the same way `unblock_notify` started).

use crate::act::{History, SeatsOut, Skip, Would, UNREAD_MAX};
use serde_json::Value;
use std::collections::BTreeMap;

/// The action name.
pub const ACTION: &str = "verify_fix";
/// The ledger act reads, under `<root>/`.
pub const LEDGER: &str = "pending/aib2-a63ab-1107/cabin-plan/plan-events.jsonl";
/// The rank skips of this source carry.
pub const SKIP_RANK: u64 = 0;
/// The stations that close a card's failed checks: passed, or the card dropped (5d33a N1 on
/// f59f50f9: a dropped card's fail stayed open for ever). Same list as unblock-u13 (N2).
const CLOSED: [&str; 2] = crate::unblock_u13::DONE;

/// The levels that are told (gaps §41: medium and up, and 「整卡未完、建議拆」 as `S`; pure
/// light / notes are not; 5d33a L2 on f59f50f9).
pub const TOLD_LEVELS: [&str; 3] = ["H", "M", "S"];

/// One open failed check.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Open {
    pub card: String,
    pub head: String,
    pub verifier: String,
    pub issue: String,
    pub level: String,
    pub fixer: String,
    pub fact: String,
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn seat_ok(x: &str) -> bool {
    x.len() == 5
        && x.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && x.bytes().any(|b| b.is_ascii_alphabetic())
        && x.bytes().any(|b| b.is_ascii_digit())
}

/// The open failed checks in the ledger's text, in the order they were written; a line that
/// is not JSON is skipped (a torn write must not hide the rest).
pub fn open_fails(text: &str) -> Vec<Open> {
    let mut open: BTreeMap<String, Vec<Open>> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();
    for v in text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
    {
        let card = s(&v, "id");
        match s(&v, "op").as_str() {
            "fail" => {
                let o = Open {
                    card: card.clone(),
                    head: s(&v, "head"),
                    verifier: s(&v, "verifier"),
                    issue: s(&v, "issue"),
                    level: s(&v, "level"),
                    fixer: s(&v, "fixer"),
                    fact: s(&v, "fact"),
                };
                if !order.contains(&card) {
                    order.push(card.clone());
                }
                let list = open.entry(card).or_default();
                if !list.contains(&o) {
                    list.push(o);
                }
            }
            _ if CLOSED.contains(&s(&v, "to").as_str()) => {
                open.remove(&card);
            }
            _ => {}
        }
    }
    order
        .iter()
        .filter_map(|c| open.remove(c))
        .flatten()
        .collect()
}

pub fn key(o: &Open) -> String {
    format!("{ACTION}:{}:{}:{}:{}", o.card, o.head, o.verifier, o.issue)
}

fn part_ok(x: &str) -> bool {
    !x.trim().is_empty() && !x.contains([':', '@', '\n', '\r'])
}

/// This round's `verify_fix` dms, added to `out`; findings not told go to `skips`.
pub fn plan(
    fails: &[Open],
    seats: &SeatsOut,
    hist: &History,
    out: &mut Vec<Would>,
    skips: &mut Vec<Skip>,
) {
    let mut skip = |why: String| {
        skips.push(Skip {
            rank: SKIP_RANK,
            why: format!("{ACTION}: {why}"),
        })
    };
    let live = crate::act::SENDABLE.contains(&ACTION);
    let mut per_seat: BTreeMap<String, Vec<&Open>> = BTreeMap::new();
    for o in fails {
        if ![&o.card, &o.head, &o.verifier, &o.issue]
            .iter()
            .all(|x| part_ok(x))
        {
            skip(format!(
                "a fail line without card / head / verifier / issue ({})",
                o.card
            ));
            continue;
        }
        if !TOLD_LEVELS.contains(&o.level.trim().to_ascii_uppercase().as_str()) {
            skip(format!(
                "{} {}: level {:?} is not told",
                o.card, o.issue, o.level
            ));
            continue;
        }
        if !seat_ok(&o.fixer) {
            skip(format!(
                "{}: fixer {:?} is not a 5-hex seat",
                o.card, o.fixer
            ));
            continue;
        }
        let to = o.fixer.as_str();
        let Some(seat) = seats.seats.iter().find(|x| x.seat == to) else {
            skip(format!("{to}: no seat facts"));
            continue;
        };
        if seat.alive.v != "yes" {
            skip(format!("{to}: alive {}", seat.alive.v));
            continue;
        }
        if seat.capacity.on_deck == Some(true) {
            skip(format!("{to}: on deck in the merge queue"));
            continue;
        }
        match seat.capacity.unread {
            Some(n) if n <= UNREAD_MAX => {}
            Some(n) => {
                skip(format!("{to}: {n} unread"));
                continue;
            }
            None => {
                skip(format!("{to}: unread unknown"));
                continue;
            }
        }
        let k = key(o);
        let told = if live {
            hist.told(&k)
        } else {
            hist.keys.contains(&k)
        };
        if told {
            skip(format!("already told ({k})"));
            continue;
        }
        per_seat.entry(to.to_owned()).or_default().push(o);
    }
    for (to, list) in per_seat {
        let first_line = match list.as_slice() {
            [o] => format!("驗不通過: {} {}({})→ {to}", o.card, o.issue, o.level),
            _ => format!("驗不通過: {} 條 → {to}", list.len()),
        };
        let body = list
            .iter()
            .map(|o| {
                format!(
                    "{} {}({})頭 {} 驗 {}:{}",
                    o.card,
                    o.issue,
                    o.level,
                    o.head.get(..8).unwrap_or(&o.head),
                    o.verifier,
                    o.fact
                )
            })
            .collect();
        let keys: Vec<String> = list.iter().map(|o| key(o)).collect();
        out.push(Would {
            action: ACTION.into(),
            to,
            first_line,
            body,
            send_key: keys[0].clone(),
            keys,
            // 5d33a L1 on f59f50f9: once live it counts toward the 30-minute limit, as unblock does
            throttled: live,
            ranks: Vec::new(),
            dm: None,
            pr_checks: None,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::act::{Capacity, Fact, Seat};

    const H1: &str = "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd";
    const H2: &str = "efefefefefefefefefefefefefefefefefefefef";

    fn fail(card: &str, head: &str, issue: &str, fixer: &str) -> String {
        serde_json::json!({"op": "fail", "id": card, "head": head, "issue": issue,
            "level": "M", "verifier": "5d33a", "fixer": fixer, "fact": "驗不通過 x"})
        .to_string()
    }

    fn seat(code: &str, unread: u64) -> Seat {
        Seat {
            seat: code.into(),
            alive: Fact { v: "yes".into() },
            capacity: Capacity {
                unread: Some(unread),
                on_deck: Some(false),
            },
            ..Seat::default()
        }
    }

    #[test]
    fn a_fail_is_open_until_the_card_is_passed() {
        let pass = serde_json::json!({"op": "set", "id": "S2", "to": "實查通過"}).to_string();
        let dropped = serde_json::json!({"op": "set", "id": "S9", "to": "作廢"}).to_string();
        let text = [
            fail("S2", H1, "M1", "a9136"),
            "not json".to_string(),
            fail("S3", H1, "M1", "043dd"),
            fail("S9", H1, "M1", "043dd"),
            pass,
            dropped,
            fail("S4", H2, "H1", "a9136"),
        ]
        .join("\n");
        let open = open_fails(&text);
        let cards: Vec<&str> = open.iter().map(|o| o.card.as_str()).collect();
        assert_eq!(cards, ["S3", "S4"]);
        // the same finding written twice is one
        let twice = [fail("S5", H1, "M1", "a9136"), fail("S5", H1, "M1", "a9136")].join("\n");
        assert_eq!(open_fails(&twice).len(), 1);
        // a new head failed again is a new key
        let again = [fail("S5", H1, "M1", "a9136"), fail("S5", H2, "M1", "a9136")].join("\n");
        let o = open_fails(&again);
        assert_ne!(key(&o[0]), key(&o[1]));
    }

    #[test]
    fn findings_go_to_their_fixer_once_and_one_dm_per_seat() {
        let text = [
            fail("S2", H1, "M1", "a9136"),
            fail("S4", H2, "H1", "a9136"),
            fail("S6", H1, "M2", "043dd"),
            fail("S7", H1, "M1", "b4297"),
            fail("S8", H1, "L1", "a9136").replace("\"M\"", "\"L\""),
        ]
        .join("\n");
        let fails = open_fails(&text);
        let seats = SeatsOut {
            seats: vec![seat("a9136", 0), seat("043dd", 99)],
            complete: true,
        };
        let mut hist = History::default();
        let (mut out, mut skips) = (Vec::new(), Vec::new());
        plan(&fails, &seats, &hist, &mut out, &mut skips);
        assert_eq!(out.len(), 1, "{out:?}");
        assert_eq!(out[0].to, "a9136");
        assert_eq!(out[0].keys.len(), 2);
        // dry: not counted toward the 30-minute limit (live: counted, as unblock)
        assert!(!out[0].throttled);
        assert!(out[0].first_line.contains("2 條"));
        assert!(skips.iter().any(|s| s.why.contains("043dd: 99 unread")));
        assert!(skips.iter().any(|s| s.why.contains("b4297: no seat facts")));
        // a light finding is not told (gaps §41)
        assert!(skips.iter().any(|s| s.why.contains("S8 L1: level")));
        // journaled once (dry): the next round does not tell it again
        hist.keys.extend(out[0].keys.iter().cloned());
        let (mut out2, mut skips2) = (Vec::new(), Vec::new());
        plan(&fails, &seats, &hist, &mut out2, &mut skips2);
        assert!(out2.is_empty());
        assert!(skips2.iter().any(|s| s.why.contains("already told")));
    }
}
