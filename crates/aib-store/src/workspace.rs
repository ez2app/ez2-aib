//! The workspace library (N-workspace second wave, SPEC-workspace-multiseat §1.2; 7b81e):
//! its own database file, `<data>/workspace.sqlite`, with its own `user_version`, like the
//! message library (an older aibd that does not know the file never opens it, and the main
//! database is untouched).
//!
//! Version 1 holds the assignments: one row = node × seat × role (Andy 11-14: 節點 × 席位 ×
//! 角色), many to many. Version 2 adds the tree's nodes (MODEL-workspace-tree-7b81e-1118 r1,
//! a9136 10:54): filled once from the progress tree's `data.json` shape, the fields aibd does
//! not read kept as they were in `extra`. A version 1 file is brought up to 2 on open.
//!
//! Same shape as [`crate::MessageDb`]: one writer thread, read connections per call.

use crate::{Result, StoreError};
use rusqlite::{params, Connection, OpenFlags};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread::JoinHandle;

/// `PRAGMA user_version` this build writes and reads.
pub const WORKSPACE_SCHEMA: i64 = 2;
/// The file name under the data directory.
pub const WORKSPACE_FILE: &str = "workspace.sqlite";
/// The most assignments one node takes (a node of six roles for every seat of a cabin fits).
pub const ASSIGN_PER_NODE_MAX: i64 = 64;

const SCHEMA_V1: &str = "
CREATE TABLE assignments (
    node_id  TEXT NOT NULL,
    seat_id  TEXT NOT NULL,
    role     TEXT NOT NULL,
    created  TEXT NOT NULL,
    by_key   TEXT NOT NULL,
    PRIMARY KEY (node_id, seat_id, role)
) WITHOUT ROWID;
CREATE INDEX assignments_seat ON assignments (seat_id);
";

const SCHEMA_V2: &str = "
CREATE TABLE nodes (
    node_id   TEXT PRIMARY KEY,
    parent_id TEXT,
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    station   TEXT,
    head      TEXT,
    need      TEXT,
    ord       INTEGER NOT NULL,
    rev       INTEGER NOT NULL DEFAULT 1,
    updated   TEXT NOT NULL,
    extra     TEXT NOT NULL DEFAULT '{}'
) WITHOUT ROWID;
CREATE INDEX nodes_parent ON nodes (parent_id, ord);
";

/// One tree node (version 2). `parent_id` `None` = a root; `extra` is a JSON object of the
/// node's other fields, as the tree file had them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeRow {
    pub node_id: String,
    pub parent_id: Option<String>,
    pub kind: String,
    pub title: String,
    pub station: Option<String>,
    pub head: Option<String>,
    pub need: Option<String>,
    pub ord: i64,
    pub rev: i64,
    pub updated: String,
    pub extra: String,
}

/// One assignment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssignRow {
    pub node_id: String,
    pub seat_id: String,
    pub role: String,
    pub created: String,
    /// Who added it (a page key `human:<16 hex>`, or `startup`).
    pub by_key: String,
}

/// What [`WorkspaceDb::add`] did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Added {
    New,
    /// The same node, seat and role was already there; nothing was written.
    Exists,
    /// The node has [`ASSIGN_PER_NODE_MAX`] already; nothing was written.
    Full,
}

type Job = Box<dyn FnOnce(&mut Connection) + Send>;

pub struct WorkspaceDb {
    path: PathBuf,
    tx: Option<mpsc::Sender<Job>>,
    writer: Option<JoinHandle<()>>,
}

impl Drop for WorkspaceDb {
    fn drop(&mut self) {
        drop(self.tx.take());
        if let Some(w) = self.writer.take() {
            let _ = w.join();
        }
    }
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
        0 | 1 => {
            let tx = conn.transaction()?;
            if v == 0 {
                tx.execute_batch(SCHEMA_V1)?;
            }
            tx.execute_batch(SCHEMA_V2)?;
            tx.pragma_update(None, "user_version", WORKSPACE_SCHEMA)?;
            tx.commit()?;
            Ok(())
        }
        WORKSPACE_SCHEMA => Ok(()),
        newer => Err(StoreError::UnknownMigration(newer)),
    }
}

fn from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<AssignRow> {
    Ok(AssignRow {
        node_id: r.get(0)?,
        seat_id: r.get(1)?,
        role: r.get(2)?,
        created: r.get(3)?,
        by_key: r.get(4)?,
    })
}

