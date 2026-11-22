//! G3 (DESIGN-pr-open-g3-7b81e-1120, 5d33a PREREAD-propen-g3; 519d0 11-20 22:30: asked by the
//! built-in page with the owner's fingerprint each time, as G2 is): one draft pull request for
//! a `web/…` branch of a registered repo, into `main`.
//!
//! `POST /api/v1/act {"type":"pr_open","repo":…,"branch":"web/…","title":…,"body":…,"ticket"?}`
//! from the built-in page in this pairing only. The page gives a registered repo's name, a
//! branch of the shape G2 pushes, and the words. aibd fixes the rest: the GitHub repository is
//! read off the remote the repo was registered with, the base is `main` (there is no field for
//! it), the pull request is a draft, and no label is put on (`ready-merge` waits for the slice
//! in which aibd itself checks a receipt). The words are cleaned and anything GitHub would act
//! on is quoted ([`quoted`]: `@name` would notify a person, `#12` would cross-link or close).
//!
//! The call to GitHub is behind [`PrApi`]. This slice has no real one: the token is not made
//! yet and reading it is the host side's work. With none set the answer is `503
//! pr_open_unavailable` before any window is raised; nothing falls back to `gh` or to a token
//! in the environment. Off unless `"pr_open": true` in `<data>/bridge.json`.
use std::sync::{Arc, OnceLock};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::api::AppState;
use crate::guard::request_id;
use crate::repo_push::{Refused, Target};

pub const OPEN: &str = "pr_open";
/// The operation the owner approves.
pub const OP: &str = "repo.pr_open";
/// Every pull request aibd opens goes into this branch.
pub const BASE: &str = "main";
/// ... and starts as a draft.
pub const DRAFT: bool = true;
pub const TITLE_MAX: usize = 200;
pub const BODY_MAX: usize = 16 * 1024;
/// The one host a pull request is opened on.
pub const GITHUB: &str = "github.com";
/// A title has none of these: it is one line.
const TITLE_BREAKS: [char; 3] = ['\n', '\r', '\t'];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Opened {
    pub number: u64,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PrError {
    /// The branch already has an open pull request (its number, when the API says).
    Exists(Option<u64>),
    /// Anything else, in the API's words (cleaned and cut before they are shown).
    Failed(String),
}

/// What opens the pull request. aibd's own client with the token in memory, when there is one.
pub trait PrApi: Send + Sync {
    /// Can a pull request be opened in `slug` at all (a token for that very repository)?
    /// Asked before any window is raised; reads no secret.
    fn ready(&self, _slug: &str) -> bool {
        true
    }
    fn open(
        &self,
        slug: &str,
        head: &str,
        base: &str,
        title: &str,
        body: &str,
        draft: bool,
    ) -> Result<Opened, PrError>;
}

static API: OnceLock<Arc<dyn PrApi>> = OnceLock::new();

/// Called once at start by the build that has a client (the first call wins). Never set from
/// a request or the environment.
pub fn set_api(api: Arc<dyn PrApi>) {
    let _ = API.set(api);
}

fn no(status: StatusCode, why: &'static str) -> Refused {
    Refused(status, why)
}

/// The GitHub repository a registered remote names: `owner/name` off `git@host:owner/name.git`,
/// `ssh://…/owner/name` or `https://host/owner/name.git`. Two parts, each of
/// `A-Z a-z 0-9 . _ -`, neither starting with `-` or `.`; anything else is none.
pub fn slug_of(remote: &str) -> Option<String> {
    // the pull request opens on GitHub: a remote anywhere else has no repository there to
    // open it in (5d33a REVIEW-pushpr-g23 L2: never a same-named repo on another host's behalf)
    if !crate::repos::remote_host(remote).eq_ignore_ascii_case(GITHUB) {
        return None;
    }
    let path = match remote.split_once("://") {
        Some((_, rest)) => rest.split_once('/')?.1,
        None => remote.split_once(':')?.1,
    };
    let path = path.trim_matches('/');
    let path = path.strip_suffix(".git").unwrap_or(path);
    crate::gh_view::repo_ok(path).then(|| path.to_owned())
}

/// What GitHub acts on in a pull request's words, longest shape first: a link (an issue's or
/// a pull request's address closes or cross-links as its number does), `owner/repo#12`,
/// `GH-12`, `@name`, `#12` (5d33a REVIEW-pushpr-g23 L1).
const ACTED_ON: &str = r"(?i)https?://[^\s`]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+|\bGH-[0-9]+|@[A-Za-z0-9][A-Za-z0-9/_-]*|#[0-9]+";

/// Words GitHub would act on, quoted so that it does not: `@name` (a notification to a real
/// person or team) and `#123` (a cross-link, and with `closes` / `fixes` a closed issue when
/// the pull request merges) are put in backticks (5d33a PREREAD-propen-g3 L2). A backtick in
/// the text is taken out first, so a quote cannot be closed from inside.
pub fn quoted(text: &str) -> String {
    static RE: OnceLock<Option<regex::Regex>> = OnceLock::new();
    let plain = text.replace('`', "'");
    match RE.get_or_init(|| regex::Regex::new(ACTED_ON).ok()) {
        Some(re) => re.replace_all(&plain, "`$0`").into_owned(),
        // the pattern is fixed; were it ever not to build, every mark is dropped instead
        None => plain.replace(['@', '#'], ""),
    }
}

/// The title and body as they are sent: cleaned as a letter's words are, the title one line of
/// 1–[`TITLE_MAX`] characters, the body at most [`BODY_MAX`] bytes, both [`quoted`].
pub fn words_of(title: &str, body: &str) -> Result<(String, String), &'static str> {
    if title.contains(TITLE_BREAKS) {
        return Err("pr_title");
    }
    let t = quoted(crate::dm_import::clean(title).trim());
    if t.is_empty() || t.chars().count() > TITLE_MAX {
        return Err("pr_title");
    }
    let b = quoted(&crate::dm_import::clean(body));
    if b.len() > BODY_MAX {
        return Err("pr_body");
    }
    Ok((t, b))
}

