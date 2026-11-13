//! The one GET-only way aib-drive and aib-expect read GitHub (DECISION-drive-
//! expect-cycle, a9136 11-12): [`Read`], [`GhRead`] through `gh api`, and
//! [`run_fixed`], a program with a fixed argument list and a deadline. Moved
//! out of aib-drive unchanged so aib-drive can depend on aib-expect without a
//! cycle; aib-drive keeps the old paths with `pub use`.

use serde_json::Value;
use std::io::Read as _;
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

/// A GET-only view of the GitHub API.
pub trait Read {
    /// `path` is an API path such as `repos/o/r/pulls?state=open`.
    fn get(&self, path: &str) -> Result<Value, String>;
}

/// [`Read`] through `gh api` (the token stays with `gh`).
pub struct GhRead {
    /// The `gh` program (tests point it at a fake).
    pub program: String,
    /// A call past this is killed and is an error.
    pub timeout: Duration,
}

impl GhRead {
    pub fn new() -> Self {
        Self {
            program: "gh".to_owned(),
            timeout: CALL_TIMEOUT,
        }
    }

    /// The whole argument list: `api` and the path, nothing that could
    /// change the method or send a body.
    pub fn args(path: &str) -> Result<Vec<String>, String> {
        // only REST paths under repos/ (1ced7 #685 N1: `graphql` is a POST in gh)
        if !path.starts_with("repos/") || path.contains(char::is_whitespace) {
            return Err(format!("path refused: {path:?}"));
        }
        Ok(vec!["api".to_owned(), path.to_owned()])
    }
}

impl Default for GhRead {
    fn default() -> Self {
        Self::new()
    }
}

impl Read for GhRead {
    fn get(&self, path: &str) -> Result<Value, String> {
        let out = run_fixed(&self.program, &Self::args(path)?, self.timeout)
            .map_err(|e| format!("gh api {path}: {e}"))?;
        serde_json::from_slice(&out).map_err(|_| format!("gh api {path}: bad json"))
    }
}
