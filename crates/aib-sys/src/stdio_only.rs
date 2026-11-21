//! A child that inherits only stdin / stdout / stderr (519d0 11-20, #867 macOS cell: a
//! preview aibd saw fds 131 140 143 146 next to 0 1 2). Rust opens every descriptor it makes
//! close-on-exec, but one this process was given without the flag (by whoever started it) or
//! one a C library opened would reach the child. The hook marks every descriptor above 2
//! close-on-exec in the forked child just before `exec`, so the child starts with stdio only.

use std::os::unix::process::CommandExt;
use std::process::Command;

/// Makes `cmd`'s child start with only descriptors 0, 1 and 2 (whatever `cmd` set them to).
pub fn stdio_only(cmd: &mut Command) {
    // SAFETY: the hook runs in the forked child before `exec`; `mark_rest` makes only
    // `getdtablesize` and `fcntl` calls, which are async-signal-safe and allocate nothing.
    unsafe { cmd.pre_exec(mark_rest) };
}

fn mark_rest() -> std::io::Result<()> {
    // SAFETY: no arguments; returns this process's descriptor table size.
    let top = unsafe { libc::getdtablesize() };
    for fd in 3..top {
        // SAFETY: F_SETFD on a number that may not be open: an unused one returns EBADF,
        // which is ignored; no pointers.
        let _ = unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) };
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::os::fd::AsRawFd;

    #[test]
    fn a_descriptor_opened_without_cloexec_does_not_reach_the_child() {
        let d = std::env::temp_dir().join(format!("aib-sys-stdio-only-{}", std::process::id()));
        let f = std::fs::File::create(&d).unwrap();
        // a copy at a number far above what `ls /dev/fd` opens for itself: with stdio only, ls
        // gets the lowest free number for the folder it lists, and when that was this file's
        // own number the file read as leaked (f1fae 11-21, lin f1fae-rkb2-84a69226b:
        // fd_reached_child on the integration head). F_DUPFD gives a descriptor without
        // close-on-exec, which is the kind under test.
        // SAFETY: duplicates a descriptor this test owns to a number of 200 or more; no pointers.
        let fd = unsafe { libc::fcntl(f.as_raw_fd(), libc::F_DUPFD, 200) };
        assert!(fd >= 200, "reason: no_high_descriptor {fd}");
        let listed = |only: bool| {
            let mut c = Command::new("/bin/ls");
            c.arg("/dev/fd");
            if only {
                stdio_only(&mut c);
            }
            let out = c.output().unwrap();
            String::from_utf8_lossy(&out.stdout)
                .split_whitespace()
                .any(|n| n == fd.to_string())
        };
        assert!(listed(false), "reason: test_fd_not_inherited_at_all");
        assert!(!listed(true), "reason: fd_reached_child");
        // SAFETY: closes the copy this test made; no pointers.
        let _ = unsafe { libc::close(fd) };
        drop(f);
        let _ = std::fs::remove_file(d);
    }
}
