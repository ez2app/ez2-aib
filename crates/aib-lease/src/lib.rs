// SPDX-License-Identifier: Apache-2.0
//! aib-lease: the per-machine run lease, for a holder that runs on **another** machine.
//!
//! The 1.x tool (`aib/tools/governance/host_lease.py`, frozen) records the holder's pid with
//! `host = <this machine>` and checks it with `kill -0` / `ps` here. When the holder is a script on
//! the Mac that reaches the test machine over ssh, that pid means nothing here: the entry is judged
//! dead (or someone else's), the lease can be taken over and the queue entry is dropped while the
//! holder is still waiting (b127; 11-09 K10 dropped out of the andymini queue twice).
//!
//! Here a holder may say `--host <its machine> --pid <its pid> --heartbeat-s <s>`. Its records keep
//! that host, so this machine never probes the pid, and they stay valid only while heartbeats keep
//! arriving: each `heartbeat` pushes `until_epoch` (queue) / `expiry_epoch` (lease) to
//! now + [`HEARTBEAT_MISSES`] × interval. That is the same field the 1.x tool reads by time for a
//! record from another host, so both tools reach the same decision on the same files.
//!
//! Files, lock and field names are the 1.x ones (`AIB_HOST_LEASE_FILE`, default
//! `~/.ez2-aib/state/host-lease.json`, `.queue.json`, `.lock`, `.log`). Added fields:
//! `heartbeat_s`, `heartbeat_epoch`, `lease_host`; `queue_leave` rows also carry `gone_why`.

pub mod run;

use serde_json::{json, Map, Value};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub const SCHEMA: &str = "aib-host-lease/v1";
pub const RC_OK: i32 = 0;
pub const RC_USAGE: i32 = 2;
pub const RC_UNREADABLE: i32 = 3;
pub const RC_HELD: i32 = 4;
/// Same values as host_lease.py.
pub const DEFAULT_TTL_S: i64 = 5400;
pub const SHORT_TTL_FLOOR_S: i64 = 3 * 3600;
pub const QUEUE_TTL_S: i64 = 30 * 60;
pub const QUEUE_NOPID_MAX_S: i64 = 10 * 60;
/// A heartbeat record is valid for this many missed intervals.
pub const HEARTBEAT_MISSES: i64 = 3;
pub const HEARTBEAT_MIN_S: i64 = 10;
pub const HEARTBEAT_MAX_S: i64 = 600;
pub const SHIP_REGISTRY: &str = "aib-ship-running";

pub type Rec = Map<String, Value>;

/// What a process probe saw for a pid on this machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProcState {
    /// No such process.
    Gone,
    /// Running; the start-time text (`ps -o lstart=`, LC_ALL=C), same text host_lease.py stores.
    Started(String),
    /// Could not tell (probe failed).
    Unknown,
}

/// Everything that looks at the machine. Tests pass a fake.
pub trait Probe {
    fn hostname(&self) -> String;
    fn proc_start(&self, pid: i64) -> ProcState;
    fn git_dir(&self, repo: &str) -> Option<PathBuf>;
    /// Local wall-clock text for an epoch, `2026-11-09T22:40:15+08:00` (host_lease.py `iso`).
    fn iso(&self, epoch: i64) -> String;
}

pub struct SysProbe;

fn run_out(cmd: &str, args: &[&str]) -> Option<(bool, String)> {
    let o = Command::new(cmd)
        .args(args)
        .env("LC_ALL", "C")
        .env("LANG", "C")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    Some((
        o.status.success(),
        String::from_utf8_lossy(&o.stdout).trim().to_string(),
    ))
}

impl Probe for SysProbe {
    /// Python `socket.gethostname()` is gethostname(3), which is what `uname -n` prints.
    fn hostname(&self) -> String {
        if let Some((true, h)) = run_out("uname", &["-n"]) {
            if !h.is_empty() {
                return h;
            }
        }
        std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .unwrap_or_else(|_| "unknown".to_string())
    }

    fn proc_start(&self, pid: i64) -> ProcState {
        if pid <= 0 {
            return ProcState::Gone;
        }
        match run_out("ps", &["-o", "lstart=", "-p", &pid.to_string()]) {
            None => ProcState::Unknown,
            Some((_, s)) if !s.is_empty() => ProcState::Started(s),
            Some((false, _)) => ProcState::Gone,
            Some((true, _)) => ProcState::Unknown,
        }
    }

    fn git_dir(&self, repo: &str) -> Option<PathBuf> {
        match run_out("git", &["-C", repo, "rev-parse", "--absolute-git-dir"]) {
            Some((true, s)) if !s.is_empty() => Some(PathBuf::from(s)),
            _ => None,
        }
    }

