//! N-webonly 第 4 件, the half between aibd and the page (7ebca 11-20, 519d0 21:33; cut agreed
//! with b4297 21:35): a page asks aibd for a receipt of one 40-hex head, sees where it stands
//! and withdraws it, and aibd reads the finished `.out` itself ([`crate::receipt_check`]).
//!
//! - Three act types of `POST /api/v1/act` (the built-in page a person paired; a user folder
//!   page or a carried pairing asks for nothing that runs on another machine):
//!   `receipt_request {seat_id, head, scope: "full"|"narrow", tests?: ["<crate>:<test file>"]}`,
//!   `receipt_read {run_id}`, `receipt_withdraw {run_id}`, `receipt_list {seat_id}` (a
//!   reloaded page finds its runs again). A request names a head and one of
//!   the two kinds of receipt; it carries no command, no path and no machine.
//! - The step that goes to a test machine is [`ReceiptRunner`]. aibd ships [`NoRunner`] (every
//!   request is refused `no_host`), so with this half alone nothing is run anywhere and no
//!   boundary moves. b4297's half goes behind the trait: the bundle and its list, the owner's
//!   fingerprint card bound to {machine, bundle sha256, run id, knife table sha256}, the
//!   trusted-hosts connection and the gate on the machine. It also says whether the head is the
//!   seat's own (`head_not_seat_head`): this module runs no git and opens no connection.
//! - A run is one entry of `<data>/receipt_runs.json` (the newest [`MAX_RUNS`]; written like
//!   the other lists there, this user only, never through a link): `asking` for the moment the
//!   runner is asked (no call to a runner is made with the file held), then `waiting_owner` → `running` → `done` (green) | `red`; `withdrawn` from either of the
//!   first two; `lost` when the runner says the machine went away. At most [`MAX_IN_FLIGHT`] of a seat are under way.
//! - Run ids are `web-<last 5 of the seat id>-<first 9 of the head>`, and `-2` … `-99` when
//!   that head is asked again (design r3; the gate checks the same shape).
//! - What a page is told is fixed words and numbers: state, reason codes, counts. Never a line
//!   of the receipt (its test output is written by the seat's own code).
//!
//! Like every local file the list is no defence against an AI on the same account: a green
//! here is a display, the merge gate reads CI (design r3 §5).

use crate::api::AppState;
use crate::guard::request_id;
use crate::receipt_check::Verdict;
use aib_store::NewEvent;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex, RwLock};

pub const FILE: &str = "receipt_runs.json";
pub const REQUEST: &str = "receipt_request";
pub const READ: &str = "receipt_read";
pub const WITHDRAW: &str = "receipt_withdraw";
/// `receipt_list {seat_id}`: the seat's runs under way and its latest [`LISTED`].
pub const LIST: &str = "receipt_list";
pub const LISTED: usize = 10;
/// A run id kept this long with no answer written was left by an aibd that stopped (5d33a N4).
pub const ASKING_SECS: i64 = 600;

/// A seat's runs under way at once.
pub const MAX_IN_FLIGHT: usize = 2;
/// The runs kept in the file (the oldest leave).
pub const MAX_RUNS: usize = 200;
/// The test files a narrow request may name.
pub const MAX_TESTS: usize = 8;
/// The largest file read, and the largest `.out` taken from a runner.
const MAX_FILE: u64 = 1024 * 1024;
pub const MAX_OUT: usize = 1024 * 1024;

/// Its run id is kept and the runner is being asked (a moment; a run left here by a crash is
/// withdrawn like any other).
pub const ASKING: &str = "asking";
pub const WAITING_OWNER: &str = "waiting_owner";
pub const RUNNING: &str = "running";
pub const DONE: &str = "done";
pub const RED: &str = "red";
pub const WITHDRAWN: &str = "withdrawn";
/// The machine went away while it ran (the runner says so: [`runner_lost`]).
pub const LOST: &str = "lost";

