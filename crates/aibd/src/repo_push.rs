//! G2 (DESIGN-repo-push-g2-7b81e-1120, 5d33a PREREAD-repopush-g2): the built-in page pushes one
//! `web/…` branch of a registered repo to the remote the repo was registered with, each time
//! with the owner's fingerprint.
//!
//! `POST /api/v1/act {"type":"repo_push","repo":…,"branch":"web/…","ticket"?}` from the built-in
//! page in this pairing only. The page names a registered repo and a branch; everything else is
//! aibd's reading ([`target`]): the repo's path and remote as registered, the branch's head now.
//! The ticket binds exactly that ([`push_ask`]), and [`push`] reads it all again first.
//!
//! How the push runs (R4: no git with a seat-writable config, hook or helper):
//! - the registered repo passes `repo_safe` right before git touches it;
//! - aibd fetches the branch into a bare repository it made for this one push, with the fixed
//!   clean environment of [`crate::seat_worktree::git`];
//! - with the fingerprint given, it fetches the remote's own `main` (never a local ref) and
//!   refuses when the `.github` tree of what is pushed differs from the remote main's (L2);
//! - the leak scan (`aib-public-leak`, as the pre-push hook runs it) must pass; a scan that
//!   cannot run refuses;
//! - `git push <the registered URL> <sha>:refs/heads/<branch>` from the bare repository: no
//!   remote name, no `+`, no force, no delete, no tag. Only here does the owner's own git
//!   authentication come back, as narrowly as [`net_command`] spells it (L1).
//!
//! Off unless `"repo_push": true` in `<data>/bridge.json`. Journals `repo.pushed` /
//! `repo.push_refused` with names and a short sha; never a path, a URL or a host.
use std::path::{Path, PathBuf};
use std::process::Command;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::guard::request_id;
use crate::seat_worktree::{git, repo_safe};

pub const PUSH: &str = "repo_push";
/// The operation the owner approves.
pub const OP: &str = "repo.push";

/// The programs the network step runs, by absolute path (5d33a L1: never looked up on PATH).
pub const GIT: &str = "/usr/bin/git";
pub const SSH: &str = "/usr/bin/ssh";
/// The PATH git's own helpers are found on.
pub const NET_PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin";
/// What ssh is told, all of it: no `~/.ssh/config` (so no ProxyCommand, no alias), no prompt,
/// only known host keys, nothing forwarded.
pub const SSH_OPTS: [&str; 10] = [
    "-F",
    "none",
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes", // only a host key already known
    "-o",
    "ClearAllForwardings=yes",
    "-o",
    "ForwardAgent=no", // the agent stays here
];

const HEAD_REF: &str = "refs/push/head";
const MAIN_REF: &str = "refs/push/main";
const ZERO: &str = "0000000000000000000000000000000000000000";

/// What would be pushed, as aibd read it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    pub repo: String,
    pub path: PathBuf,
    pub remote: String,
    pub branch: String,
    pub sha: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pushed {
    pub repo: String,
    pub branch: String,
    pub sha: String,
}

/// Why nothing is pushed: the status and the reason word.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused(pub StatusCode, pub &'static str);

fn no(status: StatusCode, why: &'static str) -> Refused {
    Refused(status, why)
}

/// How a remote is reached.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Ssh,
    Https,
    /// A folder on this machine: tests only (no real remote is one).
    #[cfg(test)]
    File,
}

/// The remote's kind: `https://…`, `ssh://…` or `user@host:path` / `host:path`. A local path,
/// `file://`, `git://`, `http://` or an option-looking word is none.
pub fn remote_kind(remote: &str) -> Option<Kind> {
    if remote.starts_with('-') || remote.chars().any(char::is_whitespace) {
        return None;
    }
    if remote.starts_with("https://") {
        return Some(Kind::Https);
    }
    if remote.starts_with("ssh://") {
        return Some(Kind::Ssh);
    }
    #[cfg(test)]
    if Path::new(remote).is_absolute() && Path::new(remote).is_dir() {
        return Some(Kind::File);
    }
    if remote.contains("://") {
        return None;
    }
    // scp-like: a host before the first ':' with no '/' in it (so not a path, not a drive)
    match remote.split_once(':') {
        Some((h, p)) if h.chars().count() > 1 && !h.contains(['/', '\\']) && !p.is_empty() => {
            Some(Kind::Ssh)
        }
        _ => None,
    }
}

