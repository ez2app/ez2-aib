//! The network exception card's choices and a seat's "always allow" list (DESIGN-sandboxnet r8
//! §4 and §15.6; ebab8 11-23 02:51 nod; Andy 11-23 02:0x「同意以下 一直允許」, 02:1x「1+2 都可以」).
//!
//! First slice of A6c, the deciding only: which choices a card has, which of them need the owner's
//! own verification, how long each lasts, and the per-seat list an "always" writes to. The card on
//! the page, the verification window ([`crate::owner_ok`]) and where the list is kept (aibd's
//! store, not a settings file: §4) are the next slices.
//!
//! - Six choices for a private address (`net.private`): 拒絕 / 只這一次 / 15 分鐘 / 1 小時 /
//!   到這一位關掉 / 一直允許這個位址. The last four need the owner (Touch ID / login password /
//!   Windows Hello); a missing, cancelled or failed verification is a refusal ([`decide`]).
//! - The ids are c2f0b's `grants::Hold` words (`once` / `min15` / `hour1` / `until_seat_ends`;
//!   one vocabulary, f163c REVIEW-touchhud M1), plus `deny` and `always`.
//! - 「到這一位關掉」 ends when the seat ends or restarts and is not written to the list; 「一直
//!   允許」 is written to the seat's own list as one `ip:port` (no ranges, no wildcards), lasts over
//!   restarts and is taken back only from the partner page; taking it back says which of the
//!   seat's open tunnels to close ([`AllowList::revoke`]).
//! - What a seat asks for is never on its list until the owner said so: [`AllowList::add`] takes
//!   a [`Verified`] that only [`decide`] makes.

use std::collections::BTreeSet;
use std::net::{IpAddr, SocketAddr};
use std::time::Duration;

/// A card's choice, as the page sends it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Choice {
    Deny,
    Once,
    Min15,
    Hour1,
    UntilClose,
    Always,
}

/// The card's choices in the order shown, with their ids and words.
pub const CHOICES: [(Choice, &str, &str); 6] = [
    (Choice::Deny, "deny", "拒絕"),
    (Choice::Once, "once", "只這一次"),
    (Choice::Min15, "min15", "15 分鐘"),
    (Choice::Hour1, "hour1", "1 小時"),
    (Choice::UntilClose, "until_seat_ends", "到這一位關掉"),
    (Choice::Always, "always", "一直允許這個位址"),
];

/// How long a yes lasts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lasts {
    /// This one connection.
    Once,
    /// From the yes for this long.
    For(Duration),
    /// Until the seat ends or restarts.
    UntilClose,
    /// On the seat's list until taken back.
    Always,
}

impl Choice {
    /// The page's id; anything else is not a choice (and so a refusal, [`decide`]).
    pub fn parse(id: &str) -> Option<Choice> {
        CHOICES
            .iter()
            .find(|(_, i, _)| *i == id)
            .map(|(c, _, _)| *c)
    }

    pub fn id(self) -> &'static str {
        CHOICES
            .iter()
            .find(|(c, _, _)| *c == self)
            .map_or("deny", |(_, i, _)| *i)
    }

    /// Whether a yes needs the owner's own verification (§15.6: all but 拒絕 and 只這一次).
    pub fn needs_owner(self) -> bool {
        !matches!(self, Choice::Deny | Choice::Once)
    }

    /// How long a yes lasts; `None` for 拒絕.
    pub fn lasts(self) -> Option<Lasts> {
        match self {
            Choice::Deny => None,
            Choice::Once => Some(Lasts::Once),
            Choice::Min15 => Some(Lasts::For(Duration::from_secs(15 * 60))),
            Choice::Hour1 => Some(Lasts::For(Duration::from_secs(60 * 60))),
            Choice::UntilClose => Some(Lasts::UntilClose),
            Choice::Always => Some(Lasts::Always),
        }
    }
}

/// What the owner's verification came to, from [`crate::owner_ok`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Owner {
    /// Not asked (the choice needs no verification).
    NotAsked,
    /// Verified by the OS.
    Yes,
    /// Cancelled, timed out, could not be shown, not this kind of machine: all the same.
    Failed,
}

/// Proof that a yes is the owner's where it has to be; only [`decide`] makes one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Verified {
    lasts: Lasts,
}

impl Verified {
    pub fn lasts(&self) -> Lasts {
        self.lasts
    }
}

/// The card's answer: `Some` lets the connection through for that long, `None` refuses it. A
/// choice that is not on the card, or one that needs the owner without the owner's yes, refuses.
pub fn decide(choice: Option<&str>, owner: Owner) -> Option<Verified> {
    let c = Choice::parse(choice?)?;
    let lasts = c.lasts()?;
    if c.needs_owner() && owner != Owner::Yes {
        return None;
    }
    Some(Verified { lasts })
}

/// One seat's "always allow" list: exact `ip:port`, nothing wider.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AllowList {
    seat: String,
    rows: BTreeSet<SocketAddr>,
}

