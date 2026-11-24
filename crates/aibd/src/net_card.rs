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

/// The high-risk card's choices (A6d, DESIGN-sandboxnet r9 §18.3: a service on this computer
/// itself): no 到這一位關掉, no 一直允許, and every yes, 只這一次 too, needs the owner.
pub const HIGH_CHOICES: [Choice; 4] = [Choice::Deny, Choice::Once, Choice::Min15, Choice::Hour1];

/// The high-risk card's answer: a choice not on it, or any yes without the owner's, refuses.
pub fn decide_high(choice: Option<&str>, owner: Owner) -> Option<Verified> {
    let c = Choice::parse(choice?)?;
    if !HIGH_CHOICES.contains(&c) {
        return None;
    }
    let lasts = c.lasts()?;
    if owner != Owner::Yes {
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

/// The "always allow" operation: no hold in its parameters (not in `grants::HOLD_ALLOWED`), its
/// own words in the window (IFACE-netcard r1).
pub const OP_NET_ALLOW_ALWAYS: &str = "net.allow_always";
/// A private address for a while (the same word as `grants::OP_NET_PRIVATE`; written out here so
/// owner_ok's scan of `operation:` constants reads it).
pub const OP_PRIVATE: &str = "net.private";
/// A service on this computer itself (the same word as `grants::OP_NET_LOCAL_SELF`).
pub const OP_LOCAL_SELF: &str = "net.local_self";

/// How long a card stays in the table after its `expires_at` (f163c N3): answers then get
/// `net_card_expired`, not `net_card_unknown`. How long a card waits is the proxy's `expires_at`.
pub const CARD_SECS: u64 = 120;

/// One card: a seat's connection held while the owner decides. Made only by aibd's proxy (or
/// `net_ask`); nothing of it comes from the page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Card {
    pub request_id: String,
    pub seat_id: String,
    pub persona_uid: String,
    pub persona_name: String,
    pub dest_name: String,
    pub dest: SocketAddr,
    /// `proxy` or `net_ask`.
    pub source: &'static str,
    /// The partner's own reason, `net_ask` only.
    pub why: Option<String>,
    pub expires_at: u64,
    /// A service on this computer itself (A6d): the high-risk card.
    pub high: bool,
    /// On the high-risk card: which program listens on that port, when aibd could tell
    /// (f163c REVIEW-sandboxnet18 L1: a hint, not a defence).
    pub listener: Option<String>,
}

/// Whether a connection to this computer itself gets the high-risk card (A6d, DESIGN-sandboxnet
/// r9 §18): aibd's own ports and the ones that hand over the whole machine are refused with no
/// card; any other port gets it.
pub fn local_card(port: u16, own_binds: &[u16], preview_ports: &[u16]) -> Result<(), &'static str> {
    match crate::egress::local_kind(port, own_binds, preview_ports) {
        crate::egress::Local::Aibd => Err("aibd_self"),
        crate::egress::Local::Machine => Err("machine_port"),
        crate::egress::Local::Service => Ok(()),
    }
}

impl Card {
    /// The choices this card offers, in order.
    pub fn choices(&self) -> Vec<Choice> {
        if self.high {
            HIGH_CHOICES.to_vec()
        } else {
            CHOICES.iter().map(|(c, _, _)| *c).collect()
        }
    }

    /// Whether a choice of this card needs the owner (on the high-risk card every yes does).
    pub fn needs_owner(&self, c: Choice) -> bool {
        c != Choice::Deny && (self.high || c.needs_owner())
    }

