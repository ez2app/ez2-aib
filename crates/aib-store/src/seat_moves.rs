//! Which web seat a 1.x seat moved into (G5 信橋, DESIGN-g5-bridge-7ebca-1121 r1 §1; 5d33a
//! PREREAD-g5-bridge M1): the only place the bridge and G3 learn a web seat's old five-letter
//! code. Written only by aibd's fingerprint act (`seat_move` / `seat_unmove`), never from a
//! file an AI could edit.
//!
//! In the message library's own file as a table that is only ever added (like the thread
//! tables): an older aibd never reads it. A move is a row; going back stamps `unmoved_at` and
//! keeps the row (the switch record, DECISION-council-cutover §4-2 / -3). At most one live row
//! per old code and per seat (partial unique indexes).

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS seat_moves (
    old_code   TEXT NOT NULL,
    seat_id    TEXT NOT NULL,
    moved_at   TEXT NOT NULL,
    moved_by   TEXT NOT NULL,
    unmoved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS seat_moves_code_live ON seat_moves (old_code)
    WHERE unmoved_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS seat_moves_seat_live ON seat_moves (seat_id)
    WHERE unmoved_at IS NULL;
CREATE INDEX IF NOT EXISTS messages_from ON messages (from_key, created_unix);
";

/// Add the table when it is missing (every open; nothing when it is there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// A 1.x seat code: five lower-case hex.
pub fn code_ok(code: &str) -> bool {
    code.len() == 5
        && code
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// What [`MessageDb::seat_move`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Moved {
    /// Written now.
    Moved,
    /// This exact move is already live: nothing written (a retry).
    Already,
    /// The old code is already moved into another seat: nothing written.
    CodeTaken { seat_id: String },
    /// The seat already stands for another old code: nothing written.
    SeatTaken { old_code: String },
}

fn live_seat(c: &Connection, old_code: &str) -> rusqlite::Result<Option<String>> {
    c.query_row(
        "SELECT seat_id FROM seat_moves WHERE old_code = ?1 AND unmoved_at IS NULL",
        params![old_code],
        |r| r.get(0),
    )
    .optional()
}

fn live_code(c: &Connection, seat_id: &str) -> rusqlite::Result<Option<String>> {
    c.query_row(
        "SELECT old_code FROM seat_moves WHERE seat_id = ?1 AND unmoved_at IS NULL",
        params![seat_id],
        |r| r.get(0),
    )
    .optional()
}

impl MessageDb {
    /// Record that 1.x seat `old_code` now lives in web seat `seat_id`. The caller has
    /// checked the owner's fingerprint and that `seat_id` is a seat of this aibd.
    pub fn seat_move(&self, old_code: &str, seat_id: &str, by: &str, at: &str) -> Result<Moved> {
        if !code_ok(old_code) {
            return Err(StoreError::Invalid("an old seat code is five lower hex"));
        }
        if seat_id.is_empty() || by.is_empty() || at.is_empty() {
            return Err(StoreError::Invalid("seat, by and time are needed"));
        }
        let (code, seat, by, at) = (
            old_code.to_string(),
            seat_id.to_string(),
            by.to_string(),
            at.to_string(),
        );
        self.write(move |c| {
            let tx = c.transaction()?;
            let have_seat = live_seat(&tx, &code)?;
            let have_code = live_code(&tx, &seat)?;
            let out = match (have_seat, have_code) {
                (Some(s), _) if s == seat => Moved::Already,
                (Some(s), _) => Moved::CodeTaken { seat_id: s },
                (None, Some(o)) => Moved::SeatTaken { old_code: o },
                (None, None) => {
                    tx.execute(
                        "INSERT INTO seat_moves (old_code, seat_id, moved_at, moved_by)
                         VALUES (?1, ?2, ?3, ?4)",
                        params![code, seat, at, by],
                    )?;
                    Moved::Moved
                }
            };
            tx.commit()?;
            Ok(out)
        })
    }

    /// Going back: the live move of `old_code` ends at `at` (the row is kept). The web seat
    /// it went to, or `None` when it was not moved.
    pub fn seat_unmove(&self, old_code: &str, at: &str) -> Result<Option<String>> {
        if !code_ok(old_code) {
            return Err(StoreError::Invalid("an old seat code is five lower hex"));
        }
        let (code, at) = (old_code.to_string(), at.to_string());
        self.write(move |c| {
            let tx = c.transaction()?;
            let seat = live_seat(&tx, &code)?;
            if seat.is_some() {
                tx.execute(
                    "UPDATE seat_moves SET unmoved_at = ?2
                     WHERE old_code = ?1 AND unmoved_at IS NULL",
                    params![code, at],
                )?;
            }
            tx.commit()?;
            Ok(seat)
        })
    }

    /// The old code web seat `seat_id` stands for now (a live move only).
    pub fn moved_code(&self, seat_id: &str) -> Result<Option<String>> {
        let c = self.read()?;
        Ok(live_code(&c, seat_id)?)
    }

    /// The web seat `old_code` last went back from (its move ended), `None` while it is moved
    /// or never was: going back again tidies that seat's inbox (5d33a REVIEW-g5um L1).
    pub fn last_unmoved_seat(&self, old_code: &str) -> Result<Option<String>> {
        let c = self.read()?;
        if live_seat(&c, old_code)?.is_some() {
            return Ok(None);
        }
        Ok(c.query_row(
            "SELECT seat_id FROM seat_moves WHERE old_code = ?1 AND unmoved_at IS NOT NULL
             ORDER BY unmoved_at DESC LIMIT 1",
            params![old_code],
            |r| r.get(0),
        )
        .optional()?)
    }

    /// The web seat 1.x seat `old_code` moved into now (a live move only).
    pub fn moved_seat(&self, old_code: &str) -> Result<Option<String>> {
        let c = self.read()?;
        Ok(live_seat(&c, old_code)?)
    }
}

/// One message handed on to another receiver (DESIGN-web-inbox-aibd-7ebca-1121 r1 B: a letter
/// still open in a 1.x ledger when its code moves goes to the web seat, one row for one
/// letter): its receiver, item and text, at `expect_rev`. The thread stays: the history is
/// where it was.
pub struct Forward<'a> {
    pub to_key: &'a str,
    pub item: serde_json::Map<String, serde_json::Value>,
    pub body: &'a str,
    pub body_sha256: &'a str,
}

