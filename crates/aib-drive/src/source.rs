//! What the snapshot layer runs and reads besides the GitHub API (PR-B,
//! a9136 on 2240e's d65adb73):
//!
//! - [`run_fixed`]: a program with a fixed argument list (no shell) and a
//!   deadline. A run past the deadline is killed and is an error; so is a
//!   non-zero exit, even with output.
//! - [`Queue`]: the dm queue, read only (`aib dm-queue list`), for
//!   `busy_seats`.
//! - [`read_notes`]: the first line of every `REVIEW-*` / `VERIFY-*` file.
//!   A folder or a file that cannot be read is an error, never "no reviews".

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Read as _;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

/// Default deadline of one `gh` / `aib` call.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(30);

// Named exception (clippy.toml, ADR 0005): the deadline of one child process is a
// channel wait on the threads that drain its output and wait for it. aib-drive is not
// aibd and has no timer registry; nothing here ticks or sleeps.
#[allow(clippy::disallowed_methods)]
/// Runs `program args…` (no shell) and returns stdout on exit 0. stdout,
/// stderr and the wait for the exit each run on a thread; one deadline
/// covers all three (1ced7 #685 L3: a child that closes its output and
/// keeps running, or a grandchild holding stderr, must not hang the call).
/// Past the deadline the child is killed and the call is an error; a
/// non-zero exit is an error even with output.
pub fn run_fixed(program: &str, args: &[String], timeout: Duration) -> Result<Vec<u8>, String> {
    let deadline = Instant::now() + timeout;
    let left = || deadline.saturating_duration_since(Instant::now());
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("{program} not run: {}", e.kind()))?;
    let (Some(mut out), Some(mut err)) = (child.stdout.take(), child.stderr.take()) else {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("{program}: no pipes"));
    };
    let pid = child.id();
    let (out_tx, out_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut b = Vec::new();
        let _ = out_tx.send(out.read_to_end(&mut b).map(|_| b));
    });
    let (err_tx, err_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut b = Vec::new();
        let _ = err.read_to_end(&mut b);
        let _ = err_tx.send(b);
    });
    // the waiter reaps the child; once it has, `pid` may be given to another
    // process. So a kill by pid first asks the waiter: only a child it is still
    // waiting for is killed (1ced7 #685 r2 L1: a grandchild holding stdout
    // used to get its exited parent's pid killed at the deadline). What is
    // left is the waiter's step from the reap to the send, not the call.
    let (exit_tx, exit_rx) = mpsc::channel();
    thread::spawn(move || {
        let _ = exit_tx.send(child.wait());
    });
    let timed_out = || -> Result<Vec<u8>, String> {
        let ms = timeout.as_millis();
        if let Err(TryRecvError::Empty) = exit_rx.try_recv() {
            kill_pid(pid);
            return Err(format!("{program}: timed out after {ms} ms"));
        }
        Err(format!(
            "{program}: timed out after {ms} ms (exited, output held open)"
        ))
    };
    let stdout = match out_rx.recv_timeout(left()) {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            if matches!(exit_rx.try_recv(), Err(TryRecvError::Empty)) {
                kill_pid(pid);
            }
            return Err(format!("{program}: stdout unreadable: {}", e.kind()));
        }
        Err(_) => return timed_out(),
    };
    let status = match exit_rx.recv_timeout(left()) {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => return Err(format!("{program}: wait failed: {}", e.kind())),
        Err(_) => return timed_out(),
    };
    if !status.success() {
        // stderr only names the failure: one held open by a grandchild is
        // not waited for past the deadline
        let stderr = err_rx.recv_timeout(left()).unwrap_or_default();
        let text = String::from_utf8_lossy(&stderr);
        let last = text.trim().lines().last().unwrap_or_default();
        let code = status
            .code()
            .map_or_else(|| "signal".to_owned(), |c| c.to_string());
        return Err(format!("{program}: exit {code}: {last}"));
    }
    Ok(stdout)
}

