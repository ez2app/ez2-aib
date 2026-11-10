// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud` — the command surface of Ez2 Managed Cloud (4.4.1 §12).
//!
//! M-R1 (this crate's first slice) is **pure planning logic**: it validates
//! operator input, builds a [`robot::Plan`] whose approval facts, URL path and
//! form body all come from the same validated values, and masks secrets out of
//! API replies. It performs no I/O: no network, no secret store, no process
//! spawning. Transports, the secret source and the human-approval gate are
//! ports to be implemented outside this crate (ADR draft, §2), so the rules
//! here can be tested without touching a host.
//!
//! The Python PoC (`aib/tools/cloud-host/robot.py`, #528) is the acceptance
//! baseline: every rule it tests (R1–R10) has a matching test in [`robot`].

//! A [`robot::Plan`] cannot be forged or edited from outside (#532 M1); both
//! of these fail to compile:
//!
//! ```compile_fail
//! use aib_cloud::robot::{Action, Mutation, Plan, ServerNumber};
//! let _forged = Plan { action: Action::Rescue, server: ServerNumber::parse("1").unwrap(),
//!     fingerprints: vec![], method: "POST", path: "/x".into(), form: vec![],
//!     mutation: Mutation::ReadOnly, recovery: "" };
//! ```
//!
//! ```compile_fail
//! use aib_cloud::robot::{Plan, ResetType, ServerNumber};
//! let mut p = Plan::reset(ServerNumber::parse("1234567").unwrap(), ResetType::Hw);
//! p.path = "/reset/7654321".to_owned();
//! ```

// #532 r3 M1 (ebb36): "no I/O" is enforced by the compiler, not by a word list. Without
// `std` there is no network, file system, process, environment or stdin to reach for;
// `alloc` gives String / Vec / format!. tests/boundary.rs only keeps the few text checks
// the compiler cannot make: no way back to std, nothing pulled in from outside src/ at
// compile time (path attributes, file inclusion, build-time environment), no build script.
#![no_std]
#[macro_use]
extern crate alloc;
#[cfg(test)]
extern crate std;
pub mod isolation;
pub mod provision;
pub mod r2token;
pub mod robot;
pub mod site;
pub mod verify;
