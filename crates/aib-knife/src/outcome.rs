//! One outcome per knife, decided in a fixed order (design §7.1, Codex H1); the first
//! rule that applies wins:
//!
//! 1. the anchor is not there exactly once ⇒ `AnchorMissing` (file untouched, next knife);
//! 2. the file did not read back byte-identical after the restore ⇒ `Undecided`, **stop**;
//! 3. timeout, processes left behind, a process list / tree result that cannot be read,
//!    or output that cannot be read ⇒ `Undecided`, **stop** (no knife on a dirty machine);
//! 4. the command passed ⇒ `Survived`;
//! 5. the expected test is among the failed names ⇒ `Bit` (before the build check, as
//!    knife.py: a test's own output may contain "could not compile"); two different full
//!    names with the same short tail ⇒ `Undecided` (Codex M3), not a stop;
//! 6. the build failed ⇒ `NotFallen`;
//! 7. anything else (red without naming the test, "everything went red") ⇒ `Survived`.
//!
//! rc: 0 every knife `Bit` and every noop green / 4 every knife `Bit` but the noop was
//! skipped (`"noop": false`, a weaker proof; Codex M1) / 1 anything else, **including a
//! red or dirty noop** (a9136 #586 H1) / 2 usage error.

use crate::names::{build_failed, failed_names, find_expected, passed_names, Hit};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Why {
    RestoreMismatch,
    Timeout,
    Leftovers(u32),
    ProcessesUnreadable,
    OutputUnreadable,
    Ambiguous,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Bit,
    Survived,
    NotFallen,
    AnchorMissing(usize),
    Undecided(Why),
}

impl Outcome {
    pub fn tag(&self) -> &'static str {
        match self {
            Outcome::Bit => "BIT",
            Outcome::Survived => "SURVIVED",
            Outcome::NotFallen => "NOT-FALLEN",
            Outcome::AnchorMissing(_) => "ANCHOR-MISSING",
            Outcome::Undecided(_) => "UNDECIDED",
        }
    }
}

impl fmt::Display for Outcome {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Outcome::AnchorMissing(n) => {
                write!(f, "ANCHOR-MISSING (old occurs {n} times, must be 1)")
            }
            Outcome::Undecided(w) => write!(f, "UNDECIDED ({w:?})"),
            o => f.write_str(o.tag()),
        }
    }
}

/// What the runner saw for leftover processes. `Unreadable` is never "none" (bb7dc #525).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Leftovers {
    NoneSeen,
    Seen(u32),
    Unreadable,
}

/// Everything the runner observed for one command run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Run {
    /// `None`: no exit code (killed by a signal); counts as failed.
    pub rc: Option<i32>,
    pub timed_out: bool,
    pub leftovers: Leftovers,
    /// `None`: the output file could not be opened or read.
    pub output: Option<String>,
}

/// Rules 3 of the order, shared by the noop and the knives.
fn dirty(r: &Run) -> Option<Why> {
    if r.timed_out {
        return Some(Why::Timeout);
    }
    match r.leftovers {
        Leftovers::Unreadable => return Some(Why::ProcessesUnreadable),
        Leftovers::Seen(n) => return Some(Why::Leftovers(n)),
        Leftovers::NoneSeen => {}
    }
    if r.output.is_none() {
        return Some(Why::OutputUnreadable);
    }
    None
}

/// The verdict for one knife, and whether the whole run must stop after it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub outcome: Outcome,
    pub stop: bool,
    /// The failed names that were read (for the report).
    pub failed: Vec<String>,
}

/// `anchor`: how often `old` occurred (only 1 runs anything). `run`/`restored` are
/// `None` when nothing ran because the anchor was missing.
pub fn decide(anchor: usize, restored: Option<bool>, run: Option<&Run>, expect: &str) -> Verdict {
    let v = |outcome, stop, failed| Verdict {
        outcome,
        stop,
        failed,
    };
    if anchor != 1 {
        return v(Outcome::AnchorMissing(anchor), false, Vec::new());
    }
    if restored != Some(true) {
        return v(Outcome::Undecided(Why::RestoreMismatch), true, Vec::new());
    }
    let Some(r) = run else {
        return v(Outcome::Undecided(Why::OutputUnreadable), true, Vec::new());
    };
    if let Some(w) = dirty(r) {
        return v(Outcome::Undecided(w), true, Vec::new());
    }
    let out = r.output.as_deref().unwrap_or("");
    let failed = failed_names(out);
    if r.rc == Some(0) {
        return v(Outcome::Survived, false, failed);
    }
    match find_expected(&failed, expect) {
        Hit::One(_) => v(Outcome::Bit, false, failed),
        Hit::Ambiguous(_) => v(Outcome::Undecided(Why::Ambiguous), false, failed),
        Hit::None if build_failed(out) => v(Outcome::NotFallen, false, failed),
        Hit::None => v(Outcome::Survived, false, failed),
    }
}

/// The noop of one command: green, not dirty, and (with `--require-pass-line`) every
/// expected test of the knives using it is seen passing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Noop {
    Green,
    Red,
    Dirty(Why),
    /// `--require-pass-line`: these expected tests were not seen passing.
    NotSeenPassing(Vec<String>),
}

