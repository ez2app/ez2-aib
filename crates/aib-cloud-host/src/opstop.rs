// SPDX-License-Identifier: Apache-2.0
//! E11 (Andy 11-11 09:5x「可以彈出提示，同意後關閉」): a long operation says how far it got,
//! `op status <op>` says whether it is running, stalled or finished, and a stuck one is
//! stopped only through a sealed plan a person approved — never by an AI on its own.
//!
//! * **Progress**: once its journal says `running`, an operation writes
//!   `<op>.progress.json` beside the journal: its pid, start time, process group and boot,
//!   the step it is in and when it last wrote. A step writes when it begins; a long step
//!   (anything waiting on a child: a clone, a download, an import) writes at least every
//!   [`op::HEARTBEAT_SECS`]. The file is a note, not evidence: the journal stays the truth
//!   about the operation, and nothing is decided from a progress file alone.
//! * **Status**: the journal's last state, the progress file and whether the recorded pid
//!   still has the recorded start time ([`aib_cloud::op::progress`]). Silence makes a
//!   live operation `stalled`, never gone.
//! * **Stop**: `op stop <op> --plan` lists the processes it would stop — the recorded pid,
//!   proven by its start time, and the processes started under it — with the step it is
//!   in, sealed like every plan. `--apply <plan>` recomputes the plan under the host
//!   lock's guard; anything different is `plan_changed`. Then SIGTERM, a wait, SIGKILL for
//!   what is left, a wait, and a `failed` line with code `stopped_at_<step>` in the
//!   operation's journal. From there the existing rule holds: check the host, then
//!   `op recovered <op>`. Each pid is checked against its planned start time again right
//!   before it is signalled: a pid reused since the plan is left alone.
//! * **Not stuck** (f1fae 11-11 23:59): an operation that still writes its progress gets a
//!   plan all the same — a person may want to stop work that is slow rather than stuck —
//!   but the plan says `not_stuck: true`, and `--apply` then also needs
//!   `--even-if-running`. The plan as printed also carries `now` (verdict, why, seconds
//!   since the last update, one line for the approval window); `now` is outside the seal
//!   and is dropped before the plan is compared.
//!
//! **What a stop cannot reach** (33e28 N2): only the processes in this operation's tree,
//! found by each one's parent. A process that left the tree (a daemon that forked twice
//! and was adopted by init) is not found; neither is anything running **inside a
//! container** (`pct exec` / `lxc-attach` on the container's side, what `lxc-start`
//! runs): a person looks at those. Signals go through the `kill` program, so between the
//! last start-time check and the signal there is a window of milliseconds in which a pid
//! could be reused; once a libc binding is in, `kill(2)` is called directly.

use std::cell::RefCell;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use aib_cloud::op::{self, Entry, Liveness, ProcSeen, StopRefusal};
use serde_json::{json, Value};

use crate::opstore;

/// The progress file beside a journal: `<op>.progress.json` (not `.jsonl`, so the
/// journal readers pass over it).
pub const PROGRESS_SUFFIX: &str = ".progress.json";
/// How long a stop waits after SIGTERM before SIGKILL, and after SIGKILL before it says
/// what is left (seconds).
pub const TERM_WAIT_SECS: u64 = 10;
pub const KILL_WAIT_SECS: u64 = 5;
/// How often a stop looks whether the processes are gone.
const POLL: Duration = Duration::from_millis(200);

fn refused(r: StopRefusal, why: &str) -> String {
    format!("{}: {why}", r.as_str())
}

pub fn progress_path(dir: &Path, op_id: &str) -> Result<PathBuf, String> {
    if !op::valid_op_id(op_id) {
        return Err(format!("operation id {op_id:?} is not valid"));
    }
    Ok(dir.join(format!("{op_id}{PROGRESS_SUFFIX}")))
}

// ------------------------------------------------------------------ /proc

/// What `/proc/<pid>/stat` says about one process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stat {
    pub comm: String,
    pub zombie: bool,
    pub ppid: u32,
    pub pgid: u32,
    pub start_time: u64,
}

/// Fields 2 (comm, in parentheses, counted to the **last** `)`), 3 (state), 4 (ppid),
/// 5 (pgrp) and 22 (start time).
pub fn parse_stat(text: &str) -> Option<Stat> {
    let open = text.find('(')?;
    let close = text.rfind(')')?;
    let comm = text.get(open + 1..close)?.to_owned();
    let mut rest = text.get(close + 1..)?.split_whitespace();
    let state = rest.next()?;
    let ppid = rest.next()?.parse().ok()?;
    let pgid = rest.next()?.parse().ok()?;
    Some(Stat {
        comm,
        zombie: state == "Z",
        ppid,
        pgid,
        start_time: opstore::stat_start_time(text)?,
    })
}

