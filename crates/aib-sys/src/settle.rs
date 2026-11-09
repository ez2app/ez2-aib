//! Kill a Job's tree and say only what was seen (043dd 11-09; bb7dc #535,
//! a63ab "every unknown branch at once").
//!
//! The algorithm is platform-free over [`KillOps`] so every failure branch is
//! tested with a fake; `session::SessionTree::kill` supplies the Windows calls.
//!
//! Rules:
//! - One hard deadline for the Job to empty and every member's handle to
//!   signal. A Job's count drops when a process *starts* terminating; its
//!   handle signals after teardown (CI 128857333329), so "Job empty" proves
//!   nothing about a handle.
//! - Every question that got no answer (list failed, open failed, creation
//!   time unreadable, a wait failed, the root's exit not confirmed) puts that
//!   pid in `unknown` (or clears `listed` / `emptied`). Any of them = not
//!   clean. A missing handle is never taken as "exited".
//! - Only a failed terminate aborts (`Err`): nothing was killed.
//! - The root is waited for (blocking) only once its own handle signaled;
//!   otherwise one non-blocking look, and "not yet" or an error = unknown.

use std::time::{Duration, Instant};

/// What a kill actually saw (the session API's `stop`: `tree_size` =
/// `members.len()`; clean only when [`KillReport::all_gone`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KillReport {
    /// The member list could be read before the terminate.
    pub listed: bool,
    /// Pids in the Job just before the terminate (root + descendants).
    pub members: Vec<u32>,
    /// The Job reported empty within the deadline.
    pub emptied: bool,
    /// Members whose own handle still said "running" at the deadline.
    pub survivors: Vec<u32>,
    /// Members (or the root) whose state could not be established.
    pub unknown: Vec<u32>,
}

impl KillReport {
    /// Listed, the Job is empty, and every member (and the root) is seen
    /// exited: on Windows a proof (the Job contains the whole tree).
    pub fn all_gone(&self) -> bool {
        self.unconfirmed().is_none() && self.survivors.is_empty()
    }

    /// Why this is not a proof (a code), when nothing is known to survive.
    /// `None` when clean or when `survivors` already says why.
    pub fn unconfirmed(&self) -> Option<&'static str> {
        if !self.listed {
            Some("members_unlisted")
        } else if !self.unknown.is_empty() {
            Some("exit_unconfirmed")
        } else if !self.emptied {
            Some("not_emptied")
        } else {
            None
        }
    }
}

/// The OS calls a kill needs. Errors are folded into the report except
/// [`KillOps::terminate`]'s.
pub trait KillOps {
    type Handle;
    type Error;
    fn root_pid(&self) -> u32;
    /// (pid, creation time) of every process in the Job now.
    fn members(&mut self) -> Result<Vec<(u32, u64)>, Self::Error>;
    /// `Ok(None)` = no process with this pid (it already exited).
    fn open(&mut self, pid: u32) -> Result<Option<Self::Handle>, Self::Error>;
    fn pid_of(&self, h: &Self::Handle) -> u32;
    fn created(&mut self, h: &Self::Handle) -> Result<u64, Self::Error>;
    fn terminate(&mut self) -> Result<(), Self::Error>;
    /// `Ok(true)` = the Job emptied within `left`.
    fn wait_empty(&mut self, left: Duration) -> Result<bool, Self::Error>;
    /// `Ok(true)` = exited within `left` (zero still lets an exited one answer).
    fn wait(&mut self, h: &Self::Handle, left: Duration) -> Result<bool, Self::Error>;
    /// Collect the root's exit: `block` only after its handle signaled.
    /// `Ok(true)` = exited.
    fn reap_root(&mut self, block: bool) -> Result<bool, Self::Error>;
}

