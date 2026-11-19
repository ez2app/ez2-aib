//! S89-events-push (a9136 11-18, ORDER-workspace-519d0-1118 第三波 2; ADR 0003): events over
//! SSE, so a page stops asking every 2 seconds. `GET /api/v1/events/stream[?cursor=N]
//! [&aggregate_id=…]` (or the `Last-Event-ID` header) answers `text/event-stream`: every stored
//! event after the cursor, then each new one as it is written. Each SSE message is one event,
//! `id:` its `global_seq`, `data:` the same object `GET /api/v1/events` lists.
//!
//! No timer: the store calls [`woke`] after each write it made ([`aib_store::Store::on_write`]),
//! which wakes every open stream to read what is new. A stream that falls more than
//! [`BUFFER`] events behind is ended (the page reconnects with its last id and catches up from
//! SQLite; a slow page never holds aibd up, ADR 0003). When aibd stops, [`close`] ends every
//! stream first, so the graceful stop does not wait on an open page.

use crate::api::AppState;
use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response};
use std::convert::Infallible;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::OnceLock;
use std::task::{Context, Poll};
use tokio::sync::{mpsc, watch};

/// How many events a stream may hold for a page that has not read them yet.
pub const BUFFER: usize = 256;
/// How many streams may be open at once (5d33a f0b7d7d5 N1: one page keeps one; this leaves
/// room for several pages and tabs, and stops a runaway client from holding many).
pub const STREAMS_MAX: usize = 16;
/// How many stored events one read takes.
const PAGE: u32 = 200;

static TICK: OnceLock<watch::Sender<u64>> = OnceLock::new();
static CLOSING: AtomicBool = AtomicBool::new(false);
static OPEN: AtomicUsize = AtomicUsize::new(0);

/// One open stream, counted while its task runs.
struct Slot;

impl Slot {
    fn take() -> Option<Slot> {
        OPEN.try_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
            (n < STREAMS_MAX).then_some(n + 1)
        })
        .ok()
        .map(|_| Slot)
    }
}

impl Drop for Slot {
    fn drop(&mut self) {
        OPEN.fetch_sub(1, Ordering::SeqCst);
    }
}

/// Install the wake-up (once, at start) and hook it to the store's writes.
pub fn install(store: &aib_store::Store) {
    let (tx, _) = watch::channel(0u64);
    let _ = TICK.set(tx);
    store.on_write(woke);
    CLOSING.store(false, Ordering::SeqCst);
}

/// Something was written: every open stream reads what is new.
pub fn woke() {
    if let Some(t) = TICK.get() {
        t.send_modify(|n| *n = n.wrapping_add(1));
    }
}

/// aibd is stopping: every stream ends now.
pub fn close() {
    CLOSING.store(true, Ordering::SeqCst);
    woke();
}

#[derive(serde::Deserialize)]
pub struct StreamQuery {
    cursor: Option<i64>,
    aggregate_id: Option<String>,
}

/// Where a stream starts: `Last-Event-ID` (a reconnect) wins over `?cursor=`; neither ⇒ 0.
pub fn start_at(headers: &HeaderMap, cursor: Option<i64>) -> i64 {
    headers
        .get("last-event-id")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.trim().parse::<i64>().ok())
        .or(cursor)
        .unwrap_or(0)
        .max(0)
}

struct Rx(mpsc::Receiver<Result<Event, Infallible>>);

impl futures_core::Stream for Rx {
    type Item = Result<Event, Infallible>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.0.poll_recv(cx)
    }
}

/// One stored event as SSE: `id:` its global_seq, `data:` the object `GET /events` lists.
pub fn sse_of(e: &aib_store::StoredEvent) -> Event {
    let data = serde_json::json!({
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
    });
    Event::default()
        .id(e.global_seq.to_string())
        .data(data.to_string())
}

/// `GET /api/v1/events/stream`.
pub async fn stream(
    State(st): State<AppState>,
    headers: HeaderMap,
    Query(q): Query<StreamQuery>,
) -> Response {
    let Some(tick) = TICK.get() else {
        return crate::guard::error_response(
            aib_core::ErrorCode::Internal,
            "event push is not running",
            &crate::guard::request_id(),
        );
    };
    let Some(slot) = Slot::take() else {
        let body = serde_json::json!({
            "code": "too_many_streams",
            "message": "too many event streams are open; close one or read GET /api/v1/events",
            "request_id": crate::guard::request_id(),
        });
        return (axum::http::StatusCode::TOO_MANY_REQUESTS, axum::Json(body)).into_response();
    };
    let mut wake = tick.subscribe();
    let mut cursor = start_at(&headers, q.cursor);
    let (tx, rx) = mpsc::channel(BUFFER);
    let store = st.store.clone();
    let only = q.aggregate_id;
    tokio::spawn(async move {
        let _slot = slot;
        loop {
            if CLOSING.load(Ordering::SeqCst) {
                return;
            }
            // mark seen before reading: a write during the read wakes the next round
            wake.borrow_and_update();
            loop {
                let (s, o) = (store.clone(), only.clone());
                let got = tokio::task::spawn_blocking(move || match o {
                    Some(id) => s.events_for_after(&id, cursor, PAGE),
                    None => s.events_after(cursor, PAGE),
                })
                .await;
                let Ok(Ok(evs)) = got else {
                    return;
                };
                let n = evs.len();
                for e in evs {
                    cursor = e.global_seq;
                    // full ⇒ the page is too slow: end, it reconnects and catches up
                    if tx.try_send(Ok(sse_of(&e))).is_err() {
                        return;
                    }
                }
                if n < PAGE as usize {
                    break;
                }
            }
            tokio::select! {
                changed = wake.changed() => if changed.is_err() { return },
                () = tx.closed() => return,
            }
        }
    });
    Sse::new(Rx(rx)).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A reconnect resumes after the id it last saw; the header wins over the query.
    #[test]
    fn a_reconnect_starts_after_the_last_id_it_saw() {
        let mut h = HeaderMap::new();
        assert_eq!(start_at(&h, None), 0);
        assert_eq!(start_at(&h, Some(7)), 7);
        assert_eq!(start_at(&h, Some(-3)), 0);
        h.insert("last-event-id", "42".parse().unwrap());
        assert_eq!(start_at(&h, Some(7)), 42);
    }

    /// N1: at most STREAMS_MAX open at once; one closing frees its place.
    #[test]
    fn open_streams_are_capped() {
        let held: Vec<Slot> = (0..STREAMS_MAX).map_while(|_| Slot::take()).collect();
        assert_eq!(held.len(), STREAMS_MAX);
        assert!(Slot::take().is_none(), "reason: streams_not_capped");
        drop(held);
        assert!(Slot::take().is_some());
    }
}
