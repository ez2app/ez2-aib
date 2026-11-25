//! aibd §8 合併類似, slice 8b-3 (DESIGN-merge-6770f-1123.md r1; IFACE-merge-6770f-1123.md r3):
//! the five built-in-page acts `merge_find`, `merge_preview`, `merge_run`, `merge_log`,
//! `merge_undo`.
//!
//! - Every act is restricted to the built-in page of this pairing (`seat_input::act`).
//! - `merge_preview` fills defaults into a canonical `plan` and caches its `plan_sha` for 10 min.
//! - `merge_run` only accepts a cached, unchanged `plan_sha`; persona/brain merges require the
//!   owner's fingerprint (`owner_ok`) with the 12-hex `window_sha12` and no text truncation.
//! - Persona & brain seat switches write `seat_next` (`8b-2` applies at next open/revive);
//!   running seats are never mutated mid-flight.
//! - Every step records `before` and `planned_after` as `pending` before mutating, then `done`;
//!   failure or `merge_undo` rolls back in reverse via `merge_plan::back`.

use crate::api::AppState;
use crate::merge_find::{self, Item};
use crate::merge_notes;
use crate::merge_plan::{self, StepState};
use aib_store::registry_archive::{ArchivedItem, MergeBatch, MergeStep};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Mutex, OnceLock};

pub const MERGE_FIND: &str = "merge_find";
pub const MERGE_PREVIEW: &str = "merge_preview";
pub const MERGE_RUN: &str = "merge_run";
pub const MERGE_LOG: &str = "merge_log";
pub const MERGE_UNDO: &str = "merge_undo";

pub const OP_PERSONA: &str = "merge.persona";
pub const OP_BRAIN: &str = "merge.brain";

const PLAN_TTL_SECS: u64 = 600;
const PLAN_CACHE_MAX: usize = 64;
const NAME_MAX_CHARS: usize = 20;
use crate::merge_notes::{
    scan as scan_brain_notes, NotesCheck, MAX_BYTES as NOTES_MAX_BYTES,
    MAX_FILES as NOTES_MAX_FILES,
};
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct CachedPlan {
    #[allow(dead_code)]
    plan: Value,
    expires_at: u64,
}

static PLANS: OnceLock<Mutex<HashMap<String, CachedPlan>>> = OnceLock::new();

fn plan_cache() -> &'static Mutex<HashMap<String, CachedPlan>> {
    PLANS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

fn now_i64() -> i64 {
    i64::try_from(now_secs()).unwrap_or(i64::MAX)
}

fn err(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"code": code, "reason": code, "message": message})),
    )
        .into_response()
}

fn bad(code: &str, message: &str) -> Response {
    err(StatusCode::BAD_REQUEST, code, message)
}

fn format_char(c: char) -> bool {
    c.is_control()
        || matches!(
            c,
            '\u{00AD}'
                | '\u{061C}'
                | '\u{200B}'..='\u{200F}'
                | '\u{202A}'..='\u{202E}'
                | '\u{2060}'..='\u{2064}'
                | '\u{2066}'..='\u{2069}'
                | '\u{FEFF}'
        )
}

/// Clean a merged display name: strip control / bidi / invisible format characters (A13 set),
/// trim, and enforce `<= 20` characters.
pub fn clean_name(s: &str) -> Result<String, &'static str> {
    let cleaned: String = s.chars().filter(|c| !format_char(*c)).collect();
    let trimmed = cleaned.trim();
    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS {
        return Err("name");
    }
    Ok(trimmed.to_string())
}

fn only_keys(body: &Value, allowed: &[&str]) -> Result<(), Box<Response>> {
    let Some(obj) = body.as_object() else {
        return Err(Box::new(bad("body", "請求格式不正確")));
    };
    if obj.keys().any(|k| !allowed.contains(&k.as_str())) {
        return Err(Box::new(bad("body", "包含不認得的欄位")));
    }
    Ok(())
}

fn known_prefixes(_st: &AppState) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(ws) = crate::assign::workspace_db() {
        if let Ok(nodes) = ws.nodes() {
            for n in nodes {
                if n.parent_id.is_none() && !n.title.trim().is_empty() {
                    out.push(n.title);
                }
            }
        }
    }
    for b in aib_seatopen::AIB2_BRANDS {
        out.push((*b).to_string());
    }
    out
}

fn load_live_items(st: &AppState, kind: &str) -> Result<Vec<Item>, Box<Response>> {
    let archived: HashSet<String> = st
        .messages
        .archived_uids(kind)
        .map_err(|_| {
            Box::new(err(
                StatusCode::INTERNAL_SERVER_ERROR,
                "store",
                "封存清單讀取失敗",
            ))
        })?
        .into_iter()
        .collect();
    let metas = st.store.seat_metas().map_err(|_| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store",
            "席位資料讀取失敗",
        ))
    })?;
    let data = crate::registry::data_of(st);
    let reg = crate::registry::load(&data).map_err(|_| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "registry",
            "清單讀取失敗",
        ))
    })?;

    match kind {
        "persona" => {
            let cards = st.personas.cards().map_err(|_| {
                Box::new(err(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "personas",
                    "人格清單讀取失敗",
                ))
            })?;
            let mut used: HashMap<String, u32> = HashMap::new();
            for m in &metas {
                if let Some(p) = m.persona_uid.as_deref() {
                    let resolved = st
                        .messages
                        .alias_resolve(p)
                        .unwrap_or_else(|_| p.to_string());
                    *used.entry(resolved).or_default() += 1;
                }
            }
            let mut items = Vec::new();
            for c in cards {
                if archived.contains(&c.uid) {
                    continue;
                }
                if let Ok(resolved) = st.messages.alias_resolve(&c.uid) {
                    if resolved != c.uid {
                        continue;
                    }
                }
                let u = used.get(&c.uid).copied().unwrap_or(0);
                items.push(Item {
                    uid: c.uid,
                    name: c.name,
                    used: u,
                    builtin: false,
                });
            }
            Ok(items)
        }
        "brain" => {
            let mut used: HashMap<String, u32> = HashMap::new();
            for m in &metas {
                if let Some(r) = m.brain_route.as_deref() {
                    *used.entry(r.to_string()).or_default() += 1;
                }
            }
            let items = reg
                .brains
                .into_iter()
                .filter(|b| !archived.contains(&b.uid))
                .map(|b| {
                    let u = b
                        .route_key
                        .as_deref()
                        .and_then(|k| used.get(k))
                        .copied()
                        .unwrap_or(0);
                    Item {
                        uid: b.uid,
                        name: b.name,
                        used: u,
                        builtin: false,
                    }
                })
                .collect();
            Ok(items)
        }
        "role" => {
            let mut used: HashMap<String, u32> = HashMap::new();
            for m in &metas {
                if let Some(r) = m.role.as_deref() {
                    *used.entry(r.to_string()).or_default() += 1;
                }
            }
            if let Some(ws) = crate::assign::workspace_db() {
                if let Ok(rows) = ws.list(None, None) {
                    for r in rows {
                        *used.entry(r.role).or_default() += 1;
                    }
                }
            }
            let mut items = Vec::new();
            for (uid, name, _) in crate::registry::BUILTIN_ROLES {
                if archived.contains(uid) {
                    continue;
                }
                items.push(Item {
                    uid: uid.to_string(),
                    name: name.to_string(),
                    used: used.get(uid).copied().unwrap_or(0),
                    builtin: true,
                });
            }
            for r in reg.roles {
                if archived.contains(&r.uid) {
                    continue;
                }
                let u = used.get(&r.uid).copied().unwrap_or(0);
                items.push(Item {
                    uid: r.uid,
                    name: r.name,
                    used: u,
                    builtin: false,
                });
            }
            Ok(items)
        }
        _ => Err(Box::new(bad("kind", "kind 必須是 persona、brain 或 role"))),
    }
}

