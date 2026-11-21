//! G8 (DECISION-council-cutover-519d0-1121 §2; DESIGN-g4-7b81e-1121; 5d33a PREREAD-g4 3, L1,
//! L2): aibd brings one commit of a registered repo into a seat's copy of it, as FETCH_HEAD
//! only (no branch of the copy moves; the seat cherry-picks or switches itself). The seat's own
//! git cannot: it has no transport and the repo is outside its folder.
//!
//! `{type: "seat_fetch", seat_id?, sha}` from the built-in page (the body names the seat) or
//! from a seat's token (the seat is the token's; a body naming another seat is refused). No
//! fingerprint: it reads the registered repo and writes only the seat's own copy. At most
//! [`PER_MINUTE`] a seat.
//!
//! Both repositories are read as data first ([`repo_safe`]: plain `.git` folder, listed config
//! keys only, no alternates or attributes), and git runs with aibd's fixed environment (no
//! system or user config, hooks at aibd's empty folder, only the file transport). The commit
//! must already be in the registered repo (a seat's work gets there by 送回倉).
use crate::api::AppState;
use crate::seat_worktree::{git, repo_safe};
use axum::{http::StatusCode, response::IntoResponse, response::Response, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const FETCH: &str = "seat_fetch";
/// Fetches a seat may ask in a minute (5d33a PREREAD-g4 3); the next is 429.
pub const PER_MINUTE: usize = 30;
/// What git runs on the registered repo's side: fixed here, so a commit that no branch
/// names can be asked for without changing that repo's own config (5d33a L1).
pub const UPLOAD_PACK: &str = "git -c uploadpack.allowAnySHA1InWant=true upload-pack";

/// A seat token's act body is this one.
pub fn is_seat_act(body: &Value) -> bool {
    body.get("type").and_then(Value::as_str) == Some(FETCH)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    seat_id: Option<String>,
    sha: String,
}

/// Why a fetch was refused (the page's word) and its status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum No {
    Shape,
    RepoUnsafe,
    CopyUnsafe,
    NotInRepo,
    Failed,
}

impl No {
    pub fn word(self) -> &'static str {
        match self {
            No::Shape => "fetch_sha_shape",
            No::RepoUnsafe => "repo_unsafe",
            No::CopyUnsafe => "copy_unsafe",
            No::NotInRepo => "fetch_not_in_repo",
            No::Failed => "fetch_failed",
        }
    }
    fn status(self) -> StatusCode {
        match self {
            No::Shape => StatusCode::BAD_REQUEST,
            No::NotInRepo => StatusCode::NOT_FOUND,
            _ => StatusCode::CONFLICT,
        }
    }
}

/// Brings `sha` of the registered repo `repo` into the copy `copy` (FETCH_HEAD only).
pub fn fetch_into(copy: &Path, repo: &Path, sha: &str, hooks: &Path) -> Result<(), No> {
    if !crate::send_back::sha_ok(sha) {
        return Err(No::Shape);
    }
    repo_safe(repo).map_err(|_| No::RepoUnsafe)?;
    repo_safe(copy).map_err(|_| No::CopyUnsafe)?;
    let kind = git(repo, hooks, &["cat-file", "-t", sha]).map_err(|_| No::NotInRepo)?;
    if kind != "commit" {
        return Err(No::NotInRepo);
    }
    let from = repo.to_string_lossy().into_owned();
    git(
        copy,
        hooks,
        &[
            "-c",
            "protocol.file.allow=always",
            "fetch",
            "-q",
            "--no-tags",
            "--no-recurse-submodules",
            "--upload-pack",
            UPLOAD_PACK,
            &from,
            sha,
        ],
    )
    .map_err(|_| No::Failed)?;
    Ok(())
}

/// The seat a fetch is for (5d33a L2): a seat's token fetches for that seat only, and a body
/// naming another seat is 403; the page names the seat in the body.
fn which_seat(
    token_seat: Option<String>,
    body_seat: Option<String>,
) -> Result<String, (StatusCode, &'static str)> {
    match (token_seat, body_seat) {
        (Some(t), Some(b)) if b != t => Err((StatusCode::FORBIDDEN, "fetch_other_seat")),
        (Some(t), _) => Ok(t),
        (None, Some(b)) => Ok(b),
        (None, None) => Err((StatusCode::BAD_REQUEST, "fetch_fields")),
    }
}

static ASKED: Mutex<Option<HashMap<String, VecDeque<Instant>>>> = Mutex::new(None);

