//! N-workspace second wave (ORDER-workspace-519d0-1118 §第二波, 7b81e row; SPEC-workspace-multiseat
//! §1.2): who works on which node, as what. One assignment = node × seat × role, many to many.
//!
//! - `GET /api/v1/assignments?node=&seat=`: the rows, filtered.
//! - `GET /api/v1/nodes/{id}/assignments`: a node's own rows and, through the tree's parents,
//!   the rows of every node above it (`inherited`, each with `from_node`): a seat on an upper
//!   node sees everything below (§1.2).
//! - act `assign_add` / `assign_remove` `{node_id, seat_id, role}` (a confirmed page): the seat
//!   is one aibd knows; while there is a tree file the node is one of its nodes. Each write
//!   leaves an `assignment.added` / `assignment.removed` event for the page's poll.
//! - [`wakes`]: seeing is not being woken. A seat is called up only when it is @-named or an
//!   event of level ① or ② turns up in its subtree (§1.2, §7); the delivery side calls this.
//!
//! The rows live in `<data>/workspace.sqlite` ([`aib_store::WorkspaceDb`]). If that file
//! cannot be opened aibd still starts, and these routes answer 503 `assignments_unavailable`.

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use aib_store::{Added, AssignRow, NewEvent, WorkspaceDb};
use axum::extract::{Path as UrlPath, Query};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;

/// The act types this module answers.
pub const ADD: &str = "assign_add";
pub const REMOVE: &str = "assign_remove";

/// The six roles (§1.2: 作者 / 審查 / 驗收 / 換上 / 相依 / 旁聽), as stored and as the page
/// sends them.
pub const ROLES: &[&str] = &[
    "author", "reviewer", "verifier", "deployer", "depends", "observer",
];
/// The longest node id taken, in characters.
pub const NODE_ID_MAX: usize = 128;
/// The longest seat id taken, in bytes (ASCII only).
pub const SEAT_ID_MAX: usize = 64;
/// How many parents up a node's inherited rows are looked for.
pub const ANCESTORS_MAX: usize = 64;

static DB: std::sync::OnceLock<Arc<WorkspaceDb>> = std::sync::OnceLock::new();

pub fn set_db(db: Arc<WorkspaceDb>) {
    let _ = DB.set(db);
}

/// `(seat_id, role)` on `node` itself, read now (a9136 threads-multi, 5d33a wave-2 N1 / N2:
/// a tree item's thread members are these, so removing one takes the thread away at the next
/// read). `None` when the workspace file did not open. Blocking (SQLite).
pub fn members_of(node: &str) -> Option<Vec<(String, String)>> {
    DB.get()?.assignments_of(node).ok()
}

/// The nodes `seat_id` is assigned on itself, each once. `None` as [`members_of`].
pub fn nodes_of(seat_id: &str) -> Option<Vec<String>> {
    let rows = DB.get()?.list(None, Some(seat_id)).ok()?;
    let mut v: Vec<String> = Vec::new();
    for r in rows {
        if !v.contains(&r.node_id) {
            v.push(r.node_id);
        }
    }
    Some(v)
}