impl MessageDb {
    /// What `from_key` sent, newest first, at most `limit` ([`crate::PAGE_MAX`] at most): a
    /// seat's sent box (DESIGN-web-inbox-aibd-7ebca-1121 r1 E).
    pub fn sent_by(&self, from_key: &str, limit: u32) -> Result<Vec<crate::MessageRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {} FROM messages WHERE from_key = ?1
             ORDER BY created_unix DESC, rowid DESC LIMIT ?2",
            crate::messages::COLS
        ))?;
        let rows = st.query_map(
            params![from_key, i64::from(limit.clamp(1, crate::PAGE_MAX))],
            crate::messages::from_row,
        )?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// How many letters `from_key` sent at `level` after `since_unix` (`created_unix`): a
    /// count in SQL, not a page (待審 1.5 期 急件上限, 5d33a REVIEW-urgent N1).
    pub fn sent_at_level_since(&self, from_key: &str, level: &str, since_unix: i64) -> Result<u64> {
        let c = self.read()?;
        let n: i64 = c.query_row(
            "SELECT COUNT(*) FROM messages WHERE from_key = ?1 AND created_unix > ?2
             AND json_extract(item, '$.level') = ?3",
            params![from_key, since_unix, level],
            |r| r.get(0),
        )?;
        Ok(u64::try_from(n).unwrap_or(0))
    }

    /// Write `f` onto the row at `expect_rev` (as [`MessageDb::update`], with the receiver and
    /// the text too).
    pub fn forward(
        &self,
        message_id: &str,
        expect_rev: i64,
        f: Forward<'_>,
        updated: &str,
    ) -> Result<crate::Updated> {
        let (id, updated) = (message_id.to_string(), updated.to_string());
        let (to, body, sha) = (
            f.to_key.to_string(),
            f.body.to_string(),
            f.body_sha256.to_string(),
        );
        let item = serde_json::Value::Object(f.item).to_string();
        let len = i64::try_from(body.len()).unwrap_or(i64::MAX);
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(now) = crate::messages::get_in(&tx, &id)? else {
                return Ok(crate::Updated::Missing);
            };
            if now.rev != expect_rev {
                return Ok(crate::Updated::Stale(Box::new(now)));
            }
            tx.execute(
                "UPDATE messages SET to_key = ?1, item = ?2, body = ?3, body_sha256 = ?4,
                 len = ?5, rev = rev + 1, updated = ?6, journaled = 0 WHERE message_id = ?7",
                params![to, item, body, sha, len, updated, id],
            )?;
            let after =
                crate::messages::get_in(&tx, &id)?.ok_or(StoreError::Invalid("row vanished"))?;
            tx.commit()?;
            Ok(crate::Updated::Done(Box::new(after)))
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

    #[test]
    fn a_move_is_one_live_row_each_way_and_going_back_keeps_it() {
        let (_d, m) = db();
        assert_eq!(m.moved_code("s1").unwrap(), None);
        assert_eq!(
            m.seat_move("7ebca", "s1", "owner", "t1").unwrap(),
            Moved::Moved
        );
        assert_eq!(
            m.seat_move("7ebca", "s1", "owner", "t2").unwrap(),
            Moved::Already
        );
        assert_eq!(
            m.seat_move("7ebca", "s2", "owner", "t2").unwrap(),
            Moved::CodeTaken {
                seat_id: "s1".into()
            }
        );
        assert_eq!(
            m.seat_move("519d0", "s1", "owner", "t2").unwrap(),
            Moved::SeatTaken {
                old_code: "7ebca".into()
            }
        );
        assert_eq!(m.moved_code("s1").unwrap().as_deref(), Some("7ebca"));
        assert_eq!(m.moved_seat("7ebca").unwrap().as_deref(), Some("s1"));

        assert_eq!(m.seat_unmove("7ebca", "t3").unwrap().as_deref(), Some("s1"));
        assert_eq!(m.moved_code("s1").unwrap(), None);
        assert_eq!(m.moved_seat("7ebca").unwrap(), None);
        assert_eq!(m.seat_unmove("7ebca", "t4").unwrap(), None);
        // moved again after going back: a new live row, the old one kept
        assert_eq!(
            m.seat_move("7ebca", "s2", "owner", "t5").unwrap(),
            Moved::Moved
        );
        let c = m.read().unwrap();
        let n: i64 = c
            .query_row("SELECT count(*) FROM seat_moves", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 2);
    }

    #[test]
    fn only_a_five_hex_code_is_taken() {
        let (_d, m) = db();
        for bad in ["7EBCA", "7ebc", "7ebcaa", "../x1", "7ebcg", ""] {
            assert!(m.seat_move(bad, "s1", "owner", "t").is_err(), "{bad}");
        }
        assert!(m.seat_move("7ebca", "", "owner", "t").is_err());
    }
}
