//! Threads of many (N-workspace, SPEC-workspace-multiseat-519d0-1118 §1.3 / §7 / §8): one
//! conversation per item of the project tree (or a free one), its members, and its posts.
//!
//! In the message library's own file, as two more tables that are only ever added: an older
//! aibd that knows `user_version` 1 opens the file and never reads them, so going back a
//! version needs nothing (the version is not bumped for an added table).
//!
//! - A post is a message row addressed to the thread itself (`to_key` = `thread_id`): the
//!   timeline is those rows. Its item says who was called (`to`) and who was not (`cc`).
//! - Each seat called by `@` gets a delivery row of its own (`to_key` = `seat:<id>`, item
//!   `post_id`), queued, with the text: the seat's inbox and the dm core move it like any
//!   message, and its status is what the page shows next to the `@`.
//! - The post and its deliveries go in one transaction, after the idempotency key is checked
//!   in the same transaction: two requests with one key write one post (5d33a 2a L2).

use crate::messages::{from_row, get_in, MessageRow, COLS, PAGE_MAX};
use crate::{Inserted, MessageDb, Result};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA_THREADS: &str = "
CREATE TABLE IF NOT EXISTS threads (
    thread_id    TEXT PRIMARY KEY,
    node         TEXT,
    title        TEXT NOT NULL,
    opened_by    TEXT NOT NULL,
    created      TEXT NOT NULL,
    created_unix INTEGER NOT NULL,
    last_unix    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_node ON threads (node, last_unix);
CREATE TABLE IF NOT EXISTS thread_members (
    thread_id TEXT NOT NULL,
    member    TEXT NOT NULL,
    role      TEXT NOT NULL,
    added     TEXT NOT NULL,
    PRIMARY KEY (thread_id, member)
);
CREATE INDEX IF NOT EXISTS thread_members_member ON thread_members (member);
";

/// Add the thread tables when they are missing (every open; nothing when they are there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA_THREADS)?;
    Ok(())
}

/// One thread of many.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ThreadRow {
    pub thread_id: String,
    /// The project tree item it belongs to; `None` = a free thread.
    pub node: Option<String>,
    pub title: String,
    pub opened_by: String,
    pub created: String,
    pub created_unix: i64,
    /// When the last post was written (the list is newest first).
    pub last_unix: i64,
}

/// What [`MessageDb::thread_open`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Opened {
    New,
    /// The tree item already has its thread (members added to it): that thread.
    Existing(Box<ThreadRow>),
    /// The id is taken (try another).
    Taken,
}

/// A member and its role on the thread's item.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    pub member: String,
    pub role: String,
}

const THREAD_COLS: &str = "thread_id, node, title, opened_by, created, created_unix, last_unix";

fn thread_from(r: &rusqlite::Row<'_>) -> rusqlite::Result<ThreadRow> {
    Ok(ThreadRow {
        thread_id: r.get(0)?,
        node: r.get(1)?,
        title: r.get(2)?,
        opened_by: r.get(3)?,
        created: r.get(4)?,
        created_unix: r.get(5)?,
        last_unix: r.get(6)?,
    })
}

fn insert_row(c: &Connection, r: &MessageRow) -> rusqlite::Result<usize> {
    c.execute(
        &format!(
            "INSERT INTO messages ({COLS}) VALUES
             (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, 0)"
        ),
        params![
            r.message_id,
            r.thread_id,
            r.from_key,
            r.to_key,
            r.created,
            r.created_unix,
            r.updated,
            r.rev,
            serde_json::Value::Object(r.item.clone()).to_string(),
            r.body,
            r.body_sha256,
            r.len,
            r.idem_owner,
            r.idem_key,
        ],
    )
}