/// The workspace library, once the server opened it.
pub fn db() -> Option<Arc<WorkspaceDb>> {
    DB.get().cloned()
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

fn unavailable() -> Response {
    refuse(
        StatusCode::SERVICE_UNAVAILABLE,
        "assignments_unavailable",
        "the workspace file could not be opened; see the log",
    )
}

/// Whether a seat is called up for something in a node it can see: only when it is
/// @-named, or the event is of level ① (a person decides) or ② (broken).
pub fn wakes(named: bool, level: Option<u8>) -> bool {
    named || matches!(level, Some(1 | 2))
}

/// A node id: 1–[`NODE_ID_MAX`] characters, no control characters, no surrounding space.
pub fn node_id_ok(s: &str) -> bool {
    let n = s.chars().count();
    (1..=NODE_ID_MAX).contains(&n) && !s.chars().any(char::is_control) && s.trim() == s
}

/// A seat id: 1–[`SEAT_ID_MAX`] of `A-Z a-z 0-9 _ -`.
pub fn seat_id_ok(s: &str) -> bool {
    (1..=SEAT_ID_MAX).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// `(node_id, seat_id, role)` of an add / remove body; any other field is refused.
pub fn parse(body: &Value) -> Result<(String, String, String), (&'static str, &'static str)> {
    let Some(o) = body.as_object() else {
        return Err(("invalid_request", "body must be a JSON object"));
    };
    if o.keys()
        .any(|k| !matches!(k.as_str(), "type" | "node_id" | "seat_id" | "role"))
    {
        return Err((
            "unknown_assign_field",
            "only type, node_id, seat_id and role are taken",
        ));
    }
    let s = |k: &str| o.get(k).and_then(Value::as_str);
    let (Some(node), Some(seat), Some(role)) = (s("node_id"), s("seat_id"), s("role")) else {
        return Err(("invalid_request", "node_id, seat_id and role are strings"));
    };
    if !node_id_ok(node) {
        return Err((
            "node_id",
            "node_id is 1-128 characters, no control characters",
        ));
    }
    if !seat_id_ok(seat) {
        return Err(("seat_id", "seat_id is 1-64 of A-Z a-z 0-9 _ -"));
    }
    if !ROLES.contains(&role) {
        return Err((
            "role",
            "role is author, reviewer, verifier, deployer, depends or observer",
        ));
    }
    Ok((node.to_owned(), seat.to_owned(), role.to_owned()))
}

/// The parents of `id` in the tree, nearest first, at most [`ANCESTORS_MAX`]; a loop ends it.
pub fn ancestors(tree: &Value, id: &str) -> Vec<String> {
    let empty = Vec::new();
    let mut parent: HashMap<&str, &str> = HashMap::new();
    for n in tree["nodes"].as_array().unwrap_or(&empty) {
        if let (Some(i), Some(p)) = (n["id"].as_str(), n["parent"].as_str()) {
            parent.entry(i).or_insert(p);
        }
    }
    let mut out: Vec<String> = Vec::new();
    let mut cur = id;
    while let Some(p) = parent.get(cur).copied() {
        if p == id || out.iter().any(|o| o == p) || out.len() >= ANCESTORS_MAX {
            break;
        }
        out.push(p.to_owned());
        cur = p;
    }
    out
}

/// Whether the tree has a node `id`.
pub fn has_node(tree: &Value, id: &str) -> bool {
    tree["nodes"]
        .as_array()
        .is_some_and(|ns| ns.iter().any(|n| n["id"].as_str() == Some(id)))
}

fn row_json(r: &AssignRow) -> Value {
    json!({
        "node_id": r.node_id,
        "seat_id": r.seat_id,
        "role": r.role,
        "created": r.created,
        "by": r.by_key,
    })
}

#[derive(Debug, Default, Deserialize)]
pub struct ListQuery {
    node: Option<String>,
    seat: Option<String>,
}

/// `GET /api/v1/assignments?node=&seat=`.
pub async fn list(Query(q): Query<ListQuery>) -> Response {
    let Some(db) = DB.get().cloned() else {
        return unavailable();
    };
    let got =
        tokio::task::spawn_blocking(move || db.list(q.node.as_deref(), q.seat.as_deref())).await;
    match got {
        Ok(Ok(rows)) => Json(json!({"assignments": rows.iter().map(row_json).collect::<Vec<_>>()}))
            .into_response(),
        _ => unavailable(),
    }
}

/// `GET /api/v1/nodes/{id}/assignments`: `{node_id, tree, direct, inherited}`.
pub async fn of_node(UrlPath(id): UrlPath<String>) -> Response {
    let Some(db) = DB.get().cloned() else {
        return unavailable();
    };
    if !node_id_ok(&id) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "node_id",
            "node_id has the wrong shape",
        );
    }
    let got = tokio::task::spawn_blocking(move || {
        let tree = crate::workspace::current_tree();
        let ups = tree.as_ref().map(|t| ancestors(t, &id)).unwrap_or_default();
        let direct = db.list(Some(&id), None)?;
        let mut inherited = Vec::new();
        for up in &ups {
            for r in db.list(Some(up), None)? {
                let mut v = row_json(&r);
                v["from_node"] = json!(up);
                inherited.push(v);
            }
        }
        Ok::<_, aib_store::StoreError>(json!({
            "node_id": id,
            "tree": if tree.is_some() { "file" } else { "none" },
            "direct": direct.iter().map(row_json).collect::<Vec<_>>(),
            "inherited": inherited,
        }))
    })
    .await;
    match got {
        Ok(Ok(v)) => Json(v).into_response(),
        _ => unavailable(),
    }
}

