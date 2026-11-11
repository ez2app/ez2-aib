// SPDX-License-Identifier: Apache-2.0
//! The order in which a site is opened, what to do next when the work is found half
//! way, and what a failed step leaves behind. No I/O: the host shell probes each step
//! and hands the answers here.
//!
//! * Opening a site is one operation in the journal ([`crate::op`]). A journal that stops
//!   at `running` is never answered by running the steps again on its word: every step
//!   is probed first, and the first one that is known not to be done is the next one.
//! * A probe has three answers. "Could not tell" stops the work; it is never read as
//!   "not done" (the step would be done twice) nor as "done" (it would be skipped).
//! * The steps only ever complete in order. A later step found done while an earlier one
//!   is not means the host is not in a state this code made; it stops for a person.
//! * A failure before the site holds anything of the customer's is undone: the half-made
//!   container is removed and the customer may ask again. From the moment the site is
//!   installed, a failure keeps the site as it is for a person to finish. Destroying a
//!   whole site because a late step failed would be the larger harm.

/// One step of opening a site, in the order they are done.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum CreateStep {
    /// The container is cloned from the template.
    Clone,
    /// Its limits and its network are set.
    Limits,
    /// Its firewall file is written with the group the host's configuration names. It
    /// is always written and read back: a clone carries the template's file with it.
    Firewall,
    /// It is started.
    Start,
    /// The site software is installed: the template package, or nothing but the bare
    /// software when the customer asked for a blank site.
    Install,
    /// The site's own secrets are made anew, its address and title are set and the
    /// first administrators are made.
    Identity,
    /// Each paid plugin of the template is activated at its vendor.
    Licenses,
    /// The entry of the host is told about the site.
    Edge,
    /// The site answers from outside with its own mark.
    Health,
}

/// Every step, first to last.
pub const STEPS: [CreateStep; 9] = [
    CreateStep::Clone,
    CreateStep::Limits,
    CreateStep::Firewall,
    CreateStep::Start,
    CreateStep::Install,
    CreateStep::Identity,
    CreateStep::Licenses,
    CreateStep::Edge,
    CreateStep::Health,
];

impl CreateStep {
    /// The fixed word for the journal and the progress answer.
    pub fn as_str(self) -> &'static str {
        match self {
            CreateStep::Clone => "clone",
            CreateStep::Limits => "limits",
            CreateStep::Firewall => "firewall",
            CreateStep::Start => "start",
            CreateStep::Install => "install",
            CreateStep::Identity => "identity",
            CreateStep::Licenses => "licenses",
            CreateStep::Edge => "edge",
            CreateStep::Health => "health",
        }
    }

    /// Whether a blank site has this step. A blank site has no paid plugins.
    pub fn applies(self, blank: bool) -> bool {
        !(blank && self == CreateStep::Licenses)
    }
}

/// What the host found when it looked at one step.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Probe {
    /// The step's result is there and checks out.
    Done,
    /// The step's result is known not to be there.
    NotDone,
    /// The host could not tell.
    Unknown,
}

/// What to do now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Next {
    /// Do this step.
    Run(CreateStep),
    /// Every step is done.
    Finished,
    /// Do nothing and say this word; a person looks.
    Stop(&'static str, CreateStep),
}

/// The next thing to do, from one probe per step in [`STEPS`] order (`probes[i]` is for
/// `STEPS[i]`; a step a blank site does not have is passed over whatever its probe
/// says). Fewer or more probes than steps is a stop, not a guess.
pub fn next(probes: &[Probe], blank: bool) -> Next {
    if probes.len() != STEPS.len() {
        return Next::Stop("probes_incomplete", CreateStep::Clone);
    }
    let mut first_open: Option<CreateStep> = None;
    for (step, probe) in STEPS.iter().zip(probes) {
        if !step.applies(blank) {
            continue;
        }
        match (probe, first_open) {
            (Probe::Unknown, _) => return Next::Stop("probe_unknown", *step),
            (Probe::Done, Some(_)) => return Next::Stop("steps_out_of_order", *step),
            (Probe::NotDone, None) => first_open = Some(*step),
            _ => {}
        }
    }
    match first_open {
        Some(step) => Next::Run(step),
        None => Next::Finished,
    }
}

