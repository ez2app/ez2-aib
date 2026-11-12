// SPDX-License-Identifier: Apache-2.0
//! "80 / 443 only from Cloudflare" as a decision (E6 C3, host sheet H8). Someone who scans
//! the host's public address on 443 gets a certificate, and the certificate names a site:
//! the site's domain then points at the provider's address. With the edge taking web
//! traffic from Cloudflare's own ranges only, a direct scan gets nothing. No I/O here: the
//! host shell fetches Cloudflare's list and reads the edge CT's firewall file.
//!
//! What this module holds:
//! * **The list is checked before it is used** ([`parse_ranges`]): IPv4 CIDR lines only,
//!   every prefix /8 to /32, no host bits, no repeats. An empty list and `0.0.0.0/0` are
//!   refused by name: either would read as "let everyone in" to a person, and an empty
//!   set behind an accept rule is easy to mistake for "nothing to restrict".
//! * **A list is in the code** ([`BUILTIN_V4`], Cloudflare's [`SOURCE_URL`] as of 11-12):
//!   a fetched list goes through the same check and its difference to this one is shown,
//!   never taken silently.
//! * **Where the rules go.** 80 / 443 reach the edge CT by DNAT on the host, so they are
//!   forwarded traffic: the edge CT's own firewall file filters them, with the visitor's
//!   source address still on the packet. [`apply`] puts the set ([`IPSET`]) in that file
//!   and [`RULES`] at the top of its `[RULES]` (first match wins); every other line stays.
//! * **Read back, not trusted** ([`readback`]): the file on disk must have the firewall on,
//!   the set equal to the list exactly (nothing missing, nothing extra) and the two rules
//!   first.
//! * **The rules must apply at all** ([`firewall_applies`], c477a M1): Proxmox filters a
//!   CT only when the cluster firewall is on (H09), the CT's file is on and **every**
//!   network card of the CT carries `firewall=1` (S03). One card without it is a way round
//!   every rule, so a clean file alone is not "only Cloudflare".
//!
//! Known limits, written into the plan for the person who signs it: the drop has no
//! source, so it also drops IPv6 to 80 / 443 (the set is IPv4 only; the edge has no public
//! IPv6 path today, which is the precondition) and drops hosts and CTs on the internal
//! bridge that call the edge on 80 / 443 (our own calls enter the edge with `pct exec` to
//! 127.0.0.1, which the CT firewall does not see). The set is referenced as
//! `+guest/<name>` (Proxmox 8 and later) so a cluster set of the same name is never taken
//! instead.

use alloc::string::{String, ToString};
use alloc::vec::Vec;
use core::fmt;

use crate::verify::{self, Outcome};

/// Where Cloudflare publishes its IPv4 ranges, one CIDR per line.
pub const SOURCE_URL: &str = "https://www.cloudflare.com/ips-v4";

/// Cloudflare's IPv4 ranges as [`SOURCE_URL`] gave them on 2026-11-12, one per line.
pub const BUILTIN_V4: [&str; 15] = [
    "173.245.48.0/20",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "103.21.244.0/22",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "103.22.200.0/22",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "103.31.4.0/22",    // ip-literal-ok: Cloudflare 官方 ips-v4
    "141.101.64.0/18",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "108.162.192.0/18", // ip-literal-ok: Cloudflare 官方 ips-v4
    "190.93.240.0/20",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "188.114.96.0/20",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "197.234.240.0/22", // ip-literal-ok: Cloudflare 官方 ips-v4
    "198.41.128.0/17",  // ip-literal-ok: Cloudflare 官方 ips-v4
    "162.158.0.0/15",   // ip-literal-ok: Cloudflare 官方 ips-v4
    "104.16.0.0/13",    // ip-literal-ok: Cloudflare 官方 ips-v4
    "104.24.0.0/14",    // ip-literal-ok: Cloudflare 官方 ips-v4
    "172.64.0.0/13",    // ip-literal-ok: Cloudflare 官方 ips-v4
    "131.0.72.0/22",    // ip-literal-ok: Cloudflare 官方 ips-v4
];

/// [`BUILTIN_V4`] through the same check as a fetched list.
pub fn builtin_ranges() -> Result<Vec<Range>, Error> {
    parse_ranges(&BUILTIN_V4.join("\n"))
}

/// The set in the edge CT's firewall file that holds the ranges.
pub const IPSET: &str = "ez2-cloudflare-v4";

/// The two rules, in this order, first in the edge CT's `[RULES]`: web ports from the set
/// are let in, web ports from anywhere else are dropped.
pub const RULES: [&str; 2] = [
    "IN ACCEPT -source +guest/ez2-cloudflare-v4 -p tcp -dport 80,443 -log nolog",
    "IN DROP -p tcp -dport 80,443 -log nolog",
];

