//! `aib-drive own done | progress | none` (S79-own o-2, DECISION-council-s79-own-519d0-1116
//! r1 題 2; ledger shape a9136 IFACE-s79-own-gate r1 §1 with 56cda's four points).
//!
//! One JSON line a call, appended to `<root>/drive/own.jsonl` in one `O_APPEND` write (no
//! lock; a line is far below the pipe buffer, a9136 r1). Common fields `v`, `at_ms`, `seat`,
//! `kind`, `turn_id`. o-2 writes only `done` / `progress` / `none`; `turn_start`, `blocked`,
//! `exempt_*`, `fault`, `unreadable` are the gate's (o-3).
//!
//! Before writing, the caller re-runs o-1 ([`crate::own::own_list`]) and passes it here:
//! `done` / `progress` need the id among the listable items now; the item's `why_you` is kept
//! as `snapshot`. `none` keeps the list's total and skip reasons as `snapshot`.
//!
//! No `turn_start` for the seat in the ledger ⇒ the line is `kind: "manual"` with `as: <kind>`
//! (the gate ignores it, OW11).
//!
//! Limits, said plainly: this checks the evidence is there and has the right shape. It does
//! not prove the commit is on the item's branch, nor that a review's first line names the
//! pushed head — those stay with the reviewer. A same-user AI can write any line here; the
//! ledger is a discipline record, not a guard.

use crate::own::{Item, OwnOut};
use serde_json::{json, Value};
use std::collections::BTreeSet;

pub const LEDGER: &str = "own.jsonl";
pub const KINDS: &[&str] = &["done", "progress", "none"];
/// The evidence types (r1 題 2, seven).
pub const EV_TYPES: &[&str] = &["file", "commit", "pr", "receipt", "review", "node", "dm"];
pub const RC_OK: i32 = 0;
pub const RC_REFUSED: i32 = 1;
pub const RC_WRITE: i32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Evidence {
    pub ty: String,
    pub reference: String,
    pub sha256: Option<String>,
}

impl Evidence {
    /// The key a piece of evidence is counted once by: its sha256, else `type:ref` (OW8).
    pub fn key(&self) -> String {
        self.sha256
            .clone()
            .unwrap_or_else(|| format!("{}:{}", self.ty, self.reference))
    }
    pub fn to_json(&self) -> Value {
        let mut v = json!({"type": self.ty, "ref": self.reference});
        if let Some(h) = &self.sha256 {
            v["sha256"] = json!(h);
        }
        v
    }
}

fn is_hex(s: &str, n: usize) -> bool {
    s.len() == n
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// A receipt's `RC <name>=<n>` lines: at least one, all 0.
fn receipt_all_zero(text: &str) -> Result<(), String> {
    let mut seen = 0;
    for l in text.lines() {
        let Some(rest) = l.strip_prefix("RC ") else {
            continue;
        };
        let Some((name, rc)) = rest.split_once('=') else {
            continue;
        };
        seen += 1;
        if rc.trim() != "0" {
            return Err(format!("收據 RC {name}={} 不是 0", rc.trim()));
        }
    }
    if seen == 0 {
        return Err("收據裡沒有 RC 行".to_owned());
    }
    Ok(())
}

/// `<type>:<ref>` checked for `kind`. `read` gives a file's bytes (None ⇒ missing); `used`
/// holds the keys already counted in the ledger.
pub fn check_evidence(
    kind: &str,
    arg: &str,
    read: &dyn Fn(&str) -> Option<Vec<u8>>,
    used: &BTreeSet<String>,
) -> Result<Evidence, String> {
    let Some((ty, reference)) = arg.split_once(':') else {
        return Err(format!(
            "證據要寫 <type>:<ref>,type 是 {}",
            EV_TYPES.join(" / ")
        ));
    };
    let (ty, reference) = (ty.trim(), reference.trim());
    if !EV_TYPES.contains(&ty) {
        return Err(format!(
            "證據種類 {ty} 不認得;只認 {}",
            EV_TYPES.join(" / ")
        ));
    }
    if reference.is_empty() {
        return Err("證據沒寫 ref".to_owned());
    }
    let mut sha256 = None;
    match ty {
        "node" if kind != "progress" => {
            return Err("node 證據只准 progress(節點改狀態不算做完)".to_owned())
        }
        "file" | "receipt" | "review" => {
            let Some(bytes) = read(reference) else {
                return Err(format!("{ty} 檔讀不到:{reference}"));
            };
            if ty == "receipt" {
                receipt_all_zero(&String::from_utf8_lossy(&bytes))?;
            }
            if ty == "review"
                && String::from_utf8_lossy(&bytes)
                    .lines()
                    .next()
                    .unwrap_or("")
                    .trim()
                    .is_empty()
            {
                return Err("審查稿第一行是空的(要綁頭)".to_owned());
            }
            sha256 = Some(crate::next::sha256_hex_bytes(&bytes));
        }
        "commit" if !is_hex(reference, 40) => {
            return Err("commit 要寫 40 碼小寫".to_owned());
        }
        "pr" => {
            let url_ok = reference.starts_with("https://github.com/")
                && reference
                    .rsplit_once("/pull/")
                    .is_some_and(|(_, n)| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
            if !url_ok && !is_hex(reference, 40) {
                return Err(
                    "pr 要寫完整網址 https://github.com/…/pull/N 或 40 碼合併頭碼".to_owned(),
                );
            }
        }
        "dm" => {
            let hex = reference.strip_prefix("q_").unwrap_or("");
            if hex.is_empty() || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err("dm 證據要寫 q_ 開頭的信件 id".to_owned());
            }
        }
        _ => {}
    }
    let ev = Evidence {
        ty: ty.to_owned(),
        reference: reference.to_owned(),
        sha256,
    };
    if used.contains(&ev.key()) {
        return Err(format!("這份證據已經抵過一次:{}", ev.key()));
    }
    Ok(ev)
}

/// The seat's last `turn_start` turn_id in the ledger text.
pub fn last_turn(ledger: &str, seat: &str) -> Option<String> {
    ledger
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|v| v["seat"] == seat && v["kind"] == "turn_start")
        .filter_map(|v| v["turn_id"].as_str().map(str::to_owned))
        .next_back()
}

