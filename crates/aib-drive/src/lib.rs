//! Read-only building blocks for aib-drive. No external actions are executed.
//!
//! G2 (DECISION-aib-drive-v1 item 2): before any real action is switched on,
//! what the dry-run would have done is compared with what was really done,
//! event by event, and the matches are counted. [`compare`] judges one pair;
//! [`MatchStreak`] counts consecutive matches over a dry-run journal.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub mod decide;
pub mod observe;
pub mod report;
pub mod snapshot;
pub mod source;

/// An observation is unknown unless its source is complete and identifiable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Observation<T> {
    Known {
        value: T,
        observed_at: u64,
        /// The state the decision was made on (e.g. the snapshot's rules
        /// version and PR head). Two decisions only compare on the same one.
        version: String,
    },
    Unknown {
        reason: String,
    },
}

/// An event is a prompt to refresh its source, never an authorization.
///
/// `(source, id)` is the event's identity. The id must name the head it is
/// about (`pr/678@<sha40>`): the same PR at a new head is a new event
/// (043dd G2 L2).
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
    /// The decision in a canonical form (the sorted intent list, serialized),
    /// so the same decision always gives the same text.
    pub decision: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Comparison {
    Match,
    Different,
    Unknown,
}

/// Match / Different only when both sides are known, name the same
/// non-empty event, carry a decision, and were made on the same state
/// (`version`, 043dd G2 L1). Anything else is Unknown, never a match.
pub fn compare(
    dry: &Observation<DecisionRecord>,
    actual: &Observation<DecisionRecord>,
) -> Comparison {
    let (
        Observation::Known {
            value: d,
            version: dv,
            ..
        },
        Observation::Known {
            value: a,
            version: av,
            ..
        },
    ) = (dry, actual)
    else {
        return Comparison::Unknown;
    };
    if d.event_source.is_empty() || d.event_id.is_empty() {
        return Comparison::Unknown;
    }
    if d.decision.is_empty() || a.decision.is_empty() {
        return Comparison::Unknown;
    }
    if d.event_source != a.event_source || d.event_id != a.event_id {
        return Comparison::Unknown;
    }
    if dv.is_empty() || dv != av {
        return Comparison::Unknown;
    }
    if d.decision == a.decision {
        Comparison::Match
    } else {
        Comparison::Different
    }
}

/// Consecutive matches over a dry-run journal.
///
/// The journal gets a line every round, so the same event comes back while
/// nothing changes (043dd G2 M2): the same event with the same version and
/// the same decision is skipped (neither counted nor a reset). The same
/// event with another version or decision is contradicting evidence and
/// resets the count, as does a Different, an Unknown, an event without a
/// source or id, or a dry-run record that is about another event.
///
/// Not saved: rebuild it from the whole journal (a restart would otherwise
/// count old events again).
#[derive(Debug, Default)]
pub struct MatchStreak {
    count: u32,
    /// `(source, id)` ⇒ `(version, decision)` already counted.
    seen: BTreeMap<(String, String), (String, String)>,
}

impl MatchStreak {
    pub fn count(&self) -> u32 {
        self.count
    }

    pub fn observe(
        &mut self,
        event: &Event,
        dry: &Observation<DecisionRecord>,
        actual: &Observation<DecisionRecord>,
    ) -> u32 {
        if event.source.is_empty() || event.id.is_empty() {
            return self.reset();
        }
        let Observation::Known {
            value: d, version, ..
        } = dry
        else {
            return self.reset();
        };
        if d.event_source != event.source || d.event_id != event.id {
            return self.reset();
        }
        if compare(dry, actual) != Comparison::Match {
            return self.reset();
        }
        let key = (event.source.clone(), event.id.clone());
        let now = (version.clone(), d.decision.clone());
        match self.seen.get(&key) {
            Some(before) if *before == now => self.count,
            Some(_) => {
                self.seen.insert(key, now);
                self.reset()
            }
            None => {
                self.seen.insert(key, now);
                self.count = self.count.saturating_add(1);
                self.count
            }
        }
    }

    fn reset(&mut self) -> u32 {
        self.count = 0;
        0
    }
}
