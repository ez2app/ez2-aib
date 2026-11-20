//! Sessions API, preview.2 ("usable today"): start a program, list it, stop
//! its whole tree. Contract: CONTRACT-sessions-preview2-a9136-1109 + a63ab
//! rulings 11-09 (H1 / H2 / H3 from bb7dc's review).
//!
//! - **H2, honest end states.** `exited` / `killed` only when the stop is
//!   `clean` (today: Windows Job only). macOS is never clean, so a session
//!   there ends `stop_unconfirmed`, with reason, survivors and blind spots.
//!   Its slot is given back only when no recorded process is known to still
//!   run ([`Stop::releases_slot`]): that is the observed concurrency going
//!   down, not proof the tree is gone, and the limit is not an isolation
//!   boundary. With survivors it stays in the running set; a DELETE then
//!   stops it again (never "already ended, nothing to do").
//! - **H3, one lock for slots and shutdown.** A POST reserves its slot under
//!   the lock before it spawns and gives it back if the spawn fails; shutdown
//!   sets `shutting` under the same lock, waits for every reservation to
//!   settle, then stops everything. A POST whose spawn finished after
//!   shutdown began stops its own tree before it answers 503: nothing started
//!   escapes the shutdown sweep.
//! - **Secrets.** `env` is never returned, logged or written to events;
//!   events and logs name only `argv[0]`'s file name and the argument count.
//!   `argv` is returned by GET (the caller gave it; the API is token-only).
//!
//! - **Queue (ADR 0005 §5).** A POST when every slot is taken (or others are
//!   already waiting) is not refused: the session is `pending` (202, no
//!   process, `pid` 0, `session.queued`) and starts in arrival order (FIFO)
//!   as slots come back. At most [`MAX_QUEUE`] wait; the next POST is `429
//!   session_queue_full`. DELETE of a pending session takes it out of the
//!   queue (`cancelled`, never started). At shutdown the waiting ones end
//!   `interrupted` (`ended_by: shutdown`); after a crash the next start marks
//!   them `interrupted` from the store (nothing is started from an old queue).
//!
//! Blocking calls (spawn, stop, the event store) run on blocking threads.

use crate::allowlist::{self, SessionPolicy};
use crate::api::AppState;
use crate::guard::{self, error_response};
use crate::lineage::Lineage;
use crate::platform::session::{CandidateDiag, Running, SpawnFn, Spec, Stop};
use aib_core::ErrorCode;
use aib_store::{NewEvent, SessionRow, Store};
use axum::body::Body;
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

pub const MAX_BODY: usize = 64 * 1024;
pub const MAX_ARGS: usize = 64;
pub const MAX_ARG_BYTES: usize = 4096;
pub const MAX_ARGV_TOTAL: usize = 32 * 1024;
pub const MAX_ENV: usize = 32;
pub const MAX_ENV_VALUE: usize = 4096;
/// S79-p11b (Windows acceptance 2.4): the longest `timeout_s` a POST may ask for (one day).
pub const MAX_TIMEOUT_S: u64 = 24 * 3600;
/// Ended sessions kept in memory for GET (not persisted).
pub const KEEP_ENDED: usize = 100;
/// Sessions that may wait for a slot (ADR 0005 §5, trial value; a63ab 19:13). The next
/// one is `429 session_queue_full`.
pub const MAX_QUEUE: usize = 64;
pub const DEFAULT_GRACE: Duration = Duration::from_millis(5000);
pub const MAX_GRACE_MS: u64 = 30_000;
/// The one stop after the root exited by itself, and each stop at shutdown.
pub const SHORT_GRACE: Duration = Duration::from_secs(2);
/// aibd's whole shutdown sweep (contract §5).
pub const SHUTDOWN_CAP: Duration = Duration::from_secs(10);
/// Background slot re-check (a63ab 11-09): every 2 s (the
/// `session.slot_rescan` timer), at most this long.
pub const RESCAN_WINDOW: Duration = Duration::from_secs(30);
/// What a re-check pass needs after its grace (KILL, its 2 s check, a
/// margin). A pass starts only with at least this much of the window left;
/// its grace is what is left beyond it, at most [`SHORT_GRACE`].
pub const RESCAN_PASS_MIN: Duration = Duration::from_secs(3);
/// `session.slot_held` reason when a pass did not answer by the end of the
/// window (bb7dc #546 7ebde8d1): the slot stays held, never released.
pub const RESCAN_PASS_TIMEOUT: &str = "rescan_pass_timeout";
/// The only stop reasons worth re-checking (a63ab 05:42, DECIDED #9): what
/// another pass can change. `lineage_unavailable`, `root_identity_lost`,
/// `job_error:*`… come out the same every time (bb7dc #546 (3)).
pub const RESCAN_REASONS: &[&str] = &["candidates_unreadable", "candidates_unreadable_older"];

fn worth_rescanning(stop: &Stop) -> bool {
    stop.survivors.is_empty()
        && stop
            .status
            .reason()
            .is_some_and(|r| RESCAN_REASONS.contains(&r))
}

/// How long a DELETE waits for a stop already running on the same session.
const BUSY_WAIT: Duration = Duration::from_secs(40);

/// Env keys refused with 400 `env_not_allowed` (a63ab 11-09: this list, not
/// wider; the token holder can run any argv anyway, so env is no second
/// defence, only a guard against mistakes).
const ENV_DENY_PREFIX: [&str; 3] = ["AIBD_", "DYLD_", "LD_"];
const ENV_DENY_EXACT: [&str; 1] = ["PATH"];

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SignalView {
    pub signal: String,
    pub targets: Vec<u32>,
}

/// `stop` in a Session (contract §1).
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct StopView {
    pub status: &'static str,
    pub reason: Option<String>,
    pub survivors: Vec<u32>,
    pub tree_size: usize,
    pub signals: Vec<SignalView>,
    pub blind_spots: Vec<&'static str>,
    /// F (1ced7 11-09): the late-exec candidate check behind `reason`
    /// (count, how many only the widened orphan check added, unreadable
    /// pid + process name). macOS only; zero / empty elsewhere.
    pub candidates: CandidateDiag,
}

impl StopView {
    fn of(s: &Stop) -> StopView {
        StopView {
            status: s.status.as_str(),
            reason: s.status.reason().map(str::to_owned),
            survivors: s.survivors.clone(),
            tree_size: s.tree_size,
            signals: s
                .signals
                .iter()
                .map(|x| SignalView {
                    signal: x.signal.clone(),
                    targets: x.targets.clone(),
                })
                .collect(),
            blind_spots: s.blind_spots.clone(),
            candidates: s.candidates.clone(),
        }
    }
}

/// One session as the API returns it. `env` is deliberately absent.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Session {
    pub id: String,
    /// The seat it runs for; `None` when started through `POST /api/v1/sessions`
    /// (preview.2 route, kept for compatibility).
    pub seat_id: Option<String>,
    pub argv: Vec<String>,
    pub cwd: String,
    /// 0 while `pending` (and for a queued start that failed).
    pub pid: u32,
    /// When it was accepted while `pending`; when its process started after.
    pub started_at: String,
    /// `pending` (queued, no process yet) | `running` | `exited` | `killed` |
    /// `timeout` (its `timeout_s` ran out and the stop was confirmed; S79-p11b) |
    /// `stop_unconfirmed` (H2) | `cancelled` (DELETE while pending) | `interrupted`
    /// (pending at shutdown) | `failed` (a queued start that could not start).
    pub state: &'static str,
    pub exit_code: Option<i32>,
    pub signal: Option<String>,
    pub ended_at: Option<String>,
    /// `root_exit` | `api` | `timeout` | `shutdown` | `spawn_failed`. A run that ran out of
    /// time is `timeout` here even when its stop could not be confirmed (then `state` is
    /// `stop_unconfirmed`): it never reads as `exited`.
    pub ended_by: Option<&'static str>,
    /// Still counts against the running limit: running, or a stop that could
    /// not show the tree is gone (H2 whitelist). DELETE again to re-check.
    pub holds_slot: bool,
    pub stop: Option<StopView>,
    /// While `pending` (043dd M2): its place in the queue now (1 = next), and
    /// the sessions holding the slots it waits for. Absent otherwise.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub queue: Option<QueueView>,
}

/// Where a pending session stands (GET only; computed when read).
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct QueueView {
    pub position: usize,
    pub slots: usize,
    /// Every session holding a slot: id, state and, for a stop that kept
    /// the slot, its reason (`candidates_unreadable` ...). A slot whose
    /// process could not be confirmed after a failed start has no session
    /// and shows in `held_unknown`.
    pub held_by: Vec<SlotHolder>,
    pub held_unknown: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SlotHolder {
    pub id: String,
    pub state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// `names` without any of `withhold` (case-blind on Windows, like env names there).
pub fn without(names: Vec<String>, withhold: &[String]) -> Vec<String> {
    names
        .into_iter()
        .filter(|n| !withhold.iter().any(|w| allowlist::env_name_eq(n, w)))
        .collect()
}

/// A validated POST body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub argv: Vec<String>,
    pub cwd: Option<PathBuf>,
    pub env: Vec<(String, String)>,
    /// S79-p11b: `timeout_s` (1..=[`MAX_TIMEOUT_S`]); still running then ⇒ stopped like a
    /// DELETE and recorded as `timeout`. `None` = no limit (as before). The clock starts
    /// when the process starts, not while the session waits in the queue.
    pub timeout: Option<Duration>,
    /// Policy env names this session does not inherit (S89-dgrs L1: a dgrs seat never gets
    /// the ssh agent). Set by aibd only, never by a request body.
    pub withhold: Vec<String>,
    /// S89-live-9 (0eed7 REVIEW-s89-input L2): the process gets a stdin pipe. Only a seat's
    /// provider reads its messages there; any other program (`ctl seat open <name> --
    /// <program>`) gets no stdin, so one that reads to EOF is not left waiting. Set by aibd
    /// only, never by a request body.
    pub input: bool,
}

/// An API error: code + message. The message names a field or a code, never
/// a value from the request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiError(pub ErrorCode, pub String);

impl ApiError {
    pub(crate) fn new(code: ErrorCode, msg: impl Into<String>) -> ApiError {
        ApiError(code, msg.into())
    }

    pub(crate) fn into_response(self) -> Response {
        let mut r = error_response(self.0, &self.1, &guard::request_id());
        if self.0 == ErrorCode::ThermalNogo {
            r.headers_mut().insert(
                axum::http::header::RETRY_AFTER,
                axum::http::HeaderValue::from_static(THERMAL_RETRY_AFTER_SECS),
            );
        }
        r
    }
}

fn bad(msg: &str) -> ApiError {
    ApiError::new(ErrorCode::InvalidRequest, msg)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRequest {
    argv: Vec<String>,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    env: Option<BTreeMap<String, String>>,
    #[serde(default)]
    timeout_s: Option<u64>,
}

pub(crate) fn env_key_ok(k: &str) -> bool {
    let b = k.as_bytes();
    !b.is_empty()
        && b.len() <= 64
        && (b[0].is_ascii_alphabetic() || b[0] == b'_')
        && b.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'_')
}

pub(crate) fn env_key_denied(k: &str) -> bool {
    let up = k.to_ascii_uppercase();
    ENV_DENY_PREFIX.iter().any(|p| up.starts_with(p)) || ENV_DENY_EXACT.contains(&up.as_str())
}

/// Validate a POST body (contract §1). serde's own messages are never echoed:
/// they can quote the offending value.
pub fn validate(body: &[u8]) -> Result<Request, ApiError> {
    validate_audit(body).map_err(|i| i.err)
}

/// A refused POST body. `audit` = the first layer writes a `session.spawn_rejected`
/// record for it. It is set where the refusal is made, never read back from the
/// message (043dd #593 N1: rewording a message used to drop the record silently).
pub(crate) struct Invalid {
    pub err: ApiError,
    pub audit: bool,
}

impl From<ApiError> for Invalid {
    fn from(err: ApiError) -> Self {
        Invalid { err, audit: false }
    }
}

fn audited(err: ApiError) -> Invalid {
    Invalid { err, audit: true }
}

/// [`validate`], also saying whether the refusal is audited (see [`Invalid`]).
pub(crate) fn validate_audit(body: &[u8]) -> Result<Request, Invalid> {
    if body.len() > MAX_BODY {
        return Err(ApiError::new(ErrorCode::PayloadTooLarge, "request body over 64 KiB").into());
    }
    let raw: RawRequest = serde_json::from_slice(body).map_err(|_| {
        bad("body must be a JSON object {argv: [string], cwd?: string, env?: {string: string}, timeout_s?: integer}")
    })?;
    let argv = raw.argv;
    if argv.is_empty() || argv.len() > MAX_ARGS {
        return Err(bad("argv must have 1-64 items").into());
    }
    let mut total = 0usize;
    // a63ab 20:32: argv[0] must be non-empty; later items may be empty (claude needs
    // `--setting-sources ""`) and are passed to the child as they are.
    for (i, a) in argv.iter().enumerate() {
        if (i == 0 && a.is_empty()) || a.len() > MAX_ARG_BYTES || a.contains('\0') {
            return Err(bad(
                "argv[0] must be non-empty; each argv item at most 4096 bytes without NUL",
            )
            .into());
        }
        total += a.len();
    }
    if total > MAX_ARGV_TOTAL {
        return Err(bad("argv over 32 KiB in total").into());
    }
    if !Path::new(&argv[0]).is_absolute() {
        return Err(audited(bad(ARGV0_RELATIVE)));
    }
    let cwd = match raw.cwd {
        None => None,
        Some(c) => {
            let p = PathBuf::from(&c);
            if c.contains('\0') || !p.is_absolute() || !p.is_dir() {
                return Err(bad("cwd must be an existing absolute directory").into());
            }
            Some(p)
        }
    };
    let env_in = raw.env.unwrap_or_default();
    if env_in.len() > MAX_ENV {
        return Err(bad("env has more than 32 keys").into());
    }
    let mut env = Vec::with_capacity(env_in.len());
    for (k, v) in env_in {
        // Windows names have no case: `TEMP` + `Temp` would both reach the
        // child; refused on every platform (043dd #539 Low)
        if env
            .iter()
            .any(|(e, _): &(String, String)| e.eq_ignore_ascii_case(&k))
        {
            return Err(bad("env has the same key twice (keys are compared without case)").into());
        }
        if !env_key_ok(&k) {
            return Err(bad("env key must match ^[A-Za-z_][A-Za-z0-9_]{0,63}$").into());
        }
        if env_key_denied(&k) {
            return Err(audited(ApiError::new(
                ErrorCode::EnvNotAllowed,
                "env key not allowed (AIBD_*, DYLD_*, LD_*, PATH)",
            )));
        }
        if v.len() > MAX_ENV_VALUE || v.contains('\0') {
            return Err(bad("env value must be at most 4096 bytes without NUL").into());
        }
        env.push((k, v));
    }
    let timeout = match raw.timeout_s {
        None => None,
        Some(t) if (1..=MAX_TIMEOUT_S).contains(&t) => Some(Duration::from_secs(t)),
        Some(_) => return Err(bad("timeout_s must be 1-86400 seconds").into()),
    };
    Ok(Request {
        argv,
        cwd,
        env,
        timeout,
        withhold: Vec::new(),
        input: false,
    })
}

const ARGV0_RELATIVE: &str = "argv[0] must be an absolute path (no PATH lookup)";

/// `argv[0]`'s file name from a body that failed [`validate`], for the audit record only
/// (empty when the body has none).
fn lenient_argv0_name(body: &[u8]) -> String {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v["argv"][0].as_str().map(str::to_owned))
        .and_then(|a| {
            Path::new(&a)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
        })
        .unwrap_or_default()
}

