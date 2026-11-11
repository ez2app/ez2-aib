// SPDX-License-Identifier: Apache-2.0
//! Is the visitor-statistics loader of a new site really working? Judged only on what the
//! statistics server and the site answer from outside, never on what the site stores: a
//! loader setting written into the site is not evidence (the same trap as a plugin that
//! writes "valid" for itself, see [`crate::sitedefaults`]). No I/O: the host shell asks
//! and hands the answers here.
//!
//! It runs after the site is ready ([`crate::sitecreate`]): the customer has no view of
//! it, so a failure is ours to hear about and never changes what the customer is told.
//! The host's configuration may switch it off; then there is nothing to check.
//!
//! Three questions, asked from outside the host (4542e B1 design, 33e28 11-11):
//! 1. the site's home page, redirects followed, read with an ordinary browser's agent:
//!    it ends on a name of this site and holds the loader of this site's key exactly once,
//!    and no loader of another key (the template's, carried over by the clone);
//! 2. the statistics server serves that loader, and it carries this site's key;
//! 3. the statistics server takes a probe event from this site's origin: it answers 400
//!    with reason `event` (the name is refused, so nothing is stored, but the origin was
//!    accepted and the route is there).
//!
//! Every answer is [`Probe::Done`] (all three hold), [`Probe::NotDone`] (something the
//! loader step itself puts right when it runs again: the loader is missing, written
//! twice or another key's, the server does not know the key, the server does not take the origin yet) or
//! [`Probe::Unknown`] (could not ask, or an answer only a person can act on). A failed
//! question is never read as "not done".
//!
//! What the caller must do for these answers to mean what they say (c477a F2 L1, N1):
//! - read the home page with a query string that passes the page cache (a fresh random
//!   value each time): the template caches pages, and a stale copy would keep answering
//!   "loader missing" after the loader was written;
//! - make the loader step **set** the site's loader (replace any loader there), never add
//!   one: run again on a stale answer it must not leave two;
//! - send the probe event the way a consenting visitor would: an ordinary browser's agent,
//!   no `Sec-GPC`, consent not `none`. The server answers 204 for any of four causes
//!   (statistics off for the account, consent none, GPC, a robot's agent), so a 204 is
//!   only "the server said nothing", never a verdict.

use crate::sitecreate::Probe;

/// One question's answer: what came back, or that nothing usable came back (no
/// connection, a timeout, an unreadable reply).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Asked<T> {
    Got(T),
    Failed,
}

/// The home page after redirects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Page {
    /// HTTP status of the page it landed on.
    pub status: u16,
    /// Whether it landed on a name the site was opened with.
    pub on_own_name: bool,
    /// How many times the loader of this site's key appears in the page.
    pub copies: usize,
    /// How many loaders of **another** key appear in the page (ebb36 L1: the template's
    /// loader carried over by the clone sends this site's visitors to the template).
    pub other_keys: usize,
}

/// The loader as the statistics server serves it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Script {
    pub status: u16,
    /// Whether the body carries this site's key.
    pub has_key: bool,
}

/// The statistics server's answer to the probe event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Intake {
    pub status: u16,
    /// Whether a 400 says the reason is the event name (and nothing else).
    pub reason_event: bool,
    /// Whether a 403 is the statistics server's own refusal of the origin
    /// (`{"reason":"origin"}`), not a firewall in front of it (c477a F2 N2).
    pub reason_origin: bool,
}

/// The verdict and the fixed word that says why (for the journal and our alert; never
/// shown to the customer).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LoaderProbe {
    pub probe: Probe,
    pub word: &'static str,
}

const fn done(word: &'static str) -> LoaderProbe {
    LoaderProbe {
        probe: Probe::Done,
        word,
    }
}
const fn not_done(word: &'static str) -> LoaderProbe {
    LoaderProbe {
        probe: Probe::NotDone,
        word,
    }
}
const fn unknown(word: &'static str) -> LoaderProbe {
    LoaderProbe {
        probe: Probe::Unknown,
        word,
    }
}

