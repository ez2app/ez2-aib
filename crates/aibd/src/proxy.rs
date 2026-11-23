//! The seats' network proxy, first slice: the deciding only (DESIGN-sandboxnet r10 §19; ebab8
//! 11-23 10:03). No socket is opened here: which port is whose, what one connection gets, what a
//! card's yes lets through and until when, and which tunnels to close. The next slice opens the
//! ports, moves the bytes and writes the sandbox's network settings.
//!
//! - [`Ports`]: one port a seat, from the reserved range 47770–47799 only, never one aibd binds
//!   itself; full ⇒ `proxy_ports_full` (the seat opens safe, offline). The port is how the proxy
//!   knows the seat.
//! - [`decide`]: the port's seat, the addresses the destination resolved to (all of them; the
//!   connection then goes to the address judged, never resolved again), this computer itself
//!   through [`crate::net_card::local_card`], the rest through [`crate::egress::verdict`] with the
//!   seat's always list and its live grants; a card only within [`CardLimit`].
//! - [`give`] / [`holds`]: what a card's yes leaves for a while (15 minutes, an hour, until the
//!   seat ends), written only from a [`Verified`] into aibd's one grants table and read without
//!   using it up.
//! - [`Tunnels`]: which open tunnel rests on what, so that an end, a taking back or a seat that
//!   ends closes them.
//!
//! Defence where the sandbox holds (claude's work mode on a Mac: the seat reaches only its own
//! port); a hint elsewhere (§16.2).

use std::collections::BTreeMap;
use std::net::{IpAddr, SocketAddr};

use crate::egress::{self, Verdict};
use crate::grants::{canon, Grants, Hold, OP_NET_LOCAL_SELF, OP_NET_PRIVATE};
use crate::host_addr::{classify, Class};
use crate::net_card::{Card, Lasts, Verified, CARD_SECS};

/// Which seat each proxy port is.
#[derive(Debug, Default)]
pub struct Ports {
    seat_of: BTreeMap<u16, String>,
    /// When each port was taken back: not given again for [`PORT_COOLDOWN`] seconds, so that a
    /// process of the seat that ended, still on its way out, is never taken for the next seat
    /// (f163c REVIEW-sandboxnet19 L3).
    freed: BTreeMap<u16, u64>,
}

/// How long a port taken back waits before another seat gets it.
pub const PORT_COOLDOWN: u64 = 120;

impl Ports {
    /// The seat's port: the one it has, or the first free one of the reserved range that aibd
    /// does not bind itself (`own_binds`).
    pub fn give(&mut self, seat: &str, own_binds: &[u16], now: u64) -> Result<u16, &'static str> {
        if let Some(p) = self.port_of(seat) {
            return Ok(p);
        }
        for p in egress::AIBD_RESERVED {
            if own_binds.contains(&p) || self.seat_of.contains_key(&p) {
                continue;
            }
            if self
                .freed
                .get(&p)
                .is_some_and(|t| now < t.saturating_add(PORT_COOLDOWN))
            {
                continue;
            }
            self.seat_of.insert(p, seat.to_owned());
            return Ok(p);
        }
        Err("proxy_ports_full")
    }

    pub fn port_of(&self, seat: &str) -> Option<u16> {
        self.seat_of
            .iter()
            .find(|(_, s)| s.as_str() == seat)
            .map(|(p, _)| *p)
    }

    /// The seat a connection to `port` comes from.
    pub fn seat(&self, port: u16) -> Option<&str> {
        self.seat_of.get(&port).map(String::as_str)
    }

    /// The seat ended or restarted: its port goes back, given again only after the cooldown.
    pub fn take_back(&mut self, seat: &str, now: u64) -> Option<u16> {
        let p = self.port_of(seat)?;
        self.seat_of.remove(&p);
        self.freed.insert(p, now);
        Some(p)
    }
}