/// What a failed step leaves behind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AfterFailure {
    /// Nothing of the customer's is in the site yet: remove what was made, each removal
    /// checked, and the customer may ask again.
    Undo,
    /// The site is installed: keep it as it is and tell a person. Nothing is removed.
    Keep,
}

/// See [`AfterFailure`]. The line is the installed site: up to and including the
/// install it is undone, after it the site is kept.
pub fn after_failure(step: CreateStep) -> AfterFailure {
    if step <= CreateStep::Install {
        AfterFailure::Undo
    } else {
        AfterFailure::Keep
    }
}

/// What the customer's key is told about a site that is being opened.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Progress {
    /// Still at work; the step it is on.
    Opening(CreateStep),
    /// Open and answering.
    Ready,
    /// The site is there and one thing is left for a person; the fixed word says what.
    NeedsAttention(&'static str),
    /// It was not opened and nothing is left of it; the customer may ask again.
    NotOpened(&'static str),
}

/// The answer for a site whose step `step` failed with the fixed word `code`.
pub fn progress_after_failure(step: CreateStep, code: &'static str) -> Progress {
    match after_failure(step) {
        AfterFailure::Undo => Progress::NotOpened(code),
        AfterFailure::Keep => Progress::NeedsAttention(code),
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
    fn the_first_step_not_done_is_next() {
        for done in 0..STEPS.len() {
            assert_eq!(next(&probes(done), false), Next::Run(STEPS[done]), "{done}");
        }
        assert_eq!(next(&probes(STEPS.len()), false), Next::Finished);
    }

    #[test]
    fn a_probe_that_could_not_tell_stops_the_work() {
        for i in 0..STEPS.len() {
            // everything before it done, and also everything before it not done
            for done in [0, i] {
                let mut p = probes(done);
                p[i] = Probe::Unknown;
                let got = next(&p, false);
                if done == i {
                    assert_eq!(got, Next::Stop("probe_unknown", STEPS[i]), "{i}");
                } else {
                    assert!(matches!(got, Next::Stop(..)), "{i}: {got:?}");
                }
            }
        }
    }

    #[test]
    fn a_later_step_done_before_an_earlier_one_stops_the_work() {
        let mut p = probes(2);
        p[4] = Probe::Done;
        assert_eq!(next(&p, false), Next::Stop("steps_out_of_order", STEPS[4]));
    }

    #[test]
    fn the_wrong_number_of_probes_is_a_stop() {
        assert!(matches!(
            next(&probes(3)[..5], false),
            Next::Stop("probes_incomplete", _)
        ));
        assert!(matches!(
            next(&[], false),
            Next::Stop("probes_incomplete", _)
        ));
        let mut many = probes(STEPS.len());
        many.push(Probe::Done);
        assert!(matches!(
            next(&many, false),
            Next::Stop("probes_incomplete", _)
        ));
    }

    #[test]
    fn a_blank_site_passes_over_the_licenses() {
        let at = STEPS
            .iter()
            .position(|s| *s == CreateStep::Licenses)
            .unwrap();
        // not done, and even "could not tell": a blank site has no such step
        for probe in [Probe::NotDone, Probe::Unknown] {
            let mut p = probes(STEPS.len());
            p[at] = probe;
            assert_eq!(next(&p, true), Next::Finished);
            assert!(next(&p, false) != Next::Finished);
        }
        let p = probes(at);
        assert_eq!(next(&p, true), Next::Run(STEPS[at + 1]));
        assert_eq!(next(&p, false), Next::Run(CreateStep::Licenses));
    }

    #[test]
    fn a_failure_is_undone_only_until_the_site_is_installed() {
        for step in STEPS {
            let want = match step {
                CreateStep::Clone
                | CreateStep::Limits
                | CreateStep::Firewall
                | CreateStep::Start
                | CreateStep::Install => AfterFailure::Undo,
                CreateStep::Identity
                | CreateStep::Licenses
                | CreateStep::Edge
                | CreateStep::Health => AfterFailure::Keep,
            };
            assert_eq!(after_failure(step), want, "{step:?}");
        }
    }

    #[test]
    fn the_customer_is_told_whether_anything_is_left() {
        assert_eq!(
            progress_after_failure(CreateStep::Clone, "clone_failed"),
            Progress::NotOpened("clone_failed")
        );
        assert_eq!(
            progress_after_failure(CreateStep::Licenses, "license_quota"),
            Progress::NeedsAttention("license_quota")
        );
    }
}
