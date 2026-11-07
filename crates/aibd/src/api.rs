//! `/api/v1` routes. Phase 0: health (public) and a read-only event cursor (authenticated).

use crate::guard::{self, error_response, GuardConfig};
use aib_core::ErrorCode;
use aib_store::Store;
use axum::extract::{Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use serde::Deserialize;
use std::sync::Arc;

pub const API_VERSION: &str = "v1";

#[derive(Clone)]
pub struct AppState {
    pub store: Arc<Store>,
}

pub fn router(state: AppState, guard_cfg: Arc<GuardConfig>) -> Router {
    Router::new()
        .route("/api/v1/health", get(health))
        .route("/api/v1/events", get(events))
        .fallback(not_found)
        .with_state(state)
        .layer(axum::middleware::from_fn_with_state(
            guard_cfg,
            guard::guard,
        ))
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok", "api_version": API_VERSION}))
}

async fn not_found() -> Response {
    error_response(ErrorCode::NotFound, "no such route", &guard::request_id())
}

#[derive(Deserialize)]
struct EventsQuery {
    #[serde(default)]
    cursor: i64,
    limit: Option<u32>,
}

async fn events(State(st): State<AppState>, Query(q): Query<EventsQuery>) -> Response {
    let limit = q.limit.unwrap_or(100).clamp(1, 500);
    let store = st.store.clone();
    let res = tokio::task::spawn_blocking(move || store.events_after(q.cursor.max(0), limit)).await;
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
            Json(serde_json::json!({"events": items, "next_cursor": next})).into_response()
        }
        _ => {
            tracing::error!("events read failed");
            error_response(ErrorCode::Internal, "internal error", &guard::request_id())
        }
    }
}
