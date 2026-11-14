//! The append-only JSONL ledger mechanics shared by the seat tools (DEV-S78 §7, r-0).
//!
//! Moved out of aib-idea unchanged: a fixed lock file taken with `File::try_lock` (flock on
//! Unix, LockFileEx on Windows) and waited for a bounded time; one whole line appended and
//! `sync_all`ed; lines replayed in file order, where a line over the size limit, a line that
//! is not a JSON object, a line the caller's rules refuse, or a last line without its newline
//! is reported (the ledger is then incomplete) instead of skipped. Blank lines are skipped.
//!
//! The crate knows no event types: the caller applies each object. Each tool keeps its own
//! file (different trust levels are not mixed in one ledger).

use serde_json::{Map, Value};
use std::fs::{self, File, OpenOptions, TryLockError};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, Instant};

/// Replay `text` in file order, handing each JSON object line to `apply`. Returns the
/// problems as `line <n>: <why>`, in file order; any entry means the ledger is incomplete.
pub fn replay(
    text: &str,
    max_line_bytes: usize,
    mut apply: impl FnMut(&Map<String, Value>) -> Result<(), String>,
) -> Vec<String> {
    let mut errors = Vec::new();
    let mut lines: Vec<&str> = text.split('\n').collect();
    // split leaves "" after the final newline; anything else there was cut short
    let tail = lines.pop().unwrap_or_default();
    for (n, line) in lines.iter().enumerate() {
        let n = n + 1;
        if line.trim().is_empty() {
            continue;
        }
        if line.len() > max_line_bytes {
            errors.push(format!("line {n}: over {max_line_bytes} bytes"));
            continue;
        }
        let r = serde_json::from_str::<Value>(line)
            .map_err(|e| format!("bad json: {e}"))
            .and_then(|v| match v {
                Value::Object(o) => apply(&o),
                _ => Err("not an object".into()),
            });
        if let Err(e) = r {
            errors.push(format!("line {n}: {e}"));
        }
    }
    if !tail.is_empty() {
        errors.push(format!("line {}: cut short (no newline)", lines.len() + 1));
    }
    errors
}

/// What reading a ledger file found.
#[derive(Debug, PartialEq, Eq)]
pub enum Read {
    Text(String),
    /// No file yet: an empty, complete ledger.
    Missing,
    /// The bytes are not UTF-8: the ledger is incomplete.
    NotUtf8,
}

pub fn read(path: &Path) -> std::io::Result<Read> {
    match fs::read(path) {
        Ok(b) => Ok(String::from_utf8(b).map_or(Read::NotUtf8, Read::Text)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Read::Missing),
        Err(e) => Err(e),
    }
}

/// ADR 0005 named exception: a short wait inside a one-shot command, bounded by the
/// caller's lock wait.
fn pause() {
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(Duration::from_millis(10));
}

/// The exclusive lock on the fixed file `path`, waited for at most `wait`. Released when the
/// returned file is dropped. Errors read `lock open: …`, `lock: …` or `lock: not taken in
/// <s> s`.
pub fn take_lock(path: &Path, wait: Duration) -> Result<File, String> {
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path)
        .map_err(|e| format!("lock open: {e}"))?;
    let until = Instant::now() + wait;
    loop {
        match f.try_lock() {
            Ok(()) => return Ok(f),
            Err(TryLockError::WouldBlock) => {}
            Err(TryLockError::Error(e)) => return Err(format!("lock: {e}")),
        }
        if Instant::now() >= until {
            return Err(format!("lock: not taken in {} s", wait.as_secs()));
        }
        pause();
    }
}

/// Why an append did not happen.
#[derive(Debug, PartialEq, Eq)]
pub enum Append {
    /// The line with its newline would be over the limit; nothing was written.
    TooLong(usize),
    /// `open: …`, `write: …` or `sync: …`.
    Io(String),
}

/// Append `line` and its newline in one write, then `sync_all`. Call with the lock held.
pub fn append(path: &Path, line: &str, max_line_bytes: usize) -> Result<(), Append> {
    if line.len() + 1 > max_line_bytes {
        return Err(Append::TooLong(max_line_bytes));
    }
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| Append::Io(format!("open: {e}")))?;
    f.write_all(format!("{line}\n").as_bytes())
        .map_err(|e| Append::Io(format!("write: {e}")))?;
    f.sync_all().map_err(|e| Append::Io(format!("sync: {e}")))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(text: &str) -> (Vec<String>, Vec<String>) {
        let mut seen = Vec::new();
        let errors = replay(text, 64, |o| {
            let k = o.get("k").and_then(Value::as_str).unwrap_or_default();
            if k == "refuse" {
                return Err("refused".into());
            }
            seen.push(k.to_string());
            Ok(())
        });
        (seen, errors)
    }

    #[test]
    fn lines_are_applied_in_file_order_and_blank_lines_skipped() {
        let (seen, errors) = keys("{\"k\":\"b\"}\n\n{\"k\":\"a\"}\n");
        assert_eq!(seen, vec!["b", "a"]);
        assert!(errors.is_empty(), "{errors:?}");
    }

    #[test]
    fn every_bad_line_is_reported_with_its_number_and_the_rest_still_apply() {
        let long = format!("{{\"k\":\"{}\"}}", "x".repeat(80));
        let text = format!(
            "[1]\n{{\"k\":\"refuse\"}}\nnot json\n{long}\n{{\"k\":\"ok\"}}\n{{\"k\":\"cut\"}}"
        );
        let (seen, errors) = keys(&text);
        assert_eq!(seen, vec!["ok"]);
        assert_eq!(errors.len(), 5, "{errors:?}");
        assert_eq!(errors[0], "line 1: not an object");
        assert_eq!(errors[1], "line 2: refused");
        assert!(errors[2].starts_with("line 3: bad json: "), "{errors:?}");
        assert_eq!(errors[3], "line 4: over 64 bytes");
        assert_eq!(errors[4], "line 6: cut short (no newline)");
    }

    #[test]
    fn a_missing_file_is_empty_and_bytes_that_are_not_utf8_say_so() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("l.jsonl");
        assert_eq!(read(&p).unwrap(), Read::Missing);
        fs::write(&p, [0xff, 0xfe, b'\n']).unwrap();
        assert_eq!(read(&p).unwrap(), Read::NotUtf8);
        fs::write(&p, "{}\n").unwrap();
        assert_eq!(read(&p).unwrap(), Read::Text("{}\n".into()));
    }

    #[test]
    fn append_writes_one_whole_line_and_refuses_an_oversize_one() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("l.jsonl");
        append(&p, "{\"k\":\"a\"}", 64).unwrap();
        append(&p, "{\"k\":\"b\"}", 64).unwrap();
        assert_eq!(append(&p, &"x".repeat(64), 64), Err(Append::TooLong(64)));
        assert_eq!(
            fs::read_to_string(&p).unwrap(),
            "{\"k\":\"a\"}\n{\"k\":\"b\"}\n"
        );
    }

    #[test]
    fn a_held_lock_is_not_taken_twice_and_is_free_once_dropped() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("l.lock");
        let held = take_lock(&p, Duration::from_millis(50)).unwrap();
        assert_eq!(
            take_lock(&p, Duration::from_millis(50)).err().as_deref(),
            Some("lock: not taken in 0 s")
        );
        drop(held);
        assert!(take_lock(&p, Duration::from_millis(50)).is_ok());
    }
}
