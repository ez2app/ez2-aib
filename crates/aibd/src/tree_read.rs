//! N-web-worktree r3 (DESIGN-web-worktree-b4297-1119 §r3 補): the reviewer reads the author
//! seat's tree, and only reads it.
//!
//! - `GET /api/v1/seats/{id}/tree?path=`: a folder's entries or a file's text, inside that
//!   seat's tree only (both separators; `..`, a drive, `.git` and a link leading out refused).
//! - `GET /api/v1/seats/{id}/tree/log`: the commits since the head the tree was opened on.
//! - `GET /api/v1/seats/{id}/tree/diff`: what those commits changed (committed only, so no
//!   filter of the repo's runs), with how many files are still uncommitted.
//!
//! Who: the start-up token, the built-in page (a user page gets 403), or a seat token whose seat
//! is the `reviewer` on a node the author seat is assigned on. Nothing here writes to the tree
//! or runs anything the repo names: git runs with aibd's empty hooks folder, no fsmonitor, no
//! external diff, no textconv, no signature check. Each read leaves `worktree.read {by, seat,
//! path}`.

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// The largest file read whole, in bytes.
pub const READ_MAX: u64 = 1 << 20;
/// The most entries listed for one folder.
pub const LIST_MAX: usize = 2000;
/// The most commits the log lists.
pub const LOG_MAX: usize = 200;
/// The longest diff given, in bytes (longer: cut, `truncated`).
pub const DIFF_MAX: usize = 2 << 20;

fn answer(status: StatusCode, reason: &str, message: &str) -> Box<Response> {
    Box::new(
        (
            status,
            Json(
                json!({"code": "tree_read", "reason": reason, "message": message,
                        "request_id": request_id()}),
            ),
        )
            .into_response(),
    )
}

/// `rel` (`/` or `\` between names) inside `root`, both resolved: `None` for `..`, a drive or
/// stream name (`:`), anything under `.git`, a path that is not there, or one whose real place
/// (after links) is outside `root`.
pub fn inside(root: &Path, rel: &str) -> Option<PathBuf> {
    let root = std::fs::canonicalize(root).ok()?;
    let mut p = root.clone();
    for part in rel.split(['/', '\\']) {
        match part {
            "" | "." => {}
            ".." => return None,
            _ if part.contains(':') || part.eq_ignore_ascii_case(".git") => return None,
            _ => p.push(part),
        }
    }
    let real = std::fs::canonicalize(&p).ok()?;
    let under = real.strip_prefix(&root).ok()?;
    let git = under
        .components()
        .any(|c| c.as_os_str().to_string_lossy().eq_ignore_ascii_case(".git"));
    (!git).then_some(real)
}

/// A folder's entries (`.git` left out), by name; or a file: its text, `binary`, or `too_big`.
pub fn read_at(real: &Path) -> std::io::Result<Value> {
    let md = std::fs::metadata(real)?;
    if md.is_dir() {
        let mut entries: Vec<Value> = Vec::new();
        let mut names: Vec<(String, std::fs::Metadata)> = std::fs::read_dir(real)?
            .flatten()
            .filter_map(|e| {
                let name = e.file_name().to_string_lossy().into_owned();
                let md = std::fs::symlink_metadata(e.path()).ok()?;
                (!name.eq_ignore_ascii_case(".git")).then_some((name, md))
            })
            .collect();
        names.sort_by(|a, b| a.0.cmp(&b.0));
        let truncated = names.len() > LIST_MAX;
        for (name, md) in names.into_iter().take(LIST_MAX) {
            let kind = if md.file_type().is_symlink() {
                "link"
            } else if md.is_dir() {
                "dir"
            } else {
                "file"
            };
            entries.push(json!({"name": name, "kind": kind, "size": md.len()}));
        }
        return Ok(json!({"kind": "dir", "entries": entries, "truncated": truncated}));
    }
    if md.len() > READ_MAX {
        return Ok(json!({"kind": "file", "size": md.len(), "too_big": true}));
    }
    let bytes = std::fs::read(real)?;
    Ok(match String::from_utf8(bytes) {
        Ok(text) => json!({"kind": "file", "size": md.len(), "text": text}),
        Err(_) => json!({"kind": "file", "size": md.len(), "binary": true}),
    })
}

