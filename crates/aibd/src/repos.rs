//! N-web-worktree wiring (DESIGN-web-worktree-b4297-1119 §1.1 §1.2 r3): the repos a dgrs seat
//! may be given a tree of. The page registers one by name and path (`repo_register`); aibd
//! reads the repo itself (its top, its remote, the project settings a seat would load) and asks
//! the owner's fingerprint ([`crate::owner_ok`]) with all of it in the window. Only an approved
//! ticket for this very request writes `<data>/repos.json`. A seat is then opened with the
//! repo's name, never a path; aibd opens the seat's own tree ([`prepare_tree`]).
//!
//! While the fingerprint queue is not wired, every registration and every open with a repo is
//! refused with `owner_check_unavailable` (5d33a §0: nothing falls back to a page button).
//! A tree is not a sandbox (decision Q1 point 5): no text here says "isolated".

use crate::api::AppState;
use crate::seat_worktree::{self, Manifest};
use axum::{http::StatusCode, response::IntoResponse, response::Response, Json};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// A base the page may name: a full commit, 40 lower-case hex (a short one could name another
/// commit with the same start, 5d33a CHECK-pv-up-p23 M1).
pub fn base_ok(b: &str) -> bool {
    b.len() == 40
        && b.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// At most this many copies of registered repos on one machine (the nine seats).
pub const REPO_COPIES_MAX: usize = 9;
/// A copy is opened only with at least this much free on the disk (one copy of ez2-aib is
/// 0.2-0.3 GB; the rest is room for its builds and the machine).
pub const REPO_DISK_FREE_MIN: u64 = 2 * 1024 * 1024 * 1024;

/// Whether one more copy may be opened: `repo_copies_full` at [`REPO_COPIES_MAX`];
/// `disk_low` under [`REPO_DISK_FREE_MIN`] or when the free space cannot be read (fail closed).
pub fn room_for_copy(copies: usize, available: Option<u64>) -> Result<(), &'static str> {
    if copies >= REPO_COPIES_MAX {
        return Err("repo_copies_full");
    }
    match available {
        Some(a) if a >= REPO_DISK_FREE_MIN => Ok(()),
        _ => Err("disk_low"),
    }
}

/// Beside a copy whose seat was archived and whose head aibd took into the registered repo
/// (`refs/aibd/seat/<wt>`, seat_worktree::collect): the copy no longer counts toward
/// [`REPO_COPIES_MAX`] (5d33a PREREAD-workrepo r2 L1). The folder stays (removing it is the
/// person's); the disk floor still holds.
pub const RELEASED_FILE: &str = "aibd-released.json";

/// Mark the copy at `tree` released ([`RELEASED_FILE`] in the folder above it).
pub fn release_copy(tree: &Path) -> std::io::Result<()> {
    let dir = tree
        .parent()
        .ok_or_else(|| std::io::Error::other("no folder above the copy"))?;
    let at = crate::sessions::now_rfc3339();
    std::fs::write(
        dir.join(RELEASED_FILE),
        serde_json::json!({"at": at}).to_string(),
    )
}

/// The copies of registered repos under `seat_work` that count: folders whose base file names
/// a `repo:` author ([`seat_worktree::BASE_FILE`]) and are not [`RELEASED_FILE`]-marked. A base
/// file that cannot be read as one counts too (N4: fail closed); no base file is a plain
/// seat folder.
pub fn repo_copies(seat_work: &Path) -> usize {
    let Ok(rd) = std::fs::read_dir(seat_work) else {
        return 0;
    };
    rd.flatten()
        .filter(|e| {
            let dir = e.path();
            if dir.join(RELEASED_FILE).exists() {
                return false;
            }
            let Ok(t) = std::fs::read_to_string(dir.join(seat_worktree::BASE_FILE)) else {
                return false;
            };
            serde_json::from_str::<serde_json::Value>(&t)
                .ok()
                .and_then(|v| v["author"].as_str().map(is_repo_copy))
                .unwrap_or(true)
        })
        .count()
}

/// One copy is counted and cloned at a time, so two opens at once cannot both pass the count
/// (5d33a PREREAD-workrepo r2 N3).
/// An archived seat's tree is collected ([`seat_worktree::collect`]); a registered repo's copy
/// whose head was taken no longer counts toward the machine's copies (5d33a PREREAD-workrepo r2
/// L1; the folder stays for the person). A collect that failed leaves it counted.
pub fn collect_copy(
    dir: &Path,
    base: &str,
    repo: Option<&Path>,
    hooks: &Path,
) -> Result<seat_worktree::Collected, String> {
    let c = seat_worktree::collect(dir, base, repo, hooks);
    let repo_copy = seat_worktree::base_of(dir).is_some_and(|(_, a)| is_repo_copy(&a));
    if c.is_ok() && repo_copy {
        if let Err(e) = release_copy(dir) {
            tracing::warn!(error = %e, "repo copy not marked released");
        }
    }
    c
}

