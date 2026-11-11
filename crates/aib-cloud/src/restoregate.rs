// SPDX-License-Identifier: Apache-2.0
//! The download gate of a restore (E3, B3 slice 3): what must hold before a customer's
//! shared WordPress backup (an All-in-One WP Migration `.wpress` file) is downloaded onto
//! the host, and what the downloaded file must be before anything is imported.
//!
//! Before the download ([`admit_download`]), in this order, each refusal a fixed word:
//! 1. The sharer: the backup must be shared from an address on the customer's own roster
//!    (the hard condition; the roster check itself is the share gate, this only reads its
//!    verdict). A check that could not be made is a refusal, not a pass.
//! 2. The file's name must be this site's: All-in-One WP Migration names a backup
//!    `<host with dots as dashes>-<YYYYMMDD>-<HHMMSS>-<random>.wpress`. The rule is the
//!    EU01 restore line's (decided 2026-07-03, levels 1 and 2): what comes before the
//!    stamp is the domain (with or without `www-`), or holds the domain's first label,
//!    of four letters or more, as a whole word. A second, coarse line after the sharer:
//!    a name without the stamp, or of a plainly other site, is refused.
//! 3. Its size must be known and not 0, and the host must have room for twice it plus
//!    [`SPARE_BYTES`] (the file, what it unpacks to, and room left for the other sites).
//! 4. The target site's database must have been up for [`MIN_DB_UP_SECS`]: one that just
//!    restarted may be restarting again (out of memory), and an import into it fails half
//!    way.
//!
//! After the download ([`check_download`]): the file is as long as the share said, its
//! first header is a `.wpress` header, and its SHA-256 is written into the step record.

/// Room left on the host beyond twice the backup.
pub const SPARE_BYTES: u64 = 10 * 1024 * 1024 * 1024;
/// How long the target's database must have been up.
pub const MIN_DB_UP_SECS: u64 = 600;
/// The length of one `.wpress` header: name 255, size 14, mtime 12, path prefix 4096.
pub const HEADER_LEN: usize = 4377;
const NAME_LEN: usize = 255;
const SIZE_LEN: usize = 14;
const MTIME_LEN: usize = 12;

/// The share gate's verdict on who shared the backup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sharer {
    Allowed,
    /// Refused, with the share gate's own fixed word.
    Refused(&'static str),
    /// The check could not be made.
    Unknown,
}

/// What is known about a backup and the host before it is downloaded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Facts<'a> {
    pub sharer: Sharer,
    /// The file's name at the share.
    pub name: &'a str,
    /// The target site's main domain.
    pub domain: &'a str,
    /// The file's size at the share, when it said.
    pub size: Option<u64>,
    /// Free bytes where downloads go, when it could be read.
    pub free: Option<u64>,
    /// How long the target's database has been up, when it could be read.
    pub db_up_secs: Option<u64>,
}

/// Whether the backup may be downloaded. Refused with a fixed word.
pub fn admit_download(f: &Facts<'_>) -> Result<(), &'static str> {
    match f.sharer {
        Sharer::Allowed => {}
        Sharer::Refused(why) => return Err(why),
        Sharer::Unknown => return Err("share_unknown"),
    }
    if !name_fits_domain(f.name, f.domain) {
        return Err("backup_name_not_this_site");
    }
    let size = f.size.ok_or("backup_size_unknown")?;
    if size == 0 {
        return Err("backup_empty");
    }
    let free = f.free.ok_or("disk_unknown")?;
    if !room_for(size, free) {
        return Err("disk_too_small");
    }
    let up = f.db_up_secs.ok_or("database_unknown")?;
    if up < MIN_DB_UP_SECS {
        return Err("database_restarted_lately");
    }
    Ok(())
}

/// Whether `free` bytes hold twice a backup of `size` and [`SPARE_BYTES`] more.
pub fn room_for(size: u64, free: u64) -> bool {
    size.checked_mul(2)
        .and_then(|twice| twice.checked_add(SPARE_BYTES))
        .is_some_and(|need| free >= need)
}

fn strip_suffix_ignore_case<'a>(s: &'a str, suffix: &str) -> Option<&'a str> {
    let cut = s.len().checked_sub(suffix.len())?;
    let (stem, tail) = (s.get(..cut)?, s.get(cut..)?);
    if tail.eq_ignore_ascii_case(suffix) {
        Some(stem)
    } else {
        None
    }
}

/// Whether `b` starts with All-in-One WP Migration's stamp: `-YYYYMMDD-HHMMSS`, then the
/// end or another `-`.
fn is_stamp(b: &[u8]) -> bool {
    b.len() >= 16
        && b[0] == b'-'
        && b[1..9].iter().all(u8::is_ascii_digit)
        && b[9] == b'-'
        && b[10..16].iter().all(u8::is_ascii_digit)
        && (b.len() == 16 || b[16] == b'-')
}

