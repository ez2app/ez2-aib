//! What a sandboxed seat's egress proxy takes and where it lets it go (DESIGN-sandboxnet-3e53b-1123
//! r6 §3 step 0–5 and §15, the first version's rules; f163c REVIEW-sandboxnet r6 passed; ebab8
//! 11-23 02:51「可以開始寫 §6 的碼了」).
//!
//! This first slice is the judging only, no socket: the shapes a request may have, the host
//! forms taken, and the verdict for the resolved addresses.
//!
//! - Shapes (8b09a L3, L7): `CONNECT host:port`, an absolute-form `http://` request, SOCKS5
//!   CONNECT. An origin-form request (what a web page in the person's browser sends), any request
//!   with an `Origin` header, SOCKS5 BIND / UDP, both `Content-Length` and `Transfer-Encoding`, or
//!   a `Transfer-Encoding` other than `chunked`: refused, no card, no list read.
//! - Hosts: a dotted IPv4 only in its standard form; any other all-number spelling the system
//!   resolver would turn into an address (`2130706433`, `0x7f.1`, `127.1`, `0177.0.0.1`) is
//!   refused, never resolved.
//! - Verdict (§15.1): [`Class::Refuse`] and [`Class::Own`] (this computer: loopback, link-local,
//!   its interface addresses) refused with no card; [`Class::Private`] passes only on the seat's
//!   own list, else the card; [`Class::Public`] passes. Several addresses: the strictest wins.
//!
//! A defence in aibd's process (the seat's sandbox lets it reach only this proxy; the decision is
//! made here, not in the seat).

use crate::host_addr::{classify, Class};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

/// What the page and the refusal say when the destination is this computer (ebab8 11-23 02:51).
pub const LOCAL_REFUSED: &str = "沙盒夥伴連不到這台電腦上的本機服務";

/// Longest request head the proxy reads (request line and headers).
pub const MAX_HEAD: usize = 16 * 1024;

/// A destination as the request named it: a checked IP or a name to resolve once.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Host {
    Ip(IpAddr),
    Name(String),
}

/// What a request asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Want {
    pub host: Host,
    pub port: u16,
    /// A tunnel (CONNECT, SOCKS5) or one plain HTTP request.
    pub tunnel: bool,
}

/// A host as written in a request. `[v6]` and a standard dotted IPv4 are addresses; a name is
/// lower-cased letters, digits, `-` and `.`; anything that reads as a number the resolver would
/// take is refused.
pub fn host_of(s: &str) -> Result<Host, &'static str> {
    if let Some(inner) = s.strip_prefix('[').and_then(|r| r.strip_suffix(']')) {
        return inner
            .parse::<Ipv6Addr>()
            .map(|a| Host::Ip(IpAddr::V6(a)))
            .map_err(|_| "host_form");
    }
    if let Ok(a) = s.parse::<Ipv4Addr>() {
        return Ok(Host::Ip(IpAddr::V4(a)));
    }
    let name = s.strip_suffix('.').unwrap_or(s).to_ascii_lowercase();
    let labels: Vec<&str> = name.split('.').collect();
    let ok = (1..=253).contains(&name.len())
        && labels.iter().all(|l| {
            (1..=63).contains(&l.len())
                && !l.starts_with('-')
                && !l.ends_with('-')
                && l.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        });
    if !ok {
        return Err("host_form");
    }
    // a number in any other spelling: all labels digits or 0x…, or a last label of digits only
    let numeric = |l: &str| {
        l.bytes().all(|b| b.is_ascii_digit())
            || l.strip_prefix("0x")
                .is_some_and(|h| h.bytes().all(|b| b.is_ascii_hexdigit()))
    };
    if labels.iter().all(|l| numeric(l)) || labels.last().is_some_and(|l| numeric(l)) {
        return Err("ip_form");
    }
    Ok(Host::Name(name))
}

