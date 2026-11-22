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

pub mod envelope;
pub mod events;
pub mod timesave;

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

/// Why [`write_if_complete`] wrote nothing.
#[derive(Debug, PartialEq, Eq)]
pub enum Refused<E> {
    /// The lock was not taken ([`take_lock`]'s text).
    Lock(String),
    /// The ledger could not be read (`read: …`).
    Read(String),
    /// The ledger is incomplete: every problem as `line <n>: <why>` (or `not UTF-8`). Every
    /// write is refused until it is fixed.
    Incomplete(Vec<String>),
    /// The caller's `decide` refused (its own error).
    Rule(E),
    /// The append failed or the line was too long.
    Append(Append),
}

/// The one write path of a ledger (S78 r-1, 1ced7 r-0 review L1): take the lock on `lock`,
/// read `path`, rebuild the caller's state with `load` (it returns the state and the
/// problems it found), refuse if there is any problem, let `decide` look at the state and
/// return the line to append (or none) and what to hand back, append it, release the lock.
///
/// A missing file is an empty, complete ledger (`load("")`). Bytes that are not UTF-8 are
/// incomplete. Nothing is written unless the whole ledger read cleanly, so no tool has to
/// remember to check first.
pub fn write_if_complete<S, T, E>(
    lock: &Path,
    path: &Path,
    wait: Duration,
    max_line_bytes: usize,
    load: impl FnOnce(&str) -> (S, Vec<String>),
    decide: impl FnOnce(&S) -> Result<(Option<String>, T), E>,
) -> Result<T, Refused<E>> {
    let held = take_lock(lock, wait).map_err(Refused::Lock)?;
    let text = match read(path).map_err(|e| Refused::Read(format!("read: {e}")))? {
        Read::Text(t) => t,
        Read::Missing => String::new(),
        Read::NotUtf8 => return Err(Refused::Incomplete(vec!["not UTF-8".into()])),
    };
    let (state, problems) = load(&text);
    if !problems.is_empty() {
        return Err(Refused::Incomplete(problems));
    }
    let (line, out) = decide(&state).map_err(Refused::Rule)?;
    if let Some(line) = line {
        append(path, &line, max_line_bytes).map_err(Refused::Append)?;
    }
    drop(held);
    Ok(out)
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

    /// The state of the test ledger: the `k` of every line, and its problems.
    fn load(text: &str) -> (Vec<String>, Vec<String>) {
        let mut seen = Vec::new();
        let problems = replay(text, 64, |o| {
            seen.push(o.get("k").and_then(Value::as_str).unwrap_or("?").to_owned());
            Ok(())
        });
        (seen, problems)
    }

    fn put(d: &Path, k: &str) -> Result<usize, Refused<String>> {
        write_if_complete(
            &d.join("l.lock"),
            &d.join("l.jsonl"),
            Duration::from_millis(50),
            64,
            load,
            |seen: &Vec<String>| {
                if seen.iter().any(|s| s == k) {
                    return Err(format!("{k} is there already"));
                }
                Ok((Some(format!("{{\"k\":\"{k}\"}}")), seen.len() + 1))
            },
        )
    }

    #[test]
    fn a_write_sees_the_whole_ledger_and_its_rule_can_refuse() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(put(d.path(), "a"), Ok(1));
        assert_eq!(put(d.path(), "b"), Ok(2));
        assert_eq!(
            put(d.path(), "a"),
            Err(Refused::Rule("a is there already".into()))
        );
        assert_eq!(
            fs::read_to_string(d.path().join("l.jsonl")).unwrap(),
            "{\"k\":\"a\"}\n{\"k\":\"b\"}\n"
        );
    }

    #[test]
    fn an_incomplete_ledger_refuses_every_write_and_says_why() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("l.jsonl");
        fs::write(&p, "{\"k\":\"a\"}\nnot json\n").unwrap();
        match put(d.path(), "b") {
            Err(Refused::Incomplete(e)) => {
                assert_eq!(e.len(), 1, "{e:?}");
                assert!(e[0].starts_with("line 2: bad json"), "{e:?}");
            }
            other => panic!("{other:?}"),
        }
        fs::write(&p, [0xff, b'\n']).unwrap();
        assert_eq!(
            put(d.path(), "b"),
            Err(Refused::Incomplete(vec!["not UTF-8".into()]))
        );
        // a last line cut short is incomplete too, and nothing was appended after it
        fs::write(&p, "{\"k\":\"a\"}").unwrap();
        assert!(matches!(put(d.path(), "b"), Err(Refused::Incomplete(_))));
        assert_eq!(fs::read_to_string(&p).unwrap(), "{\"k\":\"a\"}");
    }

    #[test]
    fn a_write_waits_for_the_lock_and_gives_up() {
        let d = tempfile::tempdir().unwrap();
        let held = take_lock(&d.path().join("l.lock"), Duration::from_millis(50)).unwrap();
        assert!(matches!(put(d.path(), "a"), Err(Refused::Lock(_))));
        assert!(!d.path().join("l.jsonl").exists());
        drop(held);
        assert_eq!(put(d.path(), "a"), Ok(1));
    }
}
