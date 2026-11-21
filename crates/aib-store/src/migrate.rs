//! Minimal migration runner over embedded `.sql` files.

use crate::{Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

pub struct Migration {
    pub version: i64,
    pub name: &'static str,
    pub sql: &'static str,
}

/// All migrations this build knows, in order. Never edit an entry that has
/// shipped; add a new one.
pub const MIGRATIONS: &[Migration] = &[
    Migration {
        version: 1,
        name: "events",
        sql: include_str!("../../../migrations/0001_events.sql"),
    },
    Migration {
        version: 2,
        name: "seats",
        sql: include_str!("../../../migrations/0002_seats.sql"),
    },
    Migration {
        version: 3,
        name: "sessions",
        sql: include_str!("../../../migrations/0003_sessions.sql"),
    },
    Migration {
        version: 4,
        name: "seat_meta",
        sql: include_str!("../../../migrations/0004_seat_meta.sql"),
    },
    Migration {
        version: 5,
        name: "seat_open",
        sql: include_str!("../../../migrations/0005_seat_open.sql"),
    },
    Migration {
        version: 6,
        name: "seat_work",
        sql: include_str!("../../../migrations/0006_seat_work.sql"),
    },
];

/// FNV-1a 64-bit: detects edits to an applied migration. Not a security hash.
fn checksum(sql: &str) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in sql.as_bytes() {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{h:016x}")
}

pub fn apply(conn: &mut Connection, migrations: &[Migration]) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS schema_migrations (
             version    INTEGER PRIMARY KEY,
             name       TEXT NOT NULL,
             checksum   TEXT NOT NULL,
             applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
         );",
    )?;
    let max_known = migrations.iter().map(|m| m.version).max().unwrap_or(0);
    let max_applied: i64 = conn.query_row(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations",
        [],
        |r| r.get(0),
    )?;
    if max_applied > max_known {
        return Err(StoreError::UnknownMigration(max_applied));
    }
    for m in migrations {
        let sum = checksum(m.sql);
        let have: Option<String> = conn
            .query_row(
                "SELECT checksum FROM schema_migrations WHERE version = ?1",
                params![m.version],
                |r| r.get(0),
            )
            .optional()?;
        match have {
            Some(s) if s == sum => continue,
            Some(_) => {
                return Err(StoreError::MigrationChecksum {
                    version: m.version,
                    name: m.name.to_string(),
                })
            }
            None => {
                let tx = conn.transaction()?;
                tx.execute_batch(m.sql)?;
                tx.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?1, ?2, ?3)",
                    params![m.version, m.name, sum],
                )?;
                tx.commit()?;
                tracing::info!(version = m.version, name = m.name, "migration applied");
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// 0006 (W1, 0eed7 a2cd0ccc4 H): a database at 0005 keeps every seat_meta row and its
    /// index when seat_meta is rebuilt, and then takes a work seat.
    #[test]
    fn rebuilding_seat_meta_for_work_keeps_its_rows() {
        let mut c = Connection::open_in_memory().unwrap();
        apply(&mut c, &MIGRATIONS[..5]).unwrap();
        c.execute_batch(
            "INSERT INTO seats (seat_id, name, state, created_at, updated_at)
                 VALUES ('seat_a', 'a', 'CREATED', 't', 't');
             INSERT INTO seat_meta (seat_id, provider, cwd, model_pinned, resume_id,
                                    updated_at, mode, cabin)
                 VALUES ('seat_a', 'claude', '/w', 1, 'r1', 't', 'safe', 'dab9');",
        )
        .unwrap();
        assert!(c.execute("UPDATE seat_meta SET mode = 'work'", []).is_err());
        apply(&mut c, MIGRATIONS).unwrap();
        let row: (String, String, i64, String, String) = c
            .query_row(
                "SELECT cwd, resume_id, model_pinned, mode, cabin FROM seat_meta
                 WHERE seat_id = 'seat_a'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .unwrap();
        assert_eq!(
            row,
            ("/w".into(), "r1".into(), 1, "safe".into(), "dab9".into()),
            "reason: rows_lost"
        );
        let idx: i64 = c
            .query_row(
                "SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name = 'seat_meta_resume'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(idx, 1, "reason: index_lost");
        assert!(
            c.execute("UPDATE seat_meta SET mode = 'work'", []).is_ok(),
            "reason: work_refused"
        );
        assert!(c.execute("UPDATE seat_meta SET mode = 'root'", []).is_err());
    }
}
