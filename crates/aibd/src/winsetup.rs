//! `aibd setup`, also run by double-clicking the same program renamed `AIB-Setup.exe`
//! (PLAN-win-customer-today-c2f0b-1126 rows 2–5, ebab8 11-26 03:10): the customer double-clicks
//! one file and types nothing.
//!
//! 1. copy this program to `%LOCALAPPDATA%\AIB\aibd.exe` (the user's own folder: no
//!    administrator; an older copy there is moved aside first, since a running program cannot be
//!    overwritten on Windows but can be renamed);
//! 2. make the pairing key with the same code as `aibd ctl pair-key init` (Windows Hello asks
//!    once); a key already there is kept; no Hello yet (no PIN) does not stop the setup, the page
//!    shows how to set one up;
//! 3. write the sign-in entry `HKCU\…\Run` value `AIB` = the quoted path ([`aib_sys::winrun`]);
//! 4. start the installed aibd in the background unless one already answers, and wait for
//!    `/api/v1/health` (at most [`HEALTH_WAIT`]);
//! 5. open the page the way `aibd ctl web` does.
//!
//! No PowerShell, no Task Scheduler, nothing downloaded. The steps and their order are plain
//! Rust here and tested on every platform with a stand-in ([`Ops`]); the Windows calls are in
//! `ctl.rs` (the binary) and `aib_sys::winrun`. Same-account threat model as the rest of aibd:
//! the folder and the Run value are the user's own, so this is convenience, not a defence.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// The Run value's name.
pub const RUN_NAME: &str = "AIB";
/// The folder under `%LOCALAPPDATA%`.
pub const DIR_NAME: &str = "AIB";
/// The installed program's file name.
pub const EXE_NAME: &str = "aibd.exe";
/// A program whose file name starts with this (any case) runs the setup when started with no
/// arguments: `AIB-Setup.exe`, and what a browser makes of a second download, `AIB-Setup (1).exe`.
pub const SETUP_STEM: &str = "aib-setup";
/// How long the setup waits for the new aibd to answer.
pub const HEALTH_WAIT: Duration = Duration::from_secs(20);
/// One wait step.
pub const HEALTH_STEP: Duration = Duration::from_millis(500);

/// Is `exe` the setup program (by its file name)?
pub fn is_setup_exe(exe: &Path) -> bool {
    exe.file_stem()
        .and_then(OsStr::to_str)
        .is_some_and(|s| s.to_ascii_lowercase().starts_with(SETUP_STEM))
}

/// Where the setup puts aibd.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Places {
    pub dir: PathBuf,
    pub exe: PathBuf,
}

/// `<local app data>\AIB\aibd.exe`; the folder must be given and absolute.
pub fn places(local_app_data: Option<&OsStr>) -> Result<Places, String> {
    let base = local_app_data
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .ok_or("LOCALAPPDATA is not set; not installing")?;
    if !base.is_absolute() {
        return Err(format!(
            "LOCALAPPDATA is not an absolute folder ({}); not installing",
            base.display()
        ));
    }
    let dir = base.join(DIR_NAME);
    let exe = dir.join(EXE_NAME);
    Ok(Places { dir, exe })
}

/// What the setup does to the machine; the real one is in `ctl.rs`, tests use a stand-in.
pub trait Ops {
    /// Are these the same file (the setup started from the installed copy)?
    fn same_file(&self, a: &Path, b: &Path) -> bool;
    /// Put `from` at `to`, moving an older `to` aside first.
    fn copy(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// Is there a pairing key already?
    fn pair_key_exists(&self) -> bool;
    /// `aibd ctl pair-key init`.
    fn pair_key_init(&self) -> Result<(), String>;
    /// Write the Run value.
    fn set_run(&self, name: &str, command: &str) -> Result<(), String>;
    /// Does an aibd answer `/api/v1/health`?
    fn healthy(&self) -> bool;
    /// Start `exe` in the background, no window.
    fn start(&self, exe: &Path) -> Result<(), String>;
    /// Wait.
    fn pace(&self, d: Duration);
    /// Open the page (`aibd ctl web`).
    fn open_page(&self) -> Result<(), String>;
}

/// How the setup ended, for the last lines it prints.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Done {
    /// The pairing key could not be made now (the reason); the page guides the rest.
    pub pair_key_later: Option<String>,
    /// An aibd was already answering, so the copied one starts at the next sign-in.
    pub already_running: bool,
}

