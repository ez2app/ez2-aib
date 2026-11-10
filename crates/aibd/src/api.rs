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
}

pub fn router(state: AppState, guard_cfg: Arc<GuardConfig>) -> Router {
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
        EVENTS, HEALTH, METRICS, PAIR_CODE, PAIR_CODES, PAIR_CONFIRM, PAIR_REDEEM,
        PAIR_REVOKE, PAIR_STATUS, SEAT, SEATS, SEAT_SESSIONS, SESSION, SESSIONS,
        SESSION_OUTPUT,
    };
    let nope = sessions::method_not_allowed;
    Some(match path {
        HEALTH => get(health),
        EVENTS => get(events),
        METRICS => get(metrics),
        SEATS => get(seats::list).post(seats::create).fallback(nope),
        SEAT => get(seats::get_one).fallback(nope),
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
        _ => return None,
    })
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok", "api_version": API_VERSION}))
}

/// Counters for diagnosis. `lineage.selfcheck` is `ok`, `failed:<tag>` or
/// `unsupported`; `lineage.failures` counts M1 reads that failed after
/// start-up (a process being gone is not a failure). `pairing` has the
/// pairing channel and failure / rate-limit totals.
async fn metrics(
    State(st): State<AppState>,
    pairing: Option<axum::extract::Extension<Arc<crate::pairing::Pairing>>>,
) -> Json<serde_json::Value> {
    let pairing = pairing.map(|p| p.0.metrics());
    Json(serde_json::json!({"lineage": st.lineage.status(), "pairing": pairing}))
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