static COPY_OPEN: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// A copy's author names a registered repo, not a seat (5d33a N6: never 合回 into it).
pub fn is_repo_copy(author: &str) -> bool {
    author.starts_with(REPO_AUTHOR_PREFIX)
}

/// The author a registered repo's copy names beside it (`repo:<name>`): not a seat, so a
/// 合回 into the registered repo's own checkout is refused (5d33a N6).
pub const REPO_AUTHOR_PREFIX: &str = "repo:";
/// The seat modes a registered repo may be given to (043dd page: `repo_modes` in version).
pub const REPO_MODES: [&str; 2] = ["dgrs", "work"];

/// The act type of the built-in page's 登記倉.
pub const REGISTER: &str = "repo_register";
/// The owner_ok operation of a registration.
pub const OP_REGISTER: &str = "repo.register";
/// The owner_ok operation of a dgrs seat opened with a repo (BOUNDARY §2).
pub const OP_OPEN: &str = "seat.open_dgrs";

pub(crate) const FILE: &str = "repos.json";

static LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Repos {
    #[serde(default)]
    pub repos: Vec<Repo>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Repo {
    pub name: String,
    pub path: String,
    #[serde(default)]
    pub remote: String,
    /// What a seat would load from the repo when it was registered; every open compares.
    pub manifest: Manifest,
    #[serde(default)]
    pub added_at: u64,
}

fn file(data: &Path) -> PathBuf {
    data.join(FILE)
}

pub fn load(data: &Path) -> Result<Repos, String> {
    match std::fs::read(file(data)) {
        Ok(b) => serde_json::from_slice(&b).map_err(|e| format!("{FILE}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Repos::default()),
        Err(e) => Err(format!("{FILE}: {e}")),
    }
}

fn save(data: &Path, r: &Repos) -> Result<(), String> {
    let tmp = data.join(format!("{FILE}.tmp"));
    let body = serde_json::to_vec_pretty(r).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| format!("{FILE}: {e}"))?;
    std::fs::rename(&tmp, file(data)).map_err(|e| format!("{FILE}: {e}"))
}

/// `GET /api/v1/version`'s `repos`: names only, never a path or a remote. A file that cannot be
/// read lists none.
pub fn names(data: &Path) -> Vec<Value> {
    load(data)
        .map(|r| r.repos.iter().map(|x| json!({"name": x.name})).collect())
        .unwrap_or_default()
}

pub fn find(data: &Path, name: &str) -> Option<Repo> {
    load(data).ok()?.repos.into_iter().find(|r| r.name == name)
}

/// What aibd read from a path the page named, before any fingerprint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Checked {
    pub name: String,
    pub top: PathBuf,
    pub remote: String,
    pub manifest: Manifest,
}

/// The registration checks (§1.1): the name's shape; an absolute path that is a repo's top
/// folder, read as its real path; not inside aibd's own folder (so never a seat's tree);
/// the project settings listed without error. `Err` = the reason word.
pub fn check(name: &str, path: &str, data: &Path, hooks: &Path) -> Result<Checked, &'static str> {
    if !seat_worktree::repo_name_ok(name) {
        return Err("name");
    }
    let p = Path::new(path);
    if !p.is_absolute() {
        return Err("path");
    }
    let real = std::fs::canonicalize(p).map_err(|_| "path")?;
    if !real.is_dir() {
        return Err("path");
    }
    let data_real = std::fs::canonicalize(data).unwrap_or_else(|_| data.to_path_buf());
    if real.starts_with(&data_real) {
        return Err("path_inside_aibd");
    }
    let top = seat_worktree::repo_top(&real, hooks).map_err(|_| "not_a_repo")?;
    let top_real = std::fs::canonicalize(&top).map_err(|_| "not_a_repo")?;
    if top_real != real {
        return Err("not_repo_top");
    }
    let remote = seat_worktree::git(&real, hooks, &["config", "--get", "remote.origin.url"])
        .unwrap_or_default();
    let manifest = seat_worktree::manifest_for(&real, hooks)?;
    Ok(Checked {
        name: name.to_owned(),
        top: real,
        remote,
        manifest,
    })
}

/// The host a remote URL points at, for the fingerprint window: `git@host:o/r`,
/// `https://u@host/o/r` and `ssh://host:22/o/r` give `host`; a local path or none gives `本機`.
pub fn remote_host(remote: &str) -> String {
    let rest = match remote.split_once("://") {
        Some((_, r)) => r,
        None if remote.contains('@') => remote,
        // scp-like without a user (`github.com:o/r`, an ssh alias `myalias:o/r`): a host before
        // the colon, not a drive letter and no slash in it (5d33a PREREAD-ownertouch r2 L1)
        None => match remote.split_once(':') {
            Some((h, _)) if h.chars().count() > 1 && !h.contains(['/', '\\']) => remote,
            _ => return "本機".to_owned(),
        },
    };
    let auth = rest.split('/').next().unwrap_or("");
    let rest = auth.rsplit_once('@').map_or(auth, |(_, h)| h);
    let host: String = rest
        .chars()
        .take_while(|c| *c != ':' && *c != '/')
        .collect();
    if host.is_empty() {
        "本機".to_owned()
    } else {
        host
    }
}