    fn iso(&self, epoch: i64) -> String {
        let fmt = "+%Y-%m-%dT%H:%M:%S%z";
        let e = epoch.to_string();
        let at = format!("@{epoch}");
        let got = match run_out("date", &["-r", &e, fmt]) {
            Some((true, s)) if !s.is_empty() => Some(s),
            _ => match run_out("date", &["-d", &at, fmt]) {
                Some((true, s)) if !s.is_empty() => Some(s),
                _ => None,
            },
        };
        match got {
            // +0800 -> +08:00 (Python isoformat)
            Some(s) if s.len() > 5 => {
                let (a, b) = s.split_at(s.len() - 2);
                format!("{a}:{b}")
            }
            _ => iso_utc(epoch),
        }
    }
}

/// UTC fallback (`date` missing): civil-from-days, no timezone database needed.
pub fn iso_utc(epoch: i64) -> String {
    let days = epoch.div_euclid(86_400);
    let secs = epoch.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}+00:00",
        secs / 3600,
        (secs / 60) % 60,
        secs % 60
    )
}

// ── paths ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct Paths {
    pub lease: PathBuf,
}

impl Paths {
    /// `AIB_HOST_LEASE_FILE`, else `~/.ez2-aib/state/host-lease.json` (per machine, not per cabin).
    pub fn from_env() -> Paths {
        let p = std::env::var("AIB_HOST_LEASE_FILE").unwrap_or_default();
        let p = p.trim();
        if !p.is_empty() {
            return Paths {
                lease: PathBuf::from(p),
            };
        }
        let home = std::env::var("HOME")
            .or_else(|_| std::env::var("USERPROFILE"))
            .unwrap_or_else(|_| ".".to_string());
        Paths {
            lease: Path::new(&home).join(".ez2-aib/state/host-lease.json"),
        }
    }
    fn with_suffix(&self, suffix: &str) -> PathBuf {
        let mut s = self.lease.clone().into_os_string();
        s.push(suffix);
        PathBuf::from(s)
    }
    pub fn queue(&self) -> PathBuf {
        self.with_suffix(".queue.json")
    }
    pub fn ledger(&self) -> PathBuf {
        self.with_suffix(".log")
    }
    pub fn lock(&self) -> PathBuf {
        self.with_suffix(".lock")
    }
}

// ── small value helpers ───────────────────────────────────────────────────────────────

fn s<'a>(r: &'a Rec, k: &str) -> Option<&'a str> {
    r.get(k).and_then(Value::as_str)
}

fn s_or<'a>(r: &'a Rec, k: &str) -> &'a str {
    s(r, k).unwrap_or("?")
}

/// Python `float(x)`: numbers, or text that parses as one.
fn num(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(t) => t.trim().parse::<f64>().ok(),
        _ => None,
    }
}

/// A pid the 1.x tool would act on (`if not pid` skips 0 / null).
fn pid_of(r: &Rec) -> Option<i64> {
    r.get("pid").and_then(Value::as_i64).filter(|p| *p != 0)
}

fn heartbeat_of(r: &Rec) -> Option<i64> {
    r.get("heartbeat_s")
        .and_then(Value::as_i64)
        .filter(|h| *h > 0)
}

// ── liveness ──────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Live {
    /// Same machine, pid running, start time matches.
    Alive,
    /// Same machine, pid not running.
    Dead,
    /// Same machine, pid running but started at another time (someone else got the number).
    Reused,
    /// Cannot tell here (other host, no pid, no start time): decide by time only.
    Unknown,
}

/// host_lease.py `holder_alive`, with False split into Dead / Reused.
pub fn holder_alive(r: &Rec, p: &dyn Probe) -> Live {
    let Some(pid) = pid_of(r) else {
        return Live::Unknown;
    };
    if s(r, "host") != Some(p.hostname().as_str()) {
        return Live::Unknown;
    }
    match p.proc_start(pid) {
        ProcState::Gone => Live::Dead,
        ProcState::Unknown => Live::Unknown,
        ProcState::Started(now_started) => match s(r, "pid_start").filter(|t| !t.is_empty()) {
            None => Live::Unknown,
            Some(want) if want == now_started => Live::Alive,
            Some(_) => Live::Reused,
        },
    }
}

/// Why a queue entry no longer counts. `HeartbeatStale` is `UntilPassed` for an entry that
/// promised heartbeats (named apart so the log says which promise was broken).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GoneWhy {
    PidDead,
    PidReused,
    UntilPassed,
    HeartbeatStale,
}

