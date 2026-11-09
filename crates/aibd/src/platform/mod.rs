//! Platform Adapter (spec 7). Every `cfg(target_os)` / `cfg(unix|windows)` in
//! aibd lives under this module.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

#[cfg(target_os = "macos")]
pub mod fork_watch;
pub mod session;
#[cfg(unix)]
pub mod session_unix;
#[cfg(windows)]
pub mod session_windows;
pub mod tree;

#[cfg(unix)]
mod unix;
#[cfg(unix)]
use unix as imp;
#[cfg(windows)]
pub mod windows;
#[cfg(windows)]
use windows as imp;

/// How exited children are kept (ADR 0008 rule 3 premise, 043dd #522 M1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChildReaping {
    /// Exited children stay zombies until we reap them (M5 can look).
    Keeps(&'static str),
    /// The kernel reaps them at once (`SIGCHLD` ignored / `SA_NOCLDWAIT`).
    AutoReaps(&'static str),
    /// Could not read it.
    Unreadable(String),
    /// Not a question on this platform (Windows keeps a HANDLE instead).
    Unsupported,
}

/// Read the `SIGCHLD` disposition (macOS); changes nothing.
pub fn child_reaping() -> ChildReaping {
    #[cfg(target_os = "macos")]
    {
        match aib_sys::sigchld() {
            Ok(s) if s.keeps_zombies() => ChildReaping::Keeps(s.as_str()),
            Ok(s) => ChildReaping::AutoReaps(s.as_str()),
            Err(e) => ChildReaping::Unreadable(e.tag().to_owned()),
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        ChildReaping::Unsupported
    }
}

/// At server start: an inherited ignored `SIGCHLD` is set back to default so
/// our children stay lookable until we reap them. Returns what it was when it
/// had to change it.
pub fn keep_child_zombies() -> Option<&'static str> {
    #[cfg(target_os = "macos")]
    {
        match aib_sys::sigchld_keep_zombies() {
            Ok(was) if !was.keeps_zombies() => Some(was.as_str()),
            _ => None,
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

/// The platform's session starter (`session::SpawnFn`).
pub fn default_spawn() -> session::SpawnFn {
    #[cfg(unix)]
    {
        session_unix::spawn
    }
    #[cfg(windows)]
    {
        // J0 + one Job per session (043dd #543).
        session_windows::spawn
    }
    #[cfg(not(any(unix, windows)))]
    {
        unsupported_spawn
    }
}

#[cfg(not(any(unix, windows)))]
fn unsupported_spawn(
    _: &session::Spec,
    _: Option<std::sync::Arc<crate::lineage::Lineage>>,
) -> Result<Box<dyn session::Running>, session::SpawnFail> {
    Err(session::SpawnFail {
        tag: "unsupported_platform".into(),
        left_running: None,
    })
}

/// Stop the trees left by failed starts once more, all within `deadline`
/// (aibd is ending; bb7dc #544). Returns the roots not confirmed gone, as
/// (pid, recorded start time).
/// Windows: (pid, Job creation time); a root never held is listed with `None`.
pub fn stop_leftovers(deadline: std::time::Instant) -> Vec<(u32, Option<u64>)> {
    #[cfg(unix)]
    {
        session_unix::stop_leftovers(deadline)
    }
    #[cfg(windows)]
    {
        session_windows::stop_leftovers(deadline)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = deadline;
        Vec::new()
    }
}

/// Resolves when aibd should stop: Ctrl-C, and on POSIX also SIGTERM (so a
/// plain `kill` still collects every session, D11).
pub async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        match signal(SignalKind::terminate()) {
            Ok(mut term) => {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = term.recv() => {}
                }
            }
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

/// OS default data dir: macOS `~/Library/Application Support/app.Ez2.AIB`,
/// Windows `%APPDATA%\Ez2\AIB\data` (CI-windows). Never the 1.x `~/.ez2-aib`.
pub fn default_data_dir() -> Option<PathBuf> {
    directories::ProjectDirs::from("app", "Ez2", "AIB").map(|p| p.data_dir().to_path_buf())
}

/// aibd's own data directory (never the AIB 1.x state root) and its `run/`
/// subdirectory for files that only live while aibd runs (token, lock).
pub struct OsDirs(pub PathBuf);

impl OsDirs {
    pub fn data_dir(&self) -> PathBuf {
        self.0.clone()
    }
    pub fn run_dir(&self) -> PathBuf {
        self.0.join("run")
    }
}

/// Create a directory only the current user can enter, and verify it:
/// 0700 on Unix; on Windows a protected DACL with one entry for the current
/// user (CI-windows). Fails instead of continuing with a looser folder.
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
    // Lock the (still empty) file down and check it before the secret goes in.
    if let Err(e) = imp::restrict_file(&tmp) {
        drop(f);
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
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

#[cfg(all(test, windows))]
mod windows_acl_tests {
    use super::*;
    use std::process::Command;

    fn icacls(p: &Path, args: &[&str]) {
        let st = Command::new(windows::acl_entries_tool())
            .arg(p)
            .args(args)
            .status()
            .unwrap_or_else(|e| panic!("icacls {}: {e}", p.display()));
        assert!(st.success(), "icacls {} {args:?}: {st}", p.display());
    }

    #[test]
    fn private_dir_and_file_are_owner_only_even_under_a_loose_parent() {
        let d = tempfile::tempdir().unwrap();
        // loose parent: Everyone (S-1-1-0) may read, inherited by children
        icacls(d.path(), &["/grant", "*S-1-1-0:(OI)(CI)R"]);
        let parent = windows::acl_entries(d.path()).unwrap();
        assert!(parent.len() >= 2, "{parent:?}");

        let run = d.path().join("run");
        create_private_dir(&run).unwrap();
        let tok = run.join("aibd.token");
        write_private_file(&tok, b"secret").unwrap();
        for p in [&run, &tok] {
            let e = windows::acl_entries(p).unwrap();
            assert_eq!(e.len(), 1, "{}: {e:?}", p.display());
            windows::verify_owner_only(p).unwrap();
        }
    }

    #[test]
    fn verify_rejects_an_extra_entry() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("f");
        write_private_file(&p, b"x").unwrap();
        windows::verify_owner_only(&p).unwrap();
        icacls(&p, &["/grant", "*S-1-1-0:R"]);
        assert!(windows::verify_owner_only(&p).is_err());
    }
}
