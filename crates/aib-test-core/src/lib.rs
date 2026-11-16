//! `aib test` 2.0 core (S69-u1). Specs: DEV-shadow-2x-519d0-1116-r7.md §1.2 / §1.3 and
//! SPEC-shadow-u0-key-exit-043dd-1116.md r2 (the key, the skip reasons, the exit codes).
//!
//! This crate knows no test framework, no knife and no GitHub: the cargo adapter and the
//! knife shell turn their things into units before they get here.

pub mod exit;
pub mod key;
pub mod skip;
