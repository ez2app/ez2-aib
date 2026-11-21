//! The pull request call itself (DESIGN-pr-api-7b81e-1121; 519d0 11-21 00:38): what
//! [`crate::pr_open::PrApi`] is on a machine that has a token for the repository.
//!
//! This file is the part that does not depend on how the bytes travel: which keychain item a
//! repository's token is ([`item_of`]), the two requests ([`open_request`], [`find_request`]),
//! and what an answer means ([`opened`], [`found`]). Sending is behind [`Wire`]: [`Curl`], the
//! system's curl with a fixed argument list, an empty environment and everything else (the
//! address, the headers with the token, the body) on its standard input (5d33a
//! PREREAD-prapi-7b81e 1). Reading the item is behind [`Secrets`]: [`Keychain`] on macOS. The
//! server sets [`platform_api`] once at start; the switch (`pr_open` in bridge.json) stays off
//! by default, and a repository with no item answers `503 pr_open_unavailable` as before.
//!
//! What holds whatever the wire is: the token is read after the owner's fingerprint, for one
//! call, and zeroed when that call ends; it has no `Debug`, `Display` or `Serialize`; it is
//! looked up under the name of the very repository the request goes to; the address is put
//! together here, the base and the draft are `pr_open`'s; nothing GitHub answers is passed on
//! as text, and the pull request's address is built from its number, not taken from the answer.
use std::io::Write as _;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Arc;

use serde_json::{json, Value};

use crate::pr_open::{Opened, PrApi, PrError};

/// Every request goes here and nowhere else.
pub const API: &str = "https://api.github.com";
/// A repository's token is the keychain item with this service name followed by
/// `<owner>/<name>`, and its owner as the account (5d33a PREREAD-prapi-7b81e 4: two owners'
/// repositories of one name never share a token; PREREAD-ghtokencard L2: `:` and `/`, which
/// no owner or name holds, so `a-b/c` and `a/b-c` are two items).
pub const SERVICE: &str = "ez2-aibd-github:";
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
    /// 1 to [`TOKEN_MAX`] of `A-Z a-z 0-9 _`, the letters GitHub's tokens are made of (5d33a
    /// REVIEW-prapi-touchwords N1): a value with a quote, a line break or anything else that
    /// could end a header or add a line to curl's config is no token.
    pub fn new(bytes: Vec<u8>) -> Option<Self> {
        let t = Self(bytes);
        let ok = (1..=TOKEN_MAX).contains(&t.0.len())
            && t.0.iter().all(|&b| b.is_ascii_alphanumeric() || b == b'_');
        ok.then_some(t)
    }

    /// The bytes, for the wire only.
    pub fn expose(&self) -> &[u8] {
        &self.0
    }
}

impl Drop for Token {
    fn drop(&mut self) {
        self.0.fill(0);
        std::hint::black_box(&self.0);
    }
}

/// Where tokens are kept. `has` reads no value and raises no window.
pub trait Secrets: Send + Sync {
    fn has(&self, service: &str, account: &str) -> bool;
    fn get(&self, service: &str, account: &str) -> Option<Token>;
    /// Keeps `token` as the item, replacing one already there (DESIGN-gh-token-card 甲).
    fn set(&self, service: &str, account: &str, token: &Token) -> Result<(), &'static str>;
}

/// What GitHub says of a repository, read with the token about to be kept: it must see the
/// repository and may push to it (DESIGN-gh-token-card §3). Only `permissions.push` is read.
pub fn repo_request(slug: &str) -> Request {
    Request {
        method: "GET",
        url: format!("{API}/repos/{slug}"),
        body: None,
    }
}

/// The token reaches the repository and may push: none; otherwise why, as the page reads it.
pub fn can_push(status: u16, answer: &[u8]) -> Result<(), &'static str> {
    match status {
        200 => {
            let v: Value = serde_json::from_slice(answer).unwrap_or(Value::Null);
            if v["permissions"]["push"] == Value::Bool(true) {
                Ok(())
            } else {
                Err(GH_NO_WRITE)
            }
        }
        401 => Err(GH_REFUSED),
        403 | 404 => Err(GH_NOT_SELECTED),
        _ => Err(GH_UNREACHABLE),
    }
}

