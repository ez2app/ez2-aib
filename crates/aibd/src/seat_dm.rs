//! G3 (PREREAD-seat-g1write-g3-a9136-1121 r2, 5d33a 0/0/0/3; DECISION-council-cutover-519d0-1121):
//! a web seat that moved from a terminal seat reads and closes the letters still in the 1.x
//! mail ledger under its old seat code, through aibd and never by running `dm_queue.py`
//! (5d33a M1: aibd does not run a script an AI can write).
//!
//! - The old code comes only from aibd's own record of the move ([`moved_code`]), never from
//!   the request.
//! - Reading takes no lock (5d33a 題 1: a letter is replaced by rename, never half written).
//! - Closing is `aib_dmq_store::recv::close` (the one Rust writer G5 shares: the seat folder
//!   held open, flock, rev, audit). Evidence is narrowed before it, to what aibd can check
//!   without git (5d33a 題 2 / 知會): a listed file in the pending folder, the seat's own reply
//!   id, a PR URL. Anything else goes in `why`.

use crate::api::AppState;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

pub const WAITING: &str = "dm_waiting";
pub const SHOW: &str = "dm_show";
pub const DONE: &str = "dm_done";
/// The states a letter waits in (`dm_queue.py list --tab waiting`).
pub const WAIT_STATUSES: [&str; 5] = ["queued", "claimed", "hold_human", "delivered", "read"];
/// Letters listed at most.
pub const WAIT_MAX: usize = 100;
/// Characters of a letter's first line in the list.
pub const FIRST_MAX: usize = 120;
/// Bytes of a letter, or of its long-message file, shown at most.
pub const SHOW_MAX: usize = 64 * 1024;
/// Letters one `dm_done` closes at most (PREREAD-seat-inbox-summary r3).
pub const DONE_MAX: usize = 20;
/// Characters of `why` or `evidence` at most.
pub const NOTE_MAX: usize = 500;

/// Whether a body is one of these acts.
pub fn is_seat_act(body: &Value) -> bool {
    matches!(
        body.get("type").and_then(Value::as_str),
        Some(WAITING | SHOW | DONE)
    )
}

/// `q_` and 16 lowercase hex digits.
pub fn id_ok(id: &str) -> bool {
    aib_dmq::evidence::is_item_id(id)
}

/// The fields each act takes; a seat code is never one of them (G3f).
pub fn fields_ok(body: &Value) -> bool {
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    body.as_object().is_some_and(|o| {
        o.keys().all(|k| match k.as_str() {
            "type" => true,
            "id" => kind == SHOW || kind == DONE,
            "rev" | "evidence" | "why" | "items" => kind == DONE,
            _ => false,
        })
    })
}

fn first_line(msg: &str) -> String {
    crate::docs::mask_mail(msg.lines().next().unwrap_or(""))
        .chars()
        .take(FIRST_MAX)
        .collect()
}

fn cut(text: &str, max: usize) -> String {
    if text.len() <= max {
        return text.to_owned();
    }
    let mut end = max;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text[..end].to_owned()
}

fn str_of<'a>(item: &'a Map<String, Value>, k: &str) -> &'a str {
    item.get(k).and_then(Value::as_str).unwrap_or("")
}

fn read(store: &aib_dmq_store::Store, code: &str, id: &str) -> Option<Map<String, Value>> {
    match store.read_item(code, id) {
        Ok(aib_dmq_store::store::ItemRead::Item { item, .. }) => Some(item),
        _ => None,
    }
}

/// A letter's grade (PREREAD-seat-inbox-summary-a9136-1121 r3, 5d33a 0/0/0/2): 0 急, 1 一般,
/// 2 通知, and whether the ledger claimed urgent without aibd's word for it. 急 only when the
/// letter came through the bridge (`aibd_message_id`) and aibd's own record has it urgent
/// ([`aibd_urgent`]); a ledger `urgent` alone is 一般 (5d33a M1: the ledger file is
/// AI-writable). `reminder` is 通知. The text is never read for it.
pub fn grade_of(item: &Map<String, Value>, urgent: &dyn Fn(&str) -> bool) -> (u8, bool) {
    let p = str_of(item, "priority").to_ascii_lowercase();
    let mid = str_of(item, "aibd_message_id");
    if !mid.is_empty() && urgent(mid) {
        return (0, false);
    }
    if p == "reminder" {
        return (2, false);
    }
    (1, p == "urgent")
}

