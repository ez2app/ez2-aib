//! L1a (IFACE-provider-lifecycle-c2f0b-1126 r1 §3, r1-1, r1-5 N2): download one measured
//! provider program, check it, and put it in aibd's own folder.
//!
//! - From where: only the row of the measured table it is given (host + full path fixed in
//!   the program, L2), refused unless it is that provider's own https download path.
//! - With what: the system's curl by absolute path (Windows: the OS system directory, never
//!   `%SystemRoot%` / `PATH`), a fixed argument list, an empty environment, no redirect, and
//!   no more bytes than the table's size.
//! - Two checks before anything is kept (§3.2): P = SHA-256 and size equal the table's; S = the
//!   platform signature, a check the caller passes in (Windows: aib-sys wintrust, L1b; the
//!   same one L2's `signed_by_maker` uses).
//! - Where it goes: `<data>/providers/<provider>/<version>/<name>`, copied from the very handle
//!   that was checked (bytes hashed again while copied), then renamed into place; the copy is
//!   checked once more. `claude install` is not run (r1-1): nothing under the customer's
//!   `~/.local` is touched.
//! - What is left on failure or cancel (§3.8): nothing; the download folder `run/dl/<random>`
//!   is removed. At start-up [`clean_downloads`] removes what a crash left, a link only as
//!   the link (N2).
//!
//! Not here: the act, the owner check and the one-job-per-provider rule (L2); the time limit
//! through timers, stopping curl midway (Job Object / killpg) and the Windows read-only lock
//! between check and copy (L1b / L1c).

use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering};

/// One row of the measured table (L2 owns the table; L1 reads only these).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FetchRow {
    pub provider: String,
    pub version: String,
    /// `win32-x64` / `win32-arm64` / `darwin-arm64` / `darwin-x64`
    pub platform: String,
    pub url: String,
    /// 64 lowercase hex
    pub sha256: String,
    pub size: u64,
}

/// The refusal codes the page shows (§5); the job's `error`.
pub const ERR_SOURCE: &str = "lifecycle_bad_source";
pub const ERR_OFFLINE: &str = "lifecycle_offline";
pub const ERR_VERIFY: &str = "lifecycle_verify_failed";
pub const ERR_UNSIGNED: &str = "lifecycle_unsigned";
pub const ERR_CANCELLED: &str = "lifecycle_cancelled";
pub const ERR_PLACE: &str = "lifecycle_place_failed";

/// Each provider's one download prefix (IFACE r1 補 2). Only Claude is fetched in L1.
const SOURCES: &[(&str, &str)] = &[(
    "claude",
    "https://downloads.claude.ai/claude-code-releases/",
)];

/// The platform name the measured table uses for this build, or none (not offered).
pub fn this_platform() -> Option<&'static str> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("windows", "x86_64") => Some("win32-x64"),
        ("windows", "aarch64") => Some("win32-arm64"),
        ("macos", "aarch64") => Some("darwin-arm64"),
        ("macos", "x86_64") => Some("darwin-x64"),
        _ => None,
    }
}

/// The program's file name on this platform.
pub fn program_name(provider: &str) -> String {
    if cfg!(windows) {
        format!("{provider}.exe")
    } else {
        provider.to_owned()
    }
}

/// Whether `row` may be fetched: a known provider, its own https prefix followed by exactly
/// `<version>/<platform>/<name>`, plain version and platform words, a 64-hex hash and a size.
pub fn source_ok(row: &FetchRow) -> bool {
    let Some((_, prefix)) = SOURCES.iter().find(|(p, _)| *p == row.provider) else {
        return false;
    };
    let word = |s: &str| {
        !s.is_empty()
            && s.len() <= 32
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-'))
            && !s.contains("..")
    };
    let name = if row.platform.starts_with("win32-") {
        format!("{}.exe", row.provider)
    } else {
        row.provider.clone()
    };
    let want = format!("{prefix}{}/{}/{name}", row.version, row.platform);
    word(&row.version)
        && word(&row.platform)
        && row.url == want
        && row.sha256.len() == 64
        && row
            .sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && row.size > 0
}

/// The system's curl, by absolute path; none when the system directory cannot be read.
pub fn curl_path() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        aib_sys::sysdir::system_dir().map(|d| d.join("curl.exe"))
    }
    #[cfg(not(windows))]
    {
        Some(PathBuf::from("/usr/bin/curl"))
    }
}

