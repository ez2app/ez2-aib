//! U13 沒人負責 (DESIGN-S79-unblock-519d0-1115 §2b), nominate only: a plan-tree task that is
//! not finished and whose owner is empty, says 待定 / 待派 / 失聯, or names a seat that is
//! not alive. Each gets a first and a second choice with a one-line reason; nothing is
//! written to the plan tree and no one is sent anything (519d0: nominate for a day, compare
//! with the coordinator's own picks, ≥80% before dispatching directly).
//!
//! Until the role table (PLAN §12) is settled, "role fits" is read as "owns other tasks under
//! the same parent"; load is open PRs + planned + work in progress; the coordinator is never
//! nominated (DESIGN: not development hands).

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// At most this many nominations a round (DESIGN §2b).
pub const MAX_ROWS: usize = 5;
/// Statuses that need no owner any more.
pub const DONE: [&str; 2] = ["實查通過", "作廢"];
/// Node kinds that are work (not containers, ideas or extensions).
pub const WORK_KINDS: [&str; 2] = ["task", "followup"];

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

/// The seat an owner text names (`a9136`, `a9136;審 1ced7`, `a9136(提名)`): its first five
/// characters when they are a seat code.
fn owner_seat(owner: &str) -> Option<&str> {
    let head = owner.get(..5)?;
    let next = owner[5..].chars().next();
    (head.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
        && !next.is_some_and(|c| c.is_ascii_alphanumeric()))
    .then_some(head)
}

fn slug(id: &str) -> String {
    let t: String = id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    t.trim_matches('-').to_owned()
}

struct Seat<'a> {
    code: &'a str,
    load: u64,
    prs: u64,
}

/// Why this node has no one, `None` = it has someone.
fn nobody(owner: &str, alive: &BTreeSet<&str>) -> Option<String> {
    if owner.trim().is_empty() {
        return Some("owner 空".into());
    }
    if ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w)) {
        return Some(format!("owner 寫「{owner}」"));
    }
    match owner_seat(owner) {
        Some(seat) if !alive.contains(seat) => Some(format!("{seat} 不在或失聯")),
        _ => None,
    }
}