impl GoneWhy {
    pub fn as_str(self) -> &'static str {
        match self {
            GoneWhy::PidDead => "pid_dead",
            GoneWhy::PidReused => "pid_reused",
            GoneWhy::UntilPassed => "until_passed",
            GoneWhy::HeartbeatStale => "heartbeat_stale",
        }
    }
}

/// host_lease.py `_queue_entry_live`, returning the reason when it is not.
pub fn queue_entry_state(e: &Rec, now: f64, p: &dyn Probe) -> Result<(), GoneWhy> {
    if pid_of(e).is_some() {
        match holder_alive(e, p) {
            Live::Dead => return Err(GoneWhy::PidDead),
            Live::Reused => return Err(GoneWhy::PidReused),
            Live::Alive | Live::Unknown => {}
        }
    }
    match num(e.get("until_epoch")) {
        Some(until) if now < until => Ok(()),
        _ if heartbeat_of(e).is_some() => Err(GoneWhy::HeartbeatStale),
        _ => Err(GoneWhy::UntilPassed),
    }
}

// ── files ─────────────────────────────────────────────────────────────────────────────

/// Exclusive lock on `<lease>.lock` (flock, the same lock host_lease.py `_mutex` takes).
pub struct Mutex {
    _fh: File,
}

pub fn mutex(paths: &Paths) -> Result<Mutex, String> {
    let lock = paths.lock();
    if let Some(d) = lock.parent() {
        fs::create_dir_all(d).map_err(|e| format!("{}: {e}", lock.display()))?;
    }
    // read + write, not append: Windows LockFileEx needs GENERIC_READ or GENERIC_WRITE,
    // and an append-only handle has neither (CI windows: every lock failed, rc 3)
    let fh = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .truncate(false)
        .open(&lock)
        .map_err(|e| format!("{}: {e}", lock.display()))?;
    fh.lock().map_err(|e| format!("{}: {e}", lock.display()))?;
    Ok(Mutex { _fh: fh })
}

/// `Ok(None)` = no lease (file missing); `Err` = unreadable (fail closed, never "no lease").
pub fn read_lease(paths: &Paths) -> Result<Option<Rec>, String> {
    let p = &paths.lease;
    let text = match fs::read_to_string(p) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("unreadable {}: {e}", p.display())),
    };
    let v: Value =
        serde_json::from_str(&text).map_err(|e| format!("unreadable {}: {e}", p.display()))?;
    match v {
        Value::Object(m) if s(&m, "schema") == Some(SCHEMA) && m.contains_key("expiry_epoch") => {
            Ok(Some(m))
        }
        _ => Err(format!("bad schema {}", p.display())),
    }
}

fn tmp_name(p: &Path, tag: &str) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(format!(".{tag}.{}", std::process::id()));
    PathBuf::from(s)
}

fn write_json(p: &Path, v: &Value) -> Result<(), String> {
    if let Some(d) = p.parent() {
        fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    let tmp = tmp_name(p, "tmp");
    let body = serde_json::to_string_pretty(v).map_err(|e| e.to_string())? + "\n";
    fs::write(&tmp, body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, p).map_err(|e| format!("{}: {e}", p.display()))
}

pub fn write_lease(paths: &Paths, r: &Rec) -> Result<(), String> {
    write_json(&paths.lease, &Value::Object(r.clone()))
}

/// host_lease.py `_queue_read`: unreadable file = empty; hand-broken rows are skipped.
pub fn queue_read(paths: &Paths) -> Vec<Rec> {
    let Ok(text) = fs::read_to_string(paths.queue()) else {
        return vec![];
    };
    let Ok(Value::Array(rows)) = serde_json::from_str::<Value>(&text) else {
        return vec![];
    };
    rows.into_iter()
        .filter_map(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        })
        .filter(|m| s(m, "run_id").is_some())
        .filter(|m| num(m.get("since_epoch")).is_some() && num(m.get("until_epoch")).is_some())
        .filter(|m| {
            matches!(m.get("pid"), None | Some(Value::Null))
                || m.get("pid").and_then(Value::as_i64).is_some()
        })
        .collect()
}

pub fn queue_write(paths: &Paths, rows: &[Rec]) -> Result<(), String> {
    let p = paths.queue();
    if rows.is_empty() {
        return match fs::remove_file(&p) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(format!("{}: {e}", p.display())),
        };
    }
    let arr = Value::Array(rows.iter().cloned().map(Value::Object).collect());
    write_json(&p, &arr)
}

