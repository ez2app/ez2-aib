//! `/api/v1/seats` (spec 8): create, list and read seats; `/api/v1/seats/open` opens a
//! seat by persona (B4-1b-1, IFACE-b4-1b-seat-open-a9136-1114.md). A seat is a long-lived
//! logical slot, separate from any OS process (spec 4.1); it lives in SQLite,
//! so it is read back after a restart (Phase 1 gate).
//!
//! Writes take an optional `Idempotency-Key` header (spec 8). The same key
//! with the same body on the same route replays the first answer (with
//! `Idempotent-Replayed: true`); with a different body it is
//! `409 idempotency_conflict`. Nothing is written in either case.

use crate::api::AppState;
use crate::personas::{PersonaCard, PersonaSourceError};
use crate::sessions::{json_content_type, new_id, now_rfc3339, ApiError};
use aib_core::state::SeatState;
use aib_core::ErrorCode;
use aib_store::{IdemKey, NewEvent, Seat, SeatCreate, SeatMeta};
use axum::body::Body;
use axum::extract::{Path as UrlPath, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;

/// Request bodies for seats are small.
pub const MAX_BODY: usize = 4 * 1024;
/// Longest seat name, in characters (the column checks the same bound).
pub const MAX_NAME: usize = 128;
/// Longest `Idempotency-Key`.
pub const MAX_KEY: usize = 128;
pub const IDEMPOTENCY_HEADER: &str = "idempotency-key";
pub const REPLAYED_HEADER: &str = "idempotent-replayed";
const CREATE_SCOPE: &str = "POST /api/v1/seats";
const OPEN_SCOPE: &str = "POST /api/v1/seats/open";
/// Longest cwd, in bytes.
pub const MAX_CWD: usize = 4096;
/// Longest cabin / axis id.
pub const MAX_TAG: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCreate {
    name: String,
}

fn bad(msg: &str) -> ApiError {
    ApiError::new(ErrorCode::InvalidRequest, msg)
}

/// The seat name: 1–128 characters, no control characters, not only spaces.
fn valid_name(n: &str) -> bool {
    let len = n.chars().count();
    (1..=MAX_NAME).contains(&len) && !n.chars().any(char::is_control) && !n.trim().is_empty()
}

/// `Idempotency-Key`: absent, or 1–128 of `A-Z a-z 0-9 - _ . :`. A key that
/// is present but unreadable is an error, never "no key" (a silent fallback
/// would turn a retry into a second seat).
pub(crate) fn idempotency_key(h: &HeaderMap) -> Result<Option<String>, ApiError> {
    let mut vals = h.get_all(IDEMPOTENCY_HEADER).iter();
    let Some(v) = vals.next() else {
        return Ok(None);
    };
    if vals.next().is_some() {
        return Err(bad("Idempotency-Key given more than once"));
    }
    let s = v
        .to_str()
        .map_err(|_| bad("Idempotency-Key must be printable ASCII"))?;
    let ok = (1..=MAX_KEY).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b':'));
    if ok {
        Ok(Some(s.to_string()))
    } else {
        Err(bad("Idempotency-Key must be 1-128 of A-Z a-z 0-9 - _ . :"))
    }
}

/// The API form of a seat with nothing else known about it (a seat made with
/// `POST /seats`, or the answer stored with an idempotency key).
pub fn view(s: &Seat) -> serde_json::Value {
    view_with(s, None, None, None)
}

/// The seat's short code: its id without `seat_`, first 5 characters (the 1.x seat code
/// is 5 characters too).
/// Whether `s` is a seat code: five lower hex digits. All digits is a code too (34396,
/// 87167 are live seats; 519d0 06:17); a file name's digits are read by
/// [`crate::import::seat_of`], which keeps asking for a letter (dates and pids there).
pub fn is_code(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn seat_code(seat_id: &str) -> String {
    seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect()
}

/// What a seat row shows (Andy 11-14 10:44): `<persona> · <provider> · <mode>`, and
/// ` · <seat code>` from the second seat of the same persona on (`dup`). A seat that was
/// not opened by persona shows its name. `name` is the persona's name now, else the
/// seat's name (the persona's name when it opened).
pub fn display(s: &Seat, m: Option<&SeatMeta>, name: Option<&str>, dup: bool) -> String {
    let Some(m) = m.filter(|m| m.persona_uid.is_some()) else {
        return s.name.clone();
    };
    let mut d = format!(
        "{} · {} · {}",
        name.unwrap_or(&s.name),
        m.provider,
        m.mode.as_deref().unwrap_or("?")
    );
    if dup {
        d.push_str(" · ");
        d.push_str(&seat_code(&s.seat_id));
    }
    d
}

/// The API form of a seat: the stored fields, then (only added, B4-1b-1) what it was
/// opened as. `card` is its persona card now (`None`: not opened by persona, the card is
/// gone, or the cards cannot be read; the name is then `null`, never an error);
/// `dup` = an older seat has the same persona (`None` = not worked out: the answer to
/// an open, whose `display` has no suffix; the list has it). cwd and the resume id never
/// appear.
pub fn view_with(
    s: &Seat,
    m: Option<&SeatMeta>,
    card: Option<&PersonaCard>,
    dup: Option<bool>,
) -> serde_json::Value {
    let persona = m.and_then(|m| m.persona_uid.as_ref()).map(|uid| {
        serde_json::json!({
            "uid": uid,
            "name": card.map(|c| &c.name),
            "persona_sha": card.map(|c| &c.persona_sha),
        })
    });
    serde_json::json!({
        "seat_id": s.seat_id,
        "name": s.name,
        "state": s.state,
        "revision": s.revision,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "persona": persona,
        "provider": m.map(|m| &m.provider),
        "mode": m.and_then(|m| m.mode.as_ref()),
        "model": m.and_then(|m| m.model.as_ref()),
        "model_pinned": m.map(|m| m.model_pinned),
        "cabin": m.and_then(|m| m.cabin.as_ref()),
        "axis_id": m.and_then(|m| m.axis_id.as_ref()),
        "display": display(s, m, card.map(|c| c.name.as_str()), dup.unwrap_or(false)),
        // filled by the list (`read_all`) for a seat waiting for a slot; null here, so a
        // created seat reads back the same (seats.rs:148 was red on the full head)
        "queue": serde_json::Value::Null,
        // filled by the list for a seat in its copy of a registered repo; null here, so a
        // created seat reads back the same (igt1-de1b725cc: seats.rs:148 red, the read had
        // repo_copy and the create answer did not)
        "repo_copy": serde_json::Value::Null,
    })
}

/// Every seat as the API shows it, oldest first: its open fields, its persona's name
/// now, and the seat-code suffix from the second seat of a persona on. The cards being
/// unreadable leaves names `null` and the list still answers.
pub fn views(seats: &[Seat], metas: &[SeatMeta], cards: &[PersonaCard]) -> Vec<serde_json::Value> {
    let meta = |id: &str| metas.iter().find(|m| m.seat_id == id);
    let mut seen: std::collections::HashSet<&str> = std::collections::HashSet::new();
    seats
        .iter()
        .map(|s| {
            let m = meta(&s.seat_id);
            let uid = m.and_then(|m| m.persona_uid.as_deref());
            let card = uid.and_then(|u| cards.iter().find(|c| c.uid == u));
            // S89-state: an archived seat neither gets nor causes a seat-code suffix
            let uid = uid.filter(|_| s.state != aib_store::ARCHIVED);
            let dup = uid.is_some_and(|u| !seen.insert(u));
            let mut v = view_with(s, m, card, Some(dup));
            if let (Some(m), Some(o)) = (m, v.as_object_mut()) {
                let read = |p: &std::path::Path| std::fs::read_to_string(p).ok();
                let owner = folder_owner(&m.cwd, &s.seat_id, &|id| meta(id).is_some(), &read);
                o.insert(
                    "folder_owner".into(),
                    owner.unwrap_or(serde_json::Value::Null),
                );
            }
            v
        })
        .collect()
}

/// Whose folder a seat works in when it is not its own (043dd 11-20: the page showed 「借 @X
/// 的」 by reading `seat.opened` events): `{"seat": <code>, "how": "folder"}` for a seat sharing
/// `<seat-work>/<X>` (with_seat), `{"seat": <code>, "how": "tree"}` for a seat in its own tree
/// off X's folder (own_tree: the tree's `.git` file names X's repository). `None`: its own
/// folder, a registered repo's tree, or a folder no seat owns. `is_seat` says whether an id is
/// a seat here; `read` reads a file.
pub fn folder_owner(
    cwd: &str,
    me: &str,
    is_seat: &dyn Fn(&str) -> bool,
    read: &dyn Fn(&std::path::Path) -> Option<String>,
) -> Option<serde_json::Value> {
    let p = std::path::Path::new(cwd);
    let name = p.file_name()?.to_str()?;
    if name.starts_with("seat_") {
        return (name != me && is_seat(name))
            .then(|| serde_json::json!({"seat": seat_code(name), "how": "folder"}));
    }
    // own copy: aibd's base file beside it names the author
    let owner = base_author(p, read)?;
    (owner.starts_with("seat_") && owner != me && is_seat(&owner))
        .then(|| serde_json::json!({"seat": seat_code(&owner), "how": "tree"}))
}

fn state_str(s: SeatState) -> String {
    serde_json::to_value(s)
        .ok()
        .and_then(|v| v.as_str().map(str::to_string))
        .unwrap_or_default()
}

fn internal() -> Response {
    ApiError::new(ErrorCode::Internal, "internal error").into_response()
}

pub async fn create(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    if !json_content_type(&headers) {
        return bad("Content-Type must be application/json").into_response();
    }
    let key = match idempotency_key(&headers) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    let Ok(bytes) = axum::body::to_bytes(body, MAX_BODY).await else {
        return ApiError::new(ErrorCode::PayloadTooLarge, "request body over 4 KiB")
            .into_response();
    };
    let req: RawCreate = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(_) => return bad("body must be {\"name\": string}").into_response(),
    };
    if !valid_name(&req.name) {
        return bad("name must be 1-128 characters, no control characters").into_response();
    }
    let Ok(seat_id) = new_id("seat_") else {
        return internal();
    };
    let Ok(event_id) = new_id("ev_") else {
        return internal();
    };
    let now = now_rfc3339();
    let seat = Seat {
        seat_id: seat_id.clone(),
        name: req.name.clone(),
        state: state_str(SeatState::Created),
        revision: 1,
        created_at: now.clone(),
        updated_at: now.clone(),
    };
    let event = NewEvent {
        event_id,
        aggregate_type: "seat".into(),
        aggregate_id: seat_id,
        sequence: 1,
        event_type: "seat.created".into(),
        correlation_id: None,
        occurred_at: now,
        payload: serde_json::json!({"name": req.name, "state": seat.state}),
    };
    // the canonical request: what the key is bound to (field order and
    // whitespace of the original body do not matter)
    let idem = key.map(|k| IdemKey {
        scope: CREATE_SCOPE.into(),
        key: k,
        request: serde_json::json!({"name": req.name}).to_string(),
    });
    let response = view(&seat);
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || {
        store.create_seat(seat, event, idem, StatusCode::CREATED.as_u16(), response)
    })
    .await;
    match res {
        Ok(Ok(SeatCreate::Created(s))) => (StatusCode::CREATED, Json(view(&s))).into_response(),
        Ok(Ok(SeatCreate::Replayed { status, response })) => {
            let code = StatusCode::from_u16(status).unwrap_or(StatusCode::OK);
            let mut r = (code, Json(response)).into_response();
            r.headers_mut()
                .insert(REPLAYED_HEADER, HeaderValue::from_static("true"));
            r
        }
        Ok(Ok(SeatCreate::KeyConflict)) => ApiError::new(
            ErrorCode::IdempotencyConflict,
            "this Idempotency-Key was used for a different request",
        )
        .into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat create failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// Every seat, its open fields and every card (names are read when shown; cards that
/// cannot be read give no names, not an error).
pub(crate) fn read_all(st: &AppState) -> aib_store::Result<Vec<serde_json::Value>> {
    let seats = st.store.seats()?;
    let metas = st.store.seat_metas()?;
    let cards = st.personas.cards().unwrap_or_else(|e| {
        tracing::warn!(error = %e, "persona cards unreadable; seat names shown without them");
        Vec::new()
    });
    let mut out = views(&seats, &metas, &cards);
    // 519d0 自測 r3 M3: a seat waiting for a session slot says where it stands and the limit
    let queued = st.sessions.queued_seats();
    let limit = st.sessions.max_running();
    for v in &mut out {
        let id = v["seat_id"].as_str().unwrap_or_default();
        v["queue"] = queued.iter().find(|(s, _)| s == id).map_or(
            serde_json::Value::Null,
            |(_, p)| serde_json::json!({"position": p, "limit": limit}),
        );
    }
    // N-webonly-c6-merge (IFACE-web-merge r1 §1): a seat in its copy of a registered repo
    let live = st.sessions.live_seats();
    for v in &mut out {
        let id = v["seat_id"].as_str().unwrap_or_default();
        let copy = metas
            .iter()
            .find(|m| m.seat_id == id)
            .map_or(serde_json::Value::Null, |m| {
                crate::send_back::repo_copy_of(&m.cwd, live.iter().any(|s| s == id))
            });
        v["repo_copy"] = copy;
    }
    // second slice: what each such seat sent back and the step it stands at (sent_trail)
    let private = st.sessions.private_dir().to_path_buf();
    let data = private.parent().unwrap_or(&private).to_path_buf();
    for v in &mut out {
        if v["repo_copy"].is_object() {
            let id = v["seat_id"].as_str().unwrap_or_default().to_owned();
            let (sent, more) = crate::sent_trail::of_seat(&data, &id);
            v["repo_copy"]["sent"] = sent.into();
            v["repo_copy"]["sent_more"] = more.into();
        }
    }
    // 043dd 11-20 round 2: a seat in its own tree says its branch and what is not merged
    let hooks = seat_git_hooks(st.sessions.private_dir());
    for v in &mut out {
        if v["folder_owner"]["how"] != "tree" {
            continue;
        }
        let id = v["seat_id"].as_str().unwrap_or_default();
        let Some(m) = metas.iter().find(|m| m.seat_id == id) else {
            continue;
        };
        let state = crate::seat_worktree::tree_state(std::path::Path::new(&m.cwd), &hooks).ok();
        v["tree"] = state.map_or(serde_json::Value::Null, |t| {
            // `unmerged: null, new: true`: commits the author's repository has not taken yet
            // (the list fetches nothing; 合回 counts them)
            serde_json::json!({"branch": t.branch, "base": t.base,
                               "unmerged": t.unmerged, "new": t.unmerged.is_none(),
                               "dirty": t.dirty, "head": t.head})
        });
    }
    Ok(out)
}

/// The tools a seat may call (S89-talk-1): aibd's own MCP server, these and no other — the
/// one-to-one ones and the threads of many it is a member of (0eed7 overnight 2943d806 M2: a
/// safe seat was refused thread_send, "haven't granted", so a called review seat could not
/// answer).
/// thread_open too (519d0 常駐實跑 M 11-18: told to open a thread, a seat had no tool for it).
/// plan_note and seat_now (ORDER-webonly-today 6, 7b81e 11-20): a seat notes the progress tree and
/// sets its own status line through aibd, as itself (both routes already took the seat token).
/// seat_tree (0eed7 RETRACT-T4 M1, 11-20): the reviewer reads the author's tree through aibd's
/// tree read (aibd lets only the reviewer on a shared node in).
pub const TALK_TOOLS: [&str; 13] = [
    "mcp__aibd__seat_list",
    "mcp__aibd__seat_send",
    "mcp__aibd__seat_inbox",
    "mcp__aibd__seat_done",
    "mcp__aibd__thread_list",
    "mcp__aibd__thread_view",
    "mcp__aibd__thread_open",
    "mcp__aibd__thread_send",
    "mcp__aibd__ask_person",
    "mcp__aibd__plan_note",
    "mcp__aibd__seat_now",
    "mcp__aibd__seat_tree",
    // 1a93b GAP §6: the seat says its handoff note is written (its own line only, like seat_now)
    "mcp__aibd__seat_handoff",
];
/// PREREAD-seat-pending-read: a web seat reads the pending folder through aibd, read-only. A
/// work / dgrs seat's, never a safe seat's (5d33a REVIEW-seatpendingread L1: a safe seat reads
/// nothing today; aibd refuses one too, [`crate::seat_pending::mode_may_read`]).
pub const PENDING_TOOLS: [&str; 6] = [
    "mcp__aibd__pending_list",
    "mcp__aibd__pending_read",
    "mcp__aibd__pending_write",
    "mcp__aibd__dm_waiting",
    "mcp__aibd__dm_show",
    "mcp__aibd__dm_done",
];
/// seat_receipt (N-webonly 第 4 件, 7ebca 11-20): a seat asks aibd for a receipt of its own head
/// and reads its own runs. Only for a seat that writes code ([`receipt_tool_for`]; 5d33a L3: a
/// safe seat has no commit of its own, the tool would only bring the owner's window up); aibd
/// refuses a safe seat's request as well (receipt_run::mode_may_ask).
pub const RECEIPT_TOOL: &str = "mcp__aibd__seat_receipt";

/// Whether a claude seat of `mode` is allowed [`RECEIPT_TOOL`].
pub fn receipt_tool_for(mode: &str) -> bool {
    crate::receipt_run::mode_may_ask(Some(mode))
}

/// S89-talk-1 (DECISION-council-seat-talk §四席一致): the argv a claude seat gets for talking
/// to other seats: one MCP server, `<aibd> mcp --data-dir <this aibd's data dir>` (stdio; it
/// reads `AIB_SEAT_TOKEN`), and `--allowedTools` naming its two tools only. No shell, no other
/// server. The data dir is an argument, not env: a session never gets an `AIBD_*` name
/// (platform::session::child_env), and without it `aibd mcp` would reach another aibd
/// (0eed7 VERIFY-talk1 M1).
pub fn talk_config(aibd_exe: &str, data_dir: &str) -> String {
    serde_json::json!({
        "mcpServers": { "aibd": {
            "type": "stdio", "command": aibd_exe, "args": ["mcp", "--data-dir", data_dir]
        } }
    })
    .to_string()
}

/// Where aibd keeps [`talk_config`] for one start (named by the session uuid, so two seats
/// opening at once never write the same file).
pub fn talk_config_path(private_dir: &std::path::Path, start: &str) -> std::path::PathBuf {
    private_dir.join(format!("mcp-{start}.json"))
}

/// N-webonly-c19 (7b81e 11-20, ORDER-webonly-today 11): the seat's status-line gate, as 1.x
/// `aib-now-gate.py stop` / `session-start`: a settings file whose Stop and SessionStart hooks
/// run `<aibd> ctl now-gate --data-dir <data>` (now_gate.rs: blocks a turn that used tools and
/// did not write its line; prints the seat's own line when it opens or is cleared). The hook
/// inherits the seat's env, so the seat token reaches it; nothing else is in the file.
pub fn gate_settings(aibd_exe: &str, data_dir: &str) -> String {
    let q = |v: &str| {
        if cfg!(windows) {
            format!("\"{v}\"")
        } else {
            format!("'{}'", v.replace('\'', "'\\''"))
        }
    };
    let cmd = format!("{} ctl now-gate --data-dir {}", q(aibd_exe), q(data_dir));
    let hook = serde_json::json!([{ "hooks": [{ "type": "command", "command": cmd }] }]);
    serde_json::json!({ "hooks": { "Stop": hook, "SessionStart": hook } }).to_string()
}

/// 5d33a PREREAD 114ee0ac1 L1: a work seat's gate rides in its own sandbox `--settings` (the
/// JSON aib-seatopen put in argv), not in a second file that could take that one's place. The
/// hooks of [`gate_settings`] are added to the one `--settings` value that holds `sandbox`;
/// the sandbox keys are untouched. `false` when there is none (the seat opens without a gate,
/// never without its sandbox).
pub fn fold_gate_into_work_settings(argv: &mut [String], gate_json: &str) -> bool {
    let Ok(gate) = serde_json::from_str::<serde_json::Value>(gate_json) else {
        return false;
    };
    let Some(i) = argv.windows(2).position(|w| {
        w[0] == "--settings"
            && serde_json::from_str::<serde_json::Value>(&w[1])
                .is_ok_and(|v| v.get("sandbox").is_some())
    }) else {
        return false;
    };
    let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&argv[i + 1]) else {
        return false;
    };
    v["hooks"] = gate["hooks"].clone();
    argv[i + 1] = v.to_string();
    true
}

