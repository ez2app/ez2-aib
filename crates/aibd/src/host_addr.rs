//! Which addresses aibd may connect to on a seat's behalf (DESIGN-hostmode-3e53b-1123 r3 §2;
//! DESIGN-sandboxnet-3e53b-1123 r5 §3, the same table for the egress proxy; 8b09a
//! REVIEW-hostnet-pre r2 passed).
//!
//! Three answers:
//! - [`Class::Refuse`]: never, whatever the person says: unspecified, multicast, broadcast,
//!   reserved and documentation ranges.
//! - [`Class::Own`]: this computer itself: loopback, link-local and any address of its own
//!   interfaces. For a managed host these are refused (a shell back on this account); for the
//!   proxy they are the high-risk class (Andy 11-23 02:2x, sandboxnet r5 §14).
//! - [`Class::Private`] / [`Class::Public`]: the rest. Private (10/8, 172.16/12, 192.168/16,
//!   100.64/10, fc00::/7) may be added as a managed host with the owner's verification; the test
//!   machines live there.
//!
//! An IPv6 address carrying an IPv4 one (mapped, compatible, SIIT, NAT64 well-known and local
//! use, 6to4, Teredo server and client) is judged by the IPv4 inside it as well; the stricter
//! answer wins. A defence in aibd's own process (GUIDE-same-user-ai-threat-model: what aibd
//! decides before it connects), on Mac and Windows alike: no platform call here.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

/// What an address is, strictest first.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Class {
    /// Never connected to.
    Refuse(&'static str),
    /// This computer itself.
    Own(&'static str),
    /// A private network.
    Private,
    /// Anything else.
    Public,
}

impl Class {
    /// The reason word for a refusal or an own address; None for the rest.
    pub fn why(self) -> Option<&'static str> {
        match self {
            Class::Refuse(w) | Class::Own(w) => Some(w),
            Class::Private | Class::Public => None,
        }
    }
}

fn v4(ip: Ipv4Addr) -> Class {
    let o = ip.octets();
    let n = u32::from(ip);
    let within = |base: [u8; 4], bits: u32| {
        let mask = if bits == 0 {
            0
        } else {
            u32::MAX << (32 - bits)
        };
        n & mask == u32::from(Ipv4Addr::from(base)) & mask
    };
    if within([0, 0, 0, 0], 8) {
        return Class::Refuse("this_network");
    }
    if ip.is_broadcast() {
        return Class::Refuse("broadcast");
    }
    if within([224, 0, 0, 0], 4) {
        return Class::Refuse("multicast");
    }
    if within([240, 0, 0, 0], 4) {
        return Class::Refuse("reserved");
    }
    for (base, bits) in [
        ([192, 0, 0, 0], 24),
        ([192, 0, 2, 0], 24),
        ([198, 51, 100, 0], 24),
        ([203, 0, 113, 0], 24),
        ([198, 18, 0, 0], 15),
        ([192, 88, 99, 0], 24),
    ] {
        if within(base, bits) {
            return Class::Refuse("reserved");
        }
    }
    if o[0] == 127 {
        return Class::Own("loopback");
    }
    if within([169, 254, 0, 0], 16) {
        return Class::Own("link_local");
    }
    if within([10, 0, 0, 0], 8)
        || within([172, 16, 0, 0], 12)
        || within([192, 168, 0, 0], 16)
        || within([100, 64, 0, 0], 10)
    {
        return Class::Private;
    }
    Class::Public
}

/// Every IPv4 address `ip` carries, by the forms that route or translate to one.
pub fn embedded_v4(ip: Ipv6Addr) -> Vec<Ipv4Addr> {
    let s = ip.segments();
    let b = ip.octets();
    let last = Ipv4Addr::new(b[12], b[13], b[14], b[15]);
    let mut out = Vec::new();
    // ::a.b.c.d (compatible), ::ffff:a.b.c.d (mapped), ::ffff:0:a.b.c.d (SIIT)
    if s[..5] == [0; 5] && (s[5] == 0 || s[5] == 0xffff) && !(s[5] == 0 && s[6] == 0) {
        out.push(last);
    }
    if s[..4] == [0; 4] && s[4] == 0xffff && s[5] == 0 {
        out.push(last);
    }
    // 64:ff9b::/96 (NAT64 well-known), 64:ff9b:1::/48 (local use: the last 32 bits here)
    if s[..6] == [0x64, 0xff9b, 0, 0, 0, 0] || (s[0] == 0x64 && s[1] == 0xff9b && s[2] == 1) {
        out.push(last);
    }
    // 2002:AABB:CCDD::/48 (6to4)
    if s[0] == 0x2002 {
        out.push(Ipv4Addr::new(b[2], b[3], b[4], b[5]));
    }
    // 2001:0000::/32 (Teredo): the server in bits 32–63, the client inverted in the last 32
    if s[0] == 0x2001 && s[1] == 0 {
        out.push(Ipv4Addr::new(b[4], b[5], b[6], b[7]));
        out.push(Ipv4Addr::new(!b[12], !b[13], !b[14], !b[15]));
    }
    out
}

