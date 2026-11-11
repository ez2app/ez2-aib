// SPDX-License-Identifier: Apache-2.0
//! The host half of what every writing command shares (4.4.1 §1.1 / §1.3 / §1.4; B1):
//! the plan hash, the operation journal on disk, the host lock file and the check that no
//! other operation on the same target is unfinished. Every judgement is
//! `aib_cloud::op`'s; this module only reads and writes the files and gathers the facts
//! (`/etc/machine-id`, the boot id, `/proc/<pid>/stat`).
//!
//! * **plan_sha256** covers the plan as printed (keys sorted), the SHA-256 of the host's
//!   `/etc/machine-id` and this crate's version. A plan approved for one machine does not
//!   count on a reinstalled one with the same site code. No machine id ⇒ no plan.
//! * **Journal**: one file per operation under [`OPS_DIR`], one JSON line per state. A line
//!   is appended only if the whole history with it still replays ([`op::replay`]); it is
//!   written in one piece and synced before [`append_entry`] returns, so a caller that
//!   gets `Ok` for `running` may change the host. A last line cut short (no newline) was
//!   never acted on: it is cut off before the next line goes in.
//! * **Lock**: [`LOCK_FILE`] holds `{op, pid, start_time, boot_id}`. Deciding and writing it
//!   happen under an exclusive `flock` on a separate guard file, so two processes never
//!   both decide "take over". The guard is not the truth about who holds the lock: the
//!   file's content and [`op::lock`] are, so a dead holder is still known by name and its
//!   operation can be resumed (`VerifyFirst`).
//! * **Unfinished**: before a new operation on a target, the last entry of every journal
//!   is read; a damaged journal refuses everything rather than being skipped.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use aib_cloud::op::{self, Entry, Holder, Liveness, Lock};
use serde_json::{json, Map, Value};

/// Where the journals are.
pub const OPS_DIR: &str = "/var/lib/aib-cloud/ops";
/// The host lock. Under /var/lib, not /run: a lock left from before a reboot must still be
/// there to be recognised as such.
pub const LOCK_FILE: &str = "/var/lib/aib-cloud/host.lock";
pub const MACHINE_ID: &str = "/etc/machine-id";
pub const BOOT_ID: &str = "/proc/sys/kernel/random/boot_id";

// ------------------------------------------------------------------ operation ids

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian (Howard Hinnant's
/// `civil_from_days`).
fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// An operation id for `now` (seconds since the epoch, UTC) and four random bytes. The
/// host makes ids; a client only ever brings one back to resume or ask about it.
pub fn op_id(now: u64, random: [u8; 4]) -> String {
    let secs = i64::try_from(now).unwrap_or(0);
    let (y, m, d) = civil(secs.div_euclid(86_400));
    let t = secs.rem_euclid(86_400);
    format!(
        "{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z-{:02x}{:02x}{:02x}{:02x}",
        t / 3600,
        t % 3600 / 60,
        t % 60,
        random[0],
        random[1],
        random[2],
        random[3]
    )
}

pub fn random4() -> Result<[u8; 4], String> {
    let mut b = [0u8; 4];
    File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("/dev/urandom: {e}"))?;
    Ok(b)
}

// ------------------------------------------------------------------ plan hash

/// SHA-256 of `data` by the system `sha256sum` (no new dependency; `self-update` already
/// relies on it). The data goes in on stdin.
pub fn sha256_hex(data: &[u8]) -> Result<String, String> {
    let mut child = Command::new("sha256sum")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("sha256sum: {e}"))?;
    {
        let mut stdin = child.stdin.take().ok_or("sha256sum: no stdin")?;
        stdin
            .write_all(data)
            .map_err(|e| format!("sha256sum: {e}"))?;
    }
    let out = child
        .wait_with_output()
        .map_err(|e| format!("sha256sum: {e}"))?;
    if !out.status.success() {
        return Err(format!("sha256sum: exit {}", out.status));
    }
    let text = String::from_utf8_lossy(&out.stdout);
    crate::update::parse_sha256sum_output(&text)
        .ok_or_else(|| format!("sha256sum: unreadable output {text:?}"))
}

/// The machine id as read, if it is one: 32 lower hex after trimming.
pub fn machine_id(text: &str) -> Option<&str> {
    let id = text.trim();
    (id.len() == 32
        && id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)))
    .then_some(id)
}

/// SHA-256 of this host's machine id. Unreadable or malformed ⇒ an error, never a stand-in
/// value: a hash over "unknown" would match every host that cannot be read.
pub fn host_fingerprint(path: &Path) -> Result<String, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let id = machine_id(&text).ok_or_else(|| format!("{}: not a machine id", path.display()))?;
    sha256_hex(id.as_bytes())
}

/// The value with every object's keys in order, so that the same plan always prints the
/// same bytes whatever order it was built in.
pub fn canonical(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            let mut out = Map::new();
            for k in keys {
                out.insert(k.clone(), canonical(&m[k]));
            }
            Value::Object(out)
        }
        Value::Array(a) => Value::Array(a.iter().map(canonical).collect()),
        other => other.clone(),
    }
}

/// The fields [`seal`] adds to a plan; they are not part of what is hashed.
pub const SEAL_FIELDS: [&str; 3] = ["host_fingerprint", "crate_version", "plan_sha256"];

/// Exactly the bytes that are hashed: the plan, the host and the version, each on its own
/// line under a format tag.
pub fn plan_hash_input(plan: &Value, fingerprint: &str, version: &str) -> String {
    format!(
        "aib-cloud-plan-v1\n{}\nhost {fingerprint}\nversion {version}\n",
        canonical(plan)
    )
}