impl WorkspaceDb {
    /// Open (or create) the workspace library and start its writer.
    pub fn open(path: &Path) -> Result<WorkspaceDb> {
        let mut conn = Connection::open(path)?;
        configure(&conn)?;
        prepare(&mut conn)?;
        let (tx, rx) = mpsc::channel::<Job>();
        let writer = std::thread::Builder::new()
            .name("aib-workspace-writer".into())
            .spawn(move || {
                for job in rx {
                    job(&mut conn);
                }
            })
            .map_err(|_| StoreError::WriterGone)?;
        Ok(WorkspaceDb {
            path: path.to_path_buf(),
            tx: Some(tx),
            writer: Some(writer),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
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

    /// Add one assignment; the per-node limit and the "already there" check are in the same
    /// transaction as the write.
    pub fn add(&self, row: AssignRow) -> Result<Added> {
        self.write(move |c| {
            let tx = c.transaction()?;
            let there: i64 = tx.query_row(
                "SELECT COUNT(*) FROM assignments WHERE node_id = ?1 AND seat_id = ?2 AND role = ?3",
                params![row.node_id, row.seat_id, row.role],
                |r| r.get(0),
            )?;
            if there > 0 {
                return Ok(Added::Exists);
            }
            let n: i64 = tx.query_row(
                "SELECT COUNT(*) FROM assignments WHERE node_id = ?1",
                params![row.node_id],
                |r| r.get(0),
            )?;
            if n >= ASSIGN_PER_NODE_MAX {
                return Ok(Added::Full);
            }
            tx.execute(
                "INSERT INTO assignments (node_id, seat_id, role, created, by_key)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![row.node_id, row.seat_id, row.role, row.created, row.by_key],
            )?;
            tx.commit()?;
            Ok(Added::New)
        })
    }

    /// Remove one assignment; whether there was one.
    pub fn remove(&self, node_id: &str, seat_id: &str, role: &str) -> Result<bool> {
        let (n, s, r) = (node_id.to_owned(), seat_id.to_owned(), role.to_owned());
        self.write(move |c| {
            let gone = c.execute(
                "DELETE FROM assignments WHERE node_id = ?1 AND seat_id = ?2 AND role = ?3",
                params![n, s, r],
            )?;
            Ok(gone > 0)
        })
    }

    /// `(seat_id, role)` of every assignment on `node` itself, by the time added (a9136
    /// threads-multi: a node's thread members are these and the person; a seat on a node above
    /// does not read this node's thread, 5d33a wave-2 C3).
    pub fn assignments_of(&self, node_id: &str) -> Result<Vec<(String, String)>> {
        Ok(self
            .list(Some(node_id), None)?
            .into_iter()
            .map(|r| (r.seat_id, r.role))
            .collect())
    }

    /// Fill the node table from `rows`, only while it is empty (one transaction); whether it
    /// was filled. Rows must have distinct ids (the caller drops repeats and says which).
    pub fn import_nodes(&self, rows: Vec<NodeRow>) -> Result<bool> {
        self.write(move |c| {
            let tx = c.transaction()?;
            let n: i64 = tx.query_row("SELECT COUNT(*) FROM nodes", [], |r| r.get(0))?;
            if n > 0 {
                return Ok(false);
            }
            for r in &rows {
                tx.execute(
                    "INSERT INTO nodes (node_id, parent_id, kind, title, station, head, need,
                                        ord, rev, updated, extra)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                    params![
                        r.node_id,
                        r.parent_id,
                        r.kind,
                        r.title,
                        r.station,
                        r.head,
                        r.need,
                        r.ord,
                        r.rev,
                        r.updated,
                        r.extra
                    ],
                )?;
            }
            tx.commit()?;
            Ok(true)
        })
    }

    /// Every node, in tree-file order (`ord`).
    pub fn nodes(&self) -> Result<Vec<NodeRow>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT node_id, parent_id, kind, title, station, head, need, ord, rev, updated, extra
             FROM nodes ORDER BY ord, node_id",
        )?;
        let rows = st.query_map([], |r| {
            Ok(NodeRow {
                node_id: r.get(0)?,
                parent_id: r.get(1)?,
                kind: r.get(2)?,
                title: r.get(3)?,
                station: r.get(4)?,
                head: r.get(5)?,
                need: r.get(6)?,
                ord: r.get(7)?,
                rev: r.get(8)?,
                updated: r.get(9)?,
                extra: r.get(10)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// The assignments of `node` and / or `seat` (every one when both are `None`), by node,
    /// then the time added, then seat and role.
    pub fn list(&self, node: Option<&str>, seat: Option<&str>) -> Result<Vec<AssignRow>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT node_id, seat_id, role, created, by_key FROM assignments
             WHERE (?1 IS NULL OR node_id = ?1) AND (?2 IS NULL OR seat_id = ?2)
             ORDER BY node_id, created, seat_id, role",
        )?;
        let rows = st.query_map(params![node, seat], from_row)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn row(node: &str, seat: &str, role: &str) -> AssignRow {
        AssignRow {
            node_id: node.into(),
            seat_id: seat.into(),
            role: role.into(),
            created: "2026-11-18T02:40:00Z".into(),
            by_key: "startup".into(),
        }
    }

    #[test]
    fn one_node_seat_and_role_is_one_row_and_a_node_takes_at_most_its_limit() {
        let dir = tempfile::tempdir().unwrap();
        let db = WorkspaceDb::open(&dir.path().join(WORKSPACE_FILE)).unwrap();
        assert_eq!(db.add(row("N", "seat_a", "author")).unwrap(), Added::New);
        assert_eq!(db.add(row("N", "seat_a", "author")).unwrap(), Added::Exists);
        // the same seat in another role is another row
        assert_eq!(db.add(row("N", "seat_a", "reviewer")).unwrap(), Added::New);
        assert_eq!(db.list(Some("N"), None).unwrap().len(), 2);
        assert_eq!(db.list(None, Some("seat_a")).unwrap().len(), 2);
        assert_eq!(db.list(Some("M"), None).unwrap().len(), 0);
        assert_eq!(
            db.assignments_of("N").unwrap(),
            [
                ("seat_a".to_owned(), "author".to_owned()),
                ("seat_a".to_owned(), "reviewer".to_owned())
            ]
        );
        for i in 2..ASSIGN_PER_NODE_MAX {
            assert_eq!(
                db.add(row("N", &format!("seat_{i}"), "observer")).unwrap(),
                Added::New
            );
        }
        assert_eq!(
            db.add(row("N", "seat_over", "observer")).unwrap(),
            Added::Full,
            "reason: node_limit_not_kept"
        );
        // another node is not full
        assert_eq!(
            db.add(row("M", "seat_over", "observer")).unwrap(),
            Added::New
        );
        assert!(db.remove("N", "seat_a", "author").unwrap());
        assert!(!db.remove("N", "seat_a", "author").unwrap());
        assert_eq!(
            db.add(row("N", "seat_over", "observer")).unwrap(),
            Added::New
        );
    }

    fn node(id: &str, parent: Option<&str>, ord: i64) -> NodeRow {
        NodeRow {
            node_id: id.into(),
            parent_id: parent.map(str::to_owned),
            kind: "item".into(),
            title: format!("項目 {id}"),
            station: None,
            head: None,
            need: None,
            ord,
            rev: 1,
            updated: "2026-11-18T03:00:00Z".into(),
            extra: "{}".into(),
        }
    }

    #[test]
    fn the_nodes_are_filled_once_and_a_version_1_file_keeps_its_assignments() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(WORKSPACE_FILE);
        // a version 1 file, as wave 2's first slice wrote it
        {
            let c = Connection::open(&p).unwrap();
            c.execute_batch(SCHEMA_V1).unwrap();
            c.pragma_update(None, "user_version", 1).unwrap();
            c.execute(
                "INSERT INTO assignments VALUES ('N', 'seat_a', 'author', 't', 'startup')",
                [],
            )
            .unwrap();
        }
        let db = WorkspaceDb::open(&p).unwrap();
        assert_eq!(
            db.assignments_of("N").unwrap().len(),
            1,
            "reason: v1_rows_lost"
        );
        assert!(db.nodes().unwrap().is_empty());
        let first = vec![node("V", None, 0), node("N", Some("V"), 1)];
        assert!(db.import_nodes(first.clone()).unwrap());
        assert!(
            !db.import_nodes(vec![node("X", None, 0)]).unwrap(),
            "reason: filled_twice"
        );
        assert_eq!(db.nodes().unwrap(), first);
        drop(db);
        let c = Connection::open(&p).unwrap();
        let v: i64 = c
            .query_row("PRAGMA user_version", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, WORKSPACE_SCHEMA);
    }

    #[test]
    fn a_file_a_newer_aibd_wrote_is_refused_and_rows_outlive_a_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(WORKSPACE_FILE);
        {
            let db = WorkspaceDb::open(&p).unwrap();
            db.add(row("N", "seat_a", "author")).unwrap();
        }
        let db = WorkspaceDb::open(&p).unwrap();
        assert_eq!(
            db.list(None, None).unwrap(),
            vec![row("N", "seat_a", "author")]
        );
        drop(db);
        let c = Connection::open(&p).unwrap();
        c.pragma_update(None, "user_version", WORKSPACE_SCHEMA + 1)
            .unwrap();
        drop(c);
        assert!(matches!(
            WorkspaceDb::open(&p),
            Err(StoreError::UnknownMigration(v)) if v == WORKSPACE_SCHEMA + 1
        ));
    }
}