/// The part of a backup's name before its first stamp; `None` when it has no stamp or
/// nothing before it.
pub fn backup_slug(name: &str) -> Option<&str> {
    let stem = strip_suffix_ignore_case(name, ".wpress")?;
    let at = (0..stem.len()).find(|&i| is_stamp(&stem.as_bytes()[i..]))?;
    stem.get(..at).filter(|slug| !slug.is_empty())
}

/// Whether a backup's file name is this domain's (module note, point 2). Case, dots or
/// dashes, a final dot and a leading `www` are not counted.
pub fn name_fits_domain(name: &str, domain: &str) -> bool {
    let Some(slug) = backup_slug(name) else {
        return false;
    };
    let slug = slug.to_ascii_lowercase().replace('.', "-");
    let slug = slug.strip_prefix("www-").unwrap_or(&slug);
    let domain = domain
        .strip_suffix('.')
        .unwrap_or(domain)
        .to_ascii_lowercase();
    let domain = domain.strip_prefix("www.").unwrap_or(&domain);
    if domain.is_empty() {
        return false;
    }
    if slug == domain.replace('.', "-") {
        return true;
    }
    let label = domain.split('.').next().unwrap_or_default();
    label.len() >= 4 && slug.split('-').any(|word| word == label)
}

fn field_is_digits(field: &[u8]) -> bool {
    let used = field.iter().position(|b| *b == 0).unwrap_or(field.len());
    used > 0
        && field[..used].iter().all(u8::is_ascii_digit)
        && field[used..].iter().all(|b| *b == 0)
}

/// Whether `head` (the file's first bytes) starts with a `.wpress` header: a printable
/// name, then a size and a time in decimal digits, each padded with zero bytes.
pub fn looks_like_wpress(head: &[u8]) -> bool {
    let Some(h) = head.get(..HEADER_LEN) else {
        return false;
    };
    let name = &h[..NAME_LEN];
    let used = name.iter().position(|b| *b == 0).unwrap_or(NAME_LEN);
    used > 0
        && name[..used].iter().all(|b| (0x20..0x7f).contains(b))
        && name[used..].iter().all(|b| *b == 0)
        && field_is_digits(&h[NAME_LEN..NAME_LEN + SIZE_LEN])
        && field_is_digits(&h[NAME_LEN + SIZE_LEN..NAME_LEN + SIZE_LEN + MTIME_LEN])
}

