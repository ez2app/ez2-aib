//! Estimates from recent samples (S73-ready §2, IFACE-mergecheck-ready-7b81e-1117 r2 §0): one
//! median shared by `aib-mergecheck ready` and S74a's queue table, so the two never disagree.
//!
//! Pure: the caller reads the samples (CI jobs, receipt runs) and passes them in.

use alloc::vec::Vec;

/// Fewer samples than this ⇒ no estimate ("未知(樣本 n 次)").
pub const MIN_SAMPLES: usize = 3;

/// One estimate: `secs` is `None` when there were too few samples to say.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Estimate {
    pub secs: Option<u64>,
    /// Samples the estimate used (after `take`).
    pub n: usize,
    /// When the newest sample used was taken (ms since the epoch).
    pub last_at_ms: Option<i64>,
}

/// The median of the newest `take` samples `(at_ms, secs)`; an even count takes the lower
/// middle (a whole number of seconds, never a half). Fewer than [`MIN_SAMPLES`] ⇒ `secs: None`.
pub fn median_recent(samples: &[(i64, u64)], take: usize) -> Estimate {
    let mut s: Vec<(i64, u64)> = samples.to_vec();
    // newest first; same time ⇒ larger value first, so the order never depends on the input order
    s.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    s.truncate(take);
    let n = s.len();
    let last_at_ms = s.first().map(|x| x.0);
    if n < MIN_SAMPLES {
        return Estimate {
            secs: None,
            n,
            last_at_ms,
        };
    }
    let mut v: Vec<u64> = s.iter().map(|x| x.1).collect();
    v.sort_unstable();
    Estimate {
        secs: Some(v[(n - 1) / 2]),
        n,
        last_at_ms,
    }
}

/// How much longer a run that has gone `elapsed` seconds will take: the median of the
/// newest `take` samples longer than `elapsed`, minus `elapsed` (REQ-merge-ready r3 §2: the
/// time left is conditional on what already ran, not "median − elapsed" over every sample).
/// A run past every sample has no estimate (`secs: None`, `n` = 0 samples longer).
pub fn remaining(samples: &[(i64, u64)], take: usize, elapsed: u64) -> Estimate {
    let mut s: Vec<(i64, u64)> = samples.to_vec();
    s.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    s.truncate(take);
    let longer: Vec<(i64, u64)> = s.into_iter().filter(|x| x.1 > elapsed).collect();
    let e = median_recent(&longer, take);
    Estimate {
        secs: e.secs.map(|m| m - elapsed),
        ..e
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_samples_are_unknown_and_three_give_the_median() {
        let e = median_recent(&[(1, 600), (2, 900)], 10);
        assert_eq!((e.secs, e.n, e.last_at_ms), (None, 2, Some(2)));
        let e = median_recent(&[(1, 600), (2, 900), (3, 300)], 10);
        assert_eq!((e.secs, e.n, e.last_at_ms), (Some(600), 3, Some(3)));
    }

    #[test]
    fn only_the_newest_take_samples_count() {
        // the two oldest (huge) samples fall outside take = 3
        let s = [(1, 9_000), (2, 9_000), (3, 100), (4, 200), (5, 300)];
        assert_eq!(median_recent(&s, 3).secs, Some(200));
    }

    #[test]
    fn an_even_count_takes_the_lower_middle() {
        assert_eq!(
            median_recent(&[(1, 10), (2, 20), (3, 30), (4, 40)], 10).secs,
            Some(20)
        );
    }

    #[test]
    fn the_remaining_time_is_conditional_on_elapsed() {
        // 10 / 20 / 30 / 40 minutes, 15 already run: the runs longer than 15 are 20 / 30 / 40,
        // median 30 ⇒ 15 left (the unconditional median − elapsed would say 5 or 10)
        let s = [(1, 600), (2, 1_200), (3, 1_800), (4, 2_400)];
        let e = remaining(&s, 10, 900);
        assert_eq!((e.secs, e.n), (Some(900), 3));
    }

    #[test]
    fn a_run_past_every_sample_has_no_estimate() {
        let s = [(1, 600), (2, 700), (3, 800)];
        let e = remaining(&s, 10, 900);
        assert_eq!((e.secs, e.n), (None, 0));
    }
}
