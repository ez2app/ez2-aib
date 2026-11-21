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
/// never types a path). Listed from `<data>/brain-candidates.json` when that file is there (a list
/// of `{route_key, title, notes?}`, kept for tests and fixed previews), else from the library
/// `brain_root` finds (c12: no path is written in, DECISION-brain-root-c12-7b81e-1120); the page
/// sees `cand_` + 8 hex of the route key's sha256 and the title, never the key or a folder.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
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

/// Where the second-brain library is (c12, Andy 11-19 22:2x: 「這個路徑 ~/Ez2/Vault-uid 也應該是
/// 不寫死的」). The same order as AIB 1.x `configured_vault`, with no guessed folder at the end:
/// `AIBD_BRAIN_ROOT` > `EZ2_VAULT_DIR` > `EZ2_VAULT_ROOT` > the one line of
/// `<home>/.ez2-aib/vault-dir` (home = `AIBD_SEAT_HOME` when absolute, else `HOME`, else
/// `USERPROFILE`). The first one that is set decides: a bad one (not absolute, not a folder) is
/// `Unusable`, never a reason to look further (Codex OPINION-c12-codex-1120).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BrainRoot {
    Unset,
    Unusable(&'static str),
    At(&'static str, PathBuf),
}

pub fn brain_root(lookup: impl Fn(&str) -> Option<String>) -> BrainRoot {
    let checked = |from: &'static str, v: &str| {
        let p = PathBuf::from(v);
        if p.is_absolute() && p.is_dir() {
            BrainRoot::At(from, p)
        } else {
            BrainRoot::Unusable(from)
        }
    };
    for var in ["AIBD_BRAIN_ROOT", "EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"] {
        if let Some(v) = lookup(var).filter(|v| !v.is_empty()) {
            return checked(var, &v);
        }
    }
    let home = lookup(crate::platform::session::SEAT_HOME_ENV)
        .filter(|h| Path::new(h).is_absolute())
        .or_else(|| lookup("HOME").filter(|h| !h.is_empty()))
        .or_else(|| lookup("USERPROFILE").filter(|h| !h.is_empty()));
    let Some(home) = home else {
        return BrainRoot::Unset;
    };
    let line = std::fs::read_to_string(Path::new(&home).join(".ez2-aib").join("vault-dir"))
        .ok()
        .and_then(|t| t.lines().next().map(|l| l.trim().to_string()))
        .filter(|l| !l.is_empty());
    match line {
        None => BrainRoot::Unset,
        Some(l) => match l.strip_prefix("~/") {
            Some(rest) => checked("vault-dir", &Path::new(&home).join(rest).to_string_lossy()),
            None => checked("vault-dir", &l),
        },
    }
}

/// The brains the library's own index lists (`<root>/.ez2/index/folder-id-index.json`): every
/// active folder with a route key (c12 Q1: a route key means it was registered; the kind does
/// not decide). `None` when the index cannot be read.
pub fn index_candidates(root: &Path) -> Option<Vec<Candidate>> {
    let b = std::fs::read(root.join(".ez2").join("index").join("folder-id-index.json")).ok()?;
    let v: Value = serde_json::from_slice(&b).ok()?;
    let mut out: Vec<Candidate> = Vec::new();
    for i in v.get("items")?.as_array()? {
        if i.get("status").and_then(Value::as_str) != Some("active") {
            continue;
        }
        let Some(key) = i
            .get("routeKey")
            .and_then(Value::as_str)
            .filter(|k| !k.is_empty())
        else {
            continue;
        };
        if out.iter().any(|c| c.route_key == key) {
            continue;
        }
        out.push(Candidate {
            route_key: key.to_string(),
            title: i
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            notes: None,
        });
    }
    out.truncate(LIST_MAX);
    Some(out)
}

/// What the candidates come from: the fixed file, the library, or why there are none.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Listing {
    File(Vec<Candidate>),
    Library(Vec<Candidate>),
    Unset,
    Unusable,
}

