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
/// a sign that something went wrong. Each is "done" from the start for many sites, and
/// none loses anything that cannot be put back:
/// * the shell way in: a site whose port was never forwarded has nothing to close;
/// * the DNS step: the zone is not ours, or the site never had a record of ours;
/// * the licences: a site whose paid plugins hold nothing that can be given back (never
///   activated, a blank site, or only activations no program can give back) has nothing
///   to do at that step.
pub fn may_be_done_early(step: RemoveStep) -> bool {
    matches!(step, RemoveStep::Access | RemoveStep::Dns) || step == RemoveStep::Licenses
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
///
/// The steps that may be done early ([`may_be_done_early`]) loosen that promise: behind a
/// "do this step" they may already be done (the shell way in closed, our DNS records
/// gone), whoever did it. Calling off then leaves them as they were found; it does not
/// open the way in or put records back. Nothing is lost for good either way. The plan
/// lists them ([`done_early`]) so a person sees it.
pub fn may_call_off_now(next: Next) -> bool {
    matches!(next, Next::Run(step) if may_call_off(step))
}

/// The steps behind the next one that are already done although the next one is not:
/// only steps that [`may_be_done_early`] can be in it (any other stops the work). Empty
/// unless the next move is "do this step".
pub fn done_early(probes: &[Probe], next: Next) -> alloc::vec::Vec<RemoveStep> {
    let Next::Run(at) = next else {
        return alloc::vec::Vec::new();
    };
    STEPS
        .iter()
        .zip(probes)
        .filter(|(step, probe)| **step > at && **probe == Probe::Done)
        .map(|(step, _)| *step)
        .collect()
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

/// How a paid plugin's activation is given back when its site is removed. Fixed per
/// plugin in [`PAID_PLUGINS`] from what was done by hand (the reviewed `site-remove.sh`
/// step 3 of the ez2-cloud-site-manual skill), never guessed for a plugin not listed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GiveBack {
    /// The plugin gives the activation back from inside the site, and the vendor can be
    /// asked again afterwards whether this site still holds one.
    InSite,
    /// No program gives it back (Andy 11-11: "if it cannot be done automatically, leave
    /// it for now"): an activation found is written into the removed site's record as
    /// left at the vendor. It never holds the removal up and is not chased.
    RecordOnly,
}

/// One paid plugin of the template and how its activation is given back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PaidPlugin {
    /// The fixed word for the plan and the record.
    pub name: &'static str,
    pub give_back: GiveBack,
}

/// Every paid plugin of the template, and whether its activation can be given back by a
/// program. The host asks the site about each of these and only these.
/// * SEOPress Pro: the vendor's own deactivate call, sent from inside the site with the
///   site's key (only when it is the template's key); the vendor is asked again after.
/// * Fluent Forms Pro: the plugin's own licensing client deactivates; the vendor is asked
///   again in the same call (deactivating clears the key the site keeps).
/// * FlyingPress: no deactivation was ever tried by hand, and a failed licence call clears
///   the key the plugin keeps; recorded only.
/// * Stackable and Blocksy: activated through Freemius, which has no reliable command-line
///   deactivation (DEV 4.6 §15.2 item 5); recorded only.
pub const PAID_PLUGINS: [PaidPlugin; 5] = [
    PaidPlugin {
        name: "seopress",
        give_back: GiveBack::InSite,
    },
    PaidPlugin {
        name: "fluentformpro",
        give_back: GiveBack::InSite,
    },
    PaidPlugin {
        name: "flyingpress",
        give_back: GiveBack::RecordOnly,
    },
    PaidPlugin {
        name: "stackable",
        give_back: GiveBack::RecordOnly,
    },
    PaidPlugin {
        name: "blocksy",
        give_back: GiveBack::RecordOnly,
    },
];

/// How `plugin`'s activation is given back; `None` for a plugin that is not in
/// [`PAID_PLUGINS`].
pub fn give_back_of(plugin: &str) -> Option<GiveBack> {
    PAID_PLUGINS
        .iter()
        .find(|p| p.name == plugin)
        .map(|p| p.give_back)
}

/// What the site (and through it the vendor) answered for one paid plugin.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VendorAnswer {
    /// This site holds an activation (for a plugin given back in the site: the vendor's
    /// own answer; for one only recorded: the site's record of its activation).
    Active,
    /// This site holds none.
    NotActive,
    /// The site holds nothing that identifies an activation (no key, the plugin is not
    /// there); one may still be held at the vendor for this address.
    NoData,
    /// The key in the site is not shown to be the template's (another key, or the
    /// template's could not be read): not ours to give back.
    KeyNotTemplate,
    /// The vendor says the key itself is not good (expired, revoked).
    NotValid,
    /// The site or the vendor did not answer, or answered something not understood.
    Unknown,
}