/// `host:port` with the port required and not 0.
fn authority(s: &str) -> Result<(Host, u16), &'static str> {
    let (h, p) = s.rsplit_once(':').ok_or("no_port")?;
    if h.contains(':') && !h.starts_with('[') {
        return Err("host_form");
    }
    let port: u16 = p.parse().map_err(|_| "port")?;
    if port == 0 || p.starts_with('0') || p.starts_with('+') {
        return Err("port");
    }
    Ok((host_of(h)?, port))
}

/// The request head (request line and headers, CRLF separated, no body). Refusals carry the
/// reason word for `net_log`.
pub fn http_head(head: &str) -> Result<Want, &'static str> {
    if head.len() > MAX_HEAD {
        return Err("head_too_long");
    }
    let mut lines = head.split("\r\n");
    let first = lines.next().ok_or("empty")?;
    let mut parts = first.split(' ');
    let (Some(method), Some(target), Some(version), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Err("request_line");
    };
    if !matches!(version, "HTTP/1.1" | "HTTP/1.0") {
        return Err("version");
    }
    let (mut length, mut chunked) = (false, false);
    for l in lines.take_while(|l| !l.is_empty()) {
        let (name, value) = l.split_once(':').ok_or("header")?;
        let value = value.trim();
        match name.trim().to_ascii_lowercase().as_str() {
            "origin" => return Err("origin_header"),
            "content-length" => length = true,
            "transfer-encoding" => {
                if !value.eq_ignore_ascii_case("chunked") {
                    return Err("transfer_encoding");
                }
                chunked = true;
            }
            _ => {}
        }
    }
    if length && chunked {
        return Err("length_and_chunked");
    }
    if method == "CONNECT" {
        let (host, port) = authority(target)?;
        return Ok(Want {
            host,
            port,
            tunnel: true,
        });
    }
    let Some(rest) = target.strip_prefix("http://") else {
        return Err(if target.starts_with('/') {
            "origin_form"
        } else {
            "target_form"
        });
    };
    let auth = rest.split(['/', '?', '#']).next().unwrap_or("");
    if auth.contains('@') {
        return Err("userinfo");
    }
    let (host, port) = if auth.ends_with(']') || !auth.contains(':') {
        (host_of(auth)?, 80)
    } else {
        authority(auth)?
    };
    Ok(Want {
        host,
        port,
        tunnel: false,
    })
}

/// A SOCKS5 request after the greeting: `VER CMD RSV ATYP DST.ADDR DST.PORT`. Only CONNECT;
/// the three address types go through the same [`host_of`] rules.
pub fn socks5_request(b: &[u8]) -> Result<Want, &'static str> {
    if b.len() < 4 || b[0] != 5 || b[2] != 0 {
        return Err("socks_form");
    }
    if b[1] != 1 {
        return Err("socks_command");
    }
    let (host, rest) = match b[3] {
        1 if b.len() == 4 + 4 + 2 => {
            let a = Ipv4Addr::new(b[4], b[5], b[6], b[7]);
            (Host::Ip(IpAddr::V4(a)), &b[8..])
        }
        4 if b.len() == 4 + 16 + 2 => {
            let mut o = [0u8; 16];
            o.copy_from_slice(&b[4..20]);
            (Host::Ip(IpAddr::V6(Ipv6Addr::from(o))), &b[20..])
        }
        3 if b.len() > 5 && b.len() == 5 + usize::from(b[4]) + 2 => {
            let n = usize::from(b[4]);
            let name = std::str::from_utf8(&b[5..5 + n]).map_err(|_| "host_form")?;
            (host_of(name)?, &b[5 + n..])
        }
        _ => return Err("socks_form"),
    };
    let port = u16::from_be_bytes([rest[0], rest[1]]);
    if port == 0 {
        return Err("port");
    }
    Ok(Want {
        host,
        port,
        tunnel: true,
    })
}

/// What the proxy does with a destination.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Connect to this address (the first resolved one, never resolved again).
    Pass(IpAddr),
    /// Raise the network card (§4, §15.6) for this address.
    Card(IpAddr),
    /// Refused, no card; the reason word.
    Refuse(&'static str),
}