/// A branch the page may push: the shape 送回倉 makes, `web/<code>-<MMDD>-<n>` — a code of 1–12
/// `a-z 0-9`, a real month and day, `n` 1–20. Never `main`, never another prefix.
pub fn branch_ok(branch: &str) -> bool {
    let Some(rest) = branch.strip_prefix("web/") else {
        return false;
    };
    let parts: Vec<&str> = rest.split('-').collect();
    let &[code, mmdd, n] = parts.as_slice() else {
        return false;
    };
    let code_ok = (1..=12).contains(&code.len())
        && code
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit());
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let date_ok = mmdd.len() == 4
        && digits(mmdd)
        && (1..=12).contains(&mmdd[..2].parse::<u32>().unwrap_or(0))
        && (1..=31).contains(&mmdd[2..].parse::<u32>().unwrap_or(0));
    let n_ok =
        digits(n) && !n.starts_with('0') && (1..=20).contains(&n.parse::<u32>().unwrap_or(0));
    code_ok && date_ok && n_ok
}

fn sha_ok(s: &str) -> bool {
    s.len() == 40
        && s != ZERO
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Read what would be pushed; nothing is changed. `hooks` is aibd's empty hooks folder.
pub fn target(data_dir: &Path, repo: &str, branch: &str, hooks: &Path) -> Result<Target, Refused> {
    if !branch_ok(branch) {
        return Err(no(StatusCode::BAD_REQUEST, "push_branch"));
    }
    let Some(r) = crate::repos::find(data_dir, repo) else {
        return Err(no(StatusCode::NOT_FOUND, "push_repo_unknown"));
    };
    if remote_kind(&r.remote).is_none() {
        return Err(no(StatusCode::CONFLICT, "push_no_remote"));
    }
    let path = PathBuf::from(&r.path);
    if repo_safe(&path).is_err() {
        return Err(no(StatusCode::CONFLICT, "push_repo_unsafe"));
    }
    let spec = format!("refs/heads/{branch}^{{commit}}");
    let sha = git(
        &path,
        hooks,
        &["rev-parse", "--verify", "--quiet", spec.as_str()],
    )
    .map_err(|_| no(StatusCode::NOT_FOUND, "push_branch_unknown"))?;
    if !sha_ok(&sha) {
        return Err(no(StatusCode::NOT_FOUND, "push_branch_unknown"));
    }
    Ok(Target {
        repo: r.name,
        path,
        remote: r.remote,
        branch: branch.to_owned(),
        sha,
    })
}

/// The remote's path as the window shows it: its last 30 characters (`owner/repo`).
fn path_shown(remote: &str) -> String {
    let p = match remote.split_once("://") {
        Some((_, rest)) => rest.split_once('/').map_or("", |(_, x)| x),
        None => remote.split_once(':').map_or(remote, |(_, x)| x),
    };
    let c: Vec<char> = p.chars().collect();
    c[c.len().saturating_sub(30)..].iter().collect()
}

/// What the owner is asked. The window is aibd's words: the repo's name, the branch, the head's
/// first 12, then where it goes — the host with its end kept and the path's end. The window
/// keeps a text's last 80, so the remote is what is never cut.
pub fn push_ask(t: &Target, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "repo": t.repo, "remote": t.remote, "branch": t.branch, "sha": t.sha,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!(
            "{} {} {} → {} · {}",
            t.repo,
            t.branch,
            &t.sha[..12.min(t.sha.len())],
            crate::seat_trust::host_shown(&crate::repos::remote_host(&t.remote)),
            path_shown(&t.remote),
        ),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// The one git call that talks to the remote, spelled whole (5d33a L1). git's HOME stays
/// aibd's own folder and no system or user config is read, so no `url.*.insteadOf`,
/// `core.sshCommand` or `credential.helper` written anywhere takes part; ssh finds the
/// account's keys by itself (the account database, not `$HOME`) and is given the agent socket
/// when aibd has one; https gets the platform's credential helper, named here.
pub fn net_command(
    kind: Kind,
    scratch: &Path,
    hooks: &Path,
    agent: Option<std::ffi::OsString>,
    args: &[&str],
) -> Command {
    let home = hooks.parent().unwrap_or(hooks);
    let mut c = Command::new(GIT);
    c.env_clear()
        .env("HOME", home)
        .env("PATH", NET_PATH)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env(
            "GIT_CONFIG_GLOBAL",
            if cfg!(windows) { "NUL" } else { "/dev/null" },
        )
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("LC_ALL", "C")
        .arg("-C")
        .arg(scratch)
        .arg("-c")
        .arg(format!("core.hooksPath={}", hooks.display()))
        .args(["-c", "core.fsmonitor=false", "-c", "protocol.allow=never"]);
    match kind {
        Kind::Ssh => {
            c.env("GIT_SSH_COMMAND", format!("{SSH} {}", SSH_OPTS.join(" ")))
                .args(["-c", "protocol.ssh.allow=always"]);
            if let Some(sock) = agent {
                c.env("SSH_AUTH_SOCK", sock);
            }
        }
        Kind::Https => {
            let helper = if cfg!(windows) {
                "manager"
            } else {
                "osxkeychain"
            };
            c.args(["-c", "protocol.https.allow=always"])
                // the first clears any list, the second names the one helper
                .args(["-c", "credential.helper="])
                .arg("-c")
                .arg(format!("credential.helper={helper}"));
        }
        #[cfg(test)]
        Kind::File => {
            c.args(["-c", "protocol.file.allow=always"]);
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // its own group: at the limit the whole group is killed
        c.process_group(0);
    }
    c.args(args);
    c
}

fn net(kind: Kind, scratch: &Path, hooks: &Path, args: &[&str]) -> (Option<i32>, String) {
    let cmd = net_command(
        kind,
        scratch,
        hooks,
        std::env::var_os("SSH_AUTH_SOCK"),
        args,
    );
    let (rc, out) = crate::gh_view::run_timed(cmd, &crate::timers::REPO_PUSH_WAIT, None);
    (rc, String::from_utf8_lossy(&out).into_owned())
}

/// The `.github` tree of a commit in `scratch` (empty: it has none).
fn github_tree(scratch: &Path, hooks: &Path, commit: &str) -> String {
    git(
        scratch,
        hooks,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            format!("{commit}:.github").as_str(),
        ],
    )
    .unwrap_or_default()
}

/// The leak scan beside aibd's own program (`aib-public-leak`), when it is there.
pub fn leak_program() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "aib-public-leak.exe"
    } else {
        "aib-public-leak"
    };
    let p = std::env::current_exe().ok()?.parent()?.join(name);
    p.is_file().then_some(p)
}

