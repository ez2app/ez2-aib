//! What aibd archived among personas / second brains / roles, the persona uids kept as
//! aliases after a merge, and the merge journal (aibd §1 封存 / 還原 and §8 合併類似;
//! DESIGN-merge-6770f-1123.md r1 §5 §7, f163c REVIEW-merge-6770f-f163c-r1 passed; table
//! shape agreed with 08de5 for §1, q_15cdafec0bb53f5b).
//!
//! - `archived_items`: one row per archiving. Persona cards stay in the 1.x registry's file,
//!   which aibd never changes: an archived persona is this row laid over it (lists leave it
//!   out, opening a seat with it is refused). `copy_dir` is where aibd kept its copy, relative
//!   to `<data>/archive/`. Restoring keeps the row (`restored_at`).
//! - `persona_aliases`: an old uid that now means another (a merged persona). Chat logs,
//!   handoffs and memory packs still carry the old uid, so lookups go through here.
//! - `merge_batches` / `merge_steps`: every step is written `pending` with its `before` and
//!   its planned `after` before anything changes, then `done` (f163c r0 L1); a rollback reads
//!   them back (`aibd::merge_plan::back`).
//!
//! In the message library's own file as tables only ever added (an older aibd never reads
//! them). aibd writes them; nothing here holds off another process of the same user (a dgrs
//! seat can open the file): these are records, not a guard (GUIDE-same-user-ai-threat-model).

use crate::{MessageDb, Result, StoreError};
use rusqlite::{params, Connection, OptionalExtension};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS archived_items (
    kind         TEXT NOT NULL,
    uid          TEXT NOT NULL,
    archived_at  INTEGER NOT NULL,
    keep_until   INTEGER,
    reason       TEXT NOT NULL,
    batch_id     TEXT,
    copy_dir     TEXT,
    sha256       TEXT,
    restored_at  INTEGER,
    PRIMARY KEY (kind, uid, archived_at)
);
CREATE INDEX IF NOT EXISTS archived_items_live ON archived_items (kind, restored_at);
CREATE TABLE IF NOT EXISTS persona_aliases (
    old_uid   TEXT PRIMARY KEY,
    new_uid   TEXT NOT NULL,
    batch_id  TEXT NOT NULL,
    at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS merge_batches (
    batch_id    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    by_key      TEXT NOT NULL,
    at          INTEGER NOT NULL,
    state       TEXT NOT NULL,
    plan_sha    TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    keep_until  INTEGER
);
CREATE TABLE IF NOT EXISTS merge_steps (
    batch_id            TEXT NOT NULL,
    seq                 INTEGER NOT NULL,
    op                  TEXT NOT NULL,
    target              TEXT NOT NULL,
    before_json         TEXT NOT NULL,
    planned_after_json  TEXT NOT NULL,
    state               TEXT NOT NULL,
    PRIMARY KEY (batch_id, seq)
);
CREATE TABLE IF NOT EXISTS seat_next (
    seat_id       TEXT PRIMARY KEY,
    persona_next  TEXT,
    brain_next    TEXT,
    batch_id      TEXT NOT NULL,
    at            INTEGER NOT NULL
);
";

/// Add the tables when they are missing (every open; nothing when they are there).
pub(crate) fn add_tables(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

/// The kinds archived here.
pub const KINDS: [&str; 3] = ["persona", "brain", "role"];
/// Why an item was archived.
pub const REASONS: [&str; 2] = ["archive", "merge"];
/// A merge batch's states.
pub const BATCH_STATES: [&str; 5] = ["run", "done", "failed", "undone", "partial_undo"];

/// The states a batch may move to `state` from (f163c regarch L2): a running batch ends done
/// or failed; an ended one is taken back whole or in part; a part taken back is finished.
pub fn batch_from(state: &str) -> &'static [&'static str] {
    match state {
        "done" | "failed" => &["run"],
        // f163c N4: a failed batch was already rolled back (IFACE r2 §3); undoing it again
        // would undo the rolled-back steps once more
        "undone" => &["done", "partial_undo"],
        "partial_undo" => &["done"],
        _ => &[],
    }
}

/// R3 (stack10 84bdef21b1: merge_undo ended `partial_undo` with every step "還原時寫入失敗",
/// because a `done` batch's steps could not become `undone`): in which batch states a step
/// may move to `step_state`. `done` only while the batch runs; `undone` while it runs, once
/// it is `done` or after a `partial_undo`, never once it `failed` (already put back, N4).
pub fn step_moves_in(step_state: &str, batch_state: &str) -> bool {
    match step_state {
        "done" => batch_state == "run",
        "undone" => matches!(batch_state, "run" | "done" | "partial_undo"),
        _ => false,
    }
}

/// A step's states.
pub const STEP_STATES: [&str; 3] = ["pending", "done", "undone"];
/// How many alias hops a lookup follows before it gives up (a loop or a long chain).
pub const ALIAS_HOPS_MAX: usize = 4;
/// The most a JSON value of a step or a plan may be.
pub const JSON_MAX: usize = 256 * 1024;
/// The most rows one list gives.
pub const LIST_MAX: u32 = 500;

/// A chain `up` hops before the new alias and `down` after it: longer than a lookup follows.
pub fn chain_too_long(up: usize, down: usize) -> bool {
    up + 1 + down > ALIAS_HOPS_MAX
}

fn need(ok: bool, why: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(StoreError::Invalid(why))
    }
}

fn key_ok(s: &str) -> bool {
    crate::ranks::key_ok(s)
}

/// A path under `<data>/archive/`: `/`-separated plain pieces (same rule as brain notes).
fn copy_dir_ok(p: &str) -> bool {
    crate::brain_notes::rel_path_ok(p)
}

fn sha_ok(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())
}

fn json_ok(s: &str) -> bool {
    s.len() <= JSON_MAX && serde_json::from_str::<serde_json::Value>(s).is_ok()
}

