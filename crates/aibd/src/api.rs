//! `/api/v1` routes: health (public); event cursor, metrics, seats and sessions
//! (authenticated); pairing (ADR 0006 §7, see `pair_api`).

use crate::guard::{self, error_response, GuardConfig};
use crate::lineage::Lineage;
use crate::pair_api;
use crate::seats;
use crate::sessions;
use aib_core::ErrorCode;
use aib_store::Store;
use axum::extract::{Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, MethodRouter};
use axum::{Json, Router};
use serde::Deserialize;
use std::sync::Arc;

pub const API_VERSION: &str = "v1";

#[derive(Clone)]
pub struct AppState {
    pub store: Arc<Store>,
    /// M1 lineage, decided once at start-up by the self-check.
    pub lineage: Arc<Lineage>,
    /// Sessions API state (preview.2).
    pub sessions: Arc<crate::sessions::Sessions>,
    /// Who `POST /api/v1/sessions` may start (M1 gate; from `Config::session_policy`).
    pub session_policy: Arc<crate::config::SessionPolicy>,
    /// The thermal guard (R1, dry run); `None` = no sensor on this machine.
    pub thermal: Option<Arc<crate::thermal::Guard>>,
    /// Persona cards (B4-1b-1): names are looked up here when a seat is shown.
    pub personas: Arc<dyn crate::personas::PersonaSource>,
    /// S89-talk-2b: what the person set about each contact, and the page's sliders.
    pub contacts: Arc<crate::contacts::Book>,
    /// S89-talk-2a: the message library (`<data>/messages.sqlite`, its own file).
    pub messages: Arc<aib_store::MessageDb>,
}

impl AppState {
    /// The session policy in force now: the one the page wrote while aibd runs (W-10, kept by
    /// [`crate::sessions::Sessions::set_policy`]), else the one read at start.
    pub fn policy(&self) -> Arc<crate::config::SessionPolicy> {
        self.sessions
            .policy()
            .unwrap_or_else(|| self.session_policy.clone())
    }
}

/// Facts about this aibd process for `GET /api/v1/health` (043dd S3, PLAN A5): `aib web`
/// and the page tell a restarted aibd apart by `instance_id` / `started_at`. Health is
/// public, so nothing here names a path (the data directory stays behind the token).
struct Process {
    started_at: u64,
    instance_id: String,
}

static PROCESS: std::sync::LazyLock<Result<Process, String>> =
    std::sync::LazyLock::new(|| new_process(getrandom::fill));

/// A failed random read is an error, not an id of zeros: zeros repeat on every restart,
/// which is what `instance_id` exists to tell apart (a63ab / 043dd A5 L2).
fn new_process(
    fill: impl FnOnce(&mut [u8]) -> Result<(), getrandom::Error>,
) -> Result<Process, String> {
    let mut b = [0u8; 8];
    fill(&mut b).map_err(|e| format!("instance id: {e}"))?;
    Ok(Process {
        started_at: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0),
        instance_id: b.iter().map(|x| format!("{x:02x}")).collect(),
    })
}

/// aibd calls this before it serves: no instance id, no start (same rule as the token).
pub fn process_ready() -> std::io::Result<()> {
    PROCESS
        .as_ref()
        .map(|_| ())
        .map_err(|e| std::io::Error::other(e.clone()))
}

pub fn router(state: AppState, guard_cfg: Arc<GuardConfig>) -> Router {
    // started_at = when the router was built
    std::sync::LazyLock::force(&PROCESS);
    // only `guard::ROUTES` is mounted: a route cannot be added without that list, and
    // the list is tested to sit under `/api/` (a9136 A3 L1)
    let mut r = Router::new();
    for path in guard::ROUTES {
        if let Some(h) = handlers(path) {
            r = r.route(path, h);
        }
    }
    r.fallback(crate::static_files::fallback)
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(
            guard_cfg,
            guard::guard,
        ))
}

