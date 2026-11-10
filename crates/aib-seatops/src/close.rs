//! `aib-seatops dm close`: close one dm of one's own seat, atomically with the Python queue.
//!
//! The Python queue (`aib/src/cockpit/dm_queue.py`) runs every state change inside a per-seat
//! `transition.lock` (#552 G1). This module takes the same lock, re-reads the item and its
//! relay inside it, compares them with the snapshot taken before the lock (content fingerprint,
//! `--expect`), and only then writes `done`. So a close can never land on a dm whose content
//! changed after it was read, and it can never overwrite (or be overwritten by) a Python
//! transition of the same dm.
//!
//! What it writes is what Python's `_transition(..., "done")` writes for a seat closing its own
//! dm: the item (status, done_at, done_evidence, done_kind, done_why, read_at), the seat's
//! source revision, the order list and the selection file when they need it, and one audit
//! line. Lock order is the one #552 fixed: transition lock, then the folder lock.
//!
//! Not handled here (refused with `use_python`, the Python path still does them): dms that are
//! still queued / claimed / on hold (they leave the send queue first), global and Telegram dms
//! (they send receipts), legacy v1 items, and evidence written inside a sentence (Python
//! extracts it; here the whole `--evidence` must be one file / commit / PR link / own reply id,
//! or use `--why`).
//!
//! Not a security boundary: queue files are writable by this account. It keeps a seat from
//! closing the wrong dm, a changed dm, or a dm another writer is changing.

use crate::{prepare_from_paths, verify_snapshot_from_paths, Snapshot};
use serde_json::{json, Value};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const ITEM_SCHEMA: &str = "aib-dm-queue-item/v2";
pub const LOCK_TIMEOUT: Duration = Duration::from_secs(10);
const TRANSITION_LOCK: &str = "transition.lock";
const ORDER_SCHEMA: &str = "aib-dm-queue-order/v1";
const MEASURED_SOURCES: [&str; 2] = ["pane_pid_chain", "bg_fork_chain"];
/// Python `V2_FIELDS`: (field, default); `kind` defaults to "other", `attempts` to 0, the rest
/// to null.
const V2_DEFAULTS: [(&str, Option<&str>); 26] = [
    ("thread", None), ("kind", Some("other")), ("sender_seat", None), ("human_id", None),
    ("sender_source", None), ("sent_at", None), ("delivered_at", None), ("read_at", None),
    ("done_at", None), ("done_evidence", None), ("archived_at", None), ("archived_why", None),
    ("deleted_at", None), ("deleted_by", None), ("deleted_why", None), ("supersedes", None),
    ("superseded_by", None), ("superseded_at", None), ("expires", None), ("expired", None),
    ("expired_at", None), ("expired_why", None), ("attempts", Some("0")), ("hold_why", None),
    ("hold_rc", None), ("held_at", None),
];
/// Statuses Python's waiting tab can show; the selection file is dropped only when none is left.
const WAITING_STATUSES: [&str; 4] = ["queued", "claimed", "delivered", "hold_human"];

/// Where things are.
#[derive(Debug, Clone)]
pub struct Roots {
    /// EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME / ~/.ez2-aib.
    pub state: PathBuf,
    /// Folders a relay file may live in (each may hold 8-digit date folders).
    pub relay_dirs: Vec<PathBuf>,
    /// The repository a commit given as evidence must be in.
    pub repo: PathBuf,
}

impl Roots {
    pub fn queue(&self) -> PathBuf {
        self.state.join("state").join("dm-queue")
    }
    pub fn seat_dir(&self, seat: &str) -> PathBuf {
        self.queue().join(seat)
    }
}

/// One close request.
#[derive(Debug, Clone)]
pub struct Request {
    pub seat: String,
    pub id: String,
    pub rev: u64,
    pub expect: String,
    pub evidence: Option<String>,
    pub why: Option<String>,
}

