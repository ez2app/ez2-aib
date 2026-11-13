// SPDX-License-Identifier: Apache-2.0
//! The consent queue (B2 T136-2, PLAN-t136-consent-rust-c477a-1113 §2 slice 2).
//!
//! Port of the frozen 1.x `aib/src/cockpit/consent.py`: the folders (:181–205), the done
//! record (:687 `_record`), the bad folder (:696 `_quarantine`), the running side files
//! (:818–825, :942), the inbox pick (:962 `_next_inbox`), the orphan sweep (:994
//! `_sweep_running`) and the loop that claims one request at a time (:1022 `run_once`).
//!
//! Claiming is the 1.x one, so a 1.x handler and this one can never both take a request:
//! the machine-wide `locks/touchid-approve.lock` (`flock`, never waits, 1.x
//! `touchid_approve.one_at_a_time`) is held while a request is moved from `inbox/` to
//! `running/` and decided; the move is a `rename`, and whoever's rename fails skips it.
//!
//! What to decide and how to run is not here: the loop calls a [`Handler`] (T136-3 decide,
//! T136-4 execute). Nothing in this file asks the person.
//!
//! Known differences from 1.x:
//! - A done record's top-level keys keep the 1.x order; objects nested in the detail are
//!   written with sorted keys (serde_json map).
//! - 1.x lets an `os.stat` error after the claim escape `run_once` (the request stays in
//!   `running/` until the sweep calls it an orphan); here [`run_once`] returns that error
//!   the same way, without handling the request.
//! - Names that are not UTF-8 are compared as bytes when two requests have the same mtime;
//!   1.x compares the surrogate-escaped strings. Such names are bad names either way.

use crate::request::{
    id_ok, read_json, token_hex, write_atomic, EXPIRE_S, FRESH_S, MAX_REQ_BYTES, RUN_TIMEOUT_S,
};
use nix::fcntl::{Flock, FlockArg};
use serde::ser::{Serialize, SerializeMap, Serializer};
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// While the lock is held by someone else, try again this often (1.x `LOCK_RETRY_STEP`).
pub const LOCK_RETRY_STEP: u64 = 5;
/// …for at most this long, then stop for this round (1.x `LOCK_RETRY_S`).
pub const LOCK_RETRY_S: u64 = 300;
/// How long a dialog waits for the person (1.x `touchid_approve.WAIT_S`).
pub const WAIT_S: u64 = 60;
/// A request older than this still in `running/` is an orphan (1.x `_sweep_running`:
/// expiry + run limit + five dialog waits).
pub const ORPHAN_S: u64 = EXPIRE_S + RUN_TIMEOUT_S + WAIT_S * 5;
/// A request may say it was written up to this many seconds in the future (1.x `decide`).
pub const SKEW_S: u64 = 60;

/// The extra fields of a done record, in the order they are written.
pub type Detail = Vec<(String, Value)>;

/// A JSON object written in the order given (Python `dict` insertion order).
pub struct Ordered(pub Vec<(String, Value)>);

impl Serialize for Ordered {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(self.0.len()))?;
        for (k, v) in &self.0 {
            m.serialize_entry(k, v)?;
        }
        m.end()
    }
}

impl Ordered {
    /// Python `d[k] = v`: replace in place if the key is there, else append.
    pub fn set(&mut self, k: &str, v: Value) {
        match self.0.iter_mut().find(|(key, _)| key == k) {
            Some(slot) => slot.1 = v,
            None => self.0.push((k.to_owned(), v)),
        }
    }
}

/// The consent folders under one state root (1.x `consent_dir` and friends).
#[derive(Clone, Debug)]
pub struct Queue {
    root: PathBuf,
}

