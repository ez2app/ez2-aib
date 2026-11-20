//! The web page's 自動推進 round (519d0 ORDER-webonly-today 第 7 項, Andy 11-20 05:5x「我要今天
//! 可以關閉 終端機 用網頁版 來接替所有開發」): aibd types one sentence to a chosen seat every so
//! many minutes, as the terminal's `/loop` did.
//!
//! - The list is `<data>/seat_ticks.json`, written only by the built-in page a person paired
//!   (`seat_tick_set` / `seat_tick_clear`; `seat_tick_list` reads it). One entry per seat.
//! - Setting a sentence and letting a paused list go again need the owner's fingerprint
//!   (DESIGN-drive-in-aibd r2 §4, 5d33a 11-21): without a ticket aibd asks (`202 {ticket}`),
//!   with it aibd writes only when the ticket approves this very seat, period and words. The
//!   entry keeps the approved words' SHA-256 and the key's signature (below); words that are
//!   no longer the approved ones are not sent (`seat.tick_skipped {reason:
//!   "changed_since_approved"}`) until approved again. Clearing and pausing need none:
//!   stopping is the safe way.
//! - The words can be a file instead (`seat_tick_set {file}`, DESIGN-drive-in-aibd r2 §1): a
//!   name, never a path, under `<data>/ticks/` (aibd's own folder, which a sandboxed seat does
//!   not write); read when it is sent — a plain file, not a link, at most 8 KB of UTF-8,
//!   cleaned as shown text is. The fingerprint binds the name and the content's SHA-256; a
//!   file that is no longer the approved content is not sent until approved again, and the
//!   window says how many lines differ from the approved copy (`ticks/.approved/<name>`,
//!   kept for that count only). What is typed starts with aibd's own line `[定時:<name> <time>]`.
//! - `seat_tick_list` gives each entry's last round and when the next is due; three rounds in
//!   a row that did not go in are one `seat.tick_stuck`, then one every three more.
//! - A sentence goes in between turns only (`write_input_between_turns`): a seat in a turn, busy,
//!   queued or starting gets it on a later pass; a seat that is not running, takes no input or
//!   is gone is skipped for that period (one `seat.tick_skipped`).
//! - It types like the page does: only to a seat whose mode takes input (safe, or dgrs with
//!   AIBD_SEAT_INPUT_DGRS=1), through the same line builder.
//! - 急停: `seat_tick_pause {on}` stops every sentence at once and keeps the list (the pass ends
//!   at its next wake); clearing an entry stops that seat's.
//! - It is not a person typing: it does not reset the cabin's [`crate::seat_send::ROUND_LIMIT`]
//!   count of seat-to-seat messages (only `type_to_seat` does), so a timed sentence cannot keep
//!   two seats talking past that gate.
//! - The first sentence comes one period after the entry is first seen (a restart sends nothing
//!   at once). The pass runs on [`crate::timers::SEAT_TICK`] only while the list is not empty,
//!   so an aibd nobody asked has no wakeups.
//!
//! Like every local file this list is no defence against an AI on the same account, so the
//! list is not what aibd trusts (GUIDE-same-user-ai-threat-model; DESIGN-drive-in-aibd r3,
//! 5d33a 11-21). An entry carries the owner's key's signature of its seat, period, file name,
//! words' SHA-256, approval time, end (30 days later) and its own id; before each send aibd
//! builds those bytes again from the entry — the operation is this module's constant, never
//! read from the list — and checks the signature with the key it checked at start (never a
//! key from the list or the disk again). No signature (every entry from before this, a text
//! one too), one that does not check out, an id on the revoked list, or one past its end: not
//! sent, and the page offers to approve it again.
//!
//! What this does not stop, said plainly: `seat_ticks_revoked.json` is a local file too — an
//! AI on the account can delete it and put a cleared entry's signed line back, until that
//! signature's 30 days end. The pause is a field of the list: flipping it back lets go only
//! what the owner signed, is unchanged and not ended (5d33a: not worth a signature). A machine
//! with no owner key (the switch off, Windows until its reader is wired) sends nothing.

use crate::api::AppState;
use crate::guard::request_id;
use crate::sessions::InputError;
use aib_store::NewEvent;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

pub const FILE: &str = "seat_ticks.json";
pub const SET: &str = "seat_tick_set";
pub const CLEAR: &str = "seat_tick_clear";
pub const LIST: &str = "seat_tick_list";
/// The page's 急停 (5d33a / 519d0 06:17): `{on: true}` stops every timed sentence at once and
/// keeps the list; `{on: false}` lets them go again.
pub const PAUSE: &str = "seat_tick_pause";
/// The operations the owner approves: one seat's sentence, and a paused list going again.
pub const OP_SET: &str = "seat.tick_set";
pub const OP_RESUME: &str = "seat.tick_resume";
/// Why a sentence that is not the approved text is not sent.
pub const CHANGED: &str = "changed_since_approved";
/// Why a file entry is not sent yet: its approval is not a signature aibd can check.
pub const UNSIGNED: &str = "unsigned_not_sent";
/// Why a signed entry is not sent: the signature is not the checked key's of this entry, its
/// id was cleared, or its 30 days ended.
pub const BAD_SIG: &str = "bad_signature";
pub const REVOKED: &str = "revoked";
pub const EXPIRED: &str = "expired";
/// How long an approval lasts.
pub const VALID_SECS: u64 = 30 * 24 * 3600;
/// The ids cleared or replaced, in aibd's data folder; at most this many (the newest).
pub const REVOKED_FILE: &str = "seat_ticks_revoked.json";
pub const MAX_REVOKED: usize = 4096;

/// The shortest and longest period, in minutes.
pub const MIN_EVERY: u32 = 5;
pub const MAX_EVERY: u32 = 24 * 60;
/// At most this many seats on the list, and this many characters in a sentence.
pub const MAX_TICKS: usize = 32;
pub const MAX_CHARS: usize = 2000;
/// The largest file read.
const MAX_FILE: u64 = 256 * 1024;
/// Where a file sentence lives, in aibd's data folder, and the approved copies under it.
pub const TICKS_DIR: &str = "ticks";
pub const APPROVED_DIR: &str = ".approved";
/// The largest file sentence, and the longest name before `.txt`.
pub const MAX_TICK_FILE: u64 = 8 * 1024;
pub const NAME_MAX: usize = 60;
/// A `seat.tick_stuck` after this many rounds in a row that did not go in, and every so many.
pub const STUCK_EVERY: u32 = 3;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tick {
    pub seat_id: String,
    pub every_min: u32,
    pub text: String,
    #[serde(default)]
    pub set_at: String,
    /// Set with the owner's fingerprint (an entry from before that: false).
    #[serde(default)]
    pub approved: bool,
    /// The SHA-256 of the text the owner approved (lower-case hex; none on an old entry).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub text_sha256: String,
    /// The file under `<data>/ticks/` whose content is sent instead of `text` (a name).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub file: String,
    /// This approval's own id (aibd's), when it was given (unix seconds), the ticket, page and
    /// window words it was given on, and the key's signature of [`entry_binding`] (hex).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub id: String,
    #[serde(default, skip_serializing_if = "is_zero")]
    pub approved_at: u64,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub ticket: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub by: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub resource: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub sig: String,
}

fn is_zero(n: &u64) -> bool {
    *n == 0
}

/// A file sentence's name: 1 to [`NAME_MAX`] of `a-z 0-9 - _`, then `.txt`.
pub fn file_name_ok(name: &str) -> bool {
    name.strip_suffix(".txt").is_some_and(|stem| {
        (1..=NAME_MAX).contains(&stem.len())
            && stem
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
    })
}

fn plain(p: &std::path::Path) -> Option<std::fs::Metadata> {
    std::fs::symlink_metadata(p).ok()
}

/// The words in `<dir>/<name>` now: a plain file (neither it nor the folder a link), at most
/// [`MAX_TICK_FILE`] bytes of UTF-8, cleaned ([`crate::dm_import::clean`]), not blank.
pub fn read_file(dir: &std::path::Path, name: &str) -> Result<String, &'static str> {
    if !file_name_ok(name) {
        return Err("file_name");
    }
    if !plain(dir).is_some_and(|m| m.is_dir()) {
        return Err("file_missing");
    }
    let p = dir.join(name);
    let Some(m) = plain(&p) else {
        return Err("file_missing");
    };
    if !m.is_file() {
        return Err("file_bad");
    }
    // one size gate, on what was read: at most one byte past the cap is read (receipt
    // a9136-tk10-a0a3f020b: KT27 did not bite while a second gate on the metadata stood)
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(
        &mut std::io::Read::take(
            std::fs::File::open(&p).map_err(|_| "file_missing")?,
            MAX_TICK_FILE + 1,
        ),
        &mut bytes,
    )
    .map_err(|_| "file_missing")?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_TICK_FILE {
        return Err("file_bad");
    }
    let text = String::from_utf8(bytes).map_err(|_| "file_bad")?;
    let text = crate::dm_import::clean(&text);
    if text.trim().is_empty() {
        return Err("file_bad");
    }
    Ok(text)
}

