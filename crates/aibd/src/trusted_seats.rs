//! Which seats are trusted now (DESIGN-trusted-g1g4 §0, 5d33a PREREAD-trusted-g1g4 Q1).
//!
//! `<aibd data>/trusted/seats.json`, beside the pinned machines ([`crate::trusted_hosts`]):
//! `{"seats": {"<seat_id>": <unix seconds it ends>}}`. Read whole at every ask, never kept: a
//! trust that has ended, or a file that is gone or bad, is no trust.
//!
//! The one way in is T1, the owner's fingerprint ([`crate::seat_trust`]): only it calls
//! [`put`]; a page may take a trust back ([`take`]) and no act of a seat writes. The file is
//! not a defence against the same account (GUIDE-same-user-ai-threat-model): a sandboxed seat
//! cannot reach aibd's data folder, and for anything else the fingerprint is the defence.
use std::path::Path;

pub const FILE: &str = "seats.json";
/// The largest file read.
const MAX_FILE: u64 = 64 * 1024;
/// A trust never runs longer than this from now (one day): a longer one is not one T1 wrote.
pub const MAX_AHEAD_S: u64 = 24 * 3600;

/// The end of `seat_id`'s trust in the file's bytes, when it names one.
pub fn until_in(bytes: &[u8], seat_id: &str) -> Option<u64> {
    let v: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    v.get("seats")?.as_object()?.get(seat_id)?.as_u64()
}

/// The end of `seat_id`'s trust on this machine now: a regular file (not a link) of at most
/// [`MAX_FILE`] bytes, else none.
pub fn until(data_dir: &Path, seat_id: &str) -> Option<u64> {
    let p = data_dir.join(crate::trusted_hosts::DIR).join(FILE);
    let m = std::fs::symlink_metadata(&p).ok()?;
    if !m.is_file() || m.len() > MAX_FILE {
        return None;
    }
    until_in(&std::fs::read(&p).ok()?, seat_id)
}

/// Whether a trust ending at `until` holds at `now`: it has not ended, and it does not end
/// more than [`MAX_AHEAD_S`] from now.
pub fn holds(until: Option<u64>, now: u64) -> bool {
    until.is_some_and(|u| u > now && u - now <= MAX_AHEAD_S)
}

/// How long a trust the owner gives lasts, from the fingerprint (5d33a PREREAD-trust-t1 1:
/// aibd has no local time zone to end it at the day's end).
pub const HOURS: u64 = 12;

/// The refusal when the file or its folder cannot be written as aibd's own.
pub const STORE: &str = "trust_store";

static WRITE: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// The trusts in the file's bytes that have not ended at `now` (a bad file holds none).
pub fn live_in(bytes: &[u8], now: u64) -> std::collections::BTreeMap<String, u64> {
    serde_json::from_slice::<serde_json::Value>(bytes)
        .ok()
        .and_then(|v| v.get("seats")?.as_object().cloned())
        .map(|o| {
            o.into_iter()
                .filter_map(|(k, v)| Some((k, v.as_u64()?)))
                .filter(|(_, u)| *u > now)
                .collect()
        })
        .unwrap_or_default()
}

/// Rewrite the file from what it holds now, changed by `f`: the folder and the file are aibd's
/// own (never through a link), ended trusts are dropped, the new file replaces the old whole.
fn rewrite(
    data_dir: &Path,
    now: u64,
    f: impl FnOnce(&mut std::collections::BTreeMap<String, u64>) -> bool,
) -> Result<bool, &'static str> {
    let _one = WRITE
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = data_dir.join(crate::trusted_hosts::DIR);
    let is_link = |p: &Path| std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink());
    let file = dir.join(FILE);
    if is_link(&dir) || is_link(&file) {
        return Err(STORE);
    }
    crate::platform::create_private_dir(&dir).map_err(|_| STORE)?;
    let mut live = match std::fs::symlink_metadata(&file) {
        Ok(m) if m.is_file() && m.len() <= MAX_FILE => {
            live_in(&std::fs::read(&file).map_err(|_| STORE)?, now)
        }
        Ok(_) => return Err(STORE),
        Err(_) => std::collections::BTreeMap::new(),
    };
    let changed = f(&mut live);
    let body = serde_json::to_vec(&serde_json::json!({ "seats": live })).map_err(|_| STORE)?;
    crate::platform::write_private_file(&file, &body).map_err(|_| STORE)?;
    Ok(changed)
}