fn sha12(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    d.iter().take(6).map(|x| format!("{x:02x}")).collect()
}

/// What the owner is asked. The ticket binds the repository, the branch, its head and the very
/// words; the window says, in aibd's words, where the pull request opens and from what, and
/// shows the title's start last-but-first-cut (the window keeps a text's last 80).
pub fn open_ask(
    t: &Target,
    slug: &str,
    title: &str,
    body: &str,
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "repo": t.repo, "slug": slug, "branch": t.branch, "sha": t.sha,
        "base": BASE, "draft": DRAFT, "title": title, "body_sha256": sha12(body.as_bytes()),
    });
    let start: String = title.chars().take(16).collect();
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!(
            "「{start}」 草稿 PR {} {} → {slug} {BASE}",
            t.branch,
            &t.sha[..12.min(t.sha.len())],
        ),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// The call itself, with what aibd fixes: the base and the draft are not the caller's.
pub fn open(
    api: &dyn PrApi,
    slug: &str,
    t: &Target,
    title: &str,
    body: &str,
) -> Result<Opened, Refused> {
    match api.open(slug, &t.branch, BASE, title, body, DRAFT) {
        Ok(o) if o.number > 0 => Ok(o),
        Ok(_) => Err(no(StatusCode::BAD_GATEWAY, "pr_failed")),
        Err(PrError::Exists(_)) => Err(no(StatusCode::CONFLICT, "pr_exists")),
        Err(PrError::Failed(why)) => Err(match why.as_str() {
            crate::pr_api::REFUSED => no(StatusCode::BAD_GATEWAY, "pr_token_refused"),
            crate::pr_api::NO_ACCESS => no(StatusCode::BAD_GATEWAY, "pr_token_no_access"),
            crate::pr_api::NO_TOKEN => no(StatusCode::SERVICE_UNAVAILABLE, "pr_open_unavailable"),
            _ => no(StatusCode::BAD_GATEWAY, "pr_failed"),
        }),
    }
}

