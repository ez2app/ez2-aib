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

pub mod robot;
