//! `aibd ctl preview swap <id> --sha <hex>`: the terminal swaps a running preview to the build
//! already in its folder, without the person's 「按一下更新」 (Andy 11-21 06:1x 「這種母版開關你要
//! 自已開關、然後自已看畫面修正就好,為什麼要我等」; PREREAD-preview-swap-ctl-a9136-1121 r1,
//! 5d33a PREREAD-previewswap 0/0/2/2).
//!
//! - Only the start-up token asks (a page uses `act preview_update`, a seat is refused). No
//!   fingerprint: the build must be the one in the preview's own folder whose sha256 is the
//!   asked one and a `ready` line of its VERSIONS; nothing else can be named, and the parent
//!   itself is never swapped.
//! - Seats running in the preview are first asked, in fixed words, to write their handoff; the
//!   swap waits until each said so or [`crate::timers::PREVIEW_HANDOFF_WAIT`] is over, then
//!   goes on and names the seats that did not (`handoff_missing`).
//! - The preview's token file is read as untrusted (5d33a L1): not through a link, a plain
//!   file, short, visible ASCII; the port is the parent's own record and only 127.0.0.1.
//! - One swap per preview at a time; [`COOL`] after a swap ended before the next; refusals do
//!   not move it and are written once per reason per [`COOL`] (5d33a N1).
use crate::api::AppState;
use crate::guard::Caller;
use axum::extract::{Path as UrlPath, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Time after a swap ended before the next one of the same preview (5d33a PREREAD 3).
pub const COOL: Duration = Duration::from_secs(60);
/// The longest token file read from a preview's `run/`.
pub const TOKEN_MAX: u64 = 256;
/// What each running seat of the preview is typed before a swap; fixed, never the caller's.
pub const HANDOFF_WORDS: &str =
    "預覽要換版了:請現在寫交接(aib handoff),寫好回報;約 5 分鐘後這一席會停下、換版後再接著做。";
/// The preview's own route the parent asks for the handoff.
pub const HANDOFF_PATH: &str = "/api/v1/preview/handoff";

/// Each preview's ctl swap as `GET` shows it.
static STATE: Mutex<BTreeMap<String, Value>> = Mutex::new(BTreeMap::new());
/// When each preview's last swap ended.
static ENDED: Mutex<BTreeMap<String, Instant>> = Mutex::new(BTreeMap::new());
/// When each (preview, reason) refusal was last written.
static REFUSED: Mutex<BTreeMap<(String, String), Instant>> = Mutex::new(BTreeMap::new());

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (status, Json(json!({"code": code, "message": message}))).into_response()
}

/// Write a refusal once per preview and reason per [`COOL`]; the answer either way.
fn refused(st: &AppState, id: &str, status: StatusCode, code: &str, message: &str) -> Response {
    let key = (id.to_owned(), code.to_owned());
    let write = {
        let mut g = lock(&REFUSED);
        let fresh = g.get(&key).is_none_or(|t| t.elapsed() >= COOL);
        if fresh {
            g.insert(key, Instant::now());
        }
        fresh
    };
    if write {
        crate::preview::record(
            &st.store,
            "preview.swap_refused",
            json!({"id": id, "by": crate::preview::BY_CTL, "why": code}),
        );
    }
    refuse(status, code, message)
}

