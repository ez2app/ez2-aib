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

use aib_ghread::run_fixed_any;
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
            program: crate::progpath::aib_program(),
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
    /// (`delivered`, `queued` or `sort-queued`) and the dm id. Err says whether
    /// it surely did not go out or may have (7b81e d3ad28dc M1).
    fn send(&self, addr: &str, text: &str) -> Result<(String, String), crate::intents::SendError>;
}

/// [`Sender`] through `auto_find_work_dm.sh dm <cabin>/<seat> <text>` (the repo shell passes
/// the arguments to `aib dm` as they are; no shell parsing, the text is one argument).
pub struct AibDm {
    pub program: String,
    pub timeout: Duration,
}

impl AibDm {
    /// Through the repo's auto_find_work_dm.sh (S79-sender), never `aib` directly.
    pub fn new() -> Self {
        Self {
            program: crate::progpath::afw_program(),
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

/// What one `aib dm` call did, from its `RESULT stage=… id=…` line and exit code
/// (src/cockpit/dm-ctl.sh contract):
/// - stage `delivered`, `queued` (the recipient engine's own queue) or `sort-queued` (queued
///   for an idle moment) = sent: Ok((stage, id)), whatever the exit code (5d33a d3ad28dc H1;
///   7b81e M1 evidence: delivered with exit 5);
/// - otherwise exit 2 = "not a word reached the target, safe to send again" = `NotSent`;
/// - anything else (exit 3 sent-unverified, exit 4 landed-unsubmitted / pasted-then-stopped,
///   no RESULT line, a signal) may have gone out = `Unknown`, never sent again (5d33a r2 1).
pub fn parse_dm_result(
    out: &str,
    code: Option<i32>,
) -> Result<(String, String), crate::intents::SendError> {
    use crate::intents::SendError;
    let line = out.lines().find(|l| l.starts_with("RESULT "));
    let field = |k: &str| line?.split_whitespace().find_map(|w| w.strip_prefix(k));
    let stage = field("stage=").unwrap_or("-");
    if matches!(stage, "delivered" | "queued" | "sort-queued") {
        return Ok((stage.to_owned(), field("id=").unwrap_or("-").to_owned()));
    }
    let why = format!("aib dm stage {stage}, exit {code:?}");
    if code == Some(2) {
        Err(SendError::NotSent(why))
    } else {
        Err(SendError::Unknown(why))
    }
}

impl Sender for AibDm {
    /// A refused address or text, or a program that could not be started, surely sent
    /// nothing. A call that timed out or whose output could not be read may have come after
    /// the dm went out: `Unknown`. Otherwise [`parse_dm_result`] decides from the RESULT line
    /// and the exit code.
    fn send(&self, addr: &str, text: &str) -> Result<(String, String), crate::intents::SendError> {
        use crate::intents::SendError;
        let args = Self::args(addr, text).map_err(SendError::NotSent)?;
        let (code, out) = run_fixed_any(&self.program, &args, self.timeout).map_err(|e| {
            if e.starts_with(&format!("{} not run:", self.program)) {
                SendError::NotSent(e)
            } else {
                SendError::Unknown(e)
            }
        })?;
        parse_dm_result(&String::from_utf8_lossy(&out), code)
    }
}

/// Every dm of a seat in the queue, any status: act's recheck asks whether one dm is still
/// queued before it nudges.
pub trait DmLookup {
    fn all(&self, seat: &str) -> Result<Value, String>;
    /// One PR as GitHub has it now (`state`, `head.sha`): act's recheck of a row from a
    /// partial scan (519d0 11-15 20:28). No way to read it = not sent.
    fn pr(&self, n: u64) -> Result<Value, String> {
        Err(format!("#{n}: no PR lookup"))
    }
}

/// Where `pr:` items of the plan live (`AIB_DRIVE_REPO` overrides).
pub const DEFAULT_REPO: &str = "ez2app/ez2-aib";

/// The live lookup of a send round: dms through `aib`, a PR through `gh api`.
pub struct LiveLookup {
    pub queue: AibQueue,
    pub gh: aib_ghread::GhRead,
    /// `owner/name`.
    pub repo: String,
}

impl LiveLookup {
    pub fn new() -> Self {
        LiveLookup {
            queue: AibQueue::new(),
            gh: aib_ghread::GhRead::new(),
            repo: std::env::var("AIB_DRIVE_REPO").unwrap_or_else(|_| DEFAULT_REPO.to_owned()),
        }
    }
}

impl Default for LiveLookup {
    fn default() -> Self {
        Self::new()
    }
}

impl DmLookup for LiveLookup {
    fn all(&self, seat: &str) -> Result<Value, String> {
        self.queue.all(seat)
    }
    /// `gh api repos/<repo>/pulls/<n>`.
    fn pr(&self, n: u64) -> Result<Value, String> {
        use aib_ghread::Read;
        self.gh.get(&format!("repos/{}/pulls/{n}", self.repo))
    }
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
        use crate::intents::SendError;
        let out = "aib-dm: …\nRESULT stage=delivered id=q_ab12 relay=/x thread=-\n";
        assert_eq!(
            parse_dm_result(out, Some(0)),
            Ok(("delivered".to_owned(), "q_ab12".to_owned()))
        );
        assert_eq!(
            parse_dm_result("RESULT stage=queued id=q_1 relay=/x thread=-\n", Some(0)),
            Ok(("queued".to_owned(), "q_1".to_owned()))
        );
        // what aib dm really prints for a queued dm (5d33a H1: a real line)
        assert_eq!(
            parse_dm_result(
                "RESULT stage=sort-queued id=q_decc1f3b relay=/x thread=-\n",
                Some(0)
            ),
            Ok(("sort-queued".to_owned(), "q_decc1f3b".to_owned()))
        );
        // a sent stage counts whatever the exit code (7b81e M1 evidence: delivered, exit 5)
        assert_eq!(
            parse_dm_result("RESULT stage=delivered id=q_ea19f2c5\n", Some(5)),
            Ok(("delivered".to_owned(), "q_ea19f2c5".to_owned()))
        );
        // exit 2 = not a word reached the target: not sent, safe to send again
        assert!(matches!(
            parse_dm_result("RESULT stage=preflight id=q_2\n", Some(2)),
            Err(SendError::NotSent(_))
        ));
        assert!(matches!(
            parse_dm_result("nothing\n", Some(2)),
            Err(SendError::NotSent(_))
        ));
        // 5d33a r2 1: exit 3 / 4 stages may have reached the target: never sent again
        for (line, code) in [
            ("RESULT stage=sent-unverified id=q_4\n", 3),
            ("RESULT stage=landed-unsubmitted id=q_5\n", 4),
            ("RESULT stage=pasted-then-stopped id=q_6\n", 4),
            ("nothing\n", 0),
            ("RESULT id=q_3\n", 0),
            // `sort` is not a stage dm-ctl prints
            ("RESULT stage=sort id=q_1\n", 0),
        ] {
            assert!(
                matches!(
                    parse_dm_result(line, Some(code)),
                    Err(SendError::Unknown(_))
                ),
                "{line} exit {code}"
            );
        }
        assert!(matches!(
            parse_dm_result("nothing\n", None),
            Err(SendError::Unknown(_))
        ));
    }

    /// S79-sender: act's sender runs the repo shell (or a name that cannot start when it is
    /// missing), never `aib` itself.
    #[test]
    fn the_dm_sender_runs_the_repo_shell_not_aib() {
        let p = AibDm::new().program;
        assert!(
            p.ends_with(crate::progpath::AFW_SHELL) || p.contains("not found"),
            "{p}"
        );
        assert_ne!(p, crate::progpath::aib_program());
    }

    /// 7b81e d3ad28dc M1: a program that cannot start sent nothing; a call that ran and failed
    /// (timed out, exit ≠ 0) may have sent it.
    #[test]
    fn a_dm_that_could_not_start_is_not_sent_and_a_failed_call_is_unknown() {
        use crate::intents::SendError;
        let missing = AibDm {
            program: "aib-drive-no-such-program-e5".into(),
            timeout: CALL_TIMEOUT,
        };
        assert!(matches!(
            missing.send("dab9/1ced7", "提醒: x"),
            Err(SendError::NotSent(_))
        ));
        assert!(matches!(
            missing.send("1ced7", "提醒: x"),
            Err(SendError::NotSent(_))
        ));
        // a call that ran, failed and printed no RESULT (here `false`) may have come after the
        // dm went out; a RESULT line is the answer whatever the exit code
        #[cfg(unix)]
        {
            let failing = AibDm {
                program: "false".into(),
                timeout: CALL_TIMEOUT,
            };
            assert!(matches!(
                failing.send("dab9/1ced7", "提醒: x"),
                Err(SendError::Unknown(_))
            ));
            use std::os::unix::fs::PermissionsExt;
            let dir = std::env::temp_dir().join(format!(
                "aib-drive-dm-rc5-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_or(0, |d| d.as_nanos())
            ));
            std::fs::create_dir_all(&dir).expect("mkdir");
            let fake = dir.join("aib");
            std::fs::write(
                &fake,
                "#!/bin/sh\necho 'RESULT stage=delivered id=q_ea19f2c5 relay=/x thread=-'\nexit 5\n",
            )
            .expect("write fake");
            std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");
            let rc5 = AibDm {
                program: fake.to_string_lossy().into_owned(),
                timeout: CALL_TIMEOUT,
            };
            let got = rc5.send("dab9/1ced7", "提醒: x");
            let _ = std::fs::remove_dir_all(&dir);
            assert_eq!(got, Ok(("delivered".to_owned(), "q_ea19f2c5".to_owned())));
        }
    }
}
