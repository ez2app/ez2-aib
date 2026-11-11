// SPDX-License-Identifier: Apache-2.0
//! Backup before an update (E10; WordPress ops checklist 2.5 / 9.8): before a customer's
//! AI updates WordPress core, a plugin or a theme, the site must have a finished backup
//! from the last few minutes. This module is the rule only — no I/O. The host reads the
//! local dump folder and hands the file names, times and sizes in.
//!
//! What counts as a backup of CT `ct`: a file in the local dump folder named exactly
//! `vzdump-lxc-<ct>-YYYY_MM_DD-HH_MM_SS.tar.zst` (or `.tar.gz` / `.tar`). Files vzdump is
//! still writing, its logs and notes do not have that name, so a backup that has not
//! finished never counts. A file of size 0 is not a backup either.
//!
//! The age is taken from the file's modification time, not from the time in its name:
//! the name is written in the host's local time zone, the modification time is not.
//!
//! The answer is only ever "allowed" for [`Fresh::Fresh`]. Everything that cannot be
//! read, and a file from the future (the clock is wrong somewhere), is
//! [`Fresh::Unknown`]: an update is not let through on a guess.

/// How recent the backup has to be when nothing else is asked, in minutes.
pub const DEFAULT_MAX_MINUTES: u32 = 30;
/// The longest window that may be asked for, in minutes (one day).
pub const MAX_MAX_MINUTES: u32 = 1440;
/// How far in the future a modification time may be before the clock is not trusted.
pub const CLOCK_SKEW_SECS: u64 = 120;

/// One entry of the local dump folder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DumpFile<'a> {
    /// File name, no path.
    pub name: &'a str,
    /// A plain file (not a folder or a link).
    pub is_file: bool,
    /// Modification time, seconds since the epoch; `None`: could not be read.
    pub mtime: Option<u64>,
    /// Size in bytes; `None`: could not be read.
    pub size: Option<u64>,
}

/// How fresh a site's newest backup is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fresh {
    /// A finished backup `age` seconds old, within the window.
    Fresh {
        /// Seconds since the backup finished.
        age: u64,
    },
    /// The newest finished backup is older than the window.
    Stale {
        /// Seconds since the backup finished.
        age: u64,
    },
    /// No finished backup of this CT in the folder.
    NoBackup,
    /// Could not tell; the fixed word says why.
    Unknown(&'static str),
}

impl Fresh {
    /// The fixed word for the answer.
    pub fn as_str(self) -> &'static str {
        match self {
            Fresh::Fresh { .. } => "fresh",
            Fresh::Stale { .. } => "stale",
            Fresh::NoBackup => "no_backup",
            Fresh::Unknown(why) => why,
        }
    }
}

/// Whether an update may go ahead: only on a fresh, finished backup.
pub fn update_allowed(f: Fresh) -> bool {
    matches!(f, Fresh::Fresh { .. })
}

/// Whether `minutes` is a window that may be asked for.
pub fn max_minutes_ok(minutes: u32) -> bool {
    (1..=MAX_MAX_MINUTES).contains(&minutes)
}

/// The CT a finished local dump belongs to, from its file name; `None` for anything else
/// (a dump still being written, a log, a note, an encrypted copy, a malformed name).
pub fn dump_ct(name: &str) -> Option<u32> {
    let rest = name.strip_prefix("vzdump-lxc-")?;
    let rest = rest
        .strip_suffix(".tar.zst")
        .or_else(|| rest.strip_suffix(".tar.gz"))
        .or_else(|| rest.strip_suffix(".tar"))?;
    let (ct, stamp) = rest.split_once('-')?;
    if ct.is_empty() || !ct.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let stamp_ok = stamp.len() == 19
        && stamp.bytes().enumerate().all(|(i, c)| match i {
            4 | 7 | 13 | 16 => c == b'_',
            10 => c == b'-',
            _ => c.is_ascii_digit(),
        });
    if !stamp_ok {
        return None;
    }
    ct.parse().ok()
}

