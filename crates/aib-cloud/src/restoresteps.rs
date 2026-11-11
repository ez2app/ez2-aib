// SPDX-License-Identifier: Apache-2.0
//! Restoring a customer's own WordPress backup into one of their sites (E3, B3 slice 2):
//! the order of the steps, what a failed step leaves behind, where an interrupted or
//! failed restore may go on from, and when a new restore is refused. No I/O: the host
//! shell keeps one step record per site and hands what it read here.
//!
//! * The steps only complete in order, and the step record says which are done. It is
//!   written whole after every step, so a record whose done steps are not the first ones
//!   in order was not written by this code: it stops for a person.
//! * Until the import the site is as it was. A failure **before** the import changes
//!   nothing on the site: what was downloaded is thrown away ([`AfterFailure::Discard`]).
//!   A failure **in** the import is taken back from the restore point made just before
//!   it ([`AfterFailure::RollBack`]). From the tidying on, the site holds the customer's
//!   content and a failure keeps it as it is for a person, who may go on from the failed
//!   step ([`AfterFailure::Keep`]): taking the site back there would throw away an import
//!   that worked because a later step did not (EU01's restore line, 4.6 §15.4 point 2).
//! * An interruption (the process died, no failure was written) is the same line: from
//!   the tidying on the step is done again, because those steps are written to be safe to
//!   do twice; before it nobody knows how far the download or the import got, so it stops
//!   for a person, who looks and closes the operation.
//! * One restore per site at a time, and one per domain: a second ask for the same site
//!   or the same domain while the first is not finished is refused, not queued. One per
//!   host: a restore interrupted before its tidying (a download or a restore point still
//!   on the host's disk, or a site half imported) holds new restores on every site up
//!   until a person has dealt with it. While a restore runs it holds the host lock of the
//!   operation journal, so two never run at once.
//! * "Ready to switch" is the last step: the site is restored and checked under its own
//!   address and nothing more is done. Pointing the customer's real domain at it is a
//!   separate, person-approved step (the fixed stop of 4.6 §15.4 point 6).

use crate::sitecreate::Probe;

/// One step of a restore, in the order they are done.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum RestoreStep {
    /// The backup file is fetched onto the host.
    Download,
    /// The file is checked: its size, its first bytes and its SHA-256 are recorded.
    Verify,
    /// The target site is archived as it is, to come back to if the import fails.
    RestorePoint,
    /// The backup is imported into the site.
    Import,
    /// What the import brought is tidied: addresses, accounts, plugins, settings.
    Tidy,
    /// The restored site is checked against what the backup says it holds.
    Accept,
    /// The site is restored and checked; nothing more is done before a person switches.
    ReadyToSwitch,
}

/// Every step, first to last.
pub const STEPS: [RestoreStep; 7] = [
    RestoreStep::Download,
    RestoreStep::Verify,
    RestoreStep::RestorePoint,
    RestoreStep::Import,
    RestoreStep::Tidy,
    RestoreStep::Accept,
    RestoreStep::ReadyToSwitch,
];

impl RestoreStep {
    /// The fixed word for the step record and the journal.
    pub fn as_str(self) -> &'static str {
        match self {
            RestoreStep::Download => "download",
            RestoreStep::Verify => "verify",
            RestoreStep::RestorePoint => "restore_point",
            RestoreStep::Import => "import",
            RestoreStep::Tidy => "tidy",
            RestoreStep::Accept => "accept",
            RestoreStep::ReadyToSwitch => "ready_to_switch",
        }
    }

    /// The step a fixed word names.
    pub fn parse(word: &str) -> Option<Self> {
        STEPS.iter().copied().find(|s| s.as_str() == word)
    }
}

/// What a failed step leaves behind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AfterFailure {
    /// The site was not touched: throw away what was downloaded. The customer may ask again.
    Discard,
    /// The import may have half happened: bring the site back from the restore point.
    RollBack,
    /// The site holds the customer's content: keep it as it is, a person goes on.
    Keep,
}

/// See [`AfterFailure`].
pub fn after_failure(step: RestoreStep) -> AfterFailure {
    if step < RestoreStep::Import {
        AfterFailure::Discard
    } else if step == RestoreStep::Import {
        AfterFailure::RollBack
    } else {
        AfterFailure::Keep
    }
}