/// `<data>/ticks`.
pub fn ticks_dir() -> Option<std::path::PathBuf> {
    crate::workspace::data_file(TICKS_DIR)
}

/// What `t` would send now: its text, or its file's words read from `dir`.
pub fn body_of(t: &Tick, dir: Option<&std::path::Path>) -> Result<String, &'static str> {
    if t.file.is_empty() {
        return Ok(t.text.clone());
    }
    read_file(dir.ok_or("file_missing")?, &t.file)
}

/// The copy of `name` the owner approved last (none, or none to read: empty).
pub fn approved_copy(dir: &std::path::Path, file: &str) -> String {
    if !file_name_ok(file) {
        return String::new();
    }
    let p = dir.join(APPROVED_DIR).join(file);
    match plain(&p) {
        Some(m) if m.is_file() && m.len() <= MAX_TICK_FILE => {
            std::fs::read_to_string(&p).unwrap_or_default()
        }
        _ => String::new(),
    }
}

/// Keep `body` as the approved copy of `name` (only the window's line count reads it).
pub fn keep_approved(dir: &std::path::Path, file: &str, body: &str) -> Result<(), String> {
    if !file_name_ok(file) {
        return Err("file_name".into());
    }
    let d = dir.join(APPROVED_DIR);
    if plain(&d).is_some_and(|m| !m.is_dir()) {
        return Err("approved folder is not a folder".into());
    }
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    let tmp = d.join(format!("{file}.tmp"));
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, d.join(file)).map_err(|e| e.to_string())
}

/// How many lines `new` has that `old` has not, and `old` has that `new` has not (a line
/// counts as often as it stands).
pub fn diff_lines(old: &str, new: &str) -> (usize, usize) {
    let mut left: HashMap<&str, i64> = HashMap::new();
    for l in old.lines() {
        *left.entry(l).or_default() += 1;
    }
    let mut added = 0;
    for l in new.lines() {
        match left.get_mut(l) {
            Some(n) if *n > 0 => *n -= 1,
            _ => added += 1,
        }
    }
    let removed: i64 = left.values().sum();
    (added, usize::try_from(removed).unwrap_or(0))
}

/// The line aibd puts before a file's words, so the seat and a person see it is not typed by
/// hand.
pub fn header(name: &str, at: &str) -> String {
    format!("[定時:{name} {at}]")
}

/// The SHA-256 of a sentence as it is typed, lower-case hex.
pub fn text_sha256(text: &str) -> String {
    crate::owner_ok::params_sha256(text.as_bytes())
}

/// The entry a fingerprint approves: it keeps the hash of this very text.
pub fn approved_tick(seat_id: &str, every_min: u32, text: &str, set_at: String) -> Tick {
    Tick {
        seat_id: seat_id.to_owned(),
        every_min,
        text: text.to_owned(),
        set_at,
        approved: true,
        text_sha256: text_sha256(text),
        ..Tick::default()
    }
}

/// The entry a fingerprint approves for a file: the name, and the hash of its words (`body`)
/// as read for the ask.
pub fn approved_file_tick(
    seat_id: &str,
    every_min: u32,
    file: &str,
    body: &str,
    set_at: String,
) -> Tick {
    Tick {
        seat_id: seat_id.to_owned(),
        every_min,
        text: String::new(),
        set_at,
        approved: true,
        text_sha256: text_sha256(body),
        file: file.to_owned(),
        ..Tick::default()
    }
}

/// Whether the entry's words now (`body`) are not the ones whose hash it carries (an entry
/// with no hash kept is changed too).
pub fn changed(t: &Tick, body: &str) -> bool {
    text_sha256(body) != t.text_sha256
}

/// When the entry's approval ends.
pub fn until_of(t: &Tick) -> u64 {
    t.approved_at.saturating_add(VALID_SECS)
}

/// What the key signs for an entry, field by field in this order (the bytes are pinned by a
/// test: a change here ends every approval).
#[derive(Serialize)]
struct SetParams<'a> {
    seat_id: &'a str,
    every_min: u32,
    file: &'a str,
    text_sha256: &'a str,
    at: u64,
    until: u64,
    id: &'a str,
}

/// The parameters of an entry's approval, built from the entry itself.
pub fn set_params(t: &Tick) -> Vec<u8> {
    serde_json::to_vec(&SetParams {
        seat_id: &t.seat_id,
        every_min: t.every_min,
        file: &t.file,
        text_sha256: &t.text_sha256,
        at: t.approved_at,
        until: until_of(t),
        id: &t.id,
    })
    .unwrap_or_default()
}

/// The owner ask of an entry with these window words, for the page `by`. The operation is
/// this constant whatever the list says, so no other act's signature fits.
fn ask_of(t: &Tick, resource: String, by: String) -> crate::owner_ok::Ask {
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_SET,
        resource,
        params: set_params(t),
        by,
    }
}

/// The bytes the key signed for this entry, built again from the entry.
pub fn entry_binding(t: &Tick) -> Vec<u8> {
    crate::owner_ok::binding(&t.ticket, &ask_of(t, t.resource.clone(), t.by.clone()))
}

fn unhex(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) || !s.is_ascii() {
        return None;
    }
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[i * 2..i * 2 + 2], 16).ok())
        .collect()
}

/// What a send is checked against: the time, the owner's key (`verify(bytes, signature)`),
/// the revoked ids.
pub struct Check<'a> {
    pub now: u64,
    pub verify: &'a dyn Fn(&[u8], &[u8]) -> bool,
    pub revoked: &'a [String],
}

/// Whether the entry's signature is the owner's key's of this very entry.
pub fn signed(t: &Tick, c: &Check) -> bool {
    unhex(&t.sig).is_some_and(|sig| !sig.is_empty() && (c.verify)(&entry_binding(t), &sig))
}

/// Why `t` is not sent this period whatever its seat does: its file cannot be read, it has no
/// signature, its words are not the approved ones, the signature does not check out, its id
/// was cleared, or its approval ended.
pub fn held(t: &Tick, body: &Result<String, &'static str>, c: &Check) -> Option<&'static str> {
    let body = match body {
        Ok(b) => b,
        Err(why) => return Some(*why),
    };
    if t.sig.is_empty() {
        return Some(UNSIGNED);
    }
    if changed(t, body) {
        return Some(CHANGED);
    }
    if !signed(t, c) {
        return Some(BAD_SIG);
    }
    if c.revoked.iter().any(|r| r == &t.id) {
        return Some(REVOKED);
    }
    if c.now >= until_of(t) {
        return Some(EXPIRED);
    }
    None
}

/// One entry's rounds since this aibd started (memory only).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Seen {
    /// When the last round ended, and how (`sent` or why it was skipped).
    pub last_at: Option<i64>,
    pub last: String,
    /// Rounds in a row that were skipped, and whole periods a due sentence has waited for a
    /// seat that is in a turn.
    pub stuck: u32,
    pub late: u32,
    /// Rounds so far (sent or skipped).
    pub round: u64,
    /// The time the next period is counted from.
    pub base: Option<i64>,
}

/// Book one try in `seen`; `Some((rounds, why))` when a `seat.tick_stuck` is due: at
/// [`STUCK_EVERY`] rounds in a row that did not go in, and every [`STUCK_EVERY`] more.
/// `base` is when the period was counted from, `now` the time of the try.
pub fn after(
    seen: &mut Seen,
    out: &Outcome,
    every_min: u32,
    base: i64,
    now: i64,
) -> Option<(u32, &'static str)> {
    match out {
        Outcome::Sent => {
            *seen = Seen {
                last_at: Some(now),
                last: "sent".into(),
                round: seen.round + 1,
                base: Some(now),
                ..Seen::default()
            };
            None
        }
        Outcome::Skipped(why) => {
            seen.last_at = Some(now);
            seen.last = (*why).to_owned();
            seen.round += 1;
            seen.stuck += 1;
            seen.late = 0;
            seen.base = Some(now);
            seen.stuck
                .is_multiple_of(STUCK_EVERY)
                .then_some((seen.stuck, *why))
        }
        Outcome::Later => {
            let period = i64::from(every_min.max(1)) * 60;
            let waited = now.saturating_sub(base) / period;
            let late = u32::try_from(waited.saturating_sub(1).max(0)).unwrap_or(u32::MAX);
            let tell = late > seen.late && late.is_multiple_of(STUCK_EVERY);
            seen.late = late;
            tell.then_some((seen.stuck.saturating_add(late), "seat_in_turn"))
        }
    }
}

