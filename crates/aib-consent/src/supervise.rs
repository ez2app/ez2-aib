// SPDX-License-Identifier: Apache-2.0
//! Run one approved job and watch it: output into the log as it comes, a small state file
//! a seat can read, a question to the person when the job has been quiet or long, and a
//! stop of the job's whole process group only when the person says so (or the hard limit
//! is reached).
//!
//! - The job is started in its own process group (`process_group(0)`); this supervisor
//!   stays in its own. Only this supervisor, which still holds the job, ever signals that
//!   group: a group number read back from a state file could have been reused.
//! - The question is asked by a separate program (`ask`, given by the caller: the Python
//!   half that owns the dialog and Touch ID). Its exit code 0 means "stop"; anything else,
//!   a timeout included, means "keep waiting". Asking runs on its own thread, so the hard
//!   limit and the job ending are seen while a dialog is open; a dialog left open when
//!   the job ends is closed (its process is killed).
//! - A seat asks for a stop by creating the stop-request file; that only leads to the same
//!   question, never to a stop by itself. After the person said "keep waiting", requests
//!   within `ask_every` are dropped and counted, not asked (2240e #661 L1: a seat must not be
//!   able to make the dialog come back every tick).
//! - A pinned program ([`Job::script`]) is not run from its path: the bytes checked against
//!   the pin are written into a pipe and the interpreter reads `/dev/fd/<n>` (2240e #661 M2:
//!   bash reads a script as it runs, so a file changed after the check would be run changed).
//!   What runs is what was checked; a same-user process can still kill or trace the job.
//! - "Quiet" is measured on a monotonic clock from the start and from the last byte read
//!   (a line end is not needed). After "keep waiting" the next question waits a full
//!   quiet period again.
//! - Stopping: TERM to the group, up to `grace` for the leader to end and the group to be
//!   empty, then KILL to the group. When the leader ends on its own and members of its
//!   group are still there, they get the same TERM / KILL. A grandchild that moved to a
//!   group or session of its own is not reached; the record says what was seen, nothing more.

use nix::errno::Errno;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::fs;
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Defaults; a request may only make them shorter.
pub const QUIET_DEFAULT: Duration = Duration::from_secs(180);
pub const ASK_AFTER_DEFAULT: Duration = Duration::from_secs(600);
pub const ASK_EVERY_DEFAULT: Duration = Duration::from_secs(600);
pub const HARD_DEFAULT: Duration = Duration::from_secs(20 * 60);
pub const GRACE_DEFAULT: Duration = Duration::from_secs(15);
/// The longest one question may take (dialog + Touch ID); after that it counts as "keep waiting".
pub const ASK_LIMIT_DEFAULT: Duration = Duration::from_secs(300);
/// Lines kept for the state file and the record.
pub const TAIL_LINES: usize = 3;
/// A line longer than this is cut into pieces for the tail.
pub const TAIL_LINE_MAX: usize = 300;
/// In [`Job::argv`], stands for the pipe the checked script is read from.
pub const SCRIPT_ARG: &str = "@aib-consent-script";

/// Everything one run needs.
#[derive(Debug, Clone)]
pub struct Job {
    pub id: String,
    pub argv: Vec<String>,
    /// The only environment the job gets (the inherited one is cleared).
    pub env: Vec<(String, String)>,
    pub cwd: PathBuf,
    pub log: PathBuf,
    pub state: PathBuf,
    pub stop_request: PathBuf,
    /// The program that asks the person; `--id <id> --why <quiet|long|request>
    /// --elapsed <s> --quiet <s>` are added.
    pub ask: Vec<String>,
    pub quiet: Duration,
    pub ask_after: Duration,
    pub ask_every: Duration,
    pub hard: Duration,
    pub grace: Duration,
    pub ask_limit: Duration,
    /// How often the loop looks (tests make it short).
    pub tick: Duration,
    /// The checked bytes of a pinned program: every [`SCRIPT_ARG`] in `argv` becomes
    /// `/dev/fd/<n>` of a pipe these bytes are written into. `None`: `argv` runs as it is.
    pub script: Option<Vec<u8>>,
}

