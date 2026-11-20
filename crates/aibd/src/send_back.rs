//! N-webonly-c6-merge (DESIGN-web-merge-a9136-1120 r2): what a seat committed in its copy of a
//! registered repo goes back to that repo as one new branch `web/<seat>-<MMDD>-<n>`. Nothing
//! is checked out, no branch the person uses moves, nothing is pushed.
//!
//! No git runs in the copy (5d33a M1): the seat writes its `.git/config` and
//! `objects/info/alternates`. aibd copies the copy's object files, as plain files, into a bare
//! scratch repository it made itself, checks the head there, and the registered repo fetches
//! from the scratch one with `fetch.fsckObjects`. The branch is made with `update-ref` against
//! the all-zero old value, so one that exists is never moved (L1).

use crate::api::AppState;
use crate::seat_worktree::{git, loads_as_settings, repo_safe};
use axum::{http::StatusCode, response::IntoResponse, response::Response, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};

/// The act type of the built-in page's 送回倉.
pub const SEND_BACK: &str = "seat_send_back";
/// The owner_ok operation of one send.
pub const OP_SEND_BACK: &str = "repo.send_back";

/// The object files of one copy are copied only up to this many bytes (T9).
pub const MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;
/// ... and this many files.
pub const MAX_FILES: usize = 200_000;
/// How many numbers a branch name tries (`-1` … `-20`) before giving up.
pub const BRANCH_TRIES: usize = 20;
/// At most this many refused files are named to the page.
pub const NAMED_MAX: usize = 20;

const ZERO: &str = "0000000000000000000000000000000000000000";
const SCRATCH_REF: &str = "refs/heads/send";
const SYMLINK_MODE: &str = "120000";
/// Files a person's git reads from a branch they check out (5d33a L2): a submodule's URL and
/// path, where LFS sends its requests.
const CHECKOUT_SETTINGS: [&str; 2] = [".gitmodules", ".lfsconfig"];
/// Shown, not refused: a filter or diff driver runs only when the repo's own config names it.
const LISTED: [&str; 1] = [".gitattributes"];

/// What would go back, as the fingerprint window and the page show it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub head: String,
    pub commits: usize,
    pub files: usize,
    pub listed: Vec<String>,
}

/// Why nothing goes back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refused {
    /// The repo already reaches the head from one of its refs.
    Nothing,
    /// A commit to send adds or changes one of these (settings a provider or a person's git
    /// loads, a workflow, a symbolic link).
    SettingsTouched(Vec<String>),
    /// The copy's objects do not hold the whole head, or git's object check refused them.
    ObjectsMissing(String),
    /// More object files than [`MAX_FILES`] or [`MAX_BYTES`].
    TooBig,
    /// The registered repo's own settings name a program ([`repo_safe`]).
    RepoUnsafe(String),
    /// Every tried branch name is taken, or git failed in the registered repo.
    Git(String),
}

/// A full commit name as aibd passes it to git: 40 lower-case hex.
pub fn sha_ok(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// `web/<seat code>-<MMDD>`: built by aibd only (T3), the page never names a branch.
pub fn branch_stem(seat_code: &str, mmdd: &str) -> Option<String> {
    let code_ok = (1..=12).contains(&seat_code.len())
        && seat_code
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit());
    let date_ok = mmdd.len() == 4 && mmdd.bytes().all(|b| b.is_ascii_digit());
    (code_ok && date_ok).then(|| format!("web/{seat_code}-{mmdd}"))
}

fn hex_name(name: &str, len: usize) -> bool {
    name.len() == len && name.bytes().all(|b| b.is_ascii_hexdigit())
}

/// A pack file git itself writes: `pack-<hex>.pack|idx|rev`.
fn pack_name(name: &str) -> bool {
    let Some(rest) = name.strip_prefix("pack-") else {
        return false;
    };
    let Some((hex, ext)) = rest.rsplit_once('.') else {
        return false;
    };
    (hex.len() == 40 || hex.len() == 64)
        && hex.bytes().all(|b| b.is_ascii_hexdigit())
        && ["pack", "idx", "rev"].contains(&ext)
}

#[cfg(unix)]
fn open_nofollow(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::fcntl::OFlag::O_NOFOLLOW.bits())
        .open(p)
}

#[cfg(windows)]
fn open_nofollow(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::windows::fs::OpenOptionsExt;
    // FILE_FLAG_OPEN_REPARSE_POINT: a link is opened as itself, and is then not a plain file
    const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(p)
}

struct Budget {
    files: usize,
    bytes: u64,
}