/// The list as the page reads it: each entry with `signed` (the key's signature checks out),
/// `not_sent` (why it would not go now, else null), `until`, `approved`,
/// `changed_since_approved`, the first 12 of the hash of its words now (none when its file
/// cannot be read), and its rounds: `stuck`, `last_at` / `last`, `next_at` (unix seconds; null
/// before the pass saw it). No signature, ticket or page id leaves aibd.
pub fn shown(
    list: &[Tick],
    dir: Option<&std::path::Path>,
    seen: &HashMap<String, Seen>,
    c: &Check,
) -> Vec<Value> {
    list.iter()
        .map(|t| {
            let body = body_of(t, dir);
            let s = seen.get(&t.seat_id).cloned().unwrap_or_default();
            let was_approved = !t.text_sha256.is_empty();
            json!({"seat_id": t.seat_id, "every_min": t.every_min, "text": t.text,
                "file": t.file, "set_at": t.set_at, "approved": t.approved,
                "signed": signed(t, c), "not_sent": held(t, &body, c),
                "until": (t.approved_at > 0).then(|| until_of(t)),
                "changed_since_approved":
                    was_approved && body.as_ref().is_ok_and(|b| changed(t, b)),
                "file_error": body.as_ref().err(),
                "text_sha12": body.as_ref().ok().map(|b| text_sha256(b)[..12].to_owned()),
                "stuck": s.stuck.saturating_add(s.late), "round": s.round,
                "last_at": s.last_at, "last": s.last,
                "next_at": s.base.map(|b| b + i64::from(t.every_min) * 60)})
        })
        .collect()
}

/// A file's name as the window shows it: whole up to 24, else its first 8, `…`, its last 15.
fn name_shown(name: &str) -> String {
    let c: Vec<char> = name.chars().collect();
    if c.len() <= 24 {
        return name.to_owned();
    }
    c[..8]
        .iter()
        .chain(std::iter::once(&'…'))
        .chain(&c[c.len() - 15..])
        .collect()
}

/// The seat's code as the window shows it: the five after `seat_`.
fn code(seat_id: &str) -> String {
    seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect()
}

/// What the owner is asked for one seat's sentence: [`set_params`] are the parameters; the
/// window is aibd's words (no word of the sentence itself), for a file with the lines that
/// differ from the approved copy (`diff`: added, removed).
pub fn set_ask(t: &Tick, diff: Option<(usize, usize)>, by: String) -> crate::owner_ok::Ask {
    let sha12: String = t.text_sha256.chars().take(12).collect();
    let resource = if t.file.is_empty() {
        format!(
            "{} 每 {} 分定時送字 · {} 字 · 內容 {sha12}",
            code(&t.seat_id),
            t.every_min,
            t.text.trim().chars().count(),
        )
    } else {
        let (added, removed) = diff.unwrap_or_default();
        format!(
            "{} 每 {} 分送檔 {} · 內容 {sha12} · +{added} −{removed} 行",
            code(&t.seat_id),
            t.every_min,
            name_shown(&t.file),
        )
    };
    ask_of(t, resource, by)
}

/// The hash a resume binds for `t`: its text's, or its file's approved one.
fn entry_sha(t: &Tick) -> String {
    if t.file.is_empty() {
        text_sha256(&t.text)
    } else {
        t.text_sha256.clone()
    }
}

/// What the owner is asked to let a paused list go again: every entry's seat, period and text
/// hash are the parameters.
pub fn resume_ask(list: &[Tick], by: String) -> crate::owner_ok::Ask {
    let entries: Vec<Value> = list
        .iter()
        .map(|t| json!([t.seat_id, t.every_min, t.file, entry_sha(t)]))
        .collect();
    let params = serde_json::to_vec(&json!({ "resume": entries })).unwrap_or_default();
    let sha = crate::owner_ok::params_sha256(&params);
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_RESUME,
        resource: format!("恢復 {} 席的定時送字 · 名單 {}", list.len(), &sha[..12]),
        params,
        by,
    }
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct List {
    #[serde(default)]
    ticks: Vec<Tick>,
    #[serde(default)]
    paused: bool,
}

/// Whether the file says paused (a malformed file is not paused: it has no entries either).
pub fn parse_paused(bytes: &[u8]) -> bool {
    serde_json::from_slice::<List>(bytes).is_ok_and(|l| l.paused)
}

/// Why an entry is refused (the act's `code`).
pub fn check(t: &Tick) -> Result<(), &'static str> {
    if t.seat_id.is_empty() || t.seat_id.len() > 128 {
        return Err("seat_id_invalid");
    }
    if !(MIN_EVERY..=MAX_EVERY).contains(&t.every_min) {
        return Err("every_min_out_of_range");
    }
    if !t.file.is_empty() {
        // a file's words are read when sent: the entry carries the name only
        if !t.text.is_empty() {
            return Err("text_or_file");
        }
        if !file_name_ok(&t.file) {
            return Err("file_name");
        }
        return Ok(());
    }
    let n = t.text.trim().chars().count();
    if n == 0 || n > MAX_CHARS {
        return Err("text_length");
    }
    Ok(())
}

/// The list in `bytes`; anything malformed is an empty list, an entry that fails [`check`] is
/// left out, a seat named twice keeps its first entry, at most [`MAX_TICKS`].
pub fn parse(bytes: &[u8]) -> Vec<Tick> {
    let list: List = serde_json::from_slice(bytes).unwrap_or_default();
    let mut out: Vec<Tick> = Vec::new();
    for t in list.ticks {
        if check(&t).is_ok() && !out.iter().any(|o| o.seat_id == t.seat_id) {
            out.push(t);
        }
        if out.len() >= MAX_TICKS {
            break;
        }
    }
    out
}

/// `list` with `t` in place of the seat's entry (or added).
pub fn with(mut list: Vec<Tick>, t: Tick) -> Result<Vec<Tick>, &'static str> {
    check(&t)?;
    if let Some(o) = list.iter_mut().find(|o| o.seat_id == t.seat_id) {
        *o = t;
        return Ok(list);
    }
    if list.len() >= MAX_TICKS {
        return Err("too_many_ticks");
    }
    list.push(t);
    Ok(list)
}

/// The seats whose sentence is due at `now` (unix seconds). A seat not in `last` yet is put
/// there at `now` and is not due (its first sentence comes one period later); seats no longer
/// on the list are dropped from `last`.
pub fn due(list: &[Tick], last: &mut HashMap<String, i64>, now: i64) -> Vec<Tick> {
    last.retain(|s, _| list.iter().any(|t| &t.seat_id == s));
    let mut out = Vec::new();
    for t in list {
        match last.get(&t.seat_id) {
            None => {
                last.insert(t.seat_id.clone(), now);
            }
            Some(&l) if now.saturating_sub(l) >= i64::from(t.every_min) * 60 => out.push(t.clone()),
            Some(_) => {}
        }
    }
    out
}

/// What one try did: sent, skipped for this period, or try again on the next pass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Sent,
    Skipped(&'static str),
    Later,
}

/// A write's error as an [`Outcome`]: a seat that will take it soon is tried again.
pub fn outcome_of(e: InputError) -> Outcome {
    match e {
        InputError::InTurn | InputError::Busy | InputError::Queued | InputError::Starting => {
            Outcome::Later
        }
        e @ (InputError::NoSession | InputError::NoInput | InputError::Closed) => {
            Outcome::Skipped(e.code())
        }
    }
}

fn path() -> Option<std::path::PathBuf> {
    crate::workspace::data_file(FILE)
}

/// The list on disk now and whether it is paused (no file, or none to read: empty, not paused).
pub fn load_all() -> (Vec<Tick>, bool) {
    let Some(p) = path() else {
        return (Vec::new(), false);
    };
    match std::fs::metadata(&p) {
        Ok(m) if m.is_file() && m.len() <= MAX_FILE => std::fs::read(&p)
            .map(|b| (parse(&b), parse_paused(&b)))
            .unwrap_or_default(),
        _ => (Vec::new(), false),
    }
}

/// The entries that may be sent now: none while paused.
pub fn load() -> Vec<Tick> {
    match load_all() {
        (_, true) => Vec::new(),
        (l, false) => l,
    }
}

fn save(list: &[Tick], paused: bool) -> Result<(), String> {
    let p = path().ok_or("no data folder")?;
    let tmp = p.with_extension("json.tmp");
    let body = serde_json::to_vec_pretty(&json!({ "ticks": list, "paused": paused }))
        .map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &p).map_err(|e| e.to_string())
}

