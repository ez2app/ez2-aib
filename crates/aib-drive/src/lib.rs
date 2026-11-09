//! Read-only building blocks for aib-drive. No external actions are executed.
use serde::{Deserialize, Serialize};

/// An observation is unknown unless its source is complete and identifiable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Observation<T> {
    Known {
        value: T,
        observed_at: u64,
        version: String,
    },
    Unknown {
        reason: String,
    },
}

/// An event is a prompt to refresh its source, never an authorization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Event {
    pub source: String,
    pub id: String,
    pub object: String,
    pub observed_at: u64,
}

/// A comparison is only valid when the two observations identify the same event.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionRecord {
    pub event_source: String,
    pub event_id: String,
    pub decision: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Comparison {
    Match,
    Different,
    Unknown,
}

pub fn compare(
    dry: &Observation<DecisionRecord>,
    actual: &Observation<DecisionRecord>,
) -> Comparison {
    match (dry, actual) {
        (Observation::Known { value: d, .. }, Observation::Known { value: a, .. })
            if !d.event_source.is_empty()
                && !d.event_id.is_empty()
                && !d.decision.is_empty()
                && !a.decision.is_empty()
                && d.event_source == a.event_source
                && d.event_id == a.event_id =>
        {
            if d.decision == a.decision {
                Comparison::Match
            } else {
                Comparison::Different
            }
        }
        _ => Comparison::Unknown,
    }
}

/// Missing, mixed or duplicated event evidence resets the consecutive-match count.
#[derive(Default)]
pub struct MatchStreak {
    count: u32,
    seen: std::collections::BTreeSet<(String, String)>,
}

impl MatchStreak {
    pub fn observe(
        &mut self,
        event: &Event,
        dry: &Observation<DecisionRecord>,
        actual: &Observation<DecisionRecord>,
    ) -> u32 {
        let valid = !event.source.is_empty()
            && !event.id.is_empty()
            && self.seen.insert((event.source.clone(), event.id.clone()));
        let belongs = matches!(dry, Observation::Known { value, .. } if value.event_source == event.source && value.event_id == event.id);
        if valid && belongs && compare(dry, actual) == Comparison::Match {
            self.count = self.count.saturating_add(1);
        } else {
            self.count = 0;
        }
        self.count
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn known(id: &str, decision: &str) -> Observation<DecisionRecord> {
        Observation::Known {
            value: DecisionRecord {
                event_source: "gh".into(),
                event_id: id.into(),
                decision: decision.into(),
            },
            observed_at: 1,
            version: "v1".into(),
        }
    }
    #[test]
    fn unknown_or_other_event_never_counts_as_match() {
        assert_eq!(
            compare(&known("1", "wait"), &known("2", "wait")),
            Comparison::Unknown
        );
        assert_eq!(
            compare(
                &known("1", "wait"),
                &Observation::Unknown {
                    reason: "missing".into()
                }
            ),
            Comparison::Unknown
        );
        assert_eq!(
            compare(&known("1", "wait"), &known("1", "merge")),
            Comparison::Different
        );
        let event = Event {
            source: "gh".into(),
            id: "1".into(),
            object: "pr".into(),
            observed_at: 1,
        };
        let mut streak = MatchStreak::default();
        assert_eq!(
            streak.observe(&event, &known("1", "wait"), &known("1", "wait")),
            1
        );
        assert_eq!(
            streak.observe(&event, &known("1", "wait"), &known("1", "wait")),
            0
        );
    }
}