/// Why a plugin is listed as left at its vendor: fixed words, after `<plugin>:`.
pub const LEFT_ACTIVATION: &str = "activation";
pub const LEFT_NOT_ASKED: &str = "not_asked";
pub const LEFT_NO_DATA: &str = "no_data_in_site";
pub const LEFT_KEY_NOT_TEMPLATE: &str = "key_not_template";
pub const LEFT_NOT_VALID: &str = "key_not_valid";
/// The site could not be read at all (its container is gone or stopped, or the site
/// software did not answer): every paid plugin is listed with this word and the removal
/// goes on (Andy 11-11).
pub const SITE_UNREADABLE: &str = "site_unreadable";

/// One plugin's seat from what was answered, and why it is left at the vendor when it is.
pub fn seat_of(plugin: &str, answer: VendorAnswer) -> (Seat, Option<&'static str>) {
    let Some(how) = give_back_of(plugin) else {
        return (Seat::Unknown, None);
    };
    match (how, answer) {
        (GiveBack::InSite, VendorAnswer::Active) => (Seat::Held, None),
        (_, VendorAnswer::NotActive) => (Seat::NotHeld, None),
        (GiveBack::InSite, VendorAnswer::Unknown) => (Seat::Unknown, None),
        (GiveBack::InSite, VendorAnswer::NoData) => (Seat::NoAutomaticReturn, Some(LEFT_NO_DATA)),
        (GiveBack::RecordOnly, VendorAnswer::NoData) => (Seat::NotHeld, None),
        (GiveBack::RecordOnly, VendorAnswer::Active) => {
            (Seat::NoAutomaticReturn, Some(LEFT_ACTIVATION))
        }
        (GiveBack::RecordOnly, VendorAnswer::Unknown) => {
            (Seat::NoAutomaticReturn, Some(LEFT_NOT_ASKED))
        }
        (_, VendorAnswer::KeyNotTemplate) => (Seat::NoAutomaticReturn, Some(LEFT_KEY_NOT_TEMPLATE)),
        (_, VendorAnswer::NotValid) => (Seat::NoAutomaticReturn, Some(LEFT_NOT_VALID)),
    }
}

/// The licence step as looked at: its probe, what is left at the vendors
/// (`<plugin>:<why>`), and the plugins to give back now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LicenceLook {
    pub probe: Probe,
    pub left: alloc::vec::Vec<alloc::string::String>,
    pub to_give_back: alloc::vec::Vec<&'static str>,
}

/// The licence step from what the site answered. `wp_site` false: a blank site, which has
/// no paid plugins. `answers` `None`: the site could not be read, so every paid plugin is
/// left at its vendor as [`SITE_UNREADABLE`] and the step does not hold the removal up.
/// Otherwise every plugin of [`PAID_PLUGINS`] must be answered exactly once and nothing
/// else; anything short of that is "could not tell".
pub fn licences_look(wp_site: bool, answers: Option<&[(&str, VendorAnswer)]>) -> LicenceLook {
    let mut look = LicenceLook {
        probe: Probe::Done,
        left: alloc::vec::Vec::new(),
        to_give_back: alloc::vec::Vec::new(),
    };
    if !wp_site {
        return look;
    }
    let Some(answers) = answers else {
        look.left = PAID_PLUGINS
            .iter()
            .map(|p| alloc::format!("{}:{SITE_UNREADABLE}", p.name))
            .collect();
        return look;
    };
    let complete = answers.len() == PAID_PLUGINS.len()
        && PAID_PLUGINS
            .iter()
            .all(|p| answers.iter().filter(|(n, _)| *n == p.name).count() == 1);
    if !complete {
        look.probe = Probe::Unknown;
        return look;
    }
    let mut seats = alloc::vec::Vec::new();
    for p in PAID_PLUGINS {
        let Some((_, answer)) = answers.iter().find(|(n, _)| *n == p.name) else {
            look.probe = Probe::Unknown;
            return look;
        };
        let (seat, why) = seat_of(p.name, *answer);
        if seat == Seat::Held {
            look.to_give_back.push(p.name);
        }
        if let Some(why) = why {
            look.left.push(alloc::format!("{}:{why}", p.name));
        }
        seats.push(SeatSeen {
            plugin: p.name,
            seat,
        });
    }
    look.probe = licences_probe(Some(&seats));
    look
}