fn v6(ip: Ipv6Addr) -> Class {
    let s = ip.segments();
    if ip.is_unspecified() {
        return Class::Refuse("unspecified");
    }
    if ip.is_loopback() {
        return Class::Own("loopback");
    }
    if s[0] & 0xff00 == 0xff00 {
        return Class::Refuse("multicast");
    }
    if s[0] & 0xffc0 == 0xfe80 {
        return Class::Own("link_local");
    }
    // site-local (deprecated), documentation, discard-only, ORCHID / benchmarking
    if s[0] & 0xffc0 == 0xfec0
        || (s[0] == 0x2001 && s[1] == 0x0db8)
        || (s[0] == 0x0100 && s[1..4] == [0, 0, 0])
        || (s[0] == 0x2001 && (s[1] & 0xfff0 == 0x0010 || s[1] & 0xfff0 == 0x0020))
        || (s[0] == 0x2001 && s[1] == 0x0002 && s[2] == 0)
        || s[0] == 0x3fff
    {
        return Class::Refuse("reserved");
    }
    if s[0] & 0xfe00 == 0xfc00 {
        return Class::Private;
    }
    Class::Public
}

/// What `ip` is. `own`: the addresses of this computer's interfaces as aibd read them just now
/// (read again at each connection: an address handed out by DHCP may become this computer's own
/// later, 8b09a L9).
pub fn classify(ip: IpAddr, own: &[IpAddr]) -> Class {
    let canon = |a: IpAddr| match a {
        IpAddr::V6(x) => x.to_ipv4_mapped().map_or(a, IpAddr::V4),
        IpAddr::V4(_) => a,
    };
    let mut worst = match ip {
        IpAddr::V4(x) => v4(x),
        IpAddr::V6(x) => {
            let mut w = v6(x);
            for inner in embedded_v4(x) {
                w = w.min(v4(inner));
            }
            w
        }
    };
    let mut seen: Vec<IpAddr> = vec![canon(ip)];
    if let IpAddr::V6(x) = ip {
        seen.extend(embedded_v4(x).into_iter().map(IpAddr::V4));
    }
    if seen.iter().any(|a| own.iter().any(|o| canon(*o) == *a)) {
        worst = worst.min(Class::Own("this_computer"));
    }
    worst
}

/// This computer's interface addresses, read now (read again for each connection, 8b09a L9).
/// Unreadable ⇒ an error, and the caller refuses: an address not known to be someone else's is
/// not connected to.
pub fn own_addresses() -> Result<Vec<IpAddr>, &'static str> {
    let n = sysinfo::Networks::new_with_refreshed_list().map_err(|_| "interfaces_unreadable")?;
    let own: Vec<IpAddr> = (&n)
        .into_iter()
        .flat_map(|(_, d)| d.ip_networks().iter().map(|i| i.addr))
        .collect();
    if own.is_empty() {
        return Err("interfaces_unreadable");
    }
    Ok(own)
}

/// For a managed host (hostmode §2): only private or public addresses; the reason otherwise.
pub fn host_ok(ip: IpAddr, own: &[IpAddr]) -> Result<(), &'static str> {
    match classify(ip, own) {
        Class::Private | Class::Public => Ok(()),
        c => Err(c.why().unwrap_or("refused")),
    }
}