/// `merge_find {kind}`.
pub async fn find(st: AppState, body: Value) -> Response {
    if let Err(r) = only_keys(&body, &["type", "kind", "idem_key"]) {
        return *r;
    }
    let Some(kind) = body.get("kind").and_then(Value::as_str).map(str::to_owned) else {
        return bad("kind", "請指定 kind（persona / brain / role）");
    };
    let st2 = st.clone();
    let k2 = kind.clone();
    let res = tokio::task::spawn_blocking(move || -> Result<Value, Box<Response>> {
        let items = load_live_items(&st2, &k2)?;
        let prefixes = known_prefixes(&st2);
        let groups = merge_find::find_similar(&items, &prefixes);
        let json_groups: Vec<Value> = groups
            .into_iter()
            .map(|g| {
                let ticked: Vec<String> = g.ticked().into_iter().map(str::to_owned).collect();
                let items: Vec<Value> = g
                    .items
                    .into_iter()
                    .map(|x| {
                        json!({
                            "uid": x.uid,
                            "name": x.name,
                            "used": x.used,
                            "builtin": x.builtin,
                        })
                    })
                    .collect();
                json!({
                    "items": items,
                    "keep": g.keep,
                    "sure": g.sure,
                    "low": g.low,
                    "ticked": ticked,
                })
            })
            .collect();
        Ok(json!({"kind": k2, "groups": json_groups}))
    })
    .await;
    match res {
        Ok(Ok(v)) => (StatusCode::OK, Json(v)).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "internal", "查詢失敗"),
    }
}

fn parse_days(v: Option<&Value>) -> Result<Value, &'static str> {
    match v {
        None | Some(Value::Null) => Ok(json!(30)),
        Some(Value::Number(n)) if matches!(n.as_u64(), Some(7 | 30 | 90)) => {
            Ok(Value::Number(n.clone()))
        }
        Some(Value::String(s)) if s == "forever" => Ok(json!("forever")),
        _ => Err("days 必須是 7、30、90 或 \"forever\""),
    }
}

fn parse_notes(v: Option<&Value>) -> Result<&'static str, &'static str> {
    match v {
        None | Some(Value::Null) => Ok("skip"),
        Some(Value::String(s)) if s == "copy" => Ok("copy"),
        Some(Value::String(s)) if s == "skip" => Ok("skip"),
        _ => Err("notes 必須是 copy 或 skip"),
    }
}

fn parse_card(v: Option<&Value>) -> Result<&'static str, &'static str> {
    match v {
        None | Some(Value::Null) => Ok("keep"),
        Some(Value::String(s)) if s == "propose" => Ok("propose"),
        Some(Value::String(s)) if s == "keep" => Ok("keep"),
        _ => Err("card 必須是 propose 或 keep"),
    }
}

type NormalizedPreview = (Value, Vec<Value>, Vec<String>, usize, Option<u32>);

