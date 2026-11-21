//! G6 驗收執行器, first slice (DESIGN-g6-verify-runner-7b81e-1121; 519d0 17:54, cutover r2):
//! a review seat in the web page checks what it used to check from a shell, by naming one of a
//! fixed list of checks. aibd answers with fixed fields; a seat sends no command, no path, no
//! address and no SQL, and nothing here writes.
//!
//! - [`RECEIPT`] `{run_id}`: what aibd read in any seat's receipt run
//!   ([`crate::receipt_run::view`]: state, counts, reason codes), not only the asker's own
//!   (checking another seat's receipt is the reviewer's job). No machine is asked: the verdict is
//!   the one aibd kept when the run ended.
//! - [`EVENTS`] `{after?, aggregate_id?, types?, limit?}`: rows of the event log without their
//!   payload (`global_seq`, `aggregate_type`, `aggregate_id`, `sequence`, `type`,
//!   `occurred_at`), through the store's own reads. At most [`MAX_ROWS`].
//! - [`SEAT`] `{seat_id}`: one seat's row, its meta without folder or resume id, and its newest
//!   [`MAX_SESSIONS`] sessions without pid or argv.
//!
//! - [`BUNDLE`] `{file, repo, base, onto?}`: a `BUNDLE-*.bundle` of the pending folder, checked
//!   in a bare repository of aibd's own that reads the registered repo's objects (as
//!   [`crate::receipt_bundle`]: no git runs in anyone's copy): its sha256 and length, its heads,
//!   whether `base` is an ancestor of the first head, how many files the head changes since
//!   `base`, and with `onto` whether the head merges onto that commit cleanly and which files
//!   conflict ([`check_bundle`]).
//!
//! Who asks: the built-in page of this pairing, or a seat's own token when that seat is the
//! `reviewer` or `verifier` on some node of the workspace ([`verifies`]); a seat may ask
//! [`PER_HOUR`] times an hour. No fingerprint (DESIGN §3): every answer is read-only.
use crate::api::AppState;
use axum::{http::StatusCode, response::IntoResponse, response::Response, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const RECEIPT: &str = "verify_receipt";
pub const EVENTS: &str = "verify_events";
pub const SEAT: &str = "verify_seat";
pub const BUNDLE: &str = "verify_bundle";
pub const MAX_HEADS: usize = 8;
pub const MAX_CONFLICTS: usize = 50;
/// The keys each answer has, and no other (5d33a PREREAD-g6 L1: the tests assert the set).
pub const EVENT_KEYS: [&str; 6] = [
    "global_seq",
    "aggregate_type",
    "aggregate_id",
    "sequence",
    "type",
    "occurred_at",
];
pub const SEAT_KEYS: [&str; 6] = [
    "seat_id",
    "name",
    "state",
    "revision",
    "created_at",
    "updated_at",
];
pub const META_KEYS: [&str; 6] = [
    "provider",
    "role",
    "model",
    "model_pinned",
    "mode",
    "updated_at",
];
pub const SESSION_KEYS: [&str; 8] = [
    "session_id",
    "state",
    "started_at",
    "ended_at",
    "ended_by",
    "exit_code",
    "signal",
    "stop_status",
];
/// What a seat may ask in an hour (5d33a PREREAD-g6 Q1: 30, and each one an event).
pub const PER_HOUR: usize = 30;
pub const MAX_ROWS: u32 = 200;
pub const MAX_TYPES: usize = 8;
pub const MAX_SESSIONS: usize = 20;
/// The roles of the workspace that verify ([`crate::assign::ROLES`]).
pub const ROLES: [&str; 2] = ["reviewer", "verifier"];

pub fn is_act(body: &Value) -> bool {
    matches!(
        body.get("type").and_then(Value::as_str),
        Some(RECEIPT | EVENTS | SEAT | BUNDLE)
    )
}

/// Whether the run's `.out` carried its own sha256 and it matched (5d33a PREREAD-g6 N2): the
/// verdict says `seal_missing` / `seal_mismatch` otherwise. `None`: no `.out` read yet.
pub fn sealed_of(run: &crate::receipt_run::Run) -> Option<bool> {
    run.verdict.as_ref().map(|v| {
        !v.reasons
            .iter()
            .any(|r| r == "seal_missing" || r == "seal_mismatch")
    })
}

/// A bundle's name in the pending folder: `BUNDLE-<letters, digits, . _ ->.bundle`.
pub fn bundle_name_ok(f: &str) -> bool {
    f.len() <= 160
        && f.starts_with("BUNDLE-")
        && f.ends_with(".bundle")
        && f.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
        && !f.contains("..")
}

/// A ref name or path as a page is told it: at most 200 characters, anything but
/// `[A-Za-z0-9/._-]` as `?`.
fn plain(s: &str) -> String {
    s.chars()
        .take(200)
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '/' | '.' | '_' | '-') {
                c
            } else {
                '?'
            }
        })
        .collect()
}

