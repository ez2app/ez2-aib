//! N-workspace (ORDER-workspace-519d0-1118, 7b81e row; SPEC-workspace-multiseat §3 / §9 / §10):
//! three reads the workspace page needs and aibd did not have.
//!
//! - `GET /api/v1/version`: what this aibd can do, so a page shows a provider it cannot open
//!   as present but not pressable (§1.2) and finds a route before calling it. Only grows.
//! - `GET /api/v1/host`: the bottom-left host box (§9): memory and the disk aibd keeps its
//!   data on, CPU count, sessions running and queued. Read the platform's own way through
//!   sysinfo (already aibd's, `platform::physical_memory`); a value that cannot be read is
//!   `null`, never 0. No path is named.
//! - `GET /api/v1/tree?root=&depth=`: the progress tree, read only (§5 step 1: "左欄唯讀接現成進度樹
//!   匯出"), from `<data>/tree.json` (the cabin-plan `data.json` shape: `{asof, nodes:[{id,
//!   parent, kind, title, status, …}]}`). Nothing here writes it; with no file the answer is
//!   `source: "none"` and no nodes. `need` (the person's own words) is given to the built-in
//!   page only, like a thread's body (S89-talk-2a): a user folder page gets its `len` and
//!   `sha256`.

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

/// The tree file in the data folder.
pub const TREE_FILE: &str = "tree.json";
/// A tree file larger than this is not read (today's is about 1.2 MB).
pub const TREE_MAX_BYTES: u64 = 16 * 1024 * 1024;
/// The deepest `depth` taken; deeper asks get this.
pub const DEPTH_MAX: usize = 64;

/// The data folder, set once by the server at start (like `static_files::set_user_dir`).
static DATA_DIR: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

pub fn set_data_dir(dir: PathBuf) {
    let _ = DATA_DIR.set(dir);
}

/// `<data>/<name>`, once the server set the data folder.
pub fn data_file(name: &str) -> Option<PathBuf> {
    DATA_DIR.get().map(|d| d.join(name))
}

/// N-workspace-c4 C (519d0 11-18 20:07; Andy 20:0x「這應該是 rust 程式化的」): the tree file aibd
/// reads and imports, named by `AIBD_TREE_SOURCE` (aib-plan's own `cabin-plan/data.json`, so
/// nobody copies it into `<data>/tree.json` by hand any more); unset, `<data>/tree.json`.
pub const TREE_SOURCE_ENV: &str = "AIBD_TREE_SOURCE";

/// The tree file for the data folder `dir` (see [`TREE_SOURCE_ENV`]).
pub fn tree_source_in(dir: &Path) -> PathBuf {
    std::env::var_os(TREE_SOURCE_ENV)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| dir.join(TREE_FILE))
}

/// The tree file, once the server set the data folder (see [`TREE_SOURCE_ENV`]).
pub fn tree_path() -> Option<PathBuf> {
    DATA_DIR.get().map(|d| tree_source_in(d))
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

// ---------------------------------------------------------------- version

/// Whether a seat of this brand can open on this machine, and if not why: aibd drives it
/// (`not_built`), the session policy lists a program for it (`not_set_up`), and that program
/// is there (`not_installed`). Only the code: never the path the policy names.
pub fn brand_ready(
    brand: &str,
    policy: &crate::allowlist::SessionPolicy,
) -> Result<(), &'static str> {
    if !aib_seatopen::PIPE_PROVIDERS.contains(&brand) {
        return Err("not_built");
    }
    let p = policy
        .programs
        .iter()
        .find(|p| p.name == brand)
        .ok_or("not_set_up")?;
    if p.path.is_file() {
        Ok(())
    } else {
        Err("not_installed")
    }
}

/// The `GET /api/v1/version` answer. Brands: AIB 2.0's only (§22), each with whether it
/// opens here and in which modes; the 1.x names of this machine's old set-up are not listed.
/// 519d0 11-19 05:0x / 0eed7 VERIFY-brandhome M1: a brand whose program is there but has no
/// login under the HOME its seats get answers `not_logged_in` (the page has the words). Only
/// whether the login file is there is looked at, never what is in it; Claude on macOS keeps
/// the token in the keychain, so `~/.claude.json` naming an account counts (its key only).
pub fn logged_in(
    brand: &str,
    home: &std::path::Path,
    codex_home: Option<&std::path::Path>,
) -> bool {
    match brand {
        "claude" => {
            home.join(".claude").join(".credentials.json").is_file()
                || std::fs::read(home.join(".claude.json"))
                    .ok()
                    .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                    .is_some_and(|v| v.get("oauthAccount").is_some_and(Value::is_object))
        }
        "codex" => codex_home
            .map(std::path::Path::to_path_buf)
            .unwrap_or_else(|| home.join(".codex"))
            .join("auth.json")
            .is_file(),
        "grok" => home.join(".grok").join("auth.json").is_file(),
        "copilot" => home.join(".copilot").join("config.json").is_file(),
        _ => true,
    }
}

/// The HOME a seat's provider runs with: the real one handed over inside a preview
/// ([`crate::platform::session::SEAT_HOME_ENV`]), else this aibd's own.
pub fn seat_login_home() -> Option<std::path::PathBuf> {
    let handed = crate::preview::is_preview()
        .then(|| std::env::var(crate::platform::session::SEAT_HOME_ENV).ok())
        .flatten()
        .map(std::path::PathBuf::from)
        .filter(|p| p.is_absolute());
    handed.or_else(|| crate::seats::home_dir(|k| std::env::var(k).ok()))
}

fn login_now(brand: &str) -> bool {
    let codex_home = std::env::var("CODEX_HOME")
        .ok()
        .map(std::path::PathBuf::from);
    seat_login_home().is_some_and(|h| logged_in(brand, &h, codex_home.as_deref()))
}

pub fn version_body(dgrs_input: bool, policy: &crate::allowlist::SessionPolicy) -> Value {
    let mut v = version_body_with(dgrs_input, policy, &login_now);
    let note = login_home_note(
        crate::preview::is_preview(),
        std::env::var(crate::platform::session::SEAT_HOME_ENV)
            .ok()
            .as_deref(),
    );
    note_login_home(&mut v, &note);
    v
}

/// 519d0 N-pv-agree-drive (Andy at 47790): whose HOME a `not_logged_in` is about. Inside a
/// preview the seats get the handed real HOME (`handed`); otherwise this aibd's own HOME
/// (`own`), and an `AIBD_SEAT_HOME` it was started with is not used (`seat_home_ignored`), so
/// the page can say "this aibd's own HOME has no login" rather than "this machine".
pub fn login_home_note(preview: bool, seat_home: Option<&str>) -> Value {
    let given = seat_home.is_some_and(|h| std::path::Path::new(h).is_absolute());
    json!({
        "login_home": if preview && given { "handed" } else { "own" },
        "seat_home_ignored": !preview && seat_home.is_some(),
    })
}

/// Adds [`login_home_note`] to every provider row that says `not_logged_in`.
pub fn note_login_home(v: &mut Value, note: &Value) {
    let Some(rows) = v["capabilities"]["providers"].as_array_mut() else {
        return;
    };
    for r in rows.iter_mut().filter(|r| r["reason"] == "not_logged_in") {
        for k in ["login_home", "seat_home_ignored"] {
            r[k] = note[k].clone();
        }
    }
}