/// The U13 rows (merge-ready, `owner` empty, `candidates` = first and second choice).
pub fn rows(plan: &Value, seats: &Value, now_ms: i64) -> Vec<Value> {
    let nodes: Vec<&Value> = plan
        .get("nodes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .collect();
    let all_seats: Vec<&Value> = seats
        .get("seats")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .collect();
    let alive: BTreeSet<&str> = all_seats
        .iter()
        .filter(|x| x.pointer("/alive/v").and_then(Value::as_str) == Some("yes"))
        .map(|x| s(x, "seat"))
        .collect();
    let num = |x: &Value, p: &str| x.pointer(p).and_then(Value::as_u64).unwrap_or(0);
    let pool: Vec<Seat> = all_seats
        .iter()
        .filter(|x| alive.contains(s(x, "seat")) && s(x, "role") != "coordinator")
        .map(|x| Seat {
            code: s(x, "seat"),
            prs: num(x, "/capacity/open_prs"),
            load: num(x, "/capacity/open_prs")
                + num(x, "/capacity/planned")
                + x.pointer("/capacity/wip_nodes")
                    .and_then(Value::as_array)
                    .map_or(0, |a| a.len() as u64),
        })
        .collect();
    let open = |n: &Value| !DONE.contains(&s(n, "status"));
    let parents: BTreeSet<&str> = nodes.iter().map(|n| s(n, "parent")).collect();
    // how many unfinished nodes list this one in `after`
    let mut unlocks: BTreeMap<&str, usize> = BTreeMap::new();
    for n in nodes.iter().filter(|n| open(n)) {
        for a in n
            .get("after")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            *unlocks.entry(a).or_default() += 1;
        }
    }
    let mut found: Vec<(usize, &Value, String)> = nodes
        .iter()
        .filter(|n| open(n) && WORK_KINDS.contains(&s(n, "kind")))
        .filter(|n| !parents.contains(s(n, "id")))
        .filter_map(|n| {
            let why = nobody(s(n, "owner"), &alive)?;
            Some((unlocks.get(s(n, "id")).copied().unwrap_or(0), *n, why))
        })
        .collect();
    found.sort_by(|a, b| b.0.cmp(&a.0).then(s(a.1, "id").cmp(s(b.1, "id"))));
    found
        .into_iter()
        .take(MAX_ROWS)
        .map(|(unl, n, why)| {
            let lost = owner_seat(s(n, "owner"));
            let parent = s(n, "parent");
            let mut ranked: Vec<(i64, &Seat, u64)> = pool
                .iter()
                .filter(|c| Some(c.code) != lost)
                .map(|c| {
                    let sib = nodes
                        .iter()
                        .filter(|m| !parent.is_empty() && s(m, "parent") == parent)
                        .filter(|m| owner_seat(s(m, "owner")) == Some(c.code))
                        .count() as u64;
                    (sib as i64 * 3 - c.load as i64, c, sib)
                })
                .collect();
            ranked.sort_by(|a, b| {
                b.0.cmp(&a.0)
                    .then(a.1.prs.cmp(&b.1.prs))
                    .then(a.1.code.cmp(b.1.code))
            });
            let pick: Vec<String> = ranked
                .iter()
                .take(2)
                .map(|(_, c, sib)| {
                    format!(
                        "{}:同一大項 {sib} 件、開著 {} 張、負載 {}",
                        c.code, c.prs, c.load
                    )
                })
                .collect();
            let cands: Vec<&str> = ranked.iter().take(2).map(|(_, c, _)| c.code).collect();
            let id = s(n, "id");
            json!({
                "id": format!("U13:node-{}", slug(id)),
                "code": "U13",
                "subject": format!("node-{}", slug(id)),
                "what": format!("{id} {} 沒人負責({why});解開 {unl} 件", s(n, "title")),
                "fingerprint": format!("n-{}", cands.first().copied().unwrap_or("none")),
                "evidence": format!("cabin-plan data.json {id} owner={:?} status={}", s(n, "owner"), s(n, "status")),
                "owner": "",
                "candidates": cands,
                "nominee_why": pick,
                "since_ms": now_ms,
                "as_of_ms": now_ms,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seat(code: &str, alive: &str, role: &str, prs: u64, planned: u64) -> Value {
        json!({"seat": code, "alive": {"v": alive}, "role": role,
            "capacity": {"open_prs": prs, "planned": planned, "wip_nodes": []}})
    }

    fn node(id: &str, parent: &str, owner: Value, status: &str) -> Value {
        json!({"id": id, "parent": parent, "kind": "task", "title": format!("t{id}"),
            "owner": owner, "status": status})
    }

    fn seats() -> Value {
        json!({"seats": [seat("a9136", "yes", "dev", 2, 1), seat("b433b", "yes", "dev", 0, 0),
            seat("519d0", "yes", "coordinator", 0, 0), seat("2240e", "no", "dev", 0, 0),
            seat("ebb36", "yes", "dev", 9, 9)]})
    }

    fn ids(r: &[Value]) -> Vec<&str> {
        r.iter().map(|x| x["id"].as_str().unwrap()).collect()
    }

    #[test]
    fn empty_placeholder_and_lost_owners_have_nobody_and_live_ones_do() {
        let plan = json!({"nodes": [
            node("T1", "A", Value::Null, "未開始"),
            node("T2", "A", json!("(待派;原 2240e 失聯)"), "未開始"),
            node("T3", "A", json!("2240e"), "開發中"),
            node("T4", "A", json!("a9136;審 1ced7"), "開發中"),
            node("T5", "A", Value::Null, "實查通過"),
            node("T6", "A", json!("Andy"), "未開始"),
        ]});
        assert_eq!(
            ids(&rows(&plan, &seats(), 0)),
            vec!["U13:node-t1", "U13:node-t2", "U13:node-t3"]
        );
    }

    #[test]
    fn containers_and_non_work_kinds_are_not_nominated() {
        let mut idea = node("I1", "A", Value::Null, "未開始");
        idea["kind"] = json!("suggestion");
        let plan = json!({"nodes": [node("A", "", Value::Null, "未開始"),
            node("T1", "A", json!("a9136"), "未開始"), idea]});
        assert!(rows(&plan, &seats(), 0).is_empty());
    }

    #[test]
    fn the_coordinator_and_the_lost_seat_are_never_picked_and_siblings_count() {
        let plan = json!({"nodes": [
            node("T1", "A", json!("2240e"), "開發中"),
            node("T2", "A", json!("a9136"), "實查通過"),
            node("T3", "A", json!("a9136"), "實查通過"),
        ]});
        let r = rows(&plan, &seats(), 0);
        assert_eq!(r[0]["candidates"], json!(["a9136", "b433b"]));
        assert!(r[0]["nominee_why"][0]
            .as_str()
            .unwrap()
            .starts_with("a9136:同一大項 2 件"));
        assert_eq!(r[0]["fingerprint"], "n-a9136");
    }

    #[test]
    fn with_no_siblings_the_lighter_load_goes_first() {
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let r = rows(&plan, &seats(), 0);
        assert_eq!(r[0]["candidates"], json!(["b433b", "a9136"]));
    }

    #[test]
    fn at_most_five_and_the_one_that_unlocks_most_first() {
        let mut nodes: Vec<Value> = (1..=7)
            .map(|i| node(&format!("T{i}"), "A", Value::Null, "未開始"))
            .collect();
        let mut w = node("W1", "B", json!("a9136"), "未開始");
        w["after"] = json!(["T7"]);
        let mut w2 = node("W2", "B", json!("a9136"), "未開始");
        w2["after"] = json!(["T7", "T6"]);
        nodes.push(w);
        nodes.push(w2);
        let r = rows(&json!({ "nodes": nodes }), &seats(), 0);
        assert_eq!(r.len(), MAX_ROWS);
        assert_eq!(ids(&r)[..2], ["U13:node-t7", "U13:node-t6"]);
        assert!(r[0]["what"].as_str().unwrap().ends_with("解開 2 件"));
    }

    #[test]
    fn owner_seat_reads_only_a_leading_seat_code() {
        assert_eq!(owner_seat("a9136;審 1ced7"), Some("a9136"));
        assert_eq!(owner_seat("a9136(提名)"), Some("a9136"));
        assert_eq!(owner_seat("deadbeef"), None);
        assert_eq!(owner_seat("Andy"), None);
    }

    #[test]
    fn a_seat_written_off_as_lost_is_not_nominated_back_even_if_it_answers() {
        let plan = json!({"nodes": [node("T1", "A", json!("b433b(失聯,待重派)"), "開發中")]});
        let r = rows(&plan, &seats(), 0);
        assert_eq!(r[0]["candidates"], json!(["a9136", "ebb36"]));
    }
}