/// Why the ask is refused before anything is looked at (caller, place, body).
pub fn ask_problem(
    c: &Caller,
    in_preview: bool,
    body: &Value,
) -> Option<(StatusCode, &'static str)> {
    if !matches!(c, Caller::StartupToken) {
        return Some((StatusCode::FORBIDDEN, "preview_swap_terminal_only"));
    }
    if in_preview {
        return Some((StatusCode::CONFLICT, "in_preview"));
    }
    let Some(o) = body.as_object() else {
        return Some((StatusCode::BAD_REQUEST, "invalid_request"));
    };
    if o.keys().any(|k| k != "sha256") {
        return Some((StatusCode::BAD_REQUEST, "unknown_field"));
    }
    if !o
        .get("sha256")
        .and_then(Value::as_str)
        .is_some_and(is_hex64)
    {
        return Some((StatusCode::BAD_REQUEST, "sha256_bad"));
    }
    None
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// The asked sha is the build in the folder and a `ready` line of its record.
pub fn build_matches(found_sha: Option<&str>, ready: &[String], asked: &str) -> bool {
    found_sha == Some(asked) && ready.iter().any(|s| s == asked)
}

/// Still cooling: a swap of `id` ended less than [`COOL`] ago.
fn cooling(id: &str) -> bool {
    lock(&ENDED).get(id).is_some_and(|t| t.elapsed() < COOL)
}

/// `POST /api/v1/previews/{id}/swap` `{sha256}`: 202 and the swap runs in the background.
pub async fn ask(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    UrlPath(id): UrlPath<String>,
    Json(body): Json<Value>,
) -> Response {
    if let Some((status, code)) = ask_problem(&c, crate::preview::is_preview(), &body) {
        return refuse(status, code, "the preview was not swapped");
    }
    let Some(dir) = crate::preview::preview_dir(&id) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "id_bad",
            "name the preview by its id",
        );
    };
    let asked = body["sha256"].as_str().unwrap_or_default().to_owned();
    if cooling(&id) {
        return refused(
            &st,
            &id,
            StatusCode::TOO_MANY_REQUESTS,
            "swap_cooling",
            "a swap of this preview ended less than 60 s ago",
        );
    }
    let d = dir.clone();
    let seen = tokio::task::spawn_blocking(move || {
        let found = crate::preview::find(&d).map(|f| f.sha256);
        let ready: Vec<String> = crate::preview::read_versions(&d)
            .into_iter()
            .filter(|v| v.state == "ready")
            .map(|v| v.sha256)
            .collect();
        (found, ready)
    })
    .await;
    let Ok((found, ready)) = seen else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "preview not read",
        );
    };
    if !build_matches(found.as_deref(), &ready, &asked) {
        return refused(
            &st,
            &id,
            StatusCode::CONFLICT,
            "sha_mismatch",
            "that is not the ready build in the preview's folder",
        );
    }
    let store = st.store.clone();
    let i = id.clone();
    let port = tokio::task::spawn_blocking(move || crate::preview::reap(&store, &i).map(|r| r.0))
        .await
        .ok()
        .flatten();
    let Some(port) = port else {
        return refused(
            &st,
            &id,
            StatusCode::CONFLICT,
            "preview_not_running",
            "start the preview first",
        );
    };
    if !lock(&crate::preview::SWAPPING).insert(id.clone()) {
        return refused(
            &st,
            &id,
            StatusCode::CONFLICT,
            "preview_swapping",
            "this preview is being swapped",
        );
    }
    let until = crate::sessions::now_rfc3339();
    lock(&STATE).insert(
        id.clone(),
        json!({"state": "handing_off", "sha256": asked, "since": until}),
    );
    let store = st.store.clone();
    let i = id.clone();
    let spawned = std::thread::Builder::new()
        .name("aibd-preview-swap".into())
        .spawn(move || {
            let out = run(&store, &i, &dir, port, &asked);
            lock(&crate::preview::SWAPPING).remove(&i);
            lock(&ENDED).insert(i.clone(), Instant::now());
            lock(&STATE).insert(i, out);
        });
    if spawned.is_err() {
        lock(&crate::preview::SWAPPING).remove(&id);
        lock(&STATE).remove(&id);
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "swap not started",
        );
    }
    (
        StatusCode::ACCEPTED,
        Json(json!({"id": id, "state": "handing_off",
            "wait_s": crate::timers::PREVIEW_HANDOFF_WAIT.max_lifetime.as_secs()})),
    )
        .into_response()
}

/// `GET /api/v1/previews/{id}/swap`: the last ctl swap of `id` (`state: none` before one).
pub async fn state(Extension(c): Extension<Caller>, UrlPath(id): UrlPath<String>) -> Response {
    if !matches!(c, Caller::StartupToken) {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_swap_terminal_only",
            "the terminal reads its swap",
        );
    }
    let v = lock(&STATE)
        .get(&id)
        .cloned()
        .unwrap_or_else(|| json!({"state": "none"}));
    Json(v).into_response()
}

/// Ask for the handoff, wait, swap; what `GET` then shows.
fn run(store: &aib_store::Store, id: &str, dir: &Path, port: u16, asked: &str) -> Value {
    let (asked_seats, missing, why) = hand_off(dir, port);
    // the folder may have changed while the seats wrote: only the asked build goes in
    if crate::preview::find(dir).map(|f| f.sha256).as_deref() != Some(asked) {
        return json!({"state": "failed", "code": "preview_changed", "sha256": asked});
    }
    let extra = json!({"handoff_asked": asked_seats, "handoff_missing": missing,
        "handoff_note": why});
    match crate::preview::swap_once(store, id, crate::preview::BY_CTL, extra) {
        Ok(v) => json!({"state": "done", "sha256": asked, "result": v,
            "handoff_asked": asked_seats, "handoff_missing": missing}),
        Err(code) => json!({"state": "failed", "code": code, "sha256": asked,
            "handoff_asked": asked_seats, "handoff_missing": missing}),
    }
}

