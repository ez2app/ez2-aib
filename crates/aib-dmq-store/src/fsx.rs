//! File primitives that behave the same on Unix and Windows.

use std::collections::hash_map::RandomState;
use std::fs::{File, OpenOptions};
use std::hash::{BuildHasher, Hasher};
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 1.x refuses a record bigger than this (atomic_json.MAX_RECORD_BYTES).
pub const MAX_RECORD_BYTES: usize = 64 * 1024;
/// Windows: a reader or a scanner may hold the target for a moment; rename is retried this
/// many times, 20 ms apart, then the error is returned.
pub const RENAME_TRIES: u32 = 10;

/// ADR 0005 named exception: a short wait inside a one-shot command (a lock retry, a rename
/// retry), never longer than the caller's timeout.
pub fn pause(d: Duration) {
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(d);
}

static SALT: AtomicU64 = AtomicU64::new(0);

/// Hex for a temp name: process id, time and a per-process counter through the std
/// randomly seeded hasher. Uniqueness comes from create_new, not from this.
pub fn rand_hex(bytes: usize) -> String {
    let mut out = String::new();
    while out.len() < bytes * 2 {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        h.write_u128(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        h.write_u64(SALT.fetch_add(1, Ordering::Relaxed));
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out.truncate(bytes * 2);
    out
}

/// Create a file only this user may read (0600 on Unix; Windows keeps the folder's ACL).
pub fn create_private(path: &Path, new: bool) -> io::Result<File> {
    let mut o = OpenOptions::new();
    o.read(true).write(true);
    if new {
        o.create_new(true);
    } else {
        o.create(true).truncate(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    o.open(path)
}

/// rename over an existing target; on Windows retried while another handle blocks it.
pub fn rename_over(from: &Path, to: &Path) -> io::Result<()> {
    let mut n = 0;
    loop {
        match std::fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e)
                if cfg!(windows)
                    && e.kind() == io::ErrorKind::PermissionDenied
                    && n + 1 < RENAME_TRIES =>
            {
                n += 1;
                pause(Duration::from_millis(20));
            }
            Err(e) => return Err(e),
        }
    }
}

/// Flush the folder entry after a rename (Unix). Windows has no folder sync; NTFS
/// journals the rename itself.
pub fn sync_dir(dir: &Path) -> io::Result<()> {
    if cfg!(unix) {
        File::open(dir)?.sync_all()
    } else {
        Ok(())
    }
}

/// How far a replace got.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Replace {
    /// The new content is in place and synced.
    Durable,
    /// The new content is in place, but syncing the folder failed: a crash now could
    /// bring the old file back. Not a failure to report as "nothing changed".
    NotSynced(String),
}

/// Write `payload` to `path` atomically: readers see the old or the new file, never half.
/// Err = nothing changed (the temp file is removed).
pub fn replace(path: &Path, payload: &[u8]) -> io::Result<Replace> {
    if payload.len() > MAX_RECORD_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "record over 64 KiB",
        ));
    }
    let dir = path.parent().unwrap_or(Path::new("."));
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("x");
    let (tmp, mut f) = loop {
        let tmp = dir.join(format!(
            ".{name}.tmp.{}.{}",
            std::process::id(),
            rand_hex(6)
        ));
        match create_private(&tmp, true) {
            Ok(f) => break (tmp, f),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    };
    let before = (|| {
        f.write_all(payload)?;
        f.sync_all()?;
        drop(f);
        rename_over(&tmp, path)
    })();
    if let Err(e) = before {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    Ok(match sync_dir(dir) {
        Ok(()) => Replace::Durable,
        Err(e) => Replace::NotSynced(e.to_string()),
    })
}

/// Why a lock was not taken.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LockWait {
    Timeout,
    Failed(String),
}

/// An exclusive lock on `f`, retried without blocking until `timeout`
/// (`File::try_lock`: flock on Unix, LockFileEx on Windows).
pub fn lock_ex(f: &File, timeout: Duration) -> Result<(), LockWait> {
    let until = Instant::now() + timeout;
    loop {
        match f.try_lock() {
            Ok(()) => return Ok(()),
            Err(std::fs::TryLockError::WouldBlock) => {}
            Err(std::fs::TryLockError::Error(e)) => return Err(LockWait::Failed(e.to_string())),
        }
        if Instant::now() >= until {
            return Err(LockWait::Timeout);
        }
        pause(Duration::from_millis(10));
    }
}