/// The only credential today is the startup token: its id is this name, never anything
/// derived from the value. Pairing cookies (Phase 3) get their own ids.
pub const TOKEN_ID_STARTUP: &str = "startup";

/// A gate refusal: the answer, and the program's file name for the audit record.
#[derive(Debug)]
pub struct Refusal {
    pub error: ApiError,
    /// The real path's file name when it resolves, else `argv[0]`'s; never more of argv.
    pub program: String,
}

/// M1 security gate (crate::allowlist), after [`validate`] (syntax first) and before
/// anything starts: `argv[0]` must resolve, now, to the real path of a listed program that
/// is not a shell / interpreter / launcher / npm install, and every env name must be
/// listed. On success `argv[0]` is replaced by that real path, so what runs is what was
/// checked. The fixed deny list still applies inside `validate`. Blocking (reads the file
/// system): call it on a blocking thread.
pub fn admit(mut req: Request, p: &SessionPolicy) -> Result<Request, Refusal> {
    let Some(argv0) = req.argv.first() else {
        return Err(Refusal {
            error: bad("argv must have 1-64 items"),
            program: String::new(),
        });
    };
    let name = |a: &str| {
        let a = Path::new(a);
        let real = std::fs::canonicalize(a).ok();
        real.as_deref()
            .unwrap_or(a)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    };
    let refuse = |r: allowlist::Refused, argv0: &str| Refusal {
        error: ApiError::new(r.code(), r.message()),
        program: name(argv0),
    };
    let real = allowlist::resolve(argv0, p).map_err(|r| refuse(r, argv0))?;
    allowlist::env_allowed(p, req.env.iter().map(|(k, _)| k.as_str()))
        .map_err(|r| refuse(r, argv0))?;
    let real = real
        .into_os_string()
        .into_string()
        .map_err(|_| refuse(allowlist::Refused::Program, argv0))?;
    // S89-live-2: codex speaks a protocol aibd measured on one version (app-server is marked
    // experimental); another version is refused, saying which, before anything starts
    if crate::codex_wire::is_codex(&real) {
        let out = std::process::Command::new(&real)
            .arg("--version")
            .stdin(std::process::Stdio::null())
            .output();
        let printed = out
            .as_ref()
            .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
            .unwrap_or_default();
        if !aib_seatopen::codex_version_ok(&printed) {
            let said = printed
                .lines()
                .last()
                .unwrap_or("")
                .trim()
                .chars()
                .take(60)
                .collect::<String>();
            return Err(Refusal {
                error: ApiError::new(
                    ErrorCode::ProgramNotAllowed,
                    format!("codex version not measured for aibd (0.167.x only): {said}"),
                ),
                program: name(&real),
            });
        }
    }
    req.argv[0] = real;
    Ok(req)
}

struct Entry {
    rec: Session,
    run: Arc<Mutex<Box<dyn Running>>>,
    /// The session's stdin (a seat's session only, S89-input); `None` = it takes no input.
    input: Option<Arc<InputPipe>>,
    /// A stop (DELETE, root exit, shutdown) is running on this session.
    stopping: bool,
    /// Next event sequence for this session's aggregate.
    seq: i64,
    order: u64,
    /// A background re-check of its slot is running (one at a time).
    rescanning: bool,
}

/// A session waiting for a slot: what is needed to start it later. Its env stays in
/// memory only (never stored, never returned).
struct Queued {
    rec: Session,
    /// `argv[0]` as the request gave it, before the M1 gate resolved it:
    /// the gate runs again at dequeue (043dd M2-1 / ADR 0003: every spawn
    /// resolves the real path again). `None` = started without the gate
    /// (tests calling [`Sessions::start`] directly).
    asked: Option<String>,
    cwd: PathBuf,
    env: Vec<(String, String)>,
    /// [`Request::withhold`], kept for the start.
    withhold: Vec<String>,
    /// [`Request::input`], kept for the start.
    input: bool,
    /// The request's `timeout_s` (S79-p11b), counted from the start, not the queue.
    timeout: Option<Duration>,
    /// Next event sequence (`session.queued` was 1).
    seq: i64,
    order: u64,
}

#[derive(Default)]
struct Inner {
    /// Waiting for a slot, oldest first (ADR 0005 §5).
    queue: VecDeque<Queued>,
    /// Running sessions, and stop_unconfirmed ones still holding a slot.
    live: HashMap<String, Entry>,
    ended: VecDeque<(u64, Session)>,
    /// Slots taken by POSTs that are spawning right now (H3).
    reserved: usize,
    /// Starts that failed but may have left a process nobody controls
    /// (`SpawnFail::left_running`): their slots stay taken (H2, 043dd #539).
    held_unknown: usize,
    shutting: bool,
    /// A dequeue thread is running (043dd L1: one at a time).
    dequeuing: bool,
    next_order: u64,
    /// S89-state (5d33a L1): the seat of each start whose slot is reserved but not yet in
    /// `live` (one entry per reserved seat start), so archiving sees it as busy.
    starting: Vec<String>,
    /// S89-state: archived seats; nothing starts on them (read back from the store at start).
    archived: std::collections::HashSet<String>,
    /// S89-state (5d33a 853ee100 L2): the archived seats could not be read at start, so no
    /// seat's session starts (an unknown list is not an empty one).
    archived_unknown: bool,
    /// S89-state (5d33a 853ee100 L1): the seat of each failed start that may have left a
    /// process running (`held_unknown`); such a seat stays busy, as its slot stays taken.
    held: Vec<String>,
}

impl Inner {
    fn started(&mut self, seat_id: Option<&String>) {
        if let Some(sid) = seat_id {
            if let Some(i) = self.starting.iter().position(|x| x == sid) {
                self.starting.swap_remove(i);
            }
        }
    }

    /// The seat has a session that is running, waiting, being started, or stopped without
    /// confirmation (still holding its slot).
    fn seat_busy(&self, seat_id: &str) -> bool {
        self.starting.iter().any(|x| x == seat_id)
            || self
                .queue
                .iter()
                .any(|q| q.rec.seat_id.as_deref() == Some(seat_id)) // waits for a slot
            || self
                .live
                .values()
                .any(|e| e.rec.seat_id.as_deref() == Some(seat_id))
            || self.held.iter().any(|x| x == seat_id)
    }

    /// Why a session may not start on this seat now (S89-state): the seat is archived, or
    /// the archived seats could not be read at start (5d33a L2). Checked under the lock
    /// archiving takes.
    fn seat_refused(&self, seat_id: Option<&String>) -> Option<ApiError> {
        let sid = seat_id?;
        if self.archived_unknown {
            return Some(ApiError::new(
                ErrorCode::Internal,
                "the archived seats could not be read when aibd started; restart aibd",
            ));
        }
        if self.archived.contains(sid) {
            return Some(ApiError::new(
                ErrorCode::SeatArchived,
                "the seat was archived",
            ));
        }
        None
    }
}

/// The archived seats read at start, and whether the read failed (S89-state, 5d33a L2).
pub(crate) fn archived_from(
    r: aib_store::Result<Vec<aib_store::Seat>>,
) -> (std::collections::HashSet<String>, bool) {
    match r {
        Ok(all) => (
            all.into_iter()
                .filter(|x| x.state == aib_store::ARCHIVED)
                .map(|x| x.seat_id)
                .collect(),
            false,
        ),
        Err(err) => {
            tracing::error!(error = %err, "could not read archived seats; no seat session starts");
            (std::collections::HashSet::new(), true)
        }
    }
}

pub struct Sessions {
    inner: Mutex<Inner>,
    cv: Condvar,
    store: Arc<Store>,
    lineage: Option<Arc<Lineage>>,
    spawn: SpawnFn,
    max: usize,
    default_cwd: PathBuf,
    /// How long the background slot re-check runs (a63ab: 30 s; tests shorten it).
    /// (window, longest pass): test hook only changes them.
    rescan_window: Mutex<(Duration, Duration)>,
    /// M1 gate rejections, folded per token + code per minute.
    rejections: allowlist::RejectLog,
    /// The M1 gate, run again when a queued session leaves the queue.
    /// Set by the server ([`Sessions::set_policy`]); `None` = no re-check.
    policy: Mutex<Option<Arc<SessionPolicy>>>,
    /// Set once by the server ([`Sessions::set_hooks`]); unset = defaults.
    hooks: std::sync::OnceLock<Hooks>,
    /// A11 who-is-alive, set once by the server ([`Sessions::set_alive`]).
    alive: std::sync::OnceLock<Arc<crate::alive::Alive>>,
    /// N-revive L7: the sessions aibd brought back at its start, and when ([`Sessions::mark_rejoined`]).
    rejoined: Mutex<std::collections::HashMap<String, std::time::Instant>>,
    /// B1dm-S5: told the seat of every session that starts (direct or out of the queue), so
    /// what was sent to it while it was not running is written now (seat_send::redeliver).
    started: std::sync::OnceLock<tokio::sync::mpsc::UnboundedSender<String>>,
    /// N-workspace: which seats are in a turn (a write starts one, the provider's result line
    /// ends it); the end of a turn is told on `started` too, so what waited is written then.
    turns: Arc<crate::turns::Turns>,
    /// N-workspace: what a seat has not seen of its shared conversations, put in front of the
    /// text that starts its next turn. Set once by the server; unset = nothing added.
    catch_up: std::sync::OnceLock<Arc<dyn crate::catch_up::CatchUp>>,
    /// S89-live-2: persona text for a codex seat's next session ([`Sessions::set_persona`]).
    personas: Mutex<std::collections::HashMap<String, String>>,
    me: std::sync::Weak<Sessions>,
}

/// The stored form of a session: argv[0]'s file name and the count only,
/// never argv or env (spec 9.4).
fn row_of(s: &Session) -> SessionRow {
    SessionRow {
        session_id: s.id.clone(),
        seat_id: s.seat_id.clone(),
        pid: s.pid,
        argv0: s
            .argv
            .first()
            .and_then(|a| Path::new(a).file_name())
            .map(|n| n.to_string_lossy().into_owned()),
        argc: i64::try_from(s.argv.len()).unwrap_or(i64::MAX),
        state: s.state.to_owned(),
        holds_slot: s.holds_slot,
        started_at: s.started_at.clone(),
        ended_at: s.ended_at.clone(),
        ended_by: s.ended_by.map(str::to_owned),
        exit_code: s.exit_code,
        signal: s.signal.clone(),
        stop_status: s.stop.as_ref().map(|v| v.status.to_owned()),
        stop_reason: s.stop.as_ref().and_then(|v| v.reason.clone()),
        updated_at: now_rfc3339(),
    }
}

/// Seats of the running sessions in `g`, once each.
fn live_seats_of(g: &Inner) -> Vec<String> {
    let mut v: Vec<String> = g
        .live
        .values()
        .filter(|e| e.rec.state == "running")
        .filter_map(|e| e.rec.seat_id.clone())
        .collect();
    v.sort();
    v.dedup();
    v
}

/// The longest a text waits for the seat's provider to take it.
pub const INPUT_WAIT: Duration = Duration::from_secs(5);

/// A seat session's stdin (S89-input) and whether a write to it is still under way.
struct InputPipe {
    w: Mutex<crate::platform::session::Input>,
    busy: std::sync::atomic::AtomicBool,
    /// S89-live-2: the session is `codex app-server`; texts go as its `turn/start`.
    codex: Option<Arc<crate::codex_wire::Codex>>,
}

/// Where [`Sessions::write_input`] put a text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Written {
    pub session_id: String,
    /// The provider had not taken it within the limit: it is still being written and arrives
    /// when the provider reads again (lost only if the pipe closes first).
    pub late: bool,
}

/// Why [`Sessions::write_input`] wrote nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputError {
    /// The seat has no running session (never started, ended, or stopping).
    NoSession,
    /// S89-live-4: the seat's session is still waiting for a free slot (`pending`), so
    /// there is no process to write to yet; said as queued, not as "not running".
    Queued,
    /// Its session takes no input (started without a stdin pipe).
    NoInput,
    /// The pipe is closed: the provider stopped reading.
    Closed,
    /// An earlier text is still being written (the provider has not taken it): nothing was
    /// written, the text can be sent again later.
    Busy,
    /// N-workspace: the seat is in a turn; a message from another seat waits for its end.
    InTurn,
    /// S89-live-2: the seat's provider is still starting its conversation (codex: thread/start
    /// not answered yet); a message waits, written once it is.
    Starting,
}

impl InputError {
    pub fn code(self) -> &'static str {
        match self {
            InputError::NoSession => "seat_not_running",
            InputError::Queued => "seat_queued",
            InputError::NoInput => "seat_takes_no_input",
            InputError::Closed => "seat_input_closed",
            InputError::Busy => "seat_input_busy",
            InputError::InTurn => "seat_in_turn",
            InputError::Starting => "seat_starting",
        }
    }
}

fn lock(m: &Mutex<Inner>) -> MutexGuard<'_, Inner> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn lock_map<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// `Retry-After` on `thermal_nogo`: the guard re-reads no faster than this.
const THERMAL_RETRY_AFTER_SECS: &str = "600";

/// What the thermal guard (ADR 0005 §1, R1 PR 3) plugs into the sessions.
///
/// Both are called **with the sessions lock held**: they may only read or
/// write their own state (atomics) and must never call back into
/// [`Sessions`], which would deadlock. To make the queue move after a NOGO
/// ends, call [`Sessions::kick_now`] from outside the hook.
pub struct Hooks {
    /// `false` ⇒ no session starts: a POST answers `thermal_nogo` (not
    /// queued) and the queue holds what it has.
    pub launch_allowed: Arc<dyn Fn() -> bool + Send + Sync>,
    /// `(old, new)` count of `live` entries (running and stop_unconfirmed),
    /// called only when it changed.
    pub on_running_changed: Arc<dyn Fn(usize, usize) + Send + Sync>,
}

