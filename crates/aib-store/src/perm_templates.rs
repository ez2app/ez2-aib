//! The person's permission templates (aibd §7, DESIGN-brainnote-08de5-1123 §2;
//! DECISION-council-aipartner-ebab8-1122 §一-5): a name, a base mode and each item on or off,
//! kept in the message library (not a settings file an AI could edit), and a log row for every
//! add, change and removal (§一-9: who, when, from what to what).
//!
//! Tables only ever added (an older aibd never reads them). Written only by aibd's page acts;
//! what a template's items mean and when a change needs the owner's fingerprint is aibd's
//! (`aibd::perm_tpl`), not this file's: here a row is kept as given, a change is checked
//! against the revision the page saw, and a name is unique among the live templates.

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS perm_templates (
    tpl_uid     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    base        TEXT NOT NULL,
    items_json  TEXT NOT NULL,
    rev         INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS perm_templates_name_live ON perm_templates (name)
    WHERE archived_at IS NULL;
CREATE TABLE IF NOT EXISTS perm_template_log (
    tpl_uid   TEXT NOT NULL,
    rev       INTEGER NOT NULL,
    action    TEXT NOT NULL,
    from_json TEXT,
    to_json   TEXT,
    at        TEXT NOT NULL,
    by        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS perm_template_log_tpl ON perm_template_log (tpl_uid, rev);
";

/// Add the tables when they are missing (every open; nothing when they are there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// One template as kept.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PermTemplate {
    pub tpl_uid: String,
    pub name: String,
    pub base: String,
    /// aibd's normal form of the items (a JSON object of item ⇒ bool, keys sorted).
    pub items_json: String,
    pub rev: i64,
    pub created_at: String,
    pub created_by: String,
    pub updated_at: String,
    pub updated_by: String,
    pub archived_at: Option<String>,
}

/// One log row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PermTemplateLog {
    pub tpl_uid: String,
    pub rev: i64,
    /// `add` / `set` / `remove`.
    pub action: String,
    pub from_json: Option<String>,
    pub to_json: Option<String>,
    pub at: String,
    pub by: String,
}

/// What a write did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TplWrite {
    /// Written; the template's revision now.
    Done(i64),
    /// No live template with this uid.
    Unknown,
    /// The template is at another revision than the page saw.
    RevChanged(i64),
    /// Another live template has this name.
    NameTaken,
}

const COLS: &str = "tpl_uid, name, base, items_json, rev, created_at, created_by, updated_at, \
                    updated_by, archived_at";

fn from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<PermTemplate> {
    Ok(PermTemplate {
        tpl_uid: r.get(0)?,
        name: r.get(1)?,
        base: r.get(2)?,
        items_json: r.get(3)?,
        rev: r.get(4)?,
        created_at: r.get(5)?,
        created_by: r.get(6)?,
        updated_at: r.get(7)?,
        updated_by: r.get(8)?,
        archived_at: r.get(9)?,
    })
}

fn need(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(StoreError::Invalid(why))
    }
}

/// The JSON a log row keeps of a template's state.
fn state_json(name: &str, base: &str, items_json: &str) -> String {
    let items: serde_json::Value =
        serde_json::from_str(items_json).unwrap_or(serde_json::Value::Null);
    serde_json::json!({"name": name, "base": base, "items": items}).to_string()
}

fn name_taken(c: &Connection, name: &str, other_than: &str) -> rusqlite::Result<bool> {
    let n: i64 = c.query_row(
        "SELECT COUNT(*) FROM perm_templates WHERE name = ?1 AND archived_at IS NULL \
         AND tpl_uid <> ?2",
        params![name, other_than],
        |r| r.get(0),
    )?;
    Ok(n > 0)
}

fn live(c: &Connection, uid: &str) -> rusqlite::Result<Option<PermTemplate>> {
    c.query_row(
        &format!("SELECT {COLS} FROM perm_templates WHERE tpl_uid = ?1 AND archived_at IS NULL"),
        params![uid],
        from_row,
    )
    .optional()
}

#[allow(clippy::too_many_arguments)]
fn log_row(
    c: &Connection,
    uid: &str,
    rev: i64,
    action: &str,
    from: Option<String>,
    to: Option<String>,
    at: &str,
    by: &str,
) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO perm_template_log (tpl_uid, rev, action, from_json, to_json, at, by)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![uid, rev, action, from, to, at, by],
    )?;
    Ok(())
}

