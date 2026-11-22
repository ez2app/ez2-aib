//! What each seat was given from a permission template (aibd §7 7b, DESIGN-brainnote-08de5-1123
//! §2d / §7; SPEC-ai-partner-open-ebab8-1123-r2 §3.7 / §6): the template and revision it was
//! opened or applied with, its items as aibd expanded them, a change waiting for the seat's
//! restart, and a log row for every change (§3.7: who, when, from what to what, and 收回).
//!
//! Tables only ever added (an older aibd never reads them). Written only by aibd. What the items
//! mean, when a change needs the owner's fingerprint and when it takes effect is aibd's
//! (`aibd::perm_apply`); here the rows are kept as given. A seat with no row was not opened from
//! a template: aibd reads its mode's own defaults (f163c REVIEW-permtpl r2 N1).

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS seat_perm (
    seat_id         TEXT PRIMARY KEY,
    tpl_uid         TEXT NOT NULL,
    tpl_rev         INTEGER NOT NULL,
    base            TEXT NOT NULL,
    items_json      TEXT NOT NULL,
    set_at          TEXT NOT NULL,
    set_by          TEXT NOT NULL,
    pending_tpl_uid TEXT,
    pending_rev     INTEGER,
    pending_base    TEXT,
    pending_items   TEXT,
    pending_at      TEXT,
    pending_by      TEXT
);
CREATE TABLE IF NOT EXISTS seat_perm_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id    TEXT NOT NULL,
    action     TEXT NOT NULL,
    from_json  TEXT,
    to_json    TEXT,
    at         TEXT NOT NULL,
    by         TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE INDEX IF NOT EXISTS seat_perm_log_seat ON seat_perm_log (seat_id, log_id);
";

/// Add the tables when they are missing (every open; nothing when they are there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// A template's state as given to a seat.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PermState {
    pub tpl_uid: String,
    pub tpl_rev: i64,
    pub base: String,
    /// aibd's normal form of the items (a JSON object of item ⇒ bool, keys sorted).
    pub items_json: String,
}

/// One seat's row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatPerm {
    pub seat_id: String,
    /// What the seat runs with now.
    pub now: PermState,
    pub set_at: String,
    pub set_by: String,
    /// A change taking effect at the seat's next restart.
    pub pending: Option<PermState>,
    pub pending_at: Option<String>,
    pub pending_by: Option<String>,
}

/// One log row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatPermLog {
    pub log_id: i64,
    pub seat_id: String,
    /// `open` / `lower` / `raise_pending` / `restart_applied` / `revoke`.
    pub action: String,
    pub from_json: Option<String>,
    pub to_json: Option<String>,
    pub at: String,
    pub by: String,
    pub revoked_at: Option<String>,
    pub revoked_by: Option<String>,
}

/// The most log rows listed for one seat.
pub const SEAT_PERM_LOG_MAX: i64 = 200;

fn need(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(StoreError::Invalid(why))
    }
}

fn state_checked(s: &PermState) -> Result<()> {
    need(
        crate::ranks::key_ok(&s.tpl_uid),
        "tpl_uid is 1-64 of A-Za-z0-9_.:-",
    )?;
    need(s.tpl_rev >= 1, "tpl_rev is 1 or more")?;
    need(
        crate::ranks::key_ok(&s.base),
        "base is 1-64 of A-Za-z0-9_.:-",
    )?;
    need(
        serde_json::from_str::<serde_json::Map<String, serde_json::Value>>(&s.items_json).is_ok(),
        "items_json is a JSON object",
    )
}

fn who_checked(seat: &str, by: &str, at: &str) -> Result<()> {
    need(
        crate::ranks::key_ok(seat),
        "seat_id is 1-64 of A-Za-z0-9_.:-",
    )?;
    need(!by.is_empty() && !at.is_empty(), "by and time are needed")
}

