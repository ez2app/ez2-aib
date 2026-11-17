//! v-2's own source `drive`: blockers aib-drive sees in its own files (DECISION-council-
//! unblock-519d0-1115 u-1). Nothing is fetched; each file that cannot be read becomes an
//! `unreadable` part covering the codes it would have shown.
//! - U21 next is not complete: one row per next source that is not `ok` (act-next.json).
//! - U10 waiting on Andy: one row while next's `for_andy` is not empty.
//! - U1 refresh stuck: refresh.log's last round was `held` and the lock is older than the
//!   takeover age.
//! - U4 a switch is off but act keeps planning that action: would lines in the last hour of
//!   act-dryrun.jsonl for a sendable action its switch holds back.

use crate::act::{Switches, SENDABLE};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub const NAME: &str = "drive";
/// A refresh lock older than this should have been taken over (main.rs AUTO_LOCK, 35 min).
pub const LOCK_STUCK_SECS: i64 = 35 * 60;
/// How far back act-dryrun.jsonl is read for U4.
pub const WOULD_WINDOW_MS: i64 = 60 * 60 * 1000;

/// What the `drive` source reads, already parsed (`Err` = could not be read, one line why).
#[derive(Debug, Clone)]
pub struct Inputs {
    pub next: Result<Value, String>,
    /// refresh.log's last line, `Ok(None)` = no rounds yet.
    pub refresh_last: Result<Option<Value>, String>,
    /// refresh.lock: holder pid and the second it was taken; `None` = not held.
    pub lock: Option<(u32, i64)>,
    pub switches: Result<Switches, String>,
    /// act-dryrun.jsonl lines (`kind` would) as `(action, at_ms)`.
    pub woulds: Result<Vec<(String, i64)>, String>,
    /// The plan tree (cabin-plan data.json) and the seat facts (act-seats.json), for U13.
    pub plan: Result<Value, String>,
    pub seats: Result<Value, String>,
}

