//! Release / merge tooling (a63ab plan v3, section 9.2). First piece: S2
//! cross-PR yield ([`yield_integ`]); A8 / A9 trial merge ([`trial`]); DEV 4.6
//! P1 host build ancestry ([`host_build`]).
//!
//! GitHub is the only source of truth: every decision is made from what the
//! API answers right now, and anything that cannot be read is UNKNOWN, never
//! "nothing to do". Local files prove nothing here.

pub mod gh;
pub mod host_build;
pub mod trial;
pub mod yield_integ;