/// What [`check_bundle`] found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundleCheck {
    pub len: u64,
    pub sha256: String,
    /// `(commit, ref)`, as [`plain`] words.
    pub heads: Vec<(String, String)>,
    pub base_is_ancestor: bool,
    pub files_changed: usize,
    /// With `onto`: `(clean, conflicting files)`.
    pub onto: Option<(bool, Vec<String>)>,
}

fn sha256_file(p: &std::path::Path) -> Option<(u64, String)> {
    use sha2::Digest;
    let mut f = std::fs::File::open(p).ok()?;
    let mut h = sha2::Sha256::new();
    let len = std::io::copy(&mut f, &mut h).ok()?;
    Some((
        len,
        h.finalize().iter().map(|b| format!("{b:02x}")).collect(),
    ))
}

fn check_bundle_in(
    repo: &std::path::Path,
    hooks: &std::path::Path,
    scratch: &std::path::Path,
    bundle: &std::path::Path,
    base: &str,
    onto: Option<&str>,
) -> Result<BundleCheck, &'static str> {
    use crate::seat_worktree::{git, git_code, repo_safe};
    use crate::send_back::sha_ok;
    if !sha_ok(base) || onto.is_some_and(|o| !sha_ok(o)) {
        return Err("verify_sha_shape");
    }
    repo_safe(repo).map_err(|_| "repo_unsafe")?;
    let (len, sha256) = sha256_file(bundle).ok_or("bundle_not_found")?;
    if len == 0 || len > aib_receipt_gate::MAX_BUNDLE {
        return Err("bundle_size");
    }
    std::fs::create_dir_all(scratch).map_err(|_| "internal")?;
    git(scratch, hooks, &["init", "-q", "--bare", "."]).map_err(|_| "internal")?;
    let objects = repo.join(".git").join("objects");
    let line = objects
        .to_str()
        .filter(|s| !s.contains('\n'))
        .ok_or("repo_unsafe")?;
    std::fs::write(
        scratch.join("objects").join("info").join("alternates"),
        format!("{line}\n"),
    )
    .map_err(|_| "internal")?;
    let b = bundle.to_str().ok_or("bundle_not_found")?;
    git(scratch, hooks, &["bundle", "verify", "-q", b]).map_err(|_| "bundle_refused")?;
    let listed = git(scratch, hooks, &["bundle", "list-heads", b]).map_err(|_| "bundle_refused")?;
    let heads: Vec<(String, String)> = listed
        .lines()
        .filter_map(|l| l.split_once(' '))
        .map(|(c, r)| (c.to_owned(), plain(r)))
        .collect();
    if heads.is_empty() || heads.iter().any(|(c, _)| !sha_ok(c)) {
        return Err("bundle_no_head");
    }
    if heads.len() > MAX_HEADS {
        return Err("bundle_too_many_heads");
    }
    // every object it brings is checked as a fetch checks it (5d33a PREREAD-g6 L2)
    git(
        scratch,
        hooks,
        &[
            "-c",
            "fetch.fsckObjects=true",
            "-c",
            "transfer.fsckObjects=true",
            "bundle",
            "unbundle",
            b,
        ],
    )
    .map_err(|_| "bundle_refused")?;
    let head = heads[0].0.clone();
    let known = |c: &str| {
        git(
            scratch,
            hooks,
            &["cat-file", "-e", &format!("{c}^{{commit}}")],
        )
        .is_ok()
    };
    if !known(base) {
        return Err("base_unknown");
    }
    let base_is_ancestor = git(
        scratch,
        hooks,
        &["merge-base", "--is-ancestor", base, &head],
    )
    .is_ok();
    let z = git(
        scratch,
        hooks,
        &["diff", "--name-only", "-z", base, &head, "--"],
    )
    .map_err(|_| "internal")?;
    let files_changed = z.split('\0').filter(|p| !p.is_empty()).count();
    let onto = match onto {
        None => None,
        Some(o) => {
            if !known(o) {
                return Err("onto_unknown");
            }
            let (code, out) = git_code(
                scratch,
                hooks,
                &[
                    "merge-tree",
                    "--write-tree",
                    "--name-only",
                    "--no-messages",
                    o,
                    &head,
                ],
            )
            .map_err(|_| "internal")?;
            match code {
                0 => Some((true, Vec::new())),
                // the first line is the tree; the conflicting files follow
                1 => Some((
                    false,
                    out.lines()
                        .skip(1)
                        .filter(|l| !l.is_empty())
                        .take(MAX_CONFLICTS)
                        .map(plain)
                        .collect(),
                )),
                _ => return Err("merge_tree_failed"),
            }
        }
    };
    Ok(BundleCheck {
        len,
        sha256,
        heads,
        base_is_ancestor,
        files_changed,
        onto,
    })
}