fn normalize_preview(st: &AppState, body: &Value) -> Result<NormalizedPreview, Box<Response>> {
    only_keys(body, &["type", "kind", "groups", "idem_key"])?;
    let Some(kind) = body.get("kind").and_then(Value::as_str) else {
        return Err(Box::new(bad("kind", "請指定 kind")));
    };
    if !matches!(kind, "persona" | "brain" | "role") {
        return Err(Box::new(bad("kind", "kind 必須是 persona、brain 或 role")));
    }
    let Some(raw_groups) = body.get("groups").and_then(Value::as_array) else {
        return Err(Box::new(bad("groups", "groups 必須是陣列")));
    };
    if raw_groups.is_empty() {
        return Err(Box::new(bad("groups", "請至少選一組")));
    }

    let items = load_live_items(st, kind)?;
    let by_uid: HashMap<String, Item> = items.into_iter().map(|i| (i.uid.clone(), i)).collect();
    let metas = st.store.seat_metas().map_err(|_| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store",
            "席位讀取失敗",
        ))
    })?;

    let mut seen_uids: HashSet<String> = HashSet::new();
    let mut norm_groups = Vec::new();
    let mut out_groups = Vec::new();
    let mut steps_text = Vec::new();
    let mut seats_next_open: usize = 0;
    let mut max_days: Option<u32> = Some(0);

    for g in raw_groups {
        only_keys(
            g,
            &[
                "keep",
                "merge",
                "name",
                "seat_to",
                "keep_brain",
                "rebind",
                "notes",
                "days",
                "card",
            ],
        )?;
        let Some(keep) = g.get("keep").and_then(Value::as_str) else {
            return Err(Box::new(bad("keep", "每組都要有 keep")));
        };
        let Some(keep_item) = by_uid.get(keep) else {
            return Err(Box::new(bad("keep", "找不到要留下的項目")));
        };
        if !seen_uids.insert(keep.to_string()) {
            return Err(Box::new(bad("groups", "同一個項目不能出現在多組")));
        }
        let Some(merge_arr) = g.get("merge").and_then(Value::as_array) else {
            return Err(Box::new(bad("merge", "merge 必須是陣列")));
        };
        if merge_arr.is_empty() {
            return Err(Box::new(bad("merge", "每組至少要有一個要合併的項目")));
        }
        let mut merge_uids = Vec::new();
        for m in merge_arr {
            let Some(u) = m.as_str() else {
                return Err(Box::new(bad("merge", "merge 項目必須是 uid")));
            };
            if u == keep {
                return Err(Box::new(bad("merge", "keep 不能同時在 merge 裡")));
            }
            let Some(mi) = by_uid.get(u) else {
                return Err(Box::new(bad("merge", "找不到要合併的項目")));
            };
            if mi.builtin {
                return Err(Box::new(bad("merge", "內建角色不能被合併")));
            }
            if !seen_uids.insert(u.to_string()) {
                return Err(Box::new(bad("merge", "同一項目不能重複合併")));
            }
            merge_uids.push(u.to_string());
        }

        let merged_name = match g.get("name") {
            None | Some(Value::Null) => keep_item.name.clone(),
            Some(Value::String(s)) => {
                if s.trim().is_empty() {
                    keep_item.name.clone()
                } else {
                    clean_name(s)
                        .map_err(|_| Box::new(bad("name", "合併後名稱不可超過 20 字或為空")))?
                }
            }
            _ => return Err(Box::new(bad("name", "name 必須是字串"))),
        };

        let days_val = parse_days(g.get("days")).map_err(|msg| Box::new(bad("days", msg)))?;
        if days_val == json!("forever") {
            max_days = None;
        } else if let (Some(cur), Some(d)) = (max_days, days_val.as_u64()) {
            max_days = Some(cur.max(u32::try_from(d).unwrap_or(30)));
        }
        let notes = parse_notes(g.get("notes")).map_err(|msg| Box::new(bad("notes", msg)))?;
        let card = parse_card(g.get("card")).map_err(|msg| Box::new(bad("card", msg)))?;

        let seat_to: BTreeMap<String, String> = match g.get("seat_to") {
            None | Some(Value::Null) => {
                let mut m = BTreeMap::new();
                if kind == "persona" {
                    let merge_set: HashSet<&str> = merge_uids.iter().map(String::as_str).collect();
                    for sm in &metas {
                        if sm
                            .persona_uid
                            .as_deref()
                            .is_some_and(|p| merge_set.contains(p))
                        {
                            m.insert(sm.seat_id.clone(), keep.to_string());
                        }
                    }
                }
                m
            }
            Some(Value::Object(o)) => {
                let mut m = BTreeMap::new();
                for (sid, v) in o {
                    let Some(target) = v.as_str() else {
                        return Err(Box::new(bad("seat_to", "seat_to 值必須是 uid")));
                    };
                    m.insert(sid.clone(), target.to_string());
                }
                m
            }
            _ => return Err(Box::new(bad("seat_to", "seat_to 必須是物件"))),
        };
        seats_next_open += seat_to.len();

        let keep_brain = match g.get("keep_brain") {
            None | Some(Value::Null) => Value::Null,
            Some(Value::String(s)) => Value::String(s.clone()),
            _ => return Err(Box::new(bad("keep_brain", "keep_brain 必須是字串或 null"))),
        };
        let rebind = match g.get("rebind") {
            None | Some(Value::Null) => json!({}),
            Some(Value::Object(o)) => Value::Object(o.clone()),
            _ => return Err(Box::new(bad("rebind", "rebind 必須是物件"))),
        };

        let mut effects = Vec::new();
        if merged_name != keep_item.name {
            effects.push(json!({"tag": "合併後名稱", "text": format!("改名為「{merged_name}」")}));
            steps_text.push(format!("改名為「{merged_name}」"));
        }
        for (sid, target) in &seat_to {
            let code = crate::seats::seat_code(sid);
            effects.push(json!({"tag": "換人格", "text": format!("@{code} 下次開改用 {target}")}));
            steps_text.push(format!("@{code} 下次開改用 {target}"));
        }
        let keep_label = match days_val.as_u64() {
            Some(d) => format!("封存（{d} 天內可還原）"),
            None => "永久封存".to_string(),
        };
        effects.push(json!({"tag": "封存", "text": keep_label}));
        steps_text.push(keep_label);

        norm_groups.push(json!({
            "keep": keep,
            "merge": merge_uids,
            "name": merged_name,
            "seat_to": seat_to,
            "keep_brain": keep_brain,
            "rebind": rebind,
            "notes": notes,
            "days": days_val,
            "card": card,
        }));
        out_groups.push(json!({
            "keep": keep,
            "merge": merge_uids,
            "effects": effects,
        }));
    }
    steps_text.push("寫進合併紀錄".to_string());

    let plan = json!({
        "kind": kind,
        "groups": norm_groups,
    });
    Ok((plan, out_groups, steps_text, seats_next_open, max_days))
}