/// One JSON row per event; a broken ledger is reported, never fatal (same as 1.x).
pub fn log_event(paths: &Paths, p: &dyn Probe, now: f64, event: &str, fields: Value) {
    let mut row = Rec::new();
    row.insert("ts".into(), json!(p.iso(now as i64)));
    row.insert("epoch".into(), json!(now as i64));
    row.insert("event".into(), json!(event));
    if let Value::Object(f) = fields {
        row.extend(f);
    }
    let path = paths.ledger();
    let res = path
        .parent()
        .map_or(Ok(()), fs::create_dir_all)
        .and_then(|()| OpenOptions::new().create(true).append(true).open(&path))
        .and_then(|mut fh| writeln!(fh, "{}", Value::Object(row)));
    if let Err(e) = res {
        eprintln!("aib-lease: ledger {}: {e}", path.display());
    }
}

// ── queue ─────────────────────────────────────────────────────────────────────────────

/// Still-waiting entries in order: priority first, then by registration time.
pub fn queue_live(paths: &Paths, now: f64, p: &dyn Probe) -> Vec<Rec> {
    let mut rows: Vec<Rec> = queue_read(paths)
        .into_iter()
        .filter(|e| queue_entry_state(e, now, p).is_ok())
        .collect();
    rows.sort_by(|a, b| {
        let ka = (
            !a.get("priority").and_then(Value::as_bool).unwrap_or(false),
            num(a.get("since_epoch")).unwrap_or(0.0),
        );
        let kb = (
            !b.get("priority").and_then(Value::as_bool).unwrap_or(false),
            num(b.get("since_epoch")).unwrap_or(0.0),
        );
        ka.partial_cmp(&kb).unwrap_or(std::cmp::Ordering::Equal)
    });
    rows
}

/// Entries ahead of `run_id` (not in the queue: everyone is ahead).
pub fn queue_ahead(paths: &Paths, run_id: &str, now: f64, p: &dyn Probe) -> Vec<Rec> {
    let rows = queue_live(paths, now, p);
    match rows.iter().position(|e| s(e, "run_id") == Some(run_id)) {
        Some(i) => rows[..i].to_vec(),
        None => rows,
    }
}

fn waited(e: &Rec, now: f64) -> i64 {
    (now - num(e.get("since_epoch")).unwrap_or(now)) as i64
}

fn log_gone(paths: &Paths, p: &dyn Probe, now: f64, e: &Rec, why: GoneWhy) {
    log_event(
        paths,
        p,
        now,
        "queue_leave",
        json!({"run_id": s(e, "run_id"), "owner": s(e, "owner"), "why": "gone",
               "gone_why": why.as_str(), "holder_host": s(e, "host"), "waited_s": waited(e, now)}),
    );
}

/// host_lease.py `_queue_leave`: drop `run_id`, return how long it waited (None = not queued);
/// drop entries that no longer count, logging *why* each one went.
pub fn queue_leave(paths: &Paths, p: &dyn Probe, run_id: &str, now: f64, why: &str) -> Option<i64> {
    let rows = queue_read(paths);
    let mut keep = Vec::new();
    let mut out = None;
    for e in &rows {
        if s(e, "run_id") == Some(run_id) {
            let w = waited(e, now);
            out = Some(w);
            log_event(
                paths,
                p,
                now,
                "queue_leave",
                json!({"run_id": run_id, "owner": s(e, "owner"), "why": why, "waited_s": w}),
            );
        } else {
            match queue_entry_state(e, now, p) {
                Ok(()) => keep.push(e.clone()),
                Err(g) => log_gone(paths, p, now, e, g),
            }
        }
    }
    if keep.len() != rows.len() {
        if let Err(e) = queue_write(paths, &keep) {
            eprintln!("aib-lease: {e}");
        }
    }
    out
}

// ── holder ────────────────────────────────────────────────────────────────────────────

/// Who holds or waits: owner / what / run id, plus where the holder process runs.
#[derive(Debug, Clone, Default)]
pub struct Holder {
    pub owner: String,
    pub what: String,
    pub run_id: String,
    pub pid: Option<i64>,
    /// The holder's machine; None or equal to this machine = local (pid probed here).
    pub host: Option<String>,
    pub heartbeat_s: Option<i64>,
}

