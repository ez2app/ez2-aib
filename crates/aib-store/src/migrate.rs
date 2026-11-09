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