    /// What `net_card_list` says of it (IFACE-netcard §1).
    pub fn shown(&self) -> serde_json::Value {
        let choices: Vec<serde_json::Value> = self
            .choices()
            .into_iter()
            .map(|c| {
                let words = CHOICES
                    .iter()
                    .find(|(x, _, _)| *x == c)
                    .map_or("", |(_, _, w)| *w);
                serde_json::json!({"id": c.id(), "words": words, "needs_owner": self.needs_owner(c)})
            })
            .collect();
        let mut v = serde_json::json!({
            "request_id": self.request_id,
            "seat_id": self.seat_id,
            "persona_uid": self.persona_uid,
            "persona_name": self.persona_name,
            "dest_name": self.dest_name,
            "dest_ip": self.dest.ip().to_string(),
            "dest_port": self.dest.port(),
            "category": if self.high { "local_self" } else { "private" },
            "source": self.source,
            "expires_at": self.expires_at,
            "choices": choices,
        });
        if let Some(w) = &self.why {
            v["why"] = serde_json::json!(w);
        }
        if self.high {
            v["risk"] = serde_json::json!("high");
            v["listener"] = serde_json::json!(self.listener.as_deref().unwrap_or("查不到"));
        }
        v
    }

    /// The owner's window for choice `c`: the operation, what is shown, and parameters that bind
    /// only this card and this choice (f163c 08:00: `request_id` + the choice, never the ticket).
    pub fn ask(&self, c: Choice, by: &str) -> crate::owner_ok::Ask {
        let category = aib_approve::category::Category::WindowGrant;
        let resource = format!("{} → {} {}", self.persona_name, self.dest_name, self.dest);
        let by = by.to_owned();
        if c == Choice::Always {
            let params = serde_json::json!({"request_id": self.request_id, "choice": c.id()});
            return crate::owner_ok::Ask {
                category,
                operation: OP_NET_ALLOW_ALWAYS,
                resource,
                params: params.to_string().into_bytes(),
                by,
            };
        }
        let params = serde_json::json!({"request_id": self.request_id, "hold": c.id()})
            .to_string()
            .into_bytes();
        if self.high {
            crate::owner_ok::Ask {
                category,
                operation: OP_LOCAL_SELF,
                resource,
                params,
                by,
            }
        } else {
            crate::owner_ok::Ask {
                category,
                operation: OP_PRIVATE,
                resource,
                params,
                by,
            }
        }
    }
}

/// A card's state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Open,
    /// A window was raised for this choice (marked only once aibd answered 202, f163c L1);
    /// only the ticket's resend finishes it.
    Asked(Choice),
    Finished,
}

/// What an answer does.
#[derive(Debug, Clone)]
pub enum Step {
    /// Finished: `None` refuses the held connection, `Some` lets it through for that long.
    Done(Choice, Option<Verified>),
    /// Raise the owner's window for this (202 with a ticket).
    Ask(Box<crate::owner_ok::Ask>),
    /// The ticket is not an approved one for this card and choice.
    NotApproved,
    Refused(&'static str),
}

impl PartialEq for Step {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Step::Done(a, x), Step::Done(b, y)) => a == b && x == y,
            (Step::Ask(a), Step::Ask(b)) => a.operation == b.operation && a.params == b.params,
            (Step::NotApproved, Step::NotApproved) => true,
            (Step::Refused(a), Step::Refused(b)) => a == b,
            _ => false,
        }
    }
}

/// The open cards. Kept only while aibd runs: a card lives two minutes.
#[derive(Debug, Default)]
pub struct Cards {
    rows: Vec<(Card, State)>,
}

impl Cards {
    pub fn add(&mut self, c: Card) {
        self.rows.push((c, State::Open));
    }

    fn sweep(&mut self, now: u64) {
        self.rows
            .retain(|(c, _)| now < c.expires_at.saturating_add(CARD_SECS));
    }

    /// The card of this request, as it is now.
    pub fn find(&self, request_id: &str) -> Option<Card> {
        self.rows
            .iter()
            .find(|(c, _)| c.request_id == request_id)
            .map(|(c, _)| c.clone())
    }

    /// The owner's window for choice `c` is up (aibd answered 202 with a ticket): from now on
    /// only that ticket finishes the card. A window that could not be raised marks nothing, so
    /// the card can be answered again (f163c REVIEW-netcardact L1).
    pub fn opened(&mut self, request_id: &str, c: Choice) {
        if let Some((_, s)) = self
            .rows
            .iter_mut()
            .find(|(card, _)| card.request_id == request_id)
        {
            if *s == State::Open {
                *s = State::Asked(c);
            }
        }
    }