/// One plain file copied; a link or anything else that is not a plain file is left out (the
/// object it stood for is then missing, and the head does not check out). The file is opened
/// without following a link and looked at again once open (5d33a N: a file swapped after the
/// first look).
fn copy_plain(src: &Path, dst: &Path, left: &mut Budget) -> Result<(), Refused> {
    let Ok(f) = open_nofollow(src) else {
        return Ok(());
    };
    let Ok(meta) = f.metadata() else {
        return Ok(());
    };
    if !meta.is_file() {
        return Ok(());
    }
    if left.files == 0 || meta.len() > left.bytes {
        return Err(Refused::TooBig);
    }
    let mut out = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(dst)
        .map_err(|e| Refused::Git(format!("scratch file: {e}")))?;
    // a file that grows while it is read stops at the budget
    let n = std::io::copy(&mut f.take(left.bytes), &mut out)
        .map_err(|e| Refused::Git(format!("scratch copy: {e}")))?;
    left.files -= 1;
    left.bytes -= n.min(left.bytes);
    Ok(())
}

fn plain_dir(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_dir())
}

/// The copy's loose objects and packs into `dst` (a bare repository's `objects`), by name:
/// `xx/<38 hex>` and `pack/pack-<hex>.pack|idx|rev`. `info/` (alternates) and every other
/// name are not read.
fn copy_objects(src: &Path, dst: &Path, max_files: usize, max_bytes: u64) -> Result<(), Refused> {
    if !plain_dir(src) {
        return Err(Refused::ObjectsMissing(
            "the copy has no objects folder".into(),
        ));
    }
    let mut left = Budget {
        files: max_files,
        bytes: max_bytes,
    };
    let rd = std::fs::read_dir(src).map_err(|e| Refused::ObjectsMissing(e.to_string()))?;
    for e in rd.flatten() {
        let name = e.file_name();
        let Some(name) = name.to_str() else { continue };
        let is_pack = name == "pack";
        if !(is_pack || hex_name(name, 2)) {
            continue;
        }
        let from = src.join(name);
        if !plain_dir(&from) {
            continue;
        }
        let to = dst.join(name);
        std::fs::create_dir_all(&to).map_err(|e| Refused::Git(format!("scratch folder: {e}")))?;
        let Ok(inner) = std::fs::read_dir(&from) else {
            continue;
        };
        for f in inner.flatten() {
            let fname = f.file_name();
            let Some(fname) = fname.to_str() else {
                continue;
            };
            let wanted = if is_pack {
                pack_name(fname)
            } else {
                hex_name(fname, 38) || hex_name(fname, 62)
            };
            if wanted {
                copy_plain(&from.join(fname), &to.join(fname), &mut left)?;
            }
        }
    }
    Ok(())
}

/// The scratch repository: made by aibd, holding the copy's objects and one ref on `sha`,
/// which is checked to be a commit whose history and trees are all there.
pub(crate) fn stage(tree: &Path, sha: &str, hooks: &Path, scratch: &Path) -> Result<(), Refused> {
    std::fs::create_dir_all(scratch).map_err(|e| Refused::Git(format!("scratch: {e}")))?;
    git(scratch, hooks, &["init", "--bare", "-q", "."]).map_err(Refused::Git)?;
    copy_objects(
        &tree.join(".git").join("objects"),
        &scratch.join("objects"),
        MAX_FILES,
        MAX_BYTES,
    )?;
    let commit = format!("{sha}^{{commit}}");
    git(scratch, hooks, &["cat-file", "-e", &commit]).map_err(Refused::ObjectsMissing)?;
    git(scratch, hooks, &["rev-list", "--objects", "--count", sha])
        .map_err(Refused::ObjectsMissing)?;
    // 5d33a N: a fetch of a bare commit name may be refused; a ref of aibd's own is fetched
    git(scratch, hooks, &["update-ref", SCRATCH_REF, sha]).map_err(Refused::Git)?;
    Ok(())
}

/// The paths of `git log --raw -z` output with what each became: `(new mode, path)`.
fn raw_entries(raw: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut it = raw.split('\0');
    while let Some(tok) = it.next() {
        let tok = tok.trim_start_matches('\n');
        // ":<old mode> <new mode> <old sha> <new sha> <status>", then the path
        let Some(meta) = tok.strip_prefix(':') else {
            continue;
        };
        let new_mode = meta.split(' ').nth(1).unwrap_or_default().to_owned();
        if let Some(path) = it.next() {
            out.push((new_mode, path.to_owned()));
        }
    }
    out
}

fn base_name(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase()
}

/// A path a commit must not bring into the registered repo this way.
fn refused_path(new_mode: &str, path: &str) -> bool {
    loads_as_settings(path)
        || CHECKOUT_SETTINGS.contains(&base_name(path).as_str())
        || new_mode == SYMLINK_MODE
}