fn page(p: Asked<Page>) -> Option<LoaderProbe> {
    let Asked::Got(p) = p else {
        return Some(unknown("page_unreadable"));
    };
    if !p.on_own_name {
        // it ends somewhere else: a redirect a person set up, or not this site at all
        return Some(unknown("page_elsewhere"));
    }
    if p.status != 200 {
        return Some(unknown("page_status"));
    }
    if p.other_keys > 0 {
        // the loader step writes this site's own loader in its place
        return Some(not_done("loader_foreign"));
    }
    match p.copies {
        1 => None,
        0 => Some(not_done("loader_missing")),
        _ => Some(not_done("loader_twice")),
    }
}

fn script(s: Asked<Script>) -> Option<LoaderProbe> {
    let Asked::Got(s) = s else {
        return Some(unknown("script_unreachable"));
    };
    match (s.status, s.has_key) {
        (200, true) => None,
        // a page that is not the loader (a proxy's error page, another service)
        (200, false) => Some(unknown("script_not_loader")),
        // the server does not know the key: asking it for the site again puts that right
        (404, _) => Some(not_done("key_unknown")),
        _ => Some(unknown("script_status")),
    }
}

fn intake(i: Asked<Intake>) -> Option<LoaderProbe> {
    let Asked::Got(i) = i else {
        return Some(unknown("intake_unreachable"));
    };
    match (i.status, i.reason_event) {
        (400, true) => None,
        // the origin is not registered for the key: asking for the site again registers it
        (403, _) if i.reason_origin => Some(not_done("origin_refused")),
        // a 403 without the server's own reason: something in front refused it
        (403, _) => Some(unknown("intake_forbidden")),
        // one of four causes (see the module notes): a person looks
        (204, _) => Some(unknown("intake_204")),
        (404, _) => Some(unknown("intake_route_missing")),
        (429, _) => Some(unknown("rate_limited")),
        _ => Some(unknown("intake_odd")),
    }
}