/// The quiet period a request may set: 60 seconds to the hard limit (c477a: vzdump and rclone
/// are often quiet for minutes, so a request may make it longer than the default too).
pub fn quiet_period(requested: Option<Duration>) -> Duration {
    match requested {
        Some(d) if d >= Duration::from_secs(60) && d <= HARD_DEFAULT => d,
        _ => QUIET_DEFAULT,
    }
}

/// Take a requested limit only when it is shorter than the default.
pub fn shorter(requested: Option<Duration>, default: Duration) -> Duration {
    match requested {
        Some(d) if !d.is_zero() && d < default => d,
        _ => default,
    }
}

/// Characters a terminal or a dialog could act on are dropped from what the tail keeps.
pub fn plain_line(s: &str) -> String {
    s.chars()
        .filter(|c| {
            !c.is_control()
                && !matches!(*c as u32,
                    0x200b..=0x200f | 0x202a..=0x202e | 0x2060..=0x2064 | 0x2066..=0x2069 | 0xfeff)
        })
        .take(TAIL_LINE_MAX)
        .collect()
}

#[derive(Default)]
struct Seen {
    bytes: AtomicU64,
    /// Milliseconds since the start at the last byte read.
    last_ms: AtomicU64,
    eof: AtomicBool,
    log_failed: AtomicBool,
    tail: Mutex<(VecDeque<String>, Vec<u8>)>,
}

impl Seen {
    fn take(&self, start: Instant, chunk: &[u8]) {
        self.bytes.fetch_add(chunk.len() as u64, Ordering::Relaxed);
        self.last_ms
            .store(start.elapsed().as_millis() as u64, Ordering::Relaxed);
        let mut g = self.tail.lock().unwrap_or_else(|e| e.into_inner());
        let (lines, partial) = &mut *g;
        for &b in chunk {
            if b == b'\n' || partial.len() >= TAIL_LINE_MAX * 4 {
                let l = plain_line(&String::from_utf8_lossy(partial));
                partial.clear();
                if !l.trim().is_empty() {
                    lines.push_back(l);
                    while lines.len() > TAIL_LINES {
                        lines.pop_front();
                    }
                }
                if b == b'\n' {
                    continue;
                }
            }
            partial.push(b);
        }
    }

    fn tail(&self) -> Vec<String> {
        let g = self.tail.lock().unwrap_or_else(|e| e.into_inner());
        let mut v: Vec<String> = g.0.iter().cloned().collect();
        let p = plain_line(&String::from_utf8_lossy(&g.1));
        if !p.trim().is_empty() {
            v.push(p);
            if v.len() > TAIL_LINES {
                v.remove(0);
            }
        }
        v
    }
}