impl AllowList {
    pub fn new(seat: &str) -> Self {
        Self {
            seat: seat.to_owned(),
            rows: BTreeSet::new(),
        }
    }

    pub fn seat(&self) -> &str {
        &self.seat
    }

    /// Writes `ip:port` when the owner chose 「一直允許」; any other yes is not written.
    pub fn add(&mut self, at: SocketAddr, yes: &Verified) -> bool {
        yes.lasts == Lasts::Always && self.rows.insert(at)
    }

    /// Whether this exact address and port is on the list.
    pub fn has(&self, ip: IpAddr, port: u16) -> bool {
        self.rows.contains(&SocketAddr::new(ip, port))
    }

    /// Takes `at` back; returns the open tunnels (of this seat, to that address and port) to
    /// close now (§4 L4), or `None` when it was not on the list.
    pub fn revoke(&mut self, at: SocketAddr, open: &[SocketAddr]) -> Option<Vec<SocketAddr>> {
        if !self.rows.remove(&at) {
            return None;
        }
        Some(open.iter().copied().filter(|o| *o == at).collect())
    }

    pub fn rows(&self) -> impl Iterator<Item = &SocketAddr> {
        self.rows.iter()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn at(s: &str) -> SocketAddr {
        s.parse().unwrap()
    }

    #[test]
    fn the_card_has_six_choices_and_four_need_the_owner() {
        let ids: Vec<&str> = CHOICES.iter().map(|(_, i, _)| *i).collect();
        assert_eq!(
            ids,
            [
                "deny",
                "once",
                "min15",
                "hour1",
                "until_seat_ends",
                "always"
            ]
        );
        let words: Vec<&str> = CHOICES.iter().map(|(_, _, w)| *w).collect();
        assert_eq!(
            words,
            [
                "拒絕",
                "只這一次",
                "15 分鐘",
                "1 小時",
                "到這一位關掉",
                "一直允許這個位址"
            ]
        );
        let need: Vec<&str> = CHOICES
            .iter()
            .filter(|(c, _, _)| c.needs_owner())
            .map(|(_, i, _)| *i)
            .collect();
        assert_eq!(
            need,
            ["min15", "hour1", "until_seat_ends", "always"],
            "reason: owner_not_asked"
        );
        for (c, i, _) in CHOICES {
            assert_eq!(Choice::parse(i), Some(c));
            assert_eq!(c.id(), i);
        }
        assert_eq!(
            Choice::Min15.lasts(),
            Some(Lasts::For(Duration::from_secs(900)))
        );
        assert_eq!(
            Choice::Hour1.lasts(),
            Some(Lasts::For(Duration::from_secs(3600)))
        );
        assert_eq!(Choice::Deny.lasts(), None);
    }

    #[test]
    fn a_yes_that_needs_the_owner_without_the_owner_refuses() {
        // once needs no one
        assert_eq!(
            decide(Some("once"), Owner::NotAsked).map(|v| v.lasts()),
            Some(Lasts::Once)
        );
        for id in ["min15", "hour1", "until_seat_ends", "always"] {
            assert_eq!(
                decide(Some(id), Owner::Failed),
                None,
                "reason: failed_owner_let_through {id}"
            );
            assert_eq!(
                decide(Some(id), Owner::NotAsked),
                None,
                "reason: owner_not_asked {id}"
            );
            assert!(decide(Some(id), Owner::Yes).is_some());
        }
        // deny, nothing, or a word not on the card: refused, whatever the owner did
        for c in [
            Some("deny"),
            None,
            Some("forever"),
            Some("ALWAYS"),
            Some(""),
        ] {
            assert_eq!(decide(c, Owner::Yes), None, "reason: not_a_choice {c:?}");
        }
    }

    #[test]
    fn only_always_writes_the_list_and_taking_back_closes_its_tunnels() {
        let mut l = AllowList::new("a1b2c");
        let ip = at("10.0.0.5:22");
        for id in ["once", "min15", "hour1", "until_seat_ends"] {
            let yes = decide(Some(id), Owner::Yes).unwrap();
            assert!(!l.add(ip, &yes), "reason: not_always_written {id}");
        }
        assert!(!l.has(ip.ip(), 22));
        let yes = decide(Some("always"), Owner::Yes).unwrap();
        assert!(l.add(ip, &yes));
        assert!(l.has(ip.ip(), 22));
        // exact ip:port only
        assert!(!l.has(ip.ip(), 23), "reason: other_port_allowed");
        assert!(
            !l.has(at("10.0.0.6:22").ip(), 22),
            "reason: other_ip_allowed"
        );
        // taking back closes this seat's tunnels to it, not the others
        let open = [ip, at("10.0.0.9:80"), ip];
        assert_eq!(
            l.revoke(ip, &open),
            Some(vec![ip, ip]),
            "reason: tunnel_left_open"
        );
        assert!(!l.has(ip.ip(), 22), "reason: still_listed");
        assert_eq!(l.revoke(ip, &open), None);
        assert_eq!(l.seat(), "a1b2c");
    }
}
