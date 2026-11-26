//! The notes a work seat wrote into the second brain it is bound to (aibd §3 brain_note,
//! DESIGN-brainnote-08de5-1123 §1d; DECISION-council-aipartner-ebab8-1122 §一-7): one row per
//! note aibd wrote, so a page lists them by seat or by brain and takes one back, or every note
//! of a seat, with one press.
//!
//! In the message library's own file as a table that is only ever added (an older aibd never
//! reads it). Written only by aibd: the row is made after aibd wrote the file itself, and a
//! note taken back keeps its row (`deleted_at` / `deleted_by`). aibd removes only files this
//! table names, and only while their SHA-256 is still the one written here.

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS brain_notes (
    note_id      TEXT PRIMARY KEY,
    seat_id      TEXT NOT NULL,
    persona_uid  TEXT,
    mode         TEXT NOT NULL,
    brain_route  TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    written_at   TEXT NOT NULL,
    written_unix INTEGER NOT NULL,
    deleted_at   TEXT,
    deleted_by   TEXT
);
CREATE INDEX IF NOT EXISTS brain_notes_seat ON brain_notes (seat_id, written_unix);
CREATE INDEX IF NOT EXISTS brain_notes_brain ON brain_notes (brain_route, written_unix);
";

/// Add the table when it is missing (every open; nothing when it is there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// One note aibd wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrainNote {
    pub note_id: String,
    pub seat_id: String,
    pub persona_uid: Option<String>,
    pub mode: String,
    pub brain_route: String,
    /// Inside the brain's folder, `/`-separated (`AIB-notes/<YYYY-MM>/<name>.md`).
    pub rel_path: String,
    pub sha256: String,
    pub bytes: i64,
    pub written_at: String,
    pub written_unix: i64,
    pub deleted_at: Option<String>,
    pub deleted_by: Option<String>,
}

const COLS: &str = "note_id, seat_id, persona_uid, mode, brain_route, rel_path, sha256, bytes, \
                    written_at, written_unix, deleted_at, deleted_by";

fn from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<BrainNote> {
    Ok(BrainNote {
        note_id: r.get(0)?,
        seat_id: r.get(1)?,
        persona_uid: r.get(2)?,
        mode: r.get(3)?,
        brain_route: r.get(4)?,
        rel_path: r.get(5)?,
        sha256: r.get(6)?,
        bytes: r.get(7)?,
        written_at: r.get(8)?,
        written_unix: r.get(9)?,
        deleted_at: r.get(10)?,
        deleted_by: r.get(11)?,
    })
}

fn key_ok(s: &str) -> bool {
    crate::ranks::key_ok(s)
}

/// A path inside a brain: `/`-separated pieces, none empty, `.` or `..`, no `\`, `:`, NUL,
/// `< > " | ? *`, trailing `.` / space, or Windows reserved device names (L6).
pub fn rel_path_ok(p: &str) -> bool {
    !p.is_empty()
        && p.len() <= 400
        && !p.starts_with('/')
        && !p.contains(['\\', ':', '\0', '<', '>', '"', '|', '?', '*'])
        && p.split('/').all(|s| {
            if s.is_empty() || s == "." || s == ".." || s.ends_with('.') || s.ends_with(' ') {
                return false;
            }
            let stem = s.split('.').next().unwrap_or("");
            let up = stem.to_ascii_uppercase();
            !matches!(
                up.as_str(),
                "CON"
                    | "PRN"
                    | "AUX"
                    | "NUL"
                    | "COM1"
                    | "COM2"
                    | "COM3"
                    | "COM4"
                    | "COM5"
                    | "COM6"
                    | "COM7"
                    | "COM8"
                    | "COM9"
                    | "LPT1"
                    | "LPT2"
                    | "LPT3"
                    | "LPT4"
                    | "LPT5"
                    | "LPT6"
                    | "LPT7"
                    | "LPT8"
                    | "LPT9"
            )
        })
}

fn need(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(StoreError::Invalid(why))
    }
}

