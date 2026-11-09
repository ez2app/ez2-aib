//! Pure thermal-protection decisions for aibd.
//!
//! Rust replacement for the decision half of 1.x `aib/src/cockpit/cooldown.py`
//! (closed PR #473, head 9a3812eb4; `docs/migration/legacy-inventory.md`
//! "to replace" R1). This crate never touches the OS: sampling lives in
//! `aib-sys`, history persistence in `aib-store`, and acting (reaping aibd's
//! own process tree) in `aibd`. Everything here takes injected samples and an
//! injected clock, so the acceptance cases run without a hot machine.
//!
//! Rules carried over from 1.x:
//! - an instantaneous spike never reaps; reaping needs the average CPU
//!   temperature ≥ [`REAP_AVG_C`] held for [`SUSTAIN_S`] seconds over at least
//!   [`SUSTAIN_MIN_SAMPLES`] samples with no gap longer than [`SUSTAIN_GAP_S`];
//! - non-zero OS thermal pressure reaps immediately;
//! - sustained ≥ [`NOGO_AVG_C`] only blocks new heavy work (no reap);
//! - when no sustained value can be computed (no history, or pressure is
//!   unreadable and samples are not continuous) fall back to the instantaneous
//!   reading — prefer reaping over losing protection;
//! - load average never grants reap authority (it is not an input here).
//!
//! Times are seconds on a monotonic clock chosen by the caller.

#![forbid(unsafe_code)]

/// Instantaneous CPU temperature levels (°C), same as 1.x.
pub const CPU_WARN_C: f64 = 75.0;
pub const CPU_HOT_C: f64 = 85.0;
pub const CPU_CRIT_C: f64 = 90.0;
/// Sustained average at or above this reaps.
pub const REAP_AVG_C: f64 = 88.0;
/// Sustained average at or above this blocks new heavy work.
pub const NOGO_AVG_C: f64 = 85.0;
/// How long the average must be held.
pub const SUSTAIN_S: f64 = 60.0;
/// Two samples further apart than this are not continuous.
pub const SUSTAIN_GAP_S: f64 = 90.0;
/// Minimum distinct samples for a streak to count.
pub const SUSTAIN_MIN_SAMPLES: usize = 3;
/// Samples closer than this count as one moment.
pub const SAMPLE_SPACING_S: f64 = 5.0;
/// History older than this is dropped.
pub const HISTORY_KEEP_S: f64 = 300.0;

/// Ordered heat level. `Unknown` means the temperature was not measured.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Ok,
    Warn,
    Hot,
    Crit,
    Unknown,
}

/// Why a decision came out the way it did (stable codes for logs / comparison
/// with 1.x `why`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Why {
    /// OS thermal pressure is non-zero.
    Pressure,
    /// CPU temperature not measured.
    Unmeasured,
    /// No usable history (missing, corrupt, or could not be stored).
    NoHistory,
    /// Pressure unreadable and samples not continuous.
    NoSignal,
    /// Average held above a limit for [`SUSTAIN_S`].
    Sustained,
    /// Nothing sustained.
    Below,
}

impl Why {
    /// Same spelling as 1.x so fixture comparison can match strings.
    pub fn code(self) -> &'static str {
        match self {
            Why::Pressure => "pressure",
            Why::Unmeasured => "unmeasured",
            Why::NoHistory => "no_history",
            Why::NoSignal => "no_signal",
            Why::Sustained => "sustained",
            Why::Below => "below",
        }
    }
}

/// One reading. `max` is the hottest sensor; `avg` the mean when available.
/// Non-finite values are treated as not measured.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Reading {
    pub max: Option<f64>,
    pub avg: Option<f64>,
}

impl Reading {
    fn max(&self) -> Option<f64> {
        self.max.filter(|v| v.is_finite())
    }
    /// Value used for the sustained check: average, else max (1.x A8).
    pub fn value(&self) -> Option<f64> {
        let max = self.max()?;
        Some(self.avg.filter(|v| v.is_finite()).unwrap_or(max))
    }
}

