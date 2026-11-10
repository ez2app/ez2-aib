// SPDX-License-Identifier: Apache-2.0
//! `backup check` as decisions (4.4.1 §10, §12): is what lies off-site what a restore
//! would need? The platform backup writes one folder per UTC day, `<remote>/<YYYY-MM-DD>/`,
//! holding one encrypted container dump per CT and one encrypted host configuration
//! bundle. This module is handed the listing of those folders and says, per rule, green /
//! warn / red / unknown. No I/O; the caller lists the remote.
//!
//! * `B01` the newest folder is recent enough (a backup within the last 26 hours).
//! * `B02` every CT that must be backed up has a dump in it — a site added last week and
//!   never included is the failure this exists for.
//! * `B03` the host configuration bundle is there.
//! * `B04` **nothing unencrypted** was uploaded: every object ends in `.age`.
//! * `B05` no dump is implausibly small, and none shrank to less than half of the day
//!   before (a dump of an emptied or half-written volume uploads just as happily).
//!
//! What this cannot say: that a dump decrypts and restores. That is the restore drill,
//! done where the private key is — the host has none, on purpose.

use alloc::string::String;
use alloc::vec::Vec;

use crate::verify::{CheckResult, Outcome};

/// Smallest size, in bytes, a container dump is believed at: a CT with an operating
/// system in it does not compress below this.
pub const DUMP_MIN_BYTES: u64 = 50 * 1024 * 1024;

/// One object in a day folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Object {
    /// File name, no path.
    pub name: String,
    /// Size in bytes.
    pub size: u64,
}

/// What an object name says it is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// `vzdump-lxc-<ct>-<date>-<time>.tar.zst.age`
    Dump(u32),
    /// `host-config-<stamp>.tar.gz.age`
    HostConfig,
    /// Ends in `.age` but is neither of the above.
    OtherEncrypted,
    /// Does not end in `.age`.
    Plain,
}

/// Classify an object by its name.
pub fn kind(name: &str) -> Kind {
    let Some(inner) = name.strip_suffix(".age") else {
        return Kind::Plain;
    };
    if let Some(rest) = inner
        .strip_prefix("vzdump-lxc-")
        .and_then(|r| r.strip_suffix(".tar.zst"))
    {
        // <ct>-YYYY_MM_DD-HH_MM_SS
        let mut parts = rest.splitn(2, '-');
        let ct = parts.next().and_then(|c| c.parse::<u32>().ok());
        let stamp_ok = parts.next().is_some_and(|s| {
            s.len() == 19
                && s.bytes().enumerate().all(|(i, c)| match i {
                    4 | 7 | 13 | 16 => c == b'_',
                    10 => c == b'-',
                    _ => c.is_ascii_digit(),
                })
        });
        if let (Some(ct), true) = (ct, stamp_ok) {
            return Kind::Dump(ct);
        }
    }
    if inner.starts_with("host-config-") && inner.ends_with(".tar.gz") {
        return Kind::HostConfig;
    }
    Kind::OtherEncrypted
}

/// Days since 1970-01-01 for a `YYYY-MM-DD` date (proleptic Gregorian); `None` when the
/// text is not a date.
pub fn day_number(date: &str) -> Option<i64> {
    let b = date.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let num = |r: core::ops::Range<usize>| date.get(r)?.parse::<i64>().ok();
    let (y, m, d) = (num(0..4)?, num(5..7)?, num(8..10)?);
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let month_days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if !(1..=12).contains(&m) {
        return None;
    }
    if !(1..=month_days[usize::try_from(m - 1).ok()?]).contains(&d) {
        return None;
    }
    // Howard Hinnant's days_from_civil
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146_097 + doe - 719_468)
}

fn result(id: &'static str, outcome: Outcome) -> CheckResult {
    CheckResult { id, outcome }
}

