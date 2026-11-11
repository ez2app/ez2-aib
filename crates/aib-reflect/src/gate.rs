//! `aib reflect gate` (R4): may this suggestion be picked up without Andy?
//!
//! A pure function. It reads nothing, starts nothing and never asks the clock:
//! the caller passes `now` (unix seconds) and everything it read. The answer is
//! one [`Verdict`] with a main reason code and every reason that applied.
//! Reason codes are a contract: never reword them, add a hint elsewhere.
//!
//! Checks run in a fixed order; the first one that applies decides the kind:
//!
//! | # | check | kind, reason |
//! |---|---|---|
//! | 1 | paused | hold `paused` |
//! | 2 | the ledger had a newer unreadable line | hold `ledger_unreadable` |
//! | 3 | a forbidden area (aib-policy) | ask_andy `forbidden:<area>` |
//! | 4 | a self-modification area | ask_andy `self_modify:<area>` |
//! | 5 | this revision already has a work item | hold `already_dispatched` |
//! | 6 | no checkable evidence (total capped at 60) | hold `evidence_missing` |
//! | 7 | necessary conditions | hold `node_not_active` / `goal_changed` / `no_baseline` / `no_acceptance` / `risk_unclassified` / `expired` |
//! | 8 | mainline part under 10 | hold `mainline_low` |
//! | 9 | total under the threshold | hold `below_threshold` |
//! | 10 | the blind review | hold `review_missing` / `reviewer_unavailable` / `reviewer_unverified` / `review_from_future` / `review_below_threshold` |
//! | 11 | the human parts differ by more than the limit | discuss `review_disagree` |
//! | 12 | owner check (shared core / two crates) | hold `owner_check_missing` / discuss `owner_conflict` |
//! | 13 | a scorer's bias over the limit | hold `scorer_biased` |
//! | 14 | resources | hold `mainline_red` / `machine_hot` / `seat_busy` |
//! | 15 | caps | hold `seat_inflight_cap` / `daily_cap` |
//! | 16 | mode | off: hold `auto_off`; shadow: shadow_auto; on: auto |

use aib_policy::{areas_of_path, needs_owner_check, Area};
use serde_json::Value;

/// A future review line is unreadable beyond this many seconds (ledger checklist L4).
pub const FUTURE_SLACK_S: i64 = 300;
/// Without checkable evidence the total cannot go above this (DECISION §1 #4).
pub const NO_EVIDENCE_CAP: u32 = 60;
/// Under this mainline part nothing is automatic, whatever the total (DECISION §2).
pub const MAINLINE_MIN: u32 = 10;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Auto,
    ShadowAuto,
    AskAndy,
    Discuss,
    Hold,
}

impl Kind {
    pub fn code(self) -> &'static str {
        match self {
            Kind::Auto => "auto",
            Kind::ShadowAuto => "shadow_auto",
            Kind::AskAndy => "ask_andy",
            Kind::Discuss => "discuss",
            Kind::Hold => "hold",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub kind: Kind,
    /// the reason that decided `kind`; for auto / shadow_auto it is the mode
    pub reason: String,
    /// every reason that applied, in check order
    pub reasons: Vec<String>,
}

/// Evidence as the ledger keeps it. Only the first four count (same rule as
/// closing a dm: a file, a commit, a full PR URL, a receipt).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Evidence {
    File { path: String, sha256: String },
    Commit(String),
    PrUrl(String),
    Receipt { path: String, sha256: String },
    Text(String),
    WebLink(String),
}

fn hex_of_len(s: &str, n: usize) -> bool {
    s.len() == n && s.bytes().all(|b| b.is_ascii_hexdigit())
}

impl Evidence {
    pub fn checkable(&self) -> bool {
        match self {
            Evidence::File { path, sha256 } | Evidence::Receipt { path, sha256 } => {
                !path.trim().is_empty() && hex_of_len(sha256, 64)
            }
            Evidence::Commit(c) => hex_of_len(c, 40),
            Evidence::PrUrl(u) => pr_url_ok(u),
            Evidence::Text(_) | Evidence::WebLink(_) => false,
        }
    }
}