/// Kill `pid`. Callers first make sure the waiter has not reaped it.
fn kill_pid(pid: u32) {
    let pid = pid.to_string();
    #[cfg(windows)]
    let (program, args) = ("taskkill", vec!["/F", "/T", "/PID", pid.as_str()]);
    #[cfg(not(windows))]
    let (program, args) = ("kill", vec!["-KILL", pid.as_str()]);
    let _ = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// The dm queue, read only.
pub trait Queue {
    /// The waiting tab of `seat` (a JSON list of items).
    fn waiting(&self, seat: &str) -> Result<Value, String>;
}

/// [`Queue`] through `aib dm-queue list`.
pub struct AibQueue {
    /// The `aib` program (tests point it at a fake).
    pub program: String,
    pub timeout: Duration,
}

impl AibQueue {
    pub fn new() -> Self {
        Self {
            program: "aib".to_owned(),
            timeout: CALL_TIMEOUT,
        }
    }

    /// The whole argument list; a seat that is not five lowercase hex
    /// characters is refused (it goes into argv).
    pub fn args(seat: &str) -> Result<Vec<String>, String> {
        if !is_seat(seat) {
            return Err(format!("seat refused: {seat:?}"));
        }
        Ok([
            "dm-queue", "list", "--seat", seat, "--tab", "waiting", "--origin", "live",
        ]
        .map(str::to_owned)
        .to_vec())
    }
}

impl Default for AibQueue {
    fn default() -> Self {
        Self::new()
    }
}

impl Queue for AibQueue {
    fn waiting(&self, seat: &str) -> Result<Value, String> {
        let out = run_fixed(&self.program, &Self::args(seat)?, self.timeout)?;
        serde_json::from_slice(&out).map_err(|_| format!("dm {seat}: bad json"))
    }
}

/// Five lowercase hex characters (a seat code).
pub fn is_seat(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// Seats with unread urgent mail waiting. Any queue that cannot be read
/// fails the whole set: an empty set would read as "nobody is busy".
pub fn busy_seats(q: &dyn Queue, seats: &[String]) -> Result<BTreeSet<String>, String> {
    let mut out = BTreeSet::new();
    for seat in seats {
        let v = q.waiting(seat).map_err(|e| format!("dm {seat}: {e}"))?;
        let items = v
            .as_array()
            .ok_or_else(|| format!("dm {seat}: not a list"))?;
        // every item is checked: one that does not read is not "not urgent"
        // (Codex #685 M2: `[null]` or a wrong type used to read as not busy)
        let mut urgent_unread = false;
        for (n, i) in items.iter().enumerate() {
            let bad = |what: &str| format!("dm {seat}: item {n}: {what}");
            let priority = i
                .get("priority")
                .and_then(Value::as_str)
                .ok_or_else(|| bad("priority not a string"))?;
            let unread = match i.get("read_at") {
                Some(Value::Null) => true,
                Some(Value::Number(_) | Value::String(_)) => false,
                _ => return Err(bad("read_at missing or not a time")),
            };
            urgent_unread |= priority == "urgent" && unread;
        }
        if urgent_unread {
            out.insert(seat.clone());
        }
    }
    Ok(out)
}

/// One review / acceptance file: its name and first line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileLine {
    pub name: String,
    pub line: String,
}

/// The first line of every `REVIEW-*.md` / `VERIFY-*.md` in `dir`, sorted by
/// name. A folder or a matching file that cannot be read is an error: left
/// out, it would read as "not reviewed" and ask for a review again.
pub fn read_notes(dir: &Path) -> Result<Vec<FileLine>, String> {
    let rd =
        std::fs::read_dir(dir).map_err(|e| format!("notes {}: {}", dir.display(), e.kind()))?;
    let mut out = Vec::new();
    for e in rd {
        let e = e.map_err(|e| format!("notes {}: {}", dir.display(), e.kind()))?;
        // a name that is not UTF-8 but looks like a note is an error too
        let raw = e.file_name();
        let Some(name) = raw.to_str().map(str::to_owned) else {
            let lossy = raw.to_string_lossy();
            if lossy.starts_with("REVIEW-") || lossy.starts_with("VERIFY-") {
                return Err(format!("note {lossy}: name not UTF-8"));
            }
            continue;
        };
        if !(name.starts_with("REVIEW-") || name.starts_with("VERIFY-")) || !name.ends_with(".md") {
            continue;
        }
        let text = std::fs::read_to_string(e.path())
            .map_err(|err| format!("note {name}: {}", err.kind()))?;
        let line = text.lines().next().unwrap_or_default().to_owned();
        out.push(FileLine { name, line });
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}