/// curl's whole argument list for one download: no `.curlrc`, https only, TLS 1.2 or newer,
/// an HTTP error is a failure, no redirect, at most the table's size, the file to `out`.
pub fn curl_args(row: &FetchRow, out: &Path) -> Vec<std::ffi::OsString> {
    let mut a: Vec<std::ffi::OsString> = [
        "-q",
        "--silent",
        "--show-error",
        "--fail",
        "--proto",
        "=https",
        "--tlsv1.2",
        "--max-redirs",
        "0",
        "--connect-timeout",
        "30",
        "--max-filesize",
    ]
    .iter()
    .map(Into::into)
    .collect();
    a.push(row.size.to_string().into());
    a.push("--output".into());
    a.push(out.as_os_str().to_owned());
    a.push("--url".into());
    a.push(row.url.clone().into());
    a
}

/// Where a job is (§2.2 `job.phase`), with bytes done of the table's size.
#[derive(Debug, Default)]
pub struct Progress {
    phase: AtomicU8,
    done: AtomicU64,
}

impl Progress {
    pub fn phase(&self) -> &'static str {
        match self.phase.load(Ordering::SeqCst) {
            0 => "download",
            1 => "verify",
            _ => "place",
        }
    }
    pub fn done(&self) -> u64 {
        self.done.load(Ordering::SeqCst)
    }
    fn set(&self, phase: u8, done: u64) {
        self.phase.store(phase, Ordering::SeqCst);
        self.done.store(done, Ordering::SeqCst);
    }
}

/// Gets the bytes of `row` into `out` (a new file). The real one runs curl; tests write bytes.
pub trait Fetch {
    fn fetch(&self, row: &FetchRow, out: &Path) -> Result<(), &'static str>;
}

/// The system curl, empty environment (Windows keeps only `SystemRoot`, which its TLS needs).
pub struct Curl;

impl Fetch for Curl {
    fn fetch(&self, row: &FetchRow, out: &Path) -> Result<(), &'static str> {
        let curl = curl_path().ok_or(ERR_OFFLINE)?;
        let mut c = std::process::Command::new(&curl);
        c.args(curl_args(row, out))
            .env_clear()
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        if let Some(root) = curl.parent().and_then(Path::parent) {
            c.env("SystemRoot", root);
        }
        match c.status() {
            Ok(s) if s.success() => Ok(()),
            _ => Err(ERR_OFFLINE),
        }
    }
}

/// The platform signature check (§3.2 S), given the checked file's path and open handle.
pub type Signer<'a> = &'a dyn Fn(&Path, &File) -> Result<(), &'static str>;

/// SHA-256 (lowercase hex) and length of everything `r` gives, stopping on `cancel`; each
/// chunk read is also written to `copy` when there is one.
fn hash_stream(
    r: &mut impl Read,
    mut copy: Option<&mut File>,
    cancel: &AtomicBool,
    progress: &Progress,
    phase: u8,
) -> Result<(String, u64), &'static str> {
    let mut h = Sha256::new();
    let mut n = 0u64;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        if cancel.load(Ordering::SeqCst) {
            return Err(ERR_CANCELLED);
        }
        let k = r.read(&mut buf).map_err(|_| ERR_VERIFY)?;
        if k == 0 {
            break;
        }
        let chunk = buf.get(..k).ok_or(ERR_VERIFY)?;
        h.update(chunk);
        if let Some(f) = copy.as_deref_mut() {
            f.write_all(chunk).map_err(|_| ERR_PLACE)?;
        }
        n = n.saturating_add(k as u64);
        progress.set(phase, n);
    }
    let hex: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
    Ok((hex, n))
}

/// Whether the file at `path` is exactly the table's: SHA-256 and size, the whole file, no
/// cache (IFACE r1 補 (a)). Opening a seat calls this each time.
pub fn verify_placed(row: &FetchRow, path: &Path) -> Result<(), &'static str> {
    let mut f = File::open(path).map_err(|_| ERR_VERIFY)?;
    let (sha, size) = hash_stream(
        &mut f,
        None,
        &AtomicBool::new(false),
        &Progress::default(),
        1,
    )?;
    if sha != row.sha256 || size != row.size {
        return Err(ERR_VERIFY);
    }
    Ok(())
}

