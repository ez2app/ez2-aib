//! `aibd setup`, also run by double-clicking the same program renamed `AIB-Setup.exe`
//! (PLAN-win-customer-today-c2f0b-1126 rows 2–5, ebab8 11-26 03:10): the customer double-clicks
//! one file and types nothing. Security conditions: COND-win-installer-3e53b-1126 r1.
//!
//! 0. check: not elevated (it would write another user's folder and Run key); no folder on the
//!    way is a link / junction (it would send the copy or the Run value elsewhere);
//! 1. copy this program to `%LOCALAPPDATA%\AIB\aibd.exe`: read it once through a read-only
//!    share, write `aibd.exe.new` (create-new), hash the new file again, and only when both
//!    hashes match move it in place (the older copy is kept as `aibd.exe.prev`). A running
//!    installed aibd is never overwritten: the setup stops and says so. Started from the
//!    installed copy itself, this step is skipped;
//! 2. the pairing key, with the same code as `aibd ctl pair-key init` (Windows Hello once); a key
//!    already there is kept (a reinstall is not a way to change keys). No key ⇒ stop here: no
//!    Run value, nothing started ("set a PIN, then double-click again");
//! 3. the sign-in entry: current user's Run value `AIB` = the quoted path, nothing else (no
//!    flags, no environment: the Windows defaults are aibd's own). A value of that name that is
//!    not ours is left alone and the setup stops; what was written is read back and compared;
//! 4. start the installed aibd in the background unless one answers, and wait for
//!    `/api/v1/health` (at most [`HEALTH_WAIT`]);
//! 5. open the page the way `aibd ctl web` does.
//!
//! `aibd setup --uninstall` removes the Run value only when it is ours; folders and data stay.
//!
//! No PowerShell, reg.exe, cmd, schtasks or setx; nothing downloaded. Steps and order are plain
//! Rust here, tested on every platform with a stand-in ([`Ops`]); the Windows calls are in
//! `ctl.rs` (the binary) and `aib_sys::winrun`. The Run value and the folder are the user's own
//! (same-account threat model): convenience, not a defence; pairing / Hello is the defence.

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
/// What to do when the pairing key could not be made (no PIN, cancelled).
const NO_KEY_HOW: &str = "set up a Windows Hello PIN (Settings > Accounts > Sign-in options), \
     then double-click AIB-Setup again";
/// What to do when the key there would be refused by aibd.
const KEY_BAD_HOW: &str = "delete that folder, then double-click AIB-Setup again";
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
    pub base: PathBuf,
    pub dir: PathBuf,
    pub exe: PathBuf,
    pub new: PathBuf,
    pub prev: PathBuf,
}

/// `<local app data>\AIB\aibd.exe` (+ `.new` / `.prev` beside it); the folder must be given and
/// absolute. It comes from the `LOCALAPPDATA` environment variable (3e53b N3): the user's own
/// setting, like everything else here; a folder that is a link is refused in step 0.
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
    let new = dir.join(format!("{EXE_NAME}.new"));
    let prev = dir.join(format!("{EXE_NAME}.prev"));
    Ok(Places {
        base,
        dir,
        exe,
        new,
        prev,
    })
}

