//! Lineage through M1 (`p_puniqueid`, aib-sys), DECISION-4 A (2026-11-07).
//!
//! What it is for: a process whose parent exited at once is re-parented to
//! launchd, so the parent-pid scan cannot find it. M1's `parent_unique`
//! still names its creator. Lineage uses that to add such processes to a
//! tree as **candidates**.
//!
//! Rules:
//! - **Start-up self-check.** It runs once ([`Lineage::detect`]). If it fails,
//!   M1 is off until restart, `selfcheck` reports `failed:<tag>`, and every
//!   cancel report carries `lineage_unavailable: true`.
//! - **M1 never decides an action.** A candidate still needs the public
//!   identity: its start time (M2 / sysinfo) must not be older than its
//!   creator's. Before a signal, the caller re-checks `(pid, start time)`,
//!   and the unique id too when it was recorded. A mismatch, or an id that
//!   cannot be read, means no signal.
//! - **Failures are counted** (`failures`). An incomplete pass marks the
//!   report `lineage_unavailable`, so it can never be `clean`.

use aib_sys::selfcheck::Failure;
use aib_sys::{ProcessInfo, SysError};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

pub struct Lineage {
    info: Option<Arc<dyn ProcessInfo>>,
    selfcheck: String,
    failures: AtomicU64,
}

impl std::fmt::Debug for Lineage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Lineage")
            .field("enabled", &self.enabled())
            .field("selfcheck", &self.selfcheck)
            .field("failures", &self.failures())
            .finish()
    }
}

/// One adopted candidate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Found {
    pub pid: u32,
    pub start: u64,
    pub unique: u64,
}

/// Result of one lineage pass.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Adopt {
    pub found: Vec<Found>,
    /// False when lineage is off, or any read that matters failed.
    pub complete: bool,
}

impl Lineage {
    /// Lineage switched off, with the reason as the self-check status.
    pub fn disabled(reason: &str) -> Self {
        Lineage {
            info: None,
            selfcheck: reason.to_owned(),
            failures: AtomicU64::new(0),
        }
    }

    /// Keep `info` only if the self-check passed.
    pub fn from_selfcheck(info: Arc<dyn ProcessInfo>, check: Result<(), Failure>) -> Self {
        match check {
            Ok(()) => Lineage {
                info: Some(info),
                selfcheck: "ok".into(),
                failures: AtomicU64::new(0),
            },
            Err(f) => Self::disabled(&format!("failed:{}", f.tag())),
        }
    }

    /// The real thing: macOS runs the self-check on this process.
    #[cfg(target_os = "macos")]
    pub fn detect() -> Self {
        let info: Arc<dyn ProcessInfo> = Arc::new(aib_sys::Macos);
        let check = aib_sys::selfcheck::run(info.as_ref());
        Self::from_selfcheck(info, check)
    }

    #[cfg(not(target_os = "macos"))]
    pub fn detect() -> Self {
        Self::disabled("unsupported")
    }

    pub fn enabled(&self) -> bool {
        self.info.is_some()
    }

    pub fn failures(&self) -> u64 {
        self.failures.load(Ordering::Relaxed)
    }

    /// `/api/v1/metrics` view.
    pub fn status(&self) -> serde_json::Value {
        serde_json::json!({
            "enabled": self.enabled(),
            "selfcheck": self.selfcheck,
            "failures": self.failures(),
        })
    }

    fn fail(&self) {
        self.failures.fetch_add(1, Ordering::Relaxed);
    }

    /// Read `pid`'s unique id now (to keep it while the process is alive).
    /// `None` when lineage is off or the process is gone; other errors are
    /// counted.
    pub fn unique_of(&self, pid: u32) -> Option<u64> {
        match self.info.as_ref()?.unique_ids(pid) {
            Ok(u) => Some(u.unique),
            Err(SysError::Gone) => None,
            Err(_) => {
                self.fail();
                None
            }
        }
    }