/// The revoked ids on disk now (no file, or none to read: none).
pub fn load_revoked() -> Vec<String> {
    let Some(p) = crate::workspace::data_file(REVOKED_FILE) else {
        return Vec::new();
    };
    match std::fs::symlink_metadata(&p) {
        Ok(m) if m.is_file() && m.len() <= MAX_FILE => std::fs::read(&p)
            .ok()
            .and_then(|b| serde_json::from_slice::<Vec<String>>(&b).ok())
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// `have` with `ids` added, the newest [`MAX_REVOKED`] kept, each once.
pub fn revoked_with(mut have: Vec<String>, ids: &[String]) -> Vec<String> {
    for id in ids {
        if !id.is_empty() && !have.contains(id) {
            have.push(id.clone());
        }
    }
    let over = have.len().saturating_sub(MAX_REVOKED);
    have.split_off(over)
}

/// Put `ids` on the revoked list (written whole, then renamed), before the list itself
/// changes: a crash in between leaves no entry that still goes (5d33a N2).
fn revoke(ids: &[String]) -> Result<(), String> {
    if ids.iter().all(String::is_empty) {
        return Ok(());
    }
    let p = crate::workspace::data_file(REVOKED_FILE).ok_or("no data folder")?;
    let tmp = p.with_extension("json.tmp");
    let body = serde_json::to_vec(&revoked_with(load_revoked(), ids)).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &p).map_err(|e| e.to_string())
}

fn owner_verify(msg: &[u8], sig: &[u8]) -> bool {
    crate::owner_ok::verify(msg, sig)
}

fn record(st: &AppState, kind: &str, payload: Value) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
    let event = NewEvent {
        event_id: id.clone(),
        aggregate_type: "seat_tick".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    if let Err(e) = st.store.append(vec![event]) {
        tracing::warn!(kind, error = %e, "seat tick event not written");
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn digest(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Type `t`'s words (`body`: read for this try) to its seat once (call it off the async
/// runtime).
fn send(st: &AppState, t: &Tick, body: &Result<String, &'static str>, c: &Check) -> Outcome {
    let meta = match st.store.seat_meta(&t.seat_id) {
        Ok(Some(m)) => m,
        Ok(None) => return Outcome::Skipped("seat_not_found"),
        Err(_) => return Outcome::Later,
    };
    let user_page = crate::static_files::web_source() == "user";
    if let Err(code) = takes(
        meta.mode.as_deref(),
        user_page,
        crate::seat_input::dgrs_input_on(),
    ) {
        return Outcome::Skipped(code);
    }
    if let Some(why) = held(t, body, c) {
        return Outcome::Skipped(why);
    }
    let Ok(body) = body else {
        return Outcome::Skipped("file_missing");
    };
    let typed = if t.file.is_empty() {
        body.clone()
    } else {
        format!(
            "{}\n{body}",
            header(&t.file, &crate::sessions::now_rfc3339())
        )
    };
    let Ok(line) = crate::seat_input::user_line(&typed) else {
        return Outcome::Skipped("text_refused");
    };
    match st.sessions.write_input_between_turns(&t.seat_id, &line) {
        Ok(_) => Outcome::Sent,
        Err(e) => outcome_of(e),
    }
}

/// Whether a seat in `mode` takes a timed sentence now: while a user page serves the whole
/// machine only types to safe seats (守則〈補充 11-18〉5), judged at each send, not only when set
/// (5d33a PREREAD-seattick L1: a page that appears later stops the work / dgrs sentences).
fn takes(mode: Option<&str>, user_page: bool, dgrs_on: bool) -> Result<(), &'static str> {
    if user_page && mode != Some("safe") {
        return Err("user_page_serves_safe_only");
    }
    if !crate::seat_input::mode_takes_input(mode, dgrs_on) {
        return Err("seat_mode_takes_no_input");
    }
    Ok(())
}

static RUNNING: AtomicBool = AtomicBool::new(false);
static SEEN: std::sync::OnceLock<std::sync::Mutex<HashMap<String, Seen>>> =
    std::sync::OnceLock::new();

fn seen() -> std::sync::MutexGuard<'static, HashMap<String, Seen>> {
    SEEN.get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

/// Start the pass when the list is not empty and it is not running (at start-up and after
/// each `seat_tick_set`). It stops by itself once the list is empty.
pub fn ensure_started(st: AppState) {
    if load().is_empty() || RUNNING.swap(true, Ordering::SeqCst) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("aibd-seat-tick".into())
        .spawn(move || {
            let mut last: HashMap<String, i64> = HashMap::new();
            loop {
                run(&st, &mut last);
                RUNNING.store(false, Ordering::SeqCst);
                // a set that came while the pass was ending found it running: look once more
                if load().is_empty() || RUNNING.swap(true, Ordering::SeqCst) {
                    break;
                }
            }
        });
    if spawned.is_err() {
        RUNNING.store(false, Ordering::SeqCst);
        tracing::error!("seat tick: could not start the thread");
    }
}

fn run(st: &AppState, last: &mut HashMap<String, i64>) {
    let op = crate::timers::SEAT_TICK.begin(None);
    let now = || crate::revive::unix_of(&crate::sessions::now_rfc3339()).unwrap_or(0);
    // the entries there now are seen at start: their first sentence is one period away
    let _ = due(&load(), last, now());
    while op.tick() {
        let list = load();
        if list.is_empty() {
            return;
        }
        let t_now = now();
        let dir = ticks_dir();
        let revoked = load_revoked();
        let c = Check {
            now: crate::pair_carry::now_unix(),
            verify: &owner_verify,
            revoked: &revoked,
        };
        for t in due(&list, last, t_now) {
            let body = body_of(&t, dir.as_deref());
            let sha12 = body.as_ref().ok().map(|b| text_sha256(b)[..12].to_owned());
            let out = send(st, &t, &body, &c);
            let base = last.get(&t.seat_id).copied().unwrap_or(t_now);
            let (told, round) = {
                let mut all = seen();
                let s = all.entry(t.seat_id.clone()).or_default();
                (after(s, &out, t.every_min, base, t_now), s.round)
            };
            match out {
                Outcome::Sent => {
                    last.insert(t.seat_id.clone(), t_now);
                    record(
                        st,
                        "seat.tick_sent",
                        json!({"seat_id": t.seat_id, "every_min": t.every_min,
                               "len": body.as_ref().map_or(0, String::len),
                               "digest": body.as_deref().map(digest).unwrap_or_default(),
                               "approved": t.approved, "file": t.file,
                               "text_sha12": sha12, "round": round}),
                    );
                }
                Outcome::Skipped(why) => {
                    last.insert(t.seat_id.clone(), t_now);
                    record(
                        st,
                        "seat.tick_skipped",
                        json!({"seat_id": t.seat_id, "reason": why, "file": t.file,
                               "text_sha12": sha12, "round": round}),
                    );
                }
                Outcome::Later => {}
            }
            if let Some((rounds, why)) = told {
                record(
                    st,
                    "seat.tick_stuck",
                    json!({"seat": t.seat_id, "rounds": rounds, "why": why}),
                );
            }
        }
        // what the page reads: only the seats on the list, each with where its period starts
        let mut all = seen();
        all.retain(|seat, _| list.iter().any(|t| &t.seat_id == seat));
        for t in &list {
            all.entry(t.seat_id.clone()).or_default().base = last.get(&t.seat_id).copied();
        }
    }
}

/// [`shown`] with the ticks folder, this aibd's rounds, the owner's key and the revoked ids.
fn listed(list: &[Tick]) -> Vec<Value> {
    let all = seen().clone();
    let revoked = load_revoked();
    let c = Check {
        now: crate::pair_carry::now_unix(),
        verify: &owner_verify,
        revoked: &revoked,
    };
    shown(list, ticks_dir().as_deref(), &all, &c)
}

/// The id and time of an approval being asked, by its ticket, until the page comes back with
/// it (memory only; a restart drops them with the tickets).
static ASKING: std::sync::OnceLock<std::sync::Mutex<HashMap<String, (String, u64)>>> =
    std::sync::OnceLock::new();

fn asking() -> std::sync::MutexGuard<'static, HashMap<String, (String, u64)>> {
    ASKING
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// `seat_tick_set {seat_id, every_min, text, ticket?}`, `seat_tick_clear {seat_id}`,
/// `seat_tick_list {}`, `seat_tick_pause {on, ticket?}`: the built-in page a person paired (not
/// a user folder page, not a carried pairing: what it sets is typed to a seat later with no one
/// watching). Setting, and letting a paused list go again, are the owner's: without a ticket
/// `202 {ticket}`, with it written only when the ticket approves this very request.
pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "seat_tick_built_in_page_only",
            "a seat's timed sentence is set from the built-in page a person paired in this run",
        );
    }
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    let seat_id = body
        .get("seat_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let by = format!("page:{}", hex(&page.digest[..8]));
    let ticket = body.get("ticket").and_then(Value::as_str);
    let (list, paused) = load_all();
    if kind == PAUSE {
        let on = body.get("on").and_then(Value::as_bool).unwrap_or(true);
        // stopping is the safe way and needs no one; going again is the owner's
        if paused && !on {
            let ask = resume_ask(&list, by);
            let Some(ticket) = ticket else {
                return crate::repos::asked(&ask);
            };
            if !crate::owner_ok::approved_for(ticket, &ask) {
                return crate::repos::not_approved();
            }
        }
        if let Err(e) = save(&list, on) {
            tracing::warn!(error = %e, "seat ticks not written");
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "list not written",
            );
        }
        record(
            &st,
            "seat.tick_paused",
            json!({ "paused": on, "ticks": list.len() }),
        );
        if !on {
            ensure_started(st);
        }
        return Json(json!({ "ticks": listed(&list), "paused": on })).into_response();
    }
    let next = match kind {
        LIST => return Json(json!({ "ticks": listed(&list), "paused": paused })).into_response(),
        CLEAR => {
            let n = list.len();
            let gone: Vec<String> = list
                .iter()
                .filter(|t| t.seat_id == seat_id)
                .map(|t| t.id.clone())
                .collect();
            let next: Vec<Tick> = list.into_iter().filter(|t| t.seat_id != seat_id).collect();
            if next.len() == n {
                return refuse(
                    StatusCode::NOT_FOUND,
                    "no_tick",
                    "no timed sentence for this seat",
                );
            }
            // the cleared approval's id first, then the list
            if let Err(e) = revoke(&gone) {
                tracing::warn!(error = %e, "seat tick: revoked ids not written");
                return refuse(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    "list not written",
                );
            }
            next
        }
        _ => {
            let every = body
                .get("every_min")
                .and_then(Value::as_u64)
                .and_then(|n| u32::try_from(n).ok())
                .unwrap_or(0);
            let text = body.get("text").and_then(Value::as_str).unwrap_or_default();
            let file = body.get("file").and_then(Value::as_str).unwrap_or_default();
            let at = crate::sessions::now_rfc3339();
            // a file is read now: the owner is asked for these very words
            let (t, words) = if file.is_empty() {
                (approved_tick(&seat_id, every, text, at), None)
            } else if !text.is_empty() {
                return refuse(
                    StatusCode::BAD_REQUEST,
                    "text_or_file",
                    "a timed sentence is its text or a file's name, not both",
                );
            } else {
                let Some(dir) = ticks_dir() else {
                    return refuse(StatusCode::BAD_REQUEST, "file_missing", "no such file");
                };
                match read_file(&dir, file) {
                    Ok(b) => (
                        approved_file_tick(&seat_id, every, file, &b, at),
                        Some((dir, b)),
                    ),
                    Err(code) => {
                        return refuse(
                            StatusCode::BAD_REQUEST,
                            code,
                            "the file is a name under aibd's ticks folder: a plain UTF-8 file of at most 8 KB",
                        )
                    }
                }
            };
            let diff = words
                .as_ref()
                .map(|(dir, b)| diff_lines(&approved_copy(dir, file), b));
            let store = st.store.clone();
            let sid = seat_id.clone();
            let known = tokio::task::spawn_blocking(move || store.seat_meta(&sid))
                .await
                .ok()
                .and_then(Result::ok)
                .flatten()
                .is_some();
            if !known {
                return refuse(StatusCode::NOT_FOUND, "seat_not_found", "no such seat");
            }
            // the approval's own id and time: new for a first ask, the asked ones for a ticket
            let mut t = t;
            let asked = ticket.and_then(|k| asking().remove(k));
            let fresh = asked.is_none();
            (t.id, t.approved_at) = match asked {
                Some(a) => a,
                None => (
                    crate::sessions::new_id("tk_").unwrap_or_default(),
                    crate::pair_carry::now_unix(),
                ),
            };
            if t.id.is_empty() {
                return refuse(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    "no id for this approval",
                );
            }
            let ask = set_ask(&t, diff, by);
            let replaced: Vec<String> = list
                .iter()
                .filter(|o| o.seat_id == seat_id)
                .map(|o| o.id.clone())
                .collect();
            if let Err(code) = with(list.clone(), t.clone()) {
                return refuse(StatusCode::BAD_REQUEST, code, "timed sentence not set");
            }
            // the entry is good and its seat is there: now it is the owner's to say
            let Some(ticket) = ticket else {
                return match crate::owner_ok::ask(&ask) {
                    crate::owner_ok::Asked::Ticket(k) => {
                        let mut a = asking();
                        if a.len() >= MAX_TICKS * 2 {
                            a.clear();
                        }
                        a.insert(k.clone(), (t.id.clone(), t.approved_at));
                        drop(a);
                        (
                            StatusCode::ACCEPTED,
                            Json(json!({"done": false, "ticket": k,
                                "reason": "owner_check_pending"})),
                        )
                            .into_response()
                    }
                    crate::owner_ok::Asked::Unavailable(why) => (
                        StatusCode::FORBIDDEN,
                        Json(json!({"code": "forbidden", "reason": why,
                            "message": "this needs the owner's fingerprint, and it cannot be asked on this machine yet",
                            "request_id": request_id()})),
                    )
                        .into_response(),
                };
            };
            // a ticket this aibd did not hand out for a sentence has no id to sign for
            let sig = if fresh {
                None
            } else {
                crate::owner_ok::approved_signed_for(ticket, &ask)
            };
            let Some(sig) = sig else {
                return crate::repos::not_approved();
            };
            t.ticket = ticket.to_owned();
            t.by = ask.by.clone();
            t.resource = ask.resource.clone();
            t.sig = hex(&sig);
            if let Some((dir, b)) = &words {
                if let Err(e) = keep_approved(dir, file, b) {
                    tracing::warn!(error = %e, "seat tick: approved copy not kept");
                }
            }
            // the replaced approval's id first, then the list
            if let Err(e) = revoke(&replaced) {
                tracing::warn!(error = %e, "seat tick: revoked ids not written");
                return refuse(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    "list not written",
                );
            }
            match with(list, t) {
                Ok(l) => l,
                Err(code) => {
                    return refuse(StatusCode::BAD_REQUEST, code, "timed sentence not set")
                }
            }
        }
    };
    if let Err(e) = save(&next, paused) {
        tracing::warn!(error = %e, "seat ticks not written");
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "list not written",
        );
    }
    let one = next.iter().find(|t| t.seat_id == seat_id);
    record(
        &st,
        if kind == CLEAR {
            "seat.tick_cleared"
        } else {
            "seat.tick_set"
        },
        json!({"seat_id": seat_id, "every_min": one.map(|t| t.every_min),
               "digest": one.map(|t| digest(&t.text)),
               "approved": one.map(|t| t.approved),
               "signed": one.map(|t| !t.sig.is_empty()),
               "until": one.filter(|t| t.approved_at > 0).map(until_of),
               "file": one.map(|t| t.file.clone()),
               "text_sha12": one.map(|t| entry_sha(t).chars().take(12).collect::<String>())}),
    );
    ensure_started(st);
    Json(json!({ "ticks": listed(&next), "paused": paused })).into_response()
}

