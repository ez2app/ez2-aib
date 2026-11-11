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
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

/// Default deadline of one `gh` / `aib` call.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(30);

// Named exception (clippy.toml, ADR 0005): the deadline of one child process is a
// channel wait on the thread draining its stdout. aib-drive is not aibd and has no
// timer registry; nothing here ticks or sleeps.
#[allow(clippy::disallowed_methods)]
/// Runs `program args…` (no shell) and returns stdout on exit 0. Output is
/// drained on threads, so a full pipe cannot stall the child; the deadline is
/// a channel wait, not a sleep loop. Past the deadline the child is killed.
pub fn run_fixed(program: &str, args: &[String], timeout: Duration) -> Result<Vec<u8>, String> {
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
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut b = Vec::new();
        let _ = tx.send(out.read_to_end(&mut b).map(|_| b));
    });
    let err_reader = thread::spawn(move || {
        let mut b = Vec::new();
        let _ = err.read_to_end(&mut b);
        b
    });
    let stdout = match rx.recv_timeout(timeout) {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{program}: stdout unreadable: {}", e.kind()));
        }
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "{program}: timed out after {} ms",
                timeout.as_millis()
            ));
        }
    };
    let status = child
        .wait()
        .map_err(|e| format!("{program}: wait failed: {}", e.kind()))?;
    let stderr = err_reader.join().unwrap_or_default();
    if !status.success() {
        let text = String::from_utf8_lossy(&stderr);
        let last = text.trim().lines().last().unwrap_or_default();
        let code = status
            .code()
            .map_or_else(|| "signal".to_owned(), |c| c.to_string());
        return Err(format!("{program}: exit {code}: {last}"));
    }
    Ok(stdout)
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
        let urgent_unread = items.iter().any(|i| {
            i.get("priority").and_then(Value::as_str) == Some("urgent")
                && i.get("read_at").is_none_or(Value::is_null)
        });
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