/// The grant a card's yes leaves (c2f0b PREREAD-sandboxnet19, f163c REVIEW-sandboxnet19 r0):
/// in aibd's one grants table ([`crate::grants::GRANTS`]), the operation by the card's kind and
/// the resource [`crate::grants::canon`] of `ip:port`. 只這一次 and 一直允許 leave none here (the
/// held connection itself; the always list). The seat's end clears them (`grants::seat_ended`).
pub fn give(
    grants: &crate::grants::Grants,
    seat: &str,
    at: SocketAddr,
    high: bool,
    yes: &Verified,
    now: u64,
) -> bool {
    let hold = match yes.lasts() {
        Lasts::For(d) => [Hold::Min15, Hold::Hour1]
            .into_iter()
            .find(|h| h.secs() == Some(d.as_secs())),
        Lasts::UntilClose => Some(Hold::UntilSeatEnds),
        Lasts::Once | Lasts::Always => None,
    };
    let Some(hold) = hold else {
        return false;
    };
    grants
        .give(seat, op_of(high), &canon(at), hold, now)
        .is_ok()
}

/// The grant's operation: this computer's services (the high-risk card) or the LAN.
pub fn op_of(high: bool) -> &'static str {
    if high {
        OP_NET_LOCAL_SELF
    } else {
        OP_NET_PRIVATE
    }
}

/// Whether the seat holds a grant of this kind for `at` now, without using one up. Only the
/// kind asked: a LAN grant never lets a service of this computer through, should this computer
/// come to have that address (f163c REVIEW-egress-ff804ceb L1).
pub fn holds(
    grants: &crate::grants::Grants,
    seat: &str,
    at: SocketAddr,
    high: bool,
    now: u64,
) -> bool {
    grants.holds(seat, op_of(high), &canon(at), now).is_some()
}

/// At most one card waiting per seat, and six in ten minutes (§15, 16.7).
#[derive(Debug, Default)]
pub struct CardLimit {
    raised: BTreeMap<String, Vec<u64>>,
}

/// The cards a seat may raise in [`CARD_WINDOW`] seconds.
pub const CARDS_PER_WINDOW: usize = 6;
pub const CARD_WINDOW: u64 = 600;

impl CardLimit {
    /// Whether the seat may raise one more card now (`waiting`: its cards not answered yet);
    /// a yes counts it.
    pub fn may_raise(&mut self, seat: &str, now: u64, waiting: usize) -> bool {
        if waiting > 0 {
            return false;
        }
        let r = self.raised.entry(seat.to_owned()).or_default();
        r.retain(|t| now < t.saturating_add(CARD_WINDOW));
        if r.len() >= CARDS_PER_WINDOW {
            return false;
        }
        r.push(now);
        true
    }
}

/// Who a seat is, for the card.
#[derive(Debug, Clone)]
pub struct Who<'a> {
    pub persona_uid: &'a str,
    pub persona_name: &'a str,
}

/// This computer and aibd, read now.
#[derive(Debug, Clone, Copy)]
pub struct Here<'a> {
    /// This computer's interface addresses.
    pub own: &'a [IpAddr],
    /// The ports this aibd binds.
    pub own_binds: &'a [u16],
    /// The previews' ports (only ever more).
    pub preview_ports: &'a [u16],
    pub now: u64,
}

/// What one connection gets.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Conn {
    /// Connect to exactly this address (the one judged).
    Pass(SocketAddr),
    /// Hold the connection and raise this card.
    Card(Box<Card>),
    Refuse(&'static str),
}