impl Holder {
    fn remote(&self, local: &str) -> bool {
        self.host.as_deref().is_some_and(|h| h != local)
    }
    /// Usage errors that do not need the files.
    pub fn check(&self, local: &str) -> Result<(), String> {
        if self.host.as_deref() == Some("") {
            return Err("--host is empty".into());
        }
        if let Some(h) = self.heartbeat_s {
            if !(HEARTBEAT_MIN_S..=HEARTBEAT_MAX_S).contains(&h) {
                return Err(format!(
                    "--heartbeat-s must be {HEARTBEAT_MIN_S}..={HEARTBEAT_MAX_S}"
                ));
            }
        }
        if self.remote(local) && self.heartbeat_s.is_none() {
            return Err("--host <another machine> needs --heartbeat-s: its pid cannot be checked here, only its heartbeats".into());
        }
        Ok(())
    }
    /// The fields both record kinds share: pid / host / pid_start (+ heartbeat fields).
    fn stamp(&self, r: &mut Rec, local: &str, now: f64, p: &dyn Probe) {
        let remote = self.remote(local);
        r.insert("pid".into(), json!(self.pid));
        r.insert(
            "host".into(),
            json!(if remote {
                self.host.as_deref().unwrap_or(local)
            } else {
                local
            }),
        );
        let start = match (self.pid, remote) {
            (Some(pid), false) => match p.proc_start(pid) {
                ProcState::Started(t) => Some(t),
                _ => None,
            },
            _ => None,
        };
        r.insert("pid_start".into(), json!(start));
        if let Some(h) = self.heartbeat_s {
            r.insert("heartbeat_s".into(), json!(h));
            r.insert("heartbeat_epoch".into(), json!(now as i64));
            r.insert("lease_host".into(), json!(local));
        }
    }
}

// ── lease decisions ───────────────────────────────────────────────────────────────────

/// host_lease.py `live_ships`: live ship registrations under the tree (dead ones are removed).
pub fn live_ships(repo: &str, p: &dyn Probe) -> Vec<Rec> {
    let Some(dir) = p.git_dir(repo).map(|g| g.join(SHIP_REGISTRY)) else {
        return vec![];
    };
    let Ok(rd) = fs::read_dir(&dir) else {
        return vec![];
    };
    let mut files: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|e| e.path())).collect();
    files.sort();
    let local = p.hostname();
    let mut out = vec![];
    for f in files {
        let name = f
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_string();
        let Ok(pid) = name.parse::<i64>() else {
            continue;
        };
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let mut rec = match fs::read_to_string(&f)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        {
            Some(Value::Object(m)) => m,
            _ => Rec::new(),
        };
        rec.insert("pid".into(), json!(pid));
        if s(&rec, "host").is_none() {
            rec.insert("host".into(), json!(local));
        }
        if matches!(holder_alive(&rec, p), Live::Dead | Live::Reused) {
            let _ = fs::remove_file(&f);
            continue;
        }
        out.push(rec);
    }
    out
}

pub fn shipping_under(cur: &Rec, p: &dyn Probe) -> Vec<Rec> {
    match s(cur, "cwd") {
        Some(cwd) if !cwd.is_empty() && s(cur, "host") == Some(p.hostname().as_str()) => {
            live_ships(cwd, p)
        }
        _ => vec![],
    }
}

/// host_lease.py `is_expired`: may this lease be taken over now?
pub fn is_expired(cur: &Rec, now: f64, p: &dyn Probe) -> bool {
    match holder_alive(cur, p) {
        Live::Alive => false,
        Live::Dead | Live::Reused => shipping_under(cur, p).is_empty(),
        Live::Unknown => match num(cur.get("expiry_epoch")) {
            Some(exp) => now >= exp,
            None => true,
        },
    }
}

/// holder_dead / pid_reused / heartbeat_stale / expired (the 1.x three, plus the heartbeat one).
pub fn takeover_why(cur: &Rec, p: &dyn Probe) -> &'static str {
    match holder_alive(cur, p) {
        Live::Dead => "holder_dead",
        Live::Reused => "pid_reused",
        _ if heartbeat_of(cur).is_some() => "heartbeat_stale",
        _ => "expired",
    }
}

// ── commands ──────────────────────────────────────────────────────────────────────────

/// What a command printed (one JSON line) and its exit code.
#[derive(Debug, Clone, PartialEq)]
pub struct Outcome {
    pub rc: i32,
    pub out: Value,
}

fn done(rc: i32, out: Value) -> Outcome {
    Outcome { rc, out }
}

fn unreadable(e: String) -> Outcome {
    done(RC_UNREADABLE, json!({"result": "unreadable", "error": e}))
}

#[derive(Debug, Clone, Default)]
pub struct AcquireArgs {
    pub holder: Holder,
    pub ttl: Option<i64>,
    pub exclusive: bool,
    pub priority: bool,
    pub cwd: Option<String>,
}

fn holder_view(r: &Rec) -> Value {
    json!({"owner": s_or(r, "owner"), "what": s_or(r, "what"), "run_id": s_or(r, "run_id"),
           "since": s_or(r, "acquired_at"), "until": s_or(r, "expiry_at"), "host": s(r, "host")})
}