pub fn version_body_with(
    dgrs_input: bool,
    policy: &crate::allowlist::SessionPolicy,
    login: &dyn Fn(&str) -> bool,
) -> Value {
    let providers: Vec<Value> = aib_seatopen::AIB2_BRANDS
        .iter()
        .map(|p| {
            // S89-live-2: and in which modes (codex: safe only)
            let modes = aib_seatopen::pipe_modes(p);
            match brand_ready(p, policy).and_then(|()| {
                if login(p) {
                    Ok(())
                } else {
                    Err("not_logged_in")
                }
            }) {
                Ok(()) => json!({"id": p, "open": true, "modes": modes}),
                Err(why) => json!({"id": p, "open": false, "modes": modes, "reason": why}),
            }
        })
        .collect();
    json!({
        "version": env!("CARGO_PKG_VERSION"),
        "api_version": crate::api::API_VERSION,
        "preview": crate::preview::is_preview(),
        "preview_name": crate::preview::preview_name(),
        "capabilities": {
            "providers": providers,
            "modes": aib_seatopen::SEAT_MODES,
            // which modes a page may type into (seat_input / message_send)
            "input_modes": if dgrs_input { json!(["safe", "work", "dgrs"]) } else { json!(["safe", "work"]) },
            "routes": crate::guard::ROUTES,
            // 1a93b GAP §8: the act types a confirmed page may send (crate::seat_input::PAGE_ACTS)
            "acts": crate::seat_input::PAGE_ACTS,
        },
        // 519d0 11-21 06:45 point 1: what an open that names no model runs on, per brand
        "default_models": crate::default_model::now(),
        // N-workspace-c3: times are shown in this zone, not the machine's (043dd: display_tz)
        "display_tz": crate::display::display_tz(),
    })
}

pub async fn version(State(st): State<AppState>) -> Response {
    let mut body = version_body(crate::seat_input::dgrs_input_on(), &st.session_policy);
    // N-web-worktree: the registered repos a dgrs seat may be opened with, names only
    let p = st.sessions.private_dir();
    body["repos"] = Value::Array(crate::repos::names(p.parent().unwrap_or(p)));
    // 043dd 9ea086eb81: the seat modes a repo may be given to
    body["repo_modes"] = json!(crate::repos::REPO_MODES);
    // 043dd (519d0 自測 r3 M3): sessions running at once, so the page says a new seat will
    // queue before it is opened
    body["max_sessions"] = json!(st.sessions.max_running());
    Json(body).into_response()
}

// ---------------------------------------------------------------- personas

/// `GET /api/v1/personas`: the cards a seat can be opened with, `{personas: [{uid,
/// display_name}]}` in the cards file's order (043dd: the add-a-seat picker had only the
/// uids of seats already open). The name only: never where the card text is, nor its hash.
/// Cards that cannot be read: 503 `persona_source_unavailable`, as when a seat is opened.
pub async fn personas(State(st): State<AppState>) -> Response {
    let (src, db) = (st.personas.clone(), st.messages.clone());
    match tokio::task::spawn_blocking(move || {
        src.cards()
            .map(|c| (c, crate::persona_edit::archived_uids(&db, "persona")))
    })
    .await
    {
        // f163c regarch L3: the archived ones unread ⇒ no list, never one showing them live
        Ok(Ok((_, None))) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"code": "internal", "reason": "archived_unreadable",
            "message": "the archived personas cannot be read", "request_id": request_id()})),
        )
            .into_response(),
        // A1: an archived card is listed apart (the page's 已封存 with ↺), not in the picker
        Ok(Ok((cards, Some(gone)))) => Json(json!({
            "personas": cards
                .iter()
                .filter(|c| !gone.contains(&c.uid))
                .map(|c| json!({"uid": c.uid, "display_name": c.name}))
                .collect::<Vec<_>>(),
            "archived": cards
                .iter()
                .filter(|a| gone.contains(&a.uid))
                .map(|a| json!({"uid": a.uid, "display_name": a.name}))
                .collect::<Vec<_>>(),
        }))
        .into_response(),
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "persona cards unreadable");
            crate::sessions::ApiError::new(
                aib_core::ErrorCode::PersonaSourceUnavailable,
                "the persona cards cannot be read",
            )
            .into_response()
        }
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"code": "internal",
            "message": "persona cards not read", "request_id": request_id()})),
        )
            .into_response(),
    }
}

// ---------------------------------------------------------------- host

/// Bytes of memory: total and available now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Memory {
    pub total: u64,
    pub available: u64,
}

/// Bytes of the disk the data folder is on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Disk {
    pub total: u64,
    pub available: u64,
}

/// Memory now; `None` when it cannot be read (a total of 0 is not a reading).
pub fn memory() -> Option<Memory> {
    let mut s = sysinfo::System::new().ok()?;
    s.refresh_memory();
    let total = s.total_memory();
    (total > 0).then(|| Memory {
        total,
        available: s.available_memory().min(total),
    })
}

/// The disk `p` is on (`None` when it cannot be read).
pub fn disk_of(p: &Path) -> Option<Disk> {
    let p = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    disk_at(&p).filter(|d| d.total > 0)
}

/// Unix: ask the filesystem `p` is on, and only it (statvfs). Listing every mount (sysinfo
/// `Disks`) waited on a sleeping network mount for 30 s and more, so `GET /api/v1/host` never
/// answered (lin's OrbStack NFS mount; tests/workspace.rs:86 red 3 runs in 5 at 865edcb6).
#[cfg(unix)]
#[allow(clippy::unnecessary_cast)] // statvfs field types differ by platform (u32 on macOS)
fn disk_at(p: &Path) -> Option<Disk> {
    let s = nix::sys::statvfs::statvfs(p).ok()?;
    let unit = s.fragment_size() as u64;
    let total = (s.blocks() as u64).saturating_mul(unit);
    let available = (s.blocks_available() as u64).saturating_mul(unit);
    Some(Disk {
        total,
        available: available.min(total),
    })
}