/// One connection to proxy `port` for a destination that resolved to `ips` (all of them), port
/// `dest_port`. `allowed`: the seat's always list. `waiting`: the seat's cards not answered yet.
#[allow(clippy::too_many_arguments)] // each is one fact the verdict needs, none optional
pub fn decide(
    ports: &Ports,
    port: u16,
    dest_name: &str,
    ips: &[IpAddr],
    dest_port: u16,
    here: Here,
    who: &Who,
    grants: &Grants,
    allowed: &dyn Fn(&str, IpAddr, u16) -> bool,
    limit: &mut CardLimit,
    waiting: usize,
    request_id: String,
) -> Conn {
    let Some(seat) = ports.seat(port) else {
        return Conn::Refuse("unknown_port");
    };
    let held = |ip: IpAddr, p: u16| {
        allowed(seat, ip, p) || holds(grants, seat, SocketAddr::new(ip, p), false, here.now)
    };
    let (at, high) = match egress::verdict(ips, dest_port, here.own, &held) {
        Verdict::Pass(ip) => return Conn::Pass(SocketAddr::new(ip, dest_port)),
        Verdict::Card(ip) => (SocketAddr::new(ip, dest_port), false),
        // this computer itself (A6d): aibd's and whole-machine ports refused, the rest the
        // high-risk card, unless a grant from one holds
        Verdict::Refuse("this_computer") => {
            // link-local stays refused, never the high-risk card (§15.5; f163c L2)
            if ips
                .iter()
                .any(|ip| classify(*ip, here.own) == Class::Own("link_local"))
            {
                return Conn::Refuse("link_local");
            }
            let Some(ip) = ips
                .iter()
                .copied()
                .find(|ip| matches!(classify(*ip, here.own), Class::Own(_)))
            else {
                return Conn::Refuse("this_computer");
            };
            if let Err(why) =
                crate::net_card::local_card(dest_port, here.own_binds, here.preview_ports)
            {
                return Conn::Refuse(why);
            }
            let at = SocketAddr::new(ip, dest_port);
            if holds(grants, seat, at, true, here.now) {
                return Conn::Pass(at);
            }
            (at, true)
        }
        Verdict::Refuse(why) => return Conn::Refuse(why),
    };
    if !limit.may_raise(seat, here.now, waiting) {
        return Conn::Refuse("card_later");
    }
    Conn::Card(Box::new(Card {
        request_id,
        seat_id: seat.to_owned(),
        persona_uid: who.persona_uid.to_owned(),
        persona_name: who.persona_name.to_owned(),
        dest_name: dest_name.to_owned(),
        dest: at,
        source: "proxy",
        why: None,
        expires_at: here.now.saturating_add(CARD_SECS),
        high,
        listener: None,
    }))
}

/// What an open tunnel rests on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum By {
    /// Let through once, or a public address: nothing to end it but itself.
    Once,
    /// A LAN grant for a while, or the always list.
    Held,
    /// A grant for a while for a service of this computer (the high-risk card).
    HeldLocal,
}

/// The open tunnels.
#[derive(Debug, Default)]
pub struct Tunnels {
    rows: Vec<(u64, String, SocketAddr, By)>,
    next: u64,
}

impl Tunnels {
    pub fn open(&mut self, seat: &str, at: SocketAddr, by: By) -> u64 {
        self.next += 1;
        self.rows.push((self.next, seat.to_owned(), at, by));
        self.next
    }

    /// The tunnel ended by itself.
    pub fn closed(&mut self, id: u64) {
        self.rows.retain(|(i, _, _, _)| *i != id);
    }

    /// The held tunnels whose grant ended and that the always list does not hold either (a
    /// taking back, §4 L4): to close now, and gone from here.
    pub fn sweep(
        &mut self,
        now: u64,
        grants: &Grants,
        allowed: &dyn Fn(&str, IpAddr, u16) -> bool,
    ) -> Vec<u64> {
        let (gone, kept): (Vec<_>, Vec<_>) =
            std::mem::take(&mut self.rows)
                .into_iter()
                .partition(|(_, s, at, by)| {
                    matches!(by, By::Held | By::HeldLocal)
                        && !holds(grants, s, *at, *by == By::HeldLocal, now)
                        && !allowed(s, at.ip(), at.port())
                });
        self.rows = kept;
        gone.into_iter().map(|(i, _, _, _)| i).collect()
    }

