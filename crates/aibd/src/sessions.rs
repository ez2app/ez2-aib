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
//! Blocking calls (spawn, stop, the event store) run on blocking threads.

use crate::allowlist::{self, SessionPolicy};
use crate::api::AppState;
use crate::guard::{self, error_response};
use crate::lineage::Lineage;
use crate::platform::session::{Running, SpawnFn, Spec, Stop};
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
/// Ended sessions kept in memory for GET (not persisted).
pub const KEEP_ENDED: usize = 100;
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
pub const RESCAN_REASONS: &[&str] = &["candidates_unreadable"];

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
    pub pid: u32,
    pub started_at: String,
    /// `running` | `exited` | `killed` | `stop_unconfirmed` (H2).
    pub state: &'static str,
    pub exit_code: Option<i32>,
    pub signal: Option<String>,
    pub ended_at: Option<String>,
    /// `root_exit` | `api` | `shutdown`.
    pub ended_by: Option<&'static str>,
    /// Still counts against the running limit: running, or a stop that could
    /// not show the tree is gone (H2 whitelist). DELETE again to re-check.
    pub holds_slot: bool,
    pub stop: Option<StopView>,
}

/// A validated POST body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub argv: Vec<String>,
    pub cwd: Option<PathBuf>,
    pub env: Vec<(String, String)>,
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
        error_response(self.0, &self.1, &guard::request_id())
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
    if body.len() > MAX_BODY {
        return Err(ApiError::new(
            ErrorCode::PayloadTooLarge,
            "request body over 64 KiB",
        ));
    }
    let raw: RawRequest = serde_json::from_slice(body).map_err(|_| {
        bad("body must be a JSON object {argv: [string], cwd?: string, env?: {string: string}}")
    })?;
    let argv = raw.argv;
    if argv.is_empty() || argv.len() > MAX_ARGS {
        return Err(bad("argv must have 1-64 items"));
    }
    let mut total = 0usize;
    for a in &argv {
        if a.is_empty() || a.len() > MAX_ARG_BYTES || a.contains('\0') {
            return Err(bad("each argv item must be 1-4096 bytes without NUL"));
        }
        total += a.len();
    }
    if total > MAX_ARGV_TOTAL {
        return Err(bad("argv over 32 KiB in total"));
    }
    if !Path::new(&argv[0]).is_absolute() {
        return Err(bad(ARGV0_RELATIVE));
    }
    let cwd = match raw.cwd {
        None => None,
        Some(c) => {
            let p = PathBuf::from(&c);
            if c.contains('\0') || !p.is_absolute() || !p.is_dir() {
                return Err(bad("cwd must be an existing absolute directory"));
            }
            Some(p)
        }
    };
    let env_in = raw.env.unwrap_or_default();
    if env_in.len() > MAX_ENV {
        return Err(bad("env has more than 32 keys"));
    }
    let mut env = Vec::with_capacity(env_in.len());
    for (k, v) in env_in {
        // Windows names have no case: `TEMP` + `Temp` would both reach the
        // child; refused on every platform (043dd #539 Low)
        if env
            .iter()
            .any(|(e, _): &(String, String)| e.eq_ignore_ascii_case(&k))
        {
            return Err(bad(
                "env has the same key twice (keys are compared without case)",
            ));
        }
        if !env_key_ok(&k) {
            return Err(bad("env key must match ^[A-Za-z_][A-Za-z0-9_]{0,63}$"));
        }
        if env_key_denied(&k) {
            return Err(ApiError::new(
                ErrorCode::EnvNotAllowed,
                "env key not allowed (AIBD_*, DYLD_*, LD_*, PATH)",
            ));
        }
        if v.len() > MAX_ENV_VALUE || v.contains('\0') {
            return Err(bad("env value must be at most 4096 bytes without NUL"));
        }
        env.push((k, v));
    }
    Ok(Request { argv, cwd, env })
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
    req.argv[0] = real;
    Ok(req)
}

struct Entry {
    rec: Session,
    run: Arc<Mutex<Box<dyn Running>>>,
    /// A stop (DELETE, root exit, shutdown) is running on this session.
    stopping: bool,
    /// Next event sequence for this session's aggregate.
    seq: i64,
    order: u64,
    /// A background re-check of its slot is running (one at a time).
    rescanning: bool,
}

