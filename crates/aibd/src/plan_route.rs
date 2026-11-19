//! N-workspace-c4 A (519d0 11-18 20:07; DESIGN-tree-plan-via-aibd-7b81e-1118.md, 5d33a CHECK 0 H/M):
//! once the tree is in aibd, `aib-plan set / note / add` reach it through
//! `POST /api/v1/nodes/plan` instead of writing `cabin-plan/data.json` themselves.
//!
//! aibd applies the op with aib-plan's own writer (`aib_plan::card::write_with`: its lock, its
//! entry conditions, its evidence segment, its event log) on the tree file aibd reads
//! (`AIBD_TREE_SOURCE`), then takes the touched card from the file into its node table. So the
//! file and the table say the same thing and every aib-plan rule holds as it is.
//!
//! - Who: a seat's own token (`AIB_SEAT_TOKEN`; aibd knows the seat, 5d33a L1), or the start-up
//!   token with a self-reported `seat` (as `aib-plan --by` today). A page credential: 403.
//! - The marker `<tree file>.aibd` says "aibd holds the tree": aib-plan sees it and comes here.
//! - L2: aibd remembers the file's mtime and SHA-256 after each of its own writes; a change it
//!   did not make (an old aib-plan that does not know the marker, a hand edit) is logged as
//!   `tree.outside_write` and aibd lets go of the tree for this run (the file is the tree again,
//!   the marker is removed).

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use aib_plan::card::{self, Op, Words};
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::SystemTime;

/// What aib-plan finds next to the tree file while aibd holds it.
pub const MARK_SUFFIX: &str = ".aibd";

/// Let go at run time (L2): the file changed under aibd.
static HELD: AtomicBool = AtomicBool::new(false);
/// The tree file as aibd last wrote or read it: `(path, mtime, sha256)`.
static KNOWN: Mutex<Option<(PathBuf, SystemTime, [u8; 32])>> = Mutex::new(None);

/// aibd let go of the tree during this run.
pub fn held() -> bool {
    HELD.load(Ordering::SeqCst)
}

fn sha_of(path: &Path) -> Option<[u8; 32]> {
    std::fs::read(path).ok().map(|b| Sha256::digest(&b).into())
}