fn checked(uid: &str, name: &str, base: &str, items_json: &str, by: &str, at: &str) -> Result<()> {
    need(
        crate::ranks::key_ok(uid),
        "tpl_uid is 1-64 of A-Za-z0-9_.:-",
    )?;
    need(
        !name.trim().is_empty() && name.chars().count() <= 40,
        "name is 1-40 characters",
    )?;
    need(crate::ranks::key_ok(base), "base is 1-64 of A-Za-z0-9_.:-")?;
    need(
        serde_json::from_str::<serde_json::Map<String, serde_json::Value>>(items_json).is_ok(),
        "items_json is a JSON object",
    )?;
    need(!by.is_empty() && !at.is_empty(), "by and time are needed")
}

impl MessageDb {
    /// Keep a new template at revision 1 (its uid is aibd's, made by the caller).
    pub fn perm_template_add(
        &self,
        uid: &str,
        name: &str,
        base: &str,
        items_json: &str,
        by: &str,
        at: &str,
    ) -> Result<TplWrite> {
        checked(uid, name, base, items_json, by, at)?;
        let (uid, name, base, items, by, at) = (
            uid.to_owned(),
            name.to_owned(),
            base.to_owned(),
            items_json.to_owned(),
            by.to_owned(),
            at.to_owned(),
        );
        self.write(move |c| {
            let tx = c.transaction()?;
            if name_taken(&tx, &name, &uid)? {
                return Ok(TplWrite::NameTaken);
            }
            tx.execute(
                &format!(
                    "INSERT INTO perm_templates ({COLS}) VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, ?5, \
                     ?6, NULL)"
                ),
                params![uid, name, base, items, at, by],
            )?;
            log_row(
                &tx,
                &uid,
                1,
                "add",
                None,
                Some(state_json(&name, &base, &items)),
                &at,
                &by,
            )?;
            tx.commit()?;
            Ok(TplWrite::Done(1))
        })
    }

