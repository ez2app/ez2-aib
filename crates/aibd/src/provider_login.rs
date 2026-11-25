//! 重新登入 AI from the page (DESIGN-relogin-card-a9136-1121 r2, 5d33a PREREAD-relogin 0/0/2/3 and
//! r2 0/0/0/3; Andy 11-21 09:1x「為什麼不直接彈窗」): a seat that ended for its login gets a
//! card with 「重新登入」; the built-in page in this pairing presses it, the owner's fingerprint
//! approves it, and aibd runs the provider's own login program the session policy lists.
//!
//! - `{"type":"provider_relogin","provider":"claude"|"codex"|"grok"|"copilot","ticket"?}`: the fingerprint ask,
//!   then the program is started with fixed words ([`args_of`]) and the environment a seat gets.
//!   The answer to this one request (no event, no replay) carries the sign-in address it printed,
//!   checked by [`url_ok`] (https, no user, no port, host exactly [`host_of`]) and rebuilt, and
//!   for codex the one-time device code (5d33a L2: treated as the address is).
//! - `{"type":"provider_relogin_code","login_id","code"}`: claude's paste-back code, written
//!   once to the program's stdin; the same pairing only.
//! - `{"type":"provider_relogin_state","login_id"}`: `running` / `ok` / `failed` and a reason
//!   code; never the address, the code or the program's output.
//!
//! The address, the code and the output live in memory for the round only: never a file, a log
//! line or an event. Events are `provider.login_started` / `provider.login_ended` with
//! `{provider, login_id, ok?, reason?}`. A preview aibd refuses (`login_in_parent`): its seats
//! use the parent's login (`AIBD_SEAT_HOME`). Windows refuses until it is tried there
//! (5d33a N5).
use std::collections::HashMap;
use std::io::Read;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;

pub const RELOGIN: &str = "provider_relogin";
pub const RELOGIN_CODE: &str = "provider_relogin_code";
pub const RELOGIN_STATE: &str = "provider_relogin_state";
/// The operation the owner approves.
pub const OP: &str = "provider.login";

/// The providers with a login aibd can run, and the one host each one's sign-in address may
/// have (P0 on Pro 13, DESIGN r2: claude 2.1.311 prints claude.com, codex 0.144.1
/// auth.openai.com).
pub const PROVIDERS: [(&str, &str); 4] = [
    ("claude", "claude.com"),
    ("codex", "auth.openai.com"),
    ("grok", "accounts.x.ai"),
    ("copilot", "github.com"),
];

/// The words the program is run with: fixed, nothing from the caller.
pub fn args_of(provider: &str) -> Option<&'static [&'static str]> {
    match provider {
        "claude" => Some(&["auth", "login", "--claudeai"]),
        "codex" => Some(&["login", "--device-auth"]),
        "grok" => Some(&["login", "--device-auth"]),
        "copilot" => Some(&["login", "--device-code"]),
        _ => None,
    }
}

/// The words that ask whether the login is there now (a yes or no only is read).
pub fn status_args_of(provider: &str) -> Option<&'static [&'static str]> {
    match provider {
        "claude" => Some(&["auth", "status", "--json"]),
        "codex" => Some(&["login", "status"]),
        // grok 1.0.70 has no status command; `models` says which account it uses (exit 0 either way)
        "grok" => Some(&["models"]),
        _ => None,
    }
}

pub fn host_of(provider: &str) -> Option<&'static str> {
    PROVIDERS
        .iter()
        .find(|(p, _)| *p == provider)
        .map(|(_, h)| *h)
}

/// The program must not open a browser on aibd's machine: the page opens the address
/// (5d33a r2: a constant, this round only, never in a seat's environment).
#[cfg(unix)]
pub const NO_BROWSER: (&str, &str) = ("BROWSER", "/usr/bin/true");
#[cfg(not(unix))]
pub const NO_BROWSER: (&str, &str) = ("BROWSER", "none");

/// After a round ends, the next one for the same provider waits this long.
pub const COOL: Duration = Duration::from_secs(30);
/// The most a paste-back code may be.
pub const CODE_MAX: usize = 512;
/// The least a paste-back code may be.
pub const CODE_MIN: usize = 8;
/// The most output read from the program (one byte past it is dropped with the rest).
pub const OUT_MAX: usize = 64 * 1024;