/// The handlers of one `guard::ROUTES` entry (`None` for any other path, which is
/// never mounted).
pub fn handlers(path: &str) -> Option<MethodRouter<AppState>> {
    use guard::{
        ACT, ASSIGNMENTS, BRAINS, BRAIN_CANDIDATES, CONTACTS, DOC, EVENTS, EVENTS_STREAM, FLOWS,
        HEALTH, HOST, HOSTS, METRICS, NODE, NODES_PLAN, NODE_ASSIGNMENTS, NODE_PREVIEW_TREE,
        NODE_USAGE, NOW, OWNER_TICKET, PAIR_CODE, PAIR_CODES, PAIR_CONFIRM, PAIR_NEXT, PAIR_PREFS,
        PAIR_PREVIEW, PAIR_RECONFIRM, PAIR_REDEEM, PAIR_REVOKE, PAIR_STATUS, PERSONAS, PREFS,
        PREVIEW, PREVIEW_HANDOFF, PREVIEW_SWAP, PREVIEW_UP, ROLES, SEARCH, SEAT, SEATS, SEATS_OPEN,
        SEAT_SESSIONS, SEAT_TREE, SEAT_TREE_DIFF, SEAT_TREE_LOG, SESSION, SESSIONS, SESSION_OUTPUT,
        SESSION_POLICY, SLOTS, THERMAL, THREADS, THREAD_LIST, TREE, USAGE, VERSION,
    };
    let nope = sessions::method_not_allowed;
    Some(match path {
        HEALTH => get(health),
        EVENTS => get(events),
        EVENTS_STREAM => get(crate::events_push::stream),
        METRICS => get(metrics),
        THERMAL => get(thermal),
        SEATS => get(seats::list).post(seats::create).fallback(nope),
        SEATS_OPEN => post(seats::open).fallback(nope),
        ACT => post(crate::seat_input::act).fallback(nope),
        CONTACTS => get(crate::contacts_api::list).fallback(nope),
        FLOWS => get(crate::contacts_api::flows).fallback(nope),
        VERSION => get(crate::workspace::version).fallback(nope),
        PERSONAS => get(crate::workspace::personas).fallback(nope),
        ROLES => get(crate::registry::roles).fallback(nope),
        BRAINS => get(crate::registry::brains).fallback(nope),
        BRAIN_CANDIDATES => get(crate::registry::brain_candidates).fallback(nope),
        HOST => get(crate::workspace::host).fallback(nope),
        TREE => get(crate::workspace::tree).fallback(nope),
        ASSIGNMENTS => get(crate::assign::list).fallback(nope),
        NODE_ASSIGNMENTS => get(crate::assign::of_node).fallback(nope),
        NODE => get(crate::workspace::node).fallback(nope),
        NODES_PLAN => post(crate::plan_route::plan).fallback(nope),
        HOSTS => get(crate::hosts::list).fallback(nope),
        NODE_USAGE => get(crate::usage::of_node).fallback(nope),
        NODE_PREVIEW_TREE => get(crate::preview_tree::tree).fallback(nope),
        PREVIEW_UP => get(crate::preview_tree::up_tree).fallback(nope),
        DOC => get(crate::docs::read).fallback(nope),
        SEARCH => get(crate::search::search).fallback(nope),
        PREVIEW => get(crate::preview::status).fallback(nope),
        PREVIEW_SWAP => get(crate::preview_swap::state)
            .post(crate::preview_swap::ask)
            .fallback(nope),
        PREVIEW_HANDOFF => post(crate::preview_swap::handoff).fallback(nope),
        PREFS => get(crate::contacts_api::prefs).fallback(nope),
        NOW => get(crate::seat_now::list).fallback(nope),
        THREAD_LIST => get(crate::threads::list).fallback(nope),
        THREADS => get(crate::messages::thread).fallback(nope),
        SLOTS => get(crate::slots::list).fallback(nope),
        USAGE => get(crate::usage::list).fallback(nope),
        SEAT => get(seats::get_one).delete(seats::archive).fallback(nope),
        SEAT_TREE => get(crate::tree_read::tree).fallback(nope),
        SEAT_TREE_LOG => get(crate::tree_read::log).fallback(nope),
        SEAT_TREE_DIFF => get(crate::tree_read::diff).fallback(nope),
        SEAT_SESSIONS => get(sessions::list_for_seat)
            .post(sessions::create_for_seat)
            .fallback(nope),
        SESSIONS => get(sessions::list).post(sessions::create).fallback(nope),
        SESSION => get(sessions::get_one)
            .delete(sessions::delete_one)
            .fallback(nope),
        SESSION_OUTPUT => get(sessions::output).fallback(nope),
        PAIR_CODES => post(pair_api::create_code).fallback(nope),
        PAIR_CODE => get(pair_api::get_code)
            .delete(pair_api::cancel)
            .fallback(nope),
        PAIR_CONFIRM => post(pair_api::confirm).fallback(nope),
        PAIR_REDEEM => post(pair_api::redeem).fallback(nope),
        PAIR_STATUS => get(pair_api::status).fallback(nope),
        PAIR_REVOKE => post(pair_api::revoke).fallback(nope),
        PAIR_RECONFIRM => post(pair_api::reconfirm).fallback(nope),
        PAIR_PREFS => get(pair_api::prefs).fallback(nope),
        PAIR_NEXT => post(pair_api::next).fallback(nope),
        PAIR_PREVIEW => post(pair_api::preview_pass).fallback(nope),
        OWNER_TICKET => get(crate::owner_ok::ticket_state).fallback(nope),
        SESSION_POLICY => get(crate::session_policy_setup::state).fallback(nope),
        _ => return None,
    })
}