/// Result of [`decide`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Decision {
    pub level: Level,
    pub why: Why,
    /// Seconds held, only for [`Why::Sustained`].
    pub held_s: Option<f64>,
}

impl Decision {
    /// Only critical grants reap authority.
    pub fn reap(&self) -> bool {
        self.level == Level::Crit
    }
    /// Hot or critical blocks new heavy work.
    pub fn nogo(&self) -> bool {
        matches!(self.level, Level::Hot | Level::Crit)
    }
}

/// History could not be built from stored rows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Corrupt;

/// Time-sorted `(at, value)` samples.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct History {
    rows: Vec<(f64, f64)>,
}

impl History {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build from stored rows. Any non-finite time or value ⇒ [`Corrupt`]
    /// (1.x A17b); the caller then treats it as no history and starts over.
    pub fn from_rows(rows: &[(f64, f64)]) -> Result<Self, Corrupt> {
        if rows.iter().any(|(t, v)| !t.is_finite() || !v.is_finite()) {
            return Err(Corrupt);
        }
        let mut rows = rows.to_vec();
        rows.sort_by(|a, b| a.0.total_cmp(&b.0));
        Ok(Self { rows })
    }

    pub fn rows(&self) -> &[(f64, f64)] {
        &self.rows
    }

    /// Add a sample at `now`. Drops rows older than [`HISTORY_KEEP_S`] but
    /// keeps rows up to [`SUSTAIN_GAP_S`] *newer* than `now`: a slow writer
    /// must not discard someone else's newer low sample (1.x A14c).
    /// Non-finite values are ignored.
    pub fn record(&mut self, now: f64, value: f64) {
        if !now.is_finite() || !value.is_finite() {
            return;
        }
        self.rows.retain(|(t, _)| {
            let age = now - t;
            (-SUSTAIN_GAP_S..=HISTORY_KEEP_S).contains(&age)
        });
        self.rows.push((now, value));
        self.rows.sort_by(|a, b| a.0.total_cmp(&b.0));
    }

    /// Seconds the newest streak at or above `limit` has lasted; 0 if it is
    /// broken by a low sample, a gap over [`SUSTAIN_GAP_S`], or has fewer than
    /// [`SUSTAIN_MIN_SAMPLES`] distinct moments (1.x A4, A16, A16b).
    pub fn held_for(&self, limit: f64) -> f64 {
        let Some(&(t_end, v_end)) = self.rows.last() else {
            return 0.0;
        };
        if v_end < limit {
            return 0.0;
        }
        let (mut t_prev, mut t_counted, mut n) = (t_end, t_end, 1usize);
        for &(t, v) in self.rows.iter().rev().skip(1) {
            if v < limit || t_prev - t > SUSTAIN_GAP_S {
                break;
            }
            t_prev = t;
            if t_counted - t >= SAMPLE_SPACING_S {
                t_counted = t;
                n += 1;
            }
        }
        if n >= SUSTAIN_MIN_SAMPLES {
            t_end - t_prev
        } else {
            0.0
        }
    }

    /// The newest two samples are no more than [`SUSTAIN_GAP_S`] apart.
    fn continuous(&self) -> bool {
        match self.rows.as_slice() {
            [.., (a, _), (b, _)] => b - a <= SUSTAIN_GAP_S,
            _ => false,
        }
    }
}

/// Level from the instantaneous hottest sensor.
pub fn instant_level(max: f64) -> Level {
    if max >= CPU_CRIT_C {
        Level::Crit
    } else if max >= CPU_HOT_C {
        Level::Hot
    } else if max >= CPU_WARN_C {
        Level::Warn
    } else {
        Level::Ok
    }
}

