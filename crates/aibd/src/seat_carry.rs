//! A seat moved in from the terminal gets its handoff and memory pack in its own folder
//! (PREREAD-seat-carry-a9136-1121; FINDING-cutover-seat-cant-read-handoff-6f1cb-1121 A; 519d0
//! 11-21 10:46): a web seat's tools do not reach `~/.ez2-aib/pending`, so aibd copies the two
//! files into `<the seat's folder>/.aib/` before the seat starts.
//!
//! - The page names a file, never a path: `HANDOFF-….md` / `MEMPACK-….md` ([`name_ok`]), read
//!   from [`crate::docs::root`] only, not through a link, a plain UTF-8 file of at most [`MAX`]
//!   bytes ([`read`]).
//! - Written read-only into `.aib/` under the same name ([`put`]); `.aib` that is a link or a
//!   file is refused. A git folder gets `/.aib/` in its `info/exclude`, so it is never
//!   committed or sent back.
use std::io::Read;
use std::path::Path;

/// The folder under the seat's folder the files go to.
pub const DIR: &str = ".aib";
/// The most one file may be.
pub const MAX: u64 = 512 * 1024;
/// The line a git folder's `info/exclude` gets.
pub const EXCLUDE: &str = "/.aib/";
pub const HANDOFF: &str = "HANDOFF-";
pub const MEMPACK: &str = "MEMPACK-";

/// A file name the page may give: `prefix` then 1–120 of `A-Z a-z 0-9 . _ -` in all, ending
/// `.md`; no path, no `..`.
pub fn name_ok(name: &str, prefix: &str) -> bool {
    (prefix.len() + 4..=120).contains(&name.len())
        && name.starts_with(prefix)
        && name.ends_with(".md")
        && !name.contains("..")
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// `name` in `root`: not through a link, a plain file, at most [`MAX`] bytes, UTF-8.
pub fn read(root: &Path, name: &str, prefix: &str) -> Result<Vec<u8>, &'static str> {
    if !name_ok(name, prefix) {
        return Err("name");
    }
    let p = root.join(name);
    let m = std::fs::symlink_metadata(&p).map_err(|_| "missing")?;
    // one size gate, on what is read (at most one byte past the cap): a second one on the
    // metadata would leave a knife on either unbitten (receipt a9136-tk10-a0a3f020b, KT27)
    if !m.file_type().is_file() {
        return Err("not_a_plain_file");
    }
    let f = open_no_follow(&p).map_err(|_| "missing")?;
    let mut bytes = Vec::new();
    f.take(MAX + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "missing")?;
    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX {
        return Err("not_a_plain_file");
    }
    if std::str::from_utf8(&bytes).is_err() {
        return Err("not_text");
    }
    Ok(bytes)
}

#[cfg(unix)]
fn open_no_follow(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(nix::libc::O_NOFOLLOW)
        .open(p)
}

#[cfg(not(unix))]
fn open_no_follow(p: &Path) -> std::io::Result<std::fs::File> {
    // Windows: the link was refused by symlink_metadata just before
    std::fs::File::open(p)
}

/// What [`put`] wrote: the paths relative to the seat's folder (`.aib/<name>`), and whether
/// `/.aib/` is in the folder's own git exclude (`false`: the folder is not a git clone of its
/// own, a worktree's `.git` file is not followed, 5d33a REVIEW-seatcarry L2; the send back
/// refuses `.aib/` either way, [`in_carry`]).
#[derive(Debug, PartialEq, Eq)]
pub struct Carried {
    pub paths: Vec<String>,
    pub excluded: bool,
}

/// A path under [`DIR`] (as git names it): never sent back to a registered repo (5d33a
/// REVIEW-seatcarry L2: the real fence is at the way out, whatever the seat commits).
pub fn in_carry(path: &str) -> bool {
    let low = path.to_ascii_lowercase();
    low == DIR || low.starts_with(".aib/")
}

/// Write `files` (name, bytes) into `<cwd>/.aib/`, read-only, and keep `.aib` out of git.
/// Unix: every step is taken from an open folder (`openat` … `O_NOFOLLOW`, `O_EXCL` scratch,
/// `renameat` in that folder), so `.aib` changed into a link meanwhile cannot send the write
/// elsewhere (5d33a REVIEW-seatcarry L1). Windows: `.aib` is looked at before and after.
pub fn put(cwd: &Path, files: &[(String, Vec<u8>)]) -> Result<Carried, &'static str> {
    if files.is_empty() {
        return Ok(Carried {
            paths: Vec::new(),
            excluded: false,
        });
    }
    let paths = write_all(cwd, files).map_err(|_| "carry_refused")?;
    let excluded = exclude(cwd).unwrap_or(false);
    Ok(Carried { paths, excluded })
}

