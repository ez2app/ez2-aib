//! The exit code of `aib test` 2.0 (SPEC r2 D). A reused pass never covers a failure that ran
//! this time; zero units, nothing run, a time-out, a cancel and an engine error each have
//! their own code that is not success.

use crate::skip::{SkipWhy, Use};

/// What happened to one unit this time.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Ran now; `met` = the result is what the unit expects (a knife expects a failure).
    /// `tests_ran` comes from the reader; `None` or `Some(0)` is not a run.
    Ran { met: bool, tests_ran: Option<u32> },
    /// A result from an earlier real run that passed every source check.
    Reused,
    /// Not run.
    Skipped(SkipWhy),
    /// Ran and hit its time limit.
    TimedOut,
    /// A reused result was spot-checked by a real run and came out different.
    SpotCheckDiffers,
}

/// The exit codes, in the order they win when several happen (1 > 8 > 5 > 6 > 7 > 4 > 3 > 0).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Exit {
    Ok = 0,
    ZeroUnits = 3,
    NothingRan = 4,
    EngineError = 7,
    Cancelled = 6,
    TimedOut = 5,
    SpotCheckDiffers = 8,
    Failed = 1,
}

impl Exit {
    pub fn code(self) -> i32 {
        self as i32
    }

    /// Higher wins.
    fn rank(self) -> u8 {
        match self {
            Exit::Ok => 0,
            Exit::ZeroUnits => 1,
            Exit::NothingRan => 2,
            Exit::EngineError => 3,
            Exit::Cancelled => 4,
            Exit::TimedOut => 5,
            Exit::SpotCheckDiffers => 6,
            Exit::Failed => 7,
        }
    }

    fn worse(self, other: Exit) -> Exit {
        if other.rank() > self.rank() {
            other
        } else {
            self
        }
    }
}

/// The run as a whole. `cancelled` / `engine_error` come from the runner, not from a unit.
/// `bite_shown` answers the cross-check for a knife that says it cannot bite here.
pub fn exit_for(
    outcomes: &[Outcome],
    used: Use,
    cancelled: bool,
    engine_error: bool,
    bite_shown: &dyn Fn(&[String]) -> bool,
) -> Exit {
    let mut e = Exit::Ok;
    if cancelled {
        e = e.worse(Exit::Cancelled);
    }
    if engine_error {
        e = e.worse(Exit::EngineError);
    }
    if outcomes.is_empty() {
        return e.worse(Exit::ZeroUnits);
    }
    let mut counted = 0usize;
    for o in outcomes {
        match o {
            Outcome::Ran { met: false, .. } => e = e.worse(Exit::Failed),
            Outcome::Ran {
                met: true,
                tests_ran,
            } => {
                if tests_ran.unwrap_or(0) > 0 {
                    counted += 1;
                } else {
                    // a pass that tested nothing is not a pass (r7 §1.3, 1ced7 M1)
                    e = e.worse(Exit::Failed);
                }
            }
            Outcome::Reused => counted += 1,
            Outcome::Skipped(w) => {
                if !w.harmless(used, |on| bite_shown(on)) {
                    e = e.worse(Exit::Failed);
                }
            }
            Outcome::TimedOut => e = e.worse(Exit::TimedOut),
            Outcome::SpotCheckDiffers => e = e.worse(Exit::SpotCheckDiffers),
        }
    }
    if counted == 0 {
        e = e.worse(Exit::NothingRan);
    }
    e
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no(_: &[String]) -> bool {
        false
    }
    fn ran(met: bool, n: u32) -> Outcome {
        Outcome::Ran {
            met,
            tests_ran: Some(n),
        }
    }

    #[test]
    fn a_reused_pass_does_not_cover_a_failure_that_ran_now() {
        let e = exit_for(
            &[Outcome::Reused, ran(false, 3)],
            Use::Local,
            false,
            false,
            &no,
        );
        assert_eq!(e.code(), 1);
    }

    #[test]
    fn zero_units_and_nothing_run_are_not_success() {
        assert_eq!(exit_for(&[], Use::Local, false, false, &no).code(), 3);
        let skipped = Outcome::Skipped(SkipWhy::NotThisOs {
            os: "windows".into(),
        });
        assert_eq!(
            exit_for(&[skipped], Use::Local, false, false, &no).code(),
            4
        );
    }

    #[test]
    fn a_pass_with_no_test_run_is_a_failure() {
        let e = exit_for(
            &[Outcome::Ran {
                met: true,
                tests_ran: Some(0),
            }],
            Use::Local,
            false,
            false,
            &no,
        );
        assert_eq!(e.code(), 1);
        let e = exit_for(
            &[Outcome::Ran {
                met: true,
                tests_ran: None,
            }],
            Use::Local,
            false,
            false,
            &no,
        );
        assert_eq!(e.code(), 1);
    }

    #[test]
    fn an_upstream_skip_fails_and_a_user_filter_fails_only_as_evidence() {
        let up = [ran(true, 5), Outcome::Skipped(SkipWhy::UpstreamFailed)];
        assert_eq!(exit_for(&up, Use::Local, false, false, &no).code(), 1);
        let f = [ran(true, 5), Outcome::Skipped(SkipWhy::UserFilter)];
        assert_eq!(exit_for(&f, Use::Local, false, false, &no).code(), 0);
        assert_eq!(exit_for(&f, Use::Evidence, false, false, &no).code(), 1);
    }

    #[test]
    fn the_worst_code_wins_in_the_written_order() {
        let all = [ran(false, 1), Outcome::SpotCheckDiffers, Outcome::TimedOut];
        assert_eq!(exit_for(&all, Use::Local, true, true, &no).code(), 1);
        let no_fail = [ran(true, 1), Outcome::SpotCheckDiffers, Outcome::TimedOut];
        assert_eq!(exit_for(&no_fail, Use::Local, true, true, &no).code(), 8);
        let t = [ran(true, 1), Outcome::TimedOut];
        assert_eq!(exit_for(&t, Use::Local, true, true, &no).code(), 5);
        assert_eq!(
            exit_for(&[ran(true, 1)], Use::Local, true, true, &no).code(),
            6
        );
        assert_eq!(
            exit_for(&[ran(true, 1)], Use::Local, false, true, &no).code(),
            7
        );
        assert_eq!(
            exit_for(&[ran(true, 1)], Use::Local, false, false, &no).code(),
            0
        );
    }
}
