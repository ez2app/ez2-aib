//! Platform parity, enforced by the compiler and the tests (DESIGN-platform-
//! parity, a9136 11-09; Andy: "這個必要有 rust 程式強制判斷").
//!
//! Every step aibd takes to start, stop and sweep what it runs is a required
//! method of [`Lifecycle`], with no default body. Each platform is one `impl`:
//! a platform that leaves a step out does not compile in its CI cell. A step a
//! platform does not do yet, or does not have at all, is an explicit variant
//! ([`Step::Unsupported`] / [`Step::NotApplicable`], [`Swept::Unsupported`]),
//! never an empty value that reads as "done": an empty list of leftovers used
//! to be the Windows answer and looked exactly like "all stopped".
//!
//! `tests/lifecycle_parity.rs` checks that every [`StepId`] has at least one
//! registered test on the platform it runs on.
//!
//! Only `macos` and `windows` are platforms aibd ships for (spec section 15,
//! the two CI cells). Any other target gets [`Unlisted`]: today's behaviour
//! (the POSIX session code on other unix) and `Unsupported` for the rest.

use super::session::SpawnFn;
use super::ChildReaping;
use std::future::Future;
use std::pin::Pin;
use std::time::Instant;

macro_rules! lifecycle_steps {
    ($($(#[$m:meta])* $v:ident),* $(,)?) => {
        /// One step of the process lifecycle. Adding one here without a method
        /// on [`Lifecycle`] and a registered test turns `lifecycle_parity` red.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub enum StepId { $($(#[$m])* $v),* }
        impl StepId {
            /// Every step, in declaration order.
            pub const ALL: &'static [StepId] = &[$(StepId::$v),*];
            pub fn name(self) -> &'static str {
                match self { $(StepId::$v => stringify!($v)),* }
            }
        }
    };
}

lifecycle_steps! {
    /// Start a session tree.
    Spawn,
    /// At shutdown: stop the trees of failed starts once more, within one deadline.
    StopLeftovers,
    /// The events that make aibd stop (and run `shutdown_all`).
    ShutdownSignal,
    /// The small tree selfcheck starts and stops.
    CheckTree,
    /// How exited children are kept (POSIX `SIGCHLD`).
    ChildReaping,
    /// At start: keep exited children lookable until we reap them.
    KeepChildZombies,
}

/// The outcome of one step on this platform.
#[must_use]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step<T> {
    Done(T),
    /// Not done on this platform in this version. The caller treats it as
    /// "not confirmed": it never frees a slot or writes a success.
    Unsupported {
        step: StepId,
        why: &'static str,
    },
    /// This platform has no such thing (the reason is part of the review).
    NotApplicable {
        step: StepId,
        why: &'static str,
    },
}

/// The shutdown sweep of leftover trees. There is no value meaning "done" that
/// a platform could return without having looked.
#[must_use]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Swept {
    /// Nothing was left, or every kept tree was confirmed gone in time.
    NoneLeft,
    /// Roots not confirmed gone by the deadline, as (pid, recorded start time;
    /// `None` when it was never held).
    Unconfirmed(Vec<(u32, Option<u64>)>),
    /// This platform cannot sweep: whatever a failed start left is unknown.
    Unsupported { why: &'static str },
}

impl Swept {
    pub(crate) fn from_unconfirmed(left: Vec<(u32, Option<u64>)>) -> Swept {
        if left.is_empty() {
            Swept::NoneLeft
        } else {
            Swept::Unconfirmed(left)
        }
    }

    /// How many roots the shutdown must report as not confirmed gone
    /// (`Unsupported` counts as one: something may be running, unlisted).
    pub fn unconfirmed(&self) -> usize {
        match self {
            Swept::NoneLeft => 0,
            Swept::Unconfirmed(v) => v.len(),
            Swept::Unsupported { .. } => 1,
        }
    }
}

/// What makes aibd stop, and what it does NOT hear on this platform (logged
/// at start, listed in the release notes; a63ab q_d0a68a06).
pub struct Shutdown {
    pub wait: Pin<Box<dyn Future<Output = ()> + Send>>,
    pub not_heard: &'static [&'static str],
}

/// Every lifecycle step, one required method each (no default bodies).
pub trait Lifecycle {
    /// The platform's name in logs and test messages.
    const NAME: &'static str;
    fn spawn() -> Step<SpawnFn>;
    fn stop_leftovers(deadline: Instant) -> Swept;
    fn shutdown_signal() -> Shutdown;
    /// The selfcheck tree's script (run by the platform's `spawn`).
    fn check_tree() -> Step<&'static str>;
    fn child_reaping() -> Step<ChildReaping>;
    /// `Done(Some(was))` when an inherited setting had to be changed.
    fn keep_child_zombies() -> Step<Option<&'static str>>;
}

#[cfg(target_os = "macos")]
pub struct MacOs;

#[cfg(target_os = "macos")]
impl Lifecycle for MacOs {
    const NAME: &'static str = "macos";

    fn spawn() -> Step<SpawnFn> {
        Step::Done(super::session_unix::spawn)
    }

    fn stop_leftovers(deadline: Instant) -> Swept {
        Swept::from_unconfirmed(super::session_unix::stop_leftovers(deadline))
    }

    fn shutdown_signal() -> Shutdown {
        Shutdown {
            wait: Box::pin(posix_shutdown()),
            not_heard: &[],
        }
    }

