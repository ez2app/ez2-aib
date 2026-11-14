//! Platform parity (a9136 11-09): every lifecycle step has at least one test
//! on the platform this runs on. The registry holds function pointers, so a
//! registered test that is deleted does not compile, and a step with no entry
//! for this platform is red with the (platform, step) to add.
//!
//! The behaviour of each step is tested where it lives (sessions, selfcheck,
//! session_windows ...); the tests here pin the step's *shape* on each
//! platform: Done, Unsupported or NotApplicable, never a silent empty value.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aibd::platform::lifecycle::{CheckTree, Current, Lifecycle, Step, StepId, Swept};
use std::time::{Duration, Instant};

type Registered = (StepId, &'static str, fn());

macro_rules! register {
    ($($step:ident => $f:ident),* $(,)?) => {
        const REGISTERED: &[Registered] = &[$((StepId::$step, stringify!($f), $f as fn())),*];
    };
}

#[test]
fn every_step_has_a_test_on_this_platform() {
    let missing: Vec<&str> = StepId::ALL
        .iter()
        .filter(|s| !REGISTERED.iter().any(|(r, _, _)| r == *s))
        .map(|s| s.name())
        .collect();
    assert!(
        missing.is_empty(),
        "platform {}: no registered lifecycle test for {missing:?} (add one to tests/lifecycle_parity.rs)",
        <Current as Lifecycle>::NAME
    );
}

/// The registered checks are not `#[test]`s of their own: they run here, so
/// the list is the tests (it cannot drift from them).
#[test]
fn every_registered_test_passes() {
    for (step, name, f) in REGISTERED {
        let r = std::panic::catch_unwind(*f);
        assert!(
            r.is_ok(),
            "{} / {:?}: {name} failed",
            <Current as Lifecycle>::NAME,
            step
        );
    }
}

fn sweep_with_nothing_kept() -> Swept {
    <Current as Lifecycle>::stop_leftovers(Instant::now() + Duration::from_secs(1))
}

#[cfg(target_os = "macos")]
mod macos {
    use super::*;
    use aibd::platform::ChildReaping;

    pub fn spawn_is_wired() {
        assert!(matches!(<Current as Lifecycle>::spawn(), Step::Done(_)));
    }

    /// Nothing kept in this test process: the sweep looked and found nothing.
    pub fn the_sweep_looks_and_says_none_left() {
        assert_eq!(sweep_with_nothing_kept(), Swept::NoneLeft);
    }

    pub fn shutdown_hears_everything_it_should() {
        assert!(<Current as Lifecycle>::shutdown_signal()
            .not_heard
            .is_empty());
    }

    pub fn the_check_tree_is_the_posix_script() {
        assert_eq!(
            <Current as Lifecycle>::check_tree(),
            Step::Done(CheckTree::Shell(aibd::selfcheck::CHECK_SCRIPT))
        );
    }

    pub fn child_reaping_is_read() {
        let Step::Done(r) = <Current as Lifecycle>::child_reaping() else {
            panic!("macOS must read the SIGCHLD disposition");
        };
        assert!(!matches!(r, ChildReaping::Unsupported), "{r:?}");
    }

    /// The gap is named, not hidden: no kill-on-exit on macOS until the
    /// lifeline shim (DECISION-11 C). Change this test when it lands.
    pub fn kill_on_exit_is_a_named_gap() {
        assert!(matches!(
            <Current as Lifecycle>::kill_on_exit(),
            Step::Unsupported {
                step: StepId::KillOnExit,
                ..
            }
        ));
    }

    pub fn lineage_is_read_on_macos() {
        assert!(matches!(<Current as Lifecycle>::lineage(), Step::Done(_)));
    }

    pub fn keep_child_zombies_is_done() {
        assert!(matches!(
            <Current as Lifecycle>::keep_child_zombies(),
            Step::Done(_)
        ));
    }
}

#[cfg(target_os = "macos")]
use macos::*;

#[cfg(target_os = "macos")]
register! {
    Spawn => spawn_is_wired,
    StopLeftovers => the_sweep_looks_and_says_none_left,
    ShutdownSignal => shutdown_hears_everything_it_should,
    CheckTree => the_check_tree_is_the_posix_script,
    ChildReaping => child_reaping_is_read,
    KeepChildZombies => keep_child_zombies_is_done,
    KillOnExit => kill_on_exit_is_a_named_gap,
    Lineage => lineage_is_read_on_macos,
}

#[cfg(windows)]
mod windows {
    use super::*;

    pub fn spawn_is_wired() {
        assert!(matches!(<Current as Lifecycle>::spawn(), Step::Done(_)));
    }

    pub fn the_sweep_looks_and_says_none_left() {
        assert_eq!(sweep_with_nothing_kept(), Swept::NoneLeft);
    }

    /// preview.2 known limit (a63ab q_d0a68a06): change this test when the
    /// close / logoff / shutdown events are heard (043dd, next PR).
    pub fn shutdown_names_what_it_does_not_hear() {
        assert_eq!(
            <Current as Lifecycle>::shutdown_signal().not_heard,
            &["ctrl_close", "ctrl_logoff", "ctrl_shutdown"]
        );
    }

    /// No /bin/sh: aibd is its own check tree (tests/selfcheck_selfexec.rs
    /// runs it through the Windows spawn).
    pub fn the_check_tree_is_aibd_itself() {
        assert_eq!(
            <Current as Lifecycle>::check_tree(),
            Step::Done(CheckTree::SelfExec)
        );
    }

    pub fn child_reaping_does_not_apply() {
        assert!(matches!(
            <Current as Lifecycle>::child_reaping(),
            Step::NotApplicable {
                step: StepId::ChildReaping,
                ..
            }
        ));
    }

    /// J0 is adopted. This test process is now in a KILL_ON_JOB_CLOSE Job:
    /// dropping the guard would close it and kill the test runner itself, so
    /// it is held until the process exits (as `main` does).
    pub fn kill_on_exit_is_j0() {
        let g = <Current as Lifecycle>::kill_on_exit();
        assert!(matches!(g, Step::Done(Ok(_))), "J0 not adopted");
        std::mem::forget(g);
    }

    pub fn lineage_does_not_apply() {
        assert!(matches!(
            <Current as Lifecycle>::lineage(),
            Step::NotApplicable {
                step: StepId::Lineage,
                ..
            }
        ));
    }

    pub fn keep_child_zombies_does_not_apply() {
        assert!(matches!(
            <Current as Lifecycle>::keep_child_zombies(),
            Step::NotApplicable {
                step: StepId::KeepChildZombies,
                ..
            }
        ));
    }
}

#[cfg(windows)]
use windows::*;

#[cfg(windows)]
register! {
    Spawn => spawn_is_wired,
    StopLeftovers => the_sweep_looks_and_says_none_left,
    ShutdownSignal => shutdown_names_what_it_does_not_hear,
    CheckTree => the_check_tree_is_aibd_itself,
    ChildReaping => child_reaping_does_not_apply,
    KeepChildZombies => keep_child_zombies_does_not_apply,
    KillOnExit => kill_on_exit_is_j0,
    Lineage => lineage_does_not_apply,
}

// A target aibd does not ship for: nothing registered, so the parity test
// says which steps have no test there.
#[cfg(not(any(target_os = "macos", windows)))]
register! {}
