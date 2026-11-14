//! `Idempotency-Key` for writes that do something outside the store first (start or stop
//! a process), so the key cannot be written in the same transaction as the result
//! (seats can: [`Store::create_seat`]). Three steps on the same `idempotency_keys` row
//! (spec 8, ADR 0003 "an `Idempotency-Key` header on every write"; T81):
//!
//! 1. [`Store::idem_claim`] before the work: no row ⇒ a row with status
//!    [`IN_PROGRESS`] is written and the caller does the work; a finished row for the
//!    same request ⇒ its answer is replayed; another request ⇒ conflict; a row still in
//!    progress ⇒ the caller must not do the work again (another request with this key
//!    is running, or aibd stopped in the middle of it).
//! 2. [`Store::idem_finish`] after a 2xx: the status and answer go into the row.
//! 3. [`Store::idem_release`] after anything else: the row goes, so a retry may try
//!    again (nothing was created).
//!
//! A row left in progress by a crash stays: whether the first request started anything
//! cannot be known from here, so the key never starts a second one (the caller sends a
//! new key once it has looked).

use crate::seats::IdemKey;
use crate::{Result, Store};
use rusqlite::{params, OptionalExtension};

/// `status` of a row whose request has not answered yet.
pub const IN_PROGRESS: u16 = 0;

/// What [`Store::idem_claim`] found.
#[derive(Debug, Clone, PartialEq)]
pub enum IdemClaim {
    /// No row before: one is now in progress and the caller does the work.
    Fresh,
    /// Finished before for the same request: answer this, do nothing.
    Replay {
        status: u16,
        response: serde_json::Value,
    },
    /// Used before for a different request.
    Conflict,
    /// The same key is in progress (running now, or cut off by a stop of aibd).
    InProgress,
}

impl Store {
    /// The row of an `Idempotency-Key`, read only: `(request, status, response)`. A
    /// caller that writes its key in the same transaction as its result (a seat open)
    /// looks first, so a retry is answered before anything else is read (043dd B4-1b-1
    /// L1). The write still checks the key again in its transaction.
    pub fn idem_peek(
        &self,
        scope: &str,
        key: &str,
    ) -> Result<Option<(String, u16, serde_json::Value)>> {
        let c = self.read()?;
        let row: Option<(String, u16, String)> = c
            .query_row(
                "SELECT request, status, response FROM idempotency_keys
                 WHERE scope = ?1 AND key = ?2",
                params![scope, key],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?;
        Ok(row.map(|(request, status, response)| {
            let response = serde_json::from_str(&response).unwrap_or(serde_json::Value::Null);
            (request, status, response)
        }))
    }

    pub fn idem_claim(&self, k: &IdemKey, now: &str) -> Result<IdemClaim> {
        let k = k.clone();
        let now = now.to_string();
        self.write(move |c| {
            let tx = c.transaction()?;
            let seen: Option<(String, u16, String)> = tx
                .query_row(
                    "SELECT request, status, response FROM idempotency_keys
                     WHERE scope = ?1 AND key = ?2",
                    params![k.scope, k.key],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .optional()?;
            let claim = match seen {
                Some((request, _, _)) if request != k.request => IdemClaim::Conflict,
                Some((_, IN_PROGRESS, _)) => IdemClaim::InProgress,
                Some((_, status, response)) => IdemClaim::Replay {
                    status,
                    response: serde_json::from_str(&response).unwrap_or(serde_json::Value::Null),
                },
                None => {
                    tx.execute(
                        "INSERT INTO idempotency_keys (scope, key, request, status, response, created_at)
                         VALUES (?1, ?2, ?3, ?4, '{}', ?5)",
                        params![k.scope, k.key, k.request, IN_PROGRESS, now],
                    )?;
                    IdemClaim::Fresh
                }
            };
            tx.commit()?;
            Ok(claim)
        })
    }

    /// Record the answer of a claimed key. `false` = no row in progress for it (nothing
    /// written).
    pub fn idem_finish(
        &self,
        k: &IdemKey,
        status: u16,
        response: &serde_json::Value,
    ) -> Result<bool> {
        let (scope, key, request) = (k.scope.clone(), k.key.clone(), k.request.clone());
        let response = response.to_string();
        self.write(move |c| {
            let n = c.execute(
                "UPDATE idempotency_keys SET status = ?4, response = ?5
                 WHERE scope = ?1 AND key = ?2 AND request = ?3 AND status = ?6",
                params![scope, key, request, status, response, IN_PROGRESS],
            )?;
            Ok(n == 1)
        })
    }

    /// At start: give back every key still in progress whose scope starts with
    /// `scope_prefix` (a write that is safe to do again, e.g. `"DELETE "`). The count
    /// given back.
    pub fn idem_release_unfinished(&self, scope_prefix: &str) -> Result<usize> {
        let prefix = scope_prefix.to_string();
        self.write(move |c| {
            // an exact, case-sensitive prefix (LIKE would fold case and read `_` / `%`)
            Ok(c.execute(
                "DELETE FROM idempotency_keys
                 WHERE status = ?1 AND substr(scope, 1, length(?2)) = ?2",
                params![IN_PROGRESS, prefix],
            )?)
        })
    }

    /// Drop a claimed key that did not end in a 2xx. `false` = no row in progress for it.
    pub fn idem_release(&self, k: &IdemKey) -> Result<bool> {
        let (scope, key, request) = (k.scope.clone(), k.key.clone(), k.request.clone());
        self.write(move |c| {
            let n = c.execute(
                "DELETE FROM idempotency_keys
                 WHERE scope = ?1 AND key = ?2 AND request = ?3 AND status = ?4",
                params![scope, key, request, IN_PROGRESS],
            )?;
            Ok(n == 1)
        })
    }
}
