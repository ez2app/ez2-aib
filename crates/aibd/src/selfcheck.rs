//! `aibd selfcheck`: what this build can do on this machine, said plainly.
//!
//! Every row is one capability with one status. Nothing is reported as fine
//! that was not checked here, and nothing that only exists as library code is
//! reported as working (a63ab PLAN-rust-preview, 2026-11-08):
//!
//! - process management: really started and stopped once (contract §6):
//!   `/bin/sh -c 'sleep 30 & wait'` → stop → the stop's own answer. macOS
//!   can never prove a clean stop, so no survivor seen = `unverified`
//!   (reason, e.g. `observed_clean`); a survivor = `failed:tree_not_clean`;
//!   a platform without sessions in this build = `not_wired`;
//! - Windows Job Object: checked on GitHub's Windows runner only, never on a
//!   real machine ⇒ `unverified`;
//! - macOS lineage (M1): even when its start-up check passes it only adds
//!   candidates, so it is `unverified`, never "clean" (DECISION-23).
//!
//! The exit code is 1 only when a row `failed`; `unverified`, `unsupported`
//! and `not_wired` are honest answers, not failures.

use crate::config::Config;
use crate::lineage::Lineage;
use crate::platform::session::{SpawnFail, SpawnFn, Spec, Stop, StopStatus};
use crate::platform::ChildReaping;
use std::sync::Arc;
use std::time::Duration;

/// One capability's answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    Ok,
    /// Checked here and it failed; the tag is stable (for scripts / metrics).
    Failed(String),
    /// Present, but not proven on this machine (or not provable as clean).
    Unverified,
    /// Does not exist on this platform.
    Unsupported,
    /// Library code exists, nothing in this build uses it yet.
    NotWired,
}