const GRADES: [&str; 3] = ["急", "一般", "通知"];

/// The waiting letters for `code` as a to-do summary: 急 → 一般 → 通知, oldest first within a
/// grade, one line each, with the counts on top.
pub fn waiting(
    store: &aib_dmq_store::Store,
    code: &str,
    now: i64,
    urgent: &dyn Fn(&str) -> bool,
) -> Value {
    let Ok(dir) = store.seat_dir(code) else {
        return json!({"counts": {"urgent": 0, "normal": 0, "notice": 0, "oldest_min": 0}, "letters": [], "more": 0});
    };
    let ids: Vec<String> = std::fs::read_dir(&dir)
        .map(|rd| {
            rd.filter_map(Result::ok)
                .filter_map(|e| e.file_name().to_str().map(str::to_owned))
                .filter_map(|n| n.strip_suffix(".json").map(str::to_owned))
                .filter(|n| id_ok(n))
                .collect()
        })
        .unwrap_or_default();
    let mut rows: Vec<(u8, i64, Value)> = ids
        .iter()
        .filter_map(|id| {
            let item = read(store, code, id)?;
            let status = str_of(&item, "status");
            if !WAIT_STATUSES.contains(&status) {
                return None;
            }
            let ts = item.get("ts").and_then(Value::as_i64).unwrap_or(0);
            let (g, claimed) = grade_of(&item, urgent);
            let row = json!({
                "id": id,
                "rev": aib_dmq::rev::item_rev(&Value::Object(item.clone())),
                "grade": GRADES[usize::from(g)],
                "claimed_urgent": claimed,
                "status": status,
                "age_min": (now - ts).max(0) / 60,
                "sender": str_of(&item, "sender_seat"),
                "sender_label": str_of(&item, "sender_label"),
                "first_line": first_line(str_of(&item, "msg")),
            });
            Some((g, ts, row))
        })
        .collect();
    rows.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    let count = |g: u8| rows.iter().filter(|r| r.0 == g).count();
    let oldest = rows
        .iter()
        .map(|r| r.1)
        .min()
        .map_or(0, |t| (now - t).max(0) / 60);
    let counts =
        json!({"urgent": count(0), "normal": count(1), "notice": count(2), "oldest_min": oldest});
    let more = rows.len().saturating_sub(WAIT_MAX);
    rows.truncate(WAIT_MAX);
    json!({"counts": counts, "letters": rows.into_iter().map(|r| r.2).collect::<Vec<_>>(), "more": more})
}

/// Whether aibd's own record has the letter `aibd_message_id` urgent, marked by Andy, 519d0 or
/// the system (7ebca's level column; 5d33a: a seat cannot mark urgent in the first stage).
/// False until that column exists.
pub fn aibd_urgent(_db: &aib_store::MessageDb, _aibd_message_id: &str) -> bool {
    false
}

/// Close several letters with one evidence (PREREAD-seat-inbox-summary r3, 5d33a 題 1): each at
/// its own rev, each its own audit line; one failing leaves the others. 200 with each result
/// unless all fail (then the first failure's status).
#[allow(clippy::too_many_arguments)]
pub fn done_many(
    store: &aib_dmq_store::Store,
    relay_dirs: &[PathBuf],
    pending: Option<&Path>,
    code: &str,
    items: &[(String, i64)],
    evidence: Option<&str>,
    why: Option<&str>,
    now: i64,
) -> (StatusCode, Value) {
    if items.is_empty() || items.len() > DONE_MAX {
        return (
            StatusCode::BAD_REQUEST,
            json!({"code": "dm_items", "message": "1 to 20 letters at a time"}),
        );
    }
    let mut first_fail = None;
    let mut out = Vec::new();
    for (id, rev) in items {
        let (st, v) = done(
            store, relay_dirs, pending, code, id, *rev, evidence, why, now,
        );
        let ok = st == StatusCode::OK;
        if !ok && first_fail.is_none() {
            first_fail = Some(st);
        }
        out.push(json!({"id": id, "ok": ok, "code": v.get("code").or(v.get("reason")).cloned().unwrap_or(Value::Null)}));
    }
    let any_ok = out.iter().any(|r| r["ok"] == json!(true));
    let status = if any_ok {
        StatusCode::OK
    } else {
        first_fail.unwrap_or(StatusCode::CONFLICT)
    };
    (status, json!({"results": out}))
}