/// The folder a checked program is kept in.
pub fn placed_dir(data: &Path, row: &FetchRow) -> PathBuf {
    data.join("providers")
        .join(&row.provider)
        .join(&row.version)
}

/// The download folder of all jobs.
pub fn downloads_dir(data: &Path) -> PathBuf {
    data.join("run").join("dl")
}

fn random_name() -> Result<String, &'static str> {
    let mut b = [0u8; 12];
    getrandom::fill(&mut b).map_err(|_| ERR_PLACE)?;
    Ok(b.iter().map(|x| format!("{x:02x}")).collect())
}

/// Download `row`, check P and S, place it; the placed path. Whatever happens, the job's
/// download folder is gone afterwards.
pub fn run(
    data: &Path,
    row: &FetchRow,
    fetch: &dyn Fetch,
    signer: Signer<'_>,
    cancel: &AtomicBool,
    progress: &Progress,
) -> Result<PathBuf, &'static str> {
    if !source_ok(row) {
        return Err(ERR_SOURCE);
    }
    let dl = downloads_dir(data).join(random_name()?);
    crate::platform::create_private_dir(&dl).map_err(|_| ERR_PLACE)?;
    let out = run_in(data, row, &dl, fetch, signer, cancel, progress);
    let _ = std::fs::remove_dir_all(&dl);
    out
}

fn run_in(
    data: &Path,
    row: &FetchRow,
    dl: &Path,
    fetch: &dyn Fetch,
    signer: Signer<'_>,
    cancel: &AtomicBool,
    progress: &Progress,
) -> Result<PathBuf, &'static str> {
    let name = program_name(&row.provider);
    let got = dl.join(&name);
    progress.set(0, 0);
    fetch.fetch(row, &got)?;
    if cancel.load(Ordering::SeqCst) {
        return Err(ERR_CANCELLED);
    }
    // P: the whole file against the table
    let mut f = File::open(&got).map_err(|_| ERR_VERIFY)?;
    let (got_sha, got_size) = hash_stream(&mut f, None, cancel, progress, 1)?;
    if got_sha != row.sha256 || got_size != row.size {
        return Err(ERR_VERIFY);
    }
    // S: the platform signature, on the same handle
    signer(&got, &f).map_err(|_| ERR_UNSIGNED)?;
    if cancel.load(Ordering::SeqCst) {
        return Err(ERR_CANCELLED);
    }
    // place: copy from the checked handle, hashing again what is written
    let dir = placed_dir(data, row);
    crate::platform::create_private_dir(&dir).map_err(|_| ERR_PLACE)?;
    let fin = dir.join(&name);
    let tmp = dir.join(format!("{name}.part"));
    let _ = std::fs::remove_file(&tmp);
    if let Err(e) = place(&mut f, &tmp, &fin, row, cancel, progress) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    verify_placed(row, &fin)?;
    Ok(fin)
}

/// Copy the checked handle `f` to `tmp`, hashing what is written, then rename it to `fin`.
fn place(
    f: &mut File,
    tmp: &Path,
    fin: &Path,
    row: &FetchRow,
    cancel: &AtomicBool,
    progress: &Progress,
) -> Result<(), &'static str> {
    f.seek(SeekFrom::Start(0)).map_err(|_| ERR_PLACE)?;
    let mut w = File::options()
        .write(true)
        .create_new(true)
        .open(tmp)
        .map_err(|_| ERR_PLACE)?;
    let (wrote_sha, wrote_size) = hash_stream(f, Some(&mut w), cancel, progress, 2)?;
    if wrote_sha != row.sha256 || wrote_size != row.size {
        return Err(ERR_VERIFY);
    }
    w.sync_all().map_err(|_| ERR_PLACE)?;
    drop(w);
    executable(tmp)?;
    std::fs::rename(tmp, fin).map_err(|_| ERR_PLACE)
}

#[cfg(unix)]
fn executable(p: &Path) -> Result<(), &'static str> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700)).map_err(|_| ERR_PLACE)
}

#[cfg(not(unix))]
fn executable(_: &Path) -> Result<(), &'static str> {
    Ok(())
}