/// Whether a step may be done a second time after an interruption: the steps from the
/// tidying on are written to be safe twice; before it nobody knows how far it got.
pub fn safe_twice(step: RestoreStep) -> bool {
    step >= RestoreStep::Tidy
}

/// The step after the steps a record says are done. They must be the first ones, in
/// order; anything else was not written by this code.
pub fn next_step(done: &[RestoreStep]) -> Result<Option<RestoreStep>, &'static str> {
    if done.len() > STEPS.len() || done.iter().zip(STEPS.iter()).any(|(d, s)| d != s) {
        return Err("record_out_of_order");
    }
    Ok(STEPS.get(done.len()).copied())
}

/// Where a site's last restore stands, as its step record says.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Standing {
    /// Every step is done.
    Finished,
    /// It failed before the import and what was downloaded is thrown away.
    Discarded,
    /// The import failed and the site is back as it was.
    RolledBack,
    /// It failed at this step, from the tidying on; the site is kept for a person.
    Kept(RestoreStep),
    /// It stopped at this step without a failure being written, or its failure could not
    /// be cleaned up after.
    Interrupted(RestoreStep),
}

/// A site's standing from its step record: the steps done, the step that failed (if one
/// did) and whether what [`after_failure`] asked for was done.
pub fn standing(
    done: &[RestoreStep],
    failed: Option<RestoreStep>,
    cleaned_up: bool,
) -> Result<Standing, &'static str> {
    let next = next_step(done)?;
    match (failed, next) {
        (None, None) => Ok(Standing::Finished),
        (None, Some(step)) => Ok(Standing::Interrupted(step)),
        (Some(_), None) => Err("record_out_of_order"),
        (Some(f), Some(step)) if f != step => Err("record_out_of_order"),
        (Some(f), Some(_)) => Ok(match after_failure(f) {
            AfterFailure::Keep => Standing::Kept(f),
            _ if !cleaned_up => Standing::Interrupted(f),
            AfterFailure::Discard => Standing::Discarded,
            AfterFailure::RollBack => Standing::RolledBack,
        }),
    }
}

/// Whether a restore in this standing is still under way (finished or cleaned up is not).
pub fn unfinished(s: Standing) -> bool {
    matches!(s, Standing::Kept(_) | Standing::Interrupted(_))
}

/// Whether a restore in this standing holds new restores on the whole host: interrupted
/// before the tidying, a download, a restore point or a half import may still be there.
pub fn holds_the_host(s: Standing) -> bool {
    matches!(s, Standing::Interrupted(step) if !safe_twice(step))
}

/// What to do now with a site's restore.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Next {
    /// Start a new restore at the first step.
    Start,
    /// Go on from this step.
    GoOn(RestoreStep),
    /// Do nothing and say this word; a person looks.
    Stop(&'static str),
}

/// Whether the last restore of a site may be gone on with, or a new one started.
pub fn next(s: Option<Standing>) -> Next {
    match s {
        None | Some(Standing::Finished | Standing::Discarded | Standing::RolledBack) => Next::Start,
        Some(Standing::Kept(step)) => Next::GoOn(step),
        Some(Standing::Interrupted(step)) if safe_twice(step) => Next::GoOn(step),
        Some(Standing::Interrupted(_)) => Next::Stop("interrupted_before_tidy"),
    }
}

/// Whether two domains are the same site's: case, a final dot and a leading `www.` do
/// not make them different.
pub fn same_domain(a: &str, b: &str) -> bool {
    fn bare(d: &str) -> &str {
        let d = d.strip_suffix('.').unwrap_or(d);
        match (d.get(..4), d.get(4..)) {
            (Some(w), Some(rest)) if w.eq_ignore_ascii_case("www.") => rest,
            _ => d,
        }
    }
    bare(a).eq_ignore_ascii_case(bare(b))
}

/// Another site's last restore, as the host read it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Other<'a> {
    pub site: &'a str,
    pub domain: &'a str,
    pub standing: Standing,
}

