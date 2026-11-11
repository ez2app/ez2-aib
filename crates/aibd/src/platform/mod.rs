//! Platform Adapter (spec 7). Every `cfg(target_os)` / `cfg(unix|windows)` in
//! aibd lives under this module.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use lifecycle::{Lifecycle, Step};

#[cfg(target_os = "macos")]
pub mod fork_watch;
pub mod lifecycle;
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
    match <lifecycle::Current as Lifecycle>::child_reaping() {
        Step::Done(r) => r,
        Step::Unsupported { .. } | Step::NotApplicable { .. } => ChildReaping::Unsupported,
    }
}

/// At server start: an inherited ignored `SIGCHLD` is set back to default so
/// our children stay lookable until we reap them. Returns what it was when it
/// had to change it.
pub fn keep_child_zombies() -> Option<&'static str> {
    match <lifecycle::Current as Lifecycle>::keep_child_zombies() {
        Step::Done(was) => was,
        Step::Unsupported { .. } | Step::NotApplicable { .. } => None,
    }
}

/// The platform's session starter (`session::SpawnFn`).
pub fn default_spawn() -> session::SpawnFn {
    match <lifecycle::Current as Lifecycle>::spawn() {
        Step::Done(f) => f,
        Step::Unsupported { .. } | Step::NotApplicable { .. } => unsupported_spawn,
    }
}

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
/// (aibd is ending; bb7dc #544). Never an empty value for "could not look"
/// ([`lifecycle::Swept`]).
pub fn stop_leftovers(deadline: std::time::Instant) -> lifecycle::Swept {
    <lifecycle::Current as Lifecycle>::stop_leftovers(deadline)
}

/// Resolves when aibd should stop. What this platform does not hear is
/// logged once, here, so it is never silent.
pub async fn shutdown_signal() {
    let s = <lifecycle::Current as Lifecycle>::shutdown_signal();
    if !s.not_heard.is_empty() {
        tracing::warn!(
            platform = <lifecycle::Current as Lifecycle>::NAME,
            not_heard = ?s.not_heard,
            "shutdown: these events do not run the session shutdown (known limit)"
        );
    }
    s.wait.await;
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

/// The store's database file, owner only before SQLite writes anything to
/// it (session output is kept there, a63ab preview.3: 0600; the folder is
/// already [`create_private_dir`]). Created empty if missing (SQLite takes
/// an empty file as a new database), locked down and checked if it exists.
/// SQLite gives `-wal` / `-shm` the main file's permissions; they are
/// locked down too when present ([`restrict_db_sidecars`]).
pub fn private_db_file(p: &Path) -> std::io::Result<()> {
    if !p.exists() {
        let mut o = OpenOptions::new();
        o.write(true).create_new(true);
        imp::private_file_options(&mut o);
        match o.open(p) {
            Ok(f) => drop(f),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(e),
        }
    }
    imp::restrict_file(p)
}

/// `-wal` / `-shm` next to the database, when present: owner only.
pub fn restrict_db_sidecars(db: &Path) -> std::io::Result<()> {
    for ext in ["-wal", "-shm"] {
        let mut s = db.as_os_str().to_owned();
        s.push(ext);
        let p = PathBuf::from(s);
        if p.exists() {
            imp::restrict_file(&p)?;
        }
    }
    Ok(())
}

/// Physical memory in bytes (the default session limit, ADR 0005 §5); `None` when it
/// cannot be read. sysinfo reads it the platform's own way (macOS `hw.memsize`, Windows
/// `GlobalMemoryStatusEx`, Linux `/proc/meminfo`).
pub fn physical_memory() -> Option<u64> {
    // sysinfo 0.40: `System::new` can fail; no reading = no memory-based default
    let mut s = sysinfo::System::new().ok()?;
    s.refresh_memory();
    Some(s.total_memory()).filter(|m| *m > 0)
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

/// Open `url` in the person's default browser (`aibd ctl web`, PLAN A5). Best effort: the
/// caller prints the address anyway. The url carries nothing secret (ADR 0006 §7: the
/// pairing code is typed in, never put in a link).
pub fn open_in_browser(url: &str) -> std::io::Result<()> {
    #[cfg(target_os = "macos")]
    let mut c = std::process::Command::new("open");
    #[cfg(windows)]
    let mut c = {
        let mut c = std::process::Command::new("rundll32");
        c.arg("url.dll,FileProtocolHandler");
        c
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut c = std::process::Command::new("xdg-open");
    let st = c
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()?;
    if st.success() {
        Ok(())
    } else {
        Err(std::io::Error::other(format!("browser opener: {st}")))
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
        // Another test in this process may be starting a child at this moment: between its
        // fork and exec the child holds a copy of every descriptor, `a`'s included, so the
        // lock outlives `drop(a)` for a few milliseconds (seen once in 20 lib runs on the
        // Pro 13, #640 receipt 11-11). The lock must come free, not instantly: when it is
        // still held, a blocking lock on another handle waits until the child lets go (no
        // polling), then the instance lock is taken again.
        let (tx, rx) = std::sync::mpsc::channel();
        let q = p.clone();
        std::thread::spawn(move || {
            for _ in 0..10 {
                if let Some(f) = instance_lock(&q).unwrap() {
                    let _ = tx.send(f);
                    return;
                }
                let w = OpenOptions::new().write(true).open(&q).unwrap();
                w.lock().unwrap();
                drop(w);
            }
        });
        // One wake-up for the answer, or at the deadline: not a periodic timer
        // (ADR 0005 named exception, as `sessions::wait_timeout`).
        #[allow(clippy::disallowed_methods)]
        let again = rx.recv_timeout(std::time::Duration::from_secs(10));
        assert!(again.is_ok(), "the lock never came free after drop");
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