impl Queue {
    /// `state_root` is what 1.x `state_root()` resolves (`EZ2_AIB_STATE_ROOT` or the
    /// seats-dir state root); resolving it is the entry point's job (T136-5).
    pub fn new(state_root: &Path) -> Self {
        Queue {
            root: state_root.to_path_buf(),
        }
    }
    pub fn consent(&self) -> PathBuf {
        self.root.join("consent")
    }
    pub fn inbox(&self) -> PathBuf {
        self.consent().join("inbox")
    }
    pub fn running(&self) -> PathBuf {
        self.consent().join("running")
    }
    pub fn done(&self) -> PathBuf {
        self.consent().join("done")
    }
    pub fn bad(&self) -> PathBuf {
        self.consent().join("bad")
    }
    /// The machine-wide Touch ID lock every 1.x approver shares.
    pub fn lock_path(&self) -> PathBuf {
        self.root.join("locks").join("touchid-approve.lock")
    }
    /// `running/<id>.state.json` (1.x `_state_path`).
    pub fn state_path(&self, rid: &str) -> PathBuf {
        self.running().join(format!("{rid}.state.json"))
    }
    /// `running/<id>.stop`: a stop request (1.x `_stop_path`).
    pub fn stop_path(&self, rid: &str) -> PathBuf {
        self.running().join(format!("{rid}.stop"))
    }
    /// 1.x `_cleanup_running`: remove the state and stop files, ignore errors.
    pub fn cleanup_running(&self, rid: &str) {
        let _ = fs::remove_file(self.state_path(rid));
        let _ = fs::remove_file(self.stop_path(rid));
    }
}

/// Seconds since the epoch as Python `time.time()`.
pub fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn mtime(m: &fs::Metadata) -> f64 {
    m.mtime() as f64 + m.mtime_nsec() as f64 / 1e9
}

/// The machine-wide lock, held until dropped (1.x `touchid_approve.one_at_a_time`).
pub struct Gate(Option<Flock<File>>);

impl Gate {
    /// Never waits. Any failure (held elsewhere, cannot create, cannot open) is "not held".
    pub fn try_lock(path: &Path) -> Gate {
        if let Some(dir) = path.parent() {
            if fs::create_dir_all(dir).is_err() {
                return Gate(None);
            }
        }
        let Ok(f) = OpenOptions::new().append(true).create(true).open(path) else {
            return Gate(None);
        };
        Gate(Flock::lock(f, FlockArg::LockExclusiveNonblock).ok())
    }
    pub fn held(&self) -> bool {
        self.0.is_some()
    }
}

/// 1.x `_record`: write `done/<id>.json` and return what was written. Fields come from
/// `req` (missing ⇒ `null`); `extra` is merged last, a repeated key keeping its place.
pub fn record(
    q: &Queue,
    rid: &str,
    result: &str,
    req: Option<&Map<String, Value>>,
    extra: &[(String, Value)],
    now: f64,
) -> io::Result<Ordered> {
    let get = |k: &str| req.and_then(|r| r.get(k)).cloned().unwrap_or(Value::Null);
    let mut d = Ordered(vec![
        ("v".into(), Value::from(1)),
        ("id".into(), Value::from(rid)),
        ("result".into(), Value::from(result)),
        ("argv".into(), get("argv")),
        ("summary".into(), get("summary")),
        ("seat".into(), get("seat")),
        ("strong".into(), get("strong")),
        ("decided_at".into(), Value::from(now.floor() as i64)),
    ]);
    for (k, v) in extra {
        d.set(k, v.clone());
    }
    write_atomic(&q.done().join(format!("{rid}.json")), &d)?;
    Ok(d)
}

/// 1.x `_quarantine`: move `path` to `bad/<secs>-<6 hex>-<why>`; if that fails, try to
/// remove it. Never fails.
pub fn quarantine(q: &Queue, path: &Path, why: &str, now: f64) {
    let hex = token_hex(3).unwrap_or_else(|_| "000000".into());
    let dst = q.bad().join(format!("{}-{hex}-{why}", now.floor() as i64));
    let moved = fs::create_dir_all(q.bad()).and_then(|()| fs::rename(path, &dst));
    if moved.is_err() {
        let _ = fs::remove_file(path);
    }
}

/// What [`next_inbox`] found.
#[derive(Debug, PartialEq, Eq)]
pub enum Next {
    /// Nothing to do (also: no inbox folder).
    Empty,
    /// Only files written in the last [`FRESH_S`] seconds: look again shortly.
    OnlyFresh,
    /// The oldest file has a bad name or is not a regular file.
    Bad(PathBuf),
    /// The oldest request: its id and path.
    Item(String, PathBuf),
}

