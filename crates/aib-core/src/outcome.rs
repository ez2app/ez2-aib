//! Final session state from what was observed (ADR 0004 section 3).
//! Completion comes from the provider's terminal event, never from the exit
//! code alone.

use crate::state::SessionState;

/// What aibd observed about one session run.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Observed {
    /// The provider emitted its terminal success event
    /// (Claude `result/success`, Codex `turn.completed`).
    pub completed_event: bool,
    /// The provider emitted a terminal error event.
    pub error_event: bool,
    pub cancel_requested: bool,
    pub timed_out: bool,
    /// Process exit code, if it exited normally. Recorded, never decisive.
    pub exit_code: Option<i32>,
    /// After cleanup, no recorded descendant is still running.
    pub tree_clean: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Outcome {
    pub state: SessionState,
    /// Stable reason code, `None` when the state says it all.
    pub reason: Option<&'static str>,
    /// The seat must go to ERROR -> RECOVERING (leftover processes).
    pub seat_needs_recovery: bool,
}

pub fn decide(o: Observed) -> Outcome {
    let dirty = !o.tree_clean;
    let (state, reason) = if o.completed_event {
        (SessionState::Completed, dirty.then_some("tree_not_clean"))
    } else if o.error_event {
        (SessionState::Failed, dirty.then_some("tree_not_clean"))
    } else if dirty {
        (SessionState::Interrupted, Some("tree_not_clean"))
    } else if o.cancel_requested {
        (SessionState::Cancelled, None)
    } else if o.timed_out {
        (SessionState::TimedOut, None)
    } else {
        (SessionState::Interrupted, Some("missing_completion_event"))
    };
    Outcome {
        state,
        reason,
        seat_needs_recovery: dirty,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn clean() -> Observed {
        Observed {
            tree_clean: true,
            ..Default::default()
        }
    }

    #[test]
    fn exit_zero_without_completion_event_is_interrupted() {
        let o = decide(Observed {
            exit_code: Some(0),
            ..clean()
        });
        assert_eq!(o.state, SessionState::Interrupted);
        assert_eq!(o.reason, Some("missing_completion_event"));
    }

    #[test]
    fn completion_event_wins_over_nonzero_exit() {
        let o = decide(Observed {
            completed_event: true,
            exit_code: Some(143),
            ..clean()
        });
        assert_eq!(o.state, SessionState::Completed);
        assert_eq!(o.reason, None);
        assert!(!o.seat_needs_recovery);
    }

    #[test]
    fn cancel_and_timeout_need_a_clean_tree() {
        let c = decide(Observed {
            cancel_requested: true,
            ..clean()
        });
        assert_eq!(c.state, SessionState::Cancelled);
        let t = decide(Observed {
            timed_out: true,
            ..clean()
        });
        assert_eq!(t.state, SessionState::TimedOut);
        let dirty = decide(Observed {
            cancel_requested: true,
            tree_clean: false,
            ..Default::default()
        });
        assert_eq!(dirty.state, SessionState::Interrupted);
        assert_eq!(dirty.reason, Some("tree_not_clean"));
        assert!(dirty.seat_needs_recovery);
    }

    #[test]
    fn completed_with_leftovers_keeps_answer_but_flags_recovery() {
        let o = decide(Observed {
            completed_event: true,
            tree_clean: false,
            ..Default::default()
        });
        assert_eq!(o.state, SessionState::Completed);
        assert_eq!(o.reason, Some("tree_not_clean"));
        assert!(o.seat_needs_recovery);
    }

    #[test]
    fn error_event_is_failed() {
        let o = decide(Observed {
            error_event: true,
            exit_code: Some(0),
            ..clean()
        });
        assert_eq!(o.state, SessionState::Failed);
    }
}
