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
//!
//! No OS calls and no unsafe code. The macOS side is the Swift helper; Windows
//! Hello / TPM go into aib-sys (DECISION-b2-windows-native, W-a).

pub mod category;
pub mod proof;
pub mod protocol;
pub mod text;