/// 1.x `_next_inbox`: the oldest file in `inbox/` by (mtime, name), skipping dot files,
/// paths in `skip` and files written in the last [`FRESH_S`] seconds (a future mtime is not
/// "fresh", or it would wait forever).
pub fn next_inbox(q: &Queue, skip: &HashSet<PathBuf>, now: f64) -> Next {
    let Ok(rd) = fs::read_dir(q.inbox()) else {
        return Next::Empty;
    };
    let mut names: Vec<_> = rd.filter_map(|e| e.ok().map(|e| e.file_name())).collect();
    names.sort();
    let mut best: Option<(f64, std::ffi::OsString, Option<String>, PathBuf)> = None;
    let mut fresh = false;
    for name in names {
        if name.as_encoded_bytes().first() == Some(&b'.') {
            continue;
        }
        let p = q.inbox().join(&name);
        if skip.contains(&p) {
            continue;
        }
        let Ok(st) = fs::symlink_metadata(&p) else {
            continue;
        };
        let mt = mtime(&st);
        let age = now - mt;
        if (0.0..FRESH_S as f64).contains(&age) {
            fresh = true;
            continue;
        }
        let rid = name
            .to_str()
            .and_then(|n| n.strip_suffix(".json"))
            .filter(|r| id_ok(r) && st.file_type().is_file())
            .map(str::to_owned);
        let better = match &best {
            None => true,
            Some((bm, bn, _, _)) => mt < *bm || (mt == *bm && name < *bn),
        };
        if better {
            best = Some((mt, name, rid, p));
        }
    }
    match best {
        None if fresh => Next::OnlyFresh,
        None => Next::Empty,
        Some((_, _, Some(rid), p)) => Next::Item(rid, p),
        Some((_, _, None, p)) => Next::Bad(p),
    }
}

/// Python `type(v) is int` (a JSON bool is not an int).
fn json_int(v: Option<&Value>) -> Option<f64> {
    crate::request::t_int(v)
}

/// 1.x `_sweep_running`: anything in `running/` whose `t` (or mtime) is more than
/// [`SKEW_S`] ahead or [`ORPHAN_S`] behind `now` is moved to `bad/` (`orphan`); if it is a
/// request with a good id and no done record yet, `failed` / `orphan_running` is recorded.
pub fn sweep_running(q: &Queue, now: f64) -> io::Result<()> {
    let Ok(rd) = fs::read_dir(q.running()) else {
        return Ok(());
    };
    for e in rd.flatten() {
        let name = e.file_name();
        let p = q.running().join(&name);
        let rid = name
            .to_str()
            .and_then(|n| n.strip_suffix(".json"))
            .unwrap_or("");
        let req = read_json(&p, MAX_REQ_BYTES).unwrap_or_default();
        let t = match json_int(req.get("t")) {
            Some(t) => t,
            None => match fs::symlink_metadata(&p) {
                Ok(m) => m.mtime() as f64,
                Err(_) => continue,
            },
        };
        let age = now - t;
        if (-(SKEW_S as f64)..=ORPHAN_S as f64).contains(&age) {
            continue;
        }
        if id_ok(rid) && !q.done().join(format!("{rid}.json")).exists() {
            let why = [("why".to_owned(), Value::from("orphan_running"))];
            record(q, rid, "failed", Some(&req), &why, now)?;
        }
        quarantine(q, &p, "orphan", now);
    }
    Ok(())
}

/// What the handler decided for one request.
pub enum Decision<R> {
    /// Finished without running: write this result and detail to `done/`.
    Record(String, Detail),
    /// Approved: run it, with this detail for the record.
    Execute(Detail, R),
}

