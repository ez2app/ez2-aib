//! An in-memory [`ProcessInfo`] for tests of crates that use aib-sys. It
//! never touches the OS, and every call can be made to fail with any
//! [`SysError`].

use crate::{BsdInfo, ProcessInfo, SysError, UniqueIds};
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

/// Which call a failure is injected into.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Call {
    UniqueIds,
    BsdInfo,
    Children,
    /// `all_pids`; the pid key is ignored (use 0).
    AllPids,
}

#[derive(Debug, Clone)]
struct Proc {
    ids: UniqueIds,
    info: BsdInfo,
}

#[derive(Debug, Default)]
struct State {
    procs: BTreeMap<u32, Proc>,
    fail: BTreeMap<(Call, u32), SysError>,
    calls: BTreeMap<Call, usize>,
}

/// A process table you write yourself.
#[derive(Debug, Default)]
pub struct FakeProcessInfo {
    st: Mutex<State>,
}

impl FakeProcessInfo {
    pub fn new() -> Self {
        Self::default()
    }

    fn st(&self) -> MutexGuard<'_, State> {
        self.st.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Add (or replace) a process. `parent_unique` is the creator's unique
    /// id, which stays when `ppid` later changes (see [`Self::reparent`]).
    pub fn add(&self, pid: u32, ppid: u32, unique: u64, parent_unique: u64, start_sec: u64) {
        self.st().procs.insert(
            pid,
            Proc {
                ids: UniqueIds {
                    unique,
                    parent_unique,
                },
                info: BsdInfo {
                    pid,
                    ppid,
                    pgid: pid,
                    uid: 501,
                    start_sec,
                    start_usec: 0,
                    comm: format!("fake{pid}"),
                },
            },
        );
    }

    /// The process exits: every call for it returns `Gone`.
    pub fn remove(&self, pid: u32) {
        self.st().procs.remove(&pid);
    }

    /// Re-parent to `ppid` (launchd is 1); M1's `parent_unique` stays.
    pub fn reparent(&self, pid: u32, ppid: u32) {
        if let Some(p) = self.st().procs.get_mut(&pid) {
            p.info.ppid = ppid;
        }
    }

    /// Another account's process (`uid` other than the default 501).
    pub fn set_uid(&self, pid: u32, uid: u32) {
        if let Some(p) = self.st().procs.get_mut(&pid) {
            p.info.uid = uid;
        }
    }

    /// Change what M1 reports for `pid` (e.g. a zero or wrong id).
    pub fn set_unique(&self, pid: u32, unique: u64, parent_unique: u64) {
        if let Some(p) = self.st().procs.get_mut(&pid) {
            p.ids = UniqueIds {
                unique,
                parent_unique,
            };
        }
    }

    /// Make `call` for `pid` fail with `err` until [`Self::heal`].
    pub fn fail(&self, call: Call, pid: u32, err: SysError) {
        self.st().fail.insert((call, pid), err);
    }

    pub fn heal(&self, call: Call, pid: u32) {
        self.st().fail.remove(&(call, pid));
    }

    /// How many times `call` was made (any pid).
    pub fn calls(&self, call: Call) -> usize {
        self.st().calls.get(&call).copied().unwrap_or(0)
    }

    fn injected(&self, call: Call, pid: u32) -> Result<(), SysError> {
        *self.st().calls.entry(call).or_insert(0) += 1;
        match self.st().fail.get(&(call, pid)) {
            Some(e) => Err(e.clone()),
            None => Ok(()),
        }
    }
}

impl ProcessInfo for FakeProcessInfo {
    fn unique_ids(&self, pid: u32) -> Result<UniqueIds, SysError> {
        self.injected(Call::UniqueIds, pid)?;
        self.st()
            .procs
            .get(&pid)
            .map(|p| p.ids)
            .ok_or(SysError::Gone)
    }

    fn bsd_info(&self, pid: u32) -> Result<BsdInfo, SysError> {
        self.injected(Call::BsdInfo, pid)?;
        self.st()
            .procs
            .get(&pid)
            .map(|p| p.info.clone())
            .ok_or(SysError::Gone)
    }

    fn children(&self, ppid: u32) -> Result<Vec<u32>, SysError> {
        self.injected(Call::Children, ppid)?;
        Ok(self
            .st()
            .procs
            .values()
            .filter(|p| p.info.ppid == ppid)
            .map(|p| p.info.pid)
            .collect())
    }