/// Where [`gate_settings`] is kept: next to the talk config, `gate-<start>.json`.
pub fn gate_settings_path(config_path: &std::path::Path) -> std::path::PathBuf {
    let name = config_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("mcp-seat.json");
    let start = name.strip_prefix("mcp-").unwrap_or(name);
    config_path.with_file_name(format!("gate-{start}"))
}

/// DECISION-council-seat-talk r0 補收 (§S89-talk-1): the config is a file aibd writes, owner
/// only (0600 / the owner's ACL on Windows), holding nothing but aibd itself; the argv names
/// the file, and `--allowedTools` the two tools. The seat token is never in either.
/// c19: the gate settings ([`gate_settings`]) are written beside it the same way.
pub fn keep_talk_config(
    private_dir: &std::path::Path,
    start: &str,
    aibd_exe: &str,
    data_dir: &str,
) -> std::io::Result<std::path::PathBuf> {
    let p = talk_config_path(private_dir, start);
    crate::platform::write_private_file(&p, talk_config(aibd_exe, data_dir).as_bytes())?;
    crate::platform::write_private_file(
        &gate_settings_path(&p),
        gate_settings(aibd_exe, data_dir).as_bytes(),
    )?;
    Ok(p)
}

/// The argv for [`talk_config`] kept at `config_path`. A `work` seat (W1) also gets its fixed
/// list ([`aib_seatopen::WORK_ALLOWED`]) in the same, single `--allowedTools`.
/// c19: the status-line gate is on only with `AIBD_SEAT_NOW_GATE=1` (a new behaviour is off
/// until the owner turns it on).
pub const NOW_GATE_ENV: &str = "AIBD_SEAT_NOW_GATE";

pub fn now_gate_on() -> bool {
    std::env::var(NOW_GATE_ENV).is_ok_and(|v| v == "1")
}

pub fn talk_args(config_path: &str, mode: &str) -> Vec<String> {
    talk_args_with(config_path, mode, now_gate_on())
}

/// [`talk_args`] with the gate switch given (tests; `gate` = [`now_gate_on`]).
pub fn talk_args_with(config_path: &str, mode: &str, gate: bool) -> Vec<String> {
    let mut allowed: Vec<&str> = TALK_TOOLS.to_vec();
    if crate::seat_pending::mode_may_read(Some(mode)) {
        allowed.extend(PENDING_TOOLS);
    }
    if receipt_tool_for(mode) {
        allowed.push(RECEIPT_TOOL);
    }
    if mode == "work" {
        allowed.extend(aib_seatopen::WORK_ALLOWED);
    }
    let mut a: Vec<String> = vec![
        "--mcp-config".into(),
        config_path.into(),
        "--allowedTools".into(),
        allowed.join(","),
    ];
    // c19: the status-line gate's hooks ride in their own settings file. Not for a work seat:
    // its sandbox comes in its own `--settings` (aib-seatopen pipe.rs), and a second one could
    // take that one's place: its hooks go into that one ([`fold_gate_into_work_settings`]).
    if gate && mode != "work" {
        a.push("--settings".into());
        a.push(
            gate_settings_path(std::path::Path::new(config_path))
                .to_string_lossy()
                .into_owned(),
        );
    }
    a
}

/// N-ws-ask-codex (11-19 深度回顧: only claude seats had the talk tools, so a codex author
/// could not `ask_person`): the same server for a codex seat, as `-c` settings of
/// `codex app-server` (keys read off codex-cli 0.167.0): `command` / `args` as for claude,
/// `env_vars` passes the seat's own token through (codex starts an MCP server with a short
/// default env), `enabled_tools` the same list as [`TALK_TOOLS`] and nothing else, and
/// `default_tools_approval_mode = "approve"` since the seat runs with `approval_policy =
/// "never"` and nobody is there to answer. Values are TOML: a JSON string or array of strings
/// is one, escapes included (a Windows path's backslashes).
pub fn codex_talk_args(aibd_exe: &str, data_dir: &str) -> Vec<String> {
    let q = |v: &str| serde_json::Value::from(v).to_string();
    let tools: Vec<&str> = TALK_TOOLS
        .iter()
        .filter_map(|t| t.strip_prefix("mcp__aibd__"))
        .collect();
    [
        format!("mcp_servers.aibd.command={}", q(aibd_exe)),
        format!(
            "mcp_servers.aibd.args={}",
            serde_json::json!(["mcp", "--data-dir", data_dir])
        ),
        format!(
            "mcp_servers.aibd.env_vars={}",
            serde_json::json!([crate::seat_token::ENV])
        ),
        format!(
            "mcp_servers.aibd.enabled_tools={}",
            serde_json::json!(tools)
        ),
        "mcp_servers.aibd.default_tools_approval_mode=\"approve\"".to_owned(),
    ]
    .into_iter()
    .flat_map(|kv| ["-c".to_owned(), kv])
    .collect()
}

/// S89-talk-1: the rows a seat may see: those whose cabin is the asking seat's (both unset
/// counts as the same, default cabin), the asking seat marked `"me": true`. A seat not in
/// the list sees nothing.
/// 519d0 L2 (VERIFY-talk1-live 04:59): only the seats running now (and itself), each with its
/// seat code, which `seat_send` also takes when it is the only one in the cabin.
pub fn same_cabin(
    rows: Vec<serde_json::Value>,
    me: &str,
    live: &[String],
) -> Vec<serde_json::Value> {
    let Some(cabin) = rows
        .iter()
        .find(|v| v["seat_id"].as_str() == Some(me))
        .map(|v| v["cabin"].clone())
    else {
        return Vec::new();
    };
    rows.into_iter()
        .filter(|v| v["cabin"] == cabin)
        .filter(|v| {
            let id = v["seat_id"].as_str().unwrap_or("");
            id == me || live.iter().any(|l| l == id)
        })
        .map(|mut v| {
            let id = v["seat_id"].as_str().unwrap_or("").to_owned();
            v["me"] = serde_json::json!(id == me);
            v["code"] = serde_json::json!(seat_code(&id));
            v
        })
        .collect()
}

/// `GET /api/v1/seats?all=1` lists archived seats too (S89-state).
#[derive(Debug, Default, Deserialize)]
pub struct ListQuery {
    #[serde(default)]
    all: Option<String>,
}

/// Archived seats are left out unless `all` is `1`.
pub fn shown(v: Vec<serde_json::Value>, all: bool) -> Vec<serde_json::Value> {
    if all {
        return v;
    }
    v.into_iter()
        .filter(|x| x["state"].as_str() != Some(aib_store::ARCHIVED))
        .collect()
}