/// Deciding and running, supplied by T136-3 / T136-4 (and fakes in tests).
pub trait Handler {
    /// What an approved request carries to [`Handler::execute`] (1.x `real, td`); dropped
    /// after the run, which is where its temporary folder goes.
    type Run;
    /// Called with the machine-wide lock held; `req` already has `id` and `t`. An `Err`
    /// is recorded as `failed` / `decide_error:<the string>` (1.x the exception class).
    fn decide(&mut self, req: &mut Map<String, Value>) -> Result<Decision<Self::Run>, String>;
    /// Called after the lock is released; writes its own done record.
    fn execute(
        &mut self,
        q: &Queue,
        rid: &str,
        req: &Map<String, Value>,
        run: Self::Run,
        extra: Detail,
    ) -> io::Result<()>;
}

/// Time for [`run_once`]: real in production, scripted in tests.
pub trait Clock {
    fn now(&self) -> f64;
    fn sleep(&mut self, secs: u64);
}

/// The real clock.
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> f64 {
        unix_now()
    }
    // ADR 0005 named exception (like `supervise::pause`): the 1.x run-once waits for the
    // shared Touch ID lock (5 s steps) and for a just-written request (2 s) in place; it
    // runs as a one-shot LaunchAgent job, not inside aibd, so there is no timer to use.
    #[allow(clippy::disallowed_methods)]
    fn sleep(&mut self, secs: u64) {
        std::thread::sleep(std::time::Duration::from_secs(secs));
    }
}

