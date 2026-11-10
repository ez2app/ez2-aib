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
use axum::routing::{get, post};
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
    Router::new()
        .route("/api/v1/health", get(health))
        .route("/api/v1/events", get(events))
        .route("/api/v1/metrics", get(metrics))
        .route(
            "/api/v1/seats",
            get(seats::list)
                .post(seats::create)
                .fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/seats/{id}",
            get(seats::get_one).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/seats/{id}/sessions",
            get(sessions::list_for_seat)
                .post(sessions::create_for_seat)
                .fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/sessions",
            get(sessions::list)
                .post(sessions::create)
                .fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/sessions/{id}",
            get(sessions::get_one)
                .delete(sessions::delete_one)
                .fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/sessions/{id}/output",
            get(sessions::output).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/codes",
            post(pair_api::create_code).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/codes/{id}",
            get(pair_api::get_code)
                .delete(pair_api::cancel)
                .fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/codes/{id}/confirm",
            post(pair_api::confirm).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/redeem",
            post(pair_api::redeem).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/status",
            get(pair_api::status).fallback(sessions::method_not_allowed),
        )
        .route(
            "/api/v1/pair/revoke",
            post(pair_api::revoke).fallback(sessions::method_not_allowed),
        )
        .fallback(crate::static_files::fallback)
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(
            guard_cfg,
            guard::guard,
        ))
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