/// Ask the preview's running seats to write their handoff and wait; (asked, missing, note).
fn hand_off(dir: &Path, port: u16) -> (Vec<String>, Vec<String>, Option<String>) {
    if crate::preview::seats_running_in(dir) == Some(0) {
        return (vec![], vec![], None);
    }
    let token = match read_token(&dir.join("data").join("run").join("aibd.token")) {
        Ok(t) => t,
        Err(e) => return (vec![], vec![], Some(e.into())),
    };
    let round = new_round();
    let op = crate::timers::PREVIEW_HANDOFF_WAIT.begin(None);
    let mut last = Seen::default();
    loop {
        match post_local(port, &token, HANDOFF_PATH, &json!({"round": round})) {
            Ok(v) => last = Seen::of(&v),
            Err(e) => last.note = Some(e),
        }
        if last.all_done() || !op.tick() {
            break;
        }
    }
    let missing = last.missing();
    (last.asked.into_iter().collect(), missing, last.note)
}

/// One answer of the preview's handoff route.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Seen {
    pub asked: BTreeSet<String>,
    pub waiting: BTreeSet<String>,
    pub done: BTreeSet<String>,
    pub note: Option<String>,
}

impl Seen {
    pub fn of(v: &Value) -> Self {
        let set = |k: &str| -> BTreeSet<String> {
            v[k].as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(str::to_owned))
                        .collect()
                })
                .unwrap_or_default()
        };
        Seen {
            asked: set("asked"),
            waiting: set("waiting"),
            done: set("done"),
            note: None,
        }
    }

    /// Every running seat was asked and said its handoff is written.
    pub fn all_done(&self) -> bool {
        self.waiting.is_empty() && self.asked.iter().all(|s| self.done.contains(s))
    }

    /// The seats that did not say so: asked and not done, and not yet asked.
    pub fn missing(&self) -> Vec<String> {
        self.asked
            .iter()
            .filter(|s| !self.done.contains(*s))
            .chain(self.waiting.iter())
            .cloned()
            .collect()
    }
}

fn new_round() -> String {
    let mut b = [0u8; 8];
    if getrandom::fill(&mut b).is_err() {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        return format!("{n:032x}");
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// The preview's token file, read as untrusted (5d33a L1): never through a link, a plain file,
/// at most [`TOKEN_MAX`] bytes, visible ASCII only.
pub fn read_token(path: &Path) -> Result<String, &'static str> {
    use std::io::Read;
    let mut o = std::fs::OpenOptions::new();
    o.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.custom_flags(nix::libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        if !std::fs::symlink_metadata(path).is_ok_and(|m| !m.file_type().is_symlink()) {
            return Err("token_not_a_file");
        }
    }
    let f = o.open(path).map_err(|_| "token_not_read")?;
    let m = f.metadata().map_err(|_| "token_not_read")?;
    if !m.is_file() {
        return Err("token_not_a_file");
    }
    if m.len() > TOKEN_MAX {
        return Err("token_too_long");
    }
    let mut text = String::new();
    f.take(TOKEN_MAX + 1)
        .read_to_string(&mut text)
        .map_err(|_| "token_not_read")?;
    let t = text.trim();
    if t.is_empty() || !t.bytes().all(|b| b.is_ascii_graphic()) {
        return Err("token_bad");
    }
    Ok(t.to_owned())
}

/// POST `body` to `path` of the preview on 127.0.0.1:`port` (only there); its JSON answer.
fn post_local(port: u16, token: &str, path: &str, body: &Value) -> Result<Value, String> {
    use std::io::{Read, Write};
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(2))
        .map_err(|e| format!("preview_unreachable {e}"))?;
    s.set_read_timeout(Some(Duration::from_secs(10)))
        .map_err(|e| e.to_string())?;
    let b = body.to_string();
    write!(
        s,
        "POST {path} HTTP/1.1\r\nHost: {addr}\r\nAuthorization: Bearer {token}\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{b}",
        b.len()
    )
    .map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    s.take(65_536)
        .read_to_end(&mut raw)
        .map_err(|e| e.to_string())?;
    answer_of(&raw)
}

/// A plain 200 JSON answer's body; anything else says what it was.
pub fn answer_of(raw: &[u8]) -> Result<Value, String> {
    let i = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("preview_answer_bad")?;
    let head = std::str::from_utf8(&raw[..i]).map_err(|_| "preview_answer_bad")?;
    if head.to_ascii_lowercase().contains("chunked") {
        return Err("preview_answer_chunked".into());
    }
    if !head.starts_with("HTTP/1.1 200") {
        return Err(format!(
            "preview_answered {}",
            head.lines()
                .next()
                .unwrap_or("")
                .chars()
                .take(40)
                .collect::<String>()
        ));
    }
    serde_json::from_slice(&raw[i + 4..]).map_err(|_| "preview_answer_bad".into())
}