/// Windows: the mounted disk whose mount point is the longest prefix of `p` (aib-slice
/// `gc::free_bytes`, 5d33a slice 2 N1).
#[cfg(windows)]
fn disk_at(p: &Path) -> Option<Disk> {
    // Windows canonical paths start `\\?\`, which no mount point (`C:\`) is a prefix of
    let p = match p.to_str().and_then(|s| s.strip_prefix(r"\\?\")) {
        Some(s) => PathBuf::from(s),
        None => p.to_path_buf(),
    };
    let disks = sysinfo::Disks::new_with_refreshed_list().ok()?;
    disks
        .iter()
        .filter(|d| p.starts_with(d.mount_point()))
        .max_by_key(|d| d.mount_point().as_os_str().len())
        .map(|d| Disk {
            total: d.total_space(),
            available: d.available_space().min(d.total_space()),
        })
}

/// The `GET /api/v1/host` answer.
pub fn host_body(
    mem: Option<Memory>,
    disk: Option<Disk>,
    cpus: Option<usize>,
    running: usize,
    queued: usize,
) -> Value {
    json!({
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "cpus": cpus,
        "memory": mem.map(|m| json!({"total_bytes": m.total, "available_bytes": m.available})),
        "disk": disk.map(|d| json!({"total_bytes": d.total, "available_bytes": d.available})),
        "sessions": {"running": running, "queued": queued},
    })
}

pub async fn host(State(st): State<AppState>) -> Response {
    let (running, queued) = st
        .sessions
        .list()
        .iter()
        .fold((0, 0), |(r, q), s| match s.state {
            "running" => (r + 1, q),
            "pending" => (r, q + 1),
            _ => (r, q),
        });
    let dir = DATA_DIR.get().cloned();
    let read = tokio::task::spawn_blocking(move || {
        (
            memory(),
            dir.as_deref().and_then(disk_of),
            std::thread::available_parallelism().ok().map(usize::from),
        )
    })
    .await;
    let (mem, disk, cpus) = read.unwrap_or((None, None, None));
    Json(host_body(mem, disk, cpus, running, queued)).into_response()
}

// ---------------------------------------------------------------- tree

#[derive(Debug, Default, Deserialize)]
pub struct TreeQuery {
    root: Option<String>,
    depth: Option<usize>,
}

/// Why a tree file was not read.
#[derive(Debug, PartialEq, Eq)]
pub enum TreeError {
    TooLarge,
    Unreadable,
    Shape,
}

fn sha(s: &str) -> String {
    Sha256::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Read and check the file: an object whose `nodes` is an array; `Ok(None)` when absent.
pub fn read_tree(path: &Path) -> Result<Option<Value>, TreeError> {
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(TreeError::Unreadable),
    };
    if meta.len() > TREE_MAX_BYTES {
        return Err(TreeError::TooLarge);
    }
    let raw = std::fs::read(path).map_err(|_| TreeError::Unreadable)?;
    let v: Value = serde_json::from_slice(&raw).map_err(|_| TreeError::Shape)?;
    if !v.get("nodes").is_some_and(Value::is_array) {
        return Err(TreeError::Shape);
    }
    Ok(Some(v))
}

fn str_of<'a>(n: &'a Value, k: &str) -> Option<&'a str> {
    n.get(k).and_then(Value::as_str)
}

/// One node as the page reads it (SPEC §1.1 names; `station` is the tree's `status`).
fn node_view(n: &Value, order: usize, with_need: bool) -> Value {
    let mut o = Map::new();
    // rev: the table's write count (null for a file node); node_set names it as if_rev
    for k in [
        "id", "parent", "kind", "title", "head", "owner", "reviewer", "rev",
    ] {
        o.insert(k.into(), n.get(k).cloned().unwrap_or(Value::Null));
    }
    o.insert(
        "station".into(),
        n.get("status").cloned().unwrap_or(Value::Null),
    );
    o.insert(
        "with".into(),
        n.get("with")
            .filter(|w| w.is_array())
            .cloned()
            .unwrap_or_else(|| json!([])),
    );
    // SPEC-workspace-multiseat §23-1: 「要靠誰」, the ids aib-plan set --needs wrote (only text)
    let needs: Vec<&str> = n
        .get("needs")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    o.insert("needs".into(), json!(needs));
    o.insert("order".into(), json!(order));
    // N-workspace-c3: the display station and its tag come from aibd (one copy of the rules)
    crate::display::add_to(&mut o, n);
    let need = str_of(n, "need").map(|s| {
        if with_need {
            json!(s)
        } else {
            json!({"len": s.chars().count(), "sha256": sha(s)})
        }
    });
    o.insert("need".into(), need.unwrap_or(Value::Null));
    Value::Object(o)
}

/// The nodes under `root` (all roots when `None`) down to `depth` levels below it, in file
/// order; a node whose id is not a string is skipped; a parent loop is walked once.
/// `None` when `root` names no node.
pub fn select(
    tree: &Value,
    root: Option<&str>,
    depth: Option<usize>,
    with_need: bool,
) -> Option<Vec<Value>> {
    let empty = Vec::new();
    let nodes = tree["nodes"].as_array().unwrap_or(&empty);
    let mut ids: HashMap<&str, usize> = HashMap::new();
    for (i, n) in nodes.iter().enumerate() {
        if let Some(id) = str_of(n, "id") {
            ids.entry(id).or_insert(i);
        }
    }
    let mut kids: HashMap<&str, Vec<usize>> = HashMap::new();
    let mut tops = Vec::new();
    for (i, n) in nodes.iter().enumerate() {
        let Some(id) = str_of(n, "id") else { continue };
        if ids.get(id) != Some(&i) {
            continue; // a repeated id: the first one stands
        }
        match str_of(n, "parent").filter(|p| ids.contains_key(p) && *p != id) {
            Some(p) => kids.entry(p).or_default().push(i),
            None => tops.push(i),
        }
    }
    let start = match root {
        Some(r) => vec![*ids.get(r)?],
        None => tops,
    };
    let limit = depth.unwrap_or(DEPTH_MAX).min(DEPTH_MAX);
    let mut seen = HashSet::new();
    let mut q: VecDeque<(usize, usize)> = start.into_iter().map(|i| (i, 0)).collect();
    let mut keep = Vec::new();
    while let Some((i, d)) = q.pop_front() {
        if !seen.insert(i) {
            continue;
        }
        keep.push(i);
        if d < limit {
            if let Some(ks) = str_of(&nodes[i], "id").and_then(|id| kids.get(id)) {
                q.extend(ks.iter().map(|k| (*k, d + 1)));
            }
        }
    }
    keep.sort_unstable();
    Some(
        keep.into_iter()
            .map(|i| node_view(&nodes[i], i, with_need))
            .collect(),
    )
}

/// Whether `need` is given as words: the built-in page and the start-up token; a user folder
/// page (or any page while a user page serves `/`) gets its length and hash.
pub(crate) async fn gives_words(c: &Caller) -> bool {
    match c {
        Caller::Page(p) if p.user_page => false,
        Caller::Page(_) => {
            tokio::task::spawn_blocking(|| crate::static_files::web_source() != "user")
                .await
                .unwrap_or(false)
        }
        _ => true,
    }
}

/// `GET /api/v1/tree?root=&depth=` (a seat token never reaches it: the guard).
pub async fn tree(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Query(q): Query<TreeQuery>,
) -> Response {
    let with_need = gives_words(&c).await;
    let Some(dir) = DATA_DIR.get().cloned() else {
        return Json(json!({"source": "none", "asof": null, "nodes": []})).into_response();
    };
    let read = tokio::task::spawn_blocking(move || {
        // L2: a write aibd did not make lets go of the tree before it is read
        if let Some(p) = tree_path() {
            crate::plan_route::check_outside(&p, Some(&*st.store));
        }
        // once the nodes are in aibd (AIBD_TREE_IN_DB at a start), the table is the tree
        let (source, t) = match tree_in_db() {
            Some(t) => ("aibd", Some(t)),
            None => ("file", read_tree(&tree_source_in(&dir))?),
        };
        Ok::<_, TreeError>(t.map(|t| {
            let picked = select(&t, q.root.as_deref(), q.depth, with_need);
            (
                source,
                t.get("asof").cloned().unwrap_or(Value::Null),
                picked,
            )
        }))
    })
    .await;
    match read {
        Ok(Ok(None)) => Json(json!({"source": "none", "asof": null, "nodes": []})).into_response(),
        Ok(Ok(Some((source, asof, Some(nodes))))) => {
            Json(json!({"source": source, "asof": asof, "nodes": nodes})).into_response()
        }
        Ok(Ok(Some((_, _, None)))) => {
            refuse(StatusCode::NOT_FOUND, "node_not_found", "no such node")
        }
        Ok(Err(TreeError::TooLarge)) => refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "tree_too_large",
            "the tree file is larger than aibd reads",
        ),
        Ok(Err(_)) | Err(_) => refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "tree_unreadable",
            "the tree file could not be read; it was left as it is",
        ),
    }
}

