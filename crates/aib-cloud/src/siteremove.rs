// SPDX-License-Identifier: Apache-2.0
//! The order in which a site is removed, what is next when the work is found half way,
//! and up to which step it can still be called off. No I/O: the host shell probes each
//! step and hands the answers here.
//!
//! * Removing a site is one operation in the journal ([`crate::op`]) and is asked for by
//!   naming the site **and** its main address; a code alone is one typing slip away from
//!   another site.
//! * Nothing that cannot be put back happens before an archive of the whole site exists
//!   and was checked. The order of the steps says so, and a later step found done while
//!   an earlier one is not stops the work.
//! * Every step is probed before anything is done; "could not tell" stops the work. Each
//!   step is safe to do twice, so a removal found half way goes on from the first step
//!   that is known not to be done.
//! * Until the archive is made and checked a removal can be called off and the site put
//!   back as it was. From the licence step on the only way is forward: a site that is half removed is finished, not
//!   revived. Bringing a site back is a restore from the archive, which is its own
//!   operation.
//! * What the site holds at vendors (one activation of each paid plugin) is given back
//!   while the site still runs. When a vendor cannot be reached the removal stops there;
//!   it goes on only when the person removing says in so many words that the
//!   activations are left at the vendor, and that is written down. An activation that no
//!   program can give back (the vendor has no way to do it from the site, or the site no
//!   longer holds what identifies it) does not stop the removal: it is listed as left at
//!   the vendor and the removal goes on ([`licences_probe`], [`left_at_vendor`]).
//! * When the site's address is in a DNS zone we manage, the address records we made for
//!   the site are deleted near the end ([`RemoveStep::Dns`]). Only records carrying the
//!   site's own marker ([`dns_marker`]) are ours; mail, text and any record without the
//!   marker are left alone and reported. A zone we do not manage is passed over and said.
//! * The record of the site is not deleted. It is moved aside with the time and the
//!   archive's name, so a removed site can be told from one that never was.

use crate::sitecreate::Probe;

/// One step of removing a site, in the order they are done.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum RemoveStep {
    /// The entry of the host no longer sends anyone to the site.
    Unserve,
    /// An archive of the whole site is made and checked (it is there, it is whole, it is
    /// of this site).
    Archive,
    /// Each paid plugin's activation is given back to its vendor.
    Licenses,
    /// The customer's way in by shell is closed on the host.
    Access,
    /// The container is stopped.
    Stop,
    /// The container and its disk are destroyed.
    Destroy,
    /// The container's firewall file is removed.
    Firewall,
    /// The site is taken off the host's export list. (The id ranges given to its
    /// container are not looked at here; they are not given out again either way.)
    Lists,
    /// The address records made for the site are deleted from its DNS zone, when that
    /// zone is one we manage. Nothing is lost for good: the zone is snapshotted first.
    Dns,
    /// The site's record is moved aside, marked removed.
    Record,
}

/// Every step, first to last.
pub const STEPS: [RemoveStep; 10] = [
    RemoveStep::Unserve,
    RemoveStep::Archive,
    RemoveStep::Licenses,
    RemoveStep::Access,
    RemoveStep::Stop,
    RemoveStep::Destroy,
    RemoveStep::Firewall,
    RemoveStep::Lists,
    RemoveStep::Dns,
    RemoveStep::Record,
];

impl RemoveStep {
    /// The fixed word for the journal and the progress answer.
    pub fn as_str(self) -> &'static str {
        match self {
            RemoveStep::Unserve => "unserve",
            RemoveStep::Archive => "archive",
            RemoveStep::Licenses => "licenses",
            RemoveStep::Access => "access",
            RemoveStep::Stop => "stop",
            RemoveStep::Destroy => "destroy",
            RemoveStep::Firewall => "firewall",
            RemoveStep::Lists => "lists",
            RemoveStep::Dns => "dns",
            RemoveStep::Record => "record",
        }
    }
}

/// Whether what was typed names the site to remove: the code and the main address of
/// the record, both exactly. Nothing is trimmed or folded; a near miss is a miss.
pub fn confirmed(
    record_code: &str,
    record_main_address: &str,
    typed_code: &str,
    typed_address: &str,
) -> bool {
    !record_code.is_empty()
        && !record_main_address.is_empty()
        && record_code == typed_code
        && record_main_address == typed_address
}