/// The verdict for one site. `enabled` is the host's configuration switch; off means
/// nothing is to be checked. The first question that does not hold decides.
pub fn fp_loader_probe(
    enabled: bool,
    p: Asked<Page>,
    s: Asked<Script>,
    i: Asked<Intake>,
) -> LoaderProbe {
    if !enabled {
        return done("off");
    }
    page(p)
        .or_else(|| script(s))
        .or_else(|| intake(i))
        .unwrap_or(done("ok"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const PAGE: Page = Page {
        status: 200,
        on_own_name: true,
        copies: 1,
        other_keys: 0,
    };
    const SCRIPT: Script = Script {
        status: 200,
        has_key: true,
    };
    const INTAKE: Intake = Intake {
        status: 400,
        reason_event: true,
        reason_origin: false,
    };

    fn ok_but(p: Asked<Page>, s: Asked<Script>, i: Asked<Intake>) -> LoaderProbe {
        fp_loader_probe(true, p, s, i)
    }

    // knife (a): all three must hold
    #[test]
    fn all_three_answers_make_it_done() {
        let r = ok_but(Asked::Got(PAGE), Asked::Got(SCRIPT), Asked::Got(INTAKE));
        assert_eq!(r, done("ok"));
        // switched off in the host's configuration: nothing to check, whatever came back
        let r = fp_loader_probe(false, Asked::Failed, Asked::Failed, Asked::Failed);
        assert_eq!(r, done("off"));
    }

    // knife (b): the loader is in the page exactly once
    #[test]
    fn the_loader_is_in_the_page_exactly_once() {
        let with = |copies| {
            ok_but(
                Asked::Got(Page { copies, ..PAGE }),
                Asked::Got(SCRIPT),
                Asked::Got(INTAKE),
            )
        };
        assert_eq!(with(0), not_done("loader_missing"));
        assert_eq!(with(2), not_done("loader_twice"));
        assert_eq!(with(3), not_done("loader_twice"));
        // landing on another name or a non-200 page: a person looks
        let elsewhere = Page {
            on_own_name: false,
            ..PAGE
        };
        assert_eq!(
            ok_but(
                Asked::Got(elsewhere),
                Asked::Got(SCRIPT),
                Asked::Got(INTAKE)
            ),
            unknown("page_elsewhere")
        );
        let status = Page {
            status: 503,
            ..PAGE
        };
        assert_eq!(
            ok_but(Asked::Got(status), Asked::Got(SCRIPT), Asked::Got(INTAKE)),
            unknown("page_status")
        );
    }

    // knife (c): a question that could not be asked is never "not done" nor "done"
    #[test]
    fn a_question_not_answered_is_unknown() {
        assert_eq!(
            ok_but(Asked::Failed, Asked::Got(SCRIPT), Asked::Got(INTAKE)),
            unknown("page_unreadable")
        );
        assert_eq!(
            ok_but(Asked::Got(PAGE), Asked::Failed, Asked::Got(INTAKE)),
            unknown("script_unreachable")
        );
        assert_eq!(
            ok_but(Asked::Got(PAGE), Asked::Got(SCRIPT), Asked::Failed),
            unknown("intake_unreachable")
        );
    }

    // knife (d): the served loader must carry this site's key
    #[test]
    fn the_served_loader_carries_the_key() {
        let s = |status, has_key| {
            ok_but(
                Asked::Got(PAGE),
                Asked::Got(Script { status, has_key }),
                Asked::Got(INTAKE),
            )
        };
        assert_eq!(s(200, false), unknown("script_not_loader"));
        assert_eq!(s(404, false), not_done("key_unknown"));
        assert_eq!(s(502, true), unknown("script_status"));
    }

    // knife (e): only "refused for the name" proves the origin was taken; nothing stored
    #[test]
    fn the_probe_event_proves_the_origin_and_stores_nothing() {
        let i = |status, reason_event| {
            ok_but(
                Asked::Got(PAGE),
                Asked::Got(SCRIPT),
                Asked::Got(Intake {
                    status,
                    reason_event,
                    reason_origin: status == 403,
                }),
            )
        };
        // a 400 for another reason is not the proof
        assert_eq!(i(400, false), unknown("intake_odd"));
        assert_eq!(i(403, false), not_done("origin_refused"));
        assert_eq!(i(204, false), unknown("intake_204"));
        assert_eq!(i(404, false), unknown("intake_route_missing"));
        assert_eq!(i(429, false), unknown("rate_limited"));
        // a 2xx that stored the probe would be data written by a check: not accepted
        assert_eq!(i(200, false), unknown("intake_odd"));
    }

    // knife (h): only the statistics server's own refusal is "not done"
    #[test]
    fn a_403_from_something_in_front_is_unknown() {
        let r = ok_but(
            Asked::Got(PAGE),
            Asked::Got(SCRIPT),
            Asked::Got(Intake {
                status: 403,
                reason_event: false,
                reason_origin: false,
            }),
        );
        assert_eq!(r, unknown("intake_forbidden"));
    }

    // knife (g): another key's loader in the page is not this site's statistics
    #[test]
    fn a_loader_of_another_key_is_not_done() {
        for copies in [0, 1, 2] {
            let r = ok_but(
                Asked::Got(Page {
                    copies,
                    other_keys: 1,
                    ..PAGE
                }),
                Asked::Got(SCRIPT),
                Asked::Got(INTAKE),
            );
            assert_eq!(r, not_done("loader_foreign"), "{copies}");
        }
    }

    #[test]
    fn the_page_is_asked_first() {
        // several things wrong at once: the page decides, so the word points at the cause
        let r = ok_but(
            Asked::Got(Page { copies: 0, ..PAGE }),
            Asked::Got(Script {
                status: 404,
                has_key: false,
            }),
            Asked::Failed,
        );
        assert_eq!(r, not_done("loader_missing"));
    }
}