/// `https://github.com/<owner>/<repo>/pull/<number>` and nothing after it.
fn pr_url_ok(u: &str) -> bool {
    let Some(rest) = u.strip_prefix("https://github.com/") else {
        return false;
    };
    let parts: Vec<&str> = rest.split('/').collect();
    parts.len() == 4
        && !parts[0].is_empty()
        && !parts[1].is_empty()
        && parts[2] == "pull"
        && !parts[3].is_empty()
        && parts[3].bytes().all(|b| b.is_ascii_digit())
}

/// Who reviewed. Only the layer that runs the reviewer (R3) can make one, so a
/// `scorer: codex` written into a ledger line never becomes a second party
/// (ledger checklist L11).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedBy(String);

impl VerifiedBy {
    // made by the reviewer runner (R3, this crate) and the tests; nothing outside can
    #[allow(dead_code)]
    pub(crate) fn new(who: &str) -> Self {
        VerifiedBy(who.to_string())
    }
    pub fn who(&self) -> &str {
        &self.0
    }
}

/// The blind second review (Codex by default).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Review {
    pub by: Option<VerifiedBy>,
    /// what the ledger line claims; never trusted, kept for the report
    pub claimed: String,
    pub at: i64,
    pub total: u32,
    /// the 40 human-scored points (benefit / cost, risk, verifiability)
    pub human: u32,
    /// the bias the calibration (R6) measured for this reviewer
    pub bias: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OwnerView {
    NoConflict,
    Conflict,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Cooldown {
    Ok,
    Warn,
    Hot,
    Critical,
}

/// One suggestion as the gate needs it (R1a adds `From<&Suggestion>`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GateInput {
    pub suggestion_id: String,
    pub revision: u32,
    pub seat: String,
    pub created_at: i64,
    /// false = the reader met a newer unreadable line for this suggestion (L3)
    pub readable: bool,
    /// areas the proposer declared (risk classification); paths add their own
    pub areas: Vec<Area>,
    /// repo-relative paths the change will touch
    pub paths: Vec<String>,
    pub risk_classified: bool,
    pub evidence: Vec<Evidence>,
    pub node_active: bool,
    pub goal_sha_scored: String,
    pub goal_sha_now: String,
    pub baseline: Option<String>,
    pub acceptance: Option<String>,
    /// the proposer's total (0–100) and its parts
    pub total: u32,
    pub mainline: u32,
    pub human: u32,
    pub proposer_bias: i32,
    pub review: Option<Review>,
    /// false = the reviewer (Codex) could not be reached
    pub reviewer_available: bool,
    pub owner: Option<OwnerView>,
    /// this revision already has a work item
    pub dispatched: bool,
}

/// What the rest of the cabin looks like when the gate runs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct World {
    pub mainline_red: bool,
    pub cooldown: Cooldown,
    /// the proposing seat has its own PR waiting for review or a push
    pub seat_has_waiting_pr: bool,
    /// automatic items this seat has in progress
    pub seat_inflight: u32,
    /// when each automatic item of the whole cabin was dispatched (unix s)
    pub dispatched_at: Vec<i64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Off,
    Shadow,
    On,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub threshold: u32,
    pub mode: Mode,
    pub daily_cap: u32,
    pub per_seat_inflight: u32,
    pub human_diff_max: u32,
    pub expire_hours: u32,
    pub bias_max: i32,
    pub paused: bool,
    /// the cabin's day starts at local midnight; Taipei is +480
    pub tz_offset_min: i32,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            threshold: 75,
            // until Andy picks (DECISION §4), shadow
            mode: Mode::Shadow,
            daily_cap: 2,
            per_seat_inflight: 1,
            human_diff_max: 15,
            expire_hours: 72,
            bias_max: 15,
            paused: false,
            tz_offset_min: 480,
        }
    }
}

fn int_in(v: &Value, key: &str, lo: i64, hi: i64) -> Result<i64, String> {
    let n = v
        .as_i64()
        .ok_or_else(|| format!("config_wrong_type: {key}"))?;
    if n < lo || n > hi {
        return Err(format!("config_out_of_range: {key}={n}"));
    }
    Ok(n)
}