// ---------------------------------------------------------------- tree into aibd

/// The node fields that have their own column; every other field goes to `extra`.
const NODE_COLUMNS: &[&str] = &["id", "parent", "kind", "title", "status", "head", "need"];

/// The node rows of a tree file (MODEL-workspace-tree r1): file order; a node whose id is not
/// a string is skipped; a repeated id keeps the first and is returned in the second list (a9136
/// 10:54 b: an event names them); a parent that is the node itself or not in the file makes
/// a root; the fields with no column are kept in `extra`.
pub fn import_rows(tree: &Value, now: &str) -> (Vec<aib_store::NodeRow>, Vec<String>) {
    let empty = Vec::new();
    let nodes = tree["nodes"].as_array().unwrap_or(&empty);
    let mut seen: HashSet<&str> = HashSet::new();
    let mut dropped = Vec::new();
    let ids: HashSet<&str> = nodes.iter().filter_map(|n| str_of(n, "id")).collect();
    let mut rows = Vec::new();
    for n in nodes {
        let Some(id) = str_of(n, "id") else { continue };
        if !seen.insert(id) {
            dropped.push(id.to_owned());
            continue;
        }
        let text = |k: &str| str_of(n, k).map(str::to_owned);
        let parent = str_of(n, "parent").filter(|p| *p != id && ids.contains(p));
        let extra: Map<String, Value> = n
            .as_object()
            .map(|o| {
                o.iter()
                    .filter(|(k, _)| !NODE_COLUMNS.contains(&k.as_str()))
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect()
            })
            .unwrap_or_default();
        rows.push(aib_store::NodeRow {
            node_id: id.to_owned(),
            parent_id: parent.map(str::to_owned),
            kind: text("kind").unwrap_or_default(),
            title: text("title").unwrap_or_default(),
            station: text("status"),
            head: text("head"),
            need: text("need"),
            ord: i64::try_from(rows.len()).unwrap_or(i64::MAX),
            rev: 1,
            updated: now.to_owned(),
            extra: Value::Object(extra).to_string(),
        });
    }
    (rows, dropped)
}

/// The rows back in the tree file's shape, so [`select`] reads both alike.
pub fn tree_of_rows(rows: &[aib_store::NodeRow]) -> Value {
    let nodes: Vec<Value> = rows
        .iter()
        .map(|r| {
            let mut o = match serde_json::from_str::<Value>(&r.extra) {
                Ok(Value::Object(m)) => m,
                _ => Map::new(),
            };
            o.insert("id".into(), json!(r.node_id));
            o.insert("parent".into(), json!(r.parent_id));
            o.insert("kind".into(), json!(r.kind));
            o.insert("title".into(), json!(r.title));
            o.insert("status".into(), json!(r.station));
            o.insert("head".into(), json!(r.head));
            o.insert("need".into(), json!(r.need));
            o.insert("rev".into(), json!(r.rev));
            Value::Object(o)
        })
        .collect();
    json!({ "nodes": nodes })
}

/// Set at start when the node table and the tree file disagree (C): the file stays the tree.
static TREE_HELD: std::sync::OnceLock<bool> = std::sync::OnceLock::new();

/// The fields compared between the table and the file (the columns; `extra` and `rev` are
/// aibd's own bookkeeping or free text the old page shows as it is).
/// The compared columns of one row.
type RowKey<'a> = (
    Option<&'a str>,
    &'a str,
    &'a str,
    Option<&'a str>,
    Option<&'a str>,
    Option<&'a str>,
);

fn row_key(r: &aib_store::NodeRow) -> RowKey<'_> {
    (
        r.parent_id.as_deref(),
        r.kind.as_str(),
        r.title.as_str(),
        r.station.as_deref(),
        r.head.as_deref(),
        r.need.as_deref(),
    )
}

/// The table against the file, id by id: `{ok, nodes_db, nodes_file, differ, only_db,
/// only_file}` (each list at most 50 ids, the counts whole). `ok` only when all agree.
pub fn reconcile(rows: &[aib_store::NodeRow], tree: &Value) -> Value {
    let (file_rows, _) = import_rows(tree, "");
    let db: HashMap<&str, &aib_store::NodeRow> =
        rows.iter().map(|r| (r.node_id.as_str(), r)).collect();
    let file: HashMap<&str, &aib_store::NodeRow> =
        file_rows.iter().map(|r| (r.node_id.as_str(), r)).collect();
    let mut differ: Vec<&str> = Vec::new();
    let mut only_file: Vec<&str> = Vec::new();
    for r in &file_rows {
        match db.get(r.node_id.as_str()) {
            Some(d) if row_key(d) == row_key(r) => {}
            Some(_) => differ.push(&r.node_id),
            None => only_file.push(&r.node_id),
        }
    }
    let only_db: Vec<&str> = rows
        .iter()
        .filter(|r| !file.contains_key(r.node_id.as_str()))
        .map(|r| r.node_id.as_str())
        .collect();
    let ok = differ.is_empty() && only_file.is_empty() && only_db.is_empty();
    let cut = |v: &[&str]| v.iter().take(50).map(|s| json!(s)).collect::<Vec<_>>();
    json!({
        "ok": ok,
        "nodes_db": rows.len(),
        "nodes_file": file_rows.len(),
        "differ": differ.len(), "differ_ids": cut(&differ),
        "only_db": only_db.len(), "only_db_ids": cut(&only_db),
        "only_file": only_file.len(), "only_file_ids": cut(&only_file),
    })
}

/// `AIBD_TREE_IN_DB=1`: the tree lives in aibd (off by default until 519d0 sets the day).
fn tree_in_db_on() -> bool {
    std::env::var("AIBD_TREE_IN_DB").as_deref() == Ok("1")
}

/// The tree as aibd knows it now, with where it came from (blocking).
fn current_tree_from() -> Result<Option<(&'static str, Value)>, TreeError> {
    if let Some(t) = tree_in_db() {
        return Ok(Some(("aibd", t)));
    }
    let Some(p) = tree_path() else {
        return Ok(None);
    };
    Ok(read_tree(&p)?.map(|t| ("file", t)))
}

/// Records the card keeps: the tree's `evidence`, one per ` | ` piece, newest first.
pub const RECORDS_MAX: usize = 200;
/// Children the card lists, in tree order.
pub const CHILDREN_MAX: usize = 200;

/// A record's leading time as the tree writes it: `MM-DD` and an optional ` HH:MM` (the
/// minutes may end in `x`, as in `12:2x`); the rest is the text.
fn record_time(e: &str) -> (&str, &str) {
    let b = e.as_bytes();
    let digits = |i: usize, most: usize| {
        let mut j = i;
        while j < b.len() && j - i < most && b[j].is_ascii_digit() {
            j += 1;
        }
        j
    };
    let m = digits(0, 2);
    if m == 0 || m >= b.len() || b[m] != b'-' {
        return ("", e);
    }
    let d = digits(m + 1, 2);
    if d == m + 1 || (d < b.len() && (b[d].is_ascii_digit() || b[d] == b'-')) {
        return ("", e);
    }
    let mut end = d;
    let mut k = d;
    while k < b.len() && b[k] == b' ' {
        k += 1;
    }
    let h = digits(k, 2);
    if k > d && h > k && h < b.len() && b[h] == b':' {
        let mut j = h + 1;
        while j < b.len() && j - h - 1 < 3 && (b[j].is_ascii_digit() || b[j] == b'x') {
            j += 1;
        }
        if j > h + 2 {
            end = j;
        }
    }
    (&e[..end], e[end..].trim_start())
}

