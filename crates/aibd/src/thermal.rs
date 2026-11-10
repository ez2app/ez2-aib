//! Thermal guard, R1 PR 3a step 2: **dry run** (ADR 0005 §1 exception;
//! DECISION-r1-thermal-c477a-1110 addendum 3).
//!
//! While aibd has at least one process of its own (`on_running_changed` 0 → n)
//! one sampler thread runs the `thermal_sample` timer: every 5 s it reads T1
//! (CPU die temperature) and T2 (thermal pressure) from `aib-sys`, feeds them to
//! `aib_thermal::step`, and **only records** what it would do: counters in
//! `/api/v1/metrics` (`thermal`) and `thermal.would_reap` / `thermal.would_nogo`
//! events. It never stops a session and never refuses a POST (`launch_allowed`
//! stays true); PR 3b turns that on after 3 days of dry run with 0 misjudgements.
//! The round in which the last own process ends drops the timer (n → 0).
//!
//! T1 (a63ab Q3, 043dd #617 r2 L1): checked once at start-up; `unsupported` /
//! `spi_missing` switch it off until restart; any other failure (`unknown`) is
//! tried again in the first round after a 0 → n change, at most once every
//! [`T1_RECHECK_S`]. With neither T1 nor T2 usable the guard is not set up at
//! all (a63ab #616 L1) and the timer never exists.
//!
//! History is kept in memory: after a restart the first rounds take the
//! fallback, which never reaps without pressure (R1 PR 3 Q2).

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, Weak};
use std::time::Instant;

use aib_store::{NewEvent, Store};
use aib_sys::{SysError, ThermalInfo};
use aib_thermal::{Decision, History, Level, Reading};

use crate::sessions::{Hooks, Sessions};
use crate::timers::Timer;

/// Least time between two T1 re-checks after an `unknown` failure (a63ab Q3).
pub const T1_RECHECK_S: f64 = 600.0;
/// A `thermal.would_reap` event for the same session is written at most this often.
pub const WOULD_REAP_EVERY_S: f64 = 60.0;

/// What paces the sampler. In aibd it is the registered `thermal_sample` timer
/// (ADR 0005: the only place that sleeps); tests give their own, so a round
/// happens when the test lets it, not after real time has passed.
pub(crate) trait Pace: Send + Sync {
    /// Begin one period of the sampler.
    fn begin(&self) -> Box<dyn Beat>;
}

/// One period of the sampler: [`Beat::tick`] waits for the next round and
/// returns `false` once the period's maximum lifetime is reached. Dropping it
/// ends the period.
pub(crate) trait Beat: Send {
    fn tick(&mut self) -> bool;
}

impl Pace for &'static Timer {
    fn begin(&self) -> Box<dyn Beat> {
        Box::new(Timer::begin(*self, None))
    }
}

impl Beat for crate::timers::Op {
    fn tick(&mut self) -> bool {
        crate::timers::Op::tick(self)
    }
}

#[derive(Debug, Clone, PartialEq)]
enum T1 {
    On,
    /// Off until restart, with the tag that switched it off.
    Off(&'static str),
    /// Last check failed for a reason that may pass; checked again after [`T1_RECHECK_S`].
    Retry {
        at: f64,
        tag: &'static str,
    },
}

impl T1 {
    fn name(&self) -> String {
        match self {
            T1::On => "on".into(),
            T1::Off(t) => format!("off:{t}"),
            T1::Retry { tag, .. } => format!("retry:{tag}"),
        }
    }
}

/// `unsupported` / `spi_missing` are for good; anything else may pass next time.
fn t1_after(err: SysError, now: f64) -> T1 {
    match err {
        SysError::Unsupported | SysError::SpiMissing => T1::Off(err.tag()),
        _ => T1::Retry {
            at: now,
            tag: err.tag(),
        },
    }
}

#[derive(Default)]
struct State {
    t1: Option<T1>,
    history: History,
    t1_errors: BTreeMap<&'static str, u64>,
    t2_errors: BTreeMap<&'static str, u64>,
    rounds: u64,
    t1_checks: u64,
    rebegins: u64,
    would_reap: u64,
    would_nogo: bool,
    nogo_changes: u64,
    last: Option<(Decision, Reading, Option<i64>)>,
    last_reap: Option<(String, f64)>,
}

pub struct Guard {
    info: Arc<dyn ThermalInfo>,
    store: Arc<Store>,
    sessions: Weak<Sessions>,
    pace: Arc<dyn Pace>,
    clock: Instant,
    running: AtomicUsize,
    sampling: AtomicBool,
    threads: AtomicU64,
    st: Mutex<State>,
    me: Weak<Guard>,
}

impl Guard {
    /// Set up the guard and plug it into `sessions`, or `None` when neither T1
    /// nor T2 can be read on this machine (nothing registered, no timer).
    pub fn install(
        info: Arc<dyn ThermalInfo>,
        store: Arc<Store>,
        sessions: &Arc<Sessions>,
    ) -> Option<Arc<Guard>> {
        Self::install_with(
            info,
            store,
            sessions,
            Arc::new(&crate::timers::THERMAL_SAMPLE),
        )
    }