/// What to do now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Next {
    /// Do this step.
    Run(RemoveStep),
    /// Every step is done.
    Finished,
    /// Do nothing and say this word; a person looks.
    Stop(&'static str, RemoveStep),
}

/// Whether a step may already be done before the steps ahead of it, without that being
/// a sign that something went wrong: the DNS step is "done" from the start when the
/// zone is not ours or the site never had a record of ours, and deleting a record loses
/// nothing that cannot be put back.
pub fn may_be_done_early(step: RemoveStep) -> bool {
    step == RemoveStep::Dns
}

/// The next thing to do, from one probe per step in [`STEPS`] order.
/// `licenses_left_at_vendor` is the person's explicit word that the activations stay at
/// the vendors: the licence step is then passed over whatever its probe says. Fewer or
/// more probes than steps is a stop, not a guess.
pub fn next(probes: &[Probe], licenses_left_at_vendor: bool) -> Next {
    if probes.len() != STEPS.len() {
        return Next::Stop("probes_incomplete", RemoveStep::Unserve);
    }
    let mut first_open: Option<RemoveStep> = None;
    for (step, probe) in STEPS.iter().zip(probes) {
        if licenses_left_at_vendor && *step == RemoveStep::Licenses {
            continue;
        }
        match (probe, first_open) {
            (Probe::Unknown, _) => return Next::Stop("probe_unknown", *step),
            (Probe::Done, Some(_)) if !may_be_done_early(*step) => {
                return Next::Stop("steps_out_of_order", *step)
            }
            (Probe::NotDone, None) => first_open = Some(*step),
            _ => {}
        }
    }
    match first_open {
        Some(step) => Next::Run(step),
        None => Next::Finished,
    }
}

/// Whether a removal that stands before `next_step` can still be called off with the
/// site put back as it was: only while nothing was given back, closed or destroyed.
/// The licence step itself is past that line: its vendors are asked one after another,
/// and a removal found before that step may already have given one activation back.
pub fn may_call_off(next_step: RemoveStep) -> bool {
    next_step < RemoveStep::Licenses
}

/// Whether a removal whose next move is `next` can still be called off. Only a plain
/// "do this step" can say yes: then every later step was probed and is known not to be
/// done. A stop — a probe that could not tell, or steps out of order — says nothing
/// about the steps behind it (the container may already be gone while the entry merely
/// could not be asked), so it is never "can be put back as it was". Both the plan and
/// the part that carries a removal out ask this, not [`may_call_off`] on a bare step.
pub fn may_call_off_now(next: Next) -> bool {
    matches!(next, Next::Run(step) if may_call_off(step))
}

/// Whether doing `step` may lose something for good, so that the archive must be known
/// to be good before it.
pub fn needs_archive(step: RemoveStep) -> bool {
    step > RemoveStep::Archive
}

/// The first step from what the host's entry is configured with: `None` when the
/// configuration could not be read. The marker line is compared the way the code that
/// swaps a site's block compares it (trailing white space does not count). A
/// configuration without the marker in which a block still opens with the site's main
/// address is not "taken off": it is "could not tell".
pub fn unserve_probe(entry_config: Option<&str>, code: &str, main_address: &str) -> Probe {
    match entry_config {
        // an empty answer is not a configuration without this site
        None | Some("") => Probe::Unknown,
        Some(text) => {
            let marker = crate::site::caddy_marker(code);
            if text.lines().any(|l| l.trim_end() == marker) {
                return Probe::NotDone;
            }
            let opens_with_address = text.lines().any(|l| {
                l.trim_end().strip_suffix('{').is_some_and(|names| {
                    names
                        .split(|c: char| c == ',' || c.is_whitespace())
                        .any(|n| !main_address.is_empty() && n == main_address)
                })
            });
            if opens_with_address {
                Probe::Unknown
            } else {
                Probe::Done
            }
        }
    }
}

/// Whether container `ct` is in the host's container list (`pct list`). `None` when the
/// list is missing or is not that list: its header line must be there before "not in it"
/// is believed.
pub fn container_listed(container_list: Option<&str>, ct: u32) -> Option<bool> {
    let text = container_list?;
    let mut lines = text.lines();
    if !lines.next()?.starts_with("VMID") {
        return None;
    }
    let id = alloc::format!("{ct}");
    Some(lines.any(|l| l.split_whitespace().next() == Some(id.as_str())))
}