/// One letter in full; a long message's file is added behind the relay wall.
pub fn show(
    store: &aib_dmq_store::Store,
    code: &str,
    id: &str,
    relay_dirs: &[PathBuf],
) -> Result<Value, &'static str> {
    if !id_ok(id) {
        return Err("dm_id");
    }
    let item = read(store, code, id).ok_or("dm_not_found")?;
    if str_of(&item, "status") == "deleted" {
        return Err("dm_not_found");
    }
    let msg = str_of(&item, "msg");
    let relay = aib_dmq_store::recv::relay_path_of(msg)
        .map(Path::new)
        .filter(|p| aib_dmq_store::recv::relay_file_ok(p, relay_dirs))
        .and_then(relay_text);
    Ok(json!({
        "id": id,
        "rev": aib_dmq::rev::item_rev(&Value::Object(item.clone())),
        "status": str_of(&item, "status"),
        "sender_seat": str_of(&item, "sender_seat"),
        "sender_label": str_of(&item, "sender_label"),
        "msg": cut(&crate::docs::mask_mail(msg), SHOW_MAX),
        "relay_text": relay.map(|t| format!("{}\n---\n{}", crate::seat_pending::RELAY_NOTE, t)),
    }))
}

/// A long-message file: a plain file (not a link), at most [`SHOW_MAX`], mail masked.
fn relay_text(p: &Path) -> Option<String> {
    use std::io::Read;
    if !std::fs::symlink_metadata(p).ok()?.file_type().is_file() {
        return None;
    }
    let mut bytes = Vec::new();
    std::fs::File::open(p)
        .ok()?
        .take(SHOW_MAX as u64)
        .read_to_end(&mut bytes)
        .ok()?;
    Some(crate::docs::mask_mail(&String::from_utf8_lossy(&bytes)))
}

/// A short note: trimmed, at most [`NOTE_MAX`] characters, no control characters.
fn note(text: Option<&str>) -> Result<Option<String>, &'static str> {
    let Some(t) = text.map(str::trim).filter(|t| !t.is_empty()) else {
        return Ok(None);
    };
    if t.chars().count() > NOTE_MAX || t.chars().any(char::is_control) {
        return Err("dm_note");
    }
    Ok(Some(t.to_owned()))
}

/// The evidence handed to the ledger: a reply id, a PR URL, or the full path of a listed file
/// in the pending folder. Nothing else (a commit would make the ledger run git).
pub fn evidence_for(text: &str, pending: Option<&Path>) -> Result<String, &'static str> {
    if aib_dmq::evidence::is_item_id(text) || aib_dmq::evidence::is_pr_url(text) {
        return Ok(text.to_owned());
    }
    let one_piece = !text.contains(['/', '\\']) && !text.starts_with('.');
    if one_piece && crate::seat_pending::seat_listed(text) {
        if let Some(p) = pending.map(|r| r.join(text)) {
            if std::fs::symlink_metadata(&p).is_ok_and(|m| m.file_type().is_file()) {
                return Ok(p.to_string_lossy().into_owned());
            }
        }
    }
    Err("dm_evidence_unverifiable")
}