    pub(crate) fn install_with(
        info: Arc<dyn ThermalInfo>,
        store: Arc<Store>,
        sessions: &Arc<Sessions>,
        pace: Arc<dyn Pace>,
    ) -> Option<Arc<Guard>> {
        let t1 = match aib_sys::thermal::selfcheck_t1(info.as_ref()) {
            Ok(_) => T1::On,
            Err(e) => t1_after(e, 0.0),
        };
        let t2_ok = !matches!(
            info.pressure(),
            Err(SysError::Unsupported | SysError::SpiMissing)
        );
        if matches!(t1, T1::Off(_)) && !t2_ok {
            tracing::info!(t1 = %t1.name(), "thermal guard: no sensor on this machine, not set up");
            return None;
        }
        tracing::info!(t1 = %t1.name(), t2 = t2_ok, "thermal guard set up (dry run)");
        let g = Arc::new_cyclic(|me| Guard {
            info,
            store,
            sessions: Arc::downgrade(sessions),
            pace,
            clock: Instant::now(),
            running: AtomicUsize::new(0),
            sampling: AtomicBool::new(false),
            threads: AtomicU64::new(0),
            st: Mutex::new(State {
                t1: Some(t1),
                t1_checks: 1,
                ..State::default()
            }),
            me: me.clone(),
        });
        let hook = Arc::downgrade(&g);
        let set = sessions.set_hooks(Hooks {
            // dry run (3a): never refuses; 3b answers `!would_nogo`
            launch_allowed: Arc::new(|| true),
            on_running_changed: Arc::new(move |_old, new| {
                if let Some(g) = hook.upgrade() {
                    g.running_changed(new);
                }
            }),
        });
        if !set {
            tracing::warn!("thermal guard: sessions hooks already set; guard not plugged in");
            return None;
        }
        Some(g)
    }

    fn st(&self) -> MutexGuard<'_, State> {
        self.st.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn now(&self) -> f64 {
        self.clock.elapsed().as_secs_f64()
    }