/// What the setup does to the machine; the real one is in `ctl.rs`, tests use a stand-in.
pub trait Ops {
    /// Is this process elevated (Run as administrator)?
    fn elevated(&self) -> Result<bool, String>;
    /// Does `p` exist?
    fn exists(&self, p: &Path) -> bool;
    /// Is `p` a symbolic link, junction or other reparse point (not followed)?
    fn is_link(&self, p: &Path) -> bool;
    /// Are these the same file (the setup started from the installed copy)?
    fn same_file(&self, a: &Path, b: &Path) -> bool;
    /// Create the folder `dir` (and parents) if missing.
    fn make_dir(&self, dir: &Path) -> Result<(), String>;
    /// Copy `from` to a NEW file `to` (fails if `to` exists), reading `from` through a
    /// read-only share; the SHA-256 (hex) of the bytes copied.
    fn copy_new(&self, from: &Path, to: &Path) -> Result<String, String>;
    /// SHA-256 (hex) of `p`, read through a read-only share.
    fn hash(&self, p: &Path) -> Result<String, String>;
    /// Remove the file `p` (a link itself, never what it points at); none there is fine.
    fn remove(&self, p: &Path);
    /// Move `from` to `to`, replacing `to`.
    fn rename(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// The pairing key already there, checked the way aibd checks it at start.
    fn pair_key(&self) -> PairKey;
    /// `aibd ctl pair-key init`.
    fn pair_key_init(&self) -> Result<(), String>;
    /// The Run value `name`, if any.
    fn run_get(&self, name: &str) -> Result<Option<String>, String>;
    /// Write the Run value.
    fn run_set(&self, name: &str, command: &str) -> Result<(), String>;
    /// Remove the Run value when it reads exactly `expected`.
    fn run_delete_if(&self, name: &str, expected: &str) -> Result<bool, String>;
    /// Does an aibd answer `/api/v1/health`?
    fn healthy(&self) -> bool;
    /// Start `exe` in the background, no window.
    fn start(&self, exe: &Path) -> Result<(), String>;
    /// Wait.
    fn pace(&self, d: Duration);
    /// Open the page (`aibd ctl web`).
    fn open_page(&self) -> Result<(), String>;
    /// One value of AIB's Settings > Apps entry, if any.
    fn settings_get(&self, name: &str) -> Result<Option<String>, String>;
    /// Write one value of AIB's Settings > Apps entry.
    fn settings_set(&self, name: &str, value: &str) -> Result<(), String>;
    /// Remove one value of that entry when it reads exactly `expected`.
    fn settings_delete_if(&self, name: &str, expected: &str) -> Result<bool, String>;
    /// Remove the folder `dir` if it is empty; anything in it keeps it.
    fn remove_dir_if_empty(&self, dir: &Path);
}

/// This aibd's version, shown in Settings > Apps.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// What Settings > Apps runs to remove AIB: the quoted program and `setup --uninstall`.
pub fn uninstall_command(command: &str) -> String {
    format!("{command} setup --uninstall")
}

/// AIB's Settings > Apps entry (COND r0 §4-1): written when there is none or it is ours (same
/// UninstallString), left alone when it is somebody else's. Not being listed there does not
/// stop the setup, so this says why instead of failing.
fn settings_entry(p: &Places, command: &str, ops: &dyn Ops) -> Result<(), String> {
    let ours = uninstall_command(command);
    if let Some(u) = ops.settings_get("UninstallString")? {
        if u != ours {
            return Err(format!(
                "Settings > Apps already has an AIB entry that is not ours ({u}); left as it is"
            ));
        }
    }
    let dir = p.dir.to_string_lossy();
    for (name, value) in [
        ("DisplayName", "AIB"),
        ("DisplayVersion", VERSION),
        ("Publisher", "Ez2"),
        ("InstallLocation", dir.as_ref()),
        ("UninstallString", ours.as_str()),
    ] {
        ops.settings_set(name, value)?;
    }
    Ok(())
}

/// The pairing key the setup finds (3e53b REVIEW-winsetup L1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PairKey {
    /// No key file at all: make one.
    None,
    /// Both files there and they pass aibd's own start-up check (load + Hello check): keep it.
    Good,
    /// Something is there but aibd would refuse it (a PIN reset voids the Hello key, one file
    /// missing, …): the reason, naming the folder. The setup stops and does not delete it.
    Bad(String),
}

/// How the setup ended, for the last lines it prints.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Done {
    /// SHA-256 of the program now installed, when this run copied it (to compare with the
    /// release page's SHA256SUMS).
    pub sha256: Option<String>,
    /// An aibd was already answering, so nothing was started.
    pub already_running: bool,
}

/// The Run value the setup writes for `p`: the quoted path and nothing else.
pub fn run_command(p: &Places) -> Result<String, String> {
    aib_sys::winrun::quoted(&p.exe.to_string_lossy())
}

/// Step 0: elevated, or a link / junction on the way ⇒ the reason to stop.
fn check(p: &Places, ops: &dyn Ops) -> Result<(), String> {
    if ops.elevated()? {
        return Err("installer_elevated: started as administrator; double-click it as yourself (not \"Run as administrator\")".into());
    }
    for layer in [&p.base, &p.dir, &p.exe, &p.prev] {
        if ops.is_link(layer) {
            return Err(format!(
                "installer_path_redirected: {} is a link or junction; not installing there",
                layer.display()
            ));
        }
    }
    Ok(())
}

