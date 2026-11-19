//! Seats (spec 4.1 / 5): the current row plus its events, written in one
//! transaction (the events table is the outbox, ADR 0003), and the
//! idempotency key of the request that created it (spec 8).

use crate::{insert_event, NewEvent, Result, Store, StoreError};
use rusqlite::{params, OptionalExtension, Row};

/// A seat as stored. `state` is `aib_core::SeatState` in its serde form
/// (`CREATED`, `READY`, ...); the store does not interpret it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seat {
    pub seat_id: String,
    pub name: String,
    pub state: String,
    pub revision: i64,
    pub created_at: String,
    pub updated_at: String,
}

/// The idempotency key of a write: `scope` is the route (`POST /api/v1/seats`),
/// `request` the canonical request body. The same key with the same request
/// replays the first answer; with another request it is a conflict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IdemKey {
    pub scope: String,
    pub key: String,
    pub request: String,
}

/// What [`Store::create_seat`] did.
#[derive(Debug, Clone, PartialEq)]
pub enum SeatCreate {
    /// Written now: the seat, its first event and (if given) the key.
    Created(Seat),
    /// The key was used before for the same request: nothing was written,
    /// this is the first answer.
    Replayed {
        status: u16,
        response: serde_json::Value,
    },
    /// The key was used before for a different request: nothing was written.
    KeyConflict,
}

/// What [`Store::archive_seat`] did (S89-state, IFACE-S89-state-archive-043dd-1118 r1).
#[derive(Debug, Clone, PartialEq)]
pub enum SeatArchive {
    /// Written now: the row is `ARCHIVED` with the next revision, and `seat.archived` is in
    /// the events; `from` is the state it had.
    Archived {
        seat: Seat,
        from: String,
    },
    /// It was archived before: nothing was written.
    Already(Seat),
    NotFound,
}

/// The stored form of `aib_core::SeatState::Archived`.
pub const ARCHIVED: &str = "ARCHIVED";

fn seat_of(r: &Row<'_>) -> rusqlite::Result<Seat> {
    Ok(Seat {
        seat_id: r.get(0)?,
        name: r.get(1)?,
        state: r.get(2)?,
        revision: r.get(3)?,
        created_at: r.get(4)?,
        updated_at: r.get(5)?,
    })
}

const SEAT_COLS: &str = "seat_id, name, state, revision, created_at, updated_at";

impl Store {
    /// Create `seat` with its first event (`event.sequence` must be 1 and
    /// `event.aggregate_id` the seat id). With `idem`, the key is checked and
    /// recorded in the same transaction, together with the answer the caller
    /// will give (`status`, `response`), so a retry gets exactly that answer.
    pub fn create_seat(
        &self,
        seat: Seat,
        event: NewEvent,
        idem: Option<IdemKey>,
        status: u16,
        response: serde_json::Value,
    ) -> Result<SeatCreate> {
        self.create_seat_inner(seat, None, event, idem, status, response)
    }

    /// [`Store::create_seat`] plus the seat's revive data (`meta`), all in
    /// one transaction: if `meta` cannot be written, the seat is not created
    /// either.
    pub(crate) fn create_seat_inner(
        &self,
        seat: Seat,
        meta: Option<crate::SeatMeta>,
        event: NewEvent,
        idem: Option<IdemKey>,
        status: u16,
        response: serde_json::Value,
    ) -> Result<SeatCreate> {
        self.write(move |c| {
            let tx = c.transaction()?;
            if let Some(k) = &idem {
                let seen: Option<(String, u16, String)> = tx
                    .query_row(
                        "SELECT request, status, response FROM idempotency_keys
                         WHERE scope = ?1 AND key = ?2",
                        params![k.scope, k.key],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                    )
                    .optional()?;
                if let Some((request, status, response)) = seen {
                    if request != k.request {
                        return Ok(SeatCreate::KeyConflict);
                    }
                    let response =
                        serde_json::from_str(&response).unwrap_or(serde_json::Value::Null);
                    return Ok(SeatCreate::Replayed { status, response });
                }
            }
            if event.aggregate_id != seat.seat_id || event.aggregate_type != "seat" {
                return Err(StoreError::Invalid(
                    "a seat's first event must be a seat event for that seat",
                ));
            }
            tx.execute(
                &format!("INSERT INTO seats ({SEAT_COLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6)"),
                params![
                    seat.seat_id,
                    seat.name,
                    seat.state,
                    seat.revision,
                    seat.created_at,
                    seat.updated_at,
                ],
            )?;
            insert_event(&tx, &event)?;
            if let Some(m) = &meta {
                crate::seat_meta::insert_meta(&tx, &seat.seat_id, m)?;
            }
            if let Some(k) = &idem {
                tx.execute(
                    "INSERT INTO idempotency_keys (scope, key, request, status, response, created_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        k.scope,
                        k.key,
                        k.request,
                        status,
                        response.to_string(),
                        seat.created_at,
                    ],
                )?;
            }
            tx.commit()?;
            Ok(SeatCreate::Created(seat))
        })
    }

    pub fn seat(&self, seat_id: &str) -> Result<Option<Seat>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {SEAT_COLS} FROM seats WHERE seat_id = ?1"),
            params![seat_id],
            seat_of,
        )
        .optional()?)
    }

    /// Archive a seat: state `ARCHIVED`, revision + 1, `updated_at = at`, and the event
    /// `seat.archived {seat_id, revision, from, by}` (next sequence of the seat), in one
    /// transaction. Sessions, events and the seat's meta stay. Whether the seat is busy is
    /// the caller's question (aibd asks it under the same lock that starts sessions).
    pub fn archive_seat(
        &self,
        seat_id: &str,
        event_id: String,
        at: String,
        by: &str,
    ) -> Result<SeatArchive> {
        let seat_id = seat_id.to_owned();
        let by = by.to_owned();
        self.write(move |c| {
            let tx = c.transaction()?;
            let cur = tx
                .query_row(
                    &format!("SELECT {SEAT_COLS} FROM seats WHERE seat_id = ?1"),
                    params![seat_id],
                    seat_of,
                )
                .optional()?;
            let Some(cur) = cur else {
                return Ok(SeatArchive::NotFound);
            };
            if cur.state == ARCHIVED {
                return Ok(SeatArchive::Already(cur));
            }
            let revision = cur.revision + 1;
            tx.execute(
                "UPDATE seats SET state = ?2, revision = ?3, updated_at = ?4 WHERE seat_id = ?1",
                params![seat_id, ARCHIVED, revision, at],
            )?;
            let sequence: i64 = tx.query_row(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE aggregate_id = ?1",
                params![seat_id],
                |r| r.get(0),
            )?;
            insert_event(
                &tx,
                &NewEvent {
                    event_id,
                    aggregate_type: "seat".into(),
                    aggregate_id: seat_id.clone(),
                    sequence,
                    event_type: "seat.archived".into(),
                    correlation_id: None,
                    occurred_at: at.clone(),
                    payload: serde_json::json!({
                        "seat_id": seat_id,
                        "revision": revision,
                        "from": cur.state,
                        "by": by,
                    }),
                },
            )?;
            tx.commit()?;
            let from = cur.state.clone();
            Ok(SeatArchive::Archived {
                seat: Seat {
                    state: ARCHIVED.into(),
                    revision,
                    updated_at: at,
                    ..cur
                },
                from,
            })
        })
    }

    /// Every seat, oldest first.
    pub fn seats(&self) -> Result<Vec<Seat>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {SEAT_COLS} FROM seats ORDER BY created_at, seat_id"
        ))?;
        let rows = st.query_map([], seat_of)?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }
}
