//! A child that inherits only stdin / stdout / stderr (519d0 11-20, #867 macOS cell: a
//! preview aibd saw fds 131 140 143 146 next to 0 1 2). Rust opens every descriptor it makes
//! close-on-exec, but one this process was given without the flag (by whoever started it) or
//! one a C library opened would reach the child. The hook marks every descriptor above 2
//! close-on-exec in the forked child just before `exec`, so the child starts with stdio only.

use std::os::fd::{FromRawFd, RawFd};
use std::os::unix::process::CommandExt;
use std::process::Command;

/// Makes `cmd`'s child start with only descriptors 0, 1 and 2 (whatever `cmd` set them to).
pub fn stdio_only(cmd: &mut Command) {
    // SAFETY: the hook runs in the forked child before `exec`; `mark_rest` makes only
    // `getdtablesize` and `fcntl` calls, which are async-signal-safe and allocate nothing.
    unsafe { cmd.pre_exec(mark_rest) };
}

/// [`stdio_only`], and the open descriptor `from` handed to the child as descriptor `to`
/// (DESIGN-gh-token-card-7b81e-1121 L1: the one-shot git helper reads its credential there, so
/// it is in no argument, no environment and no file). `from` must stay open until the spawn.
pub fn stdio_and(cmd: &mut Command, from: RawFd, to: RawFd) {
    // SAFETY: as in `stdio_only`; `give` adds `dup2` / `fcntl`, async-signal-safe as well, and
    // builds an `io::Error` from errno only (no allocation).
    unsafe { cmd.pre_exec(move || give(from, to)) };
}

fn give(from: RawFd, to: RawFd) -> std::io::Result<()> {
    mark_rest()?;
    if from == to {
        // SAFETY: F_SETFD 0 clears close-on-exec of an open descriptor; no pointers.
        let r = unsafe { libc::fcntl(to, libc::F_SETFD, 0) };
        return if r < 0 {
            Err(std::io::Error::last_os_error())
        } else {
            Ok(())
        };
    }
    // SAFETY: copies `from` onto `to`; the copy has no close-on-exec; no pointers.
    let r = unsafe { libc::dup2(from, to) };
    if r < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// Reads what was handed to this process as descriptor `fd` (by a parent's [`stdio_and`]), at
/// most `max` bytes, and closes it. An error when `fd` is not open.
pub fn read_given(fd: RawFd, max: usize) -> std::io::Result<Vec<u8>> {
    // SAFETY: F_GETFD only asks whether `fd` is open; no pointers.
    if unsafe { libc::fcntl(fd, libc::F_GETFD) } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: `fd` is open (checked above) and owned by nobody else in this process: it was
    // given at exec for this one read; the File closes it when dropped.
    let f = unsafe { std::fs::File::from_raw_fd(fd) };
    let mut b = Vec::new();
    std::io::Read::read_to_end(
        &mut std::io::Read::take(f, u64::try_from(max).unwrap_or(u64::MAX)),
        &mut b,
    )?;
    Ok(b)
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

    /// The one descriptor given reaches the child at its number, and nothing else does.
    #[test]
    fn a_given_descriptor_reaches_the_child_and_only_it() {
        let (r, mut w) = std::io::pipe().unwrap();
        std::io::Write::write_all(&mut w, b"username=u\n").unwrap();
        drop(w);
        // a stray descriptor without close-on-exec, as in the test above
        let d = std::env::temp_dir().join(format!("aib-sys-stdio-and-{}", std::process::id()));
        let f = std::fs::File::create(&d).unwrap();
        // SAFETY: duplicates a descriptor this test owns to a number of 200 or more; no pointers.
        let stray = unsafe { libc::fcntl(f.as_raw_fd(), libc::F_DUPFD, 200) };
        // given from a number far from 3: run alone, the pipe itself can be 3, and then the copy
        // onto 3 is never made (7b81e-ghj-1499e2dfb knife GB6 missed)
        // SAFETY: duplicates the pipe's read end to a number of 100 or more, close-on-exec; no
        // pointers.
        let from = unsafe { libc::fcntl(r.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 100) };
        assert!(from >= 100, "reason: no_high_copy");
        let mut c = Command::new("/bin/sh");
        c.args(["-c", "cat <&3; ls /dev/fd"]);
        stdio_and(&mut c, from, 3);
        let out = c.output().unwrap();
        // SAFETY: closes the copy this test made; no pointers.
        let _ = unsafe { libc::close(from) };
        drop(r);
        let text = String::from_utf8_lossy(&out.stdout).into_owned();
        assert!(text.starts_with("username=u\n"), "reason: not_given {text}");
        assert!(
            !text.split_whitespace().any(|n| n == stray.to_string()),
            "reason: stray_reached_child {text}"
        );
        // SAFETY: closes the copy this test made; no pointers.
        let _ = unsafe { libc::close(stray) };
        drop(f);
        let _ = std::fs::remove_file(d);
    }
}