/// The call that gives one activation back did not succeed.
pub const LICENCE_DEACTIVATE_FAILED: &str = "licence_deactivate_failed";
/// The vendor still has the site as activated after the call.
pub const LICENCE_STILL_ACTIVE: &str = "licence_still_active";
/// The vendor's answer after the call is not "not activated".
pub const LICENCE_NOT_CONFIRMED: &str = "licence_not_confirmed";

/// After giving one activation back: `call_ok` whether the call succeeded, `now` the
/// vendor's answer to a fresh question. Only a call that succeeded and a vendor that now
/// says "not activated" count; anything else is a fixed word and the work stops.
pub fn give_back_after(call_ok: bool, now: VendorAnswer) -> Result<(), &'static str> {
    match (call_ok, now) {
        (false, _) => Err(LICENCE_DEACTIVATE_FAILED),
        (true, VendorAnswer::NotActive) => Ok(()),
        (true, VendorAnswer::Active) => Err(LICENCE_STILL_ACTIVE),
        (true, _) => Err(LICENCE_NOT_CONFIRMED),
    }
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

/// The records to delete for site `code`, whose addresses are `names` (every address of
/// the site's record, not only the main one). `records` is the zone's whole listing,
/// `None` when it was not complete. Records of other types under those names (mail, text,
/// …) are not looked at; address records under them without the site's marker are
/// foreign. The marker binds a record to this site's code, so a record of ours is found
/// under any of the site's names; a name that is not the site's is never looked at.
pub fn dns_to_remove(records: Option<&[DnsRecord]>, names: &[&str], code: &str) -> DnsPick {
    let Some(records) = records else {
        return DnsPick::Unknown;
    };
    let marker = dns_marker(code);
    let mut ours = alloc::vec::Vec::new();
    let mut foreign = alloc::vec::Vec::new();
    for (i, r) in records.iter().enumerate() {
        if !names.iter().any(|n| same_name(r.name, n))
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
///
/// `NotOurs` only when the zones under our account were listed completely and the
/// address is in none of them. A key that does not work, a refused or failed call, or a
/// listing that is not complete is `Unknown`, never `NotOurs`: passing the step over on
/// such an answer would leave the records without saying so.
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
    /// No record of ours is left, but address records under the site's names without our
    /// marker are (made by hand, or before the marker was used). They are not deleted;
    /// this is said in the plan and in the record of the removed site, never as plain done.
    DoneForeignLeft,
    /// A record of ours is still there.
    NotDone,
    /// The zone or its listing could not be read.
    Unknown,
    /// This part of the work cannot ask DNS at all (the key is kept elsewhere). The steps
    /// before it go on; the work stops when the DNS step is the next one ([`next_with_dns`]).
    NotAskedHere,
    /// The records were deleted where the key is kept, and that work's ledger line was
    /// handed in and checked ([`dns_ledger_check`]): done, and said as done elsewhere.
    RemovedElsewhere,
}

impl DnsProbe {
    /// As a step's probe: a zone we do not manage does not hold the removal up.
    pub fn probe(self) -> Probe {
        match self {
            DnsProbe::Skipped | DnsProbe::Done | DnsProbe::DoneForeignLeft => Probe::Done,
            DnsProbe::NotDone | DnsProbe::NotAskedHere => Probe::NotDone,
            DnsProbe::Unknown => Probe::Unknown,
            DnsProbe::RemovedElsewhere => Probe::Done,
        }
    }

    /// The fixed word for the plan and the record.
    pub fn as_str(self) -> &'static str {
        match self {
            DnsProbe::Skipped => "skipped_zone_not_ours",
            DnsProbe::Done => "done",
            DnsProbe::DoneForeignLeft => "done_records_without_our_marker_left",
            DnsProbe::NotDone => "not_done",
            DnsProbe::Unknown => "unknown",
            DnsProbe::NotAskedHere => "not_asked_here",
            DnsProbe::RemovedElsewhere => "removed_elsewhere",
        }
    }
}

/// The DNS step from whether the zone is ours and what was picked in it.
pub fn dns_probe(rights: DnsRights, pick: &DnsPick) -> DnsProbe {
    match (rights, pick) {
        (DnsRights::NotOurs, _) => DnsProbe::Skipped,
        (DnsRights::Unknown, _) | (DnsRights::Ours, DnsPick::Unknown) => DnsProbe::Unknown,
        (DnsRights::Ours, DnsPick::Picked { ours, foreign }) if ours.is_empty() => {
            if foreign.is_empty() {
                DnsProbe::Done
            } else {
                DnsProbe::DoneForeignLeft
            }
        }
        (DnsRights::Ours, DnsPick::Picked { .. }) => DnsProbe::NotDone,
    }
}