fn plan_in(
    repo: &Path,
    tree: &Path,
    sha: &str,
    hooks: &Path,
    scratch: &Path,
) -> Result<Plan, Refused> {
    if !sha_ok(sha) {
        return Err(Refused::ObjectsMissing(
            "head is not a full commit name".into(),
        ));
    }
    repo_safe(repo).map_err(Refused::RepoUnsafe)?;
    stage(tree, sha, hooks, scratch)?;
    let scratch_s = scratch.to_string_lossy().into_owned();
    // objects only: no ref of the repo is written here, FETCH_HEAD neither
    git(
        repo,
        hooks,
        &[
            "-c",
            "protocol.file.allow=always",
            "-c",
            "fetch.fsckObjects=true",
            "fetch",
            "-q",
            "--no-tags",
            "--no-write-fetch-head",
            &scratch_s,
            SCRATCH_REF,
        ],
    )
    .map_err(Refused::ObjectsMissing)?;
    // 5d33a r2 L1: counted in the repo after the fetch, against every ref it has (a commit made
    // there after the copy was opened is not in the scratch repository)
    let n =
        git(repo, hooks, &["rev-list", "--count", sha, "--not", "--all"]).map_err(Refused::Git)?;
    let commits = n
        .trim()
        .parse::<usize>()
        .map_err(|e| Refused::Git(e.to_string()))?;
    if commits == 0 {
        return Err(Refused::Nothing);
    }
    // each commit against every parent (-m), renames as delete + add, as 合回 reads them
    let raw = git(
        repo,
        hooks,
        &[
            "log",
            "-m",
            "--no-renames",
            "--raw",
            "--no-abbrev",
            "--format=",
            "-z",
            sha,
            "--not",
            "--all",
        ],
    )
    .map_err(Refused::Git)?;
    let entries = raw_entries(&raw);
    let touched: BTreeSet<String> = entries
        .iter()
        .filter(|(mode, path)| refused_path(mode, path))
        .map(|(_, path)| path.clone())
        .collect();
    if !touched.is_empty() {
        return Err(Refused::SettingsTouched(
            touched.into_iter().take(NAMED_MAX).collect(),
        ));
    }
    let files: BTreeSet<&str> = entries.iter().map(|(_, p)| p.as_str()).collect();
    let listed: BTreeSet<String> = entries
        .iter()
        .filter(|(_, p)| LISTED.contains(&base_name(p).as_str()))
        .map(|(_, p)| p.clone())
        .collect();
    Ok(Plan {
        head: sha.to_owned(),
        commits,
        files: files.len(),
        listed: listed.into_iter().take(NAMED_MAX).collect(),
    })
}

/// What sending `sha` of the copy at `tree` back to the registered repo at `repo` would
/// bring: its objects are in the repo afterwards (no ref on them), the scratch repository is
/// gone. `scratch` is a folder of aibd's own that does not exist yet.
pub fn plan(
    repo: &Path,
    tree: &Path,
    sha: &str,
    hooks: &Path,
    scratch: &Path,
) -> Result<Plan, Refused> {
    let got = plan_in(repo, tree, sha, hooks, scratch);
    // left behind only costs disk; the next send uses another folder
    let _ = std::fs::remove_dir_all(scratch);
    got
}

/// The new branch on `sha` in the registered repo: `<stem>-<n>` for the first `n` not taken.
/// A branch is only ever created (`update-ref` with the all-zero old value).
pub fn write_branch(repo: &Path, hooks: &Path, stem: &str, sha: &str) -> Result<String, Refused> {
    if !sha_ok(sha) {
        return Err(Refused::Git("head is not a full commit name".into()));
    }
    let mut last = String::new();
    for n in 1..=BRANCH_TRIES {
        let name = format!("{stem}-{n}");
        git(repo, hooks, &["check-ref-format", "--branch", &name]).map_err(Refused::Git)?;
        let full = format!("refs/heads/{name}");
        match git(repo, hooks, &["update-ref", &full, sha, ZERO]) {
            Ok(_) => return Ok(name),
            Err(e) => {
                let taken = git(repo, hooks, &["rev-parse", "--verify", "-q", &full]).is_ok();
                if !taken {
                    return Err(Refused::Git(e));
                }
                last = e;
            }
        }
    }
    Err(Refused::Git(format!(
        "every name up to {stem}-{BRANCH_TRIES} is taken: {last}"
    )))
}