pub fn listing(data: &Path, lookup: impl Fn(&str) -> Option<String>) -> Listing {
    if let Some(v) = candidates(data) {
        return Listing::File(v);
    }
    match brain_root(lookup) {
        BrainRoot::Unset => Listing::Unset,
        BrainRoot::Unusable(_) => Listing::Unusable,
        BrainRoot::At(_, root) => {
            index_candidates(&root).map_or(Listing::Unusable, Listing::Library)
        }
    }
}

impl Listing {
    pub fn list(&self) -> &[Candidate] {
        match self {
            Listing::File(v) | Listing::Library(v) => v,
            Listing::Unset | Listing::Unusable => &[],
        }
    }
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

/// The body with where the list came from (`root`: file / library / unset / unusable), never the
/// library's path. Unset and unusable are no demo (c12 Q2): the page says the place is missing.
pub fn listing_body(l: &Listing) -> Value {
    let mut b = candidates_body(Some(l.list()));
    b["root"] = json!(match l {
        Listing::File(_) => "file",
        Listing::Library(_) => "library",
        Listing::Unset => "unset",
        Listing::Unusable => "unusable",
    });
    b
}

/// `GET /api/v1/brains/candidates`
pub async fn brain_candidates(State(st): State<AppState>) -> Response {
    Json(listing_body(&listing(&data_dir(&st), |k| {
        std::env::var(k).ok()
    })))
    .into_response()
}

/// `brain_add {candidate_id, name}`: the id must be one listed now; then the fingerprint.
pub async fn brain_add(st: AppState, body: Value, by: String) -> Response {
    let id = body
        .get("candidate_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let listed = listing(&data_dir(&st), |k| std::env::var(k).ok())
        .list()
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
        crate::owner_ok::Asked::Unavailable(why) => {
            let mut v = crate::owner_ok::refusal(why);
            v["request_id"] = json!(crate::guard::request_id());
            (StatusCode::FORBIDDEN, Json(v)).into_response()
        }
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

    fn env_of(pairs: Vec<(&'static str, String)>) -> impl Fn(&str) -> Option<String> {
        move |k: &str| pairs.iter().find(|(n, _)| *n == k).map(|(_, v)| v.clone())
    }

    #[test]
    fn the_brain_library_is_found_in_order_and_never_guessed() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        let s = |p: &Path| p.to_string_lossy().into_owned();
        // nothing set: unset, no folder guessed
        assert_eq!(
            brain_root(env_of(vec![])),
            BrainRoot::Unset,
            "reason: guessed_root"
        );
        // a home with no vault-dir line: still unset (no ~/Ez2/Vault-uid, no ~/Desktop/Obsidian/Ez2)
        assert_eq!(
            brain_root(env_of(vec![("HOME", s(a.path()))])),
            BrainRoot::Unset,
            "reason: guessed_root_under_home"
        );
        // AIBD_BRAIN_ROOT > EZ2_VAULT_DIR > EZ2_VAULT_ROOT
        assert_eq!(
            brain_root(env_of(vec![
                ("AIBD_BRAIN_ROOT", s(a.path())),
                ("EZ2_VAULT_DIR", s(b.path()))
            ])),
            BrainRoot::At("AIBD_BRAIN_ROOT", a.path().to_path_buf()),
            "reason: brain_root_env_not_first"
        );
        assert_eq!(
            brain_root(env_of(vec![
                ("EZ2_VAULT_DIR", s(a.path())),
                ("EZ2_VAULT_ROOT", s(b.path()))
            ])),
            BrainRoot::At("EZ2_VAULT_DIR", a.path().to_path_buf())
        );
        assert_eq!(
            brain_root(env_of(vec![("EZ2_VAULT_ROOT", s(b.path()))])),
            BrainRoot::At("EZ2_VAULT_ROOT", b.path().to_path_buf())
        );
        // a bad first one stops there, it never falls through to the next
        assert_eq!(
            brain_root(env_of(vec![
                ("AIBD_BRAIN_ROOT", s(&a.path().join("missing"))),
                ("EZ2_VAULT_DIR", s(b.path()))
            ])),
            BrainRoot::Unusable("AIBD_BRAIN_ROOT"),
            "reason: bad_root_fell_through"
        );
        assert_eq!(
            brain_root(env_of(vec![("EZ2_VAULT_DIR", "rel/vault".to_string())])),
            BrainRoot::Unusable("EZ2_VAULT_DIR"),
            "reason: relative_root_taken"
        );
        // the vault-dir line under the real home (the seat home first), `~/` from that home
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(home.path().join(".ez2-aib")).unwrap();
        std::fs::create_dir_all(home.path().join("Lib")).unwrap();
        std::fs::write(home.path().join(".ez2-aib").join("vault-dir"), "~/Lib\n").unwrap();
        assert_eq!(
            brain_root(env_of(vec![
                ("HOME", s(&a.path().join("scratch"))),
                ("AIBD_SEAT_HOME", s(home.path()))
            ])),
            BrainRoot::At("vault-dir", home.path().join("Lib")),
            "reason: vault_dir_not_read"
        );
    }

    #[test]
    fn the_library_lists_active_routed_folders_and_never_shows_a_key() {
        let root = tempfile::tempdir().unwrap();
        let idx = root.path().join(".ez2").join("index");
        std::fs::create_dir_all(&idx).unwrap();
        std::fs::write(
            idx.join("folder-id-index.json"),
            br#"{"items": [
                {"routeKey": "proj.core", "title": "Core", "kind": "group", "status": "active"},
                {"routeKey": "proj.sub", "title": "Sub", "kind": "sub", "status": "active"},
                {"routeKey": "proj.old", "title": "Old", "kind": "group", "status": "archived"},
                {"routeKey": "", "title": "Plain", "kind": "sub", "status": "active"},
                {"routeKey": "proj.core", "title": "Core again", "kind": "route", "status": "active"}
            ]}"#,
        )
        .unwrap();
        let keys: Vec<String> = index_candidates(root.path())
            .unwrap()
            .into_iter()
            .map(|c| c.route_key)
            .collect();
        assert_eq!(keys, vec!["proj.core", "proj.sub"], "reason: index_filter");
        let data = tempfile::tempdir().unwrap();
        let rs = root.path().to_string_lossy().into_owned();
        let l = listing(data.path(), env_of(vec![("AIBD_BRAIN_ROOT", rs.clone())]));
        let b = listing_body(&l);
        assert_eq!(b["root"], "library");
        assert_eq!(b["demo"], false);
        assert_eq!(b["candidates"].as_array().unwrap().len(), 2);
        assert!(!b.to_string().contains("proj.core"));
        assert!(!b.to_string().contains(&rs), "reason: root_path_shown");
        // the fixed file still wins
        std::fs::write(
            data.path().join("brain-candidates.json"),
            br#"[{"route_key": "proj.fixed", "title": "Fixed"}]"#,
        )
        .unwrap();
        assert_eq!(
            listing_body(&listing(data.path(), env_of(vec![("AIBD_BRAIN_ROOT", rs)])))["root"],
            "file"
        );
        // none set / a bad one: no demo, says which
        let empty = tempfile::tempdir().unwrap();
        let u = listing_body(&listing(empty.path(), env_of(vec![])));
        assert_eq!(
            (u["root"].clone(), u["demo"].clone()),
            (json!("unset"), json!(false)),
            "reason: unset_is_demo"
        );
        let bad = listing_body(&listing(
            empty.path(),
            env_of(vec![("EZ2_VAULT_DIR", "rel".to_string())]),
        ));
        assert_eq!(bad["root"], "unusable");
        // a library folder with no index is unusable, not empty
        let noidx = tempfile::tempdir().unwrap();
        assert_eq!(
            listing(
                empty.path(),
                env_of(vec![(
                    "AIBD_BRAIN_ROOT",
                    noidx.path().to_string_lossy().into_owned()
                )])
            ),
            Listing::Unusable
        );
    }
}