/// Fallback when no sustained value can be computed: instantaneous level, but
/// an average already at the reap limit still reaps (1.x A18).
fn instant(max: f64, value: f64, why: Why) -> Decision {
    let mut level = instant_level(max);
    if value >= REAP_AVG_C {
        level = Level::Crit;
    }
    Decision {
        level,
        why,
        held_s: None,
    }
}

/// Decide heat authority.
///
/// - `reading`: this round's reading.
/// - `history`: history **after** recording this round's value, or `None`
///   when it could not be loaded / stored (corrupt, store busy).
/// - `pressure`: OS thermal pressure level; `None` = could not read.
pub fn decide(reading: Reading, history: Option<&History>, pressure: Option<i64>) -> Decision {
    if let Some(p) = pressure {
        if p != 0 {
            return Decision {
                level: Level::Crit,
                why: Why::Pressure,
                held_s: None,
            };
        }
    }
    let (Some(max), Some(value)) = (reading.max(), reading.value()) else {
        return Decision {
            level: Level::Unknown,
            why: Why::Unmeasured,
            held_s: None,
        };
    };
    let Some(history) = history else {
        return instant(max, value, Why::NoHistory);
    };
    if pressure.is_none() && !history.continuous() {
        return instant(max, value, Why::NoSignal);
    }
    for (level, limit) in [(Level::Crit, REAP_AVG_C), (Level::Hot, NOGO_AVG_C)] {
        let held = history.held_for(limit);
        if held >= SUSTAIN_S {
            return Decision {
                level,
                why: Why::Sustained,
                held_s: Some(held),
            };
        }
    }
    Decision {
        level: if value >= CPU_WARN_C {
            Level::Warn
        } else {
            Level::Ok
        },
        why: Why::Below,
        held_s: None,
    }
}