/// The scan as the pre-push hook runs it; `Ok` only on its exit 0.
fn leak_scan(prog: &Path, scratch: &Path, t: &Target) -> Result<(), Refused> {
    let mut c = Command::new(prog);
    c.args(["--local-sources", "--pre-push", "--repo"])
        .arg(scratch)
        .args(["--remote", t.remote.as_str()]);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    let line = format!(
        "refs/heads/{b} {s} refs/heads/{b} {ZERO}\n",
        b = t.branch,
        s = t.sha
    );
    let (rc, _) =
        crate::gh_view::run_timed(c, &crate::timers::REPO_PUSH_WAIT, Some(line.into_bytes()));
    match rc {
        Some(0) => Ok(()),
        Some(1) => Err(no(StatusCode::CONFLICT, "push_leak")),
        _ => Err(no(StatusCode::SERVICE_UNAVAILABLE, "push_leak_unavailable")),
    }
}

/// Removes the one-push repository when the push is over, however it ends.
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The push itself. The caller has the owner's fingerprint for `approved`; nothing here asks.
/// `leak` is the scan to run (none: refused).
pub fn push(
    data_dir: &Path,
    hooks: &Path,
    approved: &Target,
    leak: Option<&Path>,
) -> Result<Pushed, Refused> {
    // read again: the repo still registered so, the branch still at that head
    let now = target(data_dir, &approved.repo, &approved.branch, hooks)?;
    if now != *approved {
        return Err(no(StatusCode::CONFLICT, "push_changed"));
    }
    let Some(kind) = remote_kind(&now.remote) else {
        return Err(no(StatusCode::CONFLICT, "push_no_remote"));
    };
    let Some(leak) = leak else {
        return Err(no(StatusCode::SERVICE_UNAVAILABLE, "push_leak_unavailable"));
    };
    let Ok(id) = crate::sessions::new_id("push_") else {
        return Err(no(StatusCode::INTERNAL_SERVER_ERROR, "push_scratch"));
    };
    let scratch = Scratch(data_dir.join("push-scratch").join(id));
    let made = crate::platform::create_private_dir(&scratch.0).is_ok()
        && git(&scratch.0, hooks, &["init", "-q", "--bare", "."]).is_ok();
    if !made {
        return Err(no(StatusCode::INTERNAL_SERVER_ERROR, "push_scratch"));
    }
    // the registered repo is checked right before git reads it (5d33a N1)
    if repo_safe(&now.path).is_err() {
        return Err(no(StatusCode::CONFLICT, "push_repo_unsafe"));
    }
    let from = now.path.to_string_lossy().into_owned();
    let take = format!("refs/heads/{}:{HEAD_REF}", now.branch);
    let fetched = git(
        &scratch.0,
        hooks,
        &[
            "-c",
            "protocol.file.allow=always",
            "-c",
            "fetch.fsckObjects=true",
            "fetch",
            "-q",
            "--no-tags",
            from.as_str(),
            take.as_str(),
        ],
    );
    let got = git(
        &scratch.0,
        hooks,
        &["rev-parse", "--verify", "--quiet", HEAD_REF],
    );
    if fetched.is_err() || got.as_deref() != Ok(now.sha.as_str()) {
        return Err(no(StatusCode::CONFLICT, "push_changed"));
    }
    // the remote's own main, fetched now with the same narrow authentication (5d33a L2)
    let want_main = format!("refs/heads/main:{MAIN_REF}");
    let (rc, _) = net(
        kind,
        &scratch.0,
        hooks,
        &[
            "fetch",
            "-q",
            "--no-tags",
            now.remote.as_str(),
            want_main.as_str(),
        ],
    );
    if rc != Some(0) {
        return Err(no(StatusCode::BAD_GATEWAY, "push_base_unknown"));
    }
    if github_tree(&scratch.0, hooks, HEAD_REF) != github_tree(&scratch.0, hooks, MAIN_REF) {
        return Err(no(StatusCode::CONFLICT, "push_touches_github"));
    }
    leak_scan(leak, &scratch.0, &now)?;
    let spec = format!("{}:refs/heads/{}", now.sha, now.branch);
    let (rc, out) = net(
        kind,
        &scratch.0,
        hooks,
        &[
            "push",
            "--porcelain",
            "--no-verify",
            now.remote.as_str(),
            spec.as_str(),
        ],
    );
    match rc {
        Some(0) => Ok(Pushed {
            repo: now.repo,
            branch: now.branch,
            sha: now.sha,
        }),
        Some(_) if out.contains("[rejected]") || out.contains("non-fast-forward") => {
            Err(no(StatusCode::CONFLICT, "push_not_fast_forward"))
        }
        Some(_) => Err(no(StatusCode::BAD_GATEWAY, "push_failed")),
        None => Err(no(StatusCode::GATEWAY_TIMEOUT, "push_no_answer")),
    }
}

