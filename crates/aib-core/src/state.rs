//! Seat and Session state machines (spec section 5). Transitions are pure
//! functions so the same rules hold in the API, in recovery and in tests.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SeatState {
    Created,
    Ready,
    Busy,
    Idle,
    Suspended,
    Archived,
    Error,
    Recovering,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SessionState {
    Pending,
    Starting,
    Running,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
    TimedOut,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("transition {from:?} -> {to:?} is not allowed")]
pub struct InvalidTransition<S: std::fmt::Debug> {
    pub from: S,
    pub to: S,
}

impl SeatState {
    pub fn can_transition(self, to: SeatState) -> bool {
        use SeatState::*;
        matches!(
            (self, to),
            (Created, Ready)
                | (Ready, Busy)
                | (Busy, Idle)
                | (Idle, Busy)
                | (Idle, Suspended)
                | (Ready, Suspended)
                | (Suspended, Ready)
                | (Busy, Error)
                | (Idle, Error)
                | (Ready, Error)
                | (Error, Recovering)
                | (Recovering, Ready)
                | (Recovering, Error)
        ) || (to == Archived && self != Archived)
    }

    pub fn transition(self, to: SeatState) -> Result<SeatState, InvalidTransition<SeatState>> {
        if self.can_transition(to) {
            Ok(to)
        } else {
            Err(InvalidTransition { from: self, to })
        }
    }
}

impl SessionState {
    pub fn is_terminal(self) -> bool {
        use SessionState::*;
        matches!(
            self,
            Completed | Failed | Cancelled | Interrupted | TimedOut
        )
    }

    pub fn can_transition(self, to: SessionState) -> bool {
        use SessionState::*;
        matches!(
            (self, to),
            (Pending, Starting | Cancelled)
                | (
                    Starting,
                    Running | Failed | Cancelled | TimedOut | Interrupted
                )
                | (
                    Running,
                    Completed | Failed | Cancelled | Interrupted | TimedOut
                )
        )
    }

    pub fn transition(
        self,
        to: SessionState,
    ) -> Result<SessionState, InvalidTransition<SessionState>> {
        if self.can_transition(to) {
            Ok(to)
        } else {
            Err(InvalidTransition { from: self, to })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seat_happy_path() {
        let s = SeatState::Created;
        let s = s.transition(SeatState::Ready).unwrap();
        let s = s.transition(SeatState::Busy).unwrap();
        let s = s.transition(SeatState::Idle).unwrap();
        let s = s.transition(SeatState::Suspended).unwrap();
        assert_eq!(
            s.transition(SeatState::Archived).unwrap(),
            SeatState::Archived
        );
    }

    #[test]
    fn seat_recovery_path() {
        let s = SeatState::Busy.transition(SeatState::Error).unwrap();
        let s = s.transition(SeatState::Recovering).unwrap();
        assert_eq!(s.transition(SeatState::Ready).unwrap(), SeatState::Ready);
    }

    #[test]
    fn seat_rejects_skips_and_leaving_archive() {
        assert!(SeatState::Created.transition(SeatState::Busy).is_err());
        assert!(SeatState::Archived.transition(SeatState::Ready).is_err());
        assert!(SeatState::Archived.transition(SeatState::Archived).is_err());
    }

    #[test]
    fn session_terminal_states_are_final() {
        use SessionState::*;
        for t in [Completed, Failed, Cancelled, Interrupted, TimedOut] {
            assert!(t.is_terminal());
            for to in [
                Pending,
                Starting,
                Running,
                Completed,
                Failed,
                Cancelled,
                Interrupted,
                TimedOut,
            ] {
                assert!(!t.can_transition(to), "{t:?} -> {to:?} must be rejected");
            }
        }
        assert!(Pending.transition(Running).is_err());
        assert_eq!(Running.transition(Interrupted).unwrap(), Interrupted);
    }
}
