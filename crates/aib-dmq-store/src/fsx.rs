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

/// One seat folder, held open (5d33a PREREAD-g5-bridge L1): on Unix the folder is opened once
/// from its parent with `O_DIRECTORY | O_NOFOLLOW`, and every file in it is reached from that
/// descriptor (`openat` with `O_NOFOLLOW`, an `O_EXCL` scratch file, `renameat` in the same
/// folder), so the seat folder changed into a link after it was opened cannot move a write.
/// Windows has no `openat`: the folder is looked at once (not a link) and used by path; no
/// Python writer and no 1.x ledger run there.
pub struct SeatDir {
    path: std::path::PathBuf,
    #[cfg(unix)]
    fd: std::os::fd::OwnedFd,
}

impl std::fmt::Debug for SeatDir {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SeatDir").field("path", &self.path).finish()
    }
}

#[cfg(unix)]
mod at {
    use nix::fcntl::OFlag;
    use nix::sys::stat::Mode;

    pub fn dir_flags() -> OFlag {
        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC
    }

    /// No link followed, and a named pipe planted under a ledger name does not block.
    pub fn file_flags(f: OFlag) -> OFlag {
        f | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC | OFlag::O_NONBLOCK
    }

    pub fn private() -> Mode {
        Mode::S_IRUSR | Mode::S_IWUSR
    }
}

impl SeatDir {
    /// `name` in `parent` as a folder, not through a link; made (0700) first when `make`.
    /// `NotFound` when it is not there and `make` is false.
    pub fn open(parent: &Path, name: &str, make: bool) -> io::Result<SeatDir> {
        let path = parent.join(name);
        #[cfg(unix)]
        {
            use nix::errno::Errno;
            use nix::fcntl::{open, openat, OFlag};
            use nix::sys::stat::{mkdirat, Mode};
            if make {
                std::fs::create_dir_all(parent)?;
            }
            let p = open(
                parent,
                OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC,
                Mode::empty(),
            )?;
            let fd = match openat(&p, name, at::dir_flags(), Mode::empty()) {
                Err(Errno::ENOENT) if make => {
                    match mkdirat(&p, name, Mode::S_IRWXU) {
                        Ok(()) | Err(Errno::EEXIST) => {}
                        Err(e) => return Err(e.into()),
                    }
                    openat(&p, name, at::dir_flags(), Mode::empty())?
                }
                r => r?,
            };
            Ok(SeatDir { path, fd })
        }
        #[cfg(not(unix))]
        {
            if make {
                std::fs::create_dir_all(&path)?;
            }
            let m = std::fs::symlink_metadata(&path)?;
            if m.file_type().is_symlink() || !m.is_dir() {
                return Err(io::Error::other("seat folder is not a plain folder"));
            }
            Ok(SeatDir { path })
        }
    }

    /// The folder `path` itself (its parent and last part).
    pub fn at(path: &Path, make: bool) -> io::Result<SeatDir> {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no folder name"))?;
        SeatDir::open(path.parent().unwrap_or(Path::new(".")), name, make)
    }

    /// Only this user may enter it (0700, like dm_queue.py's chmod; through the open folder,
    /// so a link swapped in does not get the change). Best effort; Windows keeps its ACL.
    pub fn make_private(&self) {
        #[cfg(unix)]
        {
            let _ = nix::sys::stat::fchmod(&self.fd, nix::sys::stat::Mode::S_IRWXU);
        }
    }

