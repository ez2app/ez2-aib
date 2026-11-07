//! Platform Adapter (spec 7). Every `cfg(target_os)` / `cfg(unix|windows)` in
//! aibd lives under this module.

use aib_core::ports::DataDirs;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

#[cfg(unix)]
mod unix;
#[cfg(unix)]
use unix as imp;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
use windows as imp;

/// OS default data dir: macOS `~/Library/Application Support/app.Ez2.AIB`,
/// Windows `%APPDATA%\Ez2\AIB\data` (CI-windows). Never the 1.x `~/.ez2-aib`.
pub fn default_data_dir() -> Option<PathBuf> {
    directories::ProjectDirs::from("app", "Ez2", "AIB").map(|p| p.data_dir().to_path_buf())
}

pub struct OsDirs(pub PathBuf);

impl DataDirs for OsDirs {
    fn data_dir(&self) -> PathBuf {
        self.0.clone()
    }
}

/// Create a directory only the current user can enter (0700 on Unix; on
/// Windows the per-user profile ACL applies, UNVERIFIED-desktop).
pub fn create_private_dir(p: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(p)?;
    imp::restrict_dir(p)
}

/// Write a file readable only by the current user, replacing any old one.
pub fn write_private_file(p: &Path, contents: &[u8]) -> std::io::Result<()> {
    let tmp = p.with_extension("tmp");
    let _ = std::fs::remove_file(&tmp);
    let mut o = OpenOptions::new();
    o.write(true).create_new(true);
    imp::private_file_options(&mut o);
    let mut f = o.open(&tmp)?;
    f.write_all(contents)?;
    f.sync_all()?;
    drop(f);
    std::fs::rename(&tmp, p)
}

/// Single-instance lock. Held for the life of the returned `File`.
/// Uses `std::fs::File::try_lock` (flock on Unix, LockFileEx on Windows).
pub fn instance_lock(p: &Path) -> std::io::Result<Option<File>> {
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(p)?;
    match f.try_lock() {
        Ok(()) => Ok(Some(f)),
        Err(std::fs::TryLockError::WouldBlock) => Ok(None),
        Err(std::fs::TryLockError::Error(e)) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn second_instance_lock_is_refused_then_free_after_drop() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("aibd.lock");
        let a = instance_lock(&p).unwrap();
        assert!(a.is_some());
        assert!(instance_lock(&p).unwrap().is_none());
        drop(a);
        assert!(instance_lock(&p).unwrap().is_some());
    }

    #[test]
    fn private_file_replaces_and_is_owner_only() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("t");
        write_private_file(&p, b"one").unwrap();
        write_private_file(&p, b"two").unwrap();
        assert_eq!(std::fs::read(&p).unwrap(), b"two");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&p).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
    }
}