/// A state as one JSON object (the log's from / to).
fn state_json(s: &PermState) -> String {
    let items =
        serde_json::from_str::<serde_json::Value>(&s.items_json).unwrap_or(serde_json::Value::Null);
    serde_json::json!({"tpl_uid": s.tpl_uid, "rev": s.tpl_rev, "base": s.base, "items": items})
        .to_string()
}

const COLS: &str = "seat_id, tpl_uid, tpl_rev, base, items_json, set_at, set_by, pending_tpl_uid, \
                    pending_rev, pending_base, pending_items, pending_at, pending_by";

fn from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<SeatPerm> {
    let p_uid: Option<String> = r.get(7)?;
    let p_rev: Option<i64> = r.get(8)?;
    let p_base: Option<String> = r.get(9)?;
    let p_items: Option<String> = r.get(10)?;
    let pending = match (p_uid, p_rev, p_base, p_items) {
        (Some(tpl_uid), Some(tpl_rev), Some(base), Some(items_json)) => Some(PermState {
            tpl_uid,
            tpl_rev,
            base,
            items_json,
        }),
        _ => None,
    };
    Ok(SeatPerm {
        seat_id: r.get(0)?,
        now: PermState {
            tpl_uid: r.get(1)?,
            tpl_rev: r.get(2)?,
            base: r.get(3)?,
            items_json: r.get(4)?,
        },
        set_at: r.get(5)?,
        set_by: r.get(6)?,
        pending,
        pending_at: r.get(11)?,
        pending_by: r.get(12)?,
    })
}

fn row_of(c: &Connection, seat: &str) -> rusqlite::Result<Option<SeatPerm>> {
    c.query_row(
        &format!("SELECT {COLS} FROM seat_perm WHERE seat_id = ?1"),
        params![seat],
        from_row,
    )
    .optional()
}

fn log_row(
    c: &Connection,
    seat: &str,
    action: &str,
    from: Option<String>,
    to: Option<String>,
    at: &str,
    by: &str,
) -> rusqlite::Result<i64> {
    c.execute(
        "INSERT INTO seat_perm_log (seat_id, action, from_json, to_json, at, by)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![seat, action, from, to, at, by],
    )?;
    Ok(c.last_insert_rowid())
}

/// What a change did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PermWrite {
    /// Written; the log row's id.
    Done(i64),
    /// The seat has no row (it was not opened from a template).
    Unknown,
    /// The seat already has a row (an open is written once).
    Taken,
}

impl MessageDb {
    /// What a seat opened from a template was given (written once, when the seat is made).
    pub fn seat_perm_open(
        &self,
        seat: &str,
        s: &PermState,
        by: &str,
        at: &str,
    ) -> Result<PermWrite> {
        who_checked(seat, by, at)?;
        state_checked(s)?;
        let (seat, s, by, at) = (seat.to_owned(), s.clone(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            let tx = c.transaction()?;
            if row_of(&tx, &seat)?.is_some() {
                return Ok(PermWrite::Taken);
            }
            tx.execute(
                "INSERT INTO seat_perm (seat_id, tpl_uid, tpl_rev, base, items_json, set_at, set_by)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![seat, s.tpl_uid, s.tpl_rev, s.base, s.items_json, at, by],
            )?;
            let id = log_row(&tx, &seat, "open", None, Some(state_json(&s)), &at, &by)?;
            tx.commit()?;
            Ok(PermWrite::Done(id))
        })
    }