impl Default for Hooks {
    fn default() -> Self {
        Hooks {
            launch_allowed: Arc::new(|| true),
            on_running_changed: Arc::new(|_, _| {}),
        }
    }
}

/// 043dd f6500f85 L1: a dequeue thread that panics must not leave
/// `dequeuing` set, or no kick would ever start another and the queue would
/// stand still. A normal return clears it itself, under the lock that saw
/// nothing to do; this only acts while unwinding.
struct DequeueGuard<'a>(&'a Mutex<Inner>);

impl Drop for DequeueGuard<'_> {
    fn drop(&mut self) {
        if std::thread::panicking() {
            lock(self.0).dequeuing = false;
        }
    }
}

impl Sessions {
    /// `default_cwd` (`<data dir>/sessions`) must exist.
    /// aibd's private sessions folder (also the default cwd): where a seat's checked persona
    /// text is kept for its provider to read (S89-live-persona).
    pub fn private_dir(&self) -> &std::path::Path {
        &self.default_cwd
    }

    pub fn new(
        store: Arc<Store>,
        lineage: Option<Arc<Lineage>>,
        spawn: SpawnFn,
        max: usize,
        default_cwd: PathBuf,
    ) -> Arc<Sessions> {
        // a session still open when the previous aibd stopped is
        // `interrupted`, never re-attached (DECISION-11 B, ADR 0004)
        match store.interrupt_open_sessions(now_rfc3339(), || {
            new_id("ev_").unwrap_or_else(|_| format!("ev_restart_{}", now_rfc3339()))
        }) {
            Ok(ids) if !ids.is_empty() => {
                tracing::warn!(
                    count = ids.len(),
                    "sessions open at the last stop marked interrupted"
                );
            }
            Ok(_) => {}
            Err(err) => tracing::error!(error = %err, "could not mark open sessions interrupted"),
        }
        let (archived, archived_unknown) = archived_from(store.seats());
        Arc::new_cyclic(|me| Sessions {
            inner: Mutex::new(Inner {
                archived,
                archived_unknown,
                ..Inner::default()
            }),
            cv: Condvar::new(),
            store,
            lineage,
            spawn,
            max,
            default_cwd,
            rescan_window: Mutex::new((RESCAN_WINDOW, RESCAN_PASS_MIN)),
            rejections: allowlist::RejectLog::default(),
            policy: Mutex::new(None),
            hooks: std::sync::OnceLock::new(),
            alive: std::sync::OnceLock::new(),
            rejoined: Mutex::new(std::collections::HashMap::new()),
            started: std::sync::OnceLock::new(),
            turns: Arc::default(),
            catch_up: std::sync::OnceLock::new(),
            personas: Mutex::default(),
            me: me.clone(),
        })
    }

    /// S89-state (IFACE-S89-state-archive-043dd-1118 r1): archive the seat unless it has a
    /// session running, waiting, being started or not confirmed stopped (`seat_busy`). The
    /// check, the store write and the archived mark happen under the lock every start
    /// takes (5d33a L1), so no session can start in between.
    pub fn archive_seat(
        &self,
        seat_id: &str,
        by: &str,
    ) -> Result<aib_store::SeatArchive, ApiError> {
        let event_id =
            new_id("ev_").map_err(|_| ApiError::new(ErrorCode::Internal, "internal error"))?;
        let mut g = lock(&self.inner);
        if g.seat_busy(seat_id) {
            // 5d33a 5faca987 N1: a held start is cleared only by a restart, not by a stop
            let why = if g.held.iter().any(|x| x == seat_id) {
                "a failed start on this seat may have left a process running; restart aibd first"
            } else {
                "the seat has a session; stop it first"
            };
            return Err(ApiError::new(ErrorCode::SeatBusy, why));
        }
        let r = self
            .store
            .archive_seat(seat_id, event_id, now_rfc3339(), by)
            .map_err(|_| ApiError::new(ErrorCode::Internal, "internal error"))?;
        if !matches!(r, aib_store::SeatArchive::NotFound) {
            g.archived.insert(seat_id.to_owned());
        }
        Ok(r)
    }

    /// A11: tell `alive` when a seat's session starts and ends. Set once.
    pub fn set_alive(&self, a: Arc<crate::alive::Alive>) {
        let _ = self.alive.set(a);
    }

    /// Where the seat of each session that starts is sent (B1dm-S5; set once at start).
    pub fn set_started(&self, tx: tokio::sync::mpsc::UnboundedSender<String>) {
        let _ = self.started.set(tx);
    }

    /// Seats with a running session now (A11; reads only memory).
    pub fn live_seats(&self) -> Vec<String> {
        live_seats_of(&lock(&self.inner))
    }

    /// Seats that are running or on their way to it: a live session (any state still in
    /// `live`), a start being spawned, a session waiting in the queue (0eed7 VERIFY-turnfail r2
    /// 2: a seat just opened is not said to be not running).
    pub fn seats_up(&self) -> Vec<String> {
        let g = lock(&self.inner);
        let mut v: Vec<String> = g
            .live
            .values()
            .filter_map(|e| e.rec.seat_id.clone())
            .chain(g.starting.iter().cloned())
            .chain(g.queue.iter().filter_map(|q| q.rec.seat_id.clone()))
            .collect();
        v.sort();
        v.dedup();
        v
    }

    /// The M1 policy the queue re-checks a waiting session against when it
    /// leaves the queue (the same one the POST gate used).
    pub fn set_policy(&self, p: Arc<SessionPolicy>) {
        *self.policy.lock().unwrap_or_else(|e| e.into_inner()) = Some(p);
    }

    /// The policy's env names: a child inherits aibd's own value of each (T80,
    /// [`crate::platform::session::child_env`]). None before [`Self::set_policy`].
    fn pass_names(&self) -> Vec<String> {
        self.policy
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
            .map(|p| p.env.clone())
            .unwrap_or_default()
    }

    /// [`Self::pass_names`] without `withhold` (names compared as the platform does).
    fn pass_names_without(&self, withhold: &[String]) -> Vec<String> {
        without(self.pass_names(), withhold)
    }

    /// The thermal guard's hooks ([`Hooks`]). Once: a second call is
    /// refused (`false`) and the first stays. Set under the sessions lock and
    /// told the current count at once (`(0, n)` when sessions already run), so
    /// a guard set late still hears its 0→n (a9136 3a L1).
    pub fn set_hooks(&self, h: Hooks) -> bool {
        let g = lock(&self.inner);
        if self.hooks.set(h).is_err() {
            return false;
        }
        self.running_changed(0, &g);
        true
    }

    /// Caller holds the lock.
    fn launch_allowed(&self) -> bool {
        self.hooks.get().is_none_or(|h| (h.launch_allowed)())
    }

    /// Caller holds the lock; `old` = `live.len()` before the change.
    fn running_changed(&self, old: usize, g: &Inner) {
        let new = g.live.len();
        if old != new {
            if let Some(h) = self.hooks.get() {
                (h.on_running_changed)(old, new);
            }
        }
    }

    /// Start waiting sessions now if slots are free: the thermal guard calls
    /// this when a NOGO ends, or the queue would wait for the next slot change.
    pub fn kick_now(&self) {
        let mut g = lock(&self.inner);
        self.kick(&mut g);
    }

    /// Test hook: a shorter background re-check window (and, since test
    /// stops return at once, a shorter least time a pass needs).
    #[doc(hidden)]
    pub fn set_rescan_window_for_test(&self, window: Duration, pass_min: Duration) {
        *self.rescan_window.lock().unwrap_or_else(|e| e.into_inner()) = (window, pass_min);
    }

    /// Newest first: running and stop_unconfirmed, then the recent ended.
    pub fn list(&self) -> Vec<Session> {
        let g = lock(&self.inner);
        let mut v: Vec<(u64, Session)> = g
            .live
            .values()
            .map(|e| (e.order, e.rec.clone()))
            .chain(g.queue.iter().map(|q| (q.order, q.rec.clone())))
            .chain(g.ended.iter().cloned())
            .collect();
        v.sort_by_key(|(o, _)| std::cmp::Reverse(*o));
        v.into_iter().map(|(_, s)| s).collect()
    }

    /// S89-input: write `line` to the stdin of `seat_id`'s running session, waiting at most
    /// [`INPUT_WAIT`] (call it off the async runtime). Where it went, or why nothing was.
    pub fn write_input(&self, seat_id: &str, line: &[u8]) -> Result<Written, InputError> {
        self.write_input_within(seat_id, line, INPUT_WAIT)
    }

    /// N-workspace: [`Sessions::write_input`] for a message another seat sends: refused with
    /// [`InputError::InTurn`] while `seat_id` is in a turn, so it waits for the turn to end.
    pub fn write_input_between_turns(
        &self,
        seat_id: &str,
        line: &[u8],
    ) -> Result<Written, InputError> {
        if self.turns.in_turn(seat_id) {
            return Err(InputError::InTurn);
        }
        self.write_input(seat_id, line)
    }

    /// S89-live-2: the persona card a codex seat's session hands over in thread/start, set by
    /// seats/open just before the session starts and taken once by it.
    pub fn set_persona(&self, seat_id: &str, text: String) {
        lock_map(&self.personas).insert(seat_id.to_owned(), text);
    }

    fn take_persona(&self, seat_id: &str) -> Option<String> {
        lock_map(&self.personas).remove(seat_id)
    }

    /// Set the catch-up (once; the server does it with the message library).
    pub fn set_catch_up(&self, c: Arc<dyn crate::catch_up::CatchUp>) {
        let _ = self.catch_up.set(c);
    }

    /// Whether `seat_id` is in a turn now (a text was written, its result not printed yet).
    pub fn in_turn(&self, seat_id: &str) -> bool {
        self.turns.in_turn(seat_id)
    }

    /// The write runs on its own thread: a provider that stops reading fills the pipe and blocks
    /// it. After `wait` the text is handed over but not yet taken ([`Written::late`]): the thread
    /// still writes it when the pipe drains (or drops it if the pipe closes), so the caller must
    /// not say "nothing was written" (5d33a M1 on 29e68425: a resend would be done twice).
    /// Until that thread ends every other text to that seat is [`InputError::Busy`] at once,
    /// with nothing written (no write without a limit, no lock held by a stuck write).
    fn write_input_within(
        &self,
        seat_id: &str,
        line: &[u8],
        wait: Duration,
    ) -> Result<Written, InputError> {
        use std::io::Write as _;
        use std::sync::atomic::Ordering::SeqCst;
        let (id, pipe) = {
            let g = lock(&self.inner);
            let Some((id, e)) = g
                .live
                .iter()
                .find(|(_, e)| e.rec.seat_id.as_deref() == Some(seat_id) && !e.stopping)
            else {
                // S89-live-4: a seat whose session waits for a slot is queued, not stopped
                let queued = g
                    .queue
                    .iter()
                    .any(|q| q.rec.seat_id.as_deref() == Some(seat_id));
                return Err(if queued {
                    InputError::Queued
                } else {
                    InputError::NoSession
                });
            };
            (id.clone(), e.input.clone().ok_or(InputError::NoInput)?)
        };
        if pipe.busy.swap(true, SeqCst) {
            return Err(InputError::Busy);
        }
        // N-workspace: a text that starts a turn carries what the seat has not seen of its
        // shared conversations (catch_up); its read marks move only once it is written
        let caught = (!self.turns.in_turn(seat_id))
            .then(|| self.catch_up.get())
            .flatten()
            .and_then(|c| c.unseen(seat_id).map(|u| (c.clone(), u)));
        let line = caught
            .as_ref()
            .and_then(|(_, u)| crate::catch_up::prepend(line, &u.block))
            .unwrap_or_else(|| line.to_vec());
        // S89-live-2: a codex seat takes the same line as its turn/start, once its thread is
        // there; before that nothing is written and the text waits (seat_starting)
        let line = match pipe.codex.as_ref() {
            None => line,
            Some(c) => match c.turn_line(&line) {
                Some(l) => l,
                None => {
                    pipe.busy.store(false, SeqCst);
                    return Err(if c.thread().is_none() {
                        InputError::Starting
                    } else {
                        InputError::NoInput
                    });
                }
            },
        };
        // The turn begins before the write: a provider can answer (and its turn end be read)
        // before this thread wakes, and a began after that would leave the seat in a turn it
        // already ended. A write that fails takes it back.
        self.turns.began(seat_id);
        let done = Arc::new((Mutex::new(0u64), std::sync::Condvar::new()));
        let result: Arc<Mutex<Option<bool>>> = Arc::new(Mutex::new(None));
        let (p2, d2, r2) = (pipe.clone(), done.clone(), result.clone());
        let (turns, seat) = (self.turns.clone(), seat_id.to_owned());
        let spawned = std::thread::Builder::new()
            .name("aibd-seat-input".into())
            .spawn(move || {
                let ok = {
                    let mut w = p2.w.lock().unwrap_or_else(|e| e.into_inner());
                    w.write_all(&line).and_then(|()| w.flush()).is_ok()
                };
                // the read marks move once it is written and before the next text to this
                // seat can start (it waits for busy): that text does not carry the same block
                // again (5d33a catch-up N1; integ1-2cf949fe1 told_twice)
                let carried_marks_moved = ok;
                if let (true, Some((c, u))) = (carried_marks_moved, caught.as_ref()) {
                    c.seen(&seat, &u.marks);
                }
                if !ok {
                    turns.ended(&seat);
                }
                p2.busy.store(false, SeqCst);
                *r2.lock().unwrap_or_else(|e| e.into_inner()) = Some(ok);
                let (m, cv) = &*d2;
                *m.lock().unwrap_or_else(|e| e.into_inner()) += 1;
                cv.notify_all();
            });
        if spawned.is_err() {
            self.turns.ended(seat_id);
            pipe.busy.store(false, SeqCst);
            return Err(InputError::Closed);
        }
        let op = crate::timers::SEAT_INPUT_WRITE_WAIT.begin(Some(Instant::now() + wait));
        let mut seen = 0u64;
        loop {
            if let Some(ok) = *result.lock().unwrap_or_else(|e| e.into_inner()) {
                return if ok {
                    Ok(Written {
                        session_id: id,
                        late: false,
                    })
                } else {
                    Err(InputError::Closed)
                };
            }
            if !op.tick_or_wake(&done.0, &done.1, &mut seen) {
                // handed over: the provider takes it when it reads again, a turn all the same
                // (began above; the marks move when the thread's write lands)
                return Ok(Written {
                    session_id: id,
                    late: true,
                });
            }
        }
    }

