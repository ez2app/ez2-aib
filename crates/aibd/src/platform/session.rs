//! Platform-neutral session interface (sessions API preview.2). The API layer
//! (`crate::sessions`, a9136) only ever talks to these types; each platform
//! provides one [`SpawnFn`]:
//!
//! - macOS / POSIX: process group + recorded tree (`tree.rs`, a9136);
//! - Windows: one Job per session under J0 (`session_windows.rs`, 043dd),
//!   UNVERIFIED until it runs on a real machine.
//!
//! File boundary: BOUNDARY-sessions-win-043dd-1109 (11-09). Contract:
//! CONTRACT-sessions-preview2-a9136-1109 + a63ab rulings (H1 / H2 / H3).
//!
//! Every call that can block ([`Running::stop`], [`ExitWait::wait`]) is run by
//! the API layer off the async runtime (`spawn_blocking` or its own thread).

use std::ffi::{OsStr, OsString};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use crate::lineage::Lineage;

/// What to start, already validated by the API layer (absolute `argv[0]`,
/// existing absolute `cwd`, allowed env keys only).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    /// `s_` + 16 hex; also the `AIBD_SESSION` marker value.
    pub id: String,
    pub argv: Vec<String>,
    pub cwd: PathBuf,
    /// Added on top of aibd's own environment minus every `AIBD_*` key; the
    /// platform then sets `AIBD_SESSION=<id>` last.
    pub env: Vec<(String, String)>,
}

/// How the root process ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Exit {
    Code(i32),
    /// POSIX only: the signal name, e.g. `SIGKILL`. Windows never uses it.
    Signal(String),
}

impl Exit {
    pub fn code(&self) -> Option<i32> {
        match self {
            Exit::Code(c) => Some(*c),
            Exit::Signal(_) => None,
        }
    }

    pub fn signal(&self) -> Option<&str> {
        match self {
            Exit::Code(_) => None,
            Exit::Signal(s) => Some(s),
        }
    }
}

/// `stop.status` (contract §1, DECISION-23).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StopStatus {
    /// Containment is proven: Windows only, when the Job is empty AND every
    /// handle opened beforehand says exited. Never on macOS.
    Clean,
    /// Nothing recorded is still running, but it cannot be proven that nothing
    /// escaped. The reason is a code from [`UNVERIFIED_REASONS`] or
    /// `job_error:<tag>` (Windows), never user text.
    Unverified(String),
    /// A recorded / marked process still runs (`survivors` lists them).
    TreeNotClean,
}

/// Every reason [`StopStatus::Unverified`] may carry, besides the Windows
/// `job_error:<tag>` family. Codes only; adding one is fine, renaming is not.
pub const UNVERIFIED_REASONS: &[&str] = &[
    // macOS: everything that can be seen is gone (the best macOS can say)
    "observed_clean",
    // M1 lineage off or a pass incomplete
    "lineage_unavailable",
    // the root was gone before it could be recorded
    "root_unrecorded",
    // the fork watcher knows it missed something
    "fork_watch_stopped",
    "fork_watch_unwatched",
    "fork_watch_list_failed",
    "fork_watch_child_unreadable",
    // DECISION-23 (c)/(d) candidates could not be read in full / at all
    "candidates_incomplete",
    "candidates_unreadable",
    // bb7dc H1: the root could not be kept unreaped (reaped elsewhere, SIGCHLD
    // auto-reaps): the group number is no longer provably ours, so no group
    // signal was sent after that point
    "root_identity_lost",
    // the stop itself failed (an OS error while listing / signalling)
    "stop_failed",
    // a report whose reason could not be named (not reached today)
    "unclassified",
];

/// The only `unverified` reason that gives the slot back (a63ab 11-09 M1,
/// whitelist): it LOOKED and saw nothing left. Every other reason is "could
/// not look" (job errors, a lost root, unreadable candidates, a fork-watch
/// miss, ...) and keeps the slot; so does any reason not classified here.
pub const SLOT_RELEASING_REASONS: &[&str] = &["observed_clean"];

/// The marker every session process inherits (`AIBD_SESSION=<id>`).
pub const SESSION_ENV: &str = "AIBD_SESSION";

/// Windows reason prefix: `job_error:<tag>` (043dd).
pub const JOB_ERROR_PREFIX: &str = "job_error:";