/// T1 only: `seat_id` is trusted until `until`.
pub fn put(data_dir: &Path, seat_id: &str, until: u64, now: u64) -> Result<(), &'static str> {
    rewrite(data_dir, now, |m| {
        m.insert(seat_id.to_owned(), until);
        true
    })
    .map(|_| ())
}

/// A trust taken back; whether there was one.
pub fn take(data_dir: &Path, seat_id: &str, now: u64) -> Result<bool, &'static str> {
    rewrite(data_dir, now, |m| m.remove(seat_id).is_some())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_trust_holds_until_its_end_and_never_past_a_day() {
        let now = 1_000_000;
        assert!(holds(Some(now + 1), now));
        assert!(holds(Some(now + MAX_AHEAD_S), now));
        for u in [None, Some(0), Some(now - 1), Some(now)] {
            assert!(!holds(u, now), "reason: ended_trust_holds_{u:?}");
        }
        assert!(
            !holds(Some(now + MAX_AHEAD_S + 1), now),
            "reason: trust_longer_than_a_day"
        );
        assert!(!holds(Some(u64::MAX), now), "reason: endless_trust");
    }

    #[test]
    fn the_file_names_each_seats_end_and_a_bad_file_names_none() {
        let b = br#"{"seats": {"seat_a": 2000, "seat_b": "2000", "seat_c": -1, "seat_d": 1.5}}"#;
        assert_eq!(until_in(b, "seat_a"), Some(2000));
        for s in ["seat_b", "seat_c", "seat_d", "seat_z", "seats"] {
            assert_eq!(until_in(b, s), None, "reason: read_a_trust_for_{s}");
        }
        let bads: [&[u8]; 5] = [
            b"",
            b"[]",
            b"{}",
            br#"{"seats": [["seat_a", 2000]]}"#,
            b"{\"seats\": {\"seat_a\": 2000",
        ];
        for bad in bads {
            assert_eq!(until_in(bad, "seat_a"), None, "reason: bad_file_trusted");
        }
    }

    #[test]
    fn the_file_is_read_at_each_ask_and_only_as_a_regular_file() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(until(d.path(), "seat_a"), None, "reason: no_file_trusted");
        let dir = d.path().join(crate::trusted_hosts::DIR);
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join(FILE);
        std::fs::write(&f, br#"{"seats": {"seat_a": 2000}}"#).unwrap();
        assert_eq!(until(d.path(), "seat_a"), Some(2000));
        // taken back: the next ask sees it
        std::fs::write(&f, br#"{"seats": {}}"#).unwrap();
        assert_eq!(until(d.path(), "seat_a"), None, "reason: kept_an_old_trust");
        // too large
        let mut big = br#"{"seats": {"seat_a": 2000}, "pad": ""#.to_vec();
        big.extend(vec![b'x'; MAX_FILE as usize]);
        big.extend(b"\"}");
        std::fs::write(&f, &big).unwrap();
        assert_eq!(until(d.path(), "seat_a"), None, "reason: read_a_huge_file");
        #[cfg(unix)]
        {
            let real = d.path().join("elsewhere.json");
            std::fs::write(&real, br#"{"seats": {"seat_a": 2000}}"#).unwrap();
            std::fs::remove_file(&f).unwrap();
            std::os::unix::fs::symlink(&real, &f).unwrap();
            assert_eq!(until(d.path(), "seat_a"), None, "reason: followed_a_link");
        }
    }

    #[test]
    fn a_trust_is_written_whole_and_taken_back() {
        let d = tempfile::tempdir().unwrap();
        let now = 1_000;
        put(d.path(), "seat_a", now + 60, now).unwrap();
        put(d.path(), "seat_b", now + 5, now).unwrap();
        assert_eq!(until(d.path(), "seat_a"), Some(now + 60));
        assert_eq!(until(d.path(), "seat_b"), Some(now + 5));
        // a later write drops the one that has ended and keeps the other
        put(d.path(), "seat_c", now + 90, now + 10).unwrap();
        assert_eq!(until(d.path(), "seat_b"), None, "reason: ended_trust_kept");
        assert_eq!(
            until(d.path(), "seat_a"),
            Some(now + 60),
            "reason: live_trust_lost"
        );
        assert_eq!(take(d.path(), "seat_a", now + 10), Ok(true));
        assert_eq!(until(d.path(), "seat_a"), None, "reason: taken_trust_kept");
        assert_eq!(
            until(d.path(), "seat_c"),
            Some(now + 90),
            "reason: other_trust_lost"
        );
        assert_eq!(take(d.path(), "seat_a", now + 10), Ok(false));
        // nothing left half written
        let dir = d.path().join(crate::trusted_hosts::DIR);
        let names: Vec<String> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, [FILE], "reason: stray_file {names:?}");
        // a bad file holds no one and is replaced
        std::fs::write(dir.join(FILE), b"not json").unwrap();
        put(d.path(), "seat_d", now + 60, now).unwrap();
        assert_eq!(until(d.path(), "seat_d"), Some(now + 60));
        assert_eq!(until(d.path(), "seat_c"), None, "reason: bad_file_trusted");
    }

    #[cfg(unix)]
    #[test]
    fn a_trust_is_never_written_through_a_link() {
        let d = tempfile::tempdir().unwrap();
        let other = tempfile::tempdir().unwrap();
        let dir = d.path().join(crate::trusted_hosts::DIR);
        // the file is a link
        std::fs::create_dir_all(&dir).unwrap();
        let aim = other.path().join("aim.json");
        std::fs::write(&aim, b"{}").unwrap();
        std::os::unix::fs::symlink(&aim, dir.join(FILE)).unwrap();
        assert_eq!(
            put(d.path(), "seat_a", 2_000, 1_000),
            Err(STORE),
            "reason: wrote_through_a_file_link"
        );
        assert_eq!(
            std::fs::read(&aim).unwrap(),
            b"{}",
            "reason: link_target_changed"
        );
        // the folder is a link
        std::fs::remove_file(dir.join(FILE)).unwrap();
        std::fs::remove_dir(&dir).unwrap();
        std::os::unix::fs::symlink(other.path(), &dir).unwrap();
        assert_eq!(
            put(d.path(), "seat_a", 2_000, 1_000),
            Err(STORE),
            "reason: wrote_through_a_folder_link"
        );
        assert!(
            !other.path().join(FILE).exists(),
            "reason: file_made_behind_the_link"
        );
    }

    /// 5d33a PREREAD-ghview 1 / PREREAD-trust-t1: in aibd's own code only the trust entry gives
    /// a trust, and only a page's act takes one back.
    #[test]
    fn only_the_trust_entry_writes_the_file() {
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut callers = Vec::new();
        let mut stack = vec![src];
        while let Some(d) = stack.pop() {
            for e in std::fs::read_dir(&d).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    stack.push(p);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    let t = std::fs::read_to_string(&p).unwrap();
                    let name = p.file_name().unwrap().to_string_lossy().into_owned();
                    if name == "trusted_seats.rs" {
                        continue; // this file: the functions themselves and this test's words
                    }
                    for call in [
                        "trusted_seats::put(",
                        "trusted_seats::take(",
                        "trusted_seats::rewrite(",
                    ] {
                        if t.contains(call) {
                            callers.push(format!("{name} {call}"));
                        }
                    }
                }
            }
        }
        callers.sort();
        assert_eq!(
            callers,
            [
                "seat_trust.rs trusted_seats::put(",
                "seat_trust.rs trusted_seats::take("
            ],
            "reason: another_writer"
        );
    }
}
