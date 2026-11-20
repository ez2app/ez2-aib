//! G4 (DESIGN-trusted-g1g4 §1, 5d33a BASELINE-trusted-seat §1 G4): a trusted seat reads a pull
//! request, its checks, or a workflow run of the one repo, through aibd.
//!
//! `POST /api/v1/act` `{"type":"gh_view","what":"pr"|"checks"|"run","number":…}` with the seat's
//! own token. The seat gets no new permission: no network, no gh login, no token. aibd runs
//! `gh` itself, with an argv it builds whole ([`argv`]: three read-only shapes, nothing passed
//! through), for the repo of [`aib_drive::source::repo`] (the body cannot name one), in its own
//! data folder (not inside any repo), and gives the JSON back cleaned and cut at [`OUT_MAX`].
//!
//! Off unless `"trusted_seats": true` in `<data>/bridge.json` (403 `trusted_seats_off`), and
//! then only for a seat that is trusted now ([`crate::trusted_seats`], read at every act): not a dgrs
//! seat, not while a user folder page serves `/` (N8). One ask per seat every [`SEAT_GAP`], and
//! [`ALL_MAX`] a minute for the machine; `gh` is killed at [`crate::timers::GH_VIEW_WAIT`]'s
//! end. Journals `seat.gh_view {seat, what, number, rc}`: no path, no host.
use std::collections::{HashMap, VecDeque};
use std::io::Read;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::guard::request_id;

pub const KIND: &str = "gh_view";
pub const EVENT: &str = "seat.gh_view";

/// The most of `gh`'s answer given back, in bytes.
pub const OUT_MAX: usize = 64 * 1024;
/// One ask per seat in this long.
pub const SEAT_GAP: Duration = Duration::from_secs(10);
/// Asks of every seat together in [`ALL_WINDOW`].
pub const ALL_MAX: usize = 30;
pub const ALL_WINDOW: Duration = Duration::from_secs(60);

/// The name of aibd's own setting for another gh.
pub const BIN_ENV: &str = "AIBD_GH_BIN";

/// Another gh to run, when the setting names one: an absolute path of a regular file (5d33a
/// PREREAD-ghview L1: a bare name would be looked up on PATH). Anything else counts as not set.
pub fn override_of(setting: Option<&str>) -> Option<std::path::PathBuf> {
    let p = std::path::PathBuf::from(setting?);
    (p.is_absolute() && std::fs::metadata(&p).is_ok_and(|m| m.is_file())).then_some(p)
}

/// The program run: `gh` on aibd's own PATH, or the one [`BIN_ENV`] names in aibd's own
/// environment (which a seat's act cannot set).
pub fn program() -> std::ffi::OsString {
    override_of(std::env::var(BIN_ENV).ok().as_deref())
        .map_or_else(|| "gh".into(), std::path::PathBuf::into_os_string)
}

/// Said once at start, so another gh is never run silently (5d33a PREREAD-ghview L1).
pub fn log_override() {
    match std::env::var(BIN_ENV).ok() {
        None => {}
        Some(set) => match override_of(Some(&set)) {
            Some(p) => tracing::warn!(gh = %p.display(), "gh_view runs the gh AIBD_GH_BIN names"),
            None => tracing::warn!(
                "AIBD_GH_BIN is not an absolute path of a regular file: gh_view runs gh from PATH"
            ),
        },
    }
}

pub fn is_seat_act(body: &Value) -> bool {
    body.get("type").and_then(Value::as_str) == Some(KIND)
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// What a body asks for: `(what, number)`, or the refusal's code.
pub fn parse(body: &Value) -> Result<(&'static str, String), &'static str> {
    let fields = ["type", "what", "number"];
    if !body
        .as_object()
        .is_some_and(|o| o.keys().all(|k| fields.contains(&k.as_str())))
    {
        return Err("gh_view_fields");
    }
    let what = match body.get("what").and_then(Value::as_str) {
        Some("pr") => "pr",
        Some("checks") => "checks",
        Some("run") => "run",
        _ => return Err("gh_view_what"),
    };
    let number = match body.get("number") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) if n.is_u64() => n.to_string(),
        _ => return Err("gh_view_number"),
    };
    // 1–9 digits, no leading zero: never a flag, a branch name or a URL
    if number.is_empty()
        || number.len() > 9
        || number.starts_with('0')
        || !number.bytes().all(|b| b.is_ascii_digit())
    {
        return Err("gh_view_number");
    }
    Ok((what, number))
}