    /// A change kept for the seat's next restart (`action`: `lower` when aibd restarts the seat
    /// itself, `raise_pending` when the owner approved a raise; either way it is what the next
    /// start reads). A change already waiting is replaced.
    pub fn seat_perm_pend(
        &self,
        seat: &str,
        s: &PermState,
        action: &str,
        by: &str,
        at: &str,
    ) -> Result<PermWrite> {
        who_checked(seat, by, at)?;
        state_checked(s)?;
        need(
            matches!(action, "lower" | "raise_pending" | "revoke"),
            "action is lower, raise_pending or revoke",
        )?;
        let (seat, s, action, by, at) = (
            seat.to_owned(),
            s.clone(),
            action.to_owned(),
            by.to_owned(),
            at.to_owned(),
        );
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(old) = row_of(&tx, &seat)? else {
                return Ok(PermWrite::Unknown);
            };
            tx.execute(
                "UPDATE seat_perm SET pending_tpl_uid = ?2, pending_rev = ?3, pending_base = ?4,
                 pending_items = ?5, pending_at = ?6, pending_by = ?7 WHERE seat_id = ?1",
                params![seat, s.tpl_uid, s.tpl_rev, s.base, s.items_json, at, by],
            )?;
            let id = log_row(
                &tx,
                &seat,
                &action,
                Some(state_json(&old.now)),
                Some(state_json(&s)),
                &at,
                &by,
            )?;
            tx.commit()?;
            Ok(PermWrite::Done(id))
        })
    }

    /// At the seat's restart: the waiting change becomes what it runs with (`None`: nothing was
    /// waiting, or the seat has no row).
    pub fn seat_perm_take_pending(
        &self,
        seat: &str,
        by: &str,
        at: &str,
    ) -> Result<Option<PermState>> {
        who_checked(seat, by, at)?;
        let (seat, by, at) = (seat.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(old) = row_of(&tx, &seat)? else {
                return Ok(None);
            };
            let Some(next) = old.pending.clone() else {
                return Ok(None);
            };
            tx.execute(
                "UPDATE seat_perm SET tpl_uid = ?2, tpl_rev = ?3, base = ?4, items_json = ?5,
                 set_at = ?6, set_by = ?7, pending_tpl_uid = NULL, pending_rev = NULL,
                 pending_base = NULL, pending_items = NULL, pending_at = NULL, pending_by = NULL
                 WHERE seat_id = ?1",
                params![
                    seat,
                    next.tpl_uid,
                    next.tpl_rev,
                    next.base,
                    next.items_json,
                    at,
                    by
                ],
            )?;
            log_row(
                &tx,
                &seat,
                "restart_applied",
                Some(state_json(&old.now)),
                Some(state_json(&next)),
                &at,
                &by,
            )?;
            tx.commit()?;
            Ok(Some(next))
        })
    }

    /// A seat's row.
    pub fn seat_perm(&self, seat: &str) -> Result<Option<SeatPerm>> {
        let c = self.read()?;
        Ok(row_of(&c, seat)?)
    }

    /// A seat's log, newest first, at most [`SEAT_PERM_LOG_MAX`].
    pub fn seat_perm_log(&self, seat: &str) -> Result<Vec<SeatPermLog>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT log_id, seat_id, action, from_json, to_json, at, by, revoked_at, revoked_by
             FROM seat_perm_log WHERE seat_id = ?1 ORDER BY log_id DESC LIMIT ?2",
        )?;
        let rows = st
            .query_map(params![seat, SEAT_PERM_LOG_MAX], |r| {
                Ok(SeatPermLog {
                    log_id: r.get(0)?,
                    seat_id: r.get(1)?,
                    action: r.get(2)?,
                    from_json: r.get(3)?,
                    to_json: r.get(4)?,
                    at: r.get(5)?,
                    by: r.get(6)?,
                    revoked_at: r.get(7)?,
                    revoked_by: r.get(8)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    /// Mark a log row as taken back (收回); `false`: no such row of this seat, or taken back
    /// already. What taking back changes is aibd's (it pends the row's `from` state).
    pub fn seat_perm_log_revoked(
        &self,
        seat: &str,
        log_id: i64,
        by: &str,
        at: &str,
    ) -> Result<bool> {
        who_checked(seat, by, at)?;
        let (seat, by, at) = (seat.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            let n = c.execute(
                "UPDATE seat_perm_log SET revoked_at = ?3, revoked_by = ?4
                 WHERE seat_id = ?1 AND log_id = ?2 AND revoked_at IS NULL",
                params![seat, log_id, at, by],
            )?;
            Ok(n == 1)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, MessageDb) {
        let d = tempfile::tempdir().expect("tempdir");
        let m = MessageDb::open(&d.path().join("m.db")).expect("open");
        (d, m)
    }

    fn st(rev: i64, items: &str) -> PermState {
        PermState {
            tpl_uid: "tpl_a".into(),
            tpl_rev: rev,
            base: "work".into(),
            items_json: items.into(),
        }
    }

    /// SP-S1: an open is written once; a change waits for the restart, which takes it once;
    /// every step is logged with from / to.
    #[test]
    fn a_change_waits_for_the_restart_and_is_logged() {
        let (_d, m) = db();
        let a = st(1, r#"{"brain":true,"chat":true}"#);
        let b = st(2, r#"{"brain":false,"chat":true}"#);
        assert!(matches!(
            m.seat_perm_open("s1", &a, "page:x", "t1").unwrap(),
            PermWrite::Done(_)
        ));
        assert_eq!(
            m.seat_perm_open("s1", &b, "page:x", "t1").unwrap(),
            PermWrite::Taken,
            "reason: open_written_twice"
        );
        assert_eq!(
            m.seat_perm_pend("s9", &b, "lower", "page:x", "t2").unwrap(),
            PermWrite::Unknown
        );
        assert!(matches!(
            m.seat_perm_pend("s1", &b, "lower", "page:x", "t2").unwrap(),
            PermWrite::Done(_)
        ));
        let row = m.seat_perm("s1").unwrap().unwrap();
        assert_eq!(row.now, a, "reason: change_took_effect_before_restart");
        assert_eq!(row.pending.as_ref(), Some(&b));
        assert_eq!(
            m.seat_perm_take_pending("s1", "aibd", "t3").unwrap(),
            Some(b.clone())
        );
        let row = m.seat_perm("s1").unwrap().unwrap();
        assert_eq!(row.now, b);
        assert_eq!(row.pending, None);
        assert_eq!(m.seat_perm_take_pending("s1", "aibd", "t4").unwrap(), None);
        let log = m.seat_perm_log("s1").unwrap();
        let acts: Vec<&str> = log.iter().map(|l| l.action.as_str()).collect();
        assert_eq!(acts, ["restart_applied", "lower", "open"]);
        assert!(log[1]
            .from_json
            .as_deref()
            .unwrap()
            .contains(r#""brain":true"#));
        assert!(log[1]
            .to_json
            .as_deref()
            .unwrap()
            .contains(r#""brain":false"#));
    }

    /// SP-S2: a log row is taken back once, and only the seat's own.
    #[test]
    fn a_log_row_is_taken_back_once() {
        let (_d, m) = db();
        let PermWrite::Done(id) = m
            .seat_perm_open("s1", &st(1, r#"{"chat":true}"#), "page:x", "t1")
            .unwrap()
        else {
            panic!("reason: not_opened")
        };
        assert!(!m.seat_perm_log_revoked("s2", id, "page:x", "t2").unwrap());
        assert!(m.seat_perm_log_revoked("s1", id, "page:x", "t2").unwrap());
        assert!(
            !m.seat_perm_log_revoked("s1", id, "page:x", "t3").unwrap(),
            "reason: taken_back_twice"
        );
        assert_eq!(
            m.seat_perm_log("s1").unwrap()[0].revoked_at.as_deref(),
            Some("t2")
        );
    }

    /// SP-S3: a bad shape is refused before anything is written.
    #[test]
    fn a_bad_shape_is_refused() {
        let (_d, m) = db();
        assert!(m
            .seat_perm_open("s1", &st(1, "[]"), "page:x", "t1")
            .is_err());
        assert!(m
            .seat_perm_open("s 1", &st(1, "{}"), "page:x", "t1")
            .is_err());
        assert!(m
            .seat_perm_pend("s1", &st(1, "{}"), "raise", "page:x", "t1")
            .is_err());
        assert_eq!(m.seat_perm("s1").unwrap(), None);
    }
}
