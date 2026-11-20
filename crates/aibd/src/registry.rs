//! N-ws-open-bind (SPEC-workspace-multiseat §22b, Andy 11-19 04:5x): the roles and second
//! brains a page picks when it adds a seat, each with a uid aibd makes, and the page's
//! `+ 新增` for each list. Persona cards stay where they are (`crate::personas`); a new
//! persona is asked here and needs the owner's fingerprint.
//!
//! Boundary (5d33a CHECK-ws-open-bind-5d33a-1119): only the built-in page in this pairing
//! adds (the caller checks); uids are made here, never taken from a page; a role the person
//! adds carries no permission (only the six built-in uids mean anything to aibd); adding a
//! persona's text or a second brain is a `Command` fingerprint ([`crate::owner_ok`]); a
//! brain's folder is never a string a page sends, only one of the candidates aibd lists.
//!
//! 能看 stage: the list lives in `<data>/registry.json`; brains have no candidates yet
//! (`demo: true`), and every fingerprinted add answers what the owner check answers.

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

use crate::api::AppState;

pub const ROLE_ADD: &str = "role_add";
pub const PERSONA_ADD: &str = "persona_add";
pub const BRAIN_ADD: &str = "brain_add";
pub const RENAME: &str = "registry_rename";

/// The six built-in roles: their uid is the word the assignments already store
/// (`crate::assign::ROLES`), their names and one line each are the page's.
pub const BUILTIN_ROLES: [(&str, &str, &str); 6] = [
    ("author", "作者", "寫這一項的程式或文件"),
    ("reviewer", "審查", "看別人寫的,找出問題"),
    ("verifier", "驗收", "照步驟實際走一遍"),
    ("deployer", "換上", "把審過的版本換上去"),
    ("depends", "相依", "這一項要等他的東西"),
    ("observer", "旁聽", "只看不動手"),
];