/// Checks `bundle` against the registered `repo` in `scratch` (a folder of aibd's own that
/// does not exist yet; it is gone afterwards). Blocking (git).
pub fn check_bundle(
    repo: &std::path::Path,
    hooks: &std::path::Path,
    scratch: &std::path::Path,
    bundle: &std::path::Path,
    base: &str,
    onto: Option<&str>,
) -> Result<BundleCheck, &'static str> {
    let got = check_bundle_in(repo, hooks, scratch, bundle, base, onto);
    let _ = std::fs::remove_dir_all(scratch);
    got
}

/// Whether `seat` holds one of [`ROLES`] on a node, from `nodes` and `members` (the workspace's
/// reads, [`crate::assign::nodes_of`] / [`crate::assign::members_of`]). `None`: not open.
pub fn verifies_with(
    seat: &str,
    nodes: impl Fn(&str) -> Option<Vec<String>>,
    members: impl Fn(&str) -> Option<Vec<(String, String)>>,
) -> Option<bool> {
    for node in nodes(seat)? {
        if members(&node)?
            .iter()
            .any(|(s, r)| s == seat && ROLES.contains(&r.as_str()))
        {
            return Some(true);
        }
    }
    Some(false)
}

/// [`verifies_with`] on this aibd's workspace. Blocking (SQLite).
pub fn verifies(seat: &str) -> Option<bool> {
    verifies_with(seat, crate::assign::nodes_of, crate::assign::members_of)
}

static ASKED: Mutex<Option<HashMap<String, VecDeque<Instant>>>> = Mutex::new(None);

/// Whether `seat` may ask now (and counts this one).
fn within_rate(seat: &str, now: Instant) -> bool {
    let mut g = ASKED.lock().unwrap_or_else(|e| e.into_inner());
    let q = g
        .get_or_insert_with(HashMap::new)
        .entry(seat.to_owned())
        .or_default();
    while q
        .front()
        .is_some_and(|t| now.duration_since(*t) >= Duration::from_secs(3600))
    {
        q.pop_front();
    }
    if q.len() >= PER_HOUR {
        return false;
    }
    q.push_back(now);
    true
}

