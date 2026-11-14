//! Item, order, rev and audit files of one queue root, and the locked transition.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use aib_dmq::evidence::{classify, is_item_id, Probe, MEASURED_SOURCES};
use aib_dmq::lock::{Level, TRANSITION_LOCK_TIMEOUT_MS};
use aib_dmq::norm::upgrade;
use aib_dmq::rev::{item_rev, rev_check, IfRev, Presence};
use aib_dmq::spec::{Reason, Status, ITEM_SCHEMA, LEGACY_ITEM_SCHEMAS, ORDER_SCHEMA};
use aib_dmq::transition::{decide, Decision, Outcome};
use serde_json::{json, Map, Value};

use crate::fsx::{replace, Replace};
use crate::locks::{lock, LockError, DIR_LOCK_TIMEOUT};

pub const AUDIT_FILE: &str = "audit.jsonl";
pub const ORDER_FILE: &str = "order.json";
pub const VIEW_FILE: &str = "view.json";
pub const SOURCE_REV_FILE: &str = "rev";

/// `[0-9a-z]{4,8}` (SEAT_RE): also keeps a seat name from leaving the queue folder.
pub fn seat_ok(seat: &str) -> bool {
    (4..=8).contains(&seat.len())
        && seat
            .bytes()
            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StoreError {
    BadSeat(String),
    BadId(String),
    Lock(LockError),
    /// Nothing was changed.
    Write {
        path: PathBuf,
        why: String,
    },
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::BadSeat(s) => write!(f, "bad seat {s:?}"),
            StoreError::BadId(s) => write!(f, "bad dm id {s:?}"),
            StoreError::Lock(e) => write!(f, "{e}"),
            StoreError::Write { path, why } => write!(f, "write {}: {why}", path.display()),
        }
    }
}

impl From<LockError> for StoreError {
    fn from(e: LockError) -> Self {
        StoreError::Lock(e)
    }
}

/// Result of a write that went through.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Written {
    Durable,
    /// In place, but the folder sync failed (see [`crate::fsx::Replace`]).
    NotSynced(String),
}

/// One item as read (_load_item_raw).
#[derive(Clone, Debug)]
pub enum ItemRead {
    Missing,
    /// The file is there but is not a readable item: not JSON / unknown schema / other id.
    Bad(&'static str),
    Item {
        item: Map<String, Value>,
        legacy: bool,
    },
}

impl ItemRead {
    pub fn presence(&self) -> Presence {
        match self {
            ItemRead::Item { item, .. }
                if item.get("status").and_then(Value::as_str) == Some("deleted") =>
            {
                Presence::Tombstone
            }
            ItemRead::Item { item, .. } => Presence::Live {
                rev: item_rev(&Value::Object(item.clone())),
            },
            _ => Presence::Missing,
        }
    }
}

/// A queue root: `<state>/state/dm-queue`.
#[derive(Clone, Debug)]
pub struct Store {
    pub queue: PathBuf,
    /// The repository a commit given as evidence must be in.
    pub repo: PathBuf,
}

/// What the caller measured about itself, for the audit line.
#[derive(Clone, Debug, Default)]
pub struct Caller {
    pub tmux_pane: Option<String>,
    pub instance: Option<String>,
}

impl Caller {
    pub fn from_env() -> Caller {
        let v = |k: &str| std::env::var(k).ok().filter(|s| !s.is_empty());
        Caller {
            tmux_pane: v("TMUX_PANE"),
            instance: v("EZ2_AIB_INSTANCE"),
        }
    }
}

/// One transition request as the CLI gives it.
pub struct Move<'a> {
    pub seat: &'a str,
    pub id: &'a str,
    pub to: Status,
    pub actor: Option<&'a str>,
    pub why: Option<&'a str>,
    pub evidence: Option<&'a str>,
    pub if_rev: IfRev,
    /// Explicit `--no-rev-check` (logged by the caller).
    pub no_rev_check: bool,
    pub expect_status: Option<Status>,
    pub caller_is_seat: bool,
    pub now: i64,
}