/// `address` as the page may get it: https, no user part, no port, the host exactly
/// `host_of(provider)`; rebuilt from its parts (5d33a L1), else `None`.
pub fn url_ok(address: &str, provider: &str) -> Option<String> {
    let want = host_of(provider)?;
    let rest = address.strip_prefix("https://")?;
    // no fragment, nothing a browser reads differently (`\` is `/` to it), visible ASCII only
    if !rest.bytes().all(|b| b.is_ascii_graphic()) || rest.contains(['#', '\\']) {
        return None;
    }
    let cut = rest.find(['/', '?']).unwrap_or(rest.len());
    // the authority is the host alone: a user part (`@`) or a port (`:`) makes it differ
    if &rest[..cut] != want {
        return None;
    }
    let tail = &rest[cut..];
    Some(format!("https://{want}{tail}"))
}

/// The first `https://…` run of non-space characters in `text`, ANSI colour codes taken out.
pub fn first_address(text: &str) -> Option<String> {
    let plain = strip_ansi(text);
    let at = plain.find("https://")?;
    let rest = &plain[at..];
    let end = rest
        .find(|c: char| c.is_whitespace() || c == '"' || c == '\'' || c == '<' || c == '>')
        .unwrap_or(rest.len());
    Some(rest[..end].to_owned())
}

/// codex's one-time device code: a word of 4 and 4–5 capitals or digits joined by `-`, after the
/// address.
pub fn device_code(text: &str) -> Option<String> {
    let plain = strip_ansi(text);
    let after = plain.find("https://").map_or("", |i| &plain[i..]);
    after
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '-'))
        .find(|w| {
            let Some((a, b)) = w.split_once('-') else {
                return false;
            };
            let part = |s: &str, lo: usize, hi: usize| {
                (lo..=hi).contains(&s.len())
                    && s.bytes()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
            };
            part(a, 4, 4) && part(b, 4, 5)
        })
        .map(str::to_owned)
}

fn strip_ansi(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut it = text.chars().peekable();
    while let Some(c) = it.next() {
        if c == '\u{1b}' {
            if it.peek() == Some(&'[') {
                it.next();
                for d in it.by_ref() {
                    if d.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

/// A paste-back code: [`CODE_MIN`]–[`CODE_MAX`] visible ASCII, no space or line end.
pub fn code_ok(code: &str) -> bool {
    (CODE_MIN..=CODE_MAX).contains(&code.len()) && code.bytes().all(|b| b.is_ascii_graphic())
}

/// Where a round is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Running,
    Ok,
    Failed(&'static str),
}

impl State {
    fn word(&self) -> &'static str {
        match self {
            State::Running => "running",
            State::Ok => "ok",
            State::Failed(_) => "failed",
        }
    }
}

/// One login round, in memory only.
pub struct Round {
    pub id: String,
    pub provider: &'static str,
    /// The pairing that pressed it: only it may send the code or read the state.
    pub by: String,
    pub state: State,
    pub input: Option<ChildStdin>,
    pub code_written: bool,
    pub ended: Option<Instant>,
}

type Rounds = Mutex<HashMap<&'static str, Round>>;

fn rounds() -> &'static Rounds {
    static R: std::sync::OnceLock<Rounds> = std::sync::OnceLock::new();
    R.get_or_init(|| Mutex::new(HashMap::new()))
}

/// May a round for `provider` start now? `(status, reason)` of the refusal.
pub fn gate(
    provider: &str,
    in_preview: bool,
    on_windows: bool,
    now: Instant,
) -> Result<&'static str, (StatusCode, &'static str)> {
    let Some(p) = PROVIDERS.iter().map(|(p, _)| *p).find(|p| *p == provider) else {
        return Err((StatusCode::BAD_REQUEST, "provider"));
    };
    if in_preview {
        return Err((StatusCode::CONFLICT, "login_in_parent"));
    }
    if on_windows {
        return Err((StatusCode::NOT_IMPLEMENTED, "login_not_tried_here"));
    }
    let g = rounds()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(r) = g.get(p) {
        if r.state == State::Running {
            return Err((StatusCode::CONFLICT, "login_running"));
        }
        if r.ended.is_some_and(|e| now.duration_since(e) < COOL) {
            return Err((StatusCode::TOO_MANY_REQUESTS, "login_cooling"));
        }
    }
    Ok(p)
}

/// What the owner is asked: aibd's words and the provider's name only.
pub fn ask_of(provider: &str, by: String) -> crate::owner_ok::Ask {
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!("重新登入 {provider}(在瀏覽器授權)"),
        params: serde_json::to_vec(&json!({"provider": provider})).unwrap_or_default(),
        by,
    }
}

/// The environment the login runs in: what a seat's session gets from aibd (the same HOME, so
/// the login lands where the seats read it), and [`NO_BROWSER`].
pub fn env_of(pass: &[String]) -> Vec<(std::ffi::OsString, std::ffi::OsString)> {
    let spec = crate::platform::session::Spec {
        id: String::new(),
        argv: Vec::new(),
        cwd: std::path::PathBuf::new(),
        env: vec![(NO_BROWSER.0.to_owned(), NO_BROWSER.1.to_owned())],
        pass: pass.to_vec(),
        input: false,
    };
    crate::platform::session::child_env(&spec, std::env::vars_os())
        .into_iter()
        .filter(|(k, _)| k != crate::platform::session::SESSION_ENV)
        .collect()
}

fn command(
    prog: &std::path::Path,
    args: &[&str],
    env: &[(std::ffi::OsString, std::ffi::OsString)],
) -> Command {
    let mut c = Command::new(prog);
    c.args(args)
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)));
    if let Some(home) = env.iter().find(|(k, _)| k == "HOME").map(|(_, v)| v) {
        c.current_dir(home);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // its own group: at the end the whole group is killed
        c.process_group(0);
    }
    c
}

