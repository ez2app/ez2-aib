//! How long an owner-approved "beyond this mode" ask stays open (DESIGN-persona-propose-
//! osverify-c2f0b-1123 r1 §2; Andy 11-23 02:1x「讓用戶勾選同意開放時間」, DECISION-council-
//! aipartner 十一、十二): only this once / 15 minutes / 1 hour / until this seat ends.
//!
//! - The choice ([`Hold`]) travels in the ask's own parameters as `"hold"`, so it is in the
//!   parameters' SHA-256, in the signed binding and in the window's words: the page cannot
//!   change it after the owner pressed. [`hold_in`] reads it and refuses a choice the
//!   operation's row of [`HOLD_ALLOWED`] does not give.
//! - [`HOLD_ALLOWED`] is keyed by risk class, as DESIGN-sandboxnet-3e53b r5 §11.1 / §12 L8 /
//!   §14 class connections (f163c PREREAD r0 M2): loopback and this machine's own addresses
//!   never get "until this seat ends"; connecting back to an aibd is not offered at all in the
//!   first version.
//! - What was granted ([`Grants`]) lives in aibd's memory only: a restart of aibd drops every
//!   grant (only ever stricter). Nothing is timed: every use asks [`Grants::live`] with the
//!   clock, and a once-grant is used up by that same call. A seat's grants all end when the
//!   seat ends **or starts again** ([`seat_ended`], called from `sessions` at every launch
//!   and every finish; f163c L1).
//!
//! The defence is the owner's fingerprint / password / Hello on a window whose words carry
//! the choice, plus this table and this check in aibd's own process; a sandboxed seat cannot
//! reach either. A full-rights (dgrs) seat is outside this, as it is outside every rule here.

use std::sync::{LazyLock, Mutex};

/// Host seat reading / writing its own folder (DESIGN-hostmode-3e53b r3 §9).
pub const OP_FS_OPEN: &str = "fs.open";
/// 10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7: sandboxnet's ordinary class (link-local is
/// "this machine" and refused outright in the first version, sandboxnet r6 §15.5).
pub const OP_NET_PRIVATE: &str = "net.private";
/// Loopback and this machine's own interface addresses: sandboxnet's high-risk class (L8).
pub const OP_NET_LOCAL_SELF: &str = "net.local_self";
/// Back to an aibd on this machine: kept for the design, not offered in the first version.
pub const OP_NET_AIBD_SELF: &str = "net.aibd_self";

/// How long the owner opens it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Hold {
    Once,
    Min15,
    Hour1,
    UntilSeatEnds,
}

impl Hold {
    /// The word in the parameters.
    pub fn word(self) -> &'static str {
        match self {
            Hold::Once => "once",
            Hold::Min15 => "min15",
            Hold::Hour1 => "hour1",
            Hold::UntilSeatEnds => "until_seat_ends",
        }
    }

    pub fn parse(s: &str) -> Option<Hold> {
        [Hold::Once, Hold::Min15, Hold::Hour1, Hold::UntilSeatEnds]
            .into_iter()
            .find(|h| h.word() == s)
    }

    /// Seconds it lasts; none for once (one use) and until the seat ends.
    pub fn secs(self) -> Option<u64> {
        match self {
            Hold::Min15 => Some(15 * 60),
            Hold::Hour1 => Some(60 * 60),
            Hold::Once | Hold::UntilSeatEnds => None,
        }
    }

    /// What the window says (after the plain sentence).
    pub fn words(self) -> &'static str {
        match self {
            Hold::Once => "只這一次",
            Hold::Min15 => "開放 15 分鐘(從你按下起算)",
            Hold::Hour1 => "開放 1 小時(從你按下起算)",
            Hold::UntilSeatEnds => "開到這一位關掉或重開",
        }
    }
}