impl StopStatus {
    /// The wire value of `stop.status`.
    pub fn as_str(&self) -> &'static str {
        match self {
            StopStatus::Clean => "clean",
            StopStatus::Unverified(_) => "unverified",
            StopStatus::TreeNotClean => "tree_not_clean",
        }
    }

    /// The wire value of `stop.reason` (only for `unverified`).
    pub fn reason(&self) -> Option<&str> {
        match self {
            StopStatus::Unverified(r) => Some(r),
            _ => None,
        }
    }
}

/// One signal step: what was sent and to whom (Windows: `job_terminate`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Signal {
    pub signal: String,
    pub targets: Vec<u32>,
}

/// Result of one [`Running::stop`] (contract §1 `stop`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stop {
    pub status: StopStatus,
    pub survivors: Vec<u32>,
    pub tree_size: usize,
    pub signals: Vec<Signal>,
    /// What no layer here can rule out (macOS: always the DECISION-23 list).
    /// Windows: empty.
    pub blind_spots: Vec<&'static str>,
}

impl Stop {
    /// State after a stop (H2, a63ab 11-09): `killed` / `exited` only when
    /// clean; otherwise `stop_unconfirmed`.
    pub fn confirmed(&self) -> bool {
        self.status == StopStatus::Clean
    }

    /// May the running-session slot be given back (H2)? Only when no recorded
    /// process is known to still run. This says the OBSERVED concurrency went
    /// down, not that the tree is proven gone (bb7dc 11-09): blind spots stay
    /// in the record, and the limit is not a resource-isolation boundary.
    pub fn releases_slot(&self) -> bool {
        let looked = match &self.status {
            StopStatus::Clean => true,
            StopStatus::Unverified(r) => SLOT_RELEASING_REASONS.contains(&r.as_str()),
            StopStatus::TreeNotClean => false,
        };
        looked && self.survivors.is_empty()
    }

    /// The macOS / POSIX mapping of a tree [`super::tree::Report`].
    pub fn from_report(r: &super::tree::Report) -> Stop {
        let status = match r.clean_status() {
            "clean" => StopStatus::Clean,
            "tree_not_clean" => StopStatus::TreeNotClean,
            _ => StopStatus::Unverified(r.unverified_reason().unwrap_or("unclassified").to_owned()),
        };
        Stop {
            status,
            survivors: r.survivors.clone(),
            tree_size: r.tree_size,
            signals: r
                .steps
                .iter()
                .map(|s| Signal {
                    signal: s.signal.to_owned(),
                    targets: s.targets.clone(),
                })
                .collect(),
            blind_spots: r.blind_spots(),
        }
    }
}

/// Why a start failed (→ 422 `spawn_failed`). `tag` is a code such as
/// `enoent`, never a path or an argument.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpawnFail {
    pub tag: String,
    /// A process was started but could not be put under control nor
    /// confirmed gone (Windows: `Spawn(NotReaped)`, 043dd #539 M2): the API
    /// keeps the slot and records it. `None` = nothing is left running.
    pub left_running: Option<u32>,
}

impl SpawnFail {
    pub fn from_io(e: &std::io::Error) -> SpawnFail {
        use std::io::ErrorKind as K;
        let tag = match e.kind() {
            K::NotFound => "enoent".to_owned(),
            K::PermissionDenied => "eacces".to_owned(),
            _ => match e.raw_os_error() {
                Some(n) => format!("os_{n}"),
                None => "spawn_error".to_owned(),
            },
        };
        SpawnFail {
            tag,
            left_running: None,
        }
    }
}

/// Is `k` one of aibd's own variables? Case-insensitive on every platform:
/// Windows names are, and on POSIX dropping `aibd_x` too costs nothing.
pub fn is_aibd_key(k: &OsStr) -> bool {
    k.to_string_lossy()
        .get(..5)
        .is_some_and(|p| p.eq_ignore_ascii_case("AIBD_"))
}

fn same_key(a: &OsStr, b: &OsStr) -> bool {
    if cfg!(windows) {
        a.to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy())
    } else {
        a == b
    }
}

