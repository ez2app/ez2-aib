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
//! The program never runs unless the caller let it go:
//! - only the byte [`GO`] lets it run; any other byte, EOF, an error or
//!   nothing within [`HOLD_LIMIT`] ⇒ the hook returns an error, the child
//!   exits without exec'ing and `std` reaps it and returns that error;
//! - the caller's code panics ⇒ the byte [`CANCEL`] is written, then the
//!   panic goes on. Not by closing the go pipe alone: another child forked
//!   at the same moment (another held spawn, or any fork + exec) holds a
//!   copy of our go writer until it execs, so EOF can come late or, with two
//!   held spawns failing together, never (bb7dc ad94b515 H1). And not by
//!   signalling the pid: once the child gave up at [`HOLD_LIMIT`], `std` has
//!   reaped it and the pid may be someone else's (bb7dc 52a4b1e2 H1). No
//!   signal is ever sent by pid here; only `Child::kill` on a child `std`
//!   still holds;
//! - the go pipe closes unwritten (the parent died) ⇒ EOF ⇒ the same.
//!
//! No time limit on the parent's side: while the child is held aibd only
//! makes non-blocking calls (coalition, unique id, process table, kqueue).
//! If a call that can block is ever added there, it needs a registered
//! operation timer (ADR 0005) (a63ab 11-10 05:19).

use std::io::{Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::process::CommandExt;
use std::panic::{catch_unwind, resume_unwind, AssertUnwindSafe};
use std::process::{Child, Command};

/// How long a held child waits for the go byte before it gives up without
/// exec'ing (milliseconds, for `poll`).
pub const HOLD_LIMIT: libc::c_int = 5_000;

/// The byte that lets a held child exec.
const GO: u8 = 1;
/// The byte that sends a held child away without exec'ing.
const CANCEL: u8 = 0;

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
        let pid = (&pid_r)
            .read_exact(&mut b)
            .ok()
            .map(|()| u32::from_ne_bytes(b));
        let held = match pid {
            Some(p) => match catch_unwind(AssertUnwindSafe(|| on_held(p))) {
                Ok(r) => Some(r),
                Err(panic) => {
                    // never let it run, and do not wait for an EOF another
                    // fork may hold back: tell it to go away. A failed write
                    // only means it already gave up. `spawn` then returns its
                    // error, having reaped it.
                    let _ = (&go_w).write_all(&[CANCEL]);
                    drop(go_w);
                    if let Ok(Ok(mut c)) = spawner.join() {
                        // not reachable (only GO execs); if it were, it is a
                        // `Child` std still holds, so this kill is its own
                        let _ = c.kill();
                        let _ = c.wait();
                    }
                    resume_unwind(panic);
                }
            },
            None => None,
        };
        if held.is_some() {
            // a failed write only means the child is gone: spawn says why
            let _ = (&go_w).write_all(&[GO]);
        }
        drop(go_w);
        let mut child = spawner
            .join()
            .map_err(|_| std::io::Error::other("spawn thread panicked"))??;
        if held.is_some() && u32::from_ne_bytes(b) != child.id() {
            // not reachable: the pipe is this call's own and only our child
            // writes it. If it were, the program is running and the caller
            // will hear "not started": stop it first (043dd ad94b515 L1)
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::other("held pid is not the child"));
        }
        Ok((child, held))
    })
}

/// In the forked child: close our copy of the go writer, hand over the pid,
/// wait for one byte. [`GO`] ⇒ `Ok`; any other byte, EOF, nothing within
/// [`HOLD_LIMIT`], or any error ⇒ `Err`, so `std` reports it and never execs.
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
    // Codex (DECISION-614-held-lock): an absolute deadline, so a signal that
    // interrupts `poll` does not start a new HOLD_LIMIT; only the time left is
    // waited for. CLOCK_MONOTONIC: `clock_gettime` is async-signal-safe.
    let deadline = monotonic_ms().saturating_add(i64::from(HOLD_LIMIT));
    loop {
        let left = deadline.saturating_sub(monotonic_ms());
        if left <= 0 {
            return Err(std::io::Error::from_raw_os_error(libc::ETIMEDOUT));
        }
        let left = libc::c_int::try_from(left).unwrap_or(HOLD_LIMIT);
        let mut fds = libc::pollfd {
            fd: go_r,
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: `fds` is one live, writable `pollfd`; `poll` reads and
        // writes only that one entry.
        let ready = unsafe { libc::poll(&mut fds, 1, left) };
        if ready == 0 {
            continue; // the deadline check above returns ETIMEDOUT
        }
        if ready < 0 {
            let e = std::io::Error::last_os_error();
            if e.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            return Err(e);
        }
        // SAFETY: `go` is a live, writable 1-byte buffer.
        let n = unsafe { libc::read(go_r, go.as_mut_ptr().cast(), 1) };
        match n {
            1 if go[0] == GO => return Ok(()),
            1 | 0 => return Err(std::io::Error::from_raw_os_error(libc::ECANCELED)),
            _ => {
                let e = std::io::Error::last_os_error();
                if e.raw_os_error() != Some(libc::EINTR) {
                    return Err(e);
                }
            }
        }
    }
}

