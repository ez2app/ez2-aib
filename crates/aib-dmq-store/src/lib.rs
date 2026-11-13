//! The dm queue on disk (B1 S2, PLAN-b1-dm-rust r4).
//!
//! Layout (same as 1.x): `<state>/state/dm-queue/<seat>/` holds `<id>.json` items,
//! `order.json`, `rev`, `audit.jsonl`, `view.json` and the lock files.
//!
//! Locks, in the order `aib_dmq::lock` allows (deliver → transition → folder, one seat at
//! a time, the transition lock re-entrant on one thread):
//! - transition: `transition.lock` in the seat folder, both platforms.
//! - folder: Unix locks the folder itself (flock on its descriptor), the very lock the
//!   frozen Python `atomic_json` takes, so Rust and Python writers exclude each other.
//!   Windows cannot lock a folder handle portably; it locks `.dir.lock` in the folder,
//!   a file that is created once and never replaced or deleted. No Python writer runs on
//!   Windows, so nothing there takes the other lock.
//!
//! Every read-modify-write happens inside the folder lock (1.x reads some files outside
//! it; the Rust side does not carry that over).
//!
//! Writes: temp file (create_new, random name) → write → sync → rename over the target →
//! sync the folder (Unix). Once the rename is done the change is in place: a failure after
//! it is reported as [`Written::NotSynced`], never as "nothing changed".

pub mod fsx;
pub mod locks;
pub mod store;

pub use locks::{LockError, SeatLock};
pub use store::{ItemRead, Store, StoreError, Written};