fn kill_all(child: &mut Child) {
    #[cfg(unix)]
    {
        if let Ok(pid) = i32::try_from(child.id()) {
            let _ = nix::sys::signal::killpg(
                nix::unistd::Pid::from_raw(pid),
                nix::sys::signal::Signal::SIGKILL,
            );
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// The program's output so far, in memory, at most [`OUT_MAX`] bytes.
type Out = Arc<Mutex<Vec<u8>>>;

fn read_into(mut from: impl Read + Send + 'static, out: Out) {
    let _ = std::thread::Builder::new()
        .name("aibd-login-out".into())
        .spawn(move || {
            let mut b = [0u8; 1024];
            while let Ok(n) = from.read(&mut b) {
                if n == 0 {
                    break;
                }
                let mut o = out
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                let room = OUT_MAX.saturating_sub(o.len());
                o.extend_from_slice(&b[..n.min(room)]);
            }
        });
}

/// What the page gets when the program printed its address.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Started {
    pub url: String,
    pub device_code: Option<String>,
}

/// Start `prog args` and wait (at most [`crate::timers::PROVIDER_LOGIN_URL_WAIT`]) for the
/// address it prints. The child and its stdin are given back to keep for the round; on any
/// refusal the child is killed.
pub fn start(
    prog: &std::path::Path,
    provider: &str,
    args: &[&str],
    env: &[(std::ffi::OsString, std::ffi::OsString)],
) -> Result<(Child, Option<ChildStdin>, Started), &'static str> {
    let mut cmd = command(prog, args, env);
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|_| "login_not_started")?;
    let out: Out = Arc::new(Mutex::new(Vec::new()));
    if let Some(o) = child.stdout.take() {
        read_into(o, out.clone());
    }
    if let Some(e) = child.stderr.take() {
        read_into(e, out.clone());
    }
    let input = child.stdin.take();
    let op = crate::timers::PROVIDER_LOGIN_URL_WAIT.begin(None);
    loop {
        let text = {
            let o = out
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            String::from_utf8_lossy(&o).into_owned()
        };
        if let Some(a) = first_address(&text) {
            // the whole address is there once a space or line end follows it
            let whole = strip_ansi(&text)
                .split_once(a.as_str())
                .is_some_and(|(_, after)| after.starts_with(char::is_whitespace));
            let code = device_code(&text);
            if whole && (!matches!(provider, "codex" | "grok" | "copilot") || code.is_some()) {
                let Some(url) = url_ok(&a, provider) else {
                    kill_all(&mut child);
                    return Err("url_host_refused");
                };
                return Ok((
                    child,
                    input,
                    Started {
                        url,
                        device_code: code,
                    },
                ));
            }
        }
        if crate::gh_view::ended_unreaped(&mut child).is_some() || !op.tick() {
            kill_all(&mut child);
            return Err("no_url");
        }
    }
}

/// Is the login there now? `status_args_of` run to its end; claude: its JSON's `loggedIn`;
/// codex: exit 0.
pub fn logged_in(
    prog: &std::path::Path,
    provider: &str,
    env: &[(std::ffi::OsString, std::ffi::OsString)],
) -> bool {
    let Some(args) = status_args_of(provider) else {
        if provider == "copilot" {
            let home = env
                .iter()
                .find(|(k, _)| k == "HOME")
                .map(|(_, v)| std::path::PathBuf::from(v))
                .or_else(crate::workspace::seat_login_home);
            return home.is_some_and(|h| {
                crate::workspace::copilot_users(&h.join(".copilot").join("config.json"))
            }) && crate::workspace::copilot_token_stored("copilot-cli");
        }
        return false;
    };
    let (code, out) = crate::gh_view::run_timed(
        command(prog, args, env),
        &crate::timers::PROVIDER_LOGIN_STATUS_WAIT,
        None,
    );
    match provider {
        "claude" => {
            code == Some(0)
                && serde_json::from_slice::<Value>(&out)
                    .ok()
                    .and_then(|v| v.get("loggedIn").and_then(Value::as_bool))
                    == Some(true)
        }
        "grok" => code == Some(0) && grok_says_logged_in(&out),
        _ => code == Some(0),
    }
}