/// Which notes a list asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NotesOf {
    Seat(String),
    Brain(String),
}

/// The most rows one list gives.
pub const NOTES_LIST_MAX: usize = 500;

impl MessageDb {
    /// Keep the row of a note aibd has just written. A note id already kept is refused.
    pub fn brain_note_add(&self, n: BrainNote) -> Result<()> {
        need(
            key_ok(&n.note_id) && key_ok(&n.seat_id) && key_ok(&n.mode),
            "note, seat and mode are 1-64 of A-Za-z0-9_.:-",
        )?;
        need(
            !n.brain_route.is_empty() && n.brain_route.len() <= 200,
            "brain route is 1-200 bytes",
        )?;
        need(
            rel_path_ok(&n.rel_path),
            "rel_path is a path inside the brain",
        )?;
        need(
            n.sha256.len() == 64 && n.sha256.bytes().all(|b| b.is_ascii_hexdigit()),
            "sha256 is 64 hex",
        )?;
        need(n.deleted_at.is_none(), "a new note is not taken back")?;
        self.write(move |c| {
            c.execute(
                &format!(
                    "INSERT INTO brain_notes ({COLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, \
                     ?10, NULL, NULL)"
                ),
                params![
                    n.note_id,
                    n.seat_id,
                    n.persona_uid,
                    n.mode,
                    n.brain_route,
                    n.rel_path,
                    n.sha256,
                    n.bytes,
                    n.written_at,
                    n.written_unix
                ],
            )?;
            Ok(())
        })
    }

