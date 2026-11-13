//! Who is alive (A11 T46): the last time each cabin was seen running, and
//! one alert per outage. aibd's timer calls [`Store::alive_round`] each
//! period; everything a round decides is written in one transaction on the
//! single writer, so two rounds at once still give one alert (1.x needed a
//! lock across several watches for this).
//!
//! The store does not decide who is alive or who closed on purpose; the
//! caller passes both (process gone = down; "no activity" is not down).

use crate::{Result, Store};
use rusqlite::params;

/// The alert threshold never goes below this (1.x `EZ2_AIB_ALIVE_ALERT_AFTER_S`).
pub const ALIVE_MIN_AFTER_S: i64 = 60;

/// A cabin found down in this round, past the threshold, not reported yet.
/// Its row is already marked alerted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AliveDown {
    pub cabin: String,
    pub last_seen: i64,
    pub gone_for_s: i64,
    pub seats: Vec<String>,
}

/// One row as stored, for the read-only `aib restart alive`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AliveRow {
    pub cabin: String,
    pub last_seen: i64,
    pub seats: Vec<String>,
    pub alerted_at: Option<i64>,
}

fn seats_of(json: &str) -> Vec<String> {
    serde_json::from_str(json).unwrap_or_default()
}

impl Store {
    /// One round at `now` (Unix seconds):
    /// - every cabin in `live` gets `last_seen = now`, its seats, and its
    ///   alert cleared (it is back);
    /// - every cabin in `closed` that is not live loses its row (closed on
    ///   purpose: no alert, not followed any more);
    /// - every other known cabin gone for at least `after_s` (at least
    ///   [`ALIVE_MIN_AFTER_S`]) and not alerted yet is marked alerted now
    ///   and returned, oldest seen first.
    ///
    /// A cabin first seen in this round is only recorded.
    pub fn alive_round(
        &self,
        now: i64,
        live: Vec<(String, Vec<String>)>,
        closed: Vec<String>,
        after_s: i64,
    ) -> Result<Vec<AliveDown>> {
        let after_s = after_s.max(ALIVE_MIN_AFTER_S);
        self.write(move |c| {
            let tx = c.transaction()?;
            for (cabin, seats) in &live {
                tx.execute(
                    "INSERT INTO cabin_alive (cabin, last_seen, seats_json, alerted_at)
                     VALUES (?1, ?2, ?3, NULL)
                     ON CONFLICT (cabin) DO UPDATE SET
                         last_seen = excluded.last_seen, seats_json = excluded.seats_json,
                         alerted_at = NULL",
                    params![cabin, now, serde_json::json!(seats).to_string()],
                )?;
            }
            for cabin in &closed {
                if live.iter().any(|(l, _)| l == cabin) {
                    continue;
                }
                tx.execute("DELETE FROM cabin_alive WHERE cabin = ?1", params![cabin])?;
            }
            let due: Vec<(String, i64, String)> = {
                let mut st = tx.prepare(
                    "SELECT cabin, last_seen, seats_json FROM cabin_alive
                     WHERE last_seen < ?1 AND alerted_at IS NULL AND ?1 - last_seen >= ?2
                     ORDER BY last_seen, cabin",
                )?;
                let rows = st.query_map(params![now, after_s], |r| {
                    Ok((r.get(0)?, r.get(1)?, r.get(2)?))
                })?;
                rows.collect::<std::result::Result<_, _>>()?
            };
            let mut out = Vec::with_capacity(due.len());
            for (cabin, last_seen, seats) in due {
                tx.execute(
                    "UPDATE cabin_alive SET alerted_at = ?2 WHERE cabin = ?1",
                    params![cabin, now],
                )?;
                out.push(AliveDown {
                    gone_for_s: now - last_seen,
                    seats: seats_of(&seats),
                    cabin,
                    last_seen,
                });
            }
            tx.commit()?;
            Ok(out)
        })
    }

    /// Every followed cabin, by name. Read only.
    pub fn alive_rows(&self) -> Result<Vec<AliveRow>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT cabin, last_seen, seats_json, alerted_at FROM cabin_alive ORDER BY cabin",
        )?;
        let rows = st.query_map([], |r| {
            let seats: String = r.get(2)?;
            Ok(AliveRow {
                cabin: r.get(0)?,
                last_seen: r.get(1)?,
                seats: seats_of(&seats),
                alerted_at: r.get(3)?,
            })
        })?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }
}