/// Run the setup from `current` (this program) into `p`. Every step says one line through
/// `say`; the first failing step ends it with its reason, except the pairing key (see [`Done`]).
pub fn run(
    p: &Places,
    current: &Path,
    ops: &dyn Ops,
    say: &mut dyn FnMut(&str),
) -> Result<Done, String> {
    let mut done = Done::default();
    // 1. the program
    if ops.same_file(current, &p.exe) {
        say(&format!("1/5 already in {}", p.exe.display()));
    } else {
        ops.copy(current, &p.exe)
            .map_err(|e| format!("could not copy aibd to {}: {e}", p.exe.display()))?;
        say(&format!("1/5 copied to {}", p.exe.display()));
    }
    // 2. the pairing key
    if ops.pair_key_exists() {
        say("2/5 pairing key: the one already made is kept");
    } else {
        match ops.pair_key_init() {
            Ok(()) => say("2/5 pairing key made (Windows Hello)"),
            Err(e) => {
                say(&format!(
                    "2/5 pairing key not made now ({e}); the page shows how to set it up"
                ));
                done.pair_key_later = Some(e);
            }
        }
    }
    // 3. start at sign-in
    let command = aib_sys::winrun::quoted(&p.exe.to_string_lossy())?;
    ops.set_run(RUN_NAME, &command)
        .map_err(|e| format!("could not write the sign-in entry: {e}"))?;
    say(&format!("3/5 starts at sign-in: {RUN_NAME} = {command}"));
    // 4. running now
    if ops.healthy() {
        done.already_running = true;
        say("4/5 aibd is already running (the copied program is used from the next sign-in)");
    } else {
        ops.start(&p.exe)
            .map_err(|e| format!("could not start {}: {e}", p.exe.display()))?;
        let mut waited = Duration::ZERO;
        while !ops.healthy() {
            if waited >= HEALTH_WAIT {
                return Err(format!(
                    "aibd did not answer within {} s after starting",
                    HEALTH_WAIT.as_secs()
                ));
            }
            ops.pace(HEALTH_STEP);
            waited += HEALTH_STEP;
        }
        say("4/5 aibd started");
    }
    // 5. the page
    say("5/5 opening the page");
    ops.open_page()?;
    Ok(done)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    struct Fake {
        same: bool,
        key: bool,
        key_err: Option<String>,
        copy_err: Option<String>,
        run_err: Option<String>,
        healthy_after: Option<usize>,
        calls: RefCell<Vec<String>>,
        health_asks: RefCell<usize>,
    }

    impl Ops for Fake {
        fn same_file(&self, _: &Path, _: &Path) -> bool {
            self.same
        }
        fn copy(&self, from: &Path, to: &Path) -> Result<(), String> {
            self.calls
                .borrow_mut()
                .push(format!("copy {} {}", from.display(), to.display()));
            self.copy_err.clone().map_or(Ok(()), Err)
        }
        fn pair_key_exists(&self) -> bool {
            self.key
        }
        fn pair_key_init(&self) -> Result<(), String> {
            self.calls.borrow_mut().push("pair".into());
            self.key_err.clone().map_or(Ok(()), Err)
        }
        fn set_run(&self, name: &str, command: &str) -> Result<(), String> {
            self.calls
                .borrow_mut()
                .push(format!("run {name}={command}"));
            self.run_err.clone().map_or(Ok(()), Err)
        }
        fn healthy(&self) -> bool {
            let mut n = self.health_asks.borrow_mut();
            *n += 1;
            self.healthy_after.is_some_and(|k| *n > k)
        }
        fn start(&self, exe: &Path) -> Result<(), String> {
            self.calls
                .borrow_mut()
                .push(format!("start {}", exe.display()));
            Ok(())
        }
        fn pace(&self, _: Duration) {}
        fn open_page(&self) -> Result<(), String> {
            self.calls.borrow_mut().push("open".into());
            Ok(())
        }
    }

    fn here() -> Places {
        let base = if cfg!(windows) {
            PathBuf::from(r"C:\Users\A B\AppData\Local")
        } else {
            PathBuf::from("/home/a b/local")
        };
        places(Some(base.as_os_str())).unwrap()
    }

    fn go(f: &Fake) -> (Result<Done, String>, Vec<String>) {
        let mut lines = Vec::new();
        let r = run(&here(), Path::new("dl/AIB-Setup.exe"), f, &mut |l| {
            lines.push(l.to_string());
        });
        (r, lines)
    }

    #[test]
    fn the_setup_copies_pairs_writes_the_quoted_run_value_starts_and_opens_in_that_order() {
        let f = Fake {
            healthy_after: Some(3),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert_eq!(r, Ok(Done::default()), "reason: result {lines:?}");
        let p = here();
        let calls = f.calls.borrow();
        assert_eq!(
            *calls,
            vec![
                format!("copy dl/AIB-Setup.exe {}", p.exe.display()),
                "pair".to_string(),
                format!("run AIB=\"{}\"", p.exe.display()),
                format!("start {}", p.exe.display()),
                "open".to_string(),
            ],
            "reason: order"
        );
        assert_eq!(lines.len(), 5, "reason: one line per step {lines:?}");
    }

    #[test]
    fn a_key_already_made_is_kept_and_a_running_aibd_is_not_started_again() {
        let f = Fake {
            key: true,
            same: true,
            healthy_after: Some(0),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert_eq!(
            r,
            Ok(Done {
                pair_key_later: None,
                already_running: true
            }),
            "reason: result"
        );
        let calls = f.calls.borrow();
        assert!(
            !calls
                .iter()
                .any(|c| c == "pair" || c.starts_with("start") || c.starts_with("copy")),
            "reason: redid {calls:?}"
        );
        assert!(
            calls.iter().any(|c| c.starts_with("run AIB=")),
            "reason: no_run {calls:?}"
        );
    }

    #[test]
    fn no_hello_yet_does_not_stop_the_setup() {
        let f = Fake {
            key_err: Some("no PIN".into()),
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert_eq!(
            r.map(|d| d.pair_key_later),
            Ok(Some("no PIN".to_string())),
            "reason: later"
        );
        assert!(
            lines.iter().any(|l| l.contains("page shows")),
            "reason: words {lines:?}"
        );
        assert!(
            f.calls.borrow().iter().any(|c| c == "open"),
            "reason: not_opened"
        );
    }

    #[test]
    fn a_failed_copy_or_run_value_stops_before_anything_starts() {
        for f in [
            Fake {
                copy_err: Some("denied".into()),
                ..Fake::default()
            },
            Fake {
                run_err: Some("denied".into()),
                ..Fake::default()
            },
        ] {
            let (r, _) = go(&f);
            assert!(r.is_err(), "reason: went_on");
            assert!(
                !f.calls
                    .borrow()
                    .iter()
                    .any(|c| c.starts_with("start") || c == "open"),
                "reason: started {:?}",
                f.calls.borrow()
            );
        }
    }

    #[test]
    fn an_aibd_that_never_answers_ends_the_setup_with_the_wait() {
        let f = Fake::default();
        let (r, _) = go(&f);
        let e = r.unwrap_err();
        assert!(
            e.contains("did not answer within 20 s"),
            "reason: words {e}"
        );
        assert!(
            !f.calls.borrow().iter().any(|c| c == "open"),
            "reason: opened"
        );
    }

    #[test]
    fn the_setup_program_is_known_by_its_file_name() {
        for yes in [
            "AIB-Setup.exe",
            "aib-setup.exe",
            "AIB-Setup (1).exe",
            "dir/AIB-SETUP.EXE",
        ] {
            assert!(is_setup_exe(Path::new(yes)), "reason: missed {yes}");
        }
        for no in ["aibd.exe", "setup.exe", "AIB.exe", "my-aib-setup.exe"] {
            assert!(!is_setup_exe(Path::new(no)), "reason: took {no}");
        }
    }

    #[test]
    fn the_install_folder_is_aib_under_an_absolute_local_app_data() {
        assert!(places(None).is_err(), "reason: none");
        assert!(places(Some(OsStr::new(""))).is_err(), "reason: empty");
        assert!(
            places(Some(OsStr::new("relative"))).is_err(),
            "reason: relative"
        );
        let p = here();
        assert!(
            p.exe.ends_with(Path::new("AIB").join("aibd.exe")),
            "reason: {p:?}"
        );
        assert_eq!(p.exe.parent(), Some(p.dir.as_path()), "reason: dir");
    }
}