/// The preview's handoff round now: which seats were typed the words, when it began.
struct Round {
    id: String,
    began: i64,
    asked: BTreeSet<String>,
}

static ROUND: Mutex<Option<Round>> = Mutex::new(None);

fn valid_round(s: &str) -> bool {
    (8..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_hexdigit())
}

/// `POST /api/v1/preview/handoff` `{round}` (inside a preview, its start-up token only): type
/// [`HANDOFF_WORDS`] to each running seat not yet typed this round (between turns), and say who
/// was asked, who still waits for the words, and who wrote its handoff since the round began.
pub async fn handoff(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Json(body): Json<Value>,
) -> Response {
    if !crate::preview::is_preview() {
        return refuse(
            StatusCode::NOT_FOUND,
            "not_a_preview",
            "only a preview hands off",
        );
    }
    if !matches!(c, Caller::StartupToken) {
        return refuse(
            StatusCode::FORBIDDEN,
            "handoff_parent_only",
            "the parent asks",
        );
    }
    let Some(round) = body
        .as_object()
        .filter(|o| o.len() == 1)
        .and_then(|o| o.get("round"))
        .and_then(Value::as_str)
        .filter(|r| valid_round(r))
        .map(str::to_owned)
    else {
        return refuse(StatusCode::BAD_REQUEST, "round_bad", "send {round}");
    };
    let sessions = st.sessions.clone();
    let got = tokio::task::spawn_blocking(move || type_round(&sessions, &round)).await;
    match got {
        Ok(v) => Json(v).into_response(),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "handoff failed",
        ),
    }
}

fn type_round(sessions: &crate::sessions::Sessions, round: &str) -> Value {
    let now = crate::revive::unix_of(&crate::sessions::now_rfc3339()).unwrap_or(0);
    let mut g = lock(&ROUND);
    if g.as_ref().is_none_or(|r| r.id != round) {
        *g = Some(Round {
            id: round.to_owned(),
            began: now,
            asked: BTreeSet::new(),
        });
    }
    let Some(r) = g.as_mut() else {
        return json!({"asked": [], "waiting": [], "done": []});
    };
    let mut waiting = Vec::new();
    if let Ok(line) = crate::seat_input::user_line(HANDOFF_WORDS) {
        for seat in sessions.live_seats() {
            if r.asked.contains(&seat) {
                continue;
            }
            match sessions.write_input_between_turns(&seat, &line) {
                Ok(_) => {
                    r.asked.insert(seat);
                }
                Err(_) => waiting.push(seat),
            }
        }
    }
    let done = done_since(&crate::seat_now::board().handoffs(), r.began);
    json!({"asked": r.asked, "waiting": waiting, "done": done})
}