/// Whether `seat` may fetch now (and counts this one).
fn within_rate(seat: &str, now: Instant) -> bool {
    let mut g = ASKED.lock().unwrap_or_else(|e| e.into_inner());
    let q = g
        .get_or_insert_with(HashMap::new)
        .entry(seat.to_owned())
        .or_default();
    while q
        .front()
        .is_some_and(|t| now.duration_since(*t) >= Duration::from_secs(60))
    {
        q.pop_front();
    }
    if q.len() >= PER_MINUTE {
        return false;
    }
    q.push_back(now);
    true
}

fn refuse(status: StatusCode, reason: &str, message: &str) -> Response {
    (
        status,
        Json(
            json!({"code": "refused", "reason": reason, "message": message,
                    "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

/// The registered repo a seat's copy (`cwd`) was opened from, under aibd's data folder.
fn registered(data: &Path, cwd: &Path) -> Option<(String, PathBuf)> {
    let (_, author) = crate::seat_worktree::base_of(cwd)?;
    let name = author.strip_prefix(crate::repos::REPO_AUTHOR_PREFIX)?;
    let repo = crate::repos::find(data, name)?;
    Some((repo.name, PathBuf::from(repo.path)))
}

/// `seat_fetch`. `token_seat`: the seat whose token called (5d33a L2: then the body may only
/// name that seat); `None`: the built-in page, which names the seat in the body.
pub async fn act(st: AppState, body: Value, token_seat: Option<String>) -> Response {
    let Ok(req) = serde_json::from_value::<Raw>(body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "fetch_fields",
            "body must be {type, seat_id?, sha}",
        );
    };
    let seat = match which_seat(token_seat, req.seat_id) {
        Ok(s) => s,
        Err((status, reason)) => {
            return refuse(status, reason, "a seat fetches into its own copy only")
        }
    };
    if !within_rate(&seat, Instant::now()) {
        return refuse(
            StatusCode::TOO_MANY_REQUESTS,
            "fetch_too_often",
            "at most 30 fetches a minute for a seat",
        );
    }
    let (store, sid) = (st.store.clone(), seat.clone());
    let meta = match tokio::task::spawn_blocking(move || store.seat_meta(&sid)).await {
        Ok(Ok(Some(m))) => m,
        Ok(Ok(None)) => {
            return crate::sessions::ApiError::new(
                aib_core::ErrorCode::SeatNotFound,
                "no such seat",
            )
            .into_response()
        }
        _ => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "the seat could not be read",
            )
        }
    };
    let private = st.sessions.private_dir().to_path_buf();
    let data = private.parent().unwrap_or(&private).to_path_buf();
    let copy = PathBuf::from(&meta.cwd);
    let Some((name, repo)) = registered(&data, &copy) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "fetch_not_repo_copy",
            "the seat does not work in a copy of a registered repo",
        );
    };
    let hooks = crate::seats::seat_git_hooks(&private);
    let sha = req.sha.clone();
    let done = tokio::task::spawn_blocking(move || {
        std::fs::create_dir_all(&hooks).map_err(|_| No::Failed)?;
        fetch_into(&copy, &repo, &sha, &hooks)
    })
    .await
    .unwrap_or(Err(No::Failed));
    match done {
        Ok(()) => {
            let short = req.sha.get(..12).unwrap_or_default();
            crate::seats::side_event(
                &st,
                "repo",
                "seat.fetched",
                json!({"seat_id": seat, "repo": name, "sha12": short}),
            )
            .await;
            Json(json!({"fetched": req.sha, "repo": name})).into_response()
        }
        Err(n) => refuse(n.status(), n.word(), "nothing was fetched"),
    }
}

