// SPDX-License-Identifier: Apache-2.0
//! `aib-fp` — installing the footprint engine for one shop, pure logic
//! (DESIGN-fp-install-rust-3e53b-1124 r1, slice F1; f163c PREREAD r0).
//!
//! What a person did by hand on 11-24 (nine sign sheets, five steps missed) becomes one
//! list of steps. Every step is read first and only done when it is not yet the way it
//! should be; every write is read back. This crate holds the rules only:
//!
//! - [`request`]: what may be asked for (slug, domain, kind, the reference shop, the
//!   values each shop must give itself);
//! - [`plan`]: the steps and their state, and the plan's hash over the **deciding**
//!   part only (what is done and with which values). What is merely observed (event
//!   counts, cache ages, question counts) is shown but never hashed, so a plan approved a
//!   minute ago is still the same plan (PREREAD M1);
//! - [`judge`]: reading the answers of the outside checks (headers, pages, JSON);
//! - [`cf`]: the Cloudflare cache rule for `/e2/*` (add, never overwrite).
//!
//! No I/O here: `aib-fp-host` (on the server) and `aib fp install` (Mac / Windows) read
//! and write, and hand what they read to these functions.

pub mod cf;
pub mod judge;
pub mod plan;
pub mod request;