/// One pid as it is now.
pub fn seen(proc_root: &Path, pid: u32) -> (ProcSeen, Option<Stat>) {
    match fs::read_to_string(proc_root.join(pid.to_string()).join("stat")) {
        Ok(t) => match parse_stat(&t) {
            Some(s) if s.zombie => (ProcSeen::Zombie(s.start_time), Some(s)),
            Some(s) => (ProcSeen::Live(s.start_time), Some(s)),
            None => (ProcSeen::Unreadable, None),
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (ProcSeen::Missing, None),
        Err(_) => (ProcSeen::Unreadable, None),
    }
}

/// Every live process started under `root` (children, their children …), by the parent
/// each one names now. A process that left the tree (reparented to init) is not found:
/// it is not this operation's any more by anything that can be checked.
pub fn descendants(proc_root: &Path, root: u32) -> Vec<(u32, Stat)> {
    let Ok(rd) = fs::read_dir(proc_root) else {
        return Vec::new();
    };
    let all: Vec<(u32, Stat)> = rd
        .flatten()
        .filter_map(|e| e.file_name().to_str()?.parse::<u32>().ok())
        .filter_map(|pid| match seen(proc_root, pid) {
            (ProcSeen::Live(_), Some(s)) => Some((pid, s)),
            _ => None,
        })
        .collect();
    let mut found: Vec<(u32, Stat)> = Vec::new();
    let mut parents = vec![root];
    while let Some(p) = parents.pop() {
        for (pid, s) in &all {
            if s.ppid == p && *pid != root && !found.iter().any(|(f, _)| f == pid) {
                found.push((*pid, s.clone()));
                parents.push(*pid);
            }
        }
    }
    found.sort_by_key(|(pid, _)| *pid);
    found
}

fn boot_id(path: &Path) -> Option<String> {
    let id = fs::read_to_string(path).ok()?.trim().to_owned();
    op::valid_boot_id(&id).then_some(id)
}

// ------------------------------------------------------------------ progress

/// The progress of the operation running in this thread (one operation per process; the
/// tests run side by side, so per thread rather than per process).
struct Current {
    path: PathBuf,
    rec: Value,
    last: u64,
    every: u64,
    /// Who approved the operation: a progress file that cannot be written is told the way
    /// [`opstore::tell`] tells (a customer's key: the host's log, never their stderr).
    approval: String,
    /// Where the last write that failed was told (`None`: every write so far went in).
    told: Option<opstore::Told>,
}

thread_local! {
    static CURRENT: RefCell<Option<Current>> = const { RefCell::new(None) };
}

/// Write the whole file aside, then rename it over: a reader never sees half of it.
fn put(path: &Path, rec: &Value) -> Result<(), String> {
    let mut tmp = path.to_path_buf().into_os_string();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    fs::write(&tmp, format!("{rec}\n")).map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

fn write_current(c: &mut Current) {
    if let Err(e) = put(&c.path, &c.rec) {
        // a progress file that cannot be written does not stop the work; status then
        // reads the journal's running line and the host lock instead. The error names a
        // host path: for a customer's key it goes to the host's log only (c477a S2 L1)
        let ops_dir = c.path.parent().unwrap_or(Path::new("."));
        c.told = Some(opstore::tell_in(
            ops_dir,
            &c.approval,
            &format!("progress: {e}"),
        ));
    }
}

/// Start the progress of `op_id`, run by process `pid`, at `now`; it writes at least every
/// `every` seconds from here on. Called once the journal says `running`.
pub fn begin(
    dir: &Path,
    op_id: &str,
    place: &opstore::LockPaths,
    pid: u32,
    now: u64,
    every: u64,
    approval: &str,
) {
    let Ok(path) = progress_path(dir, op_id) else {
        return;
    };
    let (now_seen, stat) = seen(&place.proc_root, pid);
    let start_time = match now_seen {
        ProcSeen::Live(st) => Some(st),
        _ => None,
    };
    let rec = json!({
        "op": op_id,
        "pid": pid,
        "start_time": start_time,
        "pgid": stat.map(|s| s.pgid),
        "boot_id": boot_id(&place.boot_id),
        "step": "running",
        "step_at": now,
        "updated_at": now,
    });
    let mut c = Current {
        path,
        rec,
        last: now,
        every,
        approval: approval.to_owned(),
        told: None,
    };
    write_current(&mut c);
    CURRENT.with(|cur| *cur.borrow_mut() = Some(c));
}

/// The operation in this thread begins `name` (written at once).
pub fn step(name: &str) {
    step_at(name, crate::probe::unix_now());
}

pub fn step_at(name: &str, now: u64) {
    CURRENT.with(|cur| {
        if let Some(c) = cur.borrow_mut().as_mut() {
            let token = op::step_token(name).unwrap_or_else(|| "unnamed".to_owned());
            c.rec["step"] = json!(token);
            c.rec["step_at"] = json!(now);
            c.rec["updated_at"] = json!(now);
            c.last = now;
            write_current(c);
        }
    });
}

/// Called from every wait on a child: writes when `every` seconds have passed since the
/// last write, so a long step is never silent for longer than that.
pub fn tick() {
    tick_at(crate::probe::unix_now());
}

pub fn tick_at(now: u64) {
    CURRENT.with(|cur| {
        if let Some(c) = cur.borrow_mut().as_mut() {
            if now.saturating_sub(c.last) >= c.every {
                c.rec["updated_at"] = json!(now);
                c.last = now;
                write_current(c);
            }
        }
    });
}

/// Where the running operation's last failed progress write was told.
#[cfg(test)]
fn last_told() -> Option<opstore::Told> {
    CURRENT.with(|cur| cur.borrow().as_ref().and_then(|c| c.told))
}

/// The operation in this thread has ended; the file stays for `op status` until
/// [`forget`] (after `verified`).
pub fn end() {
    CURRENT.with(|cur| *cur.borrow_mut() = None);
}

/// A verified operation needs no progress file.
pub fn forget(dir: &Path, op_id: &str) {
    if let Ok(p) = progress_path(dir, op_id) {
        let _ = fs::remove_file(p);
    }
}

#[cfg(test)]
pub fn current_step() -> Option<String> {
    CURRENT.with(|cur| {
        cur.borrow()
            .as_ref()
            .and_then(|c| c.rec["step"].as_str().map(str::to_owned))
    })
}

pub fn read_progress(dir: &Path, op_id: &str) -> Option<Value> {
    let text = fs::read_to_string(progress_path(dir, op_id).ok()?).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    (v["op"] == op_id).then_some(v)
}

// ------------------------------------------------------------------ what was recorded

/// The process an operation recorded: from its progress file, or else from the host lock
/// when the lock names it (an operation from before progress files, or one whose file
/// could not be written).
#[derive(Debug, Clone, PartialEq, Eq)]
struct Recorded {
    pid: u32,
    start_time: Option<u64>,
    pgid: Option<u32>,
    boot_id: Option<String>,
    step: String,
    step_at: u64,
    updated_at: u64,
    from: &'static str,
}

fn recorded(dir: &Path, lock_file: &Path, op_id: &str, running_at: u64) -> Option<Recorded> {
    let holder = fs::read_to_string(lock_file)
        .ok()
        .and_then(|t| opstore::holder_from_text(&t))
        .filter(|h| h.op == op_id);
    if let Some(p) = read_progress(dir, op_id) {
        let pid = u32::try_from(p["pid"].as_u64()?).ok()?;
        // 33e28 N3: a progress file that could not read its own start time takes the one
        // the host lock holds for the same operation and pid
        let from_lock = holder
            .as_ref()
            .filter(|h| h.pid == pid)
            .map(|h| h.start_time);
        return Some(Recorded {
            pid,
            start_time: p["start_time"].as_u64().or(from_lock),
            pgid: p["pgid"].as_u64().and_then(|g| u32::try_from(g).ok()),
            boot_id: p["boot_id"].as_str().map(str::to_owned),
            step: p["step"].as_str().unwrap_or("running").to_owned(),
            step_at: p["step_at"].as_u64().unwrap_or(running_at),
            updated_at: p["updated_at"].as_u64().unwrap_or(running_at),
            from: "progress",
        });
    }
    holder.map(|h| Recorded {
        pid: h.pid,
        start_time: Some(h.start_time),
        pgid: None,
        boot_id: Some(h.boot_id),
        step: "running".to_owned(),
        step_at: running_at,
        updated_at: running_at,
        from: "host_lock",
    })
}

/// When the journal entered `running` (the time status falls back to).
fn running_at(entries: &[Entry]) -> u64 {
    entries
        .iter()
        .rev()
        .find(|e| e.state == op::State::Running)
        .map_or(0, |e| e.at)
}

fn same_boot(rec: &Recorded, current: Option<&str>) -> bool {
    matches!((rec.boot_id.as_deref(), current), (Some(a), Some(b)) if a == b)
}

/// Where the facts are read: the host's, or a scratch directory in tests.
pub struct Facts<'a> {
    pub ops_dir: &'a Path,
    pub lock: &'a opstore::LockPaths,
    pub fingerprint: &'a str,
    /// Silence after which an operation is stalled ([`op::STALLED_SECS`] on the host).
    pub stalled_after: u64,
}

// ------------------------------------------------------------------ op status <op>

/// The operation's part of `op status <op>`: the journal, the step, how long since the
/// last update, and the verdict. Reads only.
pub fn status(f: &Facts, op_id: &str, now: u64, stalled_after: u64) -> Result<Value, String> {
    let (entries, cut) = opstore::read_journal(&opstore::journal_path(f.ops_dir, op_id)?)?;
    let last = op::replay(&entries).map_err(|e| e.to_string())?;
    let at = running_at(&entries);
    let rec = recorded(f.ops_dir, &f.lock.lock, op_id, at);
    let current_boot = boot_id(&f.lock.boot_id);
    let live = match &rec {
        Some(r) if r.boot_id.is_some() && !same_boot(r, current_boot.as_deref()) => Liveness::Gone,
        Some(r) => match (r.start_time, seen(&f.lock.proc_root, r.pid).0) {
            (_, ProcSeen::Missing) => Liveness::Gone,
            (Some(st), ProcSeen::Live(now_st) | ProcSeen::Zombie(now_st)) if st != now_st => {
                Liveness::Gone
            }
            (Some(_), ProcSeen::Zombie(_)) => Liveness::Gone,
            (Some(_), ProcSeen::Live(_)) => Liveness::Alive,
            _ => Liveness::Unknown,
        },
        None => Liveness::Unknown,
    };
    let updated_at = rec.as_ref().map_or(at, |r| r.updated_at);
    let verdict = op::progress(last.state, updated_at, now, live, stalled_after);
    Ok(json!({
        "id": op_id,
        "command": last.command,
        "target": last.target,
        "state": last.state.as_str(),
        "code": last.code,
        "seq": last.seq,
        "resume": format!("{:?}", op::resume(last.state)),
        "last_line_cut_short": cut,
        "step": rec.as_ref().map(|r| r.step.clone()),
        "step_at": rec.as_ref().map(|r| r.step_at),
        "updated_at": updated_at,
        "seconds_since_update": now.saturating_sub(updated_at),
        "pid": rec.as_ref().map(|r| r.pid),
        "recorded_in": rec.as_ref().map(|r| r.from),
        "liveness": format!("{live:?}").to_lowercase(),
        "verdict": verdict.as_str(),
        "why": verdict.why(),
        "stalled_after_seconds": stalled_after,
    }))
}

// ------------------------------------------------------------------ op stop

/// `op stop <op> --plan`: the processes a stop would signal and the step the operation is
/// in, sealed. Refused (with the fixed detail first) when the journal is not at running,
/// nothing was recorded, the boot differs, or the pid is not provably the same process.
pub fn stop_plan(f: &Facts, op_id: &str, now: u64) -> Result<Value, String> {
    plan_and_now(f, op_id, now).map(|(plan, _)| plan)
}

/// The key of what `--plan` prints beside the sealed plan: the verdict now, for the first
/// line of the approval window. Not sealed (it changes every second); `--apply` drops it.
pub const NOW_KEY: &str = "now";

/// The plan `--plan` prints: the sealed plan and, under [`NOW_KEY`], the verdict now.
pub fn plan_for_window(f: &Facts, op_id: &str, now: u64) -> Result<Value, String> {
    let (mut plan, summary) = plan_and_now(f, op_id, now)?;
    plan[NOW_KEY] = summary;
    Ok(plan)
}

fn plan_and_now(f: &Facts, op_id: &str, now: u64) -> Result<(Value, Value), String> {
    let (entries, _) = opstore::read_journal(&opstore::journal_path(f.ops_dir, op_id)?)?;
    let last = op::replay(&entries).map_err(|e| e.to_string())?;
    let Some(rec) = recorded(f.ops_dir, &f.lock.lock, op_id, running_at(&entries)) else {
        return Err(if last.state == op::State::Running {
            refused(
                StopRefusal::NoProcessRecord,
                "the operation recorded no process (no progress file, the host lock does not name it)",
            )
        } else {
            refused(StopRefusal::NotRunning, "the journal is not at running")
        });
    };
    let (main_seen, main) = seen(&f.lock.proc_root, rec.pid);
    let current_boot = boot_id(&f.lock.boot_id);
    op::may_stop(
        last.state,
        rec.start_time,
        same_boot(&rec, current_boot.as_deref()),
        main_seen,
    )
    .map_err(|r| {
        refused(
            r,
            &format!(
                "operation {op_id} (pid {}, journal {}); nothing is signalled",
                rec.pid,
                last.state.as_str()
            ),
        )
    })?;
    let main = main.ok_or_else(|| refused(StopRefusal::StartTimeUnreadable, "stat"))?;
    let mut processes = vec![json!({
        "pid": rec.pid, "ppid": main.ppid, "pgid": main.pgid,
        "start_time": main.start_time, "comm": main.comm,
    })];
    for (pid, s) in descendants(&f.lock.proc_root, rec.pid) {
        processes.push(json!({
            "pid": pid, "ppid": s.ppid, "pgid": s.pgid,
            "start_time": s.start_time, "comm": s.comm,
        }));
    }
    let code = op::stop_code(&rec.step).unwrap_or_else(|| "stopped_at_unnamed".to_owned());
    // the process was just proven to be the recorded one: only silence is left to judge
    let verdict = op::progress(
        last.state,
        rec.updated_at,
        now,
        Liveness::Alive,
        f.stalled_after,
    );
    let since = now.saturating_sub(rec.updated_at);
    let summary = json!({
        "verdict": verdict.as_str(),
        "why": verdict.why(),
        "seconds_since_update": since,
        "line": format!(
            "{} {since}s since the last update, at step {} (operation {op_id}, {} {})",
            verdict.as_str(),
            rec.step,
            last.command,
            last.target
        ),
    });
    let mut doc = json!({
        "command": "op.stop",
        "op": op_id,
        "op_command": last.command,
        "target": last.target,
        "journal_seq": last.seq,
        "step": rec.step,
        "step_at": rec.step_at,
        "pid": rec.pid,
        "start_time": rec.start_time,
        "pgid": rec.pgid,
        "boot_id": rec.boot_id,
        "processes": processes,
        "not_stuck": op::not_stuck(verdict),
        "term_wait_seconds": TERM_WAIT_SECS,
        "kill_wait_seconds": KILL_WAIT_SECS,
        "journal_after": { "state": "failed", "code": code },
        // 33e28 N1: a stop that came while the operation waited to write `verified` leaves
        // finished work journaled as failed
        "then": format!("check the host (the operation may have finished its work just before it was stopped), then `aib-cloud-host op recovered {op_id}`"),
    });
    opstore::seal(&mut doc, f.fingerprint, env!("CARGO_PKG_VERSION"))?;
    Ok((doc, summary))
}

/// Sends signals and waits. The host's is [`HostSignals`]; tests hand in one that edits a
/// made-up `/proc`.
pub trait Signals {
    /// `kill -<signal> <pid>…`.
    fn send(&mut self, signal: &str, pids: &[u32]) -> Result<(), String>;
    /// Let `d` pass.
    fn pause(&mut self, d: Duration);
}

pub struct HostSignals;

impl Signals for HostSignals {
    fn send(&mut self, signal: &str, pids: &[u32]) -> Result<(), String> {
        if pids.is_empty() {
            return Ok(());
        }
        // a pid that exited since it was checked makes kill say so; that is not an error
        let _ = Command::new("kill")
            .arg(format!("-{signal}"))
            .args(pids.iter().map(u32::to_string))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|e| format!("kill: {e}"))?;
        Ok(())
    }

    // ADR 0005: this CLI has no timer registry; a stop waits a few seconds for processes
    // to go, the same short poll as probe::wait_with_timeout
    #[allow(clippy::disallowed_methods)]
    fn pause(&mut self, d: Duration) {
        std::thread::sleep(d);
    }
}

