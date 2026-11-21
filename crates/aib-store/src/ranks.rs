//! Who may mark a letter urgent (DECISION-council-webinbox-519d0-1121 r2 「誰能標急」;
//! OPINION-role-rank-a9136-1121 r2; 5d33a PREREAD-inboxaibd F r2): a role's rank, the ranks
//! the owner granted a seat, and the least rank an act needs. Three tables in the message
//! library's own file, only ever added (an older aibd never reads them), written only by
//! aibd's fingerprint acts; nothing here is read from a file an AI can edit (M1), and a seat's
//! own node assignments never give it a rank (M2: only [`MessageDb::rank_grant`] does).
//!
//! A seat's rank is the highest rank among the roles granted to it and not taken back; it is
//! [`Rank::Plain`] when there is none or the library cannot be read. A seat is never
//! [`Rank::Owner`] (that is the person; a role is set to `Lead` at most). An act with no
//! least rank set, or a library that cannot be read, needs [`Rank::Lead`] (Q5).

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS role_ranks (
    role_uid TEXT PRIMARY KEY,
    rank     INTEGER NOT NULL,
    set_at   TEXT NOT NULL,
    set_by   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rank_grants (
    seat_id    TEXT NOT NULL,
    role_uid   TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS rank_grants_live ON rank_grants (seat_id, role_uid)
    WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS perms (
    act      TEXT PRIMARY KEY,
    min_rank INTEGER NOT NULL,
    set_at   TEXT NOT NULL,
    set_by   TEXT NOT NULL
);
";

/// Add the tables when they are missing (every open; nothing when they are there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// 一般 < 主管 < 擁有者.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Rank {
    Plain,
    Lead,
    Owner,
}

impl Rank {
    pub fn word(self) -> &'static str {
        match self {
            Rank::Plain => "plain",
            Rank::Lead => "lead",
            Rank::Owner => "owner",
        }
    }

    pub fn from_word(w: &str) -> Option<Rank> {
        match w {
            "plain" => Some(Rank::Plain),
            "lead" => Some(Rank::Lead),
            "owner" => Some(Rank::Owner),
            _ => None,
        }
    }

    fn num(self) -> i64 {
        match self {
            Rank::Plain => 0,
            Rank::Lead => 1,
            Rank::Owner => 2,
        }
    }

    /// A stored number; anything unknown reads as the lowest.
    fn of_num(n: i64) -> Rank {
        Rank::known(n).unwrap_or(Rank::Plain)
    }

    fn known(n: i64) -> Option<Rank> {
        match n {
            0 => Some(Rank::Plain),
            1 => Some(Rank::Lead),
            2 => Some(Rank::Owner),
            _ => None,
        }
    }
}

/// What an act's least rank is when none is set or it cannot be read (Q5: not open to all).
pub const DEFAULT_MIN: Rank = Rank::Lead;
/// The act a letter is marked urgent by.
pub const URGENT: &str = "urgent";

/// A role uid, a seat id or an act name: 1–64 of `[A-Za-z0-9_.:-]`.
pub fn key_ok(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b':' | b'-'))
}

fn need(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(StoreError::Invalid(why))
    }
}

/// The highest live grant's role rank, at most Lead.
fn seat_rank_in(c: &Connection, seat_id: &str) -> rusqlite::Result<Rank> {
    let n: Option<i64> = c
        .query_row(
            "SELECT MAX(r.rank) FROM rank_grants g JOIN role_ranks r ON r.role_uid = g.role_uid
             WHERE g.seat_id = ?1 AND g.revoked_at IS NULL",
            params![seat_id],
            |r| r.get(0),
        )
        .optional()?
        .flatten();
    Ok(Rank::of_num(n.unwrap_or(0)).min(Rank::Lead))
}

/// One live grant, for the page (`role_rank` is the role's rank now; `None` when the role has
/// no rank set any more).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grant {
    pub role_uid: String,
    pub role_rank: Option<Rank>,
    pub granted_at: String,
    pub granted_by: String,
}

