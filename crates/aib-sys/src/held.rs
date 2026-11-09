//! G1 `root_unrecorded` (DECISION-root-unrecorded-a9136-1110): start a
//! program held between `fork` and `exec`, so the caller can record it and
//! start watching it before it runs, forks or exits.
//!
//! `std::process::Command::spawn` returns only once the child has exec'd, so
//! the child cannot simply stop itself before `exec`. Instead it hands its
//! pid over one pipe and waits on a second one; `spawn` runs on another
//! thread meanwhile. Both pipes are close-on-exec: still open in the child
//! while the `pre_exec` hook runs, closed once it execs.
//!
//! The program never runs unless the caller let it go: if the pid cannot be
//! read, or the caller's code panics, the go pipe closes unwritten and the
//! child returns an error from the hook instead of exec'ing.

use std::io::{Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};

/// Spawn `cmd` held before `exec`; `on_held(pid)` runs while it is held, and
/// the program starts when it returns. `None` = no child was ever held
/// (nothing ran `on_held`); the spawn error, if any, is the `Err`.
pub fn spawn_held<R>(
    cmd: &mut Command,
    on_held: impl FnOnce(u32) -> R,
) -> std::io::Result<(Child, Option<R>)> {
    let (pid_r, pid_w) = std::io::pipe()?;
    let (go_r, go_w) = std::io::pipe()?;
    let (pw, gr, gw) = (pid_w.as_raw_fd(), go_r.as_raw_fd(), go_w.as_raw_fd());
    // SAFETY: the hook runs in the forked child before `exec`; `held_child`
    // only calls `close`, `getpid`, `write` and `read`, which are
    // async-signal-safe, and allocates nothing.
    unsafe { cmd.pre_exec(move || held_child(pw, gr, gw)) };
    std::thread::scope(|s| {
        let spawner = s.spawn(move || {
            let r = cmd.spawn();
            // our copies: the reader sees EOF if no child ever wrote
            drop(pid_w);
            drop(go_r);
            r
        });
        let mut b = [0u8; 4];
        let held = match (&pid_r).read_exact(&mut b) {
            Ok(()) => Some(on_held(u32::from_ne_bytes(b))),
            Err(_) => None,
        };
        if held.is_some() {
            // a failed write only means the child is gone: spawn says why
            let _ = (&go_w).write_all(&[1]);
        }
        drop(go_w);
        let child = spawner
            .join()
            .map_err(|_| std::io::Error::other("spawn thread panicked"))??;
        if held.is_some() && u32::from_ne_bytes(b) != child.id() {
            return Err(std::io::Error::other("held pid is not the child"));
        }
        Ok((child, held))
    })
}

/// In the forked child: close our copy of the go writer, hand over the pid,
/// wait for one byte. EOF (the parent let go without writing) or any error
/// ⇒ `Err`, so `std` reports it and never execs.
fn held_child(pid_w: RawFd, go_r: RawFd, go_w: RawFd) -> std::io::Result<()> {
    // SAFETY: `go_w` is an fd this child inherited; closing it only drops
    // this process's copy.
    unsafe { libc::close(go_w) };
    // SAFETY: `getpid` has no preconditions.
    let pid = unsafe { libc::getpid() };
    let b = pid.to_ne_bytes();
    // SAFETY: `b` is a live 4-byte buffer; `write` reads at most 4 bytes.
    let n = unsafe { libc::write(pid_w, b.as_ptr().cast(), b.len()) };
    if n != 4 {
        return Err(std::io::Error::last_os_error());
    }
    let mut go = [0u8; 1];
    loop {
        // SAFETY: `go` is a live, writable 1-byte buffer.
        let n = unsafe { libc::read(go_r, go.as_mut_ptr().cast(), 1) };
        match n {
            1 => return Ok(()),
            0 => return Err(std::io::Error::from_raw_os_error(libc::ECANCELED)),
            _ => {
                let e = std::io::Error::last_os_error();
                if e.raw_os_error() != Some(libc::EINTR) {
                    return Err(e);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::time::Duration;

    fn dir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("aib-sys-held-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn writer(f: &std::path::Path) -> Command {
        let mut c = Command::new("/bin/sh");
        c.arg("-c").arg(format!("echo ran > '{}'", f.display()));
        c
    }

    /// While `on_held` runs the program has not run (its file is not there);
    /// it runs once `on_held` returns, under the pid `on_held` was given.
    #[test]
    #[allow(clippy::disallowed_methods)] // test pacing only
    fn the_program_runs_only_after_on_held_returns() {
        let d = dir("runs");
        let f = d.join("ran");
        let (mut child, held) = spawn_held(&mut writer(&f), |pid| {
            std::thread::sleep(Duration::from_millis(300));
            (pid, f.exists())
        })
        .unwrap();
        let st = child.wait().unwrap();
        let late = f.exists();
        let _ = std::fs::remove_dir_all(&d);
        let (pid, early) = held.unwrap();
        assert_eq!(pid, child.id());
        assert!(!early, "the program ran while it was held");
        assert!(st.success(), "{st:?}");
        assert!(late, "the program never ran");
    }

    /// If `on_held` panics the program never runs (the go pipe closes
    /// unwritten); the panic comes back to the caller.
    #[test]
    #[allow(clippy::disallowed_methods)] // test pacing only
    fn a_panic_while_held_never_runs_the_program() {
        let d = dir("panic");
        let f = d.join("ran");
        let mut c = writer(&f);
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            spawn_held::<()>(&mut c, |_| panic!("recording failed"))
        }));
        std::thread::sleep(Duration::from_millis(300));
        let ran = f.exists();
        let _ = std::fs::remove_dir_all(&d);
        assert!(r.is_err(), "the panic did not come back");
        assert!(!ran, "the program ran although it was never let go");
    }

    /// A program that does not exist: no child was held, the spawn error
    /// comes back.
    #[test]
    fn a_missing_program_is_the_spawn_error() {
        let r = spawn_held(&mut Command::new("/nonexistent/aib-held"), |_| ());
        assert!(r.is_err(), "{:?}", r.map(|(_, h)| h));
    }
}
