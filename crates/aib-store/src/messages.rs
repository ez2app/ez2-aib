//! The message library (S89-talk-2a, DECISION-council-dmq-519d0-1118): its own database
//! file, `<data>/messages.sqlite`, next to `aibd.sqlite` and never one of its migrations.
//!
//! Why its own file (7b81e): a migration in the main database makes an older aibd refuse
//! to start after a downgrade (`UnknownMigration`). This file has its own `user_version`;
//! an older aibd that does not know it simply never opens it, and the main database is
//! untouched.
//!
//! A row is a dm item v2 (the `aib-dmq` pure core decides its moves; this module only
//! stores what it is given) plus the text, its SHA-256 and length, and two keys:
//! `rev`, bumped on every write, which callers compare (`expected_revision`); `journaled`,
//! 0 until the event of the write is in the main database.
//!
//! The order of a write is fixed: the row first, then the event, then `journaled = 1`.
//! Two files cannot share a transaction, so a stop in between leaves a row without its
//! event; [`MessageDb::unjournaled`] finds those at the next start (aibd writes a
//! `message.recovered` event for each). An event never names a row that does not exist.
//!
//! Same shape as [`crate::Store`]: one writer thread, read connections per call.

use crate::{Result, StoreError};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, Row};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread::JoinHandle;

/// `PRAGMA user_version` this build writes and reads.
pub const MESSAGES_SCHEMA: i64 = 1;
/// The file name under the data directory.
pub const MESSAGES_FILE: &str = "messages.sqlite";
/// Texts older than this many days are dropped; the row (length, hash, status) stays.
pub const BODY_KEEP_DAYS: i64 = 90;
/// Most rows one thread page holds.
pub const PAGE_MAX: u32 = 100;

/// The words in what a seat waits for (`item.dm_wait`) that name the person: the one list the
/// SQL of [`MessageDb::imported_open`] and the ledger's event cards read (case as written).
pub const PERSON_WORDS: [&str; 6] = ["Andy", "andy", "真人", "本人", "指紋", "Touch ID"];

/// The most open imported letters [`MessageDb::imported_open`] gives at once.
pub const IMPORTED_MAX: u32 = 500;

const SCHEMA_V1: &str = "
CREATE TABLE messages (
    message_id   TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    from_key     TEXT NOT NULL,
    to_key       TEXT NOT NULL,
    created      TEXT NOT NULL,
    created_unix INTEGER NOT NULL,
    updated      TEXT NOT NULL,
    rev          INTEGER NOT NULL,
    item         TEXT NOT NULL,
    body         TEXT,
    body_sha256  TEXT NOT NULL,
    len          INTEGER NOT NULL,
    idem_owner   TEXT,
    idem_key     TEXT,
    journaled    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX messages_thread ON messages (thread_id, created_unix, message_id);
CREATE INDEX messages_unjournaled ON messages (journaled) WHERE journaled = 0;
CREATE UNIQUE INDEX messages_idem ON messages (idem_owner, idem_key)
    WHERE idem_key IS NOT NULL;
CREATE TABLE read_marks (
    thread_id  TEXT NOT NULL,
    reader     TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (thread_id, reader)
);
";

/// One stored message.
#[derive(Debug, Clone, PartialEq)]
pub struct MessageRow {
    pub message_id: String,
    pub thread_id: String,
    pub from_key: String,
    pub to_key: String,
    pub created: String,
    pub created_unix: i64,
    pub updated: String,
    pub rev: i64,
    /// The dm item v2 (status, kind, read_at, done_* …) as the dmq core left it.
    pub item: serde_json::Map<String, serde_json::Value>,
    /// `None` once [`BODY_KEEP_DAYS`] passed.
    pub body: Option<String>,
    pub body_sha256: String,
    pub len: i64,
    pub idem_owner: Option<String>,
    pub idem_key: Option<String>,
    pub journaled: bool,
}

/// What the contacts list and the flow lines read of a row (S89-talk-2b ↔ 2a, 7b81e
/// 11-18): its two ends, when it was written and its dm item; never the text or its length.
#[derive(Debug, Clone, PartialEq)]
pub struct MessageFact {
    pub from_key: String,
    pub to_key: String,
    pub created: String,
    pub created_unix: i64,
    pub item: serde_json::Map<String, serde_json::Value>,
}

/// One sender's counts for a reader ([`MessageDb::unread_by_sender`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnreadRow {
    pub from_key: String,
    pub unread: i64,
    pub decide_open: i64,
    pub last_at: String,
}

