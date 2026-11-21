//! Per-seat locks, checked against the order in `aib_dmq::lock` for the calling thread.

use std::cell::RefCell;
use std::fs::File;
use std::marker::PhantomData;
use std::path::{Path, PathBuf};
use std::time::Duration;

use aib_dmq::lock::{Enter, Held, Level, OrderError, DELIVER_LOCK, TRANSITION_LOCK};

use crate::fsx::{lock_ex, LockWait, SeatDir};

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
/// does not hold it further out). Drop guards innermost first, on the thread that took
/// them: the order stack is per thread, so the guard is not `Send`.
pub struct SeatLock {
    seat: String,
    level: Level,
    file: Option<File>,
    _same_thread: PhantomData<*const ()>,
}

impl Drop for SeatLock {
    fn drop(&mut self) {
        // try_with: a guard dropped while the thread is ending still releases the real lock
        let last = HELD
            .try_with(|h| h.borrow_mut().exit(&self.seat, self.level))
            .unwrap_or(true);
        // a guard that took the real lock must be the innermost one left
        debug_assert!(
            last || self.file.is_none() || std::thread::panicking(),
            "SeatLock dropped out of order"
        );
        if last {
            if let Some(f) = self.file.take() {
                let _ = f.unlock();
            }
        }
    }
}

fn open_target(dir: &SeatDir, level: Level) -> std::io::Result<File> {
    match level {
        Level::Deliver => dir.open_private(DELIVER_LOCK),
        Level::Transition => dir.open_private(TRANSITION_LOCK),
        Level::Directory => dir.lock_handle(DIR_LOCK_FILE),
    }
}

/// Take `level` on `seat` (whose folder is `dir`), waiting up to `timeout`. The folder is
/// opened here without following a link; callers holding a [`SeatDir`] use [`lock_in`].
pub fn lock(
    dir: &Path,
    seat: &str,
    level: Level,
    timeout: Duration,
) -> Result<SeatLock, LockError> {
    let d = SeatDir::at(dir, false).map_err(|e| LockError::Failed {
        path: dir.to_path_buf(),
        why: e.to_string(),
    })?;
    lock_in(&d, seat, level, timeout)
}

/// [`lock`] in a seat folder already held open.
pub fn lock_in(
    dir: &SeatDir,
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
        _same_thread: PhantomData,
    };
    if enter == Enter::Reentered {
        return Ok(g);
    }
    // from here on, dropping `g` leaves the order stack again
    let path = dir.path().to_path_buf();
    let f = open_target(dir, level).map_err(|e| LockError::Failed {
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
