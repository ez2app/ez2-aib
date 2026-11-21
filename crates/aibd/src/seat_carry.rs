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
use std::path::{Path, PathBuf};

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

/// Write `files` (name, bytes) into `<cwd>/.aib/`, read-only, and keep `.aib` out of git. The
/// paths written, relative to `cwd` (`.aib/<name>`).
pub fn put(cwd: &Path, files: &[(String, Vec<u8>)]) -> Result<Vec<String>, &'static str> {
    if files.is_empty() {
        return Ok(Vec::new());
    }
    let dir = cwd.join(DIR);
    match std::fs::symlink_metadata(&dir) {
        Ok(m) if m.file_type().is_dir() => {}
        Ok(_) => return Err("carry_refused"),
        Err(_) => std::fs::create_dir(&dir).map_err(|_| "carry_refused")?,
    }
    let mut out = Vec::new();
    for (name, bytes) in files {
        let place = dir.join(name);
        if std::fs::symlink_metadata(&place).is_ok_and(|m| !m.file_type().is_file()) {
            return Err("carry_refused");
        }
        let tmp = dir.join(format!(".{name}.tmp"));
        let _ = std::fs::remove_file(&tmp);
        std::fs::write(&tmp, bytes).map_err(|_| "carry_refused")?;
        read_only(&tmp).map_err(|_| "carry_refused")?;
        // a file of the same name from an earlier open is read-only too: it is replaced whole
        if place.exists() {
            let _ = writable(&place);
            let _ = std::fs::remove_file(&place);
        }
        std::fs::rename(&tmp, &place).map_err(|_| "carry_refused")?;
        out.push(format!("{DIR}/{name}"));
    }
    exclude(cwd).map_err(|_| "carry_refused")?;
    Ok(out)
}

fn read_only(p: &Path) -> std::io::Result<()> {
    let mut perm = std::fs::metadata(p)?.permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        perm.set_mode(0o444);
    }
    #[cfg(not(unix))]
    perm.set_readonly(true);
    std::fs::set_permissions(p, perm)
}

fn writable(p: &Path) -> std::io::Result<()> {
    let mut perm = std::fs::metadata(p)?.permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        perm.set_mode(0o644);
    }
    #[cfg(not(unix))]
    #[allow(clippy::permissions_set_readonly_false)] // Windows: only the read-only flag
    perm.set_readonly(false);
    std::fs::set_permissions(p, perm)
}

/// The `info/exclude` of the git folder `cwd` is (its own `.git` folder, or a worktree's
/// common one); `None` when `cwd` is not a git folder.
pub fn exclude_file(cwd: &Path) -> Option<PathBuf> {
    let dot = cwd.join(".git");
    let m = std::fs::symlink_metadata(&dot).ok()?;
    let git = if m.file_type().is_dir() {
        dot
    } else if m.file_type().is_file() {
        let text = std::fs::read_to_string(&dot).ok()?;
        let line = text.lines().find_map(|l| l.strip_prefix("gitdir:"))?.trim();
        let g = PathBuf::from(line);
        let g = if g.is_absolute() { g } else { cwd.join(g) };
        match std::fs::read_to_string(g.join("commondir")) {
            Ok(c) => {
                let c = PathBuf::from(c.trim());
                if c.is_absolute() {
                    c
                } else {
                    g.join(c)
                }
            }
            Err(_) => g,
        }
    } else {
        return None;
    };
    Some(git.join("info").join("exclude"))
}

/// Add [`EXCLUDE`] to `cwd`'s git exclude once; nothing when `cwd` is not a git folder.
pub fn exclude(cwd: &Path) -> std::io::Result<()> {
    let Some(f) = exclude_file(cwd) else {
        return Ok(());
    };
    let now = std::fs::read_to_string(&f).unwrap_or_default();
    if now.lines().any(|l| l.trim() == EXCLUDE) {
        return Ok(());
    }
    if let Some(d) = f.parent() {
        std::fs::create_dir_all(d)?;
    }
    let mut text = now;
    if !text.is_empty() && !text.ends_with('\n') {
        text.push('\n');
    }
    text.push_str(EXCLUDE);
    text.push('\n');
    std::fs::write(&f, text)
}

/// The page's two names read now from [`crate::docs::root`]: `(name, bytes)` in order, or the
/// refusal `(reason, message)`. A memory pack without a handoff is refused.
pub fn gather(
    root: Option<&Path>,
    handoff: Option<&str>,
    mempack: Option<&str>,
) -> Result<Vec<(String, Vec<u8>)>, (&'static str, &'static str)> {
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
        assert_eq!(got, [".aib/HANDOFF-a.md", ".aib/MEMPACK-a9136-1121.md"]);
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

    /// A worktree's `.git` file leads to the common folder's exclude.
    #[test]
    fn a_worktree_is_excluded_in_its_common_git_folder() {
        let d = tempfile::tempdir().unwrap();
        let common = d.path().join("repo").join(".git");
        let wt = common.join("worktrees").join("w1");
        std::fs::create_dir_all(&wt).unwrap();
        std::fs::write(wt.join("commondir"), "../..\n").unwrap();
        let cwd = d.path().join("tree");
        std::fs::create_dir_all(&cwd).unwrap();
        std::fs::write(cwd.join(".git"), format!("gitdir: {}\n", wt.display())).unwrap();
        assert_eq!(
            exclude_file(&cwd).unwrap(),
            wt.join("../..").join("info").join("exclude")
        );
        exclude(&cwd).unwrap();
        let ex = std::fs::read_to_string(common.join("info").join("exclude")).unwrap();
        assert!(ex.lines().any(|l| l == EXCLUDE));
        let plain = d.path().join("plain");
        std::fs::create_dir_all(&plain).unwrap();
        assert_eq!(exclude_file(&plain), None);
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