/// act `assign_add` / `assign_remove` from a confirmed page (dispatched by `type`).
pub async fn act(st: AppState, c: Caller, body: Value) -> Response {
    let Some(db) = DB.get().cloned() else {
        return unavailable();
    };
    let (node, seat, role) = match parse(&body) {
        Ok(p) => p,
        Err((code, msg)) => return refuse(StatusCode::BAD_REQUEST, code, msg),
    };
    let adding = body["type"].as_str() == Some(ADD);
    let by = crate::contacts_api::page_key(&c).unwrap_or_else(|| "startup".into());
    let done = tokio::task::spawn_blocking(move || {
        if adding {
            if st.store.seat(&seat).map_err(|_| Fail::Internal)?.is_none() {
                return Err(Fail::NoSeat);
            }
            let tree = crate::workspace::current_tree();
            if tree.is_some_and(|t| !has_node(&t, &node)) {
                return Err(Fail::NoNode);
            }
        }
        let now = crate::sessions::now_rfc3339();
        let row = AssignRow {
            node_id: node.clone(),
            seat_id: seat.clone(),
            role: role.clone(),
            created: now.clone(),
            by_key: by.clone(),
        };
        let changed = if adding {
            match db.add(row.clone()).map_err(|_| Fail::Internal)? {
                Added::New => true,
                Added::Exists => false,
                Added::Full => return Err(Fail::Full),
            }
        } else {
            db.remove(&node, &seat, &role).map_err(|_| Fail::Internal)?
        };
        let journaled = !changed || {
            let kind = if adding {
                "assignment.added"
            } else {
                "assignment.removed"
            };
            let ev = crate::sessions::new_id("ev_").ok().map(|id| NewEvent {
                event_id: id.clone(),
                aggregate_type: "assignment".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: kind.into(),
                correlation_id: None,
                occurred_at: now,
                payload: json!({"node_id": node, "seat_id": seat, "role": role, "by": by}),
            });
            ev.is_some_and(|ev| st.store.append(vec![ev]).is_ok())
        };
        Ok(if adding {
            json!({"assignment": row_json(&row), "added": changed, "journaled": journaled})
        } else {
            json!({"node_id": row.node_id, "seat_id": row.seat_id, "role": row.role,
                   "removed": changed, "journaled": journaled})
        })
    })
    .await;
    match done {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(Fail::NoSeat)) => refuse(StatusCode::NOT_FOUND, "seat_not_found", "no such seat"),
        Ok(Err(Fail::NoNode)) => refuse(StatusCode::NOT_FOUND, "node_not_found", "no such node"),
        Ok(Err(Fail::Full)) => refuse(
            StatusCode::CONFLICT,
            "node_full",
            "this node has as many assignments as it takes",
        ),
        Ok(Err(Fail::Internal)) | Err(_) => unavailable(),
    }
}

enum Fail {
    NoSeat,
    NoNode,
    Full,
    Internal,
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    #[test]
    fn a_seat_wakes_only_when_named_or_for_a_decision_or_a_break() {
        assert!(wakes(true, None));
        assert!(wakes(false, Some(1)));
        assert!(wakes(false, Some(2)));
        for l in 3..=6 {
            assert!(!wakes(false, Some(l)), "reason: woken_for_level_{l}");
        }
        assert!(!wakes(false, None), "reason: woken_unnamed");
    }

    #[test]
    fn an_assignment_names_a_node_a_seat_and_one_of_six_roles() {
        let ok = json!({"type": ADD, "node_id": "N-workspace", "seat_id": "seat_ab12", "role": "reviewer"});
        assert_eq!(
            parse(&ok).unwrap(),
            ("N-workspace".into(), "seat_ab12".into(), "reviewer".into())
        );
        for r in ROLES {
            let mut b = ok.clone();
            b["role"] = json!(r);
            assert!(parse(&b).is_ok(), "{r}");
        }
        let bad = |k: &str, v: Value| {
            let mut b = ok.clone();
            b[k] = v;
            parse(&b).unwrap_err().0
        };
        assert_eq!(bad("role", json!("owner")), "role");
        assert_eq!(bad("role", json!("Author")), "role");
        assert_eq!(bad("seat_id", json!("seat a")), "seat_id");
        assert_eq!(bad("seat_id", json!("x".repeat(65))), "seat_id");
        assert_eq!(bad("seat_id", json!("")), "seat_id");
        assert_eq!(bad("node_id", json!("a\u{7}b")), "node_id");
        assert_eq!(bad("node_id", json!(" N")), "node_id");
        assert_eq!(bad("node_id", json!("節".repeat(129))), "node_id");
        assert!(parse(&{
            let mut b = ok.clone();
            b["node_id"] = json!("節".repeat(128));
            b
        })
        .is_ok());
        assert_eq!(bad("from", json!("x")), "unknown_assign_field");
        assert_eq!(bad("role", json!(3)), "invalid_request");
    }

    #[test]
    fn a_node_inherits_from_every_parent_up_and_a_loop_ends() {
        let t = json!({"nodes": [
            {"id": "V", "parent": null},
            {"id": "G7", "parent": "V"},
            {"id": "N", "parent": "G7"},
            {"id": "T", "parent": "N"},
            {"id": "a", "parent": "b"},
            {"id": "b", "parent": "a"},
            {"id": "s", "parent": "s"},
            {"id": "x", "parent": "a"},
            {"id": "T", "parent": "V"}
        ]});
        assert_eq!(
            ancestors(&t, "T"),
            ["N", "G7", "V"],
            "reason: chain_not_whole"
        );
        assert_eq!(ancestors(&t, "V"), Vec::<String>::new());
        assert_eq!(ancestors(&t, "a"), ["b"], "reason: loop_not_ended");
        assert_eq!(ancestors(&t, "s"), Vec::<String>::new());
        // a loop above the node, not through it, ends too
        assert_eq!(
            ancestors(&t, "x"),
            ["a", "b"],
            "reason: upper_loop_not_ended"
        );
        assert_eq!(ancestors(&t, "nope"), Vec::<String>::new());
        assert!(has_node(&t, "G7") && !has_node(&t, "G8"));
    }
}
