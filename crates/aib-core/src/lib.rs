//! AIB 2.0 core: pure domain types, state machines, error codes and port traits.
//!
//! Rules for this crate, enforced by the compiler where possible:
//! - `#![no_std]`: `std::fs`, `std::process`, `std::net`, `std::env`, threads
//!   and clocks do not exist here, however they are imported (aliases, grouped
//!   `use`, `extern crate std` outside tests are all compile errors / caught by
//!   `scripts/check-core-purity.sh`).
//! - `#![forbid(unsafe_code)]`: no FFI back door.
//! - Dependencies: an exact allow-list checked from `cargo metadata`.
//! - No platform switches (`cfg(target_os|unix|windows)`): checked by the script.
//!
//! Platform differences live behind [`ports`] and are implemented in `aibd`.

#![no_std]
#![forbid(unsafe_code)]

extern crate alloc;
#[cfg(test)]
extern crate std;

pub mod error;
pub mod ids;
pub mod ports;
pub mod state;

pub use error::{ErrorCode, ErrorCodeParseError};
