//! The status machine (_transition_locked in 1.x), without the file reads and writes.
//!
//! The caller, inside the seat's transition lock: reads the item, runs
//! [`crate::rev::rev_check`], classifies the evidence (outside the lock is fine: it only
//! depends on the seat and the text), calls [`decide`], writes `Outcome::item` back,
//! removes the id from order.json when `leaves_order`, appends the audit line, and sends
//! the receipt after the lock is released.

use serde_json::{Map, Value};

use crate::actor::{parse_actor, ActorClass};
use crate::evidence::Kind as EvKind;
use crate::spec::{
    actor_format_hint, allowed, Reason, Status, ARCHIVE_WHYS, AUTO_DELETE_WHY,
    EXPIRED_HOLD_WHY_HEAD, REOPEN_WHY_PREFIX, SUPERSEDE_DONE_PREFIX, SYSTEM_READ_WHYS,
};
use crate::util::{is_none, py_strip, take_chars, truthy};

/// Longest `reopen_why` / `done_why` kept.
pub const WHY_MAX_CHARS: usize = 500;

pub struct Request<'a> {
    /// The receiving seat (whose queue the item is in).
    pub seat: &'a str,
    pub to: Status,
    /// `--by` as typed.
    pub actor: Option<&'a str>,
    pub why: Option<&'a str>,
    /// `--evidence` as typed.
    pub evidence: Option<&'a str>,
    /// [`crate::evidence::classify`] of `evidence`; only read when `to` is done.
    pub evidence_ref: (Option<EvKind>, Option<String>),
    /// The status the caller saw. The item rev does not change on a status move, so a
    /// caller that cares passes this; a different status refuses with `transition`.
    pub expect_status: Option<Status>,
    /// The caller measured that it runs inside a seat (tmux pane / session id). A seat
    /// may not mark a dm read as `human:`, since that turns into a "the owner read it"
    /// receipt. A notice, not a defence: a same-account shell outside a seat can still.
    pub caller_is_seat: bool,
    pub now: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReceiptState {
    Delivered,
    Expired,
    Queued,
    Refused,
    Read,
}

impl ReceiptState {
    pub fn as_str(self) -> &'static str {
        match self {
            ReceiptState::Delivered => "delivered",
            ReceiptState::Expired => "expired",
            ReceiptState::Queued => "queued",
            ReceiptState::Refused => "refused",
            ReceiptState::Read => "read",
        }
    }
}

/// A receipt to the outside sender of a global (cross-owner) dm.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Receipt {
    pub state: ReceiptState,
    /// Some("human") only when the owner read it in the inbox.
    pub by_human: bool,
}

#[derive(Clone, Debug)]
pub struct Outcome {
    pub from: Status,
    pub item: Map<String, Value>,
    /// Take the id out of order.json (the new status is not active).
    pub leaves_order: bool,
    /// Only for a global item, and only for the moves that have one.
    pub receipt: Option<Receipt>,
}

#[derive(Clone, Debug)]
pub enum Decision {
    Allow(Box<Outcome>),
    Refuse {
        reason: Reason,
        from: Status,
        /// Differs from `reason.message()` only for a malformed `--by`.
        message: String,
    },
}

impl Decision {
    pub fn reason(&self) -> Reason {
        match self {
            Decision::Allow(_) => Reason::Ok,
            Decision::Refuse { reason, .. } => *reason,
        }
    }
}

fn item_status(item: &Map<String, Value>) -> Status {
    item.get("status")
        .and_then(Value::as_str)
        .and_then(Status::parse)
        .unwrap_or(Status::Queued)
}

fn why_has_rest(why: Option<&str>, prefix: &str) -> bool {
    why.and_then(|w| w.strip_prefix(prefix))
        .is_some_and(|rest| !py_strip(rest).is_empty())
}

