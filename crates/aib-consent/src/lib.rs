// SPDX-License-Identifier: Apache-2.0
//! The Rust half of `aib consent` (plan PLAN-consent-run-params-ebb36-1111, Andy 11-11:
//! "可以彈出提示，同意後關閉"). The Python half keeps the allow-list, the dialogs, Touch ID
//! and launchd; this half:
//!
//! - [`params`]: checks a reviewed shared program against the sha256 it is pinned to, the
//!   parameters it declares, and a strict parameter file; stages copies of both. Right
//!   before the run both copies are checked against their sha256 again and those checked
//!   bytes are what is used: the parameters go into the environment, the program is fed
//!   to its interpreter through a pipe (`/dev/fd/<n>`), never re-read from a path;
//! - [`request`]: what a request may look like (B2 T136-1): the allow-list with the 1.x
//!   refusal words, the limits, and the request file reader / writer;
//! - [`queue`]: the inbox → running → done folders (B2 T136-2): the 1.x claim (shared
//!   Touch ID lock + rename), the orphan sweep and the done record;
//! - [`supervise`]: runs an approved job in its own process group, writes its output and a
//!   small state file as it goes, asks the person whether to stop it when it is quiet or
//!   long, and stops its whole group only on "yes" or at the hard limit.
//!
//! This is a consent interface, not a defence against an AI running as the same user (see
//! the same-user threat guide): such an AI can kill a process or edit these files itself.
//! What it gives is that every run is watched and every stop goes past the person.

//!
//! Unix only (process groups, signals, `/dev/fd`): on other platforms the library is empty
//! and `aib-consent-run` answers `unsupported_platform` (#661 broke the Windows build).
#![cfg(unix)]

pub mod params;
pub mod queue;
pub mod request;
pub mod supervise;