/// Each operation that takes a hold, and the holds it may take (f163c M2). An operation not
/// here takes none; one here with an empty list is kept for the design and refused.
///
/// `net.private` "once" is free of the fingerprint in sandboxnet (Andy 11-23 01:3x) and does
/// not come through here; it stays listed so that a page that does send it is not refused.
pub const HOLD_ALLOWED: &[(&str, &[Hold])] = &[
    (
        OP_FS_OPEN,
        &[Hold::Once, Hold::Min15, Hold::Hour1, Hold::UntilSeatEnds],
    ),
    (
        OP_NET_PRIVATE,
        &[Hold::Once, Hold::Min15, Hold::Hour1, Hold::UntilSeatEnds],
    ),
    (OP_NET_LOCAL_SELF, &[Hold::Once, Hold::Min15, Hold::Hour1]),
    // sandboxnet r5 §14: not in the first version
    (OP_NET_AIBD_SELF, &[]),
];

/// A refusal word.
pub const NOT_ALLOWED: &str = "hold_not_allowed";
/// An operation that needs a hold was asked without one.
pub const MISSING: &str = "hold_missing";

fn row(operation: &str) -> Option<&'static [Hold]> {
    HOLD_ALLOWED
        .iter()
        .find(|(op, _)| *op == operation)
        .map(|(_, h)| *h)
}

/// The hold an ask carries: none for an operation that takes none (and then the parameters
/// must not name one), the hold for one in [`HOLD_ALLOWED`] (and then they must, with a
/// choice its row gives).
pub fn hold_in(operation: &str, params: &[u8]) -> Result<Option<Hold>, &'static str> {
    let v: Option<serde_json::Value> = serde_json::from_slice(params).ok();
    let named = v.as_ref().and_then(|v| v.get("hold"));
    let Some(allowed) = row(operation) else {
        return match named {
            Some(_) => Err(NOT_ALLOWED),
            None => Ok(None),
        };
    };
    let word = named.ok_or(MISSING)?.as_str().ok_or(NOT_ALLOWED)?;
    let h = Hold::parse(word).ok_or(NOT_ALLOWED)?;
    if allowed.contains(&h) {
        Ok(Some(h))
    } else {
        Err(NOT_ALLOWED)
    }
}

/// One grant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grant {
    pub id: String,
    pub seat_id: String,
    pub operation: String,
    pub resource: String,
    pub hold: Hold,
    /// Unix second it ends; none for once and until-the-seat-ends.
    pub until: Option<u64>,
}

/// The grants of this aibd.
#[derive(Default)]
pub struct Grants {
    list: Mutex<Vec<Grant>>,
    next: Mutex<u64>,
}

impl Grants {
    fn list(&self) -> std::sync::MutexGuard<'_, Vec<Grant>> {
        self.list.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Records what the owner approved at `approved_at`; the caller has already checked the
    /// ticket (`owner_ok::approved_for`) for exactly this operation, resource and hold. The
    /// table is checked again here, so no path grants what it does not give.
    pub fn give(
        &self,
        seat_id: &str,
        operation: &str,
        resource: &str,
        hold: Hold,
        approved_at: u64,
    ) -> Result<String, &'static str> {
        if !row(operation).is_some_and(|r| r.contains(&hold)) {
            return Err(NOT_ALLOWED);
        }
        let n = {
            let mut n = self.next.lock().unwrap_or_else(|e| e.into_inner());
            *n += 1;
            *n
        };
        let id = format!("gr_{n}");
        let until = hold.secs().map(|s| approved_at + s);
        let mut l = self.list();
        l.retain(|g| !(g.seat_id == seat_id && g.operation == operation && g.resource == resource));
        l.push(Grant {
            id: id.clone(),
            seat_id: seat_id.to_owned(),
            operation: operation.to_owned(),
            resource: resource.to_owned(),
            hold,
            until,
        });
        Ok(id)
    }

    /// Whether this seat may do this operation on this resource now. Exactly the same three;
    /// past its end ⇒ no (and dropped); a once-grant answers yes one time and is gone.
    pub fn live(&self, seat_id: &str, operation: &str, resource: &str, now: u64) -> bool {
        let mut l = self.list();
        l.retain(|g| g.until.is_none_or(|u| now < u)); // past its end: gone
        let Some(i) = l.iter().position(|g| {
            g.seat_id == seat_id && g.operation == operation && g.resource == resource
        }) else {
            return false;
        };
        if l[i].hold == Hold::Once {
            l.remove(i);
        }
        true
    }

    /// Takes one back (the owner, on the built-in page; no fingerprint: only stricter).
    pub fn revoke(&self, id: &str) -> bool {
        let mut l = self.list();
        let before = l.len();
        l.retain(|g| g.id != id);
        l.len() != before
    }

    /// A seat's live grants, for its page's countdowns.
    pub fn of_seat(&self, seat_id: &str, now: u64) -> Vec<Grant> {
        let mut l = self.list();
        l.retain(|g| g.until.is_none_or(|u| now < u));
        l.iter().filter(|g| g.seat_id == seat_id).cloned().collect()
    }

    /// Every grant of a seat ends: it ended, or it starts again.
    pub fn seat_ended(&self, seat_id: &str) {
        self.list().retain(|g| g.seat_id != seat_id);
    }
}