    /// Called under the sessions lock: atomics and, on 0 → n, one thread start.
    fn running_changed(&self, new: usize) {
        self.running.store(new, Ordering::SeqCst);
        if new == 0 || self.sampling.swap(true, Ordering::SeqCst) {
            return;
        }
        let Some(me) = self.me.upgrade() else {
            self.sampling.store(false, Ordering::SeqCst);
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("aibd-thermal-sample".into())
            .spawn(move || me.sample_loop());
        if spawned.is_err() {
            // the next 0 → n change tries again
            self.sampling.store(false, Ordering::SeqCst);
            tracing::warn!("could not start the thermal sampler");
        }
    }

    /// The sampler: one round now, then one per tick, while own processes run.
    /// At the timer's maximum lifetime it begins again in the same round (Q4).
    fn sample_loop(self: Arc<Self>) {
        self.threads.fetch_add(1, Ordering::Relaxed);
        let mut op = self.pace.begin();
        let mut first = true;
        loop {
            if self.running.load(Ordering::SeqCst) == 0 {
                self.sampling.store(false, Ordering::SeqCst);
                // a 0 → n that saw `sampling` still set did not start a sampler:
                // take that period over, unless another one already did
                if self.running.load(Ordering::SeqCst) == 0
                    || self.sampling.swap(true, Ordering::SeqCst)
                {
                    return;
                }
                first = true;
            }
            self.round(first);
            first = false;
            if !op.tick() {
                drop(op);
                op = self.pace.begin();
                self.st().rebegins += 1;
            }
        }
    }

    /// One sample and decision. `first` = first round after a 0 → n change.
    fn round(&self, first: bool) {
        self.round_at(first, self.now());
    }

    /// [`Self::round`] at `now` seconds on the guard's clock (tests set it).
    pub(crate) fn round_at(&self, first: bool, now: f64) {
        if first {
            self.maybe_recheck_t1(now);
        }
        let t1_on = matches!(self.st().t1, Some(T1::On));
        let reading = if t1_on {
            match self.info.die_temps() {
                Ok(d) => Reading {
                    max: Some(d.max),
                    avg: Some(d.avg),
                },
                Err(e) => {
                    *self.st().t1_errors.entry(e.tag()).or_default() += 1;
                    Reading {
                        max: None,
                        avg: None,
                    }
                }
            }
        } else {
            Reading {
                max: None,
                avg: None,
            }
        };
        let pressure = match self.info.pressure() {
            Ok(p) => Some(p),
            Err(e) => {
                *self.st().t2_errors.entry(e.tag()).or_default() += 1;
                None
            }
        };
        let (d, nogo_changed) = {
            let mut st = self.st();
            let d = aib_thermal::step(Some(&mut st.history), now, reading, pressure);
            st.rounds += 1;
            st.last = Some((d, reading, pressure));
            let changed = st.would_nogo != d.nogo();
            if changed {
                st.would_nogo = d.nogo();
                st.nogo_changes += 1;
            }
            (d, changed)
        };
        if nogo_changed {
            self.event(
                "thermal.would_nogo",
                serde_json::json!({
                    "nogo": d.nogo(),
                    "level": level_name(d.level),
                    "why": d.why.code(),
                    "held_s": d.held_s,
                    "max": reading.max,
                    "avg": reading.avg,
                    "pressure": pressure,
                    "dry_run": true,
                }),
            );
        }
        if d.reap() {
            self.would_reap(now, &d, reading, pressure);
        }
    }

    fn maybe_recheck_t1(&self, now: f64) {
        let due = matches!(self.st().t1, Some(T1::Retry { at, .. }) if now - at >= T1_RECHECK_S);
        if !due {
            return;
        }
        let t1 = match aib_sys::thermal::selfcheck_t1(self.info.as_ref()) {
            Ok(_) => T1::On,
            Err(e) => t1_after(e, now),
        };
        tracing::info!(t1 = %t1.name(), "thermal guard: T1 checked again");
        let mut st = self.st();
        st.t1 = Some(t1);
        st.t1_checks += 1;
    }

    /// Q1 (a63ab / a9136): the whole session, the most recently started
    /// running one. Dry run: record which, never stop it.
    fn would_reap(&self, now: f64, d: &Decision, reading: Reading, pressure: Option<i64>) {
        let victim = self.sessions.upgrade().and_then(|s| {
            s.list()
                .into_iter()
                .filter(|x| x.state == "running")
                .max_by(|a, b| a.started_at.cmp(&b.started_at))
        });
        let id = victim.as_ref().map(|v| v.id.clone());
        {
            let mut st = self.st();
            st.would_reap += 1;
            let key = id.clone().unwrap_or_default();
            if let Some((last, at)) = &st.last_reap {
                if *last == key && now - at < WOULD_REAP_EVERY_S {
                    return;
                }
            }
            st.last_reap = Some((key, now));
        }
        self.event(
            "thermal.would_reap",
            serde_json::json!({
                "session_id": id,
                "started_at": victim.as_ref().map(|v| v.started_at.clone()),
                "level": level_name(d.level),
                "why": d.why.code(),
                "held_s": d.held_s,
                "max": reading.max,
                "avg": reading.avg,
                "pressure": pressure,
                "dry_run": true,
            }),
        );
    }

    /// One event per record (aggregate `thermal`, sequence 1), like `security`.
    fn event(&self, kind: &str, payload: serde_json::Value) {
        let id =
            crate::sessions::new_id("ev_").unwrap_or_else(|_| format!("ev_thermal_{}", self.now()));
        let ev = NewEvent {
            event_id: id.clone(),
            aggregate_type: "thermal".into(),
            aggregate_id: id,
            sequence: 1,
            event_type: kind.to_owned(),
            correlation_id: None,
            occurred_at: crate::sessions::now_rfc3339(),
            payload,
        };
        if let Err(err) = self.store.append(vec![ev]) {
            tracing::error!(event = kind, error = %err, "thermal event write failed");
        }
    }

    /// `thermal` in `/api/v1/metrics`.
    pub fn metrics(&self) -> serde_json::Value {
        let st = self.st();
        let last = st.last.map(|(d, r, p)| {
            serde_json::json!({
                "level": level_name(d.level),
                "why": d.why.code(),
                "held_s": d.held_s,
                "max": r.max,
                "avg": r.avg,
                "pressure": p,
            })
        });
        serde_json::json!({
            "mode": "dry_run",
            "t1": st.t1.as_ref().map(T1::name),
            "t1_checks": st.t1_checks,
            "t1_errors": st.t1_errors,
            "t2_errors": st.t2_errors,
            "rounds": st.rounds,
            "would_reap": st.would_reap,
            "would_nogo": st.would_nogo,
            "nogo_changes": st.nogo_changes,
            "rebegins": st.rebegins,
            "sampling": self.sampling.load(Ordering::SeqCst),
            "running": self.running.load(Ordering::SeqCst),
            "last": last,
        })
    }

    /// Sampler threads started so far (tests).
    pub fn sampler_starts(&self) -> u64 {
        self.threads.load(Ordering::Relaxed)
    }
}

fn level_name(l: Level) -> &'static str {
    match l {
        Level::Ok => "ok",
        Level::Warn => "warn",
        Level::Hot => "hot",
        Level::Crit => "crit",
        Level::Unknown => "unknown",
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use crate::platform::session::{
        CandidateDiag, Exit, ExitWait, Running, SpawnFail, Spec, Stop, StopStatus,
    };
    use aib_sys::fake::FakeThermal;
    use aib_sys::DieTemps;
    use std::sync::Condvar;
    use std::time::Duration;

    /// A pace the test drives by hand (no real time passes): the sampler's
    /// `tick` waits until the test lets one round go ([`ManualPace::step`]) or
    /// ends the period at its lifetime ([`ManualPace::end_period`]).
    #[derive(Clone, Default)]
    struct ManualPace(Arc<PaceInner>);

    #[derive(Default)]
    struct PaceInner {
        st: Mutex<PaceState>,
        cv: Condvar,
    }

    #[derive(Default)]
    struct PaceState {
        /// rounds let go and not taken yet
        steps: u64,
        /// periods to end at their next tick
        ends: u64,
        /// `tick` calls so far: the sampler has finished that many rounds
        ticks: u64,
        /// periods begun and not dropped
        active: u64,
    }

    struct ManualBeat(Arc<PaceInner>);

    impl PaceInner {
        fn st(&self) -> MutexGuard<'_, PaceState> {
            self.st.lock().unwrap_or_else(|e| e.into_inner())
        }
    }

