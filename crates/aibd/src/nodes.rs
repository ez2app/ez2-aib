//! N-workspace third wave, 7b81e item 1 (ORDER-workspace-519d0-1118 §第三波;SPEC-workspace-multiseat
//! §1.1 「換站規則沿用 aib-plan 現有的進站條件」): the built-in page adds and edits tree items in
//! aibd's node table. A station change goes through aib-plan's own entry conditions
//! (`aib_plan::card::entry_problems`, the one `aib-plan set` uses), with the same evidence
//! segment `aib-plan` writes. Only once the tree is in aibd (`AIBD_TREE_IN_DB=1` and the
//! table filled); before that the tree file is aib-plan's and these acts answer 409.
//!
//! `node_set` names the `rev` it read (`if_rev`): a second writer that read the same rev gets
//! 409 `node_rev_changed` with the rev now, not a silent overwrite.

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use aib_plan::card::{self, Facts, RealFacts, Words};
use aib_store::{NewEvent, NodeRow, NodeSet};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use std::path::PathBuf;

pub const ADD: &str = "node_add";
pub const SET: &str = "node_set";
/// The longest title / fact / need taken, in characters.
pub const TITLE_MAX: usize = 200;
pub const TEXT_MAX: usize = 4000;

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// What a page asked for.
#[derive(Debug, Clone, PartialEq)]
pub enum Ask {
    Add {
        parent_id: String,
        node_id: Option<String>,
        title: String,
        owner: Option<String>,
        why: Option<String>,
        need: Option<String>,
    },
    Set {
        node_id: String,
        if_rev: i64,
        fact: String,
        station: Option<String>,
        head: Option<String>,
        title: Option<String>,
        need: Option<String>,
    },
}

fn text(body: &Value, k: &str, max: usize) -> Result<Option<String>, (&'static str, String)> {
    match &body[k] {
        Value::Null => Ok(None),
        Value::String(s) if !s.trim().is_empty() && s.chars().count() <= max => {
            Ok(Some(s.trim().to_owned()))
        }
        _ => Err((
            "invalid_request",
            format!("{k} must be text of 1–{max} characters"),
        )),
    }
}

/// The body of a `node_add` / `node_set`: only its own fields; codes for a 400.
pub fn parse(body: &Value) -> Result<Ask, (&'static str, String)> {
    let kind = body["type"].as_str().unwrap_or("");
    let allowed: &[&str] = if kind == ADD {
        &[
            "type",
            "parent_id",
            "node_id",
            "title",
            "owner",
            "why",
            "need",
        ]
    } else {
        &[
            "type", "node_id", "if_rev", "fact", "station", "head", "title", "need",
        ]
    };
    if let Some(o) = body.as_object() {
        if let Some(k) = o.keys().find(|k| !allowed.contains(&k.as_str())) {
            return Err(("unknown_node_field", format!("{kind} does not take {k}")));
        }
    }
    let id = |k: &str| -> Result<Option<String>, (&'static str, String)> {
        match body[k].as_str() {
            None if body[k].is_null() => Ok(None),
            Some(s) if crate::assign::node_id_ok(s) && !s.contains(char::is_whitespace) => {
                Ok(Some(s.to_owned()))
            }
            _ => Err(("node_id", format!("{k} has the wrong shape"))),
        }
    };
    if kind == ADD {
        let parent_id = id("parent_id")?.ok_or(("node_id", "parent_id is needed".to_string()))?;
        let title = text(body, "title", TITLE_MAX)?
            .ok_or(("invalid_request", "title is needed".to_string()))?;
        let owner = match body["owner"].as_str() {
            None if body["owner"].is_null() => None,
            Some(s) if crate::assign::seat_id_ok(s) => Some(s.to_owned()),
            _ => return Err(("seat_id", "owner is a seat id".to_string())),
        };
        return Ok(Ask::Add {
            parent_id,
            node_id: id("node_id")?,
            title,
            owner,
            why: text(body, "why", TEXT_MAX)?,
            need: text(body, "need", TEXT_MAX)?,
        });
    }
    let node_id = id("node_id")?.ok_or(("node_id", "node_id is needed".to_string()))?;
    let if_rev = body["if_rev"]
        .as_i64()
        .filter(|r| *r >= 1)
        .ok_or(("if_rev", "if_rev is the rev the page read".to_string()))?;
    let fact = text(body, "fact", TEXT_MAX)?.ok_or((
        "invalid_request",
        "fact is needed: what happened, as aib-plan set --add".to_string(),
    ))?;
    let head = match body["head"].as_str() {
        None if body["head"].is_null() => None,
        Some(h) if h.len() == 40 && h.bytes().all(|b| b.is_ascii_hexdigit()) => Some(h.to_owned()),
        _ => return Err(("head", "head is 40 hex".to_string())),
    };
    Ok(Ask::Set {
        node_id,
        if_rev,
        fact,
        station: text(body, "station", 32)?,
        head,
        title: text(body, "title", TITLE_MAX)?,
        need: text(body, "need", TEXT_MAX)?,
    })
}

