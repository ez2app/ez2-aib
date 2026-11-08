//! Start-up self-check for M1 (DECISION-4 A-2, 2026-11-07).
//!
//! Matching structure lengths is not enough: the private SPI could keep its
//! size and change its meaning. So aibd checks M1 against the public
//! parent/child relation (M2, M3) once at start-up, on itself and on one
//! child it creates. Any failure ⇒ the caller disables M1 until restart,
//! records [`Failure::tag`] in metrics, and marks cancel reports
//! `lineage_unavailable: true`.
//!
//! Re-parenting is not checked here (it needs an extra intermediate process
//! and time); the tests and the release check cover it.

use crate::{ProcessInfo, SysError};

/// Why M1 was switched off.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Failure {
    /// M1 failed on our own pid.
    OwnIds(SysError),
    /// M1 returned a zero `unique` or `parent_unique` for us.
    OwnIdsZero,
    /// The probe child could not be started.
    Spawn,
    /// M1 failed on the child.
    ChildIds(SysError),
    /// The child's `parent_unique` is not our `unique`.
    ChildParentUnique,
    /// M2 failed on the child.
    ChildInfo(SysError),
    /// M2 says the child's parent is not us.
    ChildPpid,
    /// M3 failed on us.
    List(SysError),
    /// M3 does not list the child under us.
    ChildNotListed,
}

impl Failure {
    /// Stable tag for metrics: `lineage_selfcheck=failed:<tag>`.
    pub fn tag(&self) -> String {
        match self {
            Failure::OwnIds(e) => format!("own_ids:{}", e.tag()),
            Failure::OwnIdsZero => "own_ids_zero".into(),
            Failure::Spawn => "spawn".into(),
            Failure::ChildIds(e) => format!("child_ids:{}", e.tag()),
            Failure::ChildParentUnique => "child_parent_unique".into(),
            Failure::ChildInfo(e) => format!("child_info:{}", e.tag()),
            Failure::ChildPpid => "child_ppid".into(),
            Failure::List(e) => format!("list:{}", e.tag()),
            Failure::ChildNotListed => "child_not_listed".into(),
        }
    }
}

/// The checks, given our pid and a live child we created.
pub fn check(info: &dyn ProcessInfo, me: u32, child: u32) -> Result<(), Failure> {
    let own = info.unique_ids(me).map_err(Failure::OwnIds)?;
    if own.unique == 0 || own.parent_unique == 0 {
        return Err(Failure::OwnIdsZero);
    }
    let kid = info.unique_ids(child).map_err(Failure::ChildIds)?;
    if kid.parent_unique != own.unique {
        return Err(Failure::ChildParentUnique);
    }
    let kinfo = info.bsd_info(child).map_err(Failure::ChildInfo)?;
    if kinfo.ppid != me {
        return Err(Failure::ChildPpid);
    }
    if !info.children(me).map_err(Failure::List)?.contains(&child) {
        return Err(Failure::ChildNotListed);
    }
    Ok(())
}

/// Run [`check`] on this process with a short-lived `/bin/sleep` child,
/// which is killed and reaped before returning (only that child: its pid is
/// the one we started).
#[cfg(unix)]
pub fn run(info: &dyn ProcessInfo) -> Result<(), Failure> {
    use std::process::{Command, Stdio};
    let mut child = Command::new("/bin/sleep")
        .arg("30")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| Failure::Spawn)?;
    let r = check(info, std::process::id(), child.id());
    let _ = child.kill();
    let _ = child.wait();
    r
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fake::{Call, FakeProcessInfo};

    const ME: u32 = 100;
    const KID: u32 = 200;

    fn healthy() -> FakeProcessInfo {
        let f = FakeProcessInfo::new();
        f.add(ME, 1, 1000, 1, 10);
        f.add(KID, ME, 2000, 1000, 11);
        f
    }

    #[test]
    fn a_consistent_table_passes() {
        assert_eq!(check(&healthy(), ME, KID), Ok(()));
    }

    #[test]
    fn each_failure_has_its_own_tag() {
        let f = healthy();
        f.fail(
            Call::UniqueIds,
            ME,
            SysError::LengthMismatch { got: 48, want: 56 },
        );
        assert_eq!(
            check(&f, ME, KID).unwrap_err().tag(),
            "own_ids:length_mismatch"
        );

        let f = healthy();
        f.set_unique(ME, 0, 1);
        assert_eq!(check(&f, ME, KID), Err(Failure::OwnIdsZero));

        let f = healthy();
        f.set_unique(ME, 1000, 0);
        assert_eq!(check(&f, ME, KID), Err(Failure::OwnIdsZero));

        let f = healthy();
        f.fail(Call::UniqueIds, KID, SysError::PermissionDenied);
        assert_eq!(
            check(&f, ME, KID).unwrap_err().tag(),
            "child_ids:permission_denied"
        );

        // the SPI keeps its size but means something else
        let f = healthy();
        f.set_unique(KID, 2000, 999);
        assert_eq!(check(&f, ME, KID), Err(Failure::ChildParentUnique));

        let f = healthy();
        f.fail(Call::BsdInfo, KID, SysError::Gone);
        assert_eq!(check(&f, ME, KID).unwrap_err().tag(), "child_info:gone");

        let f = healthy();
        f.reparent(KID, 1);
        assert_eq!(check(&f, ME, KID), Err(Failure::ChildPpid));

        let f = healthy();
        f.fail(Call::Children, ME, SysError::Incomplete);
        assert_eq!(check(&f, ME, KID).unwrap_err().tag(), "list:incomplete");
    }

    /// A listing that misses the child is caught, although M1 and M2 agree.
    #[test]
    fn a_child_missing_from_the_list_fails() {
        struct Hide(FakeProcessInfo);
        impl ProcessInfo for Hide {
            fn unique_ids(&self, p: u32) -> Result<crate::UniqueIds, SysError> {
                self.0.unique_ids(p)
            }
            fn bsd_info(&self, p: u32) -> Result<crate::BsdInfo, SysError> {
                self.0.bsd_info(p)
            }
            fn children(&self, _: u32) -> Result<Vec<u32>, SysError> {
                Ok(Vec::new())
            }
            fn all_pids(&self) -> Result<Vec<u32>, SysError> {
                self.0.all_pids()
            }
        }
        assert_eq!(
            check(&Hide(healthy()), ME, KID),
            Err(Failure::ChildNotListed)
        );
    }
}