/// Whether `kind` is one of this module's acts.
pub fn is_act(kind: Option<&str>) -> bool {
    matches!(kind, Some(SET | CLEAR | LIST | PAUSE))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tick(seat: &str, every: u32, text: &str) -> Tick {
        Tick {
            seat_id: seat.into(),
            every_min: every,
            text: text.into(),
            set_at: String::new(),
            ..Tick::default()
        }
    }
    fn now_of(t: &Tick) -> Result<String, &'static str> {
        Ok(t.text.clone())
    }

    use crate::touch::{KeyDevice, KeyFiles, KeyPresence, Presence, PUBLIC_KEY_LEN};

    /// A key whose signature is its public key and then the message: another key's does not
    /// check out.
    struct Dev(u8);
    impl KeyDevice for Dev {
        fn probe(&self, _: &[u8]) -> Result<(), &'static str> {
            Ok(())
        }
        fn public_key(&self, _: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
            Ok([self.0; PUBLIC_KEY_LEN])
        }
        fn sign(&self, _: &[u8], _: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
            Ok([&[self.0; PUBLIC_KEY_LEN][..], msg].concat())
        }
        fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
            sig == [&public[..], msg].concat()
        }
    }
    fn key(n: u8) -> KeyPresence {
        let files = KeyFiles {
            blob: vec![1],
            public: [n; PUBLIC_KEY_LEN],
        };
        KeyPresence::new(Box::new(Dev(n)), files).unwrap()
    }
    const BY: &str = "page:0123abcd";
    /// `t` approved at `at` by the key `k`, as the act writes it.
    fn sign(mut t: Tick, k: &KeyPresence, at: u64) -> Tick {
        t.id = "tk_1".into();
        t.approved_at = at;
        t.ticket = "ot_1".into();
        t.by = BY.into();
        t.resource = set_ask(&t, None, BY.into()).resource;
        let mut sig = Vec::new();
        k.ask_until_keep(
            "r",
            &entry_binding(&t),
            std::time::Duration::from_secs(1),
            &mut sig,
        )
        .unwrap();
        t.sig = hex(&sig);
        t
    }

    #[test]
    fn the_fingerprint_binds_the_seat_the_period_and_the_text() {
        let t = approved_tick("seat_abcde12345", 20, "繼續", String::new());
        let a = set_ask(&t, None, "page:0123abcd".into());
        assert_eq!(a.operation, OP_SET);
        assert!(
            crate::owner_ok::WIRED.contains(&OP_SET) && crate::owner_ok::WIRED.contains(&OP_RESUME),
            "reason: tick_ask_raises_no_window"
        );
        assert!(crate::owner_ok::page_category(a.category));
        let sha = text_sha256("繼續");
        assert_eq!(sha.len(), 64);
        assert_eq!(
            a.resource,
            format!("abcde 每 20 分定時送字 · 2 字 · 內容 {}", &sha[..12]),
            "reason: window_words"
        );
        assert!(a.resource.chars().count() <= crate::owner_ok::RESOURCE_MAX);
        assert!(!a.resource.contains("繼續"), "reason: sentence_in_window");
        let other = |seat: &str, every: u32, text: &str| {
            set_ask(
                &approved_tick(seat, every, text, String::new()),
                None,
                a.by.clone(),
            )
            .params
        };
        assert_eq!(other("seat_abcde12345", 20, "繼續"), a.params);
        assert_ne!(
            other("seat_zzzzz12345", 20, "繼續"),
            a.params,
            "reason: seat_not_bound"
        );
        assert_ne!(
            other("seat_abcde12345", 30, "繼續"),
            a.params,
            "reason: period_not_bound"
        );
        assert_ne!(
            other("seat_abcde12345", 20, "繼續。"),
            a.params,
            "reason: text_not_bound"
        );
        // letting a paused list go again binds every entry
        let list = vec![t.clone(), tick("seat_b", 30, "y")];
        let r = resume_ask(&list, "page:0123abcd".into());
        assert_eq!(r.operation, OP_RESUME);
        assert!(
            r.resource.starts_with("恢復 2 席的定時送字"),
            "{}",
            r.resource
        );
        let mut edited = list.clone();
        edited[1].text = "z".into();
        assert_ne!(
            resume_ask(&edited, r.by.clone()).params,
            r.params,
            "reason: resume_not_bound_to_text"
        );
        assert_ne!(
            resume_ask(&list[..1], r.by.clone()).params,
            r.params,
            "reason: resume_not_bound_to_list"
        );
    }

    #[test]
    fn a_text_that_is_not_the_approved_one_is_not_sent() {
        let k = key(4);
        let v = |m: &[u8], s: &[u8]| k.verify(m, s);
        let c = Check {
            now: 2000,
            verify: &v,
            revoked: &[],
        };
        let t = approved_tick("seat_a", 20, "繼續", String::new());
        assert!(t.approved, "reason: approval_not_kept");
        assert_eq!(
            t.text_sha256,
            text_sha256("繼續"),
            "reason: approved_hash_not_kept"
        );
        let t = sign(t, &k, 1000);
        assert_eq!(
            held(&t, &now_of(&t), &c),
            None,
            "reason: approved_text_held"
        );
        // the text in the list was edited after the fingerprint
        let mut edited = t.clone();
        edited.text = "刪掉全部".into();
        assert!(changed(&edited, &edited.text));
        assert_eq!(
            held(&edited, &now_of(&edited), &c),
            Some(CHANGED),
            "reason: changed_text_sent"
        );
        // an approval with no hash kept approves no text
        let mut bare = t.clone();
        bare.text_sha256 = String::new();
        assert_eq!(
            held(&bare, &now_of(&bare), &c),
            Some(CHANGED),
            "reason: no_hash_sent"
        );
        let old = tick("seat_o", 20, "x");
        let v = shown(&[t.clone(), edited, old], None, &HashMap::new(), &c);
        assert_eq!(
            (&v[0]["approved"], &v[0]["changed_since_approved"]),
            (&json!(true), &json!(false))
        );
        assert_eq!(
            v[1]["changed_since_approved"],
            json!(true),
            "reason: change_not_listed"
        );
        assert_eq!(
            (&v[2]["approved"], &v[2]["changed_since_approved"]),
            (&json!(false), &json!(false)),
            "reason: old_entry_listed_approved"
        );
        assert_eq!(v[0]["text_sha12"], json!(&t.text_sha256[..12]));
        // the file keeps every field; an old file reads as not approved
        let b = serde_json::to_vec(&json!({ "ticks": [t.clone()] })).unwrap();
        assert_eq!(parse(&b), vec![t], "reason: approval_lost_in_file");
        let old = parse(br#"{"ticks":[{"seat_id":"a","every_min":20,"text":"x"}]}"#);
        assert!(!old[0].approved && old[0].text_sha256.is_empty() && old[0].sig.is_empty());
    }

    #[test]
    fn only_an_entry_the_owners_key_signed_is_sent() {
        let k = key(4);
        let v = |m: &[u8], s: &[u8]| k.verify(m, s);
        let at = 1000;
        let c = Check {
            now: at + 1,
            verify: &v,
            revoked: &[],
        };
        let t = sign(approved_tick("seat_a", 20, "繼續", String::new()), &k, at);
        assert_eq!(held(&t, &now_of(&t), &c), None, "reason: signed_entry_held");
        assert!(signed(&t, &c));
        // what the key signed, byte for byte: a change here ends every approval
        let sha = text_sha256("繼續");
        let params = format!(
            r#"{{"seat_id":"seat_a","every_min":20,"file":"","text_sha256":"{sha}","at":1000,"until":2593000,"id":"tk_1"}}"#
        );
        assert_eq!(
            String::from_utf8(set_params(&t)).unwrap(),
            params,
            "reason: signed_bytes_moved"
        );
        assert_eq!(
            String::from_utf8(entry_binding(&t)).unwrap(),
            format!(
                "aibd/owner-ok/v1\not_1\nseat.tick_set\n{}\n{}\npage:0123abcd\n",
                t.resource,
                crate::owner_ok::params_sha256(params.as_bytes())
            ),
            "reason: binding_moved"
        );
        // no signature: an entry from before, or one written into the list by hand
        let unsigned = approved_tick("seat_a", 20, "繼續", String::new());
        assert_eq!(
            held(&unsigned, &now_of(&unsigned), &c),
            Some(UNSIGNED),
            "reason: unsigned_sent"
        );
        let old = tick("seat_o", 20, "x");
        assert_eq!(
            held(&old, &now_of(&old), &c),
            Some(UNSIGNED),
            "reason: old_entry_sent"
        );
        // anything the key signed, edited in the list
        type Edit = (&'static str, fn(&mut Tick));
        let edits: [Edit; 7] = [
            ("seat_edited", |t| t.seat_id = "seat_b".into()),
            ("period_edited", |t| t.every_min = 5),
            ("file_edited", |t| t.file = "a.txt".into()),
            ("time_edited", |t| t.approved_at += 86_400),
            ("id_edited", |t| t.id = "tk_2".into()),
            ("sig_cut", |t| t.sig.truncate(10)),
            ("sig_not_hex", |t| t.sig = "zz".into()),
        ];
        for (why, f) in edits {
            let mut e = t.clone();
            f(&mut e);
            assert_eq!(
                held(&e, &Ok("繼續".into()), &c),
                Some(BAD_SIG),
                "reason: {why}_sent"
            );
        }
        // a signature by a key that is not the one checked at start (a key put in the list
        // or on the disk is never read)
        let other = sign(
            approved_tick("seat_a", 20, "繼續", String::new()),
            &key(9),
            at,
        );
        assert_eq!(
            held(&other, &now_of(&other), &c),
            Some(BAD_SIG),
            "reason: other_key_taken"
        );
        // the owner's signature for another act, with these very parameters
        let mut borrowed = t.clone();
        let mut ask = ask_of(&t, t.resource.clone(), BY.into());
        ask.operation = OP_RESUME;
        let mut sig = Vec::new();
        k.ask_until_keep(
            "r",
            &crate::owner_ok::binding(&t.ticket, &ask),
            std::time::Duration::from_secs(1),
            &mut sig,
        )
        .unwrap();
        borrowed.sig = hex(&sig);
        assert_eq!(
            held(&borrowed, &now_of(&borrowed), &c),
            Some(BAD_SIG),
            "reason: other_acts_signature_taken"
        );
        // a cleared approval put back into the list
        let revoked = ["tk_0".to_owned(), "tk_1".to_owned()];
        let gone = Check {
            now: at + 1,
            verify: &v,
            revoked: &revoked,
        };
        assert_eq!(
            held(&t, &now_of(&t), &gone),
            Some(REVOKED),
            "reason: revoked_sent"
        );
        // thirty days, then it is asked again
        let last = Check {
            now: at + VALID_SECS - 1,
            verify: &v,
            revoked: &[],
        };
        assert_eq!(held(&t, &now_of(&t), &last), None);
        let ended = Check {
            now: at + VALID_SECS,
            verify: &v,
            revoked: &[],
        };
        assert_eq!(
            held(&t, &now_of(&t), &ended),
            Some(EXPIRED),
            "reason: ended_approval_sent"
        );
        // what the page reads, and nothing of the signature
        let l = shown(&[t.clone(), unsigned, other], None, &HashMap::new(), &c);
        assert_eq!(
            (&l[0]["signed"], &l[0]["not_sent"], &l[0]["until"]),
            (&json!(true), &Value::Null, &json!(at + VALID_SECS))
        );
        assert_eq!(
            (&l[1]["signed"], &l[1]["not_sent"]),
            (&json!(false), &json!(UNSIGNED)),
            "reason: unsigned_listed_signed"
        );
        assert_eq!(
            (&l[2]["signed"], &l[2]["not_sent"]),
            (&json!(false), &json!(BAD_SIG))
        );
        let text = l[0].to_string();
        for secret in [t.sig.as_str(), "ot_1", BY] {
            assert!(!text.contains(secret), "reason: leaked {secret}");
        }
        // the revoked list keeps each id once, the newest
        assert_eq!(
            revoked_with(vec!["a".into()], &["a".into(), String::new(), "b".into()]),
            ["a", "b"]
        );
        let many: Vec<String> = (0..MAX_REVOKED + 2).map(|i| format!("tk_{i}")).collect();
        let kept = revoked_with(many, &["new".into()]);
        assert_eq!(kept.len(), MAX_REVOKED);
        assert_eq!(
            kept.last().map(String::as_str),
            Some("new"),
            "reason: newest_revoked_dropped"
        );
        assert_eq!(unhex("00ff"), Some(vec![0, 255]));
        assert_eq!(unhex("0"), None);
    }

    #[test]
    fn an_entry_needs_a_seat_a_period_in_range_and_a_sentence() {
        assert_eq!(check(&tick("s", 20, "繼續")), Ok(()));
        assert_eq!(check(&tick("", 20, "x")), Err("seat_id_invalid"));
        assert_eq!(
            check(&tick("s", MIN_EVERY - 1, "x")),
            Err("every_min_out_of_range")
        );
        assert_eq!(
            check(&tick("s", MAX_EVERY + 1, "x")),
            Err("every_min_out_of_range")
        );
        assert_eq!(check(&tick("s", 20, "  ")), Err("text_length"));
        let long = "字".repeat(MAX_CHARS + 1);
        assert_eq!(check(&tick("s", 20, &long)), Err("text_length"));
    }

    #[test]
    fn the_file_keeps_good_entries_once_per_seat_and_a_bad_file_is_empty() {
        let b = br#"{"ticks":[{"seat_id":"a","every_min":20,"text":"x"},
            {"seat_id":"a","every_min":30,"text":"y"},
            {"seat_id":"b","every_min":1,"text":"z"},
            {"seat_id":"c","every_min":60,"text":"w"}]}"#;
        let l = parse(b);
        assert_eq!(l.len(), 2, "reason: bad_or_twice_kept {l:?}");
        assert_eq!((l[0].seat_id.as_str(), l[0].every_min), ("a", 20));
        assert_eq!(l[1].seat_id, "c");
        assert!(parse(b"not json").is_empty());
        assert!(
            parse_paused(br#"{"ticks":[],"paused":true}"#),
            "reason: pause_not_read"
        );
        assert!(!parse_paused(b"not json") && !parse_paused(br#"{"ticks":[]}"#));
        let many: Vec<Value> = (0..MAX_TICKS + 3)
            .map(|i| json!({"seat_id": format!("s{i}"), "every_min": 20, "text": "x"}))
            .collect();
        let b = serde_json::to_vec(&json!({ "ticks": many })).unwrap();
        assert_eq!(parse(&b).len(), MAX_TICKS);
    }

    #[test]
    fn setting_replaces_the_seats_entry_and_the_list_has_a_limit() {
        let l = with(vec![tick("a", 20, "x")], tick("a", 30, "y")).unwrap();
        assert_eq!(l, vec![tick("a", 30, "y")], "reason: not_replaced");
        let full: Vec<Tick> = (0..MAX_TICKS)
            .map(|i| tick(&format!("s{i}"), 20, "x"))
            .collect();
        assert_eq!(
            with(full.clone(), tick("new", 20, "x")),
            Err("too_many_ticks")
        );
        assert!(
            with(full, tick("s0", 25, "x")).is_ok(),
            "reason: replace_refused_when_full"
        );
        assert_eq!(
            with(vec![], tick("a", 2, "x")),
            Err("every_min_out_of_range")
        );
    }

    #[test]
    fn a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent() {
        let list = vec![tick("a", 20, "x"), tick("b", 5, "y")];
        let mut last = HashMap::new();
        assert!(
            due(&list, &mut last, 1000).is_empty(),
            "reason: sent_at_once"
        );
        assert_eq!(last.get("a"), Some(&1000));
        assert!(due(&list, &mut last, 1000 + 5 * 60 - 1).is_empty());
        let d = due(&list, &mut last, 1000 + 5 * 60);
        assert_eq!(
            d.iter().map(|t| t.seat_id.as_str()).collect::<Vec<_>>(),
            ["b"]
        );
        let d = due(&list, &mut last, 1000 + 20 * 60);
        assert_eq!(d.len(), 2, "reason: a_not_due_at_its_period");
        // a seat taken off the list is forgotten; put back, it waits a whole period again
        let mut last = HashMap::from([("a".to_owned(), 0), ("gone".to_owned(), 0)]);
        let _ = due(&list[..1], &mut last, 10);
        assert!(!last.contains_key("gone"), "reason: removed_seat_kept");
        assert!(!last.contains_key("b"));
    }

    #[test]
    fn a_seat_that_will_take_it_soon_is_tried_again_and_the_rest_wait_a_period() {
        for e in [
            InputError::InTurn,
            InputError::Busy,
            InputError::Queued,
            InputError::Starting,
        ] {
            assert_eq!(outcome_of(e), Outcome::Later, "reason: retry_dropped {e:?}");
        }
        assert_eq!(
            outcome_of(InputError::NoSession),
            Outcome::Skipped("seat_not_running")
        );
        assert_eq!(
            outcome_of(InputError::Closed),
            Outcome::Skipped("seat_input_closed")
        );
        assert!(is_act(Some(SET)) && is_act(Some(CLEAR)) && is_act(Some(LIST)));
        assert!(is_act(Some(PAUSE)), "reason: pause_not_an_act");
        assert!(!is_act(Some("seat_input")) && !is_act(None));
    }

    #[test]
    fn a_user_page_that_appears_later_stops_all_but_safe_seats() {
        assert_eq!(takes(Some("work"), false, false), Ok(()));
        assert_eq!(takes(Some("safe"), true, false), Ok(()));
        assert_eq!(
            takes(Some("work"), true, false),
            Err("user_page_serves_safe_only"),
            "reason: user_page_work_typed"
        );
        assert_eq!(
            takes(Some("dgrs"), true, true),
            Err("user_page_serves_safe_only"),
            "reason: user_page_dgrs_typed"
        );
        assert_eq!(
            takes(Some("dgrs"), false, false),
            Err("seat_mode_takes_no_input")
        );
    }

    #[test]
    fn a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean() {
        for ok in ["drive.txt", "a.txt", "plan-519d0_1.txt"] {
            assert!(file_name_ok(ok), "reason: name_refused {ok}");
        }
        let long = format!("{}.txt", "a".repeat(NAME_MAX + 1));
        for bad in [
            "",
            ".txt",
            "a.md",
            "A.txt",
            "../a.txt",
            "a/b.txt",
            "a\\b.txt",
            "a b.txt",
            ".approved",
            long.as_str(),
        ] {
            assert!(!file_name_ok(bad), "reason: name_taken {bad}");
        }
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join(TICKS_DIR);
        assert_eq!(
            read_file(&dir, "a.txt"),
            Err("file_missing"),
            "reason: no_folder"
        );
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(read_file(&dir, "a.txt"), Err("file_missing"));
        assert_eq!(
            read_file(&dir, "../a.txt"),
            Err("file_name"),
            "reason: path_read"
        );
        std::fs::write(
            dir.join("a.txt"),
            "第一行\n\u{1b}[31m第二行\u{1b}[0m\u{200b}\n",
        )
        .unwrap();
        assert_eq!(
            read_file(&dir, "a.txt").as_deref(),
            Ok("第一行\n第二行\n"),
            "reason: not_cleaned"
        );
        std::fs::write(dir.join("blank.txt"), " \n\n").unwrap();
        assert_eq!(read_file(&dir, "blank.txt"), Err("file_bad"));
        std::fs::write(dir.join("bin.txt"), [0xff, 0xfe, 0x41]).unwrap();
        assert_eq!(
            read_file(&dir, "bin.txt"),
            Err("file_bad"),
            "reason: not_utf8_read"
        );
        let cap = usize::try_from(MAX_TICK_FILE).unwrap();
        std::fs::write(dir.join("big.txt"), "a".repeat(cap + 1)).unwrap();
        assert_eq!(
            read_file(&dir, "big.txt"),
            Err("file_bad"),
            "reason: big_file_read"
        );
        std::fs::write(dir.join("full.txt"), "a".repeat(cap)).unwrap();
        assert!(read_file(&dir, "full.txt").is_ok());
        std::fs::create_dir(dir.join("dir.txt")).unwrap();
        assert_eq!(read_file(&dir, "dir.txt"), Err("file_bad"));
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(dir.join("a.txt"), dir.join("link.txt")).unwrap();
            assert_eq!(
                read_file(&dir, "link.txt"),
                Err("file_bad"),
                "reason: link_followed"
            );
            let other = d.path().join("elsewhere");
            std::os::unix::fs::symlink(&dir, &other).unwrap();
            assert_eq!(
                read_file(&other, "a.txt"),
                Err("file_missing"),
                "reason: linked_folder_read"
            );
        }
        // an entry is its text or a file's name, never both
        let mut t = tick("s", 20, "");
        t.file = "a.txt".into();
        assert_eq!(check(&t), Ok(()));
        assert_eq!(body_of(&t, Some(&dir)).as_deref(), Ok("第一行\n第二行\n"));
        assert_eq!(body_of(&t, None), Err("file_missing"));
        t.text = "x".into();
        assert_eq!(check(&t), Err("text_or_file"), "reason: both_taken");
        t.text.clear();
        t.file = "../a.txt".into();
        assert_eq!(check(&t), Err("file_name"), "reason: path_listed");
        assert_eq!(
            header("a.txt", "2026-11-21T01:00:00Z"),
            "[定時:a.txt 2026-11-21T01:00:00Z]"
        );
    }

    #[test]
    fn a_file_that_is_not_the_approved_content_is_not_sent() {
        let body = "每 20 分做一輪\n先讀信\n";
        let t = approved_file_tick("seat_abcde12345", 20, "drive.txt", body, String::new());
        assert!(t.approved && t.text.is_empty());
        assert_eq!(
            t.text_sha256,
            text_sha256(body),
            "reason: approved_content_hash_not_kept"
        );
        let k = key(4);
        let v = |m: &[u8], s: &[u8]| k.verify(m, s);
        let c = Check {
            now: 2000,
            verify: &v,
            revoked: &[],
        };
        // approved and unchanged, but not signed: not sent
        assert_eq!(
            held(&t, &Ok(body.to_owned()), &c),
            Some(UNSIGNED),
            "reason: unsigned_file_sent"
        );
        let s = sign(t.clone(), &k, 1000);
        assert_eq!(held(&s, &Ok(body.to_owned()), &c), None);
        assert_eq!(
            held(&s, &Ok(format!("{body}再刪掉全部\n")), &c),
            Some(CHANGED),
            "reason: changed_file_sent"
        );
        assert_eq!(
            held(&s, &Err("file_missing"), &c),
            Some("file_missing"),
            "reason: unreadable_file_sent"
        );
        // the signature is for this file's name
        let mut renamed = s.clone();
        renamed.file = "other.txt".into();
        assert_eq!(
            held(&renamed, &Ok(body.to_owned()), &c),
            Some(BAD_SIG),
            "reason: renamed_file_sent"
        );
        // the fingerprint binds the name and the content; the window counts the lines
        let a = set_ask(&t, Some((2, 0)), "page:0123abcd".into());
        assert_eq!(
            a.resource,
            format!(
                "abcde 每 20 分送檔 drive.txt · 內容 {} · +2 −0 行",
                &t.text_sha256[..12]
            ),
            "reason: window_words"
        );
        let ask = |file: &str, body: &str| {
            set_ask(
                &approved_file_tick("seat_abcde12345", 20, file, body, String::new()),
                None,
                a.by.clone(),
            )
            .params
        };
        assert_eq!(ask("drive.txt", body), a.params);
        assert_ne!(
            ask("other.txt", body),
            a.params,
            "reason: file_name_not_bound"
        );
        assert_ne!(
            ask("drive.txt", "別的字\n"),
            a.params,
            "reason: file_content_not_bound"
        );
        let long = format!("{}.txt", "n".repeat(NAME_MAX));
        let w = set_ask(
            &approved_file_tick("seat_abcde12345", 1440, &long, body, String::new()),
            Some((999, 999)),
            a.by.clone(),
        );
        assert!(
            w.resource.chars().count() <= crate::owner_ok::RESOURCE_MAX,
            "reason: window_cut {}",
            w.resource
        );
        // resuming binds a file entry's name and approved hash
        let r = resume_ask(std::slice::from_ref(&t), a.by.clone());
        let mut renamed = t.clone();
        renamed.file = "other.txt".into();
        assert_ne!(
            resume_ask(&[renamed], a.by.clone()).params,
            r.params,
            "reason: resume_not_bound_to_file"
        );
        // lines that differ from the approved copy
        assert_eq!(diff_lines("", "a\nb\n"), (2, 0));
        assert_eq!(diff_lines("a\nb\n", "a\nb\n"), (0, 0));
        assert_eq!(diff_lines("a\nb\nb\n", "a\nb\nc\n"), (1, 1));
        let d = tempfile::tempdir().unwrap();
        assert_eq!(approved_copy(d.path(), "drive.txt"), "");
        keep_approved(d.path(), "drive.txt", body).unwrap();
        assert_eq!(
            approved_copy(d.path(), "drive.txt"),
            body,
            "reason: approved_copy_not_kept"
        );
        assert!(keep_approved(d.path(), "../x.txt", body).is_err());
        // the page sees the change, and a file that cannot be read
        let dir = d.path().join(TICKS_DIR);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("drive.txt"), body).unwrap();
        let v = shown(std::slice::from_ref(&t), Some(&dir), &HashMap::new(), &c);
        assert_eq!(
            (&v[0]["file"], &v[0]["changed_since_approved"]),
            (&json!("drive.txt"), &json!(false))
        );
        assert_eq!(v[0]["signed"], json!(false), "reason: listed_as_signed");
        std::fs::write(dir.join("drive.txt"), "改了\n").unwrap();
        let v = shown(std::slice::from_ref(&t), Some(&dir), &HashMap::new(), &c);
        assert_eq!(
            v[0]["changed_since_approved"],
            json!(true),
            "reason: file_change_not_listed"
        );
        std::fs::remove_file(dir.join("drive.txt")).unwrap();
        let v = shown(std::slice::from_ref(&t), Some(&dir), &HashMap::new(), &c);
        assert_eq!(v[0]["file_error"], json!("file_missing"));
        assert_eq!(v[0]["text_sha12"], Value::Null);
    }

    #[test]
    fn three_rounds_in_a_row_that_do_not_go_in_are_told_and_then_every_three() {
        let mut s = Seen::default();
        let skip = Outcome::Skipped(CHANGED);
        assert_eq!(after(&mut s, &skip, 20, 0, 1200), None);
        assert_eq!(after(&mut s, &skip, 20, 1200, 2400), None);
        assert_eq!(
            after(&mut s, &skip, 20, 2400, 3600),
            Some((3, CHANGED)),
            "reason: stuck_not_told"
        );
        assert_eq!((s.stuck, s.round, s.last.as_str()), (3, 3, CHANGED));
        assert_eq!((s.last_at, s.base), (Some(3600), Some(3600)));
        assert_eq!(
            after(&mut s, &skip, 20, 3600, 4800),
            None,
            "reason: told_every_round"
        );
        assert_eq!(after(&mut s, &skip, 20, 4800, 6000), None);
        assert_eq!(after(&mut s, &skip, 20, 6000, 7200), Some((6, CHANGED)));
        // one that goes in starts the count again
        assert_eq!(after(&mut s, &Outcome::Sent, 20, 7200, 8400), None);
        assert_eq!(
            (s.stuck, s.late, s.round, s.last.as_str()),
            (0, 0, 7, "sent"),
            "reason: count_kept_after_sent"
        );
        assert_eq!(after(&mut s, &skip, 20, 8400, 9600), None);
        // a seat that stays in a turn: the sentence waits, and three whole periods late is told once
        let mut s = Seen::default();
        let p = 20 * 60;
        assert_eq!(after(&mut s, &Outcome::Later, 20, 0, p), None);
        assert_eq!(after(&mut s, &Outcome::Later, 20, 0, 3 * p), None);
        assert_eq!(
            after(&mut s, &Outcome::Later, 20, 0, 4 * p),
            Some((3, "seat_in_turn")),
            "reason: seat_in_turn_not_told"
        );
        assert_eq!(
            after(&mut s, &Outcome::Later, 20, 0, 4 * p + 60),
            None,
            "reason: told_every_pass"
        );
        assert_eq!(s.round, 0, "reason: waiting_counted_as_a_round");
        assert_eq!(
            after(&mut s, &Outcome::Later, 20, 0, 7 * p),
            Some((6, "seat_in_turn"))
        );
        // the page reads the rounds and when the next is due
        let t = tick("seat_a", 20, "x");
        let seen = HashMap::from([(
            "seat_a".to_owned(),
            Seen {
                last_at: Some(3600),
                last: CHANGED.into(),
                stuck: 3,
                late: 0,
                round: 3,
                base: Some(3600),
            },
        )]);
        let no = |_: &[u8], _: &[u8]| false;
        let c = Check {
            now: 0,
            verify: &no,
            revoked: &[],
        };
        let v = shown(std::slice::from_ref(&t), None, &seen, &c);
        assert_eq!(
            (&v[0]["stuck"], &v[0]["last_at"], &v[0]["last"]),
            (&json!(3), &json!(3600), &json!(CHANGED))
        );
        assert_eq!(v[0]["next_at"], json!(3600 + p), "reason: next_round_wrong");
        let v = shown(&[t], None, &HashMap::new(), &c);
        assert_eq!(
            (&v[0]["last_at"], &v[0]["next_at"]),
            (&Value::Null, &Value::Null)
        );
    }
}