/// The seats whose handoff note was written at or after `began` (unix seconds).
pub fn done_since(h: &BTreeMap<String, crate::seat_now::Handoff>, began: i64) -> Vec<String> {
    h.iter()
        .filter(|(_, x)| crate::revive::unix_of(&x.at).is_some_and(|t| t >= began))
        .map(|(k, _)| k.clone())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::seat_now::Handoff;

    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    /// PW1: only the terminal's start-up token asks; a seat or a page is refused, so is an ask
    /// made inside a preview, a field other than sha256 and a sha that is not 64 hex.
    #[test]
    fn only_the_start_up_token_asks_and_only_with_a_sha() {
        let ok = json!({"sha256": SHA});
        assert_eq!(ask_problem(&Caller::StartupToken, false, &ok), None);
        assert_eq!(
            ask_problem(&Caller::Anonymous, false, &ok).map(|x| x.1),
            Some("preview_swap_terminal_only"),
            "reason: anonymous_asked"
        );
        assert_eq!(
            ask_problem(&Caller::StartupToken, true, &ok).map(|x| x.1),
            Some("in_preview")
        );
        assert_eq!(
            ask_problem(
                &Caller::StartupToken,
                false,
                &json!({"sha256": SHA, "path": "/x"})
            )
            .map(|x| x.1),
            Some("unknown_field"),
            "reason: a_path_was_taken"
        );
        assert_eq!(
            ask_problem(&Caller::StartupToken, false, &json!({"sha256": "abc"})).map(|x| x.1),
            Some("sha256_bad")
        );
    }

    /// PW2: the asked sha must be the folder's build and a ready line of its record.
    #[test]
    fn only_the_ready_build_in_the_folder_goes_in() {
        let ready = vec![SHA.to_owned()];
        assert!(build_matches(Some(SHA), &ready, SHA));
        assert!(
            !build_matches(Some(SHA), &[], SHA),
            "reason: a_build_not_ready_went_in"
        );
        let other = "b".repeat(64);
        assert!(
            !build_matches(Some(&other), &ready, SHA),
            "reason: the_folder_holds_another_build"
        );
        assert!(!build_matches(None, &ready, SHA));
    }

    /// PW3 / PW4: the swap waits while a seat has not said its handoff is written or was not
    /// typed the words yet; the ones left are named, asked and waiting both.
    #[test]
    fn the_swap_waits_for_each_seats_handoff_and_names_who_did_not() {
        let s = Seen::of(&json!({"asked": ["a", "b"], "waiting": ["c"], "done": ["a"]}));
        assert!(!s.all_done(), "reason: swapped_before_the_handoffs");
        assert_eq!(s.missing(), vec!["b".to_owned(), "c".to_owned()]);
        let s = Seen::of(&json!({"asked": ["a", "b"], "waiting": [], "done": ["a"]}));
        assert!(!s.all_done(), "reason: swapped_before_b_wrote_its_handoff");
        let s = Seen::of(&json!({"asked": ["a", "b"], "waiting": [], "done": ["a", "b"]}));
        assert!(s.all_done());
        assert!(s.missing().is_empty());
        let s = Seen::of(&json!({"asked": [], "waiting": ["c"], "done": []}));
        assert!(
            !s.all_done(),
            "reason: a_seat_not_yet_typed_was_not_waited_for"
        );
        // an old note does not count for this round
        let mut h = BTreeMap::new();
        let at = |s: &str| Handoff {
            path: "/h.md".into(),
            at: s.into(),
        };
        h.insert("old".to_owned(), at("2026-11-21T00:00:00Z"));
        h.insert("new".to_owned(), at("2026-11-21T00:10:00Z"));
        let began = crate::revive::unix_of("2026-11-21T00:05:00Z").unwrap();
        assert_eq!(done_since(&h, began), vec!["new".to_owned()]);
    }

    /// PW9 (5d33a L1): the preview's token file is not read through a link, nor when long or
    /// not plain ASCII.
    #[test]
    fn the_previews_token_file_is_read_as_untrusted() {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("aibd.token");
        std::fs::write(&f, "tok-123\n").unwrap();
        assert_eq!(read_token(&f).as_deref(), Ok("tok-123"));
        std::fs::write(&f, "x".repeat(300)).unwrap();
        assert_eq!(read_token(&f), Err("token_too_long"));
        std::fs::write(&f, "a b\u{7}").unwrap();
        assert_eq!(read_token(&f), Err("token_bad"));
        #[cfg(unix)]
        {
            let secret = d.path().join("secret");
            std::fs::write(&secret, "not-for-you").unwrap();
            let link = d.path().join("link.token");
            std::os::unix::fs::symlink(&secret, &link).unwrap();
            assert!(read_token(&link).is_err(), "reason: read_through_a_link");
        }
        assert!(read_token(d.path()).is_err(), "reason: a_folder_was_read");
    }

    /// PW7: the words typed are the fixed ones; the route takes a round and nothing else.
    #[test]
    fn the_handoff_words_are_fixed_and_a_round_is_all_it_takes() {
        assert!(HANDOFF_WORDS.contains("aib handoff"));
        assert!(valid_round("0123456789abcdef"));
        assert!(!valid_round("short"));
        assert!(!valid_round("0123456789abcdeg"));
        assert!(crate::seat_input::user_line(HANDOFF_WORDS).is_ok());
    }

    #[test]
    fn only_a_plain_200_json_answer_is_read() {
        let ok = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n{}";
        assert_eq!(answer_of(ok), Ok(json!({})));
        assert!(answer_of(b"HTTP/1.1 403 Forbidden\r\n\r\n{}").is_err());
        assert!(
            answer_of(b"HTTP/1.1 200 OK\r\ntransfer-encoding: chunked\r\n\r\n2\r\n{}").is_err()
        );
    }

    /// PW5: a swap that ended less than a minute ago keeps the next one out.
    #[test]
    fn a_swap_cools_for_a_minute_after_it_ended() {
        let id = "pv_cool_test";
        assert!(!cooling(id));
        lock(&ENDED).insert(id.to_owned(), Instant::now());
        assert!(cooling(id), "reason: no_cooling");
        lock(&ENDED).insert(
            id.to_owned(),
            Instant::now() - COOL - Duration::from_secs(1),
        );
        assert!(!cooling(id));
    }
}
