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
//! - A wake that fails (no program, non-zero exit) is one warning line and the next period
//!   tries again; it never stops aibd or the timer.
//! - When aibd is not running, `aib-drive observe --watch --interval 600` is the fallback.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// `1` turns the fallback wake on; anything else leaves it off.
pub const ENV_ON: &str = "AIBD_DRIVE_WAKE";
/// The aib-drive program to run, overriding the default path.
pub const ENV_BIN: &str = "AIB_DRIVE";
/// The `by` this wake writes, so aib-drive can tell it from the events of others.
pub const BY: &str = "aibd";

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

/// Run one wake; `true` when the program ran and exited 0. Its output is dropped.
pub fn wake_once(bin: &Path) -> bool {
    Command::new(bin)
        .args(args())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// Start the wake thread when the environment turns it on. Returns whether it started.
pub fn start(env: impl Fn(&str) -> Option<String>) -> bool {
    let Some(p) = plan(env) else {
        return false;
    };
    let spawned = std::thread::Builder::new()
        .name("aibd-drive-wake".into())
        .spawn(move || {
            let op = crate::timers::DRIVE_WAKE.begin(None);
            // the first wake comes one period after start, not at start: a restart loop
            // must not turn into a wake loop
            while op.tick() {
                if !wake_once(&p.bin) {
                    tracing::warn!(bin = %p.bin.display(), "drive wake: aib-drive event tick did not run");
                }
            }
        });
    if spawned.is_err() {
        tracing::error!("drive wake: could not start the thread");
        return false;
    }
    tracing::info!(bin = %p.bin.display(), "drive wake: on, every 600 s");
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
    fn a_drive_wake_passes_tick_by_aibd_and_a_failing_program_is_false() {
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
        assert!(wake_once(&ok));
        assert_eq!(
            std::fs::read_to_string(&out).unwrap(),
            "event tick --by aibd "
        );
        assert!(!wake_once(&bad), "a non-zero exit is not a wake");
        assert!(!wake_once(&d.join("missing")), "no program is not a wake");
        let _ = std::fs::remove_dir_all(&d);
    }
}