/// The fingerprint a registration asks: the full path, the remote and the settings list are
/// all in the parameters, so a ticket approves exactly what aibd read.
pub fn register_ask(c: &Checked, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "name": c.name, "path": c.top.to_string_lossy(), "remote": c.remote,
        "manifest": c.manifest,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_REGISTER,
        // the window shows where the remote is, not only the folder (5d33a PREREAD-ownertouch L2),
        // and the repo's name first (Andy 11-21 01:23: the window named no repo)
        resource: format!(
            "{} · {} · remote {}",
            c.name,
            c.top.to_string_lossy(),
            remote_host(&c.remote)
        ),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// The fingerprint a dgrs seat with a repo asks (BOUNDARY §2: seat, brand, repo, settings).
pub fn open_ask(
    repo: &Repo,
    provider: &str,
    persona_uid: &str,
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "repo": repo.name, "path": repo.path, "provider": provider,
        "persona_uid": persona_uid, "manifest": repo.manifest,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_OPEN,
        // the brand decides the sandbox, so the window says it (5d33a PREREAD-ownertouch L2)
        resource: format!("{persona_uid} @ {} · {provider}", repo.name),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// A11 (DESIGN-a11-dgrs-open-asks-08de5-1123 r1; SPEC-ai-partner-open-ebab8-1123-r2 §3.1): the
/// fingerprint a page's dgrs open with no repo asks. Same operation as with a repo; `kind`
/// keeps an open's ticket from passing for a restart (f163c REVIEW-a11 r1 L1).
pub fn open_ask_plain(
    provider: &str,
    persona_uid: &str,
    persona_name: &str,
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "kind": "open", "provider": provider, "persona_uid": persona_uid, "repo": null,
        "persona_name": persona_name,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_OPEN,
        resource: format!("{persona_name}({persona_uid})· {provider} · 完全權限,不帶倉"),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// A11 r1 M1: the fingerprint a page's restart of a dgrs seat asks (重開, 換模型, 清對話: each
/// starts the provider again with no sandbox), from the seat's own record; `seat_id` and
/// `kind` bind the ticket to this seat and to a restart.
pub fn restart_ask(
    seat_id: &str,
    provider: &str,
    persona_uid: &str,
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "kind": "restart", "seat_id": seat_id, "provider": provider, "persona_uid": persona_uid,
    });
    let code: String = seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect();
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_OPEN,
        resource: format!("重開完全權限的席 @{code}({persona_uid} · {provider})"),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// A11 (f163c L1): a seat record read as the gate needs it: there, not there, or not read.
fn known_or_stop<T, E, J>(read: Result<Result<Option<T>, E>, J>) -> Result<Option<T>, ()> {
    match read {
        Ok(Ok(m)) => Ok(m),
        Ok(Err(_)) | Err(_) => Err(()),
    }
}

fn seat_unreadable() -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"code": "internal", "reason": "seat_unreadable",
            "message": "the seat's record could not be read; nothing was stopped or started",
            "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

/// A11: before a page's act starts a dgrs seat's provider again, the owner is asked (`Some`: the
/// answer to give now, nothing stopped or started); a safe or work seat, or an unknown one (the
/// act answers that itself), is not asked here.
pub async fn dgrs_restart_gate(
    st: &crate::api::AppState,
    seat_id: &str,
    ticket: Option<&str>,
    by: String,
) -> Option<Response> {
    let (store, id) = (st.store.clone(), seat_id.to_owned());
    let read = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        let Some(m) = store.seat_meta(&id)? else {
            return Ok(None);
        };
        let archived = store
            .seat(&id)?
            .is_some_and(|s| aib_store::ARCHIVED == s.state);
        Ok(Some((m, archived)))
    })
    .await;
    // f163c REVIEW-a11 L1: only "no such seat" is left to the act; a read that failed stops
    // here (a second read in revive could succeed and start a dgrs seat unasked)
    let (meta, archived) = match known_or_stop(read) {
        Ok(Some(m)) => m,
        Ok(None) => return None,
        Err(()) => return Some(seat_unreadable()),
    };
    let runs =
        st.sessions.list().iter().any(|s| {
            s.seat_id.as_deref() == Some(seat_id) && matches!(s.state, "running" | "pending")
        });
    match gate_step(meta.mode.as_deref(), archived, runs) {
        Step::Act => return None,
        Step::Refuse(reason, message) => {
            return Some(
                (
                    StatusCode::CONFLICT,
                    // f163c pre-0954a14a N1: the act's own shape (seat_input::refuse), so a
                    // page reading `code` sees one thing whichever answered
                    Json(json!({"code": reason, "message": message,
                        "request_id": crate::guard::request_id()})),
                )
                    .into_response(),
            );
        }
        Step::Ask => {}
    }
    let a = restart_ask(
        seat_id,
        &meta.provider,
        meta.persona_uid.as_deref().unwrap_or("-"),
        by,
    );
    let Some(t) = ticket else {
        return Some(asked(&a));
    };
    if !crate::owner_ok::approved_for(t, &a) {
        return Some(not_approved());
    }
    None
}

