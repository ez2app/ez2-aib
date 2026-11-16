//! `aib-drive own` (S79-own o-1, DECISION-council-s79-own-519d0-1116 r1): what this seat
//! can push on by itself right now, by role, top three, each with why it is this seat's,
//! why now and what its evidence looks like. Read only: nothing is claimed or sent.
//!
//! Sources (one read each, the same round):
//! - the plan tree: a node whose `owner` names this seat in a part of its own (a part
//!   starting 審 is a review duty, 裝 an install duty), at a status still to push on
//!   ([`OWN_STATUSES`]), every `after` node done; the coordinator also gets nodes nobody
//!   owns and every 未驗 node;
//! - `plan-<seat>.txt`: lines that start `<seat> 自己`;
//! - the seat's dm queue: a dispatch (first line `派工:`) not done and not marked waiting.
//!
//! Not listed (each counted with its reason in `empty`): waiting on another seat (a `pr:` /
//! `skills-pr:` before it, an `after` node not done, a dm marked waiting), waiting on Andy
//! (`gate` set), a node another seat has claimed. Order: nodes it unblocks, then how long it
//! waited; never smallest first (the council's r1: that starves big work).
use crate::next_local::{Dep, DONE_STATUS};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

/// Statuses a node can still be pushed on from (r1 題 1).
pub const OWN_STATUSES: &[&str] = &[
    "未開始",
    "開發中",
    "已同意未做",
    "已定未做",
    "未驗",
    "審過未推",
];
/// How many items `own` gives.
pub const TOP: usize = 3;
/// Exit codes (DEV-s79-own §2 o-1).
pub const RC_SOME: i32 = 0;
pub const RC_EMPTY: i32 = 4;
pub const RC_UNREADABLE: i32 = 3;

