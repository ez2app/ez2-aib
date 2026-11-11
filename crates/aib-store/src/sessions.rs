//! Sessions (spec 4.1 / 5): the current row of each session, written with
//! its event in one transaction (spec 4.3), and the restart rule: a session
//! that was still open when aibd stopped is `interrupted`, never resumed or
//! re-attached (DECISION-11 B; ADR 0004).

use crate::{insert_event, NewEvent, Result, Store, StoreError};
use rusqlite::{params, OptionalExtension, Row};

/// `state` of a session ended by an aibd restart.
pub const INTERRUPTED: &str = "interrupted";
/// `state` of a session waiting in the queue (ADR 0005 §5): no process yet.
pub const PENDING: &str = "pending";
/// `ended_by` of a session ended by an aibd restart.
pub const ENDED_BY_RESTART: &str = "aibd_restart";

/// A session as stored. No argv beyond `argv0` (the file name) and `argc`,
/// and never env (spec 9.4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionRow {
    pub session_id: String,
    pub seat_id: Option<String>,
    pub pid: u32,
    pub argv0: Option<String>,
    pub argc: i64,
    pub state: String,
    pub holds_slot: bool,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub ended_by: Option<String>,
    pub exit_code: Option<i32>,
    pub signal: Option<String>,
    pub stop_status: Option<String>,
    pub stop_reason: Option<String>,
    pub updated_at: String,
}

const COLS: &str = "session_id, seat_id, pid, argv0, argc, state, holds_slot, started_at, \
                    ended_at, ended_by, exit_code, signal, stop_status, stop_reason, updated_at";

fn row_of(r: &Row<'_>) -> rusqlite::Result<SessionRow> {
    Ok(SessionRow {
        session_id: r.get(0)?,
        seat_id: r.get(1)?,
        pid: r.get(2)?,
        argv0: r.get(3)?,
        argc: r.get(4)?,
        state: r.get(5)?,
        holds_slot: r.get(6)?,
        started_at: r.get(7)?,
        ended_at: r.get(8)?,
        ended_by: r.get(9)?,
        exit_code: r.get(10)?,
        signal: r.get(11)?,
        stop_status: r.get(12)?,
        stop_reason: r.get(13)?,
        updated_at: r.get(14)?,
    })
}

impl Store {
    /// Write the session's current row (insert or replace the mutable
    /// fields; `seat_id`, `argv0`, `argc` never change; `pid` and
    /// `started_at` change once, when a queued session starts)
    /// and its event, in one transaction. Returns the event's `global_seq`.
    pub fn session_event(&self, row: SessionRow, event: NewEvent) -> Result<i64> {
        self.write(move |c| {
            if event.aggregate_id != row.session_id || event.aggregate_type != "session" {
                return Err(StoreError::Invalid(
                    "a session event must belong to that session",
                ));
            }
            let tx = c.transaction()?;
            tx.execute(
                &format!(
                    "INSERT INTO sessions ({COLS})
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)
                     ON CONFLICT (session_id) DO UPDATE SET
                         pid = excluded.pid, started_at = excluded.started_at,
                         state = excluded.state, holds_slot = excluded.holds_slot,
                         ended_at = excluded.ended_at, ended_by = excluded.ended_by,
                         exit_code = excluded.exit_code, signal = excluded.signal,
                         stop_status = excluded.stop_status, stop_reason = excluded.stop_reason,
                         updated_at = excluded.updated_at"
                ),
                params![
                    row.session_id,
                    row.seat_id,
                    row.pid,
                    row.argv0,
                    row.argc,
                    row.state,
                    row.holds_slot,
                    row.started_at,
                    row.ended_at,
                    row.ended_by,
                    row.exit_code,
                    row.signal,
                    row.stop_status,
                    row.stop_reason,
                    row.updated_at,
                ],
            )?;
            let n = insert_event(&tx, &event)?;
            tx.commit()?;
            Ok(n)
        })
    }

    /// At aibd start: every session still `running`, still holding a slot
    /// (a stop that could not show the tree gone), or still `pending` in the
    /// queue (ADR 0005 §5) becomes `interrupted` with `ended_by =
    /// aibd_restart` and a `session.interrupted` event (`previous_state`
    /// tells them apart). Nothing is re-attached, restarted or started from
    /// the old queue. On macOS a running one's processes may still run (aibd
    /// cannot stop what a previous run started); the event says so instead of
    /// claiming a clean end; a pending one never had a process. Returns the
    /// ids, oldest first.
    pub fn interrupt_open_sessions(
        &self,
        now: String,
        mut new_event_id: impl FnMut() -> String + Send + 'static,
    ) -> Result<Vec<String>> {
        self.write(move |c| {
            let tx = c.transaction()?;
            let open: Vec<(String, String)> = {
                let mut st = tx.prepare(
                    "SELECT session_id, state FROM sessions
                     WHERE state = 'running' OR holds_slot = 1 OR state = 'pending'
                     ORDER BY started_at, session_id",
                )?;
                let rows = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
                rows.collect::<std::result::Result<_, _>>()?
            };
            for (id, was) in &open {
                tx.execute(
                    "UPDATE sessions SET state = ?2, holds_slot = 0,
                         ended_at = COALESCE(ended_at, ?3), ended_by = ?4, updated_at = ?3
                     WHERE session_id = ?1",
                    params![id, INTERRUPTED, now, ENDED_BY_RESTART],
                )?;
                let last: i64 = tx.query_row(
                    "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE aggregate_id = ?1",
                    params![id],
                    |r| r.get(0),
                )?;
                insert_event(
                    &tx,
                    &NewEvent {
                        event_id: new_event_id(),
                        aggregate_type: "session".into(),
                        aggregate_id: id.clone(),
                        sequence: last + 1,
                        event_type: "session.interrupted".into(),
                        correlation_id: None,
                        occurred_at: now.clone(),
                        payload: serde_json::json!({
                            "id": id,
                            "reason": ENDED_BY_RESTART,
                            "previous_state": was,
                            "processes_may_remain": was != PENDING,
                        }),
                    },
                )?;
            }
            tx.commit()?;
            Ok(open.into_iter().map(|(id, _)| id).collect())
        })
    }

    pub fn session_row(&self, session_id: &str) -> Result<Option<SessionRow>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {COLS} FROM sessions WHERE session_id = ?1"),
            params![session_id],
            row_of,
        )
        .optional()?)
    }

    /// The sessions of one seat, oldest first.
    pub fn sessions_of_seat(&self, seat_id: &str) -> Result<Vec<SessionRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM sessions WHERE seat_id = ?1 ORDER BY started_at, session_id"
        ))?;
        let rows = st.query_map(params![seat_id], row_of)?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }
}