#[cfg(unix)]
fn write_all(cwd: &Path, files: &[(String, Vec<u8>)]) -> std::io::Result<Vec<String>> {
    let root = at::open_dir(cwd)?;
    let dir = at::dir(&root, DIR, true)?;
    put_in(&dir, files)
}

/// The files written into the open `.aib` folder `dir` (unix).
#[cfg(unix)]
fn put_in(dir: &std::os::fd::OwnedFd, files: &[(String, Vec<u8>)]) -> std::io::Result<Vec<String>> {
    let mut out = Vec::new();
    for (name, bytes) in files {
        at::write_new(dir, name, bytes, at::read_only_mode())?;
        out.push(format!("{DIR}/{name}"));
    }
    Ok(out)
}

#[cfg(not(unix))]
fn write_all(cwd: &Path, files: &[(String, Vec<u8>)]) -> std::io::Result<Vec<String>> {
    let dir = cwd.join(DIR);
    let plain = |d: &Path| std::fs::symlink_metadata(d).is_ok_and(|m| m.file_type().is_dir());
    if std::fs::symlink_metadata(&dir).is_err() {
        std::fs::create_dir(&dir)?;
    }
    let mut out = Vec::new();
    for (name, bytes) in files {
        // no openat on Windows: .aib is a plain folder (not a reparse point) before and after
        if !plain(&dir) {
            return Err(std::io::ErrorKind::InvalidInput.into());
        }
        let (tmp, place) = (dir.join(format!(".{name}.tmp")), dir.join(name));
        let _ = std::fs::remove_file(&tmp);
        std::fs::write(&tmp, bytes)?;
        let mut perm = std::fs::metadata(&tmp)?.permissions();
        perm.set_readonly(true);
        std::fs::set_permissions(&tmp, perm)?;
        if let Ok(m) = std::fs::symlink_metadata(&place) {
            let mut perm = m.permissions();
            #[allow(clippy::permissions_set_readonly_false)] // Windows: only the read-only flag
            perm.set_readonly(false);
            let _ = std::fs::set_permissions(&place, perm);
            let _ = std::fs::remove_file(&place);
        }
        std::fs::rename(&tmp, &place)?;
        if !plain(&dir) {
            return Err(std::io::ErrorKind::InvalidInput.into());
        }
        out.push(format!("{DIR}/{name}"));
    }
    Ok(out)
}

/// Add [`EXCLUDE`] once to `<cwd>/.git/info/exclude` when `<cwd>/.git` is a folder of the
/// seat's own (aibd's copies are whole clones); `false` without writing when it is not (a
/// worktree's `.git` file names a place the seat wrote, never followed: 5d33a L2).
#[cfg(unix)]
pub fn exclude(cwd: &Path) -> std::io::Result<bool> {
    let root = at::open_dir(cwd)?;
    let Ok(git) = at::dir(&root, ".git", false) else {
        return Ok(false);
    };
    let info = at::dir(&git, "info", true)?;
    let now = at::read_plain(&info, "exclude")?.unwrap_or_default();
    if now.lines().any(|l| l.trim() == EXCLUDE) {
        return Ok(true);
    }
    at::write_new(&info, "exclude", with_line(now).as_bytes(), at::file_mode())?;
    Ok(true)
}

#[cfg(not(unix))]
pub fn exclude(cwd: &Path) -> std::io::Result<bool> {
    let git = cwd.join(".git");
    if !std::fs::symlink_metadata(&git).is_ok_and(|m| m.file_type().is_dir()) {
        return Ok(false);
    }
    let info = git.join("info");
    std::fs::create_dir_all(&info)?;
    let f = info.join("exclude");
    let now = std::fs::read_to_string(&f).unwrap_or_default();
    if !now.lines().any(|l| l.trim() == EXCLUDE) {
        std::fs::write(&f, with_line(now))?;
    }
    Ok(true)
}

fn with_line(mut text: String) -> String {
    if !text.is_empty() && !text.ends_with('\n') {
        text.push('\n');
    }
    text.push_str(EXCLUDE);
    text.push('\n');
    text
}

/// Folder-relative file work (unix): each name is one entry of an open folder, never a path.
#[cfg(unix)]
pub(crate) mod at {
    use nix::errno::Errno;
    use nix::fcntl::{openat, renameat, OFlag};
    use nix::sys::stat::{mkdirat, Mode};
    use nix::unistd::{unlinkat, UnlinkatFlags};
    use std::io::{Read, Write};
    use std::os::fd::OwnedFd;
    use std::path::Path;

