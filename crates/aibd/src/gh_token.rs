//! `repo_gh_token` (DESIGN-gh-token-card-7b81e-1121 r1; 519d0 11-21 09:27, Andy 09:2x: what
//! asked him to open a terminal is a button on the page): the GitHub token a registered
//! repository is pushed and its pull requests are opened with, pasted on the built-in page and
//! kept by aibd in the login keychain under that repository's name
//! ([`crate::pr_api::item_of`]), with the owner's fingerprint.
//!
//! The first send is checked before any window is raised: GitHub is asked, with the very token,
//! whether it sees the repository and may push to it ([`crate::pr_api::can_push`]); a no is
//! said and nothing else happens. A yes opens a ticket whose parameters hold the token's
//! SHA-256, never the token; the page sends the same body again with the ticket and the token is
//! kept. The token travels only in this process's memory, as a [`Token`] (no `Debug`, zeroed
//! when dropped); it is in no answer, log line, ticket, window or event.
use std::sync::OnceLock;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::guard::request_id;
use crate::pr_api::{Secrets, Token, Wire};

/// The page's act.
pub const SET: &str = "repo_gh_token";
/// The owner check's operation (on [`crate::owner_ok::WIRED`]).
pub const OP: &str = "repo.gh_token_set";

/// Where tokens are kept and how GitHub is asked; set once at start where there is one.
pub struct Keeper {
    pub secrets: std::sync::Arc<dyn Secrets>,
    pub wire: std::sync::Arc<dyn Wire>,
}

static KEEPER: OnceLock<Keeper> = OnceLock::new();

/// Called once at start (the first call wins).
pub fn set_keeper(k: Keeper) {
    let _ = KEEPER.set(k);
}