/// Every evidence key already in the ledger, any seat (the same sha counts once, OW8).
pub fn used_keys(ledger: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    for v in ledger
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
    {
        let ev = &v["evidence"];
        if let Some(h) = ev["sha256"].as_str() {
            out.insert(h.to_owned());
        } else if let (Some(t), Some(r)) = (ev["type"].as_str(), ev["ref"].as_str()) {
            out.insert(format!("{t}:{r}"));
        }
    }
    out
}

pub struct WriteReq<'a> {
    pub kind: &'a str,
    pub seat: &'a str,
    pub id: Option<&'a str>,
    pub evidence: Option<&'a str>,
    pub next: Option<&'a str>,
    pub why: Option<&'a str>,
    pub at_ms: i64,
}

/// The line to append, or why it is refused. `list` is o-1 run now; `ledger` the ledger's
/// text so far ("" when there is none yet).
pub fn build_line(
    req: &WriteReq<'_>,
    list: &OwnOut,
    ledger: &str,
    read: &dyn Fn(&str) -> Option<Vec<u8>>,
) -> Result<Value, String> {
    if !KINDS.contains(&req.kind) {
        return Err(format!("只認 {}", KINDS.join(" / ")));
    }
    let turn = last_turn(ledger, req.seat);
    let mut v = json!({
        "v": 1,
        "at_ms": req.at_ms,
        "seat": req.seat,
        "kind": if turn.is_some() { req.kind } else { "manual" },
        "turn_id": turn.clone().unwrap_or_else(|| "manual".to_owned()),
    });
    if turn.is_none() {
        v["as"] = json!(req.kind);
    }
    if req.kind == "none" {
        let why = req.why.map(str::trim).unwrap_or_default();
        if why.is_empty() {
            return Err("none 要帶 --why".to_owned());
        }
        v["why"] = json!(why);
        v["snapshot"] = json!({
            "total": list.total,
            "skipped": list.skipped,
            "sources": list.sources,
        });
        return Ok(v);
    }
    let id = req.id.map(str::trim).unwrap_or_default();
    if id.is_empty() {
        return Err(format!("{} 要帶 --id(aib-drive own 列出的 id)", req.kind));
    }
    let Some(item): Option<&Item> = list.all.iter().find(|i| i.id == id) else {
        return Err(format!(
            "{id} 現在不在 {} 可做的清單裡(先跑 aib-drive own --seat {})",
            req.seat, req.seat
        ));
    };
    let Some(ev) = req.evidence else {
        return Err(format!("{} 要帶 --evidence <type>:<ref>", req.kind));
    };
    let ev = check_evidence(req.kind, ev, read, &used_keys(ledger))?;
    v["id"] = json!(id);
    v["evidence"] = ev.to_json();
    v["verified_by"] = json!("aib-drive own");
    v["snapshot"] = json!(item.why_you);
    if req.kind == "progress" {
        let next = req.next.map(str::trim).unwrap_or_default();
        if next.is_empty() {
            return Err("progress 要帶 --next(下一步)".to_owned());
        }
        v["next"] = json!(next);
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::own::{own_list, OwnInput};
    use serde_json::json;

    fn list() -> OwnOut {
        let plan = json!({"nodes": [{"id": "S1", "status": "開發中", "owner": "56cda"}]});
        let claimed = BTreeSet::new();
        own_list(&OwnInput {
            seat: "56cda",
            role: "dev",
            plan: Some(&plan),
            plan_txt: None,
            dms: Some(&[]),
            claimed: &claimed,
            as_of: "t",
        })
    }

    fn req<'a>(kind: &'a str, id: Option<&'a str>, ev: Option<&'a str>) -> WriteReq<'a> {
        WriteReq {
            kind,
            seat: "56cda",
            id,
            evidence: ev,
            next: Some("下一步"),
            why: None,
            at_ms: 5,
        }
    }

    const TURN: &str = "{\"v\":1,\"at_ms\":1,\"seat\":\"56cda\",\"kind\":\"turn_start\",\"turn_id\":\"56cda-1\"}\n";
    const C40: &str = "commit:0123456789abcdef0123456789abcdef01234567";

    fn files(p: &str) -> Option<Vec<u8>> {
        match p {
            "r.out" => Some(b"RC fmt=0\nRC test=0\n".to_vec()),
            "bad.out" => Some(b"RC fmt=0\nRC test=101\n".to_vec()),
            "a.md" => Some(b"head 0123abc\nbody".to_vec()),
            _ => None,
        }
    }

    #[test]
    fn a_done_on_a_listed_item_with_evidence_is_written_under_the_last_turn() {
        let v = build_line(
            &req("done", Some("node:S1"), Some(C40)),
            &list(),
            TURN,
            &files,
        )
        .expect("ok");
        assert_eq!(v["kind"], "done");
        assert_eq!(v["turn_id"], "56cda-1");
        assert_eq!(v["evidence"]["type"], "commit");
        assert_eq!(v["snapshot"], "owner 有 56cda");
        // an id not listable now is refused
        let e = build_line(
            &req("done", Some("node:S9"), Some(C40)),
            &list(),
            TURN,
            &files,
        )
        .unwrap_err();
        assert!(e.contains("不在"), "{e}");
    }

    #[test]
    fn no_turn_start_makes_the_line_manual() {
        let v = build_line(
            &req("done", Some("node:S1"), Some(C40)),
            &list(),
            "",
            &files,
        )
        .expect("ok");
        assert_eq!(v["kind"], "manual");
        assert_eq!(v["as"], "done");
        assert_eq!(v["turn_id"], "manual");
    }

    #[test]
    fn a_node_is_progress_only_and_the_same_evidence_counts_once() {
        let e = build_line(
            &req("done", Some("node:S1"), Some("node:S1")),
            &list(),
            TURN,
            &files,
        )
        .unwrap_err();
        assert!(e.contains("只准 progress"), "{e}");
        assert!(build_line(
            &req("progress", Some("node:S1"), Some("node:S1")),
            &list(),
            TURN,
            &files
        )
        .is_ok());
        let first = build_line(
            &req("done", Some("node:S1"), Some("file:a.md")),
            &list(),
            TURN,
            &files,
        )
        .expect("ok");
        let ledger = format!("{TURN}{first}\n");
        let e = build_line(
            &req("done", Some("node:S1"), Some("file:a.md")),
            &list(),
            &ledger,
            &files,
        )
        .unwrap_err();
        assert!(e.contains("已經抵過"), "{e}");
    }

    #[test]
    fn evidence_shapes_are_checked() {
        let used = BTreeSet::new();
        let ok = |a: &str| check_evidence("done", a, &files, &used);
        assert!(ok("receipt:r.out").is_ok());
        assert!(ok("receipt:bad.out").unwrap_err().contains("101"));
        assert!(ok("file:missing").is_err());
        assert!(ok("commit:abc").is_err());
        assert!(ok("pr:https://github.com/ez2app/x/pull/12").is_ok());
        assert!(ok("pr:#12").is_err());
        assert!(ok("dm:q_1a2b").is_ok());
        assert!(ok("dm:hello").is_err());
        assert!(ok("photo:x").is_err());
        assert!(ok("review:a.md").expect("ok").sha256.is_some());
    }

    #[test]
    fn none_needs_a_why_and_keeps_the_list_snapshot() {
        assert!(build_line(&req("none", None, None), &list(), TURN, &files).is_err());
        let mut r = req("none", None, None);
        r.why = Some("名下都在等收據");
        let v = build_line(&r, &list(), TURN, &files).expect("ok");
        assert_eq!(v["snapshot"]["total"], 1);
        assert_eq!(v["why"], "名下都在等收據");
    }
}