    fn dir_flags() -> OFlag {
        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC
    }

    pub fn read_only_mode() -> Mode {
        Mode::S_IRUSR | Mode::S_IRGRP | Mode::S_IROTH
    }

    pub fn file_mode() -> Mode {
        Mode::S_IRUSR | Mode::S_IWUSR | Mode::S_IRGRP | Mode::S_IROTH
    }

    /// The seat's folder itself (aibd chose it).
    pub fn open_dir(p: &Path) -> std::io::Result<OwnedFd> {
        Ok(nix::fcntl::open(
            p,
            OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC,
            Mode::empty(),
        )?)
    }

    /// `name` in `parent` as a folder, not through a link; made first when `make`.
    pub fn dir(parent: &OwnedFd, name: &str, make: bool) -> std::io::Result<OwnedFd> {
        match openat(parent, name, dir_flags(), Mode::empty()) {
            Err(Errno::ENOENT) if make => {
                let m =
                    Mode::S_IRWXU | Mode::S_IRGRP | Mode::S_IXGRP | Mode::S_IROTH | Mode::S_IXOTH;
                mkdirat(parent, name, m)?;
                Ok(openat(parent, name, dir_flags(), Mode::empty())?)
            }
            r => Ok(r?),
        }
    }

    /// `name` in `dir` when it is there: a plain file, not through a link.
    pub fn read_plain(dir: &OwnedFd, name: &str) -> std::io::Result<Option<String>> {
        let flags = OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC;
        match openat(dir, name, flags, Mode::empty()) {
            Err(Errno::ENOENT) => Ok(None),
            Err(e) => Err(e.into()),
            Ok(fd) => {
                let mut text = String::new();
                std::fs::File::from(fd)
                    .take(super::MAX)
                    .read_to_string(&mut text)?;
                Ok(Some(text))
            }
        }
    }

    /// What `name` in `dir` is, not through a link: `None` when it is not there, `Some(true)` for
    /// a plain file, `Some(false)` for anything else (a link, a folder).
    pub fn plain_file(dir: &OwnedFd, name: &str) -> std::io::Result<Option<bool>> {
        use nix::fcntl::AtFlags;
        match nix::sys::stat::fstatat(dir, name, AtFlags::AT_SYMLINK_NOFOLLOW) {
            Err(Errno::ENOENT) => Ok(None),
            Err(e) => Err(e.into()),
            Ok(st) => Ok(Some(
                nix::sys::stat::SFlag::from_bits_truncate(st.st_mode)
                    & nix::sys::stat::SFlag::S_IFMT
                    == nix::sys::stat::SFlag::S_IFREG,
            )),
        }
    }

    /// `from` renamed to `to`, both in `dir`.
    pub fn rename(dir: &OwnedFd, from: &str, to: &str) -> std::io::Result<()> {
        Ok(renameat(dir, from, dir, to)?)
    }

    /// `bytes` as `name` in `dir`: a new scratch entry (`O_EXCL`, no link) renamed over it.
    pub fn write_new(dir: &OwnedFd, name: &str, bytes: &[u8], mode: Mode) -> std::io::Result<()> {
        let tmp = format!(".{name}.tmp");
        let _ = unlinkat(dir, tmp.as_str(), UnlinkatFlags::NoRemoveDir);
        let flags =
            OFlag::O_WRONLY | OFlag::O_CREAT | OFlag::O_EXCL | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC;
        let fd = openat(dir, tmp.as_str(), flags, mode)?;
        let mut f = std::fs::File::from(fd);
        f.write_all(bytes)?;
        f.sync_all()?;
        Ok(renameat(dir, tmp.as_str(), dir, name)?)
    }
}

/// The files carried into a seat: `(name, bytes)` in order.
pub type Files = Vec<(String, Vec<u8>)>;
/// A refusal: `(reason, message)`.
pub type Refusal = (&'static str, &'static str);

