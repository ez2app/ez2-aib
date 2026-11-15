//! S79 A2 (7b81e 11-15): the 10-minute fallback wake for aib-drive.
//!
//! Events (merge, receipt, dm_done …) wake aib-drive as they happen; this is the floor
//! under them: every [`crate::timers::DRIVE_WAKE`] period aibd runs
//! `aib-drive event tick --by aibd`, so a missed or never-written event costs at most one
//! period. aib-drive itself writes nothing when the state root has no `drive/` folder, and
//! skips events whose `by` is its own name.
//!
//! - **Off by default** (new behaviour, RULES「新行為預設關」): only `AIBD_DRIVE_WAKE=1` starts
//!   it; anything else, or unset, starts nothing and no timer runs.
//! - The program is `AIB_DRIVE` when set (non-empty), else `~/.ez2-aib/bin/aib-drive`
//!   (`USERPROFILE` when there is no `HOME`; `.exe` on Windows). No PATH lookup: aibd's
//!   environment is not a login shell's (f1fae, same rule as mergewatch / receipt-run).
//! - A wake that fails (no program, non-zero exit) never stops aibd or the timer: the next
//!   period tries again. Failures are logged quietly ([`Quiet`], 5d33a A2 L2): the first one,
//!   a recovery, and every [`FAIL_REPEAT`]th failure in a row (6 h), not one line per period.
//! - One wake has [`WAKE_LIMIT`] (60 s): an aib-drive that hangs is killed and the next
//!   period runs again, so a hung child cannot silently stop the floor (5d33a A2 L1). The
//!   wait is the registered operation timer [`crate::timers::DRIVE_WAKE_CHILD_WAIT`].
//! - When aibd is not running, `aib-drive observe --watch --interval 600` is the fallback.
//! - **Refresh** (S79 B3-1, a9136, 519d0 11-15 07:26): under the same switch a second thread
//!   runs `aib-drive refresh --auto` every [`crate::timers::DRIVE_REFRESH`] period, so the
//!   caches `aib-drive next` reads stay fresh with no window open. aib-drive decides each
//!   time whether to scan (cache and last try 20 minutes old, its lock free, 1500 GitHub
//!   calls left); a scan may take minutes, so its limit is [`REFRESH_LIMIT`], not the wake's
//!   60 s. Its failures are logged the same quiet way, on their own count.
//! - **Act** (519d0 11-15, idea A of 56cda): after each wake the same thread runs
//!   `aib-drive act --auto` (next and the seat facts worked out now, then one act round
//!   that reads the live dm queue), so nudges do not stop when no `observe --watch` runs.
//!   With every act switch off (the default) the round sends nothing and is only journaled.
//!   Its limit is [`ACT_LIMIT`]; its failures are logged on their own count.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// `1` turns the fallback wake on; anything else leaves it off.
pub const ENV_ON: &str = "AIBD_DRIVE_WAKE";
/// The aib-drive program to run, overriding the default path.
pub const ENV_BIN: &str = "AIB_DRIVE";
/// The `by` this wake writes, so aib-drive can tell it from the events of others.
pub const BY: &str = "aibd";
/// The longest one wake may run before its aib-drive is killed.
pub const WAKE_LIMIT: Duration = Duration::from_secs(60);
/// While failing, one log line every this many failures in a row (36 × 600 s = 6 h).
pub const FAIL_REPEAT: u64 = 36;
/// The longest one refresh may run (aib-drive's own scan limit is 30 minutes).
pub const REFRESH_LIMIT: Duration = Duration::from_secs(31 * 60);

/// The longest one `act --auto` may run (aib-drive's own child limit for it is 180 s).
pub const ACT_LIMIT: Duration = Duration::from_secs(180);

/// What to run, when the wake is on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub bin: PathBuf,
}

/// The plan from the environment: `None` when off or when no program path can be made.
pub fn plan(env: impl Fn(&str) -> Option<String>) -> Option<Plan> {
    if env(ENV_ON).as_deref() != Some("1") {
        return None;
    }
    let set = |k: &str| env(k).filter(|v| !v.is_empty());
    let bin = set(ENV_BIN).map(PathBuf::from).or_else(|| {
        set("HOME").or_else(|| set("USERPROFILE")).map(|h| {
            PathBuf::from(h)
                .join(".ez2-aib")
                .join("bin")
                .join(format!("aib-drive{}", std::env::consts::EXE_SUFFIX))
        })
    })?;
    Some(Plan { bin })
}

