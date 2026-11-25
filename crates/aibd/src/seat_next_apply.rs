//! §8 8b-2 (6770f, DESIGN-merge-6770f-1123 r1 §4a-1 / §4a-3 / §4b-1; f163c REVIEW-merge r0
//! M1): a merge never changes a running seat. What it sets for a seat's next open
//! (`aib_store` `seat_next`) is written into the seat's record when the seat is revived or
//! restarted, and a persona uid given to an open goes through the alias table first.
//!
//! - [`open_uid`]: an old uid merged into another opens as that other one; an archived persona
//!   does not open (`persona_archived`).
//! - [`apply_next`]: read the seat's switch, resolve its persona the same way, write it into
//!   `seat_meta` with one `seat.identity_switched` event, then remove exactly the row that was
//!   read. A crash between the two writes applies it again next time (the same values: no
//!   harm); a row a later merge wrote meanwhile stays. A persona that was archived since the
//!   merge is not switched to (the seat keeps its own) and that is said in the answer.

use aib_store::{MessageDb, Store};

/// The persona uid a seat opens with: through the alias table, never an archived one.
/// Errors: `persona_archived`, `store`.
pub fn open_uid(db: &MessageDb, uid: &str) -> Result<String, &'static str> {
    let u = db.alias_resolve(uid).map_err(|_| "store")?;
    match db.archived("persona", &u) {
        Ok(Some(_)) => Err("persona_archived"),
        Ok(None) => Ok(u),
        Err(_) => Err("store"),
    }
}

/// What [`apply_next`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Applied {
    /// No switch waiting.
    Nothing,
    /// Written. `persona` = the uid now on the seat (after aliases) or `None` when only the
    /// brain switched; `persona_refused` = the switch named a persona archived since.
    Switched {
        persona: Option<String>,
        brain: bool,
        persona_refused: bool,
    },
}