impl Status {
    /// `ok` | `failed:<tag>` | `unverified` | `unsupported` | `not_wired`.
    pub fn label(&self) -> String {
        match self {
            Status::Ok => "ok".into(),
            Status::Failed(tag) => format!("failed:{tag}"),
            Status::Unverified => "unverified".into(),
            Status::Unsupported => "unsupported".into(),
            Status::NotWired => "not_wired".into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub name: &'static str,
    pub status: Status,
    pub detail: String,
    /// Extra facts as key / value (e.g. `wait_mode`), flattened into the JSON.
    pub notes: Vec<(&'static str, String)>,
}

impl Row {
    fn new(name: &'static str, status: Status, detail: impl Into<String>) -> Self {
        Row {
            name,
            status,
            detail: detail.into(),
            notes: Vec::new(),
        }
    }
}

/// `aibd 0.2.0-preview.1 macos-aarch64`
pub fn version_line() -> String {
    format!(
        "aibd {} {}-{}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    )
}

/// What [`check_process_management`] saw: the stop, and whether the tree's
/// child had reported it started before the stop (bb7dc #549 3bacd239: a
/// stop that only met the root is not a tree stopped).
#[derive(Debug)]
pub struct Checked {
    pub stop: Stop,
    pub child_started: bool,
    /// The root was still running right before the stop. A tree that had
    /// already ended was not stopped by us: the row cannot say it was
    /// (a9136 11-09, found by the leaf-ends-at-once knife).
    pub tree_running: bool,
}

/// How long the check waits for its child to report it started.
pub const CHILD_WAIT: Duration = Duration::from_secs(3);

/// The check tree: a shell that starts `sleep`, then writes the child's pid
/// to the marker file (`$1`) and waits.
pub const CHECK_SCRIPT: &str = "sleep 30 & echo $! > \"$1\"; wait";

/// Start one small tree (a shell with a child) and stop it, the way a
/// session is started and stopped: the stop comes only once the child has
/// reported it started, or after [`CHILD_WAIT`] (then `child_started` is
/// false and the row cannot say a tree was stopped). Blocks at most about
/// 3 s + 2 s + the stop's checks.
pub fn check_process_management(
    spawn: SpawnFn,
    lineage: Option<Arc<Lineage>>,
) -> Result<Checked, SpawnFail> {
    use crate::platform::lifecycle::{CheckTree, Current, Lifecycle, Step};
    match <Current as Lifecycle>::check_tree() {
        Step::Done(CheckTree::Shell(script)) => check_tree(spawn, lineage, script, CHILD_WAIT),
        // aibd itself (Windows): the same handshake, through the same spawn
        Step::Done(CheckTree::SelfExec) => match std::env::current_exe() {
            Ok(exe) => check_argv(
                spawn,
                lineage,
                vec![
                    exe.to_string_lossy().into_owned(),
                    crate::platform::check_tree::ROOT_ARG.into(),
                ],
                CHILD_WAIT,
            ),
            Err(_) => Err(SpawnFail {
                tag: "current_exe_unreadable".into(),
                left_running: None,
            }),
        },
        // The check tree is a POSIX shell (`/bin/sh`): started on Windows it
        // would fail as `enoent` and turn the row (and selfcheck's exit code)
        // red for a missing check, not a broken process manager. Not started
        // at all: `not_wired` until a Windows check tree exists (a9136, #554).
        Step::Unsupported { .. } | Step::NotApplicable { .. } => {
            let _ = (spawn, lineage);
            Err(SpawnFail {
                tag: NO_CHECK_TREE.into(),
                left_running: None,
            })
        }
    }
}

/// [`check_process_management`]'s tag on a platform with no check tree yet.
pub const NO_CHECK_TREE: &str = "no_check_tree";

/// [`check_process_management`] with the script and the wait given (tests:
/// a script whose child never starts).
#[doc(hidden)]
pub fn check_tree(
    spawn: SpawnFn,
    lineage: Option<Arc<Lineage>>,
    script: &str,
    wait: Duration,
) -> Result<Checked, SpawnFail> {
    check_argv(
        spawn,
        lineage,
        vec!["/bin/sh".into(), "-c".into(), script.into(), "sh".into()],
        wait,
    )
}

/// Start `argv` + the marker path as the check tree and stop it once its
/// child wrote its pid to the marker, or after `wait`.
#[doc(hidden)]
pub fn check_argv(
    spawn: SpawnFn,
    lineage: Option<Arc<Lineage>>,
    mut argv: Vec<String>,
    wait: Duration,
) -> Result<Checked, SpawnFail> {
    // One number per check, never per process: two checks in one process (tests
    // run in parallel threads) must not share a marker or a session id. The
    // clock alone is not enough: macOS reads it in microseconds, and two checks
    // started in the same microsecond read each other's marker (#745 CI).
    static CHECKS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = CHECKS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let marker = std::env::temp_dir().join(format!(
        "aibd-selfcheck-{}-{n}-{}.child",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos())
    ));
    let _ = std::fs::remove_file(&marker);
    // One session id per check, never per process: the stop's marker sweep
    // (ADR 0004 layer 3) adopts every process carrying `AIBD_SESSION=<id>`,
    // so two checks sharing an id would stop each other's trees (#579 CI:
    // a concurrent test's stop took this tree's background child).
    let spec = Spec {
        id: format!("selfcheck-{}-{n}", std::process::id()),
        argv: {
            argv.push(marker.to_string_lossy().into_owned());
            argv
        },
        cwd: std::env::temp_dir(),
        env: Vec::new(),
        pass: Vec::new(),
    };
    let mut run = spawn(&spec, lineage)?;
    let started = || {
        std::fs::read_to_string(&marker)
            .ok()
            .is_some_and(|s| s.trim().parse::<u32>().is_ok())
    };
    let op = crate::timers::SELFCHECK_CHILD_WAIT.begin(Some(std::time::Instant::now() + wait));
    let mut child_started = started();
    while !child_started && op.tick() {
        child_started = started();
    }
    drop(op);
    let tree_running = matches!(run.try_exit(), Ok(None));
    // stopped whatever the handshake said: never leave the check tree behind
    let stop = run.stop(Duration::from_secs(2));
    let _ = std::fs::remove_file(&marker);
    Ok(Checked {
        stop,
        child_started,
        tree_running,
    })
}

/// The `process_management` row from [`check_process_management`]'s answer.
pub fn process_row(r: &Result<Checked, SpawnFail>) -> Row {
    const N: &str = "process_management";
    match r {
        Err(f) if f.tag == "unsupported_platform" => Row::new(
            N,
            Status::NotWired,
            "sessions are not available on this platform in this build",
        ),
        Err(f) if f.tag == NO_CHECK_TREE => Row::new(
            N,
            Status::NotWired,
            "this build has no check tree for this platform yet: the process manager was not exercised",
        ),
        Err(f) => {
            let mut row = Row::new(
                N,
                Status::Failed(if f.left_running.is_some() {
                    "spawn_left_running".into()
                } else {
                    "spawn_failed".into()
                }),
                format!("could not start the check tree: {}", f.tag),
            );
            row.notes.push(("tag", f.tag.clone()));
            row
        }
        Ok(Checked { stop, .. })
            if !stop.survivors.is_empty() || stop.status == StopStatus::TreeNotClean =>
        {
            let mut row = Row::new(
                N,
                Status::Failed("tree_not_clean".into()),
                format!(
                    "started and stopped a tree of {}: {} still running",
                    stop.tree_size,
                    stop.survivors.len()
                ),
            );
            row.notes
                .push(("survivors", stop.survivors.len().to_string()));
            row
        }
        // only the root was there to stop: not a tree stopped (bb7dc #549)
        Ok(Checked {
            child_started: false,
            stop,
            ..
        }) => {
            let mut row = Row::new(
                N,
                Status::Unverified,
                format!(
                    "the check tree's child did not report it started within {} s: \
                     only the root was stopped (tree of {}), not a tree",
                    CHILD_WAIT.as_secs(),
                    stop.tree_size
                ),
            );
            row.notes.push(("reason", "child_not_started".to_string()));
            row
        }
        // the tree had ended on its own before the stop: nothing was stopped
        Ok(Checked {
            tree_running: false,
            stop,
            ..
        }) => {
            let mut row = Row::new(
                N,
                Status::Unverified,
                format!(
                    "the check tree ended on its own before the stop (tree of {}): \
                     the process manager did not stop it",
                    stop.tree_size
                ),
            );
            row.notes
                .push(("reason", "tree_ended_before_stop".to_string()));
            row
        }
        Ok(Checked { stop, .. }) => match &stop.status {
            StopStatus::Clean => Row::new(
                N,
                Status::Ok,
                format!(
                    "started and stopped a tree of {}: proven clean",
                    stop.tree_size
                ),
            ),
            StopStatus::Unverified(reason) => {
                let mut row = Row::new(
                    N,
                    Status::Unverified,
                    format!(
                        "started and stopped a tree of {}: no survivor seen, not provable as clean ({reason})",
                        stop.tree_size
                    ),
                );
                row.notes.push(("reason", reason.clone()));
                row
            }
            // handled above
            StopStatus::TreeNotClean => Row::new(N, Status::Failed("tree_not_clean".into()), ""),
        },
    }
}

/// All rows, from what was looked up (injected so tests never touch the real
/// environment or start a child; `process` is [`process_row`]).
pub fn rows(
    config: Result<Config, String>,
    lineage: &Lineage,
    reaping: &ChildReaping,
    os: &str,
    process: Row,
) -> Vec<Row> {
    let mut out = vec![Row::new("version", Status::Ok, version_line())];
    out.push(match config {
        Ok(c) => Row::new(
            "config",
            Status::Ok,
            format!(
                "bind {} (loopback only), data dir {}",
                c.bind,
                c.data_dir.display()
            ),
        ),
        Err(e) => Row::new("config", Status::Failed("config".into()), e),
    });
    out.push(lineage_row(lineage, os));
    out.push(reaping_row(reaping));
    out.push(process);
    out.push(if os == "windows" {
        Row::new(
            "windows_job",
            Status::Unverified,
            "Job Object code checked on GitHub's Windows runner only, not on a real machine; not wired",
        )
    } else {
        Row::new("windows_job", Status::Unsupported, "Windows only")
    });
    out
}

fn lineage_row(lineage: &Lineage, os: &str) -> Row {
    if os != "macos" {
        return Row::new("lineage_m1", Status::Unsupported, "macOS only");
    }
    let sc = lineage.selfcheck_status();
    if lineage.enabled() {
        Row::new(
            "lineage_m1",
            Status::Unverified,
            "start-up check passed; lineage only adds candidates and is never reported clean (DECISION-23)",
        )
    } else {
        let tag = sc.strip_prefix("failed:").unwrap_or(sc);
        Row::new(
            "lineage_m1",
            Status::Failed(tag.to_owned()),
            "start-up check failed: lineage is off until restart",
        )
    }
}

fn reaping_row(r: &ChildReaping) -> Row {
    let mut row = match r {
        ChildReaping::Keeps(_) => Row::new(
            "sigchld",
            Status::Ok,
            "exited children stay until reaped (ADR 0008 rule 3)",
        ),
        ChildReaping::AutoReaps(_) => Row::new(
            "sigchld",
            Status::Unverified,
            "inherited: the kernel reaps children at once; the server resets it to default when it starts, this check does not",
        ),
        ChildReaping::Unreadable(tag) => Row::new(
            "sigchld",
            Status::Failed(format!("sigchld:{tag}")),
            "could not read the SIGCHLD disposition",
        ),
        ChildReaping::Unsupported => Row::new("sigchld", Status::Unsupported, "POSIX only"),
    };
    if let ChildReaping::Keeps(s) | ChildReaping::AutoReaps(s) = r {
        row.notes.push(("disposition", (*s).to_owned()));
    }
    row
}

/// 1 when any row failed, else 0.
pub fn exit_code(rows: &[Row]) -> u8 {
    u8::from(rows.iter().any(|r| matches!(r.status, Status::Failed(_))))
}

/// One line per row: `name  status  detail [k=v ...]`, then a summary line.
pub fn render_text(rows: &[Row]) -> String {
    let mut s = String::new();
    for r in rows {
        s.push_str(&format!(
            "{:<20} {:<12} {}",
            r.name,
            r.status.label(),
            r.detail
        ));
        for (k, v) in &r.notes {
            s.push_str(&format!(" {k}={v}"));
        }
        s.push('\n');
    }
    let count = |f: fn(&Status) -> bool| rows.iter().filter(|r| f(&r.status)).count();
    s.push_str(&format!(
        "summary: {} ok, {} failed, {} unverified, {} not wired, {} unsupported{}\n",
        count(|s| *s == Status::Ok),
        count(|s| matches!(s, Status::Failed(_))),
        count(|s| *s == Status::Unverified),
        count(|s| *s == Status::NotWired),
        count(|s| *s == Status::Unsupported),
        if count(|s| *s != Status::Ok && *s != Status::Unsupported) > 0 {
            " (not a clean bill: read the rows above)"
        } else {
            ""
        },
    ));
    s
}

/// `[{name, status, detail, ...notes}]`
pub fn render_json(rows: &[Row]) -> serde_json::Value {
    serde_json::Value::Array(
        rows.iter()
            .map(|r| {
                let mut m = serde_json::Map::new();
                m.insert("name".into(), r.name.into());
                m.insert("status".into(), r.status.label().into());
                m.insert("detail".into(), r.detail.clone().into());
                for (k, v) in &r.notes {
                    m.insert((*k).into(), v.clone().into());
                }
                serde_json::Value::Object(m)
            })
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const K: ChildReaping = ChildReaping::Keeps("default");

    fn cfg() -> Result<Config, String> {
        Config::from_lookup(|_| None, Some(PathBuf::from("/tmp/aibd-x"))).map_err(|e| e.to_string())
    }

    fn nw() -> Row {
        process_row(&fail("unsupported_platform", None))
    }

    fn status_of(rows: &[Row], name: &str) -> String {
        rows.iter()
            .find(|r| r.name == name)
            .map(|r| r.status.label())
            .unwrap_or_default()
    }

    #[test]
    fn macos_lineage_that_passed_is_unverified_never_ok() {
        let l = Lineage::from_selfcheck(
            std::sync::Arc::new(aib_sys::fake::FakeProcessInfo::default()),
            Ok(()),
        );
        let rows = rows(cfg(), &l, &K, "macos", nw());
        assert_eq!(status_of(&rows, "lineage_m1"), "unverified");
        assert_eq!(exit_code(&rows), 0);
        assert!(render_text(&rows).contains("not a clean bill"));
    }

    #[test]
    fn a_failed_lineage_check_is_failed_with_its_tag_and_exit_1() {
        let l = Lineage::disabled("failed:child_ppid");
        let rows = rows(cfg(), &l, &K, "macos", nw());
        assert_eq!(status_of(&rows, "lineage_m1"), "failed:child_ppid");
        assert_eq!(exit_code(&rows), 1);
    }

    /// A stop of a tree whose child reported it started.
    fn stop(status: StopStatus, survivors: Vec<u32>) -> Checked {
        Checked {
            stop: Stop {
                status,
                survivors,
                tree_size: 2,
                signals: Vec::new(),
                blind_spots: Vec::new(),
                candidates: Default::default(),
            },
            child_started: true,
            tree_running: true,
        }
    }

    fn fail(tag: &str, left: Option<u32>) -> Result<Checked, SpawnFail> {
        Err(SpawnFail {
            tag: tag.into(),
            left_running: left,
        })
    }

    /// contract §6: the row is the stop's own answer, never better.
    #[test]
    fn process_management_reports_the_real_stop_never_better() {
        let un = |r: &str| StopStatus::Unverified(r.into());
        let label = |r: Result<Checked, SpawnFail>| process_row(&r).status.label();
        assert_eq!(label(Ok(stop(un("observed_clean"), vec![]))), "unverified");
        assert_eq!(
            process_row(&Ok(stop(un("observed_clean"), vec![]))).notes,
            vec![("reason", "observed_clean".to_string())]
        );
        assert_eq!(
            label(Ok(stop(un("candidates_unreadable"), vec![]))),
            "unverified"
        );
        // a survivor fails the row whatever the status says
        assert_eq!(
            label(Ok(stop(un("observed_clean"), vec![7]))),
            "failed:tree_not_clean"
        );
        assert_eq!(
            label(Ok(stop(StopStatus::TreeNotClean, vec![]))),
            "failed:tree_not_clean"
        );
        assert_eq!(label(Ok(stop(StopStatus::Clean, vec![]))), "ok");
        assert_eq!(label(fail("unsupported_platform", None)), "not_wired");
        assert_eq!(label(fail(NO_CHECK_TREE, None)), "not_wired");
        assert_eq!(label(fail("enoent", None)), "failed:spawn_failed");
        assert_eq!(label(fail("os_1", Some(9))), "failed:spawn_left_running");
    }

    /// Without a check tree for the platform the spawn is never called
    /// (Windows has one since a9136 11-09: aibd itself).
    #[cfg(not(any(unix, windows)))]
    #[test]
    fn no_check_tree_is_not_wired_and_never_spawns() {
        fn must_not_spawn(
            _: &Spec,
            _: Option<Arc<Lineage>>,
        ) -> Result<Box<dyn crate::platform::session::Running>, SpawnFail> {
            panic!("the check tree was started on a platform without one")
        }
        let r = check_process_management(must_not_spawn, None);
        assert_eq!(
            r.as_ref().map_err(|f| f.tag.as_str()).err(),
            Some(NO_CHECK_TREE)
        );
        assert_eq!(process_row(&r).status.label(), "not_wired");
    }

    /// bb7dc #549 3bacd239: a stop that only met the root (the child never
    /// reported it started) is `unverified` / `child_not_started`, never ok;
    /// a survivor still fails the row.
    #[test]
    fn a_tree_whose_child_never_started_is_never_reported_as_stopped() {
        let only_root = |status, survivors| {
            let mut c = stop(status, survivors);
            c.child_started = false;
            c.stop.tree_size = 1;
            process_row(&Ok(c))
        };
        for st in [
            StopStatus::Clean,
            StopStatus::Unverified("observed_clean".into()),
        ] {
            let row = only_root(st, vec![]);
            assert_eq!(row.status.label(), "unverified");
            assert_eq!(row.notes, vec![("reason", "child_not_started".to_string())]);
        }
        assert_eq!(
            only_root(StopStatus::Clean, vec![5]).status.label(),
            "failed:tree_not_clean"
        );
    }

    /// a9136 11-09: a tree that ended before the stop was not stopped by us;
    /// a survivor still fails the row.
    #[test]
    fn a_tree_that_ended_before_the_stop_is_not_reported_as_stopped() {
        let ended = |status, survivors| {
            let mut c = stop(status, survivors);
            c.tree_running = false;
            process_row(&Ok(c))
        };
        let row = ended(StopStatus::Clean, vec![]);
        assert_eq!(row.status.label(), "unverified");
        assert_eq!(
            row.notes,
            vec![("reason", "tree_ended_before_stop".to_string())]
        );
        assert_eq!(
            ended(StopStatus::Clean, vec![5]).status.label(),
            "failed:tree_not_clean"
        );
    }

    #[test]
    fn a_failed_process_check_makes_the_exit_code_1() {
        let p = process_row(&Ok(stop(StopStatus::TreeNotClean, vec![3])));
        let rows = rows(cfg(), &Lineage::disabled("unsupported"), &K, "macos", p);
        assert_eq!(exit_code(&rows), 1);
    }

    #[test]
    fn windows_job_is_unverified_on_windows_and_unsupported_elsewhere() {
        let l = Lineage::disabled("unsupported");
        assert_eq!(
            status_of(&rows(cfg(), &l, &K, "windows", nw()), "windows_job"),
            "unverified"
        );
        assert_eq!(
            status_of(&rows(cfg(), &l, &K, "windows", nw()), "lineage_m1"),
            "unsupported"
        );
        assert_eq!(
            status_of(&rows(cfg(), &l, &K, "macos", nw()), "windows_job"),
            "unsupported"
        );
    }

    #[test]
    fn a_bad_config_fails_the_check() {
        let bad = Config::from_lookup(
            |k| (k == "AIBD_BIND").then(|| "0.0.0.0:1".to_string()),
            Some(PathBuf::from("/tmp/x")),
        )
        .map_err(|e| e.to_string());
        let rows = rows(bad, &Lineage::disabled("unsupported"), &K, "linux", nw());
        assert_eq!(status_of(&rows, "config"), "failed:config");
        assert_eq!(exit_code(&rows), 1);
    }

    #[test]
    fn json_has_one_object_per_row_with_notes_flattened() {
        let mut rows = rows(cfg(), &Lineage::disabled("unsupported"), &K, "linux", nw());
        rows[0].notes.push(("wait_mode", "poll".into()));
        let j = render_json(&rows);
        let a = j.as_array().expect("array");
        assert_eq!(a.len(), rows.len());
        assert_eq!(a[0]["name"], "version");
        assert_eq!(a[0]["status"], "ok");
        assert_eq!(a[0]["wait_mode"], "poll");
        assert!(a[0]["detail"].as_str().unwrap_or("").starts_with("aibd "));
    }

    #[test]
    fn an_inherited_ignored_sigchld_is_unverified_and_an_unreadable_one_fails() {
        let l = Lineage::disabled("unsupported");
        let auto = rows(
            cfg(),
            &l,
            &ChildReaping::AutoReaps("ignored"),
            "linux",
            nw(),
        );
        assert_eq!(status_of(&auto, "sigchld"), "unverified");
        assert_eq!(exit_code(&auto), 0);
        let j = render_json(&auto);
        let row = j
            .as_array()
            .and_then(|a| a.iter().find(|r| r["name"] == "sigchld"))
            .cloned()
            .unwrap_or_default();
        assert_eq!(row["disposition"], "ignored");
        let bad = rows(
            cfg(),
            &l,
            &ChildReaping::Unreadable("os".into()),
            "linux",
            nw(),
        );
        assert_eq!(status_of(&bad, "sigchld"), "failed:sigchld:os");
        assert_eq!(exit_code(&bad), 1);
        assert_eq!(
            status_of(&rows(cfg(), &l, &K, "linux", nw()), "sigchld"),
            "ok"
        );
    }
}
