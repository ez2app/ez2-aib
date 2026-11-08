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

    fn injected(&self, call: Call, pid: u32) -> Result<(), SysError> {
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