    pub fn brain_note(&self, note_id: &str) -> Result<Option<BrainNote>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {COLS} FROM brain_notes WHERE note_id = ?1"),
            params![note_id],
            from_row,
        )
        .optional()?)
    }

    /// The notes not taken back of a seat or a brain, newest first, at most [`NOTES_LIST_MAX`].
    pub fn brain_notes(&self, of: &NotesOf) -> Result<Vec<BrainNote>> {
        let c = self.read()?;
        let (col, v) = match of {
            NotesOf::Seat(s) => ("seat_id", s.clone()),
            NotesOf::Brain(b) => ("brain_route", b.clone()),
        };
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM brain_notes WHERE {col} = ?1 AND deleted_at IS NULL \
             ORDER BY written_unix DESC, note_id LIMIT {NOTES_LIST_MAX}"
        ))?;
        let rows = st
            .query_map(params![v], from_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    /// How many notes `seat_id` wrote at or after `since_unix` (taken back or not: the quota
    /// counts writes), and how many of its notes are not taken back.
    pub fn brain_note_counts(&self, seat_id: &str, since_unix: i64) -> Result<(i64, i64)> {
        let c = self.read()?;
        let recent: i64 = c.query_row(
            "SELECT COUNT(*) FROM brain_notes WHERE seat_id = ?1 AND written_unix >= ?2",
            params![seat_id, since_unix],
            |r| r.get(0),
        )?;
        let live: i64 = c.query_row(
            "SELECT COUNT(*) FROM brain_notes WHERE seat_id = ?1 AND deleted_at IS NULL",
            params![seat_id],
            |r| r.get(0),
        )?;
        Ok((recent, live))
    }

    /// Stamp a note taken back. `false`: no such note, or it was already taken back.
    pub fn brain_note_taken_back(&self, note_id: &str, by: &str, at: &str) -> Result<bool> {
        need(key_ok(note_id), "note is 1-64 of A-Za-z0-9_.:-")?;
        need(!by.is_empty() && !at.is_empty(), "by and time are needed")?;
        let (id, by, at) = (note_id.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            let n = c.execute(
                "UPDATE brain_notes SET deleted_at = ?2, deleted_by = ?3
                 WHERE note_id = ?1 AND deleted_at IS NULL",
                params![id, at, by],
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

    fn note(id: &str, seat: &str, unix: i64) -> BrainNote {
        BrainNote {
            note_id: id.into(),
            seat_id: seat.into(),
            persona_uid: Some("prs_abc".into()),
            mode: "work".into(),
            brain_route: "folder.group.x.bfecfae2".into(),
            rel_path: format!("AIB-notes/2026-11/{id}.md"),
            sha256: "a".repeat(64),
            bytes: 10,
            written_at: "t".into(),
            written_unix: unix,
            deleted_at: None,
            deleted_by: None,
        }
    }

    /// f163c bnote L2: a seat's list stops at its limit, its count does not (what a take-back of
    /// the whole seat says is still there).
    #[test]
    fn a_seats_count_goes_past_its_list() {
        let (_d, m) = db();
        let n = NOTES_LIST_MAX + 3;
        for i in 0..n {
            m.brain_note_add(note(&format!("bn_{i:016x}"), "seat_a", i as i64))
                .unwrap();
        }
        let of = NotesOf::Seat("seat_a".into());
        assert_eq!(m.brain_notes(&of).unwrap().len(), NOTES_LIST_MAX);
        assert_eq!(m.brain_note_counts("seat_a", i64::MAX).unwrap().1, n as i64);
    }

    /// BN-S1: a note is kept once, listed by seat and by brain, counted, taken back once and
    /// then no longer listed (its row stays).
    #[test]
    fn a_note_is_kept_listed_and_taken_back_once() {
        let (_d, m) = db();
        m.brain_note_add(note("bn_a", "s1", 100)).unwrap();
        m.brain_note_add(note("bn_b", "s1", 200)).unwrap();
        m.brain_note_add(note("bn_c", "s2", 300)).unwrap();
        assert!(
            m.brain_note_add(note("bn_a", "s1", 400)).is_err(),
            "reason: same_note_twice"
        );
        let s1 = m.brain_notes(&NotesOf::Seat("s1".into())).unwrap();
        assert_eq!(
            s1.iter().map(|n| n.note_id.as_str()).collect::<Vec<_>>(),
            ["bn_b", "bn_a"],
            "reason: not_newest_first"
        );
        let all = m
            .brain_notes(&NotesOf::Brain("folder.group.x.bfecfae2".into()))
            .unwrap();
        assert_eq!(all.len(), 3);
        assert_eq!(m.brain_note_counts("s1", 150).unwrap(), (1, 2));
        assert!(m.brain_note_taken_back("bn_a", "page:x", "t2").unwrap());
        assert!(
            !m.brain_note_taken_back("bn_a", "page:x", "t3").unwrap(),
            "reason: taken_back_twice"
        );
        assert_eq!(m.brain_note_counts("s1", 0).unwrap(), (2, 1));
        let a = m.brain_note("bn_a").unwrap().unwrap();
        assert_eq!(
            a.deleted_by.as_deref(),
            Some("page:x"),
            "reason: row_not_kept"
        );
        assert_eq!(m.brain_notes(&NotesOf::Seat("s1".into())).unwrap().len(), 1);
    }

    /// BN-S2: a row naming a path outside the brain, a bad hash or a note born taken back is
    /// refused.
    #[test]
    fn a_bad_row_is_refused() {
        let (_d, m) = db();
        for p in [
            "../x.md",
            "/abs.md",
            "a//b.md",
            "a/./b.md",
            "a\\b.md",
            "",
            "a:b.md",
            "CON.md",
            "sub/nul.txt",
            "LPT1",
            "a/b.",
            "a/b ",
        ] {
            let mut n = note("bn_p", "s1", 1);
            n.rel_path = p.into();
            assert!(m.brain_note_add(n).is_err(), "reason: path_taken {p:?}");
        }
        let mut n = note("bn_h", "s1", 1);
        n.sha256 = "zz".into();
        assert!(m.brain_note_add(n).is_err(), "reason: hash_taken");
        let mut n = note("bn_d", "s1", 1);
        n.deleted_at = Some("t".into());
        assert!(m.brain_note_add(n).is_err(), "reason: born_taken_back");
    }
}