impl MessageDb {
    /// Set role `role_uid`'s rank (the caller has the owner's fingerprint). A role is Lead at
    /// most: Owner is the person, never a seat.
    pub fn role_rank_set(&self, role_uid: &str, rank: Rank, by: &str, at: &str) -> Result<()> {
        need(key_ok(role_uid), "a role uid is 1-64 of A-Za-z0-9_.:-")?;
        need(
            rank <= Rank::Lead,
            "a role is lead at most; owner is the person",
        )?;
        need(!by.is_empty() && !at.is_empty(), "by and time are needed")?;
        let (role, by, at) = (role_uid.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            c.execute(
                "INSERT INTO role_ranks (role_uid, rank, set_at, set_by) VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(role_uid) DO UPDATE SET rank = ?2, set_at = ?3, set_by = ?4",
                params![role, rank.num(), at, by],
            )?;
            Ok(())
        })
    }

    /// Grant seat `seat_id` role `role_uid` (the caller has the owner's fingerprint). `false`:
    /// it was granted already (nothing written).
    pub fn rank_grant(&self, seat_id: &str, role_uid: &str, by: &str, at: &str) -> Result<bool> {
        need(
            key_ok(seat_id) && key_ok(role_uid),
            "seat and role are 1-64 of A-Za-z0-9_.:-",
        )?;
        need(!by.is_empty() && !at.is_empty(), "by and time are needed")?;
        let (seat, role, by, at) = (
            seat_id.to_owned(),
            role_uid.to_owned(),
            by.to_owned(),
            at.to_owned(),
        );
        self.write(move |c| {
            let n = c.execute(
                "INSERT OR IGNORE INTO rank_grants (seat_id, role_uid, granted_at, granted_by)
                 VALUES (?1, ?2, ?3, ?4)",
                params![seat, role, at, by],
            )?;
            Ok(n == 1)
        })
    }

    /// Take a grant back at `at` (the row is kept). `false`: there was no live grant.
    pub fn rank_revoke(&self, seat_id: &str, role_uid: &str, at: &str) -> Result<bool> {
        need(
            key_ok(seat_id) && key_ok(role_uid),
            "seat and role are 1-64 of A-Za-z0-9_.:-",
        )?;
        need(!at.is_empty(), "a time is needed")?;
        let (seat, role, at) = (seat_id.to_owned(), role_uid.to_owned(), at.to_owned());
        self.write(move |c| {
            let n = c.execute(
                "UPDATE rank_grants SET revoked_at = ?3
                 WHERE seat_id = ?1 AND role_uid = ?2 AND revoked_at IS NULL",
                params![seat, role, at],
            )?;
            Ok(n == 1)
        })
    }

    /// Set the least rank act `act` needs (the caller has the owner's fingerprint).
    pub fn perm_set(&self, act: &str, min: Rank, by: &str, at: &str) -> Result<()> {
        need(key_ok(act), "an act is 1-64 of A-Za-z0-9_.:-")?;
        need(!by.is_empty() && !at.is_empty(), "by and time are needed")?;
        let (act, by, at) = (act.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            c.execute(
                "INSERT INTO perms (act, min_rank, set_at, set_by) VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(act) DO UPDATE SET min_rank = ?2, set_at = ?3, set_by = ?4",
                params![act, min.num(), at, by],
            )?;
            Ok(())
        })
    }

    /// The live grants of `seat_id`, oldest first.
    pub fn rank_grants(&self, seat_id: &str) -> Result<Vec<Grant>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT g.role_uid, r.rank, g.granted_at, g.granted_by FROM rank_grants g
             LEFT JOIN role_ranks r ON r.role_uid = g.role_uid
             WHERE g.seat_id = ?1 AND g.revoked_at IS NULL ORDER BY g.granted_at, g.rowid",
        )?;
        let rows = st.query_map(params![seat_id], |r| {
            Ok(Grant {
                role_uid: r.get(0)?,
                role_rank: r.get::<_, Option<i64>>(1)?.map(Rank::of_num),
                granted_at: r.get(2)?,
                granted_by: r.get(3)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Role `role_uid`'s rank, `None` when none is set.
    pub fn role_rank(&self, role_uid: &str) -> Result<Option<Rank>> {
        let c = self.read()?;
        Ok(c.query_row(
            "SELECT rank FROM role_ranks WHERE role_uid = ?1",
            params![role_uid],
            |r| r.get::<_, i64>(0),
        )
        .optional()?
        .map(Rank::of_num))
    }
}

/// A seat's rank (Plain when the library cannot be read: a failure falls low).
pub fn seat_rank(db: &MessageDb, seat_id: &str) -> Rank {
    db.read()
        .ok()
        .and_then(|c| seat_rank_in(&c, seat_id).ok())
        .unwrap_or(Rank::Plain)
}

/// The least rank `act` needs ([`DEFAULT_MIN`] when none is set or it cannot be read).
pub fn perm_min(db: &MessageDb, act: &str) -> Rank {
    db.read()
        .ok()
        .and_then(|c| {
            c.query_row(
                "SELECT min_rank FROM perms WHERE act = ?1",
                params![act],
                |r| r.get::<_, i64>(0),
            )
            .optional()
            .ok()
            .flatten()
        })
        // an unknown stored number is no lower threshold (5d33a REVIEW-rank 9f58554c N1)
        .and_then(Rank::known)
        .unwrap_or(DEFAULT_MIN)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, MessageDb) {
        let d = tempfile::tempdir().expect("tempdir");
        let m = MessageDb::open(&d.path().join("m.db")).expect("open");
        (d, m)
    }

    /// RK1: a seat's rank is its highest live granted role, Plain with none, back to Plain
    /// when the grant is taken back.
    #[test]
    fn a_seat_has_the_highest_rank_the_owner_granted() {
        let (_d, m) = db();
        assert_eq!(seat_rank(&m, "s1"), Rank::Plain);
        m.role_rank_set("lead1", Rank::Lead, "owner", "t1").unwrap();
        m.role_rank_set("author", Rank::Plain, "owner", "t1")
            .unwrap();
        assert!(m.rank_grant("s1", "author", "owner", "t2").unwrap());
        assert_eq!(seat_rank(&m, "s1"), Rank::Plain);
        assert!(m.rank_grant("s1", "lead1", "owner", "t3").unwrap());
        assert!(!m.rank_grant("s1", "lead1", "owner", "t4").unwrap());
        assert_eq!(seat_rank(&m, "s1"), Rank::Lead, "reason: highest_not_taken");
        assert_eq!(seat_rank(&m, "s2"), Rank::Plain);
        assert!(m.rank_revoke("s1", "lead1", "t5").unwrap());
        assert!(!m.rank_revoke("s1", "lead1", "t6").unwrap());
        assert_eq!(
            seat_rank(&m, "s1"),
            Rank::Plain,
            "reason: revoked_still_counts"
        );
        let g = m.rank_grants("s1").unwrap();
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].role_uid, "author");
    }

    /// RK2: a role is Lead at most, and a seat never reads as Owner even from a stored 2.
    #[test]
    fn a_seat_is_never_the_owner() {
        let (_d, m) = db();
        assert!(m.role_rank_set("boss", Rank::Owner, "owner", "t").is_err());
        m.write(|c| {
            c.execute(
                "INSERT INTO role_ranks (role_uid, rank, set_at, set_by) VALUES ('boss', 2, 't', 'x')",
                [],
            )?;
            Ok(())
        })
        .unwrap();
        m.rank_grant("s1", "boss", "owner", "t").unwrap();
        assert_eq!(seat_rank(&m, "s1"), Rank::Lead, "reason: seat_reads_owner");
    }

    /// RK3 (Q5): an act with no least rank needs Lead; a set one is what was set.
    #[test]
    fn an_act_with_no_least_rank_needs_a_lead() {
        let (_d, m) = db();
        assert_eq!(
            perm_min(&m, URGENT),
            Rank::Lead,
            "reason: unset_open_to_all"
        );
        m.perm_set(URGENT, Rank::Plain, "owner", "t").unwrap();
        assert_eq!(perm_min(&m, URGENT), Rank::Plain);
        m.perm_set(URGENT, Rank::Owner, "owner", "t2").unwrap();
        assert_eq!(perm_min(&m, URGENT), Rank::Owner);
        // a number no rank has reads as the default, never as plain
        m.write(|c| {
            c.execute("UPDATE perms SET min_rank = 7 WHERE act = 'urgent'", [])?;
            Ok(())
        })
        .unwrap();
        assert_eq!(perm_min(&m, URGENT), Rank::Lead, "reason: unknown_opens");
    }

    /// RK4: a granted role whose rank is gone counts as nothing (N4: still listed).
    #[test]
    fn a_grant_of_a_role_with_no_rank_counts_as_plain() {
        let (_d, m) = db();
        m.rank_grant("s1", "gone", "owner", "t").unwrap();
        assert_eq!(seat_rank(&m, "s1"), Rank::Plain);
        assert_eq!(m.rank_grants("s1").unwrap()[0].role_rank, None);
    }

    #[test]
    fn only_plain_keys_are_taken() {
        let (_d, m) = db();
        for bad in ["", "a b", "x/y", "é", &"a".repeat(65)] {
            assert!(m.rank_grant(bad, "r", "owner", "t").is_err(), "{bad}");
            assert!(m.perm_set(bad, Rank::Lead, "owner", "t").is_err(), "{bad}");
        }
        assert_eq!(Rank::from_word("lead"), Some(Rank::Lead));
        assert_eq!(Rank::from_word("LEAD"), None);
    }
}