impl Config {
    /// A JSON object; missing keys keep the default, unknown keys and wrong
    /// types are errors. On an error the caller treats the cabin as paused.
    pub fn parse(text: &str) -> Result<Config, String> {
        let v: Value = serde_json::from_str(text).map_err(|e| format!("config_bad_json: {e}"))?;
        let obj = v
            .as_object()
            .ok_or_else(|| "config_wrong_type: top".to_string())?;
        let mut c = Config::default();
        for (k, x) in obj {
            match k.as_str() {
                "threshold" => c.threshold = int_in(x, k, 50, 100)? as u32,
                "mode" => {
                    c.mode = match x.as_str() {
                        Some("off") => Mode::Off,
                        Some("shadow") => Mode::Shadow,
                        Some("on") => Mode::On,
                        _ => return Err(format!("config_wrong_type: {k}")),
                    }
                }
                "daily_cap" => c.daily_cap = int_in(x, k, 0, 50)? as u32,
                "per_seat_inflight" => c.per_seat_inflight = int_in(x, k, 0, 10)? as u32,
                "human_diff_max" => c.human_diff_max = int_in(x, k, 0, 40)? as u32,
                "expire_hours" => c.expire_hours = int_in(x, k, 1, 720)? as u32,
                "bias_max" => c.bias_max = int_in(x, k, 0, 100)? as i32,
                "paused" => {
                    c.paused = x
                        .as_bool()
                        .ok_or_else(|| format!("config_wrong_type: {k}"))?
                }
                "tz_offset_min" => c.tz_offset_min = int_in(x, k, -720, 840)? as i32,
                _ => return Err(format!("config_unknown_key: {k}")),
            }
        }
        Ok(c)
    }
}

/// Day number of a unix time in the cabin's time zone.
pub fn local_day(t: i64, tz_offset_min: i32) -> i64 {
    t.saturating_add(i64::from(tz_offset_min) * 60)
        .div_euclid(86_400)
}

fn all_areas(s: &GateInput) -> Vec<Area> {
    let mut a: Vec<Area> = s.areas.clone();
    for p in &s.paths {
        a.extend(areas_of_path(p));
    }
    a.sort();
    a.dedup();
    a
}