/// The fingerprint one send asks: the repo, the seat, the head (40 hex) and the branch stem
/// are all in the parameters, with what the window says of them (commits, files), so a ticket
/// approves exactly this head going to this repo (DESIGN §3).
pub fn send_ask(
    repo: &str,
    seat_id: &str,
    stem: &str,
    p: &Plan,
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "repo": repo, "seat_id": seat_id, "head": p.head, "branch": stem,
        "commits": p.commits, "files": p.files, "listed": p.listed,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_SEND_BACK,
        resource: format!("{repo} <- {stem} {}", &p.head[..12.min(p.head.len())]),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

fn conflict(code: &str, message: &str, extra: Value) -> Response {
    let mut body = json!({"code": code, "message": message,
                          "request_id": crate::guard::request_id()});
    if let (Some(b), Some(e)) = (body.as_object_mut(), extra.as_object()) {
        b.extend(e.clone());
    }
    (StatusCode::CONFLICT, Json(body)).into_response()
}

fn bad(reason: &str, message: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(
            json!({"code": "bad_request", "reason": reason, "message": message,
                    "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

/// The page's word for a [`Refused`].
pub fn refused_response(r: &Refused) -> Response {
    match r {
        Refused::Nothing => conflict(
            "nothing_to_send",
            "the repo already has every commit of this seat",
            json!({}),
        ),
        Refused::SettingsTouched(files) => conflict(
            "settings_touched",
            "a commit touches settings, a workflow or a link; it is not sent this way",
            json!({"files": files}),
        ),
        Refused::ObjectsMissing(_) => conflict(
            "objects_missing",
            "the seat's copy does not hold the whole head, or git refused its objects",
            json!({}),
        ),
        Refused::TooBig => conflict(
            "too_big",
            "the seat's copy holds too much to send",
            json!({}),
        ),
        Refused::RepoUnsafe(why) => conflict(
            "repo_unsafe",
            "the registered repo's own settings name a program; nothing is run there",
            json!({"why": why}),
        ),
        Refused::Git(_) => conflict("send_failed", "git could not make the branch", json!({})),
    }
}

/// The seat list's `repo_copy` (IFACE-web-merge r1 §1): `{repo, sendable}` for a seat in its
/// copy of a registered repo, else `null`. `sendable` is a hint for the button (the seat does
/// not run now); the act itself decides.
pub fn repo_copy_of(cwd: &str, live: bool) -> Value {
    let tree = Path::new(cwd);
    crate::seat_worktree::base_of(tree)
        .and_then(|(_, author)| {
            author
                .strip_prefix(crate::repos::REPO_AUTHOR_PREFIX)
                .map(str::to_owned)
        })
        .map_or(Value::Null, |repo| {
            // the copy's committed head, read as plain files (1a93b: the page's 跑收據 needs
            // it); null when the copy's .git names none
            let head = crate::seat_worktree::clone_head(tree).map(|(_, sha)| sha);
            json!({"repo": repo, "sendable": !live, "head": head})
        })
}

#[derive(Debug, Deserialize)]
struct RawSend {
    seat_id: String,
    #[serde(default)]
    ticket: Option<String>,
    #[serde(default)]
    head: Option<String>,
}

fn plan_json(repo: &str, branch: &str, p: &Plan) -> Value {
    json!({"repo": repo, "branch": branch, "head": p.head, "commits": p.commits,
           "files": p.files, "listed": p.listed})
}

/// `POST /api/v1/act {type: "seat_send_back", seat_id, ticket?, head?}` from the built-in page
/// in this pairing (the caller refused the rest). Without a ticket: aibd reads what would go
/// back and asks the fingerprint, `202 {ticket, plan}`. With one and the plan's `head`: aibd
/// reads again and acts only when the ticket approves this very reading, then
/// `200 {done, repo, branch, head, commits, files}` and `seat.sent_to_repo` says so.
pub async fn act(st: AppState, body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<RawSend>(body) else {
        return bad("body", "body must be {type, seat_id, ticket?, head?}");
    };
    if req.ticket.is_some() != req.head.is_some() {
        return bad(
            "head",
            "a ticket comes with the head of its plan, and only then",
        );
    }
    if st.sessions.live_seats().contains(&req.seat_id) {
        return conflict("seat_busy", "the seat runs now; stop it first", json!({}));
    }
    let (store, sid) = (st.store.clone(), req.seat_id.clone());
    let meta = match tokio::task::spawn_blocking(move || store.seat_meta(&sid)).await {
        Ok(Ok(Some(m))) => m,
        Ok(Ok(None)) => {
            return crate::sessions::ApiError::new(
                aib_core::ErrorCode::SeatNotFound,
                "no such seat",
            )
            .into_response()
        }
        _ => return bad("internal", "the seat could not be read"),
    };
    let tree = PathBuf::from(&meta.cwd);
    let private = st.sessions.private_dir().to_path_buf();
    let data = private.parent().unwrap_or(&private).to_path_buf();
    // the repo's folder is the registered one, never a path read beside the copy
    let registered = crate::seat_worktree::base_of(&tree)
        .and_then(|(_, author)| {
            author
                .strip_prefix(crate::repos::REPO_AUTHOR_PREFIX)
                .map(str::to_owned)
        })
        .and_then(|name| crate::repos::find(&data, &name));
    let Some(repo) = registered else {
        return bad(
            "seat_id",
            "送回倉 is for a seat in its copy of a registered repo",
        );
    };
    let Some((_, sha)) = crate::seat_worktree::clone_head(&tree) else {
        return refused_response(&Refused::ObjectsMissing("no readable head".into()));
    };
    if req.head.as_deref().is_some_and(|h| h != sha) {
        return conflict(
            "head_moved",
            "the seat's head is not the one the fingerprint was asked for; nothing written",
            json!({}),
        );
    }
    let free = crate::workspace::disk_of(Path::new(&repo.path)).map(|d| d.available);
    if !free.is_some_and(|a| a >= crate::repos::REPO_DISK_FREE_MIN) {
        return conflict("disk_low", "the repo's disk is nearly full", json!({}));
    }
    let now = crate::sessions::now_rfc3339();
    let mmdd = format!("{}{}", &now[5..7], &now[8..10]);
    let Some(stem) = branch_stem(&crate::seats::seat_code(&req.seat_id), &mmdd) else {
        return bad("seat_id", "the seat's code cannot name a branch");
    };
    let Ok(scratch_id) = crate::sessions::new_id("sb") else {
        return bad("internal", "no scratch name");
    };
    let hooks = crate::seats::seat_git_hooks(&private);
    let scratch = data.join("send-back").join(scratch_id);
    let (repo_dir, tree2, sha2, hooks2) =
        (PathBuf::from(&repo.path), tree, sha.clone(), hooks.clone());
    let planned = tokio::task::spawn_blocking(move || {
        std::fs::create_dir_all(&hooks2).map_err(|e| Refused::Git(e.to_string()))?;
        plan(&repo_dir, &tree2, &sha2, &hooks2, &scratch)
    })
    .await;
    let p = match planned {
        Ok(Ok(p)) => p,
        Ok(Err(r)) => return refused_response(&r),
        Err(_) => return bad("internal", "the copy could not be read"),
    };
    let ask = send_ask(&repo.name, &req.seat_id, &stem, &p, by);
    let Some(ticket) = req.ticket else {
        return match crate::owner_ok::ask(&ask) {
            crate::owner_ok::Asked::Ticket(t) => (
                StatusCode::ACCEPTED,
                Json(
                    json!({"done": false, "ticket": t, "reason": "owner_check_pending",
                            "plan": plan_json(&repo.name, &format!("{stem}-1"), &p)}),
                ),
            )
                .into_response(),
            // one ask only: asking again could open a second window for the same send
            crate::owner_ok::Asked::Unavailable(why) => (
                StatusCode::FORBIDDEN,
                Json(json!({"code": "forbidden", "reason": why,
                    "message": "this needs the owner's fingerprint, and it cannot be asked on this machine yet",
                    "request_id": crate::guard::request_id()})),
            )
                .into_response(),
        };
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let (repo_dir, stem2, sha3) = (PathBuf::from(&repo.path), stem, sha);
    let written =
        tokio::task::spawn_blocking(move || write_branch(&repo_dir, &hooks, &stem2, &sha3)).await;
    let branch = match written {
        Ok(Ok(b)) => b,
        Ok(Err(r)) => return refused_response(&r),
        Err(_) => return bad("internal", "the branch could not be made"),
    };
    crate::seats::side_event(
        &st,
        "seat",
        "seat.sent_to_repo",
        json!({"seat_id": req.seat_id, "repo": repo.name, "branch": branch, "head": p.head,
               "commits": p.commits, "by": "page"}),
    )
    .await;
    let mut done = plan_json(&repo.name, &branch, &p);
    if let Some(o) = done.as_object_mut() {
        o.insert("done".into(), json!(true));
        o.remove("listed");
    }
    Json(done).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::seat_worktree::{open_clone, tree_path};
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

    fn out(dir: &Path, args: &[&str]) -> String {
        let o = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(args)
            .output()
            .unwrap();
        String::from_utf8_lossy(&o.stdout).trim().to_owned()
    }

    struct Fix {
        _repo_dir: tempfile::TempDir,
        data: tempfile::TempDir,
        repo: PathBuf,
        tree: PathBuf,
        hooks: PathBuf,
    }

    impl Fix {
        fn scratch(&self, n: usize) -> PathBuf {
            self.data.path().join("send-back").join(format!("s{n}"))
        }
        fn commit(&self, file: &str, body: &str) -> String {
            let f = self.tree.join(file);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, body).unwrap();
            g(&self.tree, &["add", "-A"]);
            g(&self.tree, &["commit", "-q", "-m", file]);
            out(&self.tree, &["rev-parse", "HEAD"])
        }
        fn plan(&self, sha: &str, n: usize) -> Result<Plan, Refused> {
            plan(&self.repo, &self.tree, sha, &self.hooks, &self.scratch(n))
        }
    }

    /// A registered repo with one commit and a seat's copy of it.
    fn fix() -> Fix {
        let repo_dir = tempfile::tempdir().unwrap();
        let repo = repo_dir.path().to_path_buf();
        g(&repo, &["init", "-q"]);
        std::fs::write(repo.join("README"), "r").unwrap();
        g(&repo, &["add", "-A"]);
        g(&repo, &["commit", "-q", "-m", "first"]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("wt1"), "repo");
        open_clone(&repo, &tree, "seat/wt1-1120", "repo:repo", &hooks).unwrap();
        Fix {
            _repo_dir: repo_dir,
            data,
            repo,
            tree,
            hooks,
        }
    }

    #[test]
    fn two_commits_go_back_as_one_new_branch_and_the_checkout_stays() {
        let f = fix();
        f.commit("a.txt", "a");
        let sha = f.commit("dir/b.txt", "b");
        let head_before = out(&f.repo, &["rev-parse", "HEAD"]);
        let branch_before = out(&f.repo, &["rev-parse", "--abbrev-ref", "HEAD"]);
        let p = f.plan(&sha, 1).unwrap();
        assert_eq!((p.commits, p.files), (2, 2), "reason: plan_counts {p:?}");
        assert_eq!(p.head, sha, "reason: plan_head");
        assert!(p.listed.is_empty(), "reason: plan_listed {p:?}");
        assert!(!f.scratch(1).exists(), "reason: scratch_left");
        assert_eq!(
            out(&f.repo, &["for-each-ref", "refs/heads/web"]),
            "",
            "reason: plan_wrote_a_ref"
        );
        let name = write_branch(&f.repo, &f.hooks, "web/a9136-1120", &sha).unwrap();
        assert_eq!(name, "web/a9136-1120-1", "reason: branch_name");
        assert_eq!(
            out(&f.repo, &["rev-parse", "refs/heads/web/a9136-1120-1"]),
            sha,
            "reason: branch_head"
        );
        assert_eq!(
            out(&f.repo, &["rev-parse", "HEAD"]),
            head_before,
            "reason: head_moved"
        );
        assert_eq!(
            out(&f.repo, &["rev-parse", "--abbrev-ref", "HEAD"]),
            branch_before,
            "reason: branch_switched"
        );
        assert!(!f.repo.join("a.txt").exists(), "reason: checkout_touched");
        assert_eq!(
            out(&f.repo, &["status", "--porcelain"]),
            "",
            "reason: repo_dirty"
        );
    }

    #[test]
    fn a_taken_name_is_never_moved_the_next_number_is_used() {
        let f = fix();
        let first = f.commit("a.txt", "a");
        f.plan(&first, 1).unwrap();
        let one = write_branch(&f.repo, &f.hooks, "web/a9136-1120", &first).unwrap();
        // the second head is a descendant of the first: a fetch refspec would fast-forward
        let second = f.commit("b.txt", "b");
        f.plan(&second, 2).unwrap();
        let two = write_branch(&f.repo, &f.hooks, "web/a9136-1120", &second).unwrap();
        assert_eq!(
            (one.as_str(), two.as_str()),
            ("web/a9136-1120-1", "web/a9136-1120-2")
        );
        assert_eq!(
            out(&f.repo, &["rev-parse", "refs/heads/web/a9136-1120-1"]),
            first,
            "reason: taken_branch_moved"
        );
        assert_eq!(
            out(&f.repo, &["rev-parse", "refs/heads/web/a9136-1120-2"]),
            second
        );
    }

    #[test]
    fn a_head_the_repo_already_reaches_is_nothing_to_send() {
        let f = fix();
        let base = out(&f.tree, &["rev-parse", "HEAD"]);
        assert_eq!(
            f.plan(&base, 1),
            Err(Refused::Nothing),
            "reason: base_head_sent"
        );
        let sha = f.commit("a.txt", "a");
        f.plan(&sha, 2).unwrap();
        write_branch(&f.repo, &f.hooks, "web/a9136-1120", &sha).unwrap();
        assert_eq!(f.plan(&sha, 3), Err(Refused::Nothing), "reason: sent_twice");
    }

    /// 5d33a r2 L1: the repo got a commit of its own after the copy was opened.
    #[test]
    fn a_repo_that_moved_on_after_the_copy_still_counts_only_the_seats_commits() {
        let f = fix();
        std::fs::write(f.repo.join("later.txt"), "l").unwrap();
        g(&f.repo, &["add", "-A"]);
        g(&f.repo, &["commit", "-q", "-m", "later"]);
        let sha = f.commit("a.txt", "a");
        let p = f.plan(&sha, 1).unwrap();
        assert_eq!(p.commits, 1, "reason: counted_against_scratch {p:?}");
    }

    #[test]
    fn settings_a_workflow_a_submodule_file_and_a_link_are_refused_by_name() {
        for (file, body) in [
            (".claude/settings.json", "{}"),
            (".github/workflows/x.yml", "on: push"),
            ("sub/.gitmodules", "[submodule \"x\"]"),
            (".lfsconfig", "[lfs]"),
        ] {
            let f = fix();
            let sha = f.commit(file, body);
            assert_eq!(
                f.plan(&sha, 1),
                Err(Refused::SettingsTouched(vec![file.to_owned()])),
                "reason: {file}_sent"
            );
        }
        let f = fix();
        f.commit("plain.txt", "p");
        // a link as git stores it, made without the file system (Windows has no plain symlink)
        let blob = {
            let p = f.tree.join("target-name");
            std::fs::write(&p, "/etc/hosts").unwrap();
            let h = out(&f.tree, &["hash-object", "-w", "target-name"]);
            std::fs::remove_file(p).unwrap();
            h
        };
        g(
            &f.tree,
            &[
                "update-index",
                "--add",
                "--cacheinfo",
                &format!("120000,{blob},link"),
            ],
        );
        g(&f.tree, &["commit", "-q", "-m", "link"]);
        let sha = out(&f.tree, &["rev-parse", "HEAD"]);
        assert_eq!(
            f.plan(&sha, 2),
            Err(Refused::SettingsTouched(vec!["link".to_owned()])),
            "reason: link_sent"
        );
    }

    #[test]
    fn gitattributes_goes_back_and_is_listed() {
        let f = fix();
        let sha = f.commit(".gitattributes", "* text=auto\n");
        let p = f.plan(&sha, 1).unwrap();
        assert_eq!(
            p.listed,
            vec![".gitattributes".to_owned()],
            "reason: not_listed"
        );
    }

    /// 5d33a M1: a program named in the copy's own config never runs, and objects the copy
    /// only borrows from another repository (alternates) do not come along.
    #[test]
    fn the_copys_config_and_alternates_are_not_read() {
        let f = fix();
        let sha = f.commit("a.txt", "a");
        let mark = f.data.path().join("ran");
        let prog = format!("touch {}", mark.display());
        g(&f.tree, &["config", "core.alternateRefsCommand", &prog]);
        g(&f.tree, &["config", "core.fsmonitor", &prog]);
        g(&f.tree, &["config", "uploadpack.packObjectsHook", &prog]);
        f.plan(&sha, 1).unwrap();
        assert!(!mark.exists(), "reason: copy_program_ran");
        for key in [
            "core.alternateRefsCommand",
            "core.fsmonitor",
            "uploadpack.packObjectsHook",
        ] {
            g(&f.tree, &["config", "--unset", key]);
        }

        // a second repository the seat cannot read through aibd, borrowed by alternates
        let other = tempfile::tempdir().unwrap();
        g(other.path(), &["init", "-q"]);
        std::fs::write(other.path().join("secret.txt"), "s3cret").unwrap();
        g(other.path(), &["add", "-A"]);
        g(other.path(), &["commit", "-q", "-m", "secret"]);
        let secret_blob = out(other.path(), &["rev-parse", "HEAD:secret.txt"]);
        let alt = f
            .tree
            .join(".git")
            .join("objects")
            .join("info")
            .join("alternates");
        std::fs::create_dir_all(alt.parent().unwrap()).unwrap();
        std::fs::write(
            &alt,
            format!("{}\n", other.path().join(".git").join("objects").display()),
        )
        .unwrap();
        // a commit in the copy whose tree names the borrowed blob
        g(
            &f.tree,
            &[
                "update-index",
                "--add",
                "--cacheinfo",
                &format!("100644,{secret_blob},borrowed.txt"),
            ],
        );
        g(&f.tree, &["commit", "-q", "-m", "borrowed"]);
        let borrowed = out(&f.tree, &["rev-parse", "HEAD"]);
        let got = f.plan(&borrowed, 2);
        assert!(
            matches!(got, Err(Refused::ObjectsMissing(_))),
            "reason: borrowed_object_sent {got:?}"
        );
        let has = Command::new("git")
            .arg("-C")
            .arg(&f.repo)
            .args(["cat-file", "-e", &secret_blob])
            .status()
            .unwrap()
            .success();
        assert!(!has, "reason: other_repos_blob_in_registered_repo");
    }

    #[cfg(unix)]
    #[test]
    fn an_object_file_that_is_a_link_is_not_copied() {
        let f = fix();
        let sha = f.commit("a.txt", "a");
        // the head commit's loose object swapped for a link to the same bytes elsewhere
        let obj = f
            .tree
            .join(".git")
            .join("objects")
            .join(&sha[..2])
            .join(&sha[2..]);
        let aside = f.data.path().join("aside");
        std::fs::rename(&obj, &aside).unwrap();
        std::os::unix::fs::symlink(&aside, &obj).unwrap();
        let got = f.plan(&sha, 1);
        assert!(
            matches!(got, Err(Refused::ObjectsMissing(_))),
            "reason: link_followed {got:?}"
        );
    }

    #[test]
    fn a_repo_whose_own_hook_would_run_is_not_run() {
        let f = fix();
        let sha = f.commit("a.txt", "a");
        let mark = f.data.path().join("hook-ran");
        let hook = f
            .repo
            .join(".git")
            .join("hooks")
            .join("reference-transaction");
        std::fs::create_dir_all(hook.parent().unwrap()).unwrap();
        std::fs::write(&hook, format!("#!/bin/sh\ntouch {}\n", mark.display())).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&hook, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        f.plan(&sha, 1).unwrap();
        write_branch(&f.repo, &f.hooks, "web/a9136-1120", &sha).unwrap();
        assert!(!mark.exists(), "reason: repo_hook_ran");
    }

    /// git's own object check on the way into the repo: a tree with an entry named `.git`
    /// (a checkout would write into the person's repository folder) is not taken.
    #[test]
    fn a_tree_naming_dot_git_is_refused_by_the_object_check() {
        use std::io::Write;
        let f = fix();
        let base = f.commit("a.txt", "a");
        let blob = out(&f.tree, &["rev-parse", "HEAD:a.txt"]);
        let mut child = Command::new("git")
            .arg("-C")
            .arg(&f.tree)
            .arg("mktree")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let line = format!("100644 blob {blob}\t.git\n");
        child
            .stdin
            .take()
            .unwrap()
            .write_all(line.as_bytes())
            .unwrap();
        let o = child.wait_with_output().unwrap();
        let tree_sha = String::from_utf8_lossy(&o.stdout).trim().to_owned();
        assert_eq!(tree_sha.len(), 40, "reason: mktree {o:?}");
        let o = Command::new("git")
            .arg("-C")
            .arg(&f.tree)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args(["commit-tree", &tree_sha, "-p", &base, "-m", "dotgit"])
            .output()
            .unwrap();
        let sha = String::from_utf8_lossy(&o.stdout).trim().to_owned();
        assert!(sha_ok(&sha), "reason: commit_tree {o:?}");
        let got = f.plan(&sha, 1);
        assert!(
            matches!(got, Err(Refused::ObjectsMissing(_))),
            "reason: dot_git_tree_taken {got:?}"
        );
    }

    #[test]
    fn the_copy_budget_refuses_more_files_or_bytes() {
        let f = fix();
        f.commit("a.txt", "a");
        let src = f.tree.join(".git").join("objects");
        let d1 = f.data.path().join("o1");
        std::fs::create_dir_all(&d1).unwrap();
        assert_eq!(
            copy_objects(&src, &d1, 1, MAX_BYTES),
            Err(Refused::TooBig),
            "reason: files"
        );
        let d2 = f.data.path().join("o2");
        std::fs::create_dir_all(&d2).unwrap();
        assert_eq!(
            copy_objects(&src, &d2, MAX_FILES, 8),
            Err(Refused::TooBig),
            "reason: bytes"
        );
        let d3 = f.data.path().join("o3");
        std::fs::create_dir_all(&d3).unwrap();
        assert_eq!(
            copy_objects(&src, &d3, MAX_FILES, MAX_BYTES),
            Ok(()),
            "reason: fits"
        );
        assert!(!d3.join("info").exists(), "reason: info_copied");
    }

    #[test]
    fn the_seat_list_names_the_repo_of_a_copy_and_whether_it_can_be_sent() {
        let f = fix();
        let cwd = f.tree.to_string_lossy().into_owned();
        let head = out(&f.tree, &["rev-parse", "HEAD"]);
        assert_eq!(
            repo_copy_of(&cwd, false),
            json!({"repo": "repo", "sendable": true, "head": head})
        );
        assert_eq!(
            repo_copy_of(&cwd, true),
            json!({"repo": "repo", "sendable": false, "head": head})
        );
        // a copy off another seat's folder is 合回's, not this
        let base = f
            .tree
            .parent()
            .unwrap()
            .join(crate::seat_worktree::BASE_FILE);
        std::fs::write(&base, r#"{"base": "/x", "author": "seat_aaaa1111"}"#).unwrap();
        assert_eq!(
            repo_copy_of(&cwd, false),
            Value::Null,
            "reason: seat_copy_listed"
        );
        assert_eq!(repo_copy_of("/nowhere/at/all", false), Value::Null);
    }

    #[test]
    fn names_aibd_builds_and_names_it_copies() {
        assert_eq!(
            branch_stem("a9136", "1120").as_deref(),
            Some("web/a9136-1120")
        );
        for (code, date) in [
            ("", "1120"),
            ("a9136", "11-20"),
            ("../x", "1120"),
            ("A9136", "1120"),
            ("a9136", "112"),
        ] {
            assert_eq!(branch_stem(code, date), None, "reason: stem {code} {date}");
        }
        assert!(sha_ok(&"a".repeat(40)) && !sha_ok(&"A".repeat(40)) && !sha_ok("abc"));
        let h = "0".repeat(40);
        assert!(pack_name(&format!("pack-{h}.pack")) && pack_name(&format!("pack-{h}.idx")));
        assert!(
            !pack_name(&format!("pack-{h}.keep"))
                && !pack_name("alternates")
                && !pack_name("pack-x.pack")
        );
        let raw = ":000000 100644 0 1 A\0a.txt\0\n:100644 120000 1 2 M\0b\0";
        assert_eq!(
            raw_entries(raw),
            vec![
                ("100644".to_owned(), "a.txt".to_owned()),
                ("120000".to_owned(), "b".to_owned())
            ]
        );
    }
}