/// What [`MessageDb::insert`] did.
#[derive(Debug, Clone, PartialEq)]
pub enum Inserted {
    New,
    /// The same `(idem_owner, idem_key)` was written before: that row, nothing written.
    Existing(Box<MessageRow>),
}

/// What [`MessageDb::update`] did.
#[derive(Debug, Clone, PartialEq)]
pub enum Updated {
    Done(Box<MessageRow>),
    /// No such message.
    Missing,
    /// `rev` moved on since the caller read it: the row as it is now.
    Stale(Box<MessageRow>),
}

type Job = Box<dyn FnOnce(&mut Connection) + Send>;

pub struct MessageDb {
    path: PathBuf,
    tx: Option<mpsc::Sender<Job>>,
    writer: Option<JoinHandle<()>>,
}

pub(crate) const COLS: &str =
    "message_id, thread_id, from_key, to_key, created, created_unix, updated,
    rev, item, body, body_sha256, len, idem_owner, idem_key, journaled";

pub(crate) fn from_row(r: &Row<'_>) -> rusqlite::Result<MessageRow> {
    let item: String = r.get(8)?;
    let item = match serde_json::from_str::<serde_json::Value>(&item) {
        Ok(serde_json::Value::Object(m)) => m,
        _ => serde_json::Map::new(),
    };
    Ok(MessageRow {
        message_id: r.get(0)?,
        thread_id: r.get(1)?,
        from_key: r.get(2)?,
        to_key: r.get(3)?,
        created: r.get(4)?,
        created_unix: r.get(5)?,
        updated: r.get(6)?,
        rev: r.get(7)?,
        item,
        body: r.get(9)?,
        body_sha256: r.get(10)?,
        len: r.get(11)?,
        idem_owner: r.get(12)?,
        idem_key: r.get(13)?,
        journaled: r.get::<_, i64>(14)? != 0,
    })
}

pub(crate) fn get_in(c: &Connection, id: &str) -> rusqlite::Result<Option<MessageRow>> {
    c.query_row(
        &format!("SELECT {COLS} FROM messages WHERE message_id = ?1"),
        params![id],
        from_row,
    )
    .optional()
}

fn configure(conn: &Connection) -> Result<()> {
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "NORMAL")?;
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(())
}

/// Create the tables in an empty file; refuse a file a newer aibd wrote.
fn prepare(conn: &mut Connection) -> Result<()> {
    let v: i64 = conn.query_row("PRAGMA user_version", [], |r| r.get(0))?;
    match v {
        0 => {
            let tx = conn.transaction()?;
            tx.execute_batch(SCHEMA_V1)?;
            tx.pragma_update(None, "user_version", MESSAGES_SCHEMA)?;
            tx.commit()?;
            crate::threads::add_tables(conn)?;
            crate::seat_moves::add_tables(conn)?;
            crate::ranks::add_tables(conn)?;
            crate::brain_notes::add_tables(conn)?;
            crate::perm_templates::add_tables(conn)
        }
        MESSAGES_SCHEMA => {
            crate::threads::add_tables(conn)?;
            crate::seat_moves::add_tables(conn)?;
            crate::ranks::add_tables(conn)?;
            crate::brain_notes::add_tables(conn)?;
            crate::perm_templates::add_tables(conn)
        }
        newer => Err(StoreError::UnknownMigration(newer)),
    }
}