    impl Pace for ManualPace {
        fn begin(&self) -> Box<dyn Beat> {
            self.0.st().active += 1;
            self.0.cv.notify_all();
            Box::new(ManualBeat(self.0.clone()))
        }
    }

    impl Beat for ManualBeat {
        fn tick(&mut self) -> bool {
            let mut s = self.0.st();
            s.ticks += 1;
            self.0.cv.notify_all();
            loop {
                if s.ends > 0 {
                    s.ends -= 1;
                    return false;
                }
                if s.steps > 0 {
                    s.steps -= 1;
                    return true;
                }
                s = self.0.cv.wait(s).unwrap_or_else(|e| e.into_inner());
            }
        }
    }

    impl Drop for ManualBeat {
        fn drop(&mut self) {
            self.0.st().active -= 1;
            self.0.cv.notify_all();
        }
    }

    impl ManualPace {
        fn step(&self) {
            self.0.st().steps += 1;
            self.0.cv.notify_all();
        }

        fn end_period(&self) {
            self.0.st().ends += 1;
            self.0.cv.notify_all();
        }

        fn ticks(&self) -> u64 {
            self.0.st().ticks
        }

        fn active(&self) -> u64 {
            self.0.st().active
        }

        /// Wait until `f` holds. The bound is only there so a broken guard
        /// fails the test instead of hanging it; nothing waits on it otherwise.
        fn until(&self, what: &str, f: impl Fn(&PaceState) -> bool) {
            let deadline = std::time::Instant::now() + Duration::from_secs(10);
            let mut s = self.0.st();
            while !f(&s) {
                let left = deadline.saturating_duration_since(std::time::Instant::now());
                assert!(!left.is_zero(), "timed out waiting for {what}");
                // named exception (ADR 0005): an upper bound for a failing test, not pacing
                #[allow(clippy::disallowed_methods)]
                let next = self.0.cv.wait_timeout(s, left);
                s = next.unwrap_or_else(|e| e.into_inner()).0;
            }
        }

