//! S79 n-4a wake (IFACE-s79-drive §4 + r1 4, 7b81e): `aib-drive event <kind> [--ref X]`.
//!
//! Something that may give an idle seat work just happened (a merge, a receipt, a review,
//! a dm closed, a seat with empty hands, the 10-minute tick): write one line into
//! `<state root>/drive/event` so `observe --watch` starts its next round now instead of at
//! its interval. An event only wakes; it carries no judgement (the round decides).
//!
//! * The line is JSON `{"id","kind","ref","at","by"}`, written to a temp file in the same
//!   folder and renamed over `event` (Windows: std's rename replaces). A reader sees the
//!   old line or the new one, never half of one.
//! * One file, the latest line: events within the same second fold into one round (observe
//!   looks once a second); an id written over in between loses only its report, not its
//!   round (r1 4, accepted; the tick catches up).
//! * No `drive/` folder = observe never ran here: nothing is written and nothing is made
//!   (exit 0, said on stdout). A caller (merge watch, receipt-run, a dm close) never fails
//!   its own work because of this: every failure is one line and exit 5.
//! * `by` (`--by`, else `AIB_DRIVE_BY`, else `cli`) names the writer; a consumer skips
//!   events it wrote itself ([`is_own`]), so act's own dms cannot wake act in a loop.
//! * [`report_event_of`] reads the file for observe: the report id is `ref` when it is a
//!   completion event (`pr/<n>@<40>`, `dm/…`), and a bare id as written before this slice
//!   still counts; observe::read_event calls it.
//!
//! Exit codes (IFACE §0): 0 written or nothing to wake · 2 usage · 5 could not write.
//!
//! Integration (S79, a9136): routed from main.rs as `aib-drive event`; observe reads the
//! event file through [`report_event_of`].

use std::io::Write;
use std::path::{Path, PathBuf};

use crate::report::is_event;
use serde::{Deserialize, Serialize};

/// The event file inside `<state root>/drive/` (observe::Paths::event).
pub const EVENT_FILE: &str = "event";
/// The kinds `event` takes (IFACE §4).
pub const KINDS: [&str; 6] = ["merge", "receipt", "review", "dm_done", "seat_free", "tick"];
/// Longest `--ref` / `--by` (report::EVENT_MAX).
pub const REF_MAX: usize = 120;
/// `by` when neither `--by` nor `AIB_DRIVE_BY` names the writer.
pub const BY_DEFAULT: &str = "cli";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Event {
    pub id: String,
    pub kind: String,
    #[serde(rename = "ref")]
    pub reference: Option<String>,
    /// Unix seconds.
    pub at: u64,
    pub by: String,
}

/// What [`write`] did.
#[derive(Debug, PartialEq, Eq)]
pub enum Wrote {
    Written(PathBuf),
    /// No `drive/` folder: observe never ran under this root, nobody to wake.
    NoDriveDir,
}

/// One line of text: printable, no whitespace but the plain space, at most REF_MAX chars.
fn text_ok(s: &str) -> bool {
    !s.is_empty()
        && s.chars().count() <= REF_MAX
        && s.chars()
            .all(|c| !c.is_control() && (c == ' ' || !c.is_whitespace()))
}

