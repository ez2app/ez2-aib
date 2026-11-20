//! Request guard applied to every route (ADR 0003). Order matters:
//! Host (DNS rebinding) -> Origin -> token-in-URL -> bearer token.
//!
//! Since pairing (ADR 0006 §7) the bearer is either the start-up token or a page
//! credential. A page credential never reaches `/api/v1/pair/codes*` (start-up token
//! only), and until it is confirmed it reaches only pair/status, pair/revoke and health.
//! `/api/v1/pair/redeem` accepts only aibd's own origin; every other path accepts aibd's own
//! origin too (the pages it serves post from it; 519d0 11-18 09:3x, 0eed7 VERIFY-workspace H1)
//! and the configured `AIBD_ALLOWED_ORIGINS`; `Origin: null` (the P2 local-file form) is
//! refused everywhere until P2 is built (043dd A4 L1).

use crate::pairing::{PageAccess, Pairing};
use crate::token::Token;
use aib_core::ErrorCode;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use std::sync::Arc;

pub const HEALTH: &str = "/api/v1/health";
pub const EVENTS: &str = "/api/v1/events";
/// S89-events-push (ADR 0003): the same events as SSE.
pub const EVENTS_STREAM: &str = "/api/v1/events/stream";
pub const METRICS: &str = "/api/v1/metrics";
/// S79-p11c (Windows acceptance 8, ADR 0005): does this machine have thermal protection.
pub const THERMAL: &str = "/api/v1/thermal";
pub const SEATS: &str = "/api/v1/seats";
pub const SEATS_OPEN: &str = "/api/v1/seats/open";
/// S89-input: a person acts on a seat (only `seat_input` for now).
pub const ACT: &str = "/api/v1/act";
/// S89-talk-2b: the contact center's reads (writes are act types).
pub const CONTACTS: &str = "/api/v1/contacts";
pub const FLOWS: &str = "/api/v1/flows";
/// N-workspace (7b81e): what this aibd can do, the host box, the progress tree (read only).
pub const VERSION: &str = "/api/v1/version";
/// The persona cards a seat can be opened with (N-workspace wave 3, the page's add-a-seat).
pub const PERSONAS: &str = "/api/v1/personas";
/// N-ws-open-bind: the roles and second brains a page picks when it adds a seat.
pub const ROLES: &str = "/api/v1/roles";
pub const BRAINS: &str = "/api/v1/brains";
pub const BRAIN_CANDIDATES: &str = "/api/v1/brains/candidates";
pub const HOST: &str = "/api/v1/host";
pub const TREE: &str = "/api/v1/tree";
/// N-workspace second wave (7b81e): node × seat × role.
pub const ASSIGNMENTS: &str = "/api/v1/assignments";
pub const NODE_ASSIGNMENTS: &str = "/api/v1/nodes/{id}/assignments";
pub const NODE: &str = "/api/v1/nodes/{id}";
/// N-workspace-c4 A: aib-plan's writes while aibd holds the tree (seat token or start-up token).
pub const NODES_PLAN: &str = "/api/v1/nodes/plan";
pub const HOSTS: &str = "/api/v1/hosts";
/// N-workspace 第四波 (SPEC §15): one node's tokens, per seat per hour (the board's own count).
pub const NODE_USAGE: &str = "/api/v1/nodes/{id}/usage";
/// §23 上預覽 (a9136 N-pv-up): an item's tree with each 支點's light.
pub const NODE_PREVIEW_TREE: &str = "/api/v1/nodes/{id}/preview-tree";
/// The same tree in 043dd's page shape (`?node=<id>`).
pub const PREVIEW_UP: &str = "/api/v1/preview_up";
pub const DOC: &str = "/api/v1/doc";
/// §18 search (a9136 IFACE-search-a9136-1118).
pub const SEARCH: &str = "/api/v1/search";
/// §20 / §20b 預覽清單 (a9136 DESIGN-preview-a9136-1118; Andy 23:2x 不能寫死一個).
pub const PREVIEW: &str = "/api/v1/previews";
/// `aibd ctl preview swap` (Andy 11-21 06:1x; PREREAD-previewswap): the start-up token only.
pub const PREVIEW_SWAP: &str = "/api/v1/previews/{id}/swap";
/// Inside a preview: its parent asks the running seats for their handoff before a swap.
pub const PREVIEW_HANDOFF: &str = "/api/v1/preview/handoff";
pub const PREFS: &str = "/api/v1/prefs/{name}";
/// N-now: every seat's status line (a page or the start-up token; a seat token cannot read it).
pub const NOW: &str = "/api/v1/now";
/// S89-talk-2a: one thread of the message library.
/// N-workspace: the threads of many (`?node=`).
pub const THREAD_LIST: &str = "/api/v1/threads";
pub const THREADS: &str = "/api/v1/threads/{thread_id}";
/// N-workspace: the event slots (a page or the start-up token; a seat token cannot read them).
pub const SLOTS: &str = "/api/v1/slots";
/// N-workspace usage: every session's context used (a page or the start-up token).
pub const USAGE: &str = "/api/v1/usage";
pub const SEAT: &str = "/api/v1/seats/{id}";
pub const SEAT_SESSIONS: &str = "/api/v1/seats/{id}/sessions";
/// N-web-worktree r3: the reviewer reads the author seat's tree (tree_read).
pub const SEAT_TREE: &str = "/api/v1/seats/{id}/tree";
pub const SEAT_TREE_LOG: &str = "/api/v1/seats/{id}/tree/log";
pub const SEAT_TREE_DIFF: &str = "/api/v1/seats/{id}/tree/diff";
pub const SESSIONS: &str = "/api/v1/sessions";
pub const SESSION: &str = "/api/v1/sessions/{id}";
pub const SESSION_OUTPUT: &str = "/api/v1/sessions/{id}/output";
pub const PAIR_CODES: &str = "/api/v1/pair/codes";
pub const PAIR_CODE: &str = "/api/v1/pair/codes/{id}";
pub const PAIR_CONFIRM: &str = "/api/v1/pair/codes/{id}/confirm";
pub const PAIR_REDEEM: &str = "/api/v1/pair/redeem";
pub const PAIR_STATUS: &str = "/api/v1/pair/status";
pub const PAIR_REVOKE: &str = "/api/v1/pair/revoke";
/// BOUNDARY r3 §3: the owner's finger for a pairing carried over an update, before one action.
pub const PAIR_RECONFIRM: &str = "/api/v1/pair/reconfirm";
/// The code page's box default (SPEC §19b-2): public, read before the page is paired.
pub const PAIR_PREFS: &str = "/api/v1/pair/prefs";
/// SPEC §19f: the code page asks for the code that replaced its expired one (form `code=`).
pub const PAIR_NEXT: &str = "/api/v1/pair/next";
/// A preview's page shows the pass its parent's paired page opened it with (Andy 11-19).
pub const PAIR_PREVIEW: &str = "/api/v1/pair/preview";
/// GAP-repo-register G2 (043dd): a fingerprint ticket's state, so the page tells "not pressed
/// yet" from "refused" without resending the act.
pub const OWNER_TICKET: &str = "/api/v1/owner/tickets/{id}";