/// 1.x `run_once`: sweep orphans, then handle the inbox until it is empty. For each
/// request: take the lock (held elsewhere ⇒ retry every [`LOCK_RETRY_STEP`] s for at most
/// `retry_s` s) → move it to `running/` (a failed move means someone else took it: skip
/// it this round) → decide → release the lock → record or run → remove it from
/// `running/`. Returns how many it handled.
pub fn run_once<H: Handler, C: Clock>(
    q: &Queue,
    h: &mut H,
    clock: &mut C,
    retry_s: u64,
) -> io::Result<usize> {
    let (mut n, mut waited, mut fresh_waits) = (0usize, 0u64, 0u32);
    let mut skip = HashSet::new();
    sweep_running(q, clock.now())?;
    loop {
        let (rid, path) = match next_inbox(q, &skip, clock.now()) {
            Next::Empty => return Ok(n),
            Next::OnlyFresh => {
                if fresh_waits >= 3 {
                    return Ok(n);
                }
                fresh_waits += 1;
                clock.sleep(FRESH_S);
                continue;
            }
            Next::Bad(p) => {
                quarantine(q, &p, "badname", clock.now());
                if fs::symlink_metadata(&p).is_ok() {
                    skip.insert(p);
                }
                n += 1;
                continue;
            }
            Next::Item(rid, p) => (rid, p),
        };
        let gate = Gate::try_lock(&q.lock_path());
        if !gate.held() {
            drop(gate);
            if waited >= retry_s {
                return Ok(n);
            }
            clock.sleep(LOCK_RETRY_STEP);
            waited += LOCK_RETRY_STEP;
            continue;
        }
        waited = 0;
        let mut rid = rid;
        // running before done (a9136 T136-2 L1): a handler finishing outside the lock writes
        // done and then removes running, so this order cannot see neither
        if q.running().join(format!("{rid}.json")).exists()
            || q.done().join(format!("{rid}.json")).exists()
        {
            // Same name sent again: a new id, the old record is never overwritten.
            let cut: String = rid.chars().take(50).collect();
            rid = format!("{cut}-{}", token_hex(3)?);
        }
        let mine = q.running().join(format!("{rid}.json"));
        if fs::create_dir_all(q.running())
            .and_then(|()| fs::rename(&path, &mine))
            .is_err()
        {
            skip.insert(path);
            continue;
        }
        let Some(mut req) = read_json(&mine, MAX_REQ_BYTES) else {
            drop(gate);
            let why = [("why".to_owned(), Value::from("unreadable"))];
            record(q, &rid, "invalid", None, &why, clock.now())?;
            quarantine(q, &mine, "unreadable", clock.now());
            n += 1;
            continue;
        };
        if json_int(req.get("t")).is_none() {
            // A hand-written request without `t`: use the file time.
            let t = fs::metadata(&mine)?.mtime();
            req.insert("t".into(), Value::from(t));
        }
        req.insert("id".into(), Value::from(rid.as_str()));
        let decided = h.decide(&mut req);
        drop(gate);
        let res = match decided {
            Ok(Decision::Record(result, detail)) => {
                record(q, &rid, &result, Some(&req), &detail, clock.now()).map(drop)
            }
            Err(class) => {
                let why = [(
                    "why".to_owned(),
                    Value::from(format!("decide_error:{class}")),
                )];
                record(q, &rid, "failed", Some(&req), &why, clock.now()).map(drop)
            }
            Ok(Decision::Execute(detail, run)) => h.execute(q, &rid, &req, run, detail),
        };
        let _ = fs::remove_file(&mine);
        res?;
        n += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::os::unix::fs::symlink;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("aibc-q-{}", token_hex(6).unwrap()));
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn put(p: &Path, body: &str, mtime: f64) {
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, body).unwrap();
        let t = UNIX_EPOCH + Duration::from_secs_f64(mtime);
        File::options()
            .write(true)
            .open(p)
            .unwrap()
            .set_modified(t)
            .unwrap();
    }

    const NOW: f64 = 1_800_000_000.5;

    #[test]
    fn next_inbox_empty_missing_fresh_and_dot() {
        let q = Queue::new(&tmp());
        let none = HashSet::new();
        assert_eq!(next_inbox(&q, &none, NOW), Next::Empty);
        put(&q.inbox().join(".hidden.json"), "{}", NOW - 100.0);
        assert_eq!(next_inbox(&q, &none, NOW), Next::Empty);
        put(&q.inbox().join("a.json"), "{}", NOW - 1.0);
        assert_eq!(next_inbox(&q, &none, NOW), Next::OnlyFresh);
    }

    #[test]
    fn next_inbox_oldest_then_name_and_future_is_not_fresh() {
        let q = Queue::new(&tmp());
        let none = HashSet::new();
        put(&q.inbox().join("b.json"), "{}", NOW - 50.0);
        put(&q.inbox().join("c.json"), "{}", NOW - 50.0);
        put(&q.inbox().join("z.json"), "{}", NOW - 10.0);
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Item("b".into(), q.inbox().join("b.json"))
        );
        let skip: HashSet<_> = [q.inbox().join("b.json")].into();
        assert_eq!(
            next_inbox(&q, &skip, NOW),
            Next::Item("c".into(), q.inbox().join("c.json"))
        );
        put(&q.inbox().join("f.json"), "{}", NOW + 3600.0);
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Item("b".into(), q.inbox().join("b.json"))
        );
        put(&q.inbox().join("old.json"), "{}", NOW - 90.0);
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Item("old".into(), q.inbox().join("old.json"))
        );
    }

    #[test]
    fn next_inbox_bad_names_and_symlinks() {
        let q = Queue::new(&tmp());
        let none = HashSet::new();
        put(&q.inbox().join("a b.json"), "{}", NOW - 30.0);
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Bad(q.inbox().join("a b.json"))
        );
        let q = Queue::new(&tmp());
        put(&q.inbox().join("x.txt"), "{}", NOW - 30.0);
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Bad(q.inbox().join("x.txt"))
        );
        let q = Queue::new(&tmp());
        let real = q.consent().join("real.json");
        put(&real, "{}", NOW - 30.0);
        fs::create_dir_all(q.inbox()).unwrap();
        symlink(&real, q.inbox().join("l.json")).unwrap();
        assert_eq!(
            next_inbox(&q, &none, NOW),
            Next::Bad(q.inbox().join("l.json"))
        );
    }

    #[test]
    fn record_keeps_1x_key_order_and_bytes() {
        let q = Queue::new(&tmp());
        let req: Map<String, Value> = serde_json::from_value(json!({
            "strong": true, "argv": ["aib", "now"], "seat": "c477a", "summary": "看一下", "t": 5
        }))
        .unwrap();
        let extra = vec![
            ("why".to_owned(), json!("x")),
            ("result".to_owned(), json!("denied")),
        ];
        record(&q, "r1", "ok", Some(&req), &extra, NOW).unwrap();
        // What 1.x json.dump(indent=1, ensure_ascii=False) writes for the same dict.
        let want = "{\n \"v\": 1,\n \"id\": \"r1\",\n \"result\": \"denied\",\n \"argv\": [\n  \"aib\",\n  \"now\"\n ],\n \"summary\": \"看一下\",\n \"seat\": \"c477a\",\n \"strong\": true,\n \"decided_at\": 1800000000,\n \"why\": \"x\"\n}";
        assert_eq!(fs::read_to_string(q.done().join("r1.json")).unwrap(), want);
        record(&q, "r2", "invalid", None, &[], NOW).unwrap();
        let v: Value =
            serde_json::from_slice(&fs::read(q.done().join("r2.json")).unwrap()).unwrap();
        assert_eq!(v["argv"], Value::Null);
        assert_eq!(v["strong"], Value::Null);
    }

    #[test]
    fn quarantine_moves_with_1x_name() {
        let q = Queue::new(&tmp());
        let p = q.inbox().join("a.json");
        put(&p, "{}", NOW);
        quarantine(&q, &p, "badname", NOW);
        assert!(!p.exists());
        let names: Vec<String> = fs::read_dir(q.bad())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        assert_eq!(names.len(), 1);
        let parts: Vec<&str> = names[0].splitn(3, '-').collect();
        assert_eq!(parts[0], "1800000000");
        assert!(parts[1].len() == 6 && parts[1].bytes().all(|b| b.is_ascii_hexdigit()));
        assert_eq!(parts[2], "badname");
    }

    #[test]
    fn sweep_running_orphans_only_and_never_overwrites_done() {
        let q = Queue::new(&tmp());
        let old = NOW - ORPHAN_S as f64 - 10.0;
        // t in the file wins over the mtime
        put(&q.running().join("keep.json"), r#"{"t": 1800000000}"#, old);
        put(
            &q.running().join("gone.json"),
            r#"{"t": 1000, "argv": ["aib","now"]}"#,
            NOW,
        );
        put(&q.running().join("gone.state.json"), "{}", old);
        put(&q.running().join("ahead.json"), r#"{"t": 1800000100}"#, NOW);
        put(&q.running().join("edge.json"), r#"{"t": 1799999941}"#, NOW); // 59.5 s ahead
                                                                          // `t: true` is not an int: falls back to the mtime
        put(&q.running().join("boolt.json"), r#"{"t": true}"#, old);
        put(&q.running().join("had.json"), "{}", old);
        put(&q.done().join("had.json"), r#"{"result":"ok"}"#, old);
        sweep_running(&q, NOW).unwrap();
        let left: HashSet<String> = fs::read_dir(q.running())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        assert_eq!(
            left,
            ["keep.json", "edge.json"]
                .iter()
                .map(|s| s.to_string())
                .collect()
        );
        let gone: Value =
            serde_json::from_slice(&fs::read(q.done().join("gone.json")).unwrap()).unwrap();
        assert_eq!(gone["result"], "failed");
        assert_eq!(gone["why"], "orphan_running");
        assert_eq!(gone["argv"], json!(["aib", "now"]));
        assert!(q.done().join("ahead.json").exists());
        assert!(q.done().join("boolt.json").exists());
        assert!(!q.done().join("gone.state.json").exists());
        assert_eq!(
            fs::read_to_string(q.done().join("had.json")).unwrap(),
            r#"{"result":"ok"}"#
        );
        assert_eq!(fs::read_dir(q.bad()).unwrap().count(), 5);
    }

    struct FakeClock {
        now: f64,
        slept: Vec<u64>,
    }
    impl Clock for FakeClock {
        fn now(&self) -> f64 {
            self.now
        }
        fn sleep(&mut self, s: u64) {
            self.slept.push(s);
            self.now += s as f64;
        }
    }

    #[derive(Default)]
    struct Fake {
        seen: Vec<Map<String, Value>>,
        ran: Vec<String>,
    }
    impl Handler for Fake {
        type Run = ();
        fn decide(&mut self, req: &mut Map<String, Value>) -> Result<Decision<()>, String> {
            self.seen.push(req.clone());
            match req.get("summary").and_then(Value::as_str) {
                Some("boom") => Err("RuntimeError".into()),
                Some("run") => Ok(Decision::Execute(vec![("x".into(), json!(1))], ())),
                _ => Ok(Decision::Record(
                    "denied".into(),
                    vec![("why".into(), json!("timeout"))],
                )),
            }
        }
        fn execute(
            &mut self,
            q: &Queue,
            rid: &str,
            req: &Map<String, Value>,
            _run: (),
            extra: Detail,
        ) -> io::Result<()> {
            assert!(q.running().join(format!("{rid}.json")).exists());
            self.ran.push(rid.to_owned());
            record(q, rid, "ok", Some(req), &extra, NOW).map(drop)
        }
    }

    fn done(q: &Queue, rid: &str) -> Value {
        serde_json::from_slice(&fs::read(q.done().join(format!("{rid}.json"))).unwrap()).unwrap()
    }

    #[test]
    fn run_once_records_runs_and_cleans_running() {
        let q = Queue::new(&tmp());
        put(
            &q.inbox().join("a.json"),
            r#"{"summary":"no","t":1800000000}"#,
            NOW - 30.0,
        );
        put(
            &q.inbox().join("b.json"),
            r#"{"summary":"run"}"#,
            NOW - 20.0,
        );
        put(
            &q.inbox().join("c.json"),
            r#"{"summary":"boom"}"#,
            NOW - 10.0,
        );
        put(&q.inbox().join("bad name.json"), "{}", NOW - 40.0);
        put(&q.inbox().join("d.json"), "not json", NOW - 5.0);
        let (mut h, mut c) = (
            Fake::default(),
            FakeClock {
                now: NOW,
                slept: vec![],
            },
        );
        assert_eq!(run_once(&q, &mut h, &mut c, LOCK_RETRY_S).unwrap(), 5);
        assert_eq!(done(&q, "a")["result"], "denied");
        assert_eq!(done(&q, "a")["why"], "timeout");
        assert_eq!(h.ran, vec!["b".to_string()]);
        assert_eq!(done(&q, "b")["x"], 1);
        assert_eq!(done(&q, "c")["result"], "failed");
        assert_eq!(done(&q, "c")["why"], "decide_error:RuntimeError");
        assert_eq!(done(&q, "d")["result"], "invalid");
        assert_eq!(done(&q, "d")["why"], "unreadable");
        // decide saw id and t (from the file time when missing)
        assert_eq!(h.seen[0]["id"], "a");
        assert_eq!(h.seen[0]["t"], 1_800_000_000);
        assert_eq!(h.seen[1]["t"], (NOW - 20.0) as i64);
        assert_eq!(fs::read_dir(q.running()).unwrap().count(), 0);
        assert_eq!(fs::read_dir(q.inbox()).unwrap().count(), 0);
        assert_eq!(fs::read_dir(q.bad()).unwrap().count(), 2); // bad name + unreadable
        assert!(c.slept.is_empty());
    }

    #[test]
    fn run_once_same_name_again_gets_a_new_id() {
        let q = Queue::new(&tmp());
        put(&q.done().join("a.json"), r#"{"result":"ok"}"#, NOW - 99.0);
        put(&q.inbox().join("a.json"), r#"{"summary":"no"}"#, NOW - 30.0);
        let (mut h, mut c) = (
            Fake::default(),
            FakeClock {
                now: NOW,
                slept: vec![],
            },
        );
        assert_eq!(run_once(&q, &mut h, &mut c, LOCK_RETRY_S).unwrap(), 1);
        assert_eq!(
            fs::read_to_string(q.done().join("a.json")).unwrap(),
            r#"{"result":"ok"}"#
        );
        let rid = h.seen[0]["id"].as_str().unwrap().to_owned();
        assert!(rid.starts_with("a-") && rid.len() == 8, "{rid}");
        assert_eq!(done(&q, &rid)["result"], "denied");
    }

    #[test]
    fn run_once_waits_for_fresh_three_times_then_stops() {
        let q = Queue::new(&tmp());
        // mtime ahead of the fake clock by 0.5 s stays fresh for one 2 s sleep only
        put(&q.inbox().join("a.json"), r#"{"summary":"no"}"#, NOW - 0.5);
        let (mut h, mut c) = (
            Fake::default(),
            FakeClock {
                now: NOW,
                slept: vec![],
            },
        );
        assert_eq!(run_once(&q, &mut h, &mut c, LOCK_RETRY_S).unwrap(), 1);
        assert_eq!(c.slept, vec![FRESH_S]);
        let q = Queue::new(&tmp());
        put(&q.inbox().join("a.json"), "{}", NOW + 1e6); // future, picked at once
        let mut c = FakeClock {
            now: NOW,
            slept: vec![],
        };
        assert_eq!(
            run_once(&q, &mut Fake::default(), &mut c, LOCK_RETRY_S).unwrap(),
            1
        );
        assert!(c.slept.is_empty());
    }

    #[test]
    fn run_once_lock_held_elsewhere_gives_up_after_retry() {
        let q = Queue::new(&tmp());
        put(&q.inbox().join("a.json"), r#"{"summary":"no"}"#, NOW - 30.0);
        let other = Gate::try_lock(&q.lock_path());
        assert!(other.held());
        assert!(!Gate::try_lock(&q.lock_path()).held());
        let (mut h, mut c) = (
            Fake::default(),
            FakeClock {
                now: NOW,
                slept: vec![],
            },
        );
        assert_eq!(run_once(&q, &mut h, &mut c, 12).unwrap(), 0);
        assert_eq!(c.slept, vec![5, 5, 5]);
        assert!(h.seen.is_empty());
        assert!(q.inbox().join("a.json").exists());
        drop(other);
        assert_eq!(run_once(&q, &mut h, &mut c, 12).unwrap(), 1);
    }

    #[test]
    fn run_once_sweeps_orphans_first() {
        let q = Queue::new(&tmp());
        put(&q.running().join("o.json"), r#"{"t": 1000}"#, NOW);
        let mut c = FakeClock {
            now: NOW,
            slept: vec![],
        };
        assert_eq!(run_once(&q, &mut Fake::default(), &mut c, 0).unwrap(), 0);
        assert_eq!(done(&q, "o")["why"], "orphan_running");
    }

    /// Test-only waits (ADR 0005 does not cover test threads racing each other).
    #[allow(clippy::disallowed_methods)]
    fn nap(ms: u64) {
        std::thread::sleep(Duration::from_millis(ms));
    }

    struct Counting(Arc<AtomicUsize>);
    impl Handler for Counting {
        type Run = ();
        fn decide(&mut self, _: &mut Map<String, Value>) -> Result<Decision<()>, String> {
            self.0.fetch_add(1, Ordering::SeqCst);
            nap(30);
            Ok(Decision::Record("denied".into(), vec![]))
        }
        fn execute(
            &mut self,
            _: &Queue,
            _: &str,
            _: &Map<String, Value>,
            _: (),
            _: Detail,
        ) -> io::Result<()> {
            unreachable!()
        }
    }

    /// Knife: claiming without the lock + rename lets two handlers take one request.
    #[test]
    fn two_handlers_never_take_the_same_request() {
        for _ in 0..5 {
            let q = Queue::new(&tmp());
            put(
                &q.inbox().join("one.json"),
                r#"{"summary":"x"}"#,
                NOW - 30.0,
            );
            let hits = Arc::new(AtomicUsize::new(0));
            let threads: Vec<_> = (0..8)
                .map(|_| {
                    let (q, hits) = (q.clone(), hits.clone());
                    std::thread::spawn(move || {
                        struct Real;
                        impl Clock for Real {
                            fn now(&self) -> f64 {
                                NOW
                            }
                            fn sleep(&mut self, _: u64) {
                                nap(5);
                            }
                        }
                        run_once(&q, &mut Counting(hits), &mut Real, 50).unwrap()
                    })
                })
                .collect();
            let handled: usize = threads.into_iter().map(|t| t.join().unwrap()).sum();
            assert_eq!(hits.load(Ordering::SeqCst), 1);
            assert_eq!(handled, 1);
            assert_eq!(fs::read_dir(q.done()).unwrap().count(), 1);
        }
    }
}
