// SPDX-License-Identifier: Apache-2.0
//! Calling off an opening that stopped half way (`site create <domain> --abandon <code>`):
//! what is removed, in which order, and when nothing may be touched. No I/O: the host
//! shell looks and hands the answers here.
//!
//! An opening that stopped after the clone is not a site yet: `site remove` reads only the
//! site records and cannot see it. Until now it was taken down by hand (`pct destroy`,
//! then the opening record). This does the same, checked, in order, each step written down
//! so it can be carried on (c477a, review of the e1a sheet).
//!
//! * **Only an opening.** A code that has a full site record, or the record of an opened
//!   site, is a site: [`ALREADY_A_SITE`], nothing is touched. The opening record must be
//!   for the code and the domain asked ([`NOT_THIS_OPENING`]).
//! * **Only its container.** The container number comes from the opening record. It is
//!   destroyed only when it carries the code as its hostname (the clone names it so) and
//!   no site record names that number ([`CT_NOT_THIS_OPENING`], [`CT_OF_A_SITE`]).
//! * **Could not tell stops.** Any probe that could not be read stops before anything is
//!   done ([`PROBE_UNKNOWN`]); it is never read as done nor as not done.
//! * **Numbers are not given back.** The record moves to the abandoned openings, where the
//!   number and the address stay counted as used, like a removed site's.

use crate::sitecreate::Probe;

/// One thing an abandoned opening leaves that is taken away, in the order it is done.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum AbandonStep {
    /// The container is stopped and destroyed with its disk.
    Container,
    /// Its firewall file is removed.
    Firewall,
    /// The DNS records carrying the site's mark are deleted, when the opening got as far as
    /// adding them.
    Dns,
    /// The site's own passwords on the host are deleted.
    Secrets,
    /// The opening record moves to the abandoned openings.
    Record,
}

/// Every step, first to last.
pub const STEPS: [AbandonStep; 5] = [
    AbandonStep::Container,
    AbandonStep::Firewall,
    AbandonStep::Dns,
    AbandonStep::Secrets,
    AbandonStep::Record,
];

impl AbandonStep {
    /// The fixed word for the journal and the plan.
    pub fn as_str(self) -> &'static str {
        match self {
            AbandonStep::Container => "container",
            AbandonStep::Firewall => "firewall",
            AbandonStep::Dns => "dns",
            AbandonStep::Secrets => "secrets",
            AbandonStep::Record => "record",
        }
    }
}

/// The code has a full site record or an opened site's record: it is a site.
pub const ALREADY_A_SITE: &str = "already_a_site";
/// No opening record for this code and domain.
pub const NOT_THIS_OPENING: &str = "not_this_opening";
/// The container under the record's number is not named for the code.
pub const CT_NOT_THIS_OPENING: &str = "ct_not_this_opening";
/// A site record names the record's container number.
pub const CT_OF_A_SITE: &str = "ct_of_a_site";
/// A probe could not be read.
pub const PROBE_UNKNOWN: &str = "probe_unknown";
/// Fewer or more probes than steps.
pub const PROBES_INCOMPLETE: &str = "probes_incomplete";

/// What the host saw before anything is done.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Guard<'a> {
    /// Whether the code has a full or opened site record; `None` could not tell.
    pub site_record: Option<bool>,
    /// Whether the opening record is there and is for this code and domain.
    pub opening_matches: bool,
    /// The container under the record's number: `Some(None)` there is none,
    /// `Some(Some(hostname))` it is there (an empty name when it has none), `None` could
    /// not tell.
    pub ct_hostname: Option<Option<&'a str>>,
    /// Whether a site record names the record's container number; `None` could not tell.
    pub ct_of_a_site: Option<bool>,
}

