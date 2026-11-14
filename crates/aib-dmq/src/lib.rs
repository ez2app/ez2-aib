//! The dm queue's pure core (B1 S1, PLAN-b1-dm-rust r4).
//!
//! Everything here is a function of its arguments: no files, no processes, no clock, no
//! `cfg(unix)`. The caller reads the item, checks evidence on disk / in git / in another
//! seat's queue (through [`evidence::Probe`]), measures who is calling, takes the locks in
//! the order [`lock`] allows, and writes the result back. S2 adds those locks and the
//! atomic writes; S3 to S5 move the subcommands over one by one.
//!
//! The reference behaviour is the frozen 1.x Python queue. `aib/tests/fixtures/dm-spec/
//! transitions-v1.json` (7910 cells, made from that Python) is the contract this crate is
//! tested against cell by cell; the rules that fixture does not reach (commit and reply-id
//! evidence, embedded evidence, the rev parser) have their own tests in this crate.

pub mod actor;
pub mod evidence;
pub mod lock;
pub mod norm;
pub mod rev;
pub mod spec;
pub mod transition;
pub mod util;

pub use actor::{parse_actor, Actor, ActorClass};
pub use spec::{Reason, Status};
pub use transition::{decide, Decision, Outcome, Receipt, ReceiptState, Request};