async fn health() -> Json<serde_json::Value> {
    // aibd does not start without it (`process_ready`); a router built without that check
    // answers null, never a made-up id
    let p = PROCESS.as_ref().ok();
    // reads the user's index.html: off the async threads (0eed7 L1 on 3b09b3ba)
    let web_source = tokio::task::spawn_blocking(crate::static_files::web_source)
        .await
        .unwrap_or("none");
    let preview_update = tokio::task::spawn_blocking(crate::preview::preview_update)
        .await
        .ok()
        .flatten();
    let preview_updated = tokio::task::spawn_blocking(crate::preview::preview_updated)
        .await
        .ok()
        .flatten();
    let preview_version = tokio::task::spawn_blocking(crate::preview::preview_version)
        .await
        .unwrap_or((None, None));
    let preview_swaps = tokio::task::spawn_blocking(crate::preview::preview_swaps)
        .await
        .unwrap_or_default();
    Json(serde_json::json!({
        "status": "ok",
        "api_version": API_VERSION,
        // §20: this is a preview build run by another aibd (the page shows 預覽 · 非正式區)
        "preview": crate::preview::is_preview(),
        "preview_name": crate::preview::preview_name(),
        "preview_id": crate::preview::preview_id(),
        // §23b (043dd a9405ae7): {version, behind, nag_secs, since} when a newer build waits
        // for 更新 (N-pv-up P1: from the version record)
        "preview_update": preview_update,
        // §23c: what the last swap brought, for the 「已更新」 line
        "preview_updated": preview_updated,
        "preview_version": preview_version.0,
        "preview_summary": preview_version.1,
        // §23d: the 載入彈窗's 「這次更新」 (who, when, from / to, what)
        "preview_history": crate::preview::history_of(&preview_swaps),
        "preview_swaps": preview_swaps,
        "version": env!("CARGO_PKG_VERSION"),
        "started_at": p.map(|p| p.started_at),
        "instance_id": p.map(|p| p.instance_id.as_str()),
        // S107-userhtml: who serves `/` (user folder / built-in / none)
        "web_source": web_source,
    }))
}

/// S79-p11c (Windows acceptance 8, ADR 0005): whether this machine has thermal protection,
/// for a screen to show. See [`thermal_body`].
async fn thermal(State(st): State<AppState>) -> Json<serde_json::Value> {
    let guard = st.thermal.as_ref().map(|g| g.metrics());
    Json(thermal_body(std::env::consts::OS, guard))
}

/// The `/api/v1/thermal` answer. `supported`:
/// - `true`: the guard runs; `guard` holds its counters (as in `/metrics`);
/// - `false`: a platform with no thermal sensors aibd reads (Windows, Linux): no thermal
///   protection here, sessions are never held back by heat (`state` `unsupported`);
/// - `null`: macOS, but no guard is running (no sensor was readable at start, or its start
///   failed; the log says which) — not claimed as either.
pub fn thermal_body(os: &str, guard: Option<serde_json::Value>) -> serde_json::Value {
    match guard {
        Some(g) => serde_json::json!({"supported": true, "os": os, "state": "on", "guard": g}),
        None if os != "macos" => serde_json::json!({
            "supported": false, "os": os, "state": "unsupported",
            "why": "no thermal protection on this platform (ADR 0005): sessions are not held back by heat"
        }),
        None => serde_json::json!({
            "supported": null, "os": os, "state": "not_running",
            "why": "no thermal guard is running (no sensor read at start, or its start failed: see the log)"
        }),
    }
}