/// Close one letter for `code` at `rev`: (HTTP status, answer).
#[allow(clippy::too_many_arguments)]
pub fn done(
    store: &aib_dmq_store::Store,
    relay_dirs: &[PathBuf],
    pending: Option<&Path>,
    code: &str,
    id: &str,
    rev: i64,
    evidence: Option<&str>,
    why: Option<&str>,
    now: i64,
) -> (StatusCode, Value) {
    let bad = |c: &str, m: &str| (StatusCode::BAD_REQUEST, json!({"code": c, "message": m}));
    if !id_ok(id) {
        return bad("dm_id", "id is q_ and 16 lowercase hex digits");
    }
    let (Ok(evidence), Ok(why)) = (note(evidence), note(why)) else {
        return bad(
            "dm_note",
            "evidence and why: at most 500 characters, one line",
        );
    };
    let evidence = match evidence.map(|e| evidence_for(&e, pending)).transpose() {
        Ok(e) => e,
        Err(c) => {
            return bad(
                c,
                "evidence is a pending file name, your own reply id or a PR URL; put anything else (a commit too) in why",
            )
        }
    };
    if evidence.is_none() && why.is_none() {
        return bad("dm_evidence", "evidence or why is needed");
    }
    let Some(item) = read(store, code, id) else {
        return (
            StatusCode::NOT_FOUND,
            json!({"code": "dm_not_found", "message": "no such letter"}),
        );
    };
    let cur = aib_dmq::rev::item_rev(&Value::Object(item));
    if cur != rev {
        return (
            StatusCode::CONFLICT,
            json!({"code": "rev_mismatch", "rev": cur, "message": "the letter changed; read it again"}),
        );
    }
    let caller = aib_dmq_store::store::Caller::default();
    let ctx = aib_dmq_store::recv::Ctx {
        caller: &caller,
        caller_is_seat: true,
        relay_dirs,
        now,
    };
    let actor = format!("seat:{code}");
    match aib_dmq_store::recv::close(
        store,
        &ctx,
        code,
        id,
        &actor,
        evidence.as_deref(),
        why.as_deref(),
    ) {
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"code": "internal", "message": format!("{e:?}")}),
        ),
        Ok(a) => {
            let reason = a.out.get("reason").and_then(Value::as_str).unwrap_or("");
            let status = match (a.out.get("ok").and_then(Value::as_bool), reason) {
                (Some(true), _) => StatusCode::OK,
                (_, "lock_timeout") => StatusCode::SERVICE_UNAVAILABLE,
                (_, "not_found") => StatusCode::NOT_FOUND,
                (_, "evidence") => StatusCode::BAD_REQUEST,
                _ => StatusCode::CONFLICT,
            };
            (status, a.out)
        }
    }
}