    pub fn get(&self, id: &str) -> Option<Session> {
        let g = lock(&self.inner);
        g.live
            .get(id)
            .map(|e| e.rec.clone())
            .or_else(|| {
                let i = g.queue.iter().position(|q| q.rec.id == id)?;
                let mut rec = g.queue[i].rec.clone();
                rec.queue = Some(self.queue_view(&g, i + 1));
                Some(rec)
            })
            .or_else(|| {
                g.ended
                    .iter()
                    .find(|(_, s)| s.id == id)
                    .map(|(_, s)| s.clone())
            })
    }

    /// 043dd M2: what a pending session waits for, so a queue that does not
    /// move says why (a stop that kept its slot shows its reason).
    fn queue_view(&self, g: &Inner, position: usize) -> QueueView {
        let mut held_by: Vec<(u64, SlotHolder)> = g
            .live
            .iter()
            .map(|(id, e)| {
                (
                    e.order,
                    SlotHolder {
                        id: id.clone(),
                        state: e.rec.state,
                        reason: e.rec.stop.as_ref().and_then(|s| s.reason.clone()),
                    },
                )
            })
            .collect();
        held_by.sort_by_key(|(o, _)| *o);
        QueueView {
            position,
            slots: self.max,
            held_by: held_by.into_iter().map(|(_, h)| h).collect(),
            held_unknown: g.held_unknown,
        }
    }

    /// How many sessions may run at once (the slots; S89-live-4 says it to a queued seat).
    pub fn max_running(&self) -> usize {
        self.max
    }

    /// Running slots in use (live + reserved), for tests and metrics.
    pub fn slots_in_use(&self) -> usize {
        let g = lock(&self.inner);
        g.live.len() + g.reserved + g.held_unknown
    }

    /// Sessions waiting for a slot, for tests and metrics.
    pub fn queued(&self) -> usize {
        lock(&self.inner).queue.len()
    }

    /// POST. Blocking (spawn, event store).
    pub fn start(self: &Arc<Self>, req: Request) -> Result<Session, ApiError> {
        self.start_for_seat(None, req)
    }

    /// POST for a seat (`POST /api/v1/seats/{id}/sessions`); the caller has
    /// checked that the seat exists (the store refuses an unknown one too).
    /// A free slot with nobody waiting ⇒ started now (`running`); otherwise
    /// queued (`pending`, ADR 0005 §5), or `session_queue_full`.
    pub fn start_for_seat(
        self: &Arc<Self>,
        seat_id: Option<String>,
        req: Request,
    ) -> Result<Session, ApiError> {
        self.start_gated(seat_id, req, None)
    }

    /// POST after the M1 gate: `asked` is `argv[0]` as the request gave it
    /// (before [`admit`] resolved it), kept so the gate runs again if the
    /// session has to wait (043dd M2-1).
    pub fn start_gated(
        self: &Arc<Self>,
        seat_id: Option<String>,
        req: Request,
        asked: Option<String>,
    ) -> Result<Session, ApiError> {
        let id = new_id("s_").map_err(|_| ApiError::new(ErrorCode::Internal, "internal error"))?;
        {
            let mut g = lock(&self.inner);
            if g.shutting {
                return Err(ApiError::new(
                    ErrorCode::ShuttingDown,
                    "aibd is shutting down",
                ));
            }
            if let Some(e) = g.seat_refused(seat_id.as_ref()) {
                return Err(e);
            }
            if !self.launch_allowed() {
                // not queued: a hot machine is not a full one (R1 PR 3)
                return Err(ApiError::new(
                    ErrorCode::ThermalNogo,
                    "the machine is too hot to start a session; try again later",
                ));
            }
            // FIFO: a new POST never overtakes one that is already waiting
            if !g.queue.is_empty() || g.live.len() + g.reserved + g.held_unknown >= self.max {
                if g.queue.len() >= MAX_QUEUE {
                    return Err(ApiError::new(
                        ErrorCode::SessionQueueFull,
                        format!("{} sessions running and {MAX_QUEUE} waiting", self.max),
                    ));
                }
                let rec = self.enqueue(&mut g, id, seat_id, req, asked);
                // 043dd L2: a free slot with a queue (its dequeue thread could
                // not start) must not wait for the next slot change
                self.kick(&mut g);
                return Ok(rec);
            }
            g.reserved += 1; // H3: the slot is ours before anything starts
            if let Some(sid) = &seat_id {
                g.starting.push(sid.clone());
            }
        }
        let cwd = req.cwd.unwrap_or_else(|| self.default_cwd.clone());
        let timeout = req.timeout;
        let spec = Spec {
            id,
            argv: req.argv,
            cwd,
            env: req.env,
            pass: self.pass_names_without(&req.withhold),
            input: req.input, // S89-live-9: a stdin pipe only for a seat's provider
        };
        self.launch(spec, seat_id, None, timeout)
    }

    /// Put a session in the queue (the caller holds the lock and checked the bound).
    fn enqueue(
        &self,
        g: &mut Inner,
        id: String,
        seat_id: Option<String>,
        req: Request,
        asked: Option<String>,
    ) -> Session {
        let order = g.next_order;
        g.next_order += 1;
        let cwd = req.cwd.unwrap_or_else(|| self.default_cwd.clone());
        let rec = Session {
            id: id.clone(),
            seat_id,
            argv: req.argv,
            cwd: cwd.to_string_lossy().into_owned(),
            pid: 0,
            started_at: now_rfc3339(),
            state: "pending",
            exit_code: None,
            signal: None,
            ended_at: None,
            ended_by: None,
            holds_slot: false,
            stop: None,
            queue: None,
        };
        let position = g.queue.len() + 1;
        self.write(
            &rec,
            1,
            "session.queued",
            serde_json::json!({
                "id": id,
                "position": position,
                "argv0": Path::new(&rec.argv[0]).file_name().map(|n| n.to_string_lossy().into_owned()),
                "argc": rec.argv.len(),
            }),
        );
        tracing::info!(session = %id, position, "session queued");
        g.queue.push_back(Queued {
            rec: rec.clone(),
            asked,
            cwd,
            env: req.env,
            withhold: req.withhold,
            input: req.input,
            timeout: req.timeout,
            seq: 2,
            order,
        });
        rec
    }

    /// Start a session whose slot is already reserved: a POST with a free
    /// slot (`queued` = `None`), or the head of the queue (its order and next
    /// event sequence).
    fn launch(
        self: &Arc<Self>,
        spec: Spec,
        seat_id: Option<String>,
        queued: Option<(u64, i64)>,
        timeout: Option<Duration>,
    ) -> Result<Session, ApiError> {
        let id = spec.id.clone();
        let res = (self.spawn)(&spec, self.lineage.clone());
        let mut g = lock(&self.inner);
        g.reserved -= 1;
        g.started(seat_id.as_ref());
        self.cv.notify_all();
        let mut run = match res {
            Ok(r) => r,
            Err(f) => {
                if f.left_running.is_some() {
                    // started but neither under control nor confirmed gone:
                    // the slot stays taken (never "not started")
                    g.held_unknown += 1;
                    if let Some(sid) = &seat_id {
                        g.held.push(sid.clone());
                    }
                } else {
                    // the slot is free again: the next waiting one may start
                    self.kick(&mut g);
                }
                if let Some(sid) = &seat_id {
                    crate::reply_back::failed(sid, &format!("這一席起不來:{}", f.tag));
                }
                let mut payload = serde_json::json!({"id": id, "tag": f.tag});
                if let Some(pid) = f.left_running {
                    payload["left_running"] = pid.into();
                    tracing::warn!(session = %id, pid, tag = %f.tag, "start failed, a process may be left running");
                }
                if let Some((order, seq)) = queued {
                    // nobody waits for this answer: the session itself ends
                    // `failed`, on its row and in the recent list
                    let rec = Session {
                        id: id.clone(),
                        seat_id,
                        argv: spec.argv.clone(),
                        cwd: spec.cwd.to_string_lossy().into_owned(),
                        pid: 0,
                        started_at: now_rfc3339(),
                        state: "failed",
                        exit_code: None,
                        signal: None,
                        ended_at: Some(now_rfc3339()),
                        ended_by: Some("spawn_failed"),
                        holds_slot: f.left_running.is_some(),
                        stop: None,
                        queue: None,
                    };
                    self.write(&rec, seq, "session.spawn_failed", payload);
                    g.ended.push_front((order, rec));
                    g.ended.truncate(KEEP_ENDED);
                } else if f.left_running.is_some() {
                    self.append(&id, 1, "session.spawn_failed", payload);
                }
                return Err(ApiError::new(
                    ErrorCode::SpawnFailed,
                    match f.left_running {
                        Some(pid) => format!(
                            "could not start: {} (a process may be left running: pid {pid})",
                            f.tag
                        ),
                        None => format!("could not start: {}", f.tag),
                    },
                ));
            }
        };
        let (order, seq) = match queued {
            Some(q) => q,
            None => {
                let o = g.next_order;
                g.next_order += 1;
                (o, 1)
            }
        };
        let rec = Session {
            id: id.clone(),
            seat_id,
            argv: spec.argv.clone(),
            cwd: spec.cwd.to_string_lossy().into_owned(),
            pid: run.pid(),
            started_at: now_rfc3339(),
            state: "running",
            exit_code: None,
            signal: None,
            ended_at: None,
            ended_by: None,
            holds_slot: true,
            stop: None,
            queue: None,
        };
        let exit_wait = run.exit_wait();
        let output = run.take_output();
        // S89-live-2: a codex seat is spoken to in its protocol; its three opening lines go
        // first, before anything else can be written (the pipe is not in `live` yet)
        let codex = crate::codex_wire::is_codex(&spec.argv[0])
            .then(|| Arc::new(crate::codex_wire::Codex::default()));
        let opening = codex.as_ref().map(|_| {
            let persona = rec.seat_id.as_deref().and_then(|s| self.take_persona(s));
            crate::codex_wire::opening(&rec.cwd, persona.as_deref())
        });
        let input = run.take_input().map(|mut w| {
            if let Some(o) = &opening {
                if let Err(e) = w.write_all(o).and_then(|()| w.flush()) {
                    tracing::warn!(session = %id, error = %e, "codex opening not written");
                }
            }
            Arc::new(InputPipe {
                w: Mutex::new(w),
                busy: std::sync::atomic::AtomicBool::new(false),
                codex: codex.clone(),
            })
        });
        let shutting = g.shutting;
        let old = g.live.len();
        g.live.insert(
            id.clone(),
            Entry {
                rec: rec.clone(),
                run: Arc::new(Mutex::new(run)),
                input,
                stopping: shutting,
                seq,
                order,
                rescanning: false,
            },
        );
        self.running_changed(old, &g);
        self.event(
            &mut g,
            &id,
            "session.started",
            serde_json::json!({
                "id": id,
                "pid": rec.pid,
                "argv0": Path::new(&spec.argv[0]).file_name().map(|n| n.to_string_lossy().into_owned()),
                "argc": spec.argv.len(),
            }),
        );
        tracing::info!(session = %id, pid = rec.pid, "session started");
        if let (Some(a), Some(seat)) = (self.alive.get(), rec.seat_id.as_deref()) {
            a.on_start(seat, live_seats_of(&g));
        }
        // never blocks (an unbounded channel): fine with the sessions lock held
        if let (Some(tx), Some(seat)) = (self.started.get(), rec.seat_id.as_deref()) {
            let _ = tx.send(seat.to_owned());
        }
        crate::usage::board().bind(&id, rec.seat_id.as_deref());
        if let Some(out) = output {
            // after `session.started`, so the first line comes after it on
            // the global cursor; the readers never take `inner`
            let pipe = g.live.get(&id).and_then(|e| e.input.clone());
            let on_stdout = rec.seat_id.clone().map(|seat| {
                // a new session is not in a turn; its result lines end its turns
                self.turns.ended(&seat);
                let (turns, tx) = (self.turns.clone(), self.started.get().cloned());
                let wake = move |tx: &Option<tokio::sync::mpsc::UnboundedSender<String>>| {
                    if let Some(tx) = tx {
                        let _ = tx.send(seat.clone());
                    }
                };
                let seat = rec.seat_id.clone().unwrap_or_default();
                let codex = pipe.as_ref().and_then(|p| p.codex.clone());
                Box::new(move |line: &[u8], cut: bool| match &codex {
                    None => {
                        // W1 condition 6: a work seat's file changes and commands
                        crate::work_audit::heard(&seat, line, cut);
                        if crate::turns::ends_turn(line, cut) {
                            // G13: the answer goes back to the conversation that called it
                            if !cut {
                                crate::reply_back::heard(&seat, line);
                            }
                            if turns.ended(&seat) {
                                wake(&tx);
                            }
                        }
                    }
                    Some(c) => {
                        let h = c.heard(line, cut);
                        if let (Some(r), Some(p)) = (&h.reply, &pipe) {
                            // codex asked aibd something: answered "no" at once
                            let mut w = p.w.lock().unwrap_or_else(|e| e.into_inner());
                            let _ = w.write_all(r).and_then(|()| w.flush());
                        }
                        // the thread is there: what waited for it is written now
                        if h.thread_started || (h.turn_ended && turns.ended(&seat)) {
                            wake(&tx);
                        }
                    }
                }) as crate::session_output::OnLine
            });
            crate::session_output::start_with(self.store.clone(), &id, out, on_stdout);
        }
        if shutting {
            // H3: shutdown began while this spawn ran; it must not escape the
            // sweep, so this request stops it before answering
            let run = g.live.get(&id).map(|e| e.run.clone());
            drop(g);
            if let Some(run) = run {
                let stop = run
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .stop(SHORT_GRACE);
                self.finish(&id, stop, "shutdown");
            }
            return Err(ApiError::new(
                ErrorCode::ShuttingDown,
                "aibd is shutting down",
            ));
        }
        drop(g);
        let me = self.clone();
        let wid = id.clone();
        let spawned = std::thread::Builder::new()
            .name("aibd-session-exit".into())
            .spawn(move || me.watch_root(&wid, exit_wait));
        // S79-p11b: its own deadline thread; no thread = no limit would hold ⇒ stop now
        let spawned = spawned.and_then(|_| match timeout {
            None => Ok(()),
            Some(t) => {
                let me = self.clone();
                let tid = id.clone();
                std::thread::Builder::new()
                    .name("aibd-session-timeout".into())
                    .spawn(move || me.watch_timeout(&tid, t))
                    .map(|_| ())
            }
        });
        if spawned.is_err() {
            // no watcher = nobody would stop it after the root exits: stop now
            if let Some(run) = self.take_for_stop(&id) {
                let stop = run
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .stop(SHORT_GRACE);
                self.finish(&id, stop, "api");
            }
            return Err(ApiError::new(ErrorCode::Internal, "internal error"));
        }
        Ok(rec)
    }

