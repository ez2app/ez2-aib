//! Human approval for AIB 2.0, policy half (B2 T135-a,
//! PLAN-b2-approval-rust-ebb36-1113.md).
//!
//! Same-user threat model (GUIDE-same-user-ai-threat-model): an AI running as the
//! person's own account can edit files, environment variables and this binary. The
//! only things here that it cannot fake are a fresh owner check it did not press
//! and a signature by a hardware key it cannot use without the owner. Everything
//! else in this crate (tables, limits, the nonce ledger) keeps the core from asking
//! for the wrong thing by mistake; it is not a defence on its own.
//!
//! - [`category`]: the category table and which proof each needs.
//! - [`text`]: what may be shown in an approval window.
//! - [`proof`]: the binding a proof covers and the checks.
//! - [`protocol`]: the native helper's exit codes and the 1.x result words.
//! - [`hwkey`]: hardware key files, making / loading a key, signing a binding (every
//!   signature is checked against the registered key before it becomes a proof).
//! - [`registry`]: the trusted-key registry, every entry signed by an owner-only root
//!   that is probed at every load; its fingerprint goes into every window.
//! - `macos` (macOS only): the Secure Enclave device and registered-key check, through
//!   aib-sys `presence_macos`.
//!
//! No unsafe code here. OS calls go through aib-sys (DECISION-b2-windows-native W-a:
//! Secure Enclave / LocalAuthentication on macOS, Windows Hello / TPM later); the 1.x
//! Swift helper is frozen.

pub mod category;
pub mod hwkey;
#[cfg(target_os = "macos")]
pub mod macos;
pub mod proof;
pub mod protocol;
pub mod registry;
pub mod text;
