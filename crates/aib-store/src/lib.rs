//! SQLite storage for aibd (ADR 0002): rusqlite with the bundled SQLite, one
//! writer thread, read connections opened per call, migrations embedded from
//! `/migrations`.
//!
//! The API is synchronous on purpose; async callers use `spawn_blocking`.

mod migrate;
mod seats;

use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread::JoinHandle;

pub use migrate::{Migration, MIGRATIONS};
pub use seats::{IdemKey, Seat, SeatCreate};

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("sqlite: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("migration {version} ({name}) was changed after it was applied")]
    MigrationChecksum { version: i64, name: String },
    #[error("database has migration {0} that this build does not know (newer aibd?)")]
    UnknownMigration(i64),
    #[error("writer thread is gone")]
    WriterGone,
    #[error("invalid write: {0}")]
    Invalid(&'static str),
    #[error("aggregate {aggregate_id}: expected next sequence {expected}, got {got}")]
    SequenceConflict {
        aggregate_id: String,
        expected: i64,
        got: i64,
    },
}

pub type Result<T> = std::result::Result<T, StoreError>;

/// An event to append. `sequence` must be the next one for the aggregate
/// (optimistic concurrency, like 1.x `--if-rev`).
#[derive(Debug, Clone)]
pub struct NewEvent {
    pub event_id: String,
    pub aggregate_type: String,
    pub aggregate_id: String,
    pub sequence: i64,
    pub event_type: String,
    pub correlation_id: Option<String>,
    pub occurred_at: String,
    pub payload: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StoredEvent {
    pub global_seq: i64,
    pub event_id: String,
    pub aggregate_type: String,
    pub aggregate_id: String,
    pub sequence: i64,
    pub event_type: String,
    pub schema_version: i64,
    pub correlation_id: Option<String>,
    pub occurred_at: String,
    pub payload: serde_json::Value,
}

type Job = Box<dyn FnOnce(&mut Connection) + Send>;

pub struct Store {
    path: PathBuf,
    tx: Option<mpsc::Sender<Job>>,
    writer: Option<JoinHandle<()>>,
}

fn configure(conn: &Connection) -> Result<()> {
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.pragma_update(None, "synchronous", "NORMAL")?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(())
}

impl Store {
    /// Open (or create) the database, apply pending migrations, start the writer.
    pub fn open(path: &Path) -> Result<Store> {
        let mut conn = Connection::open(path)?;
        configure(&conn)?;
        migrate::apply(&mut conn, MIGRATIONS)?;
        let (tx, rx) = mpsc::channel::<Job>();
        let writer = std::thread::Builder::new()
            .name("aib-store-writer".into())
            .spawn(move || {
                for job in rx {
                    job(&mut conn);
                }
            })
            .map_err(|_| StoreError::WriterGone)?;
        Ok(Store {
            path: path.to_path_buf(),
            tx: Some(tx),
            writer: Some(writer),
        })
    }

    fn write<T: Send + 'static>(
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

    fn read(&self) -> Result<Connection> {
        let c = Connection::open_with_flags(
            &self.path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        c.busy_timeout(std::time::Duration::from_secs(5))?;
        Ok(c)
    }

    /// Append events in one transaction; returns their `global_seq` values.
    /// Fails without writing anything if any sequence is not the next one.
    pub fn append(&self, events: Vec<NewEvent>) -> Result<Vec<i64>> {
        self.write(move |c| {
            let tx = c.transaction()?;
            let mut out = Vec::with_capacity(events.len());
            for e in &events {
                out.push(insert_event(&tx, e)?);
            }
            tx.commit()?;
            Ok(out)
        })
    }

    /// Events with `global_seq > cursor`, oldest first, at most `limit`.
    pub fn events_after(&self, cursor: i64, limit: u32) -> Result<Vec<StoredEvent>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT global_seq, event_id, aggregate_type, aggregate_id, sequence, type,
                    schema_version, correlation_id, occurred_at, payload
             FROM events WHERE global_seq > ?1 ORDER BY global_seq LIMIT ?2",
        )?;
        let rows = st.query_map(params![cursor, limit], |r| {
            let payload: String = r.get(9)?;
            Ok(StoredEvent {
                global_seq: r.get(0)?,
                event_id: r.get(1)?,
                aggregate_type: r.get(2)?,
                aggregate_id: r.get(3)?,
                sequence: r.get(4)?,
                event_type: r.get(5)?,
                schema_version: r.get(6)?,
                correlation_id: r.get(7)?,
                occurred_at: r.get(8)?,
                payload: serde_json::from_str(&payload).unwrap_or(serde_json::Value::Null),
            })
        })?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }

    /// Versions recorded in `schema_migrations`, ascending.
    pub fn applied_migrations(&self) -> Result<Vec<i64>> {
        let c = self.read()?;
        let mut st = c.prepare("SELECT version FROM schema_migrations ORDER BY version")?;
        let v = st.query_map([], |r| r.get(0))?;
        Ok(v.collect::<std::result::Result<_, _>>()?)
    }

    /// `PRAGMA integrity_check` (spec 16: run at start and after updates).
    pub fn integrity_ok(&self) -> Result<bool> {
        let c = self.read()?;
        let r: Option<String> = c
            .query_row("PRAGMA integrity_check", [], |r| r.get(0))
            .optional()?;
        Ok(r.as_deref() == Some("ok"))
    }
}

/// Insert one event inside an open transaction; its `sequence` must be the
/// next one for the aggregate. Returns its `global_seq`.
fn insert_event(tx: &rusqlite::Transaction<'_>, e: &NewEvent) -> Result<i64> {
    let last: i64 = tx.query_row(
        "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE aggregate_id = ?1",
        params![e.aggregate_id],
        |r| r.get(0),
    )?;
    if e.sequence != last + 1 {
        return Err(StoreError::SequenceConflict {
            aggregate_id: e.aggregate_id.clone(),
            expected: last + 1,
            got: e.sequence,
        });
    }
    tx.execute(
        "INSERT INTO events (event_id, aggregate_type, aggregate_id, sequence, type,
             correlation_id, occurred_at, payload)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            e.event_id,
            e.aggregate_type,
            e.aggregate_id,
            e.sequence,
            e.event_type,
            e.correlation_id,
            e.occurred_at,
            e.payload.to_string(),
        ],
    )?;
    Ok(tx.last_insert_rowid())
}

impl Drop for Store {
    fn drop(&mut self) {
        drop(self.tx.take());
        if let Some(h) = self.writer.take() {
            let _ = h.join();
        }
    }
}
