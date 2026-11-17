//! S107-product slice 3 (IFACE-s107-product-a9136-1117 §2.2): every dm act sends, and what
//! later became of it, as events under `<state root>/state/aib-drive/events/<YYYY-MM>.jsonl`
//! (aib-ledger's events: one line, one write, under the folder's lock).
//!
//! * `drive_send` — written by a `--send` round for each dm it tried: `sent` (stage
//!   `delivered`), `queued` (any other stage: it is still in line, not yet known to be
//!   delivered), `not_sent` (surely did not go out, with a fixed `err` word), `unknown` (may
//!   have gone out: the send timed out, or a run before was killed while sending), and `held`
//!   for work the round would send but whose switch is off or paused.
//! * `drive_send_after` — written each round (both modes: it only reads) when a dm act sent
//!   has a status in its recipient's queue not written before (`read`, `done`, `archived`,
//!   `gone` …). The same dm and status is written once: the events themselves are the record.
//!
//! An event that cannot be written is one stderr line; act's exit code and what it sends do
//! not change (IFACE §1).

use crate::act::{Sent, Switches, Would};
use crate::intents::Outcome;
use crate::track::SentWork;
use aib_ledger::events::{self, Ctx};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// The events folder's program name.
pub const PROG: &str = "aib-drive";
pub const SEND: &str = "drive_send";
pub const AFTER: &str = "drive_send_after";

/// Who writes (the seat running drive: `AIB_SEAT_CODE`, five hex only, else null), this
/// machine, and the plan's card ids to find in a dm's text.
pub struct Run {
    pub root: PathBuf,
    pub ctx: Ctx,
    pub ids: BTreeSet<String>,
}

impl Run {
    /// `root` is the state root (drive's folder's parent).
    pub fn load(root: &Path) -> Run {
        let seat = std::env::var("AIB_SEAT_CODE")
            .ok()
            .filter(|s| events::seat_code(s));
        let ids = std::fs::read_to_string(events::plan_file(
            root,
            std::env::var_os("AIB_PLAN_DATA").as_deref(),
        ))
        .map(|t| events::plan_ids(&t))
        .unwrap_or_default();
        Run {
            root: root.to_path_buf(),
            ctx: Ctx::for_run(root, seat.as_deref()),
            ids,
        }
    }

    /// The cards a dm is about: plan card ids in its text, else the running seat's own.
    pub fn ctx_for(&self, text: &str) -> Ctx {
        let found = events::cards_in(text, |w| self.ids.contains(w));
        if found.is_empty() {
            self.ctx.clone()
        } else {
            Ctx {
                cards: found,
                ..self.ctx.clone()
            }
        }
    }
}

/// A sent dm's outcome and `err` word; `None` = nothing was done (already done or abandoned
/// before).
pub fn outcome_of(s: &Sent) -> Option<(&'static str, Option<&'static str>)> {
    Some(match &s.outcome {
        Ok(Outcome::Sent) if s.stage.as_deref() == Some("delivered") => ("sent", None),
        Ok(Outcome::Sent) => ("queued", None),
        Ok(Outcome::Dropped(_)) => ("not_sent", Some("recheck")),
        Ok(Outcome::SendFailed(_)) => ("not_sent", Some("send_failed")),
        Ok(Outcome::SendUnknown(_)) => ("unknown", Some("maybe_sent")),
        Ok(Outcome::NotResent) => ("unknown", Some("interrupted")),
        Ok(Outcome::AlreadyDone | Outcome::AlreadyAbandoned(_)) => return None,
        Err(_) => ("not_sent", Some("ledger")),
    })
}

fn text_of(w: &Would) -> String {
    std::iter::once(w.first_line.as_str())
        .chain(w.body.iter().map(String::as_str))
        .collect::<Vec<_>>()
        .join("\n")
}

