//! The lock order, as types (S1 fixes the order; S2 fills in the real locks).
//!
//! Within one seat: deliver → transition → directory, never backwards. 1.x takes the
//! transition lock again on the same thread (a receipt path calling back into a
//! transition) by counting depth; [`Held`] does the same, so a nested call knows it must
//! not lock again.
//!
//! Across seats (PLAN r4, Codex [H] on S5): a caller holding any lock of seat A may not
//! take a lock of seat B. Work that touches two seats (record, then close the original in
//! the sender's queue) finishes and releases the first seat, then locks the second and
//! re-checks there. 1.x nests them; the Rust side does not.

use std::fmt;

/// File names in the seat directory. The directory itself is the third lock (on Unix the
/// directory fd, shared with the Python writer; S2 decides the Windows equivalent).
pub const DELIVER_LOCK: &str = "deliver.lock";
pub const TRANSITION_LOCK: &str = "transition.lock";
pub const SOURCE_REV_LOCK: &str = "rev.lock";
/// 1.x waits this long for the transition lock, then refuses with `lock_timeout`.
pub const TRANSITION_LOCK_TIMEOUT_MS: u64 = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Deliver = 0,
    Transition = 1,
    Directory = 2,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum OrderError {
    /// A lower level after a higher one on the same seat.
    Backwards {
        seat: String,
        held: Level,
        want: Level,
    },
    /// A lock of another seat while one is held.
    CrossSeat { held: String, want: String },
    /// Only the transition lock may be re-entered.
    NotReentrant { seat: String, level: Level },
}

impl fmt::Display for OrderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OrderError::Backwards { seat, held, want } => {
                write!(
                    f,
                    "lock order: seat {seat} holds {held:?}, may not take {want:?}"
                )
            }
            OrderError::CrossSeat { held, want } => {
                write!(
                    f,
                    "lock order: holding seat {held}, may not lock seat {want}; release first"
                )
            }
            OrderError::NotReentrant { seat, level } => {
                write!(f, "lock order: {level:?} on seat {seat} is held")
            }
        }
    }
}

/// What the caller has to do after [`Held::enter`] said yes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Enter {
    /// Take the real lock now.
    Acquire,
    /// Already held on this thread: do not lock again, only count.
    Reentered,
}

/// The locks one thread holds, innermost last.
#[derive(Clone, Debug, Default)]
pub struct Held {
    stack: Vec<(String, Level, u32)>,
}

impl Held {
    pub fn new() -> Held {
        Held::default()
    }

    pub fn is_empty(&self) -> bool {
        self.stack.is_empty()
    }

    pub fn holds(&self, seat: &str, level: Level) -> bool {
        self.stack.iter().any(|(s, l, _)| s == seat && *l == level)
    }

    /// May this thread take `level` on `seat` now?
    pub fn enter(&mut self, seat: &str, level: Level) -> Result<Enter, OrderError> {
        if let Some(top) = self.stack.last_mut() {
            if top.0 != seat {
                return Err(OrderError::CrossSeat {
                    held: top.0.clone(),
                    want: seat.to_string(),
                });
            }
            if top.1 == level {
                if level != Level::Transition {
                    return Err(OrderError::NotReentrant {
                        seat: seat.to_string(),
                        level,
                    });
                }
                top.2 += 1;
                return Ok(Enter::Reentered);
            }
            if top.1 > level {
                return Err(OrderError::Backwards {
                    seat: seat.to_string(),
                    held: top.1,
                    want: level,
                });
            }
        }
        self.stack.push((seat.to_string(), level, 1));
        Ok(Enter::Acquire)
    }

    /// Leave the innermost lock. True = the real lock is released now (depth hit 0).
    /// Leaving anything but the innermost is a bug in the caller: false, nothing changes.
    pub fn exit(&mut self, seat: &str, level: Level) -> bool {
        match self.stack.last_mut() {
            Some(top) if top.0 == seat && top.1 == level => {
                top.2 -= 1;
                if top.2 == 0 {
                    self.stack.pop();
                    true
                } else {
                    false
                }
            }
            _ => false,
        }
    }
}
