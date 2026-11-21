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
use std::path::{Path, PathBuf};
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

/// The dm queue read in process (S79-rust slice 2, 5d33a PREREAD-drive-rust 0/0/2/2): the
/// seat folders under `<root>/state/dm-queue`, each item through aib-dmq-store's reader (the
/// one aibd's bridge uses), no `aib dm-queue list` and so no Python. A row is the item with
/// the keys `dm_queue._row` derives and aib-drive reads: `status` (empty ⇒ queued),
/// `priority` (normalized), `read_at`, `msg`, `when`, `wait`. An item that does not read is
/// left out, as the 1.x list leaves it out of the rows.
pub struct StoreQueue {
    pub root: PathBuf,
}

impl StoreQueue {
    /// The state root the way aib-drive finds it: `EZ2_AIB_STATE_ROOT`, `EZ2_AIB_HOME`, else
    /// `~/.ez2-aib` (the same order as the 1.x `seats_dir_lib.state_root`).
    pub fn from_env() -> Result<Self, String> {
        let home = std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from);
        let root = crate::observe::state_root(
            std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
            std::env::var("EZ2_AIB_HOME").ok().as_deref(),
            home.as_deref(),
        )?;
        Ok(Self { root })
    }

    /// `seat`'s rows on `tab` (`None`: every tab), in file-name order.
    pub fn rows(&self, seat: &str, tab: Option<&str>) -> Result<Vec<Value>, String> {
        if !is_seat(seat) {
            return Err(format!("seat refused: {seat:?}"));
        }
        let store = aib_dmq_store::store::Store::new(&self.root, &self.root).without_git();
        let dir = store
            .seat_dir(seat)
            .map_err(|e| format!("dm {seat}: {e}"))?;
        let mut names: Vec<String> = match std::fs::read_dir(&dir) {
            Ok(rd) => rd
                .flatten()
                .filter_map(|e| e.file_name().to_str().map(str::to_owned))
                .filter_map(|n| n.strip_suffix(".json").map(str::to_owned))
                .filter(|n| aib_dmq::evidence::is_item_id(n))
                .collect(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(format!("dm {seat}: {}", e.kind())),
        };
        names.sort();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
        let mut out = Vec::new();
        for id in names {
            let Ok(aib_dmq_store::store::ItemRead::Item { item, .. }) = store.read_item(seat, &id)
            else {
                continue;
            };
            let raw = Value::Object(item);
            let status = raw["status"].as_str().filter(|s| !s.is_empty());
            if tab.is_some_and(|t| t != aib_dmq::view::tab_of_status(status)) {
                continue;
            }
            let mut row = raw.clone();
            let when = aib_dmq::view::row_when(&raw, now);
            let pri = aib_dmq::norm::priority(raw.get("priority"), "normal");
            for (k, v) in [
                ("status", Value::from(status.unwrap_or("queued"))),
                ("priority", Value::from(pri)),
                (
                    "read_at",
                    raw.get("read_at").cloned().unwrap_or(Value::Null),
                ),
                ("msg", Value::from(raw["msg"].as_str().unwrap_or_default())),
                ("when", Value::from(when)),
                ("wait", raw.get("wait").cloned().unwrap_or(Value::Null)),
            ] {
                row[k] = v;
            }
            out.push(row);
        }
        Ok(out)
    }
}

impl Queue for StoreQueue {
    fn waiting(&self, seat: &str) -> Result<Value, String> {
        self.rows(seat, Some("waiting")).map(Value::Array)
    }
}