/// Why a range list was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// No range at all.
    Empty,
    /// `0.0.0.0/0`: everyone.
    OpenToAll,
    /// A line that is not an IPv4 CIDR with a /8–/32 prefix and no host bits, or a repeat.
    Malformed(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Empty => write!(f, "the range list is empty"),
            Error::OpenToAll => write!(f, "the range list lets everyone in (0.0.0.0/0)"),
            Error::Malformed(l) => write!(f, "not a range: {l}"),
        }
    }
}

impl core::error::Error for Error {}

/// One IPv4 range: network address and prefix length.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Range {
    addr: u32,
    prefix: u8,
}

impl fmt::Display for Range {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let [a, b, c, d] = self.addr.to_be_bytes();
        write!(f, "{a}.{b}.{c}.{d}/{}", self.prefix)
    }
}

fn octet(s: &str) -> Option<u32> {
    let ok = (1..=3).contains(&s.len())
        && s.bytes().all(|b| b.is_ascii_digit())
        && (s.len() == 1 || !s.starts_with('0'));
    s.parse::<u32>().ok().filter(|v| ok && *v <= 255)
}

fn range(line: &str) -> Option<Range> {
    let (ip, len) = line.split_once('/')?;
    let parts: Vec<&str> = ip.split('.').collect();
    let len_ok = (1..=2).contains(&len.len()) && (len.len() == 1 || !len.starts_with('0'));
    let prefix = len.parse::<u8>().ok().filter(|_| len_ok)?;
    if parts.len() != 4 || !(8..=32).contains(&prefix) {
        return None;
    }
    let mut addr = 0u32;
    for p in parts {
        addr = (addr << 8) | octet(p)?;
    }
    // host bits set: the line does not say the range it seems to
    let host_bits = u32::MAX.checked_shr(u32::from(prefix)).unwrap_or(0);
    ((addr & host_bits) == 0).then_some(Range { addr, prefix })
}

/// The ranges of a list such as [`SOURCE_URL`] returns: one CIDR per line, blank lines
/// ignored, anything else refused (the whole list, not the line).
pub fn parse_ranges(text: &str) -> Result<Vec<Range>, Error> {
    let mut out: Vec<Range> = Vec::new();
    for line in text.lines().map(str::trim).filter(|l| !l.is_empty()) {
        if line == "0.0.0.0/0" {
            return Err(Error::OpenToAll);
        }
        let r = range(line).ok_or_else(|| Error::Malformed(line.to_string()))?;
        if out.contains(&r) {
            return Err(Error::Malformed(line.to_string()));
        }
        out.push(r);
    }
    if out.is_empty() {
        return Err(Error::Empty);
    }
    out.sort();
    Ok(out)
}

/// What a fetched list adds to and drops from the built-in one, as text.
pub fn difference(builtin: &[Range], fetched: &[Range]) -> (Vec<String>, Vec<String>) {
    let added = fetched.iter().filter(|r| !builtin.contains(r));
    let dropped = builtin.iter().filter(|r| !fetched.contains(r));
    (
        added.map(ToString::to_string).collect(),
        dropped.map(ToString::to_string).collect(),
    )
}

fn section(line: &str) -> Option<&str> {
    line.strip_prefix('[')
        .and_then(|l| l.split_once(']'))
        .map(|(s, _)| s.trim())
}

fn is_our_set(name: &str) -> bool {
    name.strip_prefix("IPSET ")
        .is_some_and(|n| n.trim().eq_ignore_ascii_case(IPSET))
}

fn uncommented(line: &str) -> &str {
    line.split_once('#').map_or(line, |(l, _)| l).trim()
}

/// The edge CT's firewall file with the set and the two rules put in: an old copy of the
/// set is replaced, old copies of the rules are taken out and the two put first in
/// `[RULES]` (a `[RULES]` section is added when there is none). Every other line stays.
pub fn apply(fw: &str, ranges: &[Range]) -> String {
    let mut kept: Vec<&str> = Vec::new();
    let mut in_set = false;
    for line in fw.lines() {
        if let Some(s) = section(line.trim()) {
            in_set = is_our_set(s);
        }
        if !in_set && !RULES.contains(&uncommented(line)) {
            kept.push(line);
        }
    }
    while kept.last().is_some_and(|l| l.trim().is_empty()) {
        kept.pop();
    }
    let mut out = String::new();
    let mut placed = false;
    for line in &kept {
        out.push_str(line);
        out.push('\n');
        if !placed && section(line.trim()) == Some("RULES") {
            for r in RULES {
                out.push_str(r);
                out.push('\n');
            }
            placed = true;
        }
    }
    if !placed {
        out.push_str("\n[RULES]\n");
        out.push_str(&RULES.join("\n"));
        out.push('\n');
    }
    out.push_str(&alloc::format!("\n[IPSET {IPSET}] # E6 H8: {SOURCE_URL}\n"));
    for r in ranges {
        out.push_str(&r.to_string());
        out.push('\n');
    }
    out
}

