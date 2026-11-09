//! T1 / T2: what thermal protection reads from the OS (R1, DECISION-r1-thermal-c477a-1110
//! "追加 2"; API list v0.4).
//!
//! | # | call | grade |
//! |---|---|---|
//! | T1 | `IOHIDEventSystemClient` services matching `PrimaryUsagePage 0xFF00` / `PrimaryUsage 5`, temperature events of the `PMU tdie*` sensors | **private SPI** (IOKit, no public header); symbols looked up at run time, may disappear |
//! | T2 | `notify_register_check` / `notify_get_state` / `notify_cancel` on `com.apple.system.thermalpressurelevel` | **public** (`notify.h`, `OSThermalNotification.h`) |
//!
//! Unlike M1, a wrong T1 reading **causes an action** (aibd stops its own work), so:
//!
//! - A value that cannot be read, or any `PMU tdie` reading outside `0 < t < 150 °C` (or not
//!   finite), is an error ([`SysError::Implausible`] / [`SysError::Unknown`]), never 0 and
//!   never a guess. 1.x dropped such a reading and averaged the rest; here the whole read
//!   fails, so one broken sensor cannot make a hot die look cool.
//! - No `PMU tdie` sensor at all (Intel, a future model) is [`SysError::Unsupported`], never
//!   an empty set read as "cool".
//! - A private symbol that cannot be found is [`SysError::SpiMissing`]; nothing private is
//!   linked statically, so a macOS that removes it still loads aibd.
//! - [`selfcheck_t1`] runs once at start-up (like M1's [`crate::selfcheck`]); if it fails the
//!   caller switches T1 off until restart, keeps T2, and records the tag in metrics.
//!
//! T2 values are the `OSThermalPressureLevel` enum, `0..=4`; anything else is
//! [`SysError::Implausible`] (a non-zero level reaps at once, so a garbage value must not pass).
//!
//! The walk over the HID services is written once, against [`HidApi`], so the ownership rule
//! (release exactly what a Create / Copy call returned, never what a Get call returned) is
//! tested without the OS by counting releases.

use crate::SysError;

/// Sensor name prefix kept by 1.x `rulers.py` (`read_cpu_die_temps`).
pub const TDIE_PREFIX: &str = "PMU tdie";
/// Plausible range, exclusive at both ends (1.x `0.0 < t < 150.0`).
pub const T_MIN_C: f64 = 0.0;
pub const T_MAX_C: f64 = 150.0;
/// Highest `OSThermalPressureLevel` (`kOSThermalPressureLevelSleeping`).
pub const PRESSURE_MAX: i64 = 4;

/// T1 result: the CPU die sensors this round.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DieTemps {
    /// Hottest sensor, °C.
    pub max: f64,
    /// Arithmetic mean over the sensors, °C.
    pub avg: f64,
    /// Number of distinct sensors (≥ 1).
    pub n: usize,
}

/// T1 and T2 behind one interface (real: `Macos` on macOS, [`NoThermal`] elsewhere; tests:
/// [`crate::fake::FakeThermal`]).
pub trait ThermalInfo: Send + Sync {
    /// T1. Synchronous; a caller on an async runtime runs it on a blocking thread.
    fn die_temps(&self) -> Result<DieTemps, SysError>;
    /// T2: `0` = nominal, `1..=4` = the system is already throttling.
    fn pressure(&self) -> Result<i64, SysError>;
}

/// Every platform without thermal protection (Windows, Linux): both calls are
/// [`SysError::Unsupported`]; the caller must say "no thermal protection".
#[derive(Debug, Default, Clone, Copy)]
pub struct NoThermal;

impl ThermalInfo for NoThermal {
    fn die_temps(&self) -> Result<DieTemps, SysError> {
        Err(SysError::Unsupported)
    }
    fn pressure(&self) -> Result<i64, SysError> {
        Err(SysError::Unsupported)
    }
}