impl DmLookup for StoreQueue {
    fn all(&self, seat: &str) -> Result<Value, String> {
        self.rows(seat, None).map(Value::Array)
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
    /// Through the repo's auto_find_work_dm.sh (S79-sender), never `aib` directly; on Windows
    /// the no-transport marker ([`crate::progpath::NO_DM_TRANSPORT`], #820).
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

/// [`Sender`] that writes the letter into the 1.x ledger itself (S79-rust slice 3a, 5d33a
/// PREREAD-drive-rust 0/0/2/2, 519d0 18:41): aib-dmq-store's writer, the one aibd's page
/// bridge uses (same folder lock, same bytes), no `aib dm` and no shell; the 1.x drainer
/// delivers it like any queued letter. The text passes [`aib_dmq_store::letter::check_text`]
/// (R-30, no header-shaped line) or nothing is written; one over [`DM_RELAY_AT`] bytes goes to
/// a private `RELAY-drive-*` file in `<root>/pending` and the letter is the pointer line. The
/// letter says it is the system's (`系統`, origin `aib-drive`: the header stays 系統@aib, the
/// 代發 line is in the text, §15). Like every local file this marks, it does not prove.
pub struct StoreDm {
    /// The state root; none ⇒ every send is NotSent.
    pub root: Option<PathBuf>,
}

/// Letters longer than this many bytes go to a relay file (dm-ctl's and legacy_send's 600).
pub const DM_RELAY_AT: usize = 600;
/// The longest text drive sends (legacy_send's 64 KiB).
pub const DM_TEXT_MAX: usize = 64 * 1024;
/// sender_origin of a letter drive wrote.
pub const DM_ORIGIN: &str = "aib-drive";

impl StoreDm {
    /// The cabin's full name under `<root>/instances` for the short code in an address
    /// (`dab9` ⇒ `20261113-035938-dab9`): exactly one folder named so, else none.
    pub fn cabin_of(root: &Path, short: &str) -> Option<String> {
        let tail = format!("-{short}");
        let names: Vec<String> = std::fs::read_dir(root.join("instances"))
            .ok()?
            .flatten()
            .filter(|e| e.path().is_dir())
            .filter_map(|e| e.file_name().to_str().map(str::to_owned))
            .filter(|n| n == short || n.ends_with(&tail))
            .collect();
        match names.as_slice() {
            [one] => Some(one.clone()),
            _ => None,
        }
    }
}

/// A new `q_` + 16 hex id: the clock, the process, a counter and the letter, hashed.
fn new_letter_id(addr: &str, text: &str) -> (String, String) {
    use sha2::{Digest, Sha256};
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let mut h = Sha256::new();
    h.update(format!("{nanos}\0{}\0{seq}\0{addr}\0", std::process::id()));
    h.update(text.as_bytes());
    let hex: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
    (format!("q_{}", &hex[..16]), hex[16..32].to_owned())
}

impl Sender for StoreDm {
    /// Refused address / text, no state root, no such cabin or seat folder, or a write that
    /// did not start: NotSent. The item written but order.json not: Unknown (never sent again).
    fn send(&self, addr: &str, text: &str) -> Result<(String, String), crate::intents::SendError> {
        use crate::intents::SendError;
        use aib_dmq_store::letter;
        AibDm::args(addr, text).map_err(SendError::NotSent)?;
        let Some(root) = &self.root else {
            return Err(SendError::NotSent("no state root".into()));
        };
        letter::check_text(text, DM_TEXT_MAX)
            .map_err(|(code, why)| SendError::NotSent(format!("{code}: {why}")))?;
        let (short, seat) = addr.split_once('/').unwrap_or_default();
        let cabin = Self::cabin_of(root, short)
            .ok_or_else(|| SendError::NotSent(format!("cabin {short} not found")))?;
        let store = aib_dmq_store::Store::new(root, root);
        let has_folder = store.seat_dir(seat).is_ok_and(|d| d.is_dir());
        if !has_folder {
            return Err(SendError::NotSent(format!(
                "{seat}: no folder in the ledger"
            )));
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
        let (id, tail) = new_letter_id(addr, text);
        let (msg, _) = letter::relay(
            &root.join("pending"),
            "RELAY-drive",
            seat,
            now,
            &tail,
            text,
            DM_RELAY_AT,
        )
        .map_err(|e| SendError::NotSent(format!("relay file: {}", e.kind())))?;
        let hub = format!("aihub-{cabin}");
        let mut item = letter::item(&id, seat, &msg, "系統", "", &hub, now);
        for (k, v) in [
            ("sender_kind", Value::from("system")),
            ("sender_seat", Value::Null),
            ("sender_origin", Value::from(DM_ORIGIN)),
            ("human_id", Value::Null),
            ("sender_source", Value::from(DM_ORIGIN)),
        ] {
            item.insert(k.into(), v);
        }
        match letter::enqueue(&store, seat, &item) {
            Ok(()) => Ok(("queued".to_owned(), id)),
            Err(letter::Enqueue::NotWritten(why)) => Err(SendError::NotSent(why)),
            Err(letter::Enqueue::ItemOnly(why)) => Err(SendError::Unknown(why)),
        }
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
        // preview.7 Windows: no dm transport here; nothing is started, the reason is kept
        if self.program == crate::progpath::NO_DM_TRANSPORT {
            return Err(SendError::NotSent(self.program.clone()));
        }
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

/// Where `pr:` items of the plan live when nothing is set (A15-repocfg: the export tool rewrites
/// this line for a public repo).
pub const DEFAULT_REPO: &str = "ez2app/ez2-aib"; // aib-export: repo

/// The `owner/name` repo: `AIB_DRIVE_REPO`, else `AIB_REPO`, else [`DEFAULT_REPO`]; an empty
/// value counts as unset.
pub fn repo() -> String {
    repo_from(|k| std::env::var(k).ok())
}

/// [`repo`] over any lookup (tests pass a map instead of the environment).
pub fn repo_from(get: impl Fn(&str) -> Option<String>) -> String {
    ["AIB_DRIVE_REPO", "AIB_REPO"]
        .iter()
        .find_map(|k| get(k).filter(|v| !v.trim().is_empty()))
        .unwrap_or_else(|| DEFAULT_REPO.to_owned())
}

/// The live lookup of a send round: dms read in process ([`StoreQueue`]), a PR through
/// `gh api`. No state root ⇒ every dm lookup fails (never "not queued").
pub struct LiveLookup {
    pub queue: Result<StoreQueue, String>,
    pub gh: aib_ghread::GhRead,
    /// `owner/name`.
    pub repo: String,
}

impl LiveLookup {
    pub fn new() -> Self {
        LiveLookup {
            queue: StoreQueue::from_env(),
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
        self.queue.as_ref().map_err(Clone::clone)?.all(seat)
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

/// One git call in [`branch_head_in`].
pub const BRANCH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

/// What `branch` points at in `repo` ([`crate::act::BranchHead`]): `refs/heads/<branch>`, else
/// `refs/remotes/origin/<branch>` (the scan reads both; 5d33a cf7e9e3e L1), each through
/// `git rev-parse --verify -q <ref>^{commit}`. A name git would read as an option or with
/// spaces is not looked up (`None`); git not starting, failing otherwise, or answering with
/// something that is not a 40-hex id (a sha256 repo, N1) is `None`; only git's "no such
/// ref" (rc 1) for both refs is `Some(None)`.
pub fn branch_head_in(repo: &Path, branch: &str) -> Option<Option<String>> {
    if branch.is_empty() || branch.starts_with('-') || branch.chars().any(char::is_whitespace) {
        return None;
    }
    for r in ["refs/heads", "refs/remotes/origin"] {
        let args = [
            "-C".to_owned(),
            repo.display().to_string(),
            "rev-parse".to_owned(),
            "--verify".to_owned(),
            "-q".to_owned(),
            format!("{r}/{branch}^{{commit}}"),
        ];
        let (code, out) = run_fixed_any("git", &args, BRANCH_TIMEOUT).ok()?;
        match code {
            Some(0) => {
                let h = String::from_utf8_lossy(&out).trim().replace('\r', "");
                return (h.len() == 40 && h.bytes().all(|b| b.is_ascii_hexdigit()))
                    .then_some(Some(h));
            }
            // --verify -q: 1 is "no such ref"; try the next one
            Some(1) => {}
            // 128 not a repo, a signal: unknown
            _ => return None,
        }
    }
    Some(None)
}

#[cfg(test)]
mod sender_tests {
    use super::*;

    #[test]
    fn the_repo_is_read_from_the_settings_before_the_default() {
        fn env(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
            move |k: &str| {
                pairs
                    .iter()
                    .find(|(n, _)| *n == k)
                    .map(|(_, v)| v.to_string())
            }
        }
        assert_eq!(repo_from(env(&[])), DEFAULT_REPO);
        assert_eq!(repo_from(env(&[("AIB_REPO", "me/pub")])), "me/pub");
        assert_eq!(
            repo_from(env(&[
                ("AIB_REPO", "me/pub"),
                ("AIB_DRIVE_REPO", "me/drive")
            ])),
            "me/drive"
        );
        // an empty setting is unset, not a repo named ""
        assert_eq!(
            repo_from(env(&[("AIB_DRIVE_REPO", " "), ("AIB_REPO", "me/pub")])),
            "me/pub"
        );
    }

    /// E12: the one outside call act can make is `aib dm <cabin>/<seat> <text>`.
    /// preview.7 Windows: with the no-transport marker every dm is NotSent with that reason;
    /// nothing is run (a bad address is still refused first).
    #[test]
    fn no_dm_transport_is_not_sent_with_the_reason() {
        use crate::intents::SendError;
        let none = AibDm {
            program: crate::progpath::NO_DM_TRANSPORT.into(),
            timeout: CALL_TIMEOUT,
        };
        match none.send("dab9/1ced7", "提醒: x") {
            Err(SendError::NotSent(why)) => assert_eq!(why, crate::progpath::NO_DM_TRANSPORT),
            other => panic!("{other:?}"),
        }
        match none.send("1ced7", "提醒: x") {
            Err(SendError::NotSent(why)) => assert!(why.starts_with("address refused"), "{why}"),
            other => panic!("{other:?}"),
        }
    }

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
    /// missing), never `aib` itself. On Windows it is the no-transport marker: every send is
    /// refused with that reason (#827 windows-latest: this test asserted the shell there).
    #[test]
    fn the_dm_sender_runs_the_repo_shell_not_aib() {
        let p = AibDm::new().program;
        if cfg!(windows) {
            assert_eq!(p, crate::progpath::NO_DM_TRANSPORT);
        } else {
            assert!(
                p.ends_with(crate::progpath::AFW_SHELL) || p.contains("not found"),
                "{p}"
            );
        }
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

#[cfg(test)]
mod store_queue_tests {
    use super::*;
    use aib_dmq_store::letter;
    use serde_json::json;

    fn put(root: &Path, seat: &str, id: &str, edit: impl Fn(&mut serde_json::Map<String, Value>)) {
        let store = aib_dmq_store::Store::new(root, root).without_git();
        let mut it = letter::item(id, seat, "派工: X → 519d0", "l", "", "", 1_700_000_000);
        edit(&mut it);
        letter::enqueue(&store, seat, &it).expect("enqueue");
    }

    /// SQ1 (S79-rust slice 2): the queue read in process gives the rows drive reads, a tab
    /// at a time, without the `aib` program.
    #[test]
    fn the_queue_read_in_process_gives_the_rows_drive_reads() {
        let d = crate::act::tests::Scratch::new("sq1");
        let root = d.path();
        put(root, "519d0", "q_0000000000000001", |m| {
            m.insert("priority".into(), json!(" URGENT "));
            m.remove("read_at");
        });
        put(root, "519d0", "q_0000000000000002", |m| {
            m.insert("status".into(), json!("read"));
            m.insert("read_at".into(), json!(1_700_000_100));
            m.insert("wait".into(), json!({"text": "等收據", "at": 1}));
        });
        put(root, "519d0", "q_0000000000000003", |m| {
            m.insert("status".into(), json!(""));
        });
        let dir = root.join("state/dm-queue/519d0");
        std::fs::write(dir.join("q_00000000000000ff.json"), b"{not json").expect("bad");
        std::fs::write(dir.join("notes.json"), b"{}").expect("other");
        let q = StoreQueue {
            root: root.to_path_buf(),
        };
        let w = q.rows("519d0", Some("waiting")).expect("waiting");
        let ids: Vec<&str> = w.iter().filter_map(|r| r["id"].as_str()).collect();
        assert_eq!(ids, ["q_0000000000000001", "q_0000000000000003"]);
        assert_eq!(
            w[0]["priority"], "urgent",
            "reason: priority_not_normalized"
        );
        assert_eq!(w[0]["read_at"], Value::Null, "reason: read_at_missing");
        assert_eq!(w[1]["status"], "queued", "reason: empty_status_not_queued");
        assert_eq!(w[0]["msg"], "派工: X → 519d0");
        let all = q.all("519d0").expect("all");
        let all = all.as_array().expect("list");
        assert_eq!(all.len(), 3, "reason: bad_item_listed_or_tab_dropped");
        assert_eq!(all[1]["status"], "read");
        assert_eq!(all[1]["wait"]["text"], "等收據");
        let busy = busy_seats(&q, &["519d0".to_owned()]).expect("busy");
        assert!(busy.contains("519d0"), "reason: urgent_unread_not_busy");
        assert_eq!(
            q.rows("nobody", None).unwrap_err(),
            "seat refused: \"nobody\""
        );
        assert!(q.rows("abcde", None).expect("no folder").is_empty());
    }

    /// SQ2: a dm lookup with no state root fails instead of reading as "not queued".
    #[test]
    fn no_state_root_is_an_error_not_an_empty_queue() {
        let live = LiveLookup {
            queue: Err("no root".to_owned()),
            gh: aib_ghread::GhRead::new(),
            repo: DEFAULT_REPO.to_owned(),
        };
        assert_eq!(live.all("519d0").unwrap_err(), "no root");
    }
}

#[cfg(test)]
mod store_dm_tests {
    use super::*;
    use crate::intents::SendError;

    fn world(tag: &str) -> crate::act::tests::Scratch {
        let d = crate::act::tests::Scratch::new(tag);
        std::fs::create_dir_all(d.path().join("instances/20261113-035938-dab9")).unwrap();
        std::fs::create_dir_all(d.path().join("state/dm-queue/519d0")).unwrap();
        d
    }

    /// SD1 (S79-rust slice 3a): a letter drive sends is queued in the ledger as the system's,
    /// the 代發 line kept, the hub the cabin's full name; nothing outside is run.
    #[test]
    fn a_letter_is_queued_in_the_ledger_as_the_systems() {
        let d = world("sd1");
        let dm = StoreDm {
            root: Some(d.path().to_path_buf()),
        };
        let text = "派工: S79 → 519d0\n— 代發:自動推進,開關 a9136 · 意圖 k";
        let (stage, id) = dm.send("dab9/519d0", text).expect("sent");
        assert_eq!(stage, "queued");
        assert!(aib_dmq::evidence::is_item_id(&id), "{id}");
        let q = StoreQueue {
            root: d.path().to_path_buf(),
        };
        let rows = q.rows("519d0", Some("waiting")).unwrap();
        assert_eq!(rows.len(), 1);
        let r = &rows[0];
        assert_eq!(
            (r["id"].as_str(), r["msg"].as_str()),
            (Some(id.as_str()), Some(text))
        );
        assert_eq!(r["status"], "queued");
        assert_eq!(r["sender_label"], "系統", "reason: not_the_system");
        assert_eq!(r["sender_kind"], "system");
        assert_eq!(r["sender_origin"], DM_ORIGIN);
        assert_eq!(r["sender_seat"], Value::Null, "reason: claims_a_seat");
        assert_eq!(
            r["hub"], "aihub-20261113-035938-dab9",
            "reason: short_cabin_as_hub"
        );
        let store = aib_dmq_store::Store::new(d.path(), d.path());
        assert_eq!(
            store.order_ids("519d0").unwrap(),
            [id],
            "reason: not_in_order"
        );
    }

    /// SD2: a long letter goes to a private relay file and the letter is the pointer line.
    #[test]
    fn a_long_letter_is_a_pointer_to_its_relay_file() {
        let d = world("sd2");
        std::fs::create_dir_all(d.path().join("pending")).unwrap();
        let dm = StoreDm {
            root: Some(d.path().to_path_buf()),
        };
        let text = "派工: 長\n".to_owned() + &"字".repeat(300);
        let (_, id) = dm.send("dab9/519d0", &text).expect("sent");
        let store = aib_dmq_store::Store::new(d.path(), d.path());
        let Ok(aib_dmq_store::ItemRead::Item { item, .. }) = store.read_item("519d0", &id) else {
            panic!("no item");
        };
        let files: Vec<PathBuf> = std::fs::read_dir(d.path().join("pending"))
            .unwrap()
            .flatten()
            .map(|e| e.path())
            .collect();
        let [file] = files.as_slice() else {
            panic!("reason: not_one_relay_file {files:?}");
        };
        let name = file.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name.starts_with("RELAY-drive-519d0-"), "{name}");
        assert_eq!(std::fs::read_to_string(file).unwrap(), text);
        assert_eq!(
            item["msg"].as_str(),
            Some(aib_dmq_store::letter::pointer_line(text.len(), file).as_str()),
            "reason: not_the_pointer_line"
        );
    }

    /// SD3: what cannot be written is NotSent and writes nothing: a header-shaped or R-30
    /// line, no state root, a cabin or seat not there.
    #[test]
    fn what_cannot_be_written_is_not_sent_and_writes_nothing() {
        let d = world("sd3");
        let root = Some(d.path().to_path_buf());
        let dm = StoreDm { root: root.clone() };
        for bad in ["a >> b", "第 ① 件", "長訊已落檔(9 位元組),請讀:/x"] {
            assert!(
                matches!(dm.send("dab9/519d0", bad), Err(SendError::NotSent(_))),
                "reason: lint_let_through {bad}"
            );
        }
        assert!(matches!(
            StoreDm { root: None }.send("dab9/519d0", "派工: x"),
            Err(SendError::NotSent(_))
        ));
        assert!(
            matches!(dm.send("beef/519d0", "派工: x"), Err(SendError::NotSent(_))),
            "reason: unknown_cabin_sent"
        );
        assert!(
            matches!(dm.send("dab9/abcde", "派工: x"), Err(SendError::NotSent(_))),
            "reason: seat_folder_made"
        );
        assert!(!d.path().join("state/dm-queue/abcde").exists());
        let store = aib_dmq_store::Store::new(d.path(), d.path());
        assert!(store.order_ids("519d0").unwrap().is_empty());
    }

    /// The short cabin code names exactly one folder; two that end alike name none.
    #[test]
    fn the_cabin_is_found_from_its_short_code_only_when_one_matches() {
        let d = world("sd4");
        assert_eq!(
            StoreDm::cabin_of(d.path(), "dab9").as_deref(),
            Some("20261113-035938-dab9")
        );
        std::fs::create_dir_all(d.path().join("instances/20261201-000000-dab9")).unwrap();
        assert_eq!(
            StoreDm::cabin_of(d.path(), "dab9"),
            None,
            "reason: ambiguous_taken"
        );
        assert_eq!(
            StoreDm::cabin_of(d.path(), "ab9"),
            None,
            "reason: partial_code"
        );
    }
}