fn refuse(status: StatusCode, reason: &str, message: &str) -> Response {
    (
        status,
        Json(
            json!({"code": "refused", "reason": reason, "message": message,
                    "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

/// An aggregate id or seat id as the store writes them.
fn id_ok(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b':'))
}

/// An event type (`seat.created`, `receipt_run.asked`, …).
fn type_ok(s: &str) -> bool {
    (1..=48).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'_' | b'.'))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReceiptAsk {
    #[serde(rename = "type")]
    _type: String,
    run_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EventsAsk {
    #[serde(rename = "type")]
    _type: String,
    #[serde(default)]
    after: Option<i64>,
    #[serde(default)]
    aggregate_id: Option<String>,
    #[serde(default)]
    types: Option<Vec<String>>,
    #[serde(default)]
    limit: Option<u32>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SeatAsk {
    #[serde(rename = "type")]
    _type: String,
    seat_id: String,
}

/// Which of the store's reads an [`EVENTS`] body asks for.
#[derive(Debug, PartialEq, Eq)]
pub enum EventsRead {
    After {
        after: i64,
        limit: u32,
    },
    Of {
        aggregate_id: String,
        after: i64,
        limit: u32,
    },
    Types {
        types: Vec<String>,
        limit: u32,
    },
}

/// The read an [`EVENTS`] body asks for, checked: `aggregate_id` and `types` are not asked
/// together; `after` is 0 or more; `limit` 1 to [`MAX_ROWS`] (default [`MAX_ROWS`]).
pub fn events_read(body: Value) -> Result<EventsRead, &'static str> {
    let a: EventsAsk = serde_json::from_value(body).map_err(|_| "verify_fields")?;
    let limit = a.limit.unwrap_or(MAX_ROWS);
    if !(1..=MAX_ROWS).contains(&limit) {
        return Err("verify_limit");
    }
    let after = a.after.unwrap_or(0);
    if after < 0 {
        return Err("verify_after");
    }
    match (a.aggregate_id, a.types) {
        (Some(_), Some(_)) => Err("verify_one_filter"),
        (Some(id), None) if id_ok(&id) => Ok(EventsRead::Of {
            aggregate_id: id,
            after,
            limit,
        }),
        (Some(_), None) => Err("verify_aggregate_id"),
        (None, Some(t)) if (1..=MAX_TYPES).contains(&t.len()) && t.iter().all(|s| type_ok(s)) => {
            Ok(EventsRead::Types { types: t, limit })
        }
        (None, Some(_)) => Err("verify_types"),
        (None, None) => Ok(EventsRead::After { after, limit }),
    }
}

/// One event as a review seat is told it: no payload, no event id.
pub fn event_row(e: &aib_store::StoredEvent) -> Value {
    json!({
        "global_seq": e.global_seq,
        "aggregate_type": e.aggregate_type,
        "aggregate_id": e.aggregate_id,
        "sequence": e.sequence,
        "type": e.event_type,
        "occurred_at": e.occurred_at,
    })
}

/// One seat as a review seat is told it: no folder, no resume id, no pid, no argv.
pub fn seat_view(
    s: &aib_store::Seat,
    m: Option<&aib_store::SeatMeta>,
    sessions: &[aib_store::SessionRow],
) -> Value {
    let newest: Vec<Value> = sessions
        .iter()
        .rev()
        .take(MAX_SESSIONS)
        .map(|r| {
            json!({
                "session_id": r.session_id,
                "state": r.state,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "ended_by": r.ended_by,
                "exit_code": r.exit_code,
                "signal": r.signal,
                "stop_status": r.stop_status,
            })
        })
        .collect();
    json!({
        "seat": {
            "seat_id": s.seat_id,
            "name": s.name,
            "state": s.state,
            "revision": s.revision,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        },
        "meta": m.map(|m| json!({
            "provider": m.provider,
            "role": m.role,
            "model": m.model,
            "model_pinned": m.model_pinned,
            "mode": m.mode,
            "updated_at": m.updated_at,
        })),
        "sessions": newest,
    })
}

/// A review seat's own token asks: it must hold [`ROLES`] on a node, and within [`PER_HOUR`].
pub async fn seat_act(st: AppState, seat: &str, body: Value) -> Response {
    let who = seat.to_owned();
    let ok = tokio::task::spawn_blocking(move || verifies(&who))
        .await
        .ok()
        .flatten();
    match ok {
        Some(true) => {}
        Some(false) => {
            return refuse(
                StatusCode::FORBIDDEN,
                "verify_review_seat_only",
                "only a seat that is the reviewer or verifier on a node runs a check",
            )
        }
        None => {
            return refuse(
                StatusCode::SERVICE_UNAVAILABLE,
                "workspace_unavailable",
                "the workspace is not open",
            )
        }
    }
    if !within_rate(seat, Instant::now()) {
        return refuse(
            StatusCode::TOO_MANY_REQUESTS,
            "verify_too_often",
            "at most 30 checks an hour for a seat",
        );
    }
    // the check's name is one of the fixed four (is_act let it in)
    let check = body.get("type").cloned().unwrap_or(Value::Null);
    crate::seats::side_event(
        &st,
        "verify",
        "verify.checked",
        json!({"seat": seat, "check": check}),
    )
    .await;
    act(st, body).await
}

/// One check; the caller has decided who may ask (the built-in page, or [`seat_act`]).
pub async fn act(st: AppState, body: Value) -> Response {
    match body.get("type").and_then(Value::as_str) {
        Some(RECEIPT) => receipt(body).await,
        Some(EVENTS) => events(st, body).await,
        Some(SEAT) => seat(st, body).await,
        Some(BUNDLE) => bundle(st, body).await,
        _ => refuse(StatusCode::BAD_REQUEST, "verify_unknown", "no such check"),
    }
}

async fn receipt(body: Value) -> Response {
    let Ok(a) = serde_json::from_value::<ReceiptAsk>(body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "verify_fields",
            "body must be {type, run_id}",
        );
    };
    if !crate::receipt_run::run_id_ok(&a.run_id) {
        return refuse(StatusCode::BAD_REQUEST, "run_id_shape", "not a run id");
    }
    let id = a.run_id.clone();
    match tokio::task::spawn_blocking(move || crate::receipt_run::find(&id)).await {
        Ok(Some(run)) => Json(json!({
            "run": crate::receipt_run::view(&run),
            "sealed": sealed_of(&run),
        }))
        .into_response(),
        Ok(None) => refuse(StatusCode::NOT_FOUND, "no_such_run", "no run of that id"),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the runs could not be read",
        ),
    }
}

async fn events(st: AppState, body: Value) -> Response {
    let read = match events_read(body) {
        Ok(r) => r,
        Err(why) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                why,
                "body is {type, after?, aggregate_id? | types?, limit?}",
            )
        }
    };
    let store = st.store.clone();
    let got = tokio::task::spawn_blocking(move || match read {
        EventsRead::After { after, limit } => store.events_after(after, limit),
        EventsRead::Of {
            aggregate_id,
            after,
            limit,
        } => store.events_of(&aggregate_id, after, limit),
        EventsRead::Types { types, limit } => {
            let t: Vec<&str> = types.iter().map(String::as_str).collect();
            store.events_of_types(&t, limit)
        }
    })
    .await;
    match got {
        Ok(Ok(rows)) => {
            let rows: Vec<Value> = rows.iter().map(event_row).collect();
            Json(json!({"events": rows})).into_response()
        }
        _ => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the events could not be read",
        ),
    }
}

