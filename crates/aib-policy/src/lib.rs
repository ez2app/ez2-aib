//! What an AI may never do on its own, as data that aib-reflect (R4 `gate`)
//! and aib-consult share. No dependencies, no I/O.
//!
//! Sources (a63ab 11-12 05:18 台北, DECISION-aib-reflect-a63ab-1112 §9):
//! - RULES-seats-aib2.md §14, the "不適用" line (Andy 11-10 00:17 台北):
//!   spending money, production writes, customer-facing copy, deleting another
//!   seat's tree, opening / closing seats, moving a repo / a new repo, signing,
//!   postponing an item Andy named.
//! - DECISION-aib-reflect-a63ab-1112.md §1 #7 #8 (11-12 05:2x 台北): self
//!   modification (aib-reflect, thresholds, the score table, any gate, CI
//!   workflows) and host writes / SIGN / consent / DNS / Cloudflare / customer
//!   data and restores, frozen-script exceptions, measurement protocols, merge
//!   queue order and cancelling someone else's CI, Touch ID, `run --queue`,
//!   ADR 0004, re-applying a persona, approving a goal, machine settings.
//!
//! Changing this list is itself a self-modification ([`Area::SelfPolicy`]).

/// One area an AI never enters on its own. `code()` is the contract written
/// into ledgers and verdicts; never reword it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Area {
    // RULES §14 "不適用"
    SpendMoney,
    ProductionWrite,
    CustomerCopy,
    DeleteOtherSeatTree,
    OpenCloseSeat,
    RepoMove,
    Signing,
    PostponeNamedItem,
    // DECISION §1 #8
    HostWrite,
    SignConsent,
    Dns,
    Cloudflare,
    CustomerData,
    FrozenScript,
    MeasurementProtocol,
    MergeQueueOrder,
    CancelOthersCi,
    TouchId,
    RunQueue,
    Adr0004,
    PersonaReapply,
    GoalApproval,
    MachineConfig,
    // DECISION §1 #7: self modification
    SelfReflect,
    SelfPolicy,
    SelfGate,
    SelfCi,
    SelfScoreTable,
    SelfThreshold,
}

impl Area {
    pub const ALL: [Area; 29] = [
        Area::SpendMoney,
        Area::ProductionWrite,
        Area::CustomerCopy,
        Area::DeleteOtherSeatTree,
        Area::OpenCloseSeat,
        Area::RepoMove,
        Area::Signing,
        Area::PostponeNamedItem,
        Area::HostWrite,
        Area::SignConsent,
        Area::Dns,
        Area::Cloudflare,
        Area::CustomerData,
        Area::FrozenScript,
        Area::MeasurementProtocol,
        Area::MergeQueueOrder,
        Area::CancelOthersCi,
        Area::TouchId,
        Area::RunQueue,
        Area::Adr0004,
        Area::PersonaReapply,
        Area::GoalApproval,
        Area::MachineConfig,
        Area::SelfReflect,
        Area::SelfPolicy,
        Area::SelfGate,
        Area::SelfCi,
        Area::SelfScoreTable,
        Area::SelfThreshold,
    ];

    /// `forbidden:<name>` or `self_modify:<name>`.
    pub fn code(self) -> &'static str {
        match self {
            Area::SpendMoney => "forbidden:spend_money",
            Area::ProductionWrite => "forbidden:production_write",
            Area::CustomerCopy => "forbidden:customer_copy",
            Area::DeleteOtherSeatTree => "forbidden:delete_other_seat_tree",
            Area::OpenCloseSeat => "forbidden:open_close_seat",
            Area::RepoMove => "forbidden:repo_move",
            Area::Signing => "forbidden:signing",
            Area::PostponeNamedItem => "forbidden:postpone_named_item",
            Area::HostWrite => "forbidden:host_write",
            Area::SignConsent => "forbidden:sign_consent",
            Area::Dns => "forbidden:dns",
            Area::Cloudflare => "forbidden:cloudflare",
            Area::CustomerData => "forbidden:customer_data",
            Area::FrozenScript => "forbidden:frozen_script",
            Area::MeasurementProtocol => "forbidden:measurement_protocol",
            Area::MergeQueueOrder => "forbidden:merge_queue_order",
            Area::CancelOthersCi => "forbidden:cancel_others_ci",
            Area::TouchId => "forbidden:touch_id",
            Area::RunQueue => "forbidden:run_queue",
            Area::Adr0004 => "forbidden:adr0004",
            Area::PersonaReapply => "forbidden:persona_reapply",
            Area::GoalApproval => "forbidden:goal_approval",
            Area::MachineConfig => "forbidden:machine_config",
            Area::SelfReflect => "self_modify:reflect",
            Area::SelfPolicy => "self_modify:policy",
            Area::SelfGate => "self_modify:gate",
            Area::SelfCi => "self_modify:ci",
            Area::SelfScoreTable => "self_modify:score_table",
            Area::SelfThreshold => "self_modify:threshold",
        }
    }

    pub fn is_self_modify(self) -> bool {
        self.code().starts_with("self_modify:")
    }
}

