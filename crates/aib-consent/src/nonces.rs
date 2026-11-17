//! The used-nonce ledger for approval proofs (B2 T136-3b, PLAN §7): one empty file per
//! nonce in a 0700 folder, made with `O_CREAT | O_EXCL`, so "first time seen" is decided by
//! the file system and holds across processes and restarts.
//!
//! Any error counts as "seen" (the proof is refused): a ledger that cannot write must
//! not let a proof through twice.
//!
//! This is not a defence against a same-user AI (it can delete these files). What it
//! gives is that one press of the owner approves one run, also across a restart of the
//! handler. Old files can be removed with [`FileNonces::prune`] once every proof that
//! could carry them has expired (the proof's own expiry is checked before the nonce).

use aib_approve::proof::{NonceLedger, ISSUE_SKEW_S};
use std::fs;
use std::io;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// Shortest and longest nonce taken (aib-approve `Binding::validate`: 32–128 lowercase hex).
pub const NONCE_MIN: usize = 32;
pub const NONCE_MAX: usize = 128;

/// Ledger in a folder of its own.
#[derive(Debug)]
pub struct FileNonces {
    dir: PathBuf,
}

fn nonce_ok(n: &str) -> bool {
    (NONCE_MIN..=NONCE_MAX).contains(&n.len())
        && n.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl FileNonces {
    /// Make `dir` (0700) if missing and check it: a real folder, not a link, ours, no
    /// access for group or others. Anything else is refused.
    pub fn open(dir: &Path) -> io::Result<FileNonces> {
        match fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)
        {
            Ok(()) => {}
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e),
        }
        let m = fs::symlink_metadata(dir)?;
        let ours = m.uid() == nix::unistd::getuid().as_raw();
        if !m.file_type().is_dir() || !ours || m.mode() & 0o077 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "nonce folder: not a private folder of this user",
            ));
        }
        Ok(FileNonces {
            dir: dir.to_path_buf(),
        })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Remove nonce files that no proof can still use. `lifetime_s` is the longest proof
    /// lifetime aib-approve accepts (its `TooLongLived` check; wave2 41b954ce
    /// `max_lifetime_s`, `nonce_keep_s` once it exists). A file is kept for `lifetime_s +
    /// ISSUE_SKEW_S` after it was made: a proof consumed at `c` may carry `issued` up to
    /// `c + ISSUE_SKEW_S`, so it expires by `c + ISSUE_SKEW_S + lifetime` (ebb36 T136-3b L1).
    /// The proof's own expiry is checked before the nonce, so a removed file cannot let an
    /// expired proof through. Returns how many were removed; files it cannot read are left.
    pub fn prune(&self, lifetime_s: u64, now_s: i64) -> io::Result<usize> {
        let keep = lifetime_s.saturating_add(ISSUE_SKEW_S);
        let cut = now_s.saturating_sub(i64::try_from(keep).unwrap_or(i64::MAX));
        let mut n = 0;
        for e in fs::read_dir(&self.dir)?.flatten() {
            let name = e.file_name();
            let Some(name) = name.to_str() else { continue };
            if !nonce_ok(name) {
                continue;
            }
            let Ok(m) = e.metadata() else { continue };
            if m.file_type().is_file() && m.mtime() < cut && fs::remove_file(e.path()).is_ok() {
                n += 1;
            }
        }
        Ok(n)
    }
}