    /// The seat ended: every one of its tunnels, to close now.
    pub fn end_seat(&mut self, seat: &str) -> Vec<u64> {
        let (gone, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.rows)
            .into_iter()
            .partition(|(_, s, _, _)| s == seat);
        self.rows = kept;
        gone.into_iter().map(|(i, _, _, _)| i).collect()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use crate::net_card::{decide as yes_of, Owner};

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    fn at(s: &str) -> SocketAddr {
        s.parse().unwrap()
    }

    const WHO: Who = Who {
        persona_uid: "p_1",
        persona_name: "頁面工程師",
    };

    /// This computer's one interface address in these tests.
    static OWN: [IpAddr; 1] = [IpAddr::V4(std::net::Ipv4Addr::new(192, 168, 1, 9))];

    fn here(now: u64) -> Here<'static> {
        Here {
            own: &OWN,
            own_binds: &[47770, 47780],
            preview_ports: &[],
            now,
        }
    }

    /// Public addresses the classifier passes (the documentation ranges are refused as reserved).
    const PUBLIC: &str = "8.8.8.8"; // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
    const PUBLIC2: &str = "1.1.1.1"; // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)

    fn none(_: &str, _: IpAddr, _: u16) -> bool {
        false
    }

    #[test]
    fn a_seat_gets_one_port_of_the_reserved_range_never_aibds_and_none_past_it() {
        let mut p = Ports::default();
        let first = p.give("s0", &[47770, 47780], 0).unwrap();
        assert_eq!(first, 47771);
        assert_eq!(
            p.give("s0", &[47770, 47780], 0),
            Ok(47771),
            "reason: second_port"
        );
        let mut got = vec![first];
        for i in 1..28 {
            got.push(p.give(&format!("s{i}"), &[47770, 47780], 0).unwrap());
        }
        // aibd's own binds are never a seat's (N43)
        assert!(
            !got.contains(&47770) && !got.contains(&47780),
            "reason: aibd_port_given"
        );
        assert!(got.iter().all(|x| egress::AIBD_RESERVED.contains(x)));
        // full: refused, never past the range (N42)
        let full = p.give("s28", &[47770, 47780], 0);
        assert_eq!(full.map_err(str::len), Err(16), "reason: past_range");
        assert_eq!(full.unwrap_err(), "proxy_ports_full");
        assert_eq!(p.seat(47771), Some("s0"));
        assert_eq!(p.take_back("s0", 10), Some(47771));
        assert_eq!(p.seat(47771), None);
        // not to the next seat while the old one may still be on its way out (f163c L3)
        assert!(
            p.give("s28", &[47770, 47780], 10 + PORT_COOLDOWN - 1)
                .is_err(),
            "reason: port_given_in_cooldown"
        );
        assert_eq!(
            p.give("s28", &[47770, 47780], 10 + PORT_COOLDOWN),
            Ok(47771)
        );
    }