/// Keep the `PMU tdie*` readings, one per sensor name (HID lists the same sensor twice; the
/// first wins, as in 1.x), and summarise them. Any kept reading outside the plausible range
/// fails the whole read.
pub fn summarize<'a>(
    readings: impl IntoIterator<Item = (&'a str, f64)>,
) -> Result<DieTemps, SysError> {
    let mut seen: Vec<&str> = Vec::new();
    let mut sum = 0.0;
    let mut max = f64::NEG_INFINITY;
    for (name, t) in readings {
        if !name.starts_with(TDIE_PREFIX) || seen.contains(&name) {
            continue;
        }
        if !(t.is_finite() && t > T_MIN_C && t < T_MAX_C) {
            return Err(SysError::Implausible);
        }
        seen.push(name);
        sum += t;
        if t > max {
            max = t;
        }
    }
    if seen.is_empty() {
        return Err(SysError::Unsupported);
    }
    let n = seen.len();
    Ok(DieTemps {
        max,
        avg: sum / n as f64,
        n,
    })
}

/// Check a raw T2 state.
pub fn pressure_level(raw: u64) -> Result<i64, SysError> {
    match i64::try_from(raw) {
        Ok(v) if v <= PRESSURE_MAX => Ok(v),
        _ => Err(SysError::Implausible),
    }
}

/// Start-up check for T1: one read must succeed. On `Err` the caller turns T1 off until
/// restart (T2 stays), and records [`SysError::tag`] in metrics.
pub fn selfcheck_t1(info: &dyn ThermalInfo) -> Result<DieTemps, SysError> {
    info.die_temps()
}

/// The IOHID / CoreFoundation operations the T1 walk needs. Every method that returns a
/// reference says which CF rule it follows; the walk releases **only** Create / Copy results.
pub trait HidApi {
    /// An opaque CF object reference.
    type Ref: Copy + PartialEq + std::fmt::Debug;
    /// The `{PrimaryUsagePage: 0xFF00, PrimaryUsage: 5}` dictionary. **Create** rule.
    fn matching(&self) -> Result<Self::Ref, SysError>;
    /// `IOHIDEventSystemClientCreate`. **Create** rule.
    fn client(&self) -> Result<Self::Ref, SysError>;
    /// `IOHIDEventSystemClientSetMatching`; neither argument changes owner.
    fn set_matching(&self, client: Self::Ref, matching: Self::Ref);
    /// `IOHIDEventSystemClientCopyServices`. **Copy** rule; `None` = no array.
    fn services(&self, client: Self::Ref) -> Option<Self::Ref>;
    /// `CFArrayGetCount`.
    fn count(&self, array: Self::Ref) -> usize;
    /// `CFArrayGetValueAtIndex`. **Get** rule: never released by the walk.
    fn at(&self, array: Self::Ref, i: usize) -> Option<Self::Ref>;
    /// `IOHIDServiceClientCopyProperty(service, "Product")`. **Copy** rule. Returns the
    /// object (to release) and its text when it is a CFString that converts to UTF-8.
    fn product(&self, service: Self::Ref) -> Option<(Self::Ref, Option<String>)>;
    /// `IOHIDServiceClientCopyEvent(service, temperature, 0, 0)`. **Copy** rule.
    fn event(&self, service: Self::Ref) -> Option<Self::Ref>;
    /// `IOHIDEventGetFloatValue(event, temperature level field)`.
    fn celsius(&self, event: Self::Ref) -> f64;
    /// `CFRelease`.
    fn release(&self, r: Self::Ref);
}

/// A Create / Copy result; released exactly once, on drop.
struct Owned<'a, A: HidApi> {
    api: &'a A,
    r: A::Ref,
}

impl<'a, A: HidApi> Owned<'a, A> {
    fn new(api: &'a A, r: A::Ref) -> Self {
        Self { api, r }
    }
}

impl<A: HidApi> Drop for Owned<'_, A> {
    fn drop(&mut self) {
        self.api.release(self.r);
    }
}

