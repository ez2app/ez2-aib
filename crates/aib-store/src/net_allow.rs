//! Always-allow network exception list in `aib_store` (DECISION-council-netallow-store r1;
//! IFACE-netallow-ticket r2). Every row stores the owner's signed `params` + `sig` + `ticket`;
//! `persona_uid` / `addr` / `until` are never stored as separate trusted columns.

use crate::{params, Connection, MessageDb, OptionalExtension, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetAllowRow {
    pub ticket: String,
    pub by_key: String,
    pub resource: String,
    pub params: Vec<u8>,
    pub sig: Vec<u8>,
    pub approved_at: i64,
}

pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS net_allow_always (
            ticket TEXT PRIMARY KEY,
            by_key TEXT NOT NULL,
            resource TEXT NOT NULL,
            params BLOB NOT NULL,
            sig BLOB NOT NULL,
            approved_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS net_allow_revoked (
            ticket TEXT PRIMARY KEY,
            revoked_at INTEGER NOT NULL
        );",
    )?;
    Ok(())
}

impl MessageDb {
    pub fn net_allow_add(&self, row: NetAllowRow) -> Result<bool> {
        self.write(move |c| {
            let revoked: Option<i64> = c
                .query_row(
                    "SELECT revoked_at FROM net_allow_revoked WHERE ticket = ?1",
                    params![row.ticket],
                    |r| r.get(0),
                )
                .optional()?;
            if revoked.is_some() {
                return Ok(false);
            }
            c.execute(
                "INSERT OR REPLACE INTO net_allow_always (ticket, by_key, resource, params, sig, approved_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    row.ticket,
                    row.by_key,
                    row.resource,
                    row.params,
                    row.sig,
                    row.approved_at
                ],
            )?;
            Ok(true)
        })
    }

    pub fn net_allow_list(&self) -> Result<Vec<NetAllowRow>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT ticket, by_key, resource, params, sig, approved_at
             FROM net_allow_always ORDER BY approved_at DESC, ticket ASC",
        )?;
        let rows = st
            .query_map([], |r| {
                Ok(NetAllowRow {
                    ticket: r.get(0)?,
                    by_key: r.get(1)?,
                    resource: r.get(2)?,
                    params: r.get(3)?,
                    sig: r.get(4)?,
                    approved_at: r.get(5)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    pub fn net_allow_revoke(&self, ticket: &str, revoked_at: i64) -> Result<bool> {
        let t = ticket.to_owned();
        self.write(move |c| {
            let tx = c.transaction()?;
            let changed =
                tx.execute("DELETE FROM net_allow_always WHERE ticket = ?1", params![t])?;
            tx.execute(
                "INSERT OR IGNORE INTO net_allow_revoked (ticket, revoked_at) VALUES (?1, ?2)",
                params![t, revoked_at],
            )?;
            tx.commit()?;
            Ok(changed > 0)
        })
    }

    pub fn net_allow_is_revoked(&self, ticket: &str) -> Result<bool> {
        let c = self.read()?;
        let got: Option<i64> = c
            .query_row(
                "SELECT revoked_at FROM net_allow_revoked WHERE ticket = ?1",
                params![ticket],
                |r| r.get(0),
            )
            .optional()?;
        Ok(got.is_some())
    }
}