/// `merge_preview {kind, groups[]}`.
pub async fn preview(st: AppState, body: Value) -> Response {
    let st2 = st.clone();
    let res = tokio::task::spawn_blocking(move || -> Result<Value, Box<Response>> {
        let (plan, out_groups, steps, _seats_next, _days) = normalize_preview(&st2, &body)?;
        let sha = merge_plan::plan_sha(&plan);
        let sha12: String = sha.chars().take(12).collect();
        let kind = plan["kind"].as_str().unwrap_or_default();
        let needs_owner = merge_plan::needs_owner(kind);

        let now = now_secs();
        if let Ok(mut cache) = plan_cache().lock() {
            cache.retain(|_, v| v.expires_at > now);
            if cache.len() >= PLAN_CACHE_MAX {
                if let Some(oldest) = cache.keys().next().cloned() {
                    cache.remove(&oldest);
                }
            }
            cache.insert(
                sha.clone(),
                CachedPlan {
                    plan: plan.clone(),
                    expires_at: now + PLAN_TTL_SECS,
                },
            );
        }

        let mut resp = json!({
            "plan_sha": sha,
            "plan": plan,
            "needs_owner": needs_owner,
            "window_sha12": sha12,
            "groups": out_groups,
            "steps": steps,
            "other_hosts": "別台 Mac / Windows 上的副本不會改,那邊的夥伴照舊用舊名",
        });
        if kind == "brain" {
            let wants_copy = plan["groups"]
                .as_array()
                .is_some_and(|gs| gs.iter().any(|g| g["notes"] == "copy"));
            if wants_copy {
                let mut total = NotesCheck::default();
                if let Ok(root) = crate::brain_note::library() {
                    let data = crate::registry::data_of(&st2);
                    if let Ok(reg) = crate::registry::load(&data) {
                        for g in plan["groups"].as_array().into_iter().flatten() {
                            if g["notes"] != "copy" {
                                continue;
                            }
                            for m in g["merge"].as_array().into_iter().flatten() {
                                if let Some(uid) = m.as_str() {
                                    if let Some(rk) = reg
                                        .brains
                                        .iter()
                                        .find(|b| b.uid == uid)
                                        .and_then(|b| b.route_key.as_deref())
                                    {
                                        if let Ok(rel) = crate::registry::brain_folder(&root, rk) {
                                            let c = scan_brain_notes(&root.join(rel));
                                            total.files += c.files;
                                            total.bytes = total.bytes.saturating_add(c.bytes);
                                            total.cloud_placeholders += c.cloud_placeholders;
                                            total.over_limit |= c.over_limit
                                                || total.files > NOTES_MAX_FILES
                                                || total.bytes > NOTES_MAX_BYTES;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                resp["notes_check"] = json!({
                    "files": total.files,
                    "bytes": total.bytes,
                    "cloud_placeholders": total.cloud_placeholders,
                    "over_limit": total.over_limit,
                });
            }
        }
        Ok(resp)
    })
    .await;
    match res {
        Ok(Ok(v)) => (StatusCode::OK, Json(v)).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "internal", "預覽失敗"),
    }
}

fn count_seats_and_days(plan: &Value) -> (usize, Option<u32>) {
    let groups = plan["groups"].as_array().map_or(&[][..], Vec::as_slice);
    let mut seats = 0usize;
    let mut max_days: Option<u32> = Some(0);
    for g in groups {
        if let Some(m) = g.get("seat_to").and_then(Value::as_object) {
            seats += m.len();
        }
        if g.get("days") == Some(&json!("forever")) {
            max_days = None;
        } else if let (Some(cur), Some(d)) = (max_days, g.get("days").and_then(Value::as_u64)) {
            max_days = Some(cur.max(u32::try_from(d).unwrap_or(30)));
        }
    }
    (seats, max_days)
}

/// `merge_run {plan_sha, plan, ticket?}`.
pub async fn run(st: AppState, body: Value, by: String) -> Response {
    if let Err(r) = only_keys(&body, &["type", "plan_sha", "plan", "ticket", "idem_key"]) {
        return *r;
    }
    let Some(plan_sha) = body
        .get("plan_sha")
        .and_then(Value::as_str)
        .map(str::to_owned)
    else {
        return bad("plan_sha", "請提供 plan_sha");
    };
    let Some(plan) = body.get("plan").cloned() else {
        return bad("plan", "請提供預覽回來的 plan");
    };
    let ticket = body
        .get("ticket")
        .and_then(Value::as_str)
        .map(str::to_owned);

    let computed = merge_plan::plan_sha(&plan);
    if computed != plan_sha {
        return err(
            StatusCode::CONFLICT,
            "plan_changed",
            "計畫內容與預覽不一致，請重新預覽",
        );
    }
    let cached_ok = {
        let now = now_secs();
        plan_cache()
            .lock()
            .ok()
            .and_then(|c| c.get(&plan_sha).filter(|v| v.expires_at > now).cloned())
            .is_some()
    };
    if !cached_ok {
        return err(
            StatusCode::CONFLICT,
            "plan_changed",
            "預覽已過期或不存在，請重新預覽",
        );
    }

    let kind = plan["kind"].as_str().unwrap_or_default().to_owned();
    if !matches!(kind.as_str(), "persona" | "brain" | "role") {
        return bad("kind", "不支援的合併種類");
    }
    let groups_len = plan["groups"].as_array().map_or(0, Vec::len);
    let (seats_next_open, days) = count_seats_and_days(&plan);

    if merge_plan::needs_owner(&kind) {
        let Some(words) =
            merge_plan::window_text(&kind, false, groups_len, seats_next_open, days, &plan_sha)
        else {
            return bad("window_too_long", "驗證視窗文字過長");
        };
        let op = if kind == "persona" {
            OP_PERSONA
        } else {
            OP_BRAIN
        };
        let ask = crate::owner_ok::Ask {
            category: aib_approve::category::Category::Command,
            operation: op,
            // M1 (f163c REVIEW-macts): the window shows what is merged, not a hash
            resource: words, // M1 run
            params: serde_json::to_vec(&plan).unwrap_or_default(),
            by: by.clone(),
        };
        match ticket {
            None => return crate::repos::asked(&ask),
            Some(t) => {
                if !crate::owner_ok::approved_for(&t, &ask) {
                    return crate::repos::not_approved();
                }
            }
        }
    }

    let st2 = st.clone();
    let res = tokio::task::spawn_blocking(move || execute_merge(&st2, &plan, &plan_sha, &by)).await;
    match res {
        Ok(Ok(v)) => (StatusCode::OK, Json(v)).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "internal", "執行失敗"),
    }
}

/// The run's journal (DESIGN-merge §5): every step is written `pending` before it changes
/// anything, numbered from 0 with no gap as `merge_step_pending` requires, then `done`. A
/// journal write that fails stops the run: a step the journal does not hold could not be put
/// back (f163c REVIEW-macts M2: no store error is dropped).
struct Journal<'a> {
    st: &'a AppState,
    batch_id: &'a str,
    seq: i64,
}

impl Journal<'_> {
    fn step(
        &mut self,
        op: &str,
        target: &str,
        before: &Value,
        after: &Value,
        act: impl FnOnce() -> Result<(), &'static str>,
    ) -> Result<(), &'static str> {
        let s = self.seq;
        self.st
            .messages
            .merge_step_pending(MergeStep {
                batch_id: self.batch_id.to_string(),
                seq: s,
                op: op.to_string(),
                target: target.to_string(),
                before_json: before.to_string(),
                planned_after_json: after.to_string(),
                state: "pending".to_string(),
            })
            .map_err(|_| "merge_journal")?;
        self.seq += 1;
        act()?;
        match self.st.messages.merge_step_state(self.batch_id, s, "done") {
            Ok(true) => Ok(()),
            _ => Err("merge_journal"),
        }
    }
}

/// L1 (f163c REVIEW-macts): what the plan keeps and merges is still there and not archived
/// now (another preview may have run since this one).
fn plan_still_live(st: &AppState, kind: &str, groups: &[Value]) -> Result<(), Box<Response>> {
    let live: HashSet<String> = load_live_items(st, kind)?
        .into_iter()
        .map(|i| i.uid)
        .collect();
    let stale = groups.iter().any(|g| {
        std::iter::once(&g["keep"])
            .chain(g["merge"].as_array().into_iter().flatten())
            .any(|u| u.as_str().is_none_or(|u| !live.contains(u)))
    });
    if stale {
        return Err(Box::new(err(
            StatusCode::CONFLICT,
            "plan_stale",
            "計畫裡有項目已經被封存或拿掉，請重新預覽",
        )));
    }
    Ok(())
}

fn execute_merge(
    st: &AppState,
    plan: &Value,
    plan_sha: &str,
    by: &str,
) -> Result<Value, Box<Response>> {
    let kind = plan["kind"].as_str().unwrap_or_default();
    let groups = plan["groups"]
        .as_array()
        .ok_or_else(|| Box::new(bad("plan", "groups 缺漏")))?;
    plan_still_live(st, kind, groups)?;
    let batch_id = crate::sessions::new_id("mb_").map_err(|_| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "無法產生批號",
        ))
    })?;
    let at = now_i64();
    let (_, days) = count_seats_and_days(plan);
    let keep_until = days.map(|d| at + i64::from(d) * 86_400);

    st.messages
        .merge_batch_begin(MergeBatch {
            batch_id: batch_id.clone(),
            kind: kind.to_string(),
            by_key: by.to_string(),
            at,
            state: "run".to_string(),
            plan_json: plan.to_string(),
            plan_sha: plan_sha.to_string(),
            keep_until,
        })
        .map_err(|_| {
            Box::new(err(
                StatusCode::INTERNAL_SERVER_ERROR,
                "store",
                "建立批次失敗",
            ))
        })?;

    let data = crate::registry::data_of(st);
    let mut j = Journal {
        st,
        batch_id: &batch_id,
        seq: 0,
    };
    let ran = run_steps(&mut j, kind, groups, &data, at, keep_until).and_then(|()| {
        match st.messages.merge_batch_state(&batch_id, "done") {
            Ok(true) => Ok(()),
            _ => Err("merge_journal"),
        }
    });
    match ran {
        Ok(()) => {
            // N1: a plan runs once; a second send needs a new preview
            if let Ok(mut c) = plan_cache().lock() {
                c.remove(plan_sha);
            }
            Ok(json!({
                "batch_id": batch_id,
                "state": "done",
            }))
        }
        Err(code) => {
            // L2: stopped midway ⇒ what was done is put back now, the batch is `failed`
            let back = undo_steps(st, kind, &batch_id);
            let _ = st.messages.merge_batch_state(&batch_id, "failed");
            // N1 (f163c REVIEW-macts da8e773f): say "nothing changed" only when every step
            // went back; else how many did not, and the batch's merge_log lists them
            let left = match &back {
                Ok(kept) => kept.len(),
                Err(()) => usize::MAX,
            };
            let (status, message) = if left != 0 {
                let n = if left == usize::MAX {
                    "步驟讀不到，".to_owned()
                } else {
                    format!("有 {left} 步沒有倒回，")
                };
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    format!("合併中途失敗，{n}請看合併紀錄 {batch_id}"),
                )
            } else if code.starts_with("notes_") || code.starts_with("brain_") {
                (
                    StatusCode::CONFLICT,
                    "筆記複製沒有完成，已全部收回，什麼都沒改".to_owned(),
                )
            } else {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "合併中途失敗，已做的步驟已倒回".to_owned(),
                )
            };
            Err(Box::new(err(status, code, &message)))
        }
    }
}