/// Take the lease (host_lease.py `_cmd_acquire`, plus `--host` / `--heartbeat-s`).
pub fn acquire(paths: &Paths, p: &dyn Probe, now: f64, a: &AcquireArgs) -> Outcome {
    let local = p.hostname();
    if let Err(e) = a.holder.check(&local) {
        return done(RC_USAGE, json!({"result": "usage", "error": e}));
    }
    let ttl = a.ttl.unwrap_or(DEFAULT_TTL_S);
    if ttl <= 0 {
        return done(
            RC_USAGE,
            json!({"result": "usage", "error": "--ttl must be > 0"}),
        );
    }
    let _m = match mutex(paths) {
        Ok(m) => m,
        Err(e) => return unreadable(e),
    };
    let mut cur = match read_lease(paths) {
        Ok(c) => c,
        Err(e) => return unreadable(e),
    };
    let h = &a.holder;
    let mut new = Rec::new();
    new.insert("schema".into(), json!(SCHEMA));
    new.insert("owner".into(), json!(h.owner));
    new.insert("what".into(), json!(h.what));
    new.insert("run_id".into(), json!(h.run_id));
    h.stamp(&mut new, &local, now, p);
    let cwd = a.cwd.clone().or_else(|| {
        std::env::current_dir()
            .ok()
            .map(|d| d.to_string_lossy().into_owned())
    });
    new.insert("cwd".into(), json!(cwd));
    new.insert("acquired_epoch".into(), json!(now as i64));
    new.insert("acquired_at".into(), json!(p.iso(now as i64)));
    let expiry = match h.heartbeat_s {
        Some(hb) => now as i64 + HEARTBEAT_MISSES * hb,
        // 1.x: a pid whose start time cannot be read is checked by time only ⇒ at least 3 h
        None if h.pid.is_some()
            && new.get("pid_start").is_some_and(Value::is_null)
            && ttl < SHORT_TTL_FLOOR_S =>
        {
            now as i64 + SHORT_TTL_FLOOR_S
        }
        None => now as i64 + ttl,
    };
    new.insert("expiry_epoch".into(), json!(expiry));
    new.insert("expiry_at".into(), json!(p.iso(expiry)));
    if a.exclusive {
        new.insert("exclusive".into(), json!(true));
    }
    let same = |c: &Rec| s(c, "run_id") == Some(h.run_id.as_str());
    let free_now = match &cur {
        None => true,
        Some(c) => !same(c) && is_expired(c, now, p),
    };
    if free_now && !a.priority {
        let ahead = queue_ahead(paths, &h.run_id, now, p);
        if let Some(head) = ahead.first() {
            log_event(
                paths,
                p,
                now,
                "denied",
                json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "holder": null,
                       "queue_ahead": ahead.len(), "queue_head": s(head, "run_id")}),
            );
            return done(
                RC_HELD,
                json!({"result": "queue_behind", "ahead": ahead.len(), "head": s(head, "run_id"),
                       "head_owner": s_or(head, "owner"), "head_what": s_or(head, "what")}),
            );
        }
    }
    if cur.is_none() {
        if let Some(d) = paths.lease.parent() {
            if let Err(e) = fs::create_dir_all(d) {
                return unreadable(format!("{}: {e}", d.display()));
            }
        }
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&paths.lease)
        {
            Ok(mut fh) => {
                let body = serde_json::to_string_pretty(&Value::Object(new.clone()))
                    .unwrap_or_default()
                    + "\n";
                if let Err(e) = fh.write_all(body.as_bytes()) {
                    return unreadable(format!("{}: {e}", paths.lease.display()));
                }
                let w = queue_leave(paths, p, &h.run_id, now, "acquired");
                log_event(
                    paths,
                    p,
                    now,
                    "acquire",
                    json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "expiry": new["expiry_at"],
                           "exclusive": a.exclusive, "waited_s": w, "holder_host": new["host"],
                           "heartbeat_s": h.heartbeat_s}),
                );
                return done(
                    RC_OK,
                    json!({"result": "acquired", "run_id": h.run_id, "until": new["expiry_at"]}),
                );
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => match read_lease(paths) {
                Err(e) => return unreadable(e),
                Ok(None) => return done(RC_HELD, json!({"result": "race"})),
                Ok(Some(c)) => cur = Some(c),
            },
            Err(e) => return unreadable(format!("{}: {e}", paths.lease.display())),
        }
    }
    let Some(cur) = cur else {
        return done(RC_HELD, json!({"result": "race"}));
    };
    if same(&cur) {
        if let Some(v) = cur.get("acquired_epoch") {
            new.insert("acquired_epoch".into(), v.clone());
        }
        if let Some(v) = cur.get("acquired_at") {
            new.insert("acquired_at".into(), v.clone());
        }
        if let Err(e) = write_lease(paths, &new) {
            return unreadable(e);
        }
        log_event(
            paths,
            p,
            now,
            "reenter",
            json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "expiry": new["expiry_at"]}),
        );
        return done(
            RC_OK,
            json!({"result": "reentered", "run_id": h.run_id, "until": new["expiry_at"]}),
        );
    }
    if is_expired(&cur, now, p) {
        let why = takeover_why(&cur, p);
        if let Err(e) = write_lease(paths, &new) {
            return unreadable(e);
        }
        let w = queue_leave(paths, p, &h.run_id, now, "acquired");
        log_event(
            paths,
            p,
            now,
            "takeover",
            json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "exclusive": a.exclusive,
                   "waited_s": w, "prev_owner": s(&cur, "owner"), "prev_what": s(&cur, "what"),
                   "prev_run_id": s(&cur, "run_id"), "prev_expiry": s(&cur, "expiry_at"),
                   "prev_holder_host": s(&cur, "host"), "why": why}),
        );
        return done(
            RC_OK,
            json!({"result": "took_over", "why": why, "run_id": h.run_id, "until": new["expiry_at"],
                   "prev": holder_view(&cur)}),
        );
    }
    let mut out = json!({"result": "busy", "holder": holder_view(&cur)});
    if matches!(holder_alive(&cur, p), Live::Dead | Live::Reused) {
        let pids: Vec<Value> = shipping_under(&cur, p)
            .iter()
            .map(|r| r.get("pid").cloned().unwrap_or(Value::Null))
            .collect();
        out["shipping_pids"] = Value::Array(pids);
    }
    log_event(
        paths,
        p,
        now,
        "denied",
        json!({"owner": h.owner, "what": h.what, "run_id": h.run_id, "holder": s(&cur, "owner"),
               "holder_what": s(&cur, "what"), "holder_since": s(&cur, "acquired_at")}),
    );
    done(RC_HELD, out)
}

