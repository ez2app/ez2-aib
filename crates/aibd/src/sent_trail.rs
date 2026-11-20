//! 送回倉, second slice (N-webonly-c6-merge; 519d0 11-20 22:19): what became of each branch a
//! seat sent back — `sent` → `pushed` → `pr_opened` — so the page shows the one next button
//! and a step that failed is asked again from where it stood.
//!
//! The three steps stay three acts, each with its own fingerprint: `seat_send_back`
//! ([`crate::send_back`]), then `repo_push` ([`crate::repo_push`], G2), then `pr_open`
//! ([`crate::pr_open`], G3). This file only keeps the trail between them, in
//! `<data>/sent-back.json`, written by aibd alone:
//! - 送回倉 done ⇒ an entry `sent` with the seat, the repo, the branch and the head;
//! - a push done for that branch ⇒ `pushed` (and the sha that went, which is the sent head
//!   unless a person moved the branch in the repo first);
//! - a pull request opened for it ⇒ `pr_opened` with its number.
//!
//! A step that fails moves nothing: the entry keeps its state and says which step failed and
//! why, so the same button asks again. A state never goes back, and a later success clears the
//! failure. The trail is what the page draws, not a gate: each act reads the repo and the
//! remote for itself and asks the owner each time. Nothing here reaches `main` — a branch gets
//! into `main` through its pull request on GitHub, and no act of aibd merges one.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const FILE: &str = "sent-back.json";
/// Entries kept in the file; the oldest go first.
pub const KEEP: usize = 200;
/// Entries of one seat the seat list shows, newest first.
pub const SHOWN: usize = 5;