/// The seat a record names first (`@` and five hex digits), or "".
fn record_by(text: &str) -> String {
    let b = text.as_bytes();
    for (i, _) in text.match_indices('@') {
        let w = &b[i + 1..];
        let hex = w.iter().take_while(|c| c.is_ascii_hexdigit()).count();
        if hex == 5 {
            return text[i + 1..i + 6].to_string();
        }
    }
    String::new()
}

/// Words to the built-in page; length and hash to a user folder page (the `need` rule).
pub(crate) fn words_or_hash(s: &str, words: bool) -> Value {
    if words {
        json!(s)
    } else {
        json!({"len": s.chars().count(), "sha256": sha(s)})
    }
}

/// The card window's fields (SPEC §15) for node `id` in `tree`: why, updated, eta, who
/// approved and on what, what it waits for, the two platforms, its records (newest first;
/// `kind` is left to the page, which sorts them as the board does) and the items right
/// under it. The free-text fields (why, record text) follow the `need` rule.
pub fn card(tree: &Value, id: &str, words: bool) -> Option<Map<String, Value>> {
    let empty = Vec::new();
    let nodes = tree["nodes"].as_array().unwrap_or(&empty);
    let n = nodes.iter().find(|n| str_of(n, "id") == Some(id))?;
    let mut o = Map::new();
    o.insert(
        "why".into(),
        str_of(n, "why").map_or(Value::Null, |w| words_or_hash(w, words)),
    );
    for (to, from) in [("updated", "updated"), ("eta", "eta"), ("start", "start")] {
        o.insert(to.into(), str_of(n, from).map_or(Value::Null, |v| json!(v)));
    }
    let ap = n.get("approved").filter(|a| a.is_object());
    let ap_str = |k: &str| {
        ap.and_then(|a| str_of(a, k))
            .map_or(Value::Null, |v| json!(v))
    };
    o.insert("approved_by".into(), ap_str("by"));
    o.insert("approved_at".into(), ap_str("at"));
    o.insert("approval_ref".into(), ap_str("pointer"));
    let wait: Vec<Value> = match n.get("after") {
        Some(Value::Array(a)) => a.iter().filter(|v| v.is_string()).cloned().collect(),
        Some(Value::String(s)) if !s.is_empty() => vec![json!(s)],
        _ => Vec::new(),
    };
    o.insert("wait".into(), json!(wait));
    let pf = n.get("platform").filter(|p| p.is_object());
    o.insert(
        "platforms".into(),
        pf.map_or(Value::Null, |p| {
            json!({"mac": p.get("mac").and_then(Value::as_str),
                   "win": p.get("win").and_then(Value::as_str)})
        }),
    );
    let ev = str_of(n, "evidence").unwrap_or("");
    let mut records: Vec<(usize, Value)> = ev
        .split(" | ")
        .map(str::trim)
        .filter(|x| !x.is_empty())
        .enumerate()
        .map(|(i, e)| {
            let (at, text) = record_time(e);
            let by = record_by(text);
            (i, {
                let kinds = crate::record_kind::rules().kinds(e);
                json!({"at": at, "by": by, "kind": kinds.first().copied().unwrap_or("dev"),
                           "kinds": kinds, "text": words_or_hash(text, words)})
            })
        })
        .collect();
    records.reverse();
    let total = records.len();
    let records: Vec<Value> = records
        .into_iter()
        .take(RECORDS_MAX)
        .map(|(_, r)| r)
        .collect();
    o.insert("records".into(), json!(records));
    o.insert("records_total".into(), json!(total));
    let kids: Vec<&Value> = nodes
        .iter()
        .filter(|k| str_of(k, "parent") == Some(id) && str_of(k, "id") != Some(id))
        .collect();
    let children: Vec<Value> = kids
        .iter()
        .take(CHILDREN_MAX)
        .map(|k| {
            let mut c = Map::new();
            c.insert("id".into(), json!(str_of(k, "id")));
            c.insert("title".into(), json!(str_of(k, "title")));
            c.insert("owner".into(), json!(str_of(k, "owner")));
            let station = k.get("status").cloned().unwrap_or(Value::Null);
            c.insert("station".into(), station);
            crate::display::add_to(&mut c, k);
            Value::Object(c)
        })
        .collect();
    o.insert("children".into(), json!(children));
    o.insert("children_total".into(), json!(kids.len()));
    Some(o)
}

/// `GET /api/v1/nodes/{id}` (SPEC §3, §15): one item, as the tree gives it (need only to the
/// built-in page), with the `rev` a `node_set` names and the card window's fields ([`card`]).
pub async fn node(Extension(c): Extension<Caller>, UrlPath(id): UrlPath<String>) -> Response {
    let words = gives_words(&c).await;
    let got = tokio::task::spawn_blocking(move || {
        Ok::<_, TreeError>(current_tree_from()?.map(|(source, t)| {
            let one = select(&t, Some(&id), Some(0), words)
                .and_then(|v| v.into_iter().next())
                .map(|mut n| {
                    if let (Value::Object(m), Some(more)) = (&mut n, card(&t, &id, words)) {
                        m.extend(more);
                        // the ideas that went through on their own (519d0 11-19 09:20 §3)
                        let ideas = crate::consent_cards::node_ideas_now(&id, words);
                        if !ideas.is_empty() {
                            let n_ideas = ideas.len();
                            if let Some(Value::Array(r)) = m.get_mut("records") {
                                let mut all = ideas;
                                all.append(r);
                                *r = all;
                            }
                            if let Some(t) = m.get("records_total").and_then(Value::as_u64) {
                                m.insert("records_total".into(), json!(t + n_ideas as u64));
                            }
                        }
                    }
                    n
                });
            (source, one)
        }))
    })
    .await;
    match got {
        Ok(Ok(Some((source, Some(n))))) => {
            Json(json!({"source": source, "node": n})).into_response()
        }
        Ok(Ok(_)) => refuse(StatusCode::NOT_FOUND, "node_not_found", "no such node"),
        Ok(Err(TreeError::TooLarge)) => refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "tree_too_large",
            "the tree file is larger than aibd reads",
        ),
        Ok(Err(_)) | Err(_) => refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "tree_unreadable",
            "the tree file could not be read; it was left as it is",
        ),
    }
}

/// The tree is aibd's: the flag is on and the node table has rows (blocking).
pub fn tree_is_in_aibd() -> bool {
    tree_in_db().is_some()
}

/// The tree in aibd's node table, once it has rows and only while `AIBD_TREE_IN_DB=1`: with
/// the flag off aibd reads the tree file again, so turning it off is the way back.
fn tree_in_db() -> Option<Value> {
    tree_in_db_on().then_some(())?;
    // the start's check found the table and the file apart: the file stays the tree
    if TREE_HELD.get() == Some(&true) || crate::plan_route::held() {
        return None;
    }
    crate::assign::db()
        .and_then(|db| db.nodes().ok())
        .filter(|rows| !rows.is_empty())
        .map(|rows| tree_of_rows(&rows))
}

/// The tree as aibd knows it now: the node table once filled, else the tree file (`None`
/// when neither is there or the file does not read). Blocking: call off the async threads.
pub fn current_tree() -> Option<Value> {
    tree_in_db().or_else(|| tree_path().and_then(|p| read_tree(&p).ok().flatten()))
}