/// The child's whole environment (contract §1, one rule for both platforms,
/// 043dd #539 M3): `parent` (aibd's own) minus every `AIBD_*`, then the
/// request's `env` (replacing a parent entry of the same name; Windows
/// compares names without case), then `AIBD_SESSION=<id>` last.
pub fn child_env(
    spec: &Spec,
    parent: impl IntoIterator<Item = (OsString, OsString)>,
) -> Vec<(OsString, OsString)> {
    let mut out: Vec<(OsString, OsString)> = parent
        .into_iter()
        .filter(|(k, _)| !is_aibd_key(k))
        .filter(|(k, _)| !spec.env.iter().any(|(rk, _)| same_key(k, OsStr::new(rk))))
        .collect();
    out.extend(
        spec.env
            .iter()
            .filter(|(k, _)| !is_aibd_key(OsStr::new(k)))
            .map(|(k, v)| (OsString::from(k), OsString::from(v))),
    );
    out.push((OsString::from(SESSION_ENV), OsString::from(&spec.id)));
    out
}

/// Waits for the root's exit while someone else may hold the [`Running`]
/// (a stop can run meanwhile). Never reaps.
pub trait ExitWait: Send + Sync {
    /// The root's exit once it has exited, or `None` at the timeout.
    fn wait(&self, timeout: Duration) -> Option<Exit>;
}

/// One started session tree.
pub trait Running: Send {
    fn pid(&self) -> u32;
    /// Has the root exited? Never blocks, never reaps (macOS: M5 `WNOWAIT`;
    /// the root is reaped only after the last signal, H1).
    fn try_exit(&mut self) -> std::io::Result<Option<Exit>>;
    /// A waiter that does not borrow `self` (for the root-exit watcher).
    fn exit_wait(&self) -> Arc<dyn ExitWait>;
    /// Stop the whole tree; also the one stop after the root exited by
    /// itself. Blocks at most about `grace` + 4 s. Callable again (a second
    /// DELETE on `stop_unconfirmed` re-runs it, H2).
    fn stop(&mut self, grace: Duration) -> Stop;
}

