//! S79 A3 dm_done (519d0 11-15 07:56, b433b): a dm closed (`read --done`) wakes observe.
//!
//! The 1.x dm queue is frozen Python (Andy 11-13) and the Rust store (aib-dmq-store) must
//! not depend on aib-drive, so nobody calls `aib-drive event dm_done`. Instead aib-drive
//! looks itself: both writers append one line per transition to
//! `<state root>/state/dm-queue/<seat>/audit.jsonl`, and a close is the line with
//! `"to": "done"` and `"ok": true` (power_tasks.py reads the same, aib-dmq-store pyjson.rs).
//!
//! * [`Watch::scan`] reads only what was appended since the last look, per seat, from the
//!   byte offsets kept in `<state root>/drive/dm_done.offsets`. A line still being written
//!   (no newline yet) waits for the next look.
//! * A seat seen for the first time starts at its current end: closes from before observe
//!   ran here are not events. A file that got shorter (rotated, rewritten) starts again at
//!   its new end, also without events.
//! * [`Watch::wake`] writes the newest close as one `dm_done` event (`dm/<seat>/<id>`,
//!   by [`BY`]) through [`crate::wake::write`]; several closes in the same second fold into
//!   one round, as every event does (IFACE r1 4). `by` is not `aib-drive`, so act does
//!   not skip it as its own.
//! * Failing to read or keep offsets never stops observe: the scan is skipped and the
//!   10-minute round still runs.
//!
//! RealClock calls it once a second while it waits (observe.rs).

use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// `by` of the events written here.
pub const BY: &str = "aib-drive-dmq";
/// The offsets file inside `<state root>/drive/`.
pub const OFFSETS_FILE: &str = "dm_done.offsets";
/// The per-seat transition log both dm queue writers append to.
pub const AUDIT_FILE: &str = "audit.jsonl";
/// Most bytes read from one seat in one look; the rest waits for the next look.
pub const READ_MAX: u64 = 1 << 20;

/// One close: the seat and the dm id.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Closed {
    pub seat: String,
    pub id: String,
}

impl Closed {
    /// The event ref (`wake::check` takes `dm/…`).
    pub fn reference(&self) -> String {
        format!("dm/{}/{}", self.seat, self.id)
    }
}

fn seat_code(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn dm_id(s: &str) -> bool {
    s.len() == 18
        && s.starts_with("q_")
        && s[2..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The close a line records, if it records one.
pub fn closed_in(seat: &str, line: &str) -> Option<Closed> {
    let v: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    let done = v.get("to").and_then(serde_json::Value::as_str) == Some("done")
        && v.get("ok").and_then(serde_json::Value::as_bool) == Some(true);
    let id = v.get("id").and_then(serde_json::Value::as_str)?;
    (done && dm_id(id)).then(|| Closed {
        seat: seat.to_owned(),
        id: id.to_owned(),
    })
}

/// Where the next look at one file starts, and the closes in `bytes` read from `offset`:
/// only whole lines count, a cut last line is read again next time.
pub fn closes_in(seat: &str, offset: u64, bytes: &[u8]) -> (u64, Vec<Closed>) {
    let whole = bytes.iter().rposition(|&b| b == b'\n').map_or(0, |i| i + 1);
    let out = String::from_utf8_lossy(&bytes[..whole])
        .lines()
        .filter_map(|l| closed_in(seat, l))
        .collect();
    (offset + whole as u64, out)
}

/// The dm queue folders and the offsets kept between looks.
#[derive(Debug)]
pub struct Watch {
    queues: PathBuf,
    drive: PathBuf,
    offsets: BTreeMap<String, u64>,
}

impl Watch {
    /// `root` is the state root (observe::state_root). Kept offsets are read once here;
    /// an unreadable file counts as none (every seat starts at its end).
    pub fn new(root: &Path) -> Self {
        let drive = root.join("drive");
        let offsets = std::fs::read_to_string(drive.join(OFFSETS_FILE))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Self {
            queues: root.join("state").join("dm-queue"),
            drive,
            offsets,
        }
    }

    /// The offset kept for a seat (tests).
    pub fn offset(&self, seat: &str) -> Option<u64> {
        self.offsets.get(seat).copied()
    }

    /// The closes appended since the last look, oldest first. Keeps the new offsets when
    /// they moved; a failure to keep them is ignored (the next look reads again).
    pub fn scan(&mut self) -> Vec<Closed> {
        let Ok(rd) = std::fs::read_dir(&self.queues) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut moved = false;
        let mut seats: Vec<String> = rd
            .filter_map(Result::ok)
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|s| seat_code(s))
            .collect();
        seats.sort();
        for seat in seats {
            let path = self.queues.join(&seat).join(AUDIT_FILE);
            let Ok(len) = std::fs::metadata(&path).map(|m| m.len()) else {
                continue;
            };
            let start = match self.offsets.get(&seat) {
                Some(&o) if o <= len => o,
                // first sight, or the file got shorter: from its end, no events
                _ => {
                    self.offsets.insert(seat, len);
                    moved = true;
                    continue;
                }
            };
            if start == len {
                continue;
            }
            let Some(bytes) = read_from(&path, start, (len - start).min(READ_MAX)) else {
                continue;
            };
            let (next, closes) = closes_in(&seat, start, &bytes);
            if next != start {
                self.offsets.insert(seat, next);
                moved = true;
            }
            out.extend(closes);
        }
        if moved {
            let _ = self.keep();
        }
        out
    }

    fn keep(&self) -> std::io::Result<()> {
        if !self.drive.is_dir() {
            return Ok(());
        }
        let text = serde_json::to_string(&self.offsets).map_err(std::io::Error::other)?;
        let tmp = self
            .drive
            .join(format!("{OFFSETS_FILE}.tmp-{}", std::process::id()));
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, self.drive.join(OFFSETS_FILE)).inspect_err(|_| {
            let _ = std::fs::remove_file(&tmp);
        })
    }

    /// Scans and, when something was closed, writes the newest close as a dm_done event.
    /// Returns what was written (None: nothing closed, no drive folder, or a write error).
    pub fn wake(&mut self, at: u64) -> Option<crate::wake::Event> {
        let last = self.scan().pop()?;
        let r = last.reference();
        crate::wake::check("dm_done", Some(&r)).ok()?;
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let ev = crate::wake::make("dm_done", Some(&r), BY, at, std::process::id(), nanos);
        match crate::wake::write(&self.drive, &ev) {
            Ok(crate::wake::Wrote::Written(_)) => Some(ev),
            _ => None,
        }
    }
}

fn read_from(path: &Path, start: u64, n: u64) -> Option<Vec<u8>> {
    let mut f = std::fs::File::open(path).ok()?;
    f.seek(SeekFrom::Start(start)).ok()?;
    let mut buf = Vec::new();
    f.take(n).read_to_end(&mut buf).ok()?;
    Some(buf)
}