/// Step 1: put `current` at `p.exe` through `.new`, hashes compared; the hash.
fn place(p: &Places, current: &Path, ops: &dyn Ops) -> Result<String, String> {
    if ops.exists(&p.exe) && ops.healthy() {
        return Err(format!(
            "installer_running: AIB is running from {}; quit it (signing out and back in also works), then double-click again",
            p.exe.display()
        ));
    }
    ops.make_dir(&p.dir)?;
    ops.remove(&p.new);
    let want = ops.copy_new(current, &p.new)?;
    let got = ops.hash(&p.new)?;
    if got != want {
        ops.remove(&p.new);
        return Err(format!(
            "installer_payload_mismatch: the copy in {} does not match this program; nothing replaced",
            p.new.display()
        ));
    }
    if ops.exists(&p.exe) {
        ops.rename(&p.exe, &p.prev)
            .map_err(|e| format!("keeping the older copy as {}: {e}", p.prev.display()))?;
    }
    ops.rename(&p.new, &p.exe)
        .map_err(|e| format!("moving the new copy in place: {e}"))?;
    Ok(want)
}

/// Step 3: the Run value, ours or none before, read back after.
fn sign_in_entry(command: &str, ops: &dyn Ops) -> Result<(), String> {
    if let Some(v) = ops.run_get(RUN_NAME)? {
        if v != command {
            return Err(format!(
                "installer_run_foreign: there is already a sign-in entry named {RUN_NAME} ({v}); left as it is, not installing over it"
            ));
        }
    }
    ops.run_set(RUN_NAME, command)
        .map_err(|e| format!("could not write the sign-in entry: {e}"))?;
    if ops.run_get(RUN_NAME)?.as_deref() != Some(command) {
        let _ = ops.run_delete_if(RUN_NAME, command);
        return Err(
            "installer_run_mismatch: the sign-in entry did not read back as written; removed"
                .into(),
        );
    }
    Ok(())
}

/// Run the setup from `current` (this program) into `p`. Every step says one line through
/// `say`; the first failing step ends it with its reason.
pub fn run(
    p: &Places,
    current: &Path,
    ops: &dyn Ops,
    say: &mut dyn FnMut(&str),
) -> Result<Done, String> {
    run_with(p, current, ops, &run_command(p)?, say)
}