/// `owner/name` as gh takes it after `-R`: two parts of `[A-Za-z0-9._-]`, neither starting
/// with `-` or `.` (aibd's own setting, still never a flag or a host).
pub fn repo_ok(repo: &str) -> bool {
    let part = |p: &str| {
        !p.is_empty()
            && p.len() <= 100
            && !p.starts_with(['-', '.'])
            && p.bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
    };
    matches!(repo.split_once('/'), Some((o, n)) if part(o) && part(n))
}

/// gh's whole argv for one ask. Only these three shapes exist.
pub fn argv(what: &str, number: &str, repo: &str) -> Option<Vec<String>> {
    let (sub, verb, fields) = match what {
        "pr" => (
            "pr",
            "view",
            "number,title,state,isDraft,baseRefName,headRefName,headRefOid,mergeable,reviewDecision,labels,url",
        ),
        "checks" => ("pr", "checks", "name,state,bucket,link"),
        "run" => ("run", "view", "status,conclusion,headSha,jobs"),
        _ => return None,
    };
    Some(
        [sub, verb, number, "-R", repo, "--json", fields]
            .iter()
            .map(|s| (*s).to_owned())
            .collect(),
    )
}

/// Names never handed to gh from aibd's environment: they would point it at another repo or
/// host.
pub const ENV_DROP: &[&str] = &["GH_REPO", "GH_HOST", "GH_ENTERPRISE_TOKEN"];
/// Set for every run: no prompt, no update notice, no colour.
pub const ENV_SET: &[(&str, &str)] = &[
    ("GH_PROMPT_DISABLED", "1"),
    ("GH_NO_UPDATE_NOTIFIER", "1"),
    ("NO_COLOR", "1"),
];

fn command(prog: &std::ffi::OsStr, args: &[String], cwd: &std::path::Path) -> Command {
    let mut c = Command::new(prog);
    c.args(args).current_dir(cwd);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // its own group: at the limit the whole group is killed (5d33a PREREAD-ghview L2)
        c.process_group(0);
    }
    for k in ENV_DROP {
        c.env_remove(k);
    }
    for (k, v) in ENV_SET {
        c.env(k, v);
    }
    c
}

/// Whether gh's end is looked for without reaping it, where the platform can.
const LOOK_WITHOUT_REAPING: bool = true;

/// Has gh ended? `None`: still running; `Some(code)`: ended (no code: by a signal). Where the
/// platform can, it is seen without being reaped (5d33a REVIEW-ghview L3): while gh is an
/// unreaped child its pid, which is its group's number, cannot be given to another process,
/// so the group kill at the limit never reaches a stranger.
fn ended_unreaped(child: &mut std::process::Child) -> Option<Option<i32>> {
    if !LOOK_WITHOUT_REAPING {
        return plain_wait(child);
    }
    #[cfg(target_os = "macos")]
    {
        use aib_sys::{ChildExit, ExitState};
        match aib_sys::Macos.peek(child.id()) {
            Ok(ExitState::Running) => return None,
            Ok(ExitState::Exited(c)) => return Some(Some(c)),
            Ok(ExitState::Signaled(_)) => return Some(None),
            Err(_) => {}
        }
    }
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        use nix::sys::wait::{waitid, Id, WaitPidFlag, WaitStatus};
        if let Ok(pid) = i32::try_from(child.id()) {
            let flags = WaitPidFlag::WEXITED | WaitPidFlag::WNOHANG | WaitPidFlag::WNOWAIT;
            match waitid(Id::Pid(nix::unistd::Pid::from_raw(pid)), flags) {
                Ok(WaitStatus::StillAlive) => return None,
                Ok(WaitStatus::Exited(_, c)) => return Some(Some(c)),
                Ok(WaitStatus::Signaled(..)) => return Some(None),
                _ => {}
            }
        }
    }
    // elsewhere, or when the look failed
    plain_wait(child)
}