    #[test]
    fn a_connection_gets_the_address_judged_a_card_or_a_refusal() {
        let mut ports = Ports::default();
        let port = ports.give("a1b2c", &[47770, 47780], 0).unwrap();
        let g = Grants::default();
        let mut lim = CardLimit::default();
        let d = |ips: &[IpAddr], p: u16, lim: &mut CardLimit, waiting: usize, now: u64| {
            decide(
                &ports,
                port,
                "x",
                ips,
                p,
                here(now),
                &WHO,
                &g,
                &none,
                lim,
                waiting,
                "r".into(),
            )
        };
        // a port no seat has (N44)
        assert_eq!(
            decide(
                &ports,
                47799,
                "x",
                &[ip("203.0.113.5")],
                443,
                here(1),
                &WHO,
                &g,
                &none,
                &mut lim,
                0,
                "r".into()
            ),
            Conn::Refuse("unknown_port"),
            "reason: unknown_port_served"
        );
        // public: exactly the address judged (N45)
        assert_eq!(
            d(&[ip(PUBLIC), ip(PUBLIC2)], 443, &mut lim, 0, 1),
            Conn::Pass(SocketAddr::new(ip(PUBLIC), 443)),
            "reason: other_address_connected"
        );
        // a private address: a card for it
        let Conn::Card(c) = d(&[ip("10.0.0.5")], 5432, &mut lim, 0, 1) else {
            panic!("reason: no_card");
        };
        assert_eq!(
            (c.dest, c.high, c.seat_id.as_str()),
            (at("10.0.0.5:5432"), false, "a1b2c")
        );
        assert_eq!(c.expires_at, 1 + CARD_SECS);
        // this computer: aibd's port refused, a service the high-risk card
        assert_eq!(
            d(&[ip("127.0.0.1")], 47770, &mut lim, 0, 700),
            Conn::Refuse("aibd_self")
        );
        assert_eq!(
            d(&[ip("127.0.0.1")], 22, &mut lim, 0, 700),
            Conn::Refuse("machine_port")
        );
        let Conn::Card(c) = d(&[ip("127.0.0.1")], 5432, &mut lim, 0, 700) else {
            panic!("reason: no_high_card");
        };
        assert!(c.high, "reason: local_not_high");
        // one waiting card per seat (N49)
        assert_eq!(
            d(&[ip("10.0.0.6")], 80, &mut lim, 1, 700),
            Conn::Refuse("card_later"),
            "reason: second_card_raised"
        );
        // a name that resolves back to this computer (N41, rebinding): judged on what it
        // resolved to, the public one beside it lets nothing through
        assert_eq!(
            d(&[ip(PUBLIC), ip("127.0.0.1")], 47770, &mut lim, 0, 1400),
            Conn::Refuse("aibd_self"),
            "reason: rebind_to_aibd"
        );
        assert!(
            matches!(
                d(&[ip(PUBLIC), ip("127.0.0.1")], 5432, &mut lim, 0, 2100),
                Conn::Card(c) if c.high && c.dest == at("127.0.0.1:5432")
            ),
            "reason: rebind_passed"
        );
        // link-local: refused, never a card (f163c L2)
        for a in ["169.254.169.254", "fe80::1"] {
            assert_eq!(
                d(&[ip(a)], 80, &mut lim, 0, 2800),
                Conn::Refuse("link_local"),
                "reason: link_local_carded {a}"
            );
        }
        // never-connected classes stay refused
        assert_eq!(
            d(&[ip("0.0.0.0")], 80, &mut lim, 0, 700),
            Conn::Refuse("unspecified")
        );
    }

    #[test]
    fn six_cards_in_ten_minutes_then_later() {
        let mut l = CardLimit::default();
        for t in 0..6 {
            assert!(l.may_raise("s", 100 + t, 0));
        }
        assert!(!l.may_raise("s", 200, 0), "reason: seventh_card");
        assert!(l.may_raise("other", 200, 0));
        assert!(
            l.may_raise("s", 100 + CARD_WINDOW, 0),
            "reason: window_never_ends"
        );
    }

