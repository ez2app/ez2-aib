//! B1 (519d0 11-15 22:13, Andy agreed 22:1x): a child that leaves the darwin background tier.
//!
//! aibd runs as a launchd `ProcessType = Background` job, and every process it starts inherits
//! the background tier: CPU on the efficiency cores and throttled IO. Measured on 11-15: the
//! same 40 `git cherry` calls took 2.7 s in the foreground and 9.1 s in the background, so
//! aib-slice's scan started from aibd ran past aib-drive's 10-minute limit while the same
//! scan from a shell took under 5 minutes. aibd itself stays a background job; only the
//! children it starts for real work leave the tier.
//!
//! The call is `setpriority(PRIO_DARWIN_PROCESS, 0, 0)` — public (`sys/resource.h`), on the
//! process itself, no privilege needed — made in the forked child before `exec`, so every
//! process the child starts in turn is out of the tier too.

use std::os::unix::process::CommandExt;
use std::process::Command;

/// Makes `cmd`'s child leave the darwin background tier before it execs. A child that
/// cannot leave it still runs, only as slowly as before B1.
pub fn foreground_child(cmd: &mut Command) {
    // SAFETY: the hook runs in the forked child before `exec`; `leave` makes one
    // `setpriority` syscall, which is async-signal-safe and allocates nothing.
    unsafe { cmd.pre_exec(leave) };
}

fn leave() -> std::io::Result<()> {
    // SAFETY: `setpriority` on this process with constant arguments; no pointers.
    let _ = unsafe { libc::setpriority(libc::PRIO_DARWIN_PROCESS, 0, 0) };
    Ok(())
}