/// The planned processes that are still exactly the planned ones (same start time, not
/// exited). Only these are ever signalled.
fn still_there(proc_root: &Path, planned: &[(u32, u64)]) -> Vec<u32> {
    planned
        .iter()
        .filter(|(pid, st)| op::still_the_one(*st, seen(proc_root, *pid).0))
        .map(|(pid, _)| *pid)
        .collect()
}

fn wait_gone(sig: &mut impl Signals, proc_root: &Path, planned: &[(u32, u64)], limit: Duration) {
    let mut waited = Duration::ZERO;
    while waited < limit && !still_there(proc_root, planned).is_empty() {
        sig.pause(POLL);
        waited += POLL;
    }
}

/// `op stop <op> --apply <plan>`: under the host lock's guard, the plan is recomputed and
/// must be the same; then SIGTERM, a wait, SIGKILL for what is left, a wait, and the
/// journal line `failed` / `stopped_at_<step>`. The guard is held throughout, so no other
/// operation takes the lock over half way and the stopped operation cannot write its own
/// end in between. Returns what was done; `survivors` lists any process still there.
pub fn stop_apply(
    f: &Facts,
    plan: &Value,
    even_if_running: bool,
    sig: &mut impl Signals,
    waits: (Duration, Duration),
    now: u64,
) -> Result<Value, String> {
    let op_id = plan["op"]
        .as_str()
        .filter(|o| op::valid_op_id(o))
        .ok_or_else(|| refused(StopRefusal::PlanChanged, "the plan names no operation"))?
        .to_owned();
    // what --plan printed beside the seal is not part of the plan
    let mut given = plan.clone();
    if let Some(o) = given.as_object_mut() {
        o.remove(NOW_KEY);
    }
    opstore::guarded(f.lock, || {
        let again = stop_plan(f, &op_id, now)?;
        if again != given {
            return Err(refused(
                StopRefusal::PlanChanged,
                "the operation or its processes are not as the plan saw them (or the plan was edited); make a new plan",
            ));
        }
        op::may_apply_stop(
            again["not_stuck"].as_bool().unwrap_or(true),
            even_if_running,
        )
        .map_err(|r| {
            refused(
                r,
                "the plan was made while the operation was still writing its progress; add --even-if-running to stop it anyway",
            )
        })?;
        let planned: Vec<(u32, u64)> = again["processes"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|p| {
                        Some((
                            u32::try_from(p["pid"].as_u64()?).ok()?,
                            p["start_time"].as_u64()?,
                        ))
                    })
                    .collect()
            })
            .unwrap_or_default();
        let term = still_there(&f.lock.proc_root, &planned);
        sig.send("TERM", &term)?;
        wait_gone(sig, &f.lock.proc_root, &planned, waits.0);
        let kill = still_there(&f.lock.proc_root, &planned);
        if !kill.is_empty() {
            sig.send("KILL", &kill)?;
            wait_gone(sig, &f.lock.proc_root, &planned, waits.1);
        }
        let survivors = still_there(&f.lock.proc_root, &planned);
        let code = again["journal_after"]["code"]
            .as_str()
            .unwrap_or("stopped_at_unnamed")
            .to_owned();
        let path = opstore::journal_path(f.ops_dir, &op_id)?;
        let (entries, _) = opstore::read_journal(&path)?;
        let last = op::replay(&entries).map_err(|e| e.to_string())?.clone();
        opstore::append_entry(
            f.ops_dir,
            &Entry {
                seq: last.seq + 1,
                at: now,
                state: op::State::Failed,
                code: code.clone(),
                ..last
            },
        )?;
        let step = again["step"].clone();
        if let Some(mut p) = read_progress(f.ops_dir, &op_id) {
            p["stopped"] = json!({ "at": now, "step": step, "code": code, "survivors": survivors });
            if let Ok(path) = progress_path(f.ops_dir, &op_id) {
                let _ = put(&path, &p);
            }
        }
        Ok(json!({
            "op": op_id,
            "detail": if survivors.is_empty() { "stopped" } else { "stop_survivors" },
            "stopped_at": step,
            "journal": { "state": "failed", "code": code },
            "term": term,
            "kill": kill,
            "survivors": survivors,
            "then": again["then"],
        }))
    })
}