    fn all_pids(&self) -> Result<Vec<u32>, SysError> {
        self.injected(Call::AllPids, 0)?;
        Ok(self.st().procs.keys().copied().collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reparent_keeps_the_creator_id() {
        let f = FakeProcessInfo::new();
        f.add(10, 1, 100, 1, 5);
        f.add(11, 10, 110, 100, 6);
        f.reparent(11, 1);
        assert_eq!(f.bsd_info(11).unwrap().ppid, 1);
        assert_eq!(f.unique_ids(11).unwrap().parent_unique, 100);
        assert_eq!(f.children(10).unwrap(), Vec::<u32>::new());
    }

    #[test]
    fn injected_failures_and_gone() {
        let f = FakeProcessInfo::new();
        f.add(10, 1, 100, 1, 5);
        f.fail(Call::UniqueIds, 10, SysError::PermissionDenied);
        assert_eq!(f.unique_ids(10), Err(SysError::PermissionDenied));
        f.heal(Call::UniqueIds, 10);
        assert!(f.unique_ids(10).is_ok());
        f.remove(10);
        assert_eq!(f.bsd_info(10), Err(SysError::Gone));
        f.fail(Call::AllPids, 0, SysError::Incomplete);
        assert_eq!(f.all_pids(), Err(SysError::Incomplete));
    }
}

/// An in-memory [`crate::ChildExit`]: you set each child's state and, like
/// the real call, looking never reaps. [`Self::reap`] stands for the caller's
/// one real `wait()`; after it the pid is [`SysError::NotChild`].
#[derive(Debug, Default)]
pub struct FakeChildExit {
    st: Mutex<BTreeMap<u32, Result<crate::ExitState, SysError>>>,
}

impl FakeChildExit {
    pub fn new() -> Self {
        Self::default()
    }

    fn st(&self) -> MutexGuard<'_, BTreeMap<u32, Result<crate::ExitState, SysError>>> {
        self.st.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Set what `peek(pid)` returns (and what `wait_exit` returns once it is
    /// not `Running`). An `Err` injects that failure.
    pub fn set(&self, pid: u32, state: Result<crate::ExitState, SysError>) {
        self.st().insert(pid, state);
    }

    /// The caller reaped `pid`: from now on it is not our child.
    pub fn reap(&self, pid: u32) {
        self.st().insert(pid, Err(SysError::NotChild));
    }
}

impl crate::ChildExit for FakeChildExit {
    fn peek(&self, pid: u32) -> Result<crate::ExitState, SysError> {
        self.st()
            .get(&pid)
            .cloned()
            .unwrap_or(Err(SysError::NotChild))
    }

    /// A fake cannot block: a child still `Running` gives `Unknown` so a test
    /// never hangs.
    fn wait_exit(&self, pid: u32) -> Result<crate::ExitState, SysError> {
        match self.peek(pid) {
            Ok(crate::ExitState::Running) => Err(SysError::Unknown),
            r => r,
        }
    }
}

/// An in-memory [`crate::ThermalInfo`] (T1 / T2): you set what each call returns, including
/// any [`SysError`], and it counts the calls. Starts as "nothing measured"
/// ([`SysError::Unsupported`] for both).
#[derive(Debug)]
pub struct FakeThermal {
    st: Mutex<ThermalState>,
}

#[derive(Debug)]
struct ThermalState {
    die: Result<crate::DieTemps, SysError>,
    pressure: Result<i64, SysError>,
    die_calls: usize,
    pressure_calls: usize,
}

impl Default for FakeThermal {
    fn default() -> Self {
        Self {
            st: Mutex::new(ThermalState {
                die: Err(SysError::Unsupported),
                pressure: Err(SysError::Unsupported),
                die_calls: 0,
                pressure_calls: 0,
            }),
        }
    }
}

impl FakeThermal {
    pub fn new() -> Self {
        Self::default()
    }

    fn st(&self) -> MutexGuard<'_, ThermalState> {
        self.st.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// What T1 returns from now on.
    pub fn set_die(&self, r: Result<crate::DieTemps, SysError>) {
        self.st().die = r;
    }

    /// What T2 returns from now on.
    pub fn set_pressure(&self, r: Result<i64, SysError>) {
        self.st().pressure = r;
    }

    /// `(T1 calls, T2 calls)` so far.
    pub fn calls(&self) -> (usize, usize) {
        let st = self.st();
        (st.die_calls, st.pressure_calls)
    }
}

impl crate::ThermalInfo for FakeThermal {
    fn die_temps(&self) -> Result<crate::DieTemps, SysError> {
        let mut st = self.st();
        st.die_calls += 1;
        st.die.clone()
    }

    fn pressure(&self) -> Result<i64, SysError> {
        let mut st = self.st();
        st.pressure_calls += 1;
        st.pressure.clone()
    }
}

#[cfg(test)]
mod thermal_tests {
    use super::FakeThermal;
    use crate::{DieTemps, SysError, ThermalInfo};

    #[test]
    fn fake_thermal_returns_what_was_set_and_counts() {
        let f = FakeThermal::new();
        assert_eq!(f.die_temps(), Err(SysError::Unsupported));
        let d = DieTemps {
            max: 91.0,
            avg: 80.0,
            n: 4,
        };
        f.set_die(Ok(d));
        f.set_pressure(Err(SysError::Implausible));
        assert_eq!(f.die_temps(), Ok(d));
        assert_eq!(f.pressure(), Err(SysError::Implausible));
        assert_eq!(f.calls(), (2, 1));
    }
}

#[cfg(test)]
mod child_exit_tests {
    use super::FakeChildExit;
    use crate::{ChildExit, ExitState, SysError};

    #[test]
    fn looking_never_reaps_until_the_caller_does() {
        let f = FakeChildExit::new();
        f.set(7, Ok(ExitState::Running));
        assert_eq!(f.peek(7), Ok(ExitState::Running));
        assert_eq!(f.wait_exit(7), Err(SysError::Unknown)); // a fake never blocks
        f.set(7, Ok(ExitState::Exited(3)));
        assert_eq!(f.peek(7), Ok(ExitState::Exited(3)));
        assert_eq!(f.wait_exit(7), Ok(ExitState::Exited(3)));
        assert_eq!(f.peek(7), Ok(ExitState::Exited(3)));
        f.reap(7);
        assert_eq!(f.peek(7), Err(SysError::NotChild));
        assert_eq!(f.peek(8), Err(SysError::NotChild));
        f.set(9, Err(SysError::Os(5)));
        assert_eq!(f.wait_exit(9), Err(SysError::Os(5)));
    }
}