impl MessageDb {
    /// Open (or create) the message library and start its writer.
    pub fn open(path: &Path) -> Result<MessageDb> {
        let mut conn = Connection::open(path)?;
        configure(&conn)?;
        prepare(&mut conn)?;
        let (tx, rx) = mpsc::channel::<Job>();
        let writer = std::thread::Builder::new()
            .name("aib-messages-writer".into())
            .spawn(move || {
                for job in rx {
                    job(&mut conn);
                }
            })
            .map_err(|_| StoreError::WriterGone)?;
        Ok(MessageDb {
            path: path.to_path_buf(),
            tx: Some(tx),
            writer: Some(writer),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn write<T: Send + 'static>(
        &self,
        f: impl FnOnce(&mut Connection) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let (rtx, rrx) = mpsc::sync_channel(1);
        let job: Job = Box::new(move |c| {
            let _ = rtx.send(f(c));
        });
        self.tx
            .as_ref()
            .ok_or(StoreError::WriterGone)?
            .send(job)
            .map_err(|_| StoreError::WriterGone)?;
        rrx.recv().map_err(|_| StoreError::WriterGone)?
    }

    pub(crate) fn read(&self) -> Result<Connection> {
        let c = Connection::open_with_flags(
            &self.path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        c.busy_timeout(std::time::Duration::from_secs(5))?;
        Ok(c)
    }

    /// Write a new row (`journaled` 0). With an idempotency key already used by the same
    /// owner, nothing is written and that row comes back.
    pub fn insert(&self, row: MessageRow) -> Result<Inserted> {
        self.write(move |c| {
            let tx = c.transaction()?;
            if let (Some(o), Some(k)) = (&row.idem_owner, &row.idem_key) {
                let seen = tx
                    .query_row(
                        &format!(
                            "SELECT {COLS} FROM messages WHERE idem_owner = ?1 AND idem_key = ?2"
                        ),
                        params![o, k],
                        from_row,
                    )
                    .optional()?;
                if let Some(seen) = seen {
                    return Ok(Inserted::Existing(Box::new(seen)));
                }
            }
            let item = serde_json::Value::Object(row.item.clone()).to_string();
            tx.execute(
                &format!(
                    "INSERT INTO messages ({COLS})
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, 0)"
                ),
                params![
                    row.message_id,
                    row.thread_id,
                    row.from_key,
                    row.to_key,
                    row.created,
                    row.created_unix,
                    row.updated,
                    row.rev,
                    item,
                    row.body,
                    row.body_sha256,
                    row.len,
                    row.idem_owner,
                    row.idem_key,
                ],
            )?;
            tx.commit()?;
            Ok(Inserted::New)
        })
    }

    pub fn get(&self, message_id: &str) -> Result<Option<MessageRow>> {
        Ok(get_in(&self.read()?, message_id)?)
    }

    /// The row `owner` wrote with idempotency key `key`, if any.
    pub fn by_idem(&self, owner: &str, key: &str) -> Result<Option<MessageRow>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {COLS} FROM messages WHERE idem_owner = ?1 AND idem_key = ?2"),
            params![owner, key],
            from_row,
        )
        .optional()?)
    }

    /// Replace the item of a row whose `rev` is still `expect_rev`; `rev` goes up by one
    /// and `journaled` back to 0 (the caller writes the event of this move next).
    pub fn update(
        &self,
        message_id: &str,
        expect_rev: i64,
        item: serde_json::Map<String, serde_json::Value>,
        updated: &str,
    ) -> Result<Updated> {
        let (id, updated) = (message_id.to_string(), updated.to_string());
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(now) = get_in(&tx, &id)? else {
                return Ok(Updated::Missing);
            };
            if now.rev != expect_rev {
                return Ok(Updated::Stale(Box::new(now)));
            }
            let text = serde_json::Value::Object(item).to_string();
            tx.execute(
                "UPDATE messages SET item = ?1, rev = rev + 1, updated = ?2, journaled = 0
                 WHERE message_id = ?3",
                params![text, updated, id],
            )?;
            let after = get_in(&tx, &id)?.ok_or(StoreError::Invalid("row vanished"))?;
            tx.commit()?;
            Ok(Updated::Done(Box::new(after)))
        })
    }

    /// The event of the row's last write is in the main database.
    pub fn mark_journaled(&self, message_id: &str, rev: i64) -> Result<()> {
        let id = message_id.to_string();
        self.write(move |c| {
            c.execute(
                "UPDATE messages SET journaled = 1 WHERE message_id = ?1 AND rev = ?2",
                params![id, rev],
            )?;
            Ok(())
        })
    }

    /// Rows whose last write has no event yet (a stop between the two writes).
    pub fn unjournaled(&self) -> Result<Vec<MessageRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM messages WHERE journaled = 0 ORDER BY created_unix, message_id"
        ))?;
        let rows = st.query_map([], from_row)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// One page of a thread, oldest first: the last `limit` rows before `before` (a
    /// message id of this thread), or the newest `limit` when `before` is `None`. The
    /// second value says whether older rows exist.
    pub fn thread_page(
        &self,
        thread_id: &str,
        before: Option<&str>,
        limit: u32,
    ) -> Result<(Vec<MessageRow>, bool)> {
        let c = self.read()?;
        let limit = limit.clamp(1, PAGE_MAX);
        let edge = match before {
            None => None,
            Some(id) => match get_in(&c, id)? {
                Some(r) if r.thread_id == thread_id => Some((r.created_unix, r.message_id)),
                _ => return Ok((Vec::new(), false)),
            },
        };
        let (mut rows, more) = {
            let (sql, take) = (
                format!(
                    "SELECT {COLS} FROM messages WHERE thread_id = ?1
                     AND (?2 IS NULL OR created_unix < ?2
                          OR (created_unix = ?2 AND message_id < ?3))
                     ORDER BY created_unix DESC, message_id DESC LIMIT ?4"
                ),
                i64::from(limit) + 1,
            );
            let mut st = c.prepare(&sql)?;
            let (eu, eid) = match &edge {
                Some((u, id)) => (Some(*u), Some(id.as_str())),
                None => (None, None),
            };
            let mut v = st
                .query_map(params![thread_id, eu, eid, take], from_row)?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            let more = v.len() > limit as usize;
            v.truncate(limit as usize);
            (v, more)
        };
        rows.reverse();
        Ok((rows, more))
    }

    /// What is still open for `to_key` (status queued, delivered or read; not failed), oldest
    /// first, at most `limit` ([`PAGE_MAX`] at most): a seat's inbox (b4297 B1dm-S3c).
    // N-workspace (b4297, receipt chain-e74c61b80): oldest first and, within one second, in
    // the order they were written (rowid), not by their random ids: this order is the line
    // queued messages are written in, and its place is what seat_send answers
    pub fn open_to(&self, to_key: &str, limit: u32) -> Result<Vec<MessageRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM messages WHERE to_key = ?1
             AND json_extract(item, '$.status') IN ('queued', 'delivered', 'read')
             AND coalesce(json_extract(item, '$.failed'), 0) = 0
             ORDER BY created_unix, rowid LIMIT ?2"
        ))?;
        let rows = st.query_map(
            params![to_key, i64::from(limit.clamp(1, PAGE_MAX))],
            from_row,
        )?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// [`Self::open_to`] for many receivers in one query (5d33a REVIEW-ws-slots 7075ce31 N1:
    /// the slots list asked once per seat): up to `limit` open rows per receiver, each
    /// receiver's oldest first.
    pub fn open_to_each(&self, to_keys: &[String], limit: u32) -> Result<Vec<MessageRow>> {
        if to_keys.is_empty() {
            return Ok(Vec::new());
        }
        let c = self.read()?;
        let marks = vec!["?"; to_keys.len()].join(", ");
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM (SELECT *, ROW_NUMBER() OVER
                 (PARTITION BY to_key ORDER BY created_unix, rowid) AS nth, rowid AS rid
               FROM messages WHERE to_key IN ({marks}) AND json_extract(item, '$.status')
               IN ('queued', 'delivered', 'read') AND coalesce(json_extract(item, '$.failed'), 0) = 0)
             WHERE nth <= ? ORDER BY to_key, created_unix, rid"
        ))?;
        let mut args: Vec<rusqlite::types::Value> = to_keys
            .iter()
            .map(|k| rusqlite::types::Value::Text(k.clone()))
            .collect();
        args.push(rusqlite::types::Value::Integer(i64::from(
            limit.clamp(1, PAGE_MAX),
        )));
        let rows = st.query_map(rusqlite::params_from_iter(args), from_row)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// The 1.x letters brought in under `owner` (keys `dm:*`, N-web-dmbridge) whose ledger
    /// status (`item.dm_status`, kept by the mirror) is one of `statuses` and whose ledger
    /// receiver (`item.source.to`) is one of `to`, oldest first, at most `limit` (≤
    /// [`IMPORTED_MAX`]), with how many matched in all (519d0 11-19 05:10: no fixed cut that
    /// drops the oldest; a cut keeps the oldest and says how many it left out).
    /// A `read` letter counts only when it can still be acted on (0eed7 VERIFY-dmcards M1: 107
    /// open letters, the limit dropped four old dispatches; r2: 79 letters waiting for another
    /// seat did it again): its text starts with 「派工」 or what its seat waits for
    /// (`item.dm_wait`) names the person ([`PERSON_WORDS`]).
    pub fn imported_open(
        &self,
        owner: &str,
        statuses: &[&str],
        to: &[String],
        limit: u32,
    ) -> Result<(Vec<MessageRow>, u64)> {
        if statuses.is_empty() || to.is_empty() {
            return Ok((Vec::new(), 0));
        }
        let c = self.read()?;
        let marks = vec!["?"; statuses.len()].join(", ");
        let tos = vec!["?"; to.len()].join(", ");
        let waits =
            vec!["instr(coalesce(json_extract(item, '$.dm_wait'), ''), ?) > 0"; PERSON_WORDS.len()]
                .join(" OR ");
        let filter = format!(
            "FROM messages WHERE idem_owner = ? AND idem_key LIKE 'dm:%'
               AND json_extract(item, '$.dm_status') IN ({marks})
               AND json_extract(item, '$.source.to') IN ({tos})
               AND (json_extract(item, '$.dm_status') <> 'read'
                    OR ltrim(coalesce(body, ''), ' ' || char(9) || char(10) || char(13))
                       LIKE '派工%'
                    OR {waits})"
        );
        let mut args: Vec<rusqlite::types::Value> =
            vec![rusqlite::types::Value::Text(owner.to_owned())];
        args.extend(
            statuses
                .iter()
                .map(|s| rusqlite::types::Value::Text((*s).to_owned())),
        );
        args.extend(to.iter().map(|t| rusqlite::types::Value::Text(t.clone())));
        args.extend(
            PERSON_WORDS
                .iter()
                .map(|w| rusqlite::types::Value::Text((*w).to_owned())),
        );
        let all: i64 = c.query_row(
            &format!("SELECT count(*) {filter}"),
            rusqlite::params_from_iter(args.clone()),
            |r| r.get(0),
        )?;
        args.push(rusqlite::types::Value::Integer(i64::from(
            limit.clamp(1, IMPORTED_MAX),
        )));
        let mut st = c.prepare(&format!(
            "SELECT {COLS} {filter} ORDER BY created_unix ASC, message_id ASC LIMIT ?"
        ))?;
        let rows = st.query_map(rusqlite::params_from_iter(args), from_row)?;
        let rows = rows.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok((rows, u64::try_from(all).unwrap_or(0)))
    }

    /// Rows of a thread to `reader` up to and including `upto` (by time), oldest first.
    pub fn to_reader_upto(
        &self,
        thread_id: &str,
        reader: &str,
        upto: &MessageRow,
    ) -> Result<Vec<MessageRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM messages WHERE thread_id = ?1 AND to_key = ?2
             AND (created_unix < ?3 OR (created_unix = ?3 AND message_id <= ?4))
             ORDER BY created_unix, message_id"
        ))?;
        let rows = st.query_map(
            params![thread_id, reader, upto.created_unix, upto.message_id],
            from_row,
        )?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// The facts of every row written at or after `since_unix` (all rows for `None`), oldest
    /// first; the text and its length are not read.
    pub fn facts(&self, since_unix: Option<i64>) -> Result<Vec<MessageFact>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT from_key, to_key, created, created_unix, item FROM messages
             WHERE ?1 IS NULL OR created_unix >= ?1 ORDER BY created_unix, message_id",
        )?;
        let rows = st.query_map(params![since_unix], |r| {
            let item: String = r.get(4)?;
            Ok(MessageFact {
                from_key: r.get(0)?,
                to_key: r.get(1)?,
                created: r.get(2)?,
                created_unix: r.get(3)?,
                item: match serde_json::from_str::<serde_json::Value>(&item) {
                    Ok(serde_json::Value::Object(m)) => m,
                    _ => serde_json::Map::new(),
                },
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Per sender, for what `reader` received: `(from_key, unread, open decide, newest
    /// created)`. Unread = status queued or delivered; an open decide = `talk_kind` decide
    /// not done or archived. For the seat list and the contact center (7b81e).
    pub fn unread_by_sender(&self, reader: &str) -> Result<Vec<UnreadRow>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT from_key,
                    SUM(CASE WHEN json_extract(item, '$.status') IN ('queued', 'delivered')
                        THEN 1 ELSE 0 END),
                    SUM(CASE WHEN json_extract(item, '$.talk_kind') = 'decide'
                         AND json_extract(item, '$.status') NOT IN ('done', 'archived', 'deleted')
                        THEN 1 ELSE 0 END),
                    MAX(created)
             FROM messages WHERE to_key = ?1 GROUP BY from_key ORDER BY from_key",
        )?;
        let rows = st.query_map(params![reader], |r| {
            Ok(UnreadRow {
                from_key: r.get(0)?,
                unread: r.get(1)?,
                decide_open: r.get(2)?,
                last_at: r.get(3)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Where `reader` last marked this thread read.
    pub fn read_mark(&self, thread_id: &str, reader: &str) -> Result<Option<String>> {
        let c = self.read()?;
        Ok(c.query_row(
            "SELECT message_id FROM read_marks WHERE thread_id = ?1 AND reader = ?2",
            params![thread_id, reader],
            |r| r.get(0),
        )
        .optional()?)
    }

    pub fn set_read_mark(&self, thread_id: &str, reader: &str, message_id: &str) -> Result<()> {
        let (t, r, m) = (
            thread_id.to_string(),
            reader.to_string(),
            message_id.to_string(),
        );
        self.write(move |c| {
            c.execute(
                "INSERT INTO read_marks (thread_id, reader, message_id) VALUES (?1, ?2, ?3)
                 ON CONFLICT (thread_id, reader) DO UPDATE SET message_id = excluded.message_id",
                params![t, r, m],
            )?;
            Ok(())
        })
    }

    /// Drop the texts of rows created before `before_unix`; returns how many.
    pub fn drop_bodies_before(&self, before_unix: i64) -> Result<usize> {
        self.write(move |c| {
            Ok(c.execute(
                "UPDATE messages SET body = NULL WHERE body IS NOT NULL AND created_unix < ?1",
                params![before_unix],
            )?)
        })
    }
}

impl Drop for MessageDb {
    fn drop(&mut self) {
        drop(self.tx.take());
        if let Some(w) = self.writer.take() {
            let _ = w.join();
        }
    }
}
