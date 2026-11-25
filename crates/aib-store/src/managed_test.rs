//! Snapshot storage for A5 managed test hosts (`kind=test`): stores normalized SHA-256 line
//! hashes of `~/.ssh/authorized_keys` so `revoke` can diff before/after (`HM18`).

use crate::{params, Connection, MessageDb, OptionalExtension, Result, StoreError};

pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS test_host_snapshots (
            host_name TEXT PRIMARY KEY,
            lines_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );",
    )?;
    Ok(())
}

impl MessageDb {
    pub fn test_host_snapshot_set(
        &self,
        host_name: &str,
        lines: &[String],
        now: i64,
    ) -> Result<()> {
        if host_name.is_empty() {
            return Err(StoreError::Invalid("empty_host_name"));
        }
        let host = host_name.to_owned();
        let json = serde_json::to_string(lines).map_err(|_| StoreError::Invalid("lines_json"))?;
        self.write(move |conn| {
            conn.execute(
                "INSERT INTO test_host_snapshots (host_name, lines_json, updated_at)
                 VALUES (?1, ?2, ?3)
                 ON CONFLICT(host_name) DO UPDATE SET lines_json = excluded.lines_json, updated_at = excluded.updated_at",
                params![host, json, now],
            )?;
            Ok(())
        })
    }

    pub fn test_host_snapshot_get(&self, host_name: &str) -> Result<Option<Vec<String>>> {
        let conn = self.read()?;
        let raw: Option<String> = conn
            .query_row(
                "SELECT lines_json FROM test_host_snapshots WHERE host_name = ?1",
                [host_name],
                |r| r.get(0),
            )
            .optional()?;
        match raw {
            Some(s) => {
                let parsed: Vec<String> =
                    serde_json::from_str(&s).map_err(|_| StoreError::Invalid("lines_json"))?;
                Ok(Some(parsed))
            }
            None => Ok(None),
        }
    }

    pub fn test_host_snapshot_del(&self, host_name: &str) -> Result<bool> {
        let host = host_name.to_owned();
        self.write(move |conn| {
            let n = conn.execute(
                "DELETE FROM test_host_snapshots WHERE host_name = ?1",
                [host],
            )?;
            Ok(n > 0)
        })
    }
}