fn seat_code(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The kind is one of KINDS and its ref has that kind's shape:
/// merge `pr/<n>@<40 hex>`, dm_done `dm/…`, seat_free a seat code, tick none,
/// receipt / review one line of text.
pub fn check(kind: &str, reference: Option<&str>) -> Result<(), String> {
    if !KINDS.contains(&kind) {
        return Err(format!(
            "unknown kind {kind:?} (one of {})",
            KINDS.join(" ")
        ));
    }
    let Some(r) = reference else {
        return Ok(());
    };
    let ok = match kind {
        "merge" => r.starts_with("pr/") && is_event(r),
        "dm_done" => r.starts_with("dm/") && is_event(r),
        "seat_free" => seat_code(r),
        "tick" => false,
        _ => text_ok(r),
    };
    if ok {
        Ok(())
    } else {
        Err(format!("--ref {r:?} is not a {kind} ref"))
    }
}

/// The event to write; `id` is unique per writer and moment.
pub fn make(kind: &str, reference: Option<&str>, by: &str, at: u64, pid: u32, nanos: u32) -> Event {
    Event {
        id: format!("ev-{at}-{pid}-{nanos:09}"),
        kind: kind.to_owned(),
        reference: reference.map(str::to_owned),
        at,
        by: by.to_owned(),
    }
}

/// Writes `ev` as the one line of `<drive>/event`: temp file in the same folder, then
/// rename. `drive` is `<state root>/drive`; it is never created here.
pub fn write(drive: &Path, ev: &Event) -> std::io::Result<Wrote> {
    if !drive.is_dir() {
        return Ok(Wrote::NoDriveDir);
    }
    let line = serde_json::to_string(ev).map_err(std::io::Error::other)? + "\n";
    let tmp = drive.join(format!("{EVENT_FILE}.tmp-{}", ev.id));
    let target = drive.join(EVENT_FILE);
    let put = || -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        f.write_all(line.as_bytes())?;
        f.sync_all()?;
        drop(f);
        std::fs::rename(&tmp, &target)
    };
    match put() {
        Ok(()) => Ok(Wrote::Written(target)),
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}

/// The event line in `content`, if it is one.
pub fn parse(content: &str) -> Option<Event> {
    serde_json::from_str(content.trim()).ok()
}

/// The completion event observe answers for this file content: the ref of an event line
/// when it is one (report::is_event), or a bare id written the old way; None = a plain
/// wake-up.
pub fn report_event_of(content: &str) -> Option<String> {
    match parse(content) {
        Some(ev) => ev.reference.filter(|r| is_event(r)),
        None => {
            let id = content.trim();
            is_event(id).then(|| id.to_owned())
        }
    }
}

/// An event this consumer wrote itself (`by` equal to its own name): not a reason to wake.
pub fn is_own(ev: &Event, me: &str) -> bool {
    ev.by == me
}

const USAGE: &str =
    "usage: aib-drive event <merge|receipt|review|dm_done|seat_free|tick> [--ref X] [--by NAME]";

/// What the command prints and exits with.
#[derive(Debug, PartialEq, Eq)]
pub struct Out {
    pub code: u8,
    pub stdout: String,
    pub stderr: String,
}

fn out(code: u8, stdout: String, stderr: String) -> Out {
    Out {
        code,
        stdout,
        stderr,
    }
}

/// `aib-drive event …` (args after `event`). `root` is the state root (observe::state_root),
/// `by_env` the value of AIB_DRIVE_BY, `now` (unix seconds, nanos) and `pid` the moment
/// and the writer.
pub fn cli_event(
    args: &[String],
    root: Result<PathBuf, String>,
    by_env: Option<String>,
    now: (u64, u32),
    pid: u32,
) -> Out {
    let usage = |why: String| out(2, String::new(), format!("{why}\n{USAGE}\n"));
    let mut it = args.iter();
    let Some(kind) = it.next().filter(|k| !k.starts_with('-')) else {
        return usage("no kind".to_owned());
    };
    let (mut reference, mut by) = (None, None);
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--ref" => &mut reference,
            "--by" => &mut by,
            _ => return usage(format!("unknown argument {a:?}")),
        };
        match it.next() {
            Some(v) if slot.is_none() => *slot = Some(v.clone()),
            Some(_) => return usage(format!("{a} given twice")),
            None => return usage(format!("{a} needs a value")),
        }
    }
    if let Err(e) = check(kind, reference.as_deref()) {
        return usage(e);
    }
    let by = by
        .or(by_env.filter(|b| !b.is_empty()))
        .unwrap_or_else(|| BY_DEFAULT.to_owned());
    if !text_ok(&by) {
        return usage(format!("--by {by:?} is not one line of text"));
    }
    let root = match root {
        Ok(r) => r,
        Err(e) => return out(5, String::new(), format!("aib-drive event: {e}\n")),
    };
    let ev = make(kind, reference.as_deref(), &by, now.0, pid, now.1);
    match write(&root.join("drive"), &ev) {
        Ok(Wrote::Written(p)) => out(
            0,
            format!("event {kind} {} written to {}\n", ev.id, p.display()),
            String::new(),
        ),
        Ok(Wrote::NoDriveDir) => out(
            0,
            format!(
                "event {kind}: no drive folder under {} (observe never ran here), not written\n",
                root.display()
            ),
            String::new(),
        ),
        Err(e) => out(
            5,
            String::new(),
            format!("aib-drive event: could not write: {e}\n"),
        ),
    }
}