/// Add `host_fingerprint`, `crate_version` and `plan_sha256` to a plan object. The hash is
/// over the plan as it was before these three fields; [`unsealed`] gives that back.
pub fn seal(plan: &mut Value, fingerprint: &str, version: &str) -> Result<(), String> {
    let obj = plan.as_object().ok_or("a plan is a JSON object")?;
    if SEAL_FIELDS.iter().any(|k| obj.contains_key(*k)) {
        return Err("plan is already sealed".to_owned());
    }
    let sha = sha256_hex(plan_hash_input(plan, fingerprint, version).as_bytes())?;
    let obj = plan.as_object_mut().ok_or("a plan is a JSON object")?;
    obj.insert("host_fingerprint".to_owned(), json!(fingerprint));
    obj.insert("crate_version".to_owned(), json!(version));
    obj.insert("plan_sha256".to_owned(), json!(sha));
    Ok(())
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// The plan without the fields [`seal`] added: what an apply recomputes the hash over.
pub fn unsealed(plan: &Value) -> Value {
    let mut p = plan.clone();
    if let Some(obj) = p.as_object_mut() {
        for k in SEAL_FIELDS {
            obj.remove(k);
        }
    }
    p
}

// ------------------------------------------------------------------ journal

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// What a journal line is written to: the file, or a recorder in tests.
pub trait Sink {
    fn append(&mut self, bytes: &[u8]) -> std::io::Result<()>;
    fn sync(&mut self) -> std::io::Result<()>;
}

impl Sink for File {
    fn append(&mut self, bytes: &[u8]) -> std::io::Result<()> {
        self.write_all(bytes)
    }
    fn sync(&mut self) -> std::io::Result<()> {
        self.sync_all()
    }
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// One whole line, then a sync. Returns only after both: the caller may act on `Ok`.
pub fn write_line<S: Sink>(sink: &mut S, entry: &Entry) -> Result<(), String> {
    let mut line = entry.to_json().to_string();
    line.push('\n');
    sink.append(line.as_bytes())
        .map_err(|e| format!("journal write: {e}"))?;
    sink.sync().map_err(|e| format!("journal sync: {e}"))
}

pub fn journal_path(dir: &Path, op: &str) -> Result<PathBuf, String> {
    if !op::valid_op_id(op) {
        return Err(format!("operation id {op:?} is not valid"));
    }
    Ok(dir.join(format!("{op}.jsonl")))
}

/// Read one journal: `(entries, cut short)`. No file is an empty journal.
pub fn read_journal(path: &Path) -> Result<(Vec<Entry>, bool), String> {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok((Vec::new(), false)),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    op::parse_journal(&text).map_err(|e| format!("{}: {e}", path.display()))
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// Whether `next` may follow `existing`: the whole history with it must replay.
pub fn may_append(existing: &[Entry], next: &Entry) -> Result<(), String> {
    let mut all = existing.to_vec();
    all.push(next.clone());
    op::replay(&all).map(|_| ()).map_err(|e| e.to_string())
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
fn sync_dir(dir: &Path) -> Result<(), String> {
    File::open(dir)
        .and_then(|d| d.sync_all())
        .map_err(|e| format!("{}: {e}", dir.display()))
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// Append `entry` to its operation's journal under `dir`, after checking it follows what
/// is on disk. A cut-short last line is removed first (it was never acted on, and the new
/// line must not be glued to it).
pub fn append_entry(dir: &Path, entry: &Entry) -> Result<(), String> {
    entry.validate().map_err(|e| e.to_string())?;
    let path = journal_path(dir, &entry.op)?;
    let (entries, cut) = read_journal(&path)?;
    may_append(&entries, entry)?;
    let new = !path.exists();
    if cut {
        let text = fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let keep = text.iter().rposition(|&b| b == b'\n').map_or(0, |i| i + 1);
        let f = OpenOptions::new()
            .write(true)
            .open(&path)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        f.set_len(keep as u64)
            .and_then(|()| f.sync_all())
            .map_err(|e| format!("{}: {e}", path.display()))?;
    }
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(&path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    write_line(&mut f, entry)?;
    if new {
        // the file's name must survive a crash too
        sync_dir(dir)?;
    }
    Ok(())
}

/// The last entry of every journal under `dir`. A journal that does not replay refuses
/// the whole answer: skipping it could hide an unfinished operation. A journal whose only
/// line was cut short never began and is left out.
pub fn last_entries(dir: &Path) -> Result<Vec<Entry>, String> {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    };
    let mut out = Vec::new();
    for item in rd {
        let item = item.map_err(|e| format!("{}: {e}", dir.display()))?;
        let path = item.path();
        if path.extension().and_then(|x| x.to_str()) != Some("jsonl") {
            continue;
        }
        let (entries, _) = read_journal(&path)?;
        if entries.is_empty() {
            continue;
        }
        let last = op::replay(&entries).map_err(|e| format!("{}: {e}", path.display()))?;
        out.push(last.clone());
    }
    Ok(out)
}

/// Refuse a new operation on `target` while another one on it is unfinished. `own_op` is
/// the operation asking: its own recovery is not blocked by itself.
pub fn refuse_unfinished(dir: &Path, target: &str, own_op: &str) -> Result<(), String> {
    let lasts = last_entries(dir)?;
    let blocking: Vec<&Entry> = lasts
        .iter()
        .filter(|e| e.target == target && e.op != own_op && is_unfinished(e))
        .collect();
    if blocking.is_empty() {
        Ok(())
    } else {
        let lines: Vec<String> = blocking.iter().map(|e| what_to_run(e)).collect();
        Err(format!(
            "{target} is held by an unfinished operation: {}",
            lines.join("; ")
        ))
    }
}

/// Whether an operation's last state keeps its target from a new operation.
fn is_unfinished(last: &Entry) -> bool {
    let refs = [last];
    !op::unfinished(&last.target, &refs).is_empty()
}

/// Which operation holds a target, what it was, and the command a person runs once they
/// have checked the host (f1fae 11-10: `op status` names what blocks a target and how).
pub fn what_to_run(last: &Entry) -> String {
    format!(
        "operation {} ({} {}, {}): check the host, then `aib-cloud-host op recovered {}`",
        last.op,
        last.command,
        last.target,
        last.state.as_str(),
        last.op
    )
}

/// Every unfinished operation by its target, for `op status`.
pub fn unfinished_report(lasts: &[Entry]) -> Value {
    let mut by_target = Map::new();
    for e in lasts.iter().filter(|e| is_unfinished(e)) {
        let list = by_target
            .entry(e.target.clone())
            .or_insert_with(|| Value::Array(Vec::new()));
        if let Value::Array(items) = list {
            items.push(json!({
                "op": e.op,
                "command": e.command,
                "state": e.state.as_str(),
                "what_to_run": what_to_run(e),
            }));
        }
    }
    Value::Object(by_target)
}

// ------------------------------------------------------------------ host lock

/// Field 22 of `/proc/<pid>/stat`, the start time. The command name (field 2) is in
/// parentheses and may itself hold spaces and parentheses, so fields are counted from the
/// **last** `)`: the first word after it is field 3.
pub fn stat_start_time(stat: &str) -> Option<u64> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_whitespace().nth(19)?.parse().ok()
}

/// Field 3 of `/proc/<pid>/stat`, the state letter (`Z` = zombie), counted from the last
/// `)` like [`stat_start_time`].
pub fn stat_state(stat: &str) -> Option<char> {
    stat.rsplit_once(')')?
        .1
        .split_whitespace()
        .next()?
        .chars()
        .next()
}

/// Whether the process a lock names is still the one that took it. No such pid ⇒ gone; a
/// pid now started at another time ⇒ gone (reused); the same process as a zombie ⇒ gone
/// (it has exited and holds nothing, only its parent has not reaped it); anything that
/// cannot be read ⇒ unknown, which keeps the lock.
pub fn liveness(proc_root: &Path, holder: &Holder) -> Liveness {
    let path = proc_root.join(holder.pid.to_string()).join("stat");
    match fs::read_to_string(path) {
        Ok(t) => match stat_start_time(&t) {
            Some(st) if st == holder.start_time && stat_state(&t) == Some('Z') => Liveness::Gone,
            Some(st) if st == holder.start_time => Liveness::Alive,
            Some(_) => Liveness::Gone,
            None => Liveness::Unknown,
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Liveness::Gone,
        Err(_) => Liveness::Unknown,
    }
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
pub fn holder_json(h: &Holder) -> Value {
    json!({ "op": h.op, "pid": h.pid, "start_time": h.start_time, "boot_id": h.boot_id })
}

/// A lock file's holder, or `None` when the text is not one. [`acquire`] does not read
/// `None` as "free": a lock file that exists but does not parse still means "someone", and
/// is judged as a holder about which nothing can be learnt (busy).
pub fn holder_from_text(text: &str) -> Option<Holder> {
    let v: Value = serde_json::from_str(text).ok()?;
    let o = v.as_object()?;
    let pid = u32::try_from(o.get("pid")?.as_u64()?).ok()?;
    Some(Holder {
        op: o.get("op")?.as_str()?.to_owned(),
        pid,
        start_time: o.get("start_time")?.as_u64()?,
        boot_id: o.get("boot_id")?.as_str()?.to_owned(),
    })
}

#[cfg_attr(not(test), allow(dead_code))] // `lock` / `boot_id` are read by acquire / release (B1 apply)
/// Where the lock looks for its facts. The real host uses [`LockPaths::host`]; tests point
/// it at a scratch directory.
pub struct LockPaths {
    pub lock: PathBuf,
    pub proc_root: PathBuf,
    pub boot_id: PathBuf,
}

impl LockPaths {
    /// The real files. Linux only: anywhere else `/proc/<pid>` is absent for every pid and
    /// every holder would look gone.
    pub fn host() -> Result<Self, String> {
        if !cfg!(target_os = "linux") {
            return Err("the host lock reads /proc and runs on Linux only".to_owned());
        }
        Ok(LockPaths {
            lock: PathBuf::from(LOCK_FILE),
            proc_root: PathBuf::from("/proc"),
            boot_id: PathBuf::from(BOOT_ID),
        })
    }

    #[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
    fn guard(&self) -> PathBuf {
        let mut g = self.lock.clone().into_os_string();
        g.push(".guard");
        PathBuf::from(g)
    }
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// How the lock was got.
#[derive(Debug, PartialEq, Eq)]
pub enum Got {
    Taken,
    /// From a holder that is gone or from before a reboot: its operation was interrupted
    /// and must be resumed by the journal's rules before anything else on its target.
    TookOver(Holder),
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
fn read_boot_id(path: &Path) -> Result<String, String> {
    let id = fs::read_to_string(path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .trim()
        .to_owned();
    if op::valid_boot_id(&id) {
        Ok(id)
    } else {
        Err(format!("{}: not a boot id", path.display()))
    }
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// Run `f` with the guard file's exclusive lock held. Journal lines of a running
/// operation and `op stop --apply` go through it too, so a stop and the operation's own
/// last line never interleave (E11).
pub fn guarded<T>(p: &LockPaths, f: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    let g = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .mode(0o600)
        .open(p.guard())
        .map_err(|e| format!("{}: {e}", p.guard().display()))?;
    g.lock()
        .map_err(|e| format!("{}: {e}", p.guard().display()))?;
    let r = f();
    drop(g); // closing the file releases the flock
    r
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
fn write_new(path: &Path, text: &str) -> Result<(), String> {
    let mut f = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// Put a holder in the lock file in one step: the text goes to a file of its own and is
/// synced, then renamed over the lock, then the directory is synced. A crash at any point
/// leaves either no lock file, the old one, or the whole new one — never an empty or half
/// written lock file, which would read as "cannot be proven free" and keep every
/// operation out until a person removes it (#635 seg. 1 L1, 1ced7). `write` makes the
/// file it is given; tests hand it one that fails half way.
fn put_lock(
    lock: &Path,
    pid: u32,
    text: &str,
    write: impl FnOnce(&Path, &str) -> Result<(), String>,
) -> Result<(), String> {
    let mut tmp = lock.to_path_buf().into_os_string();
    tmp.push(format!(".{pid}.new"));
    let tmp = PathBuf::from(tmp);
    let _ = fs::remove_file(&tmp);
    if let Err(e) = write(&tmp, text) {
        let _ = fs::remove_file(&tmp);
        return Err(e);
    }
    fs::rename(&tmp, lock).map_err(|e| format!("{}: {e}", lock.display()))?;
    sync_dir(lock.parent().unwrap_or(Path::new(".")))
}

/// Remove what a writer killed between writing its holder aside and renaming it left
/// behind (`<lock>.<pid>.new`). Only called with the guard held, and only the guard holder
/// ever writes one, so every such file found here belongs to a writer that is gone. They
/// never decide who holds the lock; this only keeps them from piling up (#635 L1 N1, 1ced7).
fn sweep_leftovers(lock: &Path) {
    let (Some(dir), Some(name)) = (lock.parent(), lock.file_name()) else {
        return;
    };
    let prefix = format!("{}.", name.to_string_lossy());
    let Ok(entries) = fs::read_dir(if dir.as_os_str().is_empty() {
        Path::new(".")
    } else {
        dir
    }) else {
        return;
    };
    for e in entries.flatten() {
        let n = e.file_name().to_string_lossy().into_owned();
        let leftover = n
            .strip_prefix(&prefix)
            .and_then(|r| r.strip_suffix(".new"))
            .is_some_and(|pid| !pid.is_empty() && pid.bytes().all(|b| b.is_ascii_digit()));
        if leftover {
            let _ = fs::remove_file(e.path());
        }
    }
}

/// What `op status` says, and `acquire` points at, when the lock file cannot be read as a
/// holder: nothing can start until a person has looked.
pub const DAMAGED_LOCK_HELP: &str = "the host lock file cannot be read as a holder, so no operation can start. \
Make sure no aib-cloud-host operation is running (`pgrep -a aib-cloud-host`), run `op unfinished <target>` \
for the targets you are about to touch, then move the lock file aside (keep it): \
mv /var/lib/aib-cloud/host.lock /var/lib/aib-cloud/host.lock.damaged";

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// Take the host lock for operation `op`, as process `pid`. `Busy` is an error: the caller
/// waits or gives up and writes nothing.
pub fn acquire(p: &LockPaths, op: &str, pid: u32) -> Result<Got, String> {
    if !op::valid_op_id(op) {
        return Err(format!("operation id {op:?} is not valid"));
    }
    let boot_id = read_boot_id(&p.boot_id)?;
    let stat_path = p.proc_root.join(pid.to_string()).join("stat");
    let start_time = fs::read_to_string(&stat_path)
        .ok()
        .and_then(|t| stat_start_time(&t))
        .ok_or_else(|| format!("{}: no start time", stat_path.display()))?;
    let me = Holder {
        op: op.to_owned(),
        pid,
        start_time,
        boot_id: boot_id.clone(),
    };
    let text = format!("{}\n", holder_json(&me));
    guarded(p, || {
        sweep_leftovers(&p.lock);
        let current = match fs::read_to_string(&p.lock) {
            Ok(t) => Some(holder_from_text(&t).unwrap_or(Holder {
                op: String::new(),
                pid: 0,
                start_time: 0,
                boot_id: String::new(),
            })),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(format!("{}: {e}", p.lock.display())),
        };
        let live = match &current {
            // pid 0 is a damaged file, not a process: nothing can be learnt about it
            Some(h) if h.pid == 0 => Liveness::Unknown,
            Some(h) => liveness(&p.proc_root, h),
            None => Liveness::Gone,
        };
        match op::lock(current.as_ref(), &boot_id, live) {
            Lock::Take => {
                put_lock(&p.lock, pid, &text, write_new)?;
                Ok(Got::Taken)
            }
            Lock::TakeOver => {
                put_lock(&p.lock, pid, &text, write_new)?;
                Ok(Got::TookOver(current.unwrap_or_else(|| me.clone())))
            }
            Lock::Busy => Err(match &current {
                Some(h) if h.pid != 0 => format!(
                    "host is locked by operation {} (pid {}); not writing",
                    h.op, h.pid
                ),
                _ => format!(
                    "{} cannot be read as a holder and cannot be proven free; not writing ({DAMAGED_LOCK_HELP})",
                    p.lock.display()
                ),
            }),
        }
    })
}

#[cfg_attr(not(test), allow(dead_code))] // B1: the apply step that writes is not wired yet
/// Give the lock back. Only the holder may: the file is read back and must name `op` and
/// `pid`, otherwise nothing is removed.
pub fn release(p: &LockPaths, op: &str, pid: u32) -> Result<(), String> {
    guarded(p, || {
        let text = fs::read_to_string(&p.lock).map_err(|e| format!("{}: {e}", p.lock.display()))?;
        match holder_from_text(&text) {
            Some(h) if h.op == op && h.pid == pid => {
                fs::remove_file(&p.lock).map_err(|e| format!("{}: {e}", p.lock.display()))?;
                // a removal that is not synced can come back after a crash: the next
                // operation would then take over a holder that had finished
                sync_dir(p.lock.parent().unwrap_or(Path::new(".")))
            }
            _ => Err(format!(
                "{} is not held by operation {op}; left as it is",
                p.lock.display()
            )),
        }
    })
}

// ------------------------------------------------------------------ running a writing operation

/// Where an operation keeps its journal and lock. The real host uses [`OpPlace::host`];
/// tests point it at a scratch directory and a fake `/proc`.
pub struct OpPlace {
    pub ops_dir: PathBuf,
    pub lock: LockPaths,
    /// SHA-256 of the machine id (see [`host_fingerprint`]).
    pub fingerprint: String,
}

impl OpPlace {
    pub fn host() -> Result<Self, String> {
        let lock = LockPaths::host()?;
        let fingerprint = host_fingerprint(Path::new(MACHINE_ID))?;
        fs::create_dir_all(OPS_DIR).map_err(|e| format!("{OPS_DIR}: {e}"))?;
        Ok(OpPlace {
            ops_dir: PathBuf::from(OPS_DIR),
            lock,
            fingerprint,
        })
    }
}

/// Who stands behind an operation, written as its journal's `approval`. Commands run by
/// root on the host are approved by whoever typed them; a customer's request through the
/// forced command of their key is approved by the key's entry (Andy 11-10: customers
/// operate their own site; the site code comes from that entry, never from the
/// customer). Both are **standing** approvals, not one-time nonces, and are spelled with
/// the `standing-` prefix so a journal never confuses them with a nonce from the remote
/// CLI (4542e 11-10 condition a).
///
/// **Known gap (4542e 11-10 condition b)**: `standing-local-root` means "a person typed this
/// on the host". An AI seat running the same command as root over ssh looks the same to
/// this program. That is closed by process, not by code: until the remote CLI with
/// one-time approvals exists, writes on the first host keep going through `pending/SIGN-*.sh` and a
/// human approving in the window — having this value does **not** let an AI run writes
/// directly. That covers every writing subcommand: `site suspend / resume / restore`
/// **and `op recovered`**, which puts a possibly half-changed site back in reach of new
/// operations (c477a #635 seg. 2 L4).
pub const BY_LOCAL_ROOT: &str = "standing-local-root";
pub const BY_CUSTOMER_KEY: &str = "standing-customer-key";
/// The only commands a customer's key may approve (the customer entry's writing requests).
pub const CUSTOMER_COMMANDS: &[&str] = &["site.restart", "site.php-reload"];

/// Run one change as an operation whose plan reads nothing of the host (tests, and plans
/// built only from the command line). Anything that decides from host state — a
/// Caddyfile, a site record, which CTs and IPs are in use, the quarantine marks — must be
/// read under the lock: use [`run_op_prepared`] (c477a #635 seg. 2 M1). Every command
/// the binary has decides from host state, so only the tests use this one.
#[cfg(test)]
pub fn run_op<T>(
    place: &OpPlace,
    command: &str,
    target: &str,
    plan: &Value,
    approval: &str,
    now: u64,
    change: impl FnOnce(&str) -> Result<T, String>,
) -> Result<T, String> {
    run_op_prepared(
        place,
        command,
        target,
        approval,
        now,
        || Ok(Some((plan.clone(), ()))),
        |op_id, ()| change(op_id),
    )?
    .ok_or_else(|| "nothing to do".to_owned())
}

/// Run one change as an operation (4.4.1 §1.1), in this order: take the host lock; refuse
/// while another operation on `target` is unfinished; run `prepare`, which reads what the
/// decision needs and checks it — **under the lock**, so no other operation can change
/// what it read before `change` acts on it (c477a #635 seg. 2 M1); journal `planned` →
/// `approved` → `running` (synced before `change` runs) with the plan `prepare` returned;
/// run `change` with what `prepare` handed it; journal `verified` on `Ok`, `failed` on an
/// error. The lock is given back in every case.
///
/// `prepare` changes nothing. Its error refuses the operation without any journal line
/// (nothing was touched, so nothing is left blocked); `Ok(None)` means there is nothing to
/// do, and the result is `Ok(None)` with no journal either. A `failed` operation blocks
/// further operations on its target until `op recovered` closes it: the host may be half
/// changed.
pub fn run_op_prepared<P, T>(
    place: &OpPlace,
    command: &str,
    target: &str,
    approval: &str,
    now: u64,
    prepare: impl FnOnce() -> Result<Option<(Value, P)>, String>,
    change: impl FnOnce(&str, P) -> Result<T, String>,
) -> Result<Option<T>, String> {
    let op_id = op_id(now, random4()?);
    run_op_prepared_as(place, &op_id, command, target, approval, prepare, change)
}

/// [`run_op_prepared`] under an operation id chosen before: the one a sealed plan carries,
/// so the journal, the plan the approval was given for and the apply share one id. The id
/// must be one ([`op::valid_op_id`]); an id whose journal already exists is refused at its
/// first line (`planned` must open a journal), so one plan is applied at most once.
pub fn run_op_prepared_as<P, T>(
    place: &OpPlace,
    op_id: &str,
    command: &str,
    target: &str,
    approval: &str,
    prepare: impl FnOnce() -> Result<Option<(Value, P)>, String>,
    change: impl FnOnce(&str, P) -> Result<T, String>,
) -> Result<Option<T>, String> {
    // only the two standing approvals exist until one-time approvals come with the remote
    // CLI: anything else — empty, misspelt — is refused, so no string slips past the
    // customer-key allowlist by not looking like the customer key (c477a #635 seg. 2 L3)
    if approval != BY_LOCAL_ROOT && approval != BY_CUSTOMER_KEY {
        return Err(format!("approval {approval:?} is not a known approval"));
    }
    if approval == BY_CUSTOMER_KEY && !CUSTOMER_COMMANDS.contains(&command) {
        return Err(format!("{command} cannot be approved by a customer key"));
    }
    if !op::valid_op_id(op_id) {
        return Err(format!("{op_id:?} is not an operation id"));
    }
    let op_id = op_id.to_owned();
    let pid = std::process::id();
    if let Got::TookOver(h) = acquire(&place.lock, &op_id, pid)? {
        eprintln!(
            "took the host lock over from operation {} (pid {}, gone); its journal says how to resume it",
            h.op, h.pid
        );
    }
    let result = journaled(place, &op_id, command, target, approval, prepare, change);
    if let Err(e) = release(&place.lock, &op_id, pid) {
        eprintln!("{e}");
    }
    result
}

/// [`run_op_prepared`]'s part under the lock.
fn journaled<P, T>(
    place: &OpPlace,
    op_id: &str,
    command: &str,
    target: &str,
    approval: &str,
    prepare: impl FnOnce() -> Result<Option<(Value, P)>, String>,
    change: impl FnOnce(&str, P) -> Result<T, String>,
) -> Result<Option<T>, String> {
    refuse_unfinished(&place.ops_dir, target, op_id)?;
    let Some((plan, input)) = prepare()? else {
        return Ok(None);
    };
    let plan_sha256 = sha256_hex(
        plan_hash_input(&plan, &place.fingerprint, env!("CARGO_PKG_VERSION")).as_bytes(),
    )?;
    let template = Entry {
        op: op_id.to_owned(),
        seq: 0,
        at: 0,
        state: op::State::Planned,
        command: command.to_owned(),
        target: target.to_owned(),
        plan_sha256,
        code: String::new(),
        approval: approval.to_owned(),
    };
    let mut seq = 0;
    let mut step = |state: op::State, code: &str| {
        seq += 1;
        let line = Entry {
            seq,
            at: crate::probe::unix_now(),
            state,
            code: code.to_owned(),
            approval: if state == op::State::Planned {
                String::new()
            } else {
                template.approval.clone()
            },
            ..template.clone()
        };
        // under the guard: an `op stop --apply` holds it from its recheck to its own line
        guarded(&place.lock, || append_entry(&place.ops_dir, &line))
    };
    step(op::State::Planned, "ok")?;
    step(op::State::Approved, "ok")?;
    step(op::State::Running, "ok")?;
    eprintln!("operation {op_id}: {command} {target}");
    // E11: from here the operation says which step it is in and that it is alive
    crate::opstop::begin(
        &place.ops_dir,
        op_id,
        &place.lock,
        std::process::id(),
        crate::probe::unix_now(),
        op::HEARTBEAT_SECS,
    );
    let changed = change(op_id, input);
    crate::opstop::end();
    match changed {
        Ok(v) => {
            step(op::State::Verified, "ok")?;
            crate::opstop::forget(&place.ops_dir, op_id);
            Ok(Some(v))
        }
        Err(e) => match step(op::State::Failed, "error") {
            Ok(()) => Err(format!(
                "{e} (operation {op_id} is failed: check the host, then `op recovered {op_id}`)"
            )),
            Err(j) => Err(format!("{e}; and the journal could not record it: {j}")),
        },
    }
}

/// Close an operation that a person has checked by hand: `failed` (or `running` left by a
/// process that is gone) → `recovery` → `verified`, code [`CLOSED_BY_HAND`]. Under the host
/// lock.
pub fn mark_recovered(place: &OpPlace, op_id: &str, now: u64) -> Result<(), String> {
    let path = journal_path(&place.ops_dir, op_id)?;
    let pid = std::process::id();
    acquire(&place.lock, op_id, pid)?;
    let result = recover_locked(place, &path, op_id, now);
    // the lock was taken under this operation's id: give it back whatever happened
    let _ = release(&place.lock, op_id, pid);
    result
}

/// Where [`archive_op`] moves a journal: below the journal directory, out of what
/// [`last_entries`] reads.
pub const ARCHIVE_DIR: &str = "archive";
/// One line per archived journal (when, which, why), below [`ARCHIVE_DIR`].
pub const ARCHIVE_NOTES: &str = "notes.jsonl";

/// Every line of a journal that reads as an entry on its own, and how many lines there
/// were. For telling a person what a damaged journal was doing; never for deciding that
/// an operation finished.
fn salvage(text: &str) -> (Vec<Entry>, usize) {
    let lines: Vec<&str> = text.lines().collect();
    let entries = lines
        .iter()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter_map(|v| Entry::from_json(&v).ok())
        .collect();
    (entries, lines.len())
}

/// A journal that cannot be read or replayed may still be an operation that changed the
/// host: `running` was written, then a later line was damaged. Archiving it would take
/// its target out of `refuse_unfinished` with the host possibly half changed (#638 M1,
/// 1ced7). So it is archived only when the person names the target, after checking the
/// host for it; until then the refusal shows what could be read of it.
fn damaged(path: &Path, op_id: &str, err: &str, target: Option<&str>) -> Result<String, String> {
    let text = fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
        .unwrap_or_default();
    let (readable, total) = salvage(&text);
    let mut targets: Vec<&str> = readable.iter().map(|e| e.target.as_str()).collect();
    targets.sort_unstable();
    targets.dedup();
    let changed = readable.iter().any(|e| {
        matches!(
            e.state,
            op::State::Running | op::State::Failed | op::State::Recovery
        )
    }) || readable
        .last()
        .is_some_and(|e| e.state == op::State::Approved);
    let summary = format!(
        "{err}; {}/{total} lines readable; target {}; command {}; last readable state {}{}",
        readable.len(),
        if targets.is_empty() {
            "unreadable".to_owned()
        } else {
            targets.join(", ")
        },
        readable.last().map_or("unreadable", |e| e.command.as_str()),
        readable.last().map_or("none", |e| e.state.as_str()),
        if changed {
            "; it may have changed the host (running, failed or recovery was written, or it stopped at approved)"
        } else {
            ""
        },
    );
    let named = target.is_some_and(|t| match targets.as_slice() {
        [only] => t == *only,
        [] => !t.is_empty(),
        _ => false,
    });
    if !named && targets.len() > 1 {
        // one operation has one target: a journal naming several cannot be told apart
        // by a command (#638 r2 N1, 1ced7)
        return Err(format!(
            "operation {op_id}: journal damaged ({summary}) and it names more than one target. Not archived, and `op archive` will not: check the host for every one of them, move the file aside by hand and write down what you did"
        ));
    }
    if !named {
        return Err(format!(
            "operation {op_id}: journal damaged ({summary}). Not archived: check the host for that target, then run `op archive {op_id} --target <target>` naming it"
        ));
    }
    Ok(format!(
        "damaged ({summary}); host checked by hand for {}",
        target.unwrap_or_default()
    ))
}

/// Who ran this, for the archive note (#638 r2 L1, 1ced7): the effective uid (the owner
/// of `/proc/self`), and `SUDO_USER` / `LOGNAME` as they were set. The names are only a
/// lead: anyone can set them. Linux only, like the rest of the host side; what cannot be
/// read is null.
fn who() -> Value {
    use std::os::unix::fs::MetadataExt;
    let uid = fs::metadata("/proc/self").ok().map(|m| m.uid());
    json!({
        "uid": uid,
        "sudo_user": std::env::var("SUDO_USER").ok(),
        "logname": std::env::var("LOGNAME").ok(),
    })
}

/// Append one note line and sync it. A file made by this call is also synced into its
/// directory, so the note does not depend on a later sync of the directory (#638 r2 N2).
fn append_note(path: &Path, note: &Value) -> Result<(), String> {
    let new = !path.exists();
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(format!("{note}\n").as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", path.display()))?;
    if new {
        sync_dir(path.parent().unwrap_or(Path::new(".")))?;
    }
    Ok(())
}

/// Move one operation's journal out of the way, by hand, under the host lock (#635 seg. 1
/// N2, 1ced7). Only a journal that cannot be read or replayed — which otherwise refuses
/// every operation on every target — or one whose operation is verified. An operation
/// that is running, failed or in recovery is refused here: a person closes it with
/// `op recovered` after checking the host. Returns what was archived and why.
pub fn archive_op(place: &OpPlace, op_id: &str, target: Option<&str>) -> Result<String, String> {
    let path = journal_path(&place.ops_dir, op_id)?;
    let pid = std::process::id();
    // bound, not a bare call: a knife anchors on the bare form in mark_recovered
    let _got = acquire(&place.lock, op_id, pid)?;
    let result = archive_locked(&place.ops_dir, &path, op_id, target);
    let _ = release(&place.lock, op_id, pid);
    result
}

fn archive_locked(
    dir: &Path,
    path: &Path,
    op_id: &str,
    target: Option<&str>,
) -> Result<String, String> {
    if !path.exists() {
        return Err(format!(
            "operation {op_id} has no journal in {}",
            dir.display()
        ));
    }
    let journal = read_journal(path);
    let why = match journal.as_ref().map(|(entries, _)| op::replay(entries)) {
        Err(e) => damaged(path, op_id, e, target)?,
        Ok(Err(e)) => damaged(path, op_id, &e.to_string(), target)?,
        Ok(Ok(last)) if last.state == op::State::Verified => "finished".to_owned(),
        Ok(Ok(last)) => {
            return Err(format!(
            "operation {op_id} is {}: not archived; check the host, then `op recovered {op_id}`",
            last.state.as_str()
        ))
        }
    };
    let archive = dir.join(ARCHIVE_DIR);
    fs::create_dir_all(&archive).map_err(|e| format!("{}: {e}", archive.display()))?;
    let to = archive.join(format!("{op_id}.jsonl"));
    if to.exists() {
        return Err(format!("{} already exists; not overwritten", to.display()));
    }
    // the record goes first: a crash before the move leaves a note and the journal in
    // place (run it again); never a journal moved with nothing saying why (#638 L1)
    let at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    let note = json!({
        "at": at,
        "op": op_id,
        "why": why,
        "pid": std::process::id(),
        "who": who(),
    });
    append_note(&archive.join(ARCHIVE_NOTES), &note)?;
    fs::rename(path, &to).map_err(|e| format!("{}: {e}", path.display()))?;
    sync_dir(&archive)?;
    sync_dir(dir)?;
    Ok(format!("{op_id}: archived, {why} -> {}", to.display()))
}

/// The code of the lines `op recovered` writes. The approval column cannot say who closed
/// the operation — one operation runs under one approval to its end, and a customer key's
/// operation keeps `standing-customer-key` — so the code does: closed by a person as root
/// on the host (c477a #635 seg. 2 L1).
pub const CLOSED_BY_HAND: &str = "manual_by_local_root";

fn recover_locked(place: &OpPlace, path: &Path, op_id: &str, now: u64) -> Result<(), String> {
    let (entries, _) = read_journal(path)?;
    let last = op::replay(&entries)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .clone();
    let add = |state: op::State, code: &str, seq: u32| {
        append_entry(
            &place.ops_dir,
            &Entry {
                seq,
                at: now,
                state,
                code: code.to_owned(),
                ..last.clone()
            },
        )
    };
    let mut seq = last.seq;
    match last.state {
        op::State::Running => {
            seq += 1;
            add(op::State::Failed, "interrupted", seq)?;
        }
        op::State::Failed | op::State::Recovery => {}
        other => {
            return Err(format!(
                "operation {op_id} is {}: nothing to recover",
                other.as_str()
            ))
        }
    }
    if last.state != op::State::Recovery {
        seq += 1;
        add(op::State::Recovery, CLOSED_BY_HAND, seq)?;
    }
    add(op::State::Verified, CLOSED_BY_HAND, seq + 1)
}

// ------------------------------------------------------------------ read-only command

/// `op status [<op id>]` — the lock holder (and whether it is alive) and, given an id, that
/// operation's journal: last state and what resuming it means. `op unfinished <target>` —
/// the operations that block a new one on `target`. Nothing is written.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let dir = Path::new(OPS_DIR);
    match args.first().map(String::as_str) {
        Some("status") => {
            let mut doc = json!({});
            let lock = match fs::read_to_string(LOCK_FILE) {
                Ok(t) => match holder_from_text(&t) {
                    Some(h) => {
                        let live = match LockPaths::host() {
                            Ok(p) => format!("{:?}", liveness(&p.proc_root, &h)).to_lowercase(),
                            Err(_) => "unknown".to_owned(),
                        };
                        json!({ "op": h.op, "pid": h.pid, "boot_id": h.boot_id, "liveness": live })
                    }
                    None => json!({ "damaged": true, "what_to_do": DAMAGED_LOCK_HELP }),
                },
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Value::Null,
                Err(e) => return Err(format!("{LOCK_FILE}: {e}")),
            };
            doc["lock"] = lock;
            // which targets are held, by what, and what a person runs after checking
            doc["unfinished"] = match last_entries(dir) {
                Ok(lasts) => unfinished_report(&lasts),
                Err(e) => {
                    json!({ "error": e, "what_to_do": "a journal cannot be read: every target is held until it is fixed or moved aside by hand" })
                }
            };
            // sites being opened, with the step each reached: a clone journaled `verified`
            // leaves a container that is not a site yet (c477a, B1 L1)
            doc["opening"] =
                crate::sitecreatecmd::opening_report(Path::new(crate::sitecreatecmd::CREATING));
            if let Some(op) = args.get(1) {
                // E11: the step, how long since it last wrote, running / stalled / finished
                let paths = LockPaths::host()?;
                let facts = crate::opstop::Facts {
                    ops_dir: dir,
                    lock: &paths,
                    fingerprint: "",
                };
                doc["op"] = crate::opstop::status(
                    &facts,
                    op,
                    crate::probe::unix_now(),
                    op::STALLED_SECS,
                )?;
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
            );
            Ok(0)
        }
        Some("unfinished") => {
            let target = args.get(1).ok_or("op unfinished needs <target>")?;
            match refuse_unfinished(dir, target, "") {
                Ok(()) => {
                    println!("{target}: nothing unfinished");
                    Ok(0)
                }
                Err(e) => {
                    println!("{e}");
                    Ok(1)
                }
            }
        }
        Some("recovered") => {
            // the one writing subcommand: a person checked the host and closes the operation
            let op_id = args.get(1).ok_or("op recovered needs <op id>")?;
            mark_recovered(&OpPlace::host()?, op_id, crate::probe::unix_now())?;
            println!("{op_id}: recovered (closed by hand)");
            Ok(0)
        }
        Some("stop") => crate::opstop::cmd(args.get(1..).unwrap_or_default()),
        Some("archive") => {
            // moves a damaged or finished journal aside; refuses an unfinished operation
            let op_id = args.get(1).ok_or("op archive needs <op id> [--target <target>]")?;
            let target = match args.get(2..).unwrap_or_default() {
                [] => None,
                [flag, t] if flag.as_str() == "--target" => Some(t.as_str()),
                _ => return Err("op archive needs <op id> [--target <target>]".to_owned()),
            };
            println!("{}", archive_op(&OpPlace::host()?, op_id, target)?);
            Ok(0)
        }
        _ => Err(
            "op needs status [<op id>] | unfinished <target> | recovered <op id> | archive <op id> [--target <target>] | stop <op id> --plan | --apply <plan.json>"
                .to_owned(),
        ),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::op::State;

    const BOOT_A: &str = "0f0e0d0c-0b0a-4908-8706-050403020100";
    const BOOT_B: &str = "1f0e0d0c-0b0a-4908-8706-050403020100";
    const OP1: &str = "20261110T120000Z-0a0b0c0d";
    const OP2: &str = "20261110T120100Z-1a1b1c1d";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    /// A fresh scratch directory per test.
    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "aib-opstore-{name}-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn stat_line(pid: u32, comm: &str, start: u64) -> String {
        // fields 3..21 are filler, field 22 is the start time
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        format!("{pid} ({comm}) {filler} {start} 0 0\n")
    }

    fn fake_proc(root: &Path, pid: u32, start: u64) {
        let d = root.join(pid.to_string());
        fs::create_dir_all(&d).unwrap();
        fs::write(d.join("stat"), stat_line(pid, "aib-cloud-host", start)).unwrap();
    }

    fn paths(d: &Path, boot: &str) -> LockPaths {
        fs::write(d.join("boot_id"), format!("{boot}\n")).unwrap();
        fs::create_dir_all(d.join("proc")).unwrap();
        LockPaths {
            lock: d.join("host.lock"),
            proc_root: d.join("proc"),
            boot_id: d.join("boot_id"),
        }
    }

    fn entry(op: &str, seq: u32, state: State, approval: &str) -> Entry {
        Entry {
            op: op.to_owned(),
            seq,
            at: 1_794_300_000 + u64::from(seq),
            state,
            command: "site.quarantine".to_owned(),
            target: "site:site2".to_owned(),
            plan_sha256: SHA.to_owned(),
            code: "ok".to_owned(),
            approval: approval.to_owned(),
        }
    }

    #[test]
    fn op_ids_are_what_the_journal_accepts() {
        assert_eq!(op_id(0, [0, 0, 0, 0]), "19700101T000000Z-00000000");
        assert_eq!(
            op_id(1_794_275_100, [0x0a, 0x1b, 0x2c, 0x3d]),
            "20261110T014500Z-0a1b2c3d"
        );
        assert_eq!(op_id(1_709_164_800, [255; 4]), "20240229T000000Z-ffffffff");
        assert_eq!(op_id(1_735_689_599, [1; 4]), "20241231T235959Z-01010101");
        for now in [0, 1_794_275_100, 1_709_164_800, 4_102_444_799] {
            assert!(op::valid_op_id(&op_id(now, [7; 4])), "{now}");
        }
    }

    /// B1 (a): the hashed bytes carry the host and the version, and key order does not
    /// change them.
    #[test]
    fn the_plan_hash_covers_host_and_version() {
        let plan =
            json!({ "site": "site2", "plan": "quarantine", "steps": [{ "do": "verify", "a": 1 }] });
        let reordered =
            json!({ "steps": [{ "a": 1, "do": "verify" }], "plan": "quarantine", "site": "site2" });
        let base = plan_hash_input(&plan, "f1", "0.2.0");
        assert_eq!(base, plan_hash_input(&reordered, "f1", "0.2.0"));
        assert_ne!(base, plan_hash_input(&plan, "f2", "0.2.0"), "another host");
        assert_ne!(
            base,
            plan_hash_input(&plan, "f1", "0.2.1"),
            "another version"
        );
        assert!(
            base.contains("\"plan\":\"quarantine\",\"site\":\"site2\""),
            "{base}"
        );
        assert!(
            base.contains("\nhost f1\n") && base.ends_with("\nversion 0.2.0\n"),
            "{base}"
        );
        // the seal fields are not part of the plan that is hashed
        let mut sealed = plan.clone();
        let o = sealed.as_object_mut().unwrap();
        o.insert("plan_sha256".to_owned(), json!(SHA));
        o.insert("host_fingerprint".to_owned(), json!("f1"));
        o.insert("crate_version".to_owned(), json!("0.2.0"));
        assert_eq!(plan_hash_input(&unsealed(&sealed), "f1", "0.2.0"), base);
    }

    #[test]
    fn a_machine_id_is_32_lower_hex() {
        assert_eq!(
            machine_id("0123456789abcdef0123456789abcdef\n"),
            Some("0123456789abcdef0123456789abcdef")
        );
        assert_eq!(machine_id(""), None);
        assert_eq!(machine_id("uninitialized\n"), None);
        assert_eq!(machine_id("0123456789ABCDEF0123456789ABCDEF"), None);
        assert_eq!(machine_id("0123456789abcdef0123456789abcde"), None);
        let d = scratch("mid");
        assert!(host_fingerprint(&d.join("absent")).is_err());
        fs::write(d.join("mid"), "uninitialized\n").unwrap();
        assert!(host_fingerprint(&d.join("mid")).is_err());
    }

    /// `sha256sum` is on every Linux host this runs on; elsewhere it may be missing.
    #[cfg(target_os = "linux")]
    #[test]
    fn sha256_by_the_system_tool() {
        assert_eq!(
            sha256_hex(b"abc").unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        let mut p = json!({ "plan": "quarantine" });
        seal(&mut p, "f1", "0.2.0").unwrap();
        assert_eq!(p["plan_sha256"].as_str().unwrap().len(), 64);
        assert!(seal(&mut p, "f1", "0.2.0").is_err(), "sealed twice");
    }

    #[derive(Default)]
    struct Recorder {
        log: Vec<&'static str>,
        bytes: Vec<u8>,
    }
    impl Sink for Recorder {
        fn append(&mut self, b: &[u8]) -> std::io::Result<()> {
            self.log.push("append");
            self.bytes.extend_from_slice(b);
            Ok(())
        }
        fn sync(&mut self) -> std::io::Result<()> {
            self.log.push("sync");
            Ok(())
        }
    }

    /// B1 (b): a line is one write and is synced before the caller hears Ok.
    #[test]
    fn a_line_is_synced_before_it_counts() {
        let mut r = Recorder::default();
        write_line(&mut r, &entry(OP1, 1, State::Planned, "")).unwrap();
        assert_eq!(r.log, ["append", "sync"]);
        let text = String::from_utf8(r.bytes).unwrap();
        assert!(
            text.ends_with('\n') && text.matches('\n').count() == 1,
            "{text}"
        );
        let (back, cut) = op::parse_journal(&text).unwrap();
        assert!(!cut);
        assert_eq!(back, [entry(OP1, 1, State::Planned, "")]);
    }

    #[test]
    fn a_journal_only_grows_by_lines_that_follow() {
        let d = scratch("journal");
        append_entry(&d, &entry(OP1, 1, State::Planned, "")).unwrap();
        // skipping a state, repeating a number, another plan: refused, nothing written
        assert!(append_entry(&d, &entry(OP1, 2, State::Running, "n1")).is_err());
        assert!(append_entry(&d, &entry(OP1, 1, State::Planned, "")).is_err());
        let mut other = entry(OP1, 2, State::Approved, "n1");
        other.plan_sha256 = "b".repeat(64);
        assert!(append_entry(&d, &other).is_err());
        append_entry(&d, &entry(OP1, 2, State::Approved, "n1")).unwrap();
        append_entry(&d, &entry(OP1, 3, State::Running, "n1")).unwrap();
        let (back, cut) = read_journal(&journal_path(&d, OP1).unwrap()).unwrap();
        assert!(!cut);
        assert_eq!(back.len(), 3);
        assert_eq!(op::replay(&back).unwrap().state, State::Running);
        assert!(journal_path(&d, "../x").is_err());
    }

    /// A last line cut short by a crash was never acted on: it is dropped and the next line
    /// goes after the last whole one.
    #[test]
    fn a_cut_short_last_line_is_cut_off_before_the_next() {
        let d = scratch("cut");
        append_entry(&d, &entry(OP1, 1, State::Planned, "")).unwrap();
        let path = journal_path(&d, OP1).unwrap();
        let mut f = OpenOptions::new().append(true).open(&path).unwrap();
        f.write_all(br#"{"op":"2026"#).unwrap();
        drop(f);
        let (back, cut) = read_journal(&path).unwrap();
        assert!(cut && back.len() == 1);
        append_entry(&d, &entry(OP1, 2, State::Approved, "n1")).unwrap();
        let (back, cut) = read_journal(&path).unwrap();
        assert!(!cut);
        assert_eq!(back.len(), 2);
        // a damaged line that is not the last one refuses the journal
        fs::write(
            &path,
            format!("{{bad}}\n{}\n", entry(OP1, 1, State::Planned, "").to_json()),
        )
        .unwrap();
        assert!(read_journal(&path).is_err());
    }

    /// B1 (c): `/proc/<pid>/stat` is read from the last `)`.
    #[test]
    fn stat_start_time_counts_from_the_last_paren() {
        assert_eq!(stat_start_time(&stat_line(42, "sh", 777)), Some(777));
        assert_eq!(stat_start_time(&stat_line(42, "a) b (c", 778)), Some(778));
        assert_eq!(stat_start_time(&stat_line(42, "x 1 2 3", 779)), Some(779));
        assert_eq!(stat_start_time("42 (sh) S 1"), None);
        assert_eq!(stat_start_time(""), None);
        assert_eq!(stat_state(&stat_line(42, "a) Z (c", 778)), Some('3'));
        assert_eq!(stat_state("42 (sh) Z 1"), Some('Z'));
        assert_eq!(stat_state("42 (sh)"), None);
    }

    #[test]
    fn liveness_from_proc() {
        let d = scratch("live");
        let root = d.join("proc");
        fake_proc(&root, 100, 5000);
        let h = |pid, start| Holder {
            op: OP1.to_owned(),
            pid,
            start_time: start,
            boot_id: BOOT_A.to_owned(),
        };
        assert_eq!(liveness(&root, &h(100, 5000)), Liveness::Alive);
        assert_eq!(liveness(&root, &h(100, 4999)), Liveness::Gone, "pid reused");
        assert_eq!(
            liveness(&root, &h(101, 5000)),
            Liveness::Gone,
            "no such pid"
        );
        // a stat that cannot be read is not proof of death
        fs::create_dir_all(root.join("102").join("stat")).unwrap();
        assert_eq!(liveness(&root, &h(102, 5000)), Liveness::Unknown);
        fs::create_dir_all(root.join("103")).unwrap();
        fs::write(root.join("103").join("stat"), "garbage").unwrap();
        assert_eq!(liveness(&root, &h(103, 5000)), Liveness::Unknown);
        // a zombie: pid and start time still read, but it has exited and holds nothing
        fs::create_dir_all(root.join("104")).unwrap();
        let zombie = stat_line(104, "aib-cloud-host", 5000).replacen(") 3 ", ") Z ", 1);
        fs::write(root.join("104").join("stat"), zombie).unwrap();
        assert_eq!(liveness(&root, &h(104, 5000)), Liveness::Gone, "zombie");
    }

    #[test]
    fn the_lock_is_taken_kept_and_taken_over() {
        let d = scratch("lock");
        let p = paths(&d, BOOT_A);
        fake_proc(&p.proc_root, 100, 5000);
        fake_proc(&p.proc_root, 200, 6000);
        assert_eq!(acquire(&p, OP1, 100).unwrap(), Got::Taken);
        // held by a live process: busy, and the file still names it
        let err = acquire(&p, OP2, 200).unwrap_err();
        assert!(err.contains(OP1), "{err}");
        assert_eq!(
            holder_from_text(&fs::read_to_string(&p.lock).unwrap())
                .unwrap()
                .pid,
            100
        );
        // the holder dies: taken over, and the old holder is handed back for resume
        fs::remove_dir_all(p.proc_root.join("100")).unwrap();
        match acquire(&p, OP2, 200).unwrap() {
            Got::TookOver(h) => assert_eq!((h.op.as_str(), h.pid), (OP1, 100)),
            Got::Taken => panic!("must be a take-over"),
        }
        assert_eq!(
            holder_from_text(&fs::read_to_string(&p.lock).unwrap())
                .unwrap()
                .op,
            OP2
        );
    }

    #[test]
    fn a_lock_from_before_a_reboot_is_taken_over() {
        let d = scratch("reboot");
        let p = paths(&d, BOOT_B);
        fake_proc(&p.proc_root, 100, 5000);
        fake_proc(&p.proc_root, 200, 6000);
        let old = Holder {
            op: OP1.to_owned(),
            pid: 100,
            start_time: 5000,
            boot_id: BOOT_A.to_owned(),
        };
        fs::write(&p.lock, holder_json(&old).to_string()).unwrap();
        assert_eq!(acquire(&p, OP2, 200).unwrap(), Got::TookOver(old));
    }

    /// A lock file that cannot be read as a holder proves nothing: busy, not free.
    #[test]
    fn a_damaged_lock_file_keeps_the_lock() {
        let d = scratch("damaged");
        let p = paths(&d, BOOT_A);
        fake_proc(&p.proc_root, 200, 6000);
        fs::write(&p.lock, "{not json").unwrap();
        assert!(acquire(&p, OP2, 200).is_err());
        assert_eq!(fs::read_to_string(&p.lock).unwrap(), "{not json");
        // and without a readable boot id of our own nothing is taken at all
        let q = paths(&scratch("noboot"), "not-a-boot-id");
        fake_proc(&q.proc_root, 200, 6000);
        assert!(acquire(&q, OP2, 200).is_err());
        assert!(!q.lock.exists());
    }

    /// #635 seg. 1 L1 (1ced7): a crash between making the lock file and writing it left an
    /// empty lock file, which no reboot clears. Now the holder is written aside first.
    #[test]
    fn a_crash_while_taking_the_lock_leaves_no_lock_file() {
        let d = scratch("crash-take");
        let p = paths(&d, BOOT_A);
        // the writer dies after the file exists and before anything is in it
        let died = put_lock(&p.lock, 200, "{}\n", |path, _| {
            File::create(path).unwrap();
            Err("killed".to_owned())
        });
        assert!(died.is_err());
        assert!(!p.lock.exists(), "an empty lock file was left behind");
        // nothing of it is left to trip over: the next operation simply takes the lock
        fake_proc(&p.proc_root, 200, 6000);
        assert_eq!(acquire(&p, OP2, 200).unwrap(), Got::Taken);
        let held = fs::read_to_string(&p.lock).unwrap();
        assert_eq!(holder_from_text(&held).unwrap().op, OP2);
        let left: Vec<_> = fs::read_dir(p.lock.parent().unwrap())
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".new"))
            .collect();
        assert!(left.is_empty(), "{left:?}");
        // a lock file that is damaged anyway says what to do
        fs::write(&p.lock, "").unwrap();
        let busy = acquire(&p, OP1, 200).unwrap_err();
        assert!(busy.contains("move the lock file aside"), "{busy}");
    }

    /// #635 L1 N1 (1ced7): a writer killed after writing its holder aside and before the
    /// rename leaves `<lock>.<pid>.new`; the next operation to take the lock clears it, and
    /// leaves files that only look alike.
    #[test]
    fn a_holder_written_aside_and_never_renamed_is_cleared() {
        let d = scratch("leftover");
        let p = paths(&d, BOOT_A);
        let mut aside = p.lock.clone().into_os_string();
        aside.push(".4242.new");
        let aside = PathBuf::from(aside);
        fs::write(&aside, "{\"op\":\"half\"}\n").unwrap();
        let mut other = p.lock.clone().into_os_string();
        other.push(".damaged");
        let other = PathBuf::from(other);
        fs::write(&other, "kept").unwrap();
        fake_proc(&p.proc_root, 200, 6000);
        assert_eq!(acquire(&p, OP2, 200).unwrap(), Got::Taken);
        assert!(!aside.exists(), "the half-done holder was left behind");
        assert!(other.exists(), "a file that is not a leftover was removed");
    }

    #[test]
    fn only_the_holder_releases() {
        let d = scratch("release");
        let p = paths(&d, BOOT_A);
        fake_proc(&p.proc_root, 100, 5000);
        acquire(&p, OP1, 100).unwrap();
        assert!(release(&p, OP2, 100).is_err());
        assert!(release(&p, OP1, 200).is_err());
        assert!(p.lock.exists());
        release(&p, OP1, 100).unwrap();
        assert!(!p.lock.exists());
    }

    /// B1 (d): an unfinished operation on a target blocks a new one there, not elsewhere and
    /// not its own recovery; a damaged journal blocks everything.
    #[test]
    fn unfinished_operations_block_their_target() {
        let d = scratch("unfinished");
        assert!(refuse_unfinished(&d.join("absent"), "site:site2", OP2).is_ok());
        append_entry(&d, &entry(OP1, 1, State::Planned, "")).unwrap();
        assert!(
            refuse_unfinished(&d, "site:site2", OP2).is_ok(),
            "planned changed nothing"
        );
        append_entry(&d, &entry(OP1, 2, State::Approved, "n1")).unwrap();
        append_entry(&d, &entry(OP1, 3, State::Running, "n1")).unwrap();
        let err = refuse_unfinished(&d, "site:site2", OP2).unwrap_err();
        assert!(err.contains(OP1), "{err}");
        assert!(refuse_unfinished(&d, "site:site3", OP2).is_ok());
        assert!(
            refuse_unfinished(&d, "site:site2", OP1).is_ok(),
            "its own recovery"
        );
        // a journal that does not replay is not skipped
        fs::write(
            d.join(format!("{OP2}.jsonl")),
            format!("{}\n", entry(OP2, 2, State::Approved, "n2").to_json()),
        )
        .unwrap();
        assert!(refuse_unfinished(&d, "site:site9", "20261110T130000Z-00000000").is_err());
    }

    #[test]
    fn the_real_lock_is_linux_only() {
        assert_eq!(LockPaths::host().is_ok(), cfg!(target_os = "linux"));
    }

    /// An operation place in a scratch directory, with this process in the fake `/proc`.
    fn place(name: &str) -> OpPlace {
        let d = scratch(name);
        let lock = paths(&d, BOOT_A);
        fake_proc(&lock.proc_root, std::process::id(), 4242);
        let ops_dir = d.join("ops");
        fs::create_dir_all(&ops_dir).unwrap();
        OpPlace {
            ops_dir,
            lock,
            fingerprint: "f".repeat(64),
        }
    }

    fn states(place: &OpPlace, op_id: &str) -> Vec<State> {
        let (entries, _) = read_journal(&journal_path(&place.ops_dir, op_id).unwrap()).unwrap();
        entries.iter().map(|e| e.state).collect()
    }

    #[test]
    fn an_operation_is_journaled_and_gives_the_lock_back() {
        let pl = place("runop-ok");
        let plan = json!({ "command": "site.suspend", "site": "site2" });
        let mut seen = String::new();
        let got = run_op(
            &pl,
            "site.suspend",
            "site:site2",
            &plan,
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op_id| {
                // the change runs only after `running` is on disk
                seen = op_id.to_owned();
                assert_eq!(states(&pl, op_id).last(), Some(&State::Running));
                Ok(7)
            },
        )
        .unwrap();
        assert_eq!(got, 7);
        assert_eq!(
            states(&pl, &seen),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Verified
            ]
        );
        let (entries, _) = read_journal(&journal_path(&pl.ops_dir, &seen).unwrap()).unwrap();
        assert!(entries[0].approval.is_empty());
        assert!(entries[1..].iter().all(|e| e.approval == BY_LOCAL_ROOT));
        // the same plan hash on every line, and it is the sealed hash of the plan
        let sha = sha256_hex(
            plan_hash_input(&plan, &pl.fingerprint, env!("CARGO_PKG_VERSION")).as_bytes(),
        )
        .unwrap();
        assert!(entries.iter().all(|e| e.plan_sha256 == sha));
        assert!(!pl.lock.lock.exists(), "the lock is given back");
    }

    /// c477a #635 seg. 2 M1: what an operation decides from is read under the host lock,
    /// so another operation cannot change it between the reading and the writing.
    #[test]
    fn what_an_operation_decides_from_is_read_under_the_lock() {
        let pl = place("prepare");
        let edge = pl.ops_dir.with_file_name("Caddyfile");
        fs::write(&edge, "v1").unwrap();
        // while operation A reads its base, operation B tries to rewrite it
        let mut inner: Option<Result<Option<()>, String>> = None;
        let mut inner_prepared = false;
        let done = run_op_prepared(
            &pl,
            "site.suspend",
            "site:site2",
            BY_LOCAL_ROOT,
            1_794_300_000,
            || {
                let base = fs::read_to_string(&edge).map_err(|e| e.to_string())?;
                inner = Some(run_op_prepared(
                    &pl,
                    "site.restore",
                    "site:site3",
                    BY_LOCAL_ROOT,
                    1_794_300_001,
                    || {
                        inner_prepared = true;
                        fs::write(&edge, "v2").map_err(|e| e.to_string())?;
                        Ok(Some((json!({}), ())))
                    },
                    |_, ()| Ok::<(), String>(()),
                ));
                Ok(Some((json!({ "base": base.clone() }), base)))
            },
            |_, base: String| fs::write(&edge, format!("{base}+site2")).map_err(|e| e.to_string()),
        )
        .unwrap();
        assert_eq!(done, Some(()));
        let busy = inner.unwrap().unwrap_err();
        assert!(busy.contains("locked"), "{busy}");
        assert!(!inner_prepared, "B read and wrote while A was deciding");
        assert_eq!(fs::read_to_string(&edge).unwrap(), "v1+site2");

        // a refusal while preparing leaves no journal, nothing blocked, the lock given back
        let mut changed = false;
        let err = run_op_prepared(
            &pl,
            "site.resume",
            "site:site2",
            BY_LOCAL_ROOT,
            1_794_300_060,
            || -> Result<Option<(Value, ())>, String> { Err("quarantined".to_owned()) },
            |_, ()| {
                changed = true;
                Ok::<(), String>(())
            },
        )
        .unwrap_err();
        assert_eq!(err, "quarantined");
        // nothing to do: no journal either
        let nothing = run_op_prepared(
            &pl,
            "site.resume",
            "site:site2",
            BY_LOCAL_ROOT,
            1_794_300_120,
            || Ok(None::<(Value, ())>),
            |_, ()| {
                changed = true;
                Ok::<(), String>(())
            },
        )
        .unwrap();
        assert_eq!(nothing, None);
        assert!(!changed);
        let journals = fs::read_dir(&pl.ops_dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.path().extension().is_some_and(|x| x == "jsonl"))
            .count();
        assert_eq!(journals, 1, "only A's operation is journaled");
        assert!(!pl.lock.lock.exists());
        refuse_unfinished(&pl.ops_dir, "site:site2", "").unwrap();
    }

    /// f1fae 11-10: what holds a target is named, with the command to run after checking.
    #[test]
    fn a_held_target_names_its_operation_and_the_command() {
        let pl = place("held");
        let mut failed = String::new();
        let _ = run_op(
            &pl,
            "site.restore",
            "site:site2",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op_id| -> Result<(), String> {
                failed = op_id.to_owned();
                Err("502".to_owned())
            },
        );
        let err = refuse_unfinished(&pl.ops_dir, "site:site2", "").unwrap_err();
        assert!(
            err.contains(&format!(
                "operation {failed} (site.restore site:site2, failed)"
            )) && err.contains(&format!("`aib-cloud-host op recovered {failed}`")),
            "{err}"
        );
        let report = unfinished_report(&last_entries(&pl.ops_dir).unwrap());
        assert_eq!(report["site:site2"][0]["op"], json!(failed));
        assert_eq!(report["site:site2"][0]["state"], json!("failed"));
        assert!(report.get("site:site3").is_none());
        mark_recovered(&pl, &failed, 1_794_300_060).unwrap();
        assert_eq!(
            unfinished_report(&last_entries(&pl.ops_dir).unwrap()),
            json!({})
        );
    }

    /// c477a #635 seg. 2 L2: `op recovered` is the way out for a person, never a way past an
    /// operation that is still running — its process holds the host lock.
    #[test]
    fn a_running_operation_cannot_be_closed_by_hand() {
        let pl = place("recover-live");
        let mut tried: Option<Result<(), String>> = None;
        let mut me = String::new();
        run_op(
            &pl,
            "site.suspend",
            "site:site2",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op_id| {
                me = op_id.to_owned();
                tried = Some(mark_recovered(&pl, op_id, 1_794_300_001));
                Ok(())
            },
        )
        .unwrap();
        let err = tried.unwrap().unwrap_err();
        assert!(err.contains("locked"), "{err}");
        assert_eq!(
            states(&pl, &me),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Verified
            ]
        );
    }

    /// #635 seg. 1 N2 (1ced7): one damaged journal refuses every operation on every
    /// target; `op archive` is the way out, and it does not take an unfinished one.
    /// #638 M1 (1ced7): a damaged journal of an operation that wrote `running` is not
    /// archived on its own: the refusal shows the target and what was read, and only the
    /// target named by the person (the one read from it) archives it, with a note.
    #[test]
    fn a_damaged_journal_that_ran_is_archived_only_with_its_target_named() {
        let pl = place("archive-ran");
        let mut ran = String::new();
        run_op(
            &pl,
            "site.suspend",
            "site:site4",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op| {
                ran = op.to_owned();
                Ok(())
            },
        )
        .unwrap();
        // it stopped after `running`, and a line before it was damaged
        let path = journal_path(&pl.ops_dir, &ran).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        let mut lines: Vec<&str> = text
            .lines()
            .filter(|l| !l.contains("\"verified\""))
            .collect();
        assert!(lines.iter().any(|l| l.contains("\"running\"")), "{text}");
        lines.insert(1, "{damaged");
        fs::write(&path, lines.join("\n") + "\n").unwrap();

        let why = archive_op(&pl, &ran, None).unwrap_err();
        assert!(
            why.contains("running") && why.contains("site:site4") && why.contains("--target"),
            "{why}"
        );
        assert!(path.exists());
        // another target is not this one
        assert!(archive_op(&pl, &ran, Some("site:site5")).is_err());
        assert!(path.exists());
        let done = archive_op(&pl, &ran, Some("site:site4")).unwrap();
        assert!(
            done.contains("damaged") && done.contains("site:site4"),
            "{done}"
        );
        assert!(!path.exists());
        let notes = fs::read_to_string(pl.ops_dir.join(ARCHIVE_DIR).join(ARCHIVE_NOTES)).unwrap();
        assert!(
            notes.contains(ran.as_str()) && notes.contains("site:site4"),
            "{notes}"
        );
        // who did it, as far as the host can tell (#638 r2 L1)
        let line: Value = serde_json::from_str(notes.lines().last().unwrap()).unwrap();
        assert!(line["who"].get("uid").is_some(), "{line}");
        assert!(line["who"].get("sudo_user").is_some(), "{line}");

        // a journal naming more than one target has no command way out (#638 r2 N1)
        let mut mixed = String::new();
        run_op(
            &pl,
            "site.suspend",
            "site:site6",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_300,
            |op| {
                mixed = op.to_owned();
                Ok(())
            },
        )
        .unwrap();
        let path = journal_path(&pl.ops_dir, &mixed).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        let other = text
            .lines()
            .last()
            .unwrap()
            .replace("site:site6", "site:site7");
        fs::write(&path, format!("{text}{{damaged\n{other}\n")).unwrap();
        for t in [None, Some("site:site6"), Some("site:site7")] {
            let why = archive_op(&pl, &mixed, t).unwrap_err();
            assert!(
                why.contains("more than one target") && why.contains("by hand"),
                "{why}"
            );
        }
        assert!(path.exists());
    }

    #[test]
    fn a_damaged_journal_is_archived_and_an_unfinished_one_is_not() {
        let pl = place("archive");
        let noop = |_: &str| -> Result<(), String> { Ok(()) };
        // a finished operation, a failed one, and a journal that cannot be read
        let mut done = String::new();
        run_op(
            &pl,
            "site.suspend",
            "site:site1",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op| {
                done = op.to_owned();
                Ok(())
            },
        )
        .unwrap();
        let mut failed = String::new();
        let _ = run_op(
            &pl,
            "site.restore",
            "site:site2",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_060,
            |op| -> Result<(), String> {
                failed = op.to_owned();
                Err("502".to_owned())
            },
        );
        fs::write(
            pl.ops_dir.join(format!("{OP1}.jsonl")),
            "{not a journal\n{}\n",
        )
        .unwrap();
        // the damaged one holds up an unrelated site
        assert!(run_op(
            &pl,
            "site.resume",
            "site:site3",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_120,
            noop
        )
        .is_err());

        // unfinished: refused, and the journal stays where it is
        let why = archive_op(&pl, &failed, None).unwrap_err();
        assert!(
            why.contains("failed") && why.contains("op recovered"),
            "{why}"
        );
        assert!(journal_path(&pl.ops_dir, &failed).unwrap().exists());
        // damaged and finished: moved below archive/, out of what is read
        // nothing of it is readable: only with a target named by the person
        let why = archive_op(&pl, OP1, None).unwrap_err();
        assert!(
            why.contains("--target") && why.contains("0/2 lines"),
            "{why}"
        );
        assert!(archive_op(&pl, OP1, Some("site:site9"))
            .unwrap()
            .contains("damaged"));
        assert!(archive_op(&pl, &done, None).unwrap().contains("finished"));
        assert!(pl
            .ops_dir
            .join(ARCHIVE_DIR)
            .join(format!("{OP1}.jsonl"))
            .exists());
        assert!(!journal_path(&pl.ops_dir, OP1).unwrap().exists());
        // nothing to archive, and the lock is given back every time
        assert!(archive_op(&pl, OP2, None).is_err());
        assert!(!pl.lock.lock.exists());
        // site3 goes on; site2 is still held by its failed operation
        run_op(
            &pl,
            "site.resume",
            "site:site3",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_180,
            noop,
        )
        .unwrap();
        assert!(run_op(
            &pl,
            "site.resume",
            "site:site2",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_240,
            noop
        )
        .is_err());
    }

    #[test]
    fn a_failed_operation_blocks_its_target_until_recovered() {
        let pl = place("runop-fail");
        let plan = json!({ "command": "site.restore", "site": "site2" });
        let mut failed = String::new();
        let err = run_op(
            &pl,
            "site.restore",
            "site:site2",
            &plan,
            BY_LOCAL_ROOT,
            1_794_300_000,
            |op_id| -> Result<(), String> {
                failed = op_id.to_owned();
                Err("new CT answers 502".to_owned())
            },
        )
        .unwrap_err();
        assert!(err.contains("op recovered"), "{err}");
        assert_eq!(states(&pl, &failed).last(), Some(&State::Failed));
        assert!(
            !pl.lock.lock.exists(),
            "the lock is given back after a failure too"
        );
        // the same site: refused before anything is changed
        let mut ran = false;
        let err = run_op(
            &pl,
            "site.resume",
            "site:site2",
            &plan,
            BY_LOCAL_ROOT,
            1_794_300_060,
            |_| {
                ran = true;
                Ok(())
            },
        )
        .unwrap_err();
        assert!(err.contains(&failed) && !ran, "{err}");
        // another site is not held up
        run_op(
            &pl,
            "site.resume",
            "site:site3",
            &plan,
            BY_LOCAL_ROOT,
            1_794_300_060,
            |_| Ok(()),
        )
        .unwrap();
        // a person checks and closes it: the site is free again
        mark_recovered(&pl, &failed, 1_794_300_120).unwrap();
        assert_eq!(
            states(&pl, &failed),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Failed,
                State::Recovery,
                State::Verified
            ]
        );
        run_op(
            &pl,
            "site.resume",
            "site:site2",
            &plan,
            BY_LOCAL_ROOT,
            1_794_300_180,
            |_| Ok(()),
        )
        .unwrap();
        // a finished operation has nothing to recover
        assert!(mark_recovered(&pl, &failed, 1_794_300_240).is_err());
    }

    #[test]
    fn a_customer_key_approves_only_the_customer_commands() {
        let pl = place("runop-customer");
        let mut ran = false;
        let err = run_op(
            &pl,
            "site.restore",
            "site:site2",
            &json!({}),
            BY_CUSTOMER_KEY,
            1_794_300_000,
            |_| {
                ran = true;
                Ok(())
            },
        )
        .unwrap_err();
        assert!(err.contains("customer key") && !ran, "{err}");
        assert_eq!(fs::read_dir(&pl.ops_dir).unwrap().count(), 0);
        run_op(
            &pl,
            "site.restart",
            "site:site2",
            &json!({}),
            BY_CUSTOMER_KEY,
            1_794_300_000,
            |_| Ok(()),
        )
        .unwrap();
        // standing approvals never look like a nonce
        assert!(BY_LOCAL_ROOT.starts_with("standing-") && BY_CUSTOMER_KEY.starts_with("standing-"));
        // and nothing else is an approval: a near miss is not a way round the allowlist
        for other in [
            "",
            "standing-customer-ke",
            "customer-key",
            "local-root",
            "Standing-Local-Root",
        ] {
            let err = run_op(
                &pl,
                "site.restore",
                "site:site2",
                &json!({}),
                other,
                1_794_300_060,
                |_| {
                    ran = true;
                    Ok(())
                },
            )
            .unwrap_err();
            assert!(
                err.contains("not a known approval") && !ran,
                "{other:?}: {err}"
            );
        }
    }

    #[test]
    fn a_busy_host_changes_nothing_and_writes_no_journal() {
        let pl = place("runop-busy");
        fake_proc(&pl.lock.proc_root, 77, 9000);
        assert_eq!(acquire(&pl.lock, OP1, 77).unwrap(), Got::Taken);
        let mut ran = false;
        let err = run_op(
            &pl,
            "site.suspend",
            "site:site2",
            &json!({}),
            BY_LOCAL_ROOT,
            1_794_300_000,
            |_| {
                ran = true;
                Ok(())
            },
        )
        .unwrap_err();
        assert!(err.contains(OP1) && !ran, "{err}");
        assert_eq!(fs::read_dir(&pl.ops_dir).unwrap().count(), 0);
        // and the other operation's lock is left alone
        assert_eq!(
            holder_from_text(&fs::read_to_string(&pl.lock.lock).unwrap())
                .unwrap()
                .pid,
            77
        );
    }

    #[test]
    fn an_interrupted_operation_is_closed_by_hand_through_failed() {
        let pl = place("runop-interrupted");
        // a journal left at `running` by a process that is gone
        for (seq, state) in [
            (1, State::Planned),
            (2, State::Approved),
            (3, State::Running),
        ] {
            append_entry(
                &pl.ops_dir,
                &Entry {
                    op: OP1.to_owned(),
                    seq,
                    at: 1_794_300_000,
                    state,
                    command: "site.restore".to_owned(),
                    target: "site:site2".to_owned(),
                    plan_sha256: SHA.to_owned(),
                    code: "ok".to_owned(),
                    approval: if seq == 1 {
                        String::new()
                    } else {
                        BY_LOCAL_ROOT.to_owned()
                    },
                },
            )
            .unwrap();
        }
        mark_recovered(&pl, OP1, 1_794_300_100).unwrap();
        let (entries, _) = read_journal(&journal_path(&pl.ops_dir, OP1).unwrap()).unwrap();
        let tail: Vec<(State, &str)> = entries[3..]
            .iter()
            .map(|e| (e.state, e.code.as_str()))
            .collect();
        assert_eq!(
            tail,
            [
                (State::Failed, "interrupted"),
                (State::Recovery, "manual_by_local_root"),
                (State::Verified, "manual_by_local_root")
            ]
        );
        assert!(!pl.lock.lock.exists());
    }
}