/// [`can_push`]'s words. GitHub refused the token (mistyped, expired, revoked).
pub const GH_REFUSED: &str = "gh_token_refused";
/// ... the token does not see the repository (not picked under "Repository access").
pub const GH_NOT_SELECTED: &str = "gh_token_repo_not_selected";
/// ... sees it but may not write to it.
pub const GH_NO_WRITE: &str = "gh_token_no_write";
/// ... GitHub could not be asked (no network, a timeout, a 5xx): not a no.
pub const GH_UNREACHABLE: &str = "gh_token_check_failed";

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
    // GitHub reads `Owner/Name` and `owner/name` as one repository; the keychain does not
    let lower = slug.to_ascii_lowercase();
    let (owner, name) = lower.split_once('/')?;
    Some((format!("{SERVICE}{owner}/{name}"), owner.to_owned()))
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

/// [`Secrets`] in the login keychain, read by this very process (never through
/// `/usr/bin/security`: the item's access list would then name that program, not aibd).
#[cfg(target_os = "macos")]
pub struct Keychain;

#[cfg(target_os = "macos")]
impl Secrets for Keychain {
    fn has(&self, service: &str, account: &str) -> bool {
        aib_sys::presence_macos::password_there(service, account) == Ok(true)
    }
    fn get(&self, service: &str, account: &str) -> Option<Token> {
        Token::new(aib_sys::presence_macos::password(service, account).ok()??)
    }
    fn set(&self, service: &str, account: &str, token: &Token) -> Result<(), &'static str> {
        aib_sys::presence_macos::password_store(service, account, token.expose())
            .map_err(|_| "gh_token_store")
    }
}