/// Counters for diagnosis. `lineage.selfcheck` is `ok`, `failed:<tag>` or
/// `unsupported`; `lineage.failures` counts M1 reads that failed after
/// start-up (a process being gone is not a failure). `pairing` has the
/// pairing channel and failure / rate-limit totals. `thermal` is the guard's
/// dry-run counters, or `null` when this machine has no sensor.
async fn metrics(
    State(st): State<AppState>,
    pairing: Option<axum::extract::Extension<Arc<crate::pairing::Pairing>>>,
) -> Json<serde_json::Value> {
    let pairing = pairing.map(|p| p.0.metrics());
    let thermal = st.thermal.as_ref().map(|g| g.metrics());
    Json(serde_json::json!({
        "lineage": st.lineage.status(), "pairing": pairing, "thermal": thermal
    }))
}

#[derive(Deserialize)]
struct EventsQuery {
    #[serde(default)]
    cursor: i64,
    limit: Option<u32>,
    aggregate_id: Option<String>,
}

async fn events(State(st): State<AppState>, Query(q): Query<EventsQuery>) -> Response {
    let limit = q.limit.unwrap_or(100).clamp(1, 500);
    let store = st.store.clone();
    let cursor = q.cursor.max(0);
    let filtered = q.aggregate_id.is_some();
    let res = tokio::task::spawn_blocking(move || match q.aggregate_id {
        Some(id) => store.events_for_after(&id, cursor, limit),
        None => store.events_after(cursor, limit),
    })
    .await;
    match res {
        Ok(Ok(evs)) => {
            let next = evs.last().map(|e| e.global_seq).unwrap_or(q.cursor.max(0));
            let items: Vec<_> = evs
                .into_iter()
                .map(|e| {
                    serde_json::json!({
                        "global_seq": e.global_seq,
                        "event_id": e.event_id,
                        "aggregate_type": e.aggregate_type,
                        "aggregate_id": e.aggregate_id,
                        "sequence": e.sequence,
                        "type": e.event_type,
                        "schema_version": e.schema_version,
                        "correlation_id": e.correlation_id,
                        "occurred_at": e.occurred_at,
                        "payload": e.payload,
                    })
                })
                .collect();
            if filtered {
                Json(serde_json::json!({"events": items, "next_cursor": next, "filter_applied": true})).into_response()
            } else {
                Json(serde_json::json!({"events": items, "next_cursor": next})).into_response()
            }
        }
        _ => {
            tracing::error!("events read failed");
            error_response(ErrorCode::Internal, "internal error", &guard::request_id())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{new_process, thermal_body};

    /// S79-p11c (Windows acceptance 8): Windows / Linux say `unsupported`, never `null` or
    /// a guess; a running guard says `on`; macOS without a guard is not claimed either way.
    #[test]
    fn the_thermal_answer_says_unsupported_where_there_is_no_sensor() {
        for os in ["windows", "linux"] {
            let b = thermal_body(os, None);
            assert_eq!(b["supported"], false, "{b}");
            assert_eq!(b["state"], "unsupported", "{b}");
            assert_eq!(b["os"], os, "{b}");
            assert!(b["why"].as_str().unwrap_or("").contains("ADR 0005"), "{b}");
        }
        let on = thermal_body("macos", Some(serde_json::json!({"t1": "on"})));
        assert_eq!(on["supported"], true, "{on}");
        assert_eq!(on["state"], "on", "{on}");
        assert_eq!(on["guard"]["t1"], "on", "{on}");
        let unknown = thermal_body("macos", None);
        assert!(unknown["supported"].is_null(), "{unknown}");
        assert_eq!(unknown["state"], "not_running", "{unknown}");
    }

    #[test]
    fn instance_id_needs_randomness() {
        // a failed read is an error (zeros would repeat on every restart)
        assert!(new_process(|_| Err(getrandom::Error::UNSUPPORTED)).is_err());
        let p = new_process(|b| {
            b.fill(0xab);
            Ok(())
        })
        .unwrap();
        assert_eq!(p.instance_id, "abababababababab");
    }
}