fn run_steps(
    j: &mut Journal<'_>,
    kind: &str,
    groups: &[Value],
    data: &Path,
    at: i64,
    keep_until: Option<i64>,
) -> Result<(), &'static str> {
    let (st, batch_id) = (j.st, j.batch_id);
    if kind == "brain" {
        copy_notes(j, groups, data)?;
    }
    for g in groups {
        let keep = g["keep"].as_str().unwrap_or_default();
        let merge_list = g["merge"].as_array().map_or(&[][..], Vec::as_slice);
        let merged_name = g["name"].as_str().unwrap_or_default();

        for m in merge_list {
            let old_uid = m.as_str().unwrap_or_default();
            j.step(
                "archive",
                old_uid,
                &json!({"archived": false}),
                &json!({"archived": true, "keep": keep}),
                || {
                    st.messages
                        .archive_add(ArchivedItem {
                            kind: kind.to_string(),
                            uid: old_uid.to_string(),
                            archived_at: at,
                            keep_until,
                            reason: "merge".to_string(),
                            batch_id: Some(batch_id.to_string()),
                            copy_dir: None,
                            sha256: None,
                            restored_at: None,
                        })
                        .map_err(|_| "archive_failed")
                },
            )?;
            if kind == "persona" {
                j.step(
                    "alias",
                    old_uid,
                    &json!({"alias": Value::Null}),
                    &json!({"alias": keep}),
                    || {
                        st.messages
                            .alias_set(old_uid, keep, batch_id, at)
                            .map_err(|_| "alias_failed")
                    },
                )?;
            }
        }

        if kind == "persona" {
            for (sid, target) in g
                .get("seat_to")
                .and_then(Value::as_object)
                .into_iter()
                .flatten()
            {
                let target_uid = target.as_str().unwrap_or(keep);
                // L3: the switch the seat had before (an earlier batch's) is what undo puts back
                let prev = st.messages.seat_next(sid).map_err(|_| "seat_next_failed")?;
                let before = json!({"row": prev.map(|r| json!({
                    "persona_next": r.persona_next,
                    "brain_next": r.brain_next,
                    "batch_id": r.batch_id,
                    "at": r.at,
                }))});
                j.step(
                    "seat_next_persona",
                    sid,
                    &before,
                    &json!({"persona_next": target_uid}),
                    || {
                        st.messages
                            .seat_next_set(sid, Some(target_uid), None, batch_id, at)
                            .map_err(|_| "seat_next_failed")
                    },
                )?;
            }
        } else if kind == "role" {
            let mut reg = crate::registry::load(data).map_err(|_| "registry_failed")?;
            if let Some(i) = reg.roles.iter().position(|r| r.uid == keep) {
                if !merged_name.is_empty() && reg.roles[i].name != merged_name {
                    let old_name =
                        std::mem::replace(&mut reg.roles[i].name, merged_name.to_string());
                    j.step(
                        "role_rename",
                        keep,
                        &json!({"name": old_name}),
                        &json!({"name": merged_name}),
                        || {
                            crate::registry::save_for_merge(data, &reg)
                                .map_err(|_| "role_rename_failed")
                        },
                    )?;
                }
            }
            move_assignments(j, merge_list, keep)?;
        }
    }
    Ok(())
}

