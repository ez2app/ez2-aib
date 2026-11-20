//! The pull request call itself (DESIGN-pr-api-7b81e-1121; 519d0 11-21 00:38): what
//! [`crate::pr_open::PrApi`] is on a machine that has a token for the repository.
//!
//! This file is the part that does not depend on how the bytes travel: which keychain item a
//! repository's token is ([`item_of`]), the two requests ([`open_request`], [`find_request`]),
//! and what an answer means ([`opened`], [`found`]). Reading the item is behind [`Secrets`] and
//! sending is behind [`Wire`]; neither has a real one here yet (5d33a's pre-read decides the
//! wire), so nothing calls `pr_open::set_api` and every machine still answers
//! `503 pr_open_unavailable`.
//!
//! What holds whatever the wire is: the token is read after the owner's fingerprint, for one
//! call, and zeroed when that call ends; it has no `Debug`, `Display` or `Serialize`; it is
//! looked up under the name of the very repository the request goes to; the address is put
//! together here, the base and the draft are `pr_open`'s; nothing GitHub answers is passed on
//! as text, and the pull request's address is built from its number, not taken from the answer.
use std::sync::Arc;

use serde_json::{json, Value};

use crate::pr_open::{Opened, PrApi, PrError};

/// Every request goes here and nowhere else.
pub const API: &str = "https://api.github.com";
/// A repository's token is the keychain item with this service name followed by the
/// repository's name, and its owner as the account (STEPS-github-token r1).
pub const SERVICE: &str = "ez2-aibd-github-";
pub const TOKEN_MAX: usize = 255;
/// [`PrError::Failed`]'s words: GitHub refused the token (expired or revoked).
pub const REFUSED: &str = "token";
/// ... the token does not reach this repository.
pub const NO_ACCESS: &str = "access";
/// ... this machine has no token for the repository.
pub const NO_TOKEN: &str = "no_token";
/// ... anything else.
pub const OTHER: &str = "answer";

/// The token, for one call. It cannot be printed and is zeroed when dropped.
pub struct Token(Vec<u8>);

impl Token {
    /// 1 to [`TOKEN_MAX`] visible ASCII bytes with no quote and no backslash: nothing that
    /// could end a header line or a quoted word wherever the wire writes it.
    pub fn new(bytes: Vec<u8>) -> Option<Self> {
        let t = Self(bytes);
        let ok = (1..=TOKEN_MAX).contains(&t.0.len())
            && t.0
                .iter()
                .all(|&b| b.is_ascii_graphic() && !matches!(b, b'"' | b'\'' | b'\\'));
        ok.then_some(t)
    }

    /// The bytes, for the wire only.
    pub fn expose(&self) -> &[u8] {
        &self.0
    }
}

impl Drop for Token {
    fn drop(&mut self) {
        for b in &mut self.0 {
            *b = 0;
        }
        std::hint::black_box(&self.0);
    }
}

/// Where tokens are kept. `has` reads no value and raises no window.
pub trait Secrets: Send + Sync {
    fn has(&self, service: &str, account: &str) -> bool;
    fn get(&self, service: &str, account: &str) -> Option<Token>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub method: &'static str,
    pub url: String,
    pub body: Option<String>,
}

/// One request out, its status and answer back. No redirect is followed and nothing but
/// https is spoken; an error is one fixed word.
pub trait Wire: Send + Sync {
    fn send(&self, req: &Request, token: &Token) -> Result<(u16, Vec<u8>), &'static str>;
}

/// The keychain item of `owner/name`: (service, account).
pub fn item_of(slug: &str) -> Option<(String, String)> {
    if !crate::gh_view::repo_ok(slug) {
        return None;
    }
    let (owner, name) = slug.split_once('/')?;
    Some((format!("{SERVICE}{name}"), owner.to_owned()))
}