    /// Where it was opened (for messages and Windows; Unix work never goes through it).
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// `name` in the folder as text; `Ok(None)` when it is not there. A link is refused.
    pub fn read_to_string(&self, name: &str) -> io::Result<Option<String>> {
        use std::io::Read;
        let f = match self.open_file(name, false, false) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            r => r?,
        };
        let mut s = String::new();
        f.take(4 * MAX_RECORD_BYTES as u64).read_to_string(&mut s)?;
        Ok(Some(s))
    }

    /// `name` opened read-write, made (0600) when missing, not through a link: a lock file.
    pub fn open_private(&self, name: &str) -> io::Result<File> {
        self.open_file(name, true, false)
    }

    /// `name` opened to append, made (0600) when missing, not through a link: audit.jsonl.
    pub fn append(&self, name: &str) -> io::Result<File> {
        self.open_file(name, true, true)
    }

    #[cfg(unix)]
    fn open_file(&self, name: &str, create: bool, append: bool) -> io::Result<File> {
        use nix::fcntl::{openat, OFlag};
        let mut f = if append {
            OFlag::O_WRONLY | OFlag::O_APPEND
        } else if create {
            OFlag::O_RDWR
        } else {
            OFlag::O_RDONLY
        };
        if create {
            f |= OFlag::O_CREAT;
        }
        let fd = openat(&self.fd, name, at::file_flags(f), at::private())?;
        let file = File::from(fd);
        if !file.metadata()?.is_file() {
            return Err(io::Error::other("not a plain file"));
        }
        Ok(file)
    }

    #[cfg(not(unix))]
    fn open_file(&self, name: &str, create: bool, append: bool) -> io::Result<File> {
        let p = self.path.join(name);
        if append {
            OpenOptions::new().create(true).append(true).open(p)
        } else if create {
            create_private(&p, false)
        } else {
            File::open(p)
        }
    }

    /// The folder lock's handle: on Unix the folder itself opened again (a new open file, so
    /// its flock is its own; the very lock Python atomic_json takes), on Windows a fixed
    /// file in it.
    pub fn lock_handle(&self, windows_file: &str) -> io::Result<File> {
        #[cfg(unix)]
        {
            let _ = windows_file;
            use nix::fcntl::{openat, OFlag};
            use nix::sys::stat::Mode;
            let fd = openat(
                &self.fd,
                ".",
                OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC,
                Mode::empty(),
            )?;
            Ok(File::from(fd))
        }
        #[cfg(not(unix))]
        {
            create_private(&self.path.join(windows_file), false)
        }
    }

    /// Remove `name`; already gone is fine.
    pub fn remove(&self, name: &str) -> io::Result<()> {
        #[cfg(unix)]
        let r = nix::unistd::unlinkat(&self.fd, name, nix::unistd::UnlinkatFlags::NoRemoveDir)
            .map_err(io::Error::from);
        #[cfg(not(unix))]
        let r = std::fs::remove_file(self.path.join(name));
        match r {
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
            r => r,
        }
    }

    /// [`replace`] for `name` in this folder: an `O_EXCL` scratch entry renamed over it in the
    /// same folder. Err = nothing changed (the scratch entry is removed).
    pub fn replace(&self, name: &str, payload: &[u8]) -> io::Result<Replace> {
        #[cfg(not(unix))]
        {
            replace(&self.path.join(name), payload)
        }
        #[cfg(unix)]
        {
            use nix::errno::Errno;
            use nix::fcntl::{openat, renameat, OFlag};
            if payload.len() > MAX_RECORD_BYTES {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "record over 64 KiB",
                ));
            }
            let (tmp, fd) = loop {
                let tmp = format!(".{name}.tmp.{}.{}", std::process::id(), rand_hex(6));
                let f = at::file_flags(OFlag::O_WRONLY | OFlag::O_CREAT | OFlag::O_EXCL);
                match openat(&self.fd, tmp.as_str(), f, at::private()) {
                    Ok(fd) => break (tmp, fd),
                    Err(Errno::EEXIST) => continue,
                    Err(e) => return Err(e.into()),
                }
            };
            let before = (|| {
                let mut f = File::from(fd);
                f.write_all(payload)?;
                f.sync_all()?;
                drop(f);
                renameat(&self.fd, tmp.as_str(), &self.fd, name).map_err(io::Error::from)
            })();
            if let Err(e) = before {
                let _ = self.remove(&tmp);
                return Err(e);
            }
            Ok(match self.lock_handle("").and_then(|d| d.sync_all()) {
                Ok(()) => Replace::Durable,
                Err(e) => Replace::NotSynced(e.to_string()),
            })
        }
    }
}