/// This aibd's grants.
pub static GRANTS: LazyLock<Grants> = LazyLock::new(Grants::default);

/// [`Grants::seat_ended`] on this aibd's grants (sessions: every launch and every finish).
pub fn seat_ended(seat_id: &str) {
    GRANTS.seat_ended(seat_id);
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn p(hold: &str) -> Vec<u8> {
        format!(r#"{{"folder":"seat-work/seat_ab","hold":"{hold}"}}"#).into_bytes()
    }

    #[test]
    fn the_words_round_trip() {
        for h in [Hold::Once, Hold::Min15, Hold::Hour1, Hold::UntilSeatEnds] {
            assert_eq!(Hold::parse(h.word()), Some(h));
        }
        assert_eq!(Hold::parse("2h"), None);
    }

    // HD2
    #[test]
    fn local_self_never_stays_until_the_seat_ends() {
        assert_eq!(
            hold_in(OP_NET_LOCAL_SELF, &p("until_seat_ends")),
            Err(NOT_ALLOWED)
        );
        assert_eq!(
            hold_in(OP_NET_LOCAL_SELF, &p("hour1")),
            Ok(Some(Hold::Hour1))
        );
        assert_eq!(hold_in(OP_NET_LOCAL_SELF, &p("once")), Ok(Some(Hold::Once)));
    }

    // HD2
    #[test]
    fn back_to_aibd_takes_no_hold_in_the_first_version() {
        for h in ["once", "min15", "hour1", "until_seat_ends"] {
            assert_eq!(hold_in(OP_NET_AIBD_SELF, &p(h)), Err(NOT_ALLOWED), "{h}");
        }
    }

    // HD2
    #[test]
    fn an_operation_not_in_the_table_takes_no_hold() {
        assert_eq!(hold_in("repo.register", &p("min15")), Err(NOT_ALLOWED));
        assert_eq!(hold_in("repo.register", br#"{"name":"x"}"#), Ok(None));
        assert_eq!(hold_in("repo.register", b"not json"), Ok(None));
    }

    #[test]
    fn an_operation_in_the_table_needs_a_known_hold() {
        assert_eq!(hold_in(OP_FS_OPEN, br#"{"folder":"x"}"#), Err(MISSING));
        assert_eq!(hold_in(OP_FS_OPEN, &p("2h")), Err(NOT_ALLOWED));
        assert_eq!(hold_in(OP_FS_OPEN, br#"{"hold":1}"#), Err(NOT_ALLOWED));
        assert_eq!(
            hold_in(OP_FS_OPEN, &p("until_seat_ends")),
            Ok(Some(Hold::UntilSeatEnds))
        );
    }

    #[test]
    fn give_checks_the_table_again() {
        let g = Grants::default();
        assert_eq!(
            g.give(
                "seat_ab",
                OP_NET_LOCAL_SELF,
                "127.0.0.1:5432",
                Hold::UntilSeatEnds,
                0
            ),
            Err(NOT_ALLOWED)
        );
        assert!(!g.live("seat_ab", OP_NET_LOCAL_SELF, "127.0.0.1:5432", 1));
    }

    // HD3
    #[test]
    fn a_timed_grant_ends_on_time() {
        let g = Grants::default();
        g.give("seat_ab", OP_FS_OPEN, "f", Hold::Min15, 1000)
            .unwrap();
        assert!(g.live("seat_ab", OP_FS_OPEN, "f", 1000));
        assert!(g.live("seat_ab", OP_FS_OPEN, "f", 1000 + 15 * 60 - 1));
        assert!(!g.live("seat_ab", OP_FS_OPEN, "f", 1000 + 15 * 60));
        assert!(g.of_seat("seat_ab", 1000 + 15 * 60).is_empty());
    }

    // HD3
    #[test]
    fn once_is_used_by_the_first_use() {
        let g = Grants::default();
        g.give("seat_ab", OP_FS_OPEN, "f", Hold::Once, 1000)
            .unwrap();
        assert!(g.live("seat_ab", OP_FS_OPEN, "f", 1001));
        assert!(!g.live("seat_ab", OP_FS_OPEN, "f", 1001));
    }

    #[test]
    fn only_the_same_seat_operation_and_resource() {
        let g = Grants::default();
        g.give("seat_ab", OP_NET_PRIVATE, "10.0.0.5:22", Hold::Hour1, 0)
            .unwrap();
        assert!(!g.live("seat_cd", OP_NET_PRIVATE, "10.0.0.5:22", 1));
        assert!(!g.live("seat_ab", OP_NET_LOCAL_SELF, "10.0.0.5:22", 1));
        assert!(!g.live("seat_ab", OP_NET_PRIVATE, "10.0.0.5:2222", 1));
        assert!(!g.live("seat_ab", OP_NET_PRIVATE, "10.0.0.5", 1));
        assert!(g.live("seat_ab", OP_NET_PRIVATE, "10.0.0.5:22", 1));
    }

    // HD4
    #[test]
    fn until_the_seat_ends_ends_when_it_ends_or_starts_again() {
        let g = Grants::default();
        g.give("seat_ab", OP_FS_OPEN, "f", Hold::UntilSeatEnds, 0)
            .unwrap();
        g.give("seat_cd", OP_FS_OPEN, "f", Hold::UntilSeatEnds, 0)
            .unwrap();
        assert!(g.live("seat_ab", OP_FS_OPEN, "f", 99_999_999));
        g.seat_ended("seat_ab");
        assert!(!g.live("seat_ab", OP_FS_OPEN, "f", 1));
        assert!(g.live("seat_cd", OP_FS_OPEN, "f", 1));
    }

    // HD4
    #[test]
    fn revoke_takes_it_back() {
        let g = Grants::default();
        let id = g.give("seat_ab", OP_FS_OPEN, "f", Hold::Hour1, 0).unwrap();
        assert!(g.revoke(&id));
        assert!(!g.live("seat_ab", OP_FS_OPEN, "f", 1));
        assert!(!g.revoke(&id));
    }

    #[test]
    fn a_new_grant_for_the_same_three_replaces_the_old() {
        let g = Grants::default();
        g.give("seat_ab", OP_FS_OPEN, "f", Hold::Hour1, 0).unwrap();
        g.give("seat_ab", OP_FS_OPEN, "f", Hold::Once, 0).unwrap();
        assert_eq!(g.of_seat("seat_ab", 1).len(), 1);
        assert!(g.live("seat_ab", OP_FS_OPEN, "f", 1));
        assert!(!g.live("seat_ab", OP_FS_OPEN, "f", 1));
    }
}