/// The plain look: it reaps a gh that has ended.
fn plain_wait(child: &mut std::process::Child) -> Option<Option<i32>> {
    match child.try_wait() {
        Ok(Some(s)) => Some(s.code()),
        Ok(None) => None,
        Err(_) => Some(None),
    }
}

/// Kill everything in gh's group (it was started as its own), then gh, then reap it.
fn kill_all(child: &mut std::process::Child) {
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

/// Run gh to its end or to the timer's; `(exit code, stdout up to OUT_MAX + 1 bytes)`. No code
/// = it did not start, or it or something holding its output was still there at the limit:
/// then its group is killed and the answer is not waited for (5d33a PREREAD-ghview L2).
fn run(cmd: Command) -> (Option<i32>, Vec<u8>) {
    run_timed(cmd, &crate::timers::GH_VIEW_WAIT, None)
}

/// [`run`] for any program aibd runs for a bounded time (G2's git and its leak scan): `timer`
/// is the bound, `input` what the program reads on stdin (none: closed).
pub(crate) fn run_timed(
    mut cmd: Command,
    timer: &'static crate::timers::Timer,
    input: Option<Vec<u8>>,
) -> (Option<i32>, Vec<u8>) {
    cmd.stdin(if input.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    })
    .stdout(Stdio::piped())
    .stderr(Stdio::null());
    let Ok(mut child) = cmd.spawn() else {
        return (None, Vec::new());
    };
    if let (Some(bytes), Some(mut w)) = (input, child.stdin.take()) {
        // a few lines; the pipe takes them without the reader (dropped here: stdin closes)
        let _ = std::io::Write::write_all(&mut w, &bytes);
    }
    // read while it runs, so a long answer cannot fill the pipe and hold the child; one byte
    // past the cap tells a cut answer from a whole one
    let reader = child.stdout.take().map(|mut out| {
        std::thread::spawn(move || {
            let mut b = Vec::new();
            let _ = out.by_ref().take(OUT_MAX as u64 + 1).read_to_end(&mut b);
            // the rest is read and dropped: gh ends by itself, not on a closed pipe
            let _ = std::io::copy(&mut out, &mut std::io::sink());
            b
        })
    });
    let op = timer.begin(None);
    let mut ended: Option<Option<i32>> = None;
    loop {
        if ended.is_none() {
            ended = ended_unreaped(&mut child);
        }
        // the output is whole only once every holder of the pipe has let go
        let read = reader
            .as_ref()
            .is_none_or(std::thread::JoinHandle::is_finished);
        if ended.is_some() && read {
            break;
        }
        if !op.tick() {
            kill_all(&mut child);
            return (None, Vec::new());
        }
    }
    // ended and nothing holds its output: reaped here, after the last look
    let _ = child.wait();
    let out = reader.and_then(|r| r.join().ok()).unwrap_or_default();
    (ended.flatten(), out)
}

/// gh's answer as it is given back: cut at [`OUT_MAX`] bytes (on a character), cleaned the way
/// a letter's words are; `(text, cut)`.
pub fn shown(raw: &[u8]) -> (String, bool) {
    let cut = raw.len() > OUT_MAX;
    let s = String::from_utf8_lossy(&raw[..raw.len().min(OUT_MAX)]);
    (crate::dm_import::clean(&s), cut)
}

/// Who asked when.
#[derive(Default)]
pub struct Rate {
    seat: HashMap<String, Instant>,
    all: VecDeque<Instant>,
}