/// What this build opens pull requests with: the keychain and the system's curl on macOS,
/// nothing elsewhere (Windows has no "this program only" for a stored secret; today it keeps
/// answering `pr_open_unavailable`).
pub fn platform_api() -> Option<Arc<dyn PrApi>> {
    #[cfg(target_os = "macos")]
    {
        Some(Arc::new(GitHub {
            secrets: Arc::new(Keychain),
            wire: Arc::new(Curl(PathBuf::from(CURL))),
        }))
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

/// What a pasted token is checked and kept with ([`crate::gh_token`]): the keychain and the
/// system's curl on macOS, nothing elsewhere (DESIGN-gh-token-card 5: Windows says why and
/// shows no field until its owner fingerprint is wired).
pub fn platform_keeper() -> Option<crate::gh_token::Keeper> {
    #[cfg(target_os = "macos")]
    {
        Some(crate::gh_token::Keeper {
            secrets: Arc::new(Keychain),
            wire: Arc::new(Curl(PathBuf::from(CURL))),
        })
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

/// The system's curl.
pub const CURL: &str = "/usr/bin/curl";
/// Its whole argument list, always: no `.curlrc`, the rest read from standard input, https
/// only, no redirect, 20 seconds, and the status on a last line of its own.
pub const CURL_ARGS: [&str; 15] = [
    "-q",
    "--config",
    "-",
    "--silent",
    "--show-error",
    "--proto",
    "=https",
    "--max-redirs",
    "0",
    "--max-time",
    "20",
    "--max-filesize",
    "262144",
    "--write-out",
    "\n%{http_code}",
];
/// An answer longer than this is not read.
pub const ANSWER_MAX: usize = 256 * 1024;

/// `text` as a quoted word of a curl config: only `\` and `"` mean something inside one
/// that has no control character (a JSON text and an address have none).
pub fn quoted(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        if matches!(c, '\\' | '"') {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

/// What curl reads on its standard input for one request.
pub fn config(req: &Request, token: &Token) -> Vec<u8> {
    let mut out = Vec::new();
    let mut line = |key: &str, value: &str| {
        out.extend_from_slice(format!("{key} = {}\n", quoted(value)).as_bytes());
    };
    line("url", &req.url);
    line("request", req.method);
    line("header", "Accept: application/vnd.github+json");
    line("header", "X-GitHub-Api-Version: 2022-11-28");
    line("header", "User-Agent: aibd");
    if let Some(body) = &req.body {
        line("header", "Content-Type: application/json");
        line("data", body);
    }
    // the token's own line is put together as bytes: it never becomes a String
    out.extend_from_slice(b"header = \"Authorization: Bearer ");
    out.extend_from_slice(token.expose());
    out.extend_from_slice(b"\"\n");
    out
}

/// curl's output: the answer, then the status on the last line.
pub fn answer(out: &[u8]) -> Result<(u16, Vec<u8>), &'static str> {
    let cut = out.iter().rposition(|b| *b == b'\n').ok_or("answer")?;
    let status = std::str::from_utf8(&out[cut + 1..])
        .ok()
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or("answer")?;
    if cut > ANSWER_MAX {
        return Err("answer");
    }
    Ok((status, out[..cut].to_vec()))
}

/// [`Wire`] through a curl program ([`CURL`] outside tests).
pub struct Curl(pub PathBuf);

impl Wire for Curl {
    fn send(&self, req: &Request, token: &Token) -> Result<(u16, Vec<u8>), &'static str> {
        let mut child = Command::new(&self.0)
            .args(CURL_ARGS)
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "spawn")?;
        let mut cfg = config(req, token);
        // written whole, then closed (the handle is dropped with the closure)
        let wrote = child.stdin.take().map(|mut i| i.write_all(&cfg));
        cfg.fill(0);
        let out = child.wait_with_output().map_err(|_| "wait")?;
        if !matches!(wrote, Some(Ok(()))) {
            return Err("write");
        }
        if !out.status.success() {
            return Err("curl");
        }
        answer(&out.stdout)
    }
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
                .then(|| Token::new(b"tok_1".to_vec()).unwrap())
        }
        fn set(&self, _: &str, _: &str, _: &Token) -> Result<(), &'static str> {
            Err("read_only")
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
        let store = Arc::new(Store(
            "ez2-aibd-github:ez2app/demo",
            "ez2app",
            Mutex::new(0),
        ));
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
        assert_eq!(token, b"tok_1", "reason: token_not_given_to_the_wire");
        let body: Value = serde_json::from_str(req.body.as_deref().unwrap()).unwrap();
        assert_eq!(
            body,
            json!({"title": "a \"title\"", "head": "web/ab12-1120-1", "base": "main",
                   "body": "b\\ody\n2", "draft": true}),
            "reason: body_not_as_given"
        );
    }

    #[test]
    fn a_token_is_kept_only_when_github_says_it_may_push_to_that_repository() {
        assert_eq!(
            repo_request("ez2app/ez2-aib"),
            Request {
                method: "GET",
                url: "https://api.github.com/repos/ez2app/ez2-aib".into(),
                body: None
            }
        );
        let yes = json!({"permissions": {"push": true, "pull": true}}).to_string();
        assert_eq!(can_push(200, yes.as_bytes()), Ok(()));
        for (status, answer, want) in [
            (
                200,
                r#"{"permissions":{"push":false,"pull":true}}"#,
                GH_NO_WRITE,
            ),
            (200, r#"{"permissions":{"push":"true"}}"#, GH_NO_WRITE),
            (200, "not json", GH_NO_WRITE),
            (401, "", GH_REFUSED),
            (403, "", GH_NOT_SELECTED),
            (404, "", GH_NOT_SELECTED),
            (301, "", GH_UNREACHABLE),
            (502, "", GH_UNREACHABLE),
        ] {
            assert_eq!(
                can_push(status, answer.as_bytes()),
                Err(want),
                "reason: can_push_{status}_{answer}"
            );
        }
    }

    #[test]
    fn a_token_is_the_item_named_after_the_repository_and_read_once_per_call() {
        assert_eq!(
            item_of("ez2app/ez2-aib"),
            Some((
                "ez2-aibd-github:ez2app/ez2-aib".to_owned(),
                "ez2app".to_owned()
            ))
        );
        assert_eq!(
            item_of("Ez2App/Ez2_AIB.x"),
            Some((
                "ez2-aibd-github:ez2app/ez2_aib.x".to_owned(),
                "ez2app".to_owned()
            )),
            "reason: item_name_follows_the_remotes_case"
        );
        // a dash joins nothing: a-b/c and a/b-c are two repositories, two items (5d33a L2)
        assert_ne!(
            item_of("a-b/c").map(|i| i.0),
            item_of("a/b-c").map(|i| i.0),
            "reason: items_collide"
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
    fn a_config_word_keeps_hostile_text_as_it_is() {
        assert_eq!(quoted("plain"), "\"plain\"");
        assert_eq!(quoted("a \"b\" c\\d"), "\"a \\\"b\\\" c\\\\d\"");
        // a JSON text: its own escapes (\n, \t, \u0000) are backslashes, doubled here
        let body =
            json!({"title": "q\" b\\ \u{e9}\u{4e2d}", "body": "l1\nl2\t\r\u{b}\u{0}"}).to_string();
        let word = quoted(&body);
        assert!(!word.contains('\n'), "reason: raw_line_break_in_config");
        // undo what curl undoes: \\ and \" only (the text holds no other escape curl knows,
        // because every backslash in it was doubled)
        let inner = &word[1..word.len() - 1];
        let mut back = String::new();
        let mut it = inner.chars();
        while let Some(c) = it.next() {
            if c == '\\' {
                back.push(it.next().unwrap());
            } else {
                assert_ne!(c, '"', "reason: bare_quote_in_config");
                back.push(c);
            }
        }
        assert_eq!(back, body, "reason: config_word_changed_the_text");
        let req = open_request("o/r", "web/x", "main", "t \"q\"", "b", true);
        let cfg = String::from_utf8(config(&req, &Token::new(b"tok_9".to_vec()).unwrap())).unwrap();
        let lines: Vec<&str> = cfg.lines().collect();
        assert_eq!(lines[0], "url = \"https://api.github.com/repos/o/r/pulls\"");
        assert_eq!(lines[1], "request = \"POST\"");
        assert_eq!(
            lines.last().copied(),
            Some("header = \"Authorization: Bearer tok_9\""),
            "reason: token_line"
        );
        assert_eq!(lines.len(), 8, "reason: config_lines {cfg}");
        assert!(lines[6].starts_with("data = \"{"), "{cfg}");
        // a GET has no body line and no content type
        let get = String::from_utf8(config(
            &find_request("o/r", "web/x"),
            &Token::new(b"tok_9".to_vec()).unwrap(),
        ))
        .unwrap();
        assert_eq!(get.lines().count(), 6, "{get}");
        assert!(!get.contains("data = "), "{get}");
    }

    #[test]
    fn curls_output_is_the_answer_and_a_last_line_of_status() {
        assert_eq!(
            answer(b"{\"number\":5}\n201"),
            Ok((201, b"{\"number\":5}".to_vec()))
        );
        assert_eq!(answer(b"\n404"), Ok((404, Vec::new())));
        assert_eq!(answer(b"a\nb\n422"), Ok((422, b"a\nb".to_vec())));
        for bad in [&b""[..], b"201", b"x\n", b"x\nabc", b"x\n99999", b"x\n-1"] {
            assert_eq!(answer(bad), Err("answer"), "reason: answer_{bad:?}");
        }
        let mut long = vec![b'x'; ANSWER_MAX + 1];
        long.extend_from_slice(b"\n200");
        assert_eq!(answer(&long), Err("answer"), "reason: long_answer_read");
        let mut most = vec![b'x'; ANSWER_MAX];
        most.extend_from_slice(b"\n200");
        assert!(answer(&most).is_ok());
    }

    /// 5d33a PREREAD-prapi-7b81e 1: `-q` first, the whole list fixed, an empty environment,
    /// the token on standard input only.
    #[cfg(unix)]
    #[test]
    fn curl_gets_a_fixed_argument_list_an_empty_environment_and_the_token_on_stdin() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let fake = dir.path().join("curl");
        std::fs::write(
            &fake,
            "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > \"$0.argv\"\n\
             /usr/bin/env > \"$0.env\"\n/bin/cat > \"$0.stdin\"\nprintf '{\"number\":5}\\n201'\n",
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::env::set_var("AIBD_PR_API_TEST_LEAK", "1");
        let token = Token::new(b"tok_secret_7".to_vec()).unwrap();
        let req = open_request("o/r", "web/x", "main", "t", "b", true);
        let got = Curl(fake.clone()).send(&req, &token);
        assert_eq!(got, Ok((201, b"{\"number\":5}".to_vec())));
        let read =
            |ext: &str| std::fs::read_to_string(format!("{}.{ext}", fake.display())).unwrap();
        let argv = read("argv");
        assert_eq!(
            argv.lines().collect::<Vec<_>>(),
            [
                "-q",
                "--config",
                "-",
                "--silent",
                "--show-error",
                "--proto",
                "=https",
                "--max-redirs",
                "0",
                "--max-time",
                "20",
                "--max-filesize",
                "262144",
                "--write-out",
                "",
                "%{http_code}",
            ],
            "reason: curl_arguments"
        );
        assert!(!argv.contains("tok_secret_7"), "reason: token_in_arguments");
        let env = read("env");
        assert!(
            !env.contains("AIBD_PR_API_TEST_LEAK") && !env.contains("HOME="),
            "reason: environment_not_empty {env}"
        );
        assert_eq!(
            read("stdin").into_bytes(),
            config(&req, &token),
            "reason: stdin"
        );
        // a curl that fails is one fixed word
        std::fs::write(&fake, "#!/bin/sh\n/bin/cat > /dev/null\nexit 22\n").unwrap();
        assert_eq!(Curl(fake).send(&req, &token), Err("curl"));
        assert_eq!(
            Curl(dir.path().join("none")).send(&req, &token),
            Err("spawn")
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
            b"dash-ed",
            b"dot.ted",
            b"eq=ual",
            b"sla/sh",
            "caf\u{e9}".as_bytes(),
        ] {
            assert!(Token::new(bad.to_vec()).is_none(), "reason: token_{bad:?}");
        }
        assert!(Token::new(vec![b'a'; TOKEN_MAX + 1]).is_none());
    }
}