fn mtime_of(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

/// Remember the file as it is now (after aibd's own write, or at the start's import).
pub fn remember(path: &Path) {
    if let (Some(t), Some(h)) = (mtime_of(path), sha_of(path)) {
        if let Ok(mut k) = KNOWN.lock() {
            *k = Some((path.to_path_buf(), t, h));
        }
    }
}

/// The marker path for a tree file.
pub fn mark_path(tree: &Path) -> PathBuf {
    let mut s = tree.as_os_str().to_os_string();
    s.push(MARK_SUFFIX);
    PathBuf::from(s)
}

/// Write the marker (aibd holds the tree): pid, start time and its run folder; no token.
pub fn mark(tree: &Path, run_dir: &Path) {
    let body = json!({
        "pid": std::process::id(),
        "at": crate::sessions::now_rfc3339(),
        "run": run_dir.to_string_lossy(),
    })
    .to_string();
    if let Err(e) = crate::platform::write_private_file(&mark_path(tree), body.as_bytes()) {
        tracing::warn!(error = %e, "tree marker not written");
    }
}

/// Remove the marker (aibd does not hold the tree).
pub fn unmark(tree: &Path) {
    let _ = std::fs::remove_file(mark_path(tree));
}

/// Whether the file is still as aibd left it: same mtime, or a new mtime with the same bytes.
/// `None` when aibd never saw it (nothing to compare: not a change).
pub fn same_as_known(path: &Path) -> Option<bool> {
    let k = KNOWN.lock().ok()?.clone()?;
    if k.0 != path {
        return None;
    }
    if mtime_of(path) == Some(k.1) {
        return Some(true);
    }
    let same = sha_of(path) == Some(k.2);
    if same {
        remember(path);
    }
    Some(same)
}

/// L2: when the file changed under aibd, let go of the tree (once) and say so.
pub fn check_outside(path: &Path, store: Option<&aib_store::Store>) -> bool {
    if same_as_known(path) != Some(false) {
        return true;
    }
    if !HELD.swap(true, Ordering::SeqCst) {
        unmark(path);
        tracing::warn!(
            "the tree file changed outside aibd; the file is the tree again for this run"
        );
        if let Some(store) = store {
            let ev = crate::sessions::new_id("ev_")
                .ok()
                .map(|id| aib_store::NewEvent {
                    event_id: id.clone(),
                    aggregate_type: "tree".into(),
                    aggregate_id: id,
                    sequence: 1,
                    event_type: "tree.outside_write".into(),
                    correlation_id: None,
                    occurred_at: crate::sessions::now_rfc3339(),
                    payload: json!({"let_go": true}),
                });
            if let Some(ev) = ev {
                let _ = store.append(vec![ev]);
            }
        }
    }
    false
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

fn text(body: &Value, k: &str) -> Result<Option<String>, String> {
    match &body[k] {
        Value::Null => Ok(None),
        Value::String(s) if s.chars().count() <= 4000 => Ok(Some(s.clone())),
        _ => Err(format!("{k} must be text of at most 4000 characters")),
    }
}

/// A list of texts (SPEC-plan-fields `touches`): absent / null = not given.
fn texts(body: &Value, k: &str) -> Result<Option<Vec<String>>, String> {
    match &body[k] {
        Value::Null => Ok(None),
        Value::Array(a) if a.len() <= 200 => a
            .iter()
            .map(|x| match x {
                Value::String(s) if s.chars().count() <= 4000 => Ok(s.clone()),
                _ => Err(format!("{k} must be a list of texts")),
            })
            .collect::<Result<Vec<_>, _>>()
            .map(Some),
        _ => Err(format!("{k} must be a list of at most 200 texts")),
    }
}

/// The op in the body: aib-plan's own fields, nothing else.
pub fn op_of(body: &Value) -> Result<Op, String> {
    let o = body.as_object().ok_or("the body is a JSON object")?;
    let kind = o.get("op").and_then(Value::as_str).unwrap_or("");
    let allowed: &[&str] = match kind {
        "set" => &[
            "op", "seat", "id", "status", "fact", "head", "wait", "reviewer", "need", "touches",
            "branch", "verifier",
        ],
        "note" => &["op", "seat", "id", "fact"],
        "add" => &[
            "op", "seat", "parent", "id", "title", "owner", "why", "reviewer", "need",
        ],
        // 5d33a PREREAD f2021ac9 L1: say what is missing, not just "unknown op"
        "fail" | "migrate" => {
            return Err(format!(
                "no_aibd_route: aib-plan {kind} has no aibd route yet; while aibd holds the tree \
                 nothing is written (set / note / add go through aibd)"
            ))
        }
        _ => return Err("op is set, note or add".into()),
    };
    if let Some(k) = o.keys().find(|k| !allowed.contains(&k.as_str())) {
        return Err(format!("unknown_field: {k}"));
    }
    let need = |k: &str| text(body, k)?.ok_or(format!("{k} is required"));
    Ok(match kind {
        "set" => Op::Set {
            id: need("id")?,
            status: text(body, "status")?,
            fact: need("fact")?,
            head: text(body, "head")?,
            wait: text(body, "wait")?,
            reviewer: text(body, "reviewer")?,
            need: text(body, "need")?,
            fields: card::Fields {
                touches: texts(body, "touches")?,
                branch: text(body, "branch")?,
                verifier: text(body, "verifier")?,
            },
        },
        "note" => Op::Note {
            id: need("id")?,
            fact: need("fact")?,
        },
        _ => Op::Add {
            parent: need("parent")?,
            id: text(body, "id")?,
            title: need("title")?,
            owner: need("owner")?,
            why: text(body, "why")?,
            reviewer: text(body, "reviewer")?,
            need: text(body, "need")?,
        },
    })
}

fn is_seat_code(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Who writes: the seat its token names (L1), or the start-up token's self-reported seat.
/// `(seat, self_reported)`; a page or anyone else: `Err` (403).
/// A refusal: status, code, words.
pub type Refusal = (StatusCode, &'static str, String);

pub fn writer(c: &Caller, body: &Value) -> Result<(String, bool), Refusal> {
    match c {
        Caller::Seat(key) => {
            if body.get("seat").is_some() {
                return Err((
                    StatusCode::BAD_REQUEST,
                    "unknown_field",
                    "a seat's token names the seat; leave seat out".into(),
                ));
            }
            crate::seat_token::tokens()
                .seat(key)
                .map(|s| (s, false))
                .ok_or((
                    StatusCode::FORBIDDEN,
                    "seat_unknown",
                    "this seat token is not known".into(),
                ))
        }
        Caller::StartupToken => match body.get("seat").and_then(Value::as_str) {
            Some(s) if is_seat_code(s) => Ok((s.to_owned(), true)),
            _ => Err((
                StatusCode::BAD_REQUEST,
                "bad_value",
                "seat is a 5-character seat code".into(),
            )),
        },
        _ => Err((
            StatusCode::FORBIDDEN,
            "plan_route_seat_or_startup_only",
            "aib-plan writes come with a seat's token or the start-up token".into(),
        )),
    }
}

/// aib-plan's error as an answer: its code, and its words unless they can name a path.
fn plan_error(e: &str) -> Response {
    let code: String = e
        .split([':', '：'])
        .next()
        .unwrap_or("")
        .chars()
        .take(40)
        .collect();
    let with_words = matches!(
        code.as_str(),
        "entry" | "bad_value" | "missing" | "unknown_id" | "bad_json"
    );
    let status = if code == "locked" {
        StatusCode::CONFLICT
    } else if with_words {
        StatusCode::BAD_REQUEST
    } else {
        StatusCode::INTERNAL_SERVER_ERROR
    };
    let msg: String = if with_words {
        e.chars().take(500).collect()
    } else {
        code.clone()
    };
    refuse(status, &code, &msg)
}

/// Take card `id` from the file into the table (update in place, or add at the end).
fn take_card(db: &aib_store::WorkspaceDb, tree: &Value, id: &str, now: &str) -> bool {
    let (rows, _) = crate::workspace::import_rows(tree, now);
    let Some(mut row) = rows.into_iter().find(|r| r.node_id == id) else {
        return false;
    };
    let Ok(have) = db.nodes() else { return false };
    match have.iter().find(|r| r.node_id == id) {
        Some(old) => {
            row.ord = old.ord;
            matches!(db.set_node(row, old.rev), Ok(aib_store::NodeSet::Done(_)))
        }
        None => {
            row.ord = i64::try_from(have.len()).unwrap_or(i64::MAX);
            db.add_node(row).unwrap_or(false)
        }
    }
}

/// `POST /api/v1/nodes/plan`
pub async fn plan(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Json(body): Json<Value>,
) -> Response {
    let (seat, self_reported) = match writer(&c, &body) {
        Ok(w) => w,
        Err((s, code, m)) => return refuse(s, code, &m),
    };
    let op = match op_of(&body) {
        Ok(o) => o,
        Err(m) => {
            let code = if m.starts_with("unknown_field") {
                "unknown_field"
            } else if m.starts_with("no_aibd_route") {
                "no_aibd_route"
            } else {
                "invalid_request"
            };
            return refuse(StatusCode::BAD_REQUEST, code, &m);
        }
    };
    let (Some(db), Some(path)) = (crate::assign::db(), crate::workspace::tree_path()) else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "tree_unavailable",
            "the tree is not open here",
        );
    };
    let done = tokio::task::spawn_blocking(move || {
        if !crate::workspace::tree_is_in_aibd() || !check_outside(&path, Some(&*st.store)) {
            // boxed: a Response is large (clippy::result_large_err)
            return Err(Box::new(refuse(
                StatusCode::CONFLICT,
                "tree_not_in_aibd",
                "aibd does not hold the tree now; aib-plan writes the file",
            )));
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
        let facts = crate::nodes::facts();
        let a = card::write_with(&path, &op, &seat, now, &facts, &Words::default())
            .map_err(|e| Box::new(plan_error(&e)))?;
        remember(&path);
        let tree: Value = std::fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or(Value::Null);
        let taken = take_card(&db, &tree, &a.id, &crate::sessions::now_rfc3339());
        let kind = match op {
            Op::Set { .. } => "node.set",
            Op::Note { .. } => "node.noted",
            Op::Add { .. } => "node.added",
            // op_of builds set / note / add only
            Op::Fail { .. } | Op::Migrate { .. } => "node.set",
        };
        let ev = crate::sessions::new_id("ev_")
            .ok()
            .map(|id| aib_store::NewEvent {
                event_id: id.clone(),
                aggregate_type: "node".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: kind.into(),
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload: json!({"node_id": a.id, "from": a.from, "to": a.to, "by": seat,
                            "via": "aib-plan", "seat_self_reported": self_reported}),
            });
        let journaled = ev.is_some_and(|ev| st.store.append(vec![ev]).is_ok());
        Ok(
            json!({"id": a.id, "from": a.from, "to": a.to, "fact": a.fact,
                  "in_table": taken, "journaled": journaled}),
        )
    })
    .await;
    match done {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "not written"),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// main's SPEC-plan-fields (touches / branch / verifier) reach aib-plan's writer through
    /// aibd as they do through the file; a touches that is not a list of texts is refused.
    #[test]
    fn a_set_carries_the_plan_fields_through_aibd() {
        let b = json!({"op": "set", "id": "N", "fact": "x", "touches": ["crates/aibd/src"],
            "branch": "feat/x", "verifier": "5d33a"});
        let Op::Set { fields, .. } = op_of(&b).unwrap() else {
            panic!("reason: not_a_set");
        };
        assert_eq!(fields.touches, Some(vec!["crates/aibd/src".to_owned()]));
        assert_eq!(fields.branch.as_deref(), Some("feat/x"));
        assert_eq!(fields.verifier.as_deref(), Some("5d33a"));
        let none = json!({"op": "set", "id": "N", "fact": "x"});
        let Op::Set { fields, .. } = op_of(&none).unwrap() else {
            panic!("reason: not_a_set");
        };
        assert_eq!(
            fields,
            card::Fields::default(),
            "reason: absent_is_not_given"
        );
        let bad = json!({"op": "set", "id": "N", "fact": "x", "touches": "crates"});
        assert!(op_of(&bad).is_err(), "reason: touches_not_a_list");
        // aib-plan's fail / migrate have no route here yet: refused, nothing written
        for op in ["fail", "migrate"] {
            let e = op_of(&json!({"op": op})).unwrap_err();
            assert!(
                e.starts_with("no_aibd_route"),
                "reason: {op}_not_told_missing_route"
            );
            assert!(e.contains(op), "reason: {op}_not_named");
        }
    }

    #[test]
    fn an_op_takes_only_aib_plans_own_fields() {
        let ok = json!({"op": "set", "seat": "7b81e", "id": "N", "fact": "x", "status": "開發中"});
        assert!(matches!(op_of(&ok).unwrap(), Op::Set { .. }));
        let note = json!({"op": "note", "id": "N", "fact": "x"});
        assert!(matches!(op_of(&note).unwrap(), Op::Note { .. }));
        let add = json!({"op": "add", "parent": "N", "title": "t", "owner": "7b81e"});
        assert!(matches!(op_of(&add).unwrap(), Op::Add { .. }));
        for bad in [
            json!({"op": "set", "id": "N", "fact": "x", "assign": "y"}),
            json!({"op": "note", "id": "N", "fact": "x", "status": "開發中"}),
            json!({"op": "drop", "id": "N"}),
            json!({"op": "set", "id": "N"}),
        ] {
            assert!(op_of(&bad).is_err(), "reason: field_taken {bad}");
        }
    }

    #[test]
    fn a_page_cannot_write_through_aib_plans_route() {
        let body = json!({"op": "note", "id": "N", "fact": "x", "seat": "7b81e"});
        assert_eq!(
            writer(&Caller::StartupToken, &body).unwrap(),
            ("7b81e".into(), true)
        );
        assert!(writer(&Caller::StartupToken, &json!({"seat": "not-a-seat"})).is_err());
        let e = writer(&Caller::Anonymous, &body).unwrap_err();
        assert_eq!(e.0, StatusCode::FORBIDDEN, "reason: anonymous_writes");
    }

    /// L2: a change aibd did not make is seen (mtime moved and bytes differ); the same bytes
    /// written again are not a change.
    #[test]
    fn a_write_aibd_did_not_make_is_seen() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("data.json");
        std::fs::write(&p, "{\"nodes\":[]}").unwrap();
        remember(&p);
        assert_eq!(same_as_known(&p), Some(true));
        let later = SystemTime::now() + std::time::Duration::from_secs(5);
        std::fs::write(&p, "{\"nodes\":[]}").unwrap();
        let f = std::fs::File::options().write(true).open(&p).unwrap();
        f.set_modified(later).unwrap();
        assert_eq!(
            same_as_known(&p),
            Some(true),
            "same bytes, new mtime: not a change"
        );
        std::fs::write(&p, "{\"nodes\":[{\"id\":\"X\"}]}").unwrap();
        f.set_modified(later + std::time::Duration::from_secs(5))
            .unwrap();
        assert_eq!(
            same_as_known(&p),
            Some(false),
            "reason: outside_write_unseen"
        );
        let m = mark_path(&p);
        assert!(m.to_string_lossy().ends_with("data.json.aibd"));
    }
}