/// The old seat code aibd recorded when this web seat took a terminal seat's place (7ebca G5
/// `seat_moves`, a live move only; written only by the fingerprint act). None: refused.
pub fn moved_code(db: &aib_store::MessageDb, seat_id: &str) -> Option<String> {
    db.moved_code(seat_id)
        .ok()
        .flatten()
        .filter(|c| aib_dmq_store::store::seat_ok(c))
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"code": code, "message": message, "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

/// The act, for the seat a token names.
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    if !fields_ok(&body) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "dm_waiting takes nothing, dm_show takes id, dm_done takes id, rev, evidence, why",
        );
    }
    let (store, sid) = (st.store.clone(), seat_id.to_owned());
    let mode = tokio::task::spawn_blocking(move || {
        store.seat_meta(&sid).ok().flatten().and_then(|m| m.mode)
    })
    .await
    .ok()
    .flatten();
    if !crate::seat_pending::mode_may_read(mode.as_deref()) {
        return refuse(
            StatusCode::FORBIDDEN,
            "dm_not_for_this_seat",
            "a safe seat does not read the mail ledger",
        );
    }
    let (db, sid) = (st.messages.clone(), seat_id.to_owned());
    let code = tokio::task::spawn_blocking(move || moved_code(&db, &sid))
        .await
        .ok()
        .flatten();
    let Some(code) = code else {
        return refuse(
            StatusCode::FORBIDDEN,
            "dm_not_moved",
            "this seat did not take a terminal seat's place; it has no 1.x mail",
        );
    };
    let cfg = crate::bridge_cfg::now();
    let Some(state_root) = crate::bridge_cfg::queue_root(&cfg, |k| std::env::var(k).ok())
        .and_then(|q| q.parent().and_then(Path::parent).map(Path::to_path_buf))
    else {
        return refuse(StatusCode::NOT_FOUND, "legacy_off", "no mail ledger");
    };
    let relay_dirs = aib_dmq_store::recv::relay_dirs(|k| std::env::var(k).ok());
    let pending = crate::docs::root();
    let kind = body
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let id = body
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let rev = body.get("rev").and_then(Value::as_i64);
    let items: Option<Vec<(String, i64)>> = body.get("items").map(|v| {
        v.as_array()
            .map(|a| {
                a.iter()
                    .map(|i| {
                        (
                            i["id"].as_str().unwrap_or_default().to_owned(),
                            i["rev"].as_i64().unwrap_or(-1),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default()
    });
    let db = st.messages.clone();
    let evidence = body
        .get("evidence")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let why = body.get("why").and_then(Value::as_str).map(str::to_owned);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    let got = tokio::task::spawn_blocking(move || {
        let store = aib_dmq_store::Store::new(&state_root, &state_root).without_git();
        match kind.as_str() {
            WAITING => (
                StatusCode::OK,
                waiting(&store, &code, now, &|mid| aibd_urgent(&db, mid)),
            ),
            SHOW => match show(&store, &code, &id, &relay_dirs) {
                Ok(v) => (StatusCode::OK, v),
                Err(c) => (StatusCode::NOT_FOUND, json!({"code": c})),
            },
            _ => match (items, rev) {
                (Some(items), _) => done_many(
                    &store,
                    &relay_dirs,
                    pending.as_deref(),
                    &code,
                    &items,
                    evidence.as_deref(),
                    why.as_deref(),
                    now,
                ),
                (None, None) => (
                    StatusCode::BAD_REQUEST,
                    json!({"code": "dm_rev", "message": "rev is needed"}),
                ),
                (None, Some(rev)) => done(
                    &store,
                    &relay_dirs,
                    pending.as_deref(),
                    &code,
                    &id,
                    rev,
                    evidence.as_deref(),
                    why.as_deref(),
                    now,
                ),
            },
        }
    })
    .await;
    match got {
        Ok((status, v)) => (status, Json(v)).into_response(),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "dm act failed",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CODE: &str = "a9136";

    fn ledger() -> (tempfile::TempDir, aib_dmq_store::Store) {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("state").join("dm-queue").join(CODE);
        std::fs::create_dir_all(&dir).unwrap();
        let store = aib_dmq_store::Store::new(d.path(), d.path()).without_git();
        (d, store)
    }

    fn put(store: &aib_dmq_store::Store, id: &str, status: &str, msg: &str, ts: i64) {
        let mut item = crate::legacy_send::letter(id, CODE, msg, "519d0", "seat:519d0", "", ts);
        item.insert("status".into(), json!(status));
        store.write_item(CODE, &item).unwrap();
        store.order_append(CODE, id).unwrap();
    }

    fn status(store: &aib_dmq_store::Store, id: &str) -> String {
        str_of(&read(store, CODE, id).unwrap(), "status").to_owned()
    }

    fn put_p(store: &aib_dmq_store::Store, id: &str, prio: &str, mid: &str, ts: i64) {
        let mut item = crate::legacy_send::letter(id, CODE, "x", "519d0", "seat:519d0", "", ts);
        item.insert("status".into(), json!("delivered"));
        item.insert("priority".into(), json!(prio));
        if !mid.is_empty() {
            item.insert("aibd_message_id".into(), json!(mid));
        }
        store.write_item(CODE, &item).unwrap();
        store.order_append(CODE, id).unwrap();
    }

    /// G3 read / IB1: the waiting letters only, 急 → 一般 → 通知 and oldest first, first line
    /// masked and short, counts on top.
    #[test]
    fn the_waiting_letters_are_listed_in_order() {
        let (_d, store) = ledger();
        put(
            &store,
            "q_00000000000000a1",
            "delivered",
            "寄 x@y.com 看\n第二行",
            10,
        );
        put(&store, "q_00000000000000a2", "done", "結了", 11);
        put(&store, "q_00000000000000a3", "read", &"長".repeat(400), 12);
        put_p(&store, "q_00000000000000a4", "reminder", "", 5);
        put_p(
            &store,
            "q_00000000000000a5",
            "normal",
            "q_aaaaaaaaaaaaaaaa",
            20,
        );
        let urgent = |mid: &str| mid == "q_aaaaaaaaaaaaaaaa";
        let v = waiting(&store, CODE, 600, &urgent);
        let ids: Vec<&str> = v["letters"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["id"].as_str().unwrap())
            .collect();
        assert_eq!(
            ids,
            [
                "q_00000000000000a5",
                "q_00000000000000a1",
                "q_00000000000000a3",
                "q_00000000000000a4"
            ],
            "reason: grade_order"
        );
        let rows = v["letters"].as_array().unwrap();
        let first = rows[1]["first_line"].as_str().unwrap();
        assert!(
            !first.contains("x@y.com") && !first.contains("第二行"),
            "{first}"
        );
        assert_eq!(
            rows[2]["first_line"].as_str().unwrap().chars().count(),
            FIRST_MAX
        );
        assert_eq!(
            v["counts"],
            json!({"urgent": 1, "normal": 2, "notice": 1, "oldest_min": 9})
        );
        assert_eq!(rows[1]["age_min"], json!(9));
    }

    /// IB2 / IB2b / IB6 (5d33a M1): urgent only by aibd's own record; a ledger `urgent`, or one
    /// with a bridge id aibd does not have urgent, or a text saying 急, is 一般.
    #[test]
    fn only_aibd_makes_a_letter_urgent() {
        let (_d, store) = ledger();
        put_p(&store, "q_00000000000000e1", "urgent", "", 10);
        put_p(
            &store,
            "q_00000000000000e2",
            "URGENT",
            "q_bbbbbbbbbbbbbbbb",
            11,
        );
        put(
            &store,
            "q_00000000000000e3",
            "delivered",
            "急!急件!urgent",
            12,
        );
        let urgent = |mid: &str| mid == "q_aaaaaaaaaaaaaaaa";
        let v = waiting(&store, CODE, 100, &urgent);
        for r in v["letters"].as_array().unwrap() {
            assert_eq!(
                r["grade"],
                json!("一般"),
                "reason: claimed_urgent_trusted {}",
                r["id"]
            );
        }
        let rows = v["letters"].as_array().unwrap();
        assert_eq!(rows[0]["claimed_urgent"], json!(true));
        assert_eq!(rows[1]["claimed_urgent"], json!(true));
        assert_eq!(rows[2]["claimed_urgent"], json!(false));
        assert_eq!(v["counts"]["urgent"], json!(0));
    }

    /// IB3 / IB4 / IB5 (5d33a 題 1): several at once, each at its own rev; one failing leaves
    /// the others; at most 20.
    #[test]
    fn several_letters_close_at_once_each_on_its_own() {
        let (d, store) = ledger();
        let pending = d.path().join("pending");
        std::fs::create_dir(&pending).unwrap();
        let rev_of =
            |id: &str| aib_dmq::rev::item_rev(&Value::Object(read(&store, CODE, id).unwrap()));
        for (i, id) in [
            "q_00000000000000f1",
            "q_00000000000000f2",
            "q_00000000000000f3",
        ]
        .iter()
        .enumerate()
        {
            put(&store, id, "read", "知會", 10 + i as i64);
        }
        let items = vec![
            (
                "q_00000000000000f1".to_owned(),
                rev_of("q_00000000000000f1"),
            ),
            (
                "q_00000000000000f2".to_owned(),
                rev_of("q_00000000000000f2") + 5,
            ),
            (
                "q_00000000000000f3".to_owned(),
                rev_of("q_00000000000000f3"),
            ),
        ];
        let (st, v) = done_many(
            &store,
            &[],
            Some(&pending),
            CODE,
            &items,
            None,
            Some("知會"),
            30,
        );
        assert_eq!(st, StatusCode::OK, "{v:?}");
        let ok: Vec<bool> = v["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["ok"] == json!(true))
            .collect();
        assert_eq!(ok, [true, false, true], "reason: one_fail_stops_all");
        assert_eq!(status(&store, "q_00000000000000f1"), "done");
        assert_eq!(
            status(&store, "q_00000000000000f2"),
            "read",
            "reason: rev_not_each"
        );
        assert_eq!(status(&store, "q_00000000000000f3"), "done");
        let many: Vec<(String, i64)> = (0..21).map(|i| (format!("q_{i:016x}"), 1)).collect();
        let (st, _) = done_many(
            &store,
            &[],
            Some(&pending),
            CODE,
            &many,
            None,
            Some("x"),
            30,
        );
        assert_eq!(st, StatusCode::BAD_REQUEST, "reason: no_batch_cap");
        let (st, _) = done_many(
            &store,
            &[],
            Some(&pending),
            CODE,
            &items[1..2],
            None,
            Some("x"),
            30,
        );
        assert_eq!(
            st,
            StatusCode::CONFLICT,
            "all failed: the first failure's status"
        );
    }

    /// G3a / G3b / G3e: a bad id, an old rev and evidence aibd cannot check without git are
    /// refused before the ledger is touched.
    #[test]
    fn a_letter_is_closed_only_at_its_rev_with_checkable_evidence() {
        let (d, store) = ledger();
        let pending = d.path().join("pending");
        std::fs::create_dir(&pending).unwrap();
        std::fs::write(pending.join("REVIEW-x-a9136-1121.md"), "ok").unwrap();
        let id = "q_00000000000000b1";
        put(&store, id, "delivered", "請看", 10);
        let rev = aib_dmq::rev::item_rev(&Value::Object(read(&store, CODE, id).unwrap()));
        let p = Some(pending.as_path());
        let r = done(&store, &[], p, CODE, "q_1", rev, None, Some("x"), 20);
        assert_eq!(r.0, StatusCode::BAD_REQUEST, "reason: id_unchecked");
        let r = done(&store, &[], p, CODE, id, rev + 7, None, Some("x"), 20);
        assert_eq!(r.0, StatusCode::CONFLICT, "reason: rev_unchecked");
        let sha = "0123456789abcdef0123456789abcdef01234567";
        let r = done(&store, &[], p, CODE, id, rev, Some(sha), None, 20);
        assert_eq!(
            r.0,
            StatusCode::BAD_REQUEST,
            "reason: commit_evidence_passed {:?}",
            r.1
        );
        for bad in [
            "/etc/hosts",
            "../x.md",
            "RELAY-dm-x.txt",
            "REVIEW-none-a9136.md",
        ] {
            let r = done(&store, &[], p, CODE, id, rev, Some(bad), None, 20);
            assert_eq!(
                r.0,
                StatusCode::BAD_REQUEST,
                "reason: evidence_unchecked {bad}"
            );
        }
        assert_eq!(status(&store, id), "delivered", "reason: closed_on_refusal");
        let r = done(
            &store,
            &[],
            p,
            CODE,
            id,
            rev,
            Some("REVIEW-x-a9136-1121.md"),
            None,
            20,
        );
        assert_eq!(r.0, StatusCode::OK, "{:?}", r.1);
        assert_eq!(status(&store, id), "done");
        let id2 = "q_00000000000000b2";
        put(&store, id2, "read", "知會", 11);
        let rev2 = aib_dmq::rev::item_rev(&Value::Object(read(&store, CODE, id2).unwrap()));
        let r = done(
            &store,
            &[],
            p,
            CODE,
            id2,
            rev2,
            Some(sha),
            Some("commit 寫在 why"),
            21,
        );
        assert_eq!(
            r.0,
            StatusCode::BAD_REQUEST,
            "a commit is refused even with why"
        );
        let r = done(
            &store,
            &[],
            p,
            CODE,
            id2,
            rev2,
            None,
            Some("commit 0123abc 寫在 why"),
            21,
        );
        assert_eq!(r.0, StatusCode::OK, "{:?}", r.1);
        assert_eq!(status(&store, id2), "done");
    }

    /// G3i: the old code is the live move aibd recorded for this seat, and nothing else.
    #[test]
    fn the_old_code_is_the_recorded_live_move() {
        let d = tempfile::tempdir().unwrap();
        let db = aib_store::MessageDb::open(&d.path().join("messages.sqlite")).unwrap();
        assert_eq!(
            moved_code(&db, "seat_web1"),
            None,
            "reason: unmoved_seat_has_code"
        );
        db.seat_move("a9136", "seat_web1", "test", "2026-11-21T00:00:00Z")
            .unwrap();
        assert_eq!(moved_code(&db, "seat_web1").as_deref(), Some("a9136"));
        assert_eq!(
            moved_code(&db, "seat_web2"),
            None,
            "reason: other_seat_has_code"
        );
        db.seat_unmove("a9136", "2026-11-21T00:01:00Z").unwrap();
        assert_eq!(
            moved_code(&db, "seat_web1"),
            None,
            "reason: undone_move_kept"
        );
    }

    /// G3e3 (5d33a REVIEW-g3dm L1): a pending file named in a short-head form that is gone
    /// by the time the ledger checks it never makes the ledger run git.
    #[test]
    fn the_ledger_never_runs_git_for_a_seat() {
        let store = aib_dmq_store::Store::new(Path::new("/nonexistent"), Path::new("/nonexistent"));
        assert!(store.git_evidence);
        assert!(
            !store.without_git().git_evidence,
            "reason: git_on_seat_path"
        );
        let (d, store) = ledger();
        // a git repo where the short head is a real commit: only git could find it
        let repo = d.path();
        let git = |a: &[&str]| {
            std::process::Command::new("git")
                .arg("-C")
                .arg(repo)
                .args(a)
                .env("GIT_CONFIG_GLOBAL", "/dev/null")
                .output()
                .unwrap()
        };
        git(&["init", "-q"]);
        git(&[
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ]);
        let head = String::from_utf8(git(&["rev-parse", "--short=8", "HEAD"]).stdout).unwrap();
        let head = head.trim();
        let id = "q_00000000000000d1";
        put(&store, id, "read", "x", 10);
        let caller = aib_dmq_store::store::Caller::default();
        let ctx = aib_dmq_store::recv::Ctx {
            caller: &caller,
            caller_is_seat: true,
            relay_dirs: &[],
            now: 20,
        };
        // what the pre-check let through, its file removed since: only a short head is left
        let gone = format!("/nonexistent/REVIEW-g3-a9136-{head}.md");
        let a = aib_dmq_store::recv::close(&store, &ctx, CODE, id, "seat:a9136", Some(&gone), None)
            .unwrap();
        assert_ne!(a.out["ok"], json!(true), "reason: git_ran {:?}", a.out);
        assert_eq!(status(&store, id), "read");
    }

    /// G3f: the seat code is aibd's, never a field of the request.
    #[test]
    fn the_seat_code_is_never_taken_from_the_request() {
        assert!(fields_ok(
            &json!({"type": DONE, "id": "q_x", "rev": 1, "why": "x"})
        ));
        for k in ["seat", "code", "seat_code", "actor"] {
            let mut b = json!({"type": DONE, "id": "q_x", "rev": 1});
            b[k] = json!("6f1cb");
            assert!(!fields_ok(&b), "reason: code_from_body {k}");
        }
        assert!(!fields_ok(&json!({"type": WAITING, "id": "q_x"})));
    }

    /// G3 show: a long message's file comes behind the relay wall, mail masked; a link is not
    /// read.
    #[test]
    fn a_long_message_is_shown_behind_the_wall() {
        let (d, store) = ledger();
        let relay = d.path().join("relay");
        std::fs::create_dir(&relay).unwrap();
        let relay = std::fs::canonicalize(&relay).unwrap();
        let f = relay.join("RELAY-dm-t1.txt");
        std::fs::write(&f, "長訊 a@b.com").unwrap();
        let msg = format!("長訊已落檔(9 位元組),請讀:{}", f.display());
        put(&store, "q_00000000000000c1", "delivered", &msg, 10);
        let v = show(
            &store,
            CODE,
            "q_00000000000000c1",
            std::slice::from_ref(&relay),
        )
        .unwrap();
        let t = v["relay_text"].as_str().unwrap();
        assert!(
            t.starts_with(crate::seat_pending::RELAY_NOTE),
            "reason: no_wall"
        );
        assert!(!t.contains("a@b.com"), "reason: mail_unmasked");
        assert_eq!(show(&store, CODE, "q_1", &[]), Err("dm_id"));
        assert_eq!(
            show(&store, CODE, "q_00000000000000ff", &[]),
            Err("dm_not_found")
        );
    }
}