#[derive(Debug, Clone, Default)]
pub struct QueueAddArgs {
    pub holder: Holder,
    pub ttl: Option<i64>,
    pub notify: Option<String>,
    pub priority: bool,
}

/// Register in the queue (host_lease.py `queue add`, plus `--host` / `--heartbeat-s`).
pub fn queue_add(paths: &Paths, p: &dyn Probe, now: f64, a: &QueueAddArgs) -> Outcome {
    let local = p.hostname();
    let h = &a.holder;
    if let Err(e) = h.check(&local) {
        return done(RC_USAGE, json!({"result": "usage", "error": e}));
    }
    let ttl = a.ttl.unwrap_or(QUEUE_TTL_S);
    if ttl <= 0 {
        return done(
            RC_USAGE,
            json!({"result": "usage", "error": "--ttl must be > 0"}),
        );
    }
    let _m = match mutex(paths) {
        Ok(m) => m,
        Err(e) => return unreadable(e),
    };
    let mut rows = Vec::new();
    for e in queue_read(paths) {
        match queue_entry_state(&e, now, p) {
            Ok(()) => rows.push(e),
            Err(g) => log_gone(paths, p, now, &e, g),
        }
    }
    let old = rows
        .iter()
        .find(|e| s(e, "run_id") == Some(h.run_id.as_str()))
        .cloned();
    let mut e = Rec::new();
    e.insert("run_id".into(), json!(h.run_id));
    e.insert("owner".into(), json!(h.owner));
    e.insert("what".into(), json!(h.what));
    h.stamp(&mut e, &local, now, p);
    e.insert(
        "notify".into(),
        json!(a.notify.as_deref().filter(|n| !n.is_empty())),
    );
    e.insert("priority".into(), json!(a.priority));
    let since = old
        .as_ref()
        .and_then(|o| num(o.get("since_epoch")))
        .filter(|v| *v != 0.0)
        .map_or(now as i64, |v| v as i64);
    e.insert("since_epoch".into(), json!(since));
    let until = match h.heartbeat_s {
        Some(hb) => now as i64 + HEARTBEAT_MISSES * hb,
        None if h.pid.is_some() && !h.remote(&local) => now as i64 + ttl,
        None => now as i64 + ttl.min(QUEUE_NOPID_MAX_S),
    };
    e.insert("until_epoch".into(), json!(until));
    rows.retain(|x| s(x, "run_id") != Some(h.run_id.as_str()));
    rows.push(e);
    if let Err(err) = queue_write(paths, &rows) {
        return unreadable(err);
    }
    if old.is_none() {
        log_event(
            paths,
            p,
            now,
            "queue_add",
            json!({"run_id": h.run_id, "owner": h.owner, "what": h.what, "priority": a.priority,
                   "holder_host": h.host, "heartbeat_s": h.heartbeat_s}),
        );
    }
    let live = queue_live(paths, now, p);
    match live
        .iter()
        .position(|x| s(x, "run_id") == Some(h.run_id.as_str()))
    {
        Some(i) => done(
            RC_OK,
            json!({"result": "queued", "run_id": h.run_id, "position": i + 1, "until": until}),
        ),
        // the pid given is already gone: the entry does not count; say so instead of pretending
        None => done(
            RC_USAGE,
            json!({"result": "not_in_queue", "run_id": h.run_id}),
        ),
    }
}

