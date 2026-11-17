//! U13 沒人負責 (DESIGN-S79-unblock-519d0-1115 §2b), nominate only: a plan-tree task that is
//! not finished and whose owner is empty, says 待定 / 待派 / 失聯, or names a seat that is
//! not alive. Each gets a first and a second choice with a one-line reason; nothing is
//! written to the plan tree and no one is sent anything (519d0: nominate for a day, compare
//! with the coordinator's own picks, ≥80% before dispatching directly).
//!
//! Candidates are ranked by domain, not by being free (SPEC-s79-nominate-519d0-1116, Andy
//! 11-16「集中完成 自動找事做」): the same parent's other children, the parent / grandparent
//! owner, a split written in the parent's owner (`7b81e(a/b)`), `with`, a seat code in the title
//! or evidence, the same piece of work as another node (`併 S77`), the lost owner's co-owners;
//! −2 for 10 or more unfinished nodes. Ties go to the one with fewer unfinished nodes. When the
//! first choice scores 0 the row says `weak` (not for direct dispatch). The coordinator, system
//! seats, seats with ≥ 10 unread and, unless the title says 驗收, the review seats are never
//! nominated.

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
pub(crate) fn owner_seat(owner: &str) -> Option<&str> {
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
    prs: u64,
    planned: u64,
    reviewer: bool,
    /// act-seats.json says how many unread (act does not send when it cannot tell).
    unread_known: bool,
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
        .filter(|x| {
            alive.contains(s(x, "seat")) && !matches!(s(x, "role"), "coordinator" | "system")
        })
        // 10 unread or more: never (Andy §8; the same constant act sends by, a9136 L1)
        .filter(|x| num(x, "/capacity/unread") < crate::act::UNREAD_MAX)
        .map(|x| Seat {
            code: s(x, "seat"),
            prs: num(x, "/capacity/open_prs"),
            planned: num(x, "/capacity/planned"),
            reviewer: s(x, "role") == "reviewer",
            unread_known: x.pointer("/capacity/unread").is_some_and(Value::is_u64),
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
    let by_id: BTreeMap<&str, &Value> = nodes.iter().map(|n| (s(n, "id"), *n)).collect();
    found
        .into_iter()
        .take(MAX_ROWS)
        .map(|(unl, n, why)| {
            let lost = owner_seat(s(n, "owner"));
            let review = s(n, "title").contains(REVIEW_WORD);
            let mut ranked: Vec<(Score, &Seat)> = pool
                .iter()
                .filter(|c| Some(c.code) != lost)
                .filter(|c| c.reviewer == review)
                .map(|c| (score(n, c, &nodes, &by_id, lost, review), c))
                .collect();
            ranked.sort_by(|a, b| {
                b.0.points
                    .cmp(&a.0.points)
                    .then(a.1.planned.cmp(&b.1.planned))
                    .then(a.1.code.cmp(b.1.code))
            });
            let top = &ranked[..ranked.len().min(2)];
            // weak (not for direct dispatch): no domain signal, a lead under LEAD_MIN points
            // (a9136 L3), or unread not known for the first choice (a9136 L2)
            let weak = match top {
                [] => true,
                [(a, c), rest @ ..] => {
                    a.points <= 0
                        || !c.unread_known
                        || rest.first().is_some_and(|(b, _)| a.points - b.points < LEAD_MIN)
                }
            };
            let pick: Vec<String> = top
                .iter()
                .map(|(sc, c)| {
                    let hits = if sc.hits.is_empty() {
                        "沒有領域訊號".to_owned()
                    } else {
                        sc.hits.join("、")
                    };
                    format!("{}:{} 分({hits});開著 {} 張、手上 {} 件", c.code, sc.points, c.prs, c.planned)
                })
                .collect();
            let cands: Vec<&str> = top.iter().map(|(_, c)| c.code).collect();
            let scores: Vec<Value> = top
                .iter()
                .map(|(sc, c)| json!({"seat": c.code, "points": sc.points, "hits": sc.hits}))
                .collect();
            let id = s(n, "id");
            json!({
                "id": format!("U13:node-{}", slug(id)),
                "code": "U13",
                "node": id,
                "subject": format!("node-{}", slug(id)),
                "what": format!("{id} {} 沒人負責({why});解開 {unl} 件", s(n, "title")),
                "fingerprint": format!("n-{}", cands.first().copied().unwrap_or("none")),
                "evidence": format!("cabin-plan data.json {id} owner={:?} status={}", s(n, "owner"), s(n, "status")),
                "owner": "",
                "candidates": cands,
                "nominee_why": pick,
                "scores": scores,
                "weak": weak,
                "since_ms": now_ms,
                "as_of_ms": now_ms,
            })
        })
        .collect()
}

/// A node whose title says this is an acceptance node: only review seats (role `reviewer`,
/// from u13-reviewers.txt) take it, and they take nothing else (SPEC-s79-nominate; T06 / T07
/// / T10 went to them, S74g「順便排審查」did not).
pub const REVIEW_WORD: &str = "驗收";
/// The first choice must lead the second by this many points, or the row is weak.
pub const LEAD_MIN: i64 = 2;
/// A seat with this many unfinished nodes or more loses [`HEAVY_PENALTY`] points.
pub const HEAVY: u64 = 10;
pub const HEAVY_PENALTY: i64 = 2;
/// Owning another node on the same `line` (crate / stream) is the strongest domain clue.
pub const LINE_POINTS: i64 = 8;

struct Score {
    points: i64,
    hits: Vec<String>,
}

/// Every five-hex seat code in `text` that stands alone (not part of a longer word or hash).
fn seats_in(text: &str) -> BTreeSet<&str> {
    let b = text.as_bytes();
    let alnum = |i: usize| b.get(i).is_some_and(|c| c.is_ascii_alphanumeric());
    (0..b.len().saturating_sub(4))
        .filter(|&i| text.is_char_boundary(i) && text.is_char_boundary(i + 5))
        .filter(|&i| {
            b[i..i + 5]
                .iter()
                .all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
        })
        .filter(|&i| (i == 0 || !alnum(i - 1)) && !alnum(i + 5))
        .map(|i| &text[i..i + 5])
        .collect()
}

/// Node ids written in `text` (`S74`, `T110`, `S66-5`, `A13`): a capital S / T / A, digits,
/// maybe one lower-case letter; with whether `併` / `=` / `同` stands right before it (the
/// same piece of work, 519d0 rule 4).
fn refs_in(text: &str) -> Vec<(String, bool)> {
    let cs: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < cs.len() {
        let starts = matches!(cs[i], 'S' | 'T' | 'A')
            && (i == 0 || !cs[i - 1].is_ascii_alphanumeric())
            && cs.get(i + 1).is_some_and(char::is_ascii_digit);
        if !starts {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while cs.get(j).is_some_and(char::is_ascii_digit) {
            j += 1;
        }
        if cs.get(j).is_some_and(char::is_ascii_lowercase)
            && !cs.get(j + 1).is_some_and(char::is_ascii_alphanumeric)
        {
            j += 1;
        }
        if cs.get(j).is_some_and(char::is_ascii_alphanumeric) {
            i = j;
            continue;
        }
        let before: String = cs[..i]
            .iter()
            .rev()
            .skip_while(|c| **c == ' ')
            .take(1)
            .collect();
        out.push((
            cs[i..j].iter().collect(),
            matches!(before.as_str(), "併" | "=" | "同"),
        ));
        i = j;
    }
    out
}

/// The evidence as it read before an owner was set: parts (split at ` | `) that record a
/// decision on the owner are the answer, not a clue.
fn clues(n: &Value) -> String {
    let ev: String = s(n, "evidence")
        .split(" | ")
        .filter(|p| !["定負責席", "重派", "負責席"].iter().any(|w| p.contains(w)))
        .collect::<Vec<_>>()
        .join(" | ");
    format!("{} {ev}", s(n, "title"))
}

/// How much `c` fits node `n` by domain (SPEC-s79-nominate-519d0-1116 + BACKTEST r0);
/// being free scores nothing.
fn score(
    n: &Value,
    c: &Seat,
    nodes: &[&Value],
    by_id: &BTreeMap<&str, &Value>,
    lost: Option<&str>,
    review: bool,
) -> Score {
    let id = s(n, "id");
    let owns = |m: &Value| s(m, "id") != id && seats_in(s(m, "owner")).contains(c.code);
    let mut sc = Score {
        points: 0,
        hits: Vec::new(),
    };
    let mut add = |p: i64, why: String| {
        sc.points += p;
        sc.hits.push(why);
    };
    let parent = s(n, "parent");
    // 1. who works on the same parent's other children (done ones too: who knows this area)
    let sib = nodes
        .iter()
        .filter(|m| !parent.is_empty() && s(m, "parent") == parent && owns(m))
        .count() as i64;
    if sib > 0 {
        let p = (4 + sib).min(8);
        add(p, format!("同父 {sib} 件 +{p}"));
    }
    // 2. owner of the parent or the grandparent
    let mut up = by_id.get(parent).copied();
    for _ in 0..2 {
        let Some(m) = up else { break };
        if owns(m) {
            add(4, format!("{} owner +4", s(m, "id")));
            break;
        }
        up = by_id.get(s(m, "parent")).copied();
    }
    // 3. the parent's owner text splits the work: `7b81e(a/b/f)` names this node's letter
    if let (Some(p), Some(suffix)) = (by_id.get(parent), id.strip_prefix(parent)) {
        let suffix = suffix.trim_start_matches('-');
        let own = s(p, "owner");
        let split = own.match_indices(c.code).any(|(at, _)| {
            own[at + 5..]
                .strip_prefix('(')
                .and_then(|r| r.split(')').next())
                .is_some_and(|inside| {
                    inside
                        .split(|ch: char| !ch.is_ascii_alphanumeric())
                        .any(|w| !suffix.is_empty() && w == suffix)
                })
        });
        if split {
            add(6, format!("{parent} 分工寫了 {suffix} +6"));
        }
    }
    // 3b. the same line (`line`: a crate or a stream of work, which the coordinator writes on
    // nodes whose owner nobody can read from the tree; 519d0 11-16 門檻選 1 §3)
    let line = s(n, "line");
    if !line.is_empty() {
        let same = nodes
            .iter()
            .filter(|m| s(m, "line") == line && owns(m))
            .count();
        if same > 0 {
            add(LINE_POINTS, format!("同線 {line} {same} 件 +{LINE_POINTS}"));
        }
    }
    // 4. the node's own with
    if n.get("with")
        .and_then(Value::as_array)
        .is_some_and(|w| w.iter().any(|x| x.as_str() == Some(c.code)))
    {
        add(3, "with +3".into());
    }
    let text = clues(n);
    // 5. the title / evidence names the seat (a branch, a PR head, a file); a code of digits
    // only (87167) counts only as @87167, a bare 5-digit number is a count (5d33a N1)
    let digits = c.code.bytes().all(|b| b.is_ascii_digit());
    if seats_in(&text).contains(c.code) && (!digits || text.contains(&format!("@{}", c.code))) {
        add(3, "標題 / 證據寫了席碼 +3".into());
    }
    // 6. the same piece of work as another node (併 / = / 同 Sxx) or one it points at
    let refs = refs_in(&text);
    let owner_of = |r: &str| r != id && r != parent && by_id.get(r).is_some_and(|m| owns(m));
    if let Some((r, _)) = refs.iter().find(|(r, same)| *same && owner_of(r)) {
        add(6, format!("同一件 {r} +6"));
    } else if let Some((r, _)) = refs.iter().find(|(r, _)| owner_of(r)) {
        add(4, format!("提到 {r} +4"));
    }
    // 7. the lost owner shared a node with this seat
    if let Some(gone) = lost {
        let shared = nodes.iter().any(|m| {
            let o = seats_in(s(m, "owner"));
            o.contains(gone) && o.contains(c.code)
        });
        if shared {
            add(2, format!("跟 {gone} 同節點 +2"));
        }
    }
    if review {
        add(6, "驗收節點、審查席 +6".into());
    }
    if c.planned >= HEAVY {
        add(
            -HEAVY_PENALTY,
            format!("手上 {} 件 −{HEAVY_PENALTY}", c.planned),
        );
    }
    sc
}

/// The nominations log beside unblock.json (one line per node each time its first choice
/// changes), read back by [`report`].
pub const LOG: &str = "u13-nominations.jsonl";
/// [`report`] says `ready` only after this long and this many decided nodes (519d0 11-15:
/// nominate for a day, ≥80% before dispatching directly).
pub const READY_SPAN_MS: i64 = 24 * 60 * 60 * 1000;
pub const READY_MIN: usize = 5;
pub const READY_RATE: f64 = 0.8;

/// The log lines this round adds: U13 rows (from the merged document) whose node has no line
/// yet or whose first choice differs from its last line.
pub fn log_lines(merged: &Value, log: &[Value], now_ms: i64) -> Vec<Value> {
    let mut last: BTreeMap<&str, &str> = BTreeMap::new();
    for l in log {
        last.insert(s(l, "node"), s(l, "first"));
    }
    merged
        .get("rows")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|r| s(r, "code") == "U13" && !s(r, "node").is_empty())
        .filter_map(|r| {
            let c: Vec<&str> = r
                .get("candidates")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            let first = c.first().copied().unwrap_or("");
            (last.get(s(r, "node")) != Some(&first)).then(|| {
                json!({"at_ms": now_ms, "node": s(r, "node"), "first": first,
                    "candidates": c})
            })
        })
        .collect()
}

/// How the nominations in `log` compare with who the plan tree names now: a node counts once
/// its owner is a seat (not 待定 / 待派 / 失聯); a hit when that seat was ever nominated for it.
/// An owner the nominator may never pick (the coordinator, a system seat, a seat not alive or
/// not in `seats`) is out of reach: listed, not in the rates (519d0 11-16 門檻選 1: 80% of what
/// can be hit).
pub fn report(log: &[Value], plan: &Value, seats: &Value, now_ms: i64) -> Value {
    let reachable: BTreeSet<&str> = seats
        .get("seats")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|x| x.pointer("/alive/v").and_then(Value::as_str) == Some("yes"))
        .filter(|x| !matches!(s(x, "role"), "coordinator" | "system"))
        .map(|x| s(x, "seat"))
        .collect();
    let mut out_of_reach = Vec::new();
    let mut firsts: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    let mut any: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for l in log {
        let node = s(l, "node");
        firsts.entry(node).or_default().insert(s(l, "first"));
        any.entry(node).or_default().extend(
            l.get("candidates")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str),
        );
    }
    let owners: BTreeMap<&str, &str> = plan
        .get("nodes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|n| (s(n, "id"), s(n, "owner")))
        .collect();
    let (mut hit_first, mut hit_any, mut misses, mut pending) =
        (0usize, 0usize, Vec::new(), 0usize);
    for (node, cands) in &any {
        let owner = owners.get(node).copied().unwrap_or("");
        let placeholder = ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w));
        let Some(seat) = owner_seat(owner).filter(|_| !placeholder) else {
            pending += 1;
            continue;
        };
        if !reachable.contains(seat) {
            out_of_reach.push(json!({"node": node, "owner": seat}));
            continue;
        }
        if firsts.get(node).is_some_and(|f| f.contains(seat)) {
            hit_first += 1;
        }
        if cands.contains(seat) {
            hit_any += 1;
        } else {
            misses.push(json!({"node": node, "owner": seat, "nominated": cands}));
        }
    }
    let decided = hit_any + misses.len();
    let rate = |n: usize| {
        if decided == 0 {
            0.0
        } else {
            n as f64 / decided as f64
        }
    };
    let since = log
        .iter()
        .filter_map(|l| l.get("at_ms").and_then(Value::as_i64))
        .min();
    let ready = decided >= READY_MIN
        && rate(hit_first) >= READY_RATE
        && since.is_some_and(|t| now_ms - t >= READY_SPAN_MS);
    json!({"nominated": any.len(), "decided": decided, "pending": pending,
        "out_of_reach": out_of_reach,
        "hit_first": hit_first, "hit_any": hit_any, "rate_first": rate(hit_first),
        "rate_any": rate(hit_any), "since_ms": since, "ready": ready, "misses": misses})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seat(code: &str, alive: &str, role: &str, prs: u64, planned: u64) -> Value {
        json!({"seat": code, "alive": {"v": alive}, "role": role,
            "capacity": {"open_prs": prs, "planned": planned, "unread": 0, "wip_nodes": []}})
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

    /// Seats for the report: the usual ones plus 043dd, all reachable but the coordinator
    /// and 2240e (not alive).
    fn rseats() -> Value {
        let mut s = seats();
        s["seats"]
            .as_array_mut()
            .unwrap()
            .push(seat("043dd", "yes", "dev", 0, 0));
        s
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

    /// 519d0 11-16: a seat marked role `system` (63695) takes no work and is never a candidate.
    #[test]
    fn a_system_seat_is_never_a_candidate() {
        let mut s = seats();
        s["seats"]
            .as_array_mut()
            .unwrap()
            .push(seat("63695", "yes", "system", 0, 0));
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let r = rows(&plan, &s, 0);
        let c: Vec<&str> = r[0]["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert!(!c.contains(&"63695"), "{c:?}");
        assert_eq!(c[0], "b433b");
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
            .starts_with("a9136:6 分(同父 2 件 +6)"));
        assert_eq!(r[0]["weak"], false);
        assert_eq!(r[0]["scores"][0]["points"], 6);
        assert_eq!(r[0]["fingerprint"], "n-a9136");
    }

    #[test]
    fn with_no_siblings_the_lighter_load_goes_first() {
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let r = rows(&plan, &seats(), 0);
        assert_eq!(r[0]["candidates"], json!(["b433b", "a9136"]));
        assert_eq!(
            r[0]["weak"], true,
            "no domain signal: weak, not for direct dispatch"
        );
    }

    fn first(plan: &Value, seats: &Value) -> (String, Value) {
        let r = rows(plan, seats, 0);
        (
            r[0]["candidates"][0].as_str().unwrap_or("").to_owned(),
            r[0].clone(),
        )
    }

    /// SPEC-s79-nominate: being free scores nothing; the seat that owns the area wins even
    /// with more on its hands.
    #[test]
    fn domain_beats_a_free_seat_and_each_signal_counts() {
        // the parent's owner (b433b is free, a9136 is not)
        let plan = json!({"nodes": [node("A", "G", json!("a9136"), "未開始"),
            node("T1", "A", Value::Null, "未開始")]});
        assert_eq!(first(&plan, &seats()).0, "a9136");
        // with
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["with"] = json!(["ebb36"]);
        assert_eq!(first(&json!({"nodes": [t]}), &seats()).0, "ebb36");
        // a seat code in the title or evidence (a branch name)
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["evidence"] = json!("feat/x-ebb36-1116 審過");
        assert_eq!(first(&json!({"nodes": [t]}), &seats()).0, "ebb36");
        // the same piece of work as another node beats merely pointing at one
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["title"] = json!("見 S5;併 S7");
        let plan = json!({"nodes": [t, node("S5", "B", json!("a9136"), "未開始"),
            node("S7", "B", json!("ebb36"), "未開始")]});
        let (who, row) = first(&plan, &seats());
        assert_eq!(who, "ebb36");
        assert_eq!(row["scores"][0]["points"], 6);
    }

    #[test]
    fn a_split_in_the_parents_owner_names_the_seat_for_its_letter() {
        let plan = json!({"nodes": [
            node("S74", "A", json!("a9136(a/b)/ ebb36(d)"), "開發中"),
            node("S74d", "S74", Value::Null, "未開始"),
            node("S74x", "S74", json!("a9136"), "未開始")]});
        let (who, row) = first(&plan, &seats());
        assert_eq!(who, "ebb36", "{row}");
        assert!(row["nominee_why"][0]
            .as_str()
            .unwrap()
            .contains("S74 分工寫了 d +6"));
    }

    /// The evidence part that records the owner decision is the answer, not a clue.
    #[test]
    fn the_part_of_the_evidence_that_records_the_decision_is_not_read() {
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["evidence"] = json!("規則有 | 11-16 519d0 定負責席(原 待定):ebb36");
        assert_eq!(first(&json!({"nodes": [t]}), &seats()).1["weak"], true);
    }

    #[test]
    fn review_seats_take_only_acceptance_nodes_and_then_only_they_do() {
        let mut s = seats();
        let a = s["seats"].as_array_mut().unwrap();
        a.push(seat("5d33a", "yes", "reviewer", 0, 0));
        a.push(seat("1ced7", "yes", "reviewer", 0, 3));
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let r = rows(&plan, &s, 0);
        assert!(!r[0]["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .any(|c| c == "5d33a" || c == "1ced7"));
        let mut t = node("T1", "A", json!("a9136"), "未開始");
        t["owner"] = Value::Null;
        t["title"] = json!("mac 實機整套驗收");
        let r = rows(
            &json!({"nodes": [t, node("T2", "A", json!("a9136"), "開發中")]}),
            &s,
            0,
        );
        assert_eq!(
            r[0]["candidates"],
            json!(["5d33a", "1ced7"]),
            "fewer on hand first"
        );
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["title"] = json!("順便排審查");
        let r = rows(&json!({"nodes": [t]}), &s, 0);
        assert_ne!(r[0]["candidates"][0], "5d33a", "審 alone is development");
    }

    /// a9136 M1: with no review seat listed, an acceptance node gets no development seat.
    #[test]
    fn with_no_review_seat_an_acceptance_node_gets_no_one() {
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["title"] = json!("Windows 實機整套驗收");
        let r = rows(&json!({ "nodes": [t] }), &seats(), 0);
        assert_eq!(r[0]["candidates"], json!([]));
        assert_eq!(r[0]["weak"], true);
    }

    /// No domain signal is weak even when the next one trails by 2 (a heavy hand, −2).
    #[test]
    fn zero_points_is_weak_even_with_a_clear_lead() {
        let mut s = seats();
        s["seats"][0]["capacity"]["planned"] = json!(10);
        s["seats"][4]["capacity"]["planned"] = json!(10);
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let (who, row) = first(&plan, &s);
        assert_eq!(who, "b433b");
        assert_eq!(row["scores"][1]["points"], -2);
        assert_eq!(row["weak"], true);
    }

    /// a9136 L2 / L3: a first choice whose unread is not known, or that leads by under 2, is weak.
    #[test]
    fn a_narrow_lead_or_unknown_unread_is_weak() {
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始"),
            node("T2", "A", json!("a9136"), "開發中"), node("T3", "A", json!("a9136"), "開發中")]});
        assert_eq!(first(&plan, &seats()).1["weak"], false, "6 against 0");
        let mut s = seats();
        s["seats"][0]["capacity"]
            .as_object_mut()
            .unwrap()
            .remove("unread");
        assert_eq!(
            first(&plan, &s).1["weak"],
            true,
            "a9136's unread is not known"
        );
        let close = json!({"nodes": [node("T1", "A", Value::Null, "未開始"),
            node("T2", "A", json!("a9136"), "開發中"), node("T3", "A", json!("b433b"), "開發中")]});
        assert_eq!(first(&close, &seats()).1["weak"], true, "5 against 5");
    }

    #[test]
    fn many_unread_is_never_nominated_and_a_heavy_hand_loses_two() {
        let mut s = seats();
        s["seats"][1]["capacity"]["unread"] = json!(10);
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始")]});
        let c = rows(&plan, &s, 0)[0]["candidates"].clone();
        assert!(!c.as_array().unwrap().iter().any(|x| x == "b433b"), "{c}");
        // ebb36 owns a sibling (+5) but has 10 on hand (−2): 3 points
        let mut s = seats();
        s["seats"][4]["capacity"]["planned"] = json!(10);
        let plan = json!({"nodes": [node("T1", "A", Value::Null, "未開始"),
            node("T2", "A", json!("ebb36"), "開發中")]});
        let r = rows(&plan, &s, 0);
        assert_eq!(r[0]["scores"][0]["seat"], "ebb36");
        assert_eq!(r[0]["scores"][0]["points"], 3);
    }

    /// A node whose owner nobody can read from the tree gets a `line` from the coordinator;
    /// another node on that line names the seat (S82-g dm 佇列 → 7b81e, T112 合併隊 → f1fae).
    #[test]
    fn the_same_line_beats_the_siblings() {
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["line"] = json!("dm-queue");
        let mut far = node("S9", "B", json!("ebb36"), "開發中");
        far["line"] = json!("dm-queue");
        let plan = json!({"nodes": [t, far, node("T2", "A", json!("a9136"), "開發中"),
            node("T3", "A", json!("a9136"), "開發中")]});
        let (who, row) = first(&plan, &seats());
        assert_eq!(who, "ebb36", "{row}");
        assert!(row["nominee_why"][0]
            .as_str()
            .unwrap()
            .contains("同線 dm-queue 1 件 +8"));
    }

    #[test]
    fn the_lost_owners_co_owner_scores() {
        let plan = json!({"nodes": [node("T1", "A", json!("2240e"), "開發中"),
            node("X", "B", json!("2240e / ebb36"), "開發中")]});
        let (who, row) = first(&plan, &seats());
        assert_eq!(who, "ebb36");
        assert!(row["nominee_why"][0]
            .as_str()
            .unwrap()
            .contains("跟 2240e 同節點 +2"));
    }

    /// SPEC-s79-nominate 驗證: the 17 owners 519d0 set on 11-16 (tests/fixtures/u13-backtest.json,
    /// cut from cabin-plan data.json and act-seats.json). Each node is nominated with only its own
    /// owner hidden. 3 cannot be hit by rule (S79-own / T117 → the coordinator, T129 → a seat
    /// not alive); BACKTEST-s79-nominate-043dd-1116 r0 hits 11 of the other 14 and this test
    /// kept that floor. The bar is 12 of 14 (519d0 門檻選 1). r1: 519d0 wrote `line` on the
    /// work lines (S82-g / S99 dm-queue, T112 / T106 merge-queue, T07 / S79-p07 accept) and all
    /// 14 hit; without those 3 the rules alone hit 11 of 11. The list is pinned
    /// on purpose (a9136 N1): when more hit, update it and BACKTEST. These 17 also shaped the
    /// rules, so this is a training score; the real gate is `unblock --match` on new nodes.
    #[test]
    fn the_seventeen_owners_set_on_11_16_backtest() {
        let fx: Value =
            serde_json::from_str(include_str!("../tests/fixtures/u13-backtest.json")).unwrap();
        let mut hits = Vec::new();
        let mut misses = Vec::new();
        for (node, want) in fx["decided"].as_object().unwrap() {
            let mut plan = fx["plan"].clone();
            for n in plan["nodes"].as_array_mut().unwrap() {
                if n["id"] == node.as_str() {
                    n["owner"] = Value::Null;
                }
            }
            let only: Vec<Value> = plan["nodes"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|n| n["id"] == node.as_str() || n["owner"].is_string())
                .cloned()
                .chain(
                    plan["nodes"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter(|n| n["owner"].is_null() && n["id"] != node.as_str())
                        .map(|n| {
                            let mut m = n.clone();
                            m["status"] = json!("實查通過");
                            m
                        }),
                )
                .collect();
            let r = rows(&json!({ "nodes": only }), &fx["seats"], 0);
            let got = r
                .iter()
                .find(|x| x["node"] == node.as_str())
                .map(|x| x["candidates"][0].as_str().unwrap_or("").to_owned())
                .unwrap_or_default();
            if &got == want {
                hits.push(node.as_str());
            } else {
                misses.push(node.as_str());
            }
        }
        // out of reach by rule: the coordinator (S79-own, T117), a seat not alive (T129)
        assert_eq!(misses, ["S79-own", "T117", "T129"], "hits {hits:?}");
        assert_eq!(hits.len(), 14);
        // 519d0 r1: the 3 whose `line` the coordinator wrote, left out: the rules alone hit 11 of 11
        let by_line = ["S82-g", "T07", "T112"];
        assert_eq!(hits.iter().filter(|h| !by_line.contains(h)).count(), 11);
    }

    /// 5d33a N1: a digits-only seat code in the evidence counts only when written @87167.
    #[test]
    fn a_digits_only_code_counts_only_with_an_at() {
        let mut s = seats();
        s["seats"][0]["seat"] = json!("87167");
        let mut t = node("T1", "A", Value::Null, "未開始");
        t["evidence"] = json!("跑了 87167 筆");
        let plan = json!({ "nodes": [t.clone()] });
        let hit = |p: &Value| {
            rows(p, &s, 0)[0]["scores"]
                .as_array()
                .unwrap()
                .iter()
                .any(|x| x["seat"] == "87167" && x["points"].as_i64() > Some(0))
        };
        assert!(!hit(&plan), "a count is not the seat");
        t["evidence"] = json!("交 @87167 驗");
        assert!(hit(&json!({ "nodes": [t] })), "@87167 is the seat");
    }

    #[test]
    fn seat_codes_and_node_refs_are_read_only_as_whole_words() {
        let got: Vec<&str> = seats_in("a9136;審 1ced7 deadbeef0 x2240e 7b81e(a)")
            .into_iter()
            .collect();
        assert_eq!(got, ["1ced7", "7b81e", "a9136"]);
        let r = refs_in("併 S77、見 T06-a 與 S74d;XS5 S7x8 = A13");
        let ids: Vec<(&str, bool)> = r.iter().map(|(a, b)| (a.as_str(), *b)).collect();
        assert_eq!(
            ids,
            [
                ("S77", true),
                ("T06", false),
                ("S74d", false),
                ("A13", true)
            ]
        );
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

    #[test]
    fn the_log_gets_a_line_only_when_a_nodes_first_choice_changes() {
        let m = json!({"rows": [
            {"code": "U13", "node": "T1", "candidates": ["a9136", "b433b"]},
            {"code": "U13", "node": "T2", "candidates": ["b433b"]},
            {"code": "U14", "node": "T3", "candidates": ["a9136"]}]});
        let log = vec![
            json!({"node": "T1", "first": "a9136"}),
            json!({"node": "T2", "first": "ebb36"}),
        ];
        let add = log_lines(&m, &log, 7);
        assert_eq!(add.len(), 1);
        assert_eq!(add[0]["node"], "T2");
        assert_eq!(add[0]["first"], "b433b");
        assert_eq!(add[0]["at_ms"], 7);
    }

    #[test]
    fn the_report_counts_hits_against_who_the_plan_names_now() {
        let log: Vec<Value> = [
            ("T1", "a9136", "b433b"),
            ("T2", "a9136", "b433b"),
            ("T3", "ebb36", "a9136"),
            ("T4", "a9136", "b433b"),
            ("T5", "a9136", "b433b"),
        ]
        .iter()
        .map(|(n, a, b)| json!({"at_ms": 0, "node": n, "first": a, "candidates": [a, b]}))
        .collect();
        let plan = json!({"nodes": [
            {"id": "T1", "owner": "a9136"}, {"id": "T2", "owner": "b433b;審 1ced7"},
            {"id": "T3", "owner": "043dd"}, {"id": "T4", "owner": "a9136(失聯,待重派)"},
            {"id": "T5", "owner": null}]});
        let r = report(&log, &plan, &rseats(), READY_SPAN_MS);
        assert_eq!(r["decided"], 3);
        assert_eq!(r["pending"], 2);
        assert_eq!(r["hit_first"], 1);
        assert_eq!(r["hit_any"], 2);
        assert_eq!(r["misses"][0]["node"], "T3");
        assert_eq!(r["ready"], false);
    }

    #[test]
    fn ready_needs_a_day_five_decided_and_eighty_percent() {
        let log: Vec<Value> = (1..=5)
            .map(|i| {
                json!({"at_ms": 0, "node": format!("T{i}"), "first": "a9136",
                "candidates": ["a9136"]})
            })
            .collect();
        let nodes: Vec<Value> = (1..=5)
            .map(|i| json!({"id": format!("T{i}"), "owner": "a9136"}))
            .collect();
        let plan = json!({ "nodes": nodes });
        assert_eq!(report(&log, &plan, &rseats(), READY_SPAN_MS)["ready"], true);
        assert_eq!(
            report(&log, &plan, &rseats(), READY_SPAN_MS - 1)["ready"],
            false
        );
        let others: Vec<Value> = (1..=5)
            .map(|i| json!({"id": format!("T{i}"), "owner": "b433b"}))
            .collect();
        let missed = json!({ "nodes": others });
        assert_eq!(
            report(&log, &missed, &rseats(), READY_SPAN_MS)["ready"],
            false
        );
        let short = &log[..4];
        assert_eq!(
            report(short, &plan, &rseats(), READY_SPAN_MS)["ready"],
            false
        );
    }

    /// 519d0 11-16 門檻選 1: an owner the nominator may never pick is out of reach, not a miss.
    #[test]
    fn owners_out_of_reach_are_listed_not_counted() {
        let log: Vec<Value> = ["T1", "T2", "T3"]
            .iter()
            .map(|n| json!({"at_ms": 0, "node": n, "first": "a9136", "candidates": ["a9136"]}))
            .collect();
        let plan = json!({"nodes": [{"id": "T1", "owner": "a9136"},
            {"id": "T2", "owner": "519d0(規格)/ a9136"}, {"id": "T3", "owner": "2240e"}]});
        let r = report(&log, &plan, &rseats(), READY_SPAN_MS);
        assert_eq!(r["decided"], 1);
        assert_eq!(r["rate_first"], 1.0);
        assert_eq!(
            r["out_of_reach"],
            json!([{"node": "T2", "owner": "519d0"},
            {"node": "T3", "owner": "2240e"}])
        );
    }

    /// 519d0 11-16 v-2 N1: direct dispatch sends the first choice, so ready reads rate_first;
    /// hits on the second choice are printed (rate_any) but do not make it ready.
    #[test]
    fn ready_reads_the_first_choice_rate_not_any_nomination() {
        let log: Vec<Value> = (1..=5)
            .map(|i| {
                json!({"at_ms": 0, "node": format!("T{i}"), "first": "b433b",
                "candidates": ["b433b", "a9136"]})
            })
            .collect();
        let nodes: Vec<Value> = (1..=5)
            .map(|i| json!({"id": format!("T{i}"), "owner": "a9136"}))
            .collect();
        let r = report(&log, &json!({ "nodes": nodes }), &rseats(), READY_SPAN_MS);
        assert_eq!(r["rate_any"], 1.0);
        assert_eq!(r["rate_first"], 0.0);
        assert_eq!(r["ready"], false);
    }
}