/// `grok models`' first line: `You are logged in with …` or `You are not authenticated.`
/// (grok 1.0.70, measured 08de5 11-25); its exit is 0 both ways, so the words are read.
pub fn grok_says_logged_in(out: &[u8]) -> bool {
    strip_ansi(&String::from_utf8_lossy(out))
        .lines()
        .find(|l| !l.trim().is_empty())
        .is_some_and(|l| l.trim_start().starts_with("You are logged in"))
}

/// Wait for the program's end (at most [`crate::timers::PROVIDER_LOGIN_WAIT`]), then ask the
/// status. The reason code when it did not log in.
pub fn finish(
    mut child: Child,
    prog: &std::path::Path,
    provider: &str,
    env: &[(std::ffi::OsString, std::ffi::OsString)],
) -> Result<(), &'static str> {
    let op = crate::timers::PROVIDER_LOGIN_WAIT.begin(None);
    let code = loop {
        // seen without being reaped: the group's number stays ours for the kill below
        if let Some(c) = crate::gh_view::ended_unreaped(&mut child) {
            break c;
        }
        if !op.tick() {
            kill_all(&mut child);
            return Err("timeout");
        }
    };
    // what it started may still hold the group: nothing of it outlives the round
    kill_all(&mut child);
    if code != Some(0) {
        return Err("exit_not_0");
    }
    if !logged_in(prog, provider, env) {
        return Err("not_logged_in");
    }
    Ok(())
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "provider" => "body must be {type, provider, ticket?}; provider is claude or codex",
        "login_in_parent" => "a preview logs in through the aibd that runs it: open its page",
        "login_not_tried_here" => "logging in from the page is not tried on this platform yet",
        "login_running" => "a login for this provider is running",
        "login_cooling" => "a login for this provider ended less than 30 s ago",
        "provider_not_listed" => "the session policy lists no such program",
        "login_not_started" => "the login program did not start",
        "url_host_refused" => "the login printed an address aibd does not hand on",
        "no_url" => "the login printed no address in time",
        "login_id" => "no such login round for this page",
        "code" => {
            "the code must be 8-512 visible characters, sent once, for a login that takes one"
        }
        _ => "the login could not be run",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::TOO_MANY_REQUESTS => "rate_limited",
        StatusCode::NOT_IMPLEMENTED => "not_implemented",
        StatusCode::BAD_GATEWAY => "bad_gateway",
        StatusCode::INTERNAL_SERVER_ERROR => "internal",
        _ => "forbidden",
    };
    (
        status,
        Json(json!({"code": code, "reason": reason, "message": message,
            "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ask {
    #[serde(rename = "type")]
    _kind: String,
    provider: String,
    #[serde(default)]
    ticket: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Code {
    #[serde(rename = "type")]
    _kind: String,
    login_id: String,
    code: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Look {
    #[serde(rename = "type")]
    _kind: String,
    login_id: String,
}

/// The listed program for `provider`, resolved now.
fn program(st: &AppState, provider: &str) -> Option<std::path::PathBuf> {
    let p = st.session_policy.as_ref();
    p.programs
        .iter()
        .find(|e| e.name == provider)
        .and_then(|e| crate::allowlist::resolve(&e.path.to_string_lossy(), p).ok())
}

/// `provider_relogin`, for the built-in page `by` (`page:<pair>`).
pub async fn relogin(st: AppState, body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Ask>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "provider");
    };
    let provider = match gate(
        &req.provider,
        crate::preview::is_preview(),
        cfg!(windows),
        Instant::now(),
    ) {
        Ok(p) => p,
        Err((s, why)) => return refuse(s, why),
    };
    let Some(prog) = program(&st, provider) else {
        return refuse(StatusCode::NOT_FOUND, "provider_not_listed");
    };
    let ask = ask_of(provider, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let Ok(id) = crate::sessions::new_id("l_") else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "login");
    };
    // taken before the program starts, so two approved presses cannot run two
    {
        let mut g = rounds()
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if g.get(provider).is_some_and(|r| r.state == State::Running) {
            return refuse(StatusCode::CONFLICT, "login_running");
        }
        g.insert(
            provider,
            Round {
                id: id.clone(),
                provider,
                by: by.clone(),
                state: State::Running,
                input: None,
                code_written: false,
                ended: None,
            },
        );
    }
    let env = env_of(&st.session_policy.env);
    let args = args_of(provider).unwrap_or_default();
    let (p2, e2) = (prog.clone(), env.clone());
    let started = tokio::task::spawn_blocking(move || start(&p2, provider, args, &e2))
        .await
        .unwrap_or(Err("login_not_started"));
    let (child, input, s) = match started {
        Ok(x) => x,
        Err(why) => {
            end(provider, &id, State::Failed(why));
            crate::seats::side_event(
                &st,
                "provider_login",
                "provider.login_ended",
                json!({"provider": provider, "login_id": id, "ok": false, "reason": why}),
            )
            .await;
            let status = if why == "login_not_started" {
                StatusCode::INTERNAL_SERVER_ERROR
            } else {
                StatusCode::BAD_GATEWAY
            };
            return refuse(status, why);
        }
    };
    if let Some(r) = rounds()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get_mut(provider)
        .filter(|r| r.id == id)
    {
        r.input = input;
    }
    crate::seats::side_event(
        &st,
        "provider_login",
        "provider.login_started",
        json!({"provider": provider, "login_id": id}),
    )
    .await;
    let (st2, id2) = (st.clone(), id.clone());
    tokio::spawn(async move {
        let done = tokio::task::spawn_blocking(move || finish(child, &prog, provider, &env))
            .await
            .unwrap_or(Err("login_lost"));
        let state = match done {
            Ok(()) => State::Ok,
            Err(why) => State::Failed(why),
        };
        end(provider, &id2, state.clone());
        let reason = match state {
            State::Failed(w) => Some(w),
            _ => None,
        };
        crate::seats::side_event(
            &st2,
            "provider_login",
            "provider.login_ended",
            json!({"provider": provider, "login_id": id2, "ok": reason.is_none(),
                "reason": reason}),
        )
        .await;
    });
    // this answer is the only way the address and the code leave aibd (5d33a L2)
    let mode = if s.device_code.is_some() {
        "device"
    } else {
        "paste"
    };
    Json(
        json!({"done": true, "login_id": id, "provider": provider, "mode": mode,
        "url": s.url, "device_code": s.device_code}),
    )
    .into_response()
}

/// Mark round `id` of `provider` ended (its stdin closed).
fn end(provider: &str, id: &str, state: State) {
    if let Some(r) = rounds()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get_mut(provider)
        .filter(|r| r.id == id)
    {
        r.state = state;
        r.input = None;
        r.ended = Some(Instant::now());
    }
}

/// Write `code` and a line end to round `id`'s program, once, for the pairing `by` that started
/// it; codex takes none.
pub fn write_code(id: &str, by: &str, code: &str) -> Result<(), (StatusCode, &'static str)> {
    let mut g = rounds()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(r) = g.values_mut().find(|r| r.id == id && r.by == by) else {
        return Err((StatusCode::NOT_FOUND, "login_id"));
    };
    if r.state != State::Running || r.code_written || r.provider != "claude" || !code_ok(code) {
        return Err((StatusCode::BAD_REQUEST, "code"));
    }
    let Some(w) = r.input.as_mut() else {
        return Err((StatusCode::BAD_REQUEST, "code"));
    };
    r.code_written = true;
    let mut line = code.as_bytes().to_vec();
    line.push(b'\n');
    std::io::Write::write_all(w, &line)
        .and_then(|()| std::io::Write::flush(w))
        .map_err(|_| (StatusCode::CONFLICT, "code"))
}

/// Round `id`'s state for the pairing `by`.
pub fn state_of(id: &str, by: &str) -> Option<(State, &'static str)> {
    let g = rounds()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    g.values()
        .find(|r| r.id == id && r.by == by)
        .map(|r| (r.state.clone(), r.provider))
}

/// `provider_relogin_code`, for the built-in page `by`.
pub async fn code(body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Code>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "code");
    };
    match write_code(&req.login_id, &by, &req.code) {
        Ok(()) => (
            StatusCode::ACCEPTED,
            Json(json!({"done": false, "login_id": req.login_id, "state": "running"})),
        )
            .into_response(),
        Err((s, why)) => refuse(s, why),
    }
}

