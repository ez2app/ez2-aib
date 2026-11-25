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
const NOTES_MAX_FILES: usize = 2000;
const NOTES_MAX_BYTES: u64 = 200 * 1024 * 1024;
const NOTE_MAX_FILE_BYTES: u64 = 5 * 1024 * 1024;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct NotesCheck {
    pub files: usize,
    pub bytes: u64,
    pub cloud_placeholders: usize,
    pub over_limit: bool,
}

fn is_cloud_placeholder(name: &str) -> bool {
    name.starts_with('.') && name.ends_with(".icloud")
}

/// 8c (DESIGN-merge §4b-2): scan a brain folder without following symlinks, counting regular
/// files, total bytes, and iCloud placeholders.
pub fn scan_brain_notes(root: &Path) -> NotesCheck {
    let mut check = NotesCheck::default();
    let mut dirs = vec![root.to_path_buf()];
    while let Some(d) = dirs.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in entries.flatten() {
            let p = e.path();
            let Ok(meta) = std::fs::symlink_metadata(&p) else {
                continue;
            };
            if meta.file_type().is_symlink() {
                continue;
            }
            let name = e.file_name().to_string_lossy().into_owned();
            if is_cloud_placeholder(&name) {
                check.cloud_placeholders += 1;
                continue;
            }
            if meta.is_dir() {
                dirs.push(p);
            } else if meta.is_file() {
                let len = meta.len();
                check.files += 1;
                check.bytes = check.bytes.saturating_add(len);
                if len > NOTE_MAX_FILE_BYTES
                    || check.files > NOTES_MAX_FILES
                    || check.bytes > NOTES_MAX_BYTES
                {
                    check.over_limit = true;
                }
            }
        }
    }
    check
}