        /// The sampler has finished `n` rounds and waits for the next.
        fn rounds_done(&self, n: u64) {
            self.until("the sampler's rounds", |s| s.ticks >= n);
        }

        /// Let rounds go until the sampler has left (its period dropped). A
        /// sampler that never leaves fails here after a bounded number of rounds.
        fn let_it_leave(&self) {
            for _ in 0..100 {
                if self.active() == 0 {
                    return;
                }
                let n = self.ticks();
                self.step();
                self.until("the sampler to round or leave", |s| {
                    s.active == 0 || s.ticks > n
                });
            }
            assert_eq!(self.active(), 0, "the sampler did not leave");
        }
    }

    struct Quiet;
    struct NeverExits;

    impl ExitWait for NeverExits {
        fn wait(&self, t: Duration) -> Option<Exit> {
            #[allow(clippy::disallowed_methods)] // test pacing only
            std::thread::sleep(t.min(Duration::from_millis(20)));
            None
        }
    }

    impl Running for Quiet {
        fn pid(&self) -> u32 {
            999_995
        }
        fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
            Ok(None)
        }
        fn exit_wait(&self) -> Arc<dyn ExitWait> {
            Arc::new(NeverExits)
        }
        fn stop(&mut self, _: Duration) -> Stop {
            Stop {
                status: StopStatus::Clean,
                survivors: vec![],
                tree_size: 1,
                signals: vec![],
                blind_spots: vec![],
                candidates: CandidateDiag::default(),
            }
        }
    }

    fn quiet_spawn(
        _: &Spec,
        _: Option<Arc<crate::lineage::Lineage>>,
    ) -> Result<Box<dyn Running>, SpawnFail> {
        Ok(Box::new(Quiet))
    }

    struct Rig {
        _dir: tempfile::TempDir,
        store: Arc<Store>,
        sessions: Arc<Sessions>,
        fake: Arc<FakeThermal>,
    }

    fn rig() -> Rig {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(Store::open(&dir.path().join("db.sqlite")).unwrap());
        let sessions = Sessions::new(store.clone(), None, quiet_spawn, 4, dir.path().into());
        let fake = Arc::new(FakeThermal::new());
        fake.set_die(Ok(die(60.0, 55.0)));
        fake.set_pressure(Ok(0));
        Rig {
            _dir: dir,
            store,
            sessions,
            fake,
        }
    }

    fn die(max: f64, avg: f64) -> DieTemps {
        DieTemps { max, avg, n: 4 }
    }

    fn req() -> crate::sessions::Request {
        crate::sessions::Request {
            argv: vec!["/fake/quiet".into()],
            cwd: None,
            env: vec![],
        }
    }

    fn install(r: &Rig, pace: &ManualPace) -> Arc<Guard> {
        Guard::install_with(
            r.fake.clone(),
            r.store.clone(),
            &r.sessions,
            Arc::new(pace.clone()),
        )
        .unwrap()
    }

    fn events(r: &Rig, kind: &str) -> Vec<serde_json::Value> {
        r.store
            .events_after(0, 500)
            .unwrap()
            .into_iter()
            .filter(|e| e.event_type == kind)
            .map(|e| e.payload)
            .collect()
    }

    /// Dry run: pressure says reap; the newest running session is named in
    /// `thermal.would_reap`, nothing is stopped and new POSTs still start.
    #[test]
    fn a_reap_is_only_recorded_and_names_the_newest_session() {
        let r = rig();
        let pace = ManualPace::default();
        let g = install(&r, &pace);
        let a = r.sessions.start(req()).unwrap();
        #[allow(clippy::disallowed_methods)] // started_at has millisecond resolution
        std::thread::sleep(Duration::from_millis(5));
        let b = r.sessions.start(req()).unwrap();
        // the sampler took its first round (cool) and now waits for the test
        pace.rounds_done(1);
        assert_eq!(g.metrics()["rounds"], 1);
        r.fake.set_pressure(Ok(2));
        g.round_at(false, 1_000.0);
        let reaps = events(&r, "thermal.would_reap");
        assert_eq!(reaps.len(), 1, "{reaps:?}");
        assert_eq!(reaps[0]["session_id"], b.id.as_str());
        assert_eq!(reaps[0]["why"], "pressure");
        assert_eq!(reaps[0]["dry_run"], true);
        for id in [&a.id, &b.id] {
            assert_eq!(
                r.sessions.get(id).unwrap().state,
                "running",
                "dry run stopped {id}"
            );
        }
        // the same session again within a minute: counted, not written again
        g.round_at(false, 1_005.0);
        assert_eq!(events(&r, "thermal.would_reap").len(), 1);
        assert_eq!(g.metrics()["would_reap"], 2);
        assert_eq!(g.metrics()["would_nogo"], true);
        assert_eq!(events(&r, "thermal.would_nogo").len(), 1);
        // last: a new session is newer than b and would be named next round
        assert_eq!(
            r.sessions.start(req()).unwrap().state,
            "running",
            "dry run refused"
        );
    }