/// Whether a new restore of `site` at `domain` may start, given the last restore of every
/// other site on the host. Refused with a fixed word; nothing is queued.
pub fn admit(site: &str, domain: &str, others: &[Other<'_>]) -> Result<(), &'static str> {
    for o in others {
        if !unfinished(o.standing) {
            continue;
        }
        if o.site == site {
            return Err("restore_unfinished_on_site");
        }
        if same_domain(o.domain, domain) {
            return Err("restore_in_progress_for_domain");
        }
        if holds_the_host(o.standing) {
            return Err("restore_unfinished_on_host");
        }
    }
    Ok(())
}

/// What the host found about the target site before a restore starts. The record says
/// what this code did; these say whether the host is still that way.
pub fn ready_to_go_on(probe_site_up: Probe) -> Result<(), &'static str> {
    match probe_site_up {
        Probe::Done => Ok(()),
        Probe::NotDone => Err("target_site_down"),
        Probe::Unknown => Err("target_site_unknown"),
    }
}

/// A backup's id at the share it comes from: the characters a shared file's id has, and
/// a sane length. It goes into a plan, a record and later a command line.
pub fn valid_source_id(id: &str) -> bool {
    (10..=200).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::vec::Vec;
    use RestoreStep::*;

    #[test]
    fn the_steps_are_in_the_order_they_are_done() {
        let words: Vec<&str> = STEPS.iter().map(|s| s.as_str()).collect();
        assert_eq!(
            words,
            [
                "download",
                "verify",
                "restore_point",
                "import",
                "tidy",
                "accept",
                "ready_to_switch"
            ]
        );
        for s in STEPS {
            assert_eq!(RestoreStep::parse(s.as_str()), Some(s));
        }
        assert_eq!(RestoreStep::parse("health"), None);
    }

    #[test]
    fn a_failure_before_the_import_leaves_the_site_as_it_was() {
        for s in [Download, Verify, RestorePoint] {
            assert_eq!(after_failure(s), AfterFailure::Discard, "{s:?}");
        }
    }

    #[test]
    fn a_failed_import_is_rolled_back() {
        assert_eq!(after_failure(Import), AfterFailure::RollBack);
    }

    #[test]
    fn from_the_tidying_on_the_site_is_kept() {
        for s in [Tidy, Accept, ReadyToSwitch] {
            assert_eq!(after_failure(s), AfterFailure::Keep, "{s:?}");
        }
    }

    #[test]
    fn a_record_out_of_order_stops() {
        assert_eq!(next_step(&[]), Ok(Some(Download)));
        assert_eq!(next_step(&[Download, Verify]), Ok(Some(RestorePoint)));
        assert_eq!(next_step(&STEPS), Ok(None));
        assert_eq!(next_step(&[Verify]), Err("record_out_of_order"));
        assert_eq!(next_step(&[Download, Import]), Err("record_out_of_order"));
        let mut too_many = STEPS.to_vec();
        too_many.push(ReadyToSwitch);
        assert_eq!(next_step(&too_many), Err("record_out_of_order"));
    }

    #[test]
    fn a_standing_is_read_from_the_record() {
        assert_eq!(standing(&STEPS, None, false), Ok(Standing::Finished));
        assert_eq!(
            standing(&[Download], None, false),
            Ok(Standing::Interrupted(Verify))
        );
        assert_eq!(
            standing(&[Download], Some(Verify), true),
            Ok(Standing::Discarded)
        );
        assert_eq!(
            standing(&STEPS[..3], Some(Import), true),
            Ok(Standing::RolledBack)
        );
        assert_eq!(
            standing(&STEPS[..4], Some(Tidy), false),
            Ok(Standing::Kept(Tidy))
        );
        // the failed step must be the one after the steps done
        assert_eq!(
            standing(&[Download], Some(Import), true),
            Err("record_out_of_order")
        );
        assert_eq!(
            standing(&STEPS, Some(Accept), true),
            Err("record_out_of_order")
        );
    }

    #[test]
    fn a_failure_not_cleaned_up_after_is_an_interruption() {
        assert_eq!(
            standing(&[Download], Some(Verify), false),
            Ok(Standing::Interrupted(Verify))
        );
        assert_eq!(
            standing(&STEPS[..3], Some(Import), false),
            Ok(Standing::Interrupted(Import))
        );
        assert_eq!(
            next(Some(Standing::Interrupted(Import))),
            Next::Stop("interrupted_before_tidy")
        );
    }

    #[test]
    fn a_kept_restore_goes_on_from_the_failed_step() {
        assert_eq!(next(Some(Standing::Kept(Accept))), Next::GoOn(Accept));
        assert_eq!(next(Some(Standing::Interrupted(Tidy))), Next::GoOn(Tidy));
        assert_eq!(
            next(Some(Standing::Interrupted(ReadyToSwitch))),
            Next::GoOn(ReadyToSwitch)
        );
    }

    #[test]
    fn an_interruption_before_the_tidying_is_not_gone_on_with() {
        for s in [Download, Verify, RestorePoint, Import] {
            assert_eq!(
                next(Some(Standing::Interrupted(s))),
                Next::Stop("interrupted_before_tidy"),
                "{s:?}"
            );
        }
    }

    #[test]
    fn a_finished_or_cleaned_up_restore_starts_anew() {
        for s in [
            None,
            Some(Standing::Finished),
            Some(Standing::Discarded),
            Some(Standing::RolledBack),
        ] {
            assert_eq!(next(s), Next::Start, "{s:?}");
        }
    }

    fn other(site: &'static str, domain: &'static str, standing: Standing) -> Other<'static> {
        Other {
            site,
            domain,
            standing,
        }
    }

    #[test]
    fn a_site_with_an_unfinished_restore_takes_no_other() {
        let o = [other("s-aaa111", "a.example", Standing::Kept(Accept))];
        assert_eq!(
            admit("s-aaa111", "a.example", &o),
            Err("restore_unfinished_on_site")
        );
        let done = [other("s-aaa111", "a.example", Standing::Finished)];
        assert_eq!(admit("s-aaa111", "a.example", &done), Ok(()));
    }

    #[test]
    fn a_domain_with_a_restore_under_way_takes_no_other() {
        let o = [other("s-bbb222", "Shop.Example.", Standing::Kept(Tidy))];
        assert_eq!(
            admit("s-aaa111", "www.shop.example", &o),
            Err("restore_in_progress_for_domain")
        );
        assert_eq!(admit("s-aaa111", "other.example", &o), Ok(()));
    }

    #[test]
    fn a_restore_interrupted_before_its_tidying_holds_the_host() {
        let o = [other(
            "s-bbb222",
            "b.example",
            Standing::Interrupted(Import),
        )];
        assert_eq!(
            admit("s-aaa111", "a.example", &o),
            Err("restore_unfinished_on_host")
        );
        // kept after the import: that site waits for a person, the host does not
        let kept = [other("s-bbb222", "b.example", Standing::Kept(Accept))];
        assert_eq!(admit("s-aaa111", "a.example", &kept), Ok(()));
        let later = [other("s-bbb222", "b.example", Standing::Interrupted(Tidy))];
        assert_eq!(admit("s-aaa111", "a.example", &later), Ok(()));
    }

    #[test]
    fn the_same_domain_is_the_same_whatever_its_spelling() {
        assert!(same_domain("shop.example", "SHOP.example."));
        assert!(same_domain("www.shop.example", "shop.example"));
        assert!(same_domain("WWW.shop.example", "shop.example"));
        assert!(!same_domain("wwwshop.example", "shop.example"));
        assert!(!same_domain("shop.example", "shop.example.tw"));
        assert!(!same_domain("www.", "x"));
    }

    #[test]
    fn a_target_that_is_not_up_does_not_take_a_restore() {
        assert_eq!(ready_to_go_on(Probe::Done), Ok(()));
        assert_eq!(ready_to_go_on(Probe::NotDone), Err("target_site_down"));
        assert_eq!(ready_to_go_on(Probe::Unknown), Err("target_site_unknown"));
    }

    #[test]
    fn a_source_id_is_only_what_a_shared_file_id_holds() {
        assert!(valid_source_id("1AbC_d-EfGhIjKlMnOp"));
        assert!(!valid_source_id("short"));
        assert!(!valid_source_id("1AbC/d-EfGhIjKlMnOp"));
        assert!(!valid_source_id("1AbC d-EfGhIjKlMnOp"));
        assert!(!valid_source_id(&"a".repeat(201)));
    }
}