/// Why a runner refuses, as it may be said to a page. Any other word reads `refused`.
pub const REFUSALS: [&str; 10] = [
    "no_host",
    "receipt_not_allowed",
    "head_not_seat_head",
    "receipt_tool_changed",
    "host_changed",
    "disk_low",
    "toolchain_changed",
    "tree_refused",
    "owner_refused",
    "lost",
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Run {
    pub run_id: String,
    pub seat_id: String,
    pub head: String,
    /// `full` | `narrow`.
    pub scope: String,
    #[serde(default)]
    pub tests: Vec<String>,
    pub state: String,
    /// The machine's name as the runner said it (a nickname, never an address).
    #[serde(default)]
    pub host: Option<String>,
    /// How many knives the runner sent with it.
    #[serde(default)]
    pub knives_sent: u64,
    pub asked_at: String,
    #[serde(default)]
    pub ended_at: Option<String>,
    /// What aibd read in the `.out`, once there is one.
    #[serde(default)]
    pub verdict: Option<Verdict>,
    /// Why it ended with no `.out` read (one of [`REFUSALS`]: the owner said no, the machine
    /// went away, the gate refused the tree).
    #[serde(default)]
    pub why: Option<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct List {
    #[serde(default)]
    runs: Vec<Run>,
}

/// What a request asks for, checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ask {
    pub run_id: String,
    pub seat_id: String,
    pub head: String,
    pub narrow: bool,
    pub tests: Vec<String>,
}

/// What a runner did with a request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Start {
    /// It is on a machine's queue.
    Started { host: String, knives_sent: u64 },
    /// The owner's fingerprint card is out; it goes to a machine when that is answered.
    NeedsOwner { host: String, knives_sent: u64 },
    /// Not run, and why (one of [`REFUSALS`]).
    Refused(&'static str),
}

/// The step that leaves this machine. Its calls may block (they are made off the async threads).
pub trait ReceiptRunner: Send + Sync {
    fn start(&self, ask: &Ask) -> Start;
    /// The run's `.out` once it is finished and sealed; `None` while there is none.
    fn read(&self, run_id: &str) -> Option<Vec<u8>>;
    /// The run is withdrawn: take it off the queue or stop it.
    fn cancel(&self, run_id: &str);
}

/// The runner aibd has until one is set: nothing is run.
pub struct NoRunner;

impl ReceiptRunner for NoRunner {
    fn start(&self, _ask: &Ask) -> Start {
        Start::Refused("no_host")
    }
    fn read(&self, _run_id: &str) -> Option<Vec<u8>> {
        None
    }
    fn cancel(&self, _run_id: &str) {}
}

static RUNNER: RwLock<Option<Arc<dyn ReceiptRunner>>> = RwLock::new(None);
/// One request, read or withdrawal changes the file at a time.
static FILE_LOCK: Mutex<()> = Mutex::new(());

/// Sets the runner (b4297's half at start-up; a test's double).
pub fn set_runner(r: Arc<dyn ReceiptRunner>) {
    *RUNNER.write().unwrap_or_else(|e| e.into_inner()) = Some(r);
}

fn runner() -> Arc<dyn ReceiptRunner> {
    RUNNER
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
        .unwrap_or_else(|| Arc::new(NoRunner) as Arc<dyn ReceiptRunner>)
}

fn is_hex(s: &str, len: usize) -> bool {
    s.len() == len
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `<crate>:<test file>`, both of letters, digits, `_` and `-` (what receipt-run's RR_TESTS
/// takes): it is put on a cargo line on the test machine.
pub fn test_ok(t: &str) -> bool {
    let word = |w: &str| {
        !w.is_empty()
            && w.len() <= 64
            && !w.starts_with('-')
            && w.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    };
    t.split_once(':').is_some_and(|(c, f)| word(c) && word(f))
}

/// The seat's five characters in a run id: the last five of its id, when they are lowercase
/// letters or digits.
pub fn seat_code(seat_id: &str) -> Option<&str> {
    let at = seat_id.len().checked_sub(5)?;
    let code = seat_id.get(at..)?;
    code.bytes()
        .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
        .then_some(code)
}

/// The run id for this seat and head that no run in `runs` has yet.
pub fn run_id(seat_id: &str, head: &str, runs: &[Run]) -> Option<String> {
    let base = format!("web-{}-{}", seat_code(seat_id)?, head.get(..9)?);
    let taken = |id: &str| runs.iter().any(|r| r.run_id == id);
    if !taken(&base) {
        return Some(base);
    }
    (2..=99)
        .map(|n| format!("{base}-{n}"))
        .find(|id| !taken(id))
}

/// Whether `id` has the shape of a run id (what [`READ`] and [`WITHDRAW`] take).
pub fn run_id_ok(id: &str) -> bool {
    let mut parts = id.split('-');
    let shape = parts.next() == Some("web")
        && parts.next().is_some_and(|s| {
            s.len() == 5
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
        })
        && parts.next().is_some_and(|h| is_hex(h, 9));
    let again = match parts.next() {
        None => true,
        Some(n) => !n.starts_with('0') && n.parse::<u8>().is_ok_and(|n| (2..=99).contains(&n)),
    };
    shape && again && parts.next().is_none()
}

pub fn in_flight(r: &Run) -> bool {
    r.state == ASKING || r.state == WAITING_OWNER || r.state == RUNNING
}

/// The entry that keeps an [`Ask`]'s run id while the runner is asked.
pub fn reserved(ask: &Ask, at: &str) -> Run {
    Run {
        run_id: ask.run_id.clone(),
        seat_id: ask.seat_id.clone(),
        head: ask.head.clone(),
        scope: if ask.narrow { "narrow" } else { "full" }.to_owned(),
        tests: ask.tests.clone(),
        state: ASKING.to_owned(),
        host: None,
        knives_sent: 0,
        asked_at: at.to_owned(),
        ended_at: None,
        verdict: None,
        why: None,
    }
}

/// A request's body, checked against the runs there are: the [`Ask`], or the refusal's code.
pub fn ask_of(body: &Value, runs: &[Run]) -> Result<Ask, &'static str> {
    let text = |k: &str| body.get(k).and_then(Value::as_str).unwrap_or_default();
    let (seat_id, head) = (text("seat_id"), text("head"));
    if seat_id.is_empty() || seat_id.len() > 128 || seat_code(seat_id).is_none() {
        return Err("seat_id_invalid");
    }
    if !is_hex(head, 40) {
        return Err("bad_head");
    }
    let narrow = match text("scope") {
        "full" => false,
        "narrow" => true,
        _ => return Err("bad_scope"),
    };
    let tests: Vec<String> = match body.get("tests") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(a)) => a
            .iter()
            .map(|t| t.as_str().filter(|t| test_ok(t)).map(str::to_owned))
            .collect::<Option<_>>()
            .ok_or("bad_tests")?,
        Some(_) => return Err("bad_tests"),
    };
    // a narrow receipt names what it runs; a full one runs everything and names nothing
    if narrow == tests.is_empty() || tests.len() > MAX_TESTS {
        return Err("bad_tests");
    }
    if runs
        .iter()
        .filter(|r| r.seat_id == seat_id && in_flight(r))
        .count()
        >= MAX_IN_FLIGHT
    {
        return Err("receipt_in_flight");
    }
    let run_id = run_id(seat_id, head, runs).ok_or("receipt_asked_too_often")?;
    Ok(Ask {
        run_id,
        seat_id: seat_id.to_owned(),
        head: head.to_owned(),
        narrow,
        tests,
    })
}