use std::path::Path;

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
        let Some(_words) =
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
            resource: plan_sha.clone(),
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

    let mut seq: i64 = 0;
    let data = crate::registry::data_of(st);

    for g in groups {
        let keep = g["keep"].as_str().unwrap_or_default();
        let merge_list = g["merge"].as_array().map_or(&[][..], Vec::as_slice);
        let merged_name = g["name"].as_str().unwrap_or_default();

        for m in merge_list {
            let old_uid = m.as_str().unwrap_or_default();
            seq += 1;
            let before = json!({"archived": false});
            let after = json!({"archived": true, "keep": keep});
            let _ = st.messages.merge_step_pending(MergeStep {
                batch_id: batch_id.clone(),
                seq,
                op: "archive".to_string(),
                target: old_uid.to_string(),
                before_json: before.to_string(),
                planned_after_json: after.to_string(),
                state: "pending".to_string(),
            });
            if st
                .messages
                .archive_add(ArchivedItem {
                    kind: kind.to_string(),
                    uid: old_uid.to_string(),
                    archived_at: at,
                    keep_until,
                    reason: "merge".to_string(),
                    batch_id: Some(batch_id.clone()),
                    copy_dir: None,
                    sha256: None,
                    restored_at: None,
                })
                .is_err()
            {
                let _ = st.messages.merge_batch_state(&batch_id, "failed");
                return Err(Box::new(err(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "merge_failed",
                    "封存失敗，已中止",
                )));
            }
            let _ = st.messages.merge_step_state(&batch_id, seq, "done");

            if kind == "persona" {
                seq += 1;
                let _ = st.messages.merge_step_pending(MergeStep {
                    batch_id: batch_id.clone(),
                    seq,
                    op: "alias".to_string(),
                    target: old_uid.to_string(),
                    before_json: json!({"alias": Value::Null}).to_string(),
                    planned_after_json: json!({"alias": keep}).to_string(),
                    state: "pending".to_string(),
                });
                if st.messages.alias_set(old_uid, keep, &batch_id, at).is_err() {
                    let _ = st.messages.merge_batch_state(&batch_id, "failed");
                    return Err(Box::new(err(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "merge_failed",
                        "別名寫入失敗",
                    )));
                }
                let _ = st.messages.merge_step_state(&batch_id, seq, "done");
            }
        }

        if kind == "persona" {
            if let Some(seat_to) = g.get("seat_to").and_then(Value::as_object) {
                for (sid, target) in seat_to {
                    let target_uid = target.as_str().unwrap_or(keep);
                    seq += 1;
                    let _ = st.messages.merge_step_pending(MergeStep {
                        batch_id: batch_id.clone(),
                        seq,
                        op: "seat_next_persona".to_string(),
                        target: sid.clone(),
                        before_json: json!({"persona_next": Value::Null}).to_string(),
                        planned_after_json: json!({"persona_next": target_uid}).to_string(),
                        state: "pending".to_string(),
                    });
                    if st
                        .messages
                        .seat_next_set(sid, Some(target_uid), None, &batch_id, at)
                        .is_err()
                    {
                        let _ = st.messages.merge_batch_state(&batch_id, "failed");
                        return Err(Box::new(err(
                            StatusCode::INTERNAL_SERVER_ERROR,
                            "merge_failed",
                            "寫入下次開切換失敗",
                        )));
                    }
                    let _ = st.messages.merge_step_state(&batch_id, seq, "done");
                }
            }
        } else if kind == "role" {
            if let Ok(mut reg) = crate::registry::load(&data) {
                if let Some(entry) = reg.roles.iter_mut().find(|r| r.uid == keep) {
                    if !merged_name.is_empty() && entry.name != merged_name {
                        let old_name = entry.name.clone();
                        seq += 1;
                        let _ = st.messages.merge_step_pending(MergeStep {
                            batch_id: batch_id.clone(),
                            seq,
                            op: "role_rename".to_string(),
                            target: keep.to_string(),
                            before_json: json!({"name": old_name}).to_string(),
                            planned_after_json: json!({"name": merged_name}).to_string(),
                            state: "pending".to_string(),
                        });
                        entry.name = merged_name.to_string();
                        let _ = crate::registry::save_for_merge(&data, &reg);
                        let _ = st.messages.merge_step_state(&batch_id, seq, "done");
                    }
                }
            }
            if let Some(ws) = crate::assign::workspace_db() {
                if let Ok(rows) = ws.list(None, None) {
                    let merge_set: HashSet<&str> =
                        merge_list.iter().filter_map(Value::as_str).collect();
                    for row in rows {
                        if merge_set.contains(row.role.as_str()) {
                            let _ = ws.remove(&row.node_id, &row.seat_id, &row.role);
                            let _ = ws.add(aib_store::AssignRow {
                                node_id: row.node_id,
                                seat_id: row.seat_id,
                                role: keep.to_string(),
                                created: row.created,
                                by_key: row.by_key,
                            });
                        }
                    }
                }
            }
        }
    }

    let _ = st.messages.merge_batch_state(&batch_id, "done");
    Ok(json!({
        "batch_id": batch_id,
        "state": "done",
    }))
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
        let Some(_words) = merge_plan::window_text(
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
            resource: batch.batch_id.clone(),
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

fn execute_undo(st: &AppState, batch: &MergeBatch) -> Result<Value, Box<Response>> {
    let steps = st.messages.merge_steps(&batch.batch_id).map_err(|_| {
        Box::new(err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "store",
            "步驟讀取失敗",
        ))
    })?;
    let at = now_i64();
    let data = crate::registry::data_of(st);
    let mut kept_changed: Vec<Value> = Vec::new();

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
                    .archived(&batch.kind, &s.target)
                    .ok()
                    .flatten()
                    .is_some();
                let now_val = json!({"archived": is_archived, "keep": after["keep"]});
                if merge_plan::back(state, &now_val, &before, &after) == merge_plan::Back::Restore {
                    let _ = st.messages.archive_restore(&batch.kind, &s.target, at);
                    let _ = st
                        .messages
                        .merge_step_state(&batch.batch_id, s.seq, "undone");
                }
            }
            "alias" => {
                let _ = st.messages.alias_remove_batch(&batch.batch_id);
                let _ = st
                    .messages
                    .merge_step_state(&batch.batch_id, s.seq, "undone");
            }
            "seat_next_persona" => {
                let _ = st.messages.seat_next_clear_batch(&batch.batch_id);
                let _ = st
                    .messages
                    .merge_step_state(&batch.batch_id, s.seq, "undone");
            }
            "role_rename" => {
                if let Ok(mut reg) = crate::registry::load(&data) {
                    if let Some(entry) = reg.roles.iter_mut().find(|r| r.uid == s.target) {
                        let now_val = json!({"name": entry.name});
                        match merge_plan::back(state, &now_val, &before, &after) {
                            merge_plan::Back::Restore => {
                                if let Some(old) = before["name"].as_str() {
                                    entry.name = old.to_string();
                                    let _ = crate::registry::save_for_merge(&data, &reg);
                                }
                                let _ =
                                    st.messages
                                        .merge_step_state(&batch.batch_id, s.seq, "undone");
                            }
                            merge_plan::Back::Changed => {
                                kept_changed
                                    .push(json!({"target": s.target, "why": "合併後已改過"}));
                            }
                            merge_plan::Back::Skip => {}
                        }
                    }
                }
            }
            _ => {}
        }
    }

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

#[cfg(test)]
mod tests {
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