/// M2 (f163c REVIEW-macts): every assignment on a merged role moves to the kept one as its
/// own step (`before` = the row as it was), so undo can move it back.
fn move_assignments(
    j: &mut Journal<'_>,
    merge_list: &[Value],
    keep: &str,
) -> Result<(), &'static str> {
    let Some(ws) = crate::assign::workspace_db() else {
        return Ok(());
    };
    let merge_set: HashSet<&str> = merge_list.iter().filter_map(Value::as_str).collect();
    let rows = ws.list(None, None).map_err(|_| "role_assign_failed")?;
    for row in rows
        .into_iter()
        .filter(|r| merge_set.contains(r.role.as_str()))
    {
        let keep_was_there = ws
            .list(Some(&row.node_id), Some(&row.seat_id))
            .map_err(|_| "role_assign_failed")?
            .iter()
            .any(|r| r.role == keep);
        let before = json!({
            "node": row.node_id,
            "seat": row.seat_id,
            "role": row.role,
            "created": row.created,
            "by_key": row.by_key,
            "keep_was_there": keep_was_there,
        });
        let seat = row.seat_id.clone();
        j.step(
            "role_assign",
            &seat,
            &before,
            &json!({"role": keep}),
            || {
                ws.remove(&row.node_id, &row.seat_id, &row.role)
                    .map_err(|_| "role_assign_failed")?;
                match ws.add(aib_store::AssignRow {
                    role: keep.to_string(),
                    ..row.clone()
                }) {
                    Ok(aib_store::Added::New | aib_store::Added::Exists) => Ok(()),
                    _ => {
                        let _ = ws.add(row.clone());
                        Err("role_assign_failed")
                    }
                }
            },
        )?;
    }
    Ok(())
}

/// Where a brain's folder is: its route key and the folder under the brain library.
fn brain_dir(
    root: &Path,
    reg: &crate::registry::Registry,
    uid: &str,
) -> Result<(String, PathBuf), &'static str> {
    let rk = reg
        .brains
        .iter()
        .find(|b| b.uid == uid)
        .and_then(|b| b.route_key.clone())
        .ok_or("brain_unknown")?;
    let rel = crate::registry::brain_folder(root, &rk)?;
    Ok((rk, root.join(rel)))
}

type Made = (PathBuf, merge_notes::Copied, PathBuf, i64);

fn copy_all(
    j: &mut Journal<'_>,
    groups: &[Value],
    data: &Path,
    root: &Path,
    reg: &crate::registry::Registry,
    made: &mut Vec<Made>,
) -> Result<(), &'static str> {
    for g in groups.iter().filter(|g| g["notes"] == "copy") {
        let keep = g["keep"].as_str().unwrap_or_default();
        let (keep_rk, keep_dir) = brain_dir(root, reg, keep)?;
        for m in g["merge"].as_array().into_iter().flatten() {
            let old_uid = m.as_str().unwrap_or_default();
            let (rk, dir) = brain_dir(root, reg, old_uid)?;
            let s = j.seq;
            let mpath =
                merge_notes::manifest_path(data, j.batch_id, s).ok_or("notes_copy_failed")?;
            j.step(
                "notes_copy",
                old_uid,
                &json!({"copied": false}),
                &json!({"copied": true, "into": keep}),
                || {
                    let c = merge_notes::copy_into(&dir, &keep_dir, &rk)?;
                    if let Err(e) = merge_notes::write_manifest(&mpath, &keep_rk, &c) {
                        merge_notes::remove_copy(&keep_dir, &c);
                        return Err(e);
                    }
                    made.push((keep_dir.clone(), c, mpath.clone(), s));
                    Ok(())
                },
            )?;
        }
    }
    Ok(())
}

/// 8c (DESIGN-merge §4b-2): every `notes: copy` group copies its merged brains' notes into the
/// kept brain before anything else in the run changes. One copy failing removes every copy
/// this run made (folders and manifests): a refused run leaves no half copy.
fn copy_notes(j: &mut Journal<'_>, groups: &[Value], data: &Path) -> Result<(), &'static str> {
    if !groups.iter().any(|g| g["notes"] == "copy") {
        return Ok(());
    }
    let root = crate::brain_note::library()?;
    let reg = crate::registry::load(data).map_err(|_| "brain_index_unreadable")?;
    let mut made: Vec<Made> = Vec::new();
    let res = copy_all(j, groups, data, &root, &reg, &mut made);
    if res.is_err() {
        for (keep_dir, c, mpath, s) in made.iter().rev() {
            merge_notes::remove_copy(keep_dir, c);
            let _ = std::fs::remove_file(mpath);
            let _ = j.st.messages.merge_step_state(j.batch_id, *s, "undone");
        }
    }
    res
}

/// Undo one `notes_copy` step: what is still as copied goes to `<data>/archive/merge-undo/`.
fn undo_notes(data: &Path, batch_id: &str, seq: i64) -> Option<merge_notes::TookBack> {
    let mpath = merge_notes::manifest_path(data, batch_id, seq)?;
    let (keep_rk, c) = merge_notes::read_manifest(&mpath)?;
    let root = crate::brain_note::library().ok()?;
    let keep_dir = root.join(crate::registry::brain_folder(&root, &keep_rk).ok()?);
    let undo_dir = data.join("archive").join("merge-undo").join(batch_id);
    Some(merge_notes::take_back(&keep_dir, &c, &undo_dir))
}

fn execute_undo(st: &AppState, batch: &MergeBatch) -> Result<Value, Box<Response>> {
    let kept_changed = undo_steps(st, &batch.kind, &batch.batch_id).map_err(|()| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store",
            "步驟讀取失敗",
        ))
    })?;
    let final_state = if kept_changed.is_empty() {
        "undone"
    } else {
        "partial_undo"
    };
    let _ = st.messages.merge_batch_state(&batch.batch_id, final_state);
    Ok(json!({
        "batch_id": batch.batch_id,
        "state": final_state,
        "kept_changed": kept_changed,
    }))
}