/// Where [`next_with_dns`] stops when the DNS step is the one left and this part of the
/// work cannot ask DNS.
pub const DNS_NOT_ASKED_HERE: Next = Next::Stop("dns_not_asked_here", RemoveStep::Dns);

/// [`next`] for a removal whose DNS step was found as `dns`. When this part of the work
/// cannot ask DNS ([`DnsProbe::NotAskedHere`]) the DNS step is not held against the steps
/// around it: the steps before it go on as usual, and once DNS is the only step left (also
/// for a site already removed apart from DNS, whose record step is done) the work stops
/// with its own word, never as "steps out of order".
pub fn next_with_dns(probes: &[Probe], licenses_left_at_vendor: bool, dns: DnsProbe) -> Next {
    let at = STEPS.iter().position(|s| *s == RemoveStep::Dns);
    let held = dns == DnsProbe::NotAskedHere;
    let at = match at {
        Some(at) if held && probes.get(at) == Some(&Probe::NotDone) => at,
        _ => return next(probes, licenses_left_at_vendor),
    };
    let mut p = probes.to_vec();
    p[at] = Probe::Done; // not held against the steps around it
    match next(&p, licenses_left_at_vendor) {
        Next::Run(step) if step < RemoveStep::Dns => Next::Run(step),
        Next::Run(_) | Next::Finished => DNS_NOT_ASKED_HERE,
        other => other,
    }
}

/// The first word of a DNS ledger line.
pub const DNS_LEDGER_WORD: &str = "ez2-dns-removed";
/// The longest ledger line taken.
pub const DNS_LEDGER_MAX: usize = 512;
/// The line is not a ledger line of the form [`dns_ledger_check`] takes.
pub const DNS_LEDGER_BAD_FORMAT: &str = "dns_ledger_bad_format";
/// The line's marker is not this site's (`ez2:site:<code>`).
pub const DNS_LEDGER_WRONG_SITE: &str = "dns_ledger_wrong_site";
/// The line's names are not exactly the site's addresses.
pub const DNS_LEDGER_NAMES_DIFFER: &str = "dns_ledger_names_differ";