/// Kill the tree within `limit` and report what was seen.
pub fn kill_tree<K: KillOps>(k: &mut K, limit: Duration) -> Result<KillReport, K::Error> {
    let deadline = Instant::now() + limit;
    let left = || deadline.saturating_duration_since(Instant::now());
    let (listed, members) = match k.members() {
        Ok(m) => (true, m),
        Err(_) => (false, Vec::new()),
    };
    let mut unknown = Vec::new();
    // open each member before the terminate: the handle pins the pid, so the
    // answers below are about this process, not a reused pid
    let mut handles = Vec::with_capacity(members.len());
    for &(pid, created) in &members {
        match k.open(pid) {
            Ok(Some(h)) => match k.created(&h) {
                Ok(c) if c == created => handles.push(h),
                Ok(_) => {} // pid reused: the listed process is gone
                Err(_) => unknown.push(pid),
            },
            Ok(None) => {} // already gone
            Err(_) => unknown.push(pid),
        }
    }
    k.terminate()?;
    let emptied = k.wait_empty(left()).unwrap_or(false);
    let mut survivors = Vec::new();
    let mut root_signaled = false;
    let root = k.root_pid();
    for h in &handles {
        let pid = k.pid_of(h);
        match k.wait(h, left()) {
            Ok(true) => root_signaled |= pid == root,
            Ok(false) => survivors.push(pid),
            Err(_) => unknown.push(pid),
        }
    }
    let root_seen = survivors.contains(&root) || unknown.contains(&root);
    if root_signaled {
        let _ = k.reap_root(true); // its handle said exited: returns at once
    } else {
        let exited = matches!(k.reap_root(false), Ok(true));
        if !exited && !root_seen {
            unknown.push(root); // not opened, not confirmed: never "gone"
        }
    }
    Ok(KillReport {
        listed,
        members: members.iter().map(|m| m.0).collect(),
        emptied,
        survivors,
        unknown,
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, clippy::disallowed_methods)]
    use super::*;
    use std::collections::HashMap;

    #[derive(Clone, Copy, PartialEq)]
    enum W {
        Exits,
        Never,
        Fails,
    }

    /// A Job with members `pid -> (created, how its wait answers)`.
    struct Fake {
        root: u32,
        members: Result<Vec<(u32, u64)>, ()>,
        open_fails: Vec<u32>,
        gone: Vec<u32>,
        created_fails: Vec<u32>,
        reused: Vec<u32>,
        waits: HashMap<u32, W>,
        terminate: Result<(), ()>,
        empty: Result<bool, ()>,
        reap: Result<bool, ()>,
        reap_calls: Vec<bool>,
        terminated: bool,
    }

    fn fake(pids: &[(u32, W)]) -> Fake {
        Fake {
            root: pids[0].0,
            members: Ok(pids.iter().map(|&(p, _)| (p, 100 + u64::from(p))).collect()),
            open_fails: vec![],
            gone: vec![],
            created_fails: vec![],
            reused: vec![],
            waits: pids.iter().copied().collect(),
            terminate: Ok(()),
            empty: Ok(true),
            reap: Ok(true),
            reap_calls: vec![],
            terminated: false,
        }
    }

    impl KillOps for Fake {
        type Handle = u32;
        type Error = ();
        fn root_pid(&self) -> u32 {
            self.root
        }
        fn members(&mut self) -> Result<Vec<(u32, u64)>, ()> {
            self.members.clone()
        }
        fn open(&mut self, pid: u32) -> Result<Option<u32>, ()> {
            if self.open_fails.contains(&pid) {
                Err(())
            } else if self.gone.contains(&pid) {
                Ok(None)
            } else {
                Ok(Some(pid))
            }
        }
        fn pid_of(&self, h: &u32) -> u32 {
            *h
        }
        fn created(&mut self, h: &u32) -> Result<u64, ()> {
            if self.created_fails.contains(h) {
                Err(())
            } else if self.reused.contains(h) {
                Ok(1)
            } else {
                Ok(100 + u64::from(*h))
            }
        }
        fn terminate(&mut self) -> Result<(), ()> {
            self.terminated = true;
            self.terminate
        }
        fn wait_empty(&mut self, _left: Duration) -> Result<bool, ()> {
            self.empty
        }
        fn wait(&mut self, h: &u32, left: Duration) -> Result<bool, ()> {
            match self.waits[h] {
                W::Exits => Ok(true),
                W::Never => {
                    std::thread::sleep(left);
                    Ok(false)
                }
                W::Fails => Err(()),
            }
        }
        fn reap_root(&mut self, block: bool) -> Result<bool, ()> {
            self.reap_calls.push(block);
            self.reap
        }
    }

    const LIMIT: Duration = Duration::from_millis(100);

    fn run(f: &mut Fake) -> KillReport {
        kill_tree(f, LIMIT).unwrap()
    }

    #[test]
    fn everything_answered_and_exited_is_clean() {
        let mut f = fake(&[(10, W::Exits), (11, W::Exits)]);
        let r = run(&mut f);
        assert!(r.all_gone(), "{r:?}");
        assert_eq!(r.members, vec![10, 11]);
        assert_eq!(f.reap_calls, vec![true]); // root signaled: the blocking reap is safe
    }

    #[test]
    fn an_unreadable_member_list_still_terminates_and_is_not_clean() {
        let mut f = fake(&[(10, W::Exits)]);
        f.members = Err(());
        let r = run(&mut f);
        assert!(f.terminated);
        assert_eq!(r.unconfirmed(), Some("members_unlisted"));
        assert!(!r.all_gone());
    }

    #[test]
    fn a_member_that_cannot_be_opened_is_unknown() {
        let mut f = fake(&[(10, W::Exits), (11, W::Exits)]);
        f.open_fails = vec![11];
        let r = run(&mut f);
        assert_eq!(
            (r.unknown.clone(), r.unconfirmed()),
            (vec![11], Some("exit_unconfirmed"))
        );
    }

    #[test]
    fn an_unreadable_creation_time_is_unknown() {
        let mut f = fake(&[(10, W::Exits), (11, W::Exits)]);
        f.created_fails = vec![11];
        assert_eq!(run(&mut f).unknown, vec![11]);
    }

    #[test]
    fn a_reused_pid_means_the_listed_process_is_gone() {
        let mut f = fake(&[(10, W::Exits), (11, W::Never)]);
        f.reused = vec![11]; // never waited: it is someone else now
        let r = run(&mut f);
        assert!(r.all_gone(), "{r:?}");
    }

    #[test]
    fn a_failed_terminate_is_an_error_not_a_report() {
        let mut f = fake(&[(10, W::Exits)]);
        f.terminate = Err(());
        assert!(kill_tree(&mut f, LIMIT).is_err());
    }

    #[test]
    fn a_failed_or_timed_out_job_wait_is_not_emptied() {
        for empty in [Err(()), Ok(false)] {
            let mut f = fake(&[(10, W::Exits)]);
            f.empty = empty;
            let r = run(&mut f);
            assert_eq!(r.unconfirmed(), Some("not_emptied"), "{r:?}");
        }
    }

    #[test]
    fn a_failed_handle_wait_is_unknown_and_a_timeout_is_a_survivor() {
        let mut f = fake(&[(10, W::Exits), (11, W::Fails), (12, W::Never)]);
        let r = run(&mut f);
        assert_eq!(
            (r.unknown.clone(), r.survivors.clone()),
            (vec![11], vec![12])
        );
        assert!(!r.all_gone());
    }

    #[test]
    fn a_root_without_a_handle_whose_exit_is_not_confirmed_is_unknown() {
        // bb7dc 488a6d24: Job empty, root not opened, try_wait says "not yet"
        let mut f = fake(&[(10, W::Exits), (11, W::Exits)]);
        f.gone = vec![10];
        f.reap = Ok(false);
        let r = run(&mut f);
        assert_eq!(r.unknown, vec![10]);
        assert!(!r.all_gone(), "{r:?}");
        assert_eq!(f.reap_calls, vec![false]);
    }

    #[test]
    fn a_root_without_a_handle_whose_try_wait_fails_is_unknown() {
        let mut f = fake(&[(10, W::Exits)]);
        f.gone = vec![10];
        f.reap = Err(());
        assert_eq!(run(&mut f).unknown, vec![10]);
    }

    #[test]
    fn a_root_without_a_handle_that_try_wait_saw_exit_is_clean() {
        let mut f = fake(&[(10, W::Exits), (11, W::Exits)]);
        f.gone = vec![10];
        assert!(run(&mut f).all_gone());
    }

    #[test]
    fn a_root_still_running_is_never_waited_for_blocking() {
        let mut f = fake(&[(10, W::Never)]);
        f.reap = Ok(false);
        let t0 = Instant::now();
        let r = run(&mut f);
        assert_eq!(f.reap_calls, vec![false]);
        assert_eq!((r.survivors.clone(), r.unknown.clone()), (vec![10], vec![]));
        assert!(
            t0.elapsed() < Duration::from_millis(500),
            "{:?}",
            t0.elapsed()
        );
    }

    #[test]
    fn many_members_that_never_exit_share_one_deadline() {
        let mut f = fake(&[
            (10, W::Never),
            (11, W::Never),
            (12, W::Never),
            (13, W::Never),
        ]);
        f.reap = Ok(false);
        let t0 = Instant::now();
        let r = run(&mut f);
        assert_eq!(r.survivors, vec![10, 11, 12, 13]);
        assert!(
            t0.elapsed() < Duration::from_millis(400),
            "per-member waits: {:?}",
            t0.elapsed()
        );
    }
}