/// Why nothing may be touched, or `None` when the opening may be abandoned.
pub fn refusal(code: &str, g: &Guard<'_>) -> Option<&'static str> {
    match g.site_record {
        None => return Some(PROBE_UNKNOWN),
        Some(true) => return Some(ALREADY_A_SITE),
        Some(false) => {}
    }
    if !g.opening_matches {
        return Some(NOT_THIS_OPENING);
    }
    match g.ct_of_a_site {
        None => return Some(PROBE_UNKNOWN),
        Some(true) => return Some(CT_OF_A_SITE),
        Some(false) => {}
    }
    match g.ct_hostname {
        None => Some(PROBE_UNKNOWN),
        Some(Some(h)) if h != code => Some(CT_NOT_THIS_OPENING),
        Some(_) => None,
    }
}

/// What to do now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Next {
    /// Do this step (and the ones after it that are not done).
    Run(AbandonStep),
    /// Everything is gone.
    Finished,
    /// Do nothing and say this word; a person looks.
    Stop(&'static str),
}

/// The next thing to do, from one probe per step in [`STEPS`] order. `Done` is "nothing
/// of it is left". Steps may be found done in any order (an opening that never got to its
/// DNS has nothing there); any probe that could not be read stops everything, so nothing
/// is removed while something else is in doubt.
pub fn next(code: &str, guard: &Guard<'_>, probes: &[Probe]) -> Next {
    if let Some(word) = refusal(code, guard) {
        return Next::Stop(word);
    }
    if probes.len() != STEPS.len() {
        return Next::Stop(PROBES_INCOMPLETE);
    }
    if probes.contains(&Probe::Unknown) {
        return Next::Stop(PROBE_UNKNOWN);
    }
    match STEPS
        .iter()
        .zip(probes)
        .find(|(_, p)| **p == Probe::NotDone)
    {
        Some((step, _)) => Next::Run(*step),
        None => Next::Finished,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CODE: &str = "s-ab12cd";

    fn ok() -> Guard<'static> {
        Guard {
            site_record: Some(false),
            opening_matches: true,
            ct_hostname: Some(Some(CODE)),
            ct_of_a_site: Some(false),
        }
    }

    fn all(p: Probe) -> [Probe; 5] {
        [p; 5]
    }

    #[test]
    fn the_steps_are_in_the_order_they_are_done() {
        let mut sorted = STEPS;
        sorted.sort();
        assert_eq!(sorted, STEPS);
        assert_eq!(
            STEPS.map(AbandonStep::as_str),
            ["container", "firewall", "dns", "secrets", "record"]
        );
    }

    #[test]
    fn a_site_is_never_abandoned() {
        let mut g = ok();
        g.site_record = Some(true);
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(ALREADY_A_SITE)
        );
        g.site_record = None;
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(PROBE_UNKNOWN)
        );
    }

    #[test]
    fn only_the_opening_of_this_code_and_domain() {
        let mut g = ok();
        g.opening_matches = false;
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(NOT_THIS_OPENING)
        );
    }

    #[test]
    fn only_a_container_named_for_the_code_and_no_sites() {
        let mut g = ok();
        g.ct_hostname = Some(Some("s-other1"));
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(CT_NOT_THIS_OPENING)
        );
        g.ct_hostname = Some(Some(""));
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(CT_NOT_THIS_OPENING)
        );
        g.ct_hostname = None;
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(PROBE_UNKNOWN)
        );
        // no container left: the rest may still go
        g.ct_hostname = Some(None);
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Run(AbandonStep::Container)
        );
        let mut g = ok();
        g.ct_of_a_site = Some(true);
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(CT_OF_A_SITE)
        );
        g.ct_of_a_site = None;
        assert_eq!(
            next(CODE, &g, &all(Probe::NotDone)),
            Next::Stop(PROBE_UNKNOWN)
        );
    }

    #[test]
    fn anything_in_doubt_stops_everything() {
        // the guard itself: a site record, a site's container or the container not told
        let mut g = ok();
        g.site_record = None;
        assert_eq!(refusal(CODE, &g), Some(PROBE_UNKNOWN));
        let mut g = ok();
        g.ct_of_a_site = None;
        assert_eq!(refusal(CODE, &g), Some(PROBE_UNKNOWN));
        let mut g = ok();
        g.ct_hostname = None;
        assert_eq!(refusal(CODE, &g), Some(PROBE_UNKNOWN));
        for i in 0..STEPS.len() {
            let mut p = all(Probe::NotDone);
            p[i] = Probe::Unknown;
            assert_eq!(next(CODE, &ok(), &p), Next::Stop(PROBE_UNKNOWN), "{i}");
        }
        assert_eq!(
            next(CODE, &ok(), &[Probe::NotDone; 4]),
            Next::Stop(PROBES_INCOMPLETE)
        );
    }

    #[test]
    fn the_first_thing_left_is_next_and_nothing_left_is_finished() {
        let mut p = all(Probe::Done);
        p[2] = Probe::NotDone;
        p[4] = Probe::NotDone;
        assert_eq!(next(CODE, &ok(), &p), Next::Run(AbandonStep::Dns));
        assert_eq!(next(CODE, &ok(), &all(Probe::Done)), Next::Finished);
        assert_eq!(
            next(CODE, &ok(), &all(Probe::NotDone)),
            Next::Run(AbandonStep::Container)
        );
    }
}