/// Whether `m` (from `symlink_metadata`) is a link: a symlink, or on Windows any reparse
/// point (junctions too), which is removed as itself and never followed (N2).
fn is_link(m: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        m.file_type().is_symlink() || m.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        m.file_type().is_symlink()
    }
}

fn remove_link(p: &Path, m: &std::fs::Metadata) {
    if std::fs::remove_file(p).is_err() && m.is_dir() {
        let _ = std::fs::remove_dir(p);
    }
}

/// At start-up: remove every job folder a crash left under `run/dl`. A link (or the `dl`
/// folder itself being one) is removed as the link; what it points at is not touched.
pub fn clean_downloads(data: &Path) -> usize {
    let dl = downloads_dir(data);
    let Ok(dm) = std::fs::symlink_metadata(&dl) else {
        return 0;
    };
    if is_link(&dm) {
        remove_link(&dl, &dm);
        return 1;
    }
    let Ok(rd) = std::fs::read_dir(&dl) else {
        return 0;
    };
    let mut n = 0;
    for e in rd.flatten() {
        let p = e.path();
        let Ok(m) = std::fs::symlink_metadata(&p) else {
            continue;
        };
        if is_link(&m) {
            remove_link(&p, &m);
        } else if m.is_dir() {
            let _ = std::fs::remove_dir_all(&p);
        } else {
            let _ = std::fs::remove_file(&p);
        }
        n += 1;
    }
    n
}

#[cfg(test)]
mod tests {
    use super::*;

    const BODY: &[u8] = b"pretend this is claude 2.1.326";

    fn sha(b: &[u8]) -> String {
        Sha256::digest(b)
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect()
    }

    fn row(platform: &str) -> FetchRow {
        let name = if platform.starts_with("win32-") {
            "claude.exe"
        } else {
            "claude"
        };
        FetchRow {
            provider: "claude".into(),
            version: "2.1.326".into(),
            platform: platform.into(),
            url: format!(
                "https://downloads.claude.ai/claude-code-releases/2.1.326/{platform}/{name}"
            ),
            sha256: sha(BODY),
            size: BODY.len() as u64,
        }
    }

    fn here() -> FetchRow {
        row(this_platform().unwrap_or("win32-x64"))
    }