/// Judge the off-site backup. `now` is seconds since the epoch; `days` are the folder
/// names found at the remote (`None`: the listing failed); `newest` and `previous` are
/// the objects of the newest folder and of the one before it (`None`: could not be
/// listed; an empty previous list just means there is nothing to compare with);
/// `must_have` are the CT ids that have to be in a backup.
pub fn check(
    now: u64,
    days: Option<&[String]>,
    newest: Option<&[Object]>,
    previous: Option<&[Object]>,
    must_have: &[u32],
) -> Vec<CheckResult> {
    let unknown = |code| {
        ["B01", "B02", "B03", "B04", "B05"]
            .into_iter()
            .map(|id| result(id, Outcome::Unknown(code)))
            .collect::<Vec<_>>()
    };
    let Some(days) = days else {
        return unknown("unlisted");
    };
    // B01: the newest folder that is a date; folders that are not dates are ignored here
    let newest_day = days.iter().filter_map(|d| day_number(d)).max();
    let today = i64::try_from(now / 86_400).unwrap_or(i64::MAX);
    let earliest_ok = i64::try_from(now.saturating_sub(26 * 3600) / 86_400).unwrap_or(i64::MAX);
    let b01 = match newest_day {
        None => Outcome::Red("no_backup"),
        Some(d) if d > today => Outcome::Unknown("clock"),
        Some(d) if d < earliest_ok => Outcome::Red("stale"),
        Some(_) => Outcome::Green,
    };
    let mut out = alloc::vec![result("B01", b01)];
    let Some(newest) = newest else {
        out.extend(unknown("unlisted").into_iter().skip(1));
        return out;
    };

    let dump_size = |objects: &[Object], ct: u32| {
        objects
            .iter()
            .filter(|o| kind(&o.name) == Kind::Dump(ct))
            .map(|o| o.size)
            .max()
    };
    // B02
    let missing = must_have.iter().any(|ct| dump_size(newest, *ct).is_none());
    out.push(result(
        "B02",
        if must_have.is_empty() {
            Outcome::Unknown("no_ct_list")
        } else if missing {
            Outcome::Red("ct_not_backed_up")
        } else {
            Outcome::Green
        },
    ));
    // B03
    let config = newest
        .iter()
        .any(|o| kind(&o.name) == Kind::HostConfig && o.size > 0);
    out.push(result(
        "B03",
        if config {
            Outcome::Green
        } else {
            Outcome::Red("no_host_config")
        },
    ));
    // B04
    let plain = newest.iter().any(|o| kind(&o.name) == Kind::Plain);
    out.push(result(
        "B04",
        if plain {
            Outcome::Red("plaintext_offsite")
        } else {
            Outcome::Green
        },
    ));
    // B05
    let mut b05 = Outcome::Green;
    for ct in must_have {
        let Some(size) = dump_size(newest, *ct) else {
            continue; // B02 already says so
        };
        if size < DUMP_MIN_BYTES {
            b05 = Outcome::Red("dump_too_small");
            break;
        }
        match previous.map(|p| dump_size(p, *ct)) {
            None => {
                if b05 == Outcome::Green {
                    b05 = Outcome::Unknown("previous_unlisted");
                }
            }
            Some(Some(before)) if size < before / 2 => b05 = Outcome::Warn("dump_shrank"),
            Some(_) => {}
        }
    }
    out.push(result("B05", b05));
    out
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::borrow::ToOwned;

    const MB: u64 = 1024 * 1024;
    /// 2026-11-10 12:00:00 UTC
    const NOW: u64 = 1_794_312_000;

    fn obj(name: &str, size: u64) -> Object {
        Object {
            name: name.to_owned(),
            size,
        }
    }

    fn day(objects: &[(u32, u64)]) -> Vec<Object> {
        let mut v: Vec<Object> = objects
            .iter()
            .map(|(ct, mb)| {
                obj(
                    &alloc::format!("vzdump-lxc-{ct}-2026_11_10-03_00_05.tar.zst.age"),
                    mb * MB,
                )
            })
            .collect();
        v.push(obj("host-config-20261110T020011Z.tar.gz.age", 300_000));
        v
    }

    fn days(names: &[&str]) -> Vec<String> {
        names.iter().map(|s| (*s).to_owned()).collect()
    }

    fn outcomes(r: &[CheckResult]) -> Vec<Outcome> {
        r.iter().map(|x| x.outcome).collect()
    }

    const ALL_GREEN: [Outcome; 5] = [Outcome::Green; 5];

    #[test]
    fn names() {
        assert_eq!(
            kind("vzdump-lxc-112-2026_11_10-03_00_05.tar.zst.age"),
            Kind::Dump(112)
        );
        assert_eq!(
            kind("host-config-20261110T020011Z.tar.gz.age"),
            Kind::HostConfig
        );
        assert_eq!(
            kind("vzdump-lxc-112-2026_11_10-03_00_05.tar.zst"),
            Kind::Plain
        );
        assert_eq!(kind("notes.txt"), Kind::Plain);
        assert_eq!(kind("something.age"), Kind::OtherEncrypted);
        // a name that only resembles a dump is not counted as one
        assert_eq!(kind("vzdump-lxc-112.tar.zst.age"), Kind::OtherEncrypted);
        assert_eq!(
            kind("vzdump-lxc-x-2026_11_10-03_00_05.tar.zst.age"),
            Kind::OtherEncrypted
        );
        assert_eq!(
            kind("vzdump-lxc-112-2026-11-10-03_00_05.tar.zst.age"),
            Kind::OtherEncrypted
        );
    }

    #[test]
    fn dates() {
        assert_eq!(day_number("1970-01-01"), Some(0));
        assert_eq!(
            day_number("2026-11-10"),
            Some(i64::try_from(NOW / 86_400).unwrap())
        );
        assert_eq!(
            day_number("2024-02-29").unwrap() + 1,
            day_number("2024-03-01").unwrap()
        );
        for bad in [
            "",
            "2026-13-01",
            "2026-02-30",
            "2025-02-29",
            "20261110",
            "2026-11-1",
            "latest",
        ] {
            assert_eq!(day_number(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn a_complete_recent_backup_is_green() {
        let d = days(&["2026-11-08", "2026-11-09", "2026-11-10", "tmp"]);
        let today = day(&[(101, 200), (110, 900), (112, 800)]);
        let before = day(&[(101, 200), (110, 880), (112, 790)]);
        let r = check(NOW, Some(&d), Some(&today), Some(&before), &[101, 110, 112]);
        assert_eq!(outcomes(&r), ALL_GREEN);
        assert_eq!(
            r.iter().map(|x| x.id).collect::<Vec<_>>(),
            ["B01", "B02", "B03", "B04", "B05"]
        );
    }

    #[test]
    fn b01_recency() {
        let today = day(&[(110, 900)]);
        let b01 = |names: &[&str]| {
            check(NOW, Some(&days(names)), Some(&today), Some(&[]), &[110])[0].outcome
        };
        assert_eq!(b01(&["2026-11-10"]), Outcome::Green);
        // noon on the 10th: yesterday's folder is still within 26 hours
        assert_eq!(b01(&["2026-11-09"]), Outcome::Green);
        assert_eq!(b01(&["2026-11-08"]), Outcome::Red("stale"));
        assert_eq!(b01(&[]), Outcome::Red("no_backup"));
        assert_eq!(b01(&["tmp", "latest"]), Outcome::Red("no_backup"));
        assert_eq!(b01(&["2026-11-12"]), Outcome::Unknown("clock"));
    }

    #[test]
    fn b02_every_ct_and_b03_config() {
        let d = days(&["2026-11-10"]);
        let today = day(&[(101, 200), (110, 900)]);
        // site 112 was created and never made it into the backup
        let r = check(NOW, Some(&d), Some(&today), Some(&[]), &[101, 110, 112]);
        assert_eq!(r[1].outcome, Outcome::Red("ct_not_backed_up"));
        let r = check(NOW, Some(&d), Some(&today), Some(&[]), &[]);
        assert_eq!(r[1].outcome, Outcome::Unknown("no_ct_list"));
        let no_config: Vec<Object> = today
            .iter()
            .filter(|o| kind(&o.name) != Kind::HostConfig)
            .cloned()
            .collect();
        let r = check(NOW, Some(&d), Some(&no_config), Some(&[]), &[101, 110]);
        assert_eq!(r[2].outcome, Outcome::Red("no_host_config"));
    }

    #[test]
    fn b04_nothing_unencrypted_leaves_the_host() {
        let d = days(&["2026-11-10"]);
        let mut today = day(&[(110, 900)]);
        today.push(obj("vzdump-lxc-110-2026_11_10-03_00_05.tar.zst", 900 * MB));
        let r = check(NOW, Some(&d), Some(&today), Some(&[]), &[110]);
        assert_eq!(r[3].outcome, Outcome::Red("plaintext_offsite"));
    }

    #[test]
    fn b05_sizes() {
        let d = days(&["2026-11-09", "2026-11-10"]);
        let before = day(&[(110, 900), (112, 800)]);
        let b05 = |today: &[Object], prev: Option<&[Object]>| {
            check(NOW, Some(&d), Some(today), prev, &[110, 112])[4].outcome
        };
        assert_eq!(
            b05(&day(&[(110, 900), (112, 800)]), Some(&before)),
            Outcome::Green
        );
        assert_eq!(
            b05(&day(&[(110, 900), (112, 399)]), Some(&before)),
            Outcome::Warn("dump_shrank")
        );
        assert_eq!(
            b05(&day(&[(110, 900), (112, 400)]), Some(&before)),
            Outcome::Green
        );
        assert_eq!(
            b05(&day(&[(110, 900), (112, 3)]), Some(&before)),
            Outcome::Red("dump_too_small")
        );
        // first day: nothing to compare with is fine; a listing that failed is not
        assert_eq!(
            b05(&day(&[(110, 900), (112, 800)]), Some(&[])),
            Outcome::Green
        );
        assert_eq!(
            b05(&day(&[(110, 900), (112, 800)]), None),
            Outcome::Unknown("previous_unlisted")
        );
    }

    #[test]
    fn a_listing_that_failed_is_unknown_not_green() {
        let r = check(NOW, None, None, None, &[110]);
        assert_eq!(outcomes(&r), [Outcome::Unknown("unlisted"); 5]);
        let d = days(&["2026-11-10"]);
        let r = check(NOW, Some(&d), None, None, &[110]);
        assert_eq!(r[0].outcome, Outcome::Green);
        assert_eq!(outcomes(&r[1..]), [Outcome::Unknown("unlisted"); 4]);
        assert_eq!(crate::verify::exit_code(&r), 3);
    }
}