/// The arguments one wake passes.
pub fn args() -> [&'static str; 4] {
    ["event", "tick", "--by", BY]
}

/// The arguments one refresh passes.
pub fn refresh_args() -> [&'static str; 2] {
    ["refresh", "--auto"]
}

/// The arguments one act round passes.
pub fn act_args() -> [&'static str; 2] {
    ["act", "--auto"]
}

/// What one wake did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Wake {
    /// The program ran and exited 0.
    Ran,
    /// It could not start, or exited non-zero.
    Failed,
    /// It was still running at the limit and was killed.
    TimedOut,
}

/// Run one wake, at most `limit` long (a program still running then is killed). Its output
/// is dropped.
pub fn wake_once(bin: &Path, limit: Duration) -> Wake {
    run_once(bin, &args(), limit, &crate::timers::DRIVE_WAKE_CHILD_WAIT)
}

/// Run one refresh, at most `limit` long, waited on by its own registered timer.
pub fn refresh_once(bin: &Path, limit: Duration) -> Wake {
    run_once(
        bin,
        &refresh_args(),
        limit,
        &crate::timers::DRIVE_REFRESH_CHILD_WAIT,
    )
}

/// Run one act round, at most `limit` long, waited on by its own registered timer.
pub fn act_once(bin: &Path, limit: Duration) -> Wake {
    run_once(
        bin,
        &act_args(),
        limit,
        &crate::timers::DRIVE_ACT_CHILD_WAIT,
    )
}

/// One period of the wake thread: the wake, then one act round (an act round runs even
/// when the wake failed: the dm queue does not need the tick).
pub fn wake_round(bin: &Path) -> (Wake, Wake) {
    let w = wake_once(bin, WAKE_LIMIT);
    let a = act_once(bin, ACT_LIMIT);
    (w, a)
}

/// Run `bin args`, at most `limit` long, re-checked on `wait`; output dropped.
fn run_once(
    bin: &Path,
    args: &[&str],
    limit: Duration,
    wait: &'static crate::timers::Timer,
) -> Wake {
    let Ok(mut child) = Command::new(bin)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return Wake::Failed;
    };
    let exited = |s: std::process::ExitStatus| {
        if s.success() {
            Wake::Ran
        } else {
            Wake::Failed
        }
    };
    let op = wait.begin(Some(Instant::now() + limit));
    loop {
        match child.try_wait() {
            Ok(Some(s)) => return exited(s),
            Ok(None) => {}
            Err(_) => break,
        }
        if !op.tick() {
            // one last look: a child that ended right at the limit is not a timeout
            if let Ok(Some(s)) = child.try_wait() {
                return exited(s);
            }
            let _ = child.kill();
            let _ = child.wait();
            return Wake::TimedOut;
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    Wake::Failed
}

/// Which log line a wake asks for (5d33a A2 L2: not one line per failing period).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Line {
    None,
    /// The first failure after running (or after start).
    FirstFail,
    /// Still failing: this many in a row.
    StillFailing(u64),
    /// Running again after this many failures.
    Recovered(u64),
}

/// The failures in a row so far, deciding which wakes make a log line.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Quiet {
    fails: u64,
}

impl Quiet {
    pub fn note(&mut self, w: Wake) -> Line {
        if w == Wake::Ran {
            let n = std::mem::take(&mut self.fails);
            return if n > 0 {
                Line::Recovered(n)
            } else {
                Line::None
            };
        }
        self.fails += 1;
        match self.fails {
            1 => Line::FirstFail,
            n if n.is_multiple_of(FAIL_REPEAT) => Line::StillFailing(n),
            _ => Line::None,
        }
    }
}

/// The log line `line` asks for, for job `what` (the command it runs).
fn say(what: &str, bin: &str, w: Wake, line: Line) {
    match line {
        Line::None => {}
        Line::FirstFail => tracing::warn!(
            bin = %bin,
            wake = ?w,
            "drive {what}: did not run (next line on recovery or every 36 failures)"
        ),
        Line::StillFailing(n) => tracing::warn!(
            bin = %bin,
            wake = ?w,
            failures = n,
            "drive {what}: still does not run"
        ),
        Line::Recovered(n) => tracing::info!(
            bin = %bin,
            failures = n,
            "drive {what}: runs again"
        ),
    }
}

