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
            view_with(s, m, card, Some(dup))
        })
        .collect()
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
    Ok(views(&seats, &metas, &cards))
}

/// The tools a seat may call (S89-talk-1): aibd's own MCP server, these and no other — the
/// one-to-one ones and the threads of many it is a member of (0eed7 overnight 2943d806 M2: a
/// safe seat was refused thread_send, "haven't granted", so a called review seat could not
/// answer).
/// thread_open too (519d0 常駐實跑 M 11-18: told to open a thread, a seat had no tool for it).
pub const TALK_TOOLS: [&str; 9] = [
    "mcp__aibd__seat_list",
    "mcp__aibd__seat_send",
    "mcp__aibd__seat_inbox",
    "mcp__aibd__seat_done",
    "mcp__aibd__thread_list",
    "mcp__aibd__thread_view",
    "mcp__aibd__thread_open",
    "mcp__aibd__thread_send",
    "mcp__aibd__ask_person",
];

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

/// DECISION-council-seat-talk r0 補收 (§S89-talk-1): the config is a file aibd writes, owner
/// only (0600 / the owner's ACL on Windows), holding nothing but aibd itself; the argv names
/// the file, and `--allowedTools` the two tools. The seat token is never in either.
pub fn keep_talk_config(
    private_dir: &std::path::Path,
    start: &str,
    aibd_exe: &str,
    data_dir: &str,
) -> std::io::Result<std::path::PathBuf> {
    let p = talk_config_path(private_dir, start);
    crate::platform::write_private_file(&p, talk_config(aibd_exe, data_dir).as_bytes())?;
    Ok(p)
}

/// The argv for [`talk_config`] kept at `config_path`. A `work` seat (W1) also gets its fixed
/// list ([`aib_seatopen::WORK_ALLOWED`]) in the same, single `--allowedTools`.
pub fn talk_args(config_path: &str, mode: &str) -> Vec<String> {
    let mut allowed: Vec<&str> = TALK_TOOLS.to_vec();
    if mode == "work" {
        allowed.extend(aib_seatopen::WORK_ALLOWED);
    }
    vec![
        "--mcp-config".into(),
        config_path.into(),
        "--allowedTools".into(),
        allowed.join(","),
    ]
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
    let sessions = st.sessions.clone();
    let sid = id.clone();
    match tokio::task::spawn_blocking(move || sessions.archive_seat(&sid, by)).await {
        Ok(Ok(aib_store::SeatArchive::NotFound)) => {
            ApiError::new(ErrorCode::SeatNotFound, "no such seat").into_response()
        }
        Ok(Ok(_)) => get_one(State(st), UrlPath(id)).await,
        Ok(Err(e)) => e.into_response(),
        Err(_) => internal(),
    }
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
    sessions.parent().unwrap_or(sessions).join("seat-work")
}