/// Convenience: record this round into `history` (if any) and decide.
pub fn step(
    history: Option<&mut History>,
    now: f64,
    reading: Reading,
    pressure: Option<i64>,
) -> Decision {
    match history {
        Some(h) => {
            if let Some(v) = reading.value() {
                h.record(now, v);
            }
            decide(reading, Some(h), pressure)
        }
        None => decide(reading, None, pressure),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    //! Case names follow 1.x `test-cooldown-reap-authority-34d15-1103.py`.
    use super::*;

    fn r(max: f64, avg: Option<f64>) -> Reading {
        Reading {
            max: Some(max),
            avg,
        }
    }

    /// Mirrors the 1.x `Base.step` harness: clock advances by `dt`,
    /// default pressure 0.
    struct Sim {
        t: f64,
        h: History,
        pressure: Option<i64>,
        reaps: Vec<Why>,
        nogo: bool,
    }

    impl Sim {
        fn new() -> Self {
            Self {
                t: 1_000_000.0,
                h: History::new(),
                pressure: Some(0),
                reaps: vec![],
                nogo: false,
            }
        }
        fn step(&mut self, max: f64, avg: Option<f64>, dt: f64) -> Decision {
            self.t += dt;
            let d = step(Some(&mut self.h), self.t, r(max, avg), self.pressure);
            self.after(d)
        }
        fn step_no_history(&mut self, max: f64, avg: Option<f64>) -> Decision {
            self.t += 20.0;
            let d = step(None, self.t, r(max, avg), self.pressure);
            self.after(d)
        }
        fn after(&mut self, d: Decision) -> Decision {
            if d.reap() {
                self.reaps.push(d.why);
            }
            if d.nogo() {
                self.nogo = true;
            }
            d
        }
    }

    #[test]
    fn a1_spike_not_reaped() {
        let mut s = Sim::new();
        for _ in 0..6 {
            s.step(91.9, Some(80.0), 20.0);
        }
        assert_eq!(
            instant_level(91.9),
            Level::Crit,
            "panel still shows the spike"
        );
        assert!(s.reaps.is_empty());
        assert!(!s.nogo);
    }

    #[test]
    fn a2_sustained_reaped_with_reason() {
        let mut s = Sim::new();
        for _ in 0..4 {
            s.step(95.0, Some(91.0), 20.0);
        }
        assert_eq!(s.reaps, vec![Why::Sustained]);
        assert!(s.nogo);
    }

    #[test]
    fn a3_short_not_reaped() {
        let mut s = Sim::new();
        for _ in 0..2 {
            s.step(95.0, Some(91.0), 15.0);
        }
        assert!(s.reaps.is_empty());
    }

    #[test]
    fn a4_broken_streak_restarts() {
        let mut s = Sim::new();
        for a in [91.0, 91.0, 70.0, 91.0, 91.0] {
            s.step(95.0, Some(a), 20.0);
        }
        assert!(s.reaps.is_empty(), "dropped in the middle");
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S + 1.0);
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S + 1.0);
        assert!(s.reaps.is_empty(), "gaps too long are not continuous");
    }

    #[test]
    fn a5_pressure_reaps_now() {
        let mut s = Sim::new();
        s.pressure = Some(2);
        s.step(60.0, Some(55.0), 20.0);
        assert_eq!(s.reaps, vec![Why::Pressure]);
    }

    #[test]
    fn a6_pressure_zero_or_none() {
        let mut s = Sim::new();
        for p in [Some(0), None] {
            s.pressure = p;
            s.step(60.0, Some(55.0), 20.0);
        }
        assert!(s.reaps.is_empty());
    }

    #[test]
    fn a7_no_history_falls_back_to_instant() {
        let mut s = Sim::new();
        s.step_no_history(91.0, Some(80.0));
        assert_eq!(s.reaps, vec![Why::NoHistory]);
    }

    #[test]
    fn a8_no_avg_uses_max() {
        let mut s = Sim::new();
        for _ in 0..4 {
            s.step(91.0, None, 20.0);
        }
        assert_eq!(s.reaps.len(), 1);
    }

    #[test]
    fn a9_nogo_at_85_without_reap() {
        let mut s = Sim::new();
        let mut d = None;
        for _ in 0..4 {
            d = Some(s.step(88.0, Some(86.0), 20.0));
        }
        assert!(s.reaps.is_empty());
        assert!(s.nogo);
        assert_eq!(d.unwrap().level, Level::Hot);
    }

    #[test]
    fn a11_load_never_reaps() {
        // Load is not an input to `decide`; cool CPU never reaps whatever the load.
        let mut s = Sim::new();
        for _ in 0..4 {
            s.step(60.0, Some(55.0), 20.0);
        }
        assert!(s.reaps.is_empty());
    }

    #[test]
    fn a14c_slow_writer_keeps_newer_low_sample() {
        let mut h = History::new();
        h.record(1000.0, 91.0);
        h.record(1040.0, 70.0);
        h.record(1020.0, 91.0);
        let vals: Vec<f64> = h.rows().iter().map(|r| r.1).collect();
        assert_eq!(vals, vec![91.0, 91.0, 70.0]);
        assert_eq!(h.held_for(REAP_AVG_C), 0.0);
    }

    #[test]
    fn a15_gap_without_pressure_falls_back() {
        let mut s = Sim::new();
        s.pressure = None;
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S + 1.0);
        assert_eq!(s.reaps, vec![Why::NoSignal]);
    }

    #[test]
    fn a15b_gap_with_pressure_zero_restarts() {
        let mut s = Sim::new();
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S + 1.0);
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S + 1.0);
        assert!(s.reaps.is_empty());
    }

    #[test]
    fn a16_two_samples_across_pause_not_sustained() {
        let mut s = Sim::new();
        s.step(95.0, Some(91.0), 20.0);
        s.step(95.0, Some(91.0), SUSTAIN_GAP_S - 1.0);
        assert!(
            s.reaps.is_empty(),
            "the gap between two samples is not evidence"
        );
        s.step(95.0, Some(91.0), 30.0);
        assert_eq!(s.reaps.len(), 1);
    }

    #[test]
    fn a16b_same_moment_panels_are_one_sample() {
        let h = History::from_rows(&[(0.0, 91.0), (89.0, 91.0), (89.3, 91.0)]).unwrap();
        assert_eq!(h.held_for(REAP_AVG_C), 0.0);
        let h =
            History::from_rows(&[(0.0, 91.0), (89.0, 91.0), (89.3, 91.0), (119.0, 91.0)]).unwrap();
        assert_eq!(h.held_for(REAP_AVG_C), 119.0);
    }

    #[test]
    fn a17b_nan_or_infinite_is_corrupt() {
        assert_eq!(
            History::from_rows(&[(0.0, 91.0), (30.0, f64::NAN), (60.0, 91.0)]),
            Err(Corrupt)
        );
        assert_eq!(History::from_rows(&[(0.0, f64::INFINITY)]), Err(Corrupt));
        assert_eq!(History::from_rows(&[(f64::NAN, 91.0)]), Err(Corrupt));
    }

    #[test]
    fn a18_fallback_honours_avg() {
        let mut s = Sim::new();
        s.step_no_history(89.0, Some(88.5));
        assert_eq!(s.reaps.len(), 1);
    }

    /// #615 33e28 L1: pins today's 1.x behaviour on the fallback path. No
    /// pressure and only one sample (first round after start / a pause):
    /// a lone spike (max 95, avg 80) reaps via NoSignal. Changing this is a
    /// behaviour change for the PR 2 / PR 3 DECISION, not a silent edit.
    #[test]
    fn l1_fallback_lone_spike_reaps_like_1x() {
        let mut s = Sim::new();
        s.pressure = None;
        s.step(95.0, Some(80.0), 20.0);
        assert_eq!(s.reaps, vec![Why::NoSignal]);
    }

    /// #615 33e28 L2: the Linux steady state (pressure never readable). Once
    /// samples are continuous, a spike with a low average is not reaped; only
    /// the first round (no pair yet) takes the fallback.
    #[test]
    fn l2_no_pressure_continuous_spike_not_reaped() {
        let mut s = Sim::new();
        s.pressure = None;
        for _ in 0..6 {
            s.step(91.9, Some(80.0), 20.0);
        }
        assert_eq!(s.reaps, vec![Why::NoSignal]);
    }

    #[test]
    fn unmeasured_is_unknown_and_never_reaps() {
        let d = decide(
            Reading {
                max: None,
                avg: Some(95.0),
            },
            None,
            Some(0),
        );
        assert_eq!(
            (d.level, d.why, d.reap()),
            (Level::Unknown, Why::Unmeasured, false)
        );
        let d = decide(
            Reading {
                max: Some(f64::NAN),
                avg: None,
            },
            None,
            None,
        );
        assert_eq!(d.level, Level::Unknown);
    }

    #[test]
    fn pressure_beats_unmeasured() {
        let d = decide(
            Reading {
                max: None,
                avg: None,
            },
            None,
            Some(1),
        );
        assert_eq!((d.level, d.why), (Level::Crit, Why::Pressure));
    }

    #[test]
    fn history_drops_old_rows() {
        let mut h = History::new();
        h.record(0.0, 80.0);
        h.record(HISTORY_KEEP_S + 1.0, 80.0);
        assert_eq!(h.rows().len(), 1);
    }

    #[test]
    fn why_codes_match_1x() {
        let codes: Vec<&str> = [
            Why::Pressure,
            Why::Unmeasured,
            Why::NoHistory,
            Why::NoSignal,
            Why::Sustained,
            Why::Below,
        ]
        .iter()
        .map(|w| w.code())
        .collect();
        assert_eq!(
            codes,
            [
                "pressure",
                "unmeasured",
                "no_history",
                "no_signal",
                "sustained",
                "below"
            ]
        );
    }
}