const NAME_MAX: usize = 20;
const WHAT_MAX: usize = 80;
const LIST_MAX: usize = 200;
const QUERY_MAX: usize = 50;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Entry {
    pub uid: String,
    pub name: String,
    #[serde(default)]
    pub what: String,
    /// A brain's route key in the 1.x route registry; never served to a page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub route_key: Option<String>,
    #[serde(default)]
    pub added_at: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Registry {
    #[serde(default)]
    pub roles: Vec<Entry>,
    #[serde(default)]
    pub brains: Vec<Entry>,
}

static LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn file(data: &Path) -> PathBuf {
    data.join("registry.json")
}

fn data_dir(st: &AppState) -> PathBuf {
    let p = st.sessions.private_dir();
    p.parent().unwrap_or(p).to_path_buf()
}

pub fn load(data: &Path) -> Result<Registry, String> {
    match std::fs::read(file(data)) {
        Ok(b) => serde_json::from_slice(&b).map_err(|e| format!("registry.json: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Registry::default()),
        Err(e) => Err(format!("registry.json: {e}")),
    }
}

fn save(data: &Path, r: &Registry) -> Result<(), String> {
    let body = serde_json::to_vec_pretty(r).map_err(|e| e.to_string())?;
    let tmp = data.join("registry.json.tmp");
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, file(data)).map_err(|e| e.to_string())
}

/// `<prefix>` + 6 of a-z 0-9 from the OS's random source, not one of `taken`.
pub fn new_uid(
    prefix: &str,
    taken: &[&str],
    fill: impl Fn(&mut [u8]) -> Result<(), getrandom::Error>,
) -> Result<String, String> {
    const ABC: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
    for _ in 0..16 {
        let mut b = [0u8; 6];
        fill(&mut b).map_err(|e| format!("uid: {e}"))?;
        let tail: String = b
            .iter()
            .map(|x| ABC[usize::from(*x) % ABC.len()] as char)
            .collect();
        let uid = format!("{prefix}{tail}");
        if !taken.contains(&uid.as_str()) {
            return Ok(uid);
        }
    }
    Err("uid: no free one in 16 draws".into())
}

/// A name or a line a page typed: control characters out, trimmed, length-checked.
pub fn clean(s: &str, max: usize) -> Option<String> {
    let t: String = s.chars().filter(|c| !c.is_control()).collect();
    let t = t.trim().to_string();
    (!t.is_empty() && t.chars().count() <= max).then_some(t)
}

/// Same names stay apart by a tail of the uid (§22b-3: 同名加尾碼區分).
fn shown(entries: &[(String, String)]) -> Vec<(String, String)> {
    entries
        .iter()
        .map(|(uid, name)| {
            let twins = entries.iter().filter(|(_, n)| n == name).count();
            let tail: String = uid
                .chars()
                .rev()
                .take(4)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect();
            let label = if twins > 1 {
                format!("{name}·{tail}")
            } else {
                name.clone()
            };
            (uid.clone(), label)
        })
        .collect()
}

pub fn roles_body(r: &Registry) -> Value {
    let mut all: Vec<(String, String, String, bool)> = BUILTIN_ROLES
        .iter()
        .map(|(u, n, w)| ((*u).to_string(), (*n).to_string(), (*w).to_string(), true))
        .collect();
    all.extend(
        r.roles
            .iter()
            .map(|e| (e.uid.clone(), e.name.clone(), e.what.clone(), false)),
    );
    let names: Vec<(String, String)> = all
        .iter()
        .map(|(u, n, _, _)| (u.clone(), n.clone()))
        .collect();
    let labels = shown(&names);
    json!({"roles": all.iter().zip(labels).map(|((u, _, w, b), (_, l))| json!({
        "uid": u, "name": l, "what": w, "builtin": b
    })).collect::<Vec<_>>()})
}

pub fn brains_body(r: &Registry, q: Option<&str>) -> Value {
    let q = q.map(str::to_lowercase);
    let names: Vec<(String, String)> = r
        .brains
        .iter()
        .map(|e| (e.uid.clone(), e.name.clone()))
        .collect();
    let labels = shown(&names);
    let list: Vec<Value> = r
        .brains
        .iter()
        .zip(labels)
        .filter(|(e, _)| {
            q.as_deref()
                .is_none_or(|q| e.name.to_lowercase().contains(q) || e.uid.contains(q))
        })
        .take(QUERY_MAX)
        .map(|(e, (_, l))| json!({"uid": e.uid, "name": l, "what": e.what}))
        .collect();
    // 能看: no candidate roots yet, so an empty list is a stand-in the page marks 示意
    json!({"brains": list, "demo": r.brains.is_empty()})
}

fn internal(why: &str) -> Response {
    tracing::warn!(why, "registry");
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(
            json!({"code": "internal", "message": "the registry could not be read or written",
            "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

fn bad(reason: &str, message: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(
            json!({"code": "invalid_request", "reason": reason, "message": message,
            "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

/// `GET /api/v1/roles`
pub async fn roles(State(st): State<AppState>) -> Response {
    match load(&data_dir(&st)) {
        Ok(r) => Json(roles_body(&r)).into_response(),
        Err(e) => internal(&e),
    }
}

#[derive(Deserialize)]
pub struct BrainQuery {
    q: Option<String>,
}

/// `GET /api/v1/brains?q=`
pub async fn brains(State(st): State<AppState>, Query(q): Query<BrainQuery>) -> Response {
    match load(&data_dir(&st)) {
        Ok(r) => Json(brains_body(&r, q.q.as_deref())).into_response(),
        Err(e) => internal(&e),
    }
}

/// A brain uid the page picked ⇒ its route key (what the seat's meta stores), or why not.
pub fn brain_route(data: &Path, uid: &str) -> Result<String, &'static str> {
    let r = load(data).map_err(|_| "registry_unreadable")?;
    r.brains
        .iter()
        .find(|e| e.uid == uid)
        .and_then(|e| e.route_key.clone())
        .ok_or("brain_unknown")
}

/// One second brain a person may register (043dd 05:13: the add form picks one of these,
/// never types a path). 能看 stage: read from `<data>/brain-candidates.json`, a list of
/// `{route_key, title, notes?}` put there for the preview (from `aib newproj --list-brains`);
/// the page sees `cand_` + 8 hex of the route key's sha256 and the title, never the key or
/// a folder. No file: `{candidates: [], demo: true}`.
#[derive(Debug, Clone, Deserialize)]
pub struct Candidate {
    pub route_key: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub notes: Option<u64>,
}

pub fn candidate_id(route_key: &str) -> String {
    use sha2::{Digest, Sha256};
    let d = Sha256::digest(route_key.as_bytes());
    format!(
        "cand_{}",
        d[..4]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    )
}

pub fn candidates(data: &Path) -> Option<Vec<Candidate>> {
    let b = std::fs::read(data.join("brain-candidates.json")).ok()?;
    // a bad row is skipped and logged, not the whole file (043dd 05:59: a string `notes`
    // made every candidate vanish into demo)
    let rows: Vec<Value> = match serde_json::from_slice(&b) {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!(error = %e, "brain-candidates.json is not a list");
            return None;
        }
    };
    let mut v: Vec<Candidate> = rows
        .into_iter()
        .enumerate()
        .filter_map(|(i, r)| match serde_json::from_value::<Candidate>(r) {
            Ok(c) => Some(c),
            Err(e) => {
                tracing::warn!(row = i, error = %e, "brain-candidates.json row skipped");
                None
            }
        })
        .collect();
    v.retain(|c| !c.route_key.is_empty());
    v.truncate(LIST_MAX);
    Some(v)
}

pub fn candidates_body(c: Option<&[Candidate]>) -> Value {
    match c {
        None => json!({"candidates": [], "demo": true}),
        Some(c) => json!({"candidates": c.iter().map(|c| {
            let label = if c.title.is_empty() { candidate_id(&c.route_key) } else { c.title.clone() };
            json!({"id": candidate_id(&c.route_key), "label": label, "notes": c.notes})
        }).collect::<Vec<_>>(), "demo": false}),
    }
}

/// `GET /api/v1/brains/candidates`
pub async fn brain_candidates(State(st): State<AppState>) -> Response {
    Json(candidates_body(candidates(&data_dir(&st)).as_deref())).into_response()
}

/// `brain_add {candidate_id, name}`: the id must be one listed now; then the fingerprint.
pub async fn brain_add(st: AppState, body: Value, by: String) -> Response {
    let id = body
        .get("candidate_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let listed = candidates(&data_dir(&st))
        .unwrap_or_default()
        .iter()
        .any(|c| candidate_id(&c.route_key) == id);
    if !listed {
        return bad(
            "candidate",
            "candidate_id must be one GET /api/v1/brains/candidates lists",
        );
    }
    fingerprinted_add("brain.add", body, by).await
}

pub fn data_of(st: &AppState) -> PathBuf {
    data_dir(st)
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// `role_add {name, what}`: no fingerprint, and the role carries no permission.
pub async fn role_add(st: AppState, body: Value) -> Response {
    let Some(name) = body
        .get("name")
        .and_then(Value::as_str)
        .and_then(|s| clean(s, NAME_MAX))
    else {
        return bad("name", "name must be 1-20 characters");
    };
    let what = body
        .get("what")
        .and_then(Value::as_str)
        .and_then(|s| clean(s, WHAT_MAX))
        .unwrap_or_default();
    let data = data_dir(&st);
    let _g = LOCK.lock().await;
    let mut r = match load(&data) {
        Ok(r) => r,
        Err(e) => return internal(&e),
    };
    if r.roles.len() >= LIST_MAX {
        return bad("full", "the role list is full");
    }
    let mut taken: Vec<&str> = BUILTIN_ROLES.iter().map(|(u, _, _)| *u).collect();
    taken.extend(r.roles.iter().map(|e| e.uid.as_str()));
    let uid = match new_uid("rol_", &taken, getrandom::fill) {
        Ok(u) => u,
        Err(e) => return internal(&e),
    };
    r.roles.push(Entry {
        uid: uid.clone(),
        name,
        what,
        route_key: None,
        added_at: now(),
    });
    if let Err(e) = save(&data, &r) {
        return internal(&e);
    }
    Json(json!({"uid": uid})).into_response()
}

/// `persona_add` / `brain_add`: a `Command` fingerprint first (5d33a CHECK-ws-open-bind);
/// until the owner check is wired every one is refused with its reason.
pub async fn fingerprinted_add(kind: &'static str, body: Value, by: String) -> Response {
    let Some(name) = body
        .get("name")
        .and_then(Value::as_str)
        .and_then(|s| clean(s, NAME_MAX))
    else {
        return bad("name", "name must be 1-20 characters");
    };
    let ask = crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: kind,
        resource: name,
        params: serde_json::to_vec(&body).unwrap_or_default(),
        by,
    };
    match crate::owner_ok::ask(&ask) {
        crate::owner_ok::Asked::Ticket(t) => (
            StatusCode::ACCEPTED,
            Json(json!({"done": false, "ticket": t, "reason": "owner_check_pending"})),
        )
            .into_response(),
        crate::owner_ok::Asked::Unavailable(why) => (
            StatusCode::FORBIDDEN,
            Json(json!({"code": "forbidden", "reason": why,
                "message": "this needs the owner's fingerprint, and it cannot be asked on this machine yet",
                "request_id": crate::guard::request_id()})),
        )
            .into_response(),
    }
}

/// `registry_rename {kind: "role", uid, name}`: a role the person added; built-in ones keep
/// their names. Personas and brains are renamed with their fingerprinted add (能用 stage).
pub async fn rename(st: AppState, body: Value) -> Response {
    if body.get("kind").and_then(Value::as_str) != Some("role") {
        return bad("kind", "only a role the person added is renamed here");
    }
    let Some(uid) = body.get("uid").and_then(Value::as_str).map(str::to_owned) else {
        return bad("uid", "uid is required");
    };
    let Some(name) = body
        .get("name")
        .and_then(Value::as_str)
        .and_then(|s| clean(s, NAME_MAX))
    else {
        return bad("name", "name must be 1-20 characters");
    };
    let data = data_dir(&st);
    let _g = LOCK.lock().await;
    let mut r = match load(&data) {
        Ok(r) => r,
        Err(e) => return internal(&e),
    };
    let Some(e) = r.roles.iter_mut().find(|e| e.uid == uid) else {
        return bad(
            "uid",
            "no such role the person added (built-in roles keep their names)",
        );
    };
    e.name = name;
    if let Err(e) = save(&data, &r) {
        return internal(&e);
    }
    Json(json!({"uid": uid})).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_uid_has_its_prefix_six_characters_and_never_repeats_a_taken_one() {
        let u = new_uid("rol_", &[], getrandom::fill).unwrap();
        assert!(u.starts_with("rol_") && u.len() == 10, "{u}");
        assert!(u[4..]
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()));
        // a source that always gives the same bytes: the first draw is taken, so none is free
        let same = |b: &mut [u8]| {
            b.fill(0);
            Ok(())
        };
        let first = new_uid("brn_", &[], same).unwrap();
        assert_eq!(first, "brn_aaaaaa");
        assert!(new_uid("brn_", &[first.as_str()], same).is_err());
    }

    #[test]
    fn the_six_built_in_roles_come_first_and_twins_are_told_apart() {
        let r = Registry {
            roles: vec![
                Entry {
                    uid: "rol_aaaa11".into(),
                    name: "整合".into(),
                    ..Default::default()
                },
                Entry {
                    uid: "rol_bbbb22".into(),
                    name: "整合".into(),
                    ..Default::default()
                },
            ],
            brains: vec![],
        };
        let b = roles_body(&r);
        let roles = b["roles"].as_array().unwrap();
        assert_eq!(roles.len(), 8);
        assert_eq!(roles[0]["uid"], "author");
        assert_eq!(roles[0]["builtin"], true);
        assert_eq!(roles[6]["name"], "整合·aa11");
        assert_eq!(roles[7]["name"], "整合·bb22");
        assert_eq!(roles[7]["builtin"], false);
    }

    #[test]
    fn a_brain_list_never_shows_where_its_notes_are() {
        let r = Registry {
            roles: vec![],
            brains: vec![Entry {
                uid: "brn_x1y2z3".into(),
                name: "AIB 2.0 核心".into(),
                route_key: Some("proj.aib2-core".into()),
                ..Default::default()
            }],
        };
        let b = brains_body(&r, Some("核心"));
        assert_eq!(b["brains"][0]["uid"], "brn_x1y2z3");
        assert_eq!(b["demo"], false);
        assert!(!b.to_string().contains("proj.aib2-core"));
        assert_eq!(
            brains_body(&r, Some("nothing"))["brains"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
        assert_eq!(brains_body(&Registry::default(), None)["demo"], true);
    }

    #[test]
    fn typed_names_lose_control_characters_and_their_length_is_checked() {
        assert_eq!(clean("  作者\u{0007}二 ", 20).as_deref(), Some("作者二"));
        assert_eq!(clean("   ", 20), None);
        assert_eq!(clean(&"字".repeat(21), 20), None);
    }

    #[test]
    fn a_picked_brain_is_its_route_key_and_an_unknown_one_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let r = Registry {
            roles: vec![],
            brains: vec![Entry {
                uid: "brn_x1y2z3".into(),
                name: "b".into(),
                route_key: Some("proj.aib2-core".into()),
                ..Default::default()
            }],
        };
        save(d.path(), &r).unwrap();
        assert_eq!(
            brain_route(d.path(), "brn_x1y2z3").as_deref(),
            Ok("proj.aib2-core")
        );
        assert_eq!(brain_route(d.path(), "brn_nope00"), Err("brain_unknown"));
        assert_eq!(load(d.path()).unwrap(), r);
    }

    #[test]
    fn candidates_show_an_id_and_a_title_never_the_route_key() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(
            candidates_body(candidates(d.path()).as_deref())["demo"],
            true
        );
        std::fs::write(
            d.path().join("brain-candidates.json"),
            br#"[{"route_key": "proj.aib2-core", "title": "AIB 2.0 core", "notes": 88},
                {"route_key": "", "title": "none"},
                {"route_key": "proj.bad", "title": "bad", "notes": "many"}]"#,
        )
        .unwrap();
        let b = candidates_body(candidates(d.path()).as_deref());
        assert_eq!(b["demo"], false);
        let c = b["candidates"].as_array().unwrap();
        assert_eq!(c.len(), 1);
        assert_eq!(c[0]["id"], candidate_id("proj.aib2-core"));
        assert!(c[0]["id"].as_str().unwrap().starts_with("cand_"));
        assert_eq!(c[0]["label"], "AIB 2.0 core");
        assert!(!b.to_string().contains("proj.aib2-core"));
    }
}