    fn check_tree() -> Step<&'static str> {
        Step::Done(crate::selfcheck::CHECK_SCRIPT)
    }

    fn child_reaping() -> Step<ChildReaping> {
        Step::Done(match aib_sys::sigchld() {
            Ok(s) if s.keeps_zombies() => ChildReaping::Keeps(s.as_str()),
            Ok(s) => ChildReaping::AutoReaps(s.as_str()),
            Err(e) => ChildReaping::Unreadable(e.tag().to_owned()),
        })
    }

    fn keep_child_zombies() -> Step<Option<&'static str>> {
        Step::Done(match aib_sys::sigchld_keep_zombies() {
            Ok(was) if !was.keeps_zombies() => Some(was.as_str()),
            _ => None,
        })
    }
}

#[cfg(windows)]
pub struct Windows;

#[cfg(windows)]
impl Lifecycle for Windows {
    const NAME: &'static str = "windows";

    fn spawn() -> Step<SpawnFn> {
        // J0 + one Job per session (043dd #543 / #555)
        Step::Done(super::session_windows::spawn)
    }

    fn stop_leftovers(deadline: Instant) -> Swept {
        Swept::from_unconfirmed(super::session_windows::stop_leftovers(deadline))
    }

    fn shutdown_signal() -> Shutdown {
        Shutdown {
            wait: Box::pin(async {
                let _ = tokio::signal::ctrl_c().await;
            }),
            // preview.2 known limit (a63ab q_d0a68a06): only J0 collects the
            // trees then, when aibd ends; 043dd's next PR listens to these.
            not_heard: &["ctrl_close", "ctrl_logoff", "ctrl_shutdown"],
        }
    }

    fn check_tree() -> Step<&'static str> {
        Step::Unsupported {
            step: StepId::CheckTree,
            why: "the check tree is a POSIX shell; no Windows check tree yet",
        }
    }

    fn child_reaping() -> Step<ChildReaping> {
        Step::NotApplicable {
            step: StepId::ChildReaping,
            why: "Windows keeps a process HANDLE, not a zombie",
        }
    }

    fn keep_child_zombies() -> Step<Option<&'static str>> {
        Step::NotApplicable {
            step: StepId::KeepChildZombies,
            why: "Windows keeps a process HANDLE, not a zombie",
        }
    }
}

/// A target aibd does not ship for (not macOS, not Windows).
#[cfg(not(any(target_os = "macos", windows)))]
pub struct Unlisted;

#[cfg(not(any(target_os = "macos", windows)))]
impl Lifecycle for Unlisted {
    const NAME: &'static str = "unlisted";

    fn spawn() -> Step<SpawnFn> {
        #[cfg(unix)]
        {
            Step::Done(super::session_unix::spawn)
        }
        #[cfg(not(unix))]
        {
            Step::Unsupported {
                step: StepId::Spawn,
                why: "unsupported_platform",
            }
        }
    }

    fn stop_leftovers(deadline: Instant) -> Swept {
        #[cfg(unix)]
        {
            Swept::from_unconfirmed(super::session_unix::stop_leftovers(deadline))
        }
        #[cfg(not(unix))]
        {
            let _ = deadline;
            Swept::Unsupported {
                why: "unsupported_platform",
            }
        }
    }

    fn shutdown_signal() -> Shutdown {
        #[cfg(unix)]
        {
            Shutdown {
                wait: Box::pin(posix_shutdown()),
                not_heard: &[],
            }
        }
        #[cfg(not(unix))]
        {
            Shutdown {
                wait: Box::pin(async {
                    let _ = tokio::signal::ctrl_c().await;
                }),
                not_heard: &["platform events"],
            }
        }
    }

    fn check_tree() -> Step<&'static str> {
        if cfg!(unix) {
            Step::Done(crate::selfcheck::CHECK_SCRIPT)
        } else {
            Step::Unsupported {
                step: StepId::CheckTree,
                why: "no check tree on this platform",
            }
        }
    }

    fn child_reaping() -> Step<ChildReaping> {
        Step::Unsupported {
            step: StepId::ChildReaping,
            why: "read only on macOS (M5)",
        }
    }

    fn keep_child_zombies() -> Step<Option<&'static str>> {
        Step::Unsupported {
            step: StepId::KeepChildZombies,
            why: "set only on macOS (M5)",
        }
    }
}

/// The platform this build runs on.
#[cfg(target_os = "macos")]
pub type Current = MacOs;
#[cfg(windows)]
pub type Current = Windows;
#[cfg(not(any(target_os = "macos", windows)))]
pub type Current = Unlisted;

/// Ctrl-C and SIGTERM (so a plain `kill` still collects every session, D11).
#[cfg(unix)]
async fn posix_shutdown() {
    use tokio::signal::unix::{signal, SignalKind};
    match signal(SignalKind::terminate()) {
        Ok(mut term) => {
            tokio::select! {
                _ = tokio::signal::ctrl_c() => {}
                _ = term.recv() => {}
            }
        }
        Err(_) => {
            let _ = tokio::signal::ctrl_c().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// shutdown_all reports `unconfirmed()`: a platform that cannot sweep is
    /// never counted as "nothing left".
    #[test]
    fn a_sweep_that_could_not_look_counts_as_unconfirmed() {
        assert_eq!(Swept::from_unconfirmed(vec![]), Swept::NoneLeft);
        assert_eq!(Swept::NoneLeft.unconfirmed(), 0);
        assert_eq!(
            Swept::from_unconfirmed(vec![(7, None), (8, Some(1))]).unconfirmed(),
            2
        );
        assert_eq!(Swept::Unsupported { why: "x" }.unconfirmed(), 1);
    }

    #[test]
    fn every_step_has_a_distinct_name() {
        let mut n: Vec<&str> = StepId::ALL.iter().map(|s| s.name()).collect();
        n.sort_unstable();
        n.dedup();
        assert_eq!(n.len(), StepId::ALL.len());
    }
}