impl MessageDb {
    /// Open a thread with its members (`opened_by` is not added; a person is never a member
    /// row). A thread on a tree item is that item's one thread: when the item has one, nothing
    /// new is opened, the members not in it yet are added and it comes back
    /// ([`Opened::Existing`]); checked in the same transaction, so two opens make one thread.
    pub fn thread_open(&self, t: ThreadRow, members: Vec<Member>) -> Result<Opened> {
        self.write(move |c| {
            let tx = c.transaction()?;
            let found = match &t.node {
                Some(n) => tx
                    .query_row(
                        &format!(
                            "SELECT {THREAD_COLS} FROM threads WHERE node = ?1
                             ORDER BY created_unix, thread_id LIMIT 1"
                        ),
                        params![n],
                        thread_from,
                    )
                    .optional()?,
                None => None,
            };
            let (row, fresh) = match found {
                Some(r) => (r, false),
                None => {
                    let n = tx.execute(
                        &format!(
                            "INSERT OR IGNORE INTO threads ({THREAD_COLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"
                        ),
                        params![
                            t.thread_id,
                            t.node,
                            t.title,
                            t.opened_by,
                            t.created,
                            t.created_unix,
                            t.last_unix
                        ],
                    )?;
                    if n == 0 {
                        return Ok(Opened::Taken);
                    }
                    (t, true)
                }
            };
            for m in &members {
                // an item's thread keeps the role a member already has
                tx.execute(
                    "INSERT OR IGNORE INTO thread_members (thread_id, member, role, added)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![row.thread_id, m.member, m.role, row.created],
                )?;
            }
            tx.commit()?;
            Ok(if fresh {
                Opened::New
            } else {
                Opened::Existing(Box::new(row))
            })
        })
    }

    pub fn thread_get(&self, thread_id: &str) -> Result<Option<ThreadRow>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {THREAD_COLS} FROM threads WHERE thread_id = ?1"),
            params![thread_id],
            thread_from,
        )
        .optional()?)
    }

    pub fn thread_members(&self, thread_id: &str) -> Result<Vec<Member>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT member, role FROM thread_members WHERE thread_id = ?1 ORDER BY added, member",
        )?;
        let rows = st.query_map(params![thread_id], |r| {
            Ok(Member {
                member: r.get(0)?,
                role: r.get(1)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Threads newest post first, at most `limit` ([`PAGE_MAX`] at most): of one item when
    /// `node` is given, only those `member` is in when it is given.
    pub fn threads_list(
        &self,
        node: Option<&str>,
        member: Option<&str>,
        limit: u32,
    ) -> Result<Vec<ThreadRow>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {THREAD_COLS} FROM threads t
             WHERE (?1 IS NULL OR t.node = ?1)
               AND (?2 IS NULL OR EXISTS (SELECT 1 FROM thread_members m
                                          WHERE m.thread_id = t.thread_id AND m.member = ?2))
             ORDER BY last_unix DESC, thread_id DESC LIMIT ?3"
        ))?;
        let rows = st.query_map(
            params![node, member, i64::from(limit.clamp(1, PAGE_MAX))],
            thread_from,
        )?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Write a post and its deliveries in one transaction. With the post's idempotency key
    /// already used by the same owner, nothing is written and that post comes back.
    pub fn post_insert(&self, post: MessageRow, deliveries: Vec<MessageRow>) -> Result<Inserted> {
        self.write(move |c| {
            let tx = c.transaction()?;
            if let (Some(o), Some(k)) = (&post.idem_owner, &post.idem_key) {
                let seen: Option<String> = tx
                    .query_row(
                        "SELECT message_id FROM messages WHERE idem_owner = ?1 AND idem_key = ?2",
                        params![o, k],
                        |r| r.get(0),
                    )
                    .optional()?;
                if let Some(id) = seen {
                    if let Some(r) = get_in(&tx, &id)? {
                        return Ok(Inserted::Existing(Box::new(r)));
                    }
                }
            }
            insert_row(&tx, &post)?;
            for d in &deliveries {
                insert_row(&tx, d)?;
            }
            tx.execute(
                "UPDATE threads SET last_unix = max(last_unix, ?2) WHERE thread_id = ?1",
                params![post.thread_id, post.created_unix],
            )?;
            tx.commit()?;
            Ok(Inserted::New)
        })
    }

    /// The posts of a thread, oldest first, at most `limit`, before the post `before`;
    /// `true` when older ones remain.
    pub fn posts_page(
        &self,
        thread_id: &str,
        before: Option<&str>,
        limit: u32,
    ) -> Result<(Vec<MessageRow>, bool)> {
        let c = self.read()?;
        let limit = limit.clamp(1, PAGE_MAX);
        // the order is the order written (rowid; one writer): two posts in one second keep it
        let edge: Option<i64> = match before {
            None => None,
            Some(id) => match c
                .query_row(
                    "SELECT rowid FROM messages WHERE message_id = ?1 AND thread_id = ?2
                     AND to_key = ?2",
                    params![id, thread_id],
                    |r| r.get(0),
                )
                .optional()?
            {
                Some(r) => Some(r),
                None => return Ok((Vec::new(), false)),
            },
        };
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM messages WHERE thread_id = ?1 AND to_key = ?1
             AND (?2 IS NULL OR rowid < ?2)
             ORDER BY rowid DESC LIMIT ?3"
        ))?;
        let mut v = st
            .query_map(params![thread_id, edge, i64::from(limit) + 1], from_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let more = v.len() > limit as usize;
        v.truncate(limit as usize);
        v.reverse();
        Ok((v, more))
    }

    /// Where a row sits in the order written (its rowid).
    pub fn position(&self, message_id: &str) -> Result<Option<i64>> {
        let c = self.read()?;
        Ok(c.query_row(
            "SELECT rowid FROM messages WHERE message_id = ?1",
            params![message_id],
            |r| r.get(0),
        )
        .optional()?)
    }

    /// The delivery rows of these posts (each `@`'s own row).
    pub fn deliveries_of(&self, thread_id: &str, post_ids: &[String]) -> Result<Vec<MessageRow>> {
        if post_ids.is_empty() {
            return Ok(Vec::new());
        }
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM messages WHERE thread_id = ?1 AND to_key <> ?1
             AND json_extract(item, '$.post_id') = ?2 ORDER BY to_key"
        ))?;
        let mut out = Vec::new();
        for p in post_ids {
            out.extend(
                st.query_map(params![thread_id, p], from_row)?
                    .collect::<rusqlite::Result<Vec<_>>>()?,
            );
        }
        Ok(out)
    }

    /// `(created_unix, sender_kind)` of the last `n` posts, newest first (the thread's pace).
    pub fn recent_posts(&self, thread_id: &str, n: u32) -> Result<Vec<(i64, String)>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT created_unix, coalesce(json_extract(item, '$.sender_kind'), '')
             FROM messages WHERE thread_id = ?1 AND to_key = ?1
             ORDER BY rowid DESC LIMIT ?2",
        )?;
        let rows = st.query_map(params![thread_id, i64::from(n)], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }
}

