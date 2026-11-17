//! The engine link (S69-u4 × S69-u1, NOWAIT-519d0-1117 1ced7 2): an audit is a real run of a
//! unit the engine judged reusable. This turns that run, set against the result it would have
//! reused, into the unit's outcome for [`aib_test_core::exit::exit_for`], and says whether the
//! source is revoked.
//!
//! - The audit ran this time, so the unit counts as run now, not as reused.
//! - A real run that is wrong this time is a failure, whatever the source said.
//! - A real run that is right but differs from the source is `SpotCheckDiffers` (its own exit code
//!   when nothing worse happened), and the source is revoked.
//! - A run cut off before its end has no real result, and the reuse it was checking is not
//!   trusted either: it is not met. A time-out is the engine's own `TimedOut`; a cancel is the
//!   whole run's (`exit_for`'s `cancelled`), so the unit has no outcome of its own.

use crate::{compare, Compare, Outcome, Verdict};
use aib_test_core::exit::Outcome as Unit;

/// What the run does with one audited unit.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Linked {
    /// The unit's outcome for `exit_for`; `None` = the audit was cancelled (pass
    /// `cancelled = true` to `exit_for` and leave the unit out).
    pub unit: Option<Unit>,
    pub compare: Compare,
    /// The source it would have reused is revoked (its key is not reused again).
    pub revoke: bool,
}

/// `met`: the audit's real result is what the unit expects (a knife expects a failure), as the
/// unit's reader says for any run.
pub fn link(audit: &Verdict, source: &Verdict, met: bool) -> Linked {
    let c = compare(audit, source);
    let ran = |met: bool| Unit::Ran {
        met,
        tests_ran: audit.tests_ran,
    };
    let unit = match audit.outcome {
        Outcome::Cancelled => None,
        Outcome::Timeout => Some(Unit::TimedOut),
        _ if audit.censored || !audit.outcome.finished() => Some(ran(false)),
        _ if !met => Some(ran(false)),
        _ => Some(match c {
            Compare::Mismatch => Unit::SpotCheckDiffers,
            Compare::Same | Compare::BehaviorChanged | Compare::Incomparable => ran(true),
        }),
    };
    let revoke = c == Compare::Mismatch;
    Linked {
        unit,
        compare: c,
        revoke,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use aib_test_core::exit::{exit_for, Exit};
    use aib_test_core::skip::Use;

    fn v(outcome: Outcome, tests_ran: u32, failed: &[&str]) -> Verdict {
        Verdict {
            outcome,
            tests_ran: Some(tests_ran),
            failed: failed.iter().map(|s| s.to_string()).collect(),
            output_digest: "d".into(),
            censored: false,
        }
    }

    fn code(units: &[Unit], cancelled: bool) -> i32 {
        exit_for(
            units,
            Use::Local,
            None,
            cancelled,
            false,
            &|_: &[String]| false,
        )
        .code()
    }

    #[test]
    fn an_audit_that_agrees_counts_as_run_now_not_reused() {
        let src = v(Outcome::Pass, 5, &[]);
        let l = link(&v(Outcome::Pass, 5, &[]), &src, true);
        assert_eq!(
            l.unit,
            Some(Unit::Ran {
                met: true,
                tests_ran: Some(5)
            })
        );
        assert!(!l.revoke);
        assert_eq!(code(&[l.unit.unwrap()], false), Exit::Ok.code());
    }

    #[test]
    fn a_right_run_that_differs_from_the_source_is_a_spot_check_and_revokes() {
        // the source reused a pass of 5 tests; the real run passes 6
        let l = link(&v(Outcome::Pass, 6, &[]), &v(Outcome::Pass, 5, &[]), true);
        assert_eq!(l.compare, Compare::Mismatch);
        assert_eq!(l.unit, Some(Unit::SpotCheckDiffers));
        assert!(l.revoke);
        assert_eq!(
            code(&[l.unit.unwrap()], false),
            Exit::SpotCheckDiffers.code()
        );
    }

    #[test]
    fn a_wrong_real_run_is_a_failure_whatever_the_source_said() {
        // the source reused a pass; the real run fails a test: a failure, not a spot check
        let l = link(
            &v(Outcome::Fail, 5, &["t"]),
            &v(Outcome::Pass, 5, &[]),
            false,
        );
        assert!(l.revoke);
        assert_eq!(
            l.unit,
            Some(Unit::Ran {
                met: false,
                tests_ran: Some(5)
            })
        );
        assert_eq!(code(&[l.unit.unwrap()], false), Exit::Failed.code());
    }

    #[test]
    fn a_run_cut_off_is_not_met_a_time_out_is_its_own_and_a_cancel_is_the_runs() {
        let src = v(Outcome::Pass, 5, &[]);
        let mut cut = v(Outcome::Incomplete, 2, &[]);
        let l = link(&cut, &src, true);
        assert_eq!(l.compare, Compare::Incomparable);
        assert!(
            !l.revoke,
            "an unfinished audit revokes nothing; audit it again"
        );
        assert_eq!(
            l.unit,
            Some(Unit::Ran {
                met: false,
                tests_ran: Some(2)
            })
        );
        cut.outcome = Outcome::Pass;
        cut.censored = true;
        assert!(matches!(
            link(&cut, &src, true).unit,
            Some(Unit::Ran { met: false, .. })
        ));
        let t = link(&v(Outcome::Timeout, 1, &[]), &src, true);
        assert_eq!(t.unit, Some(Unit::TimedOut));
        let c = link(&v(Outcome::Cancelled, 1, &[]), &src, true);
        assert_eq!(c.unit, None);
        assert_eq!(code(&[], true), Exit::Cancelled.code());
    }
}