pub const STEP_PUSH: &str = "push";
pub const STEP_PR: &str = "pr";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Sent,
    Pushed,
    PrOpened,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Failed {
    pub step: String,
    pub why: String,
    pub at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Entry {
    pub seat_id: String,
    pub repo: String,
    pub branch: String,
    /// The head 送回倉 wrote the branch at.
    pub head: String,
    pub state: State,
    /// The sha a push carried (the sent head, unless the branch was moved in the repo).
    #[serde(default)]
    pub pushed: Option<String>,
    #[serde(default)]
    pub number: Option<u64>,
    #[serde(default)]
    pub failed: Option<Failed>,
    pub at: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct Trail {
    #[serde(default)]
    sent: Vec<Entry>,
}

/// One writer at a time in this process (aibd is the only writer of its data folder).
static WRITING: Mutex<()> = Mutex::new(());

fn file(data: &Path) -> PathBuf {
    data.join(FILE)
}

/// The trail as the file holds it; a file that is not there or does not parse holds none.
fn load(data: &Path) -> Trail {
    std::fs::read(file(data))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn save(data: &Path, t: &Trail) -> bool {
    let tmp = data.join(format!("{FILE}.tmp"));
    let Ok(body) = serde_json::to_vec_pretty(t) else {
        return false;
    };
    std::fs::write(&tmp, body).is_ok() && std::fs::rename(&tmp, file(data)).is_ok()
}

/// Change the trail under the one lock; `change` says whether anything is to be written.
fn with<T>(data: &Path, change: impl FnOnce(&mut Trail) -> Option<T>) -> Option<T> {
    let _held = WRITING
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let mut t = load(data);
    let out = change(&mut t)?;
    save(data, &t).then_some(out)
}

fn entry_of<'a>(t: &'a mut Trail, repo: &str, branch: &str) -> Option<&'a mut Entry> {
    t.sent
        .iter_mut()
        .find(|e| e.repo == repo && e.branch == branch)
}

/// 送回倉 made `branch` at `head` for the seat. A name is made once (the branch is only ever
/// created), so an entry of that name already here is left as it is.
pub fn sent(data: &Path, seat_id: &str, repo: &str, branch: &str, head: &str, now: &str) -> bool {
    with(data, |t| {
        if entry_of(t, repo, branch).is_some() {
            return None;
        }
        t.sent.push(Entry {
            seat_id: seat_id.to_owned(),
            repo: repo.to_owned(),
            branch: branch.to_owned(),
            head: head.to_owned(),
            state: State::Sent,
            pushed: None,
            number: None,
            failed: None,
            at: now.to_owned(),
        });
        let over = t.sent.len().saturating_sub(KEEP);
        t.sent.drain(..over);
        Some(())
    })
    .is_some()
}

/// A push of `branch` went through with `sha`. The entry (when a seat sent this branch) is
/// `pushed` from now on — or stays `pr_opened` — and is returned for the seat's event.
pub fn pushed(data: &Path, repo: &str, branch: &str, sha: &str, now: &str) -> Option<Entry> {
    with(data, |t| {
        let e = entry_of(t, repo, branch)?;
        e.state = e.state.max(State::Pushed);
        e.pushed = Some(sha.to_owned());
        e.failed = None; // the push went through: what failed before is over
        e.at = now.to_owned();
        Some(e.clone())
    })
}

/// A pull request `number` was opened for `branch`.
pub fn pr_opened(data: &Path, repo: &str, branch: &str, number: u64, now: &str) -> Option<Entry> {
    with(data, |t| {
        let e = entry_of(t, repo, branch)?;
        e.state = State::PrOpened;
        e.number = Some(number);
        e.failed = None;
        e.at = now.to_owned();
        Some(e.clone())
    })
}

/// `step` failed for `branch` after the owner said yes. The state stays where it was, so the
/// same step is asked again; the entry says what failed.
pub fn failed(
    data: &Path,
    repo: &str,
    branch: &str,
    step: &str,
    why: &str,
    now: &str,
) -> Option<Entry> {
    with(data, |t| {
        let e = entry_of(t, repo, branch)?;
        e.failed = Some(Failed {
            step: step.to_owned(),
            why: why.to_owned(),
            at: now.to_owned(),
        });
        Some(e.clone())
    })
}

/// A step went through, as its act saw it: a push (`number` none) or a pull request. The
/// trail moves and, when a seat sent this branch, the seat's thread says so (`seat.pushed` /
/// `seat.pr_opened`: names, a short sha, a number — never a path, a URL or a host).
pub async fn step_done(
    st: &crate::api::AppState,
    data: PathBuf,
    repo: &str,
    branch: &str,
    sha: &str,
    number: Option<u64>,
) {
    let (r, b, s) = (repo.to_owned(), branch.to_owned(), sha.to_owned());
    let now = crate::sessions::now_rfc3339();
    let moved = tokio::task::spawn_blocking(move || match number {
        None => pushed(&data, &r, &b, &s, &now),
        Some(n) => pr_opened(&data, &r, &b, n, &now),
    })
    .await
    .ok()
    .flatten();
    let Some(e) = moved else {
        return;
    };
    let short = &sha[..12.min(sha.len())];
    let (kind, payload) = match number {
        None => (
            "seat.pushed",
            json!({"seat_id": e.seat_id, "repo": e.repo, "branch": e.branch, "sha12": short}),
        ),
        Some(n) => (
            "seat.pr_opened",
            json!({"seat_id": e.seat_id, "repo": e.repo, "branch": e.branch, "number": n}),
        ),
    };
    crate::seats::side_event(st, "seat", kind, payload).await;
}

/// A step failed after the owner said yes: the trail keeps its state and says why, and the
/// seat's thread says which step is to be asked again (`seat.send_step_failed`).
pub async fn step_failed(
    st: &crate::api::AppState,
    data: PathBuf,
    repo: &str,
    branch: &str,
    step: &'static str,
    why: &'static str,
) {
    let (r, b) = (repo.to_owned(), branch.to_owned());
    let now = crate::sessions::now_rfc3339();
    let kept = tokio::task::spawn_blocking(move || failed(&data, &r, &b, step, why, &now))
        .await
        .ok()
        .flatten();
    if let Some(e) = kept {
        crate::seats::side_event(
            st,
            "seat",
            "seat.send_step_failed",
            json!({"seat_id": e.seat_id, "repo": e.repo, "branch": e.branch,
                   "step": step, "why": why, "state": e.state}),
        )
        .await;
    }
}

/// The step the page offers next for an entry: `push`, then `pr`, then nothing.
pub fn next_of(state: State) -> Option<&'static str> {
    match state {
        State::Sent => Some(STEP_PUSH),
        State::Pushed => Some(STEP_PR),
        State::PrOpened => None,
    }
}