/// Put a batch's steps back, last first (its undo, or a run that stopped midway); what was
/// changed since and is left alone.
fn undo_steps(st: &AppState, kind: &str, batch_id: &str) -> Result<Vec<Value>, ()> {
    let steps = st.messages.merge_steps(batch_id).map_err(|_| ())?;
    let at = now_i64();
    let data = crate::registry::data_of(st);
    let mut kept_changed: Vec<Value> = Vec::new();
    // L1 (f163c REVIEW-macts da8e773f): a step is undone only when the store took the
    // put-back and the journal says so; else it is listed as kept and the batch is partial
    let undone = |seq: i64| {
        st.messages
            .merge_step_state(batch_id, seq, "undone")
            .is_ok()
    };

    for s in steps.iter().rev() {
        let state = match s.state.as_str() {
            "pending" => StepState::Pending,
            "done" => StepState::Done,
            _ => StepState::Undone,
        };
        let before: Value = serde_json::from_str(&s.before_json).unwrap_or(Value::Null);
        let after: Value = serde_json::from_str(&s.planned_after_json).unwrap_or(Value::Null);
        match s.op.as_str() {
            "archive" => {
                let is_archived = st
                    .messages
                    .archived(kind, &s.target)
                    .ok()
                    .flatten()
                    .is_some();
                let now_val = json!({"archived": is_archived, "keep": after["keep"]});
                if merge_plan::back(state, &now_val, &before, &after) == merge_plan::Back::Restore {
                    let did = st.messages.archive_restore(kind, &s.target, at).is_ok();
                    settle(did, || undone(s.seq), &s.target, &mut kept_changed);
                }
            }
            "alias" => {
                if state != StepState::Undone {
                    let did = st.messages.alias_remove_batch(batch_id).is_ok();
                    settle(did, || undone(s.seq), &s.target, &mut kept_changed);
                }
            }
            "seat_next_persona" => {
                if state == StepState::Done {
                    let now = st.messages.seat_next(&s.target).ok().flatten();
                    match now {
                        Some(r)
                            if r.batch_id == batch_id
                                && r.persona_next.as_deref() == after["persona_next"].as_str() =>
                        {
                            let mut did = st
                                .messages
                                .seat_next_done(&s.target, batch_id, r.at)
                                .is_ok();
                            let b = &before["row"];
                            if let (true, Some(pb), Some(pat)) =
                                (did, b["batch_id"].as_str(), b["at"].as_i64())
                            {
                                did = st
                                    .messages
                                    .seat_next_set(
                                        &s.target,
                                        b["persona_next"].as_str(),
                                        b["brain_next"].as_str(),
                                        pb,
                                        pat,
                                    )
                                    .is_ok();
                            }
                            settle(did, || undone(s.seq), &s.target, &mut kept_changed);
                        }
                        _ => kept_changed.push(json!({
                            "target": s.target,
                            "why": "這位已經開會換過，或之後又排了別的換法",
                        })),
                    }
                }
            }
            "role_rename" => {
                if state == StepState::Undone {
                    continue;
                }
                let Ok(mut reg) = crate::registry::load(&data) else {
                    settle(false, || false, &s.target, &mut kept_changed);
                    continue;
                };
                if let Some(i) = reg.roles.iter().position(|r| r.uid == s.target) {
                    let now_val = json!({"name": reg.roles[i].name});
                    match merge_plan::back(state, &now_val, &before, &after) {
                        merge_plan::Back::Restore => {
                            let mut did = true;
                            if let Some(old) = before["name"].as_str() {
                                reg.roles[i].name = old.to_string();
                                did = crate::registry::save_for_merge(&data, &reg).is_ok();
                            }
                            settle(did, || undone(s.seq), &s.target, &mut kept_changed);
                        }
                        merge_plan::Back::Changed => {
                            kept_changed.push(json!({"target": s.target, "why": "合併後已改過"}));
                        }
                        merge_plan::Back::Skip => {}
                    }
                }
            }
            "role_assign" => {
                if state == StepState::Done {
                    if undo_assignment(&before, &after) {
                        settle(true, || undone(s.seq), &s.target, &mut kept_changed);
                    } else {
                        kept_changed.push(json!({
                            "target": s.target,
                            "why": "這個指派合併後改過，沒有動它",
                        }));
                    }
                }
            }
            "notes_copy" => {
                if state == StepState::Done {
                    if let Some(t) = undo_notes(&data, batch_id, s.seq) {
                        if !t.changed.is_empty() {
                            kept_changed.push(json!({
                                "target": s.target,
                                "why": "複製過去的筆記合併後改過，留在原地",
                                "files": t.changed,
                            }));
                        }
                        settle(true, || undone(s.seq), &s.target, &mut kept_changed);
                    } else {
                        // the manifest or the brain folder could not be read: nothing taken back
                        settle(false, || undone(s.seq), &s.target, &mut kept_changed);
                    }
                }
            }
            _ => {}
        }
    }
    Ok(kept_changed)
}

/// L1: a put-back counts only when the store took it (`did`) and the step was then marked
/// undone; otherwise the target is listed as kept, so the batch ends `partial_undo`.
fn settle(did: bool, marked: impl FnOnce() -> bool, target: &str, kept: &mut Vec<Value>) {
    if did && marked() {
        return;
    }
    kept.push(json!({"target": target, "why": "這一步還原時寫入失敗，沒有倒回"}));
}

/// M2: move one assignment back from the kept role to the merged one; `false` when it was
/// changed since (the merged role's row is back already counts as done).
fn undo_assignment(before: &Value, after: &Value) -> bool {
    let Some(ws) = crate::assign::workspace_db() else {
        return false;
    };
    let (Some(node), Some(seat), Some(old), Some(keep)) = (
        before["node"].as_str(),
        before["seat"].as_str(),
        before["role"].as_str(),
        after["role"].as_str(),
    ) else {
        return false;
    };
    let Ok(rows) = ws.list(Some(node), Some(seat)) else {
        return false;
    };
    let has = |r: &str| rows.iter().any(|x| x.role == r);
    if has(old) {
        return true;
    }
    let keep_was_there = before["keep_was_there"].as_bool().unwrap_or(true);
    if !has(keep) && !keep_was_there {
        return false;
    }
    let keep_row = rows.iter().find(|x| x.role == keep).cloned();
    // the kept role's row first (the node's limit counts rows), then the merged one's back
    if !keep_was_there && ws.remove(node, seat, keep).is_err() {
        return false;
    }
    let back = ws.add(aib_store::AssignRow {
        node_id: node.to_string(),
        seat_id: seat.to_string(),
        role: old.to_string(),
        created: before["created"].as_str().unwrap_or_default().to_string(),
        by_key: before["by_key"].as_str().unwrap_or_default().to_string(),
    });
    if matches!(back, Ok(aib_store::Added::New | aib_store::Added::Exists)) {
        return true;
    }
    if let (false, Some(k)) = (keep_was_there, keep_row) {
        let _ = ws.add(k);
    }
    false
}