/// One archiving.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchivedItem {
    pub kind: String,
    pub uid: String,
    /// Unix seconds.
    pub archived_at: i64,
    /// Unix seconds after which the copy may be cleared; `None` = kept for good.
    pub keep_until: Option<i64>,
    pub reason: String,
    pub batch_id: Option<String>,
    pub copy_dir: Option<String>,
    pub sha256: Option<String>,
    pub restored_at: Option<i64>,
}

const ACOLS: &str =
    "kind, uid, archived_at, keep_until, reason, batch_id, copy_dir, sha256, restored_at";

fn arow(r: &rusqlite::Row<'_>) -> rusqlite::Result<ArchivedItem> {
    Ok(ArchivedItem {
        kind: r.get(0)?,
        uid: r.get(1)?,
        archived_at: r.get(2)?,
        keep_until: r.get(3)?,
        reason: r.get(4)?,
        batch_id: r.get(5)?,
        copy_dir: r.get(6)?,
        sha256: r.get(7)?,
        restored_at: r.get(8)?,
    })
}

/// One merge batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MergeBatch {
    pub batch_id: String,
    pub kind: String,
    pub by_key: String,
    pub at: i64,
    pub state: String,
    pub plan_sha: String,
    pub plan_json: String,
    pub keep_until: Option<i64>,
}

const BCOLS: &str = "batch_id, kind, by_key, at, state, plan_sha, plan_json, keep_until";

fn brow(r: &rusqlite::Row<'_>) -> rusqlite::Result<MergeBatch> {
    Ok(MergeBatch {
        batch_id: r.get(0)?,
        kind: r.get(1)?,
        by_key: r.get(2)?,
        at: r.get(3)?,
        state: r.get(4)?,
        plan_sha: r.get(5)?,
        plan_json: r.get(6)?,
        keep_until: r.get(7)?,
    })
}

/// One step of a merge batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MergeStep {
    pub batch_id: String,
    pub seq: i64,
    /// What the step does (`alias`, `archive`, `seat_next`, `brain_rebind`, `role_reassign`…).
    pub op: String,
    /// What it changes (a uid, a seat id, `node|seat|role`…).
    pub target: String,
    pub before_json: String,
    pub planned_after_json: String,
    pub state: String,
}

const SCOLS: &str = "batch_id, seq, op, target, before_json, planned_after_json, state";

fn srow(r: &rusqlite::Row<'_>) -> rusqlite::Result<MergeStep> {
    Ok(MergeStep {
        batch_id: r.get(0)?,
        seq: r.get(1)?,
        op: r.get(2)?,
        target: r.get(3)?,
        before_json: r.get(4)?,
        planned_after_json: r.get(5)?,
        state: r.get(6)?,
    })
}

impl MessageDb {
    /// Keep an archiving. The same item archived and not yet restored is refused.
    pub fn archive_add(&self, a: ArchivedItem) -> Result<()> {
        need(
            KINDS.contains(&a.kind.as_str()),
            "kind is persona, brain or role",
        )?;
        need(key_ok(&a.uid), "uid is 1-64 of A-Za-z0-9_.:-")?;
        need(
            REASONS.contains(&a.reason.as_str()),
            "reason is archive or merge",
        )?;
        need(
            a.batch_id.as_deref().is_none_or(key_ok),
            "batch id is 1-64 of A-Za-z0-9_.:-",
        )?;
        need(
            a.copy_dir.as_deref().is_none_or(copy_dir_ok),
            "copy_dir is a path inside the archive",
        )?;
        need(a.sha256.as_deref().is_none_or(sha_ok), "sha256 is 64 hex")?;
        need(a.restored_at.is_none(), "a new archiving is not restored")?;
        need(
            a.keep_until.is_none_or(|k| k >= a.archived_at),
            "keep_until is not before archived_at",
        )?;
        self.write(move |c| {
            let tx = c.transaction()?;
            let live: i64 = tx.query_row(
                "SELECT COUNT(*) FROM archived_items WHERE kind = ?1 AND uid = ?2 \
                 AND restored_at IS NULL",
                params![a.kind, a.uid],
                |r| r.get(0),
            )?;
            if live > 0 {
                return Err(StoreError::Invalid("already archived"));
            }
            let same_ts: i64 = tx.query_row(
                "SELECT COUNT(*) FROM archived_items WHERE kind = ?1 AND uid = ?2 \
                 AND archived_at = ?3",
                params![a.kind, a.uid, a.archived_at],
                |r| r.get(0),
            )?;
            if same_ts > 0 {
                return Err(StoreError::Invalid("already archived at this timestamp"));
            }
            tx.execute(
                &format!("INSERT INTO archived_items ({ACOLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, NULL)"),
                params![
                    a.kind,
                    a.uid,
                    a.archived_at,
                    a.keep_until,
                    a.reason,
                    a.batch_id,
                    a.copy_dir,
                    a.sha256
                ],
            )?;
            tx.commit()?;
            Ok(())
        })
    }

