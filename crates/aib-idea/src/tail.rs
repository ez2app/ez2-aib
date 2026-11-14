//! The last part of `aib-idea footer`: 應合未合 / 應修未修 from `aib-slice due` (S73l,
//! IFACE-s73l-aib-slice-due-1ced7-1114.md r1 §2 / §5 / §7; F8).
//!
//! - The program is `aib-slice` (`aib-slice.exe` first on Windows) in the folder this
//!   `aib-idea` runs from; PATH is never searched. `AIB_IDEA_SLICE_BIN` names another program
//!   and `AIB_IDEA_SLICE_TIMEOUT_MS` another limit: both are for tests.
//! - It runs as `aib-slice due --seat X --no-fetch` with no shell, stdin closed, stderr
//!   dropped, for at most [`TIMEOUT`]. When the time is up, the child this call started is
//!   killed (its own handle, never a pid looked up again).
//! - rc 0 or 2: its stdout is printed as it is (rc 2 output already starts with its own
//!   「掃描不完整」 line). Anything else (not found, another rc, a signal, the time is up,
//!   output that is not UTF-8): the one fixed line [`NOT_RUN`].

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

/// IFACE-s73l r1 §7.5.
pub const TIMEOUT: Duration = Duration::from_secs(30);

/// IFACE-s73l §2: what is printed when `aib-slice due` did not run to rc 0 / 2.
pub const NOT_RUN: &str = "應合未合 / 應修未修:掃描不完整(aib-slice due 未執行)\n";

/// What one run gave: the exit code (None = no code: a signal) and stdout, or why it did not
/// run (not found, could not start, the time was up).
pub type Ran = Result<(Option<i32>, Vec<u8>), String>;

/// The text to print for one run.
pub fn text(r: &Ran) -> String {
    match r {
        Ok((Some(0 | 2), out)) => match std::str::from_utf8(out) {
            Ok(s) if s.is_empty() || s.ends_with('\n') => s.to_owned(),
            Ok(s) => format!("{s}\n"),
            Err(_) => NOT_RUN.to_owned(),
        },
        _ => NOT_RUN.to_owned(),
    }
}

/// `aib-slice` next to this program: `aib-slice.exe` first on Windows, then `aib-slice`.
pub fn beside(exe: &Path) -> Option<PathBuf> {
    let dir = exe.parent()?;
    let names: &[&str] = if cfg!(windows) {
        &["aib-slice.exe", "aib-slice"]
    } else {
        &["aib-slice"]
    };
    names.iter().map(|n| dir.join(n)).find(|p| p.is_file())
}

fn program() -> Option<PathBuf> {
    match std::env::var_os("AIB_IDEA_SLICE_BIN") {
        Some(p) => Some(PathBuf::from(p)).filter(|p| p.is_file()),
        None => beside(&std::env::current_exe().ok()?),
    }
}

fn limit() -> Duration {
    std::env::var("AIB_IDEA_SLICE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .map_or(TIMEOUT, Duration::from_millis)
}

/// ADR 0005 named exception (as in aib-ledger): a short wait inside a one-shot command,
/// bounded by [`limit`].
fn pause() {
    #[allow(clippy::disallowed_methods)]
    thread::sleep(Duration::from_millis(20));
}

fn stop(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// Run `aib-slice due --seat <seat> --no-fetch` within the limit.
// Named exception (clippy.toml, ADR 0005): the deadline of one child process is a channel
// wait on the thread that drains its stdout, as in aib-ghread run_fixed. aib-idea is a
// one-shot command, not aibd, and has no timer registry.
#[allow(clippy::disallowed_methods)]
pub fn run(seat: &str) -> Ran {
    let prog = program().ok_or("aib-slice not found beside aib-idea")?;
    let deadline = Instant::now() + limit();
    let mut child = Command::new(&prog)
        .args(["due", "--seat", seat, "--no-fetch"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("not run: {}", e.kind()))?;
    let Some(mut out) = child.stdout.take() else {
        stop(&mut child);
        return Err("no stdout".into());
    };
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut b = Vec::new();
        let _ = tx.send(out.read_to_end(&mut b).map(|_| b));
    });
    let stdout = match rx.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            stop(&mut child);
            return Err(format!("stdout: {}", e.kind()));
        }
        Err(_) => {
            stop(&mut child);
            return Err("timed out".into());
        }
    };
    loop {
        match child.try_wait() {
            Ok(Some(s)) => return Ok((s.code(), stdout)),
            Ok(None) if Instant::now() >= deadline => {
                stop(&mut child);
                return Err("timed out".into());
            }
            Ok(None) => pause(),
            Err(e) => {
                stop(&mut child);
                return Err(format!("wait: {}", e.kind()));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rc_0_and_2_print_their_output_as_it_is_and_the_rest_print_the_fixed_line() {
        let out = "應合未合(0 件):\n應修未修(0 件):\n".as_bytes().to_vec();
        assert_eq!(
            text(&Ok((Some(0), out.clone()))),
            String::from_utf8(out).unwrap()
        );
        let warn = "掃描不完整,以下可能有缺:gh: timed out(共 1 個)\n應合未合(0 件):\n";
        assert_eq!(text(&Ok((Some(2), warn.as_bytes().to_vec()))), warn);
        // a last line without its newline gets one, so what follows starts on a new line
        assert_eq!(text(&Ok((Some(0), b"x".to_vec()))), "x\n");
        for r in [
            Ok((Some(64), b"usage".to_vec())),
            Ok((Some(1), Vec::new())),
            Ok((None, b"half".to_vec())),
            Ok((Some(0), vec![0xff, b'\n'])),
            Err("timed out".to_owned()),
            Err("aib-slice not found beside aib-idea".to_owned()),
        ] {
            assert_eq!(text(&r), NOT_RUN, "{r:?}");
        }
    }

    #[test]
    fn the_program_is_looked_for_only_beside_this_one() {
        let d = tempfile::tempdir().unwrap();
        let me = d.path().join("aib-idea");
        assert_eq!(beside(&me), None);
        let name = if cfg!(windows) {
            "aib-slice.exe"
        } else {
            "aib-slice"
        };
        std::fs::write(d.path().join(name), "").unwrap();
        assert_eq!(beside(&me), Some(d.path().join(name)));
        // a folder of that name is not the program
        let e = tempfile::tempdir().unwrap();
        std::fs::create_dir(e.path().join(name)).unwrap();
        assert_eq!(beside(&e.path().join("aib-idea")), None);
    }
}