/// Apply a seat's waiting switch (revive / restart). Errors: `store`.
pub fn apply_next(
    db: &MessageDb,
    store: &Store,
    seat_id: &str,
    event_id: &str,
    occurred_at: &str,
) -> Result<Applied, &'static str> {
    let Some(n) = db.seat_next(seat_id).map_err(|_| "store")? else {
        return Ok(Applied::Nothing);
    };
    let (persona, persona_refused) = match n.persona_next.as_deref() {
        None => (None, false),
        Some(p) => match open_uid(db, p) {
            Ok(u) => (Some(u), false),
            Err("persona_archived") => (None, true),
            Err(e) => return Err(e),
        },
    };
    if persona.is_some() || n.brain_next.is_some() {
        store
            .switch_identity(
                seat_id,
                persona.as_deref(),
                n.brain_next.as_deref(),
                &n.batch_id,
                event_id,
                occurred_at,
            )
            .map_err(|_| "store")?;
    }
    db.seat_next_done(seat_id, &n.batch_id, n.at)
        .map_err(|_| "store")?;
    Ok(Applied::Switched {
        persona,
        brain: n.brain_next.is_some(),
        persona_refused,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use aib_store::{ArchivedItem, NewEvent, Seat, SeatMeta};

    fn dbs() -> (tempfile::TempDir, MessageDb, Store) {
        let d = tempfile::tempdir().unwrap();
        let m = MessageDb::open(&d.path().join("m.db")).unwrap();
        let s = Store::open(&d.path().join("aibd.sqlite")).unwrap();
        (d, m, s)
    }

    fn seat(s: &Store, id: &str, persona: &str) {
        let at = "2026-11-23T00:00:00.000Z";
        s.create_seat_with_meta(
            Seat {
                seat_id: id.into(),
                name: id.into(),
                state: "CREATED".into(),
                revision: 1,
                created_at: at.into(),
                updated_at: at.into(),
            },
            SeatMeta {
                seat_id: id.into(),
                provider: "claude".into(),
                role: None,
                cwd: "/w".into(),
                model: None,
                model_pinned: false,
                resume_id: Some("r1".into()),
                project_uid: None,
                persona_uid: Some(persona.into()),
                brain_route: Some("route.a".into()),
                updated_at: at.into(),
                mode: Some("safe".into()),
                axis_id: None,
                cabin: None,
            },
            NewEvent {
                event_id: format!("ev_c_{id}"),
                aggregate_type: "seat".into(),
                aggregate_id: id.into(),
                sequence: 1,
                event_type: "seat.created".into(),
                correlation_id: None,
                occurred_at: at.into(),
                payload: serde_json::json!({}),
            },
            None,
            201,
            serde_json::json!({}),
        )
        .unwrap();
    }

    fn archive(m: &MessageDb, uid: &str) {
        m.archive_add(ArchivedItem {
            kind: "persona".into(),
            uid: uid.into(),
            archived_at: 1,
            keep_until: None,
            reason: "merge".into(),
            batch_id: Some("mb_x".into()),
            copy_dir: None,
            sha256: None,
            restored_at: None,
        })
        .unwrap();
    }

    #[test]
    fn mg18_an_old_uid_opens_as_the_kept_one_and_an_archived_one_does_not_open() {
        let (_d, m, _s) = dbs();
        assert_eq!(open_uid(&m, "aaaaa1"), Ok("aaaaa1".into()));
        m.alias_set("aaaaa2", "aaaaa1", "mb_1", 1).unwrap();
        assert_eq!(open_uid(&m, "aaaaa2"), Ok("aaaaa1".into()));
        archive(&m, "aaaaa3");
        assert_eq!(open_uid(&m, "aaaaa3"), Err("persona_archived"));
    }

    #[test]
    fn mg19_a_revived_seat_takes_its_switch_once_through_the_aliases() {
        let (_d, m, s) = dbs();
        seat(&s, "s1", "aaaaa2");
        assert_eq!(apply_next(&m, &s, "s1", "ev_1", "t1"), Ok(Applied::Nothing));
        m.alias_set("aaaaa2", "aaaaa1", "mb_1", 1).unwrap();
        m.seat_next_set("s1", Some("aaaaa2"), Some("route.b"), "mb_1", 2)
            .unwrap();
        assert_eq!(
            apply_next(&m, &s, "s1", "ev_2", "t2"),
            Ok(Applied::Switched {
                persona: Some("aaaaa1".into()),
                brain: true,
                persona_refused: false
            })
        );
        let meta = s.seat_meta("s1").unwrap().unwrap();
        assert_eq!(meta.persona_uid.as_deref(), Some("aaaaa1"));
        assert_eq!(meta.brain_route.as_deref(), Some("route.b"));
        assert!(m.seat_next("s1").unwrap().is_none());
        assert_eq!(apply_next(&m, &s, "s1", "ev_3", "t3"), Ok(Applied::Nothing));
    }

    #[test]
    fn mg19_a_persona_archived_since_is_not_switched_to_but_the_brain_is() {
        let (_d, m, s) = dbs();
        seat(&s, "s1", "aaaaa2");
        m.seat_next_set("s1", Some("aaaaa3"), Some("route.b"), "mb_1", 2)
            .unwrap();
        archive(&m, "aaaaa3");
        assert_eq!(
            apply_next(&m, &s, "s1", "ev_1", "t1"),
            Ok(Applied::Switched {
                persona: None,
                brain: true,
                persona_refused: true
            })
        );
        let meta = s.seat_meta("s1").unwrap().unwrap();
        assert_eq!(meta.persona_uid.as_deref(), Some("aaaaa2"));
        assert_eq!(meta.brain_route.as_deref(), Some("route.b"));
        assert!(m.seat_next("s1").unwrap().is_none());
        // only an archived persona: nothing written, the row still goes
        m.seat_next_set("s1", Some("aaaaa3"), None, "mb_2", 3)
            .unwrap();
        assert_eq!(
            apply_next(&m, &s, "s1", "ev_2", "t2"),
            Ok(Applied::Switched {
                persona: None,
                brain: false,
                persona_refused: true
            })
        );
        assert!(m.seat_next("s1").unwrap().is_none());
    }
}
