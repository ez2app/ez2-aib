//! This machine's CPU load, read natively (S57, Andy 11-14: 「我們沒有要使用 PowerShell」).
//!
//! `GET /api/v1/host/load` and `aibd ctl load --json` both come here. The lease / hosts
//! probes used to ssh a PowerShell one-liner to the Windows laptop; they now ssh
//! `aibd ctl load --json` instead, so no shell, PowerShell, wmic or `cmd /c` is involved on
//! any platform: sysinfo reads the counters itself (Windows: PDH / GetSystemTimes, macOS:
//! host_processor_info, Linux: /proc/stat).
//!
//! One sample takes two reads [`sysinfo::MINIMUM_CPU_UPDATE_INTERVAL`] apart (about 200 ms).
//! Callers within [`CACHE_FOR`] of a sample get that same sample, with `sampled_at` saying
//! when it was taken (519d0 11-14 08:16): a page polling the route cannot keep a core busy.
//! Nothing is made up: no core count ⇒ an error, not a guess.

use serde::Serialize;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// How long one sample is handed out again.
pub const CACHE_FOR: Duration = Duration::from_secs(1);

/// The marker the remote probes look for (`{"aib_load":1,…}`): a version, so a later
/// shape can be told apart.
pub const WIRE_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Load {
    /// Whole-machine CPU use over the sample, 0–100.
    pub cpu_pct: f64,
    /// Logical CPUs (what a load average is divided by).
    pub logical_cpus: u32,
    /// Unix seconds when the sample was taken.
    pub sampled_at: u64,
    /// How long the sample ran.
    pub sample_ms: u64,
}

static LAST: Mutex<Option<(Load, Instant)>> = Mutex::new(None);

/// A fresh sample, or the one taken less than [`CACHE_FOR`] ago. Blocks about 200 ms when it
/// samples: call it from a blocking thread.
pub fn read() -> Result<Load, String> {
    // a poisoned lock only means a sampler panicked; the cached value is still a value
    let mut last = LAST.lock().unwrap_or_else(|p| p.into_inner());
    if let Some((l, at)) = *last {
        if at.elapsed() < CACHE_FOR {
            return Ok(l);
        }
    }
    let l = sample()?;
    *last = Some((l, Instant::now()));
    Ok(l)
}

fn sample() -> Result<Load, String> {
    let started = Instant::now();
    let mut sys = sysinfo::System::new();
    sys.refresh_cpu_usage();
    std::thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
    sys.refresh_cpu_usage();
    let n = sys.cpus().len();
    let cpus = if n > 0 {
        n
    } else {
        std::thread::available_parallelism()
            .map(usize::from)
            .map_err(|e| format!("cannot count CPUs: {e}"))?
    };
    let pct = f64::from(sys.global_cpu_usage());
    check(
        pct,
        cpus,
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| format!("clock: {e}"))?
            .as_secs(),
        u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
    )
}

/// The checks a sample must pass (split out so they are tested without a real read).
pub fn check(pct: f64, cpus: usize, sampled_at: u64, sample_ms: u64) -> Result<Load, String> {
    if !pct.is_finite() || !(0.0..=100.0).contains(&pct) {
        return Err(format!("CPU use out of range: {pct}"));
    }
    let logical_cpus = u32::try_from(cpus)
        .ok()
        .filter(|n| *n > 0)
        .ok_or_else(|| format!("CPU count out of range: {cpus}"))?;
    Ok(Load {
        cpu_pct: (pct * 10.0).round() / 10.0,
        logical_cpus,
        sampled_at,
        sample_ms,
    })
}

/// The one line `aibd ctl load --json` prints: `{"aib_load":1,"cpu_pct":…,…}`.
pub fn wire_line(l: &Load) -> String {
    serde_json::json!({
        "aib_load": WIRE_VERSION,
        "cpu_pct": l.cpu_pct,
        "logical_cpus": l.logical_cpus,
        "sampled_at": l.sampled_at,
        "sample_ms": l.sample_ms,
    })
    .to_string()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn a_real_sample_is_in_range_and_cached_for_a_second() {
        let a = read().unwrap();
        assert!((0.0..=100.0).contains(&a.cpu_pct), "{a:?}");
        assert!(a.logical_cpus > 0, "{a:?}");
        assert!(a.sampled_at > 1_700_000_000, "{a:?}");
        let t = Instant::now();
        let b = read().unwrap();
        // the second call within the second is the same sample, not a new 200 ms read
        assert_eq!(a, b);
        assert!(
            t.elapsed() < Duration::from_millis(100),
            "{:?}",
            t.elapsed()
        );
    }

    #[test]
    fn out_of_range_values_are_refused_not_clamped() {
        assert!(check(f64::NAN, 4, 1, 1).is_err());
        assert!(check(100.5, 4, 1, 1).is_err());
        assert!(check(-0.1, 4, 1, 1).is_err());
        assert!(check(50.0, 0, 1, 1).is_err());
        let l = check(37.26, 8, 1_778_300_000, 201).unwrap();
        assert_eq!(l.cpu_pct, 37.3);
        let v: serde_json::Value = serde_json::from_str(&wire_line(&l)).unwrap();
        assert_eq!(
            v,
            serde_json::json!({"aib_load": 1, "cpu_pct": 37.3, "logical_cpus": 8,
                "sampled_at": 1_778_300_000u64, "sample_ms": 201})
        );
    }
}