    /// A slot may have come back: start waiting sessions on their own thread
    /// (the caller holds the lock). Nothing to do with an empty queue.
    fn kick(&self, g: &mut Inner) {
        // 043dd L1: one dequeue thread at a time; a running one re-reads the
        // queue and the free slots after every start, so it sees this change
        if g.queue.is_empty() || g.shutting || g.dequeuing {
            return;
        }
        let Some(me) = self.me.upgrade() else {
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("aibd-session-dequeue".into())
            .spawn(move || me.dequeue());
        match spawned {
            Ok(_) => g.dequeuing = true,
            // the next slot change or POST kicks again
            Err(_) => tracing::warn!("could not start the dequeue thread"),
        }
    }

    /// Start waiting sessions, oldest first, while slots are free. Each slot
    /// is reserved under the lock before its spawn (H3, as a POST).
    fn dequeue(self: &Arc<Self>) {
        let _guard = DequeueGuard(&self.inner);
        loop {
            let q = {
                let mut g = lock(&self.inner);
                if g.shutting
                    || g.live.len() + g.reserved + g.held_unknown >= self.max
                    || !self.launch_allowed()
                {
                    g.dequeuing = false; // under the lock that checked: a kick after this starts a new one
                    return;
                }
                let Some(q) = g.queue.pop_front() else {
                    g.dequeuing = false;
                    return;
                };
                g.reserved += 1;
                if let Some(sid) = &q.rec.seat_id {
                    g.starting.push(sid.clone());
                }
                q
            };
            let argv = match self.regate(&q) {
                Ok(argv) => argv,
                Err(code) => {
                    self.refuse_queued(q, code);
                    continue;
                }
            };
            let spec = Spec {
                id: q.rec.id.clone(),
                argv,
                cwd: q.cwd,
                env: q.env,
                pass: self.pass_names_without(&q.withhold),
                input: q.input,
            };
            // errors are recorded on the session itself (`failed`)
            let _ = self.launch(
                spec,
                q.rec.seat_id.clone(),
                Some((q.order, q.seq)),
                q.timeout,
            );
        }
    }

    /// 043dd M2-1 (ADR 0003: every spawn resolves the real path again): the
    /// M1 gate once more on what the request asked for, now that the session
    /// leaves the queue. The argv to start (with `argv[0]` resolved again), or
    /// the refusal's code (audited as at the POST).
    fn regate(&self, q: &Queued) -> Result<Vec<String>, ErrorCode> {
        let policy = self
            .policy
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        let (Some(p), Some(asked)) = (policy, q.asked.as_ref()) else {
            return Ok(q.rec.argv.clone());
        };
        let mut argv = q.rec.argv.clone();
        argv[0] = asked.clone();
        // the seat's own names were set by aibd after the gate (B4-1b-2b), never asked for
        let env = q
            .env
            .iter()
            .filter(|(k, _)| !allowlist::seat_owned(k))
            .cloned()
            .collect();
        let req = Request {
            argv,
            cwd: None,
            env,
            timeout: None,
            withhold: Vec::new(),
            input: false,
        };
        match admit(req, &p) {
            Ok(r) => Ok(r.argv),
            Err(r) => {
                self.record_rejection(Instant::now(), TOKEN_ID_STARTUP, r.error.0, &r.program);
                Err(r.error.0)
            }
        }
    }

    /// A queued session the gate refused at dequeue: it ends `failed`
    /// (`ended_by: policy_refused`), its reserved slot is given back, and the
    /// next one may start.
    fn refuse_queued(&self, q: Queued, code: ErrorCode) {
        let mut g = lock(&self.inner);
        g.reserved -= 1;
        g.started(q.rec.seat_id.as_ref());
        self.cv.notify_all();
        let mut rec = q.rec;
        rec.state = "failed";
        rec.ended_at = Some(now_rfc3339());
        rec.ended_by = Some("policy_refused");
        let id = rec.id.clone();
        tracing::warn!(session = %id, code = code.as_str(), "queued session refused by the M1 gate at start");
        if let Some(seat) = rec.seat_id.as_deref() {
            crate::reply_back::failed(seat, &format!("這一席起不來:{}", code.as_str()));
        }
        self.write(
            &rec,
            q.seq,
            "session.spawn_failed",
            serde_json::json!({"id": id, "tag": "policy_refused", "code": code.as_str()}),
        );
        g.ended.push_front((q.order, rec));
        g.ended.truncate(KEEP_ENDED);
    }

    /// Mark a live session as being stopped; `None` if it is not live or a
    /// stop is already running.
    fn take_for_stop(&self, id: &str) -> Option<Arc<Mutex<Box<dyn Running>>>> {
        let mut g = lock(&self.inner);
        let e = g.live.get_mut(id)?;
        if e.stopping {
            return None;
        }
        e.stopping = true;
        Some(e.run.clone())
    }

    /// Root-exit watcher: woken by the exit, not by polling (the timeout is
    /// only a bound on one wait).
    fn watch_root(&self, id: &str, w: Arc<dyn crate::platform::session::ExitWait>) {
        loop {
            if w.wait(Duration::from_secs(3600)).is_some() {
                break;
            }
            if !lock(&self.inner).live.contains_key(id) {
                return;
            }
        }
        // contract §1: the root exited by itself ⇒ one stop of what it left
        if let Some(run) = self.take_for_stop(id) {
            let stop = run
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .stop(SHORT_GRACE);
            self.finish(id, stop, "root_exit");
        }
    }

    /// S79-p11b deadline watcher: one wake-up at the deadline or on a state change (the
    /// shared condvar), never a polling timer. Still `running` and nobody stopping it at
    /// the deadline ⇒ the DELETE stop (default grace), recorded with `ended_by` `timeout`.
    /// A run that ended, or one already being stopped, is left alone.
    fn watch_timeout(&self, id: &str, t: Duration) {
        let end = Instant::now() + t;
        let mut g = lock(&self.inner);
        loop {
            match g.live.get(id) {
                Some(e) if e.rec.state == "running" && !e.stopping => {}
                _ => return,
            }
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            g = wait_timeout(&self.cv, g, left);
        }
        drop(g);
        if let Some(run) = self.take_for_stop(id) {
            let stop = run
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .stop(DEFAULT_GRACE);
            tracing::info!(session = %id, timeout_s = t.as_secs(), "session timed out");
            self.finish(id, stop, "timeout");
        }
    }

    /// Record a stop's result (H2) and give the slot back only when no
    /// recorded process is known to still run.
    fn finish(&self, id: &str, stop: Stop, by: &'static str) -> Option<Session> {
        let mut g = lock(&self.inner);
        let e = g.live.get_mut(id)?;
        let exit = e
            .run
            .lock()
            .unwrap_or_else(|x| x.into_inner())
            .try_exit()
            .ok()
            .flatten();
        if let Some(x) = &exit {
            e.rec.exit_code = x.code();
            e.rec.signal = x.signal().map(str::to_owned);
        }
        e.rec.state = if !stop.confirmed() {
            "stop_unconfirmed"
        } else if by == "root_exit" {
            "exited"
        } else if by == "timeout" {
            "timeout"
        } else {
            "killed"
        };
        e.rec.ended_at = Some(now_rfc3339());
        e.rec.ended_by = Some(by);
        e.rec.stop = Some(StopView::of(&stop));
        e.rec.holds_slot = !stop.releases_slot();
        e.stopping = false;
        let rec = e.rec.clone();
        let (kind, payload) = match rec.state {
            "exited" => (
                "session.exited",
                serde_json::json!({"id": id, "exit_code": rec.exit_code, "signal": rec.signal,
                    "stop_status": stop.status.as_str(), "survivors": stop.survivors}),
            ),
            "killed" | "timeout" => (
                if rec.state == "timeout" {
                    "session.timeout"
                } else {
                    "session.killed"
                },
                serde_json::json!({"id": id, "ended_by": by, "stop_status": stop.status.as_str(),
                    "reason": stop.status.reason(), "survivors": stop.survivors,
                    "tree_size": stop.tree_size, "candidates": stop.candidates}),
            ),
            _ => (
                "session.stop_unconfirmed",
                serde_json::json!({"id": id, "ended_by": by, "exit_code": rec.exit_code,
                    "signal": rec.signal, "stop_status": stop.status.as_str(),
                    "reason": stop.status.reason(), "survivors": stop.survivors,
                    "tree_size": stop.tree_size, "blind_spots": stop.blind_spots,
                    "candidates": stop.candidates}),
            ),
        };
        self.event(&mut g, id, kind, payload);
        // G13: a turn from a conversation that this end left unanswered says so there
        if let Some(seat) = rec.seat_id.as_deref() {
            crate::reply_back::failed(
                seat,
                &crate::reply_back::ended_why(rec.state, rec.exit_code),
            );
        }
        let back = self
            .rejoined
            .lock()
            .unwrap_or_else(|x| x.into_inner())
            .remove(id);
        if let (Some(at), Some(seat)) = (back, rec.seat_id.as_deref()) {
            if rejoin_failed(by, rec.exit_code, rec.signal.as_deref(), at.elapsed()) {
                tracing::warn!(session = %id, seat, exit_code = ?rec.exit_code, "rejoin failed");
                self.event(
                    &mut g,
                    id,
                    "seat.rejoin_failed",
                    serde_json::json!({"seat_id": seat, "session_id": id,
                        "exit_code": rec.exit_code, "signal": rec.signal}),
                );
            }
        }
        // S89-talk-1: a stopped seat's token stops working (unless another of its sessions
        // still runs: a token belongs to the seat, not to one run)
        if let Some(seat) = rec.seat_id.as_deref() {
            let still = g.live.iter().any(|(k, e)| {
                k.as_str() != id
                    && e.rec.state == "running"
                    && e.rec.seat_id.as_deref() == Some(seat)
            });
            if !still {
                crate::seat_token::tokens().revoke(seat);
                crate::seat_now::board().forget(seat);
            }
        }
        if let (Some(a), Some(seat)) = (self.alive.get(), rec.seat_id.as_deref()) {
            let others: Vec<String> = g
                .live
                .iter()
                .filter(|(k, e)| k.as_str() != id && e.rec.state == "running")
                .filter_map(|(_, e)| e.rec.seat_id.clone())
                .collect();
            a.on_end(seat, crate::alive::end_kind(by, rec.exit_code), others);
        }
        tracing::info!(session = %id, state = rec.state, ended_by = by,
            stop = stop.status.as_str(), survivors = stop.survivors.len(),
            candidates = stop.candidates.count, orphan_extra = stop.candidates.orphan_extra,
            unreadable = ?stop.candidates.unreadable,
            other_coalition = ?stop.candidates.other_coalition, "session stop");
        if stop.releases_slot() {
            let old = g.live.len();
            if let Some(e) = g.live.remove(id) {
                g.ended.push_front((e.order, e.rec));
                g.ended.truncate(KEEP_ENDED);
            }
            self.running_changed(old, &g);
            self.kick(&mut g);
        } else if worth_rescanning(&stop) && !g.shutting {
            // nothing known alive, but the stop could not look everywhere
            // (candidates_unreadable): re-check in the background
            self.start_rescan(&mut g, id);
        }
        self.cv.notify_all();
        Some(rec)
    }

    fn start_rescan(&self, g: &mut Inner, id: &str) {
        let Some(e) = g.live.get_mut(id) else {
            return;
        };
        if e.rescanning {
            return;
        }
        let Some(me) = self.me.upgrade() else {
            return;
        };
        e.rescanning = true;
        let tid = id.to_owned();
        let spawned = std::thread::Builder::new()
            .name("aibd-slot-rescan".into())
            .spawn(move || me.rescan(&tid));
        if spawned.is_err() {
            // no thread: the slot stays held (a DELETE still re-checks)
            if let Some(e) = g.live.get_mut(id) {
                e.rescanning = false;
            }
        }
    }

    /// Background re-check of a held slot (a63ab 11-09): every 2 s for at
    /// most the window, re-run the stop (it signals nothing when nothing is
    /// alive, and re-reads the candidates). `observed_clean` ⇒ slot back +
    /// `session.slot_released`; still not at the end ⇒ kept +
    /// `session.slot_held` with the last reason. Gives way to a DELETE or the
    /// shutdown. Bounded by one absolute end (bb7dc #546 7ebde8d1): each
    /// pass gets the grace that still fits and is waited for only until the
    /// end; a pass that has not answered by then ends the re-check with
    /// `slot_held {reason: rescan_pass_timeout}` (slot kept), and its late
    /// result is recorded by [`Self::late_pass`] without releasing the slot.
    fn rescan(&self, id: &str) {
        let (window, pass_min) = *self.rescan_window.lock().unwrap_or_else(|e| e.into_inner());
        let end = Instant::now() + window;
        let op = crate::timers::SESSION_SLOT_RESCAN.begin(Some(end));
        let mut tries = 0u32;
        let mut last: Option<String> = None;
        while op.tick() {
            if Instant::now() + pass_min > end {
                break; // not enough of the window left for a pass
            }
            let run = {
                let mut g = lock(&self.inner);
                if g.shutting {
                    return self.end_rescan(&mut g, id, None, tries, last);
                }
                let Some(e) = g.live.get_mut(id) else {
                    return; // released by a DELETE meanwhile
                };
                if !e.rec.holds_slot || !e.rec.stop.as_ref().is_some_and(|s| s.survivors.is_empty())
                {
                    e.rescanning = false;
                    return; // a DELETE changed it (survivors now: H2, DELETE again)
                }
                if e.stopping {
                    continue; // a DELETE is on it right now
                }
                e.stopping = true;
                e.run.clone()
            };
            tries += 1;
            let grace = end
                .saturating_duration_since(Instant::now())
                .saturating_sub(pass_min)
                .min(SHORT_GRACE);
            let (stop, mut g) = match self.pass_until(id, run, grace, end) {
                Ok(done) => done,
                Err(why) => {
                    // timeout: still `stopping`, the pass thread hands its
                    // late result to `late_pass`; nothing here touches `run`
                    let mut g = lock(&self.inner);
                    return self.end_rescan(&mut g, id, None, tries, Some(why.to_owned()));
                }
            };
            let shutting = g.shutting;
            let Some(e) = g.live.get_mut(id) else {
                return;
            };
            last = stop.status.reason().map(str::to_owned);
            if stop.releases_slot() {
                e.stopping = false;
                return self.end_rescan(&mut g, id, Some(stop), tries, last);
            }
            if shutting {
                // bb7dc #546 (1): the shutdown skipped this session because
                // this pass was stopping it. Hand the pass over as the
                // shutdown's stop (still `stopping`, so the shutdown waits
                // for this record), never a silent end.
                e.rescanning = false;
                drop(g);
                self.finish(id, stop, "shutdown");
                return;
            }
            e.stopping = false;
            e.rec.stop = Some(StopView::of(&stop));
            self.cv.notify_all();
            if !worth_rescanning(&stop) {
                // a survivor (H2: DELETE again), or a reason another pass
                // cannot change: no longer this re-check's case
                e.rec.holds_slot = true;
                return self.end_rescan(&mut g, id, None, tries, last);
            }
        }
        let mut g = lock(&self.inner);
        self.end_rescan(&mut g, id, None, tries, last);
    }

    /// One re-check pass on its own thread, waited for until `end`.
    /// `Ok((stop, guard))` = it answered in time (the guard is the inner
    /// lock, taken before the give-up flag could be set).
    /// `Err(RESCAN_PASS_TIMEOUT)` = it did not (it is still `stopping`).
    /// `Err("rescan_pass_not_started")` = no thread: nothing was stopped and
    /// the session is no longer `stopping`.
    fn pass_until(
        &self,
        id: &str,
        run: Arc<Mutex<Box<dyn Running>>>,
        grace: Duration,
        end: Instant,
    ) -> Result<(Stop, MutexGuard<'_, Inner>), &'static str> {
        let not_started = || {
            let mut g = lock(&self.inner);
            if let Some(e) = g.live.get_mut(id) {
                e.stopping = false;
            }
            self.cv.notify_all();
            Err("rescan_pass_not_started")
        };
        let Some(me) = self.me.upgrade() else {
            return not_started();
        };
        let gave_up = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let (tx, rx) = std::sync::mpsc::channel();
        let (flag, tid) = (gave_up.clone(), id.to_owned());
        let spawned = std::thread::Builder::new()
            .name("aibd-rescan-pass".into())
            .spawn(move || {
                let stop = run.lock().unwrap_or_else(|e| e.into_inner()).stop(grace);
                // under the inner lock: either the re-check still waits (send)
                // or it gave up (late result), never both, never neither
                let g = lock(&me.inner);
                if flag.load(std::sync::atomic::Ordering::SeqCst) {
                    me.late_pass(g, &tid, stop);
                } else {
                    let _ = tx.send(stop);
                }
            });
        if spawned.is_err() {
            return not_started();
        }
        // One wake-up for the answer, or at the end: not a periodic timer
        // (ADR 0005 named exception, as `sessions::wait_timeout`).
        #[allow(clippy::disallowed_methods)]
        let got = rx.recv_timeout(end.saturating_duration_since(Instant::now()));
        let g = lock(&self.inner);
        match got.ok().or_else(|| rx.try_recv().ok()) {
            Some(stop) => Ok((stop, g)),
            None => {
                gave_up.store(true, std::sync::atomic::Ordering::SeqCst);
                Err(RESCAN_PASS_TIMEOUT)
            }
        }
    }

    /// A re-check pass that answered after its window ended: record what it
    /// found, keep the slot (the re-check is over), clear `stopping`. If the
    /// shutdown began meanwhile it skipped this session, so the pass is
    /// handed over as the shutdown's stop.
    fn late_pass(&self, mut g: MutexGuard<'_, Inner>, id: &str, stop: Stop) {
        let shutting = g.shutting;
        let Some(e) = g.live.get_mut(id) else {
            return;
        };
        if shutting {
            drop(g);
            self.finish(id, stop, "shutdown");
            return;
        }
        e.stopping = false;
        e.rec.stop = Some(StopView::of(&stop));
        e.rec.holds_slot = true;
        tracing::warn!(session = %id, stop = stop.status.as_str(), "re-check pass answered after its window");
        self.cv.notify_all();
    }

    fn end_rescan(
        &self,
        g: &mut Inner,
        id: &str,
        released: Option<Stop>,
        tries: u32,
        last: Option<String>,
    ) {
        let Some(e) = g.live.get_mut(id) else {
            return;
        };
        e.rescanning = false;
        match released {
            Some(stop) => {
                e.rec.stop = Some(StopView::of(&stop));
                e.rec.holds_slot = false;
                self.event(
                    g,
                    id,
                    "session.slot_released",
                    serde_json::json!({"id": id, "reason": stop.status.reason(), "tries": tries}),
                );
                tracing::info!(session = %id, tries, "slot released after re-check");
                let old = g.live.len();
                if let Some(e) = g.live.remove(id) {
                    g.ended.push_front((e.order, e.rec));
                    g.ended.truncate(KEEP_ENDED);
                }
                self.running_changed(old, g);
                self.kick(g);
            }
            None => {
                // F: the last pass's candidate check (the record keeps it)
                let cand = g
                    .live
                    .get(id)
                    .and_then(|e| e.rec.stop.as_ref())
                    .map(|s| s.candidates.clone())
                    .unwrap_or_default();
                tracing::warn!(session = %id, tries, reason = ?last,
                    candidates = cand.count, orphan_extra = cand.orphan_extra,
                    unreadable = ?cand.unreadable,
                    other_coalition = ?cand.other_coalition, "slot still held after re-check");
                self.event(
                    g,
                    id,
                    "session.slot_held",
                    serde_json::json!({"id": id, "reason": last, "tries": tries,
                        "candidates": cand}),
                );
            }
        }
        self.cv.notify_all();
    }

    /// DELETE. Blocking: answers after the stop (≈ grace + 4 s at most).
    pub fn stop(&self, id: &str, grace: Duration) -> Result<Session, ApiError> {
        let not_found = || ApiError::new(ErrorCode::SessionNotFound, "no such session");
        let deadline = Instant::now() + BUSY_WAIT;
        let run = {
            let mut g = lock(&self.inner);
            loop {
                if let Some(i) = g.queue.iter().position(|q| q.rec.id == id) {
                    // waiting: out of the queue, never started (no tree to stop)
                    let Some(q) = g.queue.remove(i) else {
                        return Err(not_found());
                    };
                    let mut rec = q.rec;
                    rec.state = "cancelled";
                    rec.ended_at = Some(now_rfc3339());
                    rec.ended_by = Some("api");
                    self.write(
                        &rec,
                        q.seq,
                        "session.cancelled",
                        serde_json::json!({"id": id, "previous_state": "pending"}),
                    );
                    tracing::info!(session = %id, "queued session cancelled");
                    g.ended.push_front((q.order, rec.clone()));
                    g.ended.truncate(KEEP_ENDED);
                    return Ok(rec);
                }
                if let Some(e) = g.live.get_mut(id) {
                    if !e.stopping {
                        // running, or stop_unconfirmed with survivors: stop
                        // (again) — never "already ended" (H2)
                        e.stopping = true;
                        break e.run.clone();
                    }
                    let left = deadline.saturating_duration_since(Instant::now());
                    if left.is_zero() {
                        return Ok(e.rec.clone());
                    }
                    g = wait_timeout(&self.cv, g, left);
                    continue;
                }
                return g
                    .ended
                    .iter()
                    .find(|(_, s)| s.id == id)
                    .map(|(_, s)| s.clone())
                    .ok_or_else(not_found);
            }
        };
        let stop = run.lock().unwrap_or_else(|e| e.into_inner()).stop(grace);
        self.finish(id, stop, "api").ok_or_else(not_found)
    }

    /// aibd is ending (contract §5, D11): refuse new POSTs, wait for spawns in
    /// flight, stop every live session in parallel, all within
    /// [`SHUTDOWN_CAP`]. Returns how many were still stopping at the cap.
    pub fn shutdown_all(self: &Arc<Self>) -> usize {
        // folded rejection counts still in an open window are written now (1ced7 #593 M1)
        for (token_id, code, n, program) in self.rejections.drain() {
            self.write_rejection(code, &program, &token_id, n);
        }
        let deadline = Instant::now() + SHUTDOWN_CAP;
        let mut g = lock(&self.inner);
        g.shutting = true;
        // nothing waiting starts any more: each ends `interrupted` (its row too,
        // so the next start has nothing pending to mark)
        let waiting: Vec<Queued> = g.queue.drain(..).collect();
        for q in waiting {
            let mut rec = q.rec;
            rec.state = "interrupted";
            rec.ended_at = Some(now_rfc3339());
            rec.ended_by = Some("shutdown");
            self.write(
                &rec,
                q.seq,
                "session.interrupted",
                serde_json::json!({"id": rec.id, "reason": "shutdown",
                    "previous_state": "pending", "processes_may_remain": false}),
            );
            g.ended.push_front((q.order, rec));
        }
        g.ended.truncate(KEEP_ENDED);
        // A POST still spawning is not in `live` yet: it sees `shutting` when
        // its spawn returns and stops its own tree; the wait below counts its
        // reservation until that stop is recorded.
        let mut todo = Vec::new();
        for (id, e) in g.live.iter_mut() {
            if !e.stopping {
                e.stopping = true;
                todo.push((id.clone(), e.run.clone()));
            }
        }
        drop(g);
        for (id, run) in todo {
            let me = self.clone();
            let _ = std::thread::Builder::new()
                .name("aibd-session-stop".into())
                .spawn(move || {
                    let stop = run
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .stop(SHORT_GRACE);
                    me.finish(&id, stop, "shutdown");
                });
        }
        // Trees of starts that failed and were not confirmed gone (bb7dc
        // #544). A POST spawning now settles its failure (and adds its
        // leftover) before it gives its reservation back, so first wait for
        // the reservations (within the cap), then sweep.
        let mut g = lock(&self.inner);
        while g.reserved > 0 {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            g = wait_timeout(&self.cv, g, left);
        }
        let reserved = g.reserved;
        drop(g);
        // same deadline as everything else here (bb7dc #544 310cf95c D4)
        // `Swept` has no empty value for "could not look": Unsupported is
        // reported like an unconfirmed tree (platform parity, a9136 11-09)
        let swept = crate::platform::stop_leftovers(deadline);
        if swept.unconfirmed() > 0 || reserved > 0 {
            tracing::warn!(
                left_running = ?swept,
                unconfirmed = swept.unconfirmed(),
                reserved,
                "trees of failed starts not confirmed gone by the shutdown cap, or starts still in flight"
            );
        }
        let mut g = lock(&self.inner);
        loop {
            // reservations were waited for above (until the cap)
            let busy = g.live.values().filter(|e| e.stopping).count() + reserved;
            let left = deadline.saturating_duration_since(Instant::now());
            if busy == 0 || left.is_zero() {
                if busy > 0 {
                    tracing::warn!(busy, "shutdown cap reached with sessions still stopping");
                }
                return busy;
            }
            g = wait_timeout(&self.cv, g, left);
        }
    }

    /// A live session's event, written together with its current row (spec
    /// 4.3: state and outbox in one transaction), so the session is read
    /// back after a restart.
    /// N-revive L7: `id` was brought back by aibd at its start; if it ends by itself soon,
    /// unsuccessfully, `seat.rejoin_failed` is written so the page shows the seat must be
    /// opened again (0eed7: a seat whose conversation was elsewhere exited 1 in 0.1 s, quietly).
    ///
    /// Returns `false` when it already ended by itself before this was called (a 0.1 s
    /// exit can beat it): the caller writes the failure then.
    pub fn mark_rejoined(&self, id: &str) -> bool {
        // the session lock first, as the end path takes it before `rejoined`
        let g = lock(&self.inner);
        let running = g.live.get(id).is_some_and(|e| e.rec.ended_by.is_none());
        if running {
            self.rejoined
                .lock()
                .unwrap_or_else(|x| x.into_inner())
                .insert(id.to_owned(), std::time::Instant::now());
        }
        running
    }

    fn event(&self, g: &mut Inner, id: &str, kind: &str, payload: serde_json::Value) {
        let Some(e) = g.live.get_mut(id) else {
            return;
        };
        let seq = e.seq;
        e.seq += 1;
        self.write(&e.rec, seq, kind, payload);
    }

    /// One event of a session with its current row, in one transaction (a live
    /// session through [`Self::event`], a queued or ended one directly).
    fn write(&self, rec: &Session, seq: i64, kind: &str, payload: serde_json::Value) {
        let id = rec.id.as_str();
        let row = row_of(rec);
        let ev = NewEvent {
            event_id: new_id("ev_").unwrap_or_else(|_| format!("ev_{id}_{seq}")),
            aggregate_type: "session".into(),
            aggregate_id: id.to_owned(),
            sequence: seq,
            event_type: kind.to_owned(),
            correlation_id: None,
            occurred_at: now_rfc3339(),
            payload,
        };
        if let Err(err) = self.store.session_event(row, ev) {
            tracing::error!(session = %id, event = kind, error = %err, "session event write failed");
        }
    }

    /// Audit a refusal (a63ab 19:23): event `session.spawn_rejected` in aggregate type
    /// `security` (each record its own aggregate, sequence 1) plus one `warn` line, at most
    /// one per token + code per minute (`now` is injected); the count of the folded ones
    /// is written when the window closes (see [`allowlist::RejectLog`]). The payload is
    /// exactly `code`, `program` (a file name, cut to 64 characters, control characters
    /// escaped), `token_id`, `at`, `folded_before`: never env values, the full argv or the
    /// cwd.
    pub fn record_rejection(&self, now: Instant, token_id: &str, code: ErrorCode, program: &str) {
        let code = code.as_str();
        let program = allowlist::audit_name(program);
        match self.rejections.note(now, token_id, code, &program) {
            allowlist::Note::Write { folded_before } => {
                self.write_rejection(code, &program, token_id, folded_before)
            }
            allowlist::Note::Folded { arm: Some(start) } => {
                self.arm_reject_flush(token_id.to_owned(), code, start)
            }
            allowlist::Note::Folded { arm: None } => {}
        }
    }

    /// One flush per window, at its end (`session.reject_flush`, an Operation timer that
    /// lives at most one window). The thread holds only a `Weak` while it waits (1ced7
    /// #593 L2): after a shutdown the store is not kept open for up to a minute; if the
    /// sessions are gone when it wakes, it ends (the shutdown drain wrote the count).
    fn arm_reject_flush(&self, token_id: String, code: &'static str, start: Instant) {
        let me = self.me.clone();
        let spawned = std::thread::Builder::new()
            .name("aibd-reject-flush".into())
            .spawn(move || {
                let op = crate::timers::SESSION_REJECT_FLUSH
                    .begin(Some(start + allowlist::REJECT_WINDOW));
                while op.tick() {}
                drop(op);
                let Some(me) = me.upgrade() else {
                    return;
                };
                if let Some((n, program)) = me.rejections.close(&token_id, code, start) {
                    me.write_rejection(code, &program, &token_id, n);
                }
            });
        if spawned.is_err() {
            tracing::error!(code, "session.reject_flush could not start; the count is written by the next rejection or at shutdown");
        }
    }

    fn write_rejection(&self, code: &str, program: &str, token_id: &str, folded_before: u64) {
        let at = now_rfc3339();
        tracing::warn!(
            code,
            program,
            token_id,
            folded_before,
            "session spawn rejected"
        );
        let agg = new_id("rj_").unwrap_or_else(|_| format!("rj_{}", at.replace([':', '.'], "")));
        let ev = NewEvent {
            event_id: new_id("ev_").unwrap_or_else(|_| format!("ev_{agg}_1")),
            aggregate_type: "security".into(),
            aggregate_id: agg,
            sequence: 1,
            event_type: "session.spawn_rejected".into(),
            correlation_id: None,
            occurred_at: at.clone(),
            payload: serde_json::json!({
                "code": code,
                "program": program,
                "token_id": token_id,
                "at": at,
                "folded_before": folded_before,
            }),
        };
        if self.store.append(vec![ev]).is_err() {
            tracing::error!(code, "session.spawn_rejected write failed");
        }
    }

    fn append(&self, id: &str, seq: i64, kind: &str, payload: serde_json::Value) {
        let ev = NewEvent {
            event_id: new_id("ev_").unwrap_or_else(|_| format!("ev_{id}_{seq}")),
            aggregate_type: "session".into(),
            aggregate_id: id.to_owned(),
            sequence: seq,
            event_type: kind.to_owned(),
            correlation_id: None,
            occurred_at: now_rfc3339(),
            payload,
        };
        if self.store.append(vec![ev]).is_err() {
            tracing::error!(session = %id, event = kind, "session event write failed");
        }
    }
}

// One wake-up on a state change (or at the deadline), not a periodic timer
// (ADR 0005 named exception, as in tree.rs `RootExit::wait`).
#[allow(clippy::disallowed_methods)]
fn wait_timeout<'a>(cv: &Condvar, g: MutexGuard<'a, Inner>, d: Duration) -> MutexGuard<'a, Inner> {
    cv.wait_timeout(g, d)
        .map(|(g, _)| g)
        .unwrap_or_else(|e| e.into_inner().0)
}

