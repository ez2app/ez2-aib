//! AIB 2.0 core: pure domain types, state machines, error codes and port traits.
//!
//! Rules for this crate (checked in CI by `scripts/check-core-purity.sh`):
//! no I/O, no async runtime, no process spawning, no `cfg(target_os)`.
//! Platform differences live behind [`ports`] and are implemented in `aibd`.

pub mod error;
pub mod ids;
pub mod ports;
pub mod state;

pub use error::{ErrorCode, ErrorCodeParseError};
