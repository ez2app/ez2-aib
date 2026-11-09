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
use aib_sys::{ProcessInfo, SysError, UniqueIds};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

pub struct Lineage {
    info: Option<Arc<dyn ProcessInfo>>,
    selfcheck: String,
    failures: AtomicU64,
    /// G1: candidates the widened orphan check added over the narrow one,
    /// summed over every check (measured first, narrowed later if needed).
    orphan_extra: AtomicU64,
}

impl std::fmt::Debug for Lineage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Lineage")
            .field("enabled", &self.enabled())
            .field("selfcheck", &self.selfcheck)
            .field("failures", &self.failures())
            .field("orphan_candidates_extra", &self.orphan_candidates_extra())
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
            orphan_extra: AtomicU64::new(0),
        }
    }

    /// Keep `info` only if the self-check passed.
    pub fn from_selfcheck(info: Arc<dyn ProcessInfo>, check: Result<(), Failure>) -> Self {
        match check {
            Ok(()) => Lineage {
                info: Some(info),
                selfcheck: "ok".into(),
                failures: AtomicU64::new(0),
                orphan_extra: AtomicU64::new(0),
            },
            Err(f) => Self::disabled(&format!("failed:{}", f.tag())),
        }
    }

    /// The real thing: where the platform has a lineage reader (macOS), the
    /// self-check runs on this process.
    pub fn detect() -> Self {
        use crate::platform::lifecycle::{Current, Lifecycle, Step};
        match <Current as Lifecycle>::lineage() {
            Step::Done(l) => l,
            Step::Unsupported { .. } | Step::NotApplicable { .. } => Self::disabled("unsupported"),
        }
    }

    /// The self-check result: `ok`, `failed:<tag>`, `unsupported`, ...
    pub fn selfcheck_status(&self) -> &str {
        &self.selfcheck
    }

    pub fn enabled(&self) -> bool {
        self.info.is_some()
    }

    pub fn failures(&self) -> u64 {
        self.failures.load(Ordering::Relaxed)
    }

    /// G1: see the `orphan_extra` field.
    pub fn orphan_candidates_extra(&self) -> u64 {
        self.orphan_extra.load(Ordering::Relaxed)
    }

    /// `/api/v1/metrics` view.
    pub fn status(&self) -> serde_json::Value {
        serde_json::json!({
            "enabled": self.enabled(),
            "selfcheck": self.selfcheck,
            "failures": self.failures(),
            "orphan_candidates_extra": self.orphan_candidates_extra(),
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
    /// `cache` keeps the M1 read of every process that was not ours, so the
    /// next pass of the same cancel reads only new ones (043dd #510 M4).
    pub fn adopt(
        &self,
        known: &BTreeMap<u32, u64>,
        uniques: &mut BTreeMap<u32, u64>,
        me: u32,
        cache: &mut M1Cache,
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
                // 043dd #510 H2: a recorded process gone before its unique id
                // was read — whatever it re-parented can never be linked back,
                // so this pass cannot claim to be complete
                Err(SysError::Gone) => complete = false,
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
            let seen_start = cache.starts.get(&pid).copied();
            let hit = seen_start.and_then(|st| {
                cache
                    .m1
                    .get(&pid)
                    .filter(|(s, _)| *s == st)
                    .map(|(_, r)| *r)
            });
            let ids = match hit {
                Some(r) => r,
                None => {
                    let r = match info.unique_ids(pid) {
                        Ok(u) => Some(u),
                        // gone, or another account's process: not ours either way
                        Err(SysError::Gone | SysError::PermissionDenied) => None,
                        Err(_) => {
                            self.fail();
                            complete = false;
                            continue; // never cached: read again next pass
                        }
                    };
                    if let Some(st) = seen_start {
                        cache.m1.insert(pid, (st, r));
                    }
                    r
                }
            };
            if let Some(u) = ids {
                pending.push((pid, u.unique, u.parent_unique));
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

    /// DECISION-23 (c)/(d): processes that look like "re-parented, then
    /// exec'd" and so may belong to the session without lineage seeing them:
    /// same uid, `ppid` 1, `parent_unique` 1 (launchd), and created after the
    /// session root (`unique` > `root_unique`). Pids in `skip` (already known)
    /// and `me` are left out. Never signals anything; the caller only checks
    /// their environment. `complete` is false when any read failed.
    ///
    /// `wide` (G1): a child of the session was gone before its unique id was
    /// read, so its own children carry a `parent_unique` nobody recorded.
    /// Then every same-uid `ppid` 1 process newer than the root is a
    /// candidate, whatever its `parent_unique`. How many that adds is
    /// `Candidates::extra` (and summed in the metrics).
    pub fn late_exec_candidates(
        &self,
        root_unique: u64,
        me: u32,
        skip: &BTreeMap<u32, u64>,
        wide: bool,
    ) -> Candidates {
        let Some(info) = self.info.as_ref() else {
            return Candidates::default();
        };
        // our own uid: a session's processes keep it
        let uid = match info.bsd_info(me) {
            Ok(b) => b.uid,
            Err(_) => {
                self.fail();
                return Candidates::default();
            }
        };
        let all = match info.all_pids() {
            Ok(a) => a,
            Err(_) => {
                self.fail();
                return Candidates::default();
            }
        };
        let mut c = Candidates {
            pids: Vec::new(),
            complete: true,
            extra: 0,
        };
        for pid in all {
            if pid == me || skip.contains_key(&pid) {
                continue;
            }
            let b = match info.bsd_info(pid) {
                Ok(b) => b,
                // gone, or another account's process (a child keeps our uid)
                Err(SysError::Gone | SysError::PermissionDenied) => continue,
                Err(_) => {
                    self.fail();
                    c.complete = false;
                    continue;
                }
            };
            if b.ppid != 1 || b.uid != uid {
                continue;
            }
            match info.unique_ids(pid) {
                Ok(u) if u.unique > root_unique && u.parent_unique == 1 => c.pids.push(pid),
                Ok(u) if u.unique > root_unique && wide => {
                    c.pids.push(pid);
                    c.extra += 1;
                }
                Ok(_) | Err(SysError::Gone) => {}
                Err(_) => {
                    self.fail();
                    c.complete = false;
                }
            }
        }
        self.orphan_extra
            .fetch_add(c.extra as u64, Ordering::Relaxed);
        c
    }
}

/// M1 reads kept between passes of one cancel (043dd #510 M4). An entry is
/// used only while the caller saw the same start time for that pid this pass
/// (`starts`, from the caller's own process listing): a reused pid is read
/// again. Without a start time nothing is cached.
#[derive(Debug, Default)]
pub struct M1Cache {
    /// pid -> start time the caller saw in this pass
    pub starts: BTreeMap<u32, u64>,
    m1: BTreeMap<u32, (u64, Option<UniqueIds>)>,
}

/// Result of [`Lineage::late_exec_candidates`]. `Default` = incomplete.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Candidates {
    pub pids: Vec<u32>,
    pub complete: bool,
    /// How many of `pids` only the widened check (`wide`) added.
    pub extra: usize,
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

    /// DECISION-23 (c)/(d): only same-uid, ppid 1, parent_unique 1 and newer
    /// than the root count; known ones and `me` are left out.
    #[test]
    fn late_exec_candidates_are_new_same_uid_launchd_children_only() {
        let f = table();
        f.add(500, 1, 5000, 1, 70); // re-parented then exec'd: candidate
        f.add(501, 1, 900, 1, 40); // launchd child older than the root: no
        f.add(502, 1, 5020, 1, 71); // other account: no
        f.set_uid(502, 0);
        f.add(503, 7, 5030, 1, 72); // ppid not 1: no
        f.add(504, 1, 5040, 7777, 73); // creator recorded (lineage's job): no
        f.add(505, 1, 5050, 1, 74); // already known: no
        let (l, _f) = on(f);
        let c = l.late_exec_candidates(1000, ME, &known(&[(ROOT, 50), (505, 74)]), false);
        assert_eq!(
            c,
            Candidates {
                pids: vec![500],
                complete: true,
                extra: 0,
            }
        );
    }

    /// G1 / 043dd 20:42: a child gone before it was read leaves orphans whose
    /// `parent_unique` names a dead, unrecorded process. The narrow check
    /// (`parent_unique == 1`) misses them; the wide one takes every same-uid
    /// launchd child newer than the root, and says how many it added.
    #[test]
    fn an_unread_gone_child_widens_the_orphan_candidates() {
        let f = table();
        f.add(50, 1, 900_000, 77, 80); // orphan: creator 77 is dead and unrecorded
        f.add(51, 1, 900_001, 1, 81); // launchd child newer than the root
        f.add(52, 1, 700, 77, 30); // orphan older than the root: not ours
        f.add(53, 1, 900_002, 77, 82); // other account: not ours
        f.set_uid(53, 0);
        let (l, _f) = on(f);
        let k = known(&[(ROOT, 50)]);
        let narrow = l.late_exec_candidates(1000, ME, &k, false);
        assert!(!narrow.pids.contains(&50), "{narrow:?}");
        assert!(narrow.pids.contains(&51), "{narrow:?}");
        assert_eq!(narrow.extra, 0);
        assert_eq!(l.orphan_candidates_extra(), 0);
        let wide = l.late_exec_candidates(1000, ME, &k, true);
        assert!(
            wide.pids.contains(&50) && wide.pids.contains(&51),
            "{wide:?}"
        );
        assert!(
            !wide.pids.contains(&52) && !wide.pids.contains(&53),
            "{wide:?}"
        );
        // table(): 300 (creator 2000, dead) and 400 (creator 9999) are newer
        // than the root too, so the wide check adds them as well
        let added: Vec<u32> = wide
            .pids
            .iter()
            .copied()
            .filter(|p| !narrow.pids.contains(p))
            .collect();
        assert_eq!(wide.extra, added.len(), "{wide:?}");
        assert!(added.contains(&50), "{added:?}");
        assert_eq!(l.orphan_candidates_extra(), added.len() as u64);
        assert_eq!(l.status()["orphan_candidates_extra"], added.len() as u64);
    }

    #[test]
    fn a_failed_read_makes_the_candidate_list_incomplete() {
        let f = table();
        f.add(500, 1, 5000, 1, 70);
        f.fail(Call::UniqueIds, 500, SysError::Incomplete);
        let (l, _f) = on(f);
        let c = l.late_exec_candidates(1000, ME, &known(&[(ROOT, 50)]), false);
        assert!(!c.complete, "{c:?}");

        let f = table();
        f.fail(Call::BsdInfo, 400, SysError::Incomplete);
        let (l, _f) = on(f);
        assert!(
            !l.late_exec_candidates(1000, ME, &BTreeMap::new(), false)
                .complete
        );

        let f = table();
        f.fail(Call::AllPids, 0, SysError::Incomplete);
        let (l, _f) = on(f);
        assert!(
            !l.late_exec_candidates(1000, ME, &BTreeMap::new(), false)
                .complete
        );

        // lineage off: never complete
        let l = Lineage::disabled("test");
        assert!(
            !l.late_exec_candidates(1000, ME, &BTreeMap::new(), false)
                .complete
        );
    }

    #[test]
    fn a_reparented_grandchild_is_found_through_its_creator() {
        let f = table();
        f.add(200, ROOT, 2000, 1000, 50); // mid, still listed for the first pass
        let (l, f) = on(f);
        let mut u = BTreeMap::new();
        let k = known(&[(ROOT, 50), (200, 50)]);
        let a = l.adopt(&k, &mut u, ME, &mut M1Cache::default());
        assert!(a.complete);
        let pids: Vec<u32> = a.found.iter().map(|x| x.pid).collect();
        assert_eq!(pids, vec![300, 301]);
        f.remove(200);
        assert_eq!(l.failures(), 0);
    }

    #[test]
    fn a_recorded_process_gone_before_its_unique_id_was_read_makes_the_pass_incomplete() {
        // 043dd #510 H2: recorded by a scan (no unique id), exited before the pass
        let f = table();
        f.add(200, ROOT, 2000, 1000, 50);
        let (l, f) = on(f);
        let mut u = BTreeMap::from([(ROOT, 1000)]);
        f.remove(200);
        let a = l.adopt(
            &known(&[(ROOT, 50), (200, 50)]),
            &mut u,
            ME,
            &mut M1Cache::default(),
        );
        assert!(!a.complete, "{a:?}");
        // the same process with its unique id read while alive: complete, and its orphan found
        let f = table();
        let (l, _f) = on(f);
        let mut u = BTreeMap::from([(ROOT, 1000), (200, 2000)]);
        let a = l.adopt(
            &known(&[(ROOT, 50), (200, 50)]),
            &mut u,
            ME,
            &mut M1Cache::default(),
        );
        assert!(a.complete, "{a:?}");
        assert!(a.found.iter().any(|x| x.pid == 300), "{a:?}");
    }

    #[test]
    fn a_second_pass_reads_m1_only_for_new_or_restarted_pids() {
        // 043dd #510 M4: wait_gone runs a pass every 50 ms; strangers are read once
        let f = table();
        let starts: BTreeMap<u32, u64> =
            [(ME, 1), (ROOT, 50), (300, 51), (301, 52), (400, 60)].into();
        let (l, f) = on(f);
        let mut cache = M1Cache {
            starts: starts.clone(),
            ..M1Cache::default()
        };
        let k = known(&[(ROOT, 50)]);
        let mut u = BTreeMap::from([(ROOT, 1000)]);
        l.adopt(&k, &mut u, ME, &mut cache);
        let first = f.calls(Call::UniqueIds);
        l.adopt(&k, &mut u, ME, &mut cache);
        assert_eq!(
            f.calls(Call::UniqueIds),
            first,
            "unchanged strangers were read again"
        );
        // pid 400 restarted (other start time): read again, and only it
        cache.starts.insert(400, 61);
        l.adopt(&k, &mut u, ME, &mut cache);
        assert_eq!(f.calls(Call::UniqueIds), first + 1);
    }

    #[test]
    fn a_creator_that_is_already_gone_was_cached_before() {
        // mid exited before the first pass: its unique id was never read,
        // so its orphan cannot be linked. That is the stated blind spot,
        // not a false match.
        let (l, _f) = on(table());
        let mut u = BTreeMap::new();
        let a = l.adopt(&known(&[(ROOT, 50)]), &mut u, ME, &mut M1Cache::default());
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
        let a = l.adopt(
            &known(&[(ROOT, 50), (200, 50)]),
            &mut u,
            ME,
            &mut M1Cache::default(),
        );
        assert!(!a.found.iter().any(|x| x.pid == 500), "{a:?}");
    }

    #[test]
    fn failures_are_counted_and_make_the_pass_incomplete() {
        let f = table();
        f.fail(Call::AllPids, 0, SysError::Incomplete);
        let (l, _f) = on(f);
        let a = l.adopt(
            &known(&[(ROOT, 50)]),
            &mut BTreeMap::new(),
            ME,
            &mut M1Cache::default(),
        );
        assert!(!a.complete);
        assert_eq!(l.failures(), 1);

        let f = table();
        f.fail(
            Call::UniqueIds,
            400,
            SysError::LengthMismatch { got: 48, want: 56 },
        );
        let (l, _f) = on(f);
        let a = l.adopt(
            &known(&[(ROOT, 50)]),
            &mut BTreeMap::new(),
            ME,
            &mut M1Cache::default(),
        );
        assert!(!a.complete);
        assert_eq!(l.failures(), 1);
    }

    #[test]
    fn another_accounts_process_is_skipped_without_a_failure() {
        let f = table();
        f.fail(Call::UniqueIds, 400, SysError::PermissionDenied);
        let (l, _f) = on(f);
        let a = l.adopt(
            &known(&[(ROOT, 50)]),
            &mut BTreeMap::new(),
            ME,
            &mut M1Cache::default(),
        );
        assert!(a.complete);
        assert_eq!(l.failures(), 0);
    }

    #[test]
    fn disabled_lineage_adopts_nothing_and_is_never_complete() {
        let l = Lineage::from_selfcheck(Arc::new(table()), Err(Failure::ChildParentUnique));
        assert!(!l.enabled());
        assert_eq!(l.status()["selfcheck"], "failed:child_parent_unique");
        let a = l.adopt(
            &known(&[(ROOT, 50)]),
            &mut BTreeMap::new(),
            ME,
            &mut M1Cache::default(),
        );
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