/// Every reason that applies, in check order, each with the kind it would give.
fn reasons(s: &GateInput, w: &World, c: &Config, now: i64) -> Vec<(Kind, String)> {
    let mut r: Vec<(Kind, String)> = Vec::new();
    let hold = |code: &str| (Kind::Hold, code.to_string());
    // 1
    if c.paused {
        r.push(hold("paused"));
    }
    // 2
    if !s.readable {
        r.push(hold("ledger_unreadable"));
    }
    // 3, 4
    let areas = all_areas(s);
    for a in areas.iter().filter(|a| !a.is_self_modify()) {
        r.push((Kind::AskAndy, a.code().to_string()));
    }
    for a in areas.iter().filter(|a| a.is_self_modify()) {
        r.push((Kind::AskAndy, a.code().to_string()));
    }
    // 5
    if s.dispatched {
        r.push(hold("already_dispatched"));
    }
    // 6
    let has_evidence = s.evidence.iter().any(Evidence::checkable);
    if !has_evidence {
        r.push(hold("evidence_missing"));
    }
    let total = if has_evidence {
        s.total
    } else {
        s.total.min(NO_EVIDENCE_CAP)
    };
    // 7
    if !s.node_active {
        r.push(hold("node_not_active"));
    }
    if s.goal_sha_scored != s.goal_sha_now {
        r.push(hold("goal_changed"));
    }
    if s.baseline.as_deref().is_none_or(|b| b.trim().is_empty()) {
        r.push(hold("no_baseline"));
    }
    if s.acceptance.as_deref().is_none_or(|b| b.trim().is_empty()) {
        r.push(hold("no_acceptance"));
    }
    if !s.risk_classified {
        r.push(hold("risk_unclassified"));
    }
    if now.saturating_sub(s.created_at) > i64::from(c.expire_hours) * 3600 {
        r.push(hold("expired"));
    }
    // 8
    if s.mainline < MAINLINE_MIN {
        r.push(hold("mainline_low"));
    }
    // 9
    if total < c.threshold {
        r.push(hold("below_threshold"));
    }
    // 10, 11
    if !s.reviewer_available {
        r.push(hold("reviewer_unavailable"));
    }
    match &s.review {
        None => r.push(hold("review_missing")),
        Some(rv) => {
            if rv.by.is_none() {
                r.push(hold("reviewer_unverified"));
            }
            if rv.at > now.saturating_add(FUTURE_SLACK_S) {
                r.push(hold("review_from_future"));
            }
            if rv.total < c.threshold {
                r.push(hold("review_below_threshold"));
            }
            if s.human.abs_diff(rv.human) > c.human_diff_max {
                r.push((Kind::Discuss, "review_disagree".to_string()));
            }
        }
    }
    // 12
    if needs_owner_check(&s.paths) {
        match s.owner {
            None => r.push(hold("owner_check_missing")),
            Some(OwnerView::Conflict) => r.push((Kind::Discuss, "owner_conflict".to_string())),
            Some(OwnerView::NoConflict) => {}
        }
    }
    // 13
    let reviewer_bias = s.review.as_ref().map_or(0, |rv| rv.bias);
    if s.proposer_bias > c.bias_max || reviewer_bias > c.bias_max {
        r.push(hold("scorer_biased"));
    }
    // 14
    if w.mainline_red {
        r.push(hold("mainline_red"));
    }
    if w.cooldown >= Cooldown::Warn {
        r.push(hold("machine_hot"));
    }
    if w.seat_has_waiting_pr {
        r.push(hold("seat_busy"));
    }
    // 15
    if w.seat_inflight >= c.per_seat_inflight {
        r.push(hold("seat_inflight_cap"));
    }
    let today = local_day(now, c.tz_offset_min);
    let done_today = w
        .dispatched_at
        .iter()
        .filter(|&&t| local_day(t, c.tz_offset_min) == today)
        .count();
    if done_today >= c.daily_cap as usize {
        r.push(hold("daily_cap"));
    }
    // 16
    if c.mode == Mode::Off {
        r.push(hold("auto_off"));
    }
    r
}

