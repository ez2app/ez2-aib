//! Why a unit did not run (SPEC r2 D, 1ced7 N1–N3). One list for the engine and the receipt
//! reader; there is no free text, so the weekly numbers add up.

/// Where the result is going to be used. A filter the user chose is fine for the user's own
/// run, but a receipt, a push or a review needs every unit to point at a real run (r7 §2.5).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Use {
    /// `aib test` on the user's machine: its exit code.
    Local,
    /// A receipt, a push, a review: what a reviewer reads.
    Evidence,
}

/// The skip reason. Only the first four can be harmless; see [`SkipWhy::harmless`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SkipWhy {
    /// The step's condition leaves this OS out (it was `not_macos` / `not_windows`).
    NotThisOs { os: String },
    /// The step runs on another event only (for example on a push).
    NotThisEvent { event: String },
    /// The user's `--only` / filter did not pick it.
    UserFilter,
    /// The knife list says it cannot bite on this machine. `bites_on` names where it does;
    /// it is harmless only when a bite there can be shown (1ced7 N3).
    NamedCannotBiteHere { why: String, bites_on: Vec<String> },
    /// Not run because something before it failed (receipt-run's KNIVES-SKIPPED).
    UpstreamFailed,
    /// The time budget ran out before it.
    TimeoutBudget,
    /// The engine itself skipped it.
    EngineSkip,
    /// A reason we do not know.
    Unknown,
}

impl SkipWhy {
    /// Whether this skip leaves the run green. `bite_shown` answers, for
    /// [`SkipWhy::NamedCannotBiteHere`], whether a bite on one of `bites_on` can be shown
    /// (same key, or within 7 days); it is not asked for the other reasons.
    pub fn harmless(&self, used: Use, bite_shown: impl FnOnce(&[String]) -> bool) -> bool {
        match self {
            // the place must be named: a bare `why=not_this_os` says nothing (043dd u1 L1)
            SkipWhy::NotThisOs { os } => !os.trim().is_empty(),
            SkipWhy::NotThisEvent { event } => !event.trim().is_empty(),
            SkipWhy::UserFilter => used == Use::Local,
            SkipWhy::NamedCannotBiteHere { why, bites_on } => {
                !why.trim().is_empty() && !bites_on.is_empty() && bite_shown(bites_on)
            }
            SkipWhy::UpstreamFailed
            | SkipWhy::TimeoutBudget
            | SkipWhy::EngineSkip
            | SkipWhy::Unknown => false,
        }
    }

    /// The word written in the ledger and in a receipt line (`why=<word>`).
    pub fn word(&self) -> &'static str {
        match self {
            SkipWhy::NotThisOs { .. } => "not_this_os",
            SkipWhy::NotThisEvent { .. } => "not_this_event",
            SkipWhy::UserFilter => "user_filter",
            SkipWhy::NamedCannotBiteHere { .. } => "named_cannot_bite_here",
            SkipWhy::UpstreamFailed => "upstream_failed",
            SkipWhy::TimeoutBudget => "timeout_budget",
            SkipWhy::EngineSkip => "engine_skip",
            SkipWhy::Unknown => "unknown",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn never(_: &[String]) -> bool {
        panic!("only asked for named_cannot_bite_here")
    }

    #[test]
    fn an_upstream_failure_is_never_harmless() {
        for u in [Use::Local, Use::Evidence] {
            assert!(!SkipWhy::UpstreamFailed.harmless(u, never));
            assert!(!SkipWhy::TimeoutBudget.harmless(u, never));
            assert!(!SkipWhy::EngineSkip.harmless(u, never));
            assert!(!SkipWhy::Unknown.harmless(u, never));
        }
    }

    #[test]
    fn a_user_filter_is_harmless_locally_but_not_as_evidence() {
        assert!(SkipWhy::UserFilter.harmless(Use::Local, never));
        assert!(!SkipWhy::UserFilter.harmless(Use::Evidence, never));
    }

    #[test]
    fn cannot_bite_here_needs_a_reason_a_place_and_a_bite_there() {
        let s = |why: &str, on: &[&str]| SkipWhy::NamedCannotBiteHere {
            why: why.into(),
            bites_on: on.iter().map(|x| x.to_string()).collect(),
        };
        assert!(s("sysinfo reads it right on macOS", &["linux"])
            .harmless(Use::Evidence, |on| on == ["linux"]));
        assert!(
            !s("sysinfo reads it right on macOS", &["linux"]).harmless(Use::Evidence, |_| false)
        );
        assert!(!s("", &["linux"]).harmless(Use::Evidence, |_| true));
        assert!(!s("no place given", &[]).harmless(Use::Evidence, |_| true));
    }

    #[test]
    fn the_os_and_event_skips_are_harmless_anywhere() {
        let os = SkipWhy::NotThisOs {
            os: "windows".into(),
        };
        let ev = SkipWhy::NotThisEvent {
            event: "push".into(),
        };
        for u in [Use::Local, Use::Evidence] {
            assert!(os.harmless(u, never));
            assert!(ev.harmless(u, never));
        }
        assert_eq!(os.word(), "not_this_os");
        // no os / event named ⇒ not harmless
        let bare_os = SkipWhy::NotThisOs { os: " ".into() };
        let bare_ev = SkipWhy::NotThisEvent {
            event: String::new(),
        };
        assert!(!bare_os.harmless(Use::Local, never));
        assert!(!bare_ev.harmless(Use::Evidence, never));
        assert_eq!(SkipWhy::UpstreamFailed.word(), "upstream_failed");
    }
}