/// What each platform provides (`platform::session_unix::spawn`,
/// `platform::session_windows::spawn`). `lineage` is the shared M1 lineage
/// (macOS; `None` = off, and ignored elsewhere).
pub type SpawnFn = fn(&Spec, Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail>;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::tree::{Report, Step};

    #[test]
    fn status_wire_values_and_reason() {
        assert_eq!(StopStatus::Clean.as_str(), "clean");
        assert_eq!(StopStatus::TreeNotClean.as_str(), "tree_not_clean");
        let u = StopStatus::Unverified("observed_clean".into());
        assert_eq!(
            (u.as_str(), u.reason()),
            ("unverified", Some("observed_clean"))
        );
        assert_eq!(StopStatus::Clean.reason(), None);
    }

    fn stop(status: StopStatus, survivors: Vec<u32>) -> Stop {
        Stop {
            status,
            survivors,
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
        }
    }

    #[test]
    fn only_clean_is_confirmed_and_survivors_keep_the_slot() {
        let unv = stop(StopStatus::Unverified("observed_clean".into()), vec![]);
        assert!(!unv.confirmed());
        assert!(unv.releases_slot());
        assert!(stop(StopStatus::Clean, vec![]).confirmed());
        let left = stop(StopStatus::TreeNotClean, vec![42]);
        assert!(!left.confirmed());
        assert!(!left.releases_slot());
        // whitelist (a63ab M1): every "could not look" reason keeps the slot,
        // and so does a reason nobody classified
        for r in UNVERIFIED_REASONS {
            let s = stop(StopStatus::Unverified((*r).into()), vec![]);
            assert_eq!(s.releases_slot(), *r == "observed_clean", "{r}");
        }
        assert!(!stop(StopStatus::Unverified("something_new".into()), vec![]).releases_slot());
        // nothing knowable: Windows job errors and the H1 latch keep the slot
        assert!(
            !stop(StopStatus::Unverified("job_error:terminate".into()), vec![]).releases_slot()
        );
        assert!(!stop(StopStatus::Unverified("root_identity_lost".into()), vec![]).releases_slot());
        assert!(!stop(StopStatus::Unverified("stop_failed".into()), vec![]).releases_slot());
        // a status that disagrees with its survivor list never frees the slot
        assert!(!stop(StopStatus::TreeNotClean, vec![]).releases_slot());
        assert!(!stop(StopStatus::Unverified("x".into()), vec![7]).releases_slot());
    }

    #[test]
    fn a_macos_report_maps_to_unverified_with_blind_spots_never_clean() {
        let r = Report {
            steps: vec![Step {
                signal: "SIGTERM(group)",
                targets: vec![10],
            }],
            tree_size: 3,
            candidates_complete: true,
            ..Report::default()
        };
        let s = Stop::from_report(&r);
        assert_eq!(s.status, StopStatus::Unverified("observed_clean".into()));
        assert!(!s.blind_spots.is_empty());
        assert_eq!(s.signals[0].signal, "SIGTERM(group)");
        assert_eq!(s.tree_size, 3);
        let left = Report {
            survivors: vec![11],
            ..r
        };
        assert_eq!(Stop::from_report(&left).status, StopStatus::TreeNotClean);
    }

    #[test]
    fn every_macos_reason_is_listed() {
        for r in [
            Report::default(),
            Report {
                candidates_complete: true,
                ..Report::default()
            },
            Report {
                candidates_complete: true,
                lineage_unavailable: true,
                ..Report::default()
            },
            Report {
                candidates_complete: true,
                candidates_unreadable: vec![5],
                ..Report::default()
            },
            Report {
                candidates_complete: true,
                known_gaps: vec![crate::platform::tree::GAP_ROOT_UNRECORDED],
                ..Report::default()
            },
        ] {
            let reason = r.unverified_reason().unwrap();
            assert!(UNVERIFIED_REASONS.contains(&reason), "{reason} missing");
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn every_fork_watch_gap_is_listed() {
        use crate::platform::fork_watch as fw;
        for g in [
            fw::GAP_STOPPED,
            fw::GAP_UNWATCHED,
            fw::GAP_LIST_FAILED,
            fw::GAP_CHILD_UNREADABLE,
        ] {
            assert!(UNVERIFIED_REASONS.contains(&g), "{g} missing");
        }
    }

    fn os(a: &str, b: &str) -> (OsString, OsString) {
        (a.into(), b.into())
    }

    #[test]
    fn child_env_drops_aibd_keys_applies_the_request_and_sets_the_marker_last() {
        let spec = Spec {
            id: "s_0123456789abcdef".into(),
            argv: vec!["/bin/true".into()],
            cwd: "/".into(),
            env: vec![("LANG".into(), "C".into()), ("MY".into(), "1".into())],
        };
        let parent = vec![
            os("AIBD_TOKEN_FILE", "x"),
            os("aibd_lower", "x"),
            os("AIBD_SESSION", "s_parent"),
            os("LANG", "en_US.UTF-8"),
            os("HOME", "/h"),
        ];
        let env = child_env(&spec, parent);
        let keys: Vec<String> = env
            .iter()
            .map(|(k, _)| k.to_string_lossy().into())
            .collect();
        assert_eq!(keys, ["HOME", "LANG", "MY", "AIBD_SESSION"], "{env:?}");
        assert_eq!(
            env.last().unwrap(),
            &os("AIBD_SESSION", "s_0123456789abcdef")
        );
        assert!(env.contains(&os("LANG", "C")));
    }

    #[cfg(windows)]
    #[test]
    fn child_env_replaces_a_parent_name_without_case_on_windows() {
        let spec = Spec {
            id: "s_1".into(),
            argv: vec!["C:\\x.exe".into()],
            cwd: "C:\\".into(),
            env: vec![("TEMP".into(), "D:\\t".into())],
        };
        let env = child_env(&spec, vec![os("Temp", "C:\\t"), os("Aibd_X", "1")]);
        assert_eq!(env, vec![os("TEMP", "D:\\t"), os("AIBD_SESSION", "s_1")]);
    }

    #[test]
    fn spawn_fail_tags_carry_no_path() {
        let e = std::io::Error::new(std::io::ErrorKind::NotFound, "/secret/path");
        assert_eq!(SpawnFail::from_io(&e).tag, "enoent");
        let e = std::io::Error::from(std::io::ErrorKind::PermissionDenied);
        assert_eq!(SpawnFail::from_io(&e).tag, "eacces");
        let e = std::io::Error::other("no errno");
        assert_eq!(SpawnFail::from_io(&e).tag, "spawn_error");
    }
}