/// `queue list`: live entries in order; with `all`, also the entries that no longer count and why
/// (read only: nothing is removed).
pub fn queue_list(paths: &Paths, p: &dyn Probe, now: f64, all: bool) -> Outcome {
    let live: Vec<Value> = queue_live(paths, now, p)
        .into_iter()
        .map(|e| {
            let w = waited(&e, now);
            let mut v = Value::Object(e);
            v["waited_s"] = json!(w);
            v
        })
        .collect();
    if !all {
        return done(RC_OK, json!({"live": live}));
    }
    let gone: Vec<Value> = queue_read(paths)
        .into_iter()
        .filter_map(|e| {
            queue_entry_state(&e, now, p).err().map(|g| {
                json!({"run_id": s(&e, "run_id"), "owner": s(&e, "owner"), "what": s(&e, "what"),
                       "host": s(&e, "host"), "gone_why": g.as_str()})
            })
        })
        .collect();
    done(RC_OK, json!({"live": live, "gone": gone}))
}

/// `heartbeat --run-id`: the holder is still there. Pushes the deadline of its queue entry and/or
/// lease (only records that were created with `--heartbeat-s`). A record whose deadline already
/// passed is not revived: rc 4 `not_found` tells the holder it lost its place.
pub fn heartbeat(paths: &Paths, p: &dyn Probe, now: f64, run_id: &str) -> Outcome {
    let _m = match mutex(paths) {
        Ok(m) => m,
        Err(e) => return unreadable(e),
    };
    let mut hit_lease = Value::Null;
    match read_lease(paths) {
        Err(e) => return unreadable(e),
        Ok(Some(mut cur)) if s(&cur, "run_id") == Some(run_id) => {
            if let Some(hb) = heartbeat_of(&cur) {
                if num(cur.get("expiry_epoch")).is_some_and(|x| now < x) {
                    let exp = now as i64 + HEARTBEAT_MISSES * hb;
                    cur.insert("heartbeat_epoch".into(), json!(now as i64));
                    cur.insert("expiry_epoch".into(), json!(exp));
                    cur.insert("expiry_at".into(), json!(p.iso(exp)));
                    if let Err(e) = write_lease(paths, &cur) {
                        return unreadable(e);
                    }
                    hit_lease = json!(exp);
                }
            }
        }
        Ok(_) => {}
    }
    let mut rows = queue_read(paths);
    let mut hit_queue = Value::Null;
    let mut changed = false;
    let mut i = 0;
    while i < rows.len() {
        if s(&rows[i], "run_id") != Some(run_id) {
            i += 1;
            continue;
        }
        let state = queue_entry_state(&rows[i], now, p);
        match (heartbeat_of(&rows[i]), state) {
            (Some(hb), Ok(())) => {
                let until = now as i64 + HEARTBEAT_MISSES * hb;
                rows[i].insert("heartbeat_epoch".into(), json!(now as i64));
                rows[i].insert("until_epoch".into(), json!(until));
                hit_queue = json!(until);
                changed = true;
                i += 1;
            }
            (_, Err(g)) => {
                log_gone(paths, p, now, &rows[i], g);
                rows.remove(i);
                changed = true;
            }
            (None, Ok(())) => i += 1,
        }
    }
    if changed {
        if let Err(e) = queue_write(paths, &rows) {
            return unreadable(e);
        }
    }
    if hit_lease.is_null() && hit_queue.is_null() {
        return done(RC_HELD, json!({"result": "not_found", "run_id": run_id}));
    }
    done(
        RC_OK,
        json!({"result": "beat", "run_id": run_id, "lease_until": hit_lease, "queue_until": hit_queue}),
    )
}