/// The verdict for the addresses a destination resolved to (§15.1). `own`: this computer's
/// interface addresses read now; `listed`: whether this seat's own list (or a live grant) holds
/// `ip:port`.
pub fn verdict(
    ips: &[IpAddr],
    port: u16,
    own: &[IpAddr],
    listed: &dyn Fn(IpAddr, u16) -> bool,
) -> Verdict {
    let Some(first) = ips.first().copied() else {
        return Verdict::Refuse("no_address");
    };
    let worst = ips
        .iter()
        .map(|ip| (classify(*ip, own), *ip))
        .min_by_key(|(c, _)| *c)
        .unwrap_or((Class::Public, first));
    match worst {
        (Class::Refuse(w), _) => Verdict::Refuse(w),
        (Class::Own(_), _) => Verdict::Refuse("this_computer"),
        (Class::Private, ip) => {
            // every private address among them must be on the list (a public one needs none)
            if ips
                .iter()
                .filter(|a| classify(**a, own) == Class::Private)
                .all(|a| listed(*a, port))
            {
                Verdict::Pass(first)
            } else {
                Verdict::Card(ip)
            }
        }
        (Class::Public, _) => Verdict::Pass(first),
    }
}

/// Ports counted as aibd's on this computer whether or not anything listens on them
/// (DESIGN-sandboxnet r9 §18.2, A6d): the shipped aibd 47770, the mother / page 47780 and the
/// previews' defaults all fall here. Not listing them one by one is the point (§12 L8).
pub const AIBD_RESERVED: std::ops::RangeInclusive<u16> = 47770..=47799;

/// What a destination on this computer is (A6d §18.1): one of aibd's ports, refused with no card
/// (DECISION 十三: back into aibd is not in the first version), or another program's service, the
/// high-risk card.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Local {
    Aibd,
    /// A service that hands over the whole machine (f163c REVIEW-sandboxnet18 L1): refused like
    /// aibd's ports, no card.
    Machine,
    Service,
}

/// Ports where a yes would hand over the whole machine, refused with no card in the first version
/// (f163c REVIEW-sandboxnet18 L1): ssh back into this computer, the Docker API (root), screen
/// sharing / Apple Remote Desktop, a browser's remote debugging (the owner's signed-in browser).
pub const MACHINE_PORTS: [std::ops::RangeInclusive<u16>; 5] =
    [22..=22, 2375..=2376, 3283..=3283, 5900..=5900, 9222..=9229];

/// `own_binds` are the ports this aibd process bound (kept in the process); `preview_ports` are
/// what the previews' run files say, which only ever add to the list (a deleted file still leaves
/// the reserved range, the defence).
pub fn local_kind(port: u16, own_binds: &[u16], preview_ports: &[u16]) -> Local {
    if AIBD_RESERVED.contains(&port) || own_binds.contains(&port) || preview_ports.contains(&port) {
        Local::Aibd
    } else if MACHINE_PORTS.iter().any(|r| r.contains(&port)) {
        Local::Machine
    } else {
        Local::Service
    }
}

/// Whether the proxy that carries a seat's network is in this build. The judging above is; the
/// sockets are the next slice (DESIGN-sandboxnet §6). Until they are, no seat's network goes
/// through aibd, so no mode may say its rules are enforced.
pub const PROXY_READY: bool = false;