/// Every route aibd mounts, and the only list it mounts from (`api::router` walks it;
/// `api::handlers` has no route of its own). The guard skips the bearer check for paths
/// outside `/api/` (static files), so each entry must sit under `/api/`: a route added
/// elsewhere, say `/healthz`, would answer without the token (a9136 A3 L1; tested).
pub const ROUTES: &[&str] = &[
    HEALTH,
    EVENTS,
    EVENTS_STREAM,
    METRICS,
    THERMAL,
    SEATS,
    SEATS_OPEN,
    ACT,
    CONTACTS,
    FLOWS,
    VERSION,
    PERSONAS,
    ROLES,
    BRAINS,
    BRAIN_CANDIDATES,
    HOST,
    TREE,
    ASSIGNMENTS,
    NODE_ASSIGNMENTS,
    NODES_PLAN,
    NODE,
    HOSTS,
    NODE_USAGE,
    NODE_PREVIEW_TREE,
    PREVIEW_UP,
    DOC,
    SEARCH,
    PREVIEW,
    PREVIEW_SWAP,
    PREVIEW_HANDOFF,
    PREFS,
    NOW,
    THREAD_LIST,
    THREADS,
    SLOTS,
    USAGE,
    SEAT,
    SEAT_SESSIONS,
    SEAT_TREE,
    SEAT_TREE_LOG,
    SEAT_TREE_DIFF,
    SESSIONS,
    SESSION,
    SESSION_OUTPUT,
    PAIR_CODES,
    PAIR_CODE,
    PAIR_CONFIRM,
    PAIR_REDEEM,
    PAIR_STATUS,
    PAIR_REVOKE,
    PAIR_RECONFIRM,
    PAIR_PREFS,
    PAIR_NEXT,
    PAIR_PREVIEW,
    OWNER_TICKET,
];