/// The seat list's `repo_copy.sent`: what this seat sent back, newest first, at most
/// [`SHOWN`]. Names, shas and a number only — never a path, a URL or a host.
pub fn of_seat(data: &Path, seat_id: &str) -> Vec<Value> {
    load(data)
        .sent
        .iter()
        .rev()
        .filter(|e| e.seat_id == seat_id)
        .take(SHOWN)
        .map(|e| {
            json!({
                "repo": e.repo, "branch": e.branch, "head": e.head, "state": e.state,
                "pushed": e.pushed, "number": e.number, "next": next_of(e.state),
                "failed": e.failed.as_ref().map(|f| json!({"step": f.step, "why": f.why})),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const H: &str = "1111111111111111111111111111111111111111";
    const H2: &str = "2222222222222222222222222222222222222222";
    const B: &str = "web/ab12c-1120-1";

    fn one(data: &Path) -> Value {
        let v = of_seat(data, "seat_ab12c0000");
        assert_eq!(v.len(), 1, "{v:?}");
        v[0].clone()
    }

    #[test]
    fn a_sent_branch_is_pushed_then_has_its_pull_request_and_never_goes_back() {
        let d = tempfile::tempdir().unwrap();
        assert!(sent(d.path(), "seat_ab12c0000", "demo", B, H, "t0"));
        let v = one(d.path());
        assert_eq!(
            (v["state"].as_str(), v["next"].as_str()),
            (Some("sent"), Some("push")),
            "reason: sent_is_not_the_first_state {v}"
        );
        let e = pushed(d.path(), "demo", B, H, "t1").unwrap();
        assert_eq!(e.seat_id, "seat_ab12c0000");
        let v = one(d.path());
        assert_eq!(
            (
                v["state"].as_str(),
                v["next"].as_str(),
                v["pushed"].as_str()
            ),
            (Some("pushed"), Some("pr"), Some(H)),
            "reason: a_push_did_not_advance {v}"
        );
        let e = pr_opened(d.path(), "demo", B, 12, "t2").unwrap();
        assert_eq!((e.state, e.number), (State::PrOpened, Some(12)));
        let v = one(d.path());
        assert_eq!(
            (
                v["state"].as_str(),
                v["next"].as_str(),
                v["number"].as_u64()
            ),
            (Some("pr_opened"), None, Some(12)),
            "reason: a_pull_request_did_not_end_the_trail {v}"
        );
        // pushed again later (new commits were not sent, but the same branch may be pushed
        // again): the pull request is still there
        pushed(d.path(), "demo", B, H2, "t3").unwrap();
        let v = one(d.path());
        assert_eq!(
            (
                v["state"].as_str(),
                v["pushed"].as_str(),
                v["number"].as_u64()
            ),
            (Some("pr_opened"), Some(H2), Some(12)),
            "reason: a_later_push_went_back {v}"
        );
    }

    #[test]
    fn a_failed_step_moves_nothing_and_is_asked_again() {
        let d = tempfile::tempdir().unwrap();
        assert!(sent(d.path(), "seat_ab12c0000", "demo", B, H, "t0"));
        failed(
            d.path(),
            "demo",
            B,
            STEP_PUSH,
            "push_not_fast_forward",
            "t1",
        )
        .unwrap();
        let v = one(d.path());
        assert_eq!(
            (
                v["state"].as_str(),
                v["next"].as_str(),
                v["failed"]["step"].as_str(),
                v["failed"]["why"].as_str()
            ),
            (
                Some("sent"),
                Some("push"),
                Some("push"),
                Some("push_not_fast_forward")
            ),
            "reason: a_failed_push_advanced {v}"
        );
        // the retry goes through: the failure is gone
        pushed(d.path(), "demo", B, H, "t2").unwrap();
        let v = one(d.path());
        assert_eq!(
            (v["state"].as_str(), v["failed"].is_null()),
            (Some("pushed"), true),
            "reason: a_success_kept_the_failure {v}"
        );
        failed(d.path(), "demo", B, STEP_PR, "pr_failed", "t3").unwrap();
        let v = one(d.path());
        assert_eq!(
            (
                v["state"].as_str(),
                v["next"].as_str(),
                v["failed"]["step"].as_str()
            ),
            (Some("pushed"), Some("pr"), Some("pr")),
            "reason: a_failed_pull_request_advanced {v}"
        );
    }

    #[test]
    fn only_a_branch_a_seat_sent_is_on_the_trail_and_each_seat_sees_its_own() {
        let d = tempfile::tempdir().unwrap();
        // a branch nobody sent (made by hand in the repo): nothing to advance, no seat named
        assert_eq!(pushed(d.path(), "demo", B, H, "t0"), None);
        assert_eq!(pr_opened(d.path(), "demo", B, 3, "t0"), None);
        assert_eq!(failed(d.path(), "demo", B, STEP_PUSH, "x", "t0"), None);
        assert!(!file(d.path()).exists(), "reason: a_trail_for_nothing_sent");

        assert!(sent(d.path(), "seat_ab12c0000", "demo", B, H, "t1"));
        // the same name is never recorded twice (the branch is only ever created)
        assert!(!sent(d.path(), "seat_zz99z0000", "demo", B, H2, "t2"));
        // the same branch name in another repo is another entry
        assert!(sent(d.path(), "seat_zz99z0000", "other", B, H2, "t3"));
        pushed(d.path(), "other", B, H2, "t4").unwrap();
        assert_eq!(
            one(d.path())["state"],
            "sent",
            "reason: another_repo_advanced_it"
        );
        let theirs = of_seat(d.path(), "seat_zz99z0000");
        assert_eq!(
            (theirs.len(), theirs[0]["repo"].as_str()),
            (1, Some("other")),
            "reason: a_seat_saw_another_seats {theirs:?}"
        );
        assert!(of_seat(d.path(), "seat_nobody").is_empty());
    }

    #[test]
    fn the_list_is_newest_first_and_bounded_and_says_no_path() {
        let d = tempfile::tempdir().unwrap();
        for n in 1..=(KEEP + 3) {
            let b = format!("web/ab12c-1120-{n}");
            assert!(sent(d.path(), "seat_ab12c0000", "demo", &b, H, "t"));
        }
        assert_eq!(load(d.path()).sent.len(), KEEP, "reason: unbounded_file");
        let v = of_seat(d.path(), "seat_ab12c0000");
        assert_eq!(v.len(), SHOWN, "reason: unbounded_list");
        assert_eq!(
            v[0]["branch"].as_str().unwrap(),
            format!("web/ab12c-1120-{}", KEEP + 3),
            "reason: not_newest_first"
        );
        let keys: Vec<&str> = v[0]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            ["branch", "failed", "head", "next", "number", "pushed", "repo", "state"],
            "reason: the_list_says_more_than_names"
        );
        // a file that does not parse holds none, and the next send starts a new one
        std::fs::write(file(d.path()), b"{not json").unwrap();
        assert!(of_seat(d.path(), "seat_ab12c0000").is_empty());
        assert!(sent(d.path(), "seat_ab12c0000", "demo", B, H, "t"));
        assert_eq!(of_seat(d.path(), "seat_ab12c0000").len(), 1);
    }

    /// aibd's own source, test modules cut off: `(file name, text)`.
    fn sources() -> Vec<(String, String)> {
        fn walk(dir: &Path, out: &mut Vec<(String, String)>) {
            for e in std::fs::read_dir(dir).unwrap().flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    let text = std::fs::read_to_string(&p).unwrap().replace("\r\n", "\n");
                    let cut = ["\n#[cfg(test)]\nmod ", "\n#[cfg(all(test, unix))]\nmod "]
                        .iter()
                        .filter_map(|m| text.find(m))
                        .min()
                        .unwrap_or(text.len());
                    let name = p.file_name().unwrap().to_string_lossy().into_owned();
                    out.push((name, text[..cut].to_owned()));
                }
            }
        }
        let mut out = Vec::new();
        walk(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"), &mut out);
        assert!(out.len() > 50, "precondition: aibd's source was not found");
        out
    }

    fn files_with(all: &[(String, String)], what: &str) -> Vec<String> {
        let mut v: Vec<String> = all
            .iter()
            .filter(|(_, t)| t.contains(what))
            .map(|(n, _)| n.clone())
            .collect();
        v.sort();
        v
    }

    /// 合 main 一律走 PR (DESIGN-web-merge §8-3): the ways aibd can change a registered repo or
    /// its remote are counted here, and none of them names `main` or merges a pull request.
    #[test]
    fn main_is_reached_through_a_pull_request_only() {
        let all = sources();
        // one git push in all of aibd, and its destination is the branch that passed branch_ok
        assert_eq!(
            files_with(&all, "\"push\",\n"),
            ["repo_push.rs"],
            "reason: a_second_git_push"
        );
        let push = &all.iter().find(|(n, _)| n == "repo_push.rs").unwrap().1;
        assert_eq!(
            push.matches("\"push\",\n").count(),
            1,
            "reason: a_second_git_push"
        );
        assert!(
            push.contains("let spec = format!(\"{}:refs/heads/{}\", now.sha, now.branch);"),
            "reason: the_push_names_another_destination"
        );
        assert!(
            push.contains("    if !branch_ok(branch) {\n        return Err(no(StatusCode::BAD_REQUEST, \"push_branch\"));"),
            "reason: a_branch_is_read_before_its_name_is_checked"
        );
        assert!(
            !crate::repo_push::branch_ok("main") && !crate::repo_push::branch_ok("web/main"),
            "reason: main_is_a_branch_to_push"
        );
        for word in ["--force", "\"-f\"", "--mirror", "--delete", "--tags", "\"+"] {
            assert!(!push.contains(word), "reason: the_push_may_{word}");
        }
        // one place writes a ref of a registered repo, and only a new web/ branch
        assert_eq!(
            files_with(&all, "\"update-ref\""),
            ["send_back.rs"],
            "reason: a_second_ref_writer"
        );
        // one way to GitHub that changes anything: a pull request is opened, as a draft, into main
        let pr = &all.iter().find(|(n, _)| n == "pr_open.rs").unwrap().1;
        let api = pr.split("pub trait PrApi").nth(1).unwrap();
        let api = &api[..api.find("\n}\n").unwrap()];
        assert_eq!(
            api.matches("fn ").count(),
            1,
            "reason: the_github_client_does_more_than_open"
        );
        assert!(
            api.contains("fn open("),
            "reason: the_github_client_does_more_than_open"
        );
        assert_eq!(
            (crate::pr_open::BASE, crate::pr_open::DRAFT),
            ("main", true),
            "reason: a_pull_request_not_a_draft_into_main"
        );
        for (name, text) in &all {
            for word in [
                "/merge\"",
                "pulls/{}/merge",
                "\"pr\", \"merge\"",
                "merge_pull",
                "enablePullRequestAutoMerge",
            ] {
                assert!(
                    !text.contains(word),
                    "reason: {name}_merges_a_pull_request ({word})"
                );
            }
        }
        // and the three acts of this trail are the only act names that send, push or open
        let acts = (
            crate::send_back::SEND_BACK,
            crate::repo_push::PUSH,
            crate::pr_open::OPEN,
        );
        assert_eq!(acts, ("seat_send_back", "repo_push", "pr_open"));
    }

    #[cfg(unix)]
    mod whole_way {
        use super::*;
        use crate::pr_open::{Opened, PrApi, PrError};
        use std::process::Command;

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

        fn script(p: &Path, body: &str) {
            use std::os::unix::fs::PermissionsExt;
            std::fs::write(p, format!("#!/bin/sh\n{body}\n")).unwrap();
            std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o755)).unwrap();
        }

        type Call = (String, String, String, bool);
        struct Stub(Mutex<Vec<Call>>);
        impl PrApi for Stub {
            fn open(
                &self,
                slug: &str,
                head: &str,
                base: &str,
                _title: &str,
                _body: &str,
                draft: bool,
            ) -> Result<Opened, PrError> {
                self.0
                    .lock()
                    .unwrap()
                    .push((slug.into(), head.into(), base.into(), draft));
                Ok(Opened {
                    number: 7,
                    url: "https://github.example/o/r/pull/7".into(),
                })
            }
        }

        /// 送回倉 → 推 → 開 PR, each step as its act runs it (the fingerprint is the act
        /// layer's): the remote gains the one web/ branch, `main` stands where it stood at
        /// both ends, the checkout is not touched, and the pull request is a draft into main.
        /// A push that is refused in between moves nothing and is asked again.
        #[test]
        fn a_sent_branch_goes_all_the_way_and_main_never_moves() {
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
            let main = g(&repo, &["rev-parse", "HEAD"]);
            g(
                &repo,
                &[
                    "push",
                    "-q",
                    remote.to_str().unwrap(),
                    "HEAD:refs/heads/main",
                ],
            );
            // what a seat committed: in the repo's objects, on no branch (as 送回倉 leaves it
            // before the branch is made)
            g(&repo, &["checkout", "-q", "-b", "tmp"]);
            std::fs::write(repo.join("b.txt"), "b").unwrap();
            g(&repo, &["add", "."]);
            g(&repo, &["commit", "-q", "-m", "two"]);
            let head = g(&repo, &["rev-parse", "HEAD"]);
            g(&repo, &["checkout", "-q", "main"]);
            g(&repo, &["branch", "-q", "-D", "tmp"]);
            let all = crate::repos::Repos {
                repos: vec![crate::repos::Repo {
                    name: "demo".into(),
                    path: repo.to_string_lossy().into_owned(),
                    remote: remote.to_string_lossy().into_owned(),
                    manifest: crate::seat_worktree::Manifest::default(),
                    added_at: 0,
                }],
            };
            std::fs::write(
                data.join(crate::repos::FILE),
                serde_json::to_vec(&all).unwrap(),
            )
            .unwrap();
            let (leak_ok, leak_hit) = (root.join("leak-ok"), root.join("leak-hit"));
            script(&leak_ok, "cat >/dev/null; exit 0");
            script(&leak_hit, "cat >/dev/null; exit 1");
            let seat = "seat_ab12c0000";
            let state = || of_seat(&data, seat)[0].clone();
            let remote_refs = || {
                g(
                    &remote,
                    &["for-each-ref", "--format=%(refname) %(objectname)"],
                )
            };

            // 1. 送回倉
            let stem = crate::send_back::branch_stem("ab12c", "1120").unwrap();
            let branch = crate::send_back::write_branch(&repo, &hooks, &stem, &head).unwrap();
            assert_eq!(branch, "web/ab12c-1120-1");
            assert!(sent(&data, seat, "demo", &branch, &head, "t0"));
            assert_eq!(remote_refs(), format!("refs/heads/main {main}"));

            // 2. 推: refused once (the scan finds something), nothing moves, asked again
            let t = crate::repo_push::target(&data, "demo", &branch, &hooks).unwrap();
            assert_eq!(t.sha, head, "reason: the_push_reads_another_head");
            let no = crate::repo_push::push(&data, &hooks, &t, Some(&leak_hit)).unwrap_err();
            failed(&data, "demo", &branch, STEP_PUSH, no.1, "t1").unwrap();
            assert_eq!(
                (state()["state"].as_str(), state()["next"].as_str()),
                (Some("sent"), Some("push")),
                "reason: a_refused_push_advanced"
            );
            assert_eq!(
                remote_refs(),
                format!("refs/heads/main {main}"),
                "reason: a_refused_push_reached_the_remote"
            );
            let p = crate::repo_push::push(&data, &hooks, &t, Some(&leak_ok)).unwrap();
            pushed(&data, &p.repo, &p.branch, &p.sha, "t2").unwrap();
            assert_eq!(
                remote_refs(),
                format!("refs/heads/main {main}\nrefs/heads/{branch} {head}"),
                "reason: the_remote_is_not_main_plus_the_one_branch"
            );

            // 3. 開 PR: a draft into main, from the branch
            let stub = Stub(Mutex::new(Vec::new()));
            let o = crate::pr_open::open(&stub, "o/r", &t, "title", "body").unwrap();
            pr_opened(&data, "demo", &branch, o.number, "t3").unwrap();
            assert_eq!(
                *stub.0.lock().unwrap(),
                [("o/r".to_owned(), branch.clone(), "main".to_owned(), true)],
                "reason: the_pull_request_is_not_a_draft_into_main"
            );
            let v = state();
            assert_eq!(
                (
                    v["state"].as_str(),
                    v["number"].as_u64(),
                    v["next"].as_str()
                ),
                (Some("pr_opened"), Some(7), None),
                "reason: the_trail_did_not_end {v}"
            );

            // main never moved, here or there; the checkout is as it was
            assert_eq!(
                remote_refs(),
                format!("refs/heads/main {main}\nrefs/heads/{branch} {head}"),
                "reason: main_moved_on_the_remote"
            );
            assert_eq!(
                g(&repo, &["rev-parse", "refs/heads/main"]),
                main,
                "reason: main_moved_in_the_repo"
            );
            assert_eq!(g(&repo, &["symbolic-ref", "HEAD"]), "refs/heads/main");
            assert_eq!(
                g(&repo, &["status", "--porcelain"]),
                "",
                "reason: checkout_touched"
            );
            assert!(!repo.join("b.txt").exists(), "reason: checkout_touched");
        }
    }
}
