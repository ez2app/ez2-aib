//! Which seats are trusted now (DESIGN-trusted-g1g4 §0, 5d33a PREREAD-trusted-g1g4 Q1).
//!
//! `<aibd data>/trusted/seats.json`, beside the pinned machines ([`crate::trusted_hosts`]):
//! `{"seats": {"<seat_id>": <unix seconds it ends>}}`. Read whole at every ask, never kept: a
//! trust that has ended, or a file that is gone or bad, is no trust.
//!
//! This slice only reads. The one way in is T1, the owner's fingerprint, which writes the
//! file (its own slice, after Andy's a66); no act of a seat or a page writes it. The file is
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
}