/// The drive_send events of one `--send` round: one per dm tried (`sent` …), then one per
/// recipient of work held back by its switch (`held`, `why` `paused` or `switch_off`).
pub fn send_events(
    run: &Run,
    would: &[Would],
    sent: &[Sent],
    sw: Switches,
    at_ms: i64,
) -> Vec<Value> {
    let by_key: BTreeMap<&str, &Would> = would.iter().map(|w| (w.send_key.as_str(), w)).collect();
    let mut out = Vec::new();
    for s in sent {
        let Some((outcome, err)) = outcome_of(s) else {
            continue;
        };
        let w = by_key.get(s.base_key.as_str());
        let mut f = Map::new();
        f.insert("action".into(), json!(w.map(|w| w.action.as_str())));
        f.insert("to".into(), json!(s.to));
        f.insert("dm_id".into(), json!(s.dm_id));
        f.insert("key".into(), json!(s.key));
        f.insert("stage".into(), json!(s.stage));
        f.insert("dry_run".into(), json!(false));
        if let Some(e) = err {
            f.insert("err".into(), json!(e));
        }
        let ctx = w.map_or_else(|| run.ctx.clone(), |w| run.ctx_for(&text_of(w)));
        out.push(events::build(&ctx, SEND, outcome, at_ms, f));
    }
    for w in would {
        if !crate::act::SENDABLE.contains(&w.action.as_str())
            || crate::act::would_send(sw, &w.action)
        {
            continue;
        }
        let many = w.to.contains(',');
        for to in w.to.split(',') {
            let key = if many {
                format!("{}@{to}", w.send_key)
            } else {
                w.send_key.clone()
            };
            let mut f = Map::new();
            f.insert("action".into(), json!(w.action));
            f.insert("to".into(), json!(to));
            f.insert("dm_id".into(), Value::Null);
            f.insert("key".into(), json!(key));
            f.insert("stage".into(), Value::Null);
            f.insert("dry_run".into(), json!(false));
            let paused = sw.paused && sw.get(&w.action);
            f.insert(
                "why".into(),
                json!(if paused { "paused" } else { "switch_off" }),
            );
            out.push(events::build(
                &run.ctx_for(&text_of(w)),
                SEND,
                "held",
                at_ms,
                f,
            ));
        }
    }
    out
}

/// What the events written before say: each dm's statuses already written (its send stage
/// counts as written) and the cards its drive_send event carried. Read from this month's
/// file and the one before (act follows a dm for a day).
pub fn written(root: &Path, now_ms: i64) -> (BTreeSet<(String, String)>, BTreeMap<String, Value>) {
    let d = events::dir(root, PROG);
    let names: BTreeSet<String> = [now_ms, now_ms.saturating_sub(2 * 86_400_000)]
        .iter()
        .map(|ms| events::month_file(*ms))
        .collect();
    let mut seen = BTreeSet::new();
    let mut cards = BTreeMap::new();
    for n in names {
        let Ok(t) = std::fs::read_to_string(d.join(n)) else {
            continue;
        };
        for v in t
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        {
            let Some(id) = v["dm_id"].as_str() else {
                continue;
            };
            match v["kind"].as_str() {
                Some(SEND) => {
                    cards.insert(id.to_owned(), v["cards"].clone());
                }
                Some(AFTER) => {
                    if let Some(s) = v["status"].as_str() {
                        seen.insert((id.to_owned(), s.to_owned()));
                    }
                }
                _ => {}
            }
        }
    }
    (seen, cards)
}

/// The drive_send_after events due this round: a followed dm whose queue status was read and
/// is neither its send stage nor a status written before. Each dm and status once.
pub fn after_events(
    run: &Run,
    followed: &[(SentWork, Option<String>)],
    seen: &BTreeSet<(String, String)>,
    cards: &BTreeMap<String, Value>,
    at_ms: i64,
) -> Vec<Value> {
    let mut done: BTreeSet<(String, String)> = BTreeSet::new();
    let mut out = Vec::new();
    for (w, st) in followed {
        let Some(st) = st else { continue };
        let k = (w.dm_id.clone(), st.clone());
        if w.dm_id.is_empty() || *st == w.stage || seen.contains(&k) || !done.insert(k) {
            continue;
        }
        let mut ctx = run.ctx.clone();
        if let Some(c) = cards.get(&w.dm_id).and_then(Value::as_array) {
            ctx.cards = c
                .iter()
                .filter_map(|c| c.as_str().map(str::to_owned))
                .collect();
        }
        let mut f = Map::new();
        f.insert("action".into(), json!(w.action));
        f.insert("to".into(), json!(w.to));
        f.insert("dm_id".into(), json!(w.dm_id));
        f.insert("key".into(), json!(w.key));
        f.insert("status".into(), json!(st));
        f.insert("observed_at".into(), json!(crate::act::rfc3339_ms(at_ms)));
        out.push(events::build(&ctx, AFTER, "observed", at_ms, f));
    }
    out
}