    /// The live (not restored) archiving of one item, if any.
    pub fn archived(&self, kind: &str, uid: &str) -> Result<Option<ArchivedItem>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!(
                "SELECT {ACOLS} FROM archived_items WHERE kind = ?1 AND uid = ?2 \
                 AND restored_at IS NULL"
            ),
            params![kind, uid],
            arow,
        )
        .optional()?)
    }

    /// Every live archiving of a kind, newest first (at most [`LIST_MAX`]).
    pub fn archived_list(&self, kind: &str) -> Result<Vec<ArchivedItem>> {
        let c = self.read()?;
        let mut st = c.prepare(&format!(
            "SELECT {ACOLS} FROM archived_items WHERE kind = ?1 AND restored_at IS NULL \
             ORDER BY archived_at DESC, uid LIMIT {LIST_MAX}"
        ))?;
        let v = st
            .query_map(params![kind], arow)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(v)
    }

    /// Mark the live archiving of one item restored. `false` = nothing live to restore.
    /// R6: `at` must be `>= archived_at`, else `Invalid`.
    pub fn archive_restore(&self, kind: &str, uid: &str, at: i64) -> Result<bool> {
        let (kind, uid) = (kind.to_string(), uid.to_string());
        self.write(move |c| {
            let tx = c.transaction()?;
            let archived_at: Option<i64> = tx
                .query_row(
                    "SELECT archived_at FROM archived_items WHERE kind = ?1 AND uid = ?2 \
                     AND restored_at IS NULL",
                    params![kind, uid],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(arch) = archived_at {
                need(at >= arch, "restore time before archived_at")?;
            }
            let n = tx.execute(
                "UPDATE archived_items SET restored_at = ?3 WHERE kind = ?1 AND uid = ?2 \
                 AND restored_at IS NULL",
                params![kind, uid, at],
            )?;
            tx.commit()?;
            Ok(n > 0)
        })
    }

    /// Say `old` now means `new`. Refused when `old` == `new`, when `old` already has an
    /// alias, or when `new` (followed) leads back to `old`.
    pub fn alias_set(&self, old: &str, new: &str, batch_id: &str, at: i64) -> Result<()> {
        need(key_ok(old) && key_ok(new), "uid is 1-64 of A-Za-z0-9_.:-")?;
        need(key_ok(batch_id), "batch id is 1-64 of A-Za-z0-9_.:-")?;
        need(old != new, "an alias of itself")?;
        let (old, new, batch_id) = (old.to_string(), new.to_string(), batch_id.to_string());
        self.write(move |c| {
            let tx = c.transaction()?;
            // `new` followed must not reach `old`, and must end (f163c regarch L1)
            let mut cur = new.clone();
            let mut down = 0usize;
            loop {
                if cur == old {
                    return Err(StoreError::Invalid("alias loop"));
                }
                let next: Option<String> = tx
                    .query_row(
                        "SELECT new_uid FROM persona_aliases WHERE old_uid = ?1",
                        params![cur],
                        |r| r.get(0),
                    )
                    .optional()?;
                let Some(n) = next else { break };
                down += 1;
                if down > ALIAS_HOPS_MAX {
                    return Err(StoreError::Invalid("alias chain too long"));
                }
                cur = n;
            }
            // the longest chain that ends at `old`
            let mut up = 0usize;
            let mut level = vec![old.clone()];
            loop {
                let mut st =
                    tx.prepare("SELECT old_uid FROM persona_aliases WHERE new_uid = ?1")?;
                let mut before = Vec::new();
                for x in &level {
                    before.extend(
                        st.query_map(params![x], |r| r.get::<_, String>(0))?
                            .collect::<rusqlite::Result<Vec<_>>>()?,
                    );
                }
                if before.is_empty() {
                    break;
                }
                up += 1;
                if up > ALIAS_HOPS_MAX {
                    return Err(StoreError::Invalid("alias chain too long"));
                }
                level = before;
            }
            // every old uid on the chain still resolves (at most ALIAS_HOPS_MAX hops)
            if chain_too_long(up, down) {
                return Err(StoreError::Invalid("alias chain too long"));
            }
            let n = tx.execute(
                "INSERT OR IGNORE INTO persona_aliases (old_uid, new_uid, batch_id, at) \
                 VALUES (?1, ?2, ?3, ?4)",
                params![old, new, batch_id, at],
            )?;
            if n == 0 {
                return Err(StoreError::Invalid("already an alias"));
            }
            tx.commit()?;
            Ok(())
        })
    }

    /// The uid `uid` means now: itself when it has no alias, else the end of its alias chain.
    /// A chain longer than [`ALIAS_HOPS_MAX`] (or a loop written by hand) is refused.
    pub fn alias_resolve(&self, uid: &str) -> Result<String> {
        let c = self.read()?;
        let mut cur = uid.to_string();
        for _ in 0..=ALIAS_HOPS_MAX {
            let next: Option<String> = c
                .query_row(
                    "SELECT new_uid FROM persona_aliases WHERE old_uid = ?1",
                    params![cur],
                    |r| r.get(0),
                )
                .optional()?;
            match next {
                Some(n) => cur = n,
                None => return Ok(cur),
            }
        }
        Err(StoreError::Invalid("alias chain too long"))
    }

    /// Every uid of a kind archived now, with no limit (f163c regarch L3: a list that
    /// stopped at [`LIST_MAX`] would show the rest as live).
    pub fn archived_uids(&self, kind: &str) -> Result<Vec<String>> {
        let c = self.read()?;
        let mut st = c.prepare(
            "SELECT uid FROM archived_items WHERE kind = ?1 AND restored_at IS NULL ORDER BY uid",
        )?;
        let v = st
            .query_map(params![kind], |r| r.get(0))?
            .collect::<rusqlite::Result<Vec<String>>>()?;
        Ok(v)
    }

    /// Take back the aliases a batch wrote; how many.
    pub fn alias_remove_batch(&self, batch_id: &str) -> Result<usize> {
        let b = batch_id.to_string();
        self.write(move |c| {
            Ok(c.execute(
                "DELETE FROM persona_aliases WHERE batch_id = ?1",
                params![b],
            )?)
        })
    }

    /// Start a merge batch (state `run`).
    pub fn merge_batch_begin(&self, b: MergeBatch) -> Result<()> {
        need(
            key_ok(&b.batch_id) && key_ok(&b.by_key),
            "batch and by are 1-64 of A-Za-z0-9_.:-",
        )?;
        need(
            KINDS.contains(&b.kind.as_str()),
            "kind is persona, brain or role",
        )?;
        need(b.state == "run", "a batch starts in run")?;
        need(sha_ok(&b.plan_sha), "plan_sha is 64 hex")?;
        need(
            json_ok(&b.plan_json),
            "plan_json is JSON of at most 256 KiB",
        )?;
        self.write(move |c| {
            c.execute(
                &format!(
                    "INSERT INTO merge_batches ({BCOLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)"
                ),
                params![
                    b.batch_id,
                    b.kind,
                    b.by_key,
                    b.at,
                    b.state,
                    b.plan_sha,
                    b.plan_json,
                    b.keep_until
                ],
            )?;
            Ok(())
        })
    }

    /// Move a batch to another state, only along [`batch_from`]. `false` = no such batch, or
    /// not in a state that moves there.
    pub fn merge_batch_state(&self, batch_id: &str, state: &str) -> Result<bool> {
        need(BATCH_STATES.contains(&state), "unknown batch state")?;
        let from = batch_from(state);
        need(!from.is_empty(), "a batch does not move to that state")?;
        let (b, s) = (batch_id.to_string(), state.to_string());
        let list = from
            .iter()
            .map(|f| format!("'{f}'"))
            .collect::<Vec<_>>()
            .join(", ");
        self.write(move |c| {
            Ok(c.execute(
                &format!(
                    "UPDATE merge_batches SET state = ?2 WHERE batch_id = ?1 AND state IN ({list})"
                ),
                params![b, s],
            )? > 0)
        })
    }

    /// One batch.
    pub fn merge_batch(&self, batch_id: &str) -> Result<Option<MergeBatch>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {BCOLS} FROM merge_batches WHERE batch_id = ?1"),
            params![batch_id],
            brow,
        )
        .optional()?)
    }

    /// The batches, newest first; `only_state` filters (e.g. `run` at start-up).
    pub fn merge_batches(&self, only_state: Option<&str>, limit: u32) -> Result<Vec<MergeBatch>> {
        let c = self.read()?;
        let lim = limit.min(LIST_MAX);
        let v = match only_state {
            Some(s) => c
                .prepare(&format!(
                    "SELECT {BCOLS} FROM merge_batches WHERE state = ?1 \
                     ORDER BY at DESC, batch_id LIMIT {lim}"
                ))?
                .query_map(params![s], brow)?
                .collect::<rusqlite::Result<Vec<_>>>()?,
            None => c
                .prepare(&format!(
                    "SELECT {BCOLS} FROM merge_batches ORDER BY at DESC, batch_id LIMIT {lim}"
                ))?
                .query_map([], brow)?
                .collect::<rusqlite::Result<Vec<_>>>()?,
        };
        Ok(v)
    }

    /// Write a step `pending` with its `before` and planned `after`, before it changes
    /// anything (f163c r0 L1). Steps of a batch are numbered from 0 with no gap.
    pub fn merge_step_pending(&self, s: MergeStep) -> Result<()> {
        need(key_ok(&s.batch_id), "batch id is 1-64 of A-Za-z0-9_.:-")?;
        need(key_ok(&s.op), "op is 1-64 of A-Za-z0-9_.:-")?;
        need(
            !s.target.is_empty() && s.target.len() <= 200,
            "target is 1-200 bytes",
        )?;
        need(
            json_ok(&s.before_json) && json_ok(&s.planned_after_json),
            "before and after are JSON of at most 256 KiB",
        )?;
        need(s.state == "pending", "a step starts pending")?;
        self.write(move |c| {
            let tx = c.transaction()?;
            let st: Option<String> = tx
                .query_row(
                    "SELECT state FROM merge_batches WHERE batch_id = ?1",
                    params![s.batch_id],
                    |r| r.get(0),
                )
                .optional()?;
            if st.as_deref() != Some("run") {
                return Err(StoreError::Invalid("the batch is not running"));
            }
            let next: i64 = tx.query_row(
                "SELECT COUNT(*) FROM merge_steps WHERE batch_id = ?1",
                params![s.batch_id],
                |r| r.get(0),
            )?;
            if s.seq != next {
                return Err(StoreError::Invalid("steps are numbered from 0 with no gap"));
            }
            tx.execute(
                &format!("INSERT INTO merge_steps ({SCOLS}) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"),
                params![
                    s.batch_id,
                    s.seq,
                    s.op,
                    s.target,
                    s.before_json,
                    s.planned_after_json,
                    s.state
                ],
            )?;
            tx.commit()?;
            Ok(())
        })
    }

    /// Move a step: `pending` → `done`, or `pending` / `done` → `undone`. `false` = the
    /// step is not in a state that moves there. R3: a step becomes `done` only in a running
    /// batch; it becomes `undone` while the batch runs (put back midway), once it is `done`
    /// (merge_undo) or `partial_undo` (undo tried again), never in a `failed` one (N4: that
    /// batch was already put back). See [`step_moves_in`].
    pub fn merge_step_state(&self, batch_id: &str, seq: i64, state: &str) -> Result<bool> {
        let from: &[&str] = match state {
            "done" => &["pending"],
            "undone" => &["pending", "done"],
            _ => return Err(StoreError::Invalid("a step moves to done or undone")),
        };
        let (b, s) = (batch_id.to_string(), state.to_string());
        let (f0, f1) = (
            from[0].to_string(),
            from.get(1).unwrap_or(&from[0]).to_string(),
        );
        self.write(move |c| {
            let tx = c.transaction()?;
            let batch_state: Option<String> = tx
                .query_row(
                    "SELECT state FROM merge_batches WHERE batch_id = ?1",
                    params![b],
                    |r| r.get(0),
                )
                .optional()?;
            need(
                batch_state
                    .as_deref()
                    .is_some_and(|bs| step_moves_in(&s, bs)),
                "batch is not in a state whose steps move there",
            )?;
            let n = tx.execute(
                "UPDATE merge_steps SET state = ?3 WHERE batch_id = ?1 AND seq = ?2 \
                 AND state IN (?4, ?5)",
                params![b, seq, s, f0, f1],
            )?;
            tx.commit()?;
            Ok(n > 0)
        })
    }

    /// A batch's steps, in order.
    pub fn merge_steps(&self, batch_id: &str) -> Result<Vec<MergeStep>> {
        let c = self.read()?;
        let v = c
            .prepare(&format!(
                "SELECT {SCOLS} FROM merge_steps WHERE batch_id = ?1 ORDER BY seq"
            ))?
            .query_map(params![batch_id], srow)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(v)
    }

    /// Say what a seat switches to the next time it opens or is revived (f163c REVIEW-merge
    /// r0 M1: a running seat keeps its persona and brain; the merge writes here, never into
    /// the seat's own record). A later merge for the same seat replaces the row; a field
    /// left `None` keeps the earlier one's value for that field.
    pub fn seat_next_set(
        &self,
        seat_id: &str,
        persona_next: Option<&str>,
        brain_next: Option<&str>,
        batch_id: &str,
        at: i64,
    ) -> Result<()> {
        need(key_ok(seat_id), "seat is 1-64 of A-Za-z0-9_.:-")?;
        need(key_ok(batch_id), "batch id is 1-64 of A-Za-z0-9_.:-")?;
        need(
            persona_next.is_some() || brain_next.is_some(),
            "a persona or a brain to switch to",
        )?;
        need(
            persona_next.is_none_or(key_ok),
            "persona uid is 1-64 of A-Za-z0-9_.:-",
        )?;
        need(
            brain_next.is_none_or(|b| !b.is_empty() && b.len() <= 200 && !b.contains('\0')),
            "brain route is 1-200 bytes",
        )?;
        let (s, p, b, bt) = (
            seat_id.to_string(),
            persona_next.map(str::to_string),
            brain_next.map(str::to_string),
            batch_id.to_string(),
        );
        self.write(move |c| {
            c.execute(
                "INSERT INTO seat_next (seat_id, persona_next, brain_next, batch_id, at) \
                 VALUES (?1, ?2, ?3, ?4, ?5) \
                 ON CONFLICT(seat_id) DO UPDATE SET \
                 persona_next = COALESCE(excluded.persona_next, seat_next.persona_next), \
                 brain_next = COALESCE(excluded.brain_next, seat_next.brain_next), \
                 batch_id = excluded.batch_id, at = excluded.at",
                params![s, p, b, bt, at],
            )?;
            Ok(())
        })
    }

    /// What a seat switches to next time, if anything (the page shows「下次開換成 X」).
    pub fn seat_next(&self, seat_id: &str) -> Result<Option<SeatNext>> {
        let c = self.read()?;
        Ok(c.query_row(
            &format!("SELECT {NCOLS} FROM seat_next WHERE seat_id = ?1"),
            params![seat_id],
            nrow,
        )
        .optional()?)
    }

    /// Take a seat's switch when it opens or is revived: read and remove it in one
    /// transaction, so it is applied once.
    pub fn seat_next_take(&self, seat_id: &str) -> Result<Option<SeatNext>> {
        let s = seat_id.to_string();
        self.write(move |c| {
            let tx = c.transaction()?;
            let row = tx
                .query_row(
                    &format!("SELECT {NCOLS} FROM seat_next WHERE seat_id = ?1"),
                    params![s],
                    nrow,
                )
                .optional()?;
            if row.is_some() {
                tx.execute("DELETE FROM seat_next WHERE seat_id = ?1", params![s])?;
            }
            tx.commit()?;
            Ok(row)
        })
    }

    /// The switch `seat_next` read is applied: remove that row only (a later batch's row for
    /// the same seat, written meanwhile, stays). Whether it was removed.
    pub fn seat_next_done(&self, seat_id: &str, batch_id: &str, at: i64) -> Result<bool> {
        let (s, b) = (seat_id.to_string(), batch_id.to_string());
        self.write(move |c| {
            Ok(c.execute(
                "DELETE FROM seat_next WHERE seat_id = ?1 AND batch_id = ?2 AND at = ?3",
                params![s, b, at],
            )? > 0)
        })
    }

    /// Take back the switches a batch wrote (its undo); how many.
    pub fn seat_next_clear_batch(&self, batch_id: &str) -> Result<usize> {
        let b = batch_id.to_string();
        self.write(move |c| Ok(c.execute("DELETE FROM seat_next WHERE batch_id = ?1", params![b])?))
    }
}

