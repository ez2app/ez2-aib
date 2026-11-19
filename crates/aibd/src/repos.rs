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

const FILE: &str = "repos.json";

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
        resource: c.top.to_string_lossy().into_owned(),
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
        resource: format!("{persona_uid} @ {}", repo.name),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
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
        crate::owner_ok::Asked::Unavailable(why) => (
            StatusCode::FORBIDDEN,
            Json(json!({"code": "forbidden", "reason": why,
                "message": "this needs the owner's fingerprint, and it cannot be asked on this machine yet",
                "request_id": crate::guard::request_id()})),
        )
            .into_response(),
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
) -> Result<(PathBuf, String), &'static str> {
    std::fs::create_dir_all(hooks).map_err(|_| "hooks")?;
    let src = Path::new(&repo.path);
    seat_worktree::check_unchanged(&repo.manifest, src, hooks)?;
    let tree = seat_worktree::tree_path(&seat_work.join(tree_id), &repo.name);
    let branch = seat_worktree::branch_name(tree_id, mmdd);
    // 519d0 06:35 (5d33a N6): a seat with a registered repo always works in its own copy, a
    // separate clone (COUNCIL-worktree-gitdir-7ebca-1120 (a), b4297's file:// clone), never a
    // worktree writing the registered repo's `.git`; a work seat's sandbox then needs nothing
    // outside its folder to commit
    let author = format!("{REPO_AUTHOR_PREFIX}{}", repo.name);
    let head = seat_worktree::open_clone(src, &tree, &branch, &author, hooks)
        .map_err(|_| "tree_not_opened")?;
    Ok((tree, head))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

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
        let (tree, head) = prepare_tree(&reg, &work, &hooks, "wt01", "1119").unwrap();
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
        let (t2, _) =
            prepare_tree(&reg, &work, &hooks, "wt02", "1119").expect("reason: second_tree_refused");
        assert_ne!(t2, tree);
        std::fs::write(r.path().join("CLAUDE.md"), "other rules").unwrap();
        assert_eq!(
            prepare_tree(&reg, &work, &hooks, "wt03", "1119"),
            Err("project_settings_changed")
        );
        assert!(
            !work.join("wt03").join("my-repo").exists(),
            "reason: opened_after_change"
        );
    }
}