fn slug(s: &str) -> String {
    let t: String = s
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

fn row(code: &str, subject: &str, what: String, fp: &str, evidence: &str, now_ms: i64) -> Value {
    json!({"id": format!("{code}:{subject}"), "code": code, "subject": subject, "what": what,
        "fingerprint": fp, "evidence": evidence, "owner": "", "since_ms": now_ms,
        "as_of_ms": now_ms})
}

fn digest(parts: &[&str]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    for p in parts {
        h.update(p.as_bytes());
        h.update([0]);
    }
    h.finalize()[..4]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The seat that does the work in an owner text: the first segment (split at ; / 、 ,) that is
/// not marked 審 / 驗 / 疊 and starts with a seat code (5d33a L1: 「5d33a(驗)/ a9136(修)」 is a9136,
/// the same reading as aib-plan's authors()).
fn doer(owner: &str) -> Option<String> {
    owner
        .split([';', '；', '/', '、', ',', '，'])
        .filter(|seg| !["審", "驗", "疊"].iter().any(|w| seg.contains(w)))
        .find_map(|seg| crate::unblock_u13::owner_seat(seg.trim()).map(str::to_owned))
}

/// Who a row with no owner falls to (DECISION-council-nextstep-519d0-1117 §4-1; 5d33a 11-17: every
/// row had an empty owner, so act skipped them all and no blocker notice went out). A U13 row's card
/// has no live owner by definition, so it goes up the plan tree: the nearest ancestor whose owner names
/// a live seat. Everything else here (U1 / U4 / U10 / U21, and a U13 row with no live ancestor) goes
/// to the live coordinator (DESIGN-S79-unblock-519d0-1115 §2: 統籌). With neither the owner stays
/// empty and `owner_why` says 待接管, so act still skips it and says why.
pub fn assign_owner(row: &mut Value, plan: &Value, seats: &Value) {
    if row
        .get("owner")
        .and_then(Value::as_str)
        .is_some_and(|o| !o.is_empty())
    {
        return;
    }
    let str_of = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
    let all: Vec<&Value> = seats
        .get("seats")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .collect();
    let alive = |code: &str| {
        all.iter().any(|x| {
            str_of(x, "seat") == code
                && x.pointer("/alive/v").and_then(Value::as_str) == Some("yes")
                && !matches!(str_of(x, "role").as_str(), "system")
        })
    };
    let nodes: Vec<&Value> = plan
        .get("nodes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .collect();
    let mut found: Option<(String, String)> = None;
    let mut at = row.get("node").and_then(Value::as_str).map(str::to_owned);
    let mut hops = 0;
    while let Some(id) = at.take() {
        let Some(n) = nodes.iter().find(|n| str_of(n, "id") == id) else {
            break;
        };
        let parent = str_of(n, "parent");
        if hops > 0 {
            let owner = str_of(n, "owner");
            if let Some(seat) = doer(&owner).filter(|s| alive(s)) {
                found = Some((seat.to_owned(), format!("上層節點 {id} 的負責席")));
                break;
            }
        }
        hops += 1;
        if parent.is_empty() || hops > 32 {
            break;
        }
        at = Some(parent);
    }
    if found.is_none() {
        found = all
            .iter()
            .filter(|x| str_of(x, "role") == "coordinator")
            .map(|x| str_of(x, "seat"))
            .find(|c| alive(c))
            .map(|c| (c, "統籌".to_owned()));
    }
    match found {
        Some((seat, why)) => {
            row["owner"] = json!(seat);
            row["owner_why"] = json!(why);
        }
        None => row["owner_why"] = json!("待接管:沒有活著的上層負責席也沒有統籌"),
    }
}

/// The `drive` source document (`schema / as_of_ms / rows / unreadable`).
pub fn doc(inp: &Inputs, now_ms: i64) -> Value {
    let mut rows = Vec::new();
    let mut unreadable = Vec::new();
    match &inp.next {
        Ok(next) => {
            for s in next
                .get("sources")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let name = s.get("name").and_then(Value::as_str).unwrap_or("");
                let state = s.get("state").and_then(Value::as_str).unwrap_or("");
                if name.is_empty() || state == "ok" {
                    continue;
                }
                let why = s.get("why").and_then(Value::as_str).unwrap_or("");
                rows.push(row(
                    "U21",
                    &format!("src-{}", slug(name)),
                    if why.is_empty() {
                        format!("next 的 {name} {state}")
                    } else {
                        format!("next 的 {name} {state}:{why}")
                    },
                    &slug(state),
                    "drive/act-next.json sources",
                    now_ms,
                ));
            }
            let andy: Vec<&Value> = next
                .get("for_andy")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .collect();
            if !andy.is_empty() {
                let ids: Vec<&str> = andy
                    .iter()
                    .filter_map(|a| a.get("id").and_then(Value::as_str))
                    .collect();
                let most = andy
                    .iter()
                    .filter_map(|a| a.get("unblocks").and_then(Value::as_u64))
                    .max()
                    .unwrap_or(0);
                rows.push(row(
                    "U10",
                    "andy",
                    format!("{} 件等 Andy 決定(單件最多解開 {most} 件)", andy.len()),
                    &digest(&ids),
                    "drive/act-next.json for_andy",
                    now_ms,
                ));
            }
        }
        Err(why) => unreadable.push(json!({"name": "next", "why": why, "covers": ["U21", "U10"]})),
    }
    match &inp.refresh_last {
        Ok(last) => {
            let held = last
                .as_ref()
                .and_then(|l| l.get("what"))
                .and_then(Value::as_str)
                == Some("held");
            if let (true, Some((pid, at))) = (held, inp.lock) {
                let age = now_ms / 1000 - at;
                if age > LOCK_STUCK_SECS {
                    rows.push(row(
                        "U1",
                        "refresh-lock",
                        format!("refresh 被 pid {pid} 的鎖擋了 {} 分,超過接管時間", age / 60),
                        &format!("pid{pid}"),
                        "drive/refresh.lock + refresh.log",
                        now_ms,
                    ));
                }
            }
        }
        Err(why) => unreadable.push(json!({"name": "refresh-log", "why": why, "covers": ["U1"]})),
    }
    match (&inp.switches, &inp.woulds) {
        (Ok(sw), Ok(woulds)) => {
            let mut n: BTreeMap<&str, usize> = BTreeMap::new();
            for (a, at) in woulds {
                if now_ms - at <= WOULD_WINDOW_MS
                    && SENDABLE.contains(&a.as_str())
                    && !crate::act::would_send(*sw, a)
                {
                    *n.entry(a.as_str()).or_default() += 1;
                }
            }
            for (a, c) in n {
                rows.push(row(
                    "U4",
                    &format!("switch-{}", slug(a)),
                    format!("{a} 的開關關著,近 60 分 act 排了 {c} 筆"),
                    "off",
                    "drive/act.json + act-dryrun.jsonl",
                    now_ms,
                ));
            }
        }
        (Err(why), _) | (_, Err(why)) => {
            unreadable.push(json!({"name": "switches", "why": why, "covers": ["U4"]}));
        }
    }
    match (&inp.plan, &inp.seats) {
        (Ok(plan), Ok(seats)) => rows.extend(crate::unblock_u13::rows(plan, seats, now_ms)),
        (Err(why), _) | (_, Err(why)) => {
            unreadable.push(json!({"name": "plan", "why": why, "covers": ["U13"]}));
        }
    }
    if let (Ok(plan), Ok(seats)) = (&inp.plan, &inp.seats) {
        for r in &mut rows {
            assign_owner(r, plan, seats);
        }
    }
    let mut d = json!({"schema": crate::unblock::SCHEMA, "as_of_ms": now_ms, "rows": rows});
    if !unreadable.is_empty() {
        d["unreadable"] = json!(unreadable);
    }
    d
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_777_800_000;

    fn base() -> Inputs {
        Inputs {
            next: Ok(json!({"sources": [], "for_andy": []})),
            refresh_last: Ok(None),
            lock: None,
            switches: Ok(Switches::default()),
            woulds: Ok(Vec::new()),
            plan: Ok(json!({"nodes": []})),
            seats: Ok(json!({"seats": []})),
        }
    }

    fn ids(d: &Value) -> Vec<String> {
        d["rows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["id"].as_str().unwrap().to_owned())
            .collect()
    }

    /// §4-1:a U13 row goes to the nearest live ancestor owner, other rows to the live coordinator,
    /// and with neither the owner stays empty and says 待接管
    #[test]
    fn a_row_with_no_owner_falls_to_the_parent_then_the_coordinator() {
        let seats = json!({"seats": [
            {"seat": "f1fae", "role": "dev", "alive": {"v": "yes"}},
            {"seat": "519d0", "role": "coordinator", "alive": {"v": "yes"}},
            {"seat": "c477a", "role": "dev", "alive": {"v": "no"}},
            {"seat": "63695", "role": "system", "alive": {"v": "yes"}}]});
        let plan = json!({"nodes": [
            {"id": "G2", "owner": "519d0"},
            {"id": "A4a", "parent": "G2", "owner": "c477a"},
            {"id": "T136c", "parent": "A4a", "owner": "f1fae(接手 7b81e;原 c477a)"},
            {"id": "T136c-4", "parent": "T136c"},
            {"id": "T20", "parent": "A4a", "owner": "63695"},
            {"id": "T9", "parent": "nowhere"},
            {"id": "T21", "parent": "G2", "owner": "f1fae 失聯"},
            {"id": "S0", "parent": "G2", "owner": "63695"},
            {"id": "T30", "parent": "S0"},
            {"id": "M1", "parent": "G2", "owner": "519d0(驗)/ f1fae(修)"},
            {"id": "T40", "parent": "M1"}]});
        let go = |r: Value, seats: &Value| {
            let mut r = r;
            assign_owner(&mut r, &plan, seats);
            (
                r["owner"].as_str().unwrap_or("").to_owned(),
                r["owner_why"].as_str().unwrap_or("").to_owned(),
            )
        };
        let u13 =
            |n: &str| json!({"id": format!("U13:node-{n}"), "code": "U13", "node": n, "owner": ""});
        assert_eq!(
            go(u13("T136c-4"), &seats),
            ("f1fae".into(), "上層節點 T136c 的負責席".into())
        );
        // the card's own owner (a system seat) is not counted, nor a dead seat above it
        assert_eq!(
            go(u13("T20"), &seats),
            ("519d0".into(), "上層節點 G2 的負責席".into())
        );
        assert_eq!(
            go(u13("T9"), &seats).0,
            "519d0",
            "no ancestor ⇒ coordinator"
        );
        assert_eq!(
            go(u13("T21"), &seats).0,
            "519d0",
            "the card's own owner text is not its fallback"
        );
        assert_eq!(
            go(u13("T30"), &seats),
            ("519d0".into(), "上層節點 G2 的負責席".into()),
            "a system seat above is skipped"
        );
        // 5d33a L1: a mixed owner text above names the doer, not the verifier
        assert_eq!(
            go(u13("T40"), &seats),
            ("f1fae".into(), "上層節點 M1 的負責席".into()),
            "「519d0(驗)/ f1fae(修)」 is f1fae"
        );
        assert_eq!(
            go(json!({"id": "U10:andy", "owner": ""}), &seats),
            ("519d0".into(), "統籌".into())
        );
        assert_eq!(
            go(json!({"id": "U21:x", "owner": "a9136"}), &seats).0,
            "a9136",
            "an owner is kept"
        );
        let gone =
            json!({"seats": [{"seat": "519d0", "role": "coordinator", "alive": {"v": "no"}}]});
        let (o, why) = go(json!({"id": "U10:andy", "owner": ""}), &gone);
        assert!(o.is_empty() && why.starts_with("待接管"), "{o} {why}");
        // doc() fills them in
        let mut i = base();
        i.next = Ok(json!({"for_andy": [{"id": "i_1"}]}));
        i.seats = Ok(seats.clone());
        assert_eq!(doc(&i, NOW)["rows"][0]["owner"], "519d0");
    }

    #[test]
    fn every_next_source_that_is_not_ok_is_a_u21_row() {
        let mut i = base();
        i.next = Ok(json!({"sources": [
            {"name": "slice_due", "state": "stale", "why": "65 分鐘沒更新"},
            {"name": "queue", "state": "ok"},
            {"name": "gh_prs", "state": "failed", "why": "timeout"}]}));
        let d = doc(&i, NOW);
        assert_eq!(ids(&d), vec!["U21:src-slice-due", "U21:src-gh-prs"]);
        assert_eq!(d["rows"][0]["fingerprint"], "stale");
        assert_eq!(
            d["rows"][0]["what"],
            "next 的 slice_due stale:65 分鐘沒更新"
        );
        i.next = Ok(json!({"sources": [{"name": "tidy_scan", "state": "partial"}]}));
        assert_eq!(doc(&i, NOW)["rows"][0]["what"], "next 的 tidy_scan partial");
        assert!(d.get("unreadable").is_none());
    }

    #[test]
    fn waiting_on_andy_is_one_row_whose_fingerprint_follows_the_items() {
        let mut i = base();
        i.next =
            Ok(json!({"for_andy": [{"id": "i_1", "unblocks": 1}, {"id": "i_2", "unblocks": 4}]}));
        let a = doc(&i, NOW);
        assert_eq!(ids(&a), vec!["U10:andy"]);
        assert!(a["rows"][0]["what"]
            .as_str()
            .unwrap()
            .starts_with("2 件等 Andy"));
        assert!(a["rows"][0]["what"]
            .as_str()
            .unwrap()
            .contains("最多解開 4"));
        i.next = Ok(json!({"for_andy": [{"id": "i_1"}, {"id": "i_3"}]}));
        let b = doc(&i, NOW);
        assert_ne!(a["rows"][0]["fingerprint"], b["rows"][0]["fingerprint"]);
    }

    #[test]
    fn a_held_refresh_is_stuck_only_past_the_takeover_age() {
        let mut i = base();
        i.refresh_last = Ok(Some(json!({"what": "held"})));
        i.lock = Some((40555, NOW / 1000 - LOCK_STUCK_SECS + 60));
        assert!(ids(&doc(&i, NOW)).is_empty());
        i.lock = Some((40555, NOW / 1000 - LOCK_STUCK_SECS - 60));
        assert_eq!(ids(&doc(&i, NOW)), vec!["U1:refresh-lock"]);
        i.refresh_last = Ok(Some(json!({"what": "ok"})));
        assert!(ids(&doc(&i, NOW)).is_empty());
    }

    #[test]
    fn a_switch_off_with_recent_woulds_is_a_u4_row() {
        let mut i = base();
        i.switches = Ok(Switches {
            nudge: true,
            ..Switches::default()
        });
        i.woulds = Ok(vec![
            ("open_review".into(), NOW - 60_000),
            ("open_review".into(), NOW - 120_000),
            ("nudge".into(), NOW - 60_000),
            ("dispatch".into(), NOW - WOULD_WINDOW_MS - 1),
            ("unblock_notify".into(), NOW - 60_000),
        ]);
        let d = doc(&i, NOW);
        assert_eq!(ids(&d), vec!["U4:switch-open-review"]);
        assert!(d["rows"][0]["what"].as_str().unwrap().contains("排了 2 筆"));
    }

    #[test]
    fn a_file_it_cannot_read_covers_only_its_own_codes() {
        let mut i = base();
        i.next = Err("讀不到".into());
        i.switches = Err("act.json: x".into());
        let d = doc(&i, NOW);
        let u = d["unreadable"].as_array().unwrap();
        assert_eq!(u.len(), 2);
        assert_eq!(u[0]["covers"], json!(["U21", "U10"]));
        assert_eq!(u[1]["covers"], json!(["U4"]));
    }

    #[test]
    fn its_rows_pass_the_merge_check() {
        let mut i = base();
        i.next = Ok(
            json!({"sources": [{"name": "slice_due", "state": "stale", "why": "x"}],
            "for_andy": [{"id": "i_1"}]}),
        );
        i.refresh_last = Ok(Some(json!({"what": "held"})));
        i.lock = Some((1, 0));
        let src = crate::unblock::Src {
            name: NAME.into(),
            doc: Ok(doc(&i, NOW)),
        };
        let (m, _) = crate::unblock::merge(&[src], None, &Default::default(), NOW);
        assert_eq!(m["rows"].as_array().unwrap().len(), 3);
        assert!(m["sources"][0].get("why").is_none(), "{}", m["sources"][0]);
    }
}
