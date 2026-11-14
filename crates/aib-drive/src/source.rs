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
use std::path::Path;
use std::time::Duration;

pub use aib_ghread::{run_fixed, CALL_TIMEOUT};

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

/// Sends one dm (act E5). The only outside program act ever reaches, through this.
pub trait Sender {
    /// Sends `text` to `addr` (`<cabin>/<seat>`). Ok = the delivery stage aib dm printed
    /// (`delivered`, or `sort` = queued for an idle moment) and the dm id.
    fn send(&self, addr: &str, text: &str) -> Result<(String, String), String>;
}

/// [`Sender`] through `aib dm <cabin>/<seat> <text>` (no shell; the text is one argument).
pub struct AibDm {
    pub program: String,
    pub timeout: Duration,
}

impl AibDm {
    pub fn new() -> Self {
        Self {
            program: "aib".to_owned(),
            timeout: CALL_TIMEOUT,
        }
    }

    /// The whole argument list. The address is `<cabin>/<seat>` with a five-hex seat; a text
    /// that starts with `-` would be read as a flag, so it is refused.
    pub fn args(addr: &str, text: &str) -> Result<Vec<String>, String> {
        let ok = addr.split_once('/').is_some_and(|(c, s)| {
            !c.is_empty() && c.bytes().all(|b| b.is_ascii_alphanumeric()) && is_seat(s)
        });
        if !ok {
            return Err(format!("address refused: {addr:?}"));
        }
        if text.starts_with('-') || text.trim().is_empty() {
            return Err("text refused: empty or starts with -".into());
        }
        Ok(vec!["dm".to_owned(), addr.to_owned(), text.to_owned()])
    }
}

impl Default for AibDm {
    fn default() -> Self {
        Self::new()
    }
}

/// The stage and dm id from aib dm's `RESULT stage=… id=…` line. Any other stage (a
/// preflight refusal, blocked) is an error: nothing was queued.
pub fn parse_dm_result(out: &str) -> Result<(String, String), String> {
    let line = out
        .lines()
        .find(|l| l.starts_with("RESULT "))
        .ok_or("aib dm printed no RESULT line")?;
    let field = |k: &str| line.split_whitespace().find_map(|w| w.strip_prefix(k));
    let stage = field("stage=").ok_or("aib dm RESULT has no stage")?;
    if !matches!(stage, "delivered" | "sort") {
        return Err(format!("aib dm stage {stage}"));
    }
    Ok((stage.to_owned(), field("id=").unwrap_or("-").to_owned()))
}

impl Sender for AibDm {
    fn send(&self, addr: &str, text: &str) -> Result<(String, String), String> {
        let out = run_fixed(&self.program, &Self::args(addr, text)?, self.timeout)?;
        parse_dm_result(&String::from_utf8_lossy(&out))
    }
}

/// Every dm of a seat in the queue, any status: act's recheck asks whether one dm is still
/// queued before it nudges.
pub trait DmLookup {
    fn all(&self, seat: &str) -> Result<Value, String>;
}

impl DmLookup for AibQueue {
    /// `aib dm-queue list --seat <seat> --tab all`.
    fn all(&self, seat: &str) -> Result<Value, String> {
        if !is_seat(seat) {
            return Err(format!("seat refused: {seat:?}"));
        }
        let args = ["dm-queue", "list", "--seat", seat, "--tab", "all"].map(str::to_owned);
        let out = run_fixed(&self.program, &args, self.timeout)?;
        serde_json::from_slice(&out).map_err(|_| format!("dm {seat}: bad json"))
    }
}

#[cfg(test)]
mod sender_tests {
    use super::*;

    /// E12: the one outside call act can make is `aib dm <cabin>/<seat> <text>`.
    #[test]
    fn aib_dm_takes_only_an_address_and_one_text() {
        assert_eq!(
            AibDm::args("dab9/1ced7", "提醒: x").expect("ok"),
            ["dm", "dab9/1ced7", "提醒: x"]
        );
        assert!(AibDm::args("1ced7", "x").is_err());
        assert!(AibDm::args("dab9/1CED7", "x").is_err());
        assert!(AibDm::args("da b9/1ced7", "x").is_err());
        assert!(AibDm::args("dab9/1ced7", "--fast").is_err());
        assert!(AibDm::args("dab9/1ced7", " ").is_err());
    }

    #[test]
    fn the_result_line_gives_the_stage_and_the_id() {
        let out = "aib-dm: …\nRESULT stage=delivered id=q_ab12 relay=/x thread=-\n";
        assert_eq!(
            parse_dm_result(out),
            Ok(("delivered".to_owned(), "q_ab12".to_owned()))
        );
        assert_eq!(
            parse_dm_result("RESULT stage=sort id=q_1\n"),
            Ok(("sort".to_owned(), "q_1".to_owned()))
        );
        assert!(parse_dm_result("RESULT stage=preflight\n").is_err());
        // a stage that is neither delivered nor sort (preflight = nothing went out) is refused
        assert!(parse_dm_result("RESULT stage=preflight id=q_2\n").is_err());
        assert!(parse_dm_result("nothing\n").is_err());
    }
}