/// A seat's switch for its next open.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SeatNext {
    pub seat_id: String,
    pub persona_next: Option<String>,
    /// A brain's route key (what `seat_meta.brain_route` holds).
    pub brain_next: Option<String>,
    pub batch_id: String,
    pub at: i64,
}

const NCOLS: &str = "seat_id, persona_next, brain_next, batch_id, at";

fn nrow(r: &rusqlite::Row<'_>) -> rusqlite::Result<SeatNext> {
    Ok(SeatNext {
        seat_id: r.get(0)?,
        persona_next: r.get(1)?,
        brain_next: r.get(2)?,
        batch_id: r.get(3)?,
        at: r.get(4)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> (tempfile::TempDir, MessageDb) {
        let d = tempfile::tempdir().expect("tempdir");
        let m = MessageDb::open(&d.path().join("m.db")).expect("open");
        (d, m)
    }

    fn item(kind: &str, uid: &str, at: i64) -> ArchivedItem {
        ArchivedItem {
            kind: kind.into(),
            uid: uid.into(),
            archived_at: at,
            keep_until: Some(at + 30 * 86_400),
            reason: "merge".into(),
            batch_id: Some("mb_1".into()),
            copy_dir: Some(format!("persona/{uid}-{at}")),
            sha256: Some("a".repeat(64)),
            restored_at: None,
        }
    }

    fn batch(id: &str) -> MergeBatch {
        MergeBatch {
            batch_id: id.into(),
            kind: "persona".into(),
            by_key: "page:p1".into(),
            at: 100,
            state: "run".into(),
            plan_sha: "b".repeat(64),
            plan_json: r#"{"kind":"persona"}"#.into(),
            keep_until: None,
        }
    }

    fn step(b: &str, seq: i64) -> MergeStep {
        MergeStep {
            batch_id: b.into(),
            seq,
            op: "alias".into(),
            target: "aaaaa2".into(),
            before_json: "null".into(),
            planned_after_json: r#""aaaaa1""#.into(),
            state: "pending".into(),
        }
    }

    #[test]
    fn archive_then_restore_then_archive_again() {
        let (_d, m) = db();
        m.archive_add(item("persona", "aaaaa2", 10)).unwrap();
        assert!(m.archived("persona", "aaaaa2").unwrap().is_some());
        assert!(m.archived("brain", "aaaaa2").unwrap().is_none());
        // the same item twice while live: refused
        assert!(m.archive_add(item("persona", "aaaaa2", 11)).is_err());
        assert_eq!(m.archived_list("persona").unwrap().len(), 1);
        assert!(m.archive_restore("persona", "aaaaa2", 20).unwrap());
        assert!(!m.archive_restore("persona", "aaaaa2", 21).unwrap());
        assert!(m.archived("persona", "aaaaa2").unwrap().is_none());
        assert!(m.archived_list("persona").unwrap().is_empty());
        m.archive_add(item("persona", "aaaaa2", 30)).unwrap();
        assert_eq!(
            m.archived("persona", "aaaaa2")
                .unwrap()
                .unwrap()
                .archived_at,
            30
        );
    }

    #[test]
    fn archive_refuses_bad_fields() {
        let (_d, m) = db();
        let bad = |f: &dyn Fn(&mut ArchivedItem)| {
            let mut a = item("persona", "aaaaa2", 10);
            f(&mut a);
            m.archive_add(a).is_err()
        };
        assert!(bad(&|a| a.kind = "seat".into()));
        assert!(bad(&|a| a.uid = "a b".into()));
        assert!(bad(&|a| a.reason = "trash".into()));
        assert!(bad(&|a| a.copy_dir = Some("../x".into())));
        assert!(bad(&|a| a.copy_dir = Some("/abs".into())));
        assert!(bad(&|a| a.copy_dir = Some("a\\b".into())));
        assert!(bad(&|a| a.sha256 = Some("x".into())));
        assert!(bad(&|a| a.restored_at = Some(1)));
        assert!(bad(&|a| a.keep_until = Some(1)));
        // kept for good is fine
        assert!(!bad(&|a| a.keep_until = None));
    }

    #[test]
    fn mg10_aliases_resolve_and_refuse_loops_and_long_chains() {
        let (_d, m) = db();
        assert_eq!(m.alias_resolve("aaaaa1").unwrap(), "aaaaa1");
        m.alias_set("aaaaa2", "aaaaa1", "mb_1", 1).unwrap();
        assert_eq!(m.alias_resolve("aaaaa2").unwrap(), "aaaaa1");
        // a loop back is refused
        assert!(m.alias_set("aaaaa1", "aaaaa2", "mb_2", 2).is_err());
        assert!(m.alias_set("aaaaa1", "aaaaa1", "mb_2", 2).is_err());
        // the same old uid twice is refused
        assert!(m.alias_set("aaaaa2", "aaaaa3", "mb_2", 2).is_err());
        // a chain: 3 → 2 → 1
        m.alias_set("aaaaa3", "aaaaa2", "mb_2", 3).unwrap();
        assert_eq!(m.alias_resolve("aaaaa3").unwrap(), "aaaaa1");
        // undo takes back one batch only
        assert_eq!(m.alias_remove_batch("mb_2").unwrap(), 1);
        assert_eq!(m.alias_resolve("aaaaa3").unwrap(), "aaaaa3");
        assert_eq!(m.alias_resolve("aaaaa2").unwrap(), "aaaaa1");
    }

    /// f163c regarch L1: a chain is written only while every old uid on it still resolves;
    /// one written by hand longer than that is refused when read.
    #[test]
    fn mg10_a_chain_longer_than_the_hops_is_refused() {
        let (_d, m) = db();
        for i in 0..ALIAS_HOPS_MAX {
            m.alias_set(&format!("u{}", i + 1), &format!("u{i}"), "mb_1", 1)
                .unwrap();
        }
        let top = format!("u{ALIAS_HOPS_MAX}");
        assert_eq!(m.alias_resolve(&top).unwrap(), "u0");
        // one more hop at either end is refused
        let over = format!("u{}", ALIAS_HOPS_MAX + 1);
        assert!(
            m.alias_set(&over, &top, "mb_2", 2).is_err(),
            "reason: long_chain_written_above"
        );
        assert!(
            m.alias_set("u0", "w0", "mb_2", 2).is_err(),
            "reason: long_chain_written_below"
        );
        // by hand (no check): read as too long
        let n = ALIAS_HOPS_MAX + 2;
        m.write(move |c| {
            for i in 0..n {
                c.execute(
                    "INSERT INTO persona_aliases (old_uid, new_uid, batch_id, at) VALUES (?1, ?2, 'mb_h', 1)",
                    params![format!("h{}", i + 1), format!("h{i}")],
                )?;
            }
            Ok(())
        })
        .unwrap();
        assert!(m.alias_resolve(&format!("h{n}")).is_err());
    }

    /// f163c regarch L1: two short chains joined by one alias are measured whole, and a loop
    /// at the far end of the new one is found.
    #[test]
    fn mg10_two_chains_joined_are_measured_whole_and_far_loops_found() {
        let (_d, m) = db();
        // a → b → c (2 hops) and d → e → f (2 hops): c → d would make 5
        for (o, n) in [
            ("aaaaa1", "bbbbb1"),
            ("bbbbb1", "ccccc1"),
            ("ddddd1", "eeeee1"),
            ("eeeee1", "fffff1"),
        ] {
            m.alias_set(o, n, "mb_1", 1).unwrap();
        }
        assert!(
            m.alias_set("ccccc1", "ddddd1", "mb_2", 2).is_err(),
            "reason: joined_chain_too_long"
        );
        assert_eq!(m.alias_resolve("aaaaa1").unwrap(), "ccccc1");
        // a loop four hops away: g → h → i → j, then j → g
        for (o, n) in [
            ("ggggg1", "hhhhh1"),
            ("hhhhh1", "iiiii1"),
            ("iiiii1", "jjjjj1"),
        ] {
            m.alias_set(o, n, "mb_3", 3).unwrap();
        }
        assert!(
            m.alias_set("jjjjj1", "ggggg1", "mb_4", 4).is_err(),
            "reason: far_loop"
        );
        assert!(chain_too_long(ALIAS_HOPS_MAX, 0) && !chain_too_long(ALIAS_HOPS_MAX - 1, 0));
    }

    /// f163c regarch L2: a batch moves only along its table.
    #[test]
    fn a_batch_moves_only_along_its_table() {
        let (_d, m) = db();
        m.merge_batch_begin(batch("mb_1")).unwrap();
        assert!(
            !m.merge_batch_state("mb_1", "undone").unwrap(),
            "reason: run_to_undone"
        );
        assert!(
            m.merge_batch_state("mb_1", "run").is_err(),
            "reason: back_to_run"
        );
        assert!(m.merge_batch_state("mb_1", "done").unwrap());
        assert!(
            !m.merge_batch_state("mb_1", "failed").unwrap(),
            "reason: done_to_failed"
        );
        assert!(m.merge_batch_state("mb_1", "partial_undo").unwrap());
        assert!(m.merge_batch_state("mb_1", "undone").unwrap());
        assert!(
            !m.merge_batch_state("mb_1", "done").unwrap(),
            "reason: undone_to_done"
        );
        // and no step is added to a batch that is not running
        assert!(m.merge_step_pending(step("mb_1", 9)).is_err());
        // a failed batch was rolled back already: it is not undone again (f163c N4)
        m.merge_batch_begin(batch("mb_2")).unwrap();
        assert!(m.merge_batch_state("mb_2", "failed").unwrap());
        assert!(
            !m.merge_batch_state("mb_2", "undone").unwrap(),
            "reason: failed_undone_again"
        );
        assert!(!m.merge_batch_state("mb_2", "partial_undo").unwrap());
    }

    /// f163c regarch L3: every archived uid, past the list's limit.
    #[test]
    fn every_archived_uid_is_read_past_the_list_limit() {
        let (_d, m) = db();
        let n = LIST_MAX as usize + 3;
        for i in 0..n {
            m.archive_add(item("persona", &format!("p{i:05}"), i as i64))
                .unwrap();
        }
        assert_eq!(m.archived_list("persona").unwrap().len(), LIST_MAX as usize);
        assert_eq!(
            m.archived_uids("persona").unwrap().len(),
            n,
            "reason: uids_cut_at_the_list"
        );
    }

    #[test]
    fn mg9_steps_are_pending_first_in_order_and_only_while_running() {
        let (_d, m) = db();
        assert!(m.merge_step_pending(step("mb_1", 0)).is_err(), "no batch");
        m.merge_batch_begin(batch("mb_1")).unwrap();
        m.merge_step_pending(step("mb_1", 0)).unwrap();
        assert!(m.merge_step_pending(step("mb_1", 2)).is_err(), "a gap");
        assert!(m.merge_step_pending(step("mb_1", 0)).is_err(), "twice");
        let mut done = step("mb_1", 1);
        done.state = "done".into();
        assert!(m.merge_step_pending(done).is_err(), "not pending");
        m.merge_step_pending(step("mb_1", 1)).unwrap();
        assert!(m.merge_step_state("mb_1", 0, "done").unwrap());
        assert!(
            !m.merge_step_state("mb_1", 0, "done").unwrap(),
            "already done"
        );
        assert!(m.merge_step_state("mb_1", 0, "undone").unwrap());
        assert!(
            m.merge_step_state("mb_1", 1, "undone").unwrap(),
            "pending → undone"
        );
        assert!(m.merge_step_state("mb_1", 1, "pending").is_err());
        let s = m.merge_steps("mb_1").unwrap();
        assert_eq!(
            s.iter().map(|x| x.state.as_str()).collect::<Vec<_>>(),
            ["undone", "undone"]
        );
        assert_eq!(s[0].planned_after_json, r#""aaaaa1""#);
        // a finished batch takes no more steps (R3: merge_step_state also refuses)
        assert!(m.merge_batch_state("mb_1", "failed").unwrap());
        assert!(
            m.merge_step_state("mb_1", 0, "undone").is_err(),
            "N4: a failed batch was already put back"
        );
        assert!(m.merge_step_pending(step("mb_1", 2)).is_err());
        assert!(
            m.merge_step_state("mb_1", 0, "done").is_err(),
            "R3: non-running batch"
        );
    }

    #[test]
    fn a_done_batch_s_steps_can_be_undone_but_not_done_again() {
        // stack10 84bdef21b1: merge_undo runs on a `done` batch; R3 refused every step
        let (_d, m) = db();
        m.merge_batch_begin(batch("mb_1")).unwrap();
        m.merge_step_pending(step("mb_1", 0)).unwrap();
        m.merge_step_pending(step("mb_1", 1)).unwrap();
        assert!(m.merge_step_state("mb_1", 0, "done").unwrap());
        assert!(m.merge_step_state("mb_1", 1, "done").unwrap());
        assert!(m.merge_batch_state("mb_1", "done").unwrap());
        assert!(
            m.merge_step_state("mb_1", 1, "done").is_err(),
            "R3: a done batch takes no new done step"
        );
        assert!(
            m.merge_step_state("mb_1", 1, "undone").unwrap(),
            "reason: undo_refused_on_done_batch"
        );
        assert!(m.merge_batch_state("mb_1", "partial_undo").unwrap());
        assert!(
            m.merge_step_state("mb_1", 0, "undone").unwrap(),
            "reason: retry_refused_on_partial_undo"
        );
        let s = m.merge_steps("mb_1").unwrap();
        assert_eq!(
            s.iter().map(|x| x.state.as_str()).collect::<Vec<_>>(),
            ["undone", "undone"]
        );
    }

    #[test]
    fn which_batch_states_let_a_step_move() {
        for (step, batch, ok) in [
            ("done", "run", true),
            ("done", "done", false),
            ("done", "partial_undo", false),
            ("done", "failed", false),
            ("undone", "run", true),
            ("undone", "done", true),
            ("undone", "partial_undo", true),
            ("undone", "failed", false),
            ("undone", "undone", false),
            ("pending", "run", false),
        ] {
            assert_eq!(step_moves_in(step, batch), ok, "{step} in a {batch} batch");
        }
    }

    #[test]
    fn restore_before_archived_at_is_invalid() {
        let (_d, m) = db();
        m.archive_add(item("persona", "p1", 100)).unwrap();
        assert!(
            m.archive_restore("persona", "p1", 99).is_err(),
            "R6: at < archived_at"
        );
        assert!(m.archive_restore("persona", "p1", 100).unwrap());
        assert!(
            matches!(
                m.archive_add(item("persona", "p1", 100)),
                Err(StoreError::Invalid(_))
            ),
            "N5: same-second primary key must be Invalid"
        );
    }

    #[test]
    fn batches_list_by_state_for_the_start_up_rollback() {
        let (_d, m) = db();
        m.merge_batch_begin(batch("mb_1")).unwrap();
        let mut b2 = batch("mb_2");
        b2.at = 200;
        m.merge_batch_begin(b2).unwrap();
        assert!(m.merge_batch_state("mb_1", "done").unwrap());
        assert!(m.merge_batch_state("mb_1", "bogus").is_err());
        assert!(!m.merge_batch_state("mb_x", "done").unwrap());
        let run = m.merge_batches(Some("run"), 10).unwrap();
        assert_eq!(run.len(), 1);
        assert_eq!(run[0].batch_id, "mb_2");
        assert_eq!(m.merge_batches(None, 10).unwrap()[0].batch_id, "mb_2");
        assert_eq!(m.merge_batch("mb_1").unwrap().unwrap().state, "done");
        // a batch must start running, with a real sha and JSON plan
        let mut b = batch("mb_3");
        b.state = "done".into();
        assert!(m.merge_batch_begin(b).is_err());
        let mut b = batch("mb_3");
        b.plan_json = "{".into();
        assert!(m.merge_batch_begin(b).is_err());
        let mut b = batch("mb_3");
        b.plan_sha = "z".repeat(64);
        assert!(m.merge_batch_begin(b).is_err());
    }

    #[test]
    fn the_tables_come_back_after_a_reopen() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("m.db");
        {
            let m = MessageDb::open(&p).unwrap();
            m.archive_add(item("role", "rol_abc123", 5)).unwrap();
        }
        let m = MessageDb::open(&p).unwrap();
        assert!(m.archived("role", "rol_abc123").unwrap().is_some());
    }

    #[test]
    fn mg16_a_seat_switch_waits_for_the_next_open_and_is_taken_once() {
        let (_d, m) = db();
        assert!(m.seat_next("s1").unwrap().is_none());
        m.seat_next_set("s1", Some("aaaaa1"), None, "mb_1", 10)
            .unwrap();
        let n = m.seat_next("s1").unwrap().unwrap();
        assert_eq!(n.persona_next.as_deref(), Some("aaaaa1"));
        assert_eq!(n.brain_next, None);
        // a later batch adds the brain and keeps the persona it did not name
        m.seat_next_set("s1", None, Some("folder.group.x.1"), "mb_2", 20)
            .unwrap();
        let n = m.seat_next("s1").unwrap().unwrap();
        assert_eq!(n.persona_next.as_deref(), Some("aaaaa1"));
        assert_eq!(n.brain_next.as_deref(), Some("folder.group.x.1"));
        assert_eq!(n.batch_id, "mb_2");
        // opening takes it once
        assert_eq!(m.seat_next_take("s1").unwrap(), Some(n));
        assert!(m.seat_next_take("s1").unwrap().is_none());
        assert!(m.seat_next("s1").unwrap().is_none());
    }

    #[test]
    fn mg16_a_seat_switch_needs_something_checked_and_undo_clears_one_batch() {
        let (_d, m) = db();
        assert!(m.seat_next_set("s1", None, None, "mb_1", 1).is_err());
        assert!(m
            .seat_next_set("s 1", Some("aaaaa1"), None, "mb_1", 1)
            .is_err());
        assert!(m.seat_next_set("s1", Some("a a"), None, "mb_1", 1).is_err());
        assert!(m.seat_next_set("s1", None, Some(""), "mb_1", 1).is_err());
        assert!(m
            .seat_next_set("s1", None, Some(&"r".repeat(201)), "mb_1", 1)
            .is_err());
        m.seat_next_set("s1", Some("aaaaa1"), None, "mb_1", 1)
            .unwrap();
        m.seat_next_set("s2", Some("aaaaa1"), None, "mb_2", 1)
            .unwrap();
        assert_eq!(m.seat_next_clear_batch("mb_1").unwrap(), 1);
        // done removes only the row that was read
        m.seat_next_set("s3", Some("aaaaa1"), None, "mb_3", 5)
            .unwrap();
        m.seat_next_set("s3", None, Some("r.1"), "mb_4", 6).unwrap();
        assert!(!m.seat_next_done("s3", "mb_3", 5).unwrap());
        assert!(m.seat_next("s3").unwrap().is_some());
        assert!(m.seat_next_done("s3", "mb_4", 6).unwrap());
        assert!(m.seat_next("s3").unwrap().is_none());
        assert!(m.seat_next("s1").unwrap().is_none());
        assert!(m.seat_next("s2").unwrap().is_some());
    }
}