/// What the firewall file on disk says against the list: empty when it holds exactly what
/// [`apply`] puts there. Codes: `firewall_off`, `set_missing`, `range_missing <r>`,
/// `range_extra <r>`, `rules_not_first`.
pub fn readback(fw: &str, want: &[Range]) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    let mut sect = String::new();
    let mut set: Option<Vec<String>> = None;
    let mut rules: Vec<&str> = Vec::new();
    let mut enabled = false;
    for line in fw.lines().map(uncommented).filter(|l| !l.is_empty()) {
        if let Some(s) = section(line) {
            sect = s.to_string();
            if is_our_set(s) {
                set = Some(set.unwrap_or_default());
            }
            continue;
        }
        if sect.eq_ignore_ascii_case("OPTIONS") && line.replace(' ', "") == "enable:1" {
            enabled = true;
        } else if sect.eq_ignore_ascii_case("RULES") {
            rules.push(line);
        } else if is_our_set(&sect) {
            if let Some(s) = set.as_mut() {
                s.push(line.to_string());
            }
        }
    }
    if !enabled {
        found.push("firewall_off".to_string());
    }
    match set {
        None => found.push("set_missing".to_string()),
        Some(have) => {
            let want_s: Vec<String> = want.iter().map(ToString::to_string).collect();
            for w in want_s.iter().filter(|w| !have.contains(w)) {
                found.push(alloc::format!("range_missing {w}"));
            }
            for h in have.iter().filter(|h| !want_s.contains(h)) {
                found.push(alloc::format!("range_extra {h}"));
            }
        }
    }
    if rules.get(..2) != Some(&RULES[..]) {
        found.push("rules_not_first".to_string());
    }
    found
}