impl NonceLedger for FileNonces {
    fn consume(&mut self, nonce: &str) -> bool {
        if !nonce_ok(nonce) {
            return false;
        }
        let made = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(nix::libc::O_NOFOLLOW)
            .open(self.dir.join(nonce));
        let Ok(f) = made else { return false };
        // best effort, so a power cut right after does not lose the entry (ebb36 T136-3b N1);
        // the nonce is taken either way
        let _ = f.sync_all();
        let _ = fs::File::open(&self.dir).and_then(|d| d.sync_all());
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    struct Tmp(PathBuf);
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn tmp(tag: &str) -> Tmp {
        let p = std::env::temp_dir().join(format!("aib-nonces-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        Tmp(p)
    }

    // knife: create_new (O_EXCL) makes the second consume false
    #[test]
    fn a_nonce_counts_once_also_for_a_second_ledger() {
        let t = tmp("once");
        let dir = t.0.join("nonces");
        let mut a = FileNonces::open(&dir).expect("open");
        let n = "a".repeat(32);
        assert!(a.consume(&n));
        assert!(!a.consume(&n));
        let mut b = FileNonces::open(&dir).expect("reopen");
        assert!(!b.consume(&n), "a restart must still see it");
        assert!(b.consume(&"b".repeat(32)));
        assert_eq!(
            fs::metadata(&dir).expect("meta").permissions().mode() & 0o777,
            0o700
        );
    }

    #[test]
    fn a_nonce_that_is_not_lower_hex_is_refused() {
        let t = tmp("shape");
        let mut a = FileNonces::open(&t.0).expect("open");
        for bad in [
            "a".repeat(31),
            "a".repeat(129),
            "A".repeat(32),
            format!("../{}", "a".repeat(32)),
            String::new(),
        ] {
            assert!(!a.consume(&bad), "{bad}");
        }
        assert!(a.consume(&"0".repeat(128)));
    }

    #[test]
    fn a_shared_or_linked_folder_is_refused() {
        let t = tmp("perm");
        fs::create_dir_all(&t.0).expect("dir");
        fs::set_permissions(&t.0, fs::Permissions::from_mode(0o750)).expect("chmod");
        assert!(FileNonces::open(&t.0).is_err());
        fs::set_permissions(&t.0, fs::Permissions::from_mode(0o700)).expect("chmod");
        let real = t.0.join("real");
        fs::create_dir(&real).expect("real");
        fs::set_permissions(&real, fs::Permissions::from_mode(0o700)).expect("chmod");
        let link = t.0.join("link");
        std::os::unix::fs::symlink(&real, &link).expect("link");
        assert!(FileNonces::open(&link).is_err());
    }

    #[test]
    fn a_nonce_file_that_is_a_link_is_not_followed() {
        let t = tmp("link");
        let mut a = FileNonces::open(&t.0).expect("open");
        let n = "c".repeat(32);
        std::os::unix::fs::symlink("/nonexistent-target", t.0.join(&n)).expect("link");
        assert!(!a.consume(&n));
    }

    #[test]
    fn two_threads_one_nonce_one_winner() {
        let t = tmp("race");
        let dir = t.0.clone();
        FileNonces::open(&dir).expect("open");
        let n = "d".repeat(64);
        let hs: Vec<_> = (0..8)
            .map(|_| {
                let (dir, n) = (dir.clone(), n.clone());
                std::thread::spawn(move || FileNonces::open(&dir).is_ok_and(|mut l| l.consume(&n)))
            })
            .collect();
        let wins = hs
            .into_iter()
            .map(|h| h.join().unwrap_or(false))
            .filter(|w| *w)
            .count();
        assert_eq!(wins, 1);
    }

    #[test]
    fn prune_removes_only_old_nonce_files() {
        let t = tmp("prune");
        let mut a = FileNonces::open(&t.0).expect("open");
        assert!(a.consume(&"e".repeat(32)));
        fs::write(t.0.join("note.txt"), "x").expect("other");
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| i64::try_from(d.as_secs()).unwrap_or(0))
            .unwrap_or(0);
        assert_eq!(a.prune(3600, now).expect("prune"), 0);
        assert_eq!(
            a.prune(0, now + ISSUE_SKEW_S as i64 + 10).expect("prune"),
            1
        );
        assert!(t.0.join("note.txt").exists());
        assert!(a.consume(&"e".repeat(32)), "pruned: may be used again");
    }

    // knife: a file must outlive the proof by the issue skew (ebb36 T136-3b L1)
    #[test]
    fn prune_keeps_a_nonce_until_its_latest_proof_has_expired() {
        let t = tmp("edge");
        let mut a = FileNonces::open(&t.0).expect("open");
        let n = "f".repeat(32);
        assert!(a.consume(&n));
        let c = fs::metadata(t.0.join(&n)).expect("meta").mtime();
        let life = 1800;
        // the latest proof with this nonce expires at c + skew + life
        let last = c + ISSUE_SKEW_S as i64 + life as i64;
        assert_eq!(a.prune(life, last).expect("prune"), 0);
        assert_eq!(a.prune(life, c + life as i64 + 1).expect("prune"), 0);
        assert_eq!(a.prune(life, last + 1).expect("prune"), 1);
    }
}