/// The dm was closed.
#[derive(Debug, Clone, PartialEq)]
pub struct Closed {
    pub id: String,
    pub rev: u64,
    pub done_kind: String,
    /// Status before the close (delivered / read).
    pub from: String,
}

/// The dm was not closed. `reason` names the cause in the Python vocabulary where one exists.
#[derive(Debug, Clone, PartialEq)]
pub struct Refused {
    pub reason: String,
    pub message: String,
    /// true: the queue could not be read / locked / written (rc 3); false: refused (rc 2).
    pub broken: bool,
}

fn refused(reason: &str, message: impl Into<String>) -> Refused {
    Refused {
        reason: reason.into(),
        message: message.into(),
        broken: false,
    }
}

fn broken(reason: &str, message: impl Into<String>) -> Refused {
    Refused {
        reason: reason.into(),
        message: message.into(),
        broken: true,
    }
}

/// Python SEAT_RE: `^[0-9a-z]{4,8}$`.
pub fn seat_ok(s: &str) -> bool {
    (4..=8).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

/// Python ITEM_RE: `^q_[0-9a-f]{16}$`.
pub fn item_id_ok(s: &str) -> bool {
    s.len() == 18
        && s.starts_with("q_")
        && s.bytes().skip(2).all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn hex_lower(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// Python `_PR_URL_RE` (fullmatch): `https://github.com/ez2app/<repo>/(pull/<1-9 digits>|commit/<7-40 hex>)`
/// with an optional trailing `/`; `<repo>` is `[A-Za-z0-9_.-]+`, not `.`, without `..`.
pub fn pr_url_ok(s: &str) -> bool {
    let Some(rest) = s.strip_prefix("https://github.com/ez2app/") else {
        return false;
    };
    let rest = rest.strip_suffix('/').unwrap_or(rest);
    let mut parts = rest.splitn(3, '/');
    let (Some(repo), Some(what), Some(val)) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    let repo_ok = !repo.is_empty()
        && repo != "."
        && !repo.contains("..")
        && repo
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'));
    repo_ok
        && match what {
            "pull" => (1..=9).contains(&val.len()) && val.bytes().all(|b| b.is_ascii_digit()),
            "commit" => hex_lower(val, 7, 40),
            _ => false,
        }
}

fn expand_home(p: &str) -> PathBuf {
    match (p.strip_prefix("~/"), std::env::var_os("HOME")) {
        (Some(rest), Some(h)) => PathBuf::from(h).join(rest),
        _ if p == "~" => std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default(),
        _ => PathBuf::from(p),
    }
}

/// `git -C repo cat-file -e <sha>^{commit}`, at most 10 seconds (Python uses the same limit).
fn commit_exists(repo: &Path, sha: &str) -> bool {
    let Ok(mut child) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["cat-file", "-e", &format!("{sha}^{{commit}}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return false;
    };
    let until = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait() {
            Ok(Some(st)) => return st.success(),
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// Python `evidence_kind` for one whole token: dm (a reply this seat sent, with a measured
/// sender), pr, commit, file; None when it is none of these.
pub fn evidence_kind(roots: &Roots, seat: &str, ev: &str) -> Option<&'static str> {
    let ev = ev.trim();
    if ev.is_empty() {
        return None;
    }
    if item_id_ok(ev) {
        let entries = std::fs::read_dir(roots.queue()).ok()?;
        let mut seats: Vec<String> = entries
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_dir())
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|s| seat_ok(s))
            .collect();
        seats.sort();
        for other in seats {
            let p = roots.seat_dir(&other).join(format!("{ev}.json"));
            let Ok(text) = std::fs::read_to_string(&p) else {
                continue;
            };
            let Ok(v) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            let s = |k: &str| v.get(k).and_then(Value::as_str);
            if s("id") == Some(ev)
                && s("status") != Some("deleted")
                && s("sender_seat") == Some(seat)
                && s("sender_source").is_some_and(|x| MEASURED_SOURCES.contains(&x))
            {
                return Some("dm");
            }
        }
        return None;
    }
    if pr_url_ok(ev) {
        return Some("pr");
    }
    if hex_lower(ev, 7, 40) && commit_exists(&roots.repo, ev) {
        return Some("commit");
    }
    if expand_home(ev).is_file() {
        return Some("file");
    }
    None
}

fn now_s() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// An exclusive flock on `fd`, retried without blocking until `timeout` (atomic_json `_acquire`).
fn flock_ex(fd: &impl AsRawFd, timeout: Duration) -> Result<(), &'static str> {
    let until = Instant::now() + timeout;
    loop {
        // SAFETY: flock on a descriptor this process owns; no memory is passed.
        let rc = unsafe { libc::flock(fd.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
        if rc == 0 {
            return Ok(());
        }
        let e = std::io::Error::last_os_error().raw_os_error();
        if e != Some(libc::EWOULDBLOCK) && e != Some(libc::EAGAIN) && e != Some(libc::EACCES) {
            return Err("lock failed");
        }
        if Instant::now() >= until {
            return Err("lock_timeout");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// Held flock; released when dropped (closing the descriptor releases it too).
struct Held(File);

impl Drop for Held {
    fn drop(&mut self) {
        // SAFETY: unlocking a descriptor this value owns.
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

fn lock_file(path: &Path, timeout: Duration) -> Result<Held, Refused> {
    let f = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(path)
        .map_err(|e| broken("lock_failed", format!("{}: {e}", path.display())))?;
    match flock_ex(&f, timeout) {
        Ok(()) => Ok(Held(f)),
        Err("lock_timeout") => Err(broken(
            "lock_timeout",
            "another program is changing this seat's dms (waited 10 s); nothing was changed, try again",
        )),
        Err(e) => Err(broken("lock_failed", format!("{}: {e}", path.display()))),
    }
}

fn lock_dir(dir: &Path, timeout: Duration) -> Result<Held, Refused> {
    let f = File::open(dir).map_err(|e| broken("write_failed", format!("{}: {e}", dir.display())))?;
    match flock_ex(&f, timeout) {
        Ok(()) => Ok(Held(f)),
        Err(e) => Err(broken("write_failed", format!("{} folder lock: {e}", dir.display()))),
    }
}

fn rand_hex(n: usize) -> String {
    let mut b = vec![0u8; n];
    if let Ok(mut f) = File::open("/dev/urandom") {
        let _ = std::io::Read::read_exact(&mut f, &mut b);
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// tmp (O_EXCL, 0600) + fsync + rename + fsync of the folder, as atomic_json does. The caller
/// holds the folder lock when other writers may read-modify-write the same file.
fn replace_file(path: &Path, payload: &[u8]) -> Result<(), Refused> {
    let dir = path.parent().unwrap_or(Path::new("."));
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("x");
    let tmp = dir.join(format!(".{name}.tmp.{}.{}", std::process::id(), rand_hex(6)));
    let w = |e: std::io::Error| broken("write_failed", format!("{}: {e}", path.display()));
    let res = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)?;
        f.write_all(payload)?;
        f.sync_all()?;
        std::fs::rename(&tmp, path)?;
        File::open(dir)?.sync_all()
    })();
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    res.map_err(w)
}

/// Python `json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"` up to whitespace:
/// serde_json's map is ordered by key, so the keys come out sorted.
fn item_payload(v: &Value) -> Result<Vec<u8>, Refused> {
    let mut s = serde_json::to_string(v).map_err(|e| broken("write_failed", e.to_string()))?;
    s.push('\n');
    Ok(s.into_bytes())
}

/// Python `_write_item` (not locked variant): the item under the folder lock.
fn write_item(roots: &Roots, seat: &str, item: &Value) -> Result<(), Refused> {
    let dir = roots.seat_dir(seat);
    let id = item.get("id").and_then(Value::as_str).unwrap_or("");
    let payload = item_payload(item)?;
    let _g = lock_dir(&dir, LOCK_TIMEOUT)?;
    replace_file(&dir.join(format!("{id}.json")), &payload)
}

/// Python `_bump_source_rev`: `rev` + 1 under `rev.lock`. Best effort, like Python (None on error).
fn bump_source_rev(roots: &Roots, seat: &str) {
    let dir = roots.seat_dir(seat);
    let Ok(_g) = lock_file(&dir.join("rev.lock"), Duration::from_secs(3600)) else {
        return;
    };
    let path = dir.join("rev");
    let n = std::fs::read_to_string(&path)
        .ok()
        .and_then(|t| t.trim().parse::<u64>().ok())
        .unwrap_or(0)
        + 1;
    let tmp = dir.join(format!(".rev.tmp.{}.{}", std::process::id(), rand_hex(3)));
    if std::fs::write(&tmp, format!("{n}\n")).is_ok() && std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// Python `_order_remove`: drop the id from order.json (read-modify-write under the folder
/// lock, as atomic_json.update_json does), then the selection file when nothing is waiting.
/// "Nothing is waiting" is decided conservatively: no item of the seat is in a status the
/// waiting tab can show. Python asks the full tab view; where the two differ, this keeps the
/// selection (the harmless side).
fn order_remove(roots: &Roots, seat: &str, id: &str) -> Result<(), Refused> {
    let dir = roots.seat_dir(seat);
    let path = dir.join("order.json");
    let left = {
        let _g = lock_dir(&dir, LOCK_TIMEOUT)?;
        let rec: Option<Value> = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok());
        let ids: Vec<String> = rec
            .as_ref()
            .and_then(|r| r.get("ids"))
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .filter(|s| item_id_ok(s))
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let kept: Vec<String> = ids.iter().filter(|i| *i != id).cloned().collect();
        let schema_ok = rec
            .as_ref()
            .and_then(|r| r.get("schema"))
            .and_then(Value::as_str)
            .is_some_and(|s| s == ORDER_SCHEMA);
        if kept.len() != ids.len() && schema_ok {
            let mut new = rec.unwrap_or_else(|| json!({}));
            new["ids"] = json!(kept);
            replace_file(&path, &item_payload(&new)?)?;
            drop(_g);
            bump_source_rev(roots, seat);
        }
        kept.len()
    };
    if left == 0 && !anything_waiting(&dir) {
        let _ = std::fs::remove_file(dir.join("view.json"));
    }
    Ok(())
}

fn anything_waiting(dir: &Path) -> bool {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return true;
    };
    for e in rd.filter_map(|e| e.ok()) {
        let name = e.file_name();
        let Some(name) = name.to_str() else { continue };
        let Some(stem) = name.strip_suffix(".json") else {
            continue;
        };
        if !item_id_ok(stem) {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(e.path()) else {
            return true; // unreadable: assume it may be waiting
        };
        let status = serde_json::from_str::<Value>(&text)
            .ok()
            .and_then(|v| v.get("status").and_then(Value::as_str).map(str::to_owned));
        match status {
            Some(s) if WAITING_STATUSES.contains(&s.as_str()) => return true,
            None => return true,
            _ => {}
        }
    }
    false
}

/// Python `_audit`: one JSON line in audit.jsonl (written for refusals inside the lock and for
/// the close). Best effort, like Python.
fn audit(roots: &Roots, seat: &str, fields: Value) {
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    let mut rec = json!({
        "ts": now_s(),
        "caller": {
            "tmux_pane": env("TMUX_PANE"),
            "instance": env("EZ2_AIB_INSTANCE"),
            "ppid": std::os::unix::process::parent_id(),
        },
        "via": "aib-seatops",
    });
    if let (Some(r), Some(f)) = (rec.as_object_mut(), fields.as_object()) {
        for (k, v) in f {
            r.insert(k.clone(), v.clone());
        }
    }
    let path = roots.seat_dir(seat).join("audit.jsonl");
    if let Ok(mut f) = OpenOptions::new().append(true).create(true).mode(0o600).open(path) {
        let _ = f.write_all(format!("{rec}\n").as_bytes());
    }
}

/// Close one dm. `hold` (tests only) is how long to stay inside the lock between reading the
/// item and writing it.
pub fn close(roots: &Roots, req: &Request) -> Result<Closed, Refused> {
    close_with(roots, req, Duration::ZERO, &|| {})
}

/// [`close`] with two test hooks: `between` runs after the snapshot and before the lock;
/// `hold` is spent inside the lock after reading the item.
pub fn close_with(
    roots: &Roots,
    req: &Request,
    hold: Duration,
    between: &dyn Fn(),
) -> Result<Closed, Refused> {
    if !seat_ok(&req.seat) {
        return Err(refused("usage", format!("bad --seat {:?}", req.seat)));
    }
    if !item_id_ok(&req.id) {
        return Err(refused("usage", format!("bad --id {:?} (q_<16 hex>)", req.id)));
    }
    let why = req.why.as_deref().map(str::trim).filter(|w| !w.is_empty());
    let ev = req.evidence.as_deref().map(str::trim).filter(|e| !e.is_empty());
    let item_path = roots.seat_dir(&req.seat).join(format!("{}.json", req.id));
    // 1. snapshot outside the lock: the relay is read, --expect is in the content
    let snap: Snapshot = prepare_from_paths(&item_path, &roots.relay_dirs, &req.id, req.rev, &req.expect)
        .map_err(|e| match e {
            "file unreadable" => refused("not_found", format!("{}: no such dm", req.id)),
            "revision mismatch" => refused("rev_mismatch", "the dm changed since you read it (rev)"),
            other => refused("snapshot", other),
        })?;
    // 2. evidence is checked before the lock (it may run git), as #552 r1 L2 does
    let ev_kind = ev.and_then(|e| evidence_kind(roots, &req.seat, e));
    let done_kind = match (ev_kind, why) {
        (Some(k), _) => k.to_owned(),
        (None, Some(_)) => "reason".to_owned(),
        (None, None) => {
            return Err(refused(
                "evidence",
                "closing needs evidence (an existing file, a commit of the repository, a full PR link, a reply id you sent) or --why; evidence inside a sentence is not read here",
            ))
        }
    };
    between();
    // 3. the seat's transition lock: everything from here to the write is atomic with Python
    let _t = lock_file(&roots.seat_dir(&req.seat).join(TRANSITION_LOCK), LOCK_TIMEOUT).inspect_err(|r| {
        audit(roots, &req.seat, json!({"id": req.id, "to": "done", "actor": format!("seat:{}", req.seat), "ok": false, "reason": r.reason}));
    })?;
    let refuse_in = |rs: &str, msg: String, from: Option<&str>| {
        audit(roots, &req.seat, json!({"id": req.id, "from": from, "to": "done", "actor": format!("seat:{}", req.seat), "ok": false, "reason": rs, "rev": req.rev}));
        refused(rs, msg)
    };
    verify_snapshot_from_paths(&snap, &item_path, &roots.relay_dirs, &req.expect)
        .map_err(|e| refuse_in("changed", format!("the dm is not what you read: {e}"), None))?;
    let text = std::fs::read_to_string(&item_path)
        .map_err(|e| broken("not_found", format!("{}: {e}", item_path.display())))?;
    let mut item: Value =
        serde_json::from_str(&text).map_err(|_| broken("not_found", "the dm file is not JSON"))?;
    let s = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).map(str::to_owned);
    let from = s(&item, "status").unwrap_or_default();
    if s(&item, "schema").as_deref() != Some(ITEM_SCHEMA) {
        return Err(refuse_in("use_python", "an old (v1) dm: close it with aib dm-queue read --done".into(), Some(&from)));
    }
    if item.get("global").is_some_and(|g| !g.is_null() && g != &json!(false))
        || item.get("im").is_some_and(|g| !g.is_null() && g != &json!(false))
        || s(&item, "sender_kind").as_deref() == Some("external_im")
    {
        return Err(refuse_in("use_python", "a global / Telegram dm (it sends a receipt): close it with aib dm-queue read --done".into(), Some(&from)));
    }
    match from.as_str() {
        "delivered" | "read" => {}
        "deleted" => return Err(refuse_in("not_found", "this dm is no longer there".into(), Some(&from))),
        "queued" | "claimed" | "hold_human" => {
            return Err(refuse_in("use_python", format!("the dm is {from}: aib dm-queue read --done takes it off the send queue first"), Some(&from)))
        }
        other => return Err(refuse_in("transition", format!("a dm that is {other} cannot be closed"), Some(&from))),
    }
    std::thread::sleep(hold);
    // Python `_upgrade_item` fills the v2 fields an item lacks before it writes the item back
    if let Some(o) = item.as_object_mut() {
        for (k, d) in V2_DEFAULTS {
            o.entry(k.to_string()).or_insert_with(|| match (k, d) {
                ("attempts", _) => json!(0),
                (_, Some(s)) => json!(s),
                (_, None) => Value::Null,
            });
        }
        // ... and takes sent_at from ts when it has none
        if o.get("sent_at").is_none_or(Value::is_null) {
            let ts = o.get("ts").and_then(Value::as_u64).filter(|t| *t != 0);
            o.insert("sent_at".into(), ts.map_or(Value::Null, |t| json!(t)));
        }
    }
    // 4. the same fields Python's done branch writes
    let now = now_s();
    item["status"] = json!("done");
    item["done_at"] = json!(now);
    item["done_kind"] = json!(done_kind);
    if done_kind == "reason" {
        item["done_evidence"] = Value::Null;
        item["done_why"] = json!(why.unwrap_or("").chars().take(500).collect::<String>());
    } else {
        item["done_evidence"] = json!(ev.unwrap_or(""));
    }
    if item.get("read_at").is_none_or(Value::is_null) {
        item["read_at"] = json!(now);
    }
    if let Some(o) = item.as_object_mut() {
        o.retain(|k, _| !k.starts_with('_'));
    }
    write_item(roots, &req.seat, &item)?;
    bump_source_rev(roots, &req.seat);
    order_remove(roots, &req.seat, &req.id)?;
    let rev = item.get("rev").and_then(Value::as_u64).unwrap_or(0);
    let mut row = json!({"id": req.id, "from": from, "to": "done", "actor": format!("seat:{}", req.seat), "ok": true, "reason": "ok", "rev": rev, "why": why});
    if done_kind != "reason" {
        row["evidence"] = json!(ev);
    }
    audit(roots, &req.seat, row);
    Ok(Closed {
        id: req.id.clone(),
        rev,
        done_kind,
        from,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pr_links_match_the_python_pattern() {
        for ok in [
            "https://github.com/ez2app/ez2-aib/pull/627",
            "https://github.com/ez2app/ez2-aib/pull/627/",
            "https://github.com/ez2app/x.y_z-1/commit/deadbee",
        ] {
            assert!(pr_url_ok(ok), "{ok}");
        }
        for bad in [
            "https://github.com/ez2app/ez2-aib/pull/",
            "https://github.com/ez2app/ez2-aib/pull/1234567890",
            "https://github.com/ez2app/./pull/1",
            "https://github.com/ez2app/a..b/pull/1",
            "https://github.com/ez2app//pull/1",
            "https://github.com/other/ez2-aib/pull/1",
            "https://github.com/ez2app/ez2-aib/commit/DEADBEE",
            "https://github.com/ez2app/ez2-aib/issues/1",
            "https://github.com/ez2app/ez2-aib/pull/1#x",
        ] {
            assert!(!pr_url_ok(bad), "{bad}");
        }
    }

    #[test]
    fn ids_and_seats() {
        assert!(item_id_ok("q_0123456789abcdef"));
        assert!(!item_id_ok("q_0123456789ABCDEF"));
        assert!(!item_id_ok("q_0123456789abcde"));
        assert!(seat_ok("1ced7") && seat_ok("a63ab") && !seat_ok("A63AB") && !seat_ok("abc"));
    }
}