fn hex40(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Read-only git in the tree: no signature program (the diffs add `--no-ext-diff
/// --no-textconv`; the shared runner already sets the empty hooks folder and turns fsmonitor
/// off).
fn git_read(tree: &Path, hooks: &Path, args: &[&str]) -> Result<String, String> {
    let mut all = vec!["-c", "log.showSignature=false"];
    all.extend_from_slice(args);
    crate::seat_worktree::git(tree, hooks, &all)
}

/// The commits since `base`, newest first: `{sha, author, at, subject}`.
pub fn log_of(tree: &Path, base: &str, hooks: &Path) -> Result<Vec<Value>, String> {
    if !hex40(base) {
        return Err("base".into());
    }
    let range = format!("{base}..HEAD");
    let n = LOG_MAX.to_string();
    let out = git_read(
        tree,
        hooks,
        &[
            "log",
            "--no-color",
            "--format=%H%x09%an%x09%aI%x09%s",
            "-n",
            &n,
            &range,
        ],
    )?;
    Ok(out
        .lines()
        .filter_map(|l| {
            let mut f = l.splitn(4, '\t');
            let (sha, author, at, subject) = (f.next()?, f.next()?, f.next()?, f.next()?);
            Some(json!({"sha": sha, "author": author, "at": at, "subject": subject}))
        })
        .collect())
}

/// `git diff` that runs no program the repo names (external diff, textconv).
const DIFF_SAFE: &[&str] = &["diff", "--no-ext-diff", "--no-textconv", "--no-color"];

/// What the commits since `base` changed (`git diff base HEAD`, committed only), cut at
/// [`DIFF_MAX`].
pub fn diff_of(tree: &Path, base: &str, hooks: &Path) -> Result<Value, String> {
    if !hex40(base) {
        return Err("base".into());
    }
    let stat = git_read(
        tree,
        hooks,
        &[DIFF_SAFE, &["--stat", base, "HEAD"]].concat(),
    )?;
    let mut patch = git_read(tree, hooks, &[DIFF_SAFE, &[base, "HEAD"]].concat())?;
    let truncated = patch.len() > DIFF_MAX;
    if truncated {
        let mut cut = DIFF_MAX;
        while !patch.is_char_boundary(cut) {
            cut -= 1;
        }
        patch.truncate(cut);
    }
    Ok(json!({"base": base, "stat": stat, "patch": patch, "truncated": truncated}))
}

/// The author seat's tree: its folder (under `<data>/seat-work`, a real folder) and the head
/// it was opened on (the seat's `seat.opened` event, written by aibd when it opened the tree).
struct Tree {
    dir: PathBuf,
    base: String,
}

fn seat_id_ok(seat: &str) -> bool {
    seat.strip_prefix("seat_")
        .is_some_and(|h| !h.is_empty() && h.len() <= 32 && h.bytes().all(|b| b.is_ascii_hexdigit()))
}

async fn tree_of(st: &AppState, seat: &str) -> Result<Tree, Box<Response>> {
    let none = || {
        answer(
            StatusCode::NOT_FOUND,
            "no_tree",
            "this seat has no tree of a repo",
        )
    };
    if !seat_id_ok(seat) {
        return Err(none());
    }
    let store = st.store.clone();
    let want = seat.to_owned();
    let found = tokio::task::spawn_blocking(move || {
        let meta = store
            .seat_metas()
            .ok()?
            .into_iter()
            .find(|m| m.seat_id == want)?;
        let opened = store
            .events_of_types(&["seat.opened"], 100_000)
            .ok()?
            .into_iter()
            .rev()
            .find(|e| e.aggregate_id == want)?;
        let base = opened.payload["opened_by"]["base_head"]
            .as_str()?
            .to_owned();
        Some((meta.cwd, base))
    })
    .await
    .ok()
    .flatten();
    let Some((cwd, base)) = found else {
        return Err(none());
    };
    let work = crate::seats::seat_work_root(st.sessions.private_dir());
    let dir = PathBuf::from(&cwd);
    let real_dir = std::fs::symlink_metadata(&dir).is_ok_and(|m| m.file_type().is_dir());
    let (Ok(w), Ok(d)) = (std::fs::canonicalize(&work), std::fs::canonicalize(&dir)) else {
        return Err(none());
    };
    if !real_dir || !d.starts_with(&w) || d == w || !hex40(&base) {
        return Err(none());
    }
    Ok(Tree { dir: d, base })
}

/// Who may read `author`'s tree; `Ok` is who, as the event says it.
async fn reader(c: &Caller, author: &str) -> Result<String, Box<Response>> {
    match c {
        Caller::StartupToken => Ok("token".into()),
        Caller::Page(p) => {
            if crate::seat_input::held_as_user_page(p).await {
                return Err(answer(
                    StatusCode::FORBIDDEN,
                    "tree_built_in_page_only",
                    "a seat's tree is read from the built-in page only",
                ));
            }
            Ok("page".into())
        }
        Caller::Seat(key) => {
            let refused = || {
                answer(
                    StatusCode::FORBIDDEN,
                    "not_reviewer",
                    "a seat reads another seat's tree only as the reviewer on a node both are on",
                )
            };
            let Some(me) = crate::seat_token::tokens().seat(key) else {
                return Err(refused());
            };
            let (who, author) = (me.clone(), author.to_owned());
            let ok = tokio::task::spawn_blocking(move || reviews(&who, &author))
                .await
                .ok()
                .flatten();
            match ok {
                Some(true) => Ok(me),
                Some(false) => Err(refused()),
                None => Err(answer(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "assignments_unavailable",
                    "the workspace file could not be opened; see the log",
                )),
            }
        }
        Caller::Anonymous => Err(answer(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "missing bearer token",
        )),
    }
}

/// `reviewer` is the `reviewer` on a node `author` is assigned on (any role). `None`: the
/// workspace file is not open. Blocking (SQLite).
pub fn reviews(reviewer: &str, author: &str) -> Option<bool> {
    if reviewer == author {
        return Some(false);
    }
    for node in crate::assign::nodes_of(reviewer)? {
        let members = crate::assign::members_of(&node)?;
        let is_reviewer = members
            .iter()
            .any(|(s, r)| s == reviewer && r == "reviewer");
        if is_reviewer && members.iter().any(|(s, _)| s == author) {
            return Some(true);
        }
    }
    Some(false)
}

async fn journal(st: &AppState, by: &str, seat: &str, path: &str) {
    let path: String = path.chars().take(256).collect();
    crate::seats::side_event(
        st,
        "worktree",
        "worktree.read",
        json!({"by": by, "seat": seat, "path": path}),
    )
    .await;
}

#[derive(Deserialize)]
pub struct TreeQuery {
    #[serde(default)]
    path: String,
}

async fn start(
    st: &AppState,
    c: &Caller,
    seat: &str,
) -> Result<(String, Tree, PathBuf), Box<Response>> {
    let by = reader(c, seat).await?;
    let tree = tree_of(st, seat).await?;
    let hooks = crate::seats::seat_git_hooks(st.sessions.private_dir());
    Ok((by, tree, hooks))
}

/// `GET /api/v1/seats/{id}/tree?path=`.
pub async fn tree(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    UrlPath(seat): UrlPath<String>,
    Query(q): Query<TreeQuery>,
) -> Response {
    let (by, tree, _) = match start(&st, &c, &seat).await {
        Ok(t) => t,
        Err(r) => return *r,
    };
    let rel = q.path.clone();
    let root = tree.dir.clone();
    let read = tokio::task::spawn_blocking(move || {
        let real = inside(&root, &rel).ok_or(())?;
        read_at(&real).map_err(|_| ())
    })
    .await;
    match read {
        Ok(Ok(mut v)) => {
            journal(&st, &by, &seat, &q.path).await;
            v["path"] = json!(q.path);
            Json(v).into_response()
        }
        _ => *answer(
            StatusCode::BAD_REQUEST,
            "path",
            "path must name a file or folder inside the seat's tree (not .git, not outside)",
        ),
    }
}

/// `GET /api/v1/seats/{id}/tree/log`.
pub async fn log(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    UrlPath(seat): UrlPath<String>,
) -> Response {
    let (by, tree, hooks) = match start(&st, &c, &seat).await {
        Ok(t) => t,
        Err(r) => return *r,
    };
    let base = tree.base.clone();
    let out = tokio::task::spawn_blocking(move || log_of(&tree.dir, &tree.base, &hooks)).await;
    match out {
        Ok(Ok(commits)) => {
            journal(&st, &by, &seat, ":log").await;
            Json(json!({"base": base, "commits": commits})).into_response()
        }
        _ => *answer(
            StatusCode::INTERNAL_SERVER_ERROR,
            "git",
            "git log failed in the tree",
        ),
    }
}

/// `GET /api/v1/seats/{id}/tree/diff`.
pub async fn diff(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    UrlPath(seat): UrlPath<String>,
) -> Response {
    let (by, tree, hooks) = match start(&st, &c, &seat).await {
        Ok(t) => t,
        Err(r) => return *r,
    };
    let out = tokio::task::spawn_blocking(move || {
        let d = diff_of(&tree.dir, &tree.base, &hooks)?;
        let left = crate::seat_worktree::left_in(&tree.dir, &tree.base, &hooks)?;
        Ok::<_, String>((d, left.uncommitted))
    })
    .await;
    match out {
        Ok(Ok((mut d, uncommitted))) => {
            journal(&st, &by, &seat, ":diff").await;
            d["uncommitted"] = json!(uncommitted);
            Json(d).into_response()
        }
        _ => *answer(
            StatusCode::INTERNAL_SERVER_ERROR,
            "git",
            "git diff failed in the tree",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn git(dir: &Path, args: &[&str]) {
        let ok = std::process::Command::new("git")
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args(args)
            .current_dir(dir)
            .status()
            .expect("git")
            .success();
        assert!(ok, "git {args:?}");
    }

    #[test]
    fn a_seat_token_reaches_the_three_tree_reads_and_nothing_next_to_them() {
        for ok in [
            "/api/v1/seats/seat_ab12/tree",
            "/api/v1/seats/seat_ab12/tree/log",
            "/api/v1/seats/seat_ab12/tree/diff",
        ] {
            assert!(
                crate::guard::is_seat_tree(ok),
                "reason: tree_route_refused {ok}"
            );
        }
        for bad in [
            "/api/v1/seats/seat_ab12",
            "/api/v1/seats/seat_ab12/sessions",
            "/api/v1/seats//tree",
            "/api/v1/seats/seat_ab12/tree/x",
            "/api/v1/seats/seat_ab12/trees",
            "/api/v1/seats/a/b/tree",
            "/api/v1/sessions/seat_ab12/tree",
        ] {
            assert!(
                !crate::guard::is_seat_tree(bad),
                "reason: seat_token_reaches {bad}"
            );
        }
    }

    #[test]
    fn a_path_is_read_only_inside_the_tree_with_either_separator() {
        let d = tempfile::tempdir().expect("tmp");
        let root = d.path().join("tree");
        std::fs::create_dir_all(root.join("src").join("deep")).expect("mk");
        std::fs::write(root.join("src").join("deep").join("a.rs"), "fn a() {}").expect("w");
        std::fs::create_dir_all(root.join(".git")).expect("mk");
        std::fs::write(root.join(".git").join("config"), "x").expect("w");
        std::fs::write(d.path().join("outside.txt"), "secret").expect("w");
        assert!(
            inside(&root, "src/deep/a.rs").is_some(),
            "reason: slash_refused"
        );
        assert!(
            inside(&root, "src\\deep\\a.rs").is_some(),
            "reason: backslash_refused"
        );
        assert!(inside(&root, "").is_some(), "reason: top_refused");
        for bad in [
            "../outside.txt",
            "src/../../outside.txt",
            "src\\..\\..\\outside.txt",
            ".git/config",
            ".GIT\\config",
            "C:/outside.txt",
            "no-such-file",
        ] {
            assert!(inside(&root, bad).is_none(), "reason: read_outside {bad}");
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(d.path().join("outside.txt"), root.join("out")).expect("ln");
            std::os::unix::fs::symlink(root.join(".git"), root.join("g")).expect("ln");
            assert!(inside(&root, "out").is_none(), "reason: link_led_out");
            assert!(
                inside(&root, "g/config").is_none(),
                "reason: link_led_into_git"
            );
        }
        let top = read_at(&inside(&root, "").expect("top")).expect("read");
        let names: Vec<&str> = top["entries"]
            .as_array()
            .expect("entries")
            .iter()
            .filter_map(|e| e["name"].as_str())
            .collect();
        assert!(!names.contains(&".git"), "reason: git_listed {names:?}");
        let f = read_at(&inside(&root, "src/deep/a.rs").expect("f")).expect("read");
        assert_eq!(f["text"], "fn a() {}");
    }

    #[test]
    fn the_log_and_diff_are_the_commits_since_the_tree_was_opened() {
        let d = tempfile::tempdir().expect("tmp");
        let repo = d.path().join("r");
        std::fs::create_dir_all(&repo).expect("mk");
        git(&repo, &["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "one\n").expect("w");
        git(&repo, &["add", "."]);
        git(&repo, &["commit", "-qm", "base"]);
        let hooks = d.path().join("home").join("hooks");
        std::fs::create_dir_all(&hooks).expect("mk");
        let base = crate::seat_worktree::git(&repo, &hooks, &["rev-parse", "HEAD"]).expect("head");
        std::fs::write(repo.join("a.txt"), "two\n").expect("w");
        git(&repo, &["commit", "-qam", "change a"]);
        // a repo that names an external diff program: never run
        let marker = d.path().join("ran");
        git(
            &repo,
            &[
                "config",
                "diff.external",
                &format!("sh -c 'touch {}'", marker.display()),
            ],
        );
        let log = log_of(&repo, &base, &hooks).expect("log");
        assert_eq!(log.len(), 1, "reason: log_not_since_base {log:?}");
        assert_eq!(log[0]["subject"], "change a");
        let diff = diff_of(&repo, &base, &hooks).expect("diff");
        assert!(
            diff["patch"].as_str().is_some_and(|p| p.contains("+two")),
            "reason: diff_not_against_base {diff}"
        );
        assert!(!marker.exists(), "reason: external_diff_ran");
        assert!(
            log_of(&repo, "HEAD~1", &hooks).is_err(),
            "reason: base_not_a_sha"
        );
    }
}