fn words(reason: &str) -> &'static str {
    match reason {
        "pr_open_off" => "opening pull requests is off on this machine (pr_open in bridge.json)",
        "pr_open_unavailable" => {
            "this machine has no way to open a pull request yet (no GitHub token)"
        }
        "pr_fields" => "body must be {type, repo, branch, title, body, ticket?}",
        "pr_title" => "title is one line of 1 to 200 characters",
        "pr_body" => "body is at most 16 KB",
        "pr_no_github_repo" => "the repo's registered remote is not github.com owner/name",
        "pr_changed" => {
            "the repo, its remote or the branch's head changed since the fingerprint: ask again"
        }
        "pr_exists" => "this branch already has an open pull request",
        "pr_token_refused" => "GitHub refused this machine's token (expired or revoked)",
        "pr_token_no_access" => "this machine's token does not reach that repository",
        _ => "the pull request was not opened",
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
    // a push's reasons (the same reading of the repo and branch) keep their own words
    let message = if r.1.starts_with("push_") {
        "the repo or the branch cannot be used (see reason)"
    } else {
        words(r.1)
    };
    (
        r.0,
        Json(json!({"code": code, "reason": r.1, "message": message,
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
    title: String,
    body: String,
    #[serde(default)]
    ticket: Option<String>,
}

/// `pr_open`, for the built-in page `by` (`page:<pair>`).
pub async fn act(st: AppState, body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Raw>(body) else {
        return refuse(&no(StatusCode::BAD_REQUEST, "pr_fields"));
    };
    let Some(data) = crate::bridge_cfg::dir().filter(|d| crate::bridge_cfg::load(d).pr_open) else {
        return refuse(&no(StatusCode::FORBIDDEN, "pr_open_off"));
    };
    let (title, text) = match words_of(&req.title, &req.body) {
        Ok(w) => w,
        Err(why) => return refuse(&no(StatusCode::BAD_REQUEST, why)),
    };
    // no way to open one: said before any window is raised
    let Some(api) = API.get().cloned() else {
        return refuse(&no(StatusCode::SERVICE_UNAVAILABLE, "pr_open_unavailable"));
    };
    let hooks = crate::seats::seat_git_hooks(st.sessions.private_dir());
    let read = {
        let (d, h, repo, branch) = (
            data.clone(),
            hooks.clone(),
            req.repo.clone(),
            req.branch.clone(),
        );
        tokio::task::spawn_blocking(move || {
            std::fs::create_dir_all(&h)
                .map_err(|_| no(StatusCode::INTERNAL_SERVER_ERROR, "pr_failed"))?;
            crate::repo_push::target(&d, &repo, &branch, &h)
        })
        .await
    };
    let t = match read {
        Ok(Ok(t)) => t,
        Ok(Err(r)) => return refuse(&r),
        Err(_) => return refuse(&no(StatusCode::INTERNAL_SERVER_ERROR, "pr_failed")),
    };
    let Some(slug) = slug_of(&t.remote) else {
        return refuse(&no(StatusCode::CONFLICT, "pr_no_github_repo"));
    };
    // no token for this very repository: said before any window is raised, as above, with the
    // repository's GitHub name so the page can offer to keep one (DESIGN-gh-token-card §1)
    if !api.ready(&slug) {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(
                json!({"code": "unavailable", "reason": "pr_open_unavailable",
                "message": words("pr_open_unavailable"), "token_for": slug,
                "request_id": request_id()}),
            ),
        )
            .into_response();
    }
    let ask = open_ask(&t, &slug, &title, &text, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let (approved, slug2, title2, text2) = (t.clone(), slug.clone(), title.clone(), text.clone());
    let trail_dir = data.clone();
    let done = tokio::task::spawn_blocking(move || {
        // read again under the call: the switch, the repo as registered, the branch's head
        if !crate::bridge_cfg::load(&data).pr_open {
            return Err(no(StatusCode::FORBIDDEN, "pr_open_off"));
        }
        let now = crate::repo_push::target(&data, &approved.repo, &approved.branch, &hooks)?;
        if now != approved {
            return Err(no(StatusCode::CONFLICT, "pr_changed"));
        }
        open(api.as_ref(), &slug2, &approved, &title2, &text2)
    })
    .await
    .unwrap_or(Err(no(StatusCode::INTERNAL_SERVER_ERROR, "pr_failed")));
    match done {
        Ok(o) => {
            crate::seats::side_event(
                &st,
                "repo",
                "repo.pr_opened",
                json!({"repo": t.repo, "branch": t.branch, "number": o.number, "by": by}),
            )
            .await;
            let n = Some(o.number);
            crate::sent_trail::step_done(&st, trail_dir, &t.repo, &t.branch, &t.sha, n).await;
            Json(
                json!({"done": true, "repo": t.repo, "branch": t.branch, "number": o.number,
                "url": crate::dm_import::clean(&o.url), "draft": DRAFT, "base": BASE}),
            )
            .into_response()
        }
        Err(r) => {
            let step = crate::sent_trail::STEP_PR;
            crate::sent_trail::step_failed(&st, trail_dir, &t.repo, &t.branch, step, r.1).await;
            refuse(&r)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    type Call = (String, String, String, String, String, bool);

    struct Stub(Mutex<Vec<Call>>, Result<Opened, PrError>);
    impl PrApi for Stub {
        fn open(
            &self,
            slug: &str,
            head: &str,
            base: &str,
            title: &str,
            body: &str,
            draft: bool,
        ) -> Result<Opened, PrError> {
            self.0.lock().unwrap().push((
                slug.into(),
                head.into(),
                base.into(),
                title.into(),
                body.into(),
                draft,
            ));
            self.1.clone()
        }
    }

    fn target() -> Target {
        Target {
            repo: "demo".into(),
            path: std::path::PathBuf::from("/x"),
            remote: "git@github.com:ez2app/demo.git".into(),
            branch: "web/ab12-1120-1".into(),
            sha: "0123456789abcdef0123456789abcdef01234567".into(),
        }
    }

    #[test]
    fn the_github_repository_is_read_off_the_registered_remote() {
        for (r, want) in [
            ("git@github.com:ez2app/ez2-aib.git", Some("ez2app/ez2-aib")),
            (
                "https://github.com/ez2app/ez2-aib.git",
                Some("ez2app/ez2-aib"),
            ),
            ("https://github.com/ez2app/ez2-aib", Some("ez2app/ez2-aib")),
            (
                "ssh://git@github.com:22/ez2app/ez2-aib.git",
                Some("ez2app/ez2-aib"),
            ),
            ("github.com:o/r", Some("o/r")),
            ("git@GitHub.com:o/r.git", Some("o/r")),
            ("git@gitlab.com:o/r.git", None),
            ("https://github.com.evil.example/o/r.git", None),
            ("https://example.com/github.com/o/r", None),
            ("ssh://git@git.example:22/o/r.git", None),
            ("git@github.com:o/r/extra.git", None),
            ("git@github.com:just-one", None),
            ("https://github.com/", None),
            ("git@github.com:-o/r", None),
            ("git@github.com:o/..", None),
            ("/Users/x/repo", None),
            ("", None),
        ] {
            assert_eq!(slug_of(r).as_deref(), want, "reason: slug_of_{r:?}");
        }
    }

    /// 5d33a PREREAD-propen-g3 L2: nothing in the words makes GitHub act.
    #[test]
    fn a_name_or_an_issue_number_in_the_words_is_quoted() {
        assert_eq!(
            quoted("thanks @andy and @org/team"),
            "thanks `@andy` and `@org/team`"
        );
        assert_eq!(quoted("closes #12, fixes #3"), "closes `#12`, fixes `#3`");
        assert_eq!(
            quoted("plain words, a@b.c mail"),
            "plain words, a`@b`.c mail"
        );
        // a backtick of the text's own cannot close the quote
        assert_eq!(quoted("`@andy` closes `#1`"), "'`@andy`' closes '`#1`'");
        assert_eq!(quoted("#x # 12 @ name"), "#x # 12 @ name");
        // 5d33a REVIEW-pushpr-g23 L1: the other shapes GitHub acts on
        assert_eq!(
            quoted("closes https://github.com/o/r/issues/12 now"),
            "closes `https://github.com/o/r/issues/12` now",
            "reason: issue_address_sent_as_written"
        );
        assert_eq!(
            quoted("see o-1/r.x#12 and GH-7, gh-8"),
            "see `o-1/r.x#12` and `GH-7`, `gh-8`"
        );
        assert_eq!(
            quoted("HTTP://EXAMPLE.COM/a?b#3"),
            "`HTTP://EXAMPLE.COM/a?b#3`"
        );
        assert_eq!(quoted("high-12 is not one"), "high-12 is not one");
        let (t, b) = words_of(
            "  fix: the gate @andy  ",
            "see #7\n\u{202e}hidden\u{200b} \u{1b}[31mred",
        )
        .unwrap();
        assert_eq!(t, "fix: the gate `@andy`");
        assert_eq!(b, "see `#7`\nhidden red", "reason: body_not_cleaned");
        for bad in ["", "   ", "two\nlines", "tab\there", "\u{200b}"] {
            assert_eq!(words_of(bad, "b"), Err("pr_title"), "reason: title_{bad:?}");
        }
        assert_eq!(words_of(&"x".repeat(TITLE_MAX + 1), "b"), Err("pr_title"));
        assert!(words_of(&"x".repeat(TITLE_MAX), "b").is_ok());
        assert_eq!(words_of("t", &"x".repeat(BODY_MAX + 1)), Err("pr_body"));
        assert!(words_of("t", &"x".repeat(BODY_MAX)).is_ok());
    }

    #[test]
    fn the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked() {
        let stub = Stub(
            Mutex::new(Vec::new()),
            Ok(Opened {
                number: 12,
                url: "https://github.com/ez2app/demo/pull/12".into(),
            }),
        );
        let t = target();
        let o = open(&stub, "ez2app/demo", &t, "a title", "a body").unwrap();
        assert_eq!(o.number, 12);
        let calls = stub.0.lock().unwrap().clone();
        assert_eq!(
            calls,
            [(
                "ez2app/demo".to_owned(),
                "web/ab12-1120-1".to_owned(),
                "main".to_owned(),
                "a title".to_owned(),
                "a body".to_owned(),
                true
            )],
            "reason: api_not_called_as_fixed"
        );
        // the API's answers
        let says = |r: Result<Opened, PrError>| {
            open(&Stub(Mutex::new(Vec::new()), r), "o/r", &t, "t", "b").map_err(|e| e.1)
        };
        assert_eq!(says(Err(PrError::Exists(Some(3)))), Err("pr_exists"));
        assert_eq!(says(Err(PrError::Failed("boom".into()))), Err("pr_failed"));
        // the real client's three words keep their own reasons (pr_api)
        for (why, reason) in [
            (crate::pr_api::REFUSED, "pr_token_refused"),
            (crate::pr_api::NO_ACCESS, "pr_token_no_access"),
            (crate::pr_api::NO_TOKEN, "pr_open_unavailable"),
            (crate::pr_api::OTHER, "pr_failed"),
        ] {
            assert_eq!(
                says(Err(PrError::Failed(why.into()))),
                Err(reason),
                "reason: failed_{why}"
            );
        }
        assert_eq!(
            says(Ok(Opened {
                number: 0,
                url: String::new()
            })),
            Err("pr_failed"),
            "reason: number_zero_taken"
        );
    }

    #[test]
    fn the_ticket_binds_the_words_the_branch_and_where_it_opens() {
        let t = target();
        let a = open_ask(&t, "ez2app/demo", "a title", "a body", "page:1".into());
        assert_eq!(a.operation, OP);
        let differs = |x: crate::owner_ok::Ask| x.params != a.params;
        assert!(
            differs(open_ask(
                &t,
                "ez2app/demo",
                "another",
                "a body",
                "page:1".into()
            )),
            "reason: title_not_bound"
        );
        assert!(
            differs(open_ask(
                &t,
                "ez2app/demo",
                "a title",
                "another",
                "page:1".into()
            )),
            "reason: body_not_bound"
        );
        assert!(
            differs(open_ask(
                &t,
                "evil/demo",
                "a title",
                "a body",
                "page:1".into()
            )),
            "reason: repository_not_bound"
        );
        let mut moved = t.clone();
        moved.sha = "1".repeat(40);
        assert!(
            differs(open_ask(
                &moved,
                "ez2app/demo",
                "a title",
                "a body",
                "page:1".into()
            )),
            "reason: head_not_bound"
        );
        let mut other = t.clone();
        other.branch = "web/ab12-1120-2".into();
        assert!(
            differs(open_ask(
                &other,
                "ez2app/demo",
                "a title",
                "a body",
                "page:1".into()
            )),
            "reason: branch_not_bound"
        );
        // the window: aibd's facts are what the last 80 keep, however long the title
        let long = open_ask(&t, "ez2app/demo", &"長".repeat(150), "b", "page:1".into());
        let shown = crate::owner_ok::clean_resource(&long.resource);
        assert!(
            shown.ends_with("草稿 PR web/ab12-1120-1 0123456789ab → ez2app/demo main"),
            "reason: facts_cut {shown}"
        );
    }
}