/// `human:<16 hex>`.
fn is_human(k: &str) -> bool {
    k.strip_prefix("human:").is_some_and(|h| {
        h.len() == 16
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}

/// A two-key thread id (`<a>~<b>`) with `f` applied to each end, sorted again; other ids
/// unchanged.
fn rekey_thread(t: &str, f: &dyn Fn(&str) -> String) -> String {
    match t.split_once('~') {
        Some((a, b)) => {
            let (a, b) = (f(a), f(b));
            if a <= b {
                format!("{a}~{b}")
            } else {
                format!("{b}~{a}")
            }
        }
        None => t.to_owned(),
    }
}

impl MessageDb {
    /// The person's keys made one (519d0 11-18, 0eed7 VERIFY-workspace M): every
    /// `human:<16 hex>` that is not in `keep` becomes `to`, in the rows (sender, receiver,
    /// thread, idempotency owner, the item's `sender_seat`), the read marks and the threads
    /// opened. Returns how many rows moved. An idempotency key two old keys both used is
    /// dropped from the later row (the text stays; only a resend of it is no longer caught).
    pub fn merge_humans(&self, keep: Vec<String>, to: String) -> Result<usize> {
        self.write(move |c| {
            let f = |k: &str| -> String {
                if is_human(k) && !keep.iter().any(|x| x == k) {
                    to.clone()
                } else {
                    k.to_owned()
                }
            };
            let tx = c.transaction()?;
            let rows: Vec<(String, String, String, String, Option<String>, String)> = {
                let mut st = tx.prepare(
                    "SELECT message_id, thread_id, from_key, to_key, idem_owner, item FROM messages
                     WHERE from_key LIKE 'human:%' OR to_key LIKE 'human:%'
                        OR thread_id LIKE '%human:%' OR idem_owner LIKE 'human:%'",
                )?;
                let v = st
                    .query_map([], |r| {
                        Ok((
                            r.get(0)?,
                            r.get(1)?,
                            r.get(2)?,
                            r.get(3)?,
                            r.get(4)?,
                            r.get(5)?,
                        ))
                    })?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                v
            };
            let mut moved = 0;
            for (id, t, fr, to_k, owner, item) in rows {
                let (nt, nf, nto) = (rekey_thread(&t, &f), f(&fr), f(&to_k));
                let no = owner.as_deref().map(f);
                let mut it: serde_json::Value =
                    serde_json::from_str(&item).unwrap_or(serde_json::Value::Null);
                for k in ["sender_seat", "thread"] {
                    if let Some(s) = it.get(k).and_then(serde_json::Value::as_str) {
                        let n = if k == "thread" {
                            rekey_thread(s, &f)
                        } else {
                            f(s)
                        };
                        it[k] = serde_json::Value::String(n);
                    }
                }
                let ni = it.to_string();
                if (
                    nt.as_str(),
                    nf.as_str(),
                    nto.as_str(),
                    no.as_deref(),
                    ni.as_str(),
                ) == (
                    t.as_str(),
                    fr.as_str(),
                    to_k.as_str(),
                    owner.as_deref(),
                    item.as_str(),
                ) {
                    continue;
                }
                let sql = "UPDATE messages SET thread_id = ?2, from_key = ?3, to_key = ?4,
                           idem_owner = ?5, item = ?6 WHERE message_id = ?1";
                if tx.execute(sql, params![id, nt, nf, nto, no, ni]).is_err() {
                    tx.execute(
                        "UPDATE messages SET thread_id = ?2, from_key = ?3, to_key = ?4,
                         idem_owner = NULL, idem_key = NULL, item = ?5 WHERE message_id = ?1",
                        params![id, nt, nf, nto, ni],
                    )?;
                }
                moved += 1;
            }
            let marks: Vec<(String, String, String)> = {
                let mut st = tx.prepare(
                    "SELECT thread_id, reader, message_id FROM read_marks
                     WHERE reader LIKE 'human:%' OR thread_id LIKE '%human:%'",
                )?;
                let v = st
                    .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                v
            };
            for (t, r, m) in marks {
                let (nt, nr) = (rekey_thread(&t, &f), f(&r));
                if (nt.as_str(), nr.as_str()) == (t.as_str(), r.as_str()) {
                    continue;
                }
                tx.execute(
                    "DELETE FROM read_marks WHERE thread_id = ?1 AND reader = ?2",
                    params![t, r],
                )?;
                tx.execute(
                    "INSERT OR REPLACE INTO read_marks (thread_id, reader, message_id)
                     VALUES (?1, ?2, ?3)",
                    params![nt, nr, m],
                )?;
            }
            let opened: Vec<(String, String)> = {
                let mut st = tx.prepare(
                    "SELECT thread_id, opened_by FROM threads WHERE opened_by LIKE 'human:%'",
                )?;
                let v = st
                    .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                v
            };
            for (t, o) in opened {
                let n = f(&o);
                if n != o {
                    tx.execute(
                        "UPDATE threads SET opened_by = ?2 WHERE thread_id = ?1",
                        params![t, n],
                    )?;
                }
            }
            tx.commit()?;
            Ok(moved)
        })
    }
}

impl MessageDb {
    /// Every reader's read mark in a thread, with where the marked row sits:
    /// `(reader, message_id, position)` ([`MessageDb::position`]); a mark on a row that is
    /// gone is left out.
    pub fn read_marks_of(&self, thread_id: &str) -> Result<Vec<(String, String, i64)>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT r.reader, r.message_id, m.rowid FROM read_marks r
             JOIN messages m ON m.message_id = r.message_id
             WHERE r.thread_id = ?1 ORDER BY r.reader",
        )?;
        let rows = st.query_map(params![thread_id], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }
}