/// Milliseconds on the monotonic clock (for the child's deadline only).
fn monotonic_ms() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `ts` is a live, writable timespec; CLOCK_MONOTONIC always exists.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    // held is macOS only, where time_t and c_long are both i64
    ts.tv_sec
        .saturating_mul(1000)
        .saturating_add(ts.tv_nsec / 1_000_000)
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

    /// bb7dc ad94b515 H1: B is forked while A is held and holds a copy of A's
    /// go writer. A's recording panics: A must come back at once (killed, not
    /// waiting for an EOF B holds back), its program never runs, and B is
    /// still let go on its own.
    #[test]
    #[allow(clippy::disallowed_methods)] // test pacing / bounds only
    fn a_panic_while_another_spawn_is_held_comes_back_at_once() {
        use std::sync::mpsc::channel;
        let d = dir("two");
        let (fa, fb) = (d.join("a"), d.join("b"));
        let (a_held_tx, a_held) = channel::<u32>();
        let (a_go_tx, a_go) = channel::<()>();
        let mut ca = writer(&fa);
        let a = std::thread::spawn(move || {
            let t = std::time::Instant::now();
            let r = catch_unwind(AssertUnwindSafe(|| {
                spawn_held::<()>(&mut ca, |pid| {
                    a_held_tx.send(pid).unwrap();
                    let _ = a_go.recv();
                    panic!("A's recording failed")
                })
            }));
            (r.is_err(), t.elapsed())
        });
        let a_pid = a_held.recv_timeout(Duration::from_secs(10)).unwrap();
        // B forks now, while A is held: it inherits A's go writer
        let (b_held_tx, b_held) = channel::<()>();
        let (b_go_tx, b_go) = channel::<()>();
        let mut cb = writer(&fb);
        let b = std::thread::spawn(move || {
            spawn_held(&mut cb, |_| {
                b_held_tx.send(()).unwrap();
                let _ = b_go.recv();
            })
        });
        b_held.recv_timeout(Duration::from_secs(10)).unwrap();
        a_go_tx.send(()).unwrap();
        // bounded either way: if A is stuck, letting B go frees it
        let t = std::time::Instant::now();
        while !a.is_finished() && t.elapsed() < Duration::from_secs(3) {
            std::thread::sleep(Duration::from_millis(10));
        }
        let a_stuck = !a.is_finished();
        b_go_tx.send(()).unwrap();
        let (a_panicked, a_took) = a.join().unwrap();
        let (mut cb, _) = b.join().unwrap().unwrap();
        let b_ok = cb.wait().unwrap().success();
        std::thread::sleep(Duration::from_millis(200));
        let (a_ran, b_ran) = (fa.exists(), fb.exists());
        // A was reaped: its pid is no longer a process of ours
        let a_left = Command::new("/bin/kill")
            .args(["-0", &a_pid.to_string()])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        let _ = std::fs::remove_dir_all(&d);
        assert!(!a_stuck, "A waited for B (took {a_took:?})");
        assert!(a_panicked, "A's panic did not come back");
        assert!(!a_ran, "A's program ran although it was never let go");
        assert!(!a_left, "A {a_pid} left behind");
        assert!(b_ok && b_ran, "B was not let go on its own");
    }

    /// bb7dc 52a4b1e2 H1: the recording outlasts [`HOLD_LIMIT`], so the child
    /// gave up and `std` reaped it before `on_held` returns. Returning then is
    /// the spawn error; panicking then comes back as the panic. Either way the
    /// program never ran. That nothing is signalled by the stale pid is by
    /// construction (no `kill` by pid in this file; aibd.yml checks it).
    #[test]
    #[allow(clippy::disallowed_methods)] // test pacing only
    fn a_hold_past_the_limit_is_the_spawn_error_or_the_panic() {
        let past = Duration::from_millis(u64::try_from(HOLD_LIMIT).unwrap() + 700);
        let d = dir("late");
        let (fr, fp) = (d.join("r"), d.join("p"));
        let r = spawn_held(&mut writer(&fr), |pid| {
            std::thread::sleep(past);
            pid
        });
        let mut cp = writer(&fp);
        let seen = std::sync::Mutex::new(None);
        let p = catch_unwind(AssertUnwindSafe(|| {
            spawn_held::<()>(&mut cp, |pid| {
                std::thread::sleep(past);
                *seen.lock().unwrap() = Some(pid);
                panic!("late recording failed")
            })
        }));
        std::thread::sleep(Duration::from_millis(300));
        let (ran_r, ran_p) = (fr.exists(), fp.exists());
        let _ = std::fs::remove_dir_all(&d);
        assert!(
            r.is_err(),
            "a hold past the limit still started: {:?}",
            r.map(|(c, h)| (c.id(), h))
        );
        assert!(p.is_err(), "the late panic did not come back");
        assert!(seen.lock().unwrap().is_some(), "on_held never ran");
        assert!(!ran_r && !ran_p, "a program ran after its hold gave up");
    }

    /// A program that does not exist: no child was held, the spawn error
    /// comes back.
    #[test]
    fn a_missing_program_is_the_spawn_error() {
        let r = spawn_held(&mut Command::new("/nonexistent/aib-held"), |_| ());
        assert!(r.is_err(), "{:?}", r.map(|(_, h)| h));
    }
}