/// The run a runner's answer makes of an [`Ask`]; a refusal makes none (its code, as a page
/// may be told it).
pub fn started(ask: &Ask, start: Start, at: &str) -> Result<Run, &'static str> {
    let (state, host, knives_sent) = match start {
        Start::Started { host, knives_sent } => (RUNNING, host, knives_sent),
        Start::NeedsOwner { host, knives_sent } => (WAITING_OWNER, host, knives_sent),
        Start::Refused(why) => return Err(said(why)),
    };
    Ok(Run {
        state: state.to_owned(),
        host: Some(nickname(&host)),
        knives_sent,
        ..reserved(ask, at)
    })
}

/// A runner's reason as a page may be told it: one of [`REFUSALS`], or `refused`.
pub fn said(why: &str) -> &'static str {
    REFUSALS
        .iter()
        .find(|r| **r == why)
        .copied()
        .unwrap_or("refused")
}

/// The owner answered the fingerprint card of a run that waits for it: yes and it runs, no and
/// it ends red `owner_refused`. A run in any other state is left as it is (`false`).
pub fn owner_said(run: &mut Run, ok: bool, at: &str) -> bool {
    if run.state != WAITING_OWNER {
        return false;
    }
    if ok {
        run.state = RUNNING.to_owned();
    } else {
        run.state = RED.to_owned();
        run.why = Some("owner_refused".to_owned());
        run.ended_at = Some(at.to_owned());
    }
    true
}

/// The runner says a run under way ended with no `.out` (the machine went away, the gate
/// refused the tree): `lost` for `lost`, red for any other reason. An ended run is left
/// (`false`).
pub fn runner_said(run: &mut Run, why: &str, at: &str) -> bool {
    if !in_flight(run) {
        return false;
    }
    let why = said(why);
    run.state = if why == "lost" { LOST } else { RED }.to_owned();
    run.why = Some(why.to_owned());
    run.ended_at = Some(at.to_owned());
    true
}

/// A machine's name as it is kept and shown: letters, digits, `-` and `_`, at most 32 (a
/// runner that hands an address or a path here gets it cut down to nothing useful).
pub fn nickname(host: &str) -> String {
    host.chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(32)
        .collect()
}

/// A run under way and its finished `.out`: read by aibd itself, `done` only when green. A
/// narrow receipt is never green ([`crate::receipt_check`]): it ends `red` with its reasons.
pub fn finished(run: &mut Run, out: &[u8], at: &str) {
    let under_way = in_flight(run);
    if !under_way {
        return;
    }
    let v = crate::receipt_check::check(out, &run.head, run.knives_sent);
    run.state = if v.green { DONE } else { RED }.to_owned();
    run.verdict = Some(v);
    run.ended_at = Some(at.to_owned());
}

/// Withdraws a run under way; one that ended stays as it ended.
pub fn withdrawn(run: &mut Run, at: &str) -> Result<(), &'static str> {
    if in_flight(run) {
        run.state = WITHDRAWN.to_owned();
        run.ended_at = Some(at.to_owned());
        Ok(())
    } else {
        Err("already_done")
    }
}

/// What a page is told of a run: words and numbers, never a line of the receipt.
pub fn view(r: &Run) -> Value {
    let v = r.verdict.as_ref();
    json!({
        "run_id": r.run_id,
        "seat_id": r.seat_id,
        "head": r.head,
        "scope": r.scope,
        "tests": r.tests,
        "state": r.state,
        "host": r.host,
        "asked_at": r.asked_at,
        "ended_at": r.ended_at,
        "green": v.is_some_and(|v| v.green),
        "narrow": r.scope == "narrow" || v.is_some_and(|v| v.narrow),
        "reasons": reasons_of(r),
        "tests_total": v.and_then(|v| v.tests),
        "knives": v.and_then(|v| v.knives),
        "knives_sent": r.knives_sent,
    })
}

/// Why a run is not green, as codes: what aibd read in its `.out`, or why it ended without one.
pub fn reasons_of(r: &Run) -> Vec<String> {
    match (&r.verdict, &r.why) {
        (Some(v), _) => v.reasons.clone(),
        (None, Some(why)) => vec![why.clone()],
        (None, None) => Vec::new(),
    }
}