    /// Does `pid` still carry the unique id recorded for it? `false` on any
    /// doubt (gone, unreadable, lineage off).
    pub fn same_unique(&self, pid: u32, unique: u64) -> bool {
        match self.info.as_ref().map(|i| i.unique_ids(pid)) {
            Some(Ok(u)) => u.unique == unique,
            Some(Err(SysError::Gone)) | None => false,
            Some(Err(_)) => {
                self.fail();
                false
            }
        }
    }

    /// The unique ids of `known` (cached in `uniques`), then every process
    /// whose creator is one of them, transitively. `me` is never adopted.
    pub fn adopt(
        &self,
        known: &BTreeMap<u32, u64>,
        uniques: &mut BTreeMap<u32, u64>,
        me: u32,
    ) -> Adopt {
        let Some(info) = self.info.as_ref() else {
            return Adopt::default();
        };
        let mut complete = true;
        for pid in known.keys() {
            if uniques.contains_key(pid) {
                continue;
            }
            match info.unique_ids(*pid) {
                Ok(u) => {
                    uniques.insert(*pid, u.unique);
                }
                Err(SysError::Gone) => {}
                Err(_) => {
                    self.fail();
                    complete = false;
                }
            }
        }
        // creator unique id -> creator start time
        let mut creators: BTreeMap<u64, u64> = uniques
            .iter()
            .filter_map(|(pid, u)| known.get(pid).map(|s| (*u, *s)))
            .collect();
        let all = match info.all_pids() {
            Ok(a) => a,
            Err(_) => {
                self.fail();
                return Adopt {
                    found: Vec::new(),
                    complete: false,
                };
            }
        };
        // one M1 read per unknown process, then the closure in memory
        let mut pending: Vec<(u32, u64, u64)> = Vec::new();
        for pid in all {
            if pid == me || known.contains_key(&pid) {
                continue;
            }
            match info.unique_ids(pid) {
                Ok(u) => pending.push((pid, u.unique, u.parent_unique)),
                // gone, or another account's process: not ours either way
                Err(SysError::Gone | SysError::PermissionDenied) => {}
                Err(_) => {
                    self.fail();
                    complete = false;
                }
            }
        }
        let mut found = Vec::new();
        let mut taken = BTreeSet::new();
        loop {
            let mut added = false;
            for (pid, unique, parent_unique) in &pending {
                if taken.contains(pid) {
                    continue;
                }
                let Some(&pstart) = creators.get(parent_unique) else {
                    continue;
                };
                let start = match info.bsd_info(*pid) {
                    Ok(b) => b.start_sec,
                    Err(SysError::Gone) => continue,
                    Err(_) => {
                        self.fail();
                        complete = false;
                        continue;
                    }
                };
                // a child cannot be older than its creator: else not ours
                if start < pstart {
                    continue;
                }
                taken.insert(*pid);
                creators.insert(*unique, start);
                uniques.insert(*pid, *unique);
                found.push(Found {
                    pid: *pid,
                    start,
                    unique: *unique,
                });
                added = true;
            }
            if !added {
                break;
            }
        }
        Adopt { found, complete }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use aib_sys::fake::{Call, FakeProcessInfo};

    const ME: u32 = 1;
    const ROOT: u32 = 100;

    fn on(f: FakeProcessInfo) -> (Lineage, Arc<FakeProcessInfo>) {
        let f = Arc::new(f);
        let l = Lineage::from_selfcheck(f.clone(), Ok(()));
        (l, f)
    }

    /// root (1000) created mid (2000), which exited; mid created orphan,
    /// now under launchd. A stranger under launchd is not ours.
    fn table() -> FakeProcessInfo {
        let f = FakeProcessInfo::new();
        f.add(ME, 0, 10, 1, 1);
        f.add(ROOT, ME, 1000, 10, 50);
        f.add(300, 1, 3000, 2000, 51); // orphan, creator mid is gone
        f.add(301, 300, 3010, 3000, 52); // orphan's own child
        f.add(400, 1, 4000, 9999, 60); // stranger
        f
    }

    fn known(v: &[(u32, u64)]) -> BTreeMap<u32, u64> {
        v.iter().copied().collect()
    }

    #[test]
    fn a_reparented_grandchild_is_found_through_its_creator() {
        let f = table();
        f.add(200, ROOT, 2000, 1000, 50); // mid, still listed for the first pass
        let (l, f) = on(f);
        let mut u = BTreeMap::new();
        let k = known(&[(ROOT, 50), (200, 50)]);
        let a = l.adopt(&k, &mut u, ME);
        assert!(a.complete);
        let pids: Vec<u32> = a.found.iter().map(|x| x.pid).collect();
        assert_eq!(pids, vec![300, 301]);
        f.remove(200);
        assert_eq!(l.failures(), 0);
    }

    #[test]
    fn a_creator_that_is_already_gone_was_cached_before() {
        // mid exited before the first pass: its unique id was never read,
        // so its orphan cannot be linked. That is the stated blind spot,
        // not a false match.
        let (l, _f) = on(table());
        let mut u = BTreeMap::new();
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut u, ME);
        assert!(a.found.is_empty(), "{a:?}");
        assert!(a.complete);
    }