/// aibd's empty hooks folder for the git a work seat and aibd run there; its parent is the
/// git HOME aibd gives its own git (seat_worktree).
pub fn seat_git_hooks(sessions: &std::path::Path) -> std::path::PathBuf {
    sessions
        .parent()
        .unwrap_or(sessions)
        .join("seat-git")
        .join("hooks")
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

/// A seat's own folder when none is given: `<data>/seat-work/<seat id>`, next to the
/// sessions folder (`sessions`) aibd keeps private.
pub fn seat_home(sessions: &std::path::Path, seat_id: &str) -> std::path::PathBuf {
    sessions
        .parent()
        .unwrap_or(sessions)
        .join("seat-work")
        .join(seat_id)
}

/// Files a provider would load as project instructions or tools from `dir` or any folder above
/// it, the home folder included (0eed7 N1 on 257ac5ab).
pub const PROJECT_SETTINGS: [&str; 4] = [".mcp.json", "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"];

/// A project's `.claude` folder (settings, agents, commands, skills: all loaded at project level,
/// 0eed7 L1 on 257ac5ab) refuses the folder whatever it holds; the home folder's own `.claude` is
/// the account's, left out by --setting-sources project,local.
pub const PROJECT_DIR: &str = ".claude";

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
        if !at_home && d.join(PROJECT_DIR).exists() {
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
    let req: RawOpen = match serde_json::from_slice(&bytes) {
        Ok(r) => r,
        Err(_) => return Refusal(
            "body",
            "body must be {persona_uid, provider, mode, cwd, model?, cabin?, axis_id?, new?, dry_run?}",
        )
        .into_response(),
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
    #[allow(dead_code)]
    role: Option<String>,
    /// Work in the same folder as this seat (0eed7 STUCK-live-in 15:23: the demo's author and
    /// reviewer share one folder). 5d33a CHECK-preview-work-seat r2: only a new `work` seat,
    /// only with a running `work` seat, only the folder aibd made for it
    /// (`<data>/seat-work/<id>`, not a link); the page still never names a path.
    #[serde(default)]
    with_seat: Option<String>,
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
    let mut opened_by = opened_by;
    let cwd = match req.with_seat.as_deref() {
        None => None,
        Some(other) => match shared_folder(&st, &req.mode, other).await {
            Some(c) => {
                // 5d33a r2 5: the event says whose folder it is
                opened_by["with_seat"] = serde_json::json!(seat_code(other));
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
    };
    open_checked(
        st,
        raw,
        Some(format!("page:{}", req.idem_key)),
        Some(opened_by),
    )
    .await
}

/// The folder a page's new seat may share with `other` (5d33a CHECK-preview-work-seat r2):
/// the new seat is `work`; `other` is a running `work` seat (never a dgrs seat's folder, whose
/// files a seat without limits put there); its folder is the one aibd made for it
/// (`<data>/seat-work/<other>`, so a page reaches no folder a terminal chose) and is a real
/// folder now, not a link (checked each open). `None`: any of these is not so.
async fn shared_folder(st: &AppState, mode: &str, other: &str) -> Option<String> {
    if mode != "work" {
        return None;
    }
    if !st.sessions.live_seats().iter().any(|s| s == other) {
        return None;
    }
    let id_ok = other.strip_prefix("seat_").is_some_and(|h| {
        !h.is_empty() && h.len() <= 32 && h.bytes().all(|b| b.is_ascii_hexdigit())
    });
    if !id_ok {
        return None;
    }
    let store = st.store.clone();
    let metas = tokio::task::spawn_blocking(move || store.seat_metas())
        .await
        .ok()?
        .ok()?;
    let m = metas.into_iter().find(|m| m.seat_id == other)?;
    if m.mode.as_deref() != Some("work") {
        return None;
    }
    let own = seat_home(st.sessions.private_dir(), other);
    let real_dir = std::fs::symlink_metadata(&own).is_ok_and(|md| md.file_type().is_dir());
    (real_dir && std::path::Path::new(&m.cwd) == own.as_path()).then_some(m.cwd)
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
async fn side_event(st: &AppState, aggregate: &str, kind: &str, payload: serde_json::Value) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
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
    }
}

/// The open itself, once the request is read: shared by the start-up token's route and the
/// page's act. `opened_by` is put in the event (a page's opens; none for the token).
async fn open_checked(
    st: AppState,
    req: RawOpen,
    key: Option<String>,
    opened_by: Option<serde_json::Value>,
) -> Response {
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
        if let Some(f) = project_settings_in(std::path::Path::new(c), home.as_deref()) {
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
    /// S89-talk-1 (§驗收刀: one more server or a third tool ⇒ red): the talk argv names one
    /// MCP server, aibd's own, and exactly the two tools.
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
        let a = talk_args(p.to_str().unwrap(), "safe");
        assert_eq!(a.len(), 4);
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
             mcp__aibd__ask_person",
            "reason: extra_tool_allowed"
        );
        // W1: a work seat's list rides in the same flag, once
        let w = talk_args(p.to_str().unwrap(), "work");
        assert_eq!(w.iter().filter(|x| *x == "--allowedTools").count(), 1);
        assert!(w[3].starts_with(&a[3]), "reason: talk_tools_lost");
        assert!(
            w[3].ends_with(",Bash(node --test *)"),
            "reason: work_list_missing {}",
            w[3]
        );
        assert!(!a[3].contains("Bash"), "reason: safe_seat_gets_work_tools");
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