/// A seat's runs for a page that was reloaded: every one under way and the latest ended, at
/// most [`LISTED`] ended, newest first. Views only ([`view`]).
pub fn listed(runs: &[Run], seat_id: &str) -> Vec<Value> {
    let mut ended = 0usize;
    runs.iter()
        .rev()
        .filter(|r| r.seat_id == seat_id)
        .filter(|r| {
            if in_flight(r) {
                return true;
            }
            ended += 1;
            ended <= LISTED
        })
        .map(view)
        .collect()
}

/// `runs` with every `asking` entry asked before `cutoff` ended `lost`: the aibd that kept its
/// run id stopped before the runner's answer was written (it would count as under way for
/// good). `cutoff` is a time as `asked_at` is written.
pub fn aged(mut runs: Vec<Run>, cutoff: &str) -> Vec<Run> {
    for r in &mut runs {
        if r.state == ASKING && r.asked_at.as_str() < cutoff {
            r.state = LOST.to_owned();
            r.why = Some("lost".to_owned());
            r.ended_at = Some(cutoff.to_owned());
        }
    }
    runs
}

/// The runs in `bytes` (anything malformed: none).
pub fn parse(bytes: &[u8]) -> Vec<Run> {
    serde_json::from_slice::<List>(bytes)
        .map(|l| l.runs)
        .unwrap_or_default()
}

/// `runs` with `run` added, the oldest beyond [`MAX_RUNS`] that are not under way dropped.
pub fn with(mut runs: Vec<Run>, run: Run) -> Vec<Run> {
    runs.push(run);
    while runs.len() > MAX_RUNS {
        match runs.iter().position(|r| !in_flight(r)) {
            Some(i) => {
                runs.remove(i);
            }
            None => break,
        }
    }
    runs
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    FILE_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// Whether a change to the file is under way now (a runner's double asks: no call to a runner
/// is made with the file held, 5d33a L2).
pub fn file_held() -> bool {
    FILE_LOCK.try_lock().is_err()
}

/// The runs on disk: a plain file only (a link there is not followed, 5d33a L1).
fn load() -> Vec<Run> {
    let Some(p) = crate::workspace::data_file(FILE) else {
        return Vec::new();
    };
    match std::fs::symlink_metadata(&p) {
        Ok(m) if m.is_file() && m.len() <= MAX_FILE => {
            let runs = std::fs::read(&p).map(|b| parse(&b)).unwrap_or_default();
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX));
            aged(
                runs,
                &crate::sessions::rfc3339_of_unix(now.saturating_sub(ASKING_SECS)),
            )
        }
        _ => Vec::new(),
    }
}

/// Written as the other lists of the data folder are: readable by this user only, a new file
/// put in place of the old one (a link there is replaced, not written through).
fn save(runs: &[Run]) -> Result<(), String> {
    let p = crate::workspace::data_file(FILE).ok_or("no data folder")?;
    if std::fs::symlink_metadata(&p).is_ok_and(|m| !m.is_file()) {
        return Err("not a plain file".into());
    }
    let body = serde_json::to_vec_pretty(&json!({ "runs": runs })).map_err(|e| e.to_string())?;
    crate::platform::write_private_file(&p, &body).map_err(|e| e.to_string())
}

/// One change to one run, written; `None`: no such run, or `change` said no.
fn change(run_id: &str, change: impl FnOnce(&mut Run, &str) -> bool) -> Result<Option<Run>, ()> {
    let _one = lock();
    let mut runs = load();
    let Some(run) = runs.iter_mut().find(|r| r.run_id == run_id) else {
        return Ok(None);
    };
    if !change(run, &crate::sessions::now_rfc3339()) {
        return Ok(None);
    }
    let run = run.clone();
    save(&runs).map_err(|_| ())?;
    Ok(Some(run))
}

fn find(run_id: &str) -> Option<Run> {
    let _one = lock();
    load().into_iter().find(|r| r.run_id == run_id)
}

fn record(st: &AppState, kind: &str, payload: Value) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
    let event = NewEvent {
        event_id: id.clone(),
        aggregate_type: "receipt_run".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    if let Err(e) = st.store.append(vec![event]) {
        tracing::warn!(kind, error = %e, "receipt event not written");
    }
}

fn refuse(status: StatusCode, code: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "reason": code, "request_id": request_id() })),
    )
        .into_response()
}

/// The status a refusal's code answers with.
pub fn status_of(code: &str) -> StatusCode {
    match code {
        "bad_head" | "bad_scope" | "bad_tests" | "seat_id_invalid" | "bad_run_id" => {
            StatusCode::BAD_REQUEST
        }
        "receipt_not_allowed" | "head_not_seat_head" => StatusCode::FORBIDDEN,
        "no_such_run" => StatusCode::NOT_FOUND,
        "receipt_in_flight" | "receipt_asked_too_often" | "already_done" => StatusCode::CONFLICT,
        _ => StatusCode::SERVICE_UNAVAILABLE,
    }
}