/// `merge_log {limit?, batch_id?}`.
pub async fn log(st: AppState, body: Value) -> Response {
    if let Err(r) = only_keys(&body, &["type", "limit", "batch_id", "idem_key"]) {
        return *r;
    }
    let limit = body
        .get("limit")
        .and_then(Value::as_u64)
        .map_or(20, |n| u32::try_from(n.min(100)).unwrap_or(20));
    let batch_id = body
        .get("batch_id")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let st2 = st.clone();
    let res = tokio::task::spawn_blocking(move || -> Result<Value, Box<Response>> {
        let batches = match batch_id {
            Some(id) => {
                let Some(b) = st2.messages.merge_batch(&id).map_err(|_| {
                    Box::new(err(StatusCode::INTERNAL_SERVER_ERROR, "store", "讀取失敗"))
                })?
                else {
                    return Err(Box::new(err(
                        StatusCode::NOT_FOUND,
                        "batch_unknown",
                        "找不到這一批合併紀錄",
                    )));
                };
                vec![b]
            }
            None => st2.messages.merge_batches(None, limit).map_err(|_| {
                Box::new(err(StatusCode::INTERNAL_SERVER_ERROR, "store", "讀取失敗"))
            })?,
        };
        let now = now_i64();
        let mut out = Vec::with_capacity(batches.len());
        for b in batches {
            let steps = st2.messages.merge_steps(&b.batch_id).unwrap_or_default();
            let can_undo = b.state == "done" && b.keep_until.is_none_or(|k| now <= k);
            let steps_json: Vec<Value> = steps
                .into_iter()
                .map(|s| {
                    json!({
                        "seq": s.seq,
                        "op": s.op,
                        "target": s.target,
                        "state": s.state,
                    })
                })
                .collect();
            out.push(json!({
                "batch_id": b.batch_id,
                "kind": b.kind,
                "by_key": b.by_key,
                "at": b.at,
                "state": b.state,
                "keep_until": b.keep_until,
                "can_undo": can_undo,
                "steps": steps_json,
            }));
        }
        Ok(json!({"batches": out}))
    })
    .await;
    match res {
        Ok(Ok(v)) => (StatusCode::OK, Json(v)).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "internal", "查詢失敗"),
    }
}

/// `merge_undo {batch_id, ticket?}`.
pub async fn undo(st: AppState, body: Value, by: String) -> Response {
    if let Err(r) = only_keys(&body, &["type", "batch_id", "ticket", "idem_key"]) {
        return *r;
    }
    let Some(batch_id) = body
        .get("batch_id")
        .and_then(Value::as_str)
        .map(str::to_owned)
    else {
        return bad("batch_id", "請指定 batch_id");
    };
    let ticket = body
        .get("ticket")
        .and_then(Value::as_str)
        .map(str::to_owned);

    let batch = match st.messages.merge_batch(&batch_id) {
        Ok(Some(b)) => b,
        Ok(None) => {
            return err(
                StatusCode::NOT_FOUND,
                "batch_unknown",
                "找不到這一批合併紀錄",
            )
        }
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "store", "讀取失敗"),
    };
    if batch.state != "done" {
        return err(StatusCode::CONFLICT, "batch_state", "這一批目前不能還原");
    }
    let now = now_i64();
    if batch.keep_until.is_some_and(|k| now > k) {
        return err(StatusCode::CONFLICT, "undo_expired", "已經過了還原期限");
    }

    if merge_plan::needs_owner(&batch.kind) {
        let plan: Value = serde_json::from_str(&batch.plan_json).unwrap_or(Value::Null);
        let groups_len = plan["groups"].as_array().map_or(0, Vec::len);
        let (seats_next_open, days) = count_seats_and_days(&plan);
        let Some(words) = merge_plan::window_text(
            &batch.kind,
            true,
            groups_len,
            seats_next_open,
            days,
            &batch.plan_sha,
        ) else {
            return bad("window_too_long", "驗證視窗文字過長");
        };
        let op = if batch.kind == "persona" {
            OP_PERSONA
        } else {
            OP_BRAIN
        };
        let ask = crate::owner_ok::Ask {
            category: aib_approve::category::Category::Command,
            operation: op,
            resource: words, // M1 undo
            params: serde_json::to_vec(&json!({"batch_id": batch.batch_id})).unwrap_or_default(),
            by: by.clone(),
        };
        match ticket {
            None => return crate::repos::asked(&ask),
            Some(t) => {
                if !crate::owner_ok::approved_for(&t, &ask) {
                    return crate::repos::not_approved();
                }
            }
        }
    }

    let st2 = st.clone();
    let res = tokio::task::spawn_blocking(move || execute_undo(&st2, &batch)).await;
    match res {
        Ok(Ok(v)) => (StatusCode::OK, Json(v)).into_response(),
        Ok(Err(r)) => *r,
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "internal", "還原失敗"),
    }
}

#[cfg(test)]
mod tests {
    use super::settle;

    #[test]
    fn a_put_back_counts_only_when_the_store_took_it_and_the_journal_says_undone() {
        let mut kept = Vec::new();
        settle(true, || true, "t1", &mut kept);
        assert!(kept.is_empty());
        settle(false, || true, "t2", &mut kept);
        settle(true, || false, "t3", &mut kept);
        let t: Vec<_> = kept.iter().map(|k| k["target"].clone()).collect();
        assert_eq!(t, vec!["t2", "t3"]);
    }

    use super::*;

    #[test]
    fn clean_name_strips_invisible_format_chars_and_bounds_length() {
        let bidi = "架構師\u{202E}abc";
        assert_eq!(clean_name(bidi).unwrap(), "架構師abc");
        let twenty = "一二三四五六七八九十一二三四五六七八九十";
        assert_eq!(clean_name(twenty).unwrap(), twenty);
        let twenty_one = "一二三四五六七八九十一二三四五六七八九十一";
        assert_eq!(clean_name(twenty_one), Err("name"));
    }

    #[test]
    fn notes_check_counts_files_and_flags_icloud_placeholders() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("a.md"), "hello").unwrap();
        std::fs::write(d.path().join(".b.md.icloud"), "placeholder").unwrap();
        let c = scan_brain_notes(d.path());
        assert_eq!(c.files, 1);
        assert_eq!(c.bytes, 5);
        assert_eq!(c.cloud_placeholders, 1);
        assert!(!c.over_limit);
    }
}