/// What a move did.
#[derive(Clone, Debug)]
pub struct Moved {
    pub reason: Reason,
    pub message: String,
    /// The item rev after the move (or the current one when refused).
    pub rev: Option<i64>,
    pub outcome: Option<Outcome>,
    pub written: Option<Written>,
    /// The item was written (the move happened) but taking it out of order.json failed.
    /// The move is still reported and audited as done; the caller says so instead of
    /// claiming nothing changed.
    pub order_error: Option<StoreError>,
}

impl Store {
    pub fn new(state_root: &Path, repo: &Path) -> Store {
        Store {
            queue: state_root.join("state").join("dm-queue"),
            repo: repo.to_path_buf(),
        }
    }

    pub fn seat_dir(&self, seat: &str) -> Result<PathBuf, StoreError> {
        if !seat_ok(seat) {
            return Err(StoreError::BadSeat(seat.to_string()));
        }
        Ok(self.queue.join(seat))
    }

    fn ensure_dir(&self, seat: &str) -> Result<PathBuf, StoreError> {
        let d = self.seat_dir(seat)?;
        std::fs::create_dir_all(&d).map_err(|e| StoreError::Write {
            path: d.clone(),
            why: e.to_string(),
        })?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700));
        }
        Ok(d)
    }

    pub fn item_path(&self, seat: &str, id: &str) -> Result<PathBuf, StoreError> {
        if !is_item_id(id) {
            return Err(StoreError::BadId(id.to_string()));
        }
        Ok(self.seat_dir(seat)?.join(format!("{id}.json")))
    }

    pub fn read_item(&self, seat: &str, id: &str) -> Result<ItemRead, StoreError> {
        let path = self.item_path(seat, id)?;
        let Ok(text) = std::fs::read_to_string(&path) else {
            return Ok(ItemRead::Missing);
        };
        let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&text) else {
            return Ok(ItemRead::Bad(
                if serde_json::from_str::<Value>(&text).is_ok() {
                    "schema"
                } else {
                    "json"
                },
            ));
        };
        let schema = m.get("schema").and_then(Value::as_str).unwrap_or("");
        if schema != ITEM_SCHEMA && !LEGACY_ITEM_SCHEMAS.contains(&schema) {
            return Ok(ItemRead::Bad("schema"));
        }
        if m.get("id").and_then(Value::as_str) != Some(id) {
            return Ok(ItemRead::Bad("id"));
        }
        let (item, legacy) = upgrade(&m);
        Ok(ItemRead::Item { item, legacy })
    }

    /// _write_item: `_` keys dropped, a v1 item written back as v2, under the folder lock,
    /// then the seat's source rev goes up.
    pub fn write_item(&self, seat: &str, item: &Map<String, Value>) -> Result<Written, StoreError> {
        let id = item
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let path = self.item_path(seat, &id)?;
        let dir = self.ensure_dir(seat)?;
        let mut out: Map<String, Value> = item
            .iter()
            .filter(|(k, _)| !k.starts_with('_'))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        if out
            .get("schema")
            .and_then(Value::as_str)
            .is_some_and(|s| LEGACY_ITEM_SCHEMAS.contains(&s))
        {
            out.insert("schema".into(), Value::from(ITEM_SCHEMA));
        }
        let w = {
            let _g = lock(&dir, seat, Level::Directory, DIR_LOCK_TIMEOUT)?;
            put(&path, &Value::Object(out))?
        };
        self.bump_source_rev(seat);
        Ok(w)
    }

    /// order.json ids (a missing or foreign file reads as empty).
    pub fn order_ids(&self, seat: &str) -> Result<Vec<String>, StoreError> {
        let path = self.seat_dir(seat)?.join(ORDER_FILE);
        Ok(read_order(&path))
    }

    /// Change order.json inside the folder lock (read, change, write: no window between).
    fn order_update(
        &self,
        seat: &str,
        f: impl FnOnce(&mut Vec<String>),
    ) -> Result<Option<Written>, StoreError> {
        let dir = self.ensure_dir(seat)?;
        let path = dir.join(ORDER_FILE);
        let w = {
            let _g = lock(&dir, seat, Level::Directory, DIR_LOCK_TIMEOUT)?;
            let mut ids = read_order(&path);
            let before = ids.clone();
            f(&mut ids);
            if ids == before {
                None
            } else {
                Some(put(
                    &path,
                    &json!({"schema": ORDER_SCHEMA, "seat": seat, "ids": ids}),
                )?)
            }
        };
        if w.is_some() {
            self.bump_source_rev(seat);
        }
        Ok(w)
    }

    pub fn order_append(&self, seat: &str, id: &str) -> Result<Option<Written>, StoreError> {
        self.order_update(seat, |ids| ids.push(id.to_string()))
    }

    /// _order_remove; when the send queue is empty and nothing of the seat is in a status
    /// the waiting tab shows, the selection file goes too (kept when unsure).
    pub fn order_remove(&self, seat: &str, id: &str) -> Result<Option<Written>, StoreError> {
        let w = self.order_update(seat, |ids| ids.retain(|i| i != id))?;
        if self.order_ids(seat)?.is_empty() && !self.anything_waiting(seat)? {
            let _ = std::fs::remove_file(self.seat_dir(seat)?.join(VIEW_FILE));
        }
        Ok(w)
    }

    fn anything_waiting(&self, seat: &str) -> Result<bool, StoreError> {
        let dir = self.seat_dir(seat)?;
        let Ok(rd) = std::fs::read_dir(&dir) else {
            return Ok(false);
        };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            let Some(id) = name.strip_suffix(".json") else {
                continue;
            };
            if !is_item_id(id) {
                continue;
            }
            match self.read_item(seat, id)? {
                ItemRead::Item { item, .. } => {
                    let st = item
                        .get("status")
                        .and_then(Value::as_str)
                        .and_then(Status::parse);
                    if st.is_some_and(Status::is_waiting) {
                        return Ok(true);
                    }
                }
                ItemRead::Bad(_) => return Ok(true), // unsure: keep the selection
                ItemRead::Missing => {}
            }
        }
        Ok(false)
    }

    /// _bump_source_rev: +1 under rev.lock. Best effort like 1.x: a failure leaves the rev.
    /// 1.x waits on rev.lock without a limit; the hour here only stops a stuck holder from
    /// hanging the command for ever. It is not the short wait fsx::pause speaks of.
    pub fn bump_source_rev(&self, seat: &str) -> Option<u64> {
        let dir = self.seat_dir(seat).ok()?;
        let lock_path = dir.join(aib_dmq::lock::SOURCE_REV_LOCK);
        let f = crate::fsx::create_private(&lock_path, false).ok()?;
        crate::fsx::lock_ex(&f, Duration::from_secs(3600)).ok()?;
        let path = dir.join(SOURCE_REV_FILE);
        let n = self.source_rev(seat) + 1;
        let r = replace(&path, format!("{n}\n").as_bytes()).ok();
        let _ = f.unlock();
        r.map(|_| n)
    }

    pub fn source_rev(&self, seat: &str) -> u64 {
        self.seat_dir(seat)
            .ok()
            .and_then(|d| std::fs::read_to_string(d.join(SOURCE_REV_FILE)).ok())
            .and_then(|t| t.trim().parse().ok())
            .unwrap_or(0)
    }

    /// One audit line (approved and refused moves alike). Never fails the caller.
    pub fn audit(&self, seat: &str, caller: &Caller, fields: Map<String, Value>) {
        let Ok(dir) = self.ensure_dir(seat) else {
            return;
        };
        let mut rec = Map::new();
        rec.insert(
            "caller".into(),
            json!({"tmux_pane": caller.tmux_pane, "instance": caller.instance, "ppid": parent_pid()}),
        );
        rec.extend(fields);
        let Ok(line) = crate::pyjson::dumps(&Value::Object(rec)) else {
            return;
        };
        let path = dir.join(AUDIT_FILE);
        let f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path);
        if let Ok(mut f) = f {
            let _ = f.write_all(format!("{line}\n").as_bytes());
        }
    }

    /// The whole transition (_transition): evidence checked before the lock, then rev check,
    /// read, decide, write, order, audit, all inside the seat's transition lock. The receipt
    /// (if any) is in `outcome` for the caller to send after this returns.
    pub fn transition(&self, mv: &Move<'_>, caller: &Caller) -> Result<Moved, StoreError> {
        let dir = self.ensure_dir(mv.seat)?;
        let ev_ref = if mv.to == Status::Done {
            classify(mv.seat, mv.evidence, &FsProbe { store: self })
        } else {
            (None, None)
        };
        let audit_fields = |reason: Reason, from: Option<Status>, rev: Option<i64>| {
            let mut m = Map::new();
            m.insert("ts".into(), json!(mv.now));
            m.insert("id".into(), json!(mv.id));
            if let Some(f) = from {
                m.insert("from".into(), json!(f.as_str()));
            }
            m.insert("to".into(), json!(mv.to.as_str()));
            m.insert("actor".into(), json!(mv.actor));
            m.insert("ok".into(), json!(reason == Reason::Ok));
            m.insert("reason".into(), json!(reason.as_str()));
            if let Some(r) = rev {
                m.insert("rev".into(), json!(r));
            }
            m
        };
        let audit = |reason: Reason, from: Option<Status>, rev: Option<i64>| {
            self.audit(mv.seat, caller, audit_fields(reason, from, rev));
        };
        let _t = match lock(
            &dir,
            mv.seat,
            Level::Transition,
            Duration::from_millis(TRANSITION_LOCK_TIMEOUT_MS),
        ) {
            Ok(g) => g,
            Err(LockError::Timeout { .. }) => {
                audit(Reason::LockTimeout, None, None);
                return Ok(Moved {
                    reason: Reason::LockTimeout,
                    message: Reason::LockTimeout.message().into(),
                    rev: None,
                    outcome: None,
                    written: None,
                    order_error: None,
                });
            }
            Err(e) => return Err(e.into()),
        };
        let read = self.read_item(mv.seat, mv.id)?;
        let (ok, reason, cur) = rev_check(read.presence(), mv.if_rev, mv.no_rev_check);
        let refused = |reason: Reason, message: String, from: Option<Status>, rev: Option<i64>| {
            audit(reason, from, rev);
            Ok(Moved {
                reason,
                message,
                rev,
                outcome: None,
                written: None,
                order_error: None,
            })
        };
        if !ok {
            return refused(reason, reason.message().into(), None, cur);
        }
        let ItemRead::Item { item, .. } = read else {
            return refused(
                Reason::NotFound,
                Reason::NotFound.message().into(),
                None,
                None,
            );
        };
        if item.get("status").and_then(Value::as_str) == Some("deleted") {
            return refused(
                Reason::NotFound,
                Reason::NotFound.message().into(),
                None,
                None,
            );
        }
        let rev_now = item_rev(&Value::Object(item.clone()));
        let req = aib_dmq::transition::Request {
            seat: mv.seat,
            to: mv.to,
            actor: mv.actor,
            why: mv.why,
            evidence: mv.evidence,
            evidence_ref: ev_ref,
            expect_status: mv.expect_status,
            caller_is_seat: mv.caller_is_seat,
            now: mv.now,
        };
        match decide(&item, &req) {
            Decision::Refuse {
                reason,
                from,
                message,
            } => refused(reason, message, Some(from), Some(rev_now)),
            Decision::Allow(o) => {
                let written = self.write_item(mv.seat, &o.item)?;
                // the item is written: from here on the move happened, whatever fails next
                let mut order_error = None;
                if o.leaves_order {
                    order_error = self.order_remove(mv.seat, mv.id).err();
                }
                let mut m = audit_fields(Reason::Ok, Some(o.from), Some(rev_now));
                m.insert("why".into(), json!(mv.why));
                if mv.to == Status::Done {
                    // dm_queue.py:1591, the board's spot-check reads it
                    m.insert(
                        "evidence".into(),
                        o.item.get("done_evidence").cloned().unwrap_or(Value::Null),
                    );
                }
                self.audit(mv.seat, caller, m);
                Ok(Moved {
                    reason: Reason::Ok,
                    message: String::new(),
                    rev: Some(rev_now),
                    outcome: Some(*o),
                    written: Some(written),
                    order_error,
                })
            }
        }
    }

    /// Seats with a queue folder, sorted (iter_queue_seats).
    pub fn seats(&self) -> Vec<String> {
        let mut out: Vec<String> = std::fs::read_dir(&self.queue)
            .map(|rd| {
                rd.flatten()
                    .filter(|e| e.path().is_dir())
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .filter(|s| seat_ok(s))
                    .collect()
            })
            .unwrap_or_default();
        out.sort();
        out
    }
}