/// The customer's way in by shell: closed when neither the live rules nor the host's
/// network file forward the site's port to it. `live_rules` is the listing of the
/// forwarding chain, `network_file` the file the rules are loaded from at boot; either
/// missing, or a listing with no rule line at all, is "could not tell".
pub fn access_probe(
    live_rules: Option<&str>,
    network_file: Option<&str>,
    port: u16,
    ip: &str,
) -> Probe {
    let (Some(rules), Some(file)) = (live_rules, network_file) else {
        return Probe::Unknown;
    };
    if !rules
        .lines()
        .any(|l| l.starts_with("-P ") || l.starts_with("-A "))
        || file.is_empty()
    {
        return Probe::Unknown;
    }
    let dport = alloc::format!("--dport {port} ");
    let to = alloc::format!("--to-destination {ip}:22");
    let forwards = |text: &str| {
        text.lines().any(|l| {
            let l = l.trim_end();
            l.contains(dport.as_str())
                && (l.ends_with(to.as_str()) || l.contains(&alloc::format!("{to} ")))
        })
    };
    if forwards(rules) || forwards(file) {
        Probe::NotDone
    } else {
        Probe::Done
    }
}

/// Whether the site is off the host's export list (one `<code> <ct> <path>` per line,
/// `#` lines are comments). `None`: the list could not be read.
pub fn lists_probe(export_list: Option<&str>, code: &str) -> Probe {
    match export_list {
        None => Probe::Unknown,
        Some(text) => {
            if text
                .lines()
                .filter(|l| !l.trim_start().starts_with('#'))
                .any(|l| l.split_whitespace().next() == Some(code))
            {
                Probe::NotDone
            } else {
                Probe::Done
            }
        }
    }
}

/// One paid plugin's activation, as the site and its vendor answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Seat {
    /// The vendor holds an activation for this site and it can be given back from here.
    Held,
    /// The vendor holds none for this site (never activated, or already given back).
    NotHeld,
    /// An activation may be held, but no program can give it back: the vendor has no
    /// way to do it from the site, or the site no longer holds what identifies it.
    NoAutomaticReturn,
    /// The site or the vendor could not be asked.
    Unknown,
}

/// One plugin and what was answered for it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SeatSeen<'a> {
    pub plugin: &'a str,
    pub seat: Seat,
}

/// The licence step from every paid plugin's answer. `None`: the site could not be
/// asked at all. One "could not ask" is "could not tell"; one activation that can still
/// be given back is "not done"; activations that no program can give back do not hold
/// the removal up (they go into [`left_at_vendor`]).
pub fn licences_probe(seats: Option<&[SeatSeen]>) -> Probe {
    let Some(seats) = seats else {
        return Probe::Unknown;
    };
    if seats.iter().any(|s| s.seat == Seat::Unknown) {
        Probe::Unknown
    } else if seats.iter().any(|s| s.seat == Seat::Held) {
        Probe::NotDone
    } else {
        Probe::Done
    }
}

/// The plugins whose activations are left at the vendor because no program can give
/// them back; written into the record of the removed site.
pub fn left_at_vendor<'a>(seats: &[SeatSeen<'a>]) -> alloc::vec::Vec<&'a str> {
    seats
        .iter()
        .filter(|s| s.seat == Seat::NoAutomaticReturn)
        .map(|s| s.plugin)
        .collect()
}

/// The start of the comment every DNS record made for a site carries.
pub const DNS_MARKER_PREFIX: &str = "ez2:site:";

/// The comment a DNS record made for site `code` carries.
pub fn dns_marker(code: &str) -> alloc::string::String {
    alloc::format!("{DNS_MARKER_PREFIX}{code}")
}

/// The record types that point an address somewhere. Only these can be ours.
pub const DNS_ADDRESS_TYPES: [&str; 3] = ["A", "AAAA", "CNAME"];

/// One record of a DNS zone, as listed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DnsRecord<'a> {
    /// The record type (`A`, `MX`, …).
    pub kind: &'a str,
    pub name: &'a str,
    /// The record's comment; empty when it has none.
    pub comment: &'a str,
}