/// The receipt rule (_global_delivery_for_transition), before the "is it global" test.
pub fn receipt_for(
    from: Status,
    to: Status,
    class: ActorClass,
    why: Option<&str>,
) -> Option<Receipt> {
    use ReceiptState as R;
    let plain = |state| {
        Some(Receipt {
            state,
            by_human: false,
        })
    };
    match (from, to, class) {
        (_, Status::Delivered, _) => plain(R::Delivered),
        (_, Status::HoldHuman, _) => {
            if why.is_some_and(|w| w.starts_with(EXPIRED_HOLD_WHY_HEAD)) {
                plain(R::Expired)
            } else {
                None
            }
        }
        (Status::HoldHuman, Status::Queued, _) => plain(R::Queued),
        (Status::HoldHuman | Status::Queued | Status::Claimed, Status::Deleted, _) => {
            plain(R::Refused)
        }
        (_, Status::Read, ActorClass::Seat) => plain(R::Read),
        (Status::HoldHuman, Status::Read, ActorClass::Human) => Some(Receipt {
            state: R::Read,
            by_human: true,
        }),
        (Status::Delivered, Status::Done, ActorClass::Seat) => plain(R::Read),
        _ => None,
    }
}

/// Decide one move of a live item (a tombstone or a missing item is `not_found` before
/// this is reached; see [`crate::rev::rev_check`]). `item` is the upgraded item
/// ([`crate::norm::upgrade`]).
pub fn decide(item: &Map<String, Value>, req: &Request<'_>) -> Decision {
    let from = item_status(item);
    let to = req.to;
    let why = req.why;
    let refuse = |reason: Reason| Decision::Refuse {
        reason,
        from,
        message: reason.message().to_string(),
    };

    if from == Status::Deleted {
        return refuse(Reason::NotFound);
    }
    let Some(actor) = parse_actor(req.actor) else {
        let mut d = refuse(Reason::ActorMissing);
        if let (Some(given), Decision::Refuse { message, .. }) = (req.actor, &mut d) {
            let g = py_strip(given);
            if !g.is_empty() {
                *message = actor_format_hint(g, req.seat);
            }
        }
        return d;
    };
    let cls = actor.class;
    let ident = actor.ident.as_deref().unwrap_or("");
    if req.expect_status.is_some_and(|s| s != from) {
        return refuse(Reason::Transition);
    }
    let Some(who) = allowed(from, to) else {
        return refuse(Reason::Transition);
    };
    if !who.contains(&cls) {
        return refuse(Reason::Actor);
    }
    if cls == ActorClass::Seat && ident != req.seat {
        return refuse(Reason::Actor);
    }
    if cls == ActorClass::Agent && ident.split('/').next() != Some(req.seat) {
        return refuse(Reason::Actor);
    }
    if from == Status::Done && to == Status::Read && !why_has_rest(why, REOPEN_WHY_PREFIX) {
        return refuse(Reason::Transition);
    }
    if to == Status::Read
        && from == Status::HoldHuman
        && cls == ActorClass::Seat
        && truthy(item.get("global"))
    {
        return refuse(Reason::Actor);
    }
    if to == Status::Read && cls == ActorClass::Human && req.caller_is_seat {
        return refuse(Reason::Actor);
    }
    if truthy(item.get("im"))
        || item.get("sender_kind").and_then(Value::as_str) == Some("external_im")
    {
        if matches!(to, Status::Queued | Status::Claimed | Status::Delivered) {
            return refuse(Reason::ImRoomOnly);
        }
        if to == Status::Read && cls == ActorClass::Seat {
            return refuse(Reason::Actor);
        }
    }
    if to == Status::Deleted && cls == ActorClass::System && why != Some(AUTO_DELETE_WHY) {
        return refuse(Reason::Actor);
    }
    if to == Status::Read
        && cls == ActorClass::System
        && !why.is_some_and(|w| SYSTEM_READ_WHYS.contains(&w))
    {
        return refuse(Reason::Actor);
    }
    if to == Status::Done
        && cls == ActorClass::System
        && !why.is_some_and(|w| w.starts_with(SUPERSEDE_DONE_PREFIX))
    {
        return refuse(Reason::Actor);
    }
    // done: evidence, or a reason written in --why
    let mut done: Option<(&'static str, Option<&str>)> = None;
    if to == Status::Done {
        match &req.evidence_ref {
            (Some(k), r) => done = Some((k.as_str(), r.as_deref())),
            (None, _) if why.is_some_and(|w| !py_strip(w).is_empty()) => {
                done = Some(("reason", None))
            }
            (None, _) => return refuse(Reason::Evidence),
        }
    }
    if to == Status::Archived && !why.is_some_and(|w| ARCHIVE_WHYS.contains(&w)) {
        return refuse(Reason::Transition);
    }
    if to == Status::Archived && from.is_active() && why != Some("superseded") {
        return refuse(Reason::Transition);
    }

    let now = Value::from(req.now);
    let mut it = item.clone();
    let set = |it: &mut Map<String, Value>, k: &str, v: Value| {
        it.insert(k.to_string(), v);
    };
    let clear_claim = |it: &mut Map<String, Value>| {
        it.insert("claimed_at".into(), Value::Null);
        it.insert("claim_id".into(), Value::Null);
    };
    set(&mut it, "status", Value::from(to.as_str()));
    match to {
        Status::Delivered => {
            set(&mut it, "delivered_at", now.clone());
            clear_claim(&mut it);
        }
        Status::Read => {
            set(&mut it, "read_at", now.clone());
            if is_none(it.get("delivered_at")) && from != Status::Done && cls != ActorClass::Human {
                set(&mut it, "delivered_at", now.clone());
                set(&mut it, "delivered_via", Value::from("read"));
            }
            if from == Status::Done {
                for k in [
                    "done_at",
                    "done_evidence",
                    "done_kind",
                    "done_why",
                    "consolidated_into",
                ] {
                    it.remove(k);
                }
                set(&mut it, "reopened_at", now.clone());
                let w = why.map(py_strip).unwrap_or("");
                set(
                    &mut it,
                    "reopen_why",
                    Value::from(take_chars(w, WHY_MAX_CHARS)),
                );
            }
            if cls == ActorClass::System {
                set(&mut it, "read_by", Value::from("system"));
                set(&mut it, "read_why", why.map_or(Value::Null, Value::from));
            }
        }
        Status::Done => {
            let (kind, r) = done.unwrap_or(("reason", None));
            set(&mut it, "done_at", now.clone());
            let ev = req.evidence.unwrap_or("");
            set(
                &mut it,
                "done_evidence",
                if kind == "reason" {
                    Value::Null
                } else {
                    Value::from(ev)
                },
            );
            set(&mut it, "done_kind", Value::from(kind));
            if kind != "reason" {
                if let Some(r) = r.filter(|r| !r.is_empty() && *r != py_strip(ev)) {
                    set(&mut it, "done_ref", Value::from(r));
                }
            } else {
                let w = why.map(py_strip).unwrap_or("");
                set(
                    &mut it,
                    "done_why",
                    Value::from(take_chars(w, WHY_MAX_CHARS)),
                );
            }
            if is_none(it.get("read_at")) {
                set(&mut it, "read_at", now.clone());
            }
            if is_none(it.get("delivered_at")) {
                let read_at = it.get("read_at").cloned().unwrap_or(Value::Null);
                set(&mut it, "delivered_at", read_at);
                set(&mut it, "delivered_via", Value::from("read"));
            }
        }
        Status::Archived => {
            set(&mut it, "archived_at", now.clone());
            set(
                &mut it,
                "archived_why",
                why.map_or(Value::Null, Value::from),
            );
            clear_claim(&mut it);
        }
        Status::Deleted => {
            set(&mut it, "deleted_at", now.clone());
            set(
                &mut it,
                "deleted_by",
                actor.ident.clone().map_or(Value::Null, Value::from),
            );
            let w = py_strip(why.unwrap_or(""));
            set(
                &mut it,
                "deleted_why",
                if w.is_empty() {
                    Value::Null
                } else {
                    Value::from(w)
                },
            );
            clear_claim(&mut it);
        }
        Status::HoldHuman => {
            clear_claim(&mut it);
            if let Some(w) = why {
                set(&mut it, "hold_why", Value::from(w));
            }
            set(&mut it, "held_at", now.clone());
        }
        Status::Queued if from == Status::HoldHuman => {
            set(&mut it, "attempts", Value::from(0));
            for k in [
                "hold_why",
                "hold_rc",
                "held_at",
                "expired",
                "expired_at",
                "expired_why",
            ] {
                set(&mut it, k, Value::Null);
            }
        }
        Status::Queued | Status::Claimed => {}
    }
    let receipt = if truthy(it.get("global")) {
        receipt_for(from, to, cls, why)
    } else {
        None
    };
    Decision::Allow(Box::new(Outcome {
        from,
        item: it,
        leaves_order: !to.is_active(),
        receipt,
    }))
}