/// Paths reachable without the bearer token (Host/Origin still apply). Redeem is
/// authenticated by the pairing code in its body.
/// Routes answered without any token (the code page runs before pairing).
pub const PUBLIC_PATHS: &[&str] = &[HEALTH, PAIR_REDEEM, PAIR_PREFS, PAIR_NEXT, PAIR_PREVIEW];

/// What an unconfirmed page credential may call (ADR 0006 §7); health is public anyway.
const UNCONFIRMED_PATHS: &[&str] = &[PAIR_STATUS, PAIR_REVOKE];

/// Routes whose path may appear in logs (a subset of `ROUTES`, tested). Anything else is
/// logged as `UNMATCHED_PATH`, so a path like `/api/v1/<token>` never reaches the log.
pub const LOGGED_ROUTES: &[&str] = &[
    HEALTH,
    EVENTS,
    EVENTS_STREAM,
    METRICS,
    THERMAL,
    PAIR_CODES,
    PAIR_REDEEM,
    PAIR_STATUS,
    PAIR_REVOKE,
    PAIR_PREFS,
    PAIR_NEXT,
    PAIR_PREVIEW,
];
pub const UNMATCHED_PATH: &str = "<unmatched>";

/// Query parameter names that look like credentials; any of them is refused.
const CREDENTIAL_PARAMS: &[&str] = &[
    "token",
    "access_token",
    "auth",
    "authorization",
    "bearer",
    "key",
    "api_key",
    "apikey",
];

#[derive(Debug)]
pub struct GuardConfig {
    pub allowed_hosts: Vec<String>,
    pub allowed_origins: Vec<String>,
    pub token: Token,
    /// aibd's own origins (`http://` + each allowed host): the only ones pair/redeem takes, and
    /// taken on every other path too (a page aibd serves posts from them).
    pub own_origins: Vec<String>,
    pub pairing: Arc<Pairing>,
}

/// Who made the request; the guard puts it in the request extensions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Caller {
    /// No bearer (public paths).
    Anonymous,
    /// The start-up token (owner-only token file).
    StartupToken,
    /// A page credential from pairing.
    Page(PageAccess),
    /// S89-talk-1: a seat's own token (its provider's `AIB_SEAT_TOKEN`); the digest only.
    /// It lists the seats of its cabin and sends to one; nothing else.
    Seat(crate::seat_token::Key),
}

impl GuardConfig {
    pub fn new(port: u16, ipv6: bool, allowed_origins: Vec<String>, token: Token) -> Self {
        let mut allowed_hosts = vec![format!("127.0.0.1:{port}"), format!("localhost:{port}")];
        if ipv6 {
            allowed_hosts.push(format!("[::1]:{port}"));
        }
        let own_origins = allowed_hosts
            .iter()
            .map(|h| format!("http://{h}"))
            .collect();
        GuardConfig {
            allowed_hosts,
            allowed_origins,
            token,
            own_origins,
            pairing: Arc::new(Pairing::default()),
        }
    }