/// Which records under the site's address are to be deleted and which are left.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DnsPick {
    /// The zone's listing was not complete or could not be read.
    Unknown,
    /// `ours`: address records carrying the site's marker (indexes into the listing).
    /// `foreign`: address records under the same name without it; never deleted, said.
    Picked {
        ours: alloc::vec::Vec<usize>,
        foreign: alloc::vec::Vec<usize>,
    },
}

fn same_name(a: &str, b: &str) -> bool {
    let a = a.strip_suffix('.').unwrap_or(a);
    let b = b.strip_suffix('.').unwrap_or(b);
    !a.is_empty() && a.eq_ignore_ascii_case(b)
}

/// The records to delete for site `code` at address `name`. `records` is the zone's whole
/// listing, `None` when it was not complete. Records of other types under the name (mail,
/// text, …) are not looked at; address records without the site's marker are foreign.
pub fn dns_to_remove(records: Option<&[DnsRecord]>, name: &str, code: &str) -> DnsPick {
    let Some(records) = records else {
        return DnsPick::Unknown;
    };
    let marker = dns_marker(code);
    let mut ours = alloc::vec::Vec::new();
    let mut foreign = alloc::vec::Vec::new();
    for (i, r) in records.iter().enumerate() {
        if !same_name(r.name, name)
            || !DNS_ADDRESS_TYPES
                .iter()
                .any(|t| t.eq_ignore_ascii_case(r.kind))
        {
            continue;
        }
        if r.comment == marker {
            ours.push(i);
        } else {
            foreign.push(i);
        }
    }
    DnsPick::Picked { ours, foreign }
}

/// Whether the zone of the site's address is one we manage.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DnsRights {
    Ours,
    NotOurs,
    Unknown,
}

/// What the DNS step found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DnsProbe {
    /// The zone is not one we manage: nothing to do here, and that is said.
    Skipped,
    /// No record of ours is left.
    Done,
    /// A record of ours is still there.
    NotDone,
    /// The zone or its listing could not be read.
    Unknown,
}

impl DnsProbe {
    /// As a step's probe: a zone we do not manage does not hold the removal up.
    pub fn probe(self) -> Probe {
        match self {
            DnsProbe::Skipped | DnsProbe::Done => Probe::Done,
            DnsProbe::NotDone => Probe::NotDone,
            DnsProbe::Unknown => Probe::Unknown,
        }
    }

    /// The fixed word for the plan and the record.
    pub fn as_str(self) -> &'static str {
        match self {
            DnsProbe::Skipped => "skipped_zone_not_ours",
            DnsProbe::Done => "done",
            DnsProbe::NotDone => "not_done",
            DnsProbe::Unknown => "unknown",
        }
    }
}