impl Rate {
    /// Count one ask of `seat` at `now`, or name the limit it is over. A refused ask is not
    /// counted.
    pub fn take(&mut self, seat: &str, now: Instant) -> Result<(), &'static str> {
        while self
            .all
            .front()
            .is_some_and(|t| now.duration_since(*t) >= ALL_WINDOW)
        {
            self.all.pop_front();
        }
        self.seat.retain(|_, t| now.duration_since(*t) < SEAT_GAP);
        if self.seat.contains_key(seat) {
            return Err("gh_view_seat_rate");
        }
        if self.all.len() >= ALL_MAX {
            return Err("gh_view_rate");
        }
        self.seat.insert(seat.to_owned(), now);
        self.all.push_back(now);
        Ok(())
    }
}

static RATE: Mutex<Option<Rate>> = Mutex::new(None);

fn rate_take(seat: &str) -> Result<(), &'static str> {
    RATE.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get_or_insert_with(Rate::default)
        .take(seat, Instant::now())
}

/// Why a seat may not ask now, by what is known of it: the switch, a user page serving `/`, a
/// dgrs seat (or one whose mode is not known), not trusted now.
pub fn gate(
    switch_on: bool,
    user_page: bool,
    mode: Option<&str>,
    trusted_until: Option<u64>,
    now: u64,
) -> Result<(), &'static str> {
    if !switch_on {
        return Err("trusted_seats_off");
    }
    if user_page {
        return Err("gh_view_user_page");
    }
    if !matches!(mode, Some("safe" | "work")) {
        return Err("gh_view_mode");
    }
    if !crate::trusted_seats::holds(trusted_until, now) {
        return Err("seat_not_trusted");
    }
    Ok(())
}

fn gate_words(code: &str) -> &'static str {
    match code {
        "trusted_seats_off" => {
            "trusted seats are off on this machine (trusted_seats in bridge.json)"
        }
        "gh_view_user_page" => "not while a user folder page serves this aibd",
        "gh_view_mode" => "only a safe or work seat may ask",
        _ => "this seat is not trusted now: the owner's fingerprint gives it, for a time",
    }
}

fn parse_words(code: &str) -> &'static str {
    match code {
        "gh_view_fields" => "body must be {type, what, number}",
        "gh_view_what" => "what must be pr, checks or run",
        _ => "number is 1 to 9 digits",
    }
}