/// Whether a SHA-256 is written as this code writes one: 64 lowercase hex digits.
pub fn valid_sha256(hex: &str) -> bool {
    hex.len() == 64
        && hex
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// What was downloaded, against what the share said. Refused with a fixed word.
pub fn check_download(
    planned_size: u64,
    got_size: u64,
    head: &[u8],
    sha256: &str,
) -> Result<(), &'static str> {
    if got_size != planned_size {
        return Err("download_size_differs");
    }
    if !looks_like_wpress(head) {
        return Err("not_a_wpress");
    }
    if !valid_sha256(sha256) {
        return Err("download_hash_unreadable");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::vec::Vec;

    const GIB: u64 = 1024 * 1024 * 1024;

    fn facts() -> Facts<'static> {
        Facts {
            sharer: Sharer::Allowed,
            name: "example-com-20261101-101010-ab12cd.wpress",
            domain: "www.example.com",
            size: Some(2 * GIB),
            free: Some(14 * GIB),
            db_up_secs: Some(3600),
        }
    }

    fn header(name: &[u8], size: &[u8], mtime: &[u8]) -> Vec<u8> {
        let mut h = alloc::vec![0u8; HEADER_LEN];
        h[..name.len()].copy_from_slice(name);
        h[NAME_LEN..NAME_LEN + size.len()].copy_from_slice(size);
        h[NAME_LEN + SIZE_LEN..NAME_LEN + SIZE_LEN + mtime.len()].copy_from_slice(mtime);
        h
    }

    #[test]
    fn a_backup_that_holds_every_condition_is_downloaded() {
        assert_eq!(admit_download(&facts()), Ok(()));
    }

    #[test]
    fn the_sharer_is_asked_first_and_an_unknown_one_refuses() {
        let mut f = facts();
        f.sharer = Sharer::Unknown;
        f.name = "other-example-20261101-101010-ab12cd.wpress";
        assert_eq!(admit_download(&f), Err("share_unknown"));
        f.sharer = Sharer::Refused("share_address_not_on_roster");
        assert_eq!(admit_download(&f), Err("share_address_not_on_roster"));
    }

    #[test]
    fn a_backup_named_for_another_site_is_refused() {
        for (name, ok) in [
            ("example-com-20261101-101010-ab12cd.wpress", true),
            ("www-example-com-20261101-101010-ab12cd.wpress", true),
            ("Example-COM-20261101-101010-AB12CD.WPRESS", true),
            ("example.com-20261101-101010-ab12cd.wpress", true),
            ("example-com-20261101-101010.wpress", true),
            ("example-shop-20261101-101010-ab12cd.wpress", true),
            ("examples-com-20261101-101010-ab12cd.wpress", false),
            ("other-site-20261101-101010-ab12cd.wpress", false),
            ("example-com.wpress", false),
            ("example-com-2026110-101010-ab12cd.wpress", false),
            ("example-com-20261101-10101x-ab12cd.wpress", false),
            ("example-com-20261101-1010101-ab12cd.wpress", false),
            ("example-com-20261101-101010-ab12cd.zip", false),
            ("-20261101-101010-ab12cd.wpress", false),
            ("", false),
        ] {
            assert_eq!(name_fits_domain(name, "www.example.com."), ok, "{name}");
        }
        // a first label under four letters only counts as the whole domain
        for (name, ok) in [
            ("abc-io-20261101-101010-ab12cd.wpress", true),
            ("www-abc-io-20261101-101010-ab12cd.wpress", true),
            ("abc-shop-20261101-101010-ab12cd.wpress", false),
        ] {
            assert_eq!(name_fits_domain(name, "abc.io"), ok, "{name}");
        }
        assert!(!name_fits_domain("x-20261101-101010-ab12cd.wpress", ""));
        assert_eq!(backup_slug("a-b-20261101-101010-c.wpress"), Some("a-b"));
        let mut f = facts();
        f.name = "other-example-20261101-101010-ab12cd.wpress";
        assert_eq!(admit_download(&f), Err("backup_name_not_this_site"));
    }

    #[test]
    fn a_backup_of_unknown_or_no_size_is_refused() {
        let mut f = facts();
        f.size = None;
        assert_eq!(admit_download(&f), Err("backup_size_unknown"));
        f.size = Some(0);
        assert_eq!(admit_download(&f), Err("backup_empty"));
    }

    #[test]
    fn the_host_needs_room_for_twice_the_backup_and_ten_gib() {
        assert!(room_for(2 * GIB, 14 * GIB));
        assert!(!room_for(2 * GIB, 14 * GIB - 1));
        assert!(!room_for(u64::MAX / 2 + 1, u64::MAX));
        let mut f = facts();
        f.free = Some(14 * GIB - 1);
        assert_eq!(admit_download(&f), Err("disk_too_small"));
        f.free = None;
        assert_eq!(admit_download(&f), Err("disk_unknown"));
    }

    #[test]
    fn a_database_that_just_restarted_takes_no_restore() {
        let mut f = facts();
        f.db_up_secs = Some(MIN_DB_UP_SECS - 1);
        assert_eq!(admit_download(&f), Err("database_restarted_lately"));
        f.db_up_secs = Some(MIN_DB_UP_SECS);
        assert_eq!(admit_download(&f), Ok(()));
        f.db_up_secs = None;
        assert_eq!(admit_download(&f), Err("database_unknown"));
    }

    #[test]
    fn only_a_wpress_header_is_taken() {
        assert!(looks_like_wpress(&header(
            b"package.json",
            b"512",
            b"1730000000"
        )));
        assert!(!looks_like_wpress(
            &header(b"package.json", b"512", b"1730000000")[..HEADER_LEN - 1]
        ));
        assert!(!looks_like_wpress(&header(b"", b"512", b"1730000000")));
        assert!(!looks_like_wpress(&header(
            b"pack\x01age",
            b"512",
            b"1730000000"
        )));
        assert!(!looks_like_wpress(&header(
            b"package.json",
            b"5x2",
            b"1730000000"
        )));
        assert!(!looks_like_wpress(&header(
            b"package.json",
            b"",
            b"1730000000"
        )));
        assert!(!looks_like_wpress(&header(
            b"package.json",
            b"512",
            b"17300a0000"
        )));
        let mut h = header(b"package.json", b"512", b"1730000000");
        h[NAME_LEN + 4] = b'7'; // a digit after the padding
        assert!(!looks_like_wpress(&h));
        let mut zip = header(b"package.json", b"512", b"1730000000");
        zip[..4].copy_from_slice(b"PK\x03\x04");
        assert!(!looks_like_wpress(&zip));
    }

    #[test]
    fn the_download_must_be_what_the_share_said() {
        let h = header(b"package.json", b"512", b"1730000000");
        let sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        assert_eq!(check_download(9, 9, &h, sha), Ok(()));
        assert_eq!(check_download(9, 8, &h, sha), Err("download_size_differs"));
        assert_eq!(check_download(9, 9, &h[..10], sha), Err("not_a_wpress"));
        assert_eq!(
            check_download(9, 9, &h, &sha.to_uppercase()),
            Err("download_hash_unreadable")
        );
        assert_eq!(
            check_download(9, 9, &h, &sha[1..]),
            Err("download_hash_unreadable")
        );
        assert!(!valid_sha256(&sha.replace('a', "g")));
    }
}