/// Write each event; a failure is said once per event and changes nothing else.
pub fn write_all(root: &Path, evs: &[Value], at_ms: i64) {
    for e in evs {
        events::append_or_warn(root, PROG, e, at_ms);
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_900_000_000;

    fn run(dir: &Path) -> Run {
        Run {
            root: dir.to_path_buf(),
            ctx: Ctx {
                host: Some("mini".into()),
                seat: Some("a9136".into()),
                cards: vec!["S79".into()],
                status_item: Some("S79 drive".into()),
            },
            ids: ["S79", "S107-loop", "T45"]
                .iter()
                .map(|s| s.to_string())
                .collect(),
        }
    }

    fn would(action: &str, key: &str, to: &str, first: &str) -> Would {
        Would {
            action: action.into(),
            to: to.into(),
            first_line: first.into(),
            send_key: key.into(),
            ..Default::default()
        }
    }

    fn sent(
        key: &str,
        to: &str,
        o: Result<Outcome, String>,
        stage: Option<&str>,
        id: Option<&str>,
    ) -> Sent {
        Sent {
            key: key.into(),
            base_key: key.into(),
            to: to.into(),
            outcome: o,
            stage: stage.map(str::to_owned),
            dm_id: id.map(str::to_owned),
        }
    }

    fn sw_on() -> Switches {
        Switches {
            nudge: true,
            open_review: true,
            dispatch: true,
            notify_fix: true,
            ..Default::default()
        }
    }

    /// IFACE §8 item 2: sent / queued / not sent / may have gone / switch off, one event each.
    #[test]
    fn each_dm_tried_is_one_event_and_a_switch_off_is_held() {
        let t = crate::act::tests::Scratch::new("sendev");
        let r = run(t.path());
        let ws = vec![
            would("nudge", "k1", "5d33a", "提醒: S107-loop 審稿"),
            would("open_review", "k2", "f1fae", "審查: #806"),
            would("notify_fix", "k3", "043dd", "修紅: #748"),
            would("nudge", "k4", "7b81e", "提醒"),
            would("dispatch", "k5", "519d0,5d33a", "派工: T45 → 席"),
        ];
        let ss = vec![
            sent(
                "k1",
                "5d33a",
                Ok(Outcome::Sent),
                Some("delivered"),
                Some("q_1"),
            ),
            sent(
                "k2",
                "f1fae",
                Ok(Outcome::Sent),
                Some("sort-queued"),
                Some("q_2"),
            ),
            sent(
                "k3",
                "043dd",
                Ok(Outcome::SendFailed("/Users/x boom".into())),
                None,
                None,
            ),
            sent(
                "k4",
                "7b81e",
                Ok(Outcome::SendUnknown("timeout".into())),
                None,
                None,
            ),
            sent("k9", "7b81e", Ok(Outcome::AlreadyDone), None, None),
            sent(
                "k8",
                "7b81e",
                Ok(Outcome::AlreadyAbandoned("x".into())),
                None,
                None,
            ),
        ];
        let sw = Switches {
            dispatch: false,
            ..sw_on()
        };
        let ev = send_events(&r, &ws, &ss, sw, NOW);
        let got: Vec<(String, String)> = ev
            .iter()
            .map(|e| {
                (
                    e["key"].as_str().unwrap().to_owned(),
                    e["outcome"].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        let want: Vec<(String, String)> = [
            ("k1", "sent"),
            ("k2", "queued"),
            ("k3", "not_sent"),
            ("k4", "unknown"),
            ("k5@519d0", "held"),
            ("k5@5d33a", "held"),
        ]
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
        assert_eq!(got, want);
        assert_eq!(ev[0]["kind"], SEND);
        assert_eq!(ev[0]["dm_id"], "q_1");
        assert_eq!(ev[0]["action"], "nudge");
        assert_eq!(
            ev[0]["cards"],
            json!(["S107-loop"]),
            "a card in the dm's text"
        );
        assert_eq!(
            ev[2]["err"], "send_failed",
            "a fixed word, never the error text"
        );
        assert!(!ev[2].to_string().contains("/Users/"));
        assert_eq!(ev[3]["err"], "maybe_sent");
        assert_eq!(ev[4]["why"], "switch_off");
        assert_eq!(ev[4]["cards"], json!(["T45"]));
        assert_eq!(
            ev[1]["cards"],
            json!(["S79"]),
            "no card in the text: the seat's own"
        );
        assert_eq!(ev[0]["seat"], "a9136");
    }

    #[test]
    fn a_pause_holds_new_work_and_says_paused() {
        let t = crate::act::tests::Scratch::new("sendev");
        let r = run(t.path());
        let ws = vec![
            would("open_review", "k2", "f1fae", "審查"),
            would("nudge", "k1", "5d33a", "提醒"),
        ];
        let sw = Switches {
            paused: true,
            ..sw_on()
        };
        let ev = send_events(&r, &ws, &[], sw, NOW);
        assert_eq!(ev.len(), 1, "{ev:?}");
        assert_eq!(ev[0]["key"], "k2");
        assert_eq!(ev[0]["why"], "paused");
    }

    fn work(id: &str, stage: &str) -> SentWork {
        SentWork {
            key: format!("key-{id}"),
            action: "open_review".into(),
            to: "f1fae".into(),
            dm_id: id.into(),
            stage: stage.into(),
            at_ms: NOW - 60_000,
        }
    }

    /// IFACE §8 item 2: the dm turns done ⇒ one drive_send_after; looked at again ⇒ not again.
    #[test]
    fn a_status_change_is_written_once_with_the_send_events_cards() {
        let t = crate::act::tests::Scratch::new("sendev");
        let r = run(t.path());
        let ws = vec![would("open_review", "key-q_1", "f1fae", "審查: S107-loop")];
        let ss = vec![sent(
            "key-q_1",
            "f1fae",
            Ok(Outcome::Sent),
            Some("delivered"),
            Some("q_1"),
        )];
        write_all(
            t.path(),
            &send_events(&r, &ws, &ss, sw_on(), NOW - 60_000),
            NOW - 60_000,
        );
        let followed = vec![
            (work("q_1", "delivered"), Some("delivered".to_owned())),
            (work("q_1", "delivered"), Some("done".to_owned())),
            (work("q_2", "sort-queued"), None),
            (work("q_3", "sort-queued"), Some("gone".to_owned())),
        ];
        let (seen, cards) = written(t.path(), NOW);
        let ev = after_events(&r, &followed, &seen, &cards, NOW);
        let got: Vec<(&str, &str)> = ev
            .iter()
            .map(|e| (e["dm_id"].as_str().unwrap(), e["status"].as_str().unwrap()))
            .collect();
        assert_eq!(
            got,
            vec![("q_1", "done"), ("q_3", "gone")],
            "the send stage and an unread queue write nothing"
        );
        assert_eq!(ev[0]["kind"], AFTER);
        assert_eq!(
            ev[0]["cards"],
            json!(["S107-loop"]),
            "the send event's cards"
        );
        assert_eq!(ev[1]["cards"], json!(["S79"]));
        assert!(ev[0]["observed_at"].as_str().unwrap().ends_with('Z'));
        write_all(t.path(), &ev, NOW);
        let (seen, cards) = written(t.path(), NOW + 600_000);
        assert!(after_events(&r, &followed, &seen, &cards, NOW + 600_000).is_empty());
    }

    #[test]
    fn a_folder_that_cannot_be_written_changes_nothing() {
        let t = crate::act::tests::Scratch::new("sendev");
        let blocker = t.path().join("state");
        std::fs::write(&blocker, b"a file where the folder would go").unwrap();
        let r = run(t.path());
        let ev = send_events(
            &r,
            &[would("nudge", "k", "5d33a", "x")],
            &[sent(
                "k",
                "5d33a",
                Ok(Outcome::Sent),
                Some("delivered"),
                Some("q_9"),
            )],
            sw_on(),
            NOW,
        );
        write_all(t.path(), &ev, NOW);
        assert!(blocker.is_file());
    }
}