type No = (StatusCode, &'static str);

/// Does GitHub let `token` push to `slug`? A no is the reason the page reads.
pub fn check(k: &Keeper, slug: &str, token: &Token) -> Result<(), No> {
    let (status, answer) = k
        .wire
        .send(&crate::pr_api::repo_request(slug), token)
        .map_err(|_| {
            (
                StatusCode::SERVICE_UNAVAILABLE,
                crate::pr_api::GH_UNREACHABLE,
            )
        })?;
    crate::pr_api::can_push(status, &answer).map_err(|why| {
        let status = if why == crate::pr_api::GH_UNREACHABLE {
            StatusCode::SERVICE_UNAVAILABLE
        } else {
            StatusCode::CONFLICT
        };
        (status, why)
    })
}

/// Keeps `token` as `slug`'s item.
pub fn keep(k: &Keeper, slug: &str, token: &Token) -> Result<(), No> {
    let Some((service, account)) = crate::pr_api::item_of(slug) else {
        return Err((StatusCode::CONFLICT, "pr_no_github_repo"));
    };
    k.secrets
        .set(&service, &account, token)
        .map_err(|_| (StatusCode::INTERNAL_SERVER_ERROR, "gh_token_store"))
}

/// The fingerprint this asks: the repository, its GitHub name and the token's hash.
pub fn ask_of(repo: &str, slug: &str, token: &Token, by: String) -> crate::owner_ok::Ask {
    let params = json!({
        "repo": repo, "slug": slug,
        "token_sha256": crate::owner_ok::params_sha256(token.expose()),
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!("{repo} · github.com/{slug}"),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

fn refuse((status, code): No) -> Response {
    (
        status,
        Json(json!({"code": code, "reason": code, "request_id": request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    repo: String,
    token: String,
    #[serde(default)]
    ticket: Option<String>,
}

/// `repo_gh_token`, for the built-in page `by` (`page:<pair>`).
pub async fn act(st: AppState, body: Value, by: String) -> Response {
    let Ok(mut req) = serde_json::from_value::<Raw>(body) else {
        return refuse((StatusCode::BAD_REQUEST, "gh_token_fields"));
    };
    // moved, not copied: the only copy is the Token, zeroed when dropped
    let Some(token) = Token::new(std::mem::take(&mut req.token).into_bytes()) else {
        return refuse((StatusCode::BAD_REQUEST, "gh_token_shape"));
    };
    let Some(k) = KEEPER.get() else {
        return refuse((StatusCode::SERVICE_UNAVAILABLE, "gh_token_unavailable"));
    };
    let Some(data) = crate::bridge_cfg::dir() else {
        return refuse((StatusCode::SERVICE_UNAVAILABLE, "gh_token_unavailable"));
    };
    let Some(r) = crate::repos::find(&data, &req.repo) else {
        return refuse((StatusCode::NOT_FOUND, "push_repo_unknown"));
    };
    let Some(slug) = crate::pr_open::slug_of(&r.remote) else {
        return refuse((StatusCode::CONFLICT, "pr_no_github_repo"));
    };
    let ask = ask_of(&r.name, &slug, &token, by.clone());
    let Some(ticket) = req.ticket else {
        // checked before any window: a token GitHub refuses raises none
        let s = slug.clone();
        let checked = tokio::task::spawn_blocking(move || check(k, &s, &token))
            .await
            .unwrap_or(Err((StatusCode::INTERNAL_SERVER_ERROR, "gh_token_store")));
        if let Err(no) = checked {
            return refuse(no);
        }
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let s = slug.clone();
    let kept = tokio::task::spawn_blocking(move || keep(k, &s, &token))
        .await
        .unwrap_or(Err((StatusCode::INTERNAL_SERVER_ERROR, "gh_token_store")));
    if let Err(no) = kept {
        return refuse(no);
    }
    crate::seats::side_event(
        &st,
        "repo",
        "repo.gh_token_set",
        json!({"repo": r.name, "slug": slug, "by": by}),
    )
    .await;
    Json(json!({"done": true, "stored": true, "repo": r.name, "slug": slug})).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pr_api::Request;
    use std::sync::{Arc, Mutex};

    const TOKEN: &[u8] = b"github_pat_TEST7b81e_abcdefXYZ";

    #[derive(Default)]
    struct Store(Mutex<Vec<(String, String, Vec<u8>)>>);
    impl Secrets for Store {
        fn has(&self, service: &str, account: &str) -> bool {
            let s = self.0.lock().unwrap();
            s.iter()
                .any(|(a, b, _)| (a.as_str(), b.as_str()) == (service, account))
        }
        fn get(&self, _: &str, _: &str) -> Option<Token> {
            None
        }
        fn set(&self, service: &str, account: &str, token: &Token) -> Result<(), &'static str> {
            let mut s = self.0.lock().unwrap();
            s.push((service.into(), account.into(), token.expose().to_vec()));
            Ok(())
        }
    }

    struct Answers(Mutex<Vec<Request>>, Result<(u16, Vec<u8>), &'static str>);
    impl Wire for Answers {
        fn send(&self, req: &Request, token: &Token) -> Result<(u16, Vec<u8>), &'static str> {
            assert_eq!(token.expose(), TOKEN, "reason: another_token_sent");
            self.0.lock().unwrap().push(req.clone());
            self.1.clone()
        }
    }

    fn keeper(answer: Result<(u16, Vec<u8>), &'static str>) -> (Keeper, Arc<Store>, Arc<Answers>) {
        let store = Arc::new(Store::default());
        let wire = Arc::new(Answers(Mutex::default(), answer));
        let k = Keeper {
            secrets: store.clone(),
            wire: wire.clone(),
        };
        (k, store, wire)
    }

    fn token() -> Token {
        Token::new(TOKEN.to_vec()).unwrap()
    }

    #[test]
    fn a_token_is_checked_with_github_first_and_kept_under_its_repository() {
        let yes = json!({"permissions": {"push": true}})
            .to_string()
            .into_bytes();
        let (k, store, wire) = keeper(Ok((200, yes)));
        assert_eq!(check(&k, "ez2app/demo", &token()), Ok(()));
        assert_eq!(
            wire.0.lock().unwrap()[0].url,
            "https://api.github.com/repos/ez2app/demo",
            "reason: checked_another_repo"
        );
        assert!(store.0.lock().unwrap().is_empty(), "reason: kept_by_check");
        assert_eq!(keep(&k, "Ez2App/Demo", &token()), Ok(()));
        let kept = store.0.lock().unwrap().clone();
        assert_eq!(
            kept,
            [(
                "ez2-aibd-github:ez2app/demo".to_owned(),
                "ez2app".to_owned(),
                TOKEN.to_vec()
            )],
            "reason: kept_elsewhere"
        );
        for (answer, want) in [
            (
                Ok((404, Vec::new())),
                (StatusCode::CONFLICT, crate::pr_api::GH_NOT_SELECTED),
            ),
            (
                Ok((401, Vec::new())),
                (StatusCode::CONFLICT, crate::pr_api::GH_REFUSED),
            ),
            (
                Ok((200, br#"{"permissions":{"push":false}}"#.to_vec())),
                (StatusCode::CONFLICT, crate::pr_api::GH_NO_WRITE),
            ),
            (
                Ok((503, Vec::new())),
                (
                    StatusCode::SERVICE_UNAVAILABLE,
                    crate::pr_api::GH_UNREACHABLE,
                ),
            ),
            (
                Err("curl"),
                (
                    StatusCode::SERVICE_UNAVAILABLE,
                    crate::pr_api::GH_UNREACHABLE,
                ),
            ),
        ] {
            let (k, _, _) = keeper(answer);
            assert_eq!(
                check(&k, "ez2app/demo", &token()),
                Err(want),
                "reason: {want:?}"
            );
        }
    }

    #[test]
    fn the_ticket_holds_the_tokens_hash_never_the_token() {
        let a = ask_of("demo", "ez2app/demo", &token(), "page:ab".into());
        assert_eq!(a.operation, OP);
        assert!(crate::owner_ok::WIRED.contains(&OP), "reason: not_wired");
        let shown = format!("{}{}", a.resource, String::from_utf8_lossy(&a.params));
        assert!(
            !shown.contains(std::str::from_utf8(TOKEN).unwrap()),
            "reason: token_in_ticket {shown}"
        );
        let v: Value = serde_json::from_slice(&a.params).unwrap();
        assert_eq!(
            v["token_sha256"],
            crate::owner_ok::params_sha256(TOKEN),
            "reason: hash_of_another"
        );
        // another token, another ask: a ticket for one never keeps the other
        let other = Token::new(b"github_pat_OTHER".to_vec()).unwrap();
        assert_ne!(
            ask_of("demo", "ez2app/demo", &other, "page:ab".into()).params,
            a.params,
            "reason: token_not_in_params"
        );
        assert_eq!(
            crate::owner_ok::plain_words(OP, &a.params).as_deref(),
            Some("把 GitHub 權杖存進這台,給 ez2app/demo 推分支與開 PR 用"),
            "reason: plain_words"
        );
    }
}