/// Read the `PMU tdie*` sensors through `api` and summarise them ([`summarize`]).
pub fn read_die_temps<A: HidApi>(api: &A) -> Result<DieTemps, SysError> {
    let matching = Owned::new(api, api.matching()?);
    let client = Owned::new(api, api.client()?);
    api.set_matching(client.r, matching.r);
    let Some(services) = api.services(client.r) else {
        return Err(SysError::Unsupported);
    };
    let services = Owned::new(api, services);
    let mut readings: Vec<(String, f64)> = Vec::new();
    for i in 0..api.count(services.r) {
        // Get rule: borrowed from the array, which `services` keeps alive.
        let Some(service) = api.at(services.r, i) else {
            continue;
        };
        let name = match api.product(service) {
            Some((obj, text)) => {
                let _obj = Owned::new(api, obj);
                text
            }
            None => None,
        };
        let Some(name) = name else { continue };
        if !name.starts_with(TDIE_PREFIX) {
            continue;
        }
        let Some(event) = api.event(service) else {
            continue;
        };
        let event = Owned::new(api, event);
        readings.push((name, api.celsius(event.r)));
    }
    summarize(readings.iter().map(|(n, t)| (n.as_str(), *t)))
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::BTreeMap;

    #[test]
    fn t1_summary_is_max_mean_and_count() {
        let d = summarize([
            ("PMU tdie1", 50.0),
            ("PMU tdie2", 70.0),
            ("PMU tdev1", 99.0),
        ])
        .unwrap();
        assert_eq!(d.n, 2);
        assert_eq!(d.max, 70.0);
        assert_eq!(d.avg, 60.0);
    }

    #[test]
    fn t1_same_sensor_twice_counts_once_first_wins() {
        let d = summarize([
            ("PMU tdie1", 50.0),
            ("PMU tdie1", 90.0),
            ("PMU tdie2", 70.0),
        ])
        .unwrap();
        assert_eq!(d.n, 2);
        assert_eq!(d.max, 70.0);
    }

    #[test]
    fn t1_implausible_value_is_an_error_not_skipped() {
        for bad in [0.0, -1.0, 150.0, 200.0, f64::NAN, f64::INFINITY] {
            assert_eq!(
                summarize([("PMU tdie1", 60.0), ("PMU tdie2", bad)]),
                Err(SysError::Implausible),
                "{bad}"
            );
        }
        // a non-tdie sensor's value is not ours to judge
        assert!(summarize([("PMU tdie1", 60.0), ("PMU tdev1", 0.0)]).is_ok());
    }

    #[test]
    fn t1_no_tdie_is_unsupported_not_cool() {
        assert_eq!(summarize([]), Err(SysError::Unsupported));
        assert_eq!(summarize([("PMU tdev1", 40.0)]), Err(SysError::Unsupported));
    }

    #[test]
    fn t2_levels() {
        for v in 0..=4u64 {
            assert_eq!(pressure_level(v), Ok(v as i64));
        }
        assert_eq!(pressure_level(5), Err(SysError::Implausible));
        assert_eq!(pressure_level(u64::MAX), Err(SysError::Implausible));
    }

    #[test]
    fn no_thermal_says_unsupported() {
        assert_eq!(NoThermal.die_temps(), Err(SysError::Unsupported));
        assert_eq!(NoThermal.pressure(), Err(SysError::Unsupported));
        assert_eq!(selfcheck_t1(&NoThermal), Err(SysError::Unsupported));
    }

    // ---- the walk against a counting fake -------------------------------------------------

    /// Refs: 1 = matching, 2 = client, 3 = services array, 100+i = service i (Get),
    /// 200+i = product of i, 300+i = event of i.
    #[derive(Default)]
    struct FakeHid {
        sensors: Vec<(Option<&'static str>, Option<f64>)>,
        no_services: bool,
        fail_client: bool,
        released: RefCell<BTreeMap<u32, usize>>,
        owned: RefCell<Vec<u32>>,
    }

    impl FakeHid {
        fn with(sensors: Vec<(Option<&'static str>, Option<f64>)>) -> Self {
            Self {
                sensors,
                ..Self::default()
            }
        }
        fn give(&self, r: u32) -> u32 {
            self.owned.borrow_mut().push(r);
            r
        }
        /// Every owned ref released exactly once, nothing else released.
        fn assert_balanced(&self) {
            let rel = self.released.borrow();
            for r in self.owned.borrow().iter() {
                assert_eq!(
                    rel.get(r),
                    Some(&1),
                    "owned ref {r} released {:?} times",
                    rel.get(r)
                );
            }
            for (r, n) in rel.iter() {
                assert!(
                    self.owned.borrow().contains(r),
                    "ref {r} released {n}× but not owned (Get rule)"
                );
            }
        }
    }

    impl HidApi for FakeHid {
        type Ref = u32;
        fn matching(&self) -> Result<u32, SysError> {
            Ok(self.give(1))
        }
        fn client(&self) -> Result<u32, SysError> {
            if self.fail_client {
                return Err(SysError::Unknown);
            }
            Ok(self.give(2))
        }
        fn set_matching(&self, client: u32, matching: u32) {
            assert_eq!((client, matching), (2, 1));
        }
        fn services(&self, _client: u32) -> Option<u32> {
            (!self.no_services).then(|| self.give(3))
        }
        fn count(&self, _array: u32) -> usize {
            self.sensors.len()
        }
        fn at(&self, _array: u32, i: usize) -> Option<u32> {
            Some(100 + u32::try_from(i).unwrap())
        }
        fn product(&self, service: u32) -> Option<(u32, Option<String>)> {
            let i = (service - 100) as usize;
            let obj = self.give(200 + i as u32);
            Some((obj, self.sensors[i].0.map(str::to_owned)))
        }
        fn event(&self, service: u32) -> Option<u32> {
            let i = (service - 100) as usize;
            self.sensors[i].1.map(|_| self.give(300 + i as u32))
        }
        fn celsius(&self, event: u32) -> f64 {
            self.sensors[(event - 300) as usize].1.unwrap()
        }
        fn release(&self, r: u32) {
            *self.released.borrow_mut().entry(r).or_default() += 1;
        }
    }

    #[test]
    fn walk_reads_tdie_and_releases_only_create_copy_results() {
        let hid = FakeHid::with(vec![
            (Some("PMU tdie1"), Some(60.0)),
            (Some("PMU tdev3"), Some(40.0)), // not tdie: its event is never copied
            (None, Some(99.0)),              // product not a string: skipped
            (Some("PMU tdie2"), None),       // no event: skipped
            (Some("PMU tdie3"), Some(80.0)),
        ]);
        let d = read_die_temps(&hid).unwrap();
        assert_eq!((d.n, d.max, d.avg), (2, 80.0, 70.0));
        hid.assert_balanced();
        // the Get-rule service refs were never released
        assert!(hid
            .released
            .borrow()
            .keys()
            .all(|r| !(100..200).contains(r)));
    }

    #[test]
    fn walk_releases_on_every_error_path() {
        let hid = FakeHid::with(vec![(Some("PMU tdie1"), Some(200.0))]);
        assert_eq!(read_die_temps(&hid), Err(SysError::Implausible));
        hid.assert_balanced();

        let hid = FakeHid {
            no_services: true,
            ..FakeHid::default()
        };
        assert_eq!(read_die_temps(&hid), Err(SysError::Unsupported));
        hid.assert_balanced();

        let hid = FakeHid {
            fail_client: true,
            ..FakeHid::default()
        };
        assert_eq!(read_die_temps(&hid), Err(SysError::Unknown));
        hid.assert_balanced(); // the matching dict made before the failure is released

        let hid = FakeHid::with(vec![(Some("PMU tdev1"), Some(40.0))]);
        assert_eq!(read_die_temps(&hid), Err(SysError::Unsupported));
        hid.assert_balanced();
    }
}
