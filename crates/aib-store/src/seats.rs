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