    /// Change a live template that is at `rev` (the revision the page saw) to the next one.
    #[allow(clippy::too_many_arguments)]
    pub fn perm_template_set(
        &self,
        uid: &str,
        rev: i64,
        name: &str,
        base: &str,
        items_json: &str,
        by: &str,
        at: &str,
    ) -> Result<TplWrite> {
        checked(uid, name, base, items_json, by, at)?;
        let (uid, name, base, items, by, at) = (
            uid.to_owned(),
            name.to_owned(),
            base.to_owned(),
            items_json.to_owned(),
            by.to_owned(),
            at.to_owned(),
        );
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(old) = live(&tx, &uid)? else {
                return Ok(TplWrite::Unknown);
            };
            if old.rev != rev {
                return Ok(TplWrite::RevChanged(old.rev));
            }
            if name_taken(&tx, &name, &uid)? {
                return Ok(TplWrite::NameTaken);
            }
            let next = rev + 1;
            tx.execute(
                "UPDATE perm_templates SET name = ?2, base = ?3, items_json = ?4, rev = ?5,
                 updated_at = ?6, updated_by = ?7 WHERE tpl_uid = ?1",
                params![uid, name, base, items, next, at, by],
            )?;
            log_row(
                &tx,
                &uid,
                next,
                "set",
                Some(state_json(&old.name, &old.base, &old.items_json)),
                Some(state_json(&name, &base, &items)),
                &at,
                &by,
            )?;
            tx.commit()?;
            Ok(TplWrite::Done(next))
        })
    }

    /// Take a live template at `rev` out of the list (the row and its log are kept).
    pub fn perm_template_remove(
        &self,
        uid: &str,
        rev: i64,
        by: &str,
        at: &str,
    ) -> Result<TplWrite> {
        need(
            crate::ranks::key_ok(uid),
            "tpl_uid is 1-64 of A-Za-z0-9_.:-",
        )?;
        need(!by.is_empty() && !at.is_empty(), "by and time are needed")?;
        let (uid, by, at) = (uid.to_owned(), by.to_owned(), at.to_owned());
        self.write(move |c| {
            let tx = c.transaction()?;
            let Some(old) = live(&tx, &uid)? else {
                return Ok(TplWrite::Unknown);
            };
            if old.rev != rev {
                return Ok(TplWrite::RevChanged(old.rev));
            }
            let next = rev + 1;
            tx.execute(
                "UPDATE perm_templates SET archived_at = ?2, rev = ?3, updated_at = ?2,
                 updated_by = ?4 WHERE tpl_uid = ?1",
                params![uid, at, next, by],
            )?;
            log_row(
                &tx,
                &uid,
                next,
                "remove",
                Some(state_json(&old.name, &old.base, &old.items_json)),
                None,
                &at,
                &by,
            )?;
            tx.commit()?;
            Ok(TplWrite::Done(next))
        })
    }

    /// A live template.
    pub fn perm_template(&self, uid: &str) -> Result<Option<PermTemplate>> {
        let c = self.read()?;
        Ok(live(&c, uid)?)
    }

    /// The live templates, by name.
    pub fn perm_templates(&self) -> Result<Vec<PermTemplate>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {COLS} FROM perm_templates WHERE archived_at IS NULL ORDER BY name, tpl_uid \
             LIMIT 200"
        ))?;
        let rows = st
            .query_map([], from_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    /// A template's log, oldest first (removed ones too).
    pub fn perm_template_log(&self, uid: &str) -> Result<Vec<PermTemplateLog>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT tpl_uid, rev, action, from_json, to_json, at, by FROM perm_template_log
             WHERE tpl_uid = ?1 ORDER BY rev LIMIT 500",
        )?;
        let rows = st
            .query_map(params![uid], |r| {
                Ok(PermTemplateLog {
                    tpl_uid: r.get(0)?,
                    rev: r.get(1)?,
                    action: r.get(2)?,
                    from_json: r.get(3)?,
                    to_json: r.get(4)?,
                    at: r.get(5)?,
                    by: r.get(6)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, MessageDb) {
        let d = tempfile::tempdir().expect("tempdir");
        let m = MessageDb::open(&d.path().join("m.db")).expect("open");
        (d, m)
    }

    const ITEMS: &str = r#"{"chat":true,"code":false}"#;

    /// PT-S1: add, change at the revision seen, refuse a stale revision, remove; every step
    /// is logged with from / to.
    #[test]
    fn a_template_changes_only_at_the_revision_seen_and_is_logged() {
        let (_d, m) = db();
        assert_eq!(
            m.perm_template_add("tpl_a", "寫稿", "work", ITEMS, "page:x", "t1")
                .unwrap(),
            TplWrite::Done(1)
        );
        assert_eq!(
            m.perm_template_set("tpl_a", 1, "寫稿2", "work", ITEMS, "page:x", "t2")
                .unwrap(),
            TplWrite::Done(2)
        );
        assert_eq!(
            m.perm_template_set("tpl_a", 1, "寫稿3", "safe", ITEMS, "page:x", "t3")
                .unwrap(),
            TplWrite::RevChanged(2),
            "reason: stale_rev_written"
        );
        assert_eq!(m.perm_template("tpl_a").unwrap().unwrap().name, "寫稿2");
        assert_eq!(
            m.perm_template_remove("tpl_a", 2, "page:x", "t4").unwrap(),
            TplWrite::Done(3)
        );
        assert_eq!(m.perm_template("tpl_a").unwrap(), None);
        assert_eq!(
            m.perm_template_set("tpl_a", 3, "x", "work", ITEMS, "page:x", "t5")
                .unwrap(),
            TplWrite::Unknown,
            "reason: removed_changed"
        );
        let log = m.perm_template_log("tpl_a").unwrap();
        assert_eq!(
            log.iter().map(|l| l.action.as_str()).collect::<Vec<_>>(),
            ["add", "set", "remove"]
        );
        assert!(log[1].from_json.as_deref().unwrap().contains("寫稿"));
        assert!(log[1].to_json.as_deref().unwrap().contains("寫稿2"));
        assert_eq!(log[2].to_json, None);
    }

    /// PT-S2: a name is unique among live templates; a removed one frees it.
    #[test]
    fn a_name_is_unique_among_live_templates() {
        let (_d, m) = db();
        m.perm_template_add("tpl_a", "同名", "work", ITEMS, "p", "t")
            .unwrap();
        assert_eq!(
            m.perm_template_add("tpl_b", "同名", "safe", ITEMS, "p", "t")
                .unwrap(),
            TplWrite::NameTaken
        );
        m.perm_template_add("tpl_c", "別名", "safe", ITEMS, "p", "t")
            .unwrap();
        assert_eq!(
            m.perm_template_set("tpl_c", 1, "同名", "safe", ITEMS, "p", "t")
                .unwrap(),
            TplWrite::NameTaken
        );
        m.perm_template_remove("tpl_a", 1, "p", "t").unwrap();
        assert_eq!(
            m.perm_template_add("tpl_b", "同名", "safe", ITEMS, "p", "t")
                .unwrap(),
            TplWrite::Done(1)
        );
        assert_eq!(m.perm_templates().unwrap().len(), 2);
    }

    /// PT-S3: a blank or long name, a bad uid or items that are not an object are refused.
    #[test]
    fn a_bad_template_is_refused() {
        let (_d, m) = db();
        assert!(m
            .perm_template_add("tpl_a", "  ", "work", ITEMS, "p", "t")
            .is_err());
        assert!(m
            .perm_template_add("tpl_a", &"字".repeat(41), "work", ITEMS, "p", "t")
            .is_err());
        assert!(m
            .perm_template_add("tpl a", "x", "work", ITEMS, "p", "t")
            .is_err());
        assert!(m
            .perm_template_add("tpl_a", "x", "work", "[1]", "p", "t")
            .is_err());
    }
}