/// The verdict for one suggestion. Pure: same input, same answer.
pub fn gate(s: &GateInput, w: &World, c: &Config, now: i64) -> Verdict {
    let found = reasons(s, w, c, now);
    let all: Vec<String> = found.iter().map(|(_, code)| code.clone()).collect();
    match found.first() {
        Some((kind, code)) => Verdict {
            kind: *kind,
            reason: code.clone(),
            reasons: all,
        },
        None => {
            let (kind, code) = match c.mode {
                Mode::On => (Kind::Auto, "mode_on"),
                _ => (Kind::ShadowAuto, "mode_shadow"),
            };
            Verdict {
                kind,
                reason: code.to_string(),
                reasons: all,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-11-12 05:00 Taipei = 2026-11-11 21:00 UTC
    const NOW: i64 = 1_794_430_800;
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn ok_input() -> GateInput {
        GateInput {
            suggestion_id: "s1".into(),
            revision: 1,
            seat: "043dd".into(),
            created_at: NOW - 3600,
            readable: true,
            areas: vec![],
            paths: vec!["crates/aib-drive/src/lib.rs".into()],
            risk_classified: true,
            evidence: vec![Evidence::Receipt {
                path: "RECEIPT-x.txt".into(),
                sha256: SHA.into(),
            }],
            node_active: true,
            goal_sha_scored: "g1".into(),
            goal_sha_now: "g1".into(),
            baseline: Some("23.95 fork/s".into()),
            acceptance: Some("title-beat by_argv B/A <= 0.2".into()),
            total: 80,
            mainline: 15,
            human: 30,
            proposer_bias: 0,
            review: Some(Review {
                by: Some(VerifiedBy::new("codex")),
                claimed: "codex".into(),
                at: NOW - 60,
                total: 80,
                human: 30,
                bias: 0,
            }),
            reviewer_available: true,
            owner: None,
            dispatched: false,
        }
    }

    fn calm() -> World {
        World {
            mainline_red: false,
            cooldown: Cooldown::Ok,
            seat_has_waiting_pr: false,
            seat_inflight: 0,
            dispatched_at: vec![],
        }
    }

    fn on() -> Config {
        Config {
            mode: Mode::On,
            ..Config::default()
        }
    }

    fn want(v: &Verdict, kind: Kind, reason: &str) {
        assert!(
            v.kind == kind && v.reason == reason,
            "reason: want {} {reason}, got {} {} (all {:?})",
            kind.code(),
            v.kind.code(),
            v.reason,
            v.reasons
        );
    }

    fn run(s: &GateInput) -> Verdict {
        gate(s, &calm(), &on(), NOW)
    }

    #[test]
    fn x01_x02_threshold_is_at_least_75() {
        let mut s = ok_input();
        s.total = 75;
        if let Some(r) = s.review.as_mut() {
            r.total = 75;
        }
        want(&run(&s), Kind::Auto, "mode_on");
        s.total = 74;
        want(&run(&s), Kind::Hold, "below_threshold");
    }

    #[test]
    fn x03_x04_no_checkable_evidence_caps_the_total() {
        let mut s = ok_input();
        s.total = 95;
        s.evidence = vec![];
        let v = run(&s);
        want(&v, Kind::Hold, "evidence_missing");
        assert!(
            v.reasons.iter().any(|r| r == "below_threshold"),
            "reason: cap_not_applied {:?}",
            v.reasons
        );
        s.evidence = vec![
            Evidence::Text("measured, trust me".into()),
            Evidence::WebLink("https://example.invalid/x".into()),
            Evidence::Commit("abc123".into()),
            Evidence::PrUrl("https://github.com/o/r/pull/1/files".into()),
            Evidence::File {
                path: "x".into(),
                sha256: "short".into(),
            },
        ];
        want(&run(&s), Kind::Hold, "evidence_missing");
        s.evidence = vec![Evidence::PrUrl("https://github.com/o/r/pull/695".into())];
        want(&run(&s), Kind::Auto, "mode_on");
        s.evidence = vec![Evidence::Commit(
            "fbb5242885f4b662757d823a2d8bcebe41fcd643".into(),
        )];
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x05_to_x09_forbidden_and_self_modify_ask_andy_in_every_mode() {
        let cases: [(Vec<Area>, &str, &str); 5] = [
            (
                vec![Area::HostWrite],
                "crates/aib-drive/src/lib.rs",
                "forbidden:host_write",
            ),
            (
                vec![],
                "crates/aib-reflect/src/gate.rs",
                "self_modify:reflect",
            ),
            (vec![], ".github/workflows/aibd.yml", "self_modify:ci"),
            (
                vec![],
                "aib/src/cockpit/cooldown.py",
                "forbidden:frozen_script",
            ),
            (
                vec![],
                "crates/aib-forkcount/src/bin/aib-goal2-fixture.rs",
                "forbidden:measurement_protocol",
            ),
        ];
        for (areas, path, code) in cases {
            // Andy 11-12 05:2x: a forbidden hit at a full 100 is still never dispatched
            let mut s = ok_input();
            s.total = 100;
            if let Some(rv) = s.review.as_mut() {
                rv.total = 100;
            }
            s.areas = areas;
            s.paths = vec![path.into()];
            for mode in [Mode::On, Mode::Shadow, Mode::Off] {
                let c = Config { mode, ..on() };
                want(&gate(&s, &calm(), &c, NOW), Kind::AskAndy, code);
            }
        }
    }

    #[test]
    fn x10_pause_comes_first_but_lists_the_rest() {
        let mut s = ok_input();
        s.areas = vec![Area::SpendMoney];
        let c = Config {
            paused: true,
            ..on()
        };
        let v = gate(&s, &calm(), &c, NOW);
        want(&v, Kind::Hold, "paused");
        assert!(
            v.reasons.iter().any(|r| r == "forbidden:spend_money"),
            "reason: hidden_forbidden {:?}",
            v.reasons
        );
    }

    #[test]
    fn x11_x14_the_blind_review_is_required_and_verified() {
        let mut s = ok_input();
        s.review = None;
        want(&run(&s), Kind::Hold, "review_missing");
        let mut s = ok_input();
        if let Some(r) = s.review.as_mut() {
            r.by = None;
            r.claimed = "codex".into();
        }
        want(&run(&s), Kind::Hold, "reviewer_unverified");
        let mut s = ok_input();
        s.reviewer_available = false;
        want(&run(&s), Kind::Hold, "reviewer_unavailable");
        let mut s = ok_input();
        if let Some(r) = s.review.as_mut() {
            r.total = 70;
        }
        want(&run(&s), Kind::Hold, "review_below_threshold");
    }

    #[test]
    fn x12_x13_human_parts_may_differ_by_15_not_16() {
        let mut s = ok_input();
        s.human = 38;
        if let Some(r) = s.review.as_mut() {
            r.human = 20;
        }
        want(&run(&s), Kind::Discuss, "review_disagree");
        if let Some(r) = s.review.as_mut() {
            r.human = 23;
        }
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x15_mainline_under_10_is_never_automatic() {
        let mut s = ok_input();
        s.mainline = 9;
        s.total = 90;
        want(&run(&s), Kind::Hold, "mainline_low");
        s.mainline = 10;
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x16_a_changed_goal_voids_the_score() {
        let mut s = ok_input();
        s.goal_sha_now = "g2".into();
        want(&run(&s), Kind::Hold, "goal_changed");
    }

    #[test]
    fn x17_seventy_two_hours_exactly_is_still_valid() {
        let mut s = ok_input();
        s.created_at = NOW - 72 * 3600;
        want(&run(&s), Kind::Auto, "mode_on");
        s.created_at = NOW - 72 * 3600 - 1;
        want(&run(&s), Kind::Hold, "expired");
    }

    #[test]
    fn necessary_conditions_each_hold() {
        type Break = fn(&mut GateInput);
        let cases: [(Break, &str); 4] = [
            (|s| s.node_active = false, "node_not_active"),
            (|s| s.baseline = Some("  ".into()), "no_baseline"),
            (|s| s.acceptance = None, "no_acceptance"),
            (|s| s.risk_classified = false, "risk_unclassified"),
        ];
        for (f, code) in cases {
            let mut s = ok_input();
            f(&mut s);
            want(&run(&s), Kind::Hold, code);
        }
    }

    #[test]
    fn x18_a_warm_machine_starts_nothing() {
        let w = World {
            cooldown: Cooldown::Warn,
            ..calm()
        };
        want(
            &gate(&ok_input(), &w, &on(), NOW),
            Kind::Hold,
            "machine_hot",
        );
        let w = World {
            mainline_red: true,
            ..calm()
        };
        want(
            &gate(&ok_input(), &w, &on(), NOW),
            Kind::Hold,
            "mainline_red",
        );
        let w = World {
            seat_has_waiting_pr: true,
            ..calm()
        };
        want(&gate(&ok_input(), &w, &on(), NOW), Kind::Hold, "seat_busy");
    }

    #[test]
    fn x19_x20_caps() {
        let w = World {
            seat_inflight: 1,
            ..calm()
        };
        want(
            &gate(&ok_input(), &w, &on(), NOW),
            Kind::Hold,
            "seat_inflight_cap",
        );
        let w = World {
            dispatched_at: vec![NOW - 600, NOW - 300],
            ..calm()
        };
        want(&gate(&ok_input(), &w, &on(), NOW), Kind::Hold, "daily_cap");
        let c = Config {
            daily_cap: 0,
            ..on()
        };
        want(
            &gate(&ok_input(), &calm(), &c, NOW),
            Kind::Hold,
            "daily_cap",
        );
    }

    #[test]
    fn x21_x22_modes() {
        let c = Config {
            mode: Mode::Shadow,
            ..on()
        };
        want(
            &gate(&ok_input(), &calm(), &c, NOW),
            Kind::ShadowAuto,
            "mode_shadow",
        );
        let c = Config {
            mode: Mode::Off,
            ..on()
        };
        want(&gate(&ok_input(), &calm(), &c, NOW), Kind::Hold, "auto_off");
        assert_eq!(
            Config::default().mode,
            Mode::Shadow,
            "reason: default_not_shadow"
        );
    }

    #[test]
    fn x23_x24_one_work_item_per_revision() {
        let mut s = ok_input();
        s.dispatched = true;
        want(&run(&s), Kind::Hold, "already_dispatched");
        s.revision = 2;
        s.dispatched = false;
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x25_two_crates_need_the_owner() {
        let mut s = ok_input();
        s.paths = vec![
            "crates/aib-drive/src/lib.rs".into(),
            "crates/aib-forkcount/src/main.rs".into(),
        ];
        want(&run(&s), Kind::Hold, "owner_check_missing");
        s.owner = Some(OwnerView::Conflict);
        want(&run(&s), Kind::Discuss, "owner_conflict");
        s.owner = Some(OwnerView::NoConflict);
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x26_a_biased_scorer_triggers_nothing() {
        let mut s = ok_input();
        s.proposer_bias = 16;
        want(&run(&s), Kind::Hold, "scorer_biased");
        let mut s = ok_input();
        if let Some(r) = s.review.as_mut() {
            r.bias = 16;
        }
        want(&run(&s), Kind::Hold, "scorer_biased");
        let mut s = ok_input();
        s.proposer_bias = 15;
        want(&run(&s), Kind::Auto, "mode_on");
    }

    #[test]
    fn x27_the_day_turns_at_taipei_midnight() {
        // 2026-11-12 00:00 Taipei = 2026-11-11 16:00 UTC
        let midnight = NOW - 5 * 3600;
        let mut s = ok_input();
        s.created_at = midnight - 3600;
        if let Some(r) = s.review.as_mut() {
            r.at = midnight - 600;
        }
        let w = World {
            dispatched_at: vec![midnight - 120, midnight - 1],
            ..calm()
        };
        want(&gate(&s, &w, &on(), midnight), Kind::Auto, "mode_on");
        want(&gate(&s, &w, &on(), midnight - 1), Kind::Hold, "daily_cap");
    }

    #[test]
    fn x28_a_newer_unreadable_line_hides_an_older_pass() {
        let mut s = ok_input();
        s.readable = false;
        want(&run(&s), Kind::Hold, "ledger_unreadable");
    }

    #[test]
    fn x29_a_review_from_the_future_does_not_count() {
        let mut s = ok_input();
        if let Some(r) = s.review.as_mut() {
            r.at = NOW + 300;
        }
        want(&run(&s), Kind::Auto, "mode_on");
        if let Some(r) = s.review.as_mut() {
            r.at = NOW + 301;
        }
        want(&run(&s), Kind::Hold, "review_from_future");
        if let Some(r) = s.review.as_mut() {
            r.at = i64::MAX;
        }
        want(&gate(&s, &calm(), &on(), i64::MAX), Kind::Hold, "expired");
    }

    #[test]
    fn same_input_same_verdict() {
        let mut s = ok_input();
        s.areas = vec![Area::Dns, Area::SpendMoney];
        s.review = None;
        let first = run(&s);
        for _ in 0..100 {
            assert_eq!(run(&s), first, "reason: verdict_not_deterministic");
        }
    }

    #[test]
    fn c1_to_c4_config_rejects_what_it_does_not_know() {
        let cases = [
            (r#"{"treshold": 75}"#, "config_unknown_key"),
            (r#"{"threshold": 49}"#, "config_out_of_range"),
            (r#"{"threshold": 101}"#, "config_out_of_range"),
            (r#"{"mode": "yes"}"#, "config_wrong_type"),
            (r#"{"paused": "true"}"#, "config_wrong_type"),
            (r#"[]"#, "config_wrong_type"),
            (r#"{"#, "config_bad_json"),
        ];
        for (text, code) in cases {
            match Config::parse(text) {
                Ok(c) => panic!("reason: {code} not raised for {text}: {c:?}"),
                Err(e) => assert!(e.starts_with(code), "reason: want {code}, got {e}"),
            }
        }
        let c = Config::parse(r#"{"mode": "on", "daily_cap": 0, "paused": true}"#);
        assert_eq!(
            c,
            Ok(Config {
                mode: Mode::On,
                daily_cap: 0,
                paused: true,
                ..Config::default()
            }),
            "reason: config_not_read"
        );
    }
}