fn words(reason: &str) -> &'static str {
    match reason {
        "repo_push_off" => "pushing is off on this machine (repo_push in bridge.json)",
        "push_fields" => "body must be {type, repo, branch, ticket?}",
        "push_branch" => "only a web/<code>-<MMDD>-<n> branch is pushed",
        "push_repo_unknown" => "no repo is registered by that name",
        "push_branch_unknown" => "the registered repo has no such branch",
        "push_no_remote" => "the repo was registered without an ssh or https remote",
        "push_repo_unsafe" => "the registered repo's git settings are not ones aibd runs git with",
        "push_changed" => {
            "the repo, its remote or the branch's head changed since the fingerprint: ask again"
        }
        "push_base_unknown" => "the remote's main could not be fetched",
        "push_touches_github" => "the branch changes .github/ against the remote's main",
        "push_leak" => "the leak scan found something",
        "push_leak_unavailable" => "the leak scan could not run, so nothing is pushed",
        "push_not_fast_forward" => {
            "the remote already has this branch and this is not a fast-forward"
        }
        "push_no_answer" => "git did not answer in 120 s",
        _ => "the push failed",
    }
}

fn refuse(r: &Refused) -> Response {
    let code = match r.0 {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::FORBIDDEN => "forbidden",
        _ => "unavailable",
    };
    (
        r.0,
        Json(json!({"code": code, "reason": r.1, "message": words(r.1),
            "request_id": request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    repo: String,
    branch: String,
    #[serde(default)]
    ticket: Option<String>,
}

/// `repo_push`, for the built-in page `by` (`page:<pair>`).
pub async fn act(st: AppState, body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Raw>(body) else {
        return refuse(&no(StatusCode::BAD_REQUEST, "push_fields"));
    };
    let Some(data) = crate::bridge_cfg::dir().filter(|d| crate::bridge_cfg::load(d).repo_push)
    else {
        return refuse(&no(StatusCode::FORBIDDEN, "repo_push_off"));
    };
    let hooks = crate::seats::seat_git_hooks(st.sessions.private_dir());
    let (d, h, repo, branch) = (
        data.clone(),
        hooks.clone(),
        req.repo.clone(),
        req.branch.clone(),
    );
    let read = tokio::task::spawn_blocking(move || {
        std::fs::create_dir_all(&h)
            .map_err(|_| no(StatusCode::INTERNAL_SERVER_ERROR, "push_scratch"))?;
        target(&d, &repo, &branch, &h)
    })
    .await;
    let t = match read {
        Ok(Ok(t)) => t,
        Ok(Err(r)) => return refuse(&r),
        Err(_) => return refuse(&no(StatusCode::INTERNAL_SERVER_ERROR, "push_scratch")),
    };
    let ask = push_ask(&t, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let approved = t.clone();
    let trail_dir = data.clone();
    let done = tokio::task::spawn_blocking(move || {
        // the switch is read again under the push (as T1's write does)
        if !crate::bridge_cfg::load(&data).repo_push {
            return Err(no(StatusCode::FORBIDDEN, "repo_push_off"));
        }
        push(&data, &hooks, &approved, leak_program().as_deref())
    })
    .await
    .unwrap_or(Err(no(StatusCode::INTERNAL_SERVER_ERROR, "push_scratch")));
    let short = &t.sha[..12.min(t.sha.len())];
    match done {
        Ok(p) => {
            crate::seats::side_event(
                &st,
                "repo",
                "repo.pushed",
                json!({"repo": p.repo, "branch": p.branch, "sha12": short, "by": by}),
            )
            .await;
            crate::sent_trail::step_done(&st, trail_dir, &p.repo, &p.branch, &p.sha, None).await;
            Json(json!({"done": true, "repo": p.repo, "branch": p.branch, "sha": p.sha,
                "remote_host": crate::seat_trust::host_shown(&crate::repos::remote_host(&t.remote))}))
            .into_response()
        }
        Err(r) => {
            crate::seats::side_event(
                &st,
                "repo",
                "repo.push_refused",
                json!({"repo": t.repo, "branch": t.branch, "why": r.1, "by": by}),
            )
            .await;
            let step = crate::sent_trail::STEP_PUSH;
            crate::sent_trail::step_failed(&st, trail_dir, &t.repo, &t.branch, step, r.1).await;
            refuse(&r)
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn g(dir: &Path, args: &[&str]) -> String {
        let o = Command::new("git")
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
            "precondition: git {args:?}: {}",
            String::from_utf8_lossy(&o.stderr)
        );
        String::from_utf8_lossy(&o.stdout).trim().to_owned()
    }

    /// A data folder with one registered repo `demo` (main + `web/ab12-1120-1` one commit
    /// ahead), its bare remote holding main, aibd's hooks folder and a leak scan that passes.
    struct World {
        _keep: tempfile::TempDir,
        data: PathBuf,
        repo: PathBuf,
        remote: PathBuf,
        hooks: PathBuf,
        leak_ok: PathBuf,
        leak_hit: PathBuf,
    }

    const BRANCH: &str = "web/ab12-1120-1";

    fn register(data: &Path, repo: &Path, remote: &str) {
        let r = crate::repos::Repo {
            name: "demo".into(),
            path: repo.to_string_lossy().into_owned(),
            remote: remote.to_owned(),
            manifest: crate::seat_worktree::Manifest::default(),
            added_at: 0,
        };
        let all = crate::repos::Repos { repos: vec![r] };
        std::fs::write(
            data.join(crate::repos::FILE),
            serde_json::to_vec(&all).unwrap(),
        )
        .unwrap();
    }

    fn script(p: &Path, body: &str) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    fn world() -> World {
        let keep = tempfile::tempdir().unwrap();
        let root = std::fs::canonicalize(keep.path()).unwrap();
        let (data, repo, remote) = (
            root.join("data"),
            root.join("repo"),
            root.join("remote.git"),
        );
        let hooks = data.join("seat-git").join("hooks");
        for d in [&repo, &remote, &hooks] {
            std::fs::create_dir_all(d).unwrap();
        }
        g(&remote, &["init", "-q", "--bare", "."]);
        g(&repo, &["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "a").unwrap();
        g(&repo, &["add", "."]);
        g(&repo, &["commit", "-q", "-m", "one"]);
        g(
            &repo,
            &[
                "push",
                "-q",
                remote.to_str().unwrap(),
                "HEAD:refs/heads/main",
            ],
        );
        g(&repo, &["checkout", "-q", "-b", BRANCH]);
        std::fs::write(repo.join("b.txt"), "b").unwrap();
        g(&repo, &["add", "."]);
        g(&repo, &["commit", "-q", "-m", "two"]);
        g(&repo, &["checkout", "-q", "main"]);
        register(&data, &repo, remote.to_str().unwrap());
        let (leak_ok, leak_hit) = (root.join("leak-ok"), root.join("leak-hit"));
        script(&leak_ok, "cat >/dev/null; exit 0");
        script(&leak_hit, "cat >/dev/null; exit 1");
        World {
            _keep: keep,
            data,
            repo,
            remote,
            hooks,
            leak_ok,
            leak_hit,
        }
    }

    fn remote_has(w: &World, r: &str) -> Option<String> {
        let o = Command::new("git")
            .arg("-C")
            .arg(&w.remote)
            .args(["rev-parse", "--verify", "--quiet", r])
            .output()
            .unwrap();
        o.status
            .success()
            .then(|| String::from_utf8_lossy(&o.stdout).trim().to_owned())
    }

    #[test]
    fn only_a_web_branch_of_the_made_shape_is_pushed() {
        for ok in [
            "web/ab12-1120-1",
            "web/a-0101-20",
            "web/0123456789ab-1231-9",
        ] {
            assert!(branch_ok(ok), "reason: refused_{ok}");
        }
        for bad in [
            "main",
            "web/main",
            "release/1",
            "feat/x-ab12-1120",
            "web/ab12-1120",
            "web/ab12-1120-0",
            "web/ab12-1120-21",
            "web/ab12-1120-01",
            "web/ab12-1320-1",
            "web/ab12-1132-1",
            "web/ab12-0000-1",
            "web/AB12-1120-1",
            "web/ab_12-1120-1",
            "web/0123456789abc-1120-1",
            "web/-1120-1",
            "web/ab12-1120-1-x",
            "web/ab12-1120-1 ",
            "web/../main",
            "refs/heads/web/ab12-1120-1",
            "+web/ab12-1120-1",
            "web/ab12-1120-1:refs/heads/main",
            "",
        ] {
            assert!(!branch_ok(bad), "reason: took_{bad:?}");
        }
    }

    #[test]
    fn only_an_ssh_or_https_remote_is_one() {
        for (r, k) in [
            ("https://github.com/o/r.git", Some(Kind::Https)),
            ("ssh://git@github.com:22/o/r", Some(Kind::Ssh)),
            ("git@github.com:o/r.git", Some(Kind::Ssh)),
            ("github.com:o/r.git", Some(Kind::Ssh)),
            ("", None),
            ("file:///x/y", None),
            ("git://github.com/o/r", None),
            ("http://github.com/o/r", None),
            ("/nonexistent/path/repo", None),
            ("./a:b", None),
            ("C:/Users/x/repo", None),
            ("--upload-pack=x", None),
            ("-oProxyCommand=x:y", None),
            ("git@github.com:o/r.git --force", None),
            ("ext::sh -c x", None),
        ] {
            assert_eq!(remote_kind(r), k, "reason: kind_of_{r:?}");
        }
    }

    /// 5d33a PREREAD-repopush-g2 L1: the network git is one spelled command — absolute programs,
    /// aibd's own HOME, no system or user config, ssh without its config file and with nothing
    /// forwarded, the https helper named after the list is cleared.
    #[test]
    fn the_network_git_is_spelled_whole_and_reads_no_ones_config() {
        let hooks = Path::new("/d/seat-git/hooks");
        let envs = |c: &Command| -> Vec<(String, Option<String>)> {
            c.get_envs()
                .map(|(k, v)| {
                    (
                        k.to_string_lossy().into_owned(),
                        v.map(|v| v.to_string_lossy().into_owned()),
                    )
                })
                .collect()
        };
        let args = |c: &Command| -> Vec<String> {
            c.get_args()
                .map(|a| a.to_string_lossy().into_owned())
                .collect()
        };
        let has = |e: &[(String, Option<String>)], k: &str, v: &str| {
            e.contains(&(k.to_owned(), Some(v.to_owned())))
        };
        let c = net_command(
            Kind::Ssh,
            Path::new("/d/push-scratch/x"),
            hooks,
            Some("/tmp/agent.sock".into()),
            &["push", "--porcelain", "u", "s:r"],
        );
        assert_eq!(c.get_program(), GIT, "reason: git_looked_up_on_path");
        assert!(Path::new(GIT).is_absolute() && Path::new(SSH).is_absolute());
        let e = envs(&c);
        assert!(
            has(&e, "HOME", "/d/seat-git"),
            "reason: home_is_not_aibds {e:?}"
        );
        assert!(has(&e, "PATH", NET_PATH), "reason: path_inherited");
        assert!(
            has(&e, "GIT_CONFIG_NOSYSTEM", "1"),
            "reason: system_config_read"
        );
        assert!(
            has(&e, "GIT_CONFIG_GLOBAL", "/dev/null"),
            "reason: user_config_read"
        );
        assert!(has(&e, "GIT_TERMINAL_PROMPT", "0"), "reason: may_prompt");
        assert!(
            has(&e, "SSH_AUTH_SOCK", "/tmp/agent.sock"),
            "reason: no_agent"
        );
        let ssh = e
            .iter()
            .find(|(k, _)| k == "GIT_SSH_COMMAND")
            .and_then(|(_, v)| v.clone())
            .unwrap_or_default();
        assert!(
            ssh.starts_with("/usr/bin/ssh "),
            "reason: ssh_looked_up_on_path {ssh}"
        );
        for opt in [
            "-F none",
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "ClearAllForwardings=yes",
            "ForwardAgent=no",
        ] {
            assert!(ssh.contains(opt), "reason: ssh_without_{opt}");
        }
        for k in ["GIT_ASKPASS", "SSH_ASKPASS", "GIT_EXEC_PATH", "GIT_CONFIG"] {
            assert!(!e.iter().any(|(n, _)| n == k), "reason: {k}_handed_on");
        }
        let a = args(&c);
        assert_eq!(a[..2], ["-C", "/d/push-scratch/x"]);
        assert!(
            a.contains(&"core.hooksPath=/d/seat-git/hooks".to_owned()),
            "reason: hooks_may_run"
        );
        assert!(
            a.contains(&"core.fsmonitor=false".to_owned()),
            "reason: fsmonitor_may_run"
        );
        assert!(
            a.contains(&"protocol.allow=never".to_owned()),
            "reason: any_protocol"
        );
        assert!(a.contains(&"protocol.ssh.allow=always".to_owned()));
        assert!(!a
            .iter()
            .any(|x| x.contains("https.allow") || x.contains("file.allow")));
        assert_eq!(a[a.len() - 4..], ["push", "--porcelain", "u", "s:r"]);
        assert!(
            !a.iter().any(|x| x == "--force"
                || x == "-f"
                || x == "--delete"
                || x == "--tags"
                || x.starts_with('+')),
            "reason: force_or_delete_in_argv"
        );
        // https: the helper list is cleared, then the one helper named
        let c = net_command(Kind::Https, Path::new("/s"), hooks, None, &["fetch"]);
        let a = args(&c);
        let i = a.iter().position(|x| x == "credential.helper=").unwrap();
        assert!(
            a[i + 2].starts_with("credential.helper=")
                && a[i + 2].len() > "credential.helper=".len(),
            "reason: helper_not_named_after_the_clear {a:?}"
        );
        assert!(a.contains(&"protocol.https.allow=always".to_owned()));
        assert!(!a.iter().any(|x| x.contains("ssh.allow")));
        let e = envs(&c);
        assert!(!e
            .iter()
            .any(|(k, _)| k == "GIT_SSH_COMMAND" || k == "SSH_AUTH_SOCK"));
    }

    #[test]
    fn the_window_keeps_where_it_goes() {
        let t = Target {
            repo: "a-rather-long-registered-repo-name-here".into(),
            path: PathBuf::from("/x"),
            remote: format!(
                "git@github.com.{}.attacker-owned.example:own/er/repo-name.git",
                "x".repeat(60)
            ),
            branch: BRANCH.into(),
            sha: "0123456789abcdef0123456789abcdef01234567".into(),
        };
        let a = push_ask(&t, "page:1".into());
        assert_eq!(a.operation, OP);
        let shown = crate::owner_ok::clean_resource(&a.resource);
        assert!(
            shown.ends_with("r-owned.example · own/er/repo-name.git"),
            "reason: remote_cut {shown}"
        );
        assert!(
            shown.contains("0123456789ab → "),
            "reason: head_cut {shown}"
        );
        // every part of the reading moves the ticket
        let p = |f: fn(&mut Target)| {
            let mut x = t.clone();
            f(&mut x);
            push_ask(&x, "page:1".into()).params != a.params
        };
        assert!(p(|x| x.sha = "1".repeat(40)), "reason: sha_not_bound");
        assert!(
            p(|x| x.remote = "git@github.com:o/r.git".into()),
            "reason: remote_not_bound"
        );
        assert!(
            p(|x| x.branch = "web/ab12-1120-2".into()),
            "reason: branch_not_bound"
        );
        assert!(p(|x| x.repo = "other".into()), "reason: repo_not_bound");
        assert!(!p(|x| x.path = PathBuf::from("/y")));
    }

    #[test]
    fn the_branch_goes_to_the_registered_remote_from_aibds_own_repository() {
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        assert_eq!(t.sha, g(&w.repo, &["rev-parse", BRANCH]));
        assert_eq!(t.remote, w.remote.to_string_lossy());
        // what the page may not get pushed
        for (repo, branch, why) in [
            ("demo", "main", "push_branch"),
            ("demo", "web/ab12-1120-2", "push_branch_unknown"),
            ("nope", BRANCH, "push_repo_unknown"),
        ] {
            assert_eq!(
                target(&w.data, repo, branch, &w.hooks).map_err(|r| r.1),
                Err(why),
                "reason: target_{repo}_{branch}"
            );
        }
        // a pre-push hook in the registered repo: the push runs from aibd's own repository, so
        // it never runs
        let mark = w.repo.join("hook-ran");
        script(
            &w.repo.join(".git").join("hooks").join("pre-push"),
            &format!(": > '{}'", mark.display()),
        );
        let p = push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).unwrap();
        assert_eq!(
            (p.branch.as_str(), p.sha.as_str()),
            (BRANCH, t.sha.as_str())
        );
        assert_eq!(
            remote_has(&w, &format!("refs/heads/{BRANCH}")),
            Some(t.sha.clone()),
            "reason: not_pushed"
        );
        assert!(!mark.exists(), "reason: the_repos_hook_ran");
        assert_eq!(
            remote_has(&w, "refs/heads/main"),
            Some(g(&w.repo, &["rev-parse", "main"])),
            "reason: main_moved"
        );
        // nothing of the one-push repository is left
        let left = std::fs::read_dir(w.data.join("push-scratch"))
            .map(|d| d.count())
            .unwrap_or(0);
        assert_eq!(left, 0, "reason: scratch_left");
        // the registered repo itself was not written: no remote added, still on main
        assert_eq!(
            g(&w.repo, &["remote"]),
            "",
            "reason: remote_added_to_the_repo"
        );
        assert_eq!(g(&w.repo, &["rev-parse", "--abbrev-ref", "HEAD"]), "main");
    }

    #[test]
    fn what_changed_since_the_fingerprint_is_not_pushed() {
        // the branch moved
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        g(&w.repo, &["checkout", "-q", BRANCH]);
        std::fs::write(w.repo.join("c.txt"), "c").unwrap();
        g(&w.repo, &["add", "."]);
        g(&w.repo, &["commit", "-q", "-m", "three"]);
        g(&w.repo, &["checkout", "-q", "main"]);
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)),
            Err(Refused(StatusCode::CONFLICT, "push_changed")),
            "reason: moved_branch_pushed"
        );
        assert_eq!(remote_has(&w, &format!("refs/heads/{BRANCH}")), None);
        // the registered remote changed
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        let other = w.data.join("other.git");
        std::fs::create_dir_all(&other).unwrap();
        g(&other, &["init", "-q", "--bare", "."]);
        register(&w.data, &w.repo, other.to_str().unwrap());
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).map_err(|r| r.1),
            Err("push_changed"),
            "reason: pushed_to_a_changed_remote"
        );
        assert_eq!(remote_has(&w, &format!("refs/heads/{BRANCH}")), None);
        // a setting written into the registered repo after the reading
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        g(&w.repo, &["config", "core.sshCommand", "/bin/false"]);
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).map_err(|r| r.1),
            Err("push_repo_unsafe"),
            "reason: unsafe_repo_read_by_git"
        );
    }

    #[test]
    fn a_github_change_a_leak_or_a_rewound_branch_is_not_pushed() {
        // the branch changes .github/ against the remote's main
        let w = world();
        g(&w.repo, &["checkout", "-q", BRANCH]);
        std::fs::create_dir_all(w.repo.join(".github").join("workflows")).unwrap();
        std::fs::write(
            w.repo.join(".github").join("workflows").join("x.yml"),
            "on: push",
        )
        .unwrap();
        g(&w.repo, &["add", "."]);
        g(&w.repo, &["commit", "-q", "-m", "wf"]);
        g(&w.repo, &["checkout", "-q", "main"]);
        // a local ref moved up to the head does not hide it: the remote's own main is the base
        g(&w.repo, &["update-ref", "refs/remotes/origin/main", BRANCH]);
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).map_err(|r| r.1),
            Err("push_touches_github"),
            "reason: github_change_pushed"
        );
        assert_eq!(remote_has(&w, &format!("refs/heads/{BRANCH}")), None);
        // the scan finds something, or there is no scan
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_hit)).map_err(|r| r.1),
            Err("push_leak"),
            "reason: leak_pushed"
        );
        assert_eq!(
            push(&w.data, &w.hooks, &t, None).map_err(|r| r.1),
            Err("push_leak_unavailable"),
            "reason: pushed_without_a_scan"
        );
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.data.join("no-such-scan"))).map_err(|r| r.1),
            Err("push_leak_unavailable"),
            "reason: pushed_with_a_missing_scan"
        );
        assert_eq!(remote_has(&w, &format!("refs/heads/{BRANCH}")), None);
        // the remote already has the branch at another line: never forced
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        let elsewhere = w.data.join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        g(&elsewhere, &["init", "-q"]);
        std::fs::write(elsewhere.join("z.txt"), "z").unwrap();
        g(&elsewhere, &["add", "."]);
        g(&elsewhere, &["commit", "-q", "-m", "other line"]);
        let theirs = g(&elsewhere, &["rev-parse", "HEAD"]);
        g(
            &elsewhere,
            &[
                "push",
                "-q",
                w.remote.to_str().unwrap(),
                &format!("HEAD:refs/heads/{BRANCH}"),
            ],
        );
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).map_err(|r| r.1),
            Err("push_not_fast_forward"),
            "reason: rewound_branch_forced"
        );
        assert_eq!(
            remote_has(&w, &format!("refs/heads/{BRANCH}")),
            Some(theirs),
            "reason: remote_branch_moved"
        );
        // a remote without a main: no base to compare .github with
        let w = world();
        let t = target(&w.data, "demo", BRANCH, &w.hooks).unwrap();
        let o = Command::new("git")
            .arg("-C")
            .arg(&w.remote)
            .args(["update-ref", "-d", "refs/heads/main"])
            .output()
            .unwrap();
        assert!(o.status.success(), "precondition: main removed");
        assert_eq!(
            push(&w.data, &w.hooks, &t, Some(&w.leak_ok)).map_err(|r| r.1),
            Err("push_base_unknown"),
            "reason: pushed_without_a_base"
        );
    }
}