    #[test]
    fn a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close() {
        let mut ports = Ports::default();
        let port = ports.give("a1b2c", &[], 0).unwrap();
        let g = Grants::default();
        let mut lim = CardLimit::default();
        let db = at("10.0.0.5:5432");
        let min15 = yes_of(Some("min15"), Owner::Yes).unwrap();
        assert!(give(&g, "a1b2c", db, false, &min15, 1_000));
        // the one table, under the LAN operation and the one spelling of the address
        assert!(g
            .holds("a1b2c", OP_NET_PRIVATE, "10.0.0.5:5432", 1_000)
            .is_some());
        // once and always are not grants here
        let other = at("10.0.0.7:5432");
        for c in ["once", "always"] {
            let yes = yes_of(Some(c), Owner::Yes).unwrap();
            assert!(
                !give(&g, "a1b2c", other, false, &yes, 1_000),
                "reason: {c}_granted"
            );
        }
        assert!(
            !holds(&g, "a1b2c", other, false, 1_000),
            "reason: once_held"
        );
        let mut d = |now: u64, g: &Grants| {
            decide(
                &ports,
                port,
                "db",
                &[db.ip()],
                5432,
                here(now),
                &WHO,
                g,
                &none,
                &mut lim,
                0,
                "r".into(),
            )
        };
        assert_eq!(d(1_899, &g), Conn::Pass(db));
        // its end: a card again (N46)
        assert!(
            matches!(d(1_900, &g), Conn::Card(_)),
            "reason: grant_never_ends"
        );
        // its tunnels close at the end, a once tunnel does not (N47)
        let mut t = Tunnels::default();
        let held = t.open("a1b2c", db, By::Held);
        let once = t.open("a1b2c", db, By::Once);
        assert!(t.sweep(1_899, &g, &none).is_empty());
        assert_eq!(
            t.sweep(1_900, &g, &none),
            vec![held],
            "reason: tunnel_outlived_grant"
        );
        assert_eq!(t.end_seat("a1b2c"), vec![once]);
        // until the seat ends: no end of time, gone with the seat (N50)
        let until = yes_of(Some("until_seat_ends"), Owner::Yes).unwrap();
        assert!(give(&g, "a1b2c", db, false, &until, 2_000));
        assert!(holds(&g, "a1b2c", db, false, u64::MAX));
        g.seat_ended("a1b2c");
        assert!(
            !holds(&g, "a1b2c", db, false, 2_001),
            "reason: grant_outlived_seat"
        );
        // a high-risk yes is a grant for this computer's service, not the LAN's
        let local = at("127.0.0.1:5432");
        assert!(give(&g, "a1b2c", local, true, &min15, 3_000));
        assert!(
            g.holds("a1b2c", OP_NET_LOCAL_SELF, "127.0.0.1:5432", 3_000)
                .is_some(),
            "reason: local_under_private_op"
        );
        // f163c REVIEW-egress-ff804ceb L1: a LAN grant for 10.0.0.5:5432, then this computer
        // comes to have 10.0.0.5: the high-risk card, not the LAN grant
        let g = Grants::default();
        assert!(give(&g, "a1b2c", db, false, &min15, 4_000));
        let own = [db.ip()];
        let moved = Here {
            own: &own,
            own_binds: &[],
            preview_ports: &[],
            now: 4_001,
        };
        let mut lim = CardLimit::default();
        let r = decide(
            &ports,
            port,
            "db",
            &[db.ip()],
            5432,
            moved,
            &WHO,
            &g,
            &none,
            &mut lim,
            0,
            "r".into(),
        );
        assert!(
            matches!(&r, Conn::Card(c) if c.high),
            "reason: lan_grant_let_local_through {r:?}"
        );
        // and a tunnel to a service of this computer rests on its own kind only
        let mut t = Tunnels::default();
        let id = t.open("a1b2c", db, By::HeldLocal);
        assert_eq!(
            t.sweep(4_001, &g, &none),
            vec![id],
            "reason: local_tunnel_kept_by_lan_grant"
        );
    }

    #[test]
    fn taking_always_back_closes_what_rests_on_it_only() {
        let g = Grants::default();
        let mut t = Tunnels::default();
        let db = at("10.0.0.5:5432");
        let web = at("10.0.0.6:80");
        let a = t.open("a1b2c", db, By::Held);
        let b = t.open("a1b2c", web, By::Held);
        let c = t.open("d3e4f", db, By::Held);
        let listed = |s: &str, ip: IpAddr, p: u16| {
            (s == "a1b2c" && SocketAddr::new(ip, p) == web) || s == "d3e4f"
        };
        // db taken back from a1b2c only (N48)
        assert_eq!(
            t.sweep(10, &g, &listed),
            vec![a],
            "reason: revoke_closed_wrong"
        );
        assert!(t.sweep(10, &g, &listed).is_empty());
        assert_eq!(t.end_seat("d3e4f"), vec![c]);
        assert_eq!(t.end_seat("a1b2c"), vec![b]);
    }
}