/// Start the wake and refresh threads when the environment turns them on. Returns whether
/// the wake started.
pub fn start(env: impl Fn(&str) -> Option<String>) -> bool {
    let Some(p) = plan(env) else {
        return false;
    };
    // the plan moves into the threads; the start line needs the path too
    let shown = p.bin.display().to_string();
    let wake_bin = p.bin.clone();
    let spawned = std::thread::Builder::new()
        .name("aibd-drive-wake".into())
        .spawn(move || {
            let op = crate::timers::DRIVE_WAKE.begin(None);
            // the first wake comes one period after start, not at start: a restart loop
            // must not turn into a wake loop
            let (mut quiet, mut quiet_act) = (Quiet::default(), Quiet::default());
            while op.tick() {
                let (w, a) = wake_round(&wake_bin);
                let shown = wake_bin.display().to_string();
                say("wake (aib-drive event tick)", &shown, w, quiet.note(w));
                say("act (aib-drive act --auto)", &shown, a, quiet_act.note(a));
            }
        });
    if spawned.is_err() {
        tracing::error!("drive wake: could not start the thread");
        return false;
    }
    tracing::info!(bin = %shown, "drive wake: on, every 600 s");
    let refresh_bin = p.bin;
    let refresh = std::thread::Builder::new()
        .name("aibd-drive-refresh".into())
        .spawn(move || {
            let op = crate::timers::DRIVE_REFRESH.begin(None);
            let mut quiet = Quiet::default();
            while op.tick() {
                let w = refresh_once(&refresh_bin, REFRESH_LIMIT);
                say(
                    "refresh (aib-drive refresh --auto)",
                    &refresh_bin.display().to_string(),
                    w,
                    quiet.note(w),
                );
            }
        });
    if refresh.is_err() {
        tracing::error!("drive refresh: could not start the thread");
    } else {
        tracing::info!(bin = %shown, "drive refresh: on, every 300 s");
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |k| m.get(k).cloned()
    }

    #[test]
    fn the_drive_wake_is_off_unless_turned_on_with_1() {
        assert_eq!(plan(env(&[("HOME", "/h")])), None, "unset is off");
        for v in ["", "0", "true", "yes", " 1"] {
            assert_eq!(
                plan(env(&[("HOME", "/h"), (ENV_ON, v)])),
                None,
                "{v:?} is off"
            );
        }
        assert!(!start(env(&[("HOME", "/h")])), "off starts no thread");
    }

    #[test]
    fn the_drive_wake_runs_the_set_program_or_the_one_in_ez2_aib_bin() {
        let p = plan(env(&[
            (ENV_ON, "1"),
            (ENV_BIN, "/x/aib-drive"),
            ("HOME", "/h"),
        ]))
        .unwrap();
        assert_eq!(p.bin, PathBuf::from("/x/aib-drive"));
        let want = PathBuf::from("/h")
            .join(".ez2-aib")
            .join("bin")
            .join(format!("aib-drive{}", std::env::consts::EXE_SUFFIX));
        let p = plan(env(&[(ENV_ON, "1"), (ENV_BIN, ""), ("HOME", "/h")])).unwrap();
        assert_eq!(p.bin, want, "an empty AIB_DRIVE is not a path");
        let p = plan(env(&[(ENV_ON, "1"), ("USERPROFILE", "/h")])).unwrap();
        assert_eq!(p.bin, want, "USERPROFILE when there is no HOME");
        assert_eq!(plan(env(&[(ENV_ON, "1")])), None, "no home, no path");
        assert_eq!(args(), ["event", "tick", "--by", "aibd"]);
    }

    #[cfg(unix)]
    #[test]
    fn a_drive_wake_passes_tick_by_aibd_and_a_failing_program_is_not_ran() {
        use std::os::unix::fs::PermissionsExt;
        let d = std::env::temp_dir().join(format!("aibd-drive-wake-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let out = d.join("args");
        let ok = d.join("ok.sh");
        std::fs::write(
            &ok,
            format!("#!/bin/sh\nprintf '%s ' \"$@\" > '{}'\n", out.display()),
        )
        .unwrap();
        let bad = d.join("bad.sh");
        std::fs::write(&bad, "#!/bin/sh\nexit 5\n").unwrap();
        for f in [&ok, &bad] {
            std::fs::set_permissions(f, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        assert_eq!(wake_once(&ok, WAKE_LIMIT), Wake::Ran);
        assert_eq!(
            std::fs::read_to_string(&out).unwrap(),
            "event tick --by aibd "
        );
        assert_eq!(
            wake_once(&bad, WAKE_LIMIT),
            Wake::Failed,
            "a non-zero exit is not a wake"
        );
        assert_eq!(
            wake_once(&d.join("missing"), WAKE_LIMIT),
            Wake::Failed,
            "no program is not a wake"
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// S79 B3-1: the refresh thread runs `aib-drive refresh --auto` (aib-drive decides
    /// whether to scan) with its own limit, longer than a wake's.
    #[cfg(unix)]
    #[test]
    fn a_drive_refresh_passes_refresh_auto_and_has_a_scan_long_limit() {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(refresh_args(), ["refresh", "--auto"]);
        assert!(
            REFRESH_LIMIT >= Duration::from_secs(30 * 60),
            "aib-drive's scan limit is 30 min"
        );
        assert!(REFRESH_LIMIT > WAKE_LIMIT);
        let d = std::env::temp_dir().join(format!("aibd-drive-refresh-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let out = d.join("args");
        let ok = d.join("ok.sh");
        std::fs::write(
            &ok,
            format!("#!/bin/sh\nprintf '%s ' \"$@\" > '{}'\n", out.display()),
        )
        .unwrap();
        std::fs::set_permissions(&ok, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(refresh_once(&ok, REFRESH_LIMIT), Wake::Ran);
        assert_eq!(std::fs::read_to_string(&out).unwrap(), "refresh --auto ");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 519d0 11-15 (idea A of 56cda): each period runs the wake, then `act --auto`, so
    /// nudges go on with no observe --watch; act runs even when the wake failed.
    #[cfg(unix)]
    #[test]
    fn each_drive_wake_period_runs_the_tick_then_one_act_round() {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(act_args(), ["act", "--auto"]);
        assert!(ACT_LIMIT > WAKE_LIMIT && ACT_LIMIT < REFRESH_LIMIT);
        let d = std::env::temp_dir().join(format!("aibd-drive-act-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let out = d.join("args");
        // the tick fails (exit 5), act runs anyway
        let prog = d.join("drive.sh");
        std::fs::write(
            &prog,
            format!(
                "#!/bin/sh\necho \"$*\" >> '{}'\n[ \"$1\" = act ] || exit 5\n",
                out.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&prog, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(wake_round(&prog), (Wake::Failed, Wake::Ran));
        assert_eq!(
            std::fs::read_to_string(&out).unwrap(),
            "event tick --by aibd\nact --auto\n"
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 5d33a A2 L1: an aib-drive that hangs is killed at the limit, so the floor goes on.
    #[cfg(unix)]
    #[test]
    fn a_hung_drive_wake_is_killed_at_the_limit() {
        use std::os::unix::fs::PermissionsExt;
        let d = std::env::temp_dir().join(format!("aibd-drive-hang-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let hang = d.join("hang.sh");
        // exec: the killed child is the sleep itself, nothing is left behind
        std::fs::write(&hang, "#!/bin/sh\nexec sleep 30\n").unwrap();
        std::fs::set_permissions(&hang, std::fs::Permissions::from_mode(0o755)).unwrap();
        let t = Instant::now();
        assert_eq!(wake_once(&hang, Duration::from_millis(400)), Wake::TimedOut);
        assert!(
            t.elapsed() < Duration::from_secs(10),
            "killed at the limit, not left to run: {:?}",
            t.elapsed()
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 5d33a A2 L2: the first failure, a recovery and every 36th failure in a row make a line.
    #[test]
    fn failures_are_logged_once_then_every_six_hours_and_on_recovery() {
        let mut q = Quiet::default();
        assert_eq!(q.note(Wake::Ran), Line::None, "running is quiet");
        assert_eq!(q.note(Wake::Failed), Line::FirstFail);
        for _ in 2..FAIL_REPEAT {
            assert_eq!(
                q.note(Wake::TimedOut),
                Line::None,
                "a failing period is quiet"
            );
        }
        assert_eq!(q.note(Wake::Failed), Line::StillFailing(FAIL_REPEAT));
        for _ in 1..FAIL_REPEAT {
            assert_eq!(q.note(Wake::Failed), Line::None);
        }
        assert_eq!(q.note(Wake::Failed), Line::StillFailing(2 * FAIL_REPEAT));
        assert_eq!(q.note(Wake::Ran), Line::Recovered(2 * FAIL_REPEAT));
        assert_eq!(q.note(Wake::Ran), Line::None);
        assert_eq!(
            q.note(Wake::TimedOut),
            Line::FirstFail,
            "a new run of failures starts again"
        );
    }
}
