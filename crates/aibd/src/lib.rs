//! aibd: the AIB 2.0 local daemon (Phase 0 skeleton).
//!
//! Security rules (ADR 0003): bind loopback only; check `Host` and `Origin` on
//! every request; accept the startup token only as `Authorization: Bearer`;
//! reject tokens in URLs; send no CORS headers; never log headers or queries.

pub mod api;
pub mod config;
pub mod guard;
pub mod platform;
pub mod server;
pub mod token;