/// [`run`] with the Run command given (tests off Windows have no `X:\` path to quote).
fn run_with(
    p: &Places,
    current: &Path,
    ops: &dyn Ops,
    command: &str,
    say: &mut dyn FnMut(&str),
) -> Result<Done, String> {
    let mut done = Done::default();
    // 0. checks
    check(p, ops)?;
    // 1. the program
    if ops.same_file(current, &p.exe) {
        say(&format!("1/5 already in {}", p.exe.display()));
    } else {
        let sha = place(p, current, ops)?;
        say(&format!(
            "1/5 copied to {} (SHA-256 {sha})",
            p.exe.display()
        ));
        done.sha256 = Some(sha);
    }
    // 2. the pairing key
    match ops.pair_key() {
        PairKey::Good => say("2/5 pairing key: the one already made is kept"),
        PairKey::Bad(why) => {
            return Err(format!("installer_pair_key_bad: {why}; {KEY_BAD_HOW}"));
        }
        PairKey::None => {
            if let Err(e) = ops.pair_key_init() {
                return Err(format!("pairing key not made ({e}); {NO_KEY_HOW}"));
            }
            say("2/5 pairing key made (Windows Hello)");
        }
    }
    // 3. start at sign-in
    sign_in_entry(command, ops)?;
    say(&format!("3/5 starts at sign-in: {RUN_NAME} = {command}"));
    if let Err(e) = settings_entry(p, command, ops) {
        say(&format!("3/5 not listed in Settings > Apps: {e}"));
    }
    // 4. running now
    if ops.healthy() {
        done.already_running = true;
        say("4/5 aibd is already running");
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

/// `aibd setup --uninstall` (also what Settings > Apps runs): remove the Run value and the
/// Settings entry when they are ours, and the program files when AIB is not running. The data
/// folder always stays (deleting data needs Hello: not in this slice).
pub fn uninstall(p: &Places, ops: &dyn Ops, say: &mut dyn FnMut(&str)) -> Result<(), String> {
    uninstall_with(p, ops, &run_command(p)?, say)
}

fn uninstall_with(
    p: &Places,
    ops: &dyn Ops,
    command: &str,
    say: &mut dyn FnMut(&str),
) -> Result<(), String> {
    check(p, ops)?;
    match ops.run_get(RUN_NAME)? {
        Some(v) if v == command => {
            ops.run_delete_if(RUN_NAME, command)?;
            say(&format!("removed the sign-in entry {RUN_NAME}"));
        }
        Some(v) => say(&format!(
            "the sign-in entry {RUN_NAME} is not ours ({v}); left as it is"
        )),
        None => say(&format!("no sign-in entry {RUN_NAME}")),
    }
    let ours = uninstall_command(command);
    let listed_ours = ops.settings_get("UninstallString")?.as_deref() == Some(ours.as_str());
    if listed_ours {
        for name in aib_sys::winrun::UNINSTALL_NAMES {
            if let Some(v) = ops.settings_get(name)? {
                ops.settings_delete_if(name, &v)?;
            }
        }
        say("removed AIB from Settings > Apps");
    }
    let running = ops.healthy();
    if running {
        say(&format!(
            "AIB is running, so the program in {} stays; sign out and back in, then delete it",
            p.dir.display()
        ));
    } else {
        for f in [&p.exe, &p.prev, &p.new] {
            ops.remove(f);
        }
        ops.remove_dir_if_empty(&p.dir);
        say(&format!("removed the program from {}", p.dir.display()));
    }
    say("kept: your data (pairing key, seats, settings)");
    Ok(())
}

/// `aibd setup --rollback`: put the previous program (`aibd.exe.prev`) back, the current one
/// becoming `.prev`. Not while AIB is running.
pub fn rollback(p: &Places, ops: &dyn Ops, say: &mut dyn FnMut(&str)) -> Result<(), String> {
    check(p, ops)?;
    if !ops.exists(&p.prev) {
        return Err(format!(
            "installer_no_prev: there is no earlier program ({}) to go back to",
            p.prev.display()
        ));
    }
    let busy = ops.exists(&p.exe) && ops.healthy();
    if busy {
        return Err(format!(
            "installer_running: AIB is running from {}; quit it (signing out and back in also works), then try again",
            p.exe.display()
        ));
    }
    ops.remove(&p.new);
    if ops.exists(&p.exe) {
        ops.rename(&p.exe, &p.new)?;
    }
    ops.rename(&p.prev, &p.exe)?;
    if ops.exists(&p.new) {
        ops.rename(&p.new, &p.prev)?;
    }
    say(&format!(
        "went back to the earlier program in {}; the newer one is kept as {}",
        p.exe.display(),
        p.prev.display()
    ));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::BTreeMap;

    const CMD: &str = r#""C:\Users\A B\AppData\Local\AIB\aibd.exe""#;

    #[derive(Default)]
    struct Fake {
        elevated: bool,
        links: Vec<PathBuf>,
        files: RefCell<Vec<PathBuf>>,
        same: bool,
        key: Option<PairKey>,
        key_err: Option<String>,
        copy_sha: Option<String>,
        disk_sha: Option<String>,
        run: RefCell<BTreeMap<String, String>>,
        run_mangle: bool,
        settings: RefCell<BTreeMap<String, String>>,
        healthy_after: Option<usize>,
        calls: RefCell<Vec<String>>,
        health_asks: RefCell<usize>,
    }

    impl Fake {
        fn log(&self, s: String) {
            self.calls.borrow_mut().push(s);
        }
        fn has(&self, prefix: &str) -> bool {
            self.calls.borrow().iter().any(|c| c.starts_with(prefix))
        }
    }

    impl Ops for Fake {
        fn elevated(&self) -> Result<bool, String> {
            Ok(self.elevated)
        }
        fn exists(&self, p: &Path) -> bool {
            self.files.borrow().iter().any(|f| f == p)
        }
        fn is_link(&self, p: &Path) -> bool {
            self.links.iter().any(|l| l == p)
        }
        fn same_file(&self, _: &Path, _: &Path) -> bool {
            self.same
        }
        fn make_dir(&self, _: &Path) -> Result<(), String> {
            Ok(())
        }
        fn copy_new(&self, from: &Path, to: &Path) -> Result<String, String> {
            self.log(format!("copy {} {}", from.display(), to.display()));
            self.files.borrow_mut().push(to.to_path_buf());
            Ok(self.copy_sha.clone().unwrap_or_else(|| "aa".into()))
        }
        fn hash(&self, _: &Path) -> Result<String, String> {
            Ok(self
                .disk_sha
                .clone()
                .or_else(|| self.copy_sha.clone())
                .unwrap_or_else(|| "aa".into()))
        }
        fn remove(&self, p: &Path) {
            self.files.borrow_mut().retain(|f| f != p);
        }
        fn rename(&self, from: &Path, to: &Path) -> Result<(), String> {
            self.log(format!("rename {} {}", from.display(), to.display()));
            let mut f = self.files.borrow_mut();
            f.retain(|x| x != from && x != to);
            f.push(to.to_path_buf());
            Ok(())
        }
        fn pair_key(&self) -> PairKey {
            self.key.clone().unwrap_or(PairKey::None)
        }
        fn pair_key_init(&self) -> Result<(), String> {
            self.log("pair".into());
            self.key_err.clone().map_or(Ok(()), Err)
        }
        fn run_get(&self, name: &str) -> Result<Option<String>, String> {
            Ok(self.run.borrow().get(name).cloned())
        }
        fn run_set(&self, name: &str, command: &str) -> Result<(), String> {
            self.log(format!("run {name}={command}"));
            let v = if self.run_mangle {
                format!("{command} x")
            } else {
                command.to_string()
            };
            self.run.borrow_mut().insert(name.into(), v);
            Ok(())
        }
        fn run_delete_if(&self, name: &str, expected: &str) -> Result<bool, String> {
            self.log(format!("delete {name}"));
            let mut r = self.run.borrow_mut();
            if r.get(name).map(String::as_str) == Some(expected) {
                r.remove(name);
                return Ok(true);
            }
            Ok(false)
        }
        fn healthy(&self) -> bool {
            let mut n = self.health_asks.borrow_mut();
            *n += 1;
            self.healthy_after.is_some_and(|k| *n > k)
        }
        fn start(&self, exe: &Path) -> Result<(), String> {
            self.log(format!("start {}", exe.display()));
            Ok(())
        }
        fn pace(&self, _: Duration) {}
        fn open_page(&self) -> Result<(), String> {
            self.log("open".into());
            Ok(())
        }
        fn settings_get(&self, name: &str) -> Result<Option<String>, String> {
            Ok(self.settings.borrow().get(name).cloned())
        }
        fn settings_set(&self, name: &str, value: &str) -> Result<(), String> {
            self.settings.borrow_mut().insert(name.into(), value.into());
            Ok(())
        }
        fn settings_delete_if(&self, name: &str, expected: &str) -> Result<bool, String> {
            let mut s = self.settings.borrow_mut();
            if s.get(name).map(String::as_str) == Some(expected) {
                s.remove(name);
                return Ok(true);
            }
            Ok(false)
        }
        fn remove_dir_if_empty(&self, dir: &Path) {
            self.log(format!("rmdir {}", dir.display()));
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
        let r = run_with(&here(), Path::new("dl/AIB-Setup.exe"), f, CMD, &mut |l| {
            lines.push(l.to_string());
        });
        (r, lines)
    }

    #[test]
    fn the_setup_copies_pairs_writes_the_run_value_starts_and_opens_in_that_order() {
        let f = Fake {
            healthy_after: Some(3),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert_eq!(
            r,
            Ok(Done {
                sha256: Some("aa".into()),
                already_running: false
            }),
            "reason: result {lines:?}"
        );
        let p = here();
        assert_eq!(
            *f.calls.borrow(),
            vec![
                format!("copy dl/AIB-Setup.exe {}", p.new.display()),
                format!("rename {} {}", p.new.display(), p.exe.display()),
                "pair".to_string(),
                format!("run AIB={CMD}"),
                format!("start {}", p.exe.display()),
                "open".to_string(),
            ],
            "reason: order"
        );
        assert_eq!(lines.len(), 5, "reason: one line per step {lines:?}");
    }

    #[cfg(windows)]
    #[test]
    fn on_windows_the_run_command_is_the_quoted_installed_path() {
        assert_eq!(
            run_command(&here()).as_deref(),
            Ok(CMD),
            "reason: run_command"
        );
    }

    #[test]
    fn an_older_copy_is_kept_as_prev_and_the_new_one_moved_in() {
        let p = here();
        let f = Fake {
            healthy_after: Some(1),
            files: RefCell::new(vec![p.exe.clone()]),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(r.is_ok(), "reason: result {r:?}");
        let calls = f.calls.borrow();
        let prev = format!("rename {} {}", p.exe.display(), p.prev.display());
        let new = format!("rename {} {}", p.new.display(), p.exe.display());
        let at = |s: &str| calls.iter().position(|c| c == s);
        assert!(
            at(&prev).is_some() && at(&prev) < at(&new),
            "reason: prev_first {calls:?}"
        );
    }

    #[test]
    fn a_copy_whose_hash_differs_is_not_moved_in_and_nothing_after_runs() {
        let f = Fake {
            copy_sha: Some("aa".into()),
            disk_sha: Some("bb".into()),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_payload_mismatch")),
            "reason: {r:?}"
        );
        assert!(
            !f.has("rename") && !f.has("run ") && !f.has("start"),
            "reason: went_on {:?}",
            f.calls.borrow()
        );
        assert!(!f.exists(&here().new), "reason: new_left");
    }

    #[test]
    fn a_running_installed_aibd_is_not_overwritten() {
        let p = here();
        let f = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone()]),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_running")),
            "reason: {r:?}"
        );
        assert!(
            !f.has("copy") && !f.has("rename"),
            "reason: touched {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn started_from_the_installed_copy_it_only_fills_in_and_does_not_start_twice() {
        let f = Fake {
            same: true,
            key: Some(PairKey::Good),
            healthy_after: Some(0),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert_eq!(
            r,
            Ok(Done {
                sha256: None,
                already_running: true
            }),
            "reason: result"
        );
        assert!(
            !f.has("copy") && !f.has("pair") && !f.has("start"),
            "reason: redid {:?}",
            f.calls.borrow()
        );
        assert!(
            f.has("run AIB=") && f.has("open"),
            "reason: not_filled {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn a_key_already_made_is_kept() {
        let f = Fake {
            key: Some(PairKey::Good),
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert!(!f.has("pair"), "reason: remade {:?}", f.calls.borrow());
    }

    #[test]
    fn a_key_aibd_would_refuse_stops_the_setup_without_deleting_it() {
        let f = Fake {
            key: Some(PairKey::Bad("pair_key_mismatch in C:\\x\\pair-key".into())),
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_pair_key_bad") && e.contains("pair-key")),
            "reason: {r:?}"
        );
        assert!(
            !f.has("pair") && !f.has("run ") && !f.has("start") && !f.has("open"),
            "reason: went_on {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn no_pairing_key_means_no_run_value_and_nothing_started() {
        let f = Fake {
            key_err: Some("no PIN".into()),
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.contains("no PIN") && e.contains("double-click")),
            "reason: {r:?}"
        );
        assert!(
            !f.has("run ") && !f.has("start") && !f.has("open"),
            "reason: went_on {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn an_elevated_setup_stops_before_touching_anything() {
        let f = Fake {
            elevated: true,
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_elevated")),
            "reason: {r:?}"
        );
        assert!(
            f.calls.borrow().is_empty(),
            "reason: touched {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn a_link_or_junction_on_the_way_stops_the_setup() {
        let p = here();
        for l in [p.base.clone(), p.dir.clone(), p.exe.clone(), p.prev.clone()] {
            let f = Fake {
                links: vec![l.clone()],
                ..Fake::default()
            };
            let (r, _) = go(&f);
            assert!(
                r.as_ref()
                    .is_err_and(|e| e.starts_with("installer_path_redirected")),
                "reason: {} {r:?}",
                l.display()
            );
            assert!(
                f.calls.borrow().is_empty(),
                "reason: touched {:?}",
                f.calls.borrow()
            );
        }
    }

    #[test]
    fn somebody_elses_run_value_is_left_and_the_setup_stops() {
        let f = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        f.run
            .borrow_mut()
            .insert("AIB".into(), r#""C:\other\x.exe""#.into());
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_run_foreign")),
            "reason: {r:?}"
        );
        assert!(
            !f.has("run ") && !f.has("start"),
            "reason: went_on {:?}",
            f.calls.borrow()
        );
        assert_eq!(
            f.run.borrow().get("AIB").map(String::as_str),
            Some(r#""C:\other\x.exe""#),
            "reason: changed"
        );
    }

    #[test]
    fn a_run_value_that_reads_back_different_is_removed_and_the_setup_stops() {
        let f = Fake {
            run_mangle: true,
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_run_mismatch")),
            "reason: {r:?}"
        );
        assert!(
            f.has("delete AIB") && !f.has("start"),
            "reason: {:?}",
            f.calls.borrow()
        );
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
        assert!(!f.has("open"), "reason: opened");
    }

    #[test]
    fn uninstall_removes_only_our_run_value() {
        let p = here();
        let mut lines = Vec::new();
        let ours = Fake::default();
        ours.run.borrow_mut().insert("AIB".into(), CMD.into());
        assert!(uninstall_with(&p, &ours, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(ours.run.borrow().is_empty(), "reason: not_removed");
        let theirs = Fake::default();
        theirs
            .run
            .borrow_mut()
            .insert("AIB".into(), r#""C:\other\x.exe""#.into());
        assert!(uninstall_with(&p, &theirs, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(!theirs.has("delete"), "reason: deleted_theirs");
        assert_eq!(theirs.run.borrow().len(), 1, "reason: removed_theirs");
    }

    #[test]
    fn the_setup_lists_aib_in_settings_with_an_uninstall_command() {
        let f = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        let s = f.settings.borrow();
        assert_eq!(
            s.get("DisplayName").map(String::as_str),
            Some("AIB"),
            "reason: name"
        );
        assert_eq!(
            s.get("UninstallString").cloned(),
            Some(format!("{CMD} setup --uninstall")),
            "reason: uninstall_string"
        );
        assert_eq!(s.len(), 5, "reason: values {s:?}");
    }

    #[test]
    fn somebody_elses_settings_entry_is_left_and_the_setup_goes_on() {
        let f = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        f.settings
            .borrow_mut()
            .insert("UninstallString".into(), "\"C:\\x\\un.exe\"".into());
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert_eq!(
            f.settings.borrow().len(),
            1,
            "reason: wrote {:?}",
            f.settings.borrow()
        );
        assert!(
            lines.iter().any(|l| l.contains("not ours")),
            "reason: said {lines:?}"
        );
    }

    #[test]
    fn uninstall_removes_the_program_only_when_aib_is_not_running() {
        let p = here();
        let mut lines = Vec::new();
        let idle = Fake::default();
        assert!(uninstall_with(&p, &idle, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(
            idle.has("rmdir"),
            "reason: kept_idle {:?}",
            idle.calls.borrow()
        );
        let busy = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone()]),
            ..Fake::default()
        };
        assert!(uninstall_with(&p, &busy, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(
            busy.exists(&p.exe) && !busy.has("rmdir"),
            "reason: removed_running"
        );
    }

    #[test]
    fn uninstall_removes_only_our_settings_entry() {
        let p = here();
        let mut lines = Vec::new();
        let ours = Fake::default();
        let _ = settings_entry(&p, CMD, &ours);
        assert_eq!(ours.settings.borrow().len(), 5, "reason: setup");
        assert!(uninstall_with(&p, &ours, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(
            ours.settings.borrow().is_empty(),
            "reason: left {:?}",
            ours.settings.borrow()
        );
        let theirs = Fake::default();
        theirs
            .settings
            .borrow_mut()
            .insert("UninstallString".into(), "x".into());
        theirs
            .settings
            .borrow_mut()
            .insert("DisplayName".into(), "AIB".into());
        assert!(uninstall_with(&p, &theirs, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert_eq!(theirs.settings.borrow().len(), 2, "reason: removed_theirs");
    }

    #[test]
    fn rollback_swaps_in_the_previous_program_but_not_while_running() {
        let p = here();
        let mut lines = Vec::new();
        let none = Fake::default();
        assert!(rollback(&p, &none, &mut |l| lines.push(l.to_string()))
            .is_err_and(|e| e.starts_with("installer_no_prev")));
        let busy = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone(), p.prev.clone()]),
            ..Fake::default()
        };
        assert!(rollback(&p, &busy, &mut |l| lines.push(l.to_string()))
            .is_err_and(|e| e.starts_with("installer_running")));
        assert!(!busy.has("rename"), "reason: swapped_running");
        let idle = Fake {
            files: RefCell::new(vec![p.exe.clone(), p.prev.clone()]),
            ..Fake::default()
        };
        assert!(rollback(&p, &idle, &mut |l| lines.push(l.to_string())).is_ok());
        assert_eq!(
            *idle.calls.borrow(),
            vec![
                format!("rename {} {}", p.exe.display(), p.new.display()),
                format!("rename {} {}", p.prev.display(), p.exe.display()),
                format!("rename {} {}", p.new.display(), p.prev.display()),
            ],
            "reason: swap"
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
        assert_eq!(p.new.parent(), Some(p.dir.as_path()), "reason: new_beside");
    }
}