pub fn decide_noop(r: &Run, require_pass: Option<&[String]>) -> Noop {
    if let Some(w) = dirty(r) {
        return Noop::Dirty(w);
    }
    if r.rc != Some(0) {
        return Noop::Red;
    }
    if let Some(want) = require_pass {
        let passed = passed_names(r.output.as_deref().unwrap_or(""));
        // the same rule as the knife side: an ambiguous short name is not "seen passing"
        // (a9136 #586 L3: `x` must not be satisfied by `b::x` when `a::x` is gone)
        let missing: Vec<String> = want
            .iter()
            .filter(|e| !matches!(find_expected(&passed, e), Hit::One(_)))
            .cloned()
            .collect();
        if !missing.is_empty() {
            return Noop::NotSeenPassing(missing);
        }
    }
    Noop::Green
}

/// How the noop ended, for the exit code (a9136 #586 H1: "skipped" and "failed" are
/// different; a red or dirty noop must never become the weaker-but-passing rc 4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoopResult {
    /// Every noop command ran and was `Noop::Green`.
    Green,
    /// `"noop": false`: nothing ran.
    Skipped,
    /// At least one noop was red, dirty or did not show the expected test passing (or
    /// there was nothing to judge): no knife may count.
    Failed,
}

/// `None` = the spec skipped the noop.
pub fn noop_result(noops: Option<&[Noop]>) -> NoopResult {
    match noops {
        None => NoopResult::Skipped,
        Some(n) if !n.is_empty() && n.iter().all(|x| *x == Noop::Green) => NoopResult::Green,
        Some(_) => NoopResult::Failed,
    }
}