/// `provider_relogin_state`, for the built-in page `by`.
pub async fn look(body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Look>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "login_id");
    };
    let Some((state, provider)) = state_of(&req.login_id, &by) else {
        return refuse(StatusCode::NOT_FOUND, "login_id");
    };
    let reason = match state {
        State::Failed(w) => Some(w),
        _ => None,
    };
    Json(
        json!({"login_id": req.login_id, "provider": provider, "state": state.word(),
        "reason": reason}),
    )
    .into_response()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// RL4 (5d33a L1): only https, no user, no port, the host exactly the provider's; rebuilt.
    #[test]
    fn only_the_providers_own_sign_in_address_is_handed_on() {
        let ok = "https://claude.com/cai/oauth/authorize?code=true&state=abc";
        assert_eq!(url_ok(ok, "claude").as_deref(), Some(ok));
        for bad in [
            "http://claude.com/cai/oauth/authorize",
            "https://evil.example/cai",
            "https://claude.com.evil.example/x",
            "https://x.claude.com/x",
            "https://user@claude.com/x",
            "https://user:pw@claude.com/x",
            "https://claude.com:8443/x",
            "https://CLAUDE.com/x",
            "https://claude.com\\@evil.example/x",
            "https://claude.com/x#frag",
            "https://claude.com@evil.example/x",
            "https://auth.openai.com/codex/device",
        ] {
            assert_eq!(url_ok(bad, "claude"), None, "reason: handed_on {bad}");
        }
        assert_eq!(
            url_ok("https://auth.openai.com/codex/device", "codex").as_deref(),
            Some("https://auth.openai.com/codex/device")
        );
        assert_eq!(url_ok("https://claude.com/x", "gemini"), None);
        // grok / copilot: the hosts their device logins print (measured 08de5 11-25)
        assert_eq!(
            url_ok("https://accounts.x.ai/oauth2/device?user_code=X", "grok").as_deref(),
            Some("https://accounts.x.ai/oauth2/device?user_code=X")
        );
        assert_eq!(
            url_ok("https://github.com/login/device", "copilot").as_deref(),
            Some("https://github.com/login/device")
        );
        for (bad, p) in [
            ("https://x.ai/oauth2/device", "grok"),
            ("https://accounts.x.ai.evil.example/x", "grok"),
            ("https://github.com.evil.example/login/device", "copilot"),
            ("https://gist.github.com/x", "copilot"),
            ("https://github.com/login/device", "grok"),
        ] {
            assert_eq!(url_ok(bad, p), None, "reason: handed_on {bad}");
        }
    }

    /// ebab8 condition 5: grok's and copilot's own words, as their login and status print them
    /// (grok 1.0.70, copilot 1.0.115; codes made up).
    #[test]
    fn grok_and_copilot_logins_are_read_from_their_own_words() {
        let grok = "To sign in, open this URL in your browser:\n\n  https://accounts.x.ai/oauth2/device?user_code=QWER-TY12\n\nConfirm this code in your browser:\n\n  QWER-TY12\n";
        assert_eq!(
            first_address(grok).as_deref(),
            Some("https://accounts.x.ai/oauth2/device?user_code=QWER-TY12")
        );
        assert_eq!(device_code(grok).as_deref(), Some("QWER-TY12"));
        let copilot = "To authenticate, visit https://github.com/login/device and enter code ABCD-1234\nWaiting for authorization...\n";
        assert_eq!(
            first_address(copilot).as_deref(),
            Some("https://github.com/login/device")
        );
        assert_eq!(device_code(copilot).as_deref(), Some("ABCD-1234"));
        assert_eq!(args_of("grok"), Some(&["login", "--device-auth"][..]));
        assert_eq!(args_of("copilot"), Some(&["login", "--device-code"][..]));
        // grok: `models` exits 0 both ways; its first line is what says it
        assert_eq!(status_args_of("grok"), Some(&["models"][..]));
        assert!(grok_says_logged_in(
            b"You are logged in with grok.com.\n\nDefault model: grok-4.6\n"
        ));
        assert!(
            !grok_says_logged_in(b"You are not authenticated.\n\nDefault model: grok-4.6\n"),
            "reason: grok_not_authenticated_is_logged_in"
        );
        assert!(!grok_says_logged_in(b""), "reason: grok_empty_is_logged_in");
        assert!(
            !grok_says_logged_in(b"Default model: grok-4.6\nYou are logged in with grok.com.\n"),
            "reason: grok_not_first_line"
        );
        // copilot has no status words: its config and the stored token are looked at
        assert_eq!(status_args_of("copilot"), None);
    }

    #[test]
    fn the_address_and_the_device_code_are_read_from_the_output() {
        let claude = "Opening browser to sign in…\nIf the browser didn't open, visit: \u{1b}[1mhttps://claude.com/cai/oauth/authorize?a=1\u{1b}[0m\nPaste code here if prompted > ";
        assert_eq!(
            first_address(claude).as_deref(),
            Some("https://claude.com/cai/oauth/authorize?a=1")
        );
        assert_eq!(device_code(claude), None);
        let codex = "1. Open this link\n   https://auth.openai.com/codex/device\n2. Enter this one-time code\n   ABCD-12345\n";
        assert_eq!(
            first_address(codex).as_deref(),
            Some("https://auth.openai.com/codex/device")
        );
        assert_eq!(device_code(codex).as_deref(), Some("ABCD-12345"));
        assert_eq!(device_code("ABCD-1234 before any address"), None);
    }

    /// RL3: the words are fixed; nothing a caller sends is among them.
    #[test]
    fn the_program_runs_with_fixed_words_only() {
        assert_eq!(
            args_of("claude"),
            Some(&["auth", "login", "--claudeai"][..])
        );
        assert_eq!(args_of("codex"), Some(&["login", "--device-auth"][..]));
        assert_eq!(args_of("grok"), Some(&["login", "--device-auth"][..]));
        assert_eq!(args_of("copilot"), Some(&["login", "--device-code"][..]));
        assert_eq!(args_of("bash"), None);
        let bad = json!({"type": RELOGIN, "provider": "claude", "args": ["--email", "x"]});
        assert!(
            serde_json::from_value::<Ask>(bad).is_err(),
            "reason: caller_words_taken"
        );
    }

    #[test]
    fn a_code_is_visible_characters_of_a_bounded_length() {
        assert!(code_ok("abcDEF12-_#x"));
        assert!(!code_ok("short"));
        assert!(!code_ok("has space inside"));
        assert!(!code_ok("line\nend123"));
        assert!(!code_ok(&"x".repeat(CODE_MAX + 1)));
    }

    /// RL8 / N5: a preview, Windows, an unknown provider, a running round and a cooling one are
    /// refused.
    #[test]
    fn a_round_starts_only_in_the_parent_one_at_a_time() {
        let now = Instant::now();
        assert_eq!(
            gate("claude", true, false, now),
            Err((StatusCode::CONFLICT, "login_in_parent")),
            "reason: preview_logs_in"
        );
        assert_eq!(
            gate("claude", false, true, now),
            Err((StatusCode::NOT_IMPLEMENTED, "login_not_tried_here"))
        );
        assert_eq!(
            gate("bash", false, false, now),
            Err((StatusCode::BAD_REQUEST, "provider"))
        );
        // codex is kept for this test: the other tests do not use its slot
        let put = |state: State, ended: Option<Instant>| {
            rounds().lock().unwrap().insert(
                "codex",
                Round {
                    id: "l_t".into(),
                    provider: "codex",
                    by: "page:a".into(),
                    state,
                    input: None,
                    code_written: false,
                    ended,
                },
            );
        };
        put(State::Running, None);
        assert_eq!(
            gate("codex", false, false, now),
            Err((StatusCode::CONFLICT, "login_running")),
            "reason: two_rounds"
        );
        put(State::Ok, Some(now));
        assert_eq!(
            gate("codex", false, false, now + Duration::from_secs(5)),
            Err((StatusCode::TOO_MANY_REQUESTS, "login_cooling")),
            "reason: no_cooling"
        );
        assert_eq!(gate("codex", false, false, now + COOL), Ok("codex"));
        // RL6: the state is the pressing pairing's only; codex takes no code
        assert!(
            state_of("l_t", "page:b").is_none(),
            "reason: other_page_reads"
        );
        assert_eq!(state_of("l_t", "page:a").map(|s| s.1), Some("codex"));
        assert_eq!(
            write_code("l_t", "page:a", "abcdefgh123"),
            Err((StatusCode::BAD_REQUEST, "code"))
        );
        rounds().lock().unwrap().remove("codex");
    }

    /// RL2: the fingerprint is asked for this operation, which is wired.
    #[test]
    fn the_login_asks_the_owners_fingerprint() {
        assert!(
            crate::owner_ok::WIRED.contains(&OP),
            "reason: login_without_fingerprint"
        );
        let a = ask_of("claude", "page:a".into());
        assert_eq!(a.operation, OP);
        assert!(!a.resource.contains("http"));
    }

    /// RL10: the login runs with the seats' HOME and without a browser of its own; aibd's own
    /// keys and the session marker are not given.
    #[test]
    fn the_login_runs_where_the_seats_read_their_login() {
        let env = env_of(&[]);
        let get = |k: &str| {
            env.iter()
                .find(|(n, _)| n == k)
                .map(|(_, v)| v.to_string_lossy().into_owned())
        };
        assert_eq!(get(NO_BROWSER.0).as_deref(), Some(NO_BROWSER.1));
        if let Ok(h) = std::env::var("HOME") {
            assert_eq!(get("HOME"), Some(h), "reason: home_not_the_seats");
        }
        assert!(get(crate::platform::session::SESSION_ENV).is_none());
        assert!(env
            .iter()
            .all(|(k, _)| !k.to_string_lossy().starts_with("AIBD_")));
    }

    #[cfg(unix)]
    fn script(dir: &std::path::Path, body: &str) -> std::path::PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let p = dir.join("fake-login");
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    /// ebab8 condition 5: a grok / copilot login hands the page the address and the code only;
    /// whatever else the program prints (a token among it) is not in what comes back, and a
    /// device login without its code is not handed on.
    #[cfg(unix)]
    #[test]
    fn a_grok_or_copilot_login_hands_on_the_address_and_the_code_only() {
        let d = tempfile::tempdir().unwrap();
        let env = vec![("PATH".into(), "/usr/bin:/bin".into())];
        for (provider, body, url, code) in [
            (
                "grok",
                "echo 'token: xai-SECRETSECRETSECRET'\necho 'open: https://accounts.x.ai/oauth2/device?user_code=QWER-TY12 '\necho 'Confirm: QWER-TY12'\necho 'refresh_token=SECRETSECRET'\nsleep 30",
                "https://accounts.x.ai/oauth2/device?user_code=QWER-TY12",
                "QWER-TY12",
            ),
            (
                "copilot",
                "echo 'gho_SECRETSECRETSECRET'\necho 'To authenticate, visit https://github.com/login/device and enter code ABCD-1234'\nsleep 30",
                "https://github.com/login/device",
                "ABCD-1234",
            ),
        ] {
            let p = script(d.path(), body);
            let (mut child, _input, s) = start(&p, provider, &[], &env).unwrap();
            kill_all(&mut child);
            assert_eq!(s.url, url, "reason: {provider}_url");
            assert_eq!(s.device_code.as_deref(), Some(code), "reason: {provider}_code");
            let handed = format!("{} {:?}", s.url, s.device_code);
            assert!(!handed.contains("SECRET"), "reason: {provider}_token_handed_on");
        }
        // an address with no code yet is waited on, not handed on without it
        let p = script(
            d.path(),
            "echo 'visit https://github.com/login/device now'\nexit 0",
        );
        assert_eq!(
            start(&p, "copilot", &[], &env).err(),
            Some("no_url"),
            "reason: copilot_without_code"
        );
        // grok's status: exit 0 with "not authenticated" is not a login
        let p = script(
            d.path(),
            "echo 'You are not authenticated.'\necho\necho 'Default model: grok-4.6'\nexit 0",
        );
        assert!(
            !logged_in(&p, "grok", &env),
            "reason: grok_exit_0_is_logged_in"
        );
        let p = script(
            d.path(),
            "echo 'You are logged in with grok.com.'\necho\necho 'Default model: grok-4.6'\nexit 0",
        );
        assert!(logged_in(&p, "grok", &env));
    }

    /// RL5 / RL6 / RL7: the address comes back, the code reaches the program once as a line,
    /// and a program that does not end is killed at the bound.
    #[cfg(unix)]
    #[test]
    fn the_program_prints_its_address_takes_the_code_and_is_ended() {
        let d = tempfile::tempdir().unwrap();
        let got = d.path().join("got");
        let p = script(
            d.path(),
            &format!(
                "echo 'visit: https://claude.com/cai/oauth/authorize?x=1'\nprintf 'Paste code > '\nread c\necho \"$c\" > '{}'\nexit 3",
                got.display()
            ),
        );
        let env = vec![("PATH".into(), "/usr/bin:/bin".into())];
        let (child, input, s) = start(&p, "claude", &[], &env).unwrap();
        assert_eq!(s.url, "https://claude.com/cai/oauth/authorize?x=1");
        assert_eq!(s.device_code, None);
        rounds().lock().unwrap().insert(
            "claude",
            Round {
                id: "l_c".into(),
                provider: "claude",
                by: "page:a".into(),
                state: State::Running,
                input,
                code_written: false,
                ended: None,
            },
        );
        assert_eq!(
            write_code("l_c", "page:b", "abcdefgh123"),
            Err((StatusCode::NOT_FOUND, "login_id")),
            "reason: other_page_writes"
        );
        write_code("l_c", "page:a", "abcdefgh123").unwrap();
        assert_eq!(
            write_code("l_c", "page:a", "abcdefgh123"),
            Err((StatusCode::BAD_REQUEST, "code")),
            "reason: code_written_twice"
        );
        assert_eq!(
            finish(child, &p, "claude", &env),
            Err("exit_not_0"),
            "reason: a_failed_login_is_ok"
        );
        assert_eq!(std::fs::read_to_string(&got).unwrap(), "abcdefgh123\n");
        rounds().lock().unwrap().remove("claude");
        // an address on another host is not handed on, and the program is not left running
        let p = script(d.path(), "echo 'visit: https://claude.ai/x '\nsleep 30");
        assert_eq!(
            start(&p, "claude", &[], &env).err(),
            Some("url_host_refused"),
            "reason: url_host_refused"
        );
    }
}