/// `text` as one piece of an address: everything but `A-Z a-z 0-9 - . _ ~` is `%XX`.
pub fn pct(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// GitHub's "create a pull request".
pub fn open_request(
    slug: &str,
    head: &str,
    base: &str,
    title: &str,
    body: &str,
    draft: bool,
) -> Request {
    Request {
        method: "POST",
        url: format!("{API}/repos/{slug}/pulls"),
        body: Some(
            json!({"title": title, "head": head, "base": base, "body": body, "draft": draft})
                .to_string(),
        ),
    }
}

/// The open pull requests of one branch of the repository itself.
pub fn find_request(slug: &str, head: &str) -> Request {
    let owner = slug.split_once('/').map_or("", |(o, _)| o);
    Request {
        method: "GET",
        url: format!(
            "{API}/repos/{slug}/pulls?state=open&head={}",
            pct(&format!("{owner}:{head}"))
        ),
        body: None,
    }
}

/// What the answer to [`open_request`] means. Only the number is taken from it.
pub fn opened(slug: &str, status: u16, answer: &[u8]) -> Result<Opened, PrError> {
    let failed = |why: &str| Err(PrError::Failed(why.to_owned()));
    match status {
        201 => {
            let number = serde_json::from_slice::<Value>(answer)
                .ok()
                .and_then(|v| v["number"].as_u64())
                .filter(|n| *n > 0);
            match number {
                Some(number) => Ok(Opened {
                    number,
                    url: format!("https://github.com/{slug}/pull/{number}"),
                }),
                None => failed(OTHER),
            }
        }
        422 if exists(answer) => Err(PrError::Exists(None)),
        401 => failed(REFUSED),
        403 | 404 => failed(NO_ACCESS),
        _ => failed(OTHER),
    }
}

/// GitHub's 422 for a branch that has an open pull request names it in an error's message.
fn exists(answer: &[u8]) -> bool {
    serde_json::from_slice::<Value>(answer)
        .ok()
        .and_then(|v| {
            v["errors"].as_array().map(|list| {
                list.iter().any(|e| {
                    e["message"]
                        .as_str()
                        .is_some_and(|m| m.contains("pull request already exists"))
                })
            })
        })
        .unwrap_or(false)
}

/// The number in the answer to [`find_request`]: the first one listed.
pub fn found(answer: &[u8]) -> Option<u64> {
    let list = serde_json::from_slice::<Value>(answer).ok()?;
    let number = list.as_array()?.first()?["number"].as_u64();
    number.filter(|n| *n > 0)
}

/// [`PrApi`] with a token store and a wire.
pub struct GitHub {
    pub secrets: Arc<dyn Secrets>,
    pub wire: Arc<dyn Wire>,
}

impl PrApi for GitHub {
    fn ready(&self, slug: &str) -> bool {
        item_of(slug).is_some_and(|(service, account)| self.secrets.has(&service, &account))
    }

    fn open(
        &self,
        slug: &str,
        head: &str,
        base: &str,
        title: &str,
        body: &str,
        draft: bool,
    ) -> Result<Opened, PrError> {
        let failed = |why: &str| Err(PrError::Failed(why.to_owned()));
        let Some((service, account)) = item_of(slug) else {
            return failed(NO_TOKEN);
        };
        let Some(token) = self.secrets.get(&service, &account) else {
            return failed(NO_TOKEN);
        };
        let req = open_request(slug, head, base, title, body, draft);
        let (status, answer) = match self.wire.send(&req, &token) {
            Ok(got) => got,
            Err(_) => return failed(OTHER),
        };
        match opened(slug, status, &answer) {
            Err(PrError::Exists(None)) => {
                let number = match self.wire.send(&find_request(slug, head), &token) {
                    Ok((200, list)) => found(&list),
                    _ => None,
                };
                Err(PrError::Exists(number))
            }
            other => other,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// A store with one item; counts the reads of its value.
    struct Store(&'static str, &'static str, Mutex<usize>);
    impl Secrets for Store {
        fn has(&self, service: &str, account: &str) -> bool {
            (service, account) == (self.0, self.1)
        }
        fn get(&self, service: &str, account: &str) -> Option<Token> {
            *self.2.lock().unwrap() += 1;
            self.has(service, account)
                .then(|| Token::new(b"tok-1".to_vec()).unwrap())
        }
    }

    type Sent = (Request, Vec<u8>);
    type Answer = Result<(u16, Vec<u8>), &'static str>;

    /// Answers in order; keeps every request and the token it came with.
    struct Fake(Mutex<Vec<Sent>>, Mutex<Vec<Answer>>);
    impl Wire for Fake {
        fn send(&self, req: &Request, token: &Token) -> Answer {
            self.0
                .lock()
                .unwrap()
                .push((req.clone(), token.expose().to_vec()));
            self.1.lock().unwrap().remove(0)
        }
    }

    fn github(answers: Vec<Answer>) -> (GitHub, Arc<Fake>, Arc<Store>) {
        let store = Arc::new(Store("ez2-aibd-github-demo", "ez2app", Mutex::new(0)));
        let wire = Arc::new(Fake(Mutex::new(Vec::new()), Mutex::new(answers)));
        (
            GitHub {
                secrets: store.clone(),
                wire: wire.clone(),
            },
            wire,
            store,
        )
    }

    fn ok(status: u16, v: &Value) -> Answer {
        Ok((status, v.to_string().into_bytes()))
    }

    #[test]
    fn the_request_is_githubs_create_call_with_what_it_was_given() {
        let (g, wire, _) = github(vec![ok(
            201,
            &json!({"number": 12,
            "html_url": "https://evil.example/pull/12"}),
        )]);
        let o = g
            .open(
                "ez2app/demo",
                "web/ab12-1120-1",
                "main",
                "a \"title\"",
                "b\\ody\n2",
                true,
            )
            .unwrap();
        assert_eq!(o.number, 12);
        assert_eq!(
            o.url, "https://github.com/ez2app/demo/pull/12",
            "reason: address_taken_from_the_answer"
        );
        let sent = wire.0.lock().unwrap();
        assert_eq!(sent.len(), 1);
        let (req, token) = &sent[0];
        assert_eq!(req.method, "POST");
        assert_eq!(req.url, "https://api.github.com/repos/ez2app/demo/pulls");
        assert_eq!(token, b"tok-1", "reason: token_not_given_to_the_wire");
        let body: Value = serde_json::from_str(req.body.as_deref().unwrap()).unwrap();
        assert_eq!(
            body,
            json!({"title": "a \"title\"", "head": "web/ab12-1120-1", "base": "main",
                   "body": "b\\ody\n2", "draft": true}),
            "reason: body_not_as_given"
        );
    }

    #[test]
    fn a_token_is_the_item_named_after_the_repository_and_read_once_per_call() {
        assert_eq!(
            item_of("ez2app/ez2-aib"),
            Some(("ez2-aibd-github-ez2-aib".to_owned(), "ez2app".to_owned()))
        );
        for bad in ["", "one", "a/b/c", "-o/r", "o/..", "o /r", "o/r\n"] {
            assert_eq!(item_of(bad), None, "reason: item_of_{bad:?}");
        }
        let (g, wire, store) = github(vec![ok(201, &json!({"number": 3}))]);
        assert!(g.ready("ez2app/demo"));
        // another repository, another owner of the same name: no item, so not ready
        assert!(!g.ready("ez2app/other"), "reason: ready_for_any_repo");
        assert!(!g.ready("someone/demo"), "reason: ready_for_any_owner");
        assert_eq!(*store.2.lock().unwrap(), 0, "reason: ready_read_the_value");
        // ... and nothing is sent for one
        assert_eq!(
            g.open("ez2app/other", "web/x", "main", "t", "b", true),
            Err(PrError::Failed(NO_TOKEN.to_owned()))
        );
        assert!(
            wire.0.lock().unwrap().is_empty(),
            "reason: sent_without_a_token"
        );
        g.open("ez2app/demo", "web/x", "main", "t", "b", true)
            .unwrap();
        assert_eq!(*store.2.lock().unwrap(), 2, "reason: token_reads");
    }

    #[test]
    fn each_answer_reads_as_what_it_is() {
        let says = |status: u16, v: Value| opened("o/r", status, v.to_string().as_bytes());
        let failed = |why: &str| Err(PrError::Failed(why.to_owned()));
        assert_eq!(
            says(201, json!({"number": 7})),
            Ok(Opened {
                number: 7,
                url: "https://github.com/o/r/pull/7".into()
            })
        );
        for v in [
            json!({}),
            json!({"number": 0}),
            json!({"number": "7"}),
            json!([]),
        ] {
            assert_eq!(says(201, v.clone()), failed(OTHER), "reason: number_{v}");
        }
        assert_eq!(opened("o/r", 201, b"not json"), failed(OTHER));
        // only 201 is an opened pull request
        assert_eq!(
            says(200, json!({"number": 7})),
            failed(OTHER),
            "reason: not_created_read_as_opened"
        );
        assert_eq!(says(401, json!({"number": 7})), failed(REFUSED));
        assert_eq!(says(403, json!({})), failed(NO_ACCESS));
        assert_eq!(says(404, json!({})), failed(NO_ACCESS));
        assert_eq!(says(500, json!({})), failed(OTHER));
        let dup = json!({"message": "Validation Failed", "errors": [
            {"resource": "PullRequest", "code": "custom",
             "message": "A pull request already exists for o:web/x."}]});
        assert_eq!(says(422, dup.clone()), Err(PrError::Exists(None)));
        // another 422 (no commits between the two, a bad base) is not "exists"
        assert_eq!(
            says(
                422,
                json!({"message": "Validation Failed", "errors": [
                {"message": "No commits between main and web/x"}]})
            ),
            failed(OTHER),
            "reason: any_422_is_exists"
        );
        assert_eq!(says(201, dup), failed(OTHER));
    }

    #[test]
    fn a_branch_that_has_one_is_answered_with_its_number() {
        let dup =
            json!({"errors": [{"message": "A pull request already exists for ez2app:web/x."}]});
        let (g, wire, _) = github(vec![
            ok(422, &dup),
            ok(200, &json!([{"number": 41}, {"number": 9}])),
        ]);
        assert_eq!(
            g.open("ez2app/demo", "web/a b", "main", "t", "b", true),
            Err(PrError::Exists(Some(41)))
        );
        let sent = wire.0.lock().unwrap();
        assert_eq!(sent.len(), 2);
        assert_eq!(sent[1].0.method, "GET");
        assert_eq!(
            sent[1].0.url,
            "https://api.github.com/repos/ez2app/demo/pulls?state=open&head=ez2app%3Aweb%2Fa%20b",
            "reason: find_address"
        );
        assert_eq!(sent[1].0.body, None);
        drop(sent);
        // the list not read: still "exists", with no number
        for second in [
            ok(500, &json!([{"number": 5}])),
            ok(200, &json!([])),
            Err("timeout"),
        ] {
            let (g, _, _) = github(vec![ok(422, &dup), second]);
            assert_eq!(
                g.open("ez2app/demo", "web/x", "main", "t", "b", true),
                Err(PrError::Exists(None))
            );
        }
        // the wire's own failure is one fixed word
        let (g, _, _) = github(vec![Err("timeout")]);
        assert_eq!(
            g.open("ez2app/demo", "web/x", "main", "t", "b", true),
            Err(PrError::Failed(OTHER.to_owned()))
        );
    }

    #[test]
    fn a_token_is_one_plain_word() {
        assert!(Token::new(b"github_pat_11ABCDEFG0abc_defGHI".to_vec()).is_some());
        assert!(Token::new(vec![b'a'; TOKEN_MAX]).is_some());
        for bad in [
            &b""[..],
            b"two words",
            b"line\nbreak",
            b"cr\rhere",
            b"quo\"te",
            b"quo'te",
            b"back\\slash",
            b"nul\0",
            "caf\u{e9}".as_bytes(),
        ] {
            assert!(Token::new(bad.to_vec()).is_none(), "reason: token_{bad:?}");
        }
        assert!(Token::new(vec![b'a'; TOKEN_MAX + 1]).is_none());
    }
}