/// A node row as aib-plan's own `Node`, so its entry conditions read it as they read a card.
pub fn plan_node(row: &NodeRow) -> Option<aib_plan::Node> {
    let t = crate::workspace::tree_of_rows(std::slice::from_ref(row));
    serde_json::from_value(t["nodes"][0].clone()).ok()
}

fn extra_of(row: &NodeRow) -> Map<String, Value> {
    match serde_json::from_str::<Value>(&row.extra) {
        Ok(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

/// The new row for a `node_set` (or why not): `Err((status, code, message))`.
pub fn set_row(
    old: &NodeRow,
    ask: &Ask,
    by: &str,
    now: i64,
    f: &dyn Facts,
    w: &Words,
) -> Result<(NodeRow, Option<String>), (StatusCode, &'static str, String)> {
    let Ask::Set {
        fact,
        station,
        head,
        title,
        need,
        ..
    } = ask
    else {
        return Err((
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "not a node_set".into(),
        ));
    };
    let mut fact = fact.clone();
    if let Some(h) = head {
        if !fact.contains(h.as_str()) {
            fact = format!("{fact} 頭 {h}");
        }
    }
    let mut row = old.clone();
    let mut to = None;
    if let Some(st) = station {
        let Some(pos) = w.parse(st) else {
            return Err((
                StatusCode::BAD_REQUEST,
                "station",
                format!("station is one of {} / {}", w.names.join(" / "), w.void),
            ));
        };
        let k = w.name(pos).to_owned();
        if old.station.as_deref() != Some(k.as_str()) {
            let node = plan_node(old).ok_or((
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "the node could not be read".to_string(),
            ))?;
            let miss = card::entry_problems(pos, &fact, &node, f);
            if !miss.is_empty() {
                return Err((
                    StatusCode::CONFLICT,
                    "entry_refused",
                    format!("進「{k}」還缺:{}", miss.join(";")),
                ));
            }
            to = Some(k.clone());
        }
        row.station = Some(k);
    }
    if let Some(h) = head {
        row.head = Some(h.clone());
    }
    if let Some(t) = title {
        row.title = t.clone();
    }
    if let Some(n) = need {
        row.need = Some(n.clone());
    }
    let (at, full) = card::stamps(now);
    let mut extra = extra_of(old);
    let seg = card::segment(
        extra.get("evidence").and_then(Value::as_str),
        &at,
        by,
        &fact,
    );
    extra.insert("evidence".into(), json!(seg));
    extra.insert("updated".into(), json!(full));
    row.extra = Value::Object(extra).to_string();
    row.updated = crate::sessions::now_rfc3339();
    Ok((row, to))
}

/// The new row for a `node_add`: id given or `<parent>-c<k>` (as `aib-plan add`); station = the
/// first one; kind task; the opening segment as aib-plan writes it.
pub fn add_row(
    ask: &Ask,
    taken: &dyn Fn(&str) -> bool,
    by: &str,
    now: i64,
    w: &Words,
) -> Option<NodeRow> {
    let Ask::Add {
        parent_id,
        node_id,
        title,
        owner,
        why,
        need,
    } = ask
    else {
        return None;
    };
    let id = match node_id {
        Some(x) => x.clone(),
        None => (1..=10_000)
            .map(|k| format!("{parent_id}-c{k}"))
            .find(|c| !taken(c))?,
    };
    let (at, full) = card::stamps(now);
    let fact = format!("開卡:{}", why.as_deref().unwrap_or("(待補)"));
    let mut extra = Map::new();
    if let Some(o) = owner {
        extra.insert("owner".into(), json!(o));
    }
    if let Some(y) = why {
        extra.insert("why".into(), json!(y));
    }
    extra.insert(
        "evidence".into(),
        json!(card::segment(None, &at, by, &fact)),
    );
    extra.insert("updated".into(), json!(full));
    Some(NodeRow {
        node_id: id,
        parent_id: Some(parent_id.clone()),
        kind: "task".into(),
        title: title.clone(),
        station: Some(w.name(0).to_owned()),
        head: None,
        need: need.clone(),
        ord: 0,
        rev: 1,
        updated: crate::sessions::now_rfc3339(),
        extra: Value::Object(extra).to_string(),
    })
}

/// aib-plan's facts on this machine: `AIBD_PLAN_PENDING` or `~/.ez2-aib/pending`; the repo as
/// aib-plan finds it (`AIB_PLAN_REPO`, …).
pub(crate) fn facts() -> RealFacts {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    let pending = std::env::var_os("AIBD_PLAN_PENDING")
        .map(PathBuf::from)
        .or_else(|| home.as_ref().map(|h| h.join(".ez2-aib").join("pending")));
    RealFacts {
        pending,
        repo: home.as_deref().and_then(|h| card::repo_dir(None, h)),
    }
}

/// H5 (CHECKLIST-workspace-wave3 H5, card N-workspace-c1): the old flow board page still reads
/// the cabin-plan `data.json`. With `AIBD_TREE_EXPORT` naming that file (off when unset), each
/// node write here is written there too, the way `aib-plan` writes it: its lock, only that card,
/// its own check of the new text, a rename, one line in its event log. Never the whole file.
pub const EXPORT_ENV: &str = "AIBD_TREE_EXPORT";

fn export_path() -> Option<PathBuf> {
    std::env::var_os(EXPORT_ENV)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// The `aib-plan` op for a write already done in aibd (`node_id`: the id the row got).
pub fn export_op(ask: &Ask, node_id: &str, owner: &str) -> card::Op {
    match ask {
        Ask::Add {
            parent_id,
            title,
            why,
            need,
            ..
        } => card::Op::Add {
            parent: parent_id.clone(),
            id: Some(node_id.to_owned()),
            title: title.clone(),
            owner: owner.to_owned(),
            why: why.clone(),
            reviewer: None,
            need: need.clone(),
        },
        Ask::Set {
            fact,
            station,
            head,
            need,
            ..
        } => card::Op::Set {
            id: node_id.to_owned(),
            status: station.clone(),
            fact: fact.clone(),
            head: head.clone(),
            wait: None,
            reviewer: None,
            need: need.clone(),
        },
    }
}

/// Write one op into the exported file: `{"ok": true}`, or `{"ok": false, "error": <aib-plan's
/// code>}` (only the code before its first colon: its messages can name paths).
pub fn export_to(
    data: &std::path::Path,
    op: &card::Op,
    by: &str,
    now: i64,
    f: &dyn Facts,
    w: &Words,
) -> Value {
    match card::write_with(data, op, by, now, f, w) {
        Ok(_) => json!({"ok": true}),
        Err(e) => {
            let code: String = e
                .split([':', '：'])
                .next()
                .unwrap_or("")
                .chars()
                .take(40)
                .collect();
            json!({"ok": false, "error": code})
        }
    }
}

/// The export of one aibd write. A title change has no `aib-plan` op, so it stays in aibd and the
/// answer says `title: "not_exported"`.
pub fn export(
    data: &std::path::Path,
    ask: &Ask,
    node_id: &str,
    owner: &str,
    by: &str,
    now: i64,
) -> Value {
    let v0 = export_to(
        data,
        &export_op(ask, node_id, owner),
        by,
        now,
        &facts(),
        &Words::default(),
    );
    if v0["ok"] == json!(true) && crate::workspace::tree_path().as_deref() == Some(data) {
        crate::plan_route::remember(data);
    }
    let mut v = v0;
    if matches!(ask, Ask::Set { title: Some(_), .. }) {
        v["title"] = json!("not_exported");
    }
    v
}

fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0))
}

enum Fail {
    NotInAibd,
    Refused(StatusCode, &'static str, String),
    Internal,
}

/// `POST /api/v1/act` `node_add` / `node_set`, from the built-in page (the caller checks that).
pub async fn act(st: AppState, c: Caller, body: Value) -> Response {
    let Some(db) = crate::assign::db() else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "assignments_unavailable",
            "the workspace file could not be opened; see the log",
        );
    };
    let ask = match parse(&body) {
        Ok(a) => a,
        Err((code, msg)) => return refuse(StatusCode::BAD_REQUEST, code, &msg),
    };
    let by = crate::contacts_api::page_key(&c).unwrap_or_else(|| "startup".into());
    let done = tokio::task::spawn_blocking(move || {
        // L2: a write aibd did not make lets go of the tree first
        if let Some(p) = crate::workspace::tree_path() {
            crate::plan_route::check_outside(&p, Some(&*st.store));
        }
        if !crate::workspace::tree_is_in_aibd() {
            return Err(Fail::NotInAibd);
        }
        let rows = db.nodes().map_err(|_| Fail::Internal)?;
        let w = Words::default();
        let now = now_epoch();
        // both arms set it (H5: the old page's file, or null when off)
        let exported: Option<Value>;
        let (kind, payload, out) = match &ask {
            Ask::Add { parent_id, .. } => {
                if !rows.iter().any(|r| &r.node_id == parent_id) {
                    return Err(Fail::Refused(StatusCode::NOT_FOUND, "node_not_found", "no such parent".into()));
                }
                let taken = |id: &str| rows.iter().any(|r| r.node_id == id);
                let row = add_row(&ask, &taken, &by, now, &w).ok_or(Fail::Internal)?;
                if !db.add_node(row.clone()).map_err(|_| Fail::Internal)? {
                    return Err(Fail::Refused(StatusCode::CONFLICT, "node_exists", "that id is taken".into()));
                }
                let p = json!({"node_id": row.node_id, "parent_id": parent_id, "by": by});
                let owner = extra_of(&row).get("owner").and_then(Value::as_str).unwrap_or("").to_owned();
                exported = export_path().map(|d| export(&d, &ask, &row.node_id, &owner, &by, now));
                ("node.added", p, json!({"node_id": row.node_id, "rev": 1, "station": row.station}))
            }
            Ask::Set { node_id, if_rev, .. } => {
                let old = rows
                    .iter()
                    .find(|r| &r.node_id == node_id)
                    .ok_or(Fail::Refused(StatusCode::NOT_FOUND, "node_not_found", "no such node".into()))?;
                let (row, to) = set_row(old, &ask, &by, now, &facts(), &w)
                    .map_err(|(s, c, m)| Fail::Refused(s, c, m))?;
                let rev = match db.set_node(row.clone(), *if_rev).map_err(|_| Fail::Internal)? {
                    NodeSet::Done(r) => r,
                    NodeSet::Stale(r) => {
                        return Err(Fail::Refused(
                            StatusCode::CONFLICT,
                            "node_rev_changed",
                            format!("the node is at rev {r} now; read it again"),
                        ))
                    }
                    NodeSet::Missing => {
                        return Err(Fail::Refused(StatusCode::NOT_FOUND, "node_not_found", "no such node".into()))
                    }
                };
                let p = json!({"node_id": node_id, "from": old.station, "to": to, "rev": rev, "by": by});
                exported = export_path().map(|d| export(&d, &ask, node_id, "", &by, now));
                ("node.set", p, json!({"node_id": node_id, "rev": rev, "station": row.station, "moved": to.is_some()}))
            }
        };
        let ev = crate::sessions::new_id("ev_").ok().map(|id| NewEvent {
            event_id: id.clone(),
            aggregate_type: "node".into(),
            aggregate_id: id,
            sequence: 1,
            event_type: kind.into(),
            correlation_id: None,
            occurred_at: crate::sessions::now_rfc3339(),
            payload,
        });
        let journaled = ev.is_some_and(|ev| st.store.append(vec![ev]).is_ok());
        let mut out = out;
        out["journaled"] = json!(journaled);
        // null = the export is off (AIBD_TREE_EXPORT unset)
        out["exported"] = exported.unwrap_or(Value::Null);
        Ok(out)
    })
    .await;
    match done {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(Fail::NotInAibd)) => refuse(
            StatusCode::CONFLICT,
            "tree_not_in_aibd",
            "the tree is still aib-plan's file; items are added there until AIBD_TREE_IN_DB is on",
        ),
        Ok(Err(Fail::Refused(s, c, m))) => refuse(s, c, &m),
        Ok(Err(Fail::Internal)) | Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the node was not written",
        ),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// No pending files, no repo, no PRs: only the conditions that read the fact itself pass.
    struct NoFacts;
    impl Facts for NoFacts {
        fn pending(&self, _: &str) -> Option<String> {
            None
        }
        fn in_main(&self, _: &str) -> Option<bool> {
            None
        }
        fn ancestor(&self, _: &str, _: &str) -> Option<bool> {
            None
        }
        fn pr(&self, _: u32) -> Option<(bool, String)> {
            None
        }
        fn pr_of_branch(&self, _: &str) -> Option<(bool, String)> {
            None
        }
    }

    fn row(station: &str) -> NodeRow {
        NodeRow {
            node_id: "N-c1".into(),
            parent_id: Some("N".into()),
            kind: "task".into(),
            title: "樹的寫入".into(),
            station: Some(station.into()),
            head: None,
            need: None,
            ord: 3,
            rev: 2,
            updated: "t".into(),
            extra: r#"{"owner":"7b81e","evidence":"11-18 10:00 7b81e 開卡"}"#.into(),
        }
    }

    const OLD_PAGE: &str = "{\n \"nodes\": [\n  {\n   \"id\": \"N\",\n   \"parent\": null,\n   \"kind\": \"goal\",\n   \"title\": \"工作台 \\\"一\\\"\",\n   \"evidence\": \"—\"\n  },\n  {\n   \"id\": \"N-c1\",\n   \"parent\": \"N\",\n   \"title\": \"樹的寫入\",\n   \"status\": \"開發中\",\n   \"owner\": \"7b81e\",\n   \"evidence\": \"11-18 10:00 7b81e 開卡\",\n   \"after\": []\n  }\n ],\n \"asof\": \"11-18\",\n \"cabin\": \"dab9\"\n}";

    #[test]
    fn a_write_reaches_the_old_pages_file_one_card_at_a_time_the_aib_plan_way() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data.json");
        std::fs::write(&data, OLD_PAGE).unwrap();
        let w = Words::default();
        let now = 1_794_960_000;
        // a fact on N-c1: only that card changes, the rest of the text stays byte for byte
        let v = export_to(
            &data,
            &export_op(&set(None, "看過樹"), "N-c1", ""),
            "human:ab",
            now,
            &NoFacts,
            &w,
        );
        assert_eq!(v["ok"], true, "{v}");
        let after = std::fs::read_to_string(&data).unwrap();
        assert!(after.contains("看過樹"), "reason: export_not_written");
        let head_part = &OLD_PAGE[..OLD_PAGE.find("\"id\": \"N-c1\"").unwrap()];
        assert!(after.starts_with(head_part), "reason: whole_file_rewritten");
        assert!(after.ends_with("\"cabin\": \"dab9\"\n}"));
        let log = std::fs::read_to_string(d.path().join("plan-events.jsonl")).unwrap();
        assert_eq!(log.lines().count(), 1);
        // a new card under N, with the id aibd gave it
        let add = Ask::Add {
            parent_id: "N".into(),
            node_id: None,
            title: "匯出".into(),
            owner: Some("7b81e".into()),
            why: Some("H5".into()),
            need: None,
        };
        let v = export_to(
            &data,
            &export_op(&add, "N-c2", "7b81e"),
            "human:ab",
            now,
            &NoFacts,
            &w,
        );
        assert_eq!(v["ok"], true, "{v}");
        let plan: Value = serde_json::from_str(&std::fs::read_to_string(&data).unwrap()).unwrap();
        assert!(plan["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|n| n["id"] == "N-c2"));
        // someone else holds aib-plan's lock: nothing written, only the code comes back
        let before = std::fs::read_to_string(&data).unwrap();
        let held = card::Lock::take(&data, now).unwrap();
        let v = export_to(
            &data,
            &export_op(&set(None, "又看"), "N-c1", ""),
            "human:ab",
            now,
            &NoFacts,
            &w,
        );
        drop(held);
        assert_eq!(
            (v["ok"].as_bool(), v["error"].as_str()),
            (Some(false), Some("locked"))
        );
        assert!(
            !v.to_string().contains(d.path().to_str().unwrap()),
            "reason: path_shown"
        );
        assert_eq!(std::fs::read_to_string(&data).unwrap(), before);
        // a title change has no aib-plan op
        let mut t = set(None, "改名");
        if let Ask::Set { title, .. } = &mut t {
            *title = Some("新名".into());
        }
        assert_eq!(
            export(&data, &t, "N-c1", "", "human:ab", now)["title"],
            "not_exported"
        );
    }

    fn set(station: Option<&str>, fact: &str) -> Ask {
        Ask::Set {
            node_id: "N-c1".into(),
            if_rev: 2,
            fact: fact.into(),
            station: station.map(str::to_owned),
            head: None,
            title: None,
            need: None,
        }
    }

    #[test]
    fn a_node_act_takes_only_its_own_fields() {
        let ok = json!({"type": "node_set", "node_id": "N", "if_rev": 1, "fact": "f"});
        assert!(matches!(parse(&ok), Ok(Ask::Set { if_rev: 1, .. })));
        let extra = json!({"type": "node_set", "node_id": "N", "if_rev": 1, "fact": "f", "rev": 9});
        assert_eq!(parse(&extra).unwrap_err().0, "unknown_node_field");
        let no_rev = json!({"type": "node_set", "node_id": "N", "fact": "f"});
        assert_eq!(
            parse(&no_rev).unwrap_err().0,
            "if_rev",
            "reason: blind_write"
        );
        let no_fact = json!({"type": "node_set", "node_id": "N", "if_rev": 1});
        assert_eq!(parse(&no_fact).unwrap_err().0, "invalid_request");
        let short =
            json!({"type": "node_set", "node_id": "N", "if_rev": 1, "fact": "f", "head": "abc"});
        assert_eq!(parse(&short).unwrap_err().0, "head");
        let add =
            json!({"type": "node_add", "parent_id": "N", "title": "新項目", "owner": "7b81e"});
        assert!(matches!(parse(&add), Ok(Ask::Add { node_id: None, .. })));
        let spaced = json!({"type": "node_add", "parent_id": "N", "node_id": "a b", "title": "t"});
        assert_eq!(parse(&spaced).unwrap_err().0, "node_id");
    }

    #[test]
    fn a_station_moves_only_through_aib_plans_entry_conditions() {
        let w = Words::default();
        // 開發中 needs a branch or a 40-hex head in the fact (aib-plan's own rule)
        let e = set_row(
            &row("未開始"),
            &set(Some("開發中"), "開始做"),
            "human:ab",
            0,
            &NoFacts,
            &w,
        )
        .unwrap_err();
        assert_eq!(
            (e.0, e.1),
            (StatusCode::CONFLICT, "entry_refused"),
            "reason: entry_skipped"
        );
        assert!(e.2.contains("分支"), "{}", e.2);
        let (r, to) = set_row(
            &row("未開始"),
            &set(Some("開發中"), "開分支 feat/ws-nodew-7b81e"),
            "human:ab",
            0,
            &NoFacts,
            &w,
        )
        .unwrap();
        assert_eq!(to.as_deref(), Some("開發中"));
        assert_eq!(r.station.as_deref(), Some("開發中"));
        let extra: Value = serde_json::from_str(&r.extra).unwrap();
        let ev = extra["evidence"].as_str().unwrap();
        assert!(
            ev.starts_with("11-18 10:00 7b81e 開卡 | "),
            "reason: evidence_replaced {ev}"
        );
        assert!(ev.ends_with("human:ab 開分支 feat/ws-nodew-7b81e"), "{ev}");
        assert_eq!(extra["owner"], "7b81e", "reason: extra_lost");
        // the same station again, or no station, is a note: no entry check
        let (_, to) = set_row(
            &row("開發中"),
            &set(Some("開發中"), "記一筆"),
            "h",
            0,
            &NoFacts,
            &w,
        )
        .unwrap();
        assert_eq!(to, None);
        // 審過未推 needs a review file this machine does not have
        let e = set_row(
            &row("開發中"),
            &set(Some("審過未推"), "審過 feat/ws-nodew-7b81e"),
            "h",
            0,
            &NoFacts,
            &w,
        )
        .unwrap_err();
        assert_eq!(e.1, "entry_refused");
        // 5d33a wave-3 H2: the page cannot jump to 實查通過 without a verifier and evidence
        let e = set_row(
            &row("開發中"),
            &set(Some("實查通過"), "看過了"),
            "h",
            0,
            &NoFacts,
            &w,
        )
        .unwrap_err();
        assert_eq!(e.1, "entry_refused", "reason: pass_without_verifier");
        let e = set_row(
            &row("開發中"),
            &set(Some("差不多"), "f"),
            "h",
            0,
            &NoFacts,
            &w,
        )
        .unwrap_err();
        assert_eq!(e.1, "station");
    }

    #[test]
    fn a_new_item_starts_at_the_first_station_with_the_next_free_child_id() {
        let w = Words::default();
        let ask = Ask::Add {
            parent_id: "N".into(),
            node_id: None,
            title: "新項目".into(),
            owner: Some("7b81e".into()),
            why: Some("Andy 全部都上".into()),
            need: None,
        };
        let r = add_row(&ask, &|id: &str| id == "N-c1", "human:ab", 0, &w).unwrap();
        assert_eq!(r.node_id, "N-c2", "reason: child_id_taken");
        assert_eq!(r.station.as_deref(), Some("未開始"));
        assert_eq!(r.parent_id.as_deref(), Some("N"));
        let extra: Value = serde_json::from_str(&r.extra).unwrap();
        assert!(extra["evidence"]
            .as_str()
            .unwrap()
            .ends_with("human:ab 開卡:Andy 全部都上"));
        assert_eq!(extra["owner"], "7b81e");
        assert!(plan_node(&r).is_some_and(|n| n.owner.as_deref() == Some("7b81e")));
    }
}