/// What the dgrs gate does with a page's restart (f163c REVIEW-a11 N1 + pre-8947d325 M1).
#[derive(Debug, PartialEq, Eq)]
enum Step {
    /// not a dgrs seat: the act answers it, nobody asked
    Act,
    /// a dgrs seat the act would refuse anyway: refused here, with the act's own words, before
    /// the owner is asked (no fingerprint spent) — never let through to be checked again
    /// later, as the seat could stop in between and start with no sandbox unasked
    Refuse(&'static str, &'static str),
    /// the owner is asked
    Ask,
}

fn gate_step(mode: Option<&str>, archived: bool, runs: bool) -> Step {
    if mode != Some("dgrs") {
        Step::Act
    } else if archived {
        Step::Refuse("seat_archived", "the seat is archived")
    } else if runs {
        Step::Refuse("seat_running", "the seat runs now")
    } else {
        Step::Ask
    }
}

/// `202 {ticket}` while the owner has not answered, `403 owner_check_unavailable` when no
/// fingerprint can be asked.
pub fn asked(ask: &crate::owner_ok::Ask) -> Response {
    match crate::owner_ok::ask(ask) {
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

pub fn not_approved() -> Response {
    (
        StatusCode::FORBIDDEN,
        Json(
            json!({"code": "forbidden", "reason": "owner_check_not_approved",
            "message": "the ticket is not an approved, unused fingerprint for this very request",
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRegister {
    #[serde(rename = "type")]
    _kind: String,
    name: String,
    path: String,
    #[serde(default)]
    ticket: Option<String>,
}

fn data_dir(st: &AppState) -> PathBuf {
    let p = st.sessions.private_dir();
    p.parent().unwrap_or(p).to_path_buf()
}

/// `POST /api/v1/act {type: "repo_register", name, path, ticket?}` from the built-in page in
/// this pairing (the caller refused the rest). Without a ticket: aibd reads the repo and asks
/// the fingerprint. With one: aibd reads the repo again and acts only when the ticket approves
/// this very reading; then `repos.json` holds it and `repo.registered` says so.
pub async fn register(st: AppState, body: Value, by: String) -> Response {
    let req: RawRegister = match serde_json::from_value(body) {
        Ok(r) => r,
        Err(_) => return bad("body", "body must be {type, name, path, ticket?}"),
    };
    let data = data_dir(&st);
    let hooks = crate::seats::seat_git_hooks(st.sessions.private_dir());
    let (name, path, d2) = (req.name.clone(), req.path.clone(), data.clone());
    let checked = tokio::task::spawn_blocking(move || {
        std::fs::create_dir_all(&hooks).map_err(|_| "hooks")?;
        check(&name, &path, &d2, &hooks)
    })
    .await;
    let c = match checked {
        Ok(Ok(c)) => c,
        Ok(Err(why)) => return bad(
            why,
            "name is 1-40 of a-z 0-9 -; path is the top folder of a git repo outside aibd's folder",
        ),
        Err(_) => return bad("internal", "the repo could not be read"),
    };
    let ask = register_ask(&c, by);
    let Some(ticket) = req.ticket else {
        return asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return not_approved();
    }
    let _g = LOCK.lock().await;
    let mut all = match load(&data) {
        Ok(r) => r,
        Err(e) => return bad("repos_unreadable", &e),
    };
    all.repos.retain(|r| r.name != c.name);
    all.repos.push(Repo {
        name: c.name.clone(),
        path: c.top.to_string_lossy().into_owned(),
        remote: c.remote,
        manifest: c.manifest,
        added_at: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0),
    });
    if let Err(e) = save(&data, &all) {
        return bad("repos_unwritable", &e);
    }
    let path_sha = crate::owner_ok::params_sha256(c.top.to_string_lossy().as_bytes());
    crate::seats::side_event(
        &st,
        "repo",
        "repo.registered",
        json!({"name": c.name, "path_sha": &path_sha[..12], "by": "page"}),
    )
    .await;
    Json(json!({"done": true, "name": c.name})).into_response()
}

/// After the open's fingerprint: the repo's settings are what was registered (else
/// `project_settings_changed`, the page asks again), and the seat's tree is opened on the
/// repo's head under `<seat-work>/<tree id>/<repo>` with a `seat/<id>-<MMDD>` branch.
/// Returns the tree's folder (the seat's cwd) and the head it starts at.
pub fn prepare_tree(
    repo: &Repo,
    seat_work: &Path,
    hooks: &Path,
    tree_id: &str,
    mmdd: &str,
    base: Option<&str>,
) -> Result<(PathBuf, String), &'static str> {
    std::fs::create_dir_all(hooks).map_err(|_| "hooks")?;
    let src = Path::new(&repo.path);
    seat_worktree::check_unchanged(&repo.manifest, src, hooks)?;
    if base.is_some_and(|b| !base_ok(b)) {
        return Err("base");
    }
    // 5d33a PREREAD-workrepo L2: a copy is a whole clone, so not past a fixed number on this
    // machine and not on a nearly full disk (11-20: 13 GiB free, 97 %)
    let _one_at_a_time = COPY_OPEN
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    room_for_copy(
        repo_copies(seat_work),
        crate::workspace::disk_of(seat_work.parent().unwrap_or(seat_work)).map(|d| d.available),
    )?;
    let tree = seat_worktree::tree_path(&seat_work.join(tree_id), &repo.name);
    let branch = seat_worktree::branch_name(tree_id, mmdd);
    // 519d0 06:35 (5d33a N6): a seat with a registered repo always works in its own copy, a
    // separate clone (COUNCIL-worktree-gitdir-7ebca-1120 (a), b4297's file:// clone), never a
    // worktree writing the registered repo's `.git`; a work seat's sandbox then needs nothing
    // outside its folder to commit
    let author = format!("{REPO_AUTHOR_PREFIX}{}", repo.name);
    let head = seat_worktree::open_clone(src, &tree, &branch, &author, hooks)
        .map_err(|_| "tree_not_opened")?;
    // ORDER-webonly-today 12: the seat's branch may start on a commit the page names (the
    // all-head), which the copy has when a branch of the registered repo reaches it; only the
    // seat's own copy is written, before the seat runs in it
    let Some(b) = base else {
        return Ok((tree, head));
    };
    let commit = format!("{b}^{{commit}}");
    if seat_worktree::git(&tree, hooks, &["cat-file", "-e", &commit]).is_err() {
        // the copy this call just made, no seat ever ran in it: not left to count as one
        let _ = std::fs::remove_dir_all(seat_work.join(tree_id));
        return Err("base_not_in_repo");
    }
    seat_worktree::git(&tree, hooks, &["checkout", "-q", "-B", &branch, b])
        .map_err(|_| "tree_not_opened")?;
    let head =
        seat_worktree::git(&tree, hooks, &["rev-parse", "HEAD"]).map_err(|_| "tree_not_opened")?;
    Ok((tree, head))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// f163c REVIEW-a11 L1: a seat record that could not be read stops the gate; only a seat
    /// that is not there is left to the act.
    #[test]
    fn a_seat_record_not_read_stops_the_dgrs_gate() {
        // no string errors here: apps/web/test/ws-static.test.cjs reads every `Err("…")` in
        // this file as a refusal the page must have words for
        type R = Result<Result<Option<u8>, ()>, ()>;
        let (some, none, db, join): (R, R, R, R) =
            (Ok(Ok(Some(1))), Ok(Ok(None)), Ok(Err(())), Err(()));
        assert_eq!(known_or_stop(some), Ok(Some(1)));
        assert_eq!(known_or_stop(none), Ok(None));
        assert_eq!(known_or_stop(db), Err(()), "reason: read_error_let_through");
        assert_eq!(
            known_or_stop(join),
            Err(()),
            "reason: join_error_let_through"
        );
    }
    use std::process::Command;

    /// A11 (f163c REVIEW-a11 N1, pre-8947d325 M1): an archived or running dgrs seat is refused
    /// by the gate itself (no fingerprint spent, nothing let through); only a dgrs seat that is
    /// neither asks; any other seat is the act's.
    #[test]
    fn a_restart_refused_anyway_asks_no_fingerprint() {
        assert_eq!(gate_step(Some("dgrs"), false, false), Step::Ask);
        assert_eq!(
            gate_step(Some("dgrs"), true, false),
            Step::Refuse("seat_archived", "the seat is archived"),
            "reason: archived_let_through"
        );
        assert_eq!(
            gate_step(Some("dgrs"), false, true),
            Step::Refuse("seat_running", "the seat runs now"),
            "reason: running_let_through"
        );
        assert_eq!(gate_step(Some("work"), false, false), Step::Act);
        assert_eq!(gate_step(None, true, true), Step::Act);
    }

    /// A11-2 (f163c REVIEW-a11 r1 L1): an open and a restart of a dgrs seat name what they are
    /// on the window's first line, and their tickets are bound apart (kind, seat).
    #[test]
    fn a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other() {
        let o = open_ask_plain("claude", "un0eba", "架構師", "page:p".into());
        assert_eq!(o.operation, OP_OPEN);
        assert_eq!(
            crate::owner_ok::plain_words(o.operation, &o.params).as_deref(),
            Some("開一個完全權限的席(claude),人格 架構師(un0eba)")
        );
        assert!(
            o.resource.contains("架構師"),
            "reason: no_persona_name {}",
            o.resource
        );
        let r = restart_ask("seat_ab12cdef", "claude", "un0eba", "page:p".into());
        assert_eq!(
            crate::owner_ok::plain_words(r.operation, &r.params).as_deref(),
            Some("重開完全權限的席 @ab12c(claude)")
        );
        assert_ne!(
            o.params, r.params,
            "reason: open_and_restart_share_a_ticket"
        );
        let r2 = restart_ask("seat_ff12cdef", "claude", "un0eba", "page:p".into());
        assert_ne!(
            r.params, r2.params,
            "reason: restart_ticket_not_bound_to_seat"
        );
        // the repo open keeps its own sentence
        let p = br#"{"repo":"demo","provider":"claude","persona_uid":"p1"}"#;
        assert_eq!(
            crate::owner_ok::plain_words(OP_OPEN, p).as_deref(),
            Some("開一個能改程式的席,帶倉 demo(claude)")
        );
    }

    fn g(dir: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args([
                "-c",
                "commit.gpgsign=false",
                "-c",
                "init.defaultBranch=main",
            ])
            .args(args)
            .status()
            .unwrap()
            .success();
        assert!(ok, "git {args:?}");
    }

    fn repo() -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        g(d.path(), &["init", "-q"]);
        std::fs::write(d.path().join("a.txt"), "a").unwrap();
        std::fs::write(d.path().join("CLAUDE.md"), "rules").unwrap();
        g(d.path(), &["add", "."]);
        g(d.path(), &["commit", "-q", "-m", "one"]);
        d
    }

    /// 519d0 11-20 21:14 (with 5d33a REVIEW-trust-t1 L1): what the registering window shows of a
    /// long remote host is its end, where the registrable domain is; a name that only starts
    /// like a known host is never shown as that host alone.
    #[test]
    fn the_registering_window_keeps_the_end_of_a_long_remote_host() {
        let host = format!("github.com.{}.attacker-owned.example", "x".repeat(120));
        let c = Checked {
            name: "aib".into(),
            top: PathBuf::from(format!("/Users/x/{}", "deep/".repeat(30))),
            remote: format!("git@{host}:o/r.git"),
            manifest: Manifest::default(),
        };
        let a = register_ask(&c, "page:0123abcd".into());
        let shown = crate::owner_ok::clean_resource(&a.resource);
        assert!(
            shown.ends_with(".attacker-owned.example"),
            "reason: registrable_domain_cut_off {shown}"
        );
        assert!(
            !shown.ends_with("remote github.com") && shown.starts_with('…'),
            "reason: long_host_shown_as_a_known_one {shown}"
        );
        assert!(
            crate::owner_ok::window_text(&a, "ot_1", "請在 01:23:44 前按")
                .contains(".attacker-owned.example"),
            "reason: window_text_lost_the_domain"
        );
    }

    #[test]
    fn the_window_names_the_remote_host() {
        for (r, want) in [
            ("git@github.com:ez2app/ez2-aib.git", "github.com"),
            ("https://u@github.com/ez2app/ez2-aib", "github.com"),
            ("ssh://git@host.example:22/o/r", "host.example"),
            ("file:///Users/x/repo", "本機"),
            ("/Users/x/repo", "本機"),
            ("", "本機"),
            ("https://github.com/o/r@v2", "github.com"),
            ("github.com:ez2app/ez2-aib.git", "github.com"),
            ("myalias:o/r", "myalias"),
            ("C:/Users/x/repo", "本機"),
            ("./a:b", "本機"),
        ] {
            assert_eq!(remote_host(r), want, "reason: host {r}");
        }
        // what the owner reads before the finger (5d33a PREREAD-ownertouch L2)
        let repo = Repo {
            name: "aib".into(),
            path: "/Users/x/ez2-aib".into(),
            remote: "git@github.com:ez2app/ez2-aib.git".into(),
            manifest: Manifest::default(),
            added_at: 0,
        };
        let o = open_ask(&repo, "codex", "p1", "page:0123abcd".into());
        assert!(
            o.resource.ends_with("p1 @ aib · codex"),
            "reason: brand_unseen {}",
            o.resource
        );
        let c = Checked {
            name: "aib".into(),
            top: PathBuf::from("/Users/x/ez2-aib"),
            remote: repo.remote.clone(),
            manifest: Manifest::default(),
        };
        let r = register_ask(&c, "page:0123abcd".into());
        assert!(
            r.resource.ends_with("ez2-aib · remote github.com"),
            "reason: remote_unseen {}",
            r.resource
        );
    }

    #[test]
    fn a_repo_is_registered_by_its_real_top_outside_aibd_with_its_settings_listed() {
        let r = repo();
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("seat-git").join("hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let p = r.path().to_str().unwrap();
        let c = check("my-repo", p, data.path(), &hooks).expect("reason: top_refused");
        assert_eq!(c.top, std::fs::canonicalize(r.path()).unwrap());
        assert!(
            c.manifest.files.iter().any(|(f, _)| f == "CLAUDE.md"),
            "reason: settings_not_listed {:?}",
            c.manifest
        );
        assert_eq!(check("My Repo", p, data.path(), &hooks), Err("name"));
        assert_eq!(
            check("x", "relative/path", data.path(), &hooks),
            Err("path")
        );
        let sub = r.path().join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        assert_eq!(
            check("x", sub.to_str().unwrap(), data.path(), &hooks),
            Err("not_repo_top")
        );
        let plain = tempfile::tempdir().unwrap();
        assert_eq!(
            check("x", plain.path().to_str().unwrap(), data.path(), &hooks),
            Err("not_a_repo")
        );
        let inside = data.path().join("seat-work").join("t");
        std::fs::create_dir_all(&inside).unwrap();
        g(&inside, &["init", "-q"]);
        assert_eq!(
            check("x", inside.to_str().unwrap(), data.path(), &hooks),
            Err("path_inside_aibd")
        );
    }

    #[test]
    fn registering_or_opening_with_a_repo_asks_a_fingerprint_and_nothing_is_written_unasked() {
        let r = repo();
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("seat-git").join("hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let c = check("my-repo", r.path().to_str().unwrap(), data.path(), &hooks).unwrap();
        let a = register_ask(&c, "page:01".into());
        assert_eq!(a.operation, OP_REGISTER);
        assert!(crate::owner_ok::page_category(a.category));
        let params = String::from_utf8(a.params.clone()).unwrap();
        assert!(
            params.contains("CLAUDE.md"),
            "reason: settings_not_in_window {params}"
        );
        assert!(
            !crate::owner_ok::approved_for("t1", &a),
            "reason: stub_approves"
        );
        assert!(
            !file(data.path()).exists(),
            "reason: written_without_ticket"
        );
        assert!(names(data.path()).is_empty());
    }

    #[test]
    fn a_copy_needs_room_and_is_never_merged_into_the_repo() {
        let gb = 1024 * 1024 * 1024;
        assert_eq!(room_for_copy(0, Some(3 * gb)), Ok(()));
        assert_eq!(
            room_for_copy(REPO_COPIES_MAX - 1, Some(REPO_DISK_FREE_MIN)),
            Ok(())
        );
        assert_eq!(
            room_for_copy(REPO_COPIES_MAX, Some(100 * gb)),
            Err("repo_copies_full")
        );
        assert_eq!(
            room_for_copy(0, Some(REPO_DISK_FREE_MIN - 1)),
            Err("disk_low")
        );
        assert_eq!(
            room_for_copy(0, None),
            Err("disk_low"),
            "reason: unread_disk_open"
        );
        assert!(is_repo_copy("repo:my-repo"));
        assert!(
            !is_repo_copy("seat_aaaa1111"),
            "reason: seat_author_refused"
        );
    }

    fn registered(r: &Path, data: &Path) -> (Repo, PathBuf, PathBuf) {
        let hooks = data.join("seat-git").join("hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let c = check("my-repo", r.to_str().unwrap(), data, &hooks).unwrap();
        let reg = Repo {
            name: c.name,
            path: c.top.to_string_lossy().into_owned(),
            remote: c.remote,
            manifest: c.manifest,
            added_at: 0,
        };
        (reg, data.join("seat-work"), hooks)
    }

    #[test]
    fn an_archived_copy_stops_counting_only_when_its_head_was_taken() {
        // 5d33a PREREAD-workrepo r3 L: the archive's own call, not only release_copy
        let r = repo();
        let data = tempfile::tempdir().unwrap();
        let (reg, work, hooks) = registered(r.path(), data.path());
        let (t1, h1) = prepare_tree(&reg, &work, &hooks, "wt01", "1120", None).unwrap();
        assert_eq!(repo_copies(&work), 1, "precondition: one copy");
        let c = collect_copy(&t1, &h1, Some(r.path()), &hooks).expect("precondition: collected");
        assert!(!c.removed, "reason: copy_folder_removed");
        assert_eq!(
            repo_copies(&work),
            0,
            "reason: collected_copy_still_counted"
        );
        // a collect that fails (the registered repo is gone) leaves the copy counted
        let (t2, h2) = prepare_tree(&reg, &work, &hooks, "wt02", "1120", None).unwrap();
        std::fs::remove_dir_all(r.path().join(".git")).unwrap();
        assert!(
            collect_copy(&t2, &h2, Some(r.path()), &hooks).is_err(),
            "precondition: collect fails"
        );
        assert_eq!(repo_copies(&work), 1, "reason: failed_collect_released");
    }

    #[test]
    // one wake-up for the answer or at the end of the bound, twice: not a periodic timer
    // (ADR 0005 named exception, as `sessions::wait_timeout`)
    #[allow(clippy::disallowed_methods)]
    fn a_copy_opens_only_while_no_other_copy_is_opening() {
        // 5d33a PREREAD-workrepo r2 N3: two opens counting at once could pass the cap
        let r = repo();
        let data = tempfile::tempdir().unwrap();
        let (reg, work, hooks) = registered(r.path(), data.path());
        let held = COPY_OPEN
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let (tx, rx) = std::sync::mpsc::channel();
        let w = work.clone();
        let h = hooks.clone();
        std::thread::spawn(move || {
            let _ = tx.send(prepare_tree(&reg, &w, &h, "wt01", "1120", None).is_ok());
        });
        assert!(
            rx.recv_timeout(std::time::Duration::from_millis(800))
                .is_err(),
            "reason: opened_while_another_opens"
        );
        drop(held);
        assert_eq!(
            rx.recv_timeout(std::time::Duration::from_secs(60)),
            Ok(true),
            "reason: open_never_ran"
        );
        assert_eq!(repo_copies(&work), 1);
    }

    #[test]
    fn a_tree_opens_only_while_the_settings_are_what_was_registered() {
        let r = repo();
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("seat-git").join("hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let c = check("my-repo", r.path().to_str().unwrap(), data.path(), &hooks).unwrap();
        let reg = Repo {
            name: c.name,
            path: c.top.to_string_lossy().into_owned(),
            remote: c.remote,
            manifest: c.manifest,
            added_at: 0,
        };
        let work = data.path().join("seat-work");
        let (tree, head) = prepare_tree(&reg, &work, &hooks, "wt01", "1119", None).unwrap();
        assert!(tree.join("a.txt").is_file(), "reason: tree_empty");
        // 519d0 06:35 / 5d33a N6: its own copy, not a worktree of the registered repo
        assert!(tree.join(".git").is_dir(), "reason: not_its_own_copy");
        assert!(
            !r.path().join(".git").join("worktrees").exists(),
            "reason: registered_repo_git_written"
        );
        assert_eq!(
            crate::seat_worktree::base_of(&tree).map(|(_, a)| a),
            Some("repo:my-repo".to_owned()),
            "reason: copy_author"
        );
        assert_eq!(head.len(), 40, "reason: head {head}");
        assert!(tree.starts_with(&work), "reason: tree_outside_seat_work");
        // a second seat of the same repo: the first tree did not change what was registered
        let (t2, _) = prepare_tree(&reg, &work, &hooks, "wt02", "1119", None)
            .expect("reason: second_tree_refused");
        assert_ne!(t2, tree);
        // ORDER-webonly-today 12: a copy on a named base; one the repo has not, or not a full
        // commit, is refused
        let first = head.clone();
        std::fs::write(r.path().join("b.txt"), "b").unwrap();
        g(r.path(), &["add", "b.txt"]);
        g(r.path(), &["commit", "-q", "-m", "b"]);
        let (t4, h4) = prepare_tree(&reg, &work, &hooks, "wt04", "1119", Some(&first))
            .expect("reason: base_refused");
        assert_eq!(h4, first, "reason: not_on_base");
        // PREREAD-workrepo L2: three copies so far, each counted once
        assert_eq!(repo_copies(&work), 3, "reason: copies_counted");
        // r2 L1: a released copy no longer counts; N4: an unreadable base file does
        release_copy(&t4).unwrap();
        assert_eq!(repo_copies(&work), 2, "reason: released_still_counted");
        std::fs::write(work.join("wt01").join(crate::seat_worktree::BASE_FILE), "{").unwrap();
        assert_eq!(repo_copies(&work), 2, "reason: broken_base_not_counted");
        std::fs::remove_file(work.join("wt04").join(RELEASED_FILE)).unwrap();
        assert_eq!(repo_copies(&work), 3);
        assert!(!t4.join("b.txt").exists(), "reason: copy_past_base");
        assert_eq!(
            prepare_tree(&reg, &work, &hooks, "wt05", "1119", Some(&"0".repeat(40))),
            Err("base_not_in_repo")
        );
        assert!(!work.join("wt05").exists(), "reason: refused_copy_left");
        assert_eq!(
            prepare_tree(&reg, &work, &hooks, "wt06", "1119", Some(&first[..12])),
            Err("base")
        );
        assert!(
            !work.join("wt06").exists(),
            "reason: cloned_before_base_check"
        );
        std::fs::write(r.path().join("CLAUDE.md"), "other rules").unwrap();
        assert_eq!(
            prepare_tree(&reg, &work, &hooks, "wt03", "1119", None),
            Err("project_settings_changed")
        );
        assert!(
            !work.join("wt03").join("my-repo").exists(),
            "reason: opened_after_change"
        );
    }
}