/// The act, for the seat a token names.
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    let Some(data_dir) = crate::bridge_cfg::dir() else {
        return refuse(
            StatusCode::FORBIDDEN,
            "trusted_seats_off",
            gate_words("trusted_seats_off"),
        );
    };
    let (store, sid, dir) = (st.store.clone(), seat_id.to_owned(), data_dir.clone());
    // read now, never kept (5d33a PREREAD-trusted-g1g4 Q1): the switch, the page, the seat's
    // mode and its trust
    let known = tokio::task::spawn_blocking(move || {
        let switch_on = crate::bridge_cfg::load(&dir).trusted_seats;
        let user_page = crate::static_files::web_source() == "user";
        let mode = store.seat_meta(&sid).ok().flatten().and_then(|m| m.mode);
        let until = crate::trusted_seats::until(&dir, &sid);
        (switch_on, user_page, mode, until)
    })
    .await;
    let Ok((switch_on, user_page, mode, until)) = known else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the seat could not be read",
        );
    };
    if let Err(code) = gate(
        switch_on,
        user_page,
        mode.as_deref(),
        until,
        crate::pair_carry::now_unix(),
    ) {
        return refuse(StatusCode::FORBIDDEN, code, gate_words(code));
    }
    let (what, number) = match parse(&body) {
        Ok(p) => p,
        Err(code) => return refuse(StatusCode::BAD_REQUEST, code, parse_words(code)),
    };
    let repo = aib_drive::source::repo();
    let Some(args) = argv(what, &number, &repo).filter(|_| repo_ok(&repo)) else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "gh_view_repo",
            "this aibd's repo setting is not owner/name",
        );
    };
    if let Err(code) = rate_take(seat_id) {
        return refuse(
            StatusCode::TOO_MANY_REQUESTS,
            code,
            "one ask per seat every 10 s, 30 a minute for the machine",
        );
    }
    let prog = program();
    let ran = tokio::task::spawn_blocking(move || run(command(&prog, &args, &data_dir))).await;
    let (rc, raw) = ran.unwrap_or((None, Vec::new()));
    crate::seats::side_event(
        &st,
        "seat_gh_view",
        EVENT,
        json!({ "seat": seat_id, "what": what, "number": number, "rc": rc }),
    )
    .await;
    let Some(rc) = rc else {
        return refuse(
            StatusCode::GATEWAY_TIMEOUT,
            "gh_view_no_answer",
            "gh did not start, or did not answer in 20 s",
        );
    };
    let (text, cut) = shown(&raw);
    // gh's JSON when it is whole JSON; cut or not JSON ⇒ the cleaned text only
    let parsed = (!cut)
        .then(|| serde_json::from_str::<Value>(&text).ok())
        .flatten();
    Json(json!({
        "done": rc == 0,
        "rc": rc,
        "what": what,
        "number": number,
        "json": parsed,
        "text": text,
        "cut": cut,
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_three_whats_and_a_plain_number_are_asked() {
        let ok = json!({"type": KIND, "what": "pr", "number": "869"});
        assert_eq!(parse(&ok), Ok(("pr", "869".to_owned())));
        let n = json!({"type": KIND, "what": "run", "number": 123456789});
        assert_eq!(parse(&n), Ok(("run", "123456789".to_owned())));
        for what in ["api", "pr view", "", "PR", "merge"] {
            let b = json!({"type": KIND, "what": what, "number": "1"});
            assert_eq!(parse(&b), Err("gh_view_what"), "reason: what_passed_{what}");
        }
        for number in [
            json!("12a"),
            json!("-1"),
            json!("--web"),
            json!(""),
            json!("0"),
            json!("012"),
            json!("1234567890"),
            json!(" 12"),
            json!("https://example.com/pull/1"),
            json!(-5),
            json!(1.5),
            json!(null),
            json!(["1"]),
        ] {
            let b = json!({"type": KIND, "what": "pr", "number": number});
            assert_eq!(
                parse(&b),
                Err("gh_view_number"),
                "reason: number_passed_{number}"
            );
        }
        // the body names no repo, host or flag
        for extra in ["repo", "R", "host", "args", "json"] {
            let mut b = ok.clone();
            b[extra] = json!("other/repo");
            assert_eq!(
                parse(&b),
                Err("gh_view_fields"),
                "reason: field_passed_{extra}"
            );
        }
    }

    #[test]
    fn the_argv_is_one_of_three_read_only_shapes() {
        let pr = argv("pr", "7", "o/n").unwrap();
        assert_eq!(pr[..6], ["pr", "view", "7", "-R", "o/n", "--json"]);
        assert!(pr[6].contains("headRefOid") && pr[6].contains("mergeable"));
        let checks = argv("checks", "7", "o/n").unwrap();
        assert_eq!(
            checks,
            [
                "pr",
                "checks",
                "7",
                "-R",
                "o/n",
                "--json",
                "name,state,bucket,link"
            ]
        );
        let run = argv("run", "99", "o/n").unwrap();
        assert_eq!(
            run,
            [
                "run",
                "view",
                "99",
                "-R",
                "o/n",
                "--json",
                "status,conclusion,headSha,jobs"
            ]
        );
        for what in ["api", "merge", "pr merge", "release", ""] {
            assert!(argv(what, "1", "o/n").is_none(), "reason: shape_for_{what}");
        }
        for a in [&pr, &checks, &run] {
            assert_eq!(a.len(), 7, "reason: extra_argument");
            assert!(
                !a.iter().any(|x| matches!(
                    x.as_str(),
                    "api" | "merge" | "close" | "edit" | "comment" | "--web" | "--log" | "rerun"
                )),
                "reason: write_or_log_word"
            );
        }
    }

    #[test]
    fn the_repo_setting_is_owner_slash_name_only() {
        for r in ["ez2app/ez2-aib", "a/b", "A_1/x.y-z"] {
            assert!(repo_ok(r), "reason: refused_{r}");
        }
        for r in [
            "",
            "ez2-aib",
            "a/b/c",
            "-R/x",
            "a/-x",
            "a/.git",
            "example.com/a/b",
            "a b/c",
            "a/b c",
            "https://x/y",
            "a/",
            "/b",
        ] {
            assert!(!repo_ok(r), "reason: took_{r}");
        }
    }

    #[test]
    fn a_long_answer_is_cut_and_cleaned() {
        let (t, cut) = shown(b"{\"a\":1}");
        assert_eq!((t.as_str(), cut), ("{\"a\":1}", false));
        let long = vec![b'x'; OUT_MAX + 1];
        let (t, cut) = shown(&long);
        assert!(cut, "reason: long_not_cut");
        assert_eq!(t.len(), OUT_MAX, "reason: cut_length");
        let (t, cut) = shown("a\u{1b}[31mb\u{200b}c\u{202e}d".as_bytes());
        assert_eq!((t.as_str(), cut), ("abcd", false), "reason: not_cleaned");
    }

    #[test]
    fn one_ask_per_seat_and_thirty_a_minute() {
        let mut r = Rate::default();
        let t0 = Instant::now();
        assert_eq!(r.take("s1", t0), Ok(()));
        assert_eq!(
            r.take("s1", t0 + Duration::from_secs(9)),
            Err("gh_view_seat_rate"),
            "reason: seat_asked_twice"
        );
        assert_eq!(r.take("s2", t0 + Duration::from_secs(9)), Ok(()));
        assert_eq!(r.take("s1", t0 + SEAT_GAP), Ok(()));
        let mut r = Rate::default();
        for i in 0..ALL_MAX {
            assert_eq!(
                r.take(&format!("s{i}"), t0),
                Ok(()),
                "reason: under_cap_{i}"
            );
        }
        assert_eq!(
            r.take("other", t0 + Duration::from_secs(59)),
            Err("gh_view_rate"),
            "reason: over_machine_cap"
        );
        assert_eq!(r.take("other", t0 + ALL_WINDOW), Ok(()));
    }

    #[test]
    fn only_a_trusted_safe_or_work_seat_asks_and_only_with_the_switch() {
        let now = 1_000;
        assert_eq!(gate(true, false, Some("work"), Some(now + 1), now), Ok(()));
        assert_eq!(gate(true, false, Some("safe"), Some(now + 1), now), Ok(()));
        assert_eq!(
            gate(false, false, Some("work"), Some(now + 1), now),
            Err("trusted_seats_off"),
            "reason: asked_with_switch_off"
        );
        assert_eq!(
            gate(true, true, Some("work"), Some(now + 1), now),
            Err("gh_view_user_page"),
            "reason: asked_under_user_page"
        );
        for mode in [Some("dgrs"), None, Some("")] {
            assert_eq!(
                gate(true, false, mode, Some(now + 1), now),
                Err("gh_view_mode"),
                "reason: asked_as_{mode:?}"
            );
        }
        for until in [None, Some(now), Some(now - 1), Some(0)] {
            assert_eq!(
                gate(true, false, Some("work"), until, now),
                Err("seat_not_trusted"),
                "reason: asked_with_trust_{until:?}"
            );
        }
    }

    #[test]
    fn gh_runs_without_the_names_that_point_it_elsewhere() {
        let c = command(
            std::ffi::OsStr::new("gh"),
            &["pr".to_owned()],
            std::path::Path::new("."),
        );
        let envs: Vec<_> = c
            .get_envs()
            .map(|(k, v)| {
                (
                    k.to_string_lossy().into_owned(),
                    v.map(|v| v.to_string_lossy().into_owned()),
                )
            })
            .collect();
        for k in ["GH_REPO", "GH_HOST", "GH_ENTERPRISE_TOKEN"] {
            assert!(
                envs.contains(&(k.to_owned(), None)),
                "reason: {k}_handed_on"
            );
        }
        for (k, v) in [
            ("GH_PROMPT_DISABLED", "1"),
            ("GH_NO_UPDATE_NOTIFIER", "1"),
            ("NO_COLOR", "1"),
        ] {
            assert!(
                envs.contains(&(k.to_owned(), Some(v.to_owned()))),
                "reason: {k}_not_set"
            );
        }
    }

    /// 5d33a PREREAD-ghview L1: another gh is an absolute path of a regular file, or it is not
    /// set.
    #[test]
    fn another_gh_is_an_absolute_regular_file_or_none() {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("gh");
        std::fs::write(&f, b"x").unwrap();
        assert_eq!(override_of(f.to_str()), Some(f.clone()));
        assert_eq!(override_of(None), None);
        for bad in ["", "gh", "./gh", "bin/gh", " ", "~/gh"] {
            assert_eq!(override_of(Some(bad)), None, "reason: took_{bad}");
        }
        assert_eq!(
            override_of(d.path().to_str()),
            None,
            "reason: took_a_folder"
        );
        assert_eq!(
            override_of(d.path().join("none").to_str()),
            None,
            "reason: took_a_missing_file"
        );
    }

    /// 5d33a PREREAD-ghview L2: a gh that ends but leaves a child holding its output does not
    /// hold the answer past the limit; the child is killed with it.
    #[cfg(unix)]
    #[test]
    fn a_child_left_holding_the_output_is_killed_at_the_limit() {
        let d = tempfile::tempdir().unwrap();
        let mark = d.path().join("alive");
        let script = format!(
            "(trap '' HUP; while :; do : > '{}'; sleep 1; done) & echo started; exit 0",
            mark.display()
        );
        let t = Instant::now();
        let (rc, out) = run(command(
            std::ffi::OsStr::new("sh"),
            &["-c".to_owned(), script],
            d.path(),
        ));
        assert_eq!(
            (rc, out.len()),
            (None, 0),
            "reason: answered_from_a_held_pipe"
        );
        assert!(
            t.elapsed() < crate::timers::GH_VIEW_WAIT.max_lifetime + Duration::from_secs(10),
            "reason: waited_past_the_limit"
        );
        // the left child no longer writes its mark
        let _ = std::fs::remove_file(&mark);
        let op = crate::timers::GH_VIEW_WAIT.begin(Some(Instant::now() + Duration::from_secs(2)));
        while op.tick() {}
        assert!(!mark.exists(), "reason: left_child_still_runs");
    }

    /// 5d33a REVIEW-ghview L3: a gh that has ended is seen without being reaped, so its pid
    /// (its group's number) stays its own until the group kill has gone out.
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    #[test]
    fn an_ended_gh_is_seen_without_being_reaped() {
        let mut child = Command::new("sh").args(["-c", "exit 3"]).spawn().unwrap();
        let op = crate::timers::GH_VIEW_WAIT.begin(Some(Instant::now() + Duration::from_secs(10)));
        let mut seen = None;
        while seen.is_none() && op.tick() {
            seen = ended_unreaped(&mut child);
        }
        assert_eq!(seen, Some(Some(3)), "reason: end_not_seen");
        let pid = nix::unistd::Pid::from_raw(i32::try_from(child.id()).unwrap());
        assert!(
            nix::sys::signal::kill(pid, None).is_ok(),
            "reason: reaped_before_the_group_kill"
        );
        assert_eq!(child.wait().unwrap().code(), Some(3));
        assert!(
            nix::sys::signal::kill(pid, None).is_err(),
            "reason: not_reaped_at_the_end"
        );
    }

    /// A program that prints more than the cap: one byte past it is kept (so the answer reads
    /// as cut) and the program still ends by itself.
    #[cfg(unix)]
    #[test]
    fn a_long_answer_is_read_to_the_cap_and_gh_still_ends() {
        let d = tempfile::tempdir().unwrap();
        let mut c = Command::new("sh");
        c.args(["-c", "head -c 70000 /dev/zero | tr '\\0' 'x'"])
            .current_dir(d.path());
        let (rc, out) = run(c);
        assert_eq!(rc, Some(0));
        assert_eq!(out.len(), OUT_MAX + 1, "reason: read_past_the_cap");
        assert!(shown(&out).1, "reason: long_not_cut");
    }
}