// ------------------------------------------------------------------ command

const USAGE: &str =
    "op stop <op id> --plan | op stop <op id> --apply <plan.json> [--even-if-running]";

/// `op stop …` on this host. `--plan` reads only; `--apply` signals and writes, and runs
/// only from an approved sign sheet (a person approves the stop in a window).
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let (op_id, rest) = args.split_first().ok_or(USAGE)?;
    let lock = opstore::LockPaths::host().map_err(|e| refused(StopRefusal::NotLinux, &e))?;
    let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
    let ops_dir = PathBuf::from(opstore::OPS_DIR);
    let f = Facts {
        ops_dir: &ops_dir,
        lock: &lock,
        fingerprint: &fingerprint,
        stalled_after: op::STALLED_SECS,
    };
    let (apply_args, even_if_running) = match rest {
        [flag, file, even] if flag == "--apply" && even == "--even-if-running" => {
            (Some(file), true)
        }
        [flag, file] if flag == "--apply" => (Some(file), false),
        _ => (None, false),
    };
    match (rest, apply_args) {
        ([flag], None) if flag == "--plan" => {
            let doc = plan_for_window(&f, op_id, crate::probe::unix_now())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
            );
            Ok(0)
        }
        (_, Some(file)) => {
            let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
            let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{file}: {e}"))?;
            if plan["op"] != op_id.as_str() {
                return Err(refused(
                    StopRefusal::PlanChanged,
                    "the plan is for another operation",
                ));
            }
            let done = stop_apply(
                &f,
                &plan,
                even_if_running,
                &mut HostSignals,
                (
                    Duration::from_secs(TERM_WAIT_SECS),
                    Duration::from_secs(KILL_WAIT_SECS),
                ),
                crate::probe::unix_now(),
            )?;
            println!(
                "{}",
                serde_json::to_string_pretty(&done).map_err(|e| e.to_string())?
            );
            Ok(if done["detail"] == "stopped" { 0 } else { 1 })
        }
        _ => Err(USAGE.to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::op::State;

    const BOOT: &str = "0f0e0d0c-0b0a-4908-8706-050403020100";
    const BOOT_OTHER: &str = "1f0e0d0c-0b0a-4908-8706-050403020100";
    const OP: &str = "20261111T120000Z-0a0b0c0d";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const MAIN: u32 = 4100;
    const CHILD: u32 = 4101;
    const GRANDCHILD: u32 = 4102;
    /// The shell that started the operation: same process group, not under it.
    const SHELL: u32 = 4000;
    const OTHER: u32 = 5000;
    const T: u64 = 1_800_000_000;

    struct Place {
        root: PathBuf,
        ops: PathBuf,
        lock: opstore::LockPaths,
    }

    impl Place {
        fn facts(&self) -> Facts<'_> {
            Facts {
                ops_dir: &self.ops,
                lock: &self.lock,
                fingerprint: "f1",
                // the made-up operations last wrote at T; the stops come at T + 50
                stalled_after: 10,
            }
        }
    }

    fn place(name: &str) -> Place {
        let root = std::env::temp_dir().join(format!(
            "aib-opstop-{name}-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("proc")).unwrap();
        fs::create_dir_all(root.join("ops")).unwrap();
        fs::write(root.join("boot_id"), format!("{BOOT}\n")).unwrap();
        Place {
            ops: root.join("ops"),
            lock: opstore::LockPaths {
                lock: root.join("host.lock"),
                proc_root: root.join("proc"),
                boot_id: root.join("boot_id"),
            },
            root,
        }
    }

    fn proc_with(p: &Place, pid: u32, state: &str, ppid: u32, pgid: u32, start: u64) {
        let d = p.lock.proc_root.join(pid.to_string());
        fs::create_dir_all(&d).unwrap();
        let filler = (6..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            d.join("stat"),
            format!("{pid} (aib cloud) host) {state} {ppid} {pgid} {filler} {start} 0 0\n"),
        )
        .unwrap();
    }

    /// The operation's tree: MAIN (in the shell's group) with a child in a group of its
    /// own and a grandchild; the shell above it and an unrelated process beside it.
    fn tree(p: &Place) {
        proc_with(p, SHELL, "S", 1, SHELL, 100);
        proc_with(p, MAIN, "S", SHELL, SHELL, 500);
        proc_with(p, CHILD, "S", MAIN, CHILD, 510);
        proc_with(p, GRANDCHILD, "D", CHILD, CHILD, 520);
        proc_with(p, OTHER, "S", 1, SHELL, 600);
    }

    fn journal(p: &Place, states: &[State]) {
        for (i, s) in states.iter().enumerate() {
            let seq = u32::try_from(i + 1).unwrap();
            opstore::append_entry(
                &p.ops,
                &Entry {
                    op: OP.to_owned(),
                    seq,
                    at: T + u64::from(seq),
                    state: *s,
                    command: "site.create".to_owned(),
                    target: "domain:new.example.test".to_owned(),
                    plan_sha256: SHA.to_owned(),
                    code: "ok".to_owned(),
                    approval: if *s == State::Planned {
                        String::new()
                    } else {
                        opstore::BY_LOCAL_ROOT.to_owned()
                    },
                },
            )
            .unwrap();
        }
    }

    fn running(p: &Place) {
        journal(p, &[State::Planned, State::Approved, State::Running]);
    }

    fn progress_file(p: &Place, step: &str, updated: u64, start: Option<u64>, boot: &str) {
        fs::write(
            progress_path(&p.ops, OP).unwrap(),
            json!({
                "op": OP, "pid": MAIN, "start_time": start, "pgid": SHELL, "boot_id": boot,
                "step": step, "step_at": T + 10, "updated_at": updated,
            })
            .to_string(),
        )
        .unwrap();
    }

    fn states(p: &Place) -> Vec<(State, String)> {
        opstore::read_journal(&opstore::journal_path(&p.ops, OP).unwrap())
            .unwrap()
            .0
            .into_iter()
            .map(|e| (e.state, e.code))
            .collect()
    }

    #[test]
    fn a_stat_line_is_read_from_its_last_parenthesis() {
        let p = place("stat");
        tree(&p);
        let s = seen(&p.lock.proc_root, GRANDCHILD);
        assert_eq!(s.0, ProcSeen::Live(520));
        let st = s.1.unwrap();
        assert_eq!(
            (st.comm.as_str(), st.ppid, st.pgid, st.zombie),
            ("aib cloud) host", CHILD, CHILD, false)
        );
        proc_with(&p, 4200, "Z", MAIN, SHELL, 530);
        assert_eq!(seen(&p.lock.proc_root, 4200).0, ProcSeen::Zombie(530));
        assert_eq!(seen(&p.lock.proc_root, 4300).0, ProcSeen::Missing);
        fs::create_dir_all(p.lock.proc_root.join("4301")).unwrap();
        fs::write(p.lock.proc_root.join("4301/stat"), "garbage").unwrap();
        assert_eq!(seen(&p.lock.proc_root, 4301).0, ProcSeen::Unreadable);
    }

    /// E11 (1): a step writes when it begins; a long one at least every N seconds, and
    /// not more often than that.
    #[test]
    fn a_long_step_still_writes_its_progress() {
        let p = place("beat");
        tree(&p);
        begin(&p.ops, OP, &p.lock, MAIN, 1000, 30, opstore::BY_LOCAL_ROOT);
        let read = || read_progress(&p.ops, OP).unwrap();
        let r = read();
        assert_eq!(r["step"], "running");
        assert_eq!(r["updated_at"], 1000);
        assert_eq!(r["pid"], MAIN);
        assert_eq!(r["start_time"], 500);
        assert_eq!(r["pgid"], SHELL);
        assert_eq!(r["boot_id"], BOOT);
        tick_at(1029);
        assert_eq!(read()["updated_at"], 1000, "not before 30 s");
        tick_at(1030);
        assert_eq!(read()["updated_at"], 1030);
        tick_at(1059);
        assert_eq!(read()["updated_at"], 1030);
        step_at("install", 1061);
        let r = read();
        assert_eq!(
            (r["step"].clone(), r["step_at"].clone()),
            (json!("install"), json!(1061))
        );
        assert_eq!(r["updated_at"], 1061);
        step_at("Not A Step", 1062);
        assert_eq!(read()["step"], "unnamed");
        end();
        tick_at(9999);
        step_at("edge", 9999);
        assert_eq!(
            read()["updated_at"],
            1062,
            "an ended operation writes nothing"
        );
        assert_eq!(current_step(), None);
        // no file is left half written
        assert!(!p.ops.join(format!("{OP}{PROGRESS_SUFFIX}.tmp")).exists());
        // the journal readers pass over the progress file
        running(&p);
        assert_eq!(opstore::last_entries(&p.ops).unwrap().len(), 1);
        let _ = fs::remove_dir_all(&p.root);
    }

    /// A customer's key: a progress file that cannot be written names a host path, so it
    /// is told to the host's log, not on the standard error the customer reads (c477a S2 L1).
    #[test]
    fn a_customer_key_progress_error_stays_off_stderr() {
        let p = place("beat-customer");
        tree(&p);
        // the file cannot be written: a folder stands where its temporary copy goes
        fs::create_dir_all(p.ops.join(format!("{OP}{PROGRESS_SUFFIX}.tmp"))).unwrap();
        begin(
            &p.ops,
            OP,
            &p.lock,
            MAIN,
            1000,
            30,
            opstore::BY_CUSTOMER_KEY,
        );
        assert_eq!(last_told(), Some(opstore::Told::Log));
        step_at("install", 1001);
        assert_eq!(last_told(), Some(opstore::Told::Log));
        let notes = fs::read_to_string(p.ops.join(opstore::NOTES)).unwrap();
        assert_eq!(notes.matches("progress: ").count(), 2, "{notes}");
        end();
        // root on the host still reads it on stderr, and nothing is logged for it
        let q = place("beat-root");
        tree(&q);
        fs::create_dir_all(q.ops.join(format!("{OP}{PROGRESS_SUFFIX}.tmp"))).unwrap();
        begin(&q.ops, OP, &q.lock, MAIN, 1000, 30, opstore::BY_LOCAL_ROOT);
        assert_eq!(last_told(), Some(opstore::Told::Stderr));
        assert!(!q.ops.join(opstore::NOTES).exists());
        end();
        let _ = fs::remove_dir_all(&p.root);
        let _ = fs::remove_dir_all(&q.root);
    }

    /// E11 (1) on a real child: the wait loops write while the child runs.
    #[test]
    fn waiting_on_a_child_keeps_the_progress_fresh() {
        let p = place("waits");
        proc_with(&p, MAIN, "S", SHELL, SHELL, 500);
        begin(&p.ops, OP, &p.lock, MAIN, 0, 0, opstore::BY_LOCAL_ROOT);
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "sleep 0.2"]);
        crate::probe::run_capture(&mut cmd, Duration::from_secs(5)).unwrap();
        let after_capture = read_progress(&p.ops, OP).unwrap()["updated_at"]
            .as_u64()
            .unwrap();
        assert!(after_capture > 0, "run_capture's wait wrote");
        begin(&p.ops, OP, &p.lock, MAIN, 0, 0, opstore::BY_LOCAL_ROOT);
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "sleep 0.2"]);
        crate::probe::run_bounded_input(&mut cmd, Duration::from_secs(5), 1024, b"").unwrap();
        let after_bounded = read_progress(&p.ops, OP).unwrap()["updated_at"]
            .as_u64()
            .unwrap();
        assert!(after_bounded > 0, "run_bounded_input's wait wrote");
        begin(&p.ops, OP, &p.lock, MAIN, 0, 0, opstore::BY_LOCAL_ROOT);
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "sleep 0.2"]);
        crate::probe::run_capture_bounded(&mut cmd, Duration::from_secs(5), 1024).unwrap();
        let after_read = read_progress(&p.ops, OP).unwrap()["updated_at"]
            .as_u64()
            .unwrap();
        assert!(after_read > 0, "run_capture_bounded's wait wrote");
        end();
        let _ = fs::remove_dir_all(&p.root);
    }

    /// E11 (2): running, stalled, finished — from pid and start time, not from time alone.
    #[test]
    fn status_tells_running_stalled_and_finished() {
        let p = place("status");
        tree(&p);
        running(&p);
        let now = T + 1000;
        let st = |p: &Place| status(&p.facts(), OP, now, op::STALLED_SECS).unwrap();
        progress_file(&p, "install", now - 10, Some(500), BOOT);
        let s = st(&p);
        assert_eq!(
            (s["verdict"].clone(), s["why"].clone(), s["step"].clone()),
            (
                json!("running"),
                json!("updated_recently"),
                json!("install")
            )
        );
        assert_eq!(s["seconds_since_update"], 10);
        assert_eq!(s["liveness"], "alive");
        assert_eq!(s["recorded_in"], "progress");
        // silent for long, but alive: stalled, and still alive
        progress_file(&p, "install", now - op::STALLED_SECS, Some(500), BOOT);
        let s = st(&p);
        assert_eq!(
            (s["verdict"].clone(), s["why"].clone()),
            (json!("stalled"), json!("no_update"))
        );
        assert_eq!(s["liveness"], "alive");
        // fresh line, but the pid is gone
        progress_file(&p, "install", now, Some(500), BOOT);
        fs::remove_dir_all(p.lock.proc_root.join(MAIN.to_string())).unwrap();
        let s = st(&p);
        assert_eq!(
            (s["verdict"].clone(), s["why"].clone()),
            (json!("stalled"), json!("process_gone"))
        );
        // the pid belongs to another process now
        proc_with(&p, MAIN, "S", 1, 1, 999);
        let s = st(&p);
        assert_eq!(
            (s["why"].clone(), s["liveness"].clone()),
            (json!("process_gone"), json!("gone"))
        );
        // from before a reboot
        proc_with(&p, MAIN, "S", SHELL, SHELL, 500);
        progress_file(&p, "install", now, Some(500), BOOT_OTHER);
        assert_eq!(st(&p)["why"], "process_gone");
        // no progress file: the host lock names it, the running line's time counts
        fs::remove_file(progress_path(&p.ops, OP).unwrap()).unwrap();
        fs::write(
            &p.lock.lock,
            json!({ "op": OP, "pid": MAIN, "start_time": 500, "boot_id": BOOT }).to_string(),
        )
        .unwrap();
        let s = st(&p);
        assert_eq!(s["recorded_in"], "host_lock");
        assert_eq!(s["updated_at"], T + 3);
        assert_eq!(
            (s["verdict"].clone(), s["liveness"].clone()),
            (json!("stalled"), json!("alive"))
        );
        // finished
        journal_more(&p, State::Verified, 4);
        let s = st(&p);
        assert_eq!(
            (s["verdict"].clone(), s["state"].clone()),
            (json!("finished"), json!("verified"))
        );
        let _ = fs::remove_dir_all(&p.root);
    }

    fn journal_more(p: &Place, state: State, seq: u32) {
        let (entries, _) =
            opstore::read_journal(&opstore::journal_path(&p.ops, OP).unwrap()).unwrap();
        let last = entries.last().unwrap().clone();
        opstore::append_entry(&p.ops, &Entry { seq, state, ..last }).unwrap();
    }

    /// E11 (3): the plan names the recorded pid and what was started under it — not the
    /// shell above it, not a process that only shares its group.
    #[test]
    fn a_stop_plan_names_only_the_operations_own_processes() {
        let p = place("plan");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        let pids: Vec<u64> = plan["processes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x["pid"].as_u64().unwrap())
            .collect();
        assert_eq!(
            pids,
            [u64::from(MAIN), u64::from(CHILD), u64::from(GRANDCHILD)]
        );
        assert_eq!(plan["step"], "install");
        assert_eq!(plan["journal_after"]["code"], "stopped_at_install");
        assert_eq!(plan["processes"][0]["start_time"], 500);
        assert_eq!(plan["host_fingerprint"], "f1");
        assert_eq!(plan["plan_sha256"].as_str().map(str::len), Some(64));
        // the same host gives the same plan
        assert_eq!(stop_plan(&p.facts(), OP, T + 50).unwrap(), plan);
        let _ = fs::remove_dir_all(&p.root);
    }

    /// E11 (3): a reused pid, an unreadable start time, another boot, a gone process, a
    /// finished journal and no record are each refused with their fixed detail.
    #[test]
    fn a_stop_is_refused_unless_the_pid_is_provably_the_same_process() {
        let p = place("refuse");
        tree(&p);
        running(&p);
        let refusal = |p: &Place| {
            stop_plan(&p.facts(), OP, T + 50)
                .unwrap_err()
                .split(':')
                .next()
                .unwrap()
                .to_owned()
        };
        assert_eq!(refusal(&p), "no_process_record");
        progress_file(&p, "install", T, Some(499), BOOT);
        assert_eq!(refusal(&p), "pid_reused");
        progress_file(&p, "install", T, None, BOOT);
        assert_eq!(refusal(&p), "start_time_unreadable");
        progress_file(&p, "install", T, Some(500), BOOT_OTHER);
        assert_eq!(refusal(&p), "process_from_another_boot");
        progress_file(&p, "install", T, Some(500), BOOT);
        fs::write(p.lock.proc_root.join(format!("{MAIN}/stat")), "garbage").unwrap();
        assert_eq!(refusal(&p), "start_time_unreadable");
        proc_with(&p, MAIN, "Z", SHELL, SHELL, 500);
        assert_eq!(refusal(&p), "process_gone");
        fs::remove_dir_all(p.lock.proc_root.join(MAIN.to_string())).unwrap();
        assert_eq!(refusal(&p), "process_gone");
        proc_with(&p, MAIN, "S", SHELL, SHELL, 500);
        journal_more(&p, State::Verified, 4);
        assert_eq!(refusal(&p), "op_not_running");
        let _ = fs::remove_dir_all(&p.root);
    }

    /// Signals on the made-up `/proc`: TERM ends every process but the stubborn ones,
    /// KILL ends the rest; `reuse_on_term` gives a pid to another process when TERM comes.
    /// It also looks whether the host lock's guard is held while it signals.
    struct FakeSignals<'a> {
        p: &'a Place,
        stubborn: Vec<u32>,
        reuse_on_term: Option<u32>,
        sent: Vec<(String, Vec<u32>)>,
        guard_held: Vec<bool>,
    }

    impl Signals for FakeSignals<'_> {
        fn send(&mut self, signal: &str, pids: &[u32]) -> Result<(), String> {
            let mut g = self.p.lock.lock.clone().into_os_string();
            g.push(".guard");
            let f = fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .write(true)
                .open(PathBuf::from(g))
                .unwrap();
            self.guard_held.push(f.try_lock().is_err());
            self.sent.push((signal.to_owned(), pids.to_vec()));
            for pid in pids {
                if signal == "TERM" && self.stubborn.contains(pid) {
                    continue;
                }
                let _ = fs::remove_dir_all(self.p.lock.proc_root.join(pid.to_string()));
            }
            if signal == "TERM" {
                if let Some(r) = self.reuse_on_term {
                    proc_with(self.p, r, "S", 1, 1, 9999);
                }
            }
            Ok(())
        }
        fn pause(&mut self, _d: Duration) {}
    }

    fn signals(p: &Place) -> FakeSignals<'_> {
        FakeSignals {
            p,
            stubborn: Vec::new(),
            reuse_on_term: None,
            sent: Vec::new(),
            guard_held: Vec::new(),
        }
    }

    const WAITS: (Duration, Duration) = (Duration::from_secs(1), Duration::from_secs(1));

    /// E11 (3): TERM, the journal says where it stopped, and `op recovered` is needed
    /// before the target takes a new operation.
    #[test]
    fn an_approved_stop_ends_the_tree_and_leaves_the_operation_failed() {
        let p = place("apply");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        let mut sig = signals(&p);
        let done = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap();
        assert_eq!(done["detail"], "stopped");
        assert_eq!(done["stopped_at"], "install");
        assert_eq!(
            sig.sent,
            [("TERM".to_owned(), vec![MAIN, CHILD, GRANDCHILD])],
            "no KILL when TERM was enough"
        );
        assert!(
            sig.guard_held.iter().all(|h| *h),
            "signalled under the guard"
        );
        assert!(p.lock.proc_root.join(SHELL.to_string()).exists());
        assert!(p.lock.proc_root.join(OTHER.to_string()).exists());
        let st = states(&p);
        assert_eq!(
            st.last().unwrap(),
            &(State::Failed, "stopped_at_install".to_owned())
        );
        assert_eq!(
            read_progress(&p.ops, OP).unwrap()["stopped"]["step"],
            "install"
        );
        // the target is held until a person closes it
        assert!(opstore::refuse_unfinished(&p.ops, "domain:new.example.test", "").is_err());
        let place = opstore::OpPlace {
            ops_dir: p.ops.clone(),
            lock: opstore::LockPaths {
                lock: p.lock.lock.clone(),
                proc_root: p.lock.proc_root.clone(),
                boot_id: p.lock.boot_id.clone(),
            },
            fingerprint: "f1".to_owned(),
        };
        proc_with(&p, std::process::id(), "R", 1, 1, 777);
        opstore::mark_recovered(&place, OP, T + 60).unwrap();
        assert!(opstore::refuse_unfinished(&p.ops, "domain:new.example.test", "").is_ok());
        // a second apply of the same plan finds nothing to stop
        let again =
            stop_apply(&p.facts(), &plan, false, &mut signals(&p), WAITS, T + 70).unwrap_err();
        assert!(again.starts_with("op_not_running"), "{again}");
        let _ = fs::remove_dir_all(&p.root);
    }

    #[test]
    fn what_outlives_term_gets_kill() {
        let p = place("kill");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        let mut sig = signals(&p);
        sig.stubborn = vec![GRANDCHILD];
        let done = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap();
        assert_eq!(
            sig.sent,
            [
                ("TERM".to_owned(), vec![MAIN, CHILD, GRANDCHILD]),
                ("KILL".to_owned(), vec![GRANDCHILD]),
            ]
        );
        assert_eq!(done["detail"], "stopped");
        assert_eq!(done["kill"], json!([GRANDCHILD]));
        let _ = fs::remove_dir_all(&p.root);
    }

    /// A pid that changes hands between TERM and KILL is not signalled again.
    #[test]
    fn a_pid_reused_during_the_stop_is_left_alone() {
        let p = place("reuse");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        let mut sig = signals(&p);
        sig.stubborn = vec![CHILD];
        sig.reuse_on_term = Some(CHILD);
        let done = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap();
        assert_eq!(sig.sent.len(), 1, "{:?}", sig.sent);
        assert_eq!(done["survivors"], json!([]));
        assert!(p.lock.proc_root.join(CHILD.to_string()).exists());
        let _ = fs::remove_dir_all(&p.root);
    }

    /// The plan is recomputed under the guard and must be the same, word for word.
    #[test]
    fn a_plan_that_no_longer_holds_signals_nothing() {
        let p = place("changed");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        // a new process under the operation since the plan
        proc_with(&p, 4103, "S", MAIN, MAIN, 530);
        let mut sig = signals(&p);
        let e = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap_err();
        assert!(e.starts_with("plan_changed"), "{e}");
        fs::remove_dir_all(p.lock.proc_root.join("4103")).unwrap();
        // an edited plan
        let mut edited = plan.clone();
        edited["step"] = json!("edge");
        let e = stop_apply(&p.facts(), &edited, false, &mut sig, WAITS, T + 50).unwrap_err();
        assert!(e.starts_with("plan_changed"), "{e}");
        // the operation moved to another step
        progress_file(&p, "identity", T, Some(500), BOOT);
        let e = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap_err();
        assert!(e.starts_with("plan_changed"), "{e}");
        // the pid was reused since the plan
        progress_file(&p, "install", T, Some(500), BOOT);
        proc_with(&p, MAIN, "S", SHELL, SHELL, 501);
        let e = stop_apply(&p.facts(), &plan, false, &mut sig, WAITS, T + 50).unwrap_err();
        assert!(e.starts_with("pid_reused"), "{e}");
        let e =
            stop_apply(&p.facts(), &json!({ "op": "x" }), false, &mut sig, WAITS, T).unwrap_err();
        assert!(e.starts_with("plan_changed"), "{e}");
        assert!(sig.sent.is_empty(), "{:?}", sig.sent);
        assert_eq!(states(&p).len(), 3, "the journal is as it was");
        let _ = fs::remove_dir_all(&p.root);
    }

    fn op_place(p: &Place) -> opstore::OpPlace {
        proc_with(p, std::process::id(), "R", 1, 1, 4242);
        opstore::OpPlace {
            ops_dir: p.ops.clone(),
            lock: opstore::LockPaths {
                lock: p.lock.lock.clone(),
                proc_root: p.lock.proc_root.clone(),
                boot_id: p.lock.boot_id.clone(),
            },
            fingerprint: "f1".to_owned(),
        }
    }

    /// E11 (1): every operation records its process and its step once it is running; a
    /// verified one leaves no progress file, a failed one keeps it.
    #[test]
    fn a_running_operation_records_its_process_and_step() {
        let p = place("journaled");
        let place = op_place(&p);
        let plan = json!({ "x": 1 });
        let mut seen_inside = None;
        opstore::run_op(
            &place,
            "site.create",
            "domain:a.test",
            &plan,
            opstore::BY_LOCAL_ROOT,
            T,
            |op_id| {
                let r = read_progress(&p.ops, op_id).unwrap();
                step("clone");
                seen_inside = Some((r, current_step(), op_id.to_owned()));
                Ok(())
            },
        )
        .unwrap();
        let (r, now_step, op_id) = seen_inside.unwrap();
        assert_eq!(r["pid"], std::process::id());
        assert_eq!(r["start_time"], 4242);
        assert_eq!(r["step"], "running");
        assert_eq!(now_step.as_deref(), Some("clone"));
        assert!(
            read_progress(&p.ops, &op_id).is_none(),
            "verified: no progress file"
        );
        assert_eq!(current_step(), None, "ended with the operation");
        let mut failed_op = String::new();
        let e = opstore::run_op(
            &place,
            "site.create",
            "domain:b.test",
            &plan,
            opstore::BY_LOCAL_ROOT,
            T,
            |op_id| {
                step("install");
                failed_op = op_id.to_owned();
                Err::<(), String>("boom".to_owned())
            },
        );
        assert!(e.is_err());
        assert_eq!(
            read_progress(&p.ops, &failed_op).unwrap()["step"],
            "install"
        );
        let _ = fs::remove_dir_all(&p.root);
    }

    /// E11: the operation's own last line waits while a stop holds the guard, so the
    /// stop's `failed` line and the operation's `verified` never interleave.
    #[test]
    #[allow(clippy::disallowed_methods)] // a test that must hold a lock for a moment
    fn a_running_operation_writes_its_last_line_only_outside_a_stop() {
        let p = place("guard");
        let place = op_place(&p);
        let plan = json!({ "x": 2 });
        let lock = opstore::LockPaths {
            lock: p.lock.lock.clone(),
            proc_root: p.lock.proc_root.clone(),
            boot_id: p.lock.boot_id.clone(),
        };
        let ops = p.ops.clone();
        let mut holder = None;
        opstore::run_op(
            &place,
            "site.create",
            "domain:c.test",
            &plan,
            opstore::BY_LOCAL_ROOT,
            T,
            |op_id| {
                let (tx, rx) = std::sync::mpsc::channel();
                let op_id = op_id.to_owned();
                holder = Some(std::thread::spawn(move || {
                    opstore::guarded(&lock, || {
                        tx.send(()).unwrap();
                        std::thread::sleep(Duration::from_millis(400));
                        let (entries, _) =
                            opstore::read_journal(&opstore::journal_path(&ops, &op_id).unwrap())
                                .unwrap();
                        Ok(entries.last().unwrap().state)
                    })
                    .unwrap()
                }));
                rx.recv().unwrap();
                Ok(())
            },
        )
        .unwrap();
        let seen_while_held = holder.unwrap().join().unwrap();
        assert_eq!(seen_while_held, State::Running);
        let _ = fs::remove_dir_all(&p.root);
    }

    /// f1fae 11-11 L1: work that still writes its progress gets a plan marked
    /// `not_stuck`; it is carried out only with `--even-if-running`. What `--plan` prints
    /// beside the seal (`now`) is dropped before the plan is compared.
    #[test]
    fn a_plan_for_work_still_moving_is_marked_and_needs_even_if_running() {
        let p = place("moving");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, Some(500), BOOT);
        let f = Facts {
            ops_dir: &p.ops,
            lock: &p.lock,
            fingerprint: "f1",
            stalled_after: 300,
        };
        let plan = plan_for_window(&f, OP, T + 50).unwrap();
        assert_eq!(plan["not_stuck"], true);
        assert_eq!(plan[NOW_KEY]["verdict"], "running");
        assert_eq!(plan[NOW_KEY]["why"], "updated_recently");
        assert_eq!(plan[NOW_KEY]["seconds_since_update"], 50);
        let line = plan[NOW_KEY]["line"].as_str().unwrap();
        assert!(
            line.starts_with("running 50s since the last update, at step install"),
            "{line}"
        );
        // the window part is not sealed: the sealed plan is the same without it
        let mut sealed = plan.clone();
        sealed.as_object_mut().unwrap().remove(NOW_KEY);
        assert_eq!(sealed, stop_plan(&f, OP, T + 50).unwrap());
        let mut sig = signals(&p);
        let e = stop_apply(&f, &plan, false, &mut sig, WAITS, T + 50).unwrap_err();
        assert!(e.starts_with("not_stuck_needs_even_if_running"), "{e}");
        assert!(sig.sent.is_empty(), "{:?}", sig.sent);
        assert_eq!(states(&p).len(), 3, "the journal is as it was");
        let done = stop_apply(&f, &plan, true, &mut sig, WAITS, T + 50).unwrap();
        assert_eq!(done["detail"], "stopped");
        assert_eq!(states(&p).last().unwrap().0, State::Failed);
        // a stalled one needs no second word, and its plan says so
        let q = place("stalled");
        tree(&q);
        running(&q);
        progress_file(&q, "install", T, Some(500), BOOT);
        let g = Facts {
            ops_dir: &q.ops,
            lock: &q.lock,
            fingerprint: "f1",
            stalled_after: 300,
        };
        let plan = plan_for_window(&g, OP, T + 400).unwrap();
        assert_eq!(plan["not_stuck"], false);
        assert_eq!(plan[NOW_KEY]["verdict"], "stalled");
        let done = stop_apply(&g, &plan, false, &mut signals(&q), WAITS, T + 400).unwrap();
        assert_eq!(done["detail"], "stopped");
        let _ = fs::remove_dir_all(&p.root);
        let _ = fs::remove_dir_all(&q.root);
    }

    /// 33e28 N1: the work is done, but a stop approved meanwhile comes before the
    /// operation writes `verified`. The journal keeps `failed / stopped_at_<step>` (a person
    /// checks before `op recovered`), the operation's own `verified` is refused, and the
    /// plan says the work may have finished.
    #[test]
    fn a_stop_while_the_work_waits_to_write_verified_leaves_it_failed() {
        let p = place("n1");
        let place = op_place(&p);
        let plan = json!({ "x": 3 });
        let mut seen = None;
        let r = opstore::run_op(
            &place,
            "site.create",
            "domain:d.test",
            &plan,
            opstore::BY_LOCAL_ROOT,
            T,
            |op_id| {
                step("install");
                // the work itself is done here
                let f = p.facts();
                let now = crate::probe::unix_now() + 3600;
                let stop = stop_plan(&f, op_id, now).unwrap();
                let done = stop_apply(&f, &stop, false, &mut signals(&p), WAITS, now).unwrap();
                seen = Some((op_id.to_owned(), stop, done));
                Ok(())
            },
        );
        let (op_id, stop, done) = seen.unwrap();
        assert!(r.is_err(), "verified is not written after a stop");
        assert_eq!(done["detail"], "stopped");
        let then = stop["then"].as_str().unwrap();
        assert!(then.contains("may have finished its work"), "{then}");
        assert!(then.contains(&format!("op recovered {op_id}")), "{then}");
        let journal = opstore::read_journal(&opstore::journal_path(&p.ops, &op_id).unwrap())
            .unwrap()
            .0;
        let last = journal.last().unwrap();
        assert_eq!(
            (last.state, last.code.as_str()),
            (State::Failed, "stopped_at_install")
        );
        let _ = fs::remove_dir_all(&p.root);
    }

    /// 33e28 N3: a progress file without its start time takes the one the host lock
    /// holds for the same operation and pid — never another pid's.
    #[test]
    fn a_progress_file_without_start_time_takes_the_locks() {
        let p = place("n3");
        tree(&p);
        running(&p);
        progress_file(&p, "install", T, None, BOOT);
        let lock = |pid: u32| {
            fs::write(
                &p.lock.lock,
                json!({ "op": OP, "pid": pid, "start_time": 500, "boot_id": BOOT }).to_string(),
            )
            .unwrap();
        };
        lock(MAIN);
        let plan = stop_plan(&p.facts(), OP, T + 50).unwrap();
        assert_eq!(plan["start_time"], 500);
        assert_eq!(
            status(&p.facts(), OP, T + 50, 300).unwrap()["liveness"],
            "alive"
        );
        lock(4999);
        let e = stop_plan(&p.facts(), OP, T + 50).unwrap_err();
        assert!(e.starts_with("start_time_unreadable"), "{e}");
        let _ = fs::remove_dir_all(&p.root);
    }
}