async fn seat(st: AppState, body: Value) -> Response {
    let Ok(a) = serde_json::from_value::<SeatAsk>(body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "verify_fields",
            "body must be {type, seat_id}",
        );
    };
    if !crate::assign::seat_id_ok(&a.seat_id) {
        return refuse(StatusCode::BAD_REQUEST, "seat_id_shape", "not a seat id");
    }
    let store = st.store.clone();
    let got = tokio::task::spawn_blocking(move || {
        let s = store.seat(&a.seat_id)?;
        let m = store.seat_meta(&a.seat_id)?;
        let r = store.sessions_of_seat(&a.seat_id)?;
        Ok::<_, aib_store::StoreError>((s, m, r))
    })
    .await;
    match got {
        Ok(Ok((Some(s), m, r))) => Json(seat_view(&s, m.as_ref(), &r)).into_response(),
        Ok(Ok((None, _, _))) => refuse(StatusCode::NOT_FOUND, "no_such_seat", "no such seat"),
        _ => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the seat could not be read",
        ),
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BundleAsk {
    #[serde(rename = "type")]
    _type: String,
    file: String,
    repo: String,
    base: String,
    #[serde(default)]
    onto: Option<String>,
}

async fn bundle(st: AppState, body: Value) -> Response {
    let Ok(a) = serde_json::from_value::<BundleAsk>(body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "verify_fields",
            "body must be {type, file, repo, base, onto?}",
        );
    };
    if !bundle_name_ok(&a.file) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "bundle_name",
            "a BUNDLE-*.bundle of pending",
        );
    }
    let Some(root) = crate::docs::root() else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "pending_unavailable",
            "no pending folder",
        );
    };
    let path = root.join(&a.file);
    match std::fs::symlink_metadata(&path) {
        Ok(m) if m.file_type().is_file() => {}
        _ => return refuse(StatusCode::NOT_FOUND, "bundle_not_found", "no such bundle"),
    }
    let private = st.sessions.private_dir().to_path_buf();
    let data = private.parent().unwrap_or(&private).to_path_buf();
    let Some(repo) = crate::repos::find(&data, &a.repo) else {
        return refuse(
            StatusCode::NOT_FOUND,
            "repo_not_registered",
            "no registered repo of that name",
        );
    };
    let Ok(id) = crate::sessions::new_id("vb_") else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no id");
    };
    let scratch = data.join("verify-scratch").join(id);
    let hooks = crate::seats::seat_git_hooks(&private);
    let repo_path = std::path::PathBuf::from(repo.path);
    let got = tokio::task::spawn_blocking(move || {
        check_bundle(
            &repo_path,
            &hooks,
            &scratch,
            &path,
            &a.base,
            a.onto.as_deref(),
        )
    })
    .await;
    match got {
        Ok(Ok(c)) => Json(json!({
            "len": c.len,
            "sha256": c.sha256,
            "heads": c.heads.iter().map(|(h, r)| json!({"commit": h, "ref": r})).collect::<Vec<_>>(),
            "base_is_ancestor": c.base_is_ancestor,
            "files_changed": c.files_changed,
            "onto": c.onto.map(|(clean, files)| json!({"clean": clean, "conflicts": files})),
        }))
        .into_response(),
        Ok(Err(why)) => refuse(StatusCode::UNPROCESSABLE_ENTITY, why, "the bundle was not checked"),
        Err(_) => refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "the check did not end"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn members<'a>(
        rows: &'a [(&'a str, &'a str, &'a str)],
    ) -> impl Fn(&str) -> Option<Vec<(String, String)>> + 'a {
        move |node| {
            Some(
                rows.iter()
                    .filter(|(n, _, _)| *n == node)
                    .map(|(_, s, r)| (s.to_string(), r.to_string()))
                    .collect(),
            )
        }
    }

    fn nodes<'a>(
        rows: &'a [(&'a str, &'a str, &'a str)],
    ) -> impl Fn(&str) -> Option<Vec<String>> + 'a {
        move |seat| {
            Some(
                rows.iter()
                    .filter(|(_, s, _)| *s == seat)
                    .map(|(n, _, _)| n.to_string())
                    .collect(),
            )
        }
    }

    fn keys(v: &Value) -> Vec<String> {
        let mut k: Vec<String> = v.as_object().unwrap().keys().cloned().collect();
        k.sort();
        k
    }

    fn sorted(k: &[&str]) -> Vec<String> {
        let mut k: Vec<String> = k.iter().map(|s| s.to_string()).collect();
        k.sort();
        k
    }

    #[test]
    fn a_receipt_is_told_whether_its_seal_held() {
        let mut run: crate::receipt_run::Run = serde_json::from_value(json!({
            "run_id": "web-ab12c-012345678", "seat_id": "seat_ab12c", "head": "0".repeat(40),
            "scope": "full", "state": "running", "asked_at": "t"
        }))
        .unwrap();
        assert_eq!(sealed_of(&run), None, "reason: unread_called_sealed");
        let verdict = |reasons: &[&str]| crate::receipt_check::Verdict {
            green: reasons.is_empty(),
            reasons: reasons.iter().map(|s| s.to_string()).collect(),
            narrow: false,
            tests: None,
            knives: None,
            sandbox: false,
        };
        run.verdict = Some(verdict(&["rc_nonzero:test"]));
        assert_eq!(sealed_of(&run), Some(true), "reason: red_called_unsealed");
        for bad in ["seal_missing", "seal_mismatch"] {
            run.verdict = Some(verdict(&[bad, "rc_nonzero:test"]));
            assert_eq!(sealed_of(&run), Some(false), "reason: {bad}_called_sealed");
        }
    }

    #[test]
    fn only_a_reviewer_or_verifier_on_some_node_runs_a_check() {
        let rows = [
            ("N-a", "seat_rev", "reviewer"),
            ("N-a", "seat_dev", "author"),
            ("N-b", "seat_ver", "verifier"),
            ("N-b", "seat_obs", "observer"),
        ];
        let v = |s: &str| verifies_with(s, nodes(&rows), members(&rows));
        assert_eq!(v("seat_rev"), Some(true), "reason: reviewer_refused");
        assert_eq!(v("seat_ver"), Some(true), "reason: verifier_refused");
        assert_eq!(v("seat_dev"), Some(false), "reason: author_let_in");
        assert_eq!(v("seat_obs"), Some(false), "reason: observer_let_in");
        assert_eq!(v("seat_none"), Some(false), "reason: stranger_let_in");
        // a workspace that is not open is not a yes
        assert_eq!(
            verifies_with("seat_rev", |_| None, members(&rows)),
            None,
            "reason: closed_workspace_let_in"
        );
    }

    #[test]
    fn an_events_ask_takes_one_filter_of_checked_words() {
        let r = events_read;
        assert_eq!(
            r(json!({"type": EVENTS})),
            Ok(EventsRead::After {
                after: 0,
                limit: MAX_ROWS
            })
        );
        assert_eq!(
            r(json!({"type": EVENTS, "aggregate_id": "seat_ab12", "after": 3, "limit": 5})),
            Ok(EventsRead::Of {
                aggregate_id: "seat_ab12".into(),
                after: 3,
                limit: 5
            })
        );
        assert_eq!(
            r(json!({"type": EVENTS, "types": ["seat.created", "receipt_run.asked"]})),
            Ok(EventsRead::Types {
                types: vec!["seat.created".into(), "receipt_run.asked".into()],
                limit: MAX_ROWS
            })
        );
        for (b, why) in [
            (
                json!({"type": EVENTS, "aggregate_id": "a", "types": ["x"]}),
                "verify_one_filter",
            ),
            (
                json!({"type": EVENTS, "aggregate_id": "a b"}),
                "verify_aggregate_id",
            ),
            (
                json!({"type": EVENTS, "aggregate_id": "x' OR 1=1 --"}),
                "verify_aggregate_id",
            ),
            (
                json!({"type": EVENTS, "types": ["Seat.Created"]}),
                "verify_types",
            ),
            (json!({"type": EVENTS, "types": []}), "verify_types"),
            (
                json!({"type": EVENTS, "types": ["a","b","c","d","e","f","g","h","i"]}),
                "verify_types",
            ),
            (json!({"type": EVENTS, "limit": 0}), "verify_limit"),
            (json!({"type": EVENTS, "limit": 201}), "verify_limit"),
            (json!({"type": EVENTS, "after": -1}), "verify_after"),
            (json!({"type": EVENTS, "sql": "SELECT 1"}), "verify_fields"),
        ] {
            assert_eq!(r(b.clone()), Err(why), "reason: let_in {b}");
        }
    }

    #[test]
    fn an_event_is_told_without_its_payload() {
        let e = aib_store::StoredEvent {
            global_seq: 7,
            event_id: "ev_secret".into(),
            aggregate_type: "seat".into(),
            aggregate_id: "seat_ab12".into(),
            sequence: 2,
            event_type: "seat.created".into(),
            schema_version: 1,
            correlation_id: Some("c".into()),
            occurred_at: "2026-11-21T10:00:00Z".into(),
            payload: json!({"text": "a letter's words", "email": "x@example.com"}),
        };
        let v = event_row(&e);
        let s = v.to_string();
        assert!(
            !s.contains("letter") && !s.contains("example.com"),
            "reason: payload_told {s}"
        );
        assert!(!s.contains("ev_secret"), "reason: event_id_told {s}");
        assert_eq!(v["type"], "seat.created");
        assert_eq!(v["global_seq"], 7);
        assert_eq!(keys(&v), sorted(&EVENT_KEYS), "reason: event_keys");
    }

    #[test]
    fn a_seat_is_told_without_its_folder_resume_id_pid_or_argv() {
        let s = aib_store::Seat {
            seat_id: "seat_ab12".into(),
            name: "審查".into(),
            state: "RUNNING".into(),
            revision: 3,
            created_at: "t0".into(),
            updated_at: "t1".into(),
        };
        let m = aib_store::SeatMeta {
            seat_id: "seat_ab12".into(),
            provider: "claude".into(),
            role: Some("dev".into()),
            cwd: "/Users/someone/secret-folder".into(),
            model: None,
            model_pinned: false,
            resume_id: Some("resume-secret".into()),
            project_uid: None,
            persona_uid: None,
            brain_route: None,
            updated_at: "t1".into(),
            mode: Some("work".into()),
            axis_id: None,
            cabin: None,
        };
        let row = |i: usize| aib_store::SessionRow {
            session_id: format!("s{i}"),
            seat_id: Some("seat_ab12".into()),
            pid: 4242,
            argv0: Some("/opt/argv-secret".into()),
            argc: 9,
            state: "exited".into(),
            holds_slot: false,
            started_at: "t".into(),
            ended_at: None,
            ended_by: None,
            exit_code: Some(0),
            signal: None,
            stop_status: None,
            stop_reason: None,
            updated_at: "t".into(),
        };
        let rows: Vec<_> = (0..MAX_SESSIONS + 5).map(row).collect();
        let v = seat_view(&s, Some(&m), &rows);
        let t = v.to_string();
        for secret in ["secret-folder", "resume-secret", "argv-secret", "4242"] {
            assert!(!t.contains(secret), "reason: told {secret} {t}");
        }
        let told = v["sessions"].as_array().unwrap();
        assert_eq!(told.len(), MAX_SESSIONS, "reason: sessions_not_capped");
        assert_eq!(
            told[0]["session_id"],
            format!("s{}", MAX_SESSIONS + 4),
            "reason: not_newest_first"
        );
        assert_eq!(v["meta"]["mode"], "work");
        assert_eq!(
            keys(&v),
            sorted(&["meta", "seat", "sessions"]),
            "reason: top_keys"
        );
        assert_eq!(keys(&v["seat"]), sorted(&SEAT_KEYS), "reason: seat_keys");
        assert_eq!(keys(&v["meta"]), sorted(&META_KEYS), "reason: meta_keys");
        for s in told {
            assert_eq!(keys(s), sorted(&SESSION_KEYS), "reason: session_keys");
        }
    }

    #[test]
    fn a_seat_asks_at_most_its_hourly_count() {
        let t0 = Instant::now();
        let seat = "seat_rate_g6";
        for i in 0..PER_HOUR {
            assert!(within_rate(seat, t0), "reason: refused_early {i}");
        }
        assert!(!within_rate(seat, t0), "reason: over_the_count_let_in");
        assert!(
            within_rate("seat_rate_g6_other", t0),
            "reason: counted_across_seats"
        );
        assert!(
            within_rate(seat, t0 + Duration::from_secs(3600)),
            "reason: never_forgets"
        );
    }

    #[test]
    fn a_bundle_is_named_from_pending_only() {
        for ok in [
            "BUNDLE-g6-verify-7b81e-b48372ac01-cae7d5cca.bundle",
            "BUNDLE-a_b.c.bundle",
        ] {
            assert!(bundle_name_ok(ok), "reason: refused {ok}");
        }
        for bad in [
            "../BUNDLE-x.bundle",
            "BUNDLE-a/b.bundle",
            "BUNDLE-..bundle",
            "RECEIPT-x.out",
            "BUNDLE-x.bundle.sh",
            "BUNDLE-x y.bundle",
            "BUNDLE-\u{4e2d}.bundle",
        ] {
            assert!(!bundle_name_ok(bad), "reason: let_in {bad}");
        }
    }

    /// A registered repo with main M, a seat's copy with one commit on a.rs, and its bundle.
    fn bundled() -> (
        crate::receipt_bundle::fixture::Fix,
        String,
        std::path::PathBuf,
    ) {
        use crate::receipt_bundle::fixture::{fix, g};
        let f = fix();
        let head = f.commit("a.rs", "fn a() {}\n");
        let b = f.data.path().join("BUNDLE-t.bundle");
        let not_main = format!("^{}", f.main);
        g(
            &f.tree,
            &[
                "bundle",
                "create",
                "-q",
                b.to_str().unwrap(),
                "HEAD",
                &not_main,
            ],
        );
        (f, head, b)
    }

    fn checked(
        f: &crate::receipt_bundle::fixture::Fix,
        b: &std::path::Path,
        base: &str,
        onto: Option<&str>,
    ) -> Result<BundleCheck, &'static str> {
        let scratch = f.data.path().join("verify-scratch").join("s1");
        let got = check_bundle(&f.repo, &f.hooks, &scratch, b, base, onto);
        assert!(!scratch.exists(), "reason: scratch_left");
        got
    }

    #[test]
    fn a_bundle_is_told_its_head_its_size_and_what_it_changes() {
        let (f, head, b) = bundled();
        let c = checked(&f, &b, &f.main, None).unwrap();
        assert_eq!(c.heads, vec![(head.clone(), "HEAD".to_owned())]);
        assert!(c.base_is_ancestor, "reason: base_not_ancestor");
        assert_eq!(c.files_changed, 1, "reason: files_changed");
        let bytes = std::fs::read(&b).unwrap();
        assert_eq!(c.len, bytes.len() as u64);
        use sha2::Digest;
        let want: String = sha2::Sha256::digest(&bytes)
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect();
        assert_eq!(c.sha256, want, "reason: sha256_not_of_the_file");
        assert_eq!(c.onto, None);
    }

    #[test]
    fn a_bundle_onto_another_head_is_told_clean_or_its_conflicting_files() {
        use crate::receipt_bundle::fixture::{g, out};
        let (f, _head, b) = bundled();
        // the registered repo moves on twice: one commit on a.rs (conflicts), one on b.rs
        std::fs::write(f.repo.join("a.rs"), "fn a() { other() }\n").unwrap();
        g(&f.repo, &["add", "-A"]);
        g(&f.repo, &["commit", "-q", "-m", "a"]);
        let clash = out(&f.repo, &["rev-parse", "HEAD"]);
        g(&f.repo, &["checkout", "-q", "-b", "side", &f.main]);
        std::fs::write(f.repo.join("b.rs"), "fn b() {}\n").unwrap();
        g(&f.repo, &["add", "-A"]);
        g(&f.repo, &["commit", "-q", "-m", "b"]);
        let calm = out(&f.repo, &["rev-parse", "HEAD"]);
        let c = checked(&f, &b, &f.main, Some(&clash)).unwrap();
        assert_eq!(
            c.onto,
            Some((false, vec!["a.rs".to_owned()])),
            "reason: conflict_not_told"
        );
        let c = checked(&f, &b, &f.main, Some(&calm)).unwrap();
        assert_eq!(c.onto, Some((true, Vec::new())), "reason: clean_not_told");
        // a base that is not under the head is said so, not refused
        let c = checked(&f, &b, &calm, None).unwrap();
        assert!(!c.base_is_ancestor, "reason: side_base_called_ancestor");
    }

    #[test]
    fn a_bundle_the_repo_cannot_take_or_an_unknown_commit_is_refused() {
        let (f, _head, b) = bundled();
        assert_eq!(checked(&f, &b, &"0".repeat(40), None), Err("base_unknown"));
        assert_eq!(
            checked(&f, &b, &f.main, Some(&"1".repeat(40))),
            Err("onto_unknown")
        );
        assert_eq!(checked(&f, &b, "main", None), Err("verify_sha_shape"));
        // a bundle whose prerequisite only the seat's copy holds
        use crate::receipt_bundle::fixture::g;
        let first = f.commit("c.rs", "fn c() {}\n");
        f.commit("d.rs", "fn d() {}\n");
        let lone = f.data.path().join("BUNDLE-lone.bundle");
        let not_first = format!("^{first}");
        g(
            &f.tree,
            &[
                "bundle",
                "create",
                "-q",
                lone.to_str().unwrap(),
                "HEAD",
                &not_first,
            ],
        );
        assert_eq!(checked(&f, &lone, &f.main, None), Err("bundle_refused"));
        let none = f.data.path().join("BUNDLE-none.bundle");
        assert_eq!(checked(&f, &none, &f.main, None), Err("bundle_not_found"));
    }

    #[test]
    fn a_check_is_named_or_not_taken() {
        for k in [RECEIPT, EVENTS, SEAT, BUNDLE] {
            assert!(is_act(&json!({"type": k})));
        }
        for k in ["verify_sql", "verify_shell", "receipt_read", ""] {
            assert!(!is_act(&json!({"type": k})), "reason: let_in {k}");
        }
    }
}