pub async fn list(
    State(st): State<AppState>,
    caller: Option<axum::Extension<crate::guard::Caller>>,
    axum::extract::Query(q): axum::extract::Query<ListQuery>,
) -> Response {
    let all = q.all.as_deref() == Some("1");
    let caller = caller.map(|axum::Extension(c)| c);
    // M9b (0eed7 VERIFY-autotest 3a2d6b09; 519d0 r16 裁定 5): a page sees on each seat how
    // many of its messages to the person wait to be read, as the contacts list does
    let page = caller.as_ref().and_then(crate::contacts_api::page_key);
    // S89-talk-1: a seat token sees only the seats of its own cabin, itself marked
    let me = match caller {
        Some(crate::guard::Caller::Seat(key)) => match crate::seat_token::tokens().seat(&key) {
            Some(seat) => Some(seat),
            None => {
                return ApiError::new(ErrorCode::Unauthorized, "this seat has stopped")
                    .into_response()
            }
        },
        _ => None,
    };
    let live = st.sessions.live_seats();
    let counted = move || -> aib_store::Result<Vec<serde_json::Value>> {
        let mut v = read_all(&st)?;
        if let Some(page) = page {
            // unreadable ⇒ no counts, the list still shows (as contacts)
            let msgs = st.messages.facts(None).unwrap_or_default();
            with_unread(&mut v, &msgs, &page);
        }
        Ok(v)
    };
    match tokio::task::spawn_blocking(counted).await {
        Ok(Ok(v)) => {
            let v = shown(v, all);
            let v = match me {
                Some(me) => same_cabin(v, &me, &live),
                None => v,
            };
            Json(serde_json::json!({ "seats": v })).into_response()
        }
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat list failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// Each seat's `unread`: its messages to `page` (the person) not read yet; `0` when none.
pub fn with_unread(seats: &mut [serde_json::Value], msgs: &[aib_store::MessageFact], page: &str) {
    for v in seats {
        let Some(id) = v["seat_id"].as_str().map(str::to_owned) else {
            continue;
        };
        let m = crate::contacts::message_counts(msgs, &format!("seat:{id}"), Some(page));
        if let Some(o) = v.as_object_mut() {
            o.insert("unread".into(), serde_json::json!(m.unread));
        }
    }
}

pub async fn get_one(State(st): State<AppState>, UrlPath(id): UrlPath<String>) -> Response {
    // the whole list, so the seat-code suffix is the same as in the list
    let one = move || -> aib_store::Result<Option<serde_json::Value>> {
        Ok(read_all(&st)?
            .into_iter()
            .find(|v| v["seat_id"].as_str() == Some(id.as_str())))
    };
    match tokio::task::spawn_blocking(one).await {
        Ok(Ok(Some(v))) => Json(v).into_response(),
        Ok(Ok(None)) => ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat read failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// `DELETE /api/v1/seats/{id}` (S89-state, IFACE-S89-state-archive-043dd-1118 r1): archive
/// the seat. 409 `seat_busy` while it has a session; 404 for an unknown seat; an archived
/// seat answers as it is. `X-Aib-By: ctl` marks the event as asked by `aibd ctl`.
pub async fn archive(
    State(st): State<AppState>,
    UrlPath(id): UrlPath<String>,
    headers: HeaderMap,
) -> Response {
    let by = match headers.get("x-aib-by").and_then(|v| v.to_str().ok()) {
        Some("ctl") => "ctl",
        _ => "api",
    };
    archive_as(st, id, by).await
}

/// [`archive`] by `by` (`ctl`, `api`, or `page` for the page's `seat_archive` act).
pub async fn archive_as(st: AppState, id: String, by: &'static str) -> Response {
    // N-web-worktree r1 §1.3: the seat's tree of a repo, found before it is archived
    let tree = crate::tree_read::tree_dir(&st, &id).await;
    let sessions = st.sessions.clone();
    let sid = id.clone();
    match tokio::task::spawn_blocking(move || sessions.archive_seat(&sid, by)).await {
        Ok(Ok(aib_store::SeatArchive::NotFound)) => {
            ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response()
        }
        Ok(Ok(_)) => {
            if let Some((dir, base, repo)) = tree {
                collect_tree(&st, &id, dir, base, repo).await;
            }
            get_one(State(st), UrlPath(id)).await
        }
        Ok(Err(e)) => e.into_response(),
        Err(_) => internal(),
    }
}

/// The page's 合回 (043dd 11-20 round 2): merge a seat's own tree back into the folder it came
/// off ([`crate::seat_worktree::merge_back`]). Only a seat in its own tree off another seat's
/// folder (`folder_owner.how == "tree"`), not running now; the merge commit is aibd's. 200
/// `{merged, head, told}`; 409 `seat_busy`, `author_busy` (the author seat in a turn),
/// `nothing_to_merge`, `tree_dirty`, `base_dirty`, or `merge_conflict {files, card_id}` (taken
/// back, nothing changed, a 決定 card opened); 404 / 400 otherwise.
/// Each merge done leaves `seat.merged_back`.
pub async fn merge_back(st: AppState, seat_id: String) -> Response {
    use crate::seat_worktree::MergeBack as M;
    let conflict = |code: &str, msg: &str, extra: serde_json::Value| {
        let mut body = serde_json::json!({"code": code, "message": msg,
                                          "request_id": crate::guard::request_id()});
        if let (Some(b), Some(e)) = (body.as_object_mut(), extra.as_object()) {
            b.extend(e.clone());
        }
        (StatusCode::CONFLICT, Json(body)).into_response()
    };
    if st.sessions.live_seats().contains(&seat_id) {
        return conflict(
            "seat_busy",
            "the seat runs now; stop it first",
            serde_json::json!({}),
        );
    }
    let store = st.store.clone();
    let sid = seat_id.clone();
    let meta = match tokio::task::spawn_blocking(move || store.seat_meta(&sid)).await {
        Ok(Ok(Some(m))) => m,
        Ok(Ok(None)) => {
            return ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response()
        }
        _ => return internal(),
    };
    let read = |p: &std::path::Path| std::fs::read_to_string(p).ok();
    let in_tree =
        folder_owner(&meta.cwd, &seat_id, &|_| true, &read).is_some_and(|o| o["how"] == "tree");
    if !in_tree {
        return Refusal(
            "seat_id",
            "合回 is for a seat in its own tree off another seat's folder",
        )
        .into_response();
    }
    // 519d0 11-20 後審: the author seat in a turn reads and writes its folder as it goes, so
    // a merge then is refused; open but idle it is fine (told in the thread below)
    let author = base_author(std::path::Path::new(&meta.cwd), &read).unwrap_or_default();
    // 5d33a N6: a registered repo's own checkout is never written by aibd for a seat
    if crate::repos::is_repo_copy(&author) {
        return Refusal(
            "seat_id",
            "a copy of a registered repo is not merged into the repo's own checkout",
        )
        .into_response();
    }
    if !author.is_empty() && st.sessions.in_turn(&author) {
        return conflict(
            "author_busy",
            "the seat whose folder it merges into is in a turn; merge back when it is done",
            serde_json::json!({"author": seat_code(&author)}),
        );
    }
    let hooks = seat_git_hooks(st.sessions.private_dir());
    let tree = std::path::PathBuf::from(&meta.cwd);
    let done = tokio::task::spawn_blocking(move || {
        crate::seat_worktree::merge_back(&tree, &hooks, "aibd 合回", "aibd@aibd.invalid")
    })
    .await;
    match done {
        Ok(Ok(M::Merged { n, head })) => {
            side_event(
                &st,
                "worktree",
                "seat.merged_back",
                serde_json::json!({"seat": seat_code(&seat_id), "merged": n, "head": head}),
            )
            .await;
            let told = tell_merged_back(&st, &seat_id, &author, n, &head).await;
            Json(serde_json::json!({"merged": n, "head": head, "told": told})).into_response()
        }
        Ok(Ok(M::Nothing)) => conflict(
            "nothing_to_merge",
            "the seat's branch has nothing new",
            serde_json::json!({}),
        ),
        Ok(Ok(M::BaseDirty)) => conflict(
            "base_dirty",
            "the folder it merges into has files not committed",
            serde_json::json!({}),
        ),
        // 5d33a REVIEW-gitinseatrepo M1: its config names something aibd will not run git with
        Ok(Ok(M::BaseUnsafe(why))) => {
            tracing::warn!(why = %why, "merge back refused: base repository config");
            // a 壞了 card: someone has to look at the author's repository settings
            let card = side_event(
                &st,
                "worktree",
                "seat.merge_unsafe",
                serde_json::json!({"seat_id": seat_id, "why": why}),
            )
            .await;
            conflict(
                "base_unsafe",
                "the folder it merges into has git settings aibd does not run git with",
                serde_json::json!({"why": why, "card_id": card}),
            )
        }
        // N-webonly-c23 (5d33a H1): a provider would load what the seat committed there
        Ok(Ok(M::SettingsTouched(files))) => {
            let card = side_event(
                &st,
                "worktree",
                "seat.merge_settings",
                serde_json::json!({"seat_id": seat_id, "files": files}),
            )
            .await;
            conflict(
                "settings_touched",
                "the seat's commits change files an AI loads as its settings (.codex, .claude, CLAUDE.md, AGENTS.md …) or that run on push (.github/workflows, .github/actions); not merged — the person merges these by hand after reading them",
                serde_json::json!({"files": files, "card_id": card}),
            )
        }
        // L1: the merge stopped and could not be taken back: a 決定 card, the base is mid-merge
        Ok(Ok(M::AbortFailed(files))) => {
            let card = side_event(
                &st,
                "worktree",
                "seat.merge_conflict",
                serde_json::json!({"seat_id": seat_id, "files": files, "stuck": true}),
            )
            .await;
            conflict(
                "merge_left_half",
                "the merge stopped and could not be taken back; the folder is mid-merge",
                serde_json::json!({"files": files, "card_id": card}),
            )
        }
        Ok(Ok(M::Conflict(files))) => {
            // a 決定 card (slot 1) whose id is the event's: who resolves it is the person's call
            let card = side_event(
                &st,
                "worktree",
                "seat.merge_conflict",
                serde_json::json!({"seat_id": seat_id, "files": files}),
            )
            .await;
            conflict(
                "merge_conflict",
                "the merge stopped on these files and was taken back; nothing changed",
                serde_json::json!({"files": files, "card_id": card}),
            )
        }
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "merge back failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// The author named in aibd's base file beside a seat's own copy at `tree`
/// ([`crate::seat_worktree::BASE_FILE`]).
fn base_author(
    tree: &std::path::Path,
    read: &dyn Fn(&std::path::Path) -> Option<String>,
) -> Option<String> {
    let raw = read(&tree.parent()?.join(crate::seat_worktree::BASE_FILE))?;
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    v["author"].as_str().map(str::to_owned)
}

/// After a 合回: a system post 「@<seat> 的分支已合回，新頭 …」 in the newest thread both the
/// merged seat and the author are in, so the author knows its folder moved under it (519d0
/// 11-20). The thread id, or `None` when they share none.
async fn tell_merged_back(
    st: &AppState,
    seat_id: &str,
    author: &str,
    n: usize,
    head: &str,
) -> Option<String> {
    if author.is_empty() {
        return None;
    }
    let db = st.messages.clone();
    let (me, them) = (format!("seat:{seat_id}"), format!("seat:{author}"));
    let found = tokio::task::spawn_blocking(move || {
        let ts = crate::threads::threads_of(&db, Some(&me), None, 20).ok()?;
        let t = ts.into_iter().find(|t| {
            matches!(
                crate::threads::is_member_now(&db, &t.thread_id, &them),
                Ok(true)
            )
        })?;
        let last = db
            .posts_page(&t.thread_id, None, 1)
            .ok()
            .and_then(|(p, _)| p.last().map(|m| m.message_id.clone()))
            .unwrap_or_default();
        Some((t.thread_id, last))
    })
    .await
    .ok()
    .flatten()?;
    let short = head.get(..9).unwrap_or(head);
    let text = format!(
        "@{} 的分支已合回({n} 個提交),新頭 {short}",
        seat_code(seat_id)
    );
    crate::threads::system_post(st, &found.0, &found.1, &text, "merged_back", &[])
        .await
        .then_some(found.0)
}

/// The archived seat's tree (no session runs in it any more): taken away when nothing in it is
/// uncommitted, else left for the person; the branch and its commits always stay. Either way
/// an event says which, with the branch and how many commits are not merged (the page's card
/// "N commits not merged"). A failure leaves the tree and says why.
async fn collect_tree(
    st: &AppState,
    seat: &str,
    dir: std::path::PathBuf,
    base: String,
    repo: Option<std::path::PathBuf>,
) {
    let hooks = seat_git_hooks(st.sessions.private_dir());
    let done = tokio::task::spawn_blocking(move || {
        crate::repos::collect_copy(&dir, &base, repo.as_deref(), &hooks)
    })
    .await;
    let code = seat_code(seat);
    let (kind, payload) = match done {
        Ok(Ok(c)) if c.removed => (
            "seat.worktree_removed",
            serde_json::json!({"seat": code, "branch": c.branch, "unmerged": c.unmerged}),
        ),
        Ok(Ok(c)) => (
            "seat.worktree_kept",
            serde_json::json!({"seat": code, "branch": c.branch, "unmerged": c.unmerged,
                               "uncommitted": c.uncommitted, "copy": c.uncommitted.is_none()}),
        ),
        Ok(Err(why)) => (
            "seat.worktree_kept",
            serde_json::json!({"seat": code, "error": why.chars().take(200).collect::<String>()}),
        ),
        Err(_) => (
            "seat.worktree_kept",
            serde_json::json!({"seat": code, "error": "internal"}),
        ),
    };
    // 5d33a REVIEW-gitinseatrepo N7: refused because a repository's settings are not plain
    // (repo_unsafe: repo_safe, or the copy's .git naming no readable head): nothing was collected and nothing will be until someone fixes
    // those settings, so a 壞了 card says so (like 合回's seat.merge_unsafe)
    let unsafe_why = payload
        .get("error")
        .and_then(serde_json::Value::as_str)
        .filter(|w| collect_refused_unsafe(w))
        .map(str::to_owned);
    side_event(st, "worktree", kind, payload).await;
    if let Some(why) = unsafe_why {
        side_event(
            st,
            "worktree",
            "seat.collect_unsafe",
            serde_json::json!({"seat_id": seat, "why": why}),
        )
        .await;
    }
}

/// A collect error that is a refusal over a repository's settings (`repo_unsafe:` from
/// repo_safe or from a copy whose .git names no readable head), not a git failure.
fn collect_refused_unsafe(why: &str) -> bool {
    why.starts_with("repo_unsafe:")
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawOpen {
    persona_uid: String,
    provider: String,
    mode: String,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    cabin: Option<String>,
    #[serde(default)]
    axis_id: Option<String>,
    /// The seat's working folder. Left out: a dedicated empty folder in aibd's own data
    /// directory, `<data>/seat-work/<seat id>/` (S89-live-workdir, 0eed7 autotest 0c976d68: a seat
    /// opened without a folder must not work wherever the CLI happened to be). A given folder
    /// with project settings a provider would load is refused.
    #[serde(default)]
    cwd: Option<String>,
    /// Only `false`: a new cabin is not opened by 2.0 yet (the IFACE §2 example has it).
    #[serde(default)]
    new: bool,
    #[serde(default)]
    dry_run: bool,
    /// N-ws-open-bind: the route key of the second brain the page picked, looked up by aibd
    /// from the brain's uid; never read from a body (`skip`: a body naming it is refused).
    #[serde(skip)]
    brain_route: Option<String>,
    /// N-web-worktree: the cwd is the seat's own tree aibd just opened after the settings were
    /// compared with the registered ones ([`crate::repos::prepare_tree`]); never from a body.
    #[serde(skip)]
    tree: bool,
    /// A page's `with_seat` (FINDING-andy-realrun 1): the running work seat whose nodes the new
    /// seat is put on ([`crate::assign::join_nodes`]); never read from a body.
    #[serde(skip)]
    join_nodes_of: Option<String>,
    /// The role it joins those nodes with ([`crate::assign::with_seat_role`]); never from a body.
    #[serde(skip)]
    join_role: &'static str,
    /// A page's handoff and memory pack, read from the pending folder before the open
    /// ([`crate::seat_carry::gather`]): copied into `<cwd>/.aib/` once the folder is known,
    /// before the seat starts (PREREAD-seat-carry-a9136-1121); never from a body.
    #[serde(skip)]
    carry: Vec<(String, Vec<u8>)>,
    /// `aibd ctl seat open … --handoff F [--mempack F]`: the same two file names a page gives
    /// (PREREAD-seat-carry-a9136-1121), read into `carry` before the open.
    #[serde(default)]
    handoff: Option<String>,
    #[serde(default)]
    mempack: Option<String>,
}

/// A refused open: `invalid_request` with a stable `reason` a screen picks its words by
/// (043dd B4-1b-1 M1; IFACE r5): `body`, `key_missing`, `provider`, `mode`,
/// `persona_shape`, `persona_missing`, `model`, `cabin`, `axis_id`, `cwd`, `new`.
#[derive(Debug)]
struct Refusal(&'static str, &'static str);

impl Refusal {
    fn into_response(self) -> Response {
        let body = serde_json::json!({
            "code": ErrorCode::InvalidRequest.as_str(),
            "message": self.1,
            "request_id": crate::guard::request_id(),
            "reason": self.0,
        });
        (StatusCode::BAD_REQUEST, Json(body)).into_response()
    }
}

/// S89-live-5: whether aibd can start this provider over pipes, asked of
/// [`aib_seatopen::pipe_argv`] itself so the two never disagree.
fn pipe_supported(provider: &str) -> bool {
    !matches!(
        aib_seatopen::pipe_argv(
            provider,
            "safe",
            None,
            "00000000-0000-4000-8000-000000000000"
        ),
        Err(aib_seatopen::PipeRefused::Provider(_))
    )
}

/// `a-z 0-9 -`, 1–64, not starting with `-` (a cabin name, an axis id).
fn tag_ok(s: &str) -> bool {
    (1..=MAX_TAG).contains(&s.len())
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// The shape of a seat's cwd (519d0 IFACE r5): an absolute path for this platform, at
/// most `MAX_CWD` bytes, no control characters. On Windows that is `C:\…` or a UNC path
/// `\\server\share\…`; `C:x` (relative to that drive's folder) and `\x` (the current
/// drive's root) are not. Whether it is an existing folder is checked after.
fn cwd_shape(s: &str) -> bool {
    s.len() <= MAX_CWD && !s.chars().any(char::is_control) && std::path::Path::new(s).is_absolute()
}

/// The fields of an open request, checked in the order the seat-open words are
/// (aib-seatopen: AI, mode, persona, model, cabin), then axis, cwd and `new`. The
/// persona is only its uid here: names are resolved by the CLI (7b81e 11-14 §2-2).
/// 5d33a CHECK-preview-seat-home (甲) 3: a preview opens no dgrs seat; its seats use the
/// real HOME, where the person's real ledgers are. A `work` seat is let in (519d0 11-19 12:02,
/// Andy's 8-hour goal: AI seats developing together inside the preview): its file tools stay
/// in its own folder (--restricted) and its Bash is a fixed list; not a defense against a
/// same-user AI (a test it writes and runs can read HOME), see DESIGN-seat-work-tier-a9136-1119.
pub fn preview_refuses(mode: &str, in_preview: bool) -> bool {
    in_preview && mode != "safe" && mode != "work"
}

fn check_open(r: &RawOpen) -> Result<(), Refusal> {
    if preview_refuses(&r.mode, crate::preview::is_preview()) {
        return Err(Refusal(
            "preview_no_dgrs",
            "a preview opens safe and work seats only (its seats log in with this machine's real HOME)",
        ));
    }
    if !aib_seatopen::PROVIDERS.contains(&r.provider.as_str()) {
        return Err(Refusal("provider", "provider is not one aib knows"));
    }
    // S89-live-5 (0eed7 J2, 519d0 04:02): a provider aib knows but cannot yet start over
    // pipes (grok, codex) is refused before anything is written, dry run included: the seat
    // it would leave has no session and never gets one
    if !pipe_supported(&r.provider) {
        return Err(Refusal(
            "provider_unsupported",
            "this provider cannot be opened as a 2.0 seat yet; no seat was made",
        ));
    }
    if !aib_seatopen::SEAT_MODES.contains(&r.mode.as_str()) {
        return Err(Refusal("mode", "mode must be dgrs, safe or work"));
    }
    // S89-live-2 (5d33a wave 2 B5): a provider that starts, but not in this mode (codex is
    // safe only), is refused here too, before anything is written; the page only greys it
    if !aib_seatopen::pipe_modes(&r.provider).contains(&r.mode.as_str()) {
        return Err(Refusal(
            "provider_mode_unsupported",
            "this provider cannot be opened in this mode yet; no seat was made",
        ));
    }
    // N-ws-codex-work (COUNCIL-worktree-gitdir-7ebca-1120 Q3): a codex work seat writes its
    // own folder and its `.git` only. Another seat's folder or a tree off it (with_seat), a
    // registered repo's tree or a named folder would share a `.git` with another seat or the
    // person, so they wait for how a tree's git is kept apart (Q2)
    if r.provider == "codex" && r.mode == "work" && r.cwd.is_some() {
        return Err(Refusal(
            "codex_work_own_folder",
            "a codex work seat opens in its own folder only for now (no with_seat, repo or folder); no seat was made",
        ));
    }
    if !crate::personas::uid_shape(&r.persona_uid) {
        return Err(Refusal(
            "persona_shape",
            "persona_uid must be a persona card's 6-character uid",
        ));
    }
    if r.model
        .as_deref()
        .is_some_and(|m| !aib_seatopen::model_shape(m))
    {
        return Err(Refusal("model", "model has the wrong shape"));
    }
    if r.cabin.as_deref().is_some_and(|c| !tag_ok(c)) {
        return Err(Refusal("cabin", "cabin must be 1-64 of a-z 0-9 -"));
    }
    if r.axis_id.as_deref().is_some_and(|a| !tag_ok(a)) {
        return Err(Refusal("axis_id", "axis_id must be 1-64 of a-z 0-9 -"));
    }
    // the shape only: whether the folder is there is asked after a retry is answered
    // (043dd #759 L1: a folder renamed since the first answer must not turn a replay into a 400)
    if r.cwd.as_deref().is_some_and(|c| !cwd_shape(c)) {
        return Err(cwd_refused());
    }
    if r.new {
        return Err(Refusal("new", "2.0 does not open a new cabin yet"));
    }
    Ok(())
}

/// The account's home folder: `HOME`, else `USERPROFILE` (Windows); absolute only. Inside a
/// preview (`AIBD_PREVIEW=1`) the preview's own HOME is a stand-in folder, so the real one it
/// was handed (`AIBD_SEAT_HOME`) comes first (0eed7 VERIFY-realentry-79c88372 H: the walk for
/// project settings went past the stand-in up to the real home's `.claude` and refused the
/// second work seat).
pub(crate) fn home_dir(lookup: impl Fn(&str) -> Option<String>) -> Option<std::path::PathBuf> {
    let preview = lookup("AIBD_PREVIEW").as_deref() == Some("1");
    let keys: &[&str] = if preview {
        &[
            crate::platform::session::SEAT_HOME_ENV,
            "HOME",
            "USERPROFILE",
        ]
    } else {
        &["HOME", "USERPROFILE"]
    };
    keys.iter()
        .filter_map(|k| lookup(k))
        .map(std::path::PathBuf::from)
        .find(|p| p.is_absolute())
}

/// Where every work seat's folder lives: `<data>/seat-work`, next to the sessions folder.
pub fn seat_work_root(sessions: &std::path::Path) -> std::path::PathBuf {
    crate::folders::folder(sessions.parent().unwrap_or(sessions), "seat-work")
}

/// aibd's empty hooks folder for the git a work seat and aibd run there; its parent is the
/// git HOME aibd gives its own git (seat_worktree).
pub fn seat_git_hooks(sessions: &std::path::Path) -> std::path::PathBuf {
    crate::folders::folder(sessions.parent().unwrap_or(sessions), "seat-git").join("hooks")
}

/// 5d33a M1 on ecf54f91d / CHECK-preview-work-seat 2: a work seat given a folder by path works
/// only under `root` (`<data>/seat-work`), read as its real path: not the root itself, not a
/// link, not outside by `..` or a link inside (so never the home folder, ~/.ez2-aib or a repo's
/// own tree). `Err` = the reason word.
pub fn work_cwd_ok(cwd: &str, root: &std::path::Path) -> Result<(), &'static str> {
    let p = std::path::Path::new(cwd);
    if std::fs::symlink_metadata(p).map_or(true, |m| m.file_type().is_symlink()) {
        return Err("work_cwd_link_or_missing");
    }
    let (Ok(real), Ok(base)) = (p.canonicalize(), root.canonicalize()) else {
        return Err("work_cwd_outside_root");
    };
    if real == base || !real.starts_with(&base) {
        return Err("work_cwd_outside_root");
    }
    Ok(())
}

/// 5d33a CHECK-preview-work-seat 4: the git a work seat runs gets aibd's own git rules (no
/// fsmonitor, no transport, hooks at aibd's empty folder, no prompt, no system config), as
/// env the seat's commands inherit. The folder's own `.git/config` can still set others (N1).
pub fn work_git_env(hooks: &std::path::Path) -> Vec<(String, String)> {
    let kv = [
        ("GIT_CONFIG_NOSYSTEM", "1".to_owned()),
        ("GIT_TERMINAL_PROMPT", "0".to_owned()),
        ("GIT_CONFIG_COUNT", "3".to_owned()),
        ("GIT_CONFIG_KEY_0", "core.fsmonitor".to_owned()),
        ("GIT_CONFIG_VALUE_0", "false".to_owned()),
        ("GIT_CONFIG_KEY_1", "protocol.allow".to_owned()),
        ("GIT_CONFIG_VALUE_1", "never".to_owned()),
        ("GIT_CONFIG_KEY_2", "core.hooksPath".to_owned()),
        ("GIT_CONFIG_VALUE_2", hooks.display().to_string()),
    ];
    kv.into_iter().map(|(k, v)| (k.to_owned(), v)).collect()
}

/// 519d0 VERIFY-selftest 2a3e3cbc L4: on macOS `/usr/bin/git` is an xcrun stub whose cache
/// lives in the account's temp folder under `/var/folders`, which a work seat may not read
/// (`aib_seatopen::WORK_SETTINGS`), so every git printed a cache warning. The seat's PATH puts
/// the developer folder's `usr/bin` first when a git is there (`dev`: `DEVELOPER_DIR`, else
/// the `xcode-select` link), so git runs without the stub. `None`: nothing to change.
pub fn work_path(
    path: &str,
    dev: Option<&std::path::Path>,
    is_file: &dyn Fn(&std::path::Path) -> bool,
) -> Option<String> {
    let bin = dev?.join("usr").join("bin");
    if !is_file(&bin.join("git")) {
        return None;
    }
    let bin = bin.to_str()?;
    if path.split(':').next() == Some(bin) {
        return None;
    }
    Some(if path.is_empty() {
        bin.to_owned()
    } else {
        format!("{bin}:{path}")
    })
}

/// The developer folder `xcrun` would use: `DEVELOPER_DIR`, else what `xcode-select` points at.
fn developer_dir() -> Option<std::path::PathBuf> {
    std::env::var_os("DEVELOPER_DIR")
        .map(std::path::PathBuf::from)
        .or_else(|| std::fs::read_link("/var/db/xcode_select_link").ok())
        .or_else(|| {
            // no link: xcode-select's default, the Command Line Tools (else Xcode's own)
            [
                "/Library/Developer/CommandLineTools",
                "/Applications/Xcode.app/Contents/Developer",
            ]
            .into_iter()
            .map(std::path::PathBuf::from)
            .find(|d| d.is_dir())
        })
}

/// 0eed7 VERIFY-mom-preview-entry r1 / 519d0 21:42 (修法 a): a work seat's folder is a git
/// repository with one empty first commit before the seat starts, made by aibd under the seat's
/// own git env and name (the seat cannot run `git init`, it is not on the allowed list). A
/// folder that already is one (a second seat by `with_seat`, a revive) is left as it is:
/// `Ok(false)`. `Ok(true)` = made now.
pub fn init_work_repo(dir: &std::path::Path, env: &[(String, String)]) -> Result<bool, String> {
    if dir.join(".git").exists() {
        return Ok(false);
    }
    let git = |args: &[&str]| {
        std::process::Command::new("git")
            .args([
                "-c",
                "commit.gpgsign=false",
                "-c",
                "init.defaultBranch=main",
            ])
            .args(args)
            .current_dir(dir)
            .envs(env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
            .stdin(std::process::Stdio::null())
            .output()
            .map_err(|e| format!("git {}: {e}", args[0]))
            .and_then(|o| {
                if o.status.success() {
                    Ok(())
                } else {
                    Err(format!(
                        "git {}: {}",
                        args[0],
                        String::from_utf8_lossy(&o.stderr).trim()
                    ))
                }
            })
    };
    git(&["init", "-q"])?;
    git(&[
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "aib: the work seat's folder",
    ])?;
    Ok(true)
}

/// A seat's own folder when none is given: `<seat work root>/<seat id>` ([`seat_work_root`]:
/// `seat-work`, or `seat-work.noindex` on a new macOS install), next to the sessions folder
/// (`sessions`) aibd keeps private.
pub fn seat_home(sessions: &std::path::Path, seat_id: &str) -> std::path::PathBuf {
    seat_work_root(sessions).join(seat_id)
}

/// Files a provider would load as project instructions or tools from `dir` or any folder above
/// it, the home folder included (0eed7 N1 on 257ac5ab).
pub const PROJECT_SETTINGS: [&str; 4] = [".mcp.json", "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"];

/// A project's `.claude` folder (settings, agents, commands, skills: all loaded at project level,
/// 0eed7 L1 on 257ac5ab) refuses the folder whatever it holds; the home folder's own `.claude` is
/// the account's, left out by --setting-sources project,local.
pub const PROJECT_DIR: &str = ".claude";

/// What claude itself writes into a project's `.claude` while it works there (it loads none of
/// it): a `.claude` holding only these is the seat's own trail, not project settings. Without
/// this a work seat that did any work was refused at its next restart and its folder at the
/// next shared open (0eed7 VERIFY-realentry-f68d3048 M1). Anything else in it, or a `.claude`
/// that cannot be listed, still refuses.
pub const CLAUDE_OWN_TRAIL: [&str; 1] = [".cc-writes"];

/// `dir/.claude` is there and holds something claude would load (see [`CLAUDE_OWN_TRAIL`]).
fn project_dir_loads(d: &std::path::Path) -> bool {
    let p = d.join(PROJECT_DIR);
    if std::fs::symlink_metadata(&p).is_err() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(&p) else {
        return true;
    };
    let mut any = false;
    for e in entries {
        let Ok(e) = e else { return true };
        let name = e.file_name();
        if !CLAUDE_OWN_TRAIL.iter().any(|t| name == *t) {
            return true;
        }
        any = true;
    }
    !any
}

/// The first project file or `.claude` folder found in `dir` or above it (see
/// [`PROJECT_SETTINGS`], [`PROJECT_DIR`]). Both are
/// resolved first: the provider loads by the real path, so a link to a project folder is
/// searched where it leads (5d33a L on 257ac5ab).
pub fn project_settings_in(
    dir: &std::path::Path,
    home: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    let dir = std::fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    let home = home.map(|h| std::fs::canonicalize(h).unwrap_or_else(|_| h.to_path_buf()));
    for d in dir.ancestors() {
        let at_home = home.as_deref().is_some_and(|h| d == h);
        if !at_home && project_dir_loads(d) {
            return Some(d.join(PROJECT_DIR));
        }
        for f in PROJECT_SETTINGS {
            let p = d.join(f);
            if p.exists() {
                return Some(p);
            }
        }
        if at_home {
            return None;
        }
    }
    None
}

fn cwd_refused() -> Refusal {
    Refusal(
        "cwd",
        "cwd must be an existing folder, written as an absolute path",
    )
}

fn unavailable(e: &PersonaSourceError) -> Response {
    tracing::warn!(error = %e, "persona cards unreadable; seat open refused");
    ApiError::new(
        ErrorCode::PersonaSourceUnavailable,
        "the persona cards cannot be read",
    )
    .into_response()
}

fn replayed(status: u16, response: serde_json::Value) -> Response {
    let code = StatusCode::from_u16(status).unwrap_or(StatusCode::OK);
    let mut r = (code, Json(response)).into_response();
    r.headers_mut()
        .insert(REPLAYED_HEADER, HeaderValue::from_static("true"));
    r
}

fn key_conflict() -> Response {
    ApiError::new(
        ErrorCode::IdempotencyConflict,
        "this Idempotency-Key was used for a different request",
    )
    .into_response()
}

/// `POST /api/v1/seats/open` (B4-1b-1, B4-1b-2b): a seat by persona, and its provider
/// started over pipes ([`aib_seatopen::pipe_argv`]). Writes the seat, its open fields
/// (seat_meta) and `seat.opened` in one transaction, then starts the session through the
/// same M1 gate, heat gate and slots as `POST /seats/{id}/sessions`. The seat is a fact
/// once written: a session that cannot start is answered as `201 {seat, session: null,
/// session_error}`, never as an error (a caller told "not opened" would open a second
/// seat for the persona; IFACE-b4-1b-2-session §5).
///
/// The `Idempotency-Key` is required: a retry must never open a second seat or start a
/// second process. A retry is answered before the cards are read and before the folder
/// is looked at (a card renamed, the file broken or the folder gone since the first
/// answer does not turn a replay into an error; 043dd L1, F9). The key is written in the
/// seat's transaction as in progress and finished with the whole answer once the session
/// started or failed; a retry in between, or after aibd stopped in the middle, gets `409
/// idempotency_conflict` (as the session writes, T81). A replay shows the session as it
/// is now. `dry_run` answers the plan (with the argv in full and the env names aibd sets)
/// and writes nothing, not even an event.
pub async fn open(State(st): State<AppState>, headers: HeaderMap, body: Body) -> Response {
    // a client that hangs up still gets its key finished (043dd T81 M1)
    crate::idem::detached(open_detached(st, headers, body)).await
}

async fn open_detached(st: AppState, headers: HeaderMap, body: Body) -> Response {
    if !json_content_type(&headers) {
        return bad("Content-Type must be application/json").into_response();
    }
    let key = match idempotency_key(&headers) {
        Ok(k) => k,
        Err(e) => return e.into_response(),
    };
    let Ok(bytes) = axum::body::to_bytes(body, MAX_BODY).await else {
        return ApiError::new(ErrorCode::PayloadTooLarge, "request body over 4 KiB")
            .into_response();
    };
    let mut req: RawOpen = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(_) => return Refusal(
            "body",
            "body must be {persona_uid, provider, mode, cwd, model?, cabin?, axis_id?, new?, dry_run?, handoff?, mempack?}",
        )
        .into_response(),
    };
    // the same carry a page's open has, read before anything opens (PREREAD-seat-carry)
    let (ho, mp) = (req.handoff.take(), req.mempack.take());
    req.carry = match tokio::task::spawn_blocking(move || {
        crate::seat_carry::gather(crate::docs::root().as_deref(), ho.as_deref(), mp.as_deref())
    })
    .await
    {
        Ok(Ok(c)) => c,
        Ok(Err((reason, message))) => return Refusal(reason, message).into_response(),
        Err(_) => return internal(),
    };
    open_checked(st, req, key, None).await
}

/// The act type of the built-in page's "add a seat" (N-workspace wave 2).
pub const PAGE_OPEN: &str = "seat_open";

/// What the page sends to open a seat: a brand, a mode and a persona, never a folder, a
/// cabin or arguments (5d33a CHECKLIST-workspace-wave2 B2: aibd builds the argv; the seat
/// works in its own empty folder).
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PageOpen {
    #[serde(rename = "type")]
    _kind: String,
    provider: String,
    mode: String,
    persona_uid: String,
    #[serde(default)]
    model: Option<String>,
    idem_key: String,
    /// N-ws-open-bind: a `brn_` uid from `GET /api/v1/brains` (never a folder or route key).
    #[serde(default)]
    brain: Option<String>,
    /// N-ws-open-bind: a role uid; a role is kept on a tree item's assignment, not on a seat
    /// (11-14 ruling), so without an item it is taken and not stored.
    #[serde(default)]
    role: Option<String>,
    /// Work in the same folder as this seat (0eed7 STUCK-live-in 15:23: the demo's author and
    /// reviewer share one folder). 5d33a CHECK-preview-work-seat r2: only a new `work` seat,
    /// only with a running `work` seat, only the folder aibd made for it
    /// (`<data>/seat-work/<id>`, not a link); the page still never names a path.
    #[serde(default)]
    with_seat: Option<String>,
    /// With `with_seat`: the new work seat writes in its own tree of that seat's folder (a git
    /// worktree on a branch of its own), not in the folder itself, so three or more seats write
    /// at once without one index and HEAD (DECISION-workseat-3plus-a9136-1120 1). Without it the
    /// two share the folder (an author and its reviewer).
    #[serde(default)]
    own_tree: bool,
    /// N-web-worktree: a repo's registered name (`GET /api/v1/version` `repos`), never a path;
    /// a dgrs or work seat (`repo_modes`), opened in its own copy of that repo.
    #[serde(default)]
    repo: Option<String>,
    /// The fingerprint ticket the first ask with `repo` answered.
    #[serde(default)]
    ticket: Option<String>,
    /// With `repo`: the full commit the seat's branch starts on (the all-head, ORDER-webonly 12);
    /// none: the registered repo's HEAD.
    #[serde(default)]
    base: Option<String>,
    /// A seat moved in from the terminal (PREREAD-seat-carry-a9136-1121): the file name, never a
    /// path, of its handoff (`HANDOFF-….md`) in the pending folder; copied into `.aib/`.
    #[serde(default)]
    handoff: Option<String>,
    /// With `handoff`: its memory pack's file name (`MEMPACK-….md`).
    #[serde(default)]
    mempack: Option<String>,
}

/// `POST /api/v1/act {type: "seat_open", provider, mode, persona_uid, model?, idem_key}` from a
/// confirmed built-in page. The caller ([`crate::seat_input`]) already refused a user folder
/// page and a dgrs seat without this machine's switch (B1); here the open takes the same
/// checks as `POST /api/v1/seats/open` (brand and mode as `GET /api/v1/version` lists them,
/// refused before anything is written: B2 / B5; the same argv: B3), and the event names the
/// page that opened it. The key is the page's own, kept apart from the start-up token's.
pub async fn page_open(
    st: AppState,
    body: serde_json::Value,
    opened_by: serde_json::Value,
) -> Response {
    let req: PageOpen = match serde_json::from_value(body) {
        Ok(r) => r,
        Err(_) => {
            return Refusal(
                "body",
                "body must be {type, provider, mode, persona_uid, model?, idem_key, with_seat?}; a page gives no folder, cabin or arguments",
            )
            .into_response()
        }
    };
    let key_ok = (1..=MAX_KEY).contains(&req.idem_key.len())
        && req
            .idem_key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b':'));
    if !key_ok {
        return Refusal(
            "key_missing",
            "idem_key must be 1-128 of A-Z a-z 0-9 - _ . :",
        )
        .into_response();
    }
    // N-ws-codex-work (5d33a PREREAD-codexwork-1a40bef6 L1): refused before a tree is opened
    // (own_tree / repo register one in the person's .git/worktrees); check_open says it again
    if req.provider == "codex"
        && req.mode == "work"
        && (req.with_seat.is_some() || req.repo.is_some() || req.own_tree)
    {
        return Refusal(
            "codex_work_own_folder",
            "a codex work seat opens in its own folder only for now (no with_seat, repo or folder); no seat was made",
        )
        .into_response();
    }
    let brain_route = match req.brain.as_deref() {
        None => None,
        Some(uid) => match crate::registry::brain_route(&crate::registry::data_of(&st), uid) {
            Ok(k) => Some(k),
            Err(_) => {
                return Refusal("brain", "brain must be a uid GET /api/v1/brains lists")
                    .into_response()
            }
        },
    };
    if req.base.is_some() && req.repo.is_none() {
        return Refusal("base", "base goes with repo").into_response();
    }
    // read before anything is opened: a refused name opens nothing (PREREAD-seat-carry)
    let (ho, mp) = (req.handoff.clone(), req.mempack.clone());
    let carry = match tokio::task::spawn_blocking(move || {
        crate::seat_carry::gather(crate::docs::root().as_deref(), ho.as_deref(), mp.as_deref())
    })
    .await
    {
        Ok(Ok(c)) => c,
        Ok(Err((reason, message))) => return Refusal(reason, message).into_response(),
        Err(_) => return internal(),
    };
    let mut opened_by = opened_by;
    let tree_cwd = match req.repo.as_deref() {
        None => None,
        Some(name) => match repo_tree(&st, &req, name, &mut opened_by).await {
            Ok(c) => Some(c),
            Err(r) => return *r,
        },
    };
    // whose items the new seat joins: the folder's owner (H1), not a seat sharing it
    let mut join_of: Option<String> = None;
    let cwd = match req.with_seat.as_deref() {
        None if req.own_tree => {
            return Refusal(
                "own_tree",
                "own_tree goes with with_seat: a tree of whose folder",
            )
            .into_response()
        }
        None => None,
        Some(other) => match shared_folder(&st, &req.mode, other).await {
            Some((c, owner)) if req.own_tree => {
                join_of = Some(owner.clone());
                match own_tree_of(&st, &c, &owner, &mut opened_by).await {
                    Ok(t) => Some(t),
                    Err(r) => return *r,
                }
            }
            Some((c, _)) if work_seats_in(&st, &c).await >= MAX_WORK_SEATS_PER_FOLDER => {
                return folder_writers_full()
            }
            Some((c, owner)) => {
                // 5d33a r2 5: the event says whose folder it is (its owner's)
                opened_by["with_seat"] = serde_json::json!(seat_code(&owner));
                join_of = Some(owner);
                Some(c)
            }
            None => return Refusal(
                "with_seat",
                "with_seat is for a work seat, naming a running work seat whose folder aibd made",
            )
            .into_response(),
        },
    };
    let raw = RawOpen {
        persona_uid: req.persona_uid,
        provider: req.provider,
        mode: req.mode,
        model: req.model,
        cabin: None,
        axis_id: None,
        cwd,
        new: false,
        dry_run: false,
        brain_route,
        tree: tree_cwd.is_some(),
        join_nodes_of: join_of,
        join_role: crate::assign::with_seat_role(req.role.as_deref()),
        carry,
        handoff: None,
        mempack: None,
    };
    let raw = RawOpen {
        cwd: tree_cwd.or(raw.cwd),
        ..raw
    };
    open_checked(
        st,
        raw,
        Some(format!("page:{}", req.idem_key)),
        Some(opened_by),
    )
    .await
}

/// N-web-worktree (DESIGN r3 §1.2): a page's dgrs seat with a registered repo. A dgrs seat
/// only, not sharing another seat's folder, a name the registry has; then the owner's
/// fingerprint for this seat, brand and repo; once approved, the seat's own tree is opened
/// (settings compared first) and its folder is the seat's cwd. `Err` is the response.
async fn repo_tree(
    st: &AppState,
    req: &PageOpen,
    name: &str,
    opened_by: &mut serde_json::Value,
) -> Result<String, Box<Response>> {
    if !crate::repos::REPO_MODES.contains(&req.mode.as_str()) {
        return Err(Box::new(
            Refusal("repo", "a repo is given to a dgrs or work seat only").into_response(),
        ));
    }
    if req.with_seat.is_some() {
        return Err(Refusal(
            "repo",
            "a seat with a repo works in its own tree, not in another seat's folder",
        )
        .into_response()
        .into());
    }
    let data = crate::registry::data_of(st);
    let Some(repo) = crate::repos::find(&data, name) else {
        return Err(
            Refusal("repo", "repo must be a name GET /api/v1/version lists")
                .into_response()
                .into(),
        );
    };
    // DESIGN-work-repo-7b81e-1120 Q2: a work seat stays in its sandbox and writes only its own
    // copy, and the repo had the owner's finger when it was registered, so its open is not
    // asked again; a dgrs seat (no sandbox) is asked at every open as before
    // 5d33a PREREAD-workrepo L: fail closed, every mode but work is asked
    let asked_now = req.mode != "work";
    let pairing = opened_by["pairing"].as_str().unwrap_or("").to_owned();
    let ask = crate::repos::open_ask(
        &repo,
        &req.provider,
        &req.persona_uid,
        format!("page:{pairing}"),
    );
    if asked_now {
        let Some(ticket) = req.ticket.as_deref() else {
            return Err(Box::new(crate::repos::asked(&ask)));
        };
        if !crate::owner_ok::approved_for(ticket, &ask) {
            return Err(Box::new(crate::repos::not_approved()));
        }
    }
    if req
        .base
        .as_deref()
        .is_some_and(|b| !crate::repos::base_ok(b))
    {
        return Err(Box::new(
            Refusal("base", "base is a full commit: 40 lower-case hex").into_response(),
        ));
    }
    let base = req.base.clone();
    // 5d33a PREREAD-workrepo N2: the clone runs inside the request; its time goes to the log
    let started = std::time::Instant::now();
    let Ok(tree_id) = crate::sessions::new_id("wt") else {
        return Err(Box::new(internal()));
    };
    let now = crate::sessions::now_rfc3339();
    let mmdd = format!("{}{}", &now[5..7], &now[8..10]);
    let work = seat_work_root(st.sessions.private_dir());
    let hooks = seat_git_hooks(st.sessions.private_dir());
    let opened = tokio::task::spawn_blocking(move || {
        crate::repos::prepare_tree(&repo, &work, &hooks, &tree_id, &mmdd, base.as_deref())
    })
    .await;
    match opened {
        Ok(Ok((tree, head))) => {
            tracing::info!(repo = name, ms = started.elapsed().as_millis() as u64, "seat copy opened");
            opened_by["repo"] = serde_json::json!(name);
            opened_by["base_head"] = serde_json::json!(head);
            tree.to_str()
                .map(str::to_owned)
                .ok_or_else(|| Box::new(internal()))
        }
        Ok(Err(why)) => Err(Refusal(
            why,
            if why == "base_not_in_repo" {
                "the seat's tree was not opened (no branch of the registered repo reaches that base)"
            } else if why == "disk_low" {
                "the seat's tree was not opened (too little free disk for another copy)"
            } else if why == "repo_copies_full" {
                "the seat's tree was not opened (this machine keeps 9 repo copies in use; archive a seat that has one, its copy then stops counting; the folders are under aibd's data seat-work/wt*)"
            } else {
                "the seat's tree was not opened (the repo's settings changed since it was registered: register it again)"
            },
        )
        .into_response().into()),
        Err(_) => Err(Box::new(internal())),
    }
}

/// The folder a page's new seat may share with `other` (5d33a CHECK-preview-work-seat r2):
/// the new seat is `work`; `other` is a running `work` seat (never a dgrs seat's folder, whose
/// files a seat without limits put there); its folder is the one aibd made for it
/// (`<data>/seat-work/<other>`, so a page reaches no folder a terminal chose) and is a real
/// folder now, not a link (checked each open). `None`: any of these is not so.
///
/// `other` may itself share its folder (a reviewer opened with `with_seat`): the folder is then
/// its owner's, and the owner is what the new seat shares (519d0 VERIFY-selftest 2a3e3cbc H1:
/// the page's default picked the newest seat, the reviewer, and was refused). Returns the
/// folder and its owner.
async fn shared_folder(st: &AppState, mode: &str, other: &str) -> Option<(String, String)> {
    if mode != "work" {
        return None;
    }
    let live = st.sessions.live_seats();
    let id_ok = |id: &str| {
        id.strip_prefix("seat_").is_some_and(|h| {
            !h.is_empty() && h.len() <= 32 && h.bytes().all(|b| b.is_ascii_hexdigit())
        })
    };
    if !live.iter().any(|s| s == other) || !id_ok(other) {
        return None;
    }
    let store = st.store.clone();
    let metas = tokio::task::spawn_blocking(move || store.seat_metas())
        .await
        .ok()?
        .ok()?;
    let work_cwd = |id: &str| {
        metas
            .iter()
            .find(|m| m.seat_id == id && m.mode.as_deref() == Some("work"))
            .map(|m| m.cwd.clone())
    };
    let cwd = work_cwd(other)?;
    // the folder's owner: the seat whose own folder (`<seat-work>/<id>`) it is
    let owner = std::path::Path::new(&cwd).file_name()?.to_str()?.to_owned();
    if owner != other && (!live.contains(&owner) || !id_ok(&owner)) {
        return None;
    }
    if work_cwd(&owner).as_deref() != Some(cwd.as_str()) {
        return None;
    }
    let own = seat_home(st.sessions.private_dir(), &owner);
    let real_dir = std::fs::symlink_metadata(&own).is_ok_and(|md| md.file_type().is_dir());
    (real_dir && std::path::Path::new(&cwd) == own.as_path()).then_some((cwd, owner))
}

/// How many work seats may run in one shared folder (DECISION-workseat-3plus-a9136-1120 3):
/// two (Andy's 11-20 run: an author and a reviewer); a third would write the same tree, index
/// and HEAD until each writer gets its own tree (N-web-worktree).
pub const MAX_WORK_SEATS_PER_FOLDER: usize = 2;

/// The running work seats whose folder is `cwd`.
async fn work_seats_in(st: &AppState, cwd: &str) -> usize {
    let live = st.sessions.live_seats();
    let store = st.store.clone();
    let Ok(Ok(metas)) = tokio::task::spawn_blocking(move || store.seat_metas()).await else {
        // not read: count it full, so a third writer is not let in unseen
        return MAX_WORK_SEATS_PER_FOLDER;
    };
    metas
        .iter()
        .filter(|m| m.mode.as_deref() == Some("work") && m.cwd == cwd)
        .filter(|m| live.contains(&m.seat_id))
        .count()
}

/// A tree of its own for a new work seat, off the folder `other` works in (that folder is a
/// git repository with a first commit: [`init_work_repo`]): `<seat-work>/<tree id>/repo` on
/// branch `seat/<tree id>-<MMDD>` at the folder's HEAD, with aibd's empty hooks. The open
/// records `repo` and `base_head` the way a registered repo's tree does (b4297: the tree read
/// finds the tree by them). The tree is not marked `tree`: project settings a seat committed
/// into the folder still refuse the open (b4297 DECISION-workseat-3plus 2).
async fn own_tree_of(
    st: &AppState,
    folder: &str,
    other: &str,
    opened_by: &mut serde_json::Value,
) -> Result<String, Box<Response>> {
    let Ok(tree_id) = crate::sessions::new_id("wt") else {
        return Err(Box::new(internal()));
    };
    let now = crate::sessions::now_rfc3339();
    let mmdd = format!("{}{}", &now[5..7], &now[8..10]);
    let tree = crate::seat_worktree::tree_path(
        &seat_work_root(st.sessions.private_dir()).join(&tree_id),
        "repo",
    );
    let branch = crate::seat_worktree::branch_name(&tree_id, &mmdd);
    let hooks = seat_git_hooks(st.sessions.private_dir());
    let src = std::path::PathBuf::from(folder);
    let t2 = tree.clone();
    let author = other.to_owned();
    let opened = tokio::task::spawn_blocking(move || {
        crate::seat_worktree::open_clone(&src, &t2, &branch, &author, &hooks)
    })
    .await;
    match opened {
        Ok(Ok(head)) => {
            opened_by["with_seat"] = serde_json::json!(seat_code(other));
            opened_by["repo"] = serde_json::json!(format!("with:{}", seat_code(other)));
            opened_by["base_head"] = serde_json::json!(head);
            tree.to_str()
                .map(str::to_owned)
                .ok_or_else(|| Box::new(internal()))
        }
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "own tree not opened");
            Err(Box::new(
                Refusal(
                    "own_tree",
                    "the seat's own tree was not opened (the folder has no commit yet, or git refused)",
                )
                .into_response(),
            ))
        }
        Err(_) => Err(Box::new(internal())),
    }
}

/// Whether restarting `seat_id` would make one too many work seats in its folder (5d33a
/// PREREAD-nx d523a5af L1: A and B share, B stops, C opens with A, B is restarted = three
/// writers): a work seat whose folder already has [`MAX_WORK_SEATS_PER_FOLDER`] running work
/// seats besides it. An unread seat list counts as full.
pub(crate) async fn restart_overfills(st: &AppState, seat_id: &str) -> bool {
    // a seat that runs now is not restarted at all: `seat_running` says so (5d33a 7a1c8568 N1)
    if st.sessions.live_seats().iter().any(|s| s == seat_id) {
        return false;
    }
    let store = st.store.clone();
    let sid = seat_id.to_owned();
    let Ok(Ok(meta)) = tokio::task::spawn_blocking(move || store.seat_meta(&sid)).await else {
        return true;
    };
    let Some(m) = meta else {
        return false;
    };
    if !matches!(m.mode.as_deref(), Some("work")) {
        return false;
    }
    work_seats_in(st, &m.cwd).await >= MAX_WORK_SEATS_PER_FOLDER
}

/// 409 `shared_folder_writers_full`: the folder already has its work seats.
pub(crate) fn folder_writers_full() -> Response {
    let body = serde_json::json!({
        "code": "shared_folder_writers_full",
        "message": "this folder already has two work seats writing in it; open the new one in its own folder",
        "request_id": crate::guard::request_id(),
    });
    (StatusCode::CONFLICT, Json(body)).into_response()
}

/// `seat.dgrs_opened`: a page opened a dgrs seat (5d33a CHECKLIST-wave3 E4). Its own
/// aggregate (the seat's sequence 1 is `seat.opened`); not written is logged, not refused: the
/// seat is open and `seat.opened` already says by whom.
async fn dgrs_opened(st: &AppState, payload: serde_json::Value) {
    side_event(st, "seat_dgrs", "seat.dgrs_opened", payload).await;
}

/// `seat.work_opened`: a page opened a work seat (5d33a CHECK-preview-work-seat r2 b): which
/// page, the folder, and whose folder it shares; written the way `seat.dgrs_opened` is.
async fn work_opened(st: &AppState, payload: serde_json::Value) {
    side_event(st, "seat_work", "seat.work_opened", payload).await;
}

/// One event in its own aggregate beside the seat's; not written is logged, not refused.
pub(crate) async fn side_event(
    st: &AppState,
    aggregate: &str,
    kind: &str,
    payload: serde_json::Value,
) -> Option<String> {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return None;
    };
    let id_out = id.clone();
    let store = st.store.clone();
    let ev = NewEvent {
        event_id: id.clone(),
        aggregate_type: aggregate.into(),
        aggregate_id: id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    if !matches!(
        tokio::task::spawn_blocking(move || store.append(vec![ev])).await,
        Ok(Ok(_))
    ) {
        tracing::warn!(event = kind, "side event not written");
        return None;
    }
    Some(id_out)
}

/// The open itself, once the request is read: shared by the start-up token's route and the
/// page's act. `opened_by` is put in the event (a page's opens; none for the token).
async fn open_checked(
    st: AppState,
    req: RawOpen,
    key: Option<String>,
    opened_by: Option<serde_json::Value>,
) -> Response {
    // 519d0 11-21 06:45 point 1: no model named ⇒ the brand's default (none set ⇒ the CLI's own)
    let mut req = req;
    req.model = crate::default_model::pick(
        req.model.take(),
        &req.provider,
        &crate::default_model::now(),
    );
    if let Err(e) = check_open(&req) {
        return e.into_response();
    }
    if key.is_none() && !req.dry_run {
        return Refusal("key_missing", "Idempotency-Key is required to open a seat")
            .into_response();
    }
    // the key is bound to the whole request, cwd included
    let idem = key.map(|k| IdemKey {
        scope: OPEN_SCOPE.into(),
        key: k,
        request: serde_json::json!({
            "persona_uid": req.persona_uid, "provider": req.provider, "mode": req.mode,
            "model": req.model, "cabin": req.cabin, "axis_id": req.axis_id, "cwd": req.cwd,
        })
        .to_string(),
    });
    // a dry run is never a retry: it answers the plan even with a key used before (043dd #759 N1)
    if let Some(k) = idem.clone().filter(|_| !req.dry_run) {
        let store = st.store.clone();
        match tokio::task::spawn_blocking(move || store.idem_peek(&k.scope, &k.key)).await {
            Ok(Ok(Some((request, status, response)))) => {
                let same = idem.as_ref().is_some_and(|k| k.request == request);
                return match (same, status) {
                    (false, _) => key_conflict(),
                    (true, aib_store::IN_PROGRESS) => open_in_progress(),
                    (true, _) => replayed_now(&st, status, response),
                };
            }
            Ok(Ok(None)) => {}
            Ok(Err(e)) => {
                tracing::error!(error = %e, "idempotency key read failed");
                return internal();
            }
            Err(_) => return internal(),
        }
    }
    // 5d33a M1 on ecf54f91d: a work seat given a folder by path works under <data>/seat-work
    if let (Some(c), true) = (req.cwd.as_deref(), req.mode == "work") {
        if let Err(why) = work_cwd_ok(c, &seat_work_root(st.sessions.private_dir())) {
            return Refusal(why, "a work seat's folder is under aibd's seat-work folder")
                .into_response();
        }
    }
    // S89-live-workdir: a given folder must exist and carry no project settings (they would
    // be loaded: --setting-sources project,local); none given ⇒ the seat's own empty folder
    let home = home_dir(|k| std::env::var(k).ok());
    if let Some(c) = req.cwd.as_deref() {
        if !std::path::Path::new(c).is_dir() {
            return cwd_refused().into_response();
        }
        // a tree aibd opened has the repo's settings, compared with what the owner approved
        let listed = if req.tree {
            None
        } else {
            project_settings_in(std::path::Path::new(c), home.as_deref())
        };
        if let Some(f) = listed {
            let body = serde_json::json!({
                "code": ErrorCode::InvalidRequest.as_str(),
                "message": "this folder has project settings a seat would load; open the seat \
                            without cwd (its own empty folder) or in a folder without them",
                "request_id": crate::guard::request_id(),
                "reason": "cwd_has_project_settings",
                "file": f.to_string_lossy(),
            });
            return (StatusCode::BAD_REQUEST, Json(body)).into_response();
        }
    }
    let personas = st.personas.clone();
    let cards = match tokio::task::spawn_blocking(move || personas.cards()).await {
        Ok(Ok(c)) => c,
        Ok(Err(e)) => return unavailable(&e),
        Err(_) => return internal(),
    };
    let Some(card) = cards.into_iter().find(|c| c.uid == req.persona_uid) else {
        return Refusal(
            "persona_missing",
            "persona_uid: no persona card has this uid",
        )
        .into_response();
    };
    // S89-live-persona (Andy 11-18: the seat answered as plain Claude): the card text,
    // checked against the hash the cards list names, kept in aibd's private folder for the
    // provider to read. A card whose text cannot be given refuses the open; a cards file
    // that does not say where the text is (an older registry) opens without it, and says so.
    let persona_file = match card.text_path.clone() {
        None => None,
        Some(path) => {
            let sha = card.persona_sha.clone();
            let dir = st.sessions.private_dir().to_path_buf();
            let dry = req.dry_run;
            let kept = tokio::task::spawn_blocking(move || {
                let text = crate::personas::card_text(&path, &sha)?;
                if dry {
                    return Ok(crate::personas::kept_text_path(&dir, &sha));
                }
                crate::personas::keep_text(&dir, &sha, &text)
                    .map_err(|_| crate::personas::PersonaTextError::Unreadable)
            })
            .await;
            match kept {
                Ok(Ok(p)) => match p.to_str() {
                    Some(s) => Some(s.to_owned()),
                    None => return internal(),
                },
                Ok(Err(e)) => return Refusal(e.reason(), e.message()).into_response(),
                Err(_) => return internal(),
            }
        }
    };
    let Ok(session_uuid) = new_uuid() else {
        return internal();
    };
    let mut argv = aib_seatopen::pipe_argv(
        &req.provider,
        &req.mode,
        req.model.as_deref(),
        &session_uuid,
    );
    // S89-talk-1: a claude seat gets aibd's own two tools (seat_list / seat_send) and only
    // them; --strict-mcp-config (in pipe_argv) keeps every account-level server out
    if let (Ok(a), "claude") = (argv.as_mut(), req.provider.as_str()) {
        let Ok(exe) = std::env::current_exe() else {
            return internal();
        };
        let Some(exe) = exe.to_str() else {
            return internal();
        };
        // private dir = <data>/sessions
        let private = st.sessions.private_dir().to_path_buf();
        let Some(data) = private.parent().and_then(|d| d.to_str()).map(str::to_owned) else {
            return internal();
        };
        let work_gate = (now_gate_on() && req.mode == "work").then(|| gate_settings(exe, &data));
        let (exe, dry, start) = (exe.to_owned(), req.dry_run, session_uuid.clone());
        let kept = tokio::task::spawn_blocking(move || {
            if dry {
                return Ok(talk_config_path(&private, &start));
            }
            keep_talk_config(&private, &start, &exe, &data)
        })
        .await;
        let Some(path) = kept
            .ok()
            .and_then(Result::ok)
            .and_then(|p| p.to_str().map(str::to_owned))
        else {
            return internal();
        };
        a.extend(talk_args(&path, &req.mode));
        if let Some(g) = work_gate.as_deref() {
            fold_gate_into_work_settings(a, g);
        }
    }
    // N-ws-ask-codex: a codex seat gets the same server and tools, as settings (no file)
    if let (Ok(a), "codex") = (argv.as_mut(), req.provider.as_str()) {
        let exe = std::env::current_exe().ok();
        let data = st
            .sessions
            .private_dir()
            .parent()
            .map(std::path::Path::to_path_buf);
        let (Some(exe), Some(data)) = (
            exe.as_deref().and_then(std::path::Path::to_str),
            data.as_deref().and_then(std::path::Path::to_str),
        ) else {
            return internal();
        };
        a.extend(codex_talk_args(exe, data));
    }
    if let (Ok(a), Some(f)) = (argv.as_mut(), persona_file.as_deref()) {
        if !aib_seatopen::add_persona_file(&req.provider, a, f) {
            return Refusal(
                "persona_unsupported",
                "this provider cannot be given the persona card yet",
            )
            .into_response();
        }
    }
    let mut plan = serde_json::json!({
        "persona": {"uid": card.uid, "name": card.name, "persona_sha": card.persona_sha,
                    "applied": persona_file.is_some()},
        "provider": req.provider,
        "mode": req.mode,
        "model": req.model,
        "cabin": req.cabin,
        "axis_id": req.axis_id,
        "env": crate::allowlist::SEAT_ENV,
        "env_withheld": withheld_for(&req.mode),
        "cwd": req.cwd.clone().unwrap_or_else(|| {
            seat_home(st.sessions.private_dir(), "<new seat id>")
                .to_string_lossy()
                .into_owned()
        }),
    });
    match &argv {
        Ok(a) => plan["argv"] = serde_json::json!(a),
        Err(e) => plan["session_error"] = pipe_refused(e),
    }
    if req.dry_run {
        return Json(serde_json::json!({ "plan": plan })).into_response();
    }
    let (Ok(seat_id), Ok(event_id)) = (new_id("seat_"), new_id("ev_")) else {
        return internal();
    };
    let cwd = match req.cwd.clone() {
        Some(c) => c,
        None => {
            let d = seat_home(st.sessions.private_dir(), &seat_id);
            let made = d.clone();
            match tokio::task::spawn_blocking(move || crate::platform::create_private_dir(&made))
                .await
            {
                Ok(Ok(())) => match d.to_str() {
                    Some(s) => s.to_owned(),
                    None => return internal(),
                },
                _ => return internal(),
            }
        }
    };
    // the handoff and memory pack go into the folder before the seat is written and starts
    let carried = if req.carry.is_empty() {
        None
    } else {
        let (into, files) = (cwd.clone(), std::mem::take(&mut req.carry));
        match tokio::task::spawn_blocking(move || {
            crate::seat_carry::put(std::path::Path::new(&into), &files)
        })
        .await
        {
            Ok(Ok(c)) => Some(c),
            _ => {
                return Refusal(
                    "carry",
                    "the handoff could not be copied into the seat's folder (.aib is not a plain folder there)",
                )
                .into_response()
            }
        }
    };
    // what the session start needs once the seat is written
    let started = (seat_id.clone(), card.clone(), cwd.clone());
    let now = now_rfc3339();
    let seat = Seat {
        seat_id: seat_id.clone(),
        name: card.name.clone(),
        state: state_str(SeatState::Created),
        revision: 1,
        created_at: now.clone(),
        updated_at: now.clone(),
    };
    let meta = SeatMeta {
        seat_id: seat_id.clone(),
        provider: req.provider.clone(),
        role: None,
        cwd: cwd.clone(),
        model: req.model.clone(),
        model_pinned: req.model.is_some(),
        // N-revive: the conversation's id, so a restart of aibd picks it up again (--resume)
        resume_id: argv.is_ok().then(|| session_uuid.clone()),
        project_uid: None,
        persona_uid: Some(card.uid.clone()),
        brain_route: req.brain_route.clone(),
        updated_at: now.clone(),
        mode: Some(req.mode.clone()),
        axis_id: req.axis_id.clone(),
        cabin: req.cabin.clone(),
    };
    // what was opened (IFACE §2: the persona by uid and card version): never where (no
    // cwd in the journal) and never the name, which is read when shown (043dd M4)
    let mut payload = serde_json::json!({
        "seat_id": seat_id,
        "persona_uid": card.uid,
        "persona_sha": card.persona_sha,
        "persona_applied": persona_file.is_some(),
        // S89-dgrs (5d33a): which policy env names this seat does not inherit
        "env_withheld": withheld_for(&req.mode),
        "provider": req.provider,
        "mode": req.mode,
        "model": req.model,
        "cabin": req.cabin,
        "axis_id": req.axis_id,
    });
    // 5d33a CHECKLIST-wave3 E4: a dgrs seat a page opened gets its own event, for Andy to
    // find later (a seat whose tools are not stopped, opened from a page)
    let page_dgrs = opened_by.clone().filter(|_| req.mode == "dgrs").map(|by| {
        serde_json::json!({"seat_id": seat_id, "opened_by": by, "provider": req.provider,
                           "model": req.model, "persona_uid": card.uid})
    });
    let page_work = opened_by.clone().filter(|_| req.mode == "work").map(|by| {
        serde_json::json!({"seat_id": seat_id, "opened_by": by, "provider": req.provider,
                           "persona": card.uid, "cwd": cwd})
    });

    // 5d33a B1: which page opened it
    if let Some(by) = opened_by {
        payload["opened_by"] = by;
    }
    let event = NewEvent {
        event_id,
        aggregate_type: "seat".into(),
        aggregate_id: seat_id,
        sequence: 1,
        event_type: "seat.opened".into(),
        correlation_id: None,
        occurred_at: now,
        payload,
    };
    let store = st.store.clone();
    let key = idem.clone();
    let res = tokio::task::spawn_blocking(move || {
        // the answer shows the seat as the list will: the seat code when an older seat
        // has this persona (043dd M3). Two opens of one persona at the same instant may
        // both answer without it; the list is the one that counts.
        let dup = store
            .seat_metas()?
            .iter()
            .any(|m| m.persona_uid.as_deref() == Some(card.uid.as_str()));
        let response = serde_json::json!({
            "seat": view_with(&seat, Some(&meta), Some(&card), Some(dup)),
            "session": null,
        });
        store
            .create_seat_with_meta(
                seat,
                meta,
                event,
                idem,
                // in progress until the session started or failed (finished below)
                aib_store::IN_PROGRESS,
                response.clone(),
            )
            .map(|r| (r, response))
    })
    .await;
    match res {
        Ok(Ok((SeatCreate::Created(_), mut response))) => {
            if let Some(payload) = page_dgrs {
                dgrs_opened(&st, payload).await;
            }
            if let Some(payload) = page_work {
                work_opened(&st, payload).await;
            }
            let (seat_id, card, cwd) = started;
            // FINDING-andy-realrun 1: a seat sharing another's folder is on that seat's items
            if let Some(other) = req.join_nodes_of.as_deref() {
                let by = format!("with_seat:{}", seat_code(other));
                crate::assign::join_nodes(&st, other, &seat_id, by, req.join_role).await;
            }
            // S89-live-2: codex takes the card in thread/start, not as a file in argv: the
            // kept text (sha checked when kept) goes to the session that starts now
            if let (Some(f), true) = (persona_file.clone(), req.provider == "codex") {
                let text = tokio::task::spawn_blocking(move || std::fs::read_to_string(f)).await;
                match text {
                    Ok(Ok(t)) => st.sessions.set_persona(&seat_id, t),
                    _ => return internal(),
                }
            }
            match start_session(&st, &seat_id, &card, argv, &cwd, &req.mode).await {
                Ok(session) => response["session"] = session,
                Err(e) => response["session_error"] = e,
            }
            if let Some(c) = &carried {
                response["carried"] = serde_json::json!(c.paths);
                response["carried_excluded"] = serde_json::json!(c.excluded);
            }
            if let Some(k) = key {
                let store = st.store.clone();
                let answer = response.clone();
                let done = tokio::task::spawn_blocking(move || {
                    store.idem_finish(&k, StatusCode::CREATED.as_u16(), &answer)
                })
                .await;
                if !matches!(done, Ok(Ok(true))) {
                    // the key stays in progress: a retry gets 409, never a second start
                    tracing::error!("seat open key could not be finished");
                }
            }
            (StatusCode::CREATED, Json(response)).into_response()
        }
        Ok(Ok((SeatCreate::Replayed { status, .. }, _))) if status == aib_store::IN_PROGRESS => {
            open_in_progress()
        }
        Ok(Ok((SeatCreate::Replayed { status, response }, _))) => {
            replayed_now(&st, status, response)
        }
        Ok(Ok((SeatCreate::KeyConflict, _))) => key_conflict(),
        Ok(Err(e)) => {
            tracing::error!(error = %e, "seat open failed");
            internal()
        }
        Err(_) => internal(),
    }
}

/// A seat open whose key is still in progress: started by another request that has not
/// answered yet, or cut off by a stop of aibd. Never a second seat or process.
fn open_in_progress() -> Response {
    ApiError::new(
        ErrorCode::IdempotencyConflict,
        "a seat open with this Idempotency-Key is still running, or aibd stopped in the \
         middle of it: list the seats, then use a new key",
    )
    .into_response()
}

/// A replayed open: the first answer, with its session as it is now (it may have left
/// the queue or ended since).
fn replayed_now(st: &AppState, status: u16, mut response: serde_json::Value) -> Response {
    if let Some(id) = response["session"]["id"].as_str() {
        if let Some(now) = st
            .sessions
            .get(id)
            .and_then(|s| serde_json::to_value(s).ok())
        {
            response["session"] = now;
        }
    }
    replayed(status, response)
}

/// `{code, message}` of a provider [`aib_seatopen::pipe_argv`] cannot start.
fn pipe_refused(e: &aib_seatopen::PipeRefused) -> serde_json::Value {
    let code = match e {
        aib_seatopen::PipeRefused::Provider(_) => "provider_unsupported",
        aib_seatopen::PipeRefused::ProviderMode(..) => "provider_mode_unsupported",
        _ => ErrorCode::InvalidRequest.as_str(),
    };
    serde_json::json!({"code": code, "message": e.to_string()})
}

/// S89-dgrs L1 (Andy 11-18: one seat without the ssh agent, one with it): the policy env
/// names a seat of `mode` does not inherit. A dgrs seat runs tools without asking, so it never
/// gets the ssh agent (a way onto other machines) or NODE_OPTIONS (code run in its node); only
/// a safe seat gets the whole policy, so a mode added later is withheld by default (5d33a).
pub fn withheld_for(mode: &str) -> Vec<String> {
    if mode != "safe" {
        crate::seat_input::DGRS_DENIED_ENV
            .iter()
            .map(|s| (*s).to_owned())
            .collect()
    } else {
        Vec::new()
    }
}

fn session_refused(e: &ApiError) -> serde_json::Value {
    serde_json::json!({"code": e.0.as_str(), "message": e.1})
}

/// The env aibd sets for a seat's provider ([`crate::allowlist::SEAT_ENV`]): which seat,
/// which card and which version of it. The card itself never goes into argv (seen by
/// `ps`, and over the 4 KiB an item may have): the checked text goes as a file
/// (S89-live-persona; the seat runs no 1.x hooks, so no hook gives it).
fn seat_env(seat_id: &str, card: &PersonaCard) -> Vec<(String, String)> {
    let [seat, uid, sha] = crate::allowlist::SEAT_ENV;
    vec![
        (seat.to_owned(), seat_id.to_owned()),
        (uid.to_owned(), card.uid.clone()),
        (sha.to_owned(), card.persona_sha.clone()),
    ]
}

/// Start the seat's provider (B4-1b-2b): the argv's program name becomes the path the
/// session policy lists for it, then the M1 gate (audited on refusal) and
/// [`crate::sessions::Sessions::start_gated`], as `POST /seats/{id}/sessions` does. The
/// session (`running` or `pending`), or `{code, message}` of why it did not start.
pub(crate) async fn start_session(
    st: &AppState,
    seat_id: &str,
    card: &PersonaCard,
    argv: Result<Vec<String>, aib_seatopen::PipeRefused>,
    cwd: &str,
    mode: &str,
) -> Result<serde_json::Value, serde_json::Value> {
    let mut argv = argv.map_err(|e| pipe_refused(&e))?;
    // N-ws-codex-work: a codex work seat's profile names its folder, known only here (an open
    // and a revive both come through); without it codex refuses to start (CODEX_WORK_OFF). The
    // folder as the file system names it: the sandbox compares real paths
    if mode == "work" && argv.first().is_some_and(|p| p == "codex") {
        let real = std::fs::canonicalize(cwd).unwrap_or_else(|_| std::path::PathBuf::from(cwd));
        let Some(real) = real.to_str() else {
            return Err(
                serde_json::json!({"code": ErrorCode::Internal.as_str(), "message": "internal error"}),
            );
        };
        argv.extend(aib_seatopen::codex_work_args(real, &|d| {
            std::path::Path::new(d).exists()
        }));
    }
    let listed = st
        .session_policy
        .programs
        .iter()
        .find(|p| p.name == argv[0])
        .and_then(|p| p.path.to_str().map(str::to_owned));
    let Some(path) = listed else {
        return Err(serde_json::json!({
            "code": ErrorCode::ProgramNotAllowed.as_str(),
            "message": "the session policy lists no program for this provider",
        }));
    };
    argv[0] = path;
    let req = crate::sessions::Request {
        argv,
        cwd: Some(std::path::PathBuf::from(cwd)),
        env: Vec::new(),
        timeout: None,
        withhold: withheld_for(mode),
        // S89-live-9: the provider reads its messages from stdin
        input: true,
    };
    let (mut req, asked) = crate::sessions::gate(st, req)
        .await
        .map_err(|e| session_refused(&e))?;
    // after the gate: a request may not set these, aibd does (allowlist::seat_owned)
    req.env = seat_env(seat_id, card);
    let mut repo_note: Option<String> = None;
    if mode == "work" {
        req.env
            .extend(work_git_env(&seat_git_hooks(st.sessions.private_dir())));
        // 0eed7 VERIFY N2: its commits carry its own name, not the folder's
        req.env
            .extend(crate::work_audit::git_identity(seat_id, &card.name));
        if cfg!(target_os = "macos") {
            let path = std::env::var("PATH").unwrap_or_default();
            if let Some(p) = work_path(&path, developer_dir().as_deref(), &|f| f.is_file()) {
                req.env.push(("PATH".to_owned(), p));
            }
        }
        // 0eed7 VERIFY-mom-preview-entry r1: the folder is a repository before the seat starts
        let (dir, env) = (std::path::PathBuf::from(cwd), req.env.clone());
        if let Ok(Err(e)) = tokio::task::spawn_blocking(move || init_work_repo(&dir, &env)).await {
            tracing::warn!(seat = seat_id, "work folder not made a repository: {e}");
            // 0eed7 N on 7bdfcabf4: the open says so, not only the log
            repo_note = Some(format!(
                "the work folder is not a git repository ({e}); the seat cannot commit until `git init` is run there"
            ));
        }
        // condition 6: what it does is journaled from its first output line
        crate::work_audit::watch(seat_id, std::path::Path::new(cwd));
    }
    // S89-talk-1: the seat's own token, for listing its cabin and sending (seat_token)
    let token = crate::seat_token::tokens().issue(seat_id).map_err(
        |_| serde_json::json!({"code": ErrorCode::Internal.as_str(), "message": "internal error"}),
    )?;
    req.env.push((crate::seat_token::ENV.to_owned(), token));
    let s = st.sessions.clone();
    let seat = seat_id.to_owned();
    match tokio::task::spawn_blocking(move || s.start_gated(Some(seat), req, asked)).await {
        Ok(Ok(rec)) => {
            // S89-live-4: a session that waits for a slot is answered with its place in the
            // queue and the slots (the open said 201 with nothing about the limit)
            let rec = if rec.state == "pending" {
                st.sessions.get(&rec.id).unwrap_or(rec)
            } else {
                rec
            };
            let mut v = serde_json::to_value(rec).map_err(|_| {
                serde_json::json!({"code": ErrorCode::Internal.as_str(), "message": "internal error"})
            })?;
            if let (Some(n), Some(o)) = (repo_note, v.as_object_mut()) {
                o.insert("work_repo_note".into(), serde_json::json!(n));
            }
            Ok(v)
        }
        Ok(Err(e)) => Err(session_refused(&e)),
        Err(_) => Err(serde_json::json!({
            "code": ErrorCode::Internal.as_str(), "message": "internal error",
        })),
    }
}

/// A random (version 4) uuid: the id the provider's conversation gets (`--session-id`).
pub(crate) fn new_uuid() -> Result<String, getrandom::Error> {
    let mut b = [0u8; 16];
    getrandom::fill(&mut b)?;
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h: String = b.iter().map(|x| format!("{x:02x}")).collect();
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    ))
}

#[cfg(test)]
mod tests {
    /// 5d33a N7: only a refusal over a repository's settings opens the 壞了 card
    #[test]
    fn a_collect_refused_over_repo_settings_is_told_apart_from_a_git_failure() {
        use super::collect_refused_unsafe;
        assert!(collect_refused_unsafe("repo_unsafe: .git/commondir"));
        // L1: 0eed7's real entry, a seat pointing its own copy's .git elsewhere
        assert!(collect_refused_unsafe(
            crate::seat_worktree::COPY_HEAD_UNREADABLE
        ));
        assert!(!collect_refused_unsafe("git worktree remove: exit 128"));
        assert!(!collect_refused_unsafe("internal"));
    }

    #[test]
    fn a_seat_says_whose_folder_or_tree_it_works_in() {
        use super::folder_owner;
        let is_seat = |id: &str| id == "seat_aaaa1111" || id == "seat_bbbb2222";
        let none = |_: &std::path::Path| None;
        let w = "/d/seat-work";
        assert_eq!(
            folder_owner(
                &format!("{w}/seat_aaaa1111"),
                "seat_bbbb2222",
                &is_seat,
                &none
            ),
            Some(serde_json::json!({"seat": "aaaa1", "how": "folder"})),
            "reason: sharer_not_told"
        );
        assert_eq!(
            folder_owner(
                &format!("{w}/seat_aaaa1111"),
                "seat_aaaa1111",
                &is_seat,
                &none
            ),
            None,
            "reason: own_folder_told"
        );
        let tree = |p: &std::path::Path| {
            (p == std::path::Path::new("/d/seat-work/wt01/aibd-base.json")).then(|| {
                r#"{"base":"/d/seat-work/seat_aaaa1111","author":"seat_aaaa1111"}"#.to_owned()
            })
        };
        assert_eq!(
            folder_owner(&format!("{w}/wt01/repo"), "seat_cccc3333", &is_seat, &tree),
            Some(serde_json::json!({"seat": "aaaa1", "how": "tree"})),
            "reason: tree_owner_not_told"
        );
        // a registered repo's tree: the gitdir is the user's repo, no seat owns it
        let repo = |_: &std::path::Path| Some("gitdir: /Users/u/proj/.git/worktrees/x".to_owned());
        assert_eq!(
            folder_owner(&format!("{w}/wt02/proj"), "seat_c", &is_seat, &repo),
            None
        );
        assert_eq!(
            folder_owner("/elsewhere/seat_zzzz", "seat_c", &is_seat, &none),
            None
        );
    }

    // #869 rust (windows-latest) round 2: `work_path` joins with the platform's separator, so on
    // Windows this macOS case read `…CommandLineTools\\usr\\bin:…`. The seat's PATH is only
    // rewritten when `cfg!(target_os = "macos")` (seat open, above), and the case is spelled in
    // Unix paths with `:` between entries, so it is a Unix-only test.
    #[cfg(unix)]
    #[test]
    fn a_work_seats_git_skips_the_xcrun_stub_when_a_developer_git_is_there() {
        use super::work_path;
        let clt = std::path::Path::new("/Library/Developer/CommandLineTools");
        let has = |f: &std::path::Path| f == clt.join("usr/bin/git");
        assert_eq!(
            work_path("/usr/bin:/bin", Some(clt), &has).as_deref(),
            Some("/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin"),
            "reason: developer_git_not_first"
        );
        // already first, no developer folder, no git there: left alone
        assert_eq!(
            work_path(
                "/Library/Developer/CommandLineTools/usr/bin:/bin",
                Some(clt),
                &has
            ),
            None
        );
        assert_eq!(work_path("/usr/bin", None, &has), None);
        assert_eq!(
            work_path("/usr/bin", Some(clt), &|_| false),
            None,
            "reason: no_git"
        );
    }

    /// S89-talk-1 (§驗收刀: one more server or a third tool ⇒ red): the talk argv names one
    /// MCP server, aibd's own, and exactly the two tools.
    /// 5d33a PREREAD 114ee0ac1 L1: a work seat's gate is in its sandbox settings, which keep
    /// every sandbox key; argv without them is left alone.
    #[test]
    fn a_work_seats_gate_rides_in_its_sandbox_settings() {
        let mut argv = aib_seatopen::pipe_argv(
            "claude",
            "work",
            None,
            "0f1e2d3c-4b5a-4968-8776-655443322110",
        )
        .unwrap();
        let before: Vec<serde_json::Value> = argv
            .windows(2)
            .filter(|w| w[0] == "--settings")
            .filter_map(|w| serde_json::from_str(&w[1]).ok())
            .collect();
        assert_eq!(before.len(), 1, "reason: one_sandbox_settings {argv:?}");
        let gate = gate_settings("/opt/aibd", "/var/aibd");
        assert!(fold_gate_into_work_settings(&mut argv, &gate));
        let after: Vec<serde_json::Value> = argv
            .windows(2)
            .filter(|w| w[0] == "--settings")
            .filter_map(|w| serde_json::from_str(&w[1]).ok())
            .collect();
        assert_eq!(after.len(), 1, "reason: second_settings");
        assert_eq!(
            after[0]["sandbox"], before[0]["sandbox"],
            "reason: sandbox_changed"
        );
        let g: serde_json::Value = serde_json::from_str(&gate).unwrap();
        assert_eq!(after[0]["hooks"], g["hooks"], "reason: gate_not_folded");
        for k in before[0].as_object().unwrap().keys() {
            assert_eq!(after[0][k], before[0][k], "reason: key_changed {k}");
        }
        // a seat without sandbox settings: nothing added, nothing changed
        let other = r#"{"permissions":{}}"#;
        let mut plain = vec!["--settings".to_owned(), other.to_owned()];
        assert!(!fold_gate_into_work_settings(&mut plain, &gate));
        assert_eq!(plain[1], other, "reason: folded_into_other_settings");
    }

    #[test]
    fn a_seat_gets_aibds_one_server_and_its_two_tools_only() {
        let dir = tempfile::tempdir().unwrap();
        let p = keep_talk_config(dir.path(), "u-1", "/opt/aibd", "/var/aibd data").unwrap();
        assert_ne!(
            p,
            talk_config_path(dir.path(), "u-2"),
            "reason: one_file_for_two_starts"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&p).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "reason: mcp_config_not_owner_only");
        }
        // c19: off by default, the argv is the talk config and the tools only
        assert_eq!(
            talk_args_with(p.to_str().unwrap(), "safe", false).len(),
            4,
            "reason: gate_on_by_default"
        );
        let a = talk_args_with(p.to_str().unwrap(), "safe", true);
        assert_eq!(a.len(), 6);
        // c19: the gate's settings file, beside the talk config, owner only, hooks only
        assert_eq!(a[4], "--settings", "reason: no_gate_settings");
        let g = gate_settings_path(&p);
        assert_eq!(a[5], g.to_str().unwrap());
        let gs: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&g).unwrap()).unwrap();
        assert_eq!(
            gs.as_object().unwrap().len(),
            1,
            "reason: gate_settings_extra"
        );
        for ev in ["Stop", "SessionStart"] {
            let cmd = gs["hooks"][ev][0]["hooks"][0]["command"]
                .as_str()
                .unwrap_or("");
            assert!(
                cmd.contains("/opt/aibd")
                    && cmd.contains(" ctl now-gate --data-dir ")
                    && cmd.contains("aibd data"),
                "reason: gate_hook_missing {ev} {cmd}"
            );
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&g).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "reason: gate_settings_not_owner_only");
            // a data dir with a space stays one word for the hook's shell
            assert!(
                gs["hooks"]["Stop"][0]["hooks"][0]["command"]
                    .as_str()
                    .unwrap()
                    .ends_with("--data-dir '/var/aibd data'"),
                "reason: gate_path_split"
            );
        }
        assert_eq!(a[0], "--mcp-config");
        assert_eq!(a[1], p.to_str().unwrap(), "reason: config_inline_not_file");
        let c: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        let servers = c["mcpServers"].as_object().unwrap();
        assert_eq!(servers.len(), 1, "reason: extra_mcp_server");
        assert_eq!(c["mcpServers"]["aibd"]["command"], "/opt/aibd");
        assert_eq!(
            c["mcpServers"]["aibd"]["args"],
            serde_json::json!(["mcp", "--data-dir", "/var/aibd data"]),
            "reason: mcp_reaches_another_aibd"
        );
        assert_eq!(a[2], "--allowedTools");
        assert_eq!(
            a[3],
            "mcp__aibd__seat_list,mcp__aibd__seat_send,mcp__aibd__seat_inbox,mcp__aibd__seat_done,\
             mcp__aibd__thread_list,mcp__aibd__thread_view,mcp__aibd__thread_open,mcp__aibd__thread_send,\
             mcp__aibd__ask_person,mcp__aibd__plan_note,mcp__aibd__seat_now,mcp__aibd__seat_tree,mcp__aibd__seat_handoff",
            "reason: extra_tool_allowed"
        );
        // W1: a work seat's list rides in the same flag, once
        let w = talk_args_with(p.to_str().unwrap(), "work", true);
        assert_eq!(w.iter().filter(|x| *x == "--allowedTools").count(), 1);
        assert!(w[3].starts_with(&a[3]), "reason: talk_tools_lost");
        assert!(
            w[3].ends_with(",Bash(node --test *)"),
            "reason: work_list_missing {}",
            w[3]
        );
        assert!(!a[3].contains("Bash"), "reason: safe_seat_gets_work_tools");
        // 第 4 件 (5d33a L3): the receipt tool is a work / dgrs seat's, never a safe seat's
        assert!(
            !a[3].contains("seat_receipt"),
            "reason: safe_seat_gets_receipt_tool"
        );
        assert!(
            !a[3].contains("pending_"),
            "reason: safe_seat_gets_pending_tools"
        );
        assert!(
            w[3].contains(",mcp__aibd__pending_list,mcp__aibd__pending_read,"),
            "reason: work_seat_lacks_pending_tools {}",
            w[3]
        );
        assert!(
            w[3].contains(",mcp__aibd__seat_receipt,"),
            "reason: work_seat_lacks_receipt_tool {}",
            w[3]
        );
        let d = talk_args_with(p.to_str().unwrap(), "dgrs", true);
        assert!(
            d[3].ends_with(",mcp__aibd__seat_receipt"),
            "reason: dgrs_seat_lacks_receipt_tool {}",
            d[3]
        );
        // c19: a work seat's sandbox --settings is not followed by a second one
        assert!(
            !w.iter().any(|x| x == "--settings"),
            "reason: work_settings_shadowed"
        );
    }

    /// N-ws-ask-codex: a codex seat gets aibd's server as `-c` settings: the same tools as a
    /// claude seat and no other, the token passed by name (never its value), every value a
    /// quoted string or array (a Windows path's backslashes and a space stay one value), no
    /// claude flag.
    #[test]
    fn a_codex_seat_gets_the_talk_tools_as_settings() {
        let a = codex_talk_args("C:\\Program Files\\aibd\\aibd.exe", "/var/aibd data");
        assert!(
            a.chunks(2).all(|p| p[0] == "-c"),
            "reason: not_settings {a:?}"
        );
        let set: std::collections::BTreeMap<&str, serde_json::Value> = a
            .chunks(2)
            .map(|p| {
                let (k, v) = p[1].split_once('=').unwrap();
                (k, serde_json::from_str(v).unwrap())
            })
            .collect();
        assert_eq!(
            set["mcp_servers.aibd.command"], "C:\\Program Files\\aibd\\aibd.exe",
            "reason: command_mangled"
        );
        assert_eq!(
            set["mcp_servers.aibd.args"],
            serde_json::json!(["mcp", "--data-dir", "/var/aibd data"]),
            "reason: args_mangled"
        );
        assert_eq!(
            set["mcp_servers.aibd.env_vars"],
            serde_json::json!([crate::seat_token::ENV]),
            "reason: token_not_passed"
        );
        let tools: Vec<String> = TALK_TOOLS
            .iter()
            .map(|t| t.trim_start_matches("mcp__aibd__").to_owned())
            .collect();
        assert_eq!(
            set["mcp_servers.aibd.enabled_tools"],
            serde_json::json!(tools),
            "reason: tools_differ"
        );
        assert!(
            set["mcp_servers.aibd.enabled_tools"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("ask_person")),
            "reason: no_ask_person"
        );
        assert_eq!(
            set["mcp_servers.aibd.default_tools_approval_mode"], "approve",
            "reason: tools_wait_for_approval"
        );
        assert_eq!(set.len(), 5, "reason: extra_setting {a:?}");
        assert!(
            !a.iter().any(|w| w.starts_with("--")),
            "reason: claude_flag {a:?}"
        );
    }

    /// S89-talk-1: a seat token lists only its own cabin, itself marked.
    #[test]
    fn a_seat_sees_only_the_seats_of_its_own_cabin() {
        let rows = vec![
            serde_json::json!({"seat_id": "seat_a", "cabin": null}),
            serde_json::json!({"seat_id": "seat_b", "cabin": null}),
            serde_json::json!({"seat_id": "seat_c", "cabin": "other"}),
        ];
        let live = ["seat_b".to_string(), "seat_c".to_string()];
        let v = same_cabin(rows.clone(), "seat_a", &live);
        let ids: Vec<&str> = v.iter().map(|r| r["seat_id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["seat_a", "seat_b"], "reason: other_cabin_listed");
        assert_eq!(v[0]["me"], true);
        assert_eq!(v[1]["me"], false);
        assert_eq!(v[1]["code"], "b", "the seat code seat_send takes");
        assert!(same_cabin(rows.clone(), "seat_gone", &live).is_empty());
        // 519d0 L2: a stopped seat is not listed (itself always is)
        let v = same_cabin(rows, "seat_a", &[]);
        let ids: Vec<&str> = v.iter().map(|r| r["seat_id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["seat_a"], "reason: stopped_seat_listed");
    }

    use super::*;

    /// B4-1b-2b K5: the provider is told which seat and which card, by env (IFACE §4).
    #[test]
    fn a_seat_s_provider_gets_its_seat_and_card_by_env() {
        let card = PersonaCard {
            uid: "mnnftg".into(),
            name: "AIB 2.0 主軸統籌".into(),
            persona_sha: "a".repeat(64),
            text_path: None,
        };
        let env = seat_env("seat_0123", &card);
        let get = |k: &str| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(get("AIB_SEAT_ID"), Some("seat_0123"));
        assert_eq!(get("AIB_PERSONA_UID"), Some("mnnftg"));
        assert_eq!(get("AIB_PERSONA_SHA"), Some("a".repeat(64).as_str()));
        assert_eq!(env.len(), 3, "the card text never goes in");
    }

    /// The conversation id is a version 4 uuid that `pipe_argv` takes.
    #[test]
    #[allow(clippy::unwrap_used)] // test code: a panic is the failure report
    fn a_new_session_uuid_is_a_v4_uuid() {
        let a = new_uuid().unwrap();
        assert_eq!(a.len(), 36);
        assert_eq!(&a[14..15], "4");
        assert!("89ab".contains(&a[19..20]));
        assert!(aib_seatopen::pipe_argv("claude", "safe", None, &a).is_ok());
        assert_ne!(a, new_uuid().unwrap());
    }

    /// 519d0 IFACE r5: cwd is absolute on both platforms (Windows drive letter and UNC).
    #[test]
    fn a_cwd_is_an_absolute_path_on_this_platform() {
        assert!(!cwd_shape("rel/dir"));
        assert!(!cwd_shape(""));
        assert!(!cwd_shape("/tmp/a\nb"), "no control characters");
        #[cfg(unix)]
        {
            assert!(cwd_shape("/home/u/work"));
            assert!(
                !cwd_shape(r"C:\Users\u"),
                "a drive path is relative on unix"
            );
        }
        #[cfg(windows)]
        {
            assert!(cwd_shape(r"C:\Users\u\work"), "drive letter");
            assert!(cwd_shape(r"\\server\share\work"), "UNC");
            assert!(!cwd_shape(r"C:work"), "relative to the drive's folder");
            assert!(!cwd_shape(r"\work"), "the current drive's root");
        }
        let long = format!("{}{}", std::env::temp_dir().display(), "a".repeat(MAX_CWD));
        assert!(!cwd_shape(&long), "over MAX_CWD");
    }

    fn raw(cwd: &str) -> RawOpen {
        RawOpen {
            persona_uid: "mnnftg".into(),
            provider: "claude".into(),
            mode: "dgrs".into(),
            model: None,
            cabin: None,
            axis_id: None,
            cwd: Some(cwd.into()),
            new: false,
            dry_run: false,
            brain_route: None,
            tree: false,
            join_nodes_of: None,
            join_role: "observer",
            carry: Vec::new(),
            handoff: None,
            mempack: None,
        }
    }

    /// 519d0 IFACE r5: a cwd that is missing or is a file is refused with reason `cwd`.
    #[test]
    #[allow(clippy::unwrap_used)] // test code: a panic is the failure report
    fn the_body_check_reads_the_cwd_shape_not_the_disk() {
        // whether the folder is there is asked after a retry is answered (043dd #759 L1);
        // the open handler refuses a missing folder or a file (tests/seats_open.rs)
        let d = tempfile::tempdir().unwrap();
        let file = d.path().join("a-file");
        std::fs::write(&file, "x").unwrap();
        let reason = |p: &std::path::Path| check_open(&raw(p.to_str().unwrap())).err().map(|e| e.0);
        assert_eq!(reason(d.path()), None);
        assert_eq!(reason(&d.path().join("missing")), None);
        assert_eq!(reason(&file), None);
        assert_eq!(
            check_open(&raw("relative/work")).err().map(|e| e.0),
            Some("cwd")
        );
    }

    /// S89-live-workdir: the settings a provider would load from the folder or above it are
    /// found (up to, not including, the home folder whose `.claude` is the account's).
    #[test]
    fn inside_a_preview_the_handed_real_home_is_the_home() {
        // 0eed7 VERIFY-realentry-79c88372 H: HOME is the preview's stand-in, the real home
        // (with the account's .claude) was handed as AIBD_SEAT_HOME
        let real = tempfile::tempdir().unwrap();
        let fake = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(real.path().join(".claude")).unwrap();
        let work = real.path().join("data").join("seat-work").join("seat_1");
        std::fs::create_dir_all(&work).unwrap();
        let env = |preview: bool| {
            let (r, f) = (
                real.path().display().to_string(),
                fake.path().display().to_string(),
            );
            move |k: &str| match k {
                "AIBD_PREVIEW" if preview => Some("1".to_owned()),
                "AIBD_SEAT_HOME" => Some(r.clone()),
                "HOME" => Some(f.clone()),
                _ => None,
            }
        };
        let home = home_dir(env(true));
        assert_eq!(
            home.as_deref(),
            Some(real.path()),
            "reason: preview_took_the_stand_in"
        );
        assert_eq!(
            project_settings_in(&work, home.as_deref()),
            None,
            "reason: real_home_claude_refused"
        );
        // outside a preview the handed value is not read
        assert_eq!(
            home_dir(env(false)).as_deref(),
            Some(fake.path()),
            "reason: seat_home_read_outside_preview"
        );
        // only an absolute one
        assert_eq!(
            home_dir(|k: &str| match k {
                "AIBD_PREVIEW" => Some("1".into()),
                "AIBD_SEAT_HOME" => Some("rel/home".into()),
                "HOME" => Some(fake.path().display().to_string()),
                _ => None,
            })
            .as_deref(),
            Some(fake.path()),
            "reason: relative_seat_home_taken"
        );
    }

    #[test]
    fn a_claude_folder_holding_only_claudes_own_trail_is_no_project_settings() {
        let home = tempfile::tempdir().unwrap();
        let work = home.path().join("seat-work").join("seat_a");
        let dot = work.join(".claude");
        std::fs::create_dir_all(dot.join(".cc-writes")).unwrap();
        // what claude leaves after a turn of work: the seat reopens there (0eed7 f68d3048 M1)
        assert_eq!(project_settings_in(&work, Some(home.path())), None);
        // anything claude would load next to it still refuses
        std::fs::write(dot.join("settings.json"), "{}").unwrap();
        assert_eq!(
            project_settings_in(&work, Some(home.path())),
            Some(std::fs::canonicalize(&work).unwrap().join(".claude"))
        );
        std::fs::remove_file(dot.join("settings.json")).unwrap();
        // an empty one, or a .claude that is a file, is not taken as the trail
        std::fs::remove_dir_all(dot.join(".cc-writes")).unwrap();
        assert!(
            project_settings_in(&work, Some(home.path())).is_some(),
            "empty"
        );
        std::fs::remove_dir_all(&dot).unwrap();
        std::fs::write(&dot, "x").unwrap();
        assert!(
            project_settings_in(&work, Some(home.path())).is_some(),
            "file"
        );
    }

    #[test]
    fn project_settings_in_the_folder_or_above_are_found_up_to_home() {
        let home = tempfile::tempdir().unwrap();
        let repo = home.path().join("repo");
        let sub = repo.join("crates").join("x");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::create_dir_all(home.path().join(".claude")).unwrap();
        std::fs::write(home.path().join(".claude/settings.json"), "{}").unwrap();
        assert_eq!(project_settings_in(&sub, Some(home.path())), None);
        // a project's .claude folder, whatever it holds (agents / commands / skills)
        std::fs::create_dir_all(repo.join(".claude/agents")).unwrap();
        assert_eq!(
            project_settings_in(&sub, Some(home.path())),
            Some(std::fs::canonicalize(&repo).unwrap().join(".claude"))
        );
        std::fs::remove_dir_all(repo.join(".claude")).unwrap();
        // the home folder's own CLAUDE.md is loaded too (0eed7 N1)
        std::fs::write(home.path().join("CLAUDE.md"), "x").unwrap();
        assert_eq!(
            project_settings_in(&sub, Some(home.path())),
            Some(
                std::fs::canonicalize(home.path())
                    .unwrap()
                    .join("CLAUDE.md")
            )
        );
        std::fs::remove_file(home.path().join("CLAUDE.md")).unwrap();
        for f in PROJECT_SETTINGS {
            let p = repo.join(f);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(&p, "x").unwrap();
            assert_eq!(
                project_settings_in(&sub, Some(home.path())),
                Some(std::fs::canonicalize(&p).unwrap()),
                "{f}"
            );
            std::fs::remove_file(&p).unwrap();
        }
        // a link elsewhere that leads into the project is searched where it leads
        #[cfg(unix)]
        {
            std::fs::write(repo.join("CLAUDE.md"), "x").unwrap();
            let elsewhere = tempfile::tempdir().unwrap();
            let link = elsewhere.path().join("x");
            std::os::unix::fs::symlink(&sub, &link).unwrap();
            assert_eq!(
                project_settings_in(&link, Some(home.path())),
                Some(std::fs::canonicalize(&repo).unwrap().join("CLAUDE.md"))
            );
            std::fs::remove_file(repo.join("CLAUDE.md")).unwrap();
        }
        assert_eq!(
            seat_home(std::path::Path::new("/d/sessions"), "seat_1"),
            std::path::Path::new("/d/seat-work/seat_1")
        );
    }

    fn h(vals: &[&str]) -> HeaderMap {
        let mut m = HeaderMap::new();
        for v in vals {
            m.append(
                IDEMPOTENCY_HEADER,
                HeaderValue::from_str(v).unwrap_or(HeaderValue::from_static("x")),
            );
        }
        m
    }

    #[test]
    fn names() {
        assert!(valid_name("dev"));
        assert!(valid_name("主線核心架構師"));
        assert!(valid_name(&"字".repeat(MAX_NAME)));
        assert!(!valid_name(&"字".repeat(MAX_NAME + 1)));
        assert!(!valid_name(""));
        assert!(!valid_name("   "));
        assert!(!valid_name("a\nb"));
        assert!(!valid_name("a\u{7}"));
    }

    #[test]
    fn keys() {
        assert_eq!(idempotency_key(&HeaderMap::new()).ok(), Some(None));
        assert_eq!(
            idempotency_key(&h(&["abc-1_2.3:4"])).ok(),
            Some(Some("abc-1_2.3:4".into()))
        );
        assert!(idempotency_key(&h(&["a", "b"])).is_err(), "twice");
        assert!(idempotency_key(&h(&["a b"])).is_err(), "space");
        assert!(idempotency_key(&h(&["a/b"])).is_err(), "slash");
        assert!(
            idempotency_key(&h(&[&"k".repeat(MAX_KEY + 1)])).is_err(),
            "long"
        );
        assert!(idempotency_key(&h(&[&"k".repeat(MAX_KEY)])).is_ok());
    }

    #[test]
    fn a_new_seat_is_created() {
        assert_eq!(state_str(SeatState::Created), "CREATED");
    }

    /// M9b: each seat on the page's list carries how many of its messages to the person wait
    /// (read ones, ones to another person, ones the person sent do not count).
    #[test]
    fn the_seat_list_shows_what_each_seat_left_unread_for_the_person() {
        let fact = |from: &str, to: &str, status: &str| {
            let mut item = serde_json::Map::new();
            item.insert("status".into(), serde_json::json!(status));
            aib_store::MessageFact {
                from_key: from.into(),
                to_key: to.into(),
                created: "2026-11-18T05:00:00Z".into(),
                created_unix: 0,
                item,
            }
        };
        let me = "human:00000000000000aa";
        let msgs = vec![
            fact("seat:s1", me, "delivered"),
            fact("seat:s1", me, "queued"),
            fact("seat:s1", me, "read"),
            fact("seat:s1", "human:00000000000000bb", "delivered"),
            fact(me, "seat:s1", "delivered"),
        ];
        let mut v = vec![
            serde_json::json!({"seat_id": "s1"}),
            serde_json::json!({"seat_id": "s2"}),
        ];
        with_unread(&mut v, &msgs, me);
        assert_eq!(v[0]["unread"], 2, "reason: unread_not_counted");
        assert_eq!(v[1]["unread"], 0);
    }

    #[test]
    fn a_preview_opens_safe_seats_only() {
        assert!(preview_refuses("dgrs", true));
        assert!(!preview_refuses("safe", true));
        assert!(
            !preview_refuses("work", true),
            "reason: work_refused_in_preview"
        );
        assert!(preview_refuses("wrok", true), "reason: unknown_mode_let_in");
        assert!(!preview_refuses("dgrs", false));
    }

    /// 5d33a M1 on ecf54f91d / CHECK r2 §4: a work seat's folder is under <data>/seat-work by its
    /// real path (a link there is refused); its git gets aibd's rules.
    #[test]
    #[allow(clippy::unwrap_used)] // test code: a panic is the failure report
    fn a_work_seat_folder_is_under_seat_work_and_not_a_link() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("seat-work");
        let hooks = d.path().join("seat-git").join("hooks");
        #[cfg(unix)]
        {
            let elsewhere = d.path().join("elsewhere");
            std::fs::create_dir_all(&elsewhere).unwrap();
            std::fs::create_dir_all(&root).unwrap();
            std::os::unix::fs::symlink(&elsewhere, root.join("ln")).unwrap();
            assert_eq!(
                work_cwd_ok(&root.join("ln").display().to_string(), &root),
                Err("work_cwd_link_or_missing"),
                "reason: link_followed"
            );
        }
        // a work seat given a path: under the root only
        let inside = root.join("s1");
        std::fs::create_dir_all(&inside).unwrap();
        let s = |p: &std::path::Path| p.display().to_string();
        assert_eq!(work_cwd_ok(&s(&inside), &root), Ok(()));
        assert_eq!(
            work_cwd_ok(&s(d.path()), &root),
            Err("work_cwd_outside_root"),
            "reason: outside_taken"
        );
        assert_eq!(work_cwd_ok(&s(&root), &root), Err("work_cwd_outside_root"));
        let dotdot = inside.join("..").join("..");
        assert_eq!(
            work_cwd_ok(&s(&dotdot), &root),
            Err("work_cwd_outside_root")
        );
        let env = work_git_env(&hooks);
        let get = |k: &str| env.iter().find(|(a, _)| a == k).map(|(_, v)| v.clone());
        assert_eq!(get("GIT_CONFIG_VALUE_1").as_deref(), Some("never"));
        assert_eq!(get("GIT_CONFIG_VALUE_2"), Some(hooks.display().to_string()));
    }

    /// 0eed7 VERIFY-mom-preview-entry r1: the seat could write and test but not commit, its
    /// folder was no repository and `git init` is not on its list.
    #[cfg(unix)]
    #[test]
    fn a_work_seats_folder_is_a_repository_with_one_commit_by_the_seat() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("seat_1");
        std::fs::create_dir_all(&dir).unwrap();
        let mut env = work_git_env(&d.path().join("hooks"));
        env.extend(crate::work_audit::git_identity("seat_1abcdef", "作者"));
        env.push(("HOME".into(), d.path().display().to_string()));
        assert_eq!(init_work_repo(&dir, &env), Ok(true), "reason: not_made");
        let log = |fmt: &str| {
            let o = std::process::Command::new("git")
                .args(["log", &format!("--format={fmt}")])
                .current_dir(&dir)
                .output()
                .unwrap();
            String::from_utf8_lossy(&o.stdout).trim().to_owned()
        };
        assert_eq!(log("%an").lines().count(), 1, "reason: not_one_commit");
        assert!(
            log("%an").starts_with("作者"),
            "reason: not_the_seat {}",
            log("%an")
        );
        // a second seat in the same folder, or a revive: nothing is redone
        assert_eq!(init_work_repo(&dir, &env), Ok(false), "reason: made_twice");
        assert_eq!(log("%H").lines().count(), 1, "reason: second_commit");
    }
}