pub(crate) fn new_id(prefix: &str) -> Result<String, getrandom::Error> {
    let mut b = [0u8; 8];
    getrandom::fill(&mut b)?;
    Ok(format!(
        "{prefix}{}",
        b.iter().map(|x| format!("{x:02x}")).collect::<String>()
    ))
}

/// UTC, millisecond RFC 3339 (`2026-11-09T04:31:02.123Z`).
pub fn now_rfc3339() -> String {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    rfc3339(d.as_secs() as i64, d.subsec_millis())
}

/// UTC RFC 3339 of whole Unix seconds (A11 alive events).
pub(crate) fn rfc3339_of_unix(secs: i64) -> String {
    rfc3339(secs, 0)
}

fn rfc3339(secs: i64, ms: u32) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // civil_from_days (H. Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.{ms:03}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

// ---- HTTP handlers ----

pub(crate) fn json_content_type(h: &HeaderMap) -> bool {
    h.get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.split(';').next())
        .is_some_and(|v| v.trim().eq_ignore_ascii_case("application/json"))
}

/// Content type, size and [`validate`], shared by both POST routes. The first layer's
/// refusals that matter are audited (1ced7 #593 M2: deny-listed env names, a relative
/// `argv[0]`); plain shape errors (bad JSON, too long) are not.
async fn read_valid(st: &AppState, headers: &HeaderMap, body: Body) -> Result<Request, ApiError> {
    if !json_content_type(headers) {
        return Err(bad("Content-Type must be application/json"));
    }
    let bytes = match axum::body::to_bytes(body, MAX_BODY).await {
        Ok(b) => b,
        Err(_) => {
            return Err(ApiError::new(
                ErrorCode::PayloadTooLarge,
                "request body over 64 KiB",
            ))
        }
    };
    match validate_audit(&bytes) {
        Ok(r) => Ok(r),
        Err(Invalid { err: e, audit }) => {
            if audit {
                let program = lenient_argv0_name(&bytes);
                let audit = st.sessions.clone();
                let code = e.0;
                let _ = tokio::task::spawn_blocking(move || {
                    audit.record_rejection(Instant::now(), TOKEN_ID_STARTUP, code, &program)
                })
                .await;
            }
            Err(e)
        }
    }
}