#[derive(Default)]
struct Inner {
    /// Running sessions, and stop_unconfirmed ones still holding a slot.
    live: HashMap<String, Entry>,
    ended: VecDeque<(u64, Session)>,
    /// Slots taken by POSTs that are spawning right now (H3).
    reserved: usize,
    /// Starts that failed but may have left a process nobody controls
    /// (`SpawnFail::left_running`): their slots stay taken (H2, 043dd #539).
    held_unknown: usize,
    shutting: bool,
    next_order: u64,
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

fn lock(m: &Mutex<Inner>) -> MutexGuard<'_, Inner> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Sessions {
    /// `default_cwd` (`<data dir>/sessions`) must exist.
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
        Arc::new_cyclic(|me| Sessions {
            inner: Mutex::new(Inner::default()),
            cv: Condvar::new(),
            store,
            lineage,
            spawn,
            max,
            default_cwd,
            rescan_window: Mutex::new((RESCAN_WINDOW, RESCAN_PASS_MIN)),
            rejections: allowlist::RejectLog::default(),
            me: me.clone(),
        })
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
            .chain(g.ended.iter().cloned())
            .collect();
        v.sort_by_key(|(o, _)| std::cmp::Reverse(*o));
        v.into_iter().map(|(_, s)| s).collect()
    }

    pub fn get(&self, id: &str) -> Option<Session> {
        let g = lock(&self.inner);
        g.live.get(id).map(|e| e.rec.clone()).or_else(|| {
            g.ended
                .iter()
                .find(|(_, s)| s.id == id)
                .map(|(_, s)| s.clone())
        })
    }

    /// Running slots in use (live + reserved), for tests and metrics.
    pub fn slots_in_use(&self) -> usize {
        let g = lock(&self.inner);
        g.live.len() + g.reserved + g.held_unknown
    }

    /// POST. Blocking (spawn, event store).
    pub fn start(self: &Arc<Self>, req: Request) -> Result<Session, ApiError> {
        self.start_for_seat(None, req)
    }

    /// POST for a seat (`POST /api/v1/seats/{id}/sessions`); the caller has
    /// checked that the seat exists (the store refuses an unknown one too).
    pub fn start_for_seat(
        self: &Arc<Self>,
        seat_id: Option<String>,
        req: Request,
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
            if g.live.len() + g.reserved + g.held_unknown >= self.max {
                return Err(ApiError::new(
                    ErrorCode::TooManySessions,
                    format!("{} sessions already running", self.max),
                ));
            }
            g.reserved += 1; // H3: the slot is ours before anything starts
        }
        let cwd = req.cwd.unwrap_or_else(|| self.default_cwd.clone());
        let spec = Spec {
            id: id.clone(),
            argv: req.argv,
            cwd,
            env: req.env,
        };
        let res = (self.spawn)(&spec, self.lineage.clone());
        let mut g = lock(&self.inner);
        g.reserved -= 1;
        self.cv.notify_all();
        let mut run = match res {
            Ok(r) => r,
            Err(f) => {
                let Some(pid) = f.left_running else {
                    return Err(ApiError::new(
                        ErrorCode::SpawnFailed,
                        format!("could not start: {}", f.tag),
                    ));
                };
                // started but neither under control nor confirmed gone: the
                // slot stays taken and the event says so (never "not started")
                g.held_unknown += 1;
                self.append(
                    &id,
                    1,
                    "session.spawn_failed",
                    serde_json::json!({"id": id, "tag": f.tag, "left_running": pid}),
                );
                tracing::warn!(session = %id, pid, tag = %f.tag, "start failed, a process may be left running");
                return Err(ApiError::new(
                    ErrorCode::SpawnFailed,
                    format!(
                        "could not start: {} (a process may be left running: pid {pid})",
                        f.tag
                    ),
                ));
            }
        };
        let order = g.next_order;
        g.next_order += 1;
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
        };
        let exit_wait = run.exit_wait();
        let output = run.take_output();
        let shutting = g.shutting;
        g.live.insert(
            id.clone(),
            Entry {
                rec: rec.clone(),
                run: Arc::new(Mutex::new(run)),
                stopping: shutting,
                seq: 1,
                order,
                rescanning: false,
            },
        );
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
        if let Some(out) = output {
            // after `session.started`, so the first line comes after it on
            // the global cursor; the readers never take `inner`
            crate::session_output::start(self.store.clone(), &id, out);
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
            "killed" => (
                "session.killed",
                serde_json::json!({"id": id, "ended_by": by, "stop_status": stop.status.as_str(),
                    "reason": stop.status.reason(), "survivors": stop.survivors,
                    "tree_size": stop.tree_size}),
            ),
            _ => (
                "session.stop_unconfirmed",
                serde_json::json!({"id": id, "ended_by": by, "exit_code": rec.exit_code,
                    "signal": rec.signal, "stop_status": stop.status.as_str(),
                    "reason": stop.status.reason(), "survivors": stop.survivors,
                    "tree_size": stop.tree_size, "blind_spots": stop.blind_spots}),
            ),
        };
        self.event(&mut g, id, kind, payload);
        tracing::info!(session = %id, state = rec.state, ended_by = by,
            stop = stop.status.as_str(), survivors = stop.survivors.len(), "session stop");
        if stop.releases_slot() {
            if let Some(e) = g.live.remove(id) {
                g.ended.push_front((e.order, e.rec));
                g.ended.truncate(KEEP_ENDED);
            }
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
                if let Some(e) = g.live.remove(id) {
                    g.ended.push_front((e.order, e.rec));
                    g.ended.truncate(KEEP_ENDED);
                }
            }
            None => {
                self.event(
                    g,
                    id,
                    "session.slot_held",
                    serde_json::json!({"id": id, "reason": last, "tries": tries}),
                );
                tracing::warn!(session = %id, tries, reason = ?last, "slot still held after re-check");
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
    fn event(&self, g: &mut Inner, id: &str, kind: &str, payload: serde_json::Value) {
        let Some(e) = g.live.get_mut(id) else {
            return;
        };
        let seq = e.seq;
        e.seq += 1;
        let row = row_of(&e.rec);
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
    /// lives at most one window).
    fn arm_reject_flush(&self, token_id: String, code: &'static str, start: Instant) {
        let Some(me) = self.me.upgrade() else {
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("aibd-reject-flush".into())
            .spawn(move || {
                let op = crate::timers::SESSION_REJECT_FLUSH
                    .begin(Some(start + allowlist::REJECT_WINDOW));
                while op.tick() {}
                drop(op);
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
async fn read_valid(st: &AppState, headers: &HeaderMap, body: Body) -> Result<Request, Response> {
    if !json_content_type(headers) {
        return Err(bad("Content-Type must be application/json").into_response());
    }
    let bytes = match axum::body::to_bytes(body, MAX_BODY).await {
        Ok(b) => b,
        Err(_) => {
            return Err(
                ApiError::new(ErrorCode::PayloadTooLarge, "request body over 64 KiB")
                    .into_response(),
            )
        }
    };
    match validate(&bytes) {
        Ok(r) => Ok(r),
        Err(e) => {
            if e.0 == ErrorCode::EnvNotAllowed || e.1 == ARGV0_RELATIVE {
                let program = lenient_argv0_name(&bytes);
                let audit = st.sessions.clone();
                let code = e.0;
                let _ = tokio::task::spawn_blocking(move || {
                    audit.record_rejection(Instant::now(), TOKEN_ID_STARTUP, code, &program)
                })
                .await;
            }
            Err(e.into_response())
        }
    }
}

/// The M1 gate ([`admit`]) on a blocking thread, shared by both POST routes; a refusal
/// is audited.
async fn gate(st: &AppState, req: Request) -> Result<Request, Response> {
    let policy = st.session_policy.clone();
    let audit = st.sessions.clone();
    let run = move || {
        admit(req, &policy).map_err(|r| {
            audit.record_rejection(Instant::now(), TOKEN_ID_STARTUP, r.error.0, &r.program);
            r.error
        })
    };
    match tokio::task::spawn_blocking(run).await {
        Ok(Ok(r)) => Ok(r),
        Ok(Err(e)) => Err(e.into_response()),
        Err(_) => Err(ApiError::new(ErrorCode::Internal, "internal error").into_response()),
    }
}

pub async fn create(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    let req = match read_valid(&st, &headers, body).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    let req = match gate(&st, req).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    let s = st.sessions.clone();
    match tokio::task::spawn_blocking(move || s.start(req)).await {
        Ok(Ok(rec)) => (StatusCode::CREATED, Json(rec)).into_response(),
        Ok(Err(e)) => e.into_response(),
        Err(_) => ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
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
        Err(resp) => return resp,
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
    // Same M1 gate as `POST /api/v1/sessions` (integ preview.3: the seat route came from
    // #592 and the gate from #593; f1fae joined them, 043dd made them one helper).
    let req = match gate(&st, req).await {
        Ok(r) => r,
        Err(resp) => return resp,
    };
    let s = st.sessions.clone();
    match tokio::task::spawn_blocking(move || s.start_for_seat(Some(seat), req)).await {
        Ok(Ok(rec)) => (StatusCode::CREATED, Json(rec)).into_response(),
        Ok(Err(e)) => e.into_response(),
        Err(_) => ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
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
) -> Response {
    let grace = match q.get("grace_ms") {
        None => DEFAULT_GRACE,
        Some(v) => match v.parse::<u64>() {
            Ok(ms) if ms <= MAX_GRACE_MS => Duration::from_millis(ms),
            _ => return bad("grace_ms must be an integer 0-30000").into_response(),
        },
    };
    let s = st.sessions.clone();
    match tokio::task::spawn_blocking(move || s.stop(&id, grace)).await {
        Ok(Ok(rec)) => Json(rec).into_response(),
        Ok(Err(e)) => e.into_response(),
        Err(_) => ApiError::new(ErrorCode::Internal, "internal error").into_response(),
    }
}

pub async fn method_not_allowed() -> Response {
    ApiError::new(ErrorCode::MethodNotAllowed, "method not allowed").into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    // absolute on the platform the test runs on (validation only, nothing
    // is started): "/bin/true" is not absolute on Windows
    const TRUE_ABS: &str = if cfg!(windows) {
        r"C:\Windows\System32\whoami.exe"
    } else {
        "/bin/true"
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
            serde_json::json!([ECHO_ABS, ""]),
            serde_json::json!([ECHO_ABS, "a\u{0}b"]),
            serde_json::json!(vec![ECHO_ABS; 65]),
            serde_json::json!([ECHO_ABS, "x".repeat(4097)]),
        ] {
            let e = v(serde_json::json!({ "argv": bad_argv })).unwrap_err();
            assert_eq!(e.0, ErrorCode::InvalidRequest, "{bad_argv}");
        }
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
}