/// Whether the edge CT's firewall applies at all, by the same checks `site verify` uses:
/// S03 on the CT's configuration (`pct config`) and its file, H09 on the cluster file.
/// Empty when it does. Codes: `nic_firewall_off` (a card without `firewall=1`),
/// `edge_config_unread`, S03's other codes (`fw_disabled`, `fw_file_missing`,
/// `ct_policy_open`), `cluster_fw_off`, `cluster_policy_in_open`, `cluster_fw_unread`.
pub fn firewall_applies(
    pct_config: Option<&str>,
    edge_fw: Option<&str>,
    cluster_fw: Option<&str>,
) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    match verify::s03_firewall_attached(pct_config, edge_fw).outcome {
        Outcome::Green => {}
        Outcome::Red("fw_not_on_card") => found.push("nic_firewall_off".to_string()),
        Outcome::Red(c) | Outcome::Warn(c) => found.push(c.to_string()),
        Outcome::Unknown(_) => found.push("edge_config_unread".to_string()),
    }
    match verify::h09_cluster_firewall(cluster_fw).outcome {
        Outcome::Green => {}
        Outcome::Red("cluster_fw_off") => found.push("cluster_fw_off".to_string()),
        Outcome::Red(c) | Outcome::Warn(c) => found.push(alloc::format!("cluster_{c}")),
        Outcome::Unknown(_) => found.push("cluster_fw_unread".to_string()),
    }
    found
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    /// A list in the documentation ranges: tests never carry a real address.
    const SAMPLE: &str = "192.0.2.0/24\n198.51.100.0/24\n203.0.113.128/25\n";
    const EDGE_FW: &str = "[OPTIONS]\nenable: 1\npolicy_in: DROP\n\n[RULES]\nIN ACCEPT -p tcp -dport 80,443 -log nolog\nGROUP ez2-edge-v1\n";

    #[test]
    fn the_builtin_list_is_cloudflare_s_and_passes_its_own_check() {
        let r = builtin_ranges().unwrap();
        assert_eq!(r.len(), BUILTIN_V4.len());
        let mut shown: Vec<String> = r.iter().map(ToString::to_string).collect();
        let mut listed: Vec<String> = BUILTIN_V4.iter().map(|l| (*l).to_string()).collect();
        shown.sort();
        listed.sort();
        assert_eq!(shown, listed);
    }

    #[test]
    fn only_cloudflare_ranges_are_let_in() {
        let r = parse_ranges(SAMPLE).unwrap();
        let fw = apply(EDGE_FW, &r);
        // the two rules come first: the old "80,443 from anywhere" line is after the drop
        let rules: Vec<&str> = fw.lines().skip_while(|l| *l != "[RULES]").skip(1).collect();
        assert_eq!(rules[..2], RULES);
        assert!(rules[0].contains(&alloc::format!("-source +guest/{IPSET}")));
        assert!(rules[1].starts_with("IN DROP") && !rules[1].contains("-source"));
        assert_eq!(readback(&fw, &r), Vec::<String>::new());
        // applied twice is applied once; nothing else in the file is lost
        assert_eq!(apply(&fw, &r), fw);
        assert!(fw.contains("GROUP ez2-edge-v1\n") && fw.contains("policy_in: DROP\n"));
        // a file without the rules, or with them after another rule, reads back as such
        assert!(readback(EDGE_FW, &r).contains(&"rules_not_first".to_string()));
        assert!(readback(EDGE_FW, &r).contains(&"set_missing".to_string()));
        let late = fw.replacen("[RULES]\n", "[RULES]\nIN ACCEPT -p tcp -dport 443\n", 1);
        assert_eq!(readback(&late, &r), ["rules_not_first"]);
        let off = fw.replace("enable: 1", "enable: 0");
        assert_eq!(readback(&off, &r), ["firewall_off"]);
    }

    #[test]
    fn every_card_of_the_edge_must_carry_the_firewall() {
        let fw = apply(EDGE_FW, &parse_ranges(SAMPLE).unwrap());
        let one = "hostname: edge\nnet0: name=eth0,bridge=vmbr0,firewall=1\nunprivileged: 1\n";
        let cluster = "[OPTIONS]\nenable: 1\npolicy_in: DROP\n";
        assert_eq!(
            firewall_applies(Some(one), Some(&fw), Some(cluster)),
            Vec::<String>::new()
        );
        // a second card without firewall=1 is a way round every rule
        let two = alloc::format!("{one}net1: name=eth1,bridge=vmbr1\n");
        assert_eq!(
            firewall_applies(Some(&two), Some(&fw), Some(cluster)),
            ["nic_firewall_off"]
        );
        let off = cluster.replace("enable: 1", "enable: 0");
        assert_eq!(
            firewall_applies(Some(one), Some(&fw), Some(&off)),
            ["cluster_fw_off"]
        );
        assert_eq!(
            firewall_applies(None, Some(&fw), None),
            ["edge_config_unread", "cluster_fw_unread"]
        );
    }

    #[test]
    fn a_read_back_set_must_match_exactly() {
        let r = parse_ranges(SAMPLE).unwrap();
        let fw = apply(EDGE_FW, &r);
        let extra = alloc::format!("{fw}203.0.113.0/26\n");
        assert_eq!(readback(&extra, &r), ["range_extra 203.0.113.0/26"]);
        let short = fw.replace("198.51.100.0/24\n", "");
        assert_eq!(readback(&short, &r), ["range_missing 198.51.100.0/24"]);
        let (added, dropped) =
            difference(&r, &parse_ranges("192.0.2.0/24\n203.0.113.0/26").unwrap());
        assert_eq!(added, ["203.0.113.0/26"]);
        assert_eq!(dropped, ["198.51.100.0/24", "203.0.113.128/25"]);
    }

    #[test]
    fn a_malformed_range_list_is_refused() {
        assert_eq!(parse_ranges(""), Err(Error::Empty));
        assert_eq!(parse_ranges("\n \n"), Err(Error::Empty));
        assert_eq!(parse_ranges("0.0.0.0/0\n"), Err(Error::OpenToAll));
        assert_eq!(
            parse_ranges("192.0.2.0/24\n0.0.0.0/0"),
            Err(Error::OpenToAll)
        );
        for bad in [
            "192.0.2.0",
            "10.0.0.0/7",
            "0.0.0.0/1",
            "192.0.2.0/33",
            "192.0.2.0/024",
            "192.0.002.0/24",
            "192.0.2.1/24",
            "192.0.2.0/24 x",
            "256.0.0.0/8",
            "2001:db8::/32",
            "<html>",
            "192.0.2.0/24\n192.0.2.0/24",
        ] {
            assert!(
                matches!(parse_ranges(bad), Err(Error::Malformed(_))),
                "{bad} was taken"
            );
        }
        assert_eq!(
            parse_ranges("192.0.2.7/32").unwrap()[0].to_string(),
            "192.0.2.7/32"
        );
    }
}