/// The M1 gate ([`admit`]) on a blocking thread, shared by both POST routes; a refusal
/// is audited.
pub(crate) async fn gate(
    st: &AppState,
    req: Request,
) -> Result<(Request, Option<String>), ApiError> {
    let policy = st.session_policy.clone();
    let audit = st.sessions.clone();
    let asked = req.argv.first().cloned();
    let run = move || {
        admit(req, &policy).map(|r| (r, asked)).map_err(|r| {
            audit.record_rejection(Instant::now(), TOKEN_ID_STARTUP, r.error.0, &r.program);
            r.error
        })
    };
    match tokio::task::spawn_blocking(run).await {
        Ok(Ok(r)) => Ok(r),
        Ok(Err(e)) => Err(e),
        Err(_) => Err(ApiError::new(ErrorCode::Internal, "internal error")),
    }
}

/// What an `Idempotency-Key` of a POST is bound to (hashed, never stored as is: argv
/// and env stay out of the store, spec 9.4). Env pairs are sorted.
fn post_canonical(req: &Request) -> serde_json::Value {
    let mut env = req.env.clone();
    env.sort();
    serde_json::json!({
        "argv": req.argv,
        "cwd": req.cwd.as_ref().map(|p| p.to_string_lossy().into_owned()),
        "env": env,
    })
}

/// Start `req` (already read and valid) under an optional key: claim, gate, start,
/// then keep or give back the key. Run through [`crate::idem::detached`], so a
/// client that hangs up never leaves the key claimed (043dd T81 M1).
async fn start_keyed(
    st: AppState,
    headers: HeaderMap,
    scope: String,
    seat: Option<String>,
    req: Request,
) -> Response {
    let st = &st;
    let key = match crate::idem::key_of(&headers, scope, &post_canonical(&req)) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    if let Some(answer) = crate::idem::begin(st, &key).await {
        return answer;
    }
    let (req, asked) = match gate(st, req).await {
        Ok(r) => r,
        Err(e) => {
            crate::idem::end(st, key, StatusCode::FORBIDDEN, None).await;
            return e.into_response();
        }
    };
    let s = st.sessions.clone();
    match tokio::task::spawn_blocking(move || s.start_gated(seat, req, asked)).await {
        Ok(Ok(rec)) => {
            let code = created_or_queued(&rec);
            crate::idem::end(st, key, code, Some(rec.id.as_str())).await;
            (code, Json(rec)).into_response()
        }
        Ok(Err(e)) => {
            crate::idem::end(st, key, StatusCode::BAD_REQUEST, None).await;
            e.into_response()
        }
        Err(_) => {
            crate::idem::end(st, key, StatusCode::INTERNAL_SERVER_ERROR, None).await;
            ApiError::new(ErrorCode::Internal, "internal error").into_response()
        }
    }
}

/// 201 for a started session, 202 for one waiting in the queue (ADR 0005 §5).
fn created_or_queued(rec: &Session) -> StatusCode {
    if rec.state == "pending" {
        StatusCode::ACCEPTED
    } else {
        StatusCode::CREATED
    }
}

pub async fn create(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    let req = match read_valid(&st, &headers, body).await {
        Ok(r) => r,
        Err(e) => return e.into_response(),
    };
    let scope = "POST /api/v1/sessions".to_string();
    crate::idem::detached(start_keyed(st, headers, scope, None, req)).await
}