/// The roles the list is built for (r1 題 1).
pub const ROLES: &[&str] = &["dev", "review", "ops", "coordinator"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Item {
    /// `node:<id>` / `plan:<first 8 hex of the line's sha256>` / `dm:<id>` (a plan line's id
    /// stays when lines above it move; a9136 IFACE s79-own-gate r0, 56cda point 2)
    pub id: String,
    pub what: String,
    pub why_you: String,
    pub why_now: String,
    pub evidence: String,
    /// unfinished nodes waiting on it (0 for a plan line or a dm)
    pub unblocks: usize,
    /// the source's own time as written, for the order (older first)
    #[serde(skip)]
    pub since: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Skip {
    pub id: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OwnOut {
    pub schema: &'static str,
    pub seat: String,
    pub role: String,
    pub as_of: String,
    /// the first [`TOP`]
    pub items: Vec<Item>,
    /// how many were listable before the cut
    pub total: usize,
    /// why each source gave nothing it could list, or a candidate was left out
    pub skipped: Vec<Skip>,
    /// `plan` / `plan_txt` / `dms` → ok / missing / failed
    pub sources: BTreeMap<String, String>,
    /// every listable item before the cut (o-2 checks an id against it; not in the JSON)
    #[serde(skip)]
    pub all: Vec<Item>,
}

pub const SCHEMA: &str = "aib-drive-own/v1";

impl OwnOut {
    pub fn rc(&self) -> i32 {
        if self.sources.get("plan").map(String::as_str) != Some("ok") {
            RC_UNREADABLE
        } else if self.items.is_empty() {
            RC_EMPTY
        } else {
            RC_SOME
        }
    }
}

pub struct OwnInput<'a> {
    pub seat: &'a str,
    pub role: &'a str,
    /// the plan tree's data.json, `None` = could not be read
    pub plan: Option<&'a Value>,
    /// `plan-<seat>.txt`, `None` = no file
    pub plan_txt: Option<&'a str>,
    /// the seat's dm rows (waiting and read tabs), `None` = could not be read
    pub dms: Option<&'a [Value]>,
    /// nodes another seat has claimed (act's intents ledger)
    pub claimed: &'a BTreeSet<String>,
    pub as_of: &'a str,
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

/// The role a seat gets when none is given: the coordinator by code, else dev.
pub fn default_role(seat: &str) -> &'static str {
    if seat == crate::seats::COORDINATOR {
        "coordinator"
    } else {
        "dev"
    }
}

/// What `owner` says this seat does on the node: `main`, `review` (a part starting 審),
/// `install` (a part starting 裝), or `None` when the seat is not named.
pub fn duty(owner: &str, seat: &str) -> Option<&'static str> {
    let mut found = None;
    for part in owner.split([';', '；', '/', ',', '，', '、']) {
        let p = part.trim();
        // the seat as a word, not inside a longer hash or word (5d33a 2cc602ad N1)
        let word = p.match_indices(seat).any(|(i, _)| {
            let before = p[..i].chars().next_back();
            let after = p[i + seat.len()..].chars().next();
            !before.is_some_and(|c| c.is_ascii_alphanumeric())
                && !after.is_some_and(|c| c.is_ascii_alphanumeric())
        });
        if !word {
            continue;
        }
        let d = if p.starts_with('審') {
            "review"
        } else if p.starts_with('裝') {
            "install"
        } else {
            "main"
        };
        // a main part wins over a side duty named elsewhere on the same node
        if d == "main" || found.is_none() {
            found = Some(d);
        }
    }
    found
}

/// Whether a duty counts for a role: every role pushes its main parts; a review seat also
/// its review parts; install parts are the integrator's (ops).
fn duty_counts(d: &str, role: &str) -> bool {
    match d {
        "main" => true,
        "review" => role == "review",
        "install" => role == "ops",
        _ => false,
    }
}

pub fn own_list(inp: &OwnInput<'_>) -> OwnOut {
    let mut items: Vec<Item> = Vec::new();
    let mut skipped: Vec<Skip> = Vec::new();
    let mut sources = BTreeMap::new();
    let skip = |id: String, reason: String, skipped: &mut Vec<Skip>| {
        skipped.push(Skip { id, reason });
    };

    match inp.plan {
        None => {
            sources.insert("plan".to_owned(), "failed".to_owned());
            skip("plan".into(), "進度樹讀不到".into(), &mut skipped);
        }
        Some(plan) => {
            sources.insert("plan".to_owned(), "ok".to_owned());
            let ns: Vec<&Value> = plan
                .get("nodes")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter(|n| !s(n, "id").is_empty()).collect())
                .unwrap_or_default();
            let status_of: BTreeMap<&str, &str> =
                ns.iter().map(|n| (s(n, "id"), s(n, "status"))).collect();
            let afters = |n: &Value| -> Vec<String> {
                n.get("after")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default()
            };
            // unfinished nodes that wait on each node, directly
            let mut waiting_on: BTreeMap<String, usize> = BTreeMap::new();
            for n in &ns {
                if s(n, "status") == DONE_STATUS {
                    continue;
                }
                for a in afters(n) {
                    if let Some(Dep::Node(id)) = Dep::parse(&a) {
                        *waiting_on.entry(id).or_default() += 1;
                    }
                }
            }
            for n in &ns {
                let (id, status, owner) = (s(n, "id"), s(n, "status"), s(n, "owner"));
                if !OWN_STATUSES.contains(&status) {
                    continue;
                }
                let d = duty(owner, inp.seat);
                let coord = inp.role == "coordinator";
                let unowned = owner.trim().is_empty();
                let why_you = match d {
                    Some(d) if duty_counts(d, inp.role) => match d {
                        "review" => format!("owner 寫 審 {}", inp.seat),
                        "install" => format!("owner 寫 裝 {}", inp.seat),
                        _ => format!("owner 有 {}", inp.seat),
                    },
                    _ if coord && unowned => "沒人負責的節點,統籌接".to_owned(),
                    _ if coord && status == "未驗" => "未驗節點,統籌可實查".to_owned(),
                    _ => continue,
                };
                let key = format!("node:{id}");
                if inp.claimed.contains(id) {
                    skip(key, format!("節點 {id} 已有人接"), &mut skipped);
                    continue;
                }
                if !n.get("gate").is_none_or(Value::is_null) {
                    skip(key, format!("節點 {id} 有 gate,等 Andy"), &mut skipped);
                    continue;
                }
                let mut wait = None;
                for a in afters(n) {
                    match Dep::parse(&a) {
                        Some(Dep::Node(dep)) => match status_of.get(dep.as_str()) {
                            Some(st) if *st == DONE_STATUS => {}
                            Some(st) => {
                                wait = Some(format!("前置 {dep} 還是 {st}"));
                                break;
                            }
                            None => {
                                wait = Some(format!("前置 {dep} 不在進度樹"));
                                break;
                            }
                        },
                        Some(dep) => {
                            wait = Some(format!("前置 {} 等合併", dep.text()));
                            break;
                        }
                        None => {
                            wait = Some(format!("前置 {a} 讀不懂"));
                            break;
                        }
                    }
                }
                if let Some(w) = wait {
                    skip(key, w, &mut skipped);
                    continue;
                }
                let evidence = if status == "未驗" {
                    "實查後節點升狀態(只能當 progress)+ 實查檔 / 收據".to_owned()
                } else if status == "審過未推" {
                    "PR 網址或合併頭碼".to_owned()
                } else if d == Some("review") {
                    "審查稿,第一行綁頭".to_owned()
                } else {
                    "這件的 commit / 收據 RC 全 0 綁頭 / PR 網址".to_owned()
                };
                items.push(Item {
                    id: key,
                    what: format!("{id} {}", s(n, "title")),
                    why_you,
                    why_now: format!("狀態 {status},前置都好"),
                    evidence,
                    unblocks: waiting_on.get(id).copied().unwrap_or(0),
                    since: s(n, "updated").to_owned(),
                });
            }
        }
    }

    match inp.plan_txt {
        None => {
            sources.insert("plan_txt".to_owned(), "missing".to_owned());
        }
        Some(txt) => {
            sources.insert("plan_txt".to_owned(), "ok".to_owned());
            let own = format!("{} 自己", inp.seat);
            for line in txt.lines() {
                let l = line.trim();
                if !l.starts_with(&own) {
                    continue;
                }
                let body = l.split_once(['→', ':', '：']).map_or(l, |(_, b)| b).trim();
                items.push(Item {
                    id: format!("plan:{}", &crate::next::sha256_hex(l)[..8]),
                    what: body.to_owned(),
                    why_you: format!("plan-{}.txt 寫「{own}」", inp.seat),
                    why_now: "不等別人".to_owned(),
                    evidence: "這件的檔 / commit / 收據,內容提到這件".to_owned(),
                    unblocks: 0,
                    since: String::new(),
                });
            }
        }
    }

    match inp.dms {
        None => {
            sources.insert("dms".to_owned(), "failed".to_owned());
            skip("dms".into(), "待審框讀不到".into(), &mut skipped);
        }
        Some(rows) => {
            sources.insert("dms".to_owned(), "ok".to_owned());
            for r in rows {
                let msg = s(r, "msg");
                let first = msg.lines().next().unwrap_or("").trim();
                if !first.starts_with("派工:") && !first.starts_with("派工：") {
                    continue;
                }
                let id = format!("dm:{}", s(r, "id"));
                // dm_queue's wait is {"text", "at"} (5d33a 2cc602ad M2); a plain string still reads
                let wait = r
                    .get("wait")
                    .and_then(|w| w.as_str().or_else(|| w.get("text").and_then(Value::as_str)))
                    .unwrap_or_default();
                if !wait.is_empty() {
                    skip(id, format!("在等:{wait}"), &mut skipped);
                    continue;
                }
                items.push(Item {
                    id,
                    what: first.to_owned(),
                    why_you: format!("派給 {} 的信", inp.seat),
                    why_now: "派工還沒結".to_owned(),
                    evidence: "照派工交件(commit / PR / 審查稿),結信帶 --evidence".to_owned(),
                    unblocks: 0,
                    since: s(r, "when").to_owned(),
                });
            }
        }
    }

    // more unblocked first, then waited longer (older time first; none written goes last),
    // then the id
    items.sort_by(|a, b| {
        b.unblocks
            .cmp(&a.unblocks)
            .then_with(|| (a.since.is_empty(), &a.since).cmp(&(b.since.is_empty(), &b.since)))
            .then_with(|| a.id.cmp(&b.id))
    });
    let total = items.len();
    if total == 0 && inp.plan.is_some() {
        skip(
            "empty".into(),
            format!("{} 名下沒有前置都好、不用等人的事", inp.seat),
            &mut skipped,
        );
    }
    let all = items.clone();
    items.truncate(TOP);
    OwnOut {
        schema: SCHEMA,
        seat: inp.seat.to_owned(),
        role: inp.role.to_owned(),
        as_of: inp.as_of.to_owned(),
        items,
        total,
        skipped,
        sources,
        all,
    }
}