/// A request: its run id is kept in the file, then the runner is asked with the file free (it
/// may go to another machine), then the answer is written. A refusal takes the entry out
/// again; an answer that cannot be written is cancelled at the runner (no run without its
/// entry).
fn request(body: &Value) -> Result<Run, &'static str> {
    let ask = {
        let _one = lock();
        let runs = load();
        let ask = ask_of(body, &runs)?;
        let kept = reserved(&ask, &crate::sessions::now_rfc3339());
        save(&with(runs, kept)).map_err(|_| "receipt_list_not_written")?;
        ask
    };
    let answer = started(&ask, runner().start(&ask), &crate::sessions::now_rfc3339());
    let one = lock();
    let mut runs = load();
    let at = runs.iter().position(|r| r.run_id == ask.run_id);
    let kept = match at {
        Some(i) => runs.get_mut(i),
        None => None,
    }
    .filter(|r| r.state == ASKING);
    let written = match (&answer, kept) {
        (Ok(run), Some(slot)) => {
            *slot = run.clone();
            save(&runs).is_ok()
        }
        // withdrawn (or gone) while the runner was asked: what it started is stopped
        (Ok(_), None) => false,
        (Err(_), _) => {
            runs.retain(|r| !(r.run_id == ask.run_id && r.state == ASKING));
            save(&runs).is_ok()
        }
    };
    drop(one);
    match answer {
        Ok(run) if written => Ok(run),
        Ok(_) => {
            runner().cancel(&ask.run_id);
            Err("receipt_list_not_written")
        }
        Err(why) => Err(why),
    }
}

/// The run as it is now; `true` when this read ended it. The runner is asked for the `.out`
/// with the file free.
fn read(run_id: &str) -> Result<(Run, bool), &'static str> {
    let run = find(run_id).ok_or("no_such_run")?;
    if run.state != WAITING_OWNER && run.state != RUNNING {
        return Ok((run, false));
    }
    let Some(out) = runner().read(run_id).filter(|o| o.len() <= MAX_OUT) else {
        return Ok((run, false));
    };
    match change(run_id, |r, at| {
        let was = in_flight(r);
        finished(r, &out, at);
        was
    }) {
        Ok(Some(run)) => Ok((run, true)),
        // it ended another way while the .out was fetched: as it is now
        Ok(None) => find(run_id).map(|r| (r, false)).ok_or("no_such_run"),
        Err(()) => Err("receipt_list_not_written"),
    }
}

fn withdraw(run_id: &str) -> Result<Run, &'static str> {
    let mut why = "no_such_run";
    let done = change(run_id, |r, at| match withdrawn(r, at) {
        Ok(()) => true,
        Err(e) => {
            why = e;
            false
        }
    });
    match done {
        Ok(Some(run)) => {
            runner().cancel(run_id);
            Ok(run)
        }
        Ok(None) => Err(why),
        Err(()) => Err("receipt_list_not_written"),
    }
}

/// One change a runner reports, written to the file; `None` when there is no such run or it
/// was not in the state the change is for.
fn reported(run_id: &str, by: impl FnOnce(&mut Run, &str) -> bool) -> Option<Run> {
    change(run_id, by).ok().flatten()
}

fn tell(st: &AppState, kind: &str, run: &Run) {
    record(
        st,
        kind,
        json!({
            "run_id": run.run_id, "seat": run.seat_id, "head": run.head,
            "host": run.host, "state": run.state, "green": false,
            "reasons": reasons_of(run),
        }),
    );
}

/// For the runner (b4297's half): the owner answered the card of `run_id`. Blocking (file).
/// `false`: no such run, or it was not waiting for the owner.
pub fn owner_answered(st: &AppState, run_id: &str, ok: bool) -> bool {
    let Some(run) = reported(run_id, |r, at| owner_said(r, ok, at)) else {
        return false;
    };
    tell(
        st,
        if ok {
            "seat.receipt_started"
        } else {
            "seat.receipt_done"
        },
        &run,
    );
    true
}

/// For the runner: a run under way ended with no `.out`, and why (one of [`REFUSALS`]; the
/// machine going away is `lost`). Blocking (file). `false`: no such run under way.
pub fn runner_lost(st: &AppState, run_id: &str, why: &str) -> bool {
    let Some(run) = reported(run_id, |r, at| runner_said(r, why, at)) else {
        return false;
    };
    tell(st, "seat.receipt_done", &run);
    true
}