/// The one wait in this crate: the supervisor's poll of a child it holds (ADR 0005 named
/// exception, like probe.rs `wait_with_timeout`). It is a separate short-lived program, not
/// part of aibd, so aibd's registered timers are not there to use.
#[allow(clippy::disallowed_methods)]
fn pause(d: Duration) {
    thread::sleep(d);
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn write_state(path: &PathBuf, v: &Value) {
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension("state.tmp");
    let ok = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .and_then(|mut f| f.write_all(v.to_string().as_bytes()));
    if ok.is_ok() {
        let _ = fs::rename(&tmp, path);
    }
}

/// Is any process still in group `pg`? Errors other than "no such group" count as "yes"
/// (we cannot tell, so we do not say it is gone).
fn group_alive(pg: Pid) -> bool {
    !matches!(killpg(pg, None), Err(Errno::ESRCH))
}

fn signal_group(pg: Pid, sig: Signal, errors: &mut Vec<String>) {
    match killpg(pg, sig) {
        Ok(()) | Err(Errno::ESRCH) => {}
        Err(e) => errors.push(format!("{}:{e}", sig.as_str())),
    }
}

/// TERM, wait up to `grace` for the leader to end and the group to empty, then KILL.
/// Returns (the leader's status if it ended, whether KILL was needed, errors).
fn stop_group(
    child: &mut Child,
    pg: Pid,
    grace: Duration,
    tick: Duration,
    mut status: Option<ExitStatus>,
) -> (Option<ExitStatus>, bool, Vec<String>) {
    let mut errors = Vec::new();
    signal_group(pg, Signal::SIGTERM, &mut errors);
    let until = Instant::now() + grace;
    loop {
        if status.is_none() {
            status = child.try_wait().ok().flatten();
        }
        if status.is_some() && !group_alive(pg) {
            return (status, false, errors);
        }
        if Instant::now() >= until {
            break;
        }
        pause(tick);
    }
    signal_group(pg, Signal::SIGKILL, &mut errors);
    if status.is_none() {
        status = child.wait().ok();
    }
    let short = Instant::now() + Duration::from_secs(2);
    while group_alive(pg) && Instant::now() < short {
        pause(tick);
    }
    if group_alive(pg) {
        errors.push("group_left_after_kill".into());
    }
    (status, true, errors)
}

/// One question to the person, on its own thread. `Some(true)` = stop.
struct Asking {
    why: String,
    started: Instant,
    pid: Option<u32>,
    answer: Arc<Mutex<Option<bool>>>,
}

fn start_asking(job: &Job, why: &str, elapsed: Duration, quiet: Duration) -> Asking {
    let answer = Arc::new(Mutex::new(None));
    let mut pid = None;
    let mut cmd = Command::new(
        job.ask
            .first()
            .map(String::as_str)
            .unwrap_or("/usr/bin/false"),
    );
    cmd.args(job.ask.iter().skip(1))
        .args([
            "--id",
            &job.id,
            "--why",
            why,
            "--elapsed",
            &elapsed.as_secs().to_string(),
            "--quiet",
            &quiet.as_secs().to_string(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    match cmd.spawn() {
        Ok(mut c) => {
            pid = Some(c.id());
            let a = Arc::clone(&answer);
            thread::spawn(move || {
                let stop = c.wait().map(|s| s.success()).unwrap_or(false);
                *a.lock().unwrap_or_else(|e| e.into_inner()) = Some(stop);
            });
        }
        Err(_) => *answer.lock().unwrap_or_else(|e| e.into_inner()) = Some(false),
    }
    Asking {
        why: why.to_owned(),
        started: Instant::now(),
        pid,
        answer,
    }
}

fn close_asking(a: &Asking) {
    if let Some(pid) = a.pid {
        let _ = nix::sys::signal::kill(Pid::from_raw(pid as i32), Signal::SIGTERM);
    }
}

/// A pipe for the script: the write end stays close-on-exec (only we write), the read end
/// is inherited by the job (and only by the job: it is dropped right after the spawn, before
/// any question is asked).
fn script_pipe() -> std::io::Result<(std::io::PipeReader, std::io::PipeWriter)> {
    use nix::fcntl::{fcntl, FcntlArg, FdFlag};
    let (r, w) = std::io::pipe()?;
    fcntl(&r, FcntlArg::F_SETFD(FdFlag::empty())).map_err(std::io::Error::from)?;
    Ok((r, w))
}

fn status_json(s: Option<ExitStatus>) -> (Value, Value) {
    match s {
        Some(s) => (json!(s.code()), json!(s.signal())),
        None => (Value::Null, Value::Null),
    }
}

/// Run the job to its end. Returns the record (also the last state written).
pub fn supervise(job: &Job) -> Value {
    use std::os::unix::fs::OpenOptionsExt;
    let start = Instant::now();
    let started_unix = now_unix();
    let log = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&job.log);
    let mut log = match log {
        Ok(f) => f,
        Err(e) => return json!({"id": job.id, "result": "failed", "why": format!("log_open:{e}")}),
    };
    let Some(prog) = job.argv.first() else {
        return json!({"id": job.id, "result": "failed", "why": "argv_empty"});
    };
    let (reader, writer) = match std::io::pipe() {
        Ok(p) => p,
        Err(e) => return json!({"id": job.id, "result": "failed", "why": format!("pipe:{e}")}),
    };
    let writer2 = match writer.try_clone() {
        Ok(w) => w,
        Err(e) => return json!({"id": job.id, "result": "failed", "why": format!("pipe:{e}")}),
    };
    // a pinned program: its checked bytes go through a pipe whose read end the job inherits
    let script = match &job.script {
        None => None,
        Some(bytes) => match script_pipe() {
            Ok((r, w)) => Some((r, w, bytes.clone())),
            Err(e) => {
                return json!({"id": job.id, "result": "failed", "why": format!("script_pipe:{e}")})
            }
        },
    };
    let fd_arg = script
        .as_ref()
        .map(|(r, _, _)| format!("/dev/fd/{}", r.as_raw_fd()));
    let argv: Vec<&str> = job
        .argv
        .iter()
        .map(|a| match (&fd_arg, a.as_str()) {
            (Some(fd), SCRIPT_ARG) => fd.as_str(),
            _ => a.as_str(),
        })
        .collect();
    if fd_arg.is_none() && argv.contains(&SCRIPT_ARG) {
        return json!({"id": job.id, "result": "failed", "why": "script_missing"});
    }
    let mut cmd = Command::new(prog);
    cmd.args(&argv[1..])
        .env_clear()
        .envs(job.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        .current_dir(&job.cwd)
        .stdin(Stdio::null())
        .stdout(writer)
        .stderr(writer2)
        .process_group(0);
    let spawned = cmd.spawn();
    drop(cmd); // our copies of the write end go away; only the job holds it now
    if let Some((r, w, bytes)) = script {
        drop(r); // only the job reads the script now
        let mut w = w;
        // a job that never reads its script (it failed to start) ends the write with EPIPE
        thread::spawn(move || {
            let _ = w.write_all(&bytes);
        });
    }
    let mut child = match spawned {
        Ok(c) => c,
        Err(e) => {
            return json!({"id": job.id, "result": "failed", "why": format!("spawn:{}", e.kind())})
        }
    };
    let pg = Pid::from_raw(child.id() as i32);

    let seen = Arc::new(Seen::default());
    {
        let seen = Arc::clone(&seen);
        let mut reader = reader;
        thread::spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        seen.take(start, &buf[..n]);
                        if !seen.log_failed.load(Ordering::Relaxed)
                            && log.write_all(&buf[..n]).is_err()
                        {
                            seen.log_failed.store(true, Ordering::Relaxed);
                        }
                    }
                }
            }
            seen.eof.store(true, Ordering::Relaxed);
        });
    }

    let mut asking: Option<Asking> = None;
    let mut asks: u32 = 0;
    let mut requests_ignored: u32 = 0;
    let mut requests_quiet_until = Duration::ZERO; // after "keep waiting", requests are dropped until here
    let mut next_long = job.ask_after;
    let mut quiet_from = Duration::ZERO; // after "keep waiting", quiet counts from here at the earliest
    let mut last_state: Option<Instant> = None;
    let mut status: Option<ExitStatus> = None;
    let (result, stopped_by, killed, errors): (&str, Option<&str>, bool, Vec<String>);

    loop {
        let elapsed = start.elapsed();
        if status.is_none() {
            status = child.try_wait().ok().flatten();
        }
        if let Some(s) = status {
            if let Some(a) = asking.take() {
                close_asking(&a);
            }
            if group_alive(pg) {
                // the leader ended and left members of its group behind
                let (_, k, e) = stop_group(&mut child, pg, job.grace, job.tick, Some(s));
                result = "exited";
                stopped_by = Some("leftovers");
                killed = k;
                errors = e;
            } else {
                result = "exited";
                stopped_by = None;
                killed = false;
                errors = Vec::new();
            }
            break;
        }
        if elapsed >= job.hard {
            if let Some(a) = asking.take() {
                close_asking(&a);
            }
            let (s, k, e) = stop_group(&mut child, pg, job.grace, job.tick, None);
            status = s;
            result = "hard_timeout";
            stopped_by = Some("hard_limit");
            killed = k;
            errors = e;
            break;
        }
        let last_byte = Duration::from_millis(seen.last_ms.load(Ordering::Relaxed));
        let quiet = elapsed.saturating_sub(last_byte.max(quiet_from));
        if let Some(a) = &asking {
            let answer = *a.answer.lock().unwrap_or_else(|e| e.into_inner());
            let timed_out = a.started.elapsed() >= job.ask_limit;
            if answer == Some(true) {
                asking = None;
                // the job may have ended while the person was answering
                status = child.try_wait().ok().flatten();
                if status.is_none() {
                    let (s, k, e) = stop_group(&mut child, pg, job.grace, job.tick, None);
                    status = s;
                    result = "stopped";
                    stopped_by = Some("person");
                    killed = k;
                    errors = e;
                    break;
                }
                continue;
            }
            if answer == Some(false) || timed_out {
                if timed_out {
                    close_asking(a);
                }
                asking = None;
                quiet_from = start.elapsed();
                requests_quiet_until = quiet_from + job.ask_every;
                if elapsed >= next_long {
                    next_long = elapsed + job.ask_every;
                }
            }
        } else {
            let mut request = job.stop_request.exists();
            if request && elapsed < requests_quiet_until {
                let _ = fs::remove_file(&job.stop_request);
                requests_ignored += 1;
                request = false;
            }
            let why = if request {
                Some("request")
            } else if quiet >= job.quiet {
                Some("quiet")
            } else if elapsed >= next_long {
                Some("long")
            } else {
                None
            };
            if let Some(why) = why {
                if request {
                    let _ = fs::remove_file(&job.stop_request);
                }
                asks += 1;
                asking = Some(start_asking(job, why, elapsed, quiet));
            }
        }
        if last_state.is_none_or(|t| t.elapsed() >= Duration::from_secs(2)) {
            last_state = Some(Instant::now());
            write_state(
                &job.state,
                &json!({
                    "id": job.id, "phase": if asking.is_some() { "asking" } else { "running" },
                    "asking_why": asking.as_ref().map(|a| a.why.as_str()),
                    "pid": child.id(), "started": started_unix, "elapsed_s": elapsed.as_secs(),
                    "quiet_s": quiet.as_secs(), "bytes": seen.bytes.load(Ordering::Relaxed),
                    "asks": asks, "requests_ignored": requests_ignored, "tail": seen.tail(),
                    "log": job.log,
                }),
            );
        }
        pause(job.tick);
    }

    let (rc, sig) = status_json(status);
    let record = json!({
        "id": job.id, "result": result, "rc": rc, "signal": sig, "stopped_by": stopped_by,
        "kill_needed": killed, "stop_errors": errors, "asks": asks, "requests_ignored": requests_ignored,
        "elapsed_s": start.elapsed().as_secs(), "bytes": seen.bytes.load(Ordering::Relaxed),
        "tail": seen.tail(), "log": job.log, "log_failed": seen.log_failed.load(Ordering::Relaxed),
        "remote": if stopped_by.is_some() { json!("not_confirmed") } else { Value::Null },
    });
    write_state(
        &job.state,
        &json!({"id": job.id, "phase": "done", "record": record}),
    );
    record
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_quiet_period_stays_between_a_minute_and_the_hard_limit() {
        assert_eq!(
            quiet_period(Some(Duration::from_secs(600))),
            Duration::from_secs(600)
        );
        assert_eq!(quiet_period(Some(Duration::from_secs(30))), QUIET_DEFAULT);
        assert_eq!(quiet_period(Some(Duration::from_secs(1300))), QUIET_DEFAULT);
        assert_eq!(quiet_period(None), QUIET_DEFAULT);
    }

    #[test]
    fn requests_may_only_shorten() {
        let d = Duration::from_secs(180);
        assert_eq!(
            shorter(Some(Duration::from_secs(60)), d),
            Duration::from_secs(60)
        );
        assert_eq!(shorter(Some(Duration::from_secs(900)), d), d);
        assert_eq!(shorter(Some(Duration::ZERO), d), d);
        assert_eq!(shorter(None, d), d);
    }

    #[test]
    fn the_tail_drops_controls_and_keeps_three_lines() {
        let s = Seen::default();
        let t = Instant::now();
        s.take(
            t,
            "one\ntwo\n\x1b[31mred\x1b[0m\nfour\u{202e}x\npart".as_bytes(),
        );
        assert_eq!(s.tail(), vec!["[31mred[0m", "fourx", "part"]);
        assert!(s.bytes.load(Ordering::Relaxed) > 0);
    }
}
