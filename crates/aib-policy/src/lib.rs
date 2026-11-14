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

/// How a path rule matches a normalised repo-relative path (see [`normalize`]).
/// Every pattern is written in lower case.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Match {
    /// the path starts with this folder (written with a trailing `/`)
    Under(&'static str),
    /// the path starts with this text (a file-name prefix such as `scripts/knives-`)
    Prefix(&'static str),
    /// the path is exactly this file
    Exact(&'static str),
    /// the path starts with the folder and ends with the suffix
    UnderWithSuffix(&'static str, &'static str),
    /// the path ends with this suffix, wherever it is
    Suffix(&'static str),
}

/// Paths whose change puts a suggestion in an [`Area`]. Repo-relative, lower case.
pub const PATH_RULES: &[(Match, Area)] = &[
    (Match::Under("crates/aib-reflect/"), Area::SelfReflect),
    (Match::Under("crates/aib-policy/"), Area::SelfPolicy),
    // CI: workflows, rulesets, the scripts they call, and the xtask checks they run
    (Match::Under(".github/"), Area::SelfCi),
    (Match::Under("xtask/src/"), Area::SelfCi),
    // the Stop / push / merge gates
    (Match::Under("aib/src/policy/hooks/"), Area::SelfGate),
    (Match::Under("scripts/hooks/"), Area::SelfGate),
    (Match::Prefix("scripts/check-"), Area::SelfGate),
    (
        Match::Exact("aib/tools/release/docs_only.py"),
        Area::SelfGate,
    ),
    // the knives themselves: a softer knife is a softer gate (1ced7 R4 M2)
    (Match::Exact("scripts/knife.py"), Area::SelfGate),
    (Match::Exact("scripts/knife-build.py"), Area::SelfGate),
    (Match::Prefix("scripts/knives-"), Area::SelfGate),
    // consent / SIGN (DECISION §1 #8) and ADR 0004
    (Match::Under("crates/aib-consent/"), Area::SignConsent),
    (Match::Prefix("docs/adr/0004-"), Area::Adr0004),
    // all Python is frozen (Andy 11-13: "程式只能是 rust 沒有 Python"), and the shell under
    // aib/ (Andy 11-09): a change there is a frozen-script exception, only the captain grants it
    (Match::Suffix(".py"), Area::FrozenScript),
    (Match::UnderWithSuffix("aib/", ".sh"), Area::FrozenScript),
    // goal2 measurement protocol: the fixture's segment / cool-down parameters
    (
        Match::Exact("crates/aib-forkcount/src/bin/aib-goal2-fixture.rs"),
        Area::MeasurementProtocol,
    ),
];

/// A suggestion's path as the rules compare it: `\` read as `/`, empty and `.` segments
/// dropped, ASCII lower case (macOS and Windows file systems do not tell `Crates/` from
/// `crates/`, so neither may the rules; 1ced7 R4 M1). `None` when it cannot be judged:
/// absolute, climbing out (`..`), a drive or stream (`:`), or nothing left.
pub fn normalize(path: &str) -> Option<String> {
    let p = path.replace('\\', "/");
    if p.starts_with('/') || p.contains(':') {
        return None;
    }
    let mut segs = Vec::new();
    for seg in p.split('/') {
        match seg {
            "" | "." => {}
            ".." => return None,
            s => segs.push(s.to_ascii_lowercase()),
        }
    }
    if segs.is_empty() {
        return None;
    }
    Some(segs.join("/"))
}

/// Every area a repo-relative path falls in (sorted, no duplicates). A path [`normalize`]
/// cannot judge is put in every self-modify area: it is never let through as harmless.
pub fn areas_of_path(path: &str) -> Vec<Area> {
    let Some(p) = normalize(path) else {
        return Area::ALL
            .iter()
            .copied()
            .filter(|a| a.is_self_modify())
            .collect();
    };
    let mut out: Vec<Area> = PATH_RULES
        .iter()
        .filter(|(m, _)| match *m {
            Match::Under(dir) => p.starts_with(dir),
            Match::Prefix(pre) => p.starts_with(pre),
            Match::Exact(f) => p == f,
            Match::UnderWithSuffix(dir, suf) => p.starts_with(dir) && p.ends_with(suf),
            Match::Suffix(suf) => p.ends_with(suf),
        })
        .map(|(_, a)| *a)
        .collect();
    out.sort();
    out.dedup();
    out
}

/// Folders where a change needs the owning seat's look (DECISION §1 #6:
/// shared core, GUARDED paths, host code), besides touching two or more crates.
/// Lower case, compared with the [`normalize`]d path.
pub const OWNER_CHECK_UNDER: &[&str] = &[
    "crates/aib-core/",
    "crates/aib-store/",
    "crates/aib-sys/",
    "crates/aibd/",
    "crates/aib-cloud-host/",
    "cargo.toml",
    "cargo.lock",
];

/// The crate folder a path is in (`crates/<name>`, normalised), if any.
pub fn crate_of(path: &str) -> Option<String> {
    let p = normalize(path)?;
    let rest = p.strip_prefix("crates/")?;
    let (name, _) = rest.split_once('/')?;
    Some(format!("crates/{name}"))
}

/// Whether a change to these paths needs the owning seat to say "no conflict".
/// A path that cannot be judged always needs it.
pub fn needs_owner_check(paths: &[String]) -> bool {
    let mut crates: Vec<String> = paths.iter().filter_map(|p| crate_of(p)).collect();
    crates.sort_unstable();
    crates.dedup();
    crates.len() >= 2
        || paths.iter().any(|p| match normalize(p) {
            None => true,
            Some(n) => OWNER_CHECK_UNDER.iter().any(|d| n.starts_with(d)),
        })
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
        assert!(
            !needs_owner_check(&one),
            "reason: owner_check_for_one_crate"
        );
        let two = vec![
            "crates/aib-drive/src/lib.rs".to_string(),
            "crates/aib-plan/src/main.rs".to_string(),
        ];
        assert!(needs_owner_check(&two), "reason: two_crates_not_checked");
        let core = vec!["crates/aib-core/src/lib.rs".to_string()];
        assert!(needs_owner_check(&core), "reason: shared_core_not_checked");
    }

    #[test]
    fn other_spellings_of_a_path_fall_in_the_same_areas() {
        // 1ced7 R4 M1: ./, //, \ and case are the same file on macOS / Windows
        for (p, want) in [
            ("./crates/aib-reflect/src/gate.rs", vec![Area::SelfReflect]),
            ("crates//aib-reflect/src/gate.rs", vec![Area::SelfReflect]),
            ("./.github/workflows/aibd.yml", vec![Area::SelfCi]),
            ("Crates/aib-reflect/src/gate.rs", vec![Area::SelfReflect]),
            (".GitHub/workflows/aibd.yml", vec![Area::SelfCi]),
            ("crates\\aib-policy\\.\\src\\lib.rs", vec![Area::SelfPolicy]),
        ] {
            assert_eq!(areas_of_path(p), want, "reason: spelling_escapes_area {p}");
        }
        for p in ["", ".", "./", "//"] {
            assert!(
                areas_of_path(p).iter().any(|a| a.is_self_modify()),
                "reason: empty_path_let_through {p:?}"
            );
        }
    }

    #[test]
    fn the_repo_gates_and_frozen_scripts_fall_in_their_areas() {
        // 1ced7 R4 M2: the gates and knives in this repo today
        for (p, want) in [
            ("scripts/hooks/pre-push", Area::SelfGate),
            ("scripts/check-ip-literals.py", Area::SelfGate),
            ("scripts/check-core-purity.sh", Area::SelfGate),
            ("scripts/knife.py", Area::SelfGate),
            ("scripts/knife-build.py", Area::SelfGate),
            ("scripts/knives-reflect.sh", Area::SelfGate),
            ("aib/tools/release/docs_only.py", Area::SelfGate),
            ("xtask/src/knife_anchors.rs", Area::SelfCi),
            (".github/scripts/x.sh", Area::SelfCi),
            (".github/rulesets/main.json", Area::SelfCi),
            ("crates/aib-consent/src/lib.rs", Area::SignConsent),
            ("docs/adr/0004-any-name.md", Area::Adr0004),
            ("tests/test_aib_lease.py", Area::FrozenScript),
            ("scripts/prev_release.py", Area::FrozenScript),
            ("extensions/x/y.py", Area::FrozenScript),
            ("aib/src/x.sh", Area::FrozenScript),
        ] {
            assert!(
                areas_of_path(p).contains(&want),
                "reason: repo_gate_missed {p} {want:?}"
            );
        }
        assert!(
            areas_of_path("docs/adr/0005-other.md").is_empty(),
            "reason: other_adr_flagged"
        );
    }

    #[test]
    fn owner_check_reads_the_normalised_path() {
        for p in [
            "./crates/aib-core/src/lib.rs",
            "Crates/AIB-core/src/lib.rs",
            "./Cargo.lock",
        ] {
            assert!(
                needs_owner_check(&[p.to_string()]),
                "reason: owner_check_spelling_escapes {p}"
            );
        }
        let two = vec![
            "./crates/aib-drive/src/lib.rs".to_string(),
            "crates//aib-plan/src/main.rs".to_string(),
        ];
        assert!(
            needs_owner_check(&two),
            "reason: two_crates_spelling_escapes"
        );
        let same = vec![
            "crates/aib-drive/src/lib.rs".to_string(),
            "./Crates/aib-drive/src/x.rs".to_string(),
        ];
        assert!(!needs_owner_check(&same), "reason: one_crate_counted_twice");
        assert!(
            needs_owner_check(&["../x.rs".to_string()]),
            "reason: unjudgeable_path_skips_owner"
        );
    }
}