/// The DNS step from whether the zone is ours and what was picked in it.
pub fn dns_probe(rights: DnsRights, pick: &DnsPick) -> DnsProbe {
    match (rights, pick) {
        (DnsRights::NotOurs, _) => DnsProbe::Skipped,
        (DnsRights::Unknown, _) | (DnsRights::Ours, DnsPick::Unknown) => DnsProbe::Unknown,
        (DnsRights::Ours, DnsPick::Picked { ours, .. }) if ours.is_empty() => DnsProbe::Done,
        (DnsRights::Ours, DnsPick::Picked { .. }) => DnsProbe::NotDone,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::vec::Vec;

    fn probes(done: usize) -> Vec<Probe> {
        (0..STEPS.len())
            .map(|i| {
                if i < done {
                    Probe::Done
                } else {
                    Probe::NotDone
                }
            })
            .collect()
    }

    #[test]
    fn the_steps_are_in_the_order_they_are_done() {
        let mut sorted = STEPS;
        sorted.sort();
        assert_eq!(sorted, STEPS);
        let mut words: Vec<&str> = STEPS.iter().map(|s| s.as_str()).collect();
        words.sort_unstable();
        words.dedup();
        assert_eq!(words.len(), STEPS.len());
    }

    #[test]
    fn the_site_and_its_address_must_both_be_named_exactly() {
        assert!(confirmed(
            "site9",
            "www.example.com",
            "site9",
            "www.example.com"
        ));
        for (code, address) in [
            ("site9", "example.com"),
            ("site9", "www.example.com "),
            ("site9", "WWW.example.com"),
            ("site8", "www.example.com"),
            ("site9", ""),
            ("", "www.example.com"),
        ] {
            assert!(
                !confirmed("site9", "www.example.com", code, address),
                "{code} {address}"
            );
        }
        assert!(!confirmed("", "", "", ""));
        assert!(!confirmed("site9", "", "site9", ""));
    }

    #[test]
    fn the_first_step_not_done_is_next() {
        for (done, step) in STEPS.iter().enumerate() {
            assert_eq!(next(&probes(done), false), Next::Run(*step), "{done}");
        }
        assert_eq!(next(&probes(STEPS.len()), false), Next::Finished);
    }

    #[test]
    fn a_probe_that_could_not_tell_stops_the_work() {
        for (i, step) in STEPS.iter().enumerate() {
            let mut p = probes(i);
            p[i] = Probe::Unknown;
            assert_eq!(next(&p, false), Next::Stop("probe_unknown", *step), "{i}");
        }
    }

    #[test]
    fn nothing_is_destroyed_before_the_archive_is_known_good() {
        // the archive is not done (or not known) and a later step already is: stop
        for later in 2..STEPS.len() {
            if may_be_done_early(STEPS[later]) {
                continue;
            }
            let mut p = probes(1);
            p[later] = Probe::Done;
            assert_eq!(
                next(&p, false),
                Next::Stop("steps_out_of_order", STEPS[later]),
                "{later}"
            );
        }
        let mut p = probes(STEPS.len());
        p[1] = Probe::Unknown;
        assert_eq!(
            next(&p, false),
            Next::Stop("probe_unknown", RemoveStep::Archive)
        );
        // and the same when the person leaves the activations at the vendors
        assert_eq!(
            next(&p, true),
            Next::Stop("probe_unknown", RemoveStep::Archive)
        );
        for step in STEPS {
            assert_eq!(needs_archive(step), step > RemoveStep::Archive, "{step:?}");
        }
        assert!(RemoveStep::Archive < RemoveStep::Licenses);
        assert!(RemoveStep::Archive < RemoveStep::Destroy);
    }

    #[test]
    fn licences_are_given_back_while_the_site_still_runs() {
        assert!(RemoveStep::Licenses < RemoveStep::Stop);
        assert!(RemoveStep::Licenses < RemoveStep::Destroy);
    }

    #[test]
    fn a_vendor_that_cannot_be_reached_stops_the_removal_unless_the_person_says_so() {
        let at = STEPS
            .iter()
            .position(|s| *s == RemoveStep::Licenses)
            .unwrap();
        // the vendors could not be asked: stop there
        let mut p = probes(at);
        p[at] = Probe::Unknown;
        assert_eq!(
            next(&p, false),
            Next::Stop("probe_unknown", RemoveStep::Licenses)
        );
        assert_eq!(next(&p, true), Next::Run(STEPS[at + 1]));
        // the activations are known to be still there: give them back
        let mut p = probes(at);
        p[at] = Probe::NotDone;
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Licenses));
        assert_eq!(next(&p, true), Next::Run(STEPS[at + 1]));
    }

    #[test]
    fn a_removal_that_stopped_cannot_be_called_off() {
        // untouched: the next move is the first step, and it can still be called off
        assert!(may_call_off_now(next(&probes(0), false)));
        assert!(may_call_off_now(next(&probes(1), false)));
        // from the licence step on, and when finished: no
        assert!(!may_call_off_now(next(&probes(2), false)));
        assert!(!may_call_off_now(next(&probes(STEPS.len()), false)));
        // the entry could not be asked while everything behind it is already done
        // (the container is gone): a stop at the first step is not "nothing was lost"
        let mut p = probes(STEPS.len());
        p[0] = Probe::Unknown;
        let stopped = next(&p, false);
        assert_eq!(stopped, Next::Stop("probe_unknown", RemoveStep::Unserve));
        assert!(!may_call_off_now(stopped));
        // steps out of order: the archive is not done and the licences were given back
        let mut p = probes(0);
        p[1] = Probe::Done;
        p[2] = Probe::Done;
        let stopped = next(&p, false);
        assert!(matches!(stopped, Next::Stop("steps_out_of_order", _)));
        assert!(!may_call_off_now(stopped));
        // the wrong number of probes
        assert!(!may_call_off_now(next(&[], false)));
    }

    #[test]
    fn the_wrong_number_of_probes_is_a_stop() {
        assert!(matches!(
            next(&probes(3)[..5], false),
            Next::Stop("probes_incomplete", _)
        ));
        assert!(matches!(
            next(&[], true),
            Next::Stop("probes_incomplete", _)
        ));
    }

    #[test]
    fn a_removal_can_be_called_off_only_while_nothing_is_lost() {
        for step in STEPS {
            let want = matches!(step, RemoveStep::Unserve | RemoveStep::Archive);
            assert_eq!(may_call_off(step), want, "{step:?}");
        }
    }
    #[test]
    fn the_entry_is_read_by_the_sites_own_marker_line() {
        let with = "# site:site9\nwww.example.com {\n}\n# site:site90\nother.example.com {\n}\n";
        let p = |text: Option<&str>, code: &str| unserve_probe(text, code, "www.example.com");
        assert_eq!(p(Some(with), "site9"), Probe::NotDone);
        assert_eq!(
            unserve_probe(Some(with), "site90", "other.example.com"),
            Probe::NotDone
        );
        // a longer code that merely starts the same is another site
        assert_eq!(
            p(Some("# site:site90\nx.example.com {\n}\n"), "site9"),
            Probe::Done
        );
        assert_eq!(p(None, "site9"), Probe::Unknown);
        assert_eq!(p(Some(""), "site9"), Probe::Unknown);
        // white space or a carriage return after the marker: the block is still there
        for tail in [" ", "\t", "\r", "  \r"] {
            let text = alloc::format!("# site:site9{tail}\nwww.example.com {{\n}}\n");
            assert_eq!(p(Some(&text), "site9"), Probe::NotDone, "{tail:?}");
        }
        // the marker is gone but a block still opens with the site's address: not "taken off"
        for text in [
            "www.example.com {\n}\n",
            "other.example.com, www.example.com {\n}\n",
            "www.example.com other.example.com {\n}\n",
        ] {
            assert_eq!(p(Some(text), "site9"), Probe::Unknown, "{text}");
        }
        // an address that only contains it, or a mention inside a block, is not a block of its own
        assert_eq!(p(Some("xwww.example.com {\n}\n"), "site9"), Probe::Done);
        assert_eq!(
            p(
                Some("other.example.com {\n\tredir https://www.example.com\n}\n"),
                "site9"
            ),
            Probe::Done
        );
    }

    #[test]
    fn a_container_is_absent_only_on_a_list_that_answered() {
        let list = "VMID       Status     Lock         Name\n101        running                 edge\n1120       running                 other\n";
        assert_eq!(container_listed(Some(list), 101), Some(true));
        assert_eq!(container_listed(Some(list), 112), Some(false));
        assert_eq!(container_listed(Some(list), 1120), Some(true));
        assert_eq!(container_listed(Some(""), 112), None);
        assert_eq!(container_listed(Some("no such command\n"), 112), None);
        assert_eq!(container_listed(None, 112), None);
        // a host with no containers still prints its header
        assert_eq!(
            container_listed(Some("VMID       Status     Lock         Name\n"), 112),
            Some(false)
        );
    }

    #[test]
    fn the_way_in_is_closed_only_when_neither_place_forwards_it() {
        let live = "-P PREROUTING ACCEPT\n-A PREROUTING -i eth9 -p tcp -m tcp --dport 2112 -j DNAT --to-destination 10.77.0.12:22\n";
        let none = "-P PREROUTING ACCEPT\n-A PREROUTING -i eth9 -p tcp -m multiport --dports 80,443 -j DNAT --to-destination 10.77.0.2\n";
        let file = "iface x\n    post-up   iptables -t nat -A PREROUTING -i eth9 -p tcp --dport 2112 -j DNAT --to-destination 10.77.0.12:22 2>/dev/null\n";
        let clean = "iface x\n";
        assert_eq!(
            access_probe(Some(live), Some(clean), 2112, "10.77.0.12"),
            Probe::NotDone
        );
        assert_eq!(
            access_probe(Some(none), Some(file), 2112, "10.77.0.12"),
            Probe::NotDone
        );
        assert_eq!(
            access_probe(Some(none), Some(clean), 2112, "10.77.0.12"),
            Probe::Done
        );
        // another site's port, and a port that only starts the same
        assert_eq!(
            access_probe(Some(live), Some(clean), 2113, "10.77.0.13"),
            Probe::Done
        );
        assert_eq!(
            access_probe(Some(live), Some(clean), 211, "10.77.0.12"),
            Probe::Done
        );
        // could not tell: a missing listing, a listing with no rule line, an empty file
        assert_eq!(
            access_probe(None, Some(clean), 2112, "10.77.0.12"),
            Probe::Unknown
        );
        assert_eq!(
            access_probe(Some(none), None, 2112, "10.77.0.12"),
            Probe::Unknown
        );
        assert_eq!(
            access_probe(Some(""), Some(clean), 2112, "10.77.0.12"),
            Probe::Unknown
        );
        assert_eq!(
            access_probe(Some(none), Some(""), 2112, "10.77.0.12"),
            Probe::Unknown
        );
    }

    #[test]
    fn the_export_list_is_read_by_its_first_word() {
        let list = "# site9 is only named in a comment\nsite90 190 /var/www/site90\n";
        assert_eq!(lists_probe(Some(list), "site9"), Probe::Done);
        assert_eq!(lists_probe(Some(list), "site90"), Probe::NotDone);
        assert_eq!(lists_probe(Some(""), "site9"), Probe::Done);
        assert_eq!(lists_probe(None, "site9"), Probe::Unknown);
    }

    #[test]
    fn dns_comes_after_the_licences_and_before_the_record() {
        assert!(RemoveStep::Licenses < RemoveStep::Dns);
        assert!(RemoveStep::Lists < RemoveStep::Dns);
        assert!(RemoveStep::Dns < RemoveStep::Record);
        assert!(!may_call_off(RemoveStep::Dns));
        assert!(needs_archive(RemoveStep::Dns));
    }

    #[test]
    fn a_dns_step_done_from_the_start_does_not_stop_the_work() {
        let at = STEPS.iter().position(|s| *s == RemoveStep::Dns).unwrap();
        // untouched site whose zone is not ours (or never had a record of ours)
        let mut p = probes(0);
        p[at] = DnsProbe::Skipped.probe();
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Unserve));
        assert!(may_call_off_now(next(&p, false)));
        // every step before it done: the next one is the record
        let mut p = probes(at);
        p[at] = DnsProbe::Done.probe();
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Record));
        // only the DNS step may be early: the record moved aside before the DNS is not
        let mut p = probes(at);
        p[at + 1] = Probe::Done;
        assert_eq!(
            next(&p, false),
            Next::Stop("steps_out_of_order", RemoveStep::Record)
        );
        for step in STEPS {
            assert_eq!(may_be_done_early(step), step == RemoveStep::Dns, "{step:?}");
        }
        // a record of ours still there is the next thing to do once the lists are done
        let mut p = probes(at);
        p[at] = DnsProbe::NotDone.probe();
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Dns));
        // could not tell: stop, also when it is the only open question
        let mut p = probes(STEPS.len());
        p[at] = DnsProbe::Unknown.probe();
        assert_eq!(
            next(&p, false),
            Next::Stop("probe_unknown", RemoveStep::Dns)
        );
    }

    #[test]
    fn only_address_records_with_the_sites_marker_are_ours() {
        let marker = dns_marker("s-abc123");
        assert_eq!(marker, "ez2:site:s-abc123");
        assert!(marker.starts_with(DNS_MARKER_PREFIX));
        let recs = [
            DnsRecord {
                kind: "A",
                name: "example.test",
                comment: &marker,
            },
            DnsRecord {
                kind: "MX",
                name: "example.test",
                comment: &marker,
            },
            DnsRecord {
                kind: "TXT",
                name: "example.test",
                comment: "",
            },
            DnsRecord {
                kind: "CNAME",
                name: "www.example.test",
                comment: "",
            },
            DnsRecord {
                kind: "AAAA",
                name: "Example.Test.",
                comment: &marker,
            },
            DnsRecord {
                kind: "A",
                name: "example.test",
                comment: "made by hand",
            },
            DnsRecord {
                kind: "A",
                name: "example.test",
                comment: "ez2:site:s-abc1234",
            },
            DnsRecord {
                kind: "A",
                name: "other.example.test",
                comment: &marker,
            },
            DnsRecord {
                kind: "cname",
                name: "example.test",
                comment: &marker,
            },
        ];
        assert_eq!(
            dns_to_remove(Some(&recs), "example.test", "s-abc123"),
            DnsPick::Picked {
                ours: alloc::vec![0, 4, 8],
                foreign: alloc::vec![5, 6],
            }
        );
        // nothing under the name: nothing to delete, nothing foreign
        assert_eq!(
            dns_to_remove(Some(&recs), "none.example.test", "s-abc123"),
            DnsPick::Picked {
                ours: Vec::new(),
                foreign: Vec::new()
            }
        );
        // an empty name never matches
        assert_eq!(
            dns_to_remove(
                Some(&[DnsRecord {
                    kind: "A",
                    name: "",
                    comment: &marker
                }]),
                "",
                "s-abc123"
            ),
            DnsPick::Picked {
                ours: Vec::new(),
                foreign: Vec::new()
            }
        );
        // a listing that was not complete
        assert_eq!(
            dns_to_remove(None, "example.test", "s-abc123"),
            DnsPick::Unknown
        );
    }

    #[test]
    fn the_dns_step_is_passed_over_only_when_the_zone_is_not_ours() {
        let ours = DnsPick::Picked {
            ours: alloc::vec![0],
            foreign: Vec::new(),
        };
        let none = DnsPick::Picked {
            ours: Vec::new(),
            foreign: alloc::vec![1],
        };
        assert_eq!(dns_probe(DnsRights::NotOurs, &ours), DnsProbe::Skipped);
        assert_eq!(
            dns_probe(DnsRights::NotOurs, &DnsPick::Unknown),
            DnsProbe::Skipped
        );
        assert_eq!(dns_probe(DnsRights::Ours, &ours), DnsProbe::NotDone);
        assert_eq!(dns_probe(DnsRights::Ours, &none), DnsProbe::Done);
        assert_eq!(
            dns_probe(DnsRights::Ours, &DnsPick::Unknown),
            DnsProbe::Unknown
        );
        assert_eq!(dns_probe(DnsRights::Unknown, &none), DnsProbe::Unknown);
        assert_eq!(DnsProbe::Skipped.probe(), Probe::Done);
        assert_eq!(DnsProbe::Done.probe(), Probe::Done);
        assert_eq!(DnsProbe::NotDone.probe(), Probe::NotDone);
        assert_eq!(DnsProbe::Unknown.probe(), Probe::Unknown);
        let mut words: Vec<&str> = [
            DnsProbe::Skipped,
            DnsProbe::Done,
            DnsProbe::NotDone,
            DnsProbe::Unknown,
        ]
        .iter()
        .map(|d| d.as_str())
        .collect();
        words.sort_unstable();
        words.dedup();
        assert_eq!(words.len(), 4);
    }

    #[test]
    fn activations_no_program_can_give_back_do_not_hold_the_removal_up() {
        let s = |plugin, seat| SeatSeen { plugin, seat };
        let left_only = [
            s("seo", Seat::NotHeld),
            s("blocks", Seat::NoAutomaticReturn),
            s("theme", Seat::NoAutomaticReturn),
        ];
        assert_eq!(licences_probe(Some(&left_only)), Probe::Done);
        assert_eq!(left_at_vendor(&left_only), alloc::vec!["blocks", "theme"]);
        // and the removal goes on past the licence step without the person's word
        let at = STEPS
            .iter()
            .position(|x| *x == RemoveStep::Licenses)
            .unwrap();
        let mut p = probes(at);
        p[at] = licences_probe(Some(&left_only));
        assert_eq!(next(&p, false), Next::Run(STEPS[at + 1]));
        // one that can still be given back: not done
        let held = [s("seo", Seat::Held), s("blocks", Seat::NoAutomaticReturn)];
        assert_eq!(licences_probe(Some(&held)), Probe::NotDone);
        // one vendor that could not be asked: could not tell (the person's word is still needed)
        let unknown = [
            s("seo", Seat::Unknown),
            s("blocks", Seat::NoAutomaticReturn),
        ];
        assert_eq!(licences_probe(Some(&unknown)), Probe::Unknown);
        assert_eq!(licences_probe(None), Probe::Unknown);
        // no paid plugin at all
        assert_eq!(licences_probe(Some(&[])), Probe::Done);
        assert!(left_at_vendor(&[s("seo", Seat::Held)]).is_empty());
    }
}