/// At start, with `AIBD_TREE_IN_DB=1` only (off by default: aib-plan still writes the file
/// until 519d0 sets the day): fill the node table from `<data>/tree.json` once, and say so
/// in an event — how many came in and the repeated ids that did not (at most 50 named).
pub fn import_at_start(db: &aib_store::WorkspaceDb, store: &aib_store::Store, data_dir: &Path) {
    // A: a marker from an earlier run goes first; it comes back only when this start takes over
    crate::plan_route::unmark(&tree_source_in(data_dir));
    if !tree_in_db_on() {
        return;
    }
    let tree = match read_tree(&tree_source_in(data_dir)) {
        Ok(Some(t)) => t,
        Ok(None) => return,
        Err(e) => {
            tracing::warn!(?e, "tree file not read; nodes not imported");
            return;
        }
    };
    let now = crate::sessions::now_rfc3339();
    let (rows, dropped) = import_rows(&tree, &now);
    let n = rows.len();
    match db.import_nodes(rows) {
        Ok(true) => {
            let ev = crate::sessions::new_id("ev_")
                .ok()
                .map(|id| aib_store::NewEvent {
                    event_id: id.clone(),
                    aggregate_type: "tree".into(),
                    aggregate_id: id,
                    sequence: 1,
                    event_type: "tree.imported".into(),
                    correlation_id: None,
                    occurred_at: now,
                    payload: json!({
                        "imported": n,
                        "dropped": dropped.len(),
                        "dropped_ids": dropped.iter().take(50).collect::<Vec<_>>(),
                    }),
                });
            if !ev.is_some_and(|ev| store.append(vec![ev]).is_ok()) {
                tracing::warn!("tree imported; its event was not written");
            }
            tracing::info!(
                nodes = n,
                dropped = dropped.len(),
                "tree imported into aibd"
            );
        }
        Ok(false) => {}
        Err(e) => tracing::warn!(error = %e, "nodes not imported"),
    }
    // C: whatever the table held before, it must say what the file says now, field by field,
    // or the file stays the tree (aibd says so and does not take over)
    if let Ok(rows) = db.nodes() {
        let r = reconcile(&rows, &tree);
        let ok = r["ok"] == json!(true);
        let _ = TREE_HELD.set(!ok);
        // A: the marker tells aib-plan to write through aibd, only while aibd holds the tree
        let source = tree_source_in(data_dir);
        if ok {
            crate::plan_route::remember(&source);
            crate::plan_route::mark(&source, &data_dir.join("run"));
        }
        let ev = crate::sessions::new_id("ev_")
            .ok()
            .map(|id| aib_store::NewEvent {
                event_id: id.clone(),
                aggregate_type: "tree".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: "tree.reconciled".into(),
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload: r.clone(),
            });
        if !ev.is_some_and(|ev| store.append(vec![ev]).is_ok()) {
            tracing::warn!("tree checked; its event was not written");
        }
        if ok {
            tracing::info!(nodes = rows.len(), "tree in aibd matches the file");
        } else {
            tracing::warn!(report = %r, "tree in aibd differs from the file; the file stays the tree");
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// N-workspace-c4 C: the table and the file agree field by field, or the start keeps the
    /// file as the tree.
    #[test]
    fn the_table_and_the_file_are_checked_id_by_id() {
        let file = json!({"nodes": [
            {"id": "A", "parent": null, "kind": "goal", "title": "主線", "status": "開發中"},
            {"id": "A-c1", "parent": "A", "title": "卡", "status": "未驗", "head": "abc", "evidence": "x"}
        ]});
        let (rows, _) = import_rows(&file, "t");
        let r = reconcile(&rows, &file);
        assert_eq!(r["ok"], true, "{r}");
        assert_eq!(
            (r["nodes_db"].as_u64(), r["nodes_file"].as_u64()),
            (Some(2), Some(2))
        );
        // aib-plan moved a card the table did not hear about
        let mut moved = rows.clone();
        moved[1].station = Some("審過未推".into());
        let r = reconcile(&moved, &file);
        assert_eq!(
            (r["ok"].as_bool(), r["differ"].as_u64()),
            (Some(false), Some(1)),
            "reason: differ_unseen"
        );
        assert_eq!(r["differ_ids"][0], "A-c1");
        // a card only the file has, a card only the table has
        let r = reconcile(&rows[..1], &file);
        assert_eq!(
            (r["ok"].as_bool(), r["only_file"].as_u64()),
            (Some(false), Some(1)),
            "reason: missing_unseen"
        );
        let mut more = rows.clone();
        more.push(aib_store::NodeRow {
            node_id: "A-c2".into(),
            ..rows[1].clone()
        });
        let r = reconcile(&more, &file);
        assert_eq!(
            (r["ok"].as_bool(), r["only_db"].as_u64()),
            (Some(false), Some(1))
        );
        // free text in extra (evidence) and rev are not compared
        let mut noted = rows.clone();
        noted[1].extra = json!({"evidence": "y"}).to_string();
        noted[1].rev = 9;
        assert_eq!(reconcile(&noted, &file)["ok"], true);
    }

    fn card_tree() -> Value {
        json!({"nodes": [
            {"id": "N-ws", "parent": "G7", "title": "工作台", "status": "開發中",
             "why": "Andy 要", "updated": "11-18", "eta": "11-19",
             "approved": {"at": "11-18 09:2x", "by": "Andy", "pointer": "ORDER-x.md"},
             "after": ["node:N-a"], "platform": {"mac": "綠", "win": "未驗"},
             "evidence": "11-17 總結 @519d0 | 11-18 12:2x 新的 @7b81e 做 | 沒有時間"},
            {"id": "c1", "parent": "N-ws", "title": "樹", "status": "實作中", "owner": "7b81e"},
            {"id": "c2", "parent": "N-ws", "title": "主機"},
            {"id": "gc", "parent": "c1", "title": "孫"}
        ]})
    }

    #[test]
    fn the_card_has_every_field_records_newest_first_and_the_items_right_under() {
        let c = card(&card_tree(), "N-ws", true).unwrap();
        assert_eq!(c["why"], "Andy 要");
        assert_eq!(c["approved_by"], "Andy");
        assert_eq!(c["approval_ref"], "ORDER-x.md");
        assert_eq!(c["wait"], json!(["node:N-a"]));
        assert_eq!(c["platforms"]["win"], "未驗");
        let r = c["records"].as_array().unwrap();
        assert_eq!(r.len(), 3);
        assert_eq!(r[0]["text"], "沒有時間", "reason: records_not_newest_first");
        assert_eq!(r[1]["at"], "11-18 12:2x");
        assert_eq!(r[1]["by"], "7b81e");
        assert_eq!(r[1]["text"], "新的 @7b81e 做");
        assert_eq!(r[2]["at"], "11-17");
        assert_eq!(r[2]["kind"], "sum", "reason: record_kind_missing");
        assert_eq!(r[1]["kind"], "dev");
        let k = c["children"].as_array().unwrap();
        assert_eq!(k.len(), 2, "reason: grandchild_listed {k:?}");
        assert_eq!(k[0]["station"], "實作中");
        assert_eq!(k[0]["owner"], "7b81e", "reason: child_owner_missing");
    }

    #[test]
    fn a_user_folder_page_gets_no_words_of_why_or_records() {
        let c = card(&card_tree(), "N-ws", false).unwrap();
        assert_eq!(c["why"]["len"], 6, "reason: why_words_to_user_page"); // "Andy 要": 6 chars
        let r = c["records"].as_array().unwrap();
        assert!(
            r[1]["text"]["sha256"].is_string(),
            "reason: record_words_to_user_page"
        );
        assert_eq!(r[1]["at"], "11-18 12:2x");
    }

    #[test]
    fn a_record_time_is_only_a_month_day_and_clock() {
        assert_eq!(record_time("11-18 12:2x 字"), ("11-18 12:2x", "字"));
        assert_eq!(record_time("11-18 字"), ("11-18", "字"));
        assert_eq!(record_time("2026-11-18 字"), ("", "2026-11-18 字"));
        assert_eq!(record_time("#855 字"), ("", "#855 字"));
        assert_eq!(record_time("1-2-3"), ("", "1-2-3"));
        assert_eq!(record_by("見 @abcde1 與 @519d0"), "519d0");
    }

    fn t() -> Value {
        json!({"asof": "11-18", "nodes": [
            {"id": "V", "parent": null, "kind": "vision", "title": "總綱", "status": null},
            {"id": "G7", "parent": "V", "kind": "group", "title": "G7", "with": ["a9136"]},
            {"id": "N-ws", "parent": "G7", "kind": "item", "title": "工作台", "status": "開發中",
             "need": "無條件", "owner": "519d0"},
            {"id": "T1", "parent": "N-ws", "kind": "task", "title": "樹"},
            {"id": "loopA", "parent": "loopB", "title": "a"},
            {"id": "loopB", "parent": "loopA", "title": "b"},
            {"id": "self", "parent": "self", "title": "自己"},
            {"id": 7, "parent": "V", "title": "數字 id"},
            {"id": "N-ws", "parent": "V", "title": "重複的 id"},
            {"id": "lost", "parent": "nobody", "title": "父不在"}
        ]})
    }

    fn ids(v: &[Value]) -> Vec<&str> {
        v.iter().map(|n| n["id"].as_str().unwrap()).collect()
    }

    #[test]
    fn the_tree_is_read_in_file_order_under_a_root_to_a_depth() {
        let all = select(&t(), None, None, true).unwrap();
        // a node whose parent is itself or not in the file is a root; a loop with no way in
        // is not reached; a repeated id keeps the first; a number id is skipped
        assert_eq!(ids(&all), ["V", "G7", "N-ws", "T1", "self", "lost"]);
        let sub = select(&t(), Some("G7"), Some(1), true).unwrap();
        assert_eq!(ids(&sub), ["G7", "N-ws"], "reason: depth_not_kept");
        let loop_ = select(&t(), Some("loopA"), None, true).unwrap();
        assert_eq!(ids(&loop_), ["loopA", "loopB"], "reason: loop_walked_twice");
        assert_eq!(select(&t(), Some("nope"), None, true), None);
        let n = &sub[1];
        assert_eq!(n["station"], "開發中");
        assert_eq!(n["order"], 2);
        assert_eq!(n["owner"], "519d0");
        assert_eq!(n["with"], json!([]));
        assert_eq!(sub[0]["with"], json!(["a9136"]));
        assert_eq!(n["need"], "無條件");
    }

    /// N-workspace-c3 + N-pv-needs: GET nodes / tree give the display station, its tag and the
    /// 「要靠誰」 ids from aibd (the page and stations-gen do not work them out).
    #[test]
    fn a_node_carries_its_display_station_tag_and_needs() {
        let tree = json!({"nodes": [
            {"id": "A", "parent": null, "status": "實查不通過", "needs": ["B", 7, "C"]},
            {"id": "B", "parent": "A", "status": "未驗", "verify_fail": {"sev": "H"}},
            {"id": "C", "parent": "A", "status": "未驗", "verify_fail": {"sev": "H", "closed": true}}
        ]});
        let v = select(&tree, None, None, true).unwrap();
        assert_eq!(v[0]["display_station"], "開發中");
        assert_eq!(v[0]["tag"]["text"], "實查不通過");
        assert_eq!(v[0]["station"], "實查不通過", "reason: station_rewritten");
        assert_eq!(v[0]["needs"], json!(["B", "C"]), "reason: needs_lost");
        assert_eq!(v[1]["display_station"], "未驗");
        assert_eq!(v[1]["tag"]["text"], "驗不通過");
        assert_eq!(v[1]["broken"], true);
        assert_eq!(v[2]["tag"], Value::Null, "reason: closed_fail_tagged");
        assert_eq!(v[2]["needs"], json!([]));
    }

    #[test]
    fn a_tree_file_becomes_rows_once_per_id_and_reads_back_alike() {
        let (rows, dropped) = import_rows(&t(), "now");
        let ids: Vec<&str> = rows.iter().map(|r| r.node_id.as_str()).collect();
        assert_eq!(
            ids,
            ["V", "G7", "N-ws", "T1", "loopA", "loopB", "self", "lost"],
            "reason: order_or_skip"
        );
        assert_eq!(dropped, ["N-ws"], "reason: repeat_not_named");
        let by = |id: &str| rows.iter().find(|r| r.node_id == id).unwrap();
        assert_eq!(by("N-ws").parent_id.as_deref(), Some("G7"));
        assert_eq!(by("N-ws").station.as_deref(), Some("開發中"));
        assert_eq!(by("N-ws").need.as_deref(), Some("無條件"));
        assert_eq!(by("self").parent_id, None, "reason: self_parent_kept");
        assert_eq!(by("lost").parent_id, None, "reason: missing_parent_kept");
        assert_eq!(by("loopA").parent_id.as_deref(), Some("loopB"));
        let extra: Value = serde_json::from_str(&by("G7").extra).unwrap();
        assert_eq!(extra, json!({"with": ["a9136"]}), "reason: extra_lost");
        assert_eq!(by("T1").ord, 3);
        // back in the file's shape, the same walk gives the same nodes
        let back = tree_of_rows(&rows);
        let a = select(&t(), Some("G7"), None, true).unwrap();
        let mut b = select(&back, Some("G7"), None, true).unwrap();
        // the only field a row adds: its rev, which node_set names
        for n in &mut b {
            assert_eq!(n["rev"], 1);
            n["rev"] = Value::Null;
        }
        assert_eq!(a, b, "reason: table_reads_otherwise");
    }

    #[test]
    fn a_user_page_gets_the_length_and_hash_of_the_persons_words() {
        let sub = select(&t(), Some("N-ws"), Some(0), false).unwrap();
        assert_eq!(sub[0]["need"]["len"], 3);
        assert_eq!(sub[0]["need"]["sha256"], sha("無條件"));
        assert!(
            !sub[0].to_string().contains("無條件"),
            "reason: words_to_user_page"
        );
    }

    #[test]
    fn a_tree_file_is_absent_too_large_or_not_a_tree() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(TREE_FILE);
        assert_eq!(read_tree(&p), Ok(None));
        std::fs::write(&p, b"{\"nodes\": {}}").unwrap();
        assert_eq!(read_tree(&p), Err(TreeError::Shape));
        std::fs::write(&p, b"not json").unwrap();
        assert_eq!(read_tree(&p), Err(TreeError::Shape));
        std::fs::write(&p, t().to_string()).unwrap();
        assert!(read_tree(&p).unwrap().is_some());
        let f = std::fs::File::options().write(true).open(&p).unwrap();
        f.set_len(TREE_MAX_BYTES + 1).unwrap();
        assert_eq!(read_tree(&p), Err(TreeError::TooLarge));
    }

    #[test]
    fn the_host_box_says_null_for_what_it_could_not_read() {
        let v = host_body(None, None, None, 2, 1);
        assert_eq!(v["memory"], Value::Null);
        assert_eq!(v["disk"], Value::Null);
        assert_eq!(v["cpus"], Value::Null);
        assert_eq!(v["sessions"], json!({"running": 2, "queued": 1}));
        let v = host_body(
            Some(Memory {
                total: 16,
                available: 4,
            }),
            Some(Disk {
                total: 100,
                available: 40,
            }),
            Some(8),
            0,
            0,
        );
        assert_eq!(v["memory"]["available_bytes"], 4);
        assert_eq!(v["disk"]["total_bytes"], 100);
        assert_eq!(v["os"], std::env::consts::OS);
    }

    #[test]
    fn this_machine_reads_its_memory_and_the_disk_of_a_folder() {
        let m = memory().expect("memory is readable on macOS, Windows and Linux");
        assert!(m.total > 0 && m.available <= m.total);
        let dir = tempfile::tempdir().unwrap();
        let d = disk_of(dir.path()).expect("the temp folder sits on a mounted disk");
        assert!(d.total > 0 && d.available <= d.total);
    }

    fn policy_with(programs: &[(&str, std::path::PathBuf)]) -> crate::allowlist::SessionPolicy {
        crate::allowlist::SessionPolicy {
            programs: programs
                .iter()
                .map(|(n, p)| crate::allowlist::Program {
                    name: (*n).to_owned(),
                    path: p.clone(),
                    signer: None,
                })
                .collect(),
            ..Default::default()
        }
    }

    #[test]
    fn version_names_what_opens_and_what_a_page_types_into() {
        let dir = tempfile::tempdir().unwrap();
        let claude = dir.path().join("claude");
        std::fs::write(&claude, b"").unwrap();
        // codex listed where nothing is (a Windows-shaped name too: only is_file decides)
        let pol = policy_with(&[
            ("claude", claude),
            ("codex", dir.path().join("bin").join("codex.exe")),
        ]);
        let v = version_body_with(false, &pol, &|_| true);
        let ps = v["capabilities"]["providers"].as_array().unwrap();
        assert_eq!(
            ps,
            &vec![
                json!({"id": "claude", "open": true, "modes": ["dgrs", "safe", "work"]}),
                json!({"id": "codex", "open": false, "modes": aib_seatopen::pipe_modes("codex"), "reason": "not_installed"}),
            ],
            "reason: brands_are_aib2_only_each_ready_or_why"
        );
        assert!(
            !v.to_string().contains(&dir.path().display().to_string()),
            "reason: version_names_a_path"
        );
        assert_eq!(v["capabilities"]["input_modes"], json!(["safe", "work"]));
        let acts = v["capabilities"]["acts"]
            .as_array()
            .expect("reason: version_has_no_acts");
        for a in ["seat_input", "node_set", "seat_send_back", "receipt_list"] {
            assert!(acts.iter().any(|x| x == a), "reason: act_not_listed {a}");
        }
        assert_eq!(
            acts.len(),
            crate::seat_input::PAGE_ACTS.len(),
            "reason: acts_not_the_list"
        );
        assert_eq!(
            version_body_with(true, &pol, &|_| true)["capabilities"]["input_modes"],
            json!(["safe", "work", "dgrs"])
        );
        assert!(v["capabilities"]["routes"]
            .as_array()
            .unwrap()
            .contains(&json!(crate::guard::TREE)));
    }

    #[test]
    fn the_old_set_ups_names_are_never_brands() {
        let v = version_body_with(false, &policy_with(&[]), &|_| true);
        let ids: Vec<&str> = v["capabilities"]["providers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, ["claude", "codex"], "reason: brands_not_aib2_only");
        for old in [
            "openrouterjev",
            "typesafe",
            "nvidia",
            "openrouter",
            "deepseek",
            "copilot",
            "musebot",
        ] {
            assert!(!v.to_string().contains(old), "reason: old_name_shown {old}");
        }
        // nothing listed for them here: not set up
        assert_eq!(brand_ready("claude", &policy_with(&[])), Err("not_set_up"));
        assert_eq!(brand_ready("grok", &policy_with(&[])), Err("not_built"));
    }

    #[test]
    fn a_brand_without_a_login_under_the_seat_home_is_not_logged_in() {
        let h = tempfile::tempdir().unwrap();
        assert!(!logged_in("claude", h.path(), None));
        assert!(!logged_in("codex", h.path(), None));
        // claude: the account named in ~/.claude.json (macOS keychain) or the credentials file
        std::fs::write(
            h.path().join(".claude.json"),
            br#"{"oauthAccount": {"x": 1}}"#,
        )
        .unwrap();
        assert!(logged_in("claude", h.path(), None));
        std::fs::write(h.path().join(".claude.json"), br#"{"oauthAccount": null}"#).unwrap();
        assert!(!logged_in("claude", h.path(), None));
        std::fs::create_dir(h.path().join(".claude")).unwrap();
        std::fs::write(h.path().join(".claude").join(".credentials.json"), b"{}").unwrap();
        assert!(logged_in("claude", h.path(), None));
        // codex: auth.json under ~/.codex, or under CODEX_HOME when that is set
        std::fs::create_dir(h.path().join(".codex")).unwrap();
        std::fs::write(h.path().join(".codex").join("auth.json"), b"{}").unwrap();
        assert!(logged_in("codex", h.path(), None));
        let other = tempfile::tempdir().unwrap();
        assert!(!logged_in("codex", h.path(), Some(other.path())));
        // the version answer says so
        let prog = h.path().join("claude");
        std::fs::write(&prog, b"").unwrap();
        let v = version_body_with(false, &policy_with(&[("claude", prog)]), &|_| false);
        assert_eq!(v["capabilities"]["providers"][0]["reason"], "not_logged_in");
    }

    /// 519d0 N-pv-agree-drive: a not_logged_in says whose HOME it looked in, and that a
    /// hand-started (not preview) aibd does not use the AIBD_SEAT_HOME it was given.
    #[test]
    fn a_missing_login_says_whose_home_and_an_ignored_seat_home() {
        // an absolute path in this platform's shape: `/real` has no drive, so on Windows
        // `Path::is_absolute` says no and the handed HOME would read as not given
        let real = if cfg!(windows) { r"C:\real" } else { "/real" };
        let n = login_home_note(false, Some(real));
        assert_eq!(n["login_home"], "own", "reason: not_preview_handed");
        assert_eq!(n["seat_home_ignored"], true, "reason: ignored_not_said");
        let n = login_home_note(true, Some(real));
        assert_eq!(n["login_home"], "handed");
        assert_eq!(n["seat_home_ignored"], false);
        let n = login_home_note(true, Some("rel"));
        assert_eq!(n["login_home"], "own", "reason: relative_home_taken");
        let n = login_home_note(false, None);
        assert_eq!(n["seat_home_ignored"], false);
        let mut v = json!({"capabilities": {"providers": [
            {"id": "claude", "open": false, "reason": "not_logged_in"},
            {"id": "codex", "open": true}
        ]}});
        note_login_home(&mut v, &login_home_note(false, Some(real)));
        assert_eq!(v["capabilities"]["providers"][0]["login_home"], "own");
        assert_eq!(v["capabilities"]["providers"][0]["seat_home_ignored"], true);
        assert!(
            v["capabilities"]["providers"][1]
                .get("login_home")
                .is_none(),
            "reason: open_row_noted"
        );
    }
}