fn put(path: &Path, v: &Value) -> Result<Written, StoreError> {
    let mut s = crate::pyjson::dumps(v).map_err(|e| StoreError::Write {
        path: path.into(),
        why: e.to_string(),
    })?;
    s.push('\n');
    match replace(path, s.as_bytes()) {
        Ok(Replace::Durable) => Ok(Written::Durable),
        Ok(Replace::NotSynced(w)) => Ok(Written::NotSynced(w)),
        Err(e) => Err(StoreError::Write {
            path: path.into(),
            why: e.to_string(),
        }),
    }
}

fn read_order(path: &Path) -> Vec<String> {
    let v: Option<Value> = std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok());
    match v {
        Some(v) if v.get("schema").and_then(Value::as_str) == Some(ORDER_SCHEMA) => v
            .get("ids")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

fn parent_pid() -> Option<u32> {
    #[cfg(unix)]
    {
        Some(std::os::unix::process::parent_id())
    }
    #[cfg(not(unix))]
    {
        None
    }
}

/// Evidence checks against this machine: files, `git cat-file` in the repo, reply ids in
/// every seat's queue sent by `seat` with a measured sender.
pub struct FsProbe<'a> {
    pub store: &'a Store,
}

impl Probe for FsProbe<'_> {
    fn is_file(&self, text: &str) -> bool {
        let p = match text.strip_prefix('~') {
            Some(rest) if rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\') => {
                match std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
                    Some(h) => PathBuf::from(h).join(rest.trim_start_matches(['/', '\\'])),
                    None => return false,
                }
            }
            _ => PathBuf::from(text),
        };
        p.is_file()
    }

    fn commit_exists(&self, sha: &str) -> bool {
        Command::new("git")
            .arg("-C")
            .arg(&self.store.repo)
            .args(["cat-file", "-e", &format!("{sha}^{{commit}}")])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
    }

    fn reply_sent_by(&self, seat: &str, id: &str) -> bool {
        self.store
            .seats()
            .iter()
            .any(|other| match self.store.read_item(other, id) {
                Ok(ItemRead::Item { item, .. }) => {
                    item.get("status").and_then(Value::as_str) != Some("deleted")
                        && item.get("sender_seat").and_then(Value::as_str) == Some(seat)
                        && item
                            .get("sender_source")
                            .and_then(Value::as_str)
                            .is_some_and(|s| MEASURED_SOURCES.contains(&s))
                }
                _ => false,
            })
    }
}
