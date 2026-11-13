//! Per-seat locks, checked against the order in `aib_dmq::lock` for the calling thread.

use std::cell::RefCell;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::time::Duration;

use aib_dmq::lock::{Enter, Held, Level, OrderError, DELIVER_LOCK, TRANSITION_LOCK};

use crate::fsx::{create_private, lock_ex, LockWait};

/// The Windows stand-in for locking the seat folder: created once, never replaced or
/// deleted (deleting it while held would let a second writer lock a new file).
pub const DIR_LOCK_FILE: &str = ".dir.lock";
/// atomic_json.LOCK_TIMEOUT_S.
pub const DIR_LOCK_TIMEOUT: Duration = Duration::from_secs(10);

thread_local! {
    static HELD: RefCell<Held> = RefCell::new(Held::new());
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LockError {
    /// Waited the whole timeout: another program holds it. Nothing was changed.
    Timeout {
        seat: String,
        level: Level,
    },
    Failed {
        path: PathBuf,
        why: String,
    },
    /// A bug in the caller: the lock order was broken. Nothing was locked.
    Order(OrderError),
}

impl std::fmt::Display for LockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LockError::Timeout { seat, level } => {
                write!(f, "{level:?} lock of seat {seat} timed out")
            }
            LockError::Failed { path, why } => write!(f, "lock {}: {why}", path.display()),
            LockError::Order(e) => write!(f, "{e}"),
        }
    }
}

/// A held lock. Dropping it leaves the lock (and releases the real one when this thread
/// does not hold it further out).
pub struct SeatLock {
    seat: String,
    level: Level,
    file: Option<File>,
}

impl Drop for SeatLock {
    fn drop(&mut self) {
        let last = HELD.with(|h| h.borrow_mut().exit(&self.seat, self.level));
        if last {
            if let Some(f) = self.file.take() {
                let _ = f.unlock();
            }
        }
    }
}

fn target(dir: &Path, level: Level) -> PathBuf {
    match level {
        Level::Deliver => dir.join(DELIVER_LOCK),
        Level::Transition => dir.join(TRANSITION_LOCK),
        Level::Directory if cfg!(unix) => dir.to_path_buf(),
        Level::Directory => dir.join(DIR_LOCK_FILE),
    }
}

fn open_target(path: &Path, level: Level) -> std::io::Result<File> {
    if level == Level::Directory && cfg!(unix) {
        File::open(path)
    } else {
        create_private(path, false)
    }
}

/// Take `level` on `seat` (whose folder is `dir`), waiting up to `timeout`.
pub fn lock(
    dir: &Path,
    seat: &str,
    level: Level,
    timeout: Duration,
) -> Result<SeatLock, LockError> {
    let enter = HELD
        .with(|h| h.borrow_mut().enter(seat, level))
        .map_err(LockError::Order)?;
    let mut g = SeatLock {
        seat: seat.to_string(),
        level,
        file: None,
    };
    if enter == Enter::Reentered {
        return Ok(g);
    }
    // from here on, dropping `g` leaves the order stack again
    let path = target(dir, level);
    let f = open_target(&path, level).map_err(|e| LockError::Failed {
        path: path.clone(),
        why: e.to_string(),
    })?;
    match lock_ex(&f, timeout) {
        Ok(()) => {
            g.file = Some(f);
            Ok(g)
        }
        Err(LockWait::Timeout) => Err(LockError::Timeout {
            seat: seat.to_string(),
            level,
        }),
        Err(LockWait::Failed(why)) => Err(LockError::Failed { path, why }),
    }
}

/// This thread holds `level` on `seat` (for asserts in callers that need it held).
pub fn holds(seat: &str, level: Level) -> bool {
    HELD.with(|h| h.borrow().holds(seat, level))
}