/// The page's two names read now from [`crate::docs::root`]: `(name, bytes)` in order, or the
/// refusal `(reason, message)`. A memory pack without a handoff is refused.
pub fn gather(
    root: Option<&Path>,
    handoff: Option<&str>,
    mempack: Option<&str>,
) -> Result<Files, Refusal> {
    if handoff.is_none() {
        return if mempack.is_some() {
            Err(("mempack", "a memory pack goes with a handoff"))
        } else {
            Ok(Vec::new())
        };
    }
    let Some(root) = root else {
        return Err(("handoff", "no pending folder on this machine"));
    };
    let mut out = Vec::new();
    for (name, prefix, reason) in [(handoff, HANDOFF, "handoff"), (mempack, MEMPACK, "mempack")] {
        let Some(n) = name else { continue };
        let bytes = read(root, n, prefix).map_err(|_| {
            (
                reason,
                "a HANDOFF-…md / MEMPACK-…md file name in the pending folder: a plain text file of at most 512 KB, not a link",
            )
        })?;
        out.push((n.to_owned(), bytes));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// SC1 / SC2: a name, never a path, with the kind's prefix.
    #[test]
    fn the_page_names_a_handoff_file_not_a_path() {
        assert!(name_ok("HANDOFF-a9136-1120-2237.md", HANDOFF));
        assert!(name_ok("MEMPACK-a9136-1121.md", MEMPACK));
        for bad in [
            "../HANDOFF-x.md",
            "sub/HANDOFF-x.md",
            "HANDOFF-x/../y.md",
            "HANDOFF-..md",
            "HANDOFF-x.txt",
            "SUMMARY-x.md",
            "HANDOFF-x y.md",
            "HANDOFF-.md",
        ] {
            assert!(!name_ok(bad, HANDOFF), "reason: name_taken {bad}");
        }
        assert!(
            !name_ok("HANDOFF-x.md", MEMPACK),
            "reason: prefix_unchecked"
        );
    }

    /// SC3 / SC4: only a plain text file no larger than the cap, not through a link.
    #[test]
    fn only_a_plain_small_file_of_the_pending_folder_is_read() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        std::fs::write(r.join("HANDOFF-a.md"), "交接").unwrap();
        assert_eq!(read(r, "HANDOFF-a.md", HANDOFF).unwrap(), "交接".as_bytes());
        let big = usize::try_from(MAX).unwrap() + 1;
        std::fs::write(r.join("HANDOFF-big.md"), vec![b'x'; big]).unwrap();
        assert_eq!(
            read(r, "HANDOFF-big.md", HANDOFF),
            Err("not_a_plain_file"),
            "reason: no_cap"
        );
        std::fs::write(r.join("HANDOFF-bin.md"), [0xff, 0xfe, 0x00]).unwrap();
        assert_eq!(read(r, "HANDOFF-bin.md", HANDOFF), Err("not_text"));
        assert_eq!(read(r, "HANDOFF-none.md", HANDOFF), Err("missing"));
        std::fs::create_dir(r.join("HANDOFF-dir.md")).unwrap();
        assert_eq!(
            read(r, "HANDOFF-dir.md", HANDOFF),
            Err("not_a_plain_file"),
            "reason: folder_read"
        );
        #[cfg(unix)]
        {
            let secret = d.path().join("secret.txt");
            std::fs::write(&secret, "key").unwrap();
            std::os::unix::fs::symlink(&secret, r.join("HANDOFF-link.md")).unwrap();
            assert_eq!(
                read(r, "HANDOFF-link.md", HANDOFF),
                Err("not_a_plain_file"),
                "reason: link_followed"
            );
            // the open itself does not follow a link either (a link put in after the look)
            assert!(
                open_no_follow(&r.join("HANDOFF-link.md")).is_err(),
                "reason: open_follows"
            );
        }
    }

    /// SC5 / SC6 / SC7: written read-only under `.aib/`, kept out of git; `.aib` that is a
    /// link is refused.
    #[test]
    fn the_files_go_read_only_into_the_seats_folder_out_of_git() {
        let d = tempfile::tempdir().unwrap();
        let cwd = d.path().join("seat");
        std::fs::create_dir_all(cwd.join(".git").join("info")).unwrap();
        let files = vec![
            ("HANDOFF-a.md".to_owned(), b"one".to_vec()),
            ("MEMPACK-a9136-1121.md".to_owned(), b"two".to_vec()),
        ];
        let got = put(&cwd, &files).unwrap();
        assert_eq!(
            got.paths,
            [".aib/HANDOFF-a.md", ".aib/MEMPACK-a9136-1121.md"]
        );
        assert!(got.excluded, "reason: not_excluded");
        let h = cwd.join(".aib").join("HANDOFF-a.md");
        assert_eq!(std::fs::read(&h).unwrap(), b"one");
        assert!(
            std::fs::metadata(&h).unwrap().permissions().readonly(),
            "reason: writable"
        );
        let ex = std::fs::read_to_string(cwd.join(".git/info/exclude")).unwrap();
        assert_eq!(
            ex.lines().filter(|l| *l == EXCLUDE).count(),
            1,
            "reason: not_excluded"
        );
        // again: replaced whole, the exclude line not written twice
        put(&cwd, &[("HANDOFF-a.md".to_owned(), b"new".to_vec())]).unwrap();
        assert_eq!(std::fs::read(&h).unwrap(), b"new");
        let ex = std::fs::read_to_string(cwd.join(".git/info/exclude")).unwrap();
        assert_eq!(ex.lines().filter(|l| *l == EXCLUDE).count(), 1);
        #[cfg(unix)]
        {
            let other = d.path().join("elsewhere");
            std::fs::create_dir_all(&other).unwrap();
            let cwd2 = d.path().join("seat2");
            std::fs::create_dir_all(&cwd2).unwrap();
            std::os::unix::fs::symlink(&other, cwd2.join(".aib")).unwrap();
            assert_eq!(
                put(&cwd2, &files),
                Err("carry_refused"),
                "reason: link_written"
            );
            assert!(std::fs::read_dir(&other).unwrap().next().is_none());
        }
    }

    /// 5d33a L2: a worktree's `.git` file is not followed (the seat wrote it); `.aib/` is refused
    /// on the way back whatever the seat commits.
    #[test]
    fn a_git_file_is_not_followed_and_aib_never_goes_back() {
        let d = tempfile::tempdir().unwrap();
        let elsewhere = d.path().join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        let cwd = d.path().join("tree");
        std::fs::create_dir_all(&cwd).unwrap();
        std::fs::write(
            cwd.join(".git"),
            format!("gitdir: {}\n", elsewhere.display()),
        )
        .unwrap();
        let got = put(&cwd, &[("HANDOFF-a.md".to_owned(), b"h".to_vec())]).unwrap();
        assert!(!got.excluded, "reason: git_file_followed");
        assert!(
            std::fs::read_dir(&elsewhere).unwrap().next().is_none(),
            "reason: git_file_followed"
        );
        let plain = d.path().join("plain");
        std::fs::create_dir_all(&plain).unwrap();
        assert!(!exclude(&plain).unwrap());
        for p in [".aib", ".aib/HANDOFF-a.md", ".AIB/x"] {
            assert!(in_carry(p), "reason: carry_sent_back {p}");
        }
        for p in ["aib/x", ".aibx/y", "src/.aib/x"] {
            assert!(!in_carry(p), "{p}");
        }
    }

    /// SC10 (5d33a L1): the files go into the `.aib` folder opened at the start even when
    /// `.aib` is changed into a link before the write.
    #[cfg(unix)]
    #[test]
    fn a_link_put_in_after_the_look_does_not_move_the_write() {
        let d = tempfile::tempdir().unwrap();
        let cwd = d.path().join("seat");
        let other = d.path().join("elsewhere");
        std::fs::create_dir_all(cwd.join(DIR)).unwrap();
        std::fs::create_dir_all(&other).unwrap();
        let root = at::open_dir(&cwd).unwrap();
        let dir = at::dir(&root, DIR, false).unwrap();
        std::fs::rename(cwd.join(DIR), cwd.join("was")).unwrap();
        std::os::unix::fs::symlink(&other, cwd.join(DIR)).unwrap();
        put_in(&dir, &[("HANDOFF-a.md".to_owned(), b"h".to_vec())]).unwrap();
        assert_eq!(
            std::fs::read(cwd.join("was").join("HANDOFF-a.md")).unwrap(),
            b"h",
            "reason: written_by_path"
        );
        assert!(std::fs::read_dir(&other).unwrap().next().is_none());
    }

    /// SC8: a memory pack goes with a handoff; nothing given, nothing read.
    #[test]
    fn a_memory_pack_comes_with_its_handoff() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        std::fs::write(r.join("HANDOFF-a.md"), "h").unwrap();
        std::fs::write(r.join("MEMPACK-a9136-1121.md"), "m").unwrap();
        assert_eq!(
            gather(Some(r), None, Some("MEMPACK-a9136-1121.md"))
                .err()
                .map(|e| e.0),
            Some("mempack"),
            "reason: mempack_alone"
        );
        assert!(gather(Some(r), None, None).unwrap().is_empty());
        let got = gather(Some(r), Some("HANDOFF-a.md"), Some("MEMPACK-a9136-1121.md")).unwrap();
        assert_eq!(got.len(), 2);
        assert_eq!(
            gather(Some(r), Some("MEMPACK-a9136-1121.md"), None)
                .err()
                .map(|e| e.0),
            Some("handoff")
        );
    }
}