    /// Use this pairing state (tests pass one with a fake clock).
    pub fn with_pairing(mut self, pairing: Arc<Pairing>) -> Self {
        self.pairing = pairing;
        self
    }
}

/// Under `/api` (bearer rules, `no-store`); everything else is a static file.
pub fn is_api(path: &str) -> bool {
    path == "/api" || path.starts_with("/api/")
}

/// `/api/v1/seats/<one name>/tree`, `…/tree/log` or `…/tree/diff` (a seat token may read these;
/// tree_read decides whether this seat may read that one).
pub fn is_seat_tree(path: &str) -> bool {
    let Some(rest) = path.strip_prefix("/api/v1/seats/") else {
        return false;
    };
    let Some((id, tail)) = rest.split_once('/') else {
        return false;
    };
    !id.is_empty() && matches!(tail, "tree" | "tree/log" | "tree/diff")
}

pub fn request_id() -> String {
    let mut b = [0u8; 8];
    let _ = getrandom::fill(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

pub fn error_response(code: ErrorCode, message: &str, request_id: &str) -> Response {
    let status =
        StatusCode::from_u16(code.http_status()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = serde_json::json!({
        "code": code.as_str(),
        "message": message,
        "request_id": request_id,
    });
    (
        status,
        [(header::CONTENT_TYPE, "application/json")],
        body.to_string(),
    )
        .into_response()
}

fn single_header(h: &HeaderMap, name: header::HeaderName) -> Result<Option<&str>, ()> {
    let mut it = h.get_all(name).iter();
    match (it.next(), it.next()) {
        (None, _) => Ok(None),
        (Some(v), None) => v.to_str().map(Some).map_err(|_| ()),
        (Some(_), Some(_)) => Err(()), // duplicated header: refuse rather than guess
    }
}

/// `application/x-www-form-urlencoded` decoding: `+` is a space, `%XX` a byte.
/// Invalid escapes are kept literally (the check below still sees them).
pub(crate) fn form_decode(s: &str) -> String {
    fn hex(c: u8) -> Option<u8> {
        match c {
            b'0'..=b'9' => Some(c - b'0'),
            b'a'..=b'f' => Some(c - b'a' + 10),
            b'A'..=b'F' => Some(c - b'A' + 10),
            _ => None,
        }
    }
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' => {
                match (
                    b.get(i + 1).copied().and_then(hex),
                    b.get(i + 2).copied().and_then(hex),
                ) {
                    (Some(h), Some(l)) => {
                        out.push(h * 16 + l);
                        i += 2;
                    }
                    _ => out.push(b'%'),
                }
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query_has_credential(query: &str, token: &Token) -> bool {
    // Names: each key decoded twice (`%74oken`, `%2574oken`), case-insensitive.
    let names_hit = query.split('&').any(|pair| {
        let (k, _) = pair.split_once('=').unwrap_or((pair, ""));
        let k = form_decode(&form_decode(k)).to_ascii_lowercase();
        CREDENTIAL_PARAMS.contains(&k.trim())
    });
    // Values: the token itself, raw, encoded once or twice, anywhere.
    let once = form_decode(query);
    let twice = form_decode(&once);
    let value_hit = [query, once.as_str(), twice.as_str()]
        .iter()
        .any(|q| q.contains(token.expose()));
    names_hit || value_hit
}

/// A live page credential anywhere in the path or the query (each segment / value raw,
/// decoded once and twice). Only 43-character candidates (base64url of 32 bytes) are
/// hashed and looked up, so this costs nothing on ordinary URLs (043dd A4 L5).
fn page_credential_in_url(cfg: &GuardConfig, path: &str, query: Option<&str>) -> bool {
    let parts = path
        .split('/')
        .chain(query.into_iter().flat_map(|q| q.split('&')))
        .flat_map(|p| p.split('='));
    for part in parts {
        let once = form_decode(part);
        let twice = form_decode(&once);
        for cand in [part, once.as_str(), twice.as_str()] {
            if cand.len() == 43 && cfg.pairing.access(cand).is_some() {
                return true;
            }
        }
    }
    false
}

/// The path as it may be logged: a known route, or a fixed placeholder.
pub fn loggable_path(path: &str) -> &'static str {
    LOGGED_ROUTES
        .iter()
        .find(|r| **r == path)
        .copied()
        .unwrap_or(UNMATCHED_PATH)
}

/// Placeholder for any non-standard HTTP method in logs.
pub const OTHER_METHOD: &str = "<other>";

/// The method as it may be logged: a standard method, or a fixed
/// placeholder. A method is an arbitrary token on the wire, so a caller could
/// put a secret there (review by @3f312, 11-07).
pub fn loggable_method(m: &Method) -> &'static str {
    const STANDARD: [Method; 9] = [
        Method::GET,
        Method::POST,
        Method::PUT,
        Method::DELETE,
        Method::PATCH,
        Method::HEAD,
        Method::OPTIONS,
        Method::CONNECT,
        Method::TRACE,
    ];
    STANDARD
        .iter()
        .find(|s| *s == m)
        .map(|s| s.as_str())
        .unwrap_or(OTHER_METHOD)
}

pub async fn guard(
    State(cfg): State<Arc<GuardConfig>>,
    mut req: Request<Body>,
    next: Next,
) -> Response {
    let rid = request_id();
    // never log the raw method, path or query: any of them may carry a secret
    let method = loggable_method(req.method());
    let path = loggable_path(req.uri().path());
    let raw_path = req.uri().path().to_string();
    let verdict = check(&cfg, &req);
    let mut resp = match verdict {
        Err((code, msg)) => error_response(code, msg, &rid),
        Ok(caller) => {
            req.extensions_mut().insert(caller);
            req.extensions_mut().insert(cfg.pairing.clone());
            next.run(req).await
        }
    };
    crate::static_files::api_no_store(&raw_path, &mut resp);
    tracing::info!(request_id = %rid, %method, %path, status = resp.status().as_u16(), "request");
    resp
}

fn check(cfg: &GuardConfig, req: &Request<Body>) -> Result<Caller, (ErrorCode, &'static str)> {
    let h = req.headers();

    let host = single_header(h, header::HOST)
        .map_err(|_| (ErrorCode::HostNotAllowed, "bad Host header"))?
        .ok_or((ErrorCode::HostNotAllowed, "missing Host header"))?;
    if !cfg
        .allowed_hosts
        .iter()
        .any(|a| a.eq_ignore_ascii_case(host))
    {
        return Err((
            ErrorCode::HostNotAllowed,
            "Host is not this loopback service",
        ));
    }

    let path = req.uri().path();
    let origin = single_header(h, header::ORIGIN)
        .map_err(|_| (ErrorCode::OriginNotAllowed, "bad Origin header"))?;
    if path == PAIR_REDEEM || path == PAIR_NEXT || path == PAIR_PREVIEW {
        // W1: the person types the code into a page aibd serves, so the form comes from our
        // own origin. `null` (the P2 local-file form) is not taken until P2 is built: today
        // it would only let any web page's sandboxed frame fill the failure limit (043dd L1)
        let ok = origin.is_some_and(|o| cfg.own_origins.iter().any(|a| a.eq_ignore_ascii_case(o)));
        if !ok {
            return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed"));
        }
    } else {
        let own = |o: &str| cfg.own_origins.iter().any(|x| x.eq_ignore_ascii_case(o));
        match origin {
            // `null` identifies nothing (sandboxed frames, data: pages, any local file)
            Some("null") => return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed")),
            // H1 (0eed7 VERIFY-workspace-0eed7-2436023c): a browser sends Origin on every POST;
            // the pages aibd serves itself come from its own origin and must reach act
            Some(o) if !cfg.allowed_origins.iter().any(|a| a == o) && !own(o) => {
                return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed"))
            }
            _ => {}
        }
    }

    let raw_path = req.uri().path();
    if [
        raw_path.to_string(),
        form_decode(raw_path),
        form_decode(&form_decode(raw_path)),
    ]
    .iter()
    .any(|p| p.contains(cfg.token.expose()))
    {
        return Err((
            ErrorCode::TokenInUrl,
            "credentials must not be sent in the URL",
        ));
    }
    if let Some(q) = req.uri().query() {
        if query_has_credential(q, &cfg.token) {
            return Err((
                ErrorCode::TokenInUrl,
                "credentials must not be sent in the URL",
            ));
        }
    }

    if page_credential_in_url(cfg, raw_path, req.uri().query()) {
        return Err((
            ErrorCode::TokenInUrl,
            "credentials must not be sent in the URL",
        ));
    }

    if path.starts_with("/api/v1/pair/") && req.uri().query().is_some() {
        // pairing codes and credentials travel only in bodies and headers
        return Err((
            ErrorCode::TokenInUrl,
            "credentials must not be sent in the URL",
        ));
    }

    // static files (ADR 0006 §6) are public and identical for every caller
    if PUBLIC_PATHS.contains(&path) || !is_api(path) {
        return Ok(Caller::Anonymous);
    }
    let auth = single_header(h, header::AUTHORIZATION)
        .map_err(|_| (ErrorCode::Unauthorized, "bad Authorization header"))?
        .ok_or((ErrorCode::Unauthorized, "missing bearer token"))?;
    let presented = auth
        .strip_prefix("Bearer ")
        .ok_or((ErrorCode::Unauthorized, "expected Bearer token"))?;
    if cfg.token.matches(presented) {
        return Ok(Caller::StartupToken);
    }
    // S89-talk-1 (DECISION-council-seat-talk §驗收刀: a seat token on pair / seats/open ⇒ 403):
    // a seat may only list seats and send (the handlers narrow both to its cabin)
    if let Some(key) = crate::seat_token::tokens().key(presented) {
        let ok = (path == SEATS && matches!(*req.method(), Method::GET | Method::HEAD))
            // N-workspace-c4 A (5d33a L1): a seat writes the tree as itself through aib-plan
            || (path == NODES_PLAN && *req.method() == Method::POST)
            // N-web-worktree r3: a seat reads another seat's tree (tree_read checks it is the
            // reviewer on a node both are on)
            || (is_seat_tree(path) && matches!(*req.method(), Method::GET | Method::HEAD))
            || (path == ACT && *req.method() == Method::POST);
        if !ok {
            return Err((
                ErrorCode::PageWriteNotAllowed,
                "a seat token may only list its cabin's seats and send to them",
            ));
        }
        return Ok(Caller::Seat(key));
    }
    let Some(page) = cfg.pairing.access(presented) else {
        return Err((ErrorCode::Unauthorized, "wrong token"));
    };
    if path == PAIR_CODES || path.starts_with("/api/v1/pair/codes/") {
        return Err((
            ErrorCode::Unauthorized,
            "this endpoint takes the start-up token only",
        ));
    }
    if !page.confirmed && !UNCONFIRMED_PATHS.contains(&path) {
        return Err((
            ErrorCode::PairingUnconfirmed,
            "confirm the short code in the terminal first",
        ));
    }
    // a9136 A4 M1: a page credential stays weaker than the start-up token. Until the
    // launch-profile routes exist (Q2: the page sends a profile name, never argv), a
    // confirmed page may only read, and revoke itself.
    // S89-input: and type to a seat (`POST /api/v1/act`, seat_input only; the handler checks
    // the kind and the confirmation again).
    let reads = matches!(*req.method(), Method::GET | Method::HEAD);
    // BOUNDARY r3 §3: the re-confirm ask (the handler takes carried pairings only)
    if path == PAIR_RECONFIRM && *req.method() == Method::POST {
        return Ok(Caller::Page(page));
    }
    if !reads && path != PAIR_REVOKE && path != ACT {
        return Err((
            ErrorCode::PageWriteNotAllowed,
            "the page credential can only read for now",
        ));
    }
    Ok(Caller::Page(page))
}