#[cfg(all(test, unix))]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn g(dir: &Path, args: &[&str]) -> String {
        let o = std::process::Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
            .args([
                "-c",
                "init.defaultBranch=main",
                "-c",
                "commit.gpgsign=false",
            ])
            .args(args)
            .output()
            .unwrap();
        assert!(
            o.status.success(),
            "{args:?} {}",
            String::from_utf8_lossy(&o.stderr)
        );
        String::from_utf8_lossy(&o.stdout).trim().to_owned()
    }

    /// A registered repo with a commit no branch names, and a seat's copy cloned before it.
    fn world() -> (tempfile::TempDir, PathBuf, PathBuf, PathBuf, String) {
        let d = tempfile::tempdir().unwrap();
        let repo = d.path().join("repo");
        std::fs::create_dir_all(&repo).unwrap();
        g(&repo, &["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "a").unwrap();
        g(&repo, &["add", "-A"]);
        g(&repo, &["commit", "-qm", "a"]);
        let copy = d.path().join("copy");
        g(
            d.path(),
            &[
                "clone",
                "-q",
                repo.to_str().unwrap(),
                copy.to_str().unwrap(),
            ],
        );
        // a commit only a hidden ref names (as 送回倉 leaves objects in the repo)
        std::fs::write(repo.join("b.txt"), "b").unwrap();
        g(&repo, &["add", "-A"]);
        g(&repo, &["commit", "-qm", "b"]);
        let sha = g(&repo, &["rev-parse", "HEAD"]);
        g(&repo, &["update-ref", "refs/aibd/seat/x", &sha]);
        g(&repo, &["reset", "-q", "--hard", "HEAD~1"]);
        let hooks = d.path().join("hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        (d, repo, copy, hooks, sha)
    }

    #[test]
    fn a_commit_of_the_registered_repo_comes_in_as_fetch_head_and_no_branch_moves() {
        let (_d, repo, copy, hooks, sha) = world();
        let before = g(&copy, &["rev-parse", "HEAD"]);
        fetch_into(&copy, &repo, &sha, &hooks).unwrap();
        assert_eq!(
            g(&copy, &["rev-parse", "FETCH_HEAD"]),
            sha,
            "reason: not_fetched"
        );
        assert_eq!(
            g(&copy, &["rev-parse", "HEAD"]),
            before,
            "reason: branch_moved"
        );
        // a short name, a branch name, a commit the repo does not have
        assert_eq!(fetch_into(&copy, &repo, &sha[..12], &hooks), Err(No::Shape));
        assert_eq!(fetch_into(&copy, &repo, "main", &hooks), Err(No::Shape));
        let other = "0123456789abcdef0123456789abcdef01234567";
        assert_eq!(fetch_into(&copy, &repo, other, &hooks), Err(No::NotInRepo));
    }

    /// 5d33a L1: a registered repo (or the copy) whose settings say more than the listed keys,
    /// or that borrows objects from elsewhere, is not read by git.
    #[test]
    fn a_repo_or_copy_with_settings_beyond_the_list_is_not_fetched_from() {
        let (_d, repo, copy, hooks, sha) = world();
        let cfg = repo.join(".git").join("config");
        let plain = std::fs::read_to_string(&cfg).unwrap();
        std::fs::write(
            &cfg,
            format!("{plain}[uploadpack]\n\tpackObjectsHook = touch /tmp/x\n"),
        )
        .unwrap();
        assert_eq!(fetch_into(&copy, &repo, &sha, &hooks), Err(No::RepoUnsafe));
        std::fs::write(&cfg, plain).unwrap();
        let alt = repo.join(".git").join("objects").join("info");
        std::fs::create_dir_all(&alt).unwrap();
        std::fs::write(alt.join("alternates"), "/elsewhere/objects\n").unwrap();
        assert_eq!(fetch_into(&copy, &repo, &sha, &hooks), Err(No::RepoUnsafe));
        std::fs::remove_file(alt.join("alternates")).unwrap();
        let ccfg = copy.join(".git").join("config");
        let cplain = std::fs::read_to_string(&ccfg).unwrap();
        std::fs::write(
            &ccfg,
            format!("{cplain}[core]\n\tsshCommand = touch /tmp/y\n"),
        )
        .unwrap();
        assert_eq!(fetch_into(&copy, &repo, &sha, &hooks), Err(No::CopyUnsafe));
        std::fs::write(&ccfg, cplain).unwrap();
        fetch_into(&copy, &repo, &sha, &hooks).unwrap();
    }

    #[test]
    fn a_seats_token_fetches_for_that_seat_only() {
        let s = |x: &str| Some(x.to_owned());
        assert_eq!(which_seat(s("seat_a"), None), Ok("seat_a".to_owned()));
        assert_eq!(
            which_seat(s("seat_a"), s("seat_a")),
            Ok("seat_a".to_owned())
        );
        assert_eq!(
            which_seat(s("seat_a"), s("seat_b")),
            Err((StatusCode::FORBIDDEN, "fetch_other_seat")),
            "reason: other_seat_fetched"
        );
        assert_eq!(which_seat(None, s("seat_b")), Ok("seat_b".to_owned()));
        assert_eq!(
            which_seat(None, None),
            Err((StatusCode::BAD_REQUEST, "fetch_fields"))
        );
    }

    #[test]
    fn a_seat_fetches_at_most_thirty_times_a_minute() {
        let t = Instant::now();
        let seat = format!("seat_rate_{}", std::process::id());
        for i in 0..PER_MINUTE {
            assert!(within_rate(&seat, t), "reason: refused_early {i}");
        }
        assert!(!within_rate(&seat, t), "reason: no_limit");
        assert!(
            within_rate(&seat, t + Duration::from_secs(61)),
            "reason: never_frees"
        );
    }
}