/// `POST /api/v1/seats/{id}/sessions`: the same body and answers as
/// `POST /api/v1/sessions`, for an existing seat (`404 seat_not_found`
/// otherwise; spec 4.2: a session belongs to a seat).
pub async fn create_for_seat(
    State(st): State<AppState>,
    UrlPath(seat): UrlPath<String>,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let req = match read_valid(&st, &headers, body).await {
        Ok(r) => r,
        Err(e) => return e.into_response(),
    };
    let store = st.store.clone();
    let sid = seat.clone();
    match tokio::task::spawn_blocking(move || store.seat(&sid)).await {
        Ok(Ok(Some(_))) => {}
        Ok(Ok(None)) => {
            return ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response()
        }
        _ => return ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
    // Same M1 gate and Idempotency-Key as `POST /api/v1/sessions` (integ preview.3: the
    // seat route came from #592 and the gate from #593; f1fae joined them, 043dd made
    // them one helper; T81 the key).
    let scope = format!("POST /api/v1/seats/{seat}/sessions");
    crate::idem::detached(start_keyed(st, headers, scope, Some(seat), req)).await
}

/// `GET /api/v1/seats/{id}/sessions`: the seat's sessions as stored (also
/// after a restart), oldest first. The stored form has no argv beyond
/// `argv0` and `argc`, and never env.
pub async fn list_for_seat(State(st): State<AppState>, UrlPath(seat): UrlPath<String>) -> Response {
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || {
        let s = store.seat(&seat)?;
        Ok::<_, aib_store::StoreError>(match s {
            Some(_) => Some(store.sessions_of_seat(&seat)?),
            None => None,
        })
    })
    .await;
    match res {
        Ok(Ok(Some(rows))) => {
            let v: Vec<_> = rows.iter().map(stored_view).collect();
            Json(serde_json::json!({"sessions": v})).into_response()
        }
        Ok(Ok(None)) => ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response(),
        _ => ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
}

fn stored_view(r: &SessionRow) -> serde_json::Value {
    serde_json::json!({
        "id": r.session_id,
        "seat_id": r.seat_id,
        "pid": r.pid,
        "argv0": r.argv0,
        "argc": r.argc,
        "state": r.state,
        "holds_slot": r.holds_slot,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "ended_by": r.ended_by,
        "exit_code": r.exit_code,
        "signal": r.signal,
        "stop_status": r.stop_status,
        "stop_reason": r.stop_reason,
    })
}

pub async fn list(State(st): State<AppState>) -> Response {
    Json(serde_json::json!({"sessions": st.sessions.list()})).into_response()
}

pub async fn get_one(State(st): State<AppState>, UrlPath(id): UrlPath<String>) -> Response {
    match st.sessions.get(&id) {
        Some(s) => Json(s).into_response(),
        None => ApiError::new(ErrorCode::SessionNotFound, "no such session").into_response(),
    }
}

/// Query of `GET /api/v1/sessions/{id}/output`.
#[derive(Debug, serde::Deserialize)]
pub struct OutputQuery {
    #[serde(default)]
    after: i64,
    limit: Option<u32>,
}

/// `GET /api/v1/sessions/{id}/output?after=&limit=`: the session's output
/// events (`session.output`, `session.output_truncated`,
/// `session.output_closed`) with `sequence > after`, from the store, so a
/// session of an earlier aibd run reads back the same. `next` is the last
/// sequence returned (pass it as `after`).
pub async fn output(
    State(st): State<AppState>,
    UrlPath(id): UrlPath<String>,
    Query(q): Query<OutputQuery>,
) -> Response {
    let limit = q.limit.unwrap_or(500).clamp(1, 1000);
    let after = q.after.max(0);
    let known = st.sessions.get(&id).is_some();
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || {
        if !known && store.session_row(&id)?.is_none() {
            return Ok(None);
        }
        store
            .events_of(&crate::session_output::aggregate_id(&id), after, limit)
            .map(Some)
    })
    .await;
    match res {
        Ok(Ok(Some(evs))) => {
            let next = evs.last().map(|e| e.sequence).unwrap_or(after);
            let items: Vec<_> = evs
                .into_iter()
                .map(|e| {
                    serde_json::json!({
                        "sequence": e.sequence,
                        "global_seq": e.global_seq,
                        "type": e.event_type,
                        "occurred_at": e.occurred_at,
                        "payload": e.payload,
                    })
                })
                .collect();
            Json(serde_json::json!({"output": items, "next": next})).into_response()
        }
        Ok(Ok(None)) => {
            ApiError::new(ErrorCode::SessionNotFound, "no such session").into_response()
        }
        _ => ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
}

pub async fn delete_one(
    State(st): State<AppState>,
    UrlPath(id): UrlPath<String>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let grace = match q.get("grace_ms") {
        None => DEFAULT_GRACE,
        Some(v) => match v.parse::<u64>() {
            Ok(ms) if ms <= MAX_GRACE_MS => Duration::from_millis(ms),
            _ => return bad("grace_ms must be an integer 0-30000").into_response(),
        },
    };
    crate::idem::detached(stop_keyed(st, id, grace, headers)).await
}

/// Stop `id` under an optional key (run detached like [`start_keyed`]).
async fn stop_keyed(st: AppState, id: String, grace: Duration, headers: HeaderMap) -> Response {
    let grace_ms = u64::try_from(grace.as_millis()).unwrap_or(u64::MAX);
    let canonical = serde_json::json!({ "grace_ms": grace_ms });
    let key = match crate::idem::key_of(
        &headers,
        format!("DELETE /api/v1/sessions/{id}"),
        &canonical,
    ) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    if let Some(answer) = crate::idem::begin(&st, &key).await {
        return answer;
    }
    let s = st.sessions.clone();
    let sid = id.clone();
    match tokio::task::spawn_blocking(move || s.stop(&sid, grace)).await {
        Ok(Ok(rec)) => {
            crate::idem::end(&st, key, StatusCode::OK, Some(id.as_str())).await;
            Json(rec).into_response()
        }
        Ok(Err(e)) => {
            crate::idem::end(&st, key, StatusCode::BAD_REQUEST, None).await;
            e.into_response()
        }
        Err(_) => {
            crate::idem::end(&st, key, StatusCode::INTERNAL_SERVER_ERROR, None).await;
            ApiError::new(ErrorCode::Internal, "internal error").into_response()
        }
    }
}

pub async fn method_not_allowed() -> Response {
    ApiError::new(ErrorCode::MethodNotAllowed, "method not allowed").into_response()
}

/// How soon a brought-back seat must end for its rejoin to count as failed.
pub const REJOIN_FAIL_WINDOW: std::time::Duration = std::time::Duration::from_secs(10);

/// N-revive L7: a brought-back session that ended by itself (`root_exit`) within
/// [`REJOIN_FAIL_WINDOW`], not with 0, did not come back.
pub fn rejoin_failed(
    by: &str,
    exit_code: Option<i32>,
    signal: Option<&str>,
    after: std::time::Duration,
) -> bool {
    by == "root_exit" && after < REJOIN_FAIL_WINDOW && (exit_code != Some(0) || signal.is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// S89-state (5d33a 853ee100 L2): an unreadable seat list is not an empty one.
    #[test]
    fn archived_seats_unread_at_start_close_every_seat() {
        let seat = |id: &str, state: &str| aib_store::Seat {
            seat_id: id.into(),
            name: id.into(),
            state: state.into(),
            revision: 1,
            created_at: String::new(),
            updated_at: String::new(),
        };
        let (set, unknown) = archived_from(Ok(vec![
            seat("a", aib_store::ARCHIVED),
            seat("b", "CREATED"),
        ]));
        assert_eq!(set.into_iter().collect::<Vec<_>>(), vec!["a".to_string()]);
        assert!(!unknown);
        let (set, unknown) = archived_from(Err(aib_store::StoreError::WriterGone));
        assert!(set.is_empty());
        assert!(unknown);
        // and then a seat's session is refused; one without a seat still may start
        let g = Inner {
            archived: set,
            archived_unknown: unknown,
            ..Inner::default()
        };
        let sid = "b".to_string();
        assert_eq!(g.seat_refused(Some(&sid)).unwrap().0, ErrorCode::Internal);
        assert!(g.seat_refused(None).is_none());
        let g = Inner {
            archived: ["a".to_string()].into_iter().collect(),
            ..Inner::default()
        };
        let a = "a".to_string();
        assert_eq!(g.seat_refused(Some(&a)).unwrap().0, ErrorCode::SeatArchived);
        assert!(g.seat_refused(Some(&sid)).is_none());
    }

    /// 043dd f6500f85 L1: a dequeue thread that panics clears `dequeuing`
    /// (the next kick starts a new one); a normal drop leaves it to the
    /// loop, which clears it under its own lock.
    /// S79-p11b: `timeout_s` is whole seconds 1..=86400; absent = no limit.
    #[test]
    fn timeout_s_is_one_second_to_one_day() {
        // an absolute argv[0] of this platform (Windows: `C:\...`, JSON-escaped): validate refuses
        // a relative one before it reads timeout_s (519d0 11-17: #828 Windows red)
        let argv0 = serde_json::to_string(TRUE_ABS).unwrap();
        let t = |extra: &str| validate(format!(r#"{{"argv":[{argv0}]{extra}}}"#).as_bytes());
        assert_eq!(t("").unwrap().timeout, None);
        assert_eq!(
            t(r#","timeout_s":1"#).unwrap().timeout,
            Some(Duration::from_secs(1))
        );
        assert_eq!(
            t(r#","timeout_s":86400"#).unwrap().timeout,
            Some(Duration::from_secs(MAX_TIMEOUT_S))
        );
        for bad in [
            r#","timeout_s":0"#,
            r#","timeout_s":86401"#,
            r#","timeout_s":-1"#,
            r#","timeout_s":1.5"#,
            r#","timeout_s":"5""#,
        ] {
            let e = t(bad).unwrap_err();
            assert_eq!(e.0, ErrorCode::InvalidRequest, "{bad}");
        }
    }

    #[test]
    fn a_dequeue_thread_that_panics_does_not_stop_the_queue() {
        let m = Arc::new(Mutex::new(Inner {
            dequeuing: true,
            ..Inner::default()
        }));
        let m2 = m.clone();
        let r = std::thread::spawn(move || {
            let _g = DequeueGuard(&m2);
            panic!("dequeue thread panics");
        })
        .join();
        assert!(r.is_err());
        assert!(!lock(&m).dequeuing, "a panic left the queue stopped");
        lock(&m).dequeuing = true;
        drop(DequeueGuard(&m));
        assert!(lock(&m).dequeuing, "a normal end is the loop's to clear");
    }

    // absolute on the platform the test runs on (validation only, nothing
    // is started): "/bin/true" is not absolute on Windows
    const TRUE_ABS: &str = if cfg!(windows) {
        r"C:\Windows\System32\whoami.exe"
    } else {
        "/usr/bin/true" // macOS has no /bin/true (Linux has both)
    };
    const ECHO_ABS: &str = if cfg!(windows) {
        r"C:\Windows\System32\cmd.exe"
    } else {
        "/bin/echo"
    };

    fn policy() -> SessionPolicy {
        SessionPolicy {
            programs: vec![allowlist::Program {
                name: "fx".into(),
                path: PathBuf::from(TRUE_ABS),
                signer: None,
            }],
            env: vec!["MY_VAR".into()],
            interpreters_ok: false,
        }
    }

    fn req(argv0: &str, env: &[(&str, &str)]) -> Request {
        Request {
            argv: vec![argv0.into(), "--secret-arg".into()],
            cwd: None,
            env: env
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            timeout: None,
            withhold: Vec::new(),
            input: false,
        }
    }

    #[test]
    fn m1_gate_a_listed_program_runs_by_its_real_path() {
        let r = admit(req(TRUE_ABS, &[("MY_VAR", "1")]), &policy()).unwrap();
        let real = std::fs::canonicalize(TRUE_ABS).unwrap();
        assert_eq!(Path::new(&r.argv[0]), allowlist_exec(&real));
        assert_eq!(r.argv[1], "--secret-arg");
        assert!(admit(req(TRUE_ABS, &[]), &policy()).is_ok());
    }

    /// What admit runs for a real path (Windows drops the `\\?\` prefix).
    fn allowlist_exec(real: &Path) -> PathBuf {
        let s = real.to_str().unwrap();
        PathBuf::from(s.strip_prefix(r"\\?\").unwrap_or(s))
    }

    #[test]
    fn m1_gate_other_program_is_program_not_allowed_and_not_echoed() {
        let r = admit(req(ECHO_ABS, &[]), &policy()).unwrap_err();
        let want = std::fs::canonicalize(ECHO_ABS).unwrap();
        assert_eq!(r.program, want.file_name().unwrap().to_str().unwrap());
        let e = r.error;
        assert_eq!(e.0, ErrorCode::ProgramNotAllowed);
        assert_eq!(e.0.http_status(), 403);
        assert!(!e.1.contains(ECHO_ABS), "{}", e.1);
        assert!(!e.1.contains("secret"), "{}", e.1);
    }

    #[test]
    fn m1_gate_unlisted_env_name_is_env_not_allowed() {
        let e = admit(req(TRUE_ABS, &[("OTHER", "v-SECRET")]), &policy())
            .unwrap_err()
            .error;
        assert_eq!(e.0, ErrorCode::EnvNotAllowed);
        assert!(!e.1.contains("OTHER") && !e.1.contains("SECRET"), "{}", e.1);
    }

    /// 043dd #593 N1: which first-layer refusals are audited is decided where the
    /// refusal is made (`Invalid::audit`), not from the message text. Exactly two are:
    /// a denied env key and a relative argv[0].
    #[test]
    fn first_layer_audit_is_flagged_at_the_refusal_not_by_its_message() {
        let audit = |j: serde_json::Value| match validate_audit(j.to_string().as_bytes()) {
            Ok(_) => panic!("accepted: {j}"),
            Err(i) => (i.err.0, i.audit),
        };
        let yes = [
            serde_json::json!({"argv": [TRUE_ABS], "env": {"DYLD_INSERT_LIBRARIES": "x"}}),
            serde_json::json!({"argv": [TRUE_ABS], "env": {"PATH": "/x"}}),
            serde_json::json!({"argv": ["true"]}),
        ];
        for j in yes {
            assert!(audit(j.clone()).1, "not audited: {j}");
        }
        assert_eq!(
            audit(serde_json::json!({"argv": [TRUE_ABS], "env": {"AIBD_X": "1"}})).0,
            ErrorCode::EnvNotAllowed
        );
        let no = [
            serde_json::json!({"argv": []}),
            serde_json::json!({"argv": [""]}),
            serde_json::json!({"argv": [TRUE_ABS, "a\u{0}b"]}),
            serde_json::json!({"argv": [TRUE_ABS], "cwd": "relative"}),
            serde_json::json!({"argv": [TRUE_ABS], "env": {"1BAD": "x"}}),
            serde_json::json!({"argv": [TRUE_ABS], "env": {"A": "x", "a": "y"}}),
            serde_json::json!({"argv": [TRUE_ABS], "extra": 1}),
        ];
        for j in no {
            let (code, a) = audit(j.clone());
            assert!(!a, "audited: {j}");
            assert_eq!(code, ErrorCode::InvalidRequest, "{j}");
        }
        // `validate` keeps its old shape: the same error, without the flag
        let e = validate(br#"{"argv": ["true"]}"#).unwrap_err();
        assert_eq!(e.1, ARGV0_RELATIVE);
    }

    #[test]
    fn m1_gate_empty_policy_allows_nothing() {
        let none = SessionPolicy::default();
        assert_eq!(
            admit(req(TRUE_ABS, &[]), &none).unwrap_err().error.0,
            ErrorCode::ProgramNotAllowed
        );
    }

    fn v(j: serde_json::Value) -> Result<Request, ApiError> {
        validate(j.to_string().as_bytes())
    }

    #[test]
    fn rfc3339_known_points() {
        assert_eq!(rfc3339(0, 0), "1970-01-01T00:00:00.000Z");
        assert_eq!(rfc3339(1_794_169_836, 7), "2026-11-08T20:30:36.007Z");
        assert_eq!(rfc3339(951_782_400, 999), "2000-02-29T00:00:00.999Z");
    }

    #[test]
    fn argv_rules() {
        assert!(v(serde_json::json!({"argv": [TRUE_ABS]})).is_ok());
        for bad_argv in [
            serde_json::json!([]),
            serde_json::json!(["true"]),
            serde_json::json!(["./x"]),
            serde_json::json!([""]),
            serde_json::json!(["", "x"]),
            serde_json::json!([ECHO_ABS, "a\u{0}b"]),
            serde_json::json!(vec![ECHO_ABS; 65]),
            serde_json::json!([ECHO_ABS, "x".repeat(4097)]),
        ] {
            let e = v(serde_json::json!({ "argv": bad_argv })).unwrap_err();
            assert_eq!(e.0, ErrorCode::InvalidRequest, "{bad_argv}");
        }
        // a63ab 20:32: empty items after argv[0] are kept as they are, in place
        let r =
            v(serde_json::json!({"argv": [ECHO_ABS, "--setting-sources", "", "x", ""]})).unwrap();
        assert_eq!(r.argv, [ECHO_ABS, "--setting-sources", "", "x", ""]);
        let many: Vec<String> = std::iter::once(ECHO_ABS.to_owned())
            .chain(std::iter::repeat_n("y".repeat(4000), 9))
            .collect();
        assert_eq!(
            v(serde_json::json!({ "argv": many })).unwrap_err().0,
            ErrorCode::InvalidRequest
        );
    }

    #[test]
    fn env_rules_and_messages_never_echo_values() {
        let secret = "sk-SECRET-VALUE-123";
        for k in [
            "AIBD_X",
            "aibd_token",
            "DYLD_INSERT_LIBRARIES",
            "LD_PRELOAD",
            "PATH",
            "path",
        ] {
            let e = v(serde_json::json!({"argv": [TRUE_ABS], "env": {k: secret}})).unwrap_err();
            assert_eq!(e.0, ErrorCode::EnvNotAllowed, "{k}");
            assert!(!e.1.contains(secret));
        }
        for k in ["1A", "A-B", "", &"K".repeat(65)] {
            let e = v(serde_json::json!({"argv": [TRUE_ABS], "env": {k: "v"}})).unwrap_err();
            assert_eq!(e.0, ErrorCode::InvalidRequest, "{k}");
        }
        let e =
            v(serde_json::json!({"argv": [TRUE_ABS], "env": {"A": "x".repeat(4097)}})).unwrap_err();
        assert_eq!(e.0, ErrorCode::InvalidRequest);
        let mut m = serde_json::Map::new();
        for i in 0..33 {
            m.insert(format!("K{i}"), "v".into());
        }
        assert_eq!(
            v(serde_json::json!({"argv": [TRUE_ABS], "env": m}))
                .unwrap_err()
                .0,
            ErrorCode::InvalidRequest
        );
        // a type error quoting the value is not echoed either
        let e = validate(
            format!(
                r#"{{"argv": [{abs}], "env": {{"A": {{"x": "{secret}"}}}}}}"#,
                abs = serde_json::to_string(TRUE_ABS).unwrap()
            )
            .as_bytes(),
        )
        .unwrap_err();
        assert!(!e.1.contains(secret), "{}", e.1);
        assert!(
            v(serde_json::json!({"argv": [TRUE_ABS], "env": {"LANG": "C", "_X1": ""}})).is_ok()
        );
        assert_eq!(
            v(serde_json::json!({"argv": [TRUE_ABS], "env": {"TEMP": "a", "Temp": "b"}}))
                .unwrap_err()
                .0,
            ErrorCode::InvalidRequest
        );
    }

    #[test]
    fn cwd_and_shape_rules() {
        assert_eq!(
            v(serde_json::json!({"argv": [TRUE_ABS], "cwd": "rel"}))
                .unwrap_err()
                .0,
            ErrorCode::InvalidRequest
        );
        assert_eq!(
            v(serde_json::json!({"argv": [TRUE_ABS], "cwd": std::env::temp_dir().join("no-such-dir-a9136").join("x")}))
                .unwrap_err()
                .0,
            ErrorCode::InvalidRequest
        );
        let d = std::env::temp_dir();
        assert!(v(serde_json::json!({"argv": [TRUE_ABS], "cwd": d})).is_ok());
        assert_eq!(
            v(serde_json::json!({"argv": [TRUE_ABS], "extra": 1}))
                .unwrap_err()
                .0,
            ErrorCode::InvalidRequest
        );
        assert_eq!(
            validate(b"not json").unwrap_err().0,
            ErrorCode::InvalidRequest
        );
        assert_eq!(
            validate(&vec![b' '; MAX_BODY + 1]).unwrap_err().0,
            ErrorCode::PayloadTooLarge
        );
    }

    /// R1 PR 3a: a NOGO answer tells the client when to try again.
    #[test]
    fn a_thermal_nogo_answers_503_with_retry_after() {
        let r = ApiError::new(ErrorCode::ThermalNogo, "hot").into_response();
        assert_eq!(r.status().as_u16(), 503);
        assert_eq!(
            r.headers()
                .get(axum::http::header::RETRY_AFTER)
                .and_then(|v| v.to_str().ok()),
            Some(THERMAL_RETRY_AFTER_SECS)
        );
        let other = ApiError::new(ErrorCode::ShuttingDown, "x").into_response();
        assert!(other
            .headers()
            .get(axum::http::header::RETRY_AFTER)
            .is_none());
    }
}