/// The text form: one line an item, then why the rest was left out.
pub fn render(o: &OwnOut) -> String {
    let mut out = String::new();
    if o.items.is_empty() {
        out.push_str(&format!("own {} ({}): empty\n", o.seat, o.role));
    } else {
        out.push_str(&format!(
            "own {} ({}): {} of {}\n",
            o.seat,
            o.role,
            o.items.len(),
            o.total
        ));
    }
    for (i, it) in o.items.iter().enumerate() {
        out.push_str(&format!(
            "{}. {} — {}\n   為什麼是你:{};為什麼現在:{};證據:{}\n",
            i + 1,
            it.id,
            it.what,
            it.why_you,
            it.why_now,
            it.evidence
        ));
    }
    for k in &o.skipped {
        out.push_str(&format!("skipped {}: {}\n", k.id, k.reason));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_part_starting_review_or_install_is_a_side_duty() {
        assert_eq!(duty("56cda", "56cda"), Some("main"));
        assert_eq!(duty("a9136;審 56cda", "56cda"), Some("review"));
        assert_eq!(duty("56cda;裝 1ced7", "1ced7"), Some("install"));
        assert_eq!(duty("審 56cda / 56cda u-4", "56cda"), Some("main"));
        assert_eq!(duty("043dd", "56cda"), None);
        // a seat inside a longer hash or word is not the seat (5d33a 2cc602ad N1)
        assert_eq!(duty("a956cda1 合後", "56cda"), None);
        assert_eq!(duty("@56cda(工程師)", "56cda"), Some("main"));
    }

    fn input<'a>(
        plan: &'a Value,
        txt: Option<&'a str>,
        dms: Option<&'a [Value]>,
        claimed: &'a BTreeSet<String>,
        role: &'a str,
    ) -> OwnInput<'a> {
        OwnInput {
            seat: "56cda",
            role,
            plan: Some(plan),
            plan_txt: txt,
            dms,
            claimed,
            as_of: "2026-11-16T00:00:00Z",
        }
    }

    #[test]
    fn a_dev_seat_gets_its_own_nodes_ready_to_push_most_unblocking_first() {
        let plan = json!({"nodes": [
            {"id": "A", "status": "未開始", "owner": "56cda", "title": "a", "updated": "11-15 10:00"},
            {"id": "B", "status": "開發中", "owner": "56cda;審 5d33a", "title": "b", "updated": "11-14 10:00"},
            {"id": "C", "status": "未開始", "owner": "043dd", "title": "c", "after": ["node:A"]},
            {"id": "D", "status": "未開始", "owner": "56cda", "title": "d", "after": ["node:C"]},
            {"id": "E", "status": "未開始", "owner": "56cda", "title": "e", "after": ["pr:9"]},
            {"id": "F", "status": "未開始", "owner": "56cda", "title": "f", "gate": "andy"},
            {"id": "G", "status": "實查通過", "owner": "56cda", "title": "g"},
            {"id": "H", "status": "未開始", "owner": "a9136;審 56cda", "title": "h"},
            {"id": "I", "status": "未開始", "owner": "56cda", "title": "i"},
        ]});
        let mut claimed = BTreeSet::new();
        claimed.insert("I".to_owned());
        let out = own_list(&input(&plan, None, Some(&[]), &claimed, "dev"));
        let ids: Vec<&str> = out.items.iter().map(|i| i.id.as_str()).collect();
        // A unblocks C; B waited longer than nothing else unblocking
        assert_eq!(ids, vec!["node:A", "node:B"], "{out:?}");
        let why = |id: &str| {
            out.skipped
                .iter()
                .find(|k| k.id == id)
                .map(|k| k.reason.clone())
        };
        assert_eq!(why("node:D").as_deref(), Some("前置 C 還是 未開始"));
        assert_eq!(why("node:E").as_deref(), Some("前置 pr:9 等合併"));
        assert_eq!(why("node:F").as_deref(), Some("節點 F 有 gate,等 Andy"));
        assert_eq!(why("node:I").as_deref(), Some("節點 I 已有人接"));
        assert_eq!(why("node:H"), None, "a review duty is not a dev seat's");
        assert_eq!(out.rc(), RC_SOME);
        // the same plan for a review seat: its review part counts
        let out = own_list(&input(&plan, None, Some(&[]), &claimed, "review"));
        assert!(out
            .items
            .iter()
            .any(|i| i.id == "node:H" && i.evidence.contains("綁頭")));
    }

    #[test]
    fn plan_lines_of_its_own_and_open_dispatches_are_listed_waiting_ones_are_not() {
        let plan = json!({"nodes": []});
        let txt = "#755 合後 → 56cda:等別人\n56cda 自己 → 補收據\n519d0 自己 → 不是我的\n";
        let dms = [
            json!({"id": "q_1", "msg": "派工: 修 X → 56cda\n證據", "when": "11-16 07:00"}),
            json!({"id": "q_2", "msg": "派工: 審 Y → 56cda", "wait": {"text": "等收據", "at": 1794788446}}),
            json!({"id": "q_3", "msg": "進度回報"}),
        ];
        let claimed = BTreeSet::new();
        let out = own_list(&input(&plan, Some(txt), Some(&dms), &claimed, "dev"));
        let ids: Vec<&str> = out.items.iter().map(|i| i.id.as_str()).collect();
        let line_id = format!(
            "plan:{}",
            &crate::next::sha256_hex("56cda 自己 → 補收據")[..8]
        );
        assert_eq!(ids, vec!["dm:q_1", line_id.as_str()], "{out:?}");
        // a line added above it does not change its id
        let moved = format!("新的一行\n{txt}");
        let again = own_list(&input(&plan, Some(&moved), Some(&dms), &claimed, "dev"));
        assert!(again.items.iter().any(|i| i.id == line_id), "{again:?}");
        assert_eq!(out.items[1].what, "補收據");
        assert!(out
            .skipped
            .iter()
            .any(|k| k.id == "dm:q_2" && k.reason == "在等:等收據"));
    }

    #[test]
    fn nothing_listable_is_empty_with_a_reason_and_an_unreadable_plan_is_rc_3() {
        let plan = json!({"nodes": [{"id": "A", "status": "實查通過", "owner": "56cda"}]});
        let claimed = BTreeSet::new();
        let out = own_list(&input(&plan, None, Some(&[]), &claimed, "dev"));
        assert_eq!(out.rc(), RC_EMPTY);
        assert!(out.skipped.iter().any(|k| k.id == "empty"), "{out:?}");
        assert!(render(&out).contains("empty"));
        let out = own_list(&OwnInput {
            plan: None,
            ..input(&plan, None, None, &claimed, "dev")
        });
        assert_eq!(out.rc(), RC_UNREADABLE);
        assert_eq!(out.sources.get("dms").map(String::as_str), Some("failed"));
    }

    #[test]
    fn the_coordinator_also_gets_unowned_and_unverified_nodes() {
        let plan = json!({"nodes": [
            {"id": "U", "status": "未開始", "owner": "", "title": "u"},
            {"id": "V", "status": "未驗", "owner": "043dd", "title": "v"},
            {"id": "W", "status": "未開始", "owner": "043dd", "title": "w"},
        ]});
        let claimed = BTreeSet::new();
        let mut inp = input(&plan, None, Some(&[]), &claimed, "coordinator");
        inp.seat = "519d0";
        let out = own_list(&inp);
        let ids: Vec<&str> = out.items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, vec!["node:U", "node:V"], "{out:?}");
        assert!(out.items[1].evidence.contains("progress"));
    }
}
