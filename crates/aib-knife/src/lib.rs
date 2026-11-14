//! aib-knife — knife tests in Rust (replaces the 1.x `scripts/knife.py`, #525; RULES §9).
//!
//! A knife breaks one line on purpose, runs a test command, and restores the file. It
//! proves a test *bites*: the named test must go red for that change. Every knife ends in
//! exactly one [`outcome::Outcome`]; only `Bit` is good.
//!
//! This first part is pure (no files, no processes), so it is tested on every OS:
//! - [`spec`]: read and check a knife spec (JSON, same fields as knife.py);
//! - [`names`]: failed / passed test names from cargo, unittest and pytest output, and the
//!   name match rule;
//! - [`anchor`]: the anchor must occur exactly once; apply and check the restore;
//! - [`outcome`]: the verdict for one knife, the noop verdict, and the exit code.
//!
//! Running commands comes next, through aibd's `platform::session` (a63ab 11-09 2.2 ruling
//! A): aib-knife never calls OS process APIs itself (ADR 0004).
//!
//! Known limit, stated on purpose: aib-knife trusts the command's output. A program that
//! prints `test x ... FAILED` and exits 1 can fake a `Bit`. Knives measure whether tests
//! bite; they do not defend against a hostile test (Codex review, design §7.3).

pub mod anchor;
pub mod names;
pub mod outcome;
pub mod spec;
