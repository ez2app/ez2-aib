//! aib-reflect: every seat leaves a reflection when it finishes something; a
//! suggestion scored at or above the threshold, reviewed blind by a second
//! party and outside every forbidden area may be picked up without waiting for
//! Andy (DECISION-aib-reflect-a63ab-1112). Pure library: no aib-drive, no
//! aib-expect (DECISION §1 #12); drive passes its snapshot in.
//!
//! R4 (043dd): [`gate`] decides, nothing here schedules or writes.

pub mod gate;