/// How a path rule matches a repo-relative path (always `/`-separated).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Match {
    /// the path starts with this folder (written with a trailing `/`)
    Under(&'static str),
    /// the path is exactly this file
    Exact(&'static str),
    /// the path starts with the folder and ends with the suffix
    UnderWithSuffix(&'static str, &'static str),
}

/// Paths whose change puts a suggestion in an [`Area`]. Repo-relative.
pub const PATH_RULES: &[(Match, Area)] = &[
    (Match::Under("crates/aib-reflect/"), Area::SelfReflect),
    (Match::Under("crates/aib-policy/"), Area::SelfPolicy),
    (Match::Under(".github/workflows/"), Area::SelfCi),
    // the Stop / push / merge gates
    (Match::Under("aib/src/policy/hooks/"), Area::SelfGate),
    // all Python and shell under aib/ is frozen (Andy 11-09: everything new is Rust);
    // a change there is a frozen-script exception, which only the captain grants
    (Match::UnderWithSuffix("aib/", ".py"), Area::FrozenScript),
    (Match::UnderWithSuffix("aib/", ".sh"), Area::FrozenScript),
    (Match::UnderWithSuffix("extensions/", ".py"), Area::FrozenScript),
    // goal2 measurement protocol: the fixture's segment / cool-down parameters
    (
        Match::Exact("crates/aib-forkcount/src/bin/aib-goal2-fixture.rs"),
        Area::MeasurementProtocol,
    ),
];

/// Every area a repo-relative path falls in (sorted, no duplicates). A path that
/// tries to climb out (`..`) or is absolute is put in every self-modify area:
/// it cannot be judged, so it is never let through as harmless.
pub fn areas_of_path(path: &str) -> Vec<Area> {
    let p = path.replace('\\', "/");
    if p.starts_with('/') || p.split('/').any(|seg| seg == "..") || p.contains(':') {
        return Area::ALL
            .iter()
            .copied()
            .filter(|a| a.is_self_modify())
            .collect();
    }
    let mut out: Vec<Area> = PATH_RULES
        .iter()
        .filter(|(m, _)| match *m {
            Match::Under(dir) => p.starts_with(dir),
            Match::Exact(f) => p == f,
            Match::UnderWithSuffix(dir, suf) => p.starts_with(dir) && p.ends_with(suf),
        })
        .map(|(_, a)| *a)
        .collect();
    out.sort();
    out.dedup();
    out
}

/// Folders where a change needs the owning seat's look (DECISION §1 #6:
/// shared core, GUARDED paths, host code), besides touching two or more crates.
pub const OWNER_CHECK_UNDER: &[&str] = &[
    "crates/aib-core/",
    "crates/aib-store/",
    "crates/aib-sys/",
    "crates/aibd/",
    "crates/aib-cloud-host/",
    "Cargo.toml",
    "Cargo.lock",
];

/// The crate folder a path is in (`crates/<name>`), if any.
pub fn crate_of(path: &str) -> Option<&str> {
    let rest = path.strip_prefix("crates/")?;
    let name = rest.split('/').next()?;
    if name.is_empty() || !rest.contains('/') {
        return None;
    }
    Some(&path[..("crates/".len() + name.len())])
}

/// Whether a change to these paths needs the owning seat to say "no conflict".
pub fn needs_owner_check(paths: &[String]) -> bool {
    let mut crates: Vec<&str> = paths.iter().filter_map(|p| crate_of(p)).collect();
    crates.sort_unstable();
    crates.dedup();
    crates.len() >= 2
        || paths
            .iter()
            .any(|p| OWNER_CHECK_UNDER.iter().any(|d| p.starts_with(d)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_are_unique_and_prefixed() {
        let mut seen = std::collections::BTreeSet::new();
        for a in Area::ALL {
            let c = a.code();
            assert!(
                c.starts_with("forbidden:") || c.starts_with("self_modify:"),
                "reason: code_unprefixed {c}"
            );
            assert!(seen.insert(c), "reason: code_duplicated {c}");
        }
    }

    #[test]
    fn paths_fall_in_their_areas() {
        assert_eq!(
            areas_of_path("crates/aib-reflect/src/gate.rs"),
            vec![Area::SelfReflect],
            "reason: reflect_path_missed"
        );
        assert_eq!(
            areas_of_path(".github/workflows/aibd.yml"),
            vec![Area::SelfCi],
            "reason: ci_path_missed"
        );
        assert_eq!(
            areas_of_path("aib/src/cockpit/cooldown.py"),
            vec![Area::FrozenScript],
            "reason: frozen_path_missed"
        );
        assert_eq!(
            areas_of_path("aib/src/policy/hooks/aib-now-gate.py"),
            vec![Area::FrozenScript, Area::SelfGate],
            "reason: gate_path_missed"
        );
        assert!(
            areas_of_path("crates/aib-drive/src/lib.rs").is_empty(),
            "reason: plain_path_flagged"
        );
    }

    #[test]
    fn paths_that_climb_out_are_never_harmless() {
        for p in ["../x.rs", "crates/../../etc", "/etc/passwd", "C:foo"] {
            assert!(
                areas_of_path(p).iter().any(|a| a.is_self_modify()),
                "reason: escaping_path_let_through {p}"
            );
        }
    }

    #[test]
    fn owner_check_for_shared_core_or_two_crates() {
        let one = vec!["crates/aib-drive/src/lib.rs".to_string()];
        assert!(!needs_owner_check(&one), "reason: owner_check_for_one_crate");
        let two = vec![
            "crates/aib-drive/src/lib.rs".to_string(),
            "crates/aib-plan/src/main.rs".to_string(),
        ];
        assert!(needs_owner_check(&two), "reason: two_crates_not_checked");
        let core = vec!["crates/aib-core/src/lib.rs".to_string()];
        assert!(needs_owner_check(&core), "reason: shared_core_not_checked");
    }
}