/// Whether `line`, the ledger line of a DNS removal done where the DNS key is kept, says
/// that the records of site `code`, whose addresses are `names`, were deleted. The line is
/// exactly
///
/// `ez2-dns-removed marker=ez2:site:<code> names=<name>,<name>… at=<YYYYMMDDTHHMMSSZ> ref=<evidence>`
///
/// one space between the parts, in that order, nothing else; printable ASCII only, at most
/// [`DNS_LEDGER_MAX`] bytes. The marker must be this site's own ([`dns_marker`]) and the
/// names exactly the site's addresses (each once, in any order, case and a final dot
/// aside). `ref` says where the removal's own output is kept. Anything else is refused with
/// a fixed word; nothing is guessed.
pub fn dns_ledger_check(line: &str, code: &str, names: &[&str]) -> Result<(), &'static str> {
    if line.is_empty()
        || line.len() > DNS_LEDGER_MAX
        || !line.bytes().all(|b| b == b' ' || b.is_ascii_graphic())
    {
        return Err(DNS_LEDGER_BAD_FORMAT);
    }
    let parts: alloc::vec::Vec<&str> = line.split(' ').collect();
    let [word, marker, listed, at, evidence] = parts.as_slice() else {
        return Err(DNS_LEDGER_BAD_FORMAT);
    };
    fn field<'a>(part: &'a str, key: &str) -> Option<&'a str> {
        part.strip_prefix(key)?
            .strip_prefix('=')
            .filter(|v| !v.is_empty())
    }
    let (Some(marker), Some(listed), Some(at), Some(_)) = (
        field(marker, "marker"),
        field(listed, "names"),
        field(at, "at"),
        field(evidence, "ref"),
    ) else {
        return Err(DNS_LEDGER_BAD_FORMAT);
    };
    let stamp = at.as_bytes();
    let stamp_ok = stamp.len() == 16
        && stamp[8] == b'T'
        && stamp[15] == b'Z'
        && stamp
            .iter()
            .enumerate()
            .all(|(i, b)| i == 8 || i == 15 || b.is_ascii_digit());
    if *word != DNS_LEDGER_WORD || !marker.starts_with(DNS_MARKER_PREFIX) || !stamp_ok {
        return Err(DNS_LEDGER_BAD_FORMAT);
    }
    if code.is_empty() || marker != dns_marker(code) {
        return Err(DNS_LEDGER_WRONG_SITE);
    }
    let listed: alloc::vec::Vec<&str> = listed.split(',').collect();
    if listed.iter().any(|n| n.is_empty()) {
        return Err(DNS_LEDGER_BAD_FORMAT);
    }
    let once = |n: &str, all: &[&str]| all.iter().filter(|m| same_name(n, m)).count() == 1;
    let same = !names.is_empty()
        && listed.len() == names.len()
        && listed.iter().all(|n| once(n, names))
        && names.iter().all(|n| once(n, listed.as_slice()));
    if !same {
        return Err(DNS_LEDGER_NAMES_DIFFER);
    }
    Ok(())
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
            assert_eq!(
                may_be_done_early(step),
                step == RemoveStep::Access || step == RemoveStep::Dns,
                "{step:?}"
            );
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
            dns_to_remove(Some(&recs), &["example.test"], "s-abc123"),
            DnsPick::Picked {
                ours: alloc::vec![0, 4, 8],
                foreign: alloc::vec![5, 6],
            }
        );
        // nothing under the name: nothing to delete, nothing foreign
        assert_eq!(
            dns_to_remove(Some(&recs), &["none.example.test"], "s-abc123"),
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
                &[""],
                "s-abc123"
            ),
            DnsPick::Picked {
                ours: Vec::new(),
                foreign: Vec::new()
            }
        );
        // a listing that was not complete
        assert_eq!(
            dns_to_remove(None, &["example.test"], "s-abc123"),
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
            foreign: Vec::new(),
        };
        let foreign_only = DnsPick::Picked {
            ours: Vec::new(),
            foreign: alloc::vec![1],
        };
        let both = DnsPick::Picked {
            ours: alloc::vec![0],
            foreign: alloc::vec![1],
        };
        assert_eq!(dns_probe(DnsRights::NotOurs, &ours), DnsProbe::Skipped);
        assert_eq!(
            dns_probe(DnsRights::NotOurs, &DnsPick::Unknown),
            DnsProbe::Skipped
        );
        assert_eq!(dns_probe(DnsRights::Ours, &ours), DnsProbe::NotDone);
        assert_eq!(dns_probe(DnsRights::Ours, &none), DnsProbe::Done);
        // a record under the site's name without our marker is left and said, never plain done
        assert_eq!(
            dns_probe(DnsRights::Ours, &foreign_only),
            DnsProbe::DoneForeignLeft
        );
        assert_eq!(dns_probe(DnsRights::Ours, &both), DnsProbe::NotDone);
        assert_eq!(DnsProbe::DoneForeignLeft.probe(), Probe::Done);
        assert_ne!(DnsProbe::DoneForeignLeft.as_str(), DnsProbe::Done.as_str());
        assert_eq!(DnsProbe::NotAskedHere.probe(), Probe::NotDone);
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
            DnsProbe::DoneForeignLeft,
            DnsProbe::NotAskedHere,
            DnsProbe::RemovedElsewhere,
        ]
        .iter()
        .map(|d| d.as_str())
        .collect();
        words.sort_unstable();
        words.dedup();
        assert_eq!(words.len(), 7);
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

    #[test]
    fn a_site_whose_way_in_was_never_opened_does_not_stop_the_work() {
        let at = STEPS.iter().position(|s| *s == RemoveStep::Access).unwrap();
        // untouched site without a forwarded port: the entry is still first
        let mut p = probes(0);
        p[at] = Probe::Done;
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Unserve));
        assert!(may_call_off_now(next(&p, false)));
        // half way: the archive is made, the licences are next
        let mut p = probes(2);
        p[at] = Probe::Done;
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Licenses));
        // past the licences the next one is the stop, not the access step
        let mut p = probes(at);
        p[at] = Probe::Done;
        assert_eq!(next(&p, false), Next::Run(RemoveStep::Stop));
        // the steps that destroy are still never early
        let mut p = probes(1);
        p[at + 2] = Probe::Done;
        assert_eq!(
            next(&p, false),
            Next::Stop("steps_out_of_order", RemoveStep::Destroy)
        );
    }

    #[test]
    fn every_address_of_the_site_is_looked_at() {
        let marker = dns_marker("s-abc123");
        let recs = [
            DnsRecord {
                kind: "A",
                name: "example.test",
                comment: &marker,
            },
            DnsRecord {
                kind: "CNAME",
                name: "www.example.test",
                comment: &marker,
            },
            DnsRecord {
                kind: "A",
                name: "shop.example.test",
                comment: "",
            },
            DnsRecord {
                kind: "A",
                name: "not-the-sites.example.test",
                comment: &marker,
            },
        ];
        let names = ["example.test", "www.example.test", "shop.example.test"];
        assert_eq!(
            dns_to_remove(Some(&recs), &names, "s-abc123"),
            DnsPick::Picked {
                ours: alloc::vec![0, 1],
                foreign: alloc::vec![2],
            }
        );
        // only the main address given: the record under the second name is missed
        assert_eq!(
            dns_to_remove(Some(&recs), &names[..1], "s-abc123"),
            DnsPick::Picked {
                ours: alloc::vec![0],
                foreign: Vec::new(),
            }
        );
        assert_eq!(
            dns_to_remove(Some(&recs), &[], "s-abc123"),
            DnsPick::Picked {
                ours: Vec::new(),
                foreign: Vec::new(),
            }
        );
    }

    #[test]
    fn a_part_that_cannot_ask_dns_lets_the_steps_before_it_go_on() {
        let at = STEPS.iter().position(|s| *s == RemoveStep::Dns).unwrap();
        let mut p = probes(0);
        p[at] = DnsProbe::NotAskedHere.probe();
        let n = next_with_dns(&p, true, DnsProbe::NotAskedHere);
        assert_eq!(n, Next::Run(RemoveStep::Unserve));
        assert!(may_call_off_now(n));
        // everything before it done: stop at the DNS step with its own word
        let mut p = probes(at);
        p[at] = DnsProbe::NotAskedHere.probe();
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::NotAskedHere),
            Next::Stop("dns_not_asked_here", RemoveStep::Dns)
        );
        // a site already removed apart from DNS (record step done): its own word, not
        // "steps out of order" (33e28 r2 L1)
        let mut p = probes(STEPS.len());
        p[at] = DnsProbe::NotAskedHere.probe();
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::NotAskedHere),
            DNS_NOT_ASKED_HERE
        );
        assert_eq!(
            next_with_dns(&p, true, DnsProbe::NotAskedHere),
            DNS_NOT_ASKED_HERE
        );
        // a step after DNS not done yet while DNS cannot be asked: still DNS's word
        let mut p = probes(at);
        p[at] = DnsProbe::NotAskedHere.probe();
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::NotAskedHere),
            DNS_NOT_ASKED_HERE
        );
        // asked and a record of ours is there: do the step
        let mut p = probes(at);
        p[at] = DnsProbe::NotDone.probe();
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::NotDone),
            Next::Run(RemoveStep::Dns)
        );
        // an answer that could not be read still stops wherever it is
        let mut p = probes(0);
        p[at] = DnsProbe::Unknown.probe();
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::Unknown),
            Next::Stop("probe_unknown", RemoveStep::Dns)
        );
    }

    #[test]
    fn steps_done_early_are_listed_behind_a_step_that_can_be_called_off() {
        let access = STEPS.iter().position(|s| *s == RemoveStep::Access).unwrap();
        let dns = STEPS.iter().position(|s| *s == RemoveStep::Dns).unwrap();
        let mut p = probes(0);
        p[access] = Probe::Done;
        p[dns] = Probe::Done;
        let n = next(&p, false);
        assert!(may_call_off_now(n));
        assert_eq!(
            done_early(&p, n),
            alloc::vec![RemoveStep::Access, RemoveStep::Dns]
        );
        // nothing early: nothing listed; a stop or finished: nothing listed
        assert!(done_early(&probes(0), next(&probes(0), false)).is_empty());
        let all = probes(STEPS.len());
        assert!(done_early(&all, next(&all, false)).is_empty());
        let mut p = probes(0);
        p[0] = Probe::Unknown;
        assert!(done_early(&p, next(&p, false)).is_empty());
    }

    // ------------------------------------------------------------------ licences, by vendor

    fn every(answer: VendorAnswer) -> Vec<(&'static str, VendorAnswer)> {
        PAID_PLUGINS.iter().map(|p| (p.name, answer)).collect()
    }

    fn with(
        mut a: Vec<(&'static str, VendorAnswer)>,
        plugin: &str,
        answer: VendorAnswer,
    ) -> Vec<(&'static str, VendorAnswer)> {
        for x in &mut a {
            if x.0 == plugin {
                x.1 = answer;
            }
        }
        a
    }

    #[test]
    fn the_vendor_table_says_which_plugins_are_given_back_and_guesses_no_other() {
        let mut names: Vec<&str> = PAID_PLUGINS.iter().map(|p| p.name).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), PAID_PLUGINS.len());
        assert_eq!(give_back_of("seopress"), Some(GiveBack::InSite));
        assert_eq!(give_back_of("fluentformpro"), Some(GiveBack::InSite));
        for p in ["flyingpress", "stackable", "blocksy"] {
            assert_eq!(give_back_of(p), Some(GiveBack::RecordOnly), "{p}");
        }
        assert_eq!(give_back_of("someother"), None);
        assert_eq!(
            seat_of("someother", VendorAnswer::Active),
            (Seat::Unknown, None)
        );
        assert_eq!(
            seat_of("someother", VendorAnswer::NotActive),
            (Seat::Unknown, None)
        );
    }

    #[test]
    fn an_activation_given_back_in_the_site_is_held_until_the_vendor_says_otherwise() {
        let none = every(VendorAnswer::NotActive);
        let look = licences_look(true, Some(&none));
        assert_eq!(look.probe, Probe::Done);
        assert!(look.left.is_empty() && look.to_give_back.is_empty());
        // SEOPress held: given back here
        let held = with(none.clone(), "seopress", VendorAnswer::Active);
        let look = licences_look(true, Some(&held));
        assert_eq!(look.probe, Probe::NotDone);
        assert_eq!(look.to_give_back, ["seopress"]);
        // the vendor of a plugin given back here did not answer: could not tell
        let unknown = with(none.clone(), "fluentformpro", VendorAnswer::Unknown);
        assert_eq!(licences_look(true, Some(&unknown)).probe, Probe::Unknown);
        // only recorded: an activation, or a site that did not say, is left and does not hold up
        let rec = with(
            with(none.clone(), "flyingpress", VendorAnswer::Active),
            "blocksy",
            VendorAnswer::Unknown,
        );
        let look = licences_look(true, Some(&rec));
        assert_eq!(look.probe, Probe::Done);
        assert_eq!(look.left, ["flyingpress:activation", "blocksy:not_asked"]);
        assert!(look.to_give_back.is_empty());
        // a key that is not the template's, an expired key, no data: left, said why
        let odd = with(
            with(
                with(none, "seopress", VendorAnswer::KeyNotTemplate),
                "fluentformpro",
                VendorAnswer::NoData,
            ),
            "stackable",
            VendorAnswer::NotValid,
        );
        let look = licences_look(true, Some(&odd));
        assert_eq!(look.probe, Probe::Done);
        assert_eq!(
            look.left,
            [
                "seopress:key_not_template",
                "fluentformpro:no_data_in_site",
                "stackable:key_not_valid"
            ]
        );
        // a plugin only recorded with nothing in the site: nothing left
        assert_eq!(
            seat_of("flyingpress", VendorAnswer::NoData),
            (Seat::NotHeld, None)
        );
    }

    #[test]
    fn a_site_that_cannot_be_read_is_recorded_and_does_not_hold_the_removal_up() {
        let look = licences_look(true, None);
        assert_eq!(look.probe, Probe::Done);
        assert_eq!(look.left.len(), PAID_PLUGINS.len());
        assert!(
            look.left.iter().all(|l| l.ends_with(":site_unreadable")),
            "{:?}",
            look.left
        );
        assert!(look.to_give_back.is_empty());
        // a blank site has no paid plugins at all
        let look = licences_look(false, None);
        assert_eq!(look.probe, Probe::Done);
        assert!(look.left.is_empty());
    }

    #[test]
    fn an_answer_that_does_not_cover_every_paid_plugin_once_is_could_not_tell() {
        let all = every(VendorAnswer::NotActive);
        assert_eq!(licences_look(true, Some(&all[1..])).probe, Probe::Unknown);
        let mut extra = all.clone();
        extra.push(("someother", VendorAnswer::NotActive));
        assert_eq!(licences_look(true, Some(&extra)).probe, Probe::Unknown);
        let mut twice = all.clone();
        twice[1] = twice[0];
        assert_eq!(licences_look(true, Some(&twice)).probe, Probe::Unknown);
        assert_eq!(licences_look(true, Some(&[])).probe, Probe::Unknown);
    }

    #[test]
    fn given_back_only_when_the_call_succeeded_and_the_vendor_now_says_not_activated() {
        assert_eq!(give_back_after(true, VendorAnswer::NotActive), Ok(()));
        assert_eq!(
            give_back_after(false, VendorAnswer::NotActive),
            Err(LICENCE_DEACTIVATE_FAILED)
        );
        assert_eq!(
            give_back_after(true, VendorAnswer::Active),
            Err(LICENCE_STILL_ACTIVE)
        );
        for now in [
            VendorAnswer::Unknown,
            VendorAnswer::NoData,
            VendorAnswer::NotValid,
            VendorAnswer::KeyNotTemplate,
        ] {
            assert_eq!(
                give_back_after(true, now),
                Err(LICENCE_NOT_CONFIRMED),
                "{now:?}"
            );
        }
    }

    #[test]
    fn nothing_to_give_back_at_the_start_does_not_stop_the_work() {
        let at = STEPS
            .iter()
            .position(|s| *s == RemoveStep::Licenses)
            .unwrap();
        let mut p = probes(0);
        p[at] = Probe::Done;
        let n = next(&p, false);
        assert_eq!(n, Next::Run(RemoveStep::Unserve));
        assert!(may_call_off_now(n));
        assert_eq!(done_early(&p, n), [RemoveStep::Licenses]);
    }

    // ------------------------------------------------------------------ DNS done elsewhere

    const LEDGER: &str = "ez2-dns-removed marker=ez2:site:site9 names=www.example.com,shop.example.com at=20261111T150000Z ref=dns-rm-site9-20261111T150000Z.log";

    #[test]
    fn a_dns_ledger_line_is_taken_only_for_this_site_and_its_own_names() {
        let names = ["www.example.com", "shop.example.com"];
        assert_eq!(dns_ledger_check(LEDGER, "site9", &names), Ok(()));
        // names in another order, upper case, a final dot: the same names
        let other_order = LEDGER.replace(
            "www.example.com,shop.example.com",
            "SHOP.example.com.,www.example.com",
        );
        assert_eq!(dns_ledger_check(&other_order, "site9", &names), Ok(()));
        // another site's marker, or a site whose code merely starts the same
        assert_eq!(
            dns_ledger_check(LEDGER, "site90", &names),
            Err(DNS_LEDGER_WRONG_SITE)
        );
        let site90 = LEDGER.replace("ez2:site:site9 ", "ez2:site:site90 ");
        assert_eq!(
            dns_ledger_check(&site90, "site9", &names),
            Err(DNS_LEDGER_WRONG_SITE)
        );
        assert_eq!(
            dns_ledger_check(LEDGER, "", &names),
            Err(DNS_LEDGER_WRONG_SITE)
        );
        // one name short, one name more, one name twice
        assert_eq!(
            dns_ledger_check(LEDGER, "site9", &names[..1]),
            Err(DNS_LEDGER_NAMES_DIFFER)
        );
        let short = LEDGER.replace(",shop.example.com", "");
        assert_eq!(
            dns_ledger_check(&short, "site9", &names),
            Err(DNS_LEDGER_NAMES_DIFFER)
        );
        let twice = LEDGER.replace("shop.example.com", "www.example.com");
        assert_eq!(
            dns_ledger_check(&twice, "site9", &names),
            Err(DNS_LEDGER_NAMES_DIFFER)
        );
        assert_eq!(
            dns_ledger_check(LEDGER, "site9", &[]),
            Err(DNS_LEDGER_NAMES_DIFFER)
        );
        // not the form: every one refused with the same word
        for bad in [
            alloc::string::String::new(),
            LEDGER.replace("ez2-dns-removed", "ez2-dns-added"),
            LEDGER.replace("marker=ez2:site:", "marker=ez3:site:"),
            LEDGER.replace(" at=", "  at="),
            LEDGER.replace("at=20261111T150000Z", "at=2026-11-11"),
            LEDGER.replace("at=20261111T150000Z", "at=20261111X150000Z"),
            LEDGER.replace(" ref=dns-rm-site9-20261111T150000Z.log", ""),
            LEDGER.replace("ref=dns-rm-site9-20261111T150000Z.log", "ref="),
            alloc::format!("{LEDGER} extra=1"),
            alloc::format!("{LEDGER}\n"),
            LEDGER.replace("names=", "hosts="),
            LEDGER.replace(",shop.example.com", ","),
            LEDGER.replace("ref=dns", "ref=\u{7f}dns"),
            alloc::format!("{LEDGER}{}", "x".repeat(DNS_LEDGER_MAX)),
        ] {
            assert_eq!(
                dns_ledger_check(&bad, "site9", &names),
                Err(DNS_LEDGER_BAD_FORMAT),
                "{bad:?}"
            );
        }
        // the DNS step done elsewhere is done, said as its own word
        assert_eq!(DnsProbe::RemovedElsewhere.probe(), Probe::Done);
        assert_eq!(DnsProbe::RemovedElsewhere.as_str(), "removed_elsewhere");
        let mut p = probes(STEPS.len());
        let dns = STEPS.iter().position(|s| *s == RemoveStep::Dns).unwrap();
        p[dns] = DnsProbe::RemovedElsewhere.probe();
        p[dns + 1] = Probe::NotDone;
        assert_eq!(
            next_with_dns(&p, false, DnsProbe::RemovedElsewhere),
            Next::Run(RemoveStep::Record)
        );
    }
}