    /// Cards still waiting for the owner (`net_cards` on `GET /api/v1/seats`).
    pub fn waiting(&mut self, now: u64) -> usize {
        self.sweep(now);
        self.rows.iter().filter(|(c, _)| now < c.expires_at).count()
    }

    /// `net_card_list`.
    pub fn list(&mut self, now: u64) -> Vec<serde_json::Value> {
        self.sweep(now);
        self.rows
            .iter()
            .filter(|(c, _)| now < c.expires_at)
            .map(|(c, _)| c.shown())
            .collect()
    }

    /// `net_card_answer {request_id, choice, ticket?}` (IFACE-netcard r1 §2, choice (a)).
    /// `approved` is `owner_ok::approved_for` (it uses the ticket up).
    pub fn answer(
        &mut self,
        request_id: &str,
        choice: &str,
        ticket: Option<&str>,
        now: u64,
        by: &str,
        approved: impl FnOnce(&str, &crate::owner_ok::Ask) -> bool,
    ) -> Step {
        let Some(i) = self
            .rows
            .iter()
            .position(|(c, _)| c.request_id == request_id)
        else {
            return Step::Refused("net_card_unknown");
        };
        let (card, state) = &mut self.rows[i];
        if *state == State::Finished {
            return Step::Refused("net_card_used");
        }
        if now >= card.expires_at {
            *state = State::Finished;
            return Step::Refused("net_card_expired");
        }
        let Some(c) = Choice::parse(choice).filter(|c| card.choices().contains(c)) else {
            return Step::Refused("not_a_choice");
        };
        if !card.needs_owner(c) {
            // 拒絕 stays open while a window is asked (the owner changed their mind); 只這一次 is
            // a first answer only
            let first = *state == State::Open;
            if ticket.is_some() || !(first || c == Choice::Deny) {
                return Step::Refused("net_card_used");
            }
            *state = State::Finished;
            let yes = (c != Choice::Deny).then_some(Verified { lasts: Lasts::Once });
            return Step::Done(c, yes);
        }
        let ask = card.ask(c, by);
        match (ticket, *state) {
            // the window; the card is marked only once it is up (Cards::opened)
            (None, State::Open) => Step::Ask(Box::new(ask)),
            // the second send without the ticket (IFACE r1)
            (None, _) => Step::Refused("net_card_used"),
            // a ticket for another choice than the window's (f163c N1): not even looked up
            (Some(_), State::Asked(asked)) if asked != c => Step::NotApproved,
            (Some(t), State::Asked(_)) => {
                if !approved(t, &ask) {
                    return Step::NotApproved;
                }
                *state = State::Finished;
                let lasts = c.lasts();
                Step::Done(c, lasts.map(|lasts| Verified { lasts }))
            }
            (Some(_), _) => Step::NotApproved,
        }
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
    fn the_high_risk_card_has_four_choices_and_every_yes_needs_the_owner() {
        let ids: Vec<&str> = HIGH_CHOICES.iter().map(|c| c.id()).collect();
        assert_eq!(ids, ["deny", "once", "min15", "hour1"]);
        // once is free on the ordinary card, never here (N38)
        assert!(decide(Some("once"), Owner::NotAsked).is_some());
        for id in ["once", "min15", "hour1"] {
            assert_eq!(
                decide_high(Some(id), Owner::NotAsked),
                None,
                "reason: high_yes_without_owner {id}"
            );
            assert_eq!(
                decide_high(Some(id), Owner::Failed),
                None,
                "reason: high_yes_without_owner {id}"
            );
            assert!(decide_high(Some(id), Owner::Yes).is_some());
        }
        // not on this card, whatever the owner did (N37)
        for id in ["until_seat_ends", "always", "deny", "forever"] {
            assert_eq!(
                decide_high(Some(id), Owner::Yes),
                None,
                "reason: high_card_took {id}"
            );
        }
        // a high-risk yes is never written to the always list
        let yes = decide_high(Some("hour1"), Owner::Yes).unwrap();
        let mut l = AllowList::new("a1b2c");
        assert!(!l.add(at("127.0.0.1:5432"), &yes));
    }

    fn card(id: &str, high: bool) -> Card {
        Card {
            request_id: id.into(),
            seat_id: "a1b2c".into(),
            persona_uid: "p_1".into(),
            persona_name: "頁面工程師".into(),
            dest_name: "db.lan".into(),
            dest: at(if high {
                "127.0.0.1:5432"
            } else {
                "10.0.0.5:5432"
            }),
            source: "proxy",
            why: None,
            expires_at: 1_000,
            high,
            listener: None,
        }
    }

    #[test]
    fn a_local_service_gets_the_high_risk_card_and_aibd_or_the_whole_machine_none() {
        assert_eq!(local_card(5432, &[], &[]), Ok(()));
        assert_eq!(local_card(3000, &[51000], &[52000]), Ok(()));
        for p in [47770, 47788, 51000, 52000] {
            assert_eq!(
                local_card(p, &[51000], &[52000]),
                Err("aibd_self"),
                "reason: aibd_carded {p}"
            );
        }
        for p in [22, 2375, 5900, 9222] {
            assert_eq!(
                local_card(p, &[], &[]),
                Err("machine_port"),
                "reason: machine_carded {p}"
            );
        }
        let v = card("r1", true).shown();
        assert_eq!(v["risk"], "high");
        assert_eq!(v["listener"], "查不到", "reason: listener_unknown_not_said");
        assert_eq!(v["category"], "local_self");
        let mut c = card("r2", true);
        c.listener = Some("postgres".into());
        assert_eq!(c.shown()["listener"], "postgres");
        assert!(card("r3", false).shown().get("risk").is_none());
    }

    fn approve(ok: bool) -> impl FnOnce(&str, &crate::owner_ok::Ask) -> bool {
        move |t, _| ok && t == "ot_good"
    }

    #[test]
    fn a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket() {
        let mut cs = Cards::default();
        cs.add(card("r1", false));
        cs.add(card("r2", false));
        cs.add(card("r3", false));
        // first send: the window
        let Step::Ask(ask) = cs.answer("r1", "min15", None, 900, "page:1", approve(true)) else {
            panic!("reason: no_window");
        };
        assert_eq!(ask.operation, OP_PRIVATE);
        // L1: a window that could not be raised marks nothing; asking again raises it again
        assert!(
            matches!(
                cs.answer("r1", "min15", None, 900, "page:1", approve(true)),
                Step::Ask(_)
            ),
            "reason: card_locked_without_window"
        );
        cs.opened("r1", Choice::Min15);
        // NA2 the second send without the ticket
        assert_eq!(
            cs.answer("r1", "min15", None, 901, "page:1", approve(true)),
            Step::Refused("net_card_used"),
            "reason: second_send_without_ticket"
        );
        // NA1 with the approved ticket
        let Step::Done(c, Some(yes)) =
            cs.answer("r1", "min15", Some("ot_good"), 902, "page:1", approve(true))
        else {
            panic!("reason: approved_ticket_not_done");
        };
        assert_eq!(c, Choice::Min15);
        assert_eq!(yes.lasts(), Lasts::For(Duration::from_secs(900)));
        // NA4 the same ticket again
        assert_eq!(
            cs.answer("r1", "min15", Some("ot_good"), 903, "page:1", approve(true)),
            Step::Refused("net_card_used"),
            "reason: ticket_twice"
        );
        // NA3 a ticket the owner did not approve for this choice: nothing finished
        assert!(matches!(
            cs.answer("r2", "min15", None, 900, "page:1", approve(true)),
            Step::Ask(_)
        ));
        cs.opened("r2", Choice::Min15);
        assert_eq!(
            cs.answer("r2", "hour1", Some("ot_good"), 901, "page:1", approve(true)),
            Step::NotApproved,
            "reason: other_choice_taken"
        );
        // 拒絕 still answers a card whose window is open
        assert_eq!(
            cs.answer("r2", "deny", None, 902, "page:1", approve(true)),
            Step::Done(Choice::Deny, None)
        );
        // a ticket sent before any window
        assert_eq!(
            cs.answer("r3", "hour1", Some("ot_good"), 900, "page:1", approve(true)),
            Step::NotApproved,
            "reason: ticket_before_window"
        );
    }

    #[test]
    fn the_window_binds_only_this_card_and_this_choice() {
        let c = card("r9", false);
        // N4: each choice is in its own parameters
        let m = c.ask(Choice::Min15, "page:1");
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&m.params).unwrap(),
            serde_json::json!({"request_id": "r9", "hold": "min15"}),
            "reason: choice_not_bound"
        );
        let a = c.ask(Choice::Hour1, "page:1");
        let p: serde_json::Value = serde_json::from_slice(&a.params).unwrap();
        assert_eq!(p, serde_json::json!({"request_id": "r9", "hold": "hour1"}));
        assert_eq!(
            crate::grants::hold_in(a.operation, &a.params),
            Ok(Some(crate::grants::Hold::Hour1))
        );
        let al = c.ask(Choice::Always, "page:1");
        assert_eq!(al.operation, OP_NET_ALLOW_ALWAYS);
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&al.params).unwrap(),
            serde_json::json!({"request_id": "r9", "choice": "always"})
        );
        // no hold for always: grants raises a window for it
        assert_eq!(crate::grants::hold_in(al.operation, &al.params), Ok(None));
        let h = card("r8", true).ask(Choice::Once, "page:1");
        assert_eq!(h.operation, OP_LOCAL_SELF);
        assert_eq!(
            crate::grants::hold_in(h.operation, &h.params),
            Ok(Some(crate::grants::Hold::Once))
        );
        assert_eq!(OP_PRIVATE, crate::grants::OP_NET_PRIVATE);
        assert_eq!(OP_LOCAL_SELF, crate::grants::OP_NET_LOCAL_SELF);
        assert!(a.resource.contains("10.0.0.5:5432"));
    }

    #[test]
    fn the_cards_list_counts_and_ends() {
        let mut cs = Cards::default();
        cs.add(card("r1", false));
        cs.add(card("r2", true));
        assert_eq!(cs.waiting(10), 2);
        let l = cs.list(10);
        assert_eq!(l[0]["dest_port"], 5432);
        assert_eq!(l[0]["choices"].as_array().unwrap().len(), 6);
        let high: Vec<(String, bool)> = l[1]["choices"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| {
                (
                    c["id"].as_str().unwrap().to_owned(),
                    c["needs_owner"].as_bool().unwrap(),
                )
            })
            .collect();
        assert_eq!(
            high,
            [
                ("deny", false),
                ("once", true),
                ("min15", true),
                ("hour1", true)
            ]
            .map(|(a, b)| (a.to_owned(), b))
        );
        // once on the ordinary card finishes at once, without the owner
        assert_eq!(
            cs.answer("r1", "once", None, 11, "page:1", approve(false)),
            Step::Done(Choice::Once, Some(Verified { lasts: Lasts::Once }))
        );
        assert_eq!(cs.waiting(12), 1, "reason: answered_still_counted");
        // on the high-risk card 只這一次 raises the window, 一直允許 is not a choice
        assert!(matches!(
            cs.answer("r2", "once", None, 12, "p", approve(true)),
            Step::Ask(_)
        ));
        assert_eq!(
            cs.answer("r2", "always", None, 12, "p", approve(true)),
            Step::Refused("not_a_choice")
        );
        // past its time: not counted, then refused
        assert_eq!(cs.waiting(1_000), 0, "reason: expired_counted");
        assert_eq!(
            cs.answer("r2", "deny", None, 1_000, "p", approve(true)),
            Step::Refused("net_card_expired")
        );
        assert_eq!(
            cs.answer("zz", "deny", None, 1, "p", approve(true)),
            Step::Refused("net_card_unknown")
        );
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