/// `receipt_request`, `receipt_read`, `receipt_withdraw` from a confirmed page.
pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    let kind = body
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    // asking a test machine to run something, or stopping it, is the built-in page's (a user
    // folder page and a carried pairing only read)
    if kind == LIST {
        let seat = body
            .get("seat_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if seat.is_empty() || seat.len() > 128 {
            return refuse(StatusCode::BAD_REQUEST, "seat_id_invalid");
        }
        let runs = tokio::task::spawn_blocking(move || {
            let _one = lock();
            listed(&load(), &seat)
        })
        .await
        .unwrap_or_default();
        return Json(json!({ "runs": runs })).into_response();
    }
    if kind != READ && (page.carried || crate::seat_input::held_as_user_page(&page).await) {
        return refuse(StatusCode::FORBIDDEN, "receipt_built_in_page_only");
    }
    let run_id = body
        .get("run_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if kind != REQUEST && !run_id_ok(&run_id) {
        return refuse(StatusCode::BAD_REQUEST, "bad_run_id");
    }
    let k = kind.clone();
    let done = tokio::task::spawn_blocking(move || match k.as_str() {
        REQUEST => request(&body).map(|r| (r, false)),
        READ => read(&run_id),
        _ => withdraw(&run_id).map(|r| (r, false)),
    })
    .await;
    let (run, ended) = match done {
        Ok(Ok(x)) => x,
        Ok(Err(code)) => return refuse(status_of(code), code),
        Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal"),
    };
    let said = json!({
        "run_id": run.run_id, "seat": run.seat_id, "head": run.head,
        "host": run.host, "state": run.state,
        "green": run.verdict.as_ref().is_some_and(|v| v.green),
        "reasons": reasons_of(&run),
    });
    match kind.as_str() {
        REQUEST => {
            record(&st, "seat.receipt_requested", said);
            (StatusCode::CREATED, Json(view(&run))).into_response()
        }
        READ => {
            if ended {
                record(&st, "seat.receipt_done", said);
            }
            Json(view(&run)).into_response()
        }
        _ => {
            record(&st, "seat.receipt_withdrawn", said);
            Json(view(&run)).into_response()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SEAT: &str = "seat_06a6c06c5d3fb381";
    const H: &str = "6f547e599826ea87ecaa770771fefcd188c59cd4";

    fn full() -> Value {
        json!({"type": REQUEST, "seat_id": SEAT, "head": H, "scope": "full"})
    }

    fn run(state: &str) -> Run {
        let ask = ask_of(&full(), &[]).unwrap();
        let start = Start::Started {
            host: "andymini".into(),
            knives_sent: 2,
        };
        let mut r = started(&ask, start, "2026-11-20T13:00:00.000Z").unwrap();
        r.state = state.to_owned();
        r
    }

    #[test]
    fn a_request_names_a_head_and_a_kind_and_nothing_else() {
        let a = ask_of(&full(), &[]).unwrap();
        assert_eq!(a.run_id, "web-fb381-6f547e599");
        assert!(!a.narrow && a.tests.is_empty());
        let with = |k: &str, v: Value| {
            let mut b = full();
            b[k] = v;
            ask_of(&b, &[])
        };
        // a head is 40 lowercase hex: not a short one, a branch, or an option
        for bad in ["6f547e5998", "main", "--upload-pack=x", &H.to_uppercase()] {
            assert_eq!(
                with("head", json!(bad)),
                Err("bad_head"),
                "reason: head_taken {bad}"
            );
        }
        assert_eq!(with("scope", json!("all")), Err("bad_scope"));
        assert_eq!(with("scope", Value::Null), Err("bad_scope"));
        assert_eq!(with("seat_id", json!("")), Err("seat_id_invalid"));
        assert_eq!(with("seat_id", json!("seat_../x")), Err("seat_id_invalid"));
        // a full receipt names no tests; a narrow one names 1 to 8 `<crate>:<test file>`
        assert_eq!(with("tests", json!(["aibd:usage"])), Err("bad_tests"));
        let narrow = |tests: Value| {
            let mut b = full();
            b["scope"] = json!("narrow");
            b["tests"] = tests;
            ask_of(&b, &[])
        };
        let a = narrow(json!(["aibd:usage", "aib-store:store_v2"])).unwrap();
        assert!(a.narrow, "reason: narrow_not_kept");
        assert_eq!(a.tests, ["aibd:usage", "aib-store:store_v2"]);
        assert_eq!(narrow(json!([])), Err("bad_tests"));
        assert_eq!(narrow(json!("aibd:usage")), Err("bad_tests"));
        assert_eq!(narrow(json!(vec!["aibd:usage"; 9])), Err("bad_tests"));
        for bad in [
            "aibd",
            "aibd:",
            ":usage",
            "aibd:usage -- --nocapture",
            "aibd:usage;id",
            "-p:usage",
            "aibd:--test",
            "aibd:a:b",
            "aibd:../x",
            "aibd:測試",
        ] {
            assert_eq!(
                narrow(json!([bad])),
                Err("bad_tests"),
                "reason: test_word_taken {bad}"
            );
        }
        assert_eq!(narrow(json!([7])), Err("bad_tests"));
    }

    #[test]
    fn a_run_id_is_the_seat_and_the_head_and_new_each_time() {
        assert_eq!(seat_code(SEAT), Some("fb381"));
        assert_eq!(seat_code("ab1"), None);
        assert_eq!(seat_code("seat_ABCDE"), None);
        assert_eq!(seat_code("seat_a/b.c"), None);
        let mut runs = vec![run(DONE)];
        assert_eq!(
            run_id(SEAT, H, &runs).as_deref(),
            Some("web-fb381-6f547e599-2"),
            "reason: run_id_reused"
        );
        runs.push(Run {
            run_id: "web-fb381-6f547e599-2".into(),
            ..run(DONE)
        });
        assert_eq!(
            run_id(SEAT, H, &runs).as_deref(),
            Some("web-fb381-6f547e599-3")
        );
        for ok in [
            "web-fb381-6f547e599",
            "web-fb381-6f547e599-2",
            "web-fb381-6f547e599-99",
        ] {
            assert!(run_id_ok(ok), "reason: run_id_refused {ok}");
        }
        for bad in [
            "",
            "web-fb381-6f547e59",
            "web-fb381-6f547e599-1",
            "web-fb381-6f547e599-02",
            "web-fb381-6f547e599-100",
            "web-fb381-6f547e599-2-3",
            "web-FB381-6f547e599",
            "web-fb381-6F547E599",
            "7ebca-g1-6f547e599",
            "web-fb381-6f547e599 read",
            "web-fb381-6f547e599;id",
            "../web-fb381-6f547e599",
        ] {
            assert!(!run_id_ok(bad), "reason: run_id_taken {bad}");
        }
    }

    #[test]
    fn a_seat_has_two_runs_under_way_at_most() {
        let other = Run {
            seat_id: "seat_189c122b6c504411".into(),
            ..run(RUNNING)
        };
        let one = vec![run(RUNNING), run(DONE), run(WITHDRAWN), other.clone()];
        assert!(ask_of(&full(), &one).is_ok(), "reason: second_refused");
        let two = vec![run(RUNNING), run(WAITING_OWNER), other];
        assert_eq!(
            ask_of(&full(), &two),
            Err("receipt_in_flight"),
            "reason: third_taken"
        );
    }

    #[test]
    fn a_runners_answer_makes_the_run_or_refuses_it() {
        let ask = ask_of(&full(), &[]).unwrap();
        let at = "2026-11-20T13:00:00.000Z";
        let r = started(
            &ask,
            Start::Started {
                host: "andymini".into(),
                knives_sent: 60,
            },
            at,
        )
        .unwrap();
        assert_eq!(r.state, RUNNING);
        assert_eq!((r.host.as_deref(), r.knives_sent), (Some("andymini"), 60));
        assert_eq!((r.scope.as_str(), r.asked_at.as_str()), ("full", at));
        let r = started(
            &ask,
            Start::NeedsOwner {
                host: "user@192.0.2.7:/tmp/x".into(),
                knives_sent: 1,
            },
            at,
        )
        .unwrap();
        assert_eq!(r.state, WAITING_OWNER, "reason: owner_not_waited_for");
        // only a nickname is kept: no address, no path
        assert_eq!(r.host.as_deref(), Some("user192027tmpx"));
        assert_eq!(
            started(&ask, Start::Refused("disk_low"), at),
            Err("disk_low")
        );
        // a runner's own words are not passed on
        assert_eq!(
            started(&ask, Start::Refused("ssh: connect to host 192.0.2.7"), at),
            Err("refused"),
            "reason: runner_text_passed_on"
        );
        // the runner aibd ships runs nothing
        assert_eq!(NoRunner.start(&ask), Start::Refused("no_host"));
        assert_eq!(NoRunner.read(&ask.run_id), None);
    }

    #[test]
    fn a_finished_run_is_done_only_when_aibd_reads_it_green() {
        let mut r = run(RUNNING);
        finished(&mut r, b"KNIVES-OK\n", "t1");
        assert_eq!(r.state, RED, "reason: unread_receipt_done");
        assert_eq!(r.ended_at.as_deref(), Some("t1"));
        let v = view(&r);
        assert_eq!(v["green"], false);
        assert_eq!(v["reasons"][0], "seal_missing");
        // an ended run is not read again
        let before = r.clone();
        finished(&mut r, b"", "t2");
        assert_eq!(r, before, "reason: ended_run_read_again");
        let mut w = run(WITHDRAWN);
        finished(&mut w, b"", "t2");
        assert_eq!(w.state, WITHDRAWN);
        // the view has no line of the receipt: only the keys below
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            [
                "asked_at",
                "ended_at",
                "green",
                "head",
                "host",
                "knives",
                "knives_sent",
                "narrow",
                "reasons",
                "run_id",
                "scope",
                "seat_id",
                "state",
                "tests",
                "tests_total"
            ],
            "reason: view_changed"
        );
    }

    #[test]
    fn the_owners_answer_and_a_lost_machine_move_a_run() {
        // yes: it runs; no: red, owner_refused
        let mut r = run(WAITING_OWNER);
        assert!(owner_said(&mut r, true, "t"));
        assert_eq!((r.state.as_str(), r.ended_at.as_deref()), (RUNNING, None));
        let mut r = run(WAITING_OWNER);
        assert!(owner_said(&mut r, false, "t"));
        assert_eq!(r.state, RED, "reason: refused_run_runs");
        assert_eq!(view(&r)["reasons"], json!(["owner_refused"]));
        assert_eq!(view(&r)["green"], false);
        // an answer for a run that does not wait for one changes nothing
        for state in [ASKING, RUNNING, DONE, RED, WITHDRAWN, LOST] {
            let mut r = run(state);
            assert!(!owner_said(&mut r, true, "t"), "reason: late_answer_taken");
            assert_eq!(r.state, state);
        }
        // the machine went away: lost; the gate refused the tree: red
        let mut r = run(RUNNING);
        assert!(runner_said(&mut r, "lost", "t"));
        assert_eq!(r.state, LOST, "reason: lost_not_said");
        assert_eq!(reasons_of(&r), ["lost"]);
        let mut r = run(WAITING_OWNER);
        assert!(runner_said(&mut r, "tree_refused", "t"));
        assert_eq!((r.state.as_str(), r.ended_at.as_deref()), (RED, Some("t")));
        // a runner's own words are not kept
        let mut r = run(RUNNING);
        assert!(runner_said(&mut r, "ssh: connect to host 192.0.2.7", "t"));
        assert_eq!(reasons_of(&r), ["refused"], "reason: runner_text_kept");
        // an ended run stays as it ended, and a lost run is not under way
        let mut r = run(DONE);
        assert!(!runner_said(&mut r, "lost", "t"), "reason: ended_run_lost");
        assert_eq!(r.state, DONE);
        assert!(!in_flight(&run(LOST)));
    }

    #[test]
    fn a_reloaded_page_finds_a_seats_runs_again() {
        let mut runs = Vec::new();
        for n in 0..(LISTED + 3) {
            runs.push(Run {
                run_id: format!("e{n}"),
                ..run(if n % 2 == 0 { DONE } else { RED })
            });
        }
        runs.insert(
            0,
            Run {
                run_id: "old-under-way".into(),
                ..run(RUNNING)
            },
        );
        runs.push(Run {
            run_id: "theirs".into(),
            seat_id: "seat_189c122b6c504411".into(),
            ..run(RUNNING)
        });
        let l = listed(&runs, SEAT);
        let ids: Vec<&str> = l.iter().map(|v| v["run_id"].as_str().unwrap()).collect();
        // newest first, the latest LISTED ended, and every run under way however old
        assert_eq!(ids.len(), LISTED + 1, "reason: list_length {ids:?}");
        assert_eq!(
            ids[0],
            format!("e{}", LISTED + 2),
            "reason: not_newest_first"
        );
        assert_eq!(ids[LISTED - 1], "e3", "reason: too_many_ended");
        assert_eq!(
            ids[LISTED], "old-under-way",
            "reason: run_under_way_left_out"
        );
        assert!(!ids.contains(&"theirs"), "reason: other_seats_run_listed");
        // a row is the same view a read gives
        assert_eq!(l[LISTED], view(&runs[0]));
        assert!(listed(&runs, "seat_none").is_empty());
    }

    #[test]
    fn a_run_id_kept_by_an_aibd_that_stopped_is_lost() {
        let kept = |id: &str, at: &str| Run {
            run_id: id.into(),
            ..reserved(&ask_of(&full(), &[]).unwrap(), at)
        };
        let runs = vec![
            kept("old", "2026-11-20T12:49:59.000Z"),
            kept("fresh", "2026-11-20T12:55:00.000Z"),
            Run {
                run_id: "running".into(),
                ..run(RUNNING)
            },
        ];
        let runs = aged(runs, "2026-11-20T12:50:00.000Z");
        assert_eq!(runs[0].state, LOST, "reason: stale_asking_kept");
        assert_eq!(reasons_of(&runs[0]), ["lost"]);
        assert!(!in_flight(&runs[0]));
        assert_eq!(runs[1].state, ASKING, "reason: fresh_asking_lost");
        // a run the runner has (asked at 13:00 in `run`) is not this function's to end
        let older = aged(runs, "2026-11-20T23:00:00.000Z");
        assert_eq!(older[2].state, RUNNING, "reason: running_run_lost");
        assert_eq!(older[1].state, LOST);
    }

    #[test]
    fn only_a_run_under_way_is_withdrawn() {
        // the entry that keeps a run id while the runner is asked counts as under way
        let kept = reserved(&ask_of(&full(), &[]).unwrap(), "t0");
        assert_eq!((kept.state.as_str(), kept.host.as_deref()), (ASKING, None));
        assert!(in_flight(&kept), "reason: kept_id_not_counted");
        assert_eq!(
            run_id(SEAT, H, std::slice::from_ref(&kept)).as_deref(),
            Some("web-fb381-6f547e599-2")
        );
        for state in [RUNNING, WAITING_OWNER, ASKING] {
            let mut r = run(state);
            assert_eq!(withdrawn(&mut r, "t"), Ok(()));
            assert_eq!(r.state, WITHDRAWN);
        }
        for state in [DONE, RED, WITHDRAWN, LOST] {
            let mut r = run(state);
            assert_eq!(
                withdrawn(&mut r, "t"),
                Err("already_done"),
                "reason: ended_run_withdrawn"
            );
            assert_eq!(r.state, state);
        }
    }

    #[test]
    fn the_list_keeps_the_newest_and_every_run_under_way() {
        let mut runs = Vec::new();
        for n in 0..MAX_RUNS {
            runs.push(Run {
                run_id: format!("r{n}"),
                ..run(if n == 0 { RUNNING } else { DONE })
            });
        }
        let runs = with(
            runs,
            Run {
                run_id: "new".into(),
                ..run(DONE)
            },
        );
        assert_eq!(runs.len(), MAX_RUNS);
        assert_eq!(runs[0].run_id, "r0", "reason: run_under_way_dropped");
        assert_eq!(runs[1].run_id, "r2", "reason: oldest_ended_kept");
        assert_eq!(runs.last().unwrap().run_id, "new");
        // the file round-trips; a malformed one is no runs
        let bytes = serde_json::to_vec(&json!({ "runs": runs })).unwrap();
        assert_eq!(parse(&bytes), runs);
        assert!(parse(b"{").is_empty());
        assert_eq!(status_of("bad_head"), StatusCode::BAD_REQUEST);
        assert_eq!(status_of("no_host"), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(status_of("no_such_run"), StatusCode::NOT_FOUND);
        assert_eq!(status_of("receipt_in_flight"), StatusCode::CONFLICT);
        assert_eq!(status_of("head_not_seat_head"), StatusCode::FORBIDDEN);
    }
}