/// Every address a name resolved to must pass: one refused refuses them all (hostmode §3-3).
pub fn all_ok(ips: &[IpAddr], own: &[IpAddr]) -> Result<(), &'static str> {
    if ips.is_empty() {
        return Err("no_address");
    }
    ips.iter().try_for_each(|ip| host_ok(*ip, own))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    /// H6 (hostmode §5): loopback, this computer, link-local and the IPv6 forms that carry one
    /// are refused as a managed host; private networks and 100.64/10 are not.
    #[test]
    fn a_host_back_on_this_computer_or_a_reserved_one_is_refused() {
        let own = [ip("192.168.1.20"), ip("2001:470:1:2::20")];
        for (a, why) in [
            ("127.0.0.1", "loopback"),
            ("127.200.3.4", "loopback"),
            ("::1", "loopback"),
            ("169.254.10.1", "link_local"),
            ("fe80::1", "link_local"),
            ("0.0.0.0", "this_network"),
            ("::", "unspecified"),
            ("224.0.0.1", "multicast"),
            ("ff02::1", "multicast"),
            ("255.255.255.255", "broadcast"),
            ("240.0.0.1", "reserved"),
            ("192.0.2.10", "reserved"),
            ("2001:db8::1", "reserved"),
            ("192.168.1.20", "this_computer"),
            ("2001:470:1:2::20", "this_computer"),
            // the IPv6 forms of a refused IPv4
            ("::ffff:127.0.0.1", "loopback"),
            ("::127.0.0.1", "loopback"),
            ("::ffff:0:127.0.0.1", "loopback"),
            ("64:ff9b::7f00:1", "loopback"),
            ("64:ff9b:1::7f00:1", "loopback"),
            ("2002:7f00:1::1", "loopback"),
            ("2002:a9fe:0101::1", "link_local"),
            ("::ffff:192.168.1.20", "this_computer"),
            ("64:ff9b::c0a8:114", "this_computer"),
        ] {
            assert_eq!(host_ok(ip(a), &own), Err(why), "reason: host_taken {a}");
        }
        // Teredo: the client is the last 32 bits inverted (127.0.0.1 = 80ff:fffe)
        assert_eq!(
            host_ok(ip("2001:0:4136:e378:8000:63bf:80ff:fffe"), &own),
            Err("loopback"),
            "reason: teredo_client_taken"
        );
        assert_eq!(
            host_ok(ip("2001:0:7f00:1:0:0:f7f7:f7f7"), &own),
            Err("loopback"),
            "reason: teredo_server_taken"
        );
        for a in [
            "10.0.0.5",
            "172.16.4.4",
            "192.168.1.21",
            "100.116.110.31",
            "fd00::5",
            "8.8.8.8",
            "2606:4700::1111",
            "::ffff:10.0.0.5",
        ] {
            assert_eq!(host_ok(ip(a), &own), Ok(()), "reason: host_refused {a}");
        }
        assert_eq!(classify(ip("100.64.0.1"), &[]), Class::Private);
        assert_eq!(classify(ip("8.8.8.8"), &[]), Class::Public);
        // the proxy's own class (sandboxnet r5 §14): loopback and own addresses are Own, not
        // Refuse, so the high-risk card can be raised for them
        assert_eq!(classify(ip("127.0.0.1"), &[]), Class::Own("loopback"));
    }

    /// hostmode §3-3: a name that resolved to several addresses is refused if any one is.
    #[test]
    fn one_refused_address_among_several_refuses_the_name() {
        let own = [ip("10.0.0.9")];
        assert_eq!(all_ok(&[ip("10.0.0.5"), ip("8.8.8.8")], &own), Ok(()));
        assert_eq!(
            all_ok(&[ip("10.0.0.5"), ip("127.0.0.1")], &own),
            Err("loopback"),
            "reason: second_address_unchecked"
        );
        assert_eq!(
            all_ok(&[ip("10.0.0.9"), ip("10.0.0.5")], &own),
            Err("this_computer")
        );
        assert_eq!(all_ok(&[], &own), Err("no_address"), "reason: empty_taken");
    }

    /// N10 / hostmode §2: the interface addresses are read from the system, and every one of
    /// them is this computer's own.
    #[test]
    fn this_computers_own_addresses_are_read_and_refused() {
        let own = own_addresses().unwrap();
        assert!(!own.is_empty(), "reason: no_interfaces");
        for a in &own {
            assert!(
                matches!(classify(*a, &own), Class::Own(_) | Class::Refuse(_)),
                "reason: own_address_taken {a}"
            );
            assert!(host_ok(*a, &own).is_err(), "reason: own_host_taken {a}");
        }
    }

    #[test]
    fn the_embedded_forms_are_read_out() {
        let lo = Ipv4Addr::LOCALHOST;
        for a in [
            "::ffff:127.0.0.1",
            "::127.0.0.1",
            "::ffff:0:127.0.0.1",
            "64:ff9b::7f00:1",
            "64:ff9b:1::7f00:1",
            "2002:7f00:1::1",
        ] {
            let x: Ipv6Addr = a.parse().unwrap();
            assert!(embedded_v4(x).contains(&lo), "reason: not_read {a}");
        }
        let plain: Ipv6Addr = "2606:4700::1111".parse().unwrap();
        assert!(embedded_v4(plain).is_empty());
        // ::1 and :: are not an IPv4 inside (judged as themselves)
        assert!(embedded_v4(Ipv6Addr::LOCALHOST).is_empty());
        assert!(embedded_v4(Ipv6Addr::UNSPECIFIED).is_empty());
    }
}
