//! S79-rust 3b (DECISION-council-driveweb r3): aibd sends the work orders aib-drive planned for
//! web seats ([`crate::drive_plan`]), as the system, through 7ebca's
//! [`crate::seat_send::system_send`]. What drive printed decides only the seat code, the text
//! and the key; aibd decides the rest:
//!
//! - the seat: a code with a live move into a web seat now (else refused, `not_a_web_seat`);
//! - the origin: only from aibd's own ledger ([`origin_for`]), never the tree file's owner
//!   field (5d33a REVIEW-d3wake r5 L4, 519d0 r3 1). None found ⇒ system_send's own rule
//!   decides (origin_not_found today; 7ebca's L4 then sends a dgrs seat the fixed form only);
//! - the pace: one work order to a seat per [`PACE`] (L3), the same key again excepted
//!   (system_send answers it with the first message and writes nothing).
//!
//! Each outcome is one log line; drive has the plan written as handed (final), so nothing here
//! is sent twice by drive, and system_send keys by `send_key`.

use crate::api::AppState;
use crate::drive_plan::Plan;
use std::collections::HashMap;
use std::time::{Duration, Instant};

/// At most one work order to a seat in this long (drive's own limit is the same 30 minutes).
pub const PACE: Duration = Duration::from_secs(30 * 60);

/// What one plan comes to before anything is written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// Send to this web seat.
    Send { seat_id: String },
    /// The code has no live move into a web seat.
    NotWeb,
    /// The seat had a work order less than [`PACE`] ago (another key).
    Paced,
}

/// When each seat last got a work order from here, and with which key.
#[derive(Debug, Default)]
pub struct Pace {
    last: HashMap<String, (Instant, String)>,
}

impl Pace {
    /// The step for `plan` whose code's web seat is `seat_id`, at `now`; a [`Step::Send`] is
    /// counted as sent.
    pub fn decide(&mut self, plan: &Plan, seat_id: Option<String>, now: Instant) -> Step {
        let Some(seat_id) = seat_id else {
            return Step::NotWeb;
        };
        if let Some((at, key)) = self.last.get(&seat_id) {
            if *key != plan.send_key && now.saturating_duration_since(*at) < PACE {
                return Step::Paced;
            }
        }
        self.last
            .insert(seat_id.clone(), (now, plan.send_key.clone()));
        Step::Send { seat_id }
    }
}

/// The seat behind a work order, from aibd's own ledger only. Nothing aibd writes yet names
/// which seat made a card or an idea (assignments are written by the owner's page, the plan
/// tree by aib-plan, a file any AI can write), so there is none: system_send then refuses it
/// as origin_not_found, and 7ebca's L4 sends a dgrs seat the fixed form only (519d0 r3 2).
pub fn origin_for(_st: &AppState, _send_key: &str) -> Option<String> {
    None
}

/// Install the executor: the web seats are the live moves, the plans go to one task that
/// sends them in order. Called once from the server, inside its runtime.
pub fn start(st: AppState) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<Plan>>();
    let db = st.messages.clone();
    let installed = crate::drive_plan::install(
        Box::new(move || db.moved_codes().unwrap_or_default()),
        Box::new(move |plans| {
            let _ = tx.send(plans);
        }),
    );
    if !installed {
        return;
    }
    tokio::spawn(async move {
        let mut pace = Pace::default();
        while let Some(plans) = rx.recv().await {
            for plan in plans {
                send_one(&st, &mut pace, &plan).await;
            }
        }
    });
}

async fn send_one(st: &AppState, pace: &mut Pace, plan: &Plan) {
    let (db, code) = (st.messages.clone(), plan.to.clone());
    let seat_id = tokio::task::spawn_blocking(move || db.moved_seat(&code))
        .await
        .ok()
        .and_then(Result::ok)
        .flatten();
    match pace.decide(plan, seat_id, Instant::now()) {
        Step::NotWeb => {
            tracing::warn!(to = %plan.to, key = %plan.send_key, "drive send: not a web seat now");
        }
        Step::Paced => {
            tracing::warn!(to = %plan.to, key = %plan.send_key, "drive send: paced (one in 30 min)");
        }
        Step::Send { seat_id } => {
            let origin = origin_for(st, &plan.send_key).unwrap_or_default();
            match crate::seat_send::system_send(st, &seat_id, &plan.text, &plan.send_key, &origin)
                .await
            {
                Ok(s) => tracing::info!(
                    to = %plan.to,
                    key = %plan.send_key,
                    message = %s.message_id,
                    state = %s.state,
                    again = s.again,
                    "drive send: sent"
                ),
                Err(why) => tracing::warn!(
                    to = %plan.to,
                    key = %plan.send_key,
                    why,
                    "drive send: refused"
                ),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(key: &str) -> Plan {
        Plan {
            to: "7ebca".into(),
            text: "派工: N-1 → 7ebca".into(),
            send_key: key.into(),
        }
    }

    #[test]
    fn a_seat_gets_one_work_order_in_thirty_minutes_and_its_own_key_again() {
        let (mut p, t0) = (Pace::default(), Instant::now());
        let s = || Some("s1".to_owned());
        assert_eq!(p.decide(&plan("k1"), None, t0), Step::NotWeb);
        assert_eq!(
            p.decide(&plan("k1"), s(), t0),
            Step::Send {
                seat_id: "s1".into()
            }
        );
        assert_eq!(
            p.decide(&plan("k2"), s(), t0 + PACE - Duration::from_secs(1)),
            Step::Paced,
            "reason: not_paced"
        );
        // the same key again goes to system_send, which answers it with the first message
        assert_eq!(
            p.decide(&plan("k1"), s(), t0 + Duration::from_secs(1)),
            Step::Send {
                seat_id: "s1".into()
            },
            "reason: same_key_paced"
        );
        // another seat is paced on its own
        assert_eq!(
            p.decide(&plan("k3"), Some("s2".into()), t0),
            Step::Send {
                seat_id: "s2".into()
            }
        );
        assert_eq!(
            p.decide(&plan("k2"), s(), t0 + PACE + Duration::from_secs(2)),
            Step::Send {
                seat_id: "s1".into()
            },
            "reason: paced_forever"
        );
    }
}