    /// Writes `bytes` where curl would, counting its calls.
    struct Bytes(Vec<u8>, std::cell::Cell<u32>);
    impl Fetch for Bytes {
        fn fetch(&self, _: &FetchRow, out: &Path) -> Result<(), &'static str> {
            self.1.set(self.1.get() + 1);
            std::fs::write(out, &self.0).map_err(|_| ERR_OFFLINE)
        }
    }
    fn bytes(b: &[u8]) -> Bytes {
        Bytes(b.to_vec(), std::cell::Cell::new(0))
    }
    struct Offline;
    impl Fetch for Offline {
        fn fetch(&self, _: &FetchRow, _: &Path) -> Result<(), &'static str> {
            Err(ERR_OFFLINE)
        }
    }

    fn signed(_: &Path, _: &File) -> Result<(), &'static str> {
        Ok(())
    }
    /// A signer that says yes and fails the test when it is shown a file P should have refused.
    fn only_measured(p: &Path, _: &File) -> Result<(), &'static str> {
        assert_eq!(std::fs::read(p).unwrap(), BODY, "reason: signed_unmeasured");
        Ok(())
    }
    fn unsigned(_: &Path, _: &File) -> Result<(), &'static str> {
        Err("unsigned")
    }

    fn go(
        data: &Path,
        r: &FetchRow,
        f: &dyn Fetch,
        s: Signer<'_>,
    ) -> Result<PathBuf, &'static str> {
        run(data, r, f, s, &AtomicBool::new(false), &Progress::default())
    }

    fn dl_empty(data: &Path) -> bool {
        std::fs::read_dir(downloads_dir(data))
            .ok()
            .and_then(|mut d| d.next())
            .is_none()
    }

    fn nothing_placed(data: &Path, r: &FetchRow) -> bool {
        !placed_dir(data, r).join(program_name("claude")).exists()
    }

    #[test]
    fn a_measured_file_is_placed_in_aibds_folder_and_the_download_folder_is_gone() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        let p = go(d.path(), &r, &bytes(BODY), &signed).unwrap();
        assert_eq!(
            p,
            d.path()
                .join("providers/claude/2.1.326")
                .join(program_name("claude"))
        );
        assert_eq!(std::fs::read(&p).unwrap(), BODY);
        assert!(!p
            .with_file_name(format!("{}.part", program_name("claude")))
            .exists());
        assert!(dl_empty(d.path()), "reason: download_left");
        assert_eq!(verify_placed(&r, &p), Ok(()));
        // the same version again replaces it with the same bytes
        assert_eq!(go(d.path(), &r, &bytes(BODY), &signed), Ok(p));
    }

    #[test]
    fn a_file_that_is_not_the_measured_one_is_never_kept() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        // same size, other bytes
        let mut other = BODY.to_vec();
        other[0] ^= 1;
        assert_eq!(
            go(d.path(), &r, &bytes(&other), &only_measured),
            Err(ERR_VERIFY)
        );
        assert!(nothing_placed(d.path(), &r), "reason: wrong_hash_placed");
        // one byte more (curl's --max-filesize is the first stop; this is the second)
        let mut long = BODY.to_vec();
        long.push(b'x');
        let mut r2 = r.clone();
        r2.sha256 = sha(&long);
        assert_eq!(
            go(d.path(), &r2, &bytes(&long), &only_measured),
            Err(ERR_VERIFY)
        );
        assert!(nothing_placed(d.path(), &r), "reason: wrong_size_placed");
        assert!(dl_empty(d.path()), "reason: download_left");
    }

    #[test]
    fn an_unsigned_file_is_refused_even_when_its_hash_is_measured() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        assert_eq!(go(d.path(), &r, &bytes(BODY), &unsigned), Err(ERR_UNSIGNED));
        assert!(nothing_placed(d.path(), &r), "reason: unsigned_placed");
        assert!(dl_empty(d.path()), "reason: download_left");
    }

    #[test]
    fn only_the_providers_own_https_path_is_fetched() {
        let d = tempfile::tempdir().unwrap();
        let good = here();
        assert!(source_ok(&good));
        let bad = [
            ("http", good.url.replacen("https://", "http://", 1)),
            (
                "host",
                good.url
                    .replace("downloads.claude.ai", "downloads.claude.ai.evil.test"),
            ),
            ("path", good.url.replace("claude-code-releases", "other")),
            ("query", format!("{}?x=1", good.url)),
            ("version", good.url.replace("2.1.326", "2.1.327")),
        ];
        for (why, url) in bad {
            let r = FetchRow {
                url,
                ..good.clone()
            };
            let f = bytes(BODY);
            assert_eq!(go(d.path(), &r, &f, &signed), Err(ERR_SOURCE), "{why}");
            assert_eq!(f.1.get(), 0, "reason: fetched_bad_source {why}");
        }
        let words = [
            FetchRow {
                provider: "codex".into(),
                ..good.clone()
            },
            FetchRow {
                version: "../x".into(),
                ..good.clone()
            },
            FetchRow {
                sha256: good.sha256.to_uppercase(),
                ..good.clone()
            },
            FetchRow {
                sha256: "ab".into(),
                ..good.clone()
            },
            FetchRow {
                size: 0,
                ..good.clone()
            },
        ];
        for r in words {
            assert!(!source_ok(&r), "{r:?}");
        }
        assert!(dl_empty(d.path()));
    }

    #[test]
    fn curl_gets_one_fixed_argument_list_that_stops_at_the_measured_size() {
        let r = row("win32-x64");
        let a: Vec<String> = curl_args(&r, Path::new("out"))
            .iter()
            .map(|s| s.to_string_lossy().into_owned())
            .collect();
        let s = a.join(" ");
        assert!(s.starts_with("-q "), "reason: curlrc_read {s}");
        for need in [
            "--fail",
            "--proto =https",
            "--tlsv1.2",
            "--max-redirs 0",
            &format!("--max-filesize {}", r.size),
            "--output out",
        ] {
            assert!(s.contains(need), "reason: curl_arg_missing {need}: {s}");
        }
        assert_eq!(a.last(), Some(&r.url));
        assert!(!s.contains("--location") && !s.contains(" -L"));
        if cfg!(not(windows)) {
            assert_eq!(curl_path(), Some(PathBuf::from("/usr/bin/curl")));
        }
    }

    #[test]
    fn an_offline_or_cancelled_job_leaves_nothing() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        assert_eq!(go(d.path(), &r, &Offline, &signed), Err(ERR_OFFLINE));
        assert!(dl_empty(d.path()));
        let cancel = AtomicBool::new(true);
        let p = Progress::default();
        assert_eq!(
            run(d.path(), &r, &bytes(BODY), &signed, &cancel, &p),
            Err(ERR_CANCELLED)
        );
        assert!(nothing_placed(d.path(), &r), "reason: cancelled_placed");
        assert!(dl_empty(d.path()), "reason: download_left");
    }

    #[test]
    fn the_copy_is_checked_again_as_it_is_written() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        let src = d.path().join("src");
        std::fs::write(&src, b"not the measured bytes").unwrap();
        let mut f = File::open(&src).unwrap();
        let (tmp, fin) = (d.path().join("t.part"), d.path().join("t"));
        let none = AtomicBool::new(false);
        assert_eq!(
            place(&mut f, &tmp, &fin, &r, &none, &Progress::default()),
            Err(ERR_VERIFY)
        );
        assert!(!fin.exists(), "reason: unchecked_copy_placed");
    }

    #[test]
    fn a_cancel_stops_the_hash_midway() {
        let mut r: &[u8] = BODY;
        let cancel = AtomicBool::new(true);
        assert_eq!(
            hash_stream(&mut r, None, &cancel, &Progress::default(), 1),
            Err(ERR_CANCELLED)
        );
        let mut r: &[u8] = BODY;
        let ok = hash_stream(
            &mut r,
            None,
            &AtomicBool::new(false),
            &Progress::default(),
            1,
        );
        assert_eq!(ok, Ok((sha(BODY), BODY.len() as u64)));
    }

    #[test]
    fn a_placed_file_changed_later_fails_the_check_a_seat_open_makes() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        let p = go(d.path(), &r, &bytes(BODY), &signed).unwrap();
        let mut b = std::fs::read(&p).unwrap();
        b[1] ^= 1;
        std::fs::write(&p, &b).unwrap();
        assert_eq!(
            verify_placed(&r, &p),
            Err(ERR_VERIFY),
            "reason: changed_kept"
        );
        b.truncate(3);
        std::fs::write(&p, &b).unwrap();
        assert_eq!(verify_placed(&r, &p), Err(ERR_VERIFY));
        assert_eq!(verify_placed(&r, &d.path().join("none")), Err(ERR_VERIFY));
    }

    #[test]
    fn progress_counts_bytes_and_names_the_phase() {
        let d = tempfile::tempdir().unwrap();
        let r = here();
        let p = Progress::default();
        assert_eq!(p.phase(), "download");
        run(
            d.path(),
            &r,
            &bytes(BODY),
            &signed,
            &AtomicBool::new(false),
            &p,
        )
        .unwrap();
        assert_eq!((p.phase(), p.done()), ("place", r.size));
    }

    #[test]
    fn start_up_removes_what_a_crash_left_and_only_the_link_of_a_link() {
        let d = tempfile::tempdir().unwrap();
        let dl = downloads_dir(d.path());
        std::fs::create_dir_all(dl.join("a1/sub")).unwrap();
        std::fs::write(dl.join("a1/sub/claude"), b"x").unwrap();
        std::fs::write(dl.join("stray"), b"x").unwrap();
        #[cfg(unix)]
        {
            let outside = tempfile::tempdir().unwrap();
            std::fs::write(outside.path().join("keep"), b"k").unwrap();
            std::os::unix::fs::symlink(outside.path(), dl.join("link")).unwrap();
            assert_eq!(clean_downloads(d.path()), 3);
            assert!(
                outside.path().join("keep").exists(),
                "reason: followed_link"
            );
            // the dl folder itself a link: the link goes, the target stays
            std::fs::remove_dir_all(&dl).unwrap();
            std::os::unix::fs::symlink(outside.path(), &dl).unwrap();
            assert_eq!(clean_downloads(d.path()), 1);
            assert!(std::fs::symlink_metadata(&dl).is_err());
            assert!(
                outside.path().join("keep").exists(),
                "reason: followed_dl_link"
            );
        }
        #[cfg(not(unix))]
        assert_eq!(clean_downloads(d.path()), 2);
        assert!(dl_empty(d.path()));
        assert_eq!(clean_downloads(d.path()), 0);
    }
}
