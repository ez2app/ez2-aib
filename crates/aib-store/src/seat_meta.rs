//! What reviving a seat needs (A11 S37): provider, role, cwd, model, resume
//! id, project and persona, written with the seat in one transaction so a
//! seat that exists can always be revived (1.x lost a cabin because only a
//! periodic snapshot carried this). The resume id comes later, when the CLI
//! reports it: one UPDATE plus a `seat.resume_id_filled` event, without a
//! seat revision (Seat is the API contract; a9136 11-13).

use crate::IdemKey;
use crate::{insert_event, NewEvent, Result, Seat, SeatCreate, Store, StoreError};
use rusqlite::{params, OptionalExtension, Row, Transaction};

/// A seat's revive data. `resume_id: None` means pending (not reported yet);
/// `Some("")` is refused, an empty string never stands for a value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatMeta {
    pub seat_id: String,
    pub provider: String,
    pub role: Option<String>,
    pub cwd: String,
    pub model: Option<String>,
    /// The model was chosen by hand (revive must pass it again).
    pub model_pinned: bool,
    pub resume_id: Option<String>,
    pub project_uid: Option<String>,
    pub persona_uid: Option<String>,
    pub brain_route: Option<String>,
    pub updated_at: String,
}

/// What [`Store::fill_resume_id`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResumeFill {
    /// Written now, with its event.
    Filled,
    /// Already this value: nothing written (a retry).
    AlreadySet,
    /// Already another value: nothing written.
    Conflict { existing: String },
    /// The seat has no revive data: nothing written.
    NoMeta,
}

const COLS: &str = "seat_id, provider, role, cwd, model, model_pinned, resume_id, \
                    project_uid, persona_uid, brain_route, updated_at";

fn meta_of(r: &Row<'_>) -> rusqlite::Result<SeatMeta> {
    Ok(SeatMeta {
        seat_id: r.get(0)?,
        provider: r.get(1)?,
        role: r.get(2)?,
        cwd: r.get(3)?,
        model: r.get(4)?,
        model_pinned: r.get(5)?,
        resume_id: r.get(6)?,
        project_uid: r.get(7)?,
        persona_uid: r.get(8)?,
        brain_route: r.get(9)?,
        updated_at: r.get(10)?,
    })
}

/// Insert `m` for `seat_id` inside the seat's transaction.
pub(crate) fn insert_meta(tx: &Transaction<'_>, seat_id: &str, m: &SeatMeta) -> Result<()> {
    if m.seat_id != seat_id {
        return Err(StoreError::Invalid(
            "seat meta must belong to the seat it is written with",
        ));
    }
    if m.resume_id.as_deref() == Some("") {
        return Err(StoreError::Invalid(
            "an empty resume id is not a value; use None (pending)",
        ));
    }
    tx.execute(
        &format!(
            "INSERT INTO seat_meta ({COLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)"
        ),
        params![
            m.seat_id,
            m.provider,
            m.role,
            m.cwd,
            m.model,
            m.model_pinned,
            m.resume_id,
            m.project_uid,
            m.persona_uid,
            m.brain_route,
            m.updated_at,
        ],
    )?;
    Ok(())
}

impl Store {
    /// Create a seat together with its revive data (see
    /// [`Store::create_seat`] for `event`, `idem`, `status`, `response`).
    /// One transaction: if `meta` is refused, nothing is written and the
    /// error is returned (the seat did not open).
    pub fn create_seat_with_meta(
        &self,
        seat: Seat,
        meta: SeatMeta,
        event: NewEvent,
        idem: Option<IdemKey>,
        status: u16,
        response: serde_json::Value,
    ) -> Result<SeatCreate> {
        self.create_seat_inner(seat, Some(meta), event, idem, status, response)
    }

    /// Record the resume id the CLI reported, once: an UPDATE plus a
    /// `seat.resume_id_filled` event (next sequence of the seat, assigned
    /// here) in one transaction. The event payload does not carry the id.
    pub fn fill_resume_id(
        &self,
        seat_id: &str,
        resume_id: &str,
        event_id: &str,
        occurred_at: &str,
    ) -> Result<ResumeFill> {
        if resume_id.is_empty() {
            return Err(StoreError::Invalid("an empty resume id is not a value"));
        }
        let (seat_id, resume_id) = (seat_id.to_string(), resume_id.to_string());
        let (event_id, occurred_at) = (event_id.to_string(), occurred_at.to_string());
        self.write(move |c| {
            let tx = c.transaction()?;
            let have: Option<Option<String>> = tx
                .query_row(
                    "SELECT resume_id FROM seat_meta WHERE seat_id = ?1",
                    params![seat_id],
                    |r| r.get(0),
                )
                .optional()?;
            let Some(have) = have else {
                return Ok(ResumeFill::NoMeta);
            };
            match have {
                Some(h) if h == resume_id => return Ok(ResumeFill::AlreadySet),
                Some(h) => return Ok(ResumeFill::Conflict { existing: h }),
                None => {}
            }
            tx.execute(
                "UPDATE seat_meta SET resume_id = ?2, updated_at = ?3 WHERE seat_id = ?1",
                params![seat_id, resume_id, occurred_at],
            )?;
            let last: i64 = tx.query_row(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE aggregate_id = ?1",
                params![seat_id],
                |r| r.get(0),
            )?;
            insert_event(
                &tx,
                &NewEvent {
                    event_id,
                    aggregate_type: "seat".into(),
                    aggregate_id: seat_id.clone(),
                    sequence: last + 1,
                    event_type: "seat.resume_id_filled".into(),
                    correlation_id: None,
                    occurred_at,
                    payload: serde_json::json!({ "field": "resume_id" }),
                },
            )?;
            tx.commit()?;
            Ok(ResumeFill::Filled)
        })
    }

    pub fn seat_meta(&self, seat_id: &str) -> Result<Option<SeatMeta>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {COLS} FROM seat_meta WHERE seat_id = ?1"),
            params![seat_id],
            meta_of,
        )
        .optional()?)
    }

    /// The seat a revived one continues (A11 S41 rule 6): the newest other
    /// seat with the same resume id, never `except` itself.
    pub fn previous_seat_with_resume(
        &self,
        resume_id: &str,
        except: &str,
    ) -> Result<Option<SeatMeta>> {
        let c = self.read()?;
        let cols = COLS
            .split(", ")
            .map(|k| format!("m.{k}"))
            .collect::<Vec<_>>()
            .join(", ");
        Ok(c.query_row(
            &format!(
                "SELECT {cols} FROM seat_meta m JOIN seats s ON s.seat_id = m.seat_id
                 WHERE m.resume_id = ?1 AND m.seat_id != ?2
                 ORDER BY s.created_at DESC, m.seat_id DESC LIMIT 1"
            ),
            params![resume_id, except],
            meta_of,
        )
        .optional()?)
    }
}