    #[test]
    fn an_older_process_is_never_adopted() {
        let f = table();
        f.add(200, ROOT, 2000, 1000, 50);
        f.add(500, 1, 5000, 2000, 10); // claims mid as creator, but older than mid
        let (l, _f) = on(f);
        let mut u = BTreeMap::new();
        let a = l.adopt(&known(&[(ROOT, 50), (200, 50)]), &mut u, ME);
        assert!(!a.found.iter().any(|x| x.pid == 500), "{a:?}");
    }

    #[test]
    fn failures_are_counted_and_make_the_pass_incomplete() {
        let f = table();
        f.fail(Call::AllPids, 0, SysError::Incomplete);
        let (l, _f) = on(f);
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut BTreeMap::new(), ME);
        assert!(!a.complete);
        assert_eq!(l.failures(), 1);

        let f = table();
        f.fail(
            Call::UniqueIds,
            400,
            SysError::LengthMismatch { got: 48, want: 56 },
        );
        let (l, _f) = on(f);
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut BTreeMap::new(), ME);
        assert!(!a.complete);
        assert_eq!(l.failures(), 1);
    }

    #[test]
    fn another_accounts_process_is_skipped_without_a_failure() {
        let f = table();
        f.fail(Call::UniqueIds, 400, SysError::PermissionDenied);
        let (l, _f) = on(f);
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut BTreeMap::new(), ME);
        assert!(a.complete);
        assert_eq!(l.failures(), 0);
    }

    #[test]
    fn disabled_lineage_adopts_nothing_and_is_never_complete() {
        let l = Lineage::from_selfcheck(Arc::new(table()), Err(Failure::ChildParentUnique));
        assert!(!l.enabled());
        assert_eq!(l.status()["selfcheck"], "failed:child_parent_unique");
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut BTreeMap::new(), ME);
        assert_eq!(a, Adopt::default());
        assert!(!a.complete);
        assert!(!l.same_unique(ROOT, 1000));
    }

    #[test]
    fn same_unique_refuses_on_any_doubt() {
        let (l, f) = on(table());
        assert!(l.same_unique(ROOT, 1000));
        assert!(!l.same_unique(ROOT, 1001)); // reused pid
        f.remove(ROOT);
        assert!(!l.same_unique(ROOT, 1000)); // gone
        f.add(ROOT, ME, 1000, 10, 50);
        f.fail(Call::UniqueIds, ROOT, SysError::PermissionDenied);
        assert!(!l.same_unique(ROOT, 1000));
        assert_eq!(l.failures(), 1);
    }
}