    /// a63ab Q3 / 043dd #617 r2 L1: `unknown` keeps T1 for a later try, in the
    /// first round of a 0 → n period, at most every T1_RECHECK_S.
    #[test]
    fn an_unknown_t1_is_tried_again_after_ten_minutes_on_a_first_round() {
        let r = rig();
        r.fake.set_die(Err(SysError::Unknown));
        let g = install(&r, &ManualPace::default());
        assert_eq!(g.metrics()["t1"], "retry:unknown");
        r.fake.set_die(Ok(die(60.0, 55.0)));
        g.round_at(true, T1_RECHECK_S - 1.0);
        assert_eq!(g.metrics()["t1"], "retry:unknown", "too soon");
        g.round_at(false, T1_RECHECK_S + 1.0);
        assert_eq!(g.metrics()["t1"], "retry:unknown", "only on a first round");
        g.round_at(true, T1_RECHECK_S + 2.0);
        assert_eq!(g.metrics()["t1"], "on");
        assert_eq!(g.metrics()["t1_checks"], 2);
    }

    /// `unsupported` T1 is off until restart; T2 alone still runs the guard.
    #[test]
    fn an_unsupported_t1_is_off_and_t2_alone_is_enough() {
        let r = rig();
        r.fake.set_die(Err(SysError::Unsupported));
        let g = install(&r, &ManualPace::default());
        assert_eq!(g.metrics()["t1"], "off:unsupported");
        g.round_at(true, T1_RECHECK_S * 10.0);
        assert_eq!(g.metrics()["t1"], "off:unsupported");
        assert_eq!(g.metrics()["t1_checks"], 1);
    }

    /// a63ab #616 L1: neither sensor ⇒ not set up, hooks left free.
    #[test]
    fn no_sensor_at_all_sets_nothing_up() {
        let r = rig();
        r.fake.set_die(Err(SysError::Unsupported));
        r.fake.set_pressure(Err(SysError::Unsupported));
        assert!(Guard::install_with(
            r.fake.clone(),
            r.store.clone(),
            &r.sessions,
            Arc::new(ManualPace::default())
        )
        .is_none());
        assert!(r.sessions.set_hooks(Hooks::default()), "hooks were taken");
    }

    /// ADR 0005 §1 condition 2 and Q4: the sampler exists only while own
    /// processes run, begins again at its maximum lifetime, and is gone after.
    #[test]
    fn the_sampler_lives_with_the_sessions_and_begins_again_at_its_lifetime() {
        let pace = ManualPace::default();
        let r = rig();
        let g = install(&r, &pace);
        assert_eq!(pace.active(), 0);
        let a = r.sessions.start(req()).unwrap();
        pace.rounds_done(1);
        pace.step();
        pace.step();
        pace.rounds_done(3);
        assert_eq!(pace.active(), 1);
        assert_eq!(g.metrics()["rounds"], 3);
        // the period's lifetime: a new one begins, in the same round, never two
        pace.end_period();
        pace.until("a re-begin", |s| s.ticks >= 4);
        assert_eq!(
            g.metrics()["rebegins"],
            1,
            "no re-begin at the maximum lifetime"
        );
        assert_eq!(pace.active(), 1, "a re-begin must not leave two");
        r.sessions.stop(&a.id, Duration::from_secs(1)).unwrap();
        pace.let_it_leave();
        assert_eq!(g.metrics()["sampling"], false);
        // gone, not asleep: its period was dropped on the way out, so no
        // round can follow (the old test slept 200 ms to see that)
        assert_eq!(pace.active(), 0, "sampled with 0 sessions");
        assert_eq!(g.sampler_starts(), 1);
        // a new 0 → 1 starts it again
        let b = r.sessions.start(req()).unwrap();
        pace.until("a new sampler", |s| s.active == 1);
        r.sessions.stop(&b.id, Duration::from_secs(1)).unwrap();
        pace.let_it_leave();
        assert_eq!(g.sampler_starts(), 2);
    }
}