/// The run's exit code: 0 every knife `Bit` and the noop green / 4 every knife `Bit`
/// and the noop skipped / 1 anything else, including a failed noop.
pub fn exit_code(noop: NoopResult, outcomes: &[Outcome]) -> i32 {
    if noop == NoopResult::Failed
        || outcomes.is_empty()
        || !outcomes.iter().all(|o| *o == Outcome::Bit)
    {
        return 1;
    }
    match noop {
        NoopResult::Green => 0,
        NoopResult::Skipped => 4,
        NoopResult::Failed => 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(rc: i32, out: &str) -> Run {
        Run {
            rc: Some(rc),
            timed_out: false,
            leftovers: Leftovers::NoneSeen,
            output: Some(out.to_string()),
        }
    }

    const RED_X: &str = "test m::x ... FAILED\n";

    #[test]
    fn bit_when_red_and_named() {
        let d = decide(1, Some(true), Some(&run(101, RED_X)), "x");
        assert_eq!(d.outcome, Outcome::Bit);
        assert!(!d.stop);
        assert_eq!(d.failed, vec!["m::x".to_string()]);
    }

    #[test]
    fn survived_when_green_even_if_output_names_the_test() {
        assert_eq!(
            decide(1, Some(true), Some(&run(0, RED_X)), "x").outcome,
            Outcome::Survived
        );
    }

    #[test]
    fn survived_when_red_without_the_name() {
        let d = decide(1, Some(true), Some(&run(1, "test m::y ... FAILED\n")), "x");
        assert_eq!(d.outcome, Outcome::Survived);
    }

    #[test]
    fn not_fallen_on_build_failure() {
        let d = decide(
            1,
            Some(true),
            Some(&run(101, "error[E0425]: x\nerror: could not compile `a`\n")),
            "x",
        );
        assert_eq!(d.outcome, Outcome::NotFallen);
    }

    #[test]
    fn bit_wins_over_build_text_in_test_output() {
        let d = decide(
            1,
            Some(true),
            Some(&run(
                101,
                "could not compile (a test's log)\ntest m::x ... FAILED\n",
            )),
            "x",
        );
        assert_eq!(d.outcome, Outcome::Bit);
    }

    #[test]
    fn anchor_missing_runs_nothing_and_does_not_stop() {
        let d = decide(0, None, None, "x");
        assert_eq!(d.outcome, Outcome::AnchorMissing(0));
        assert!(!d.stop);
        assert_eq!(
            decide(2, None, None, "x").outcome,
            Outcome::AnchorMissing(2)
        );
    }

    #[test]
    fn h1_order_restore_mismatch_beats_a_bit_and_stops() {
        let d = decide(1, Some(false), Some(&run(101, RED_X)), "x");
        assert_eq!(d.outcome, Outcome::Undecided(Why::RestoreMismatch));
        assert!(d.stop);
    }

    #[test]
    fn h1_order_dirty_beats_a_bit_and_stops() {
        let mut r = run(101, RED_X);
        r.timed_out = true;
        assert_eq!(
            decide(1, Some(true), Some(&r), "x").outcome,
            Outcome::Undecided(Why::Timeout)
        );
        let mut r = run(101, RED_X);
        r.leftovers = Leftovers::Seen(2);
        let d = decide(1, Some(true), Some(&r), "x");
        assert_eq!(d.outcome, Outcome::Undecided(Why::Leftovers(2)));
        assert!(d.stop);
        let mut r = run(101, RED_X);
        r.leftovers = Leftovers::Unreadable;
        assert_eq!(
            decide(1, Some(true), Some(&r), "x").outcome,
            Outcome::Undecided(Why::ProcessesUnreadable)
        );
        let mut r = run(101, RED_X);
        r.output = None;
        assert_eq!(
            decide(1, Some(true), Some(&r), "x").outcome,
            Outcome::Undecided(Why::OutputUnreadable)
        );
    }

    #[test]
    fn a_signal_kill_counts_as_failed() {
        let r = Run {
            rc: None,
            ..run(0, RED_X)
        };
        assert_eq!(decide(1, Some(true), Some(&r), "x").outcome, Outcome::Bit);
    }

    #[test]
    fn m3_ambiguous_is_undecided_not_a_stop() {
        let d = decide(
            1,
            Some(true),
            Some(&run(1, "test a::x ... FAILED\ntest b::x ... FAILED\n")),
            "x",
        );
        assert_eq!(d.outcome, Outcome::Undecided(Why::Ambiguous));
        assert!(!d.stop);
    }

    #[test]
    fn noop_green_red_dirty() {
        assert_eq!(decide_noop(&run(0, ""), None), Noop::Green);
        assert_eq!(decide_noop(&run(1, ""), None), Noop::Red);
        let mut r = run(0, "");
        r.leftovers = Leftovers::Seen(1);
        assert_eq!(decide_noop(&r, None), Noop::Dirty(Why::Leftovers(1)));
    }

    #[test]
    fn noop_require_pass_line() {
        let want = ["x".to_string(), "z".to_string()];
        let r = run(0, "test m::x ... ok\ntest m::y ... ok\n");
        assert_eq!(
            decide_noop(&r, Some(&want[..])),
            Noop::NotSeenPassing(vec!["z".to_string()])
        );
        let r = run(0, "test m::x ... ok\ntest m::z ... ok\n");
        assert_eq!(decide_noop(&r, Some(&want[..])), Noop::Green);
    }

    #[test]
    fn exit_codes() {
        let g = NoopResult::Green;
        assert_eq!(exit_code(g, &[Outcome::Bit, Outcome::Bit]), 0);
        assert_eq!(exit_code(NoopResult::Skipped, &[Outcome::Bit]), 4);
        assert_eq!(exit_code(g, &[Outcome::Bit, Outcome::Survived]), 1);
        assert_eq!(exit_code(g, &[Outcome::AnchorMissing(0)]), 1);
        assert_eq!(exit_code(g, &[]), 1);
        assert_eq!(exit_code(NoopResult::Skipped, &[]), 1);
    }

    #[test]
    fn h1_a_failed_noop_is_rc_1_never_4_even_when_every_knife_bit() {
        let all_bit = [Outcome::Bit, Outcome::Bit];
        let red = [Noop::Red];
        let dirty = [Noop::Green, Noop::Dirty(Why::Leftovers(1))];
        let unseen = [Noop::NotSeenPassing(vec!["x".to_string()])];
        for n in [&red[..], &dirty[..], &unseen[..], &[][..]] {
            assert_eq!(noop_result(Some(n)), NoopResult::Failed, "{n:?}");
            assert_eq!(exit_code(noop_result(Some(n)), &all_bit), 1, "{n:?}");
        }
        assert_eq!(noop_result(None), NoopResult::Skipped);
        assert_eq!(exit_code(noop_result(None), &all_bit), 4);
        assert_eq!(
            noop_result(Some(&[Noop::Green, Noop::Green][..])),
            NoopResult::Green
        );
        assert_eq!(
            exit_code(noop_result(Some(&[Noop::Green][..])), &all_bit),
            0
        );
    }

    #[test]
    fn l3_require_pass_line_refuses_an_ambiguous_or_other_test() {
        let want = ["x".to_string()];
        let only_b = run(0, "test b::x ... ok\n");
        assert_eq!(decide_noop(&only_b, Some(&want[..])), Noop::Green); // one x, unambiguous
        let two = run(0, "test a::x ... ok\ntest b::x ... ok\n");
        assert_eq!(
            decide_noop(&two, Some(&want[..])),
            Noop::NotSeenPassing(vec!["x".to_string()])
        );
        let want_a = ["a::x".to_string()];
        assert_eq!(
            decide_noop(&only_b, Some(&want_a[..])),
            Noop::NotSeenPassing(vec!["a::x".to_string()])
        );
        assert_eq!(decide_noop(&two, Some(&want_a[..])), Noop::Green);
    }

    #[test]
    fn display_tags() {
        assert_eq!(Outcome::Bit.to_string(), "BIT");
        assert_eq!(Outcome::NotFallen.to_string(), "NOT-FALLEN");
        assert!(Outcome::AnchorMissing(2).to_string().contains("2 times"));
        assert!(Outcome::Undecided(Why::Timeout)
            .to_string()
            .starts_with("UNDECIDED"));
    }
}
