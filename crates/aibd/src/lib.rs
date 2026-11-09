//! aibd: the AIB 2.0 local daemon (Phase 0 skeleton).
//!
//! Security rules (ADR 0003): bind loopback only; check `Host` and `Origin` on
//! every request; accept the startup token only as `Authorization: Bearer`;
//! reject tokens in URLs; send no CORS headers; never log headers or queries.

pub mod allowlist;
pub mod api;
pub mod config;
pub mod guard;
pub mod lineage;
pub mod platform;
pub mod runtime;
pub mod selfcheck;
pub mod server;
pub mod sessions;
pub mod timers;
pub mod token;