/// What the page may say about one provider's mode and the network (DESIGN-sandboxnet 16.2,
/// Andy 11-23「5能上網」): `"enforced"` (the sandbox lets the seat reach only the proxy: Mac,
/// claude work), `"hint"` (the rules are only a hint and this computer and the LAN are reachable:
/// any dgrs seat, and a work seat with no sandbox, Windows), `"off"` (no network: safe and host
/// seats have no network tool; codex is not given the network in the first version, §7-4 / 16.6).
/// The page picks its words from this, never from the platform itself.
pub fn net_state(os: &str, provider: &str, mode: &str, proxy: bool) -> &'static str {
    match (provider, mode) {
        (_, "dgrs") => "hint",
        ("claude", "work") if !proxy => "off",
        ("claude", "work") if os == "macos" => "enforced",
        ("claude", "work") => "hint",
        _ => "off",
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    #[test]
    fn aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is() {
        // the reserved range, listened on or not (N36)
        for p in [47770, 47780, 47788, 47799] {
            assert_eq!(
                local_kind(p, &[], &[]),
                Local::Aibd,
                "reason: reserved_port_open {p}"
            );
        }
        // a preview's run file deleted: still in the range (N39)
        assert_eq!(local_kind(47781, &[], &[]), Local::Aibd);
        // outside the range: this process's own binds and the previews' listed ports
        assert_eq!(
            local_kind(51000, &[51000], &[]),
            Local::Aibd,
            "reason: own_bind_open"
        );
        assert_eq!(
            local_kind(52000, &[], &[52000]),
            Local::Aibd,
            "reason: preview_port_open"
        );
        // another program's service: the high-risk card (N37)
        // these hand over the whole machine: refused, no card (L1, N40)
        for p in [22, 2375, 2376, 3283, 5900, 9222, 9229] {
            assert_eq!(
                local_kind(p, &[], &[]),
                Local::Machine,
                "reason: machine_port_carded {p}"
            );
        }
        for p in [21, 23, 3000, 5432, 9221, 9230, 47769, 47800] {
            assert_eq!(
                local_kind(p, &[51000], &[52000]),
                Local::Service,
                "reason: service_refused {p}"
            );
        }
    }

    #[test]
    fn a_mode_says_enforced_only_where_the_sandbox_holds_and_the_proxy_is_there() {
        // Mac claude work: enforced once the proxy is in, off before
        assert_eq!(net_state("macos", "claude", "work", true), "enforced");
        assert_eq!(
            net_state("macos", "claude", "work", false),
            "off",
            "reason: enforced_without_proxy"
        );
        // Windows (and anything with no sandbox): only a hint
        assert_eq!(
            net_state("windows", "claude", "work", true),
            "hint",
            "reason: windows_said_enforced"
        );
        assert_eq!(net_state("linux", "claude", "work", true), "hint");
        // dgrs is a hint everywhere; safe, host and codex have no network
        for os in ["macos", "windows"] {
            assert_eq!(
                net_state(os, "claude", "dgrs", true),
                "hint",
                "reason: dgrs_said_enforced"
            );
            assert_eq!(net_state(os, "codex", "dgrs", false), "hint");
            for (p, m) in [
                ("claude", "safe"),
                ("claude", "host"),
                ("codex", "work"),
                ("codex", "safe"),
            ] {
                assert_eq!(
                    net_state(os, p, m, true),
                    "off",
                    "reason: no_network_mode_on {p} {m}"
                );
            }
        }
    }

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    /// N3 (r6 §15.4): only the standard dotted IPv4 is an address; other numeric spellings the
    /// resolver would read are refused, never resolved.
    #[test]
    fn numbers_in_other_spellings_are_refused_not_resolved() {
        assert_eq!(host_of("127.0.0.1"), Ok(Host::Ip(ip("127.0.0.1"))));
        assert_eq!(host_of("[::1]"), Ok(Host::Ip(ip("::1"))));
        assert_eq!(
            host_of("Example.COM."),
            Ok(Host::Name("example.com".into()))
        );
        assert_eq!(host_of("cafe.be"), Ok(Host::Name("cafe.be".into())));
        assert_eq!(
            host_of("1password.com"),
            Ok(Host::Name("1password.com".into()))
        );
        for s in [
            "2130706433",
            "0x7f000001",
            "0x7f.1",
            "127.1",
            "0177.0.0.1",
            "127.0.0.1.",
            "1.2.3.4.5",
            "017700000001",
        ] {
            assert!(
                matches!(host_of(s), Err("ip_form" | "host_form")),
                "reason: numeric_taken {s} {:?}",
                host_of(s)
            );
        }
        for s in [
            "",
            "a..b",
            "-a.com",
            "a_b.com",
            "ex ample.com",
            "::1",
            "[::1",
            "é.com",
        ] {
            assert_eq!(host_of(s), Err("host_form"), "reason: bad_host_taken {s}");
        }
    }

    /// N11 + L7 (r6 §3 step 0, §12): the shapes the proxy takes and the ones it refuses.
    #[test]
    fn only_connect_absolute_form_and_socks_connect_are_taken() {
        let r = |s: &str| http_head(s);
        assert_eq!(
            r("CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"),
            Ok(Want {
                host: Host::Name("example.com".into()),
                port: 443,
                tunnel: true
            })
        );
        assert_eq!(
            r("GET http://10.0.0.5:8080/x?y HTTP/1.1\r\nHost: 10.0.0.5\r\n\r\n"),
            Ok(Want {
                host: Host::Ip(ip("10.0.0.5")),
                port: 8080,
                tunnel: false
            })
        );
        assert_eq!(
            r("GET http://[fd00::5]/ HTTP/1.1\r\n\r\n").unwrap().host,
            Host::Ip(ip("fd00::5"))
        );
        assert_eq!(
            r("CONNECT [::1]:22 HTTP/1.1\r\n\r\n").unwrap().host,
            Host::Ip(ip("::1"))
        );
        for (s, why) in [
            ("GET / HTTP/1.1\r\nHost: 127.0.0.1:47770\r\n\r\n", "origin_form"),
            (
                "CONNECT example.com:443 HTTP/1.1\r\nOrigin: https://evil.test\r\n\r\n",
                "origin_header",
            ),
            ("GET http://a.com/ HTTP/1.1\r\norigin: null\r\n\r\n", "origin_header"),
            (
                "POST http://a.com/ HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n",
                "length_and_chunked",
            ),
            (
                "POST http://a.com/ HTTP/1.1\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
                "transfer_encoding",
            ),
            ("GET https://a.com/ HTTP/1.1\r\n\r\n", "target_form"),
            ("GET http://u:p@10.0.0.5/ HTTP/1.1\r\n\r\n", "userinfo"),
            ("CONNECT example.com HTTP/1.1\r\n\r\n", "no_port"),
            ("CONNECT example.com:0 HTTP/1.1\r\n\r\n", "port"),
            ("CONNECT example.com:022 HTTP/1.1\r\n\r\n", "port"),
            ("CONNECT 2130706433:22 HTTP/1.1\r\n\r\n", "ip_form"),
            ("CONNECT ::1:22 HTTP/1.1\r\n\r\n", "host_form"),
            ("GET http://a.com/ HTTP/2\r\n\r\n", "version"),
            ("GET  http://a.com/ HTTP/1.1\r\n\r\n", "request_line"),
        ] {
            assert_eq!(r(s), Err(why), "reason: shape_taken {s:?}");
        }
        assert_eq!(r(&"a".repeat(MAX_HEAD + 1)), Err("head_too_long"));
    }

    /// N3 / N11 SOCKS5 (r6 §15.4, 8b09a r2 N4, f163c r5 N1): CONNECT only; the three address
    /// types go through the same rules.
    #[test]
    fn socks5_takes_connect_only_and_every_address_type_is_checked() {
        let v4 = [5, 1, 0, 1, 127, 0, 0, 1, 0, 22];
        assert_eq!(socks5_request(&v4).unwrap().host, Host::Ip(ip("127.0.0.1")));
        let mut v6 = vec![5, 1, 0, 4];
        v6.extend_from_slice(&"::ffff:127.0.0.1".parse::<Ipv6Addr>().unwrap().octets());
        v6.extend_from_slice(&[0, 22]);
        assert_eq!(
            socks5_request(&v6).unwrap().host,
            Host::Ip(ip("::ffff:127.0.0.1"))
        );
        let name = b"localtest.me";
        let mut d = vec![5, 1, 0, 3, name.len() as u8];
        d.extend_from_slice(name);
        d.extend_from_slice(&[1, 187]);
        let w = socks5_request(&d).unwrap();
        assert_eq!((w.host, w.port), (Host::Name("localtest.me".into()), 443));
        // a numeric name is refused like in HTTP
        let num = b"2130706433";
        let mut d = vec![5, 1, 0, 3, num.len() as u8];
        d.extend_from_slice(num);
        d.extend_from_slice(&[0, 22]);
        assert_eq!(socks5_request(&d), Err("ip_form"));
        // BIND, UDP ASSOCIATE, a bad version, a short body, port 0
        assert_eq!(
            socks5_request(&[5, 2, 0, 1, 1, 2, 3, 4, 0, 22]),
            Err("socks_command")
        );
        assert_eq!(
            socks5_request(&[5, 3, 0, 1, 1, 2, 3, 4, 0, 22]),
            Err("socks_command")
        );
        assert_eq!(
            socks5_request(&[4, 1, 0, 1, 1, 2, 3, 4, 0, 22]),
            Err("socks_form")
        );
        assert_eq!(socks5_request(&[5, 1, 0, 1, 1, 2, 3]), Err("socks_form"));
        assert_eq!(socks5_request(&[5, 1, 0, 1, 1, 2, 3, 4, 0, 0]), Err("port"));
        assert_eq!(
            socks5_request(&[5, 1, 0, 9, 1, 2, 3, 4, 0, 22]),
            Err("socks_form")
        );
    }

    /// N32 + N5 + N10 (r6 §15.1, §15.4): this computer is refused with no card; a private address
    /// raises the card unless listed; public passes; the strictest of several wins.
    #[test]
    fn this_computer_is_refused_private_asks_and_public_passes() {
        let own = [ip("192.168.1.20"), ip("fd00::99")];
        let none = |_: IpAddr, _: u16| false;
        let all = |_: IpAddr, _: u16| true;
        for a in [
            "127.0.0.1",
            "::1",
            "169.254.169.254",
            "fe80::1",
            "192.168.1.20",
            "fd00::99",
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1", // ip-literal-ok: NAT64 form wrapping a test IPv4 (the classifier reads it)
        ] {
            assert_eq!(
                verdict(&[ip(a)], 47781, &own, &all),
                Verdict::Refuse("this_computer"),
                "reason: own_taken {a}"
            );
        }
        assert_eq!(
            verdict(&[ip("224.0.0.1")], 1, &own, &all), // ip-literal-ok: multicast / reserved range example
            Verdict::Refuse("multicast")
        );
        assert_eq!(
            verdict(&[ip("10.0.0.5")], 22, &own, &none),
            Verdict::Card(ip("10.0.0.5"))
        );
        assert_eq!(
            verdict(&[ip("100.64.0.1")], 22, &own, &none),
            Verdict::Card(ip("100.64.0.1"))
        );
        assert_eq!(
            verdict(&[ip("10.0.0.5")], 22, &own, &all),
            Verdict::Pass(ip("10.0.0.5"))
        );
        let only_22 = |_: IpAddr, p: u16| p == 22;
        assert_eq!(
            verdict(&[ip("10.0.0.5")], 23, &own, &only_22),
            Verdict::Card(ip("10.0.0.5")),
            "reason: list_ignores_port"
        );
        assert_eq!(
            verdict(&[ip("8.8.8.8")], 443, &own, &none), // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
            Verdict::Pass(ip("8.8.8.8")) // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
        );
        // a name resolved to a public and a loopback address: refused, not the public one
        assert_eq!(
            verdict(&[ip("1.2.3.4"), ip("127.0.0.1")], 80, &own, &all), // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
            Verdict::Refuse("this_computer"),
            "reason: mixed_took_public"
        );
        assert_eq!(
            verdict(&[ip("1.2.3.4"), ip("10.0.0.5")], 80, &own, &none), // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
            Verdict::Card(ip("10.0.0.5"))
        );
        let only_private = |a: IpAddr, _: u16| a == ip("10.0.0.5");
        assert_eq!(
            verdict(&[ip("1.2.3.4"), ip("10.0.0.5")], 80, &own, &only_private), // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
            Verdict::Pass(ip("1.2.3.4")) // ip-literal-ok: a public address the classifier must pass (documentation ranges are refused)
        );
        assert_eq!(verdict(&[], 80, &own, &all), Verdict::Refuse("no_address"));
        assert!(LOCAL_REFUSED.contains("本機服務"));
    }
}