/// Judge CT `ct`'s newest backup. `files` is the local dump folder (`None`: it could not
/// be listed); `now` is seconds since the epoch; `max_secs` is the window.
pub fn judge(ct: u32, files: Option<&[DumpFile<'_>]>, now: u64, max_secs: u64) -> Fresh {
    let Some(files) = files else {
        return Fresh::Unknown("dump_folder_unreadable");
    };
    let mut newest: Option<u64> = None;
    for f in files {
        if dump_ct(f.name) != Some(ct) {
            continue;
        }
        if !f.is_file {
            continue; // a folder or a link with a dump's name
        }
        let (Some(mtime), Some(size)) = (f.mtime, f.size) else {
            return Fresh::Unknown("dump_unreadable");
        };
        if size == 0 {
            continue; // an empty file is not a backup
        }
        if mtime > now.saturating_add(CLOCK_SKEW_SECS) {
            return Fresh::Unknown("dump_from_the_future");
        }
        newest = Some(newest.map_or(mtime, |n| n.max(mtime)));
    }
    let Some(newest) = newest else {
        return Fresh::NoBackup;
    };
    let age = now.saturating_sub(newest);
    if age <= max_secs {
        Fresh::Fresh { age }
    } else {
        Fresh::Stale { age }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_794_390_000;
    const MAX: u64 = 30 * 60;

    fn f(name: &str, mtime: u64, size: u64) -> DumpFile<'_> {
        DumpFile {
            name,
            is_file: true,
            mtime: Some(mtime),
            size: Some(size),
        }
    }

    #[test]
    fn only_finished_dumps_of_the_ct_are_named() {
        assert_eq!(
            dump_ct("vzdump-lxc-112-2026_11_11-18_30_05.tar.zst"),
            Some(112)
        );
        assert_eq!(
            dump_ct("vzdump-lxc-112-2026_11_11-18_30_05.tar.gz"),
            Some(112)
        );
        assert_eq!(dump_ct("vzdump-lxc-112-2026_11_11-18_30_05.tar"), Some(112));
        for not in [
            "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst.dat",
            "vzdump-lxc-112-2026_11_11-18_30_05.log",
            "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst.notes",
            "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst.age",
            "vzdump-qemu-112-2026_11_11-18_30_05.tar.zst",
            "vzdump-lxc-x12-2026_11_11-18_30_05.tar.zst",
            "vzdump-lxc-+112-2026_11_11-18_30_05.tar.zst",
            "vzdump-lxc--2026_11_11-18_30_05.tar.zst",
            "vzdump-lxc-112-2026-11-11-18_30_05.tar.zst",
            "vzdump-lxc-112.tar.zst",
        ] {
            assert_eq!(dump_ct(not), None, "{not}");
        }
    }

    #[test]
    fn a_backup_within_the_window_lets_the_update_through() {
        let files = [
            f(
                "vzdump-lxc-112-2026_11_11-03_00_05.tar.zst",
                NOW - 15 * 3600,
                900,
            ),
            f("vzdump-lxc-112-2026_11_11-18_30_05.tar.zst", NOW - 600, 900),
        ];
        let got = judge(112, Some(&files), NOW, MAX);
        assert_eq!(got, Fresh::Fresh { age: 600 });
        assert!(update_allowed(got));
        assert_eq!(got.as_str(), "fresh");
    }

    #[test]
    fn an_old_backup_does_not() {
        let files = [f(
            "vzdump-lxc-112-2026_11_11-03_00_05.tar.zst",
            NOW - 3600,
            900,
        )];
        let got = judge(112, Some(&files), NOW, MAX);
        assert_eq!(got, Fresh::Stale { age: 3600 });
        assert!(!update_allowed(got));
        // the edge of the window still counts
        let edge = [f(
            "vzdump-lxc-112-2026_11_11-03_00_05.tar.zst",
            NOW - MAX,
            900,
        )];
        assert_eq!(judge(112, Some(&edge), NOW, MAX), Fresh::Fresh { age: MAX });
    }

    #[test]
    fn another_sites_backup_does_not_count() {
        let files = [
            f("vzdump-lxc-113-2026_11_11-18_30_05.tar.zst", NOW - 60, 900),
            f("vzdump-lxc-1120-2026_11_11-18_30_05.tar.zst", NOW - 60, 900),
        ];
        assert_eq!(judge(112, Some(&files), NOW, MAX), Fresh::NoBackup);
        assert!(!update_allowed(Fresh::NoBackup));
    }

    #[test]
    fn a_dump_still_being_written_or_empty_does_not_count() {
        let mut dir = f("vzdump-lxc-112-2026_11_11-18_30_05.tar.zst", NOW - 60, 900);
        dir.is_file = false;
        let files = [
            f(
                "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst.dat",
                NOW - 60,
                900,
            ),
            f("vzdump-lxc-112-2026_11_11-18_31_05.tar.zst", NOW - 30, 0),
            dir,
        ];
        assert_eq!(judge(112, Some(&files), NOW, MAX), Fresh::NoBackup);
    }

    #[test]
    fn what_cannot_be_read_is_unknown_never_allowed() {
        assert_eq!(
            judge(112, None, NOW, MAX),
            Fresh::Unknown("dump_folder_unreadable")
        );
        let mut no_time = f("vzdump-lxc-112-2026_11_11-18_30_05.tar.zst", 0, 900);
        no_time.mtime = None;
        let fresh = f("vzdump-lxc-112-2026_11_11-18_35_05.tar.zst", NOW - 60, 900);
        // one unreadable dump of this CT spoils the answer, even next to a fresh one
        assert_eq!(
            judge(112, Some(&[fresh, no_time]), NOW, MAX),
            Fresh::Unknown("dump_unreadable")
        );
        let mut no_size = fresh;
        no_size.size = None;
        assert_eq!(
            judge(112, Some(&[no_size]), NOW, MAX),
            Fresh::Unknown("dump_unreadable")
        );
        for u in [
            Fresh::Unknown("dump_folder_unreadable"),
            Fresh::Unknown("dump_unreadable"),
            Fresh::Unknown("dump_from_the_future"),
        ] {
            assert!(!update_allowed(u));
        }
    }

    #[test]
    fn a_dump_from_the_future_is_unknown() {
        let skew = [f(
            "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst",
            NOW + CLOCK_SKEW_SECS,
            900,
        )];
        assert_eq!(judge(112, Some(&skew), NOW, MAX), Fresh::Fresh { age: 0 });
        let ahead = [f(
            "vzdump-lxc-112-2026_11_11-18_30_05.tar.zst",
            NOW + CLOCK_SKEW_SECS + 1,
            900,
        )];
        assert_eq!(
            judge(112, Some(&ahead), NOW, MAX),
            Fresh::Unknown("dump_from_the_future")
        );
    }

    #[test]
    fn the_window_is_one_minute_to_one_day() {
        assert!(max_minutes_ok(1));
        assert!(max_minutes_ok(DEFAULT_MAX_MINUTES));
        assert!(max_minutes_ok(MAX_MAX_MINUTES));
        assert!(!max_minutes_ok(0));
        assert!(!max_minutes_ok(MAX_MAX_MINUTES + 1));
    }
}
