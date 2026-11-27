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
//! 2. Windows Hello (B2, Andy 11-26 10:1x): only asked whether it can be used here — no window,
//!    no key made, nothing kept, the pair-key folder not read. Not usable ⇒ the setup goes on
//!    (B3) and what needs the owner stays closed; the line keeps the code and the HRESULT, and
//!    only a machine without Windows Hello is told to set a PIN ([`hello_not_ready_how`]);
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

use aib_sys::winrun::PathKind;
use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// The Run value's name.
pub const RUN_NAME: &str = "AIB";
/// The folder under `%LOCALAPPDATA%`.
pub const DIR_NAME: &str = "AIB";
/// The installed program's file name.
pub const EXE_NAME: &str = "aibd.exe";
/// `aib` (Andy 11-27 10:1x): a copy of aibd.exe beside it; called `aib` it takes `aib web` only.
pub const AIB_EXE: &str = "aib.exe";
/// In the AIB folder: the PATH entry the setup added, exactly as written (uninstall takes out
/// that entry only).
pub const PATH_NOTE: &str = "aib-path.txt";
/// What Windows runs for a typed `aib`: another of these anywhere on PATH ⇒ ours is not placed.
pub const AIB_NAMES: [&str; 4] = ["aib.exe", "aib.cmd", "aib.bat", "aib.com"];
/// A program whose file name starts with this (any case) runs the setup when started with no
/// arguments: `AIB-Setup.exe`, and what a browser makes of a second download, `AIB-Setup (1).exe`.
pub const SETUP_STEM: &str = "aib-setup";
/// What to do when Windows did not answer the Hello check for any other reason: the code and
/// the HRESULT in the line say why; a PIN is not the answer there (ebab8 11-26 10:01, the
/// laptop's `se_error` was read as "set a PIN" on a machine that has one).
const HELLO_REFUSED_HOW: &str = "Windows did not answer the Windows Hello check (the code above \
     says why); if this line stays after a restart, send it to support";

/// What the Hello check says when it is not running in the desktop session (ssh, remote
/// desktop, a task); also how [`hello_not_ready_how`] tells that `unavailable` from a machine
/// without Windows Hello.
pub const DESKTOP_ONLY: &str =
    "run this at the computer, signed in at its desktop (not over ssh or remote desktop)";
/// What to do then.
const HELLO_DESKTOP_HOW: &str = "double-click AIB-Setup at this computer's desktop (not over \
     ssh, remote desktop or a task) to check Windows Hello again";

/// The hint after "2/5 Windows Hello is not ready (...)", by the 1.x code in it: only a machine
/// without Windows Hello (`no_biometry`, `unavailable` not about the session) is told to set up
/// a PIN; the laptop's `se_error` (0x80090027) is not.
pub fn hello_not_ready_how(why: &str) -> &'static str {
    let has = |code: &str| {
        why.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .any(|w| w == code)
    };
    if why.contains(DESKTOP_ONLY) {
        HELLO_DESKTOP_HOW
    } else if has("no_biometry") || has("unavailable") {
        HELLO_LATER_HOW
    } else {
        HELLO_REFUSED_HOW
    }
}

/// A Windows Hello error as the setup log says it: the 1.x code and what the OS said, so an
/// `se_error` keeps its HRESULT (`se_error (os error 0x80004005)`) instead of losing it.
pub fn hello_error_line(e: &aib_sys::hello::HelloError) -> String {
    format!("{} ({e})", e.code())
}

/// B2 / B3: what to do when Windows Hello is not set up; AIB picks it up without a reinstall.
const HELLO_LATER_HOW: &str =
    "set up a Windows Hello PIN (Settings > Accounts > Sign-in options); no need to install again";
/// How to end the running AIB before the setup (or a rollback) can swap the program (U1,
/// PLAN-win-upgrade-path-90856-1126, 3e53b 11:2x). aibd on Windows has no console to stop it
/// with, and signing out and back in does not help: the Run value starts it again at sign-in.
pub fn quit_how(then: &str) -> String {
    format!(
        "to update it: 1. open Task Manager (Ctrl+Shift+Esc) > Details, select aibd.exe, End task \
         (AI seats that are open stop too; your data stays); 2. {then}"
    )
}
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
    pub aib: PathBuf,
    pub aib_new: PathBuf,
    pub path_note: PathBuf,
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
        aib: dir.join(AIB_EXE),
        aib_new: dir.join(format!("{AIB_EXE}.new")),
        path_note: dir.join(PATH_NOTE),
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
    /// B2 (Andy 11-26 10:1x): is Windows Hello set up for this user here (no window, no key)?
    /// The error is the Hello error line ([`hello_error_line`]).
    fn hello_ready(&self) -> Result<(), String>;
    /// The Run value `name`, if any.
    fn run_get(&self, name: &str) -> Result<Option<String>, String>;
    /// Write the Run value.
    fn run_set(&self, name: &str, command: &str) -> Result<(), String>;
    /// Remove the Run value when it reads exactly `expected`.
    fn run_delete_if(&self, name: &str, expected: &str) -> Result<bool, String>;
    /// Does an aibd answer `/api/v1/health`?
    fn healthy(&self) -> bool;
    /// Show `line` at the console and wait: Enter ⇒ true, the window closed / no input ⇒ false.
    /// Only against closing an AIB in use by mistake ([`END_RUNNING_ASK`]); never an owner check.
    fn ask_end_running(&self, line: &str) -> bool;
    /// End the one running copy of `exe` of this user and session (aib_sys::winstop::stop_one).
    fn end_running(&self, exe: &Path) -> Result<(), String>;
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
    /// The folders an "AIB" shortcut goes in: this user's desktop and Start menu.
    fn shortcut_dirs(&self) -> Result<Vec<PathBuf>, String>;
    /// Target and arguments written in the shortcut `link`.
    fn shortcut_read(&self, link: &Path) -> Result<(PathBuf, String), String>;
    /// Write the shortcut `link` to `exe` with `args`.
    fn shortcut_make(&self, link: &Path, exe: &Path, args: &str) -> Result<(), String>;
    /// This process's PATH (where a typed `aib` is looked up).
    fn path_env(&self) -> Option<OsString>;
    /// One environment variable, to compare a `%NAME%` PATH entry.
    fn env_var(&self, name: &str) -> Option<String>;
    /// The user's PATH as stored, and its type.
    fn user_path_read(&self) -> Result<Option<(String, PathKind)>, String>;
    /// Write the user's PATH when it still reads `before`; read back after.
    fn user_path_write(
        &self,
        new: &str,
        kind: PathKind,
        before: Option<(&str, PathKind)>,
    ) -> Result<(), String>;
    /// Tell open programs the environment changed.
    fn env_changed(&self);
    /// A small text file of ours ([`PATH_NOTE`]); `None` when there is none.
    fn note_read(&self, p: &Path) -> Option<String>;
    /// Write that file.
    fn note_write(&self, p: &Path, text: &str) -> Result<(), String>;
}

/// The shortcut's file name, on the desktop and in the Start menu.
pub const SHORTCUT: &str = "AIB.lnk";
/// What the shortcut runs after the installed aibd.exe: open the workbench.
pub const SHORTCUT_ARGS: &str = "ctl web";

/// Is `link` the setup's own shortcut: not a link itself, readable, pointing at `exe` (case
/// aside, Windows paths) with [`SHORTCUT_ARGS`]? Anything else is somebody else's.
fn shortcut_ours(link: &Path, exe: &Path, ops: &dyn Ops) -> bool {
    if ops.is_link(link) {
        return false;
    }
    let same = |a: &Path, b: &Path| {
        a.to_string_lossy().to_lowercase() == b.to_string_lossy().to_lowercase()
    };
    matches!(ops.shortcut_read(link), Ok((t, a)) if same(&t, exe) && a == SHORTCUT_ARGS)
}

/// Step 3 (ebab8 11-26 12:20): an "AIB" shortcut on the desktop and in the Start menu that
/// opens the workbench. Written when none is there or it is ours; somebody else's file of
/// that name (or a link) is left as it is. Never stops the setup: a line says what happened.
fn shortcuts(p: &Places, ops: &dyn Ops, say: &mut dyn FnMut(&str)) {
    let dirs = match ops.shortcut_dirs() {
        Ok(d) => d,
        Err(e) => {
            say(&format!("3/5 no AIB shortcut: {e}"));
            return;
        }
    };
    // the target is the installed aibd.exe only: a plain file, never a link (3e53b r1 #2)
    if ops.is_link(&p.exe) || !ops.exists(&p.exe) {
        say(&format!(
            "3/5 no AIB shortcut: {} is not a plain file",
            p.exe.display()
        ));
        return;
    }
    for d in dirs {
        let link = d.join(SHORTCUT);
        if ops.exists(&link) && !shortcut_ours(&link, &p.exe, ops) {
            say(&format!(
                "3/5 {} is somebody else's; left as it is",
                link.display()
            ));
            continue;
        }
        // written ⇒ read back: only target + arguments as meant count (3e53b r1 #4)
        let made = ops
            .shortcut_make(&link, &p.exe, SHORTCUT_ARGS)
            .and_then(|()| {
                let read_back_ours = shortcut_ours(&link, &p.exe, ops);
                if read_back_ours {
                    Ok(())
                } else {
                    Err("it does not read back as written".to_string())
                }
            });
        match made {
            Ok(()) => say(&format!("3/5 AIB shortcut: {}", link.display())),
            Err(why) => say(&format!("3/5 no AIB shortcut in {}: {why}", d.display())),
        }
    }
}

// ---------------------------------------------------------------- aib (Andy 11-27 10:1x)
// PRE-READ-aib-cmd-windows-3e53b-1127: aib.exe beside aibd.exe the same way aibd.exe is placed
// (.new, hashes compared), the AIB folder at the END of the user's PATH (not before: ours would
// hide the customer's other commands of that name), never setx / PowerShell. Another `aib`
// anywhere on PATH (AIB 1.x) ⇒ none placed (f163c / 3e53b r1). The same account can change its
// own PATH and put another aib first: `aib` is a convenience, not a defence.

/// Another `aib` on this process's PATH (any of [`AIB_NAMES`]; our own aib.exe does not count).
fn another_aib(p: &Places, ops: &dyn Ops) -> Option<PathBuf> {
    let path = ops.path_env()?;
    let ours = crate::aib_cmd::seg_key(&p.dir.to_string_lossy());
    std::env::split_paths(&path)
        .flat_map(|d| {
            let mine = crate::aib_cmd::seg_key(&d.to_string_lossy()) == ours;
            AIB_NAMES
                .iter()
                .filter(move |n| !(mine && **n == AIB_EXE))
                .map(move |n| d.join(n))
        })
        .find(|f| ops.exists(f))
}

/// aib.exe = this aibd.exe: written when none is there or it is ours (this aibd.exe's or the
/// kept older one's hash), never through a link; the copy's hash compared before it is moved in.
fn place_aib(p: &Places, ops: &dyn Ops) -> Result<(), String> {
    if ops.is_link(&p.aib) || ops.is_link(&p.exe) {
        return Err(format!(
            "{} is a link; not written through",
            p.aib.display()
        ));
    }
    let sha = ops.hash(&p.exe)?;
    if ops.exists(&p.aib) {
        let have = ops.hash(&p.aib)?;
        if have == sha {
            return Ok(());
        }
        if ops.hash(&p.prev).ok().as_deref() != Some(have.as_str()) {
            return Err(format!(
                "{} is another program by that name; left as it is",
                p.aib.display()
            ));
        }
    }
    ops.remove(&p.aib_new);
    let want = ops.copy_new(&p.exe, &p.aib_new)?;
    let got = ops.hash(&p.aib_new)?;
    if want != sha || got != sha {
        ops.remove(&p.aib_new);
        return Err(format!(
            "the copy in {} does not match aibd.exe; not placed",
            p.aib_new.display()
        ));
    }
    ops.rename(&p.aib_new, &p.aib)
        .map_err(|e| format!("moving aib.exe in place: {e}"))
}

/// The AIB folder at the end of the user's PATH. `Ok(false)` = there already (nothing written,
/// and it is not ours to take out later). The entry is noted first, so a PATH written is
/// always one uninstall knows; a read that changed meanwhile is tried once more.
fn user_path_add(p: &Places, ops: &dyn Ops) -> Result<bool, String> {
    let seg = p.dir.to_string_lossy().into_owned();
    for _ in 0..2 {
        let before = ops.user_path_read()?;
        let (old, kind) = before
            .as_ref()
            .map_or(("", PathKind::ExpandSz), |(v, k)| (v.as_str(), *k));
        let Some(new) = crate::aib_cmd::path_with(&seg, old, &|n| ops.env_var(n))? else {
            return Ok(false);
        };
        ops.note_write(&p.path_note, &seg)
            .map_err(|e| format!("could not note what is added ({e}); PATH not changed"))?;
        let before = before.as_ref().map(|(v, k)| (v.as_str(), *k));
        match ops.user_path_write(&new, kind, before) {
            Ok(()) => {
                ops.env_changed();
                return Ok(true);
            }
            Err(e) if e == aib_sys::winrun::PATH_CHANGED => {}
            Err(e) => {
                ops.remove(&p.path_note);
                return Err(e);
            }
        }
    }
    ops.remove(&p.path_note);
    Err("the user PATH kept changing while AIB wrote it; not changed".into())
}

/// Step 3: `aib` (aib.exe + the user's PATH). Never stops the setup: a line says what happened.
fn aib_command(p: &Places, ops: &dyn Ops, say: &mut dyn FnMut(&str)) {
    if let Some(other) = another_aib(p, ops) {
        say(&format!(
            "AIB-CMD-LEFT {} is another aib (AIB 1.x?), so none was placed; open the workbench with the AIB shortcut",
            other.display()
        ));
        return;
    }
    if let Err(e) = place_aib(p, ops) {
        say(&format!("AIB-CMD-NOT-WRITTEN {e}"));
        return;
    }
    say(&format!(
        "AIB-CMD {} (aib web opens the workbench)",
        p.aib.display()
    ));
    match user_path_add(p, ops) {
        Ok(true) => say(&format!(
            "AIB-CMD-PATH {} added to the end of your PATH: a new terminal window takes aib web",
            p.dir.display()
        )),
        Ok(false) => {}
        Err(e) => say(&format!(
            "AIB-CMD-PATH {} is not on your PATH ({e}): until it is, type \"{}\" web",
            p.dir.display(),
            p.aib.display()
        )),
    }
}

/// Uninstall: take out of the user's PATH the entry the setup noted, only when it is there
/// exactly once; changed since (gone, or more than once) ⇒ left, and said.
fn user_path_remove(p: &Places, ops: &dyn Ops, say: &mut dyn FnMut(&str)) {
    let Some(seg) = ops.note_read(&p.path_note) else {
        return;
    };
    // the note is the user's file too: only this AIB folder is ever taken out (3e53b r5 N1)
    let this = crate::aib_cmd::seg_key(&p.dir.to_string_lossy());
    if crate::aib_cmd::seg_key(&seg) != this {
        say(&format!(
            "{} names {seg}, not this AIB folder; your PATH is left as it is",
            p.path_note.display()
        ));
        return;
    }
    for _ in 0..2 {
        let before = match ops.user_path_read() {
            Ok(Some(b)) => b,
            Ok(None) => break,
            Err(e) => {
                say(&format!(
                    "your PATH was not read ({e}); remove {seg} from it yourself"
                ));
                return;
            }
        };
        let new = match crate::aib_cmd::path_without(&seg, &before.0) {
            crate::aib_cmd::Without::Removed(n) => n,
            crate::aib_cmd::Without::NotThere => break,
            crate::aib_cmd::Without::Many(n) => {
                say(&format!(
                    "{seg} is on your PATH {n} times (changed since AIB added it); left as it is, remove it yourself"
                ));
                ops.remove(&p.path_note);
                return;
            }
        };
        match ops.user_path_write(&new, before.1, Some((before.0.as_str(), before.1))) {
            Ok(()) => {
                ops.env_changed();
                ops.remove(&p.path_note);
                say(&format!("removed {seg} from your PATH"));
                return;
            }
            Err(e) if e == aib_sys::winrun::PATH_CHANGED => {}
            Err(e) => {
                say(&format!(
                    "{seg} stays on your PATH ({e}); remove it yourself"
                ));
                return;
            }
        }
    }
    ops.remove(&p.path_note);
    say(&format!(
        "{seg} is not on your PATH as AIB added it; nothing taken out"
    ));
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
    for layer in [&p.base, &p.dir, &p.exe, &p.prev, &p.new] {
        if ops.is_link(layer) {
            return Err(format!(
                "installer_path_redirected: {} is a link or junction; not installing there",
                layer.display()
            ));
        }
    }
    Ok(())
}

/// The question before the setup ends a running AIB (U2). Pressing Enter only keeps an AIB in
/// use from being closed by mistake: it is **not** an owner check (a same-account program can
/// end aibd.exe itself), nothing is written, and no other check is relaxed for it (3e53b 11:2x).
pub const END_RUNNING_ASK: &str = "AIB is running. Press Enter to end it and update (AI seats \
     that are open stop too; your data stays), or close this window to leave it running. \
     (This only keeps an AIB you are using from being closed by mistake; it is not an owner check.)";

/// U2 (PLAN-win-upgrade-path-90856-1126): AIB answers from the installed copy ⇒ ask once, end
/// the one running copy (aib_sys::winstop: identity read on the handle that ends it, exactly
/// one), and go on only when it no longer answers. Not asked / not ended ⇒ stop as before.
fn end_running(p: &Places, ops: &dyn Ops) -> Result<(), String> {
    let running = || {
        format!(
            "installer_running: AIB is running from {}; {}",
            p.exe.display(),
            quit_how("double-click AIB-Setup again")
        )
    };
    if !ops.ask_end_running(END_RUNNING_ASK) {
        return Err(running());
    }
    ops.end_running(&p.exe).map_err(|e| {
        format!(
            "installer_end_failed: {e}; {}",
            quit_how("double-click AIB-Setup again")
        )
    })?;
    // something still answers: never swap under a running AIB
    let still_answers = ops.healthy();
    if still_answers {
        return Err(running());
    }
    Ok(())
}

/// Step 1: put `current` at `p.exe` through `.new`, hashes compared; the hash.
fn place(p: &Places, current: &Path, ops: &dyn Ops) -> Result<String, String> {
    if ops.exists(&p.exe) && ops.healthy() {
        end_running(p, ops)?;
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
    // 2. Windows Hello (B2, Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): no key is
    //    made; AIB asks for Hello at each action that needs the owner. Not set up ⇒ the setup
    //    goes on and those actions stay closed (B3) until a PIN is set; no reinstall needed.
    match ops.hello_ready() {
        Ok(()) => say("2/5 Windows Hello is set up: AIB asks for it each time it needs you"),
        Err(e) => say(&format!(
            "2/5 Windows Hello is not ready ({e}): AIB runs, but what needs you stays closed; {}",
            hello_not_ready_how(&e)
        )),
    }
    // 3. start at sign-in
    sign_in_entry(command, ops)?;
    say(&format!("3/5 starts at sign-in: {RUN_NAME} = {command}"));
    if let Err(e) = settings_entry(p, command, ops) {
        say(&format!("3/5 not listed in Settings > Apps: {e}"));
    }
    shortcuts(p, ops, say);
    aib_command(p, ops, say);
    // 4. running now
    if ops.healthy() {
        done.already_running = true;
        say("4/5 aibd is already running");
    } else {
        start_and_wait(&p.exe, ops)?;
        say("4/5 aibd started");
    }
    // 5. the page
    say("5/5 opening the page");
    ops.open_page()?;
    Ok(done)
}

/// The two plain lines the AIB shortcut's window shows while the page pairs (ebab8 11-26 12:39:
/// the window is the customer's, not a terminal's). The code stays visible: it is what the
/// person compares with the page (3e53b PREREAD-win-shortcut-wake r1).
pub fn shortcut_wait_lines(lang: &str, code: &str, secs: u64) -> [String; 2] {
    match lang {
        "zh-TW" => [
            "正在打開 AIB 工作台…".to_string(),
            format!("配對碼 {code}(頁面上已自動填好,{secs} 秒內有效)"),
        ],
        "ja" => [
            "AIB ワークベンチを開いています…".to_string(),
            format!("ペアリングコード {code}(ページに入力済み、{secs} 秒間有効)"),
        ],
        "ko" => [
            "AIB 워크벤치를 여는 중…".to_string(),
            format!("페어링 코드 {code} (페이지에 이미 입력됨, {secs}초 동안 유효)"),
        ],
        _ => [
            "Opening the AIB workbench…".to_string(),
            format!("Pairing code {code} (already filled in on the page; valid {secs} s)"),
        ],
    }
}

/// What the shortcut's window says before it closes after a failure, so the reason above it
/// can be read first.
pub fn shortcut_close_line(lang: &str) -> &'static str {
    match lang {
        "zh-TW" => "按 Enter 關閉這個視窗。",
        "ja" => "Enter キーを押すと、このウィンドウを閉じます。",
        "ko" => "Enter 키를 누르면 이 창이 닫힙니다.",
        _ => "Press Enter to close this window.",
    }
}

/// Start the installed aibd.exe (no window) and wait until it answers.
fn start_and_wait(exe: &Path, ops: &dyn Ops) -> Result<(), String> {
    ops.start(exe)
        .map_err(|e| format!("could not start {}: {e}", exe.display()))?;
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
    Ok(())
}

/// `aibd ctl web` on Windows when aibd does not answer (ebab8 11-26 12:20: the AIB shortcut
/// must work after AIB was ended): start the installed aibd.exe and wait for it, as step 4
/// does. Only when this program is the installed copy and the line names the resident aibd
/// (`resident`, [`crate::wake::names_resident`]); otherwise one line says what to do. What is
/// started is always the installed path from [`places`] (a plain file, not a link), never
/// `current`, a shortcut's target or anything from the command line or the page; the real
/// [`Ops::start`] runs it directly, no shell (3e53b PREREAD-win-shortcut-wake r1).
/// `Ok(true)` = started here, `Ok(false)` = it already answered.
pub fn wake(p: &Places, current: &Path, resident: bool, ops: &dyn Ops) -> Result<bool, String> {
    if ops.healthy() {
        return Ok(false);
    }
    // from aib.exe (`aib web`): ours only (a plain file, this aibd.exe's hash); what is started
    // is still p.exe
    let from_aib = || {
        ops.same_file(current, &p.aib)
            && !ops.is_link(&p.aib)
            && ops
                .hash(&p.aib)
                .is_ok_and(|h| ops.hash(&p.exe).is_ok_and(|e| e == h))
    };
    if !resident || !(ops.same_file(current, &p.exe) || from_aib()) {
        return Err(format!(
            "aibd is not running. Double-click AIB on the desktop (or run {} ctl web)",
            p.exe.display()
        ));
    }
    let plain = !ops.is_link(&p.exe) && ops.exists(&p.exe);
    if !plain {
        return Err(format!(
            "aibd is not running, and {} is not a plain file; run AIB-Setup again",
            p.exe.display()
        ));
    }
    start_and_wait(&p.exe, ops).map(|()| true)
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
    if let Ok(dirs) = ops.shortcut_dirs() {
        for d in dirs {
            let link = d.join(SHORTCUT);
            if !ops.exists(&link) {
                continue;
            }
            if shortcut_ours(&link, &p.exe, ops) {
                ops.remove(&link);
                say(&format!("removed the shortcut {}", link.display()));
            } else {
                say(&format!(
                    "the shortcut {} is not ours; left as it is",
                    link.display()
                ));
            }
        }
    }
    user_path_remove(p, ops, say);
    let running = ops.healthy();
    if running {
        say(&format!(
            "AIB is running, so the program in {} stays; sign out and back in, then delete it",
            p.dir.display()
        ));
    } else {
        // aib.exe: ours only (this aibd.exe's or the older one's hash), looked at before they go
        if ops.exists(&p.aib) {
            let have = ops.hash(&p.aib).ok();
            let ours = have.is_some()
                && !ops.is_link(&p.aib)
                && [&p.exe, &p.prev]
                    .into_iter()
                    .any(|f| ops.hash(f).ok() == have);
            if ours {
                ops.remove(&p.aib);
            } else {
                say(&format!("{} is not AIB's; left as it is", p.aib.display()));
            }
        }
        ops.remove(&p.aib_new);
        for f in [&p.exe, &p.prev, &p.new] {
            ops.remove(f);
        }
        // 3e53b REVIEW-winsetup-w2 L1: Windows does not delete a running program (Settings runs
        // this very aibd.exe), so what is left is looked at again and said as it is
        let left: Vec<String> = [&p.exe, &p.prev, &p.new]
            .into_iter()
            .filter(|f| ops.exists(f))
            .map(|f| f.display().to_string())
            .collect();
        if left.is_empty() {
            ops.remove_dir_if_empty(&p.dir);
            say(&format!("removed the program from {}", p.dir.display()));
        } else {
            say(&format!(
                "could not remove {}; close this window, then delete the folder {}",
                left.join(", "),
                p.dir.display()
            ));
        }
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
            "installer_running: AIB is running from {}; {}",
            p.exe.display(),
            quit_how("run it again")
        ));
    }
    ops.remove(&p.new);
    let moved = ops.exists(&p.exe);
    if moved {
        ops.rename(&p.exe, &p.new)?;
    }
    // 3e53b N1: a failed swap puts the current program back instead of leaving no aibd.exe
    if let Err(e) = ops.rename(&p.prev, &p.exe) {
        // 3e53b Na (80d4c947): nothing was moved aside, so nothing is put back
        if !moved {
            return Err(format!(
                "installer_rollback_failed: {e}; nothing changed (there was no {} before)",
                p.exe.display()
            ));
        }
        let back = ops.rename(&p.new, &p.exe);
        return Err(match back {
            Ok(()) => format!("installer_rollback_failed: {e}; the current program is back in place"),
            Err(b) => format!(
                "installer_rollback_failed: {e}; the current program is now {} ({b}); rename it to {}",
                p.new.display(),
                p.exe.display()
            ),
        });
    }
    if ops.exists(&p.new) {
        ops.rename(&p.new, &p.prev).map_err(|e| {
            format!(
                "installer_rollback_partial: the earlier program is in place; the newer one is {} ({e})",
                p.new.display()
            )
        })?;
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
        hello_err: Option<String>,
        copy_sha: Option<String>,
        disk_sha: Option<String>,
        run: RefCell<BTreeMap<String, String>>,
        run_mangle: bool,
        settings: RefCell<BTreeMap<String, String>>,
        stuck: Vec<PathBuf>,
        bad_rename: Option<PathBuf>,
        healthy_after: Option<usize>,
        calls: RefCell<Vec<String>>,
        health_asks: RefCell<usize>,
        lnk: RefCell<BTreeMap<PathBuf, (PathBuf, String)>>,
        lnk_dirs_err: bool,
        ask_yes: bool,
        end_err: Option<String>,
        end_keeps_running: bool,
        ended: RefCell<bool>,
        started: RefCell<bool>,
        hashes: BTreeMap<PathBuf, String>,
        same_as: Option<PathBuf>,
        path_env: Option<OsString>,
        user_path: RefCell<Option<(String, PathKind)>>,
        path_changes: RefCell<usize>,
        notes: RefCell<BTreeMap<PathBuf, String>>,
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
        fn same_file(&self, _: &Path, b: &Path) -> bool {
            self.same || self.same_as.as_deref() == Some(b)
        }
        fn make_dir(&self, _: &Path) -> Result<(), String> {
            Ok(())
        }
        fn copy_new(&self, from: &Path, to: &Path) -> Result<String, String> {
            self.log(format!("copy {} {}", from.display(), to.display()));
            self.files.borrow_mut().push(to.to_path_buf());
            Ok(self.copy_sha.clone().unwrap_or_else(|| "aa".into()))
        }
        fn hash(&self, p: &Path) -> Result<String, String> {
            if let Some(h) = self.hashes.get(p) {
                return Ok(h.clone());
            }
            Ok(self
                .disk_sha
                .clone()
                .or_else(|| self.copy_sha.clone())
                .unwrap_or_else(|| "aa".into()))
        }
        fn remove(&self, p: &Path) {
            if !self.stuck.iter().any(|s| s == p) {
                self.files.borrow_mut().retain(|f| f != p);
                self.notes.borrow_mut().remove(p);
            }
        }
        fn rename(&self, from: &Path, to: &Path) -> Result<(), String> {
            self.log(format!("rename {} {}", from.display(), to.display()));
            if self.bad_rename.as_deref() == Some(from) {
                return Err("in use".into());
            }
            let mut f = self.files.borrow_mut();
            f.retain(|x| x != from && x != to);
            f.push(to.to_path_buf());
            Ok(())
        }
        fn hello_ready(&self) -> Result<(), String> {
            self.log("hello".into());
            self.hello_err.clone().map_or(Ok(()), Err)
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
            if *self.ended.borrow() && !self.end_keeps_running && !*self.started.borrow() {
                return false;
            }
            self.healthy_after.is_some_and(|k| *n > k)
        }
        fn ask_end_running(&self, line: &str) -> bool {
            self.log(format!("ask {}", line.contains("not an owner check")));
            self.ask_yes
        }
        fn end_running(&self, exe: &Path) -> Result<(), String> {
            self.log(format!("end {}", exe.display()));
            if let Some(e) = &self.end_err {
                return Err(e.clone());
            }
            *self.ended.borrow_mut() = true;
            Ok(())
        }
        fn start(&self, exe: &Path) -> Result<(), String> {
            self.log(format!("start {}", exe.display()));
            *self.started.borrow_mut() = true;
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
        fn shortcut_dirs(&self) -> Result<Vec<PathBuf>, String> {
            if self.lnk_dirs_err {
                return Err("no desktop".into());
            }
            Ok(vec![PathBuf::from("desk"), PathBuf::from("menu")])
        }
        fn shortcut_read(&self, link: &Path) -> Result<(PathBuf, String), String> {
            self.lnk
                .borrow()
                .get(link)
                .cloned()
                .ok_or_else(|| "unreadable".to_string())
        }
        fn shortcut_make(&self, link: &Path, exe: &Path, args: &str) -> Result<(), String> {
            self.log(format!("lnk {}", link.display()));
            self.lnk
                .borrow_mut()
                .insert(link.to_path_buf(), (exe.to_path_buf(), args.to_string()));
            let mut f = self.files.borrow_mut();
            if !f.iter().any(|x| x == link) {
                f.push(link.to_path_buf());
            }
            Ok(())
        }
        fn remove_dir_if_empty(&self, dir: &Path) {
            self.log(format!("rmdir {}", dir.display()));
        }
        fn path_env(&self) -> Option<OsString> {
            self.path_env.clone()
        }
        fn env_var(&self, n: &str) -> Option<String> {
            (n == "LOCALAPPDATA").then(|| here().base.to_string_lossy().into_owned())
        }
        fn user_path_read(&self) -> Result<Option<(String, PathKind)>, String> {
            Ok(self.user_path.borrow().clone())
        }
        fn user_path_write(
            &self,
            new: &str,
            kind: PathKind,
            before: Option<(&str, PathKind)>,
        ) -> Result<(), String> {
            let mut n = self.path_changes.borrow_mut();
            if *n > 0 {
                *n -= 1;
                return Err(aib_sys::winrun::PATH_CHANGED.into());
            }
            let mut u = self.user_path.borrow_mut();
            if u.as_ref().map(|(v, k)| (v.as_str(), *k)) != before {
                return Err(aib_sys::winrun::PATH_CHANGED.into());
            }
            self.log(format!("path {new}"));
            *u = Some((new.to_string(), kind));
            Ok(())
        }
        fn env_changed(&self) {
            self.log("env".into());
        }
        fn note_read(&self, p: &Path) -> Option<String> {
            self.notes.borrow().get(p).cloned()
        }
        fn note_write(&self, p: &Path, t: &str) -> Result<(), String> {
            self.notes.borrow_mut().insert(p.to_path_buf(), t.into());
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
                "hello".to_string(),
                format!("run AIB={CMD}"),
                format!("lnk {}", Path::new("desk").join(SHORTCUT).display()),
                format!("lnk {}", Path::new("menu").join(SHORTCUT).display()),
                format!("copy {} {}", p.exe.display(), p.aib_new.display()),
                format!("rename {} {}", p.aib_new.display(), p.aib.display()),
                format!("path {}", p.dir.display()),
                "env".to_string(),
                format!("start {}", p.exe.display()),
                "open".to_string(),
            ],
            "reason: order"
        );
        // one line per step, plus one per shortcut and two for aib
        assert_eq!(lines.len(), 9, "reason: one line per step {lines:?}");
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

    /// U2: AIB running from the installed copy, Enter pressed ⇒ the one copy is ended, then
    /// the program is swapped, the new one started; the question says it is not an owner check.
    #[test]
    fn a_running_aib_is_ended_after_enter_then_updated() {
        let p = here();
        let f = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone()]),
            ask_yes: true,
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        let calls = f.calls.borrow();
        let at = |pre: &str| calls.iter().position(|c| c.starts_with(pre));
        assert_eq!(at("ask "), Some(0), "reason: not_asked_first {calls:?}");
        assert_eq!(calls[0], "ask true", "reason: ask_words {calls:?}");
        assert!(
            at("end ").is_some() && at("end ") < at("copy ") && at("copy ").is_some(),
            "reason: order {calls:?}"
        );
    }

    /// U2: the AIB still answers after it was ended ⇒ nothing is swapped.
    #[test]
    fn an_aib_that_still_answers_after_ending_is_not_overwritten() {
        let p = here();
        let f = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone()]),
            ask_yes: true,
            end_keeps_running: true,
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_running")),
            "reason: {r:?}"
        );
        assert!(!f.has("copy"), "reason: swapped {:?}", f.calls.borrow());
    }

    /// U2: ending failed (none / many / not ended) ⇒ nothing is swapped, the way out is said.
    #[test]
    fn an_aib_that_could_not_be_ended_is_not_overwritten() {
        let p = here();
        let f = Fake {
            healthy_after: Some(0),
            files: RefCell::new(vec![p.exe.clone()]),
            ask_yes: true,
            end_err: Some("stop_many: 2 copies".into()),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_end_failed")
                    && e.contains("stop_many")
                    && e.contains("Task Manager")),
            "reason: {r:?}"
        );
        assert!(!f.has("copy"), "reason: swapped {:?}", f.calls.borrow());
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
        // U1: the line says how to end it (Task Manager), and not "sign out", which the Run
        // value undoes at sign-in
        let e = r.unwrap_err();
        assert!(
            e.contains("Task Manager")
                && e.contains("aibd.exe")
                && e.contains("double-click")
                && e.contains("your data stays"),
            "reason: no_way_out {e}"
        );
        assert!(!e.contains("sign"), "reason: sign_out_said {e}");
        assert!(
            !f.has("copy") && !f.has("rename") && !f.has("end "),
            "reason: touched {:?}",
            f.calls.borrow()
        );
    }

    #[test]
    fn started_from_the_installed_copy_it_only_fills_in_and_does_not_start_twice() {
        let f = Fake {
            same: true,
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
            !f.has("copy dl/") && !f.has("start"),
            "reason: redid {:?}",
            f.calls.borrow()
        );
        assert!(
            f.has("run AIB=") && f.has("open"),
            "reason: not_filled {:?}",
            f.calls.borrow()
        );
    }

    /// B2 / B3 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): no key is made;
    /// Windows Hello not set up ⇒ the setup still installs, starts and opens, and the line
    /// keeps the code and HRESULT and says to set a PIN, with no reinstall.
    #[test]
    fn without_hello_the_setup_still_installs_and_says_to_set_a_pin() {
        let why = "no_biometry (Windows Hello is not set up or not available on this machine)";
        let f = Fake {
            hello_err: Some(why.into()),
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: stopped {r:?}");
        assert!(
            f.has("run AIB=") && f.has("start") && f.has("open"),
            "reason: not_installed {:?}",
            f.calls.borrow()
        );
        let two = lines
            .iter()
            .find(|l| l.starts_with("2/5"))
            .expect("reason: no_step_2");
        assert!(
            two.contains(why)
                && two.contains("Windows Hello PIN")
                && two.contains("no need to install"),
            "reason: step_2_line {two}"
        );
        let f = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (_, lines) = go(&f);
        assert!(
            lines
                .iter()
                .any(|l| l.starts_with("2/5 Windows Hello is set up")),
            "reason: ready_line {lines:?}"
        );
    }

    /// 90856 (ebab8 10:01 / 10:17): the step-2 line asks for a PIN only on a machine without
    /// Windows Hello; the laptop's se_error and a session that is not the desktop are not.
    #[test]
    fn only_a_machine_without_hello_is_told_to_set_a_pin() {
        let desk = format!("unavailable (not available): {DESKTOP_ONLY}");
        for (why, want_pin, want) in [
            ("se_error (os error 0x80090027)", false, "did not answer"),
            ("no_biometry (no Windows Hello)", true, "no need to install"),
            ("unavailable (not set up)", true, "no need to install"),
            (desk.as_str(), false, "at this computer's desktop"),
            ("auth_failed (refused)", false, "did not answer"),
        ] {
            let f = Fake {
                hello_err: Some(why.into()),
                healthy_after: Some(1),
                ..Fake::default()
            };
            let (r, lines) = go(&f);
            assert!(r.is_ok(), "reason: stopped {why} {r:?}");
            let two = lines
                .iter()
                .find(|l| l.starts_with("2/5"))
                .cloned()
                .unwrap_or_default();
            assert_eq!(
                two.contains("PIN"),
                want_pin,
                "reason: pin_hint {why} => {two}"
            );
            assert!(
                two.contains(why) && two.contains(want) && two.contains("stays closed"),
                "reason: step_2_line {why} => {two}"
            );
        }
    }

    #[test]
    fn a_hello_error_line_keeps_the_hresult() {
        let e = aib_sys::hello::classify(0x8000_4005);
        let line = hello_error_line(&e);
        assert!(
            line.starts_with("se_error (") && line.contains("0x80004005"),
            "reason: hresult_lost {line}"
        );
        let c = hello_error_line(&aib_sys::hello::classify(0x8009_0036));
        assert!(c.starts_with("cancel ("), "reason: code_first {c}");
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

    /// ebab8 11-26 12:20: the setup leaves an "AIB" shortcut on the desktop and in the Start
    /// menu that runs the installed aibd.exe with `ctl web`.
    #[test]
    fn the_setup_puts_aib_on_the_desktop_and_in_the_start_menu() {
        let f = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        let exe = here().exe;
        for d in ["desk", "menu"] {
            assert_eq!(
                f.lnk.borrow().get(&Path::new(d).join(SHORTCUT)),
                Some(&(exe.clone(), SHORTCUT_ARGS.to_string())),
                "reason: shortcut_{d} {lines:?}"
            );
        }
        assert_eq!(SHORTCUT_ARGS, "ctl web", "reason: args");
    }

    /// Somebody else's AIB.lnk (another target) is not written over.
    #[test]
    fn somebody_elses_aib_shortcut_is_left_as_it_is() {
        let other = (PathBuf::from(r"C:\other\x.exe"), String::new());
        let desk = Path::new("desk").join(SHORTCUT);
        let f = Fake {
            healthy_after: Some(1),
            files: RefCell::new(vec![desk.clone()]),
            lnk: RefCell::new(BTreeMap::from([(desk.clone(), other.clone())])),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert_eq!(
            f.lnk.borrow().get(&desk),
            Some(&other),
            "reason: overwritten"
        );
        assert!(
            lines.iter().any(|l| l.contains("somebody else's")),
            "reason: not_said {lines:?}"
        );
        assert!(
            f.lnk
                .borrow()
                .contains_key(&Path::new("menu").join(SHORTCUT)),
            "reason: menu_skipped"
        );
    }

    /// An AIB.lnk that is itself a link is never written through, even when it reads as ours.
    #[test]
    fn a_shortcut_that_is_a_link_is_not_written_through() {
        let desk = Path::new("desk").join(SHORTCUT);
        let f = Fake {
            healthy_after: Some(1),
            links: vec![desk.clone()],
            files: RefCell::new(vec![desk.clone()]),
            lnk: RefCell::new(BTreeMap::from([(
                desk.clone(),
                (here().exe, SHORTCUT_ARGS.to_string()),
            )])),
            ..Fake::default()
        };
        let (r, _) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert!(
            !f.has(&format!("lnk {}", desk.display())),
            "reason: written_through {:?}",
            f.calls.borrow()
        );
    }

    /// No desktop / Start menu folder ⇒ the setup still finishes and opens the page.
    #[test]
    fn no_shortcut_does_not_stop_the_setup() {
        let f = Fake {
            healthy_after: Some(1),
            lnk_dirs_err: true,
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert!(f.has("open"), "reason: page_not_opened");
        assert!(
            lines.iter().any(|l| l.starts_with("3/5 no AIB shortcut")),
            "reason: not_said {lines:?}"
        );
    }

    /// Uninstall removes our shortcuts and leaves somebody else's.
    #[test]
    fn uninstall_removes_only_our_shortcuts() {
        let p = here();
        let desk = Path::new("desk").join(SHORTCUT);
        let menu = Path::new("menu").join(SHORTCUT);
        let f = Fake {
            files: RefCell::new(vec![desk.clone(), menu.clone()]),
            lnk: RefCell::new(BTreeMap::from([
                (desk.clone(), (p.exe.clone(), SHORTCUT_ARGS.to_string())),
                (
                    menu.clone(),
                    (PathBuf::from(r"C:\other\x.exe"), String::new()),
                ),
            ])),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &f, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(!f.exists(&desk), "reason: ours_kept {lines:?}");
        assert!(f.exists(&menu), "reason: theirs_removed {lines:?}");
    }

    /// The shortcut after AIB was ended: `ctl web` from the installed copy starts it again.
    /// What is started is the installed path, not the path this program was run from.
    #[test]
    fn web_from_the_installed_copy_starts_a_stopped_aib() {
        let p = here();
        let f = Fake {
            same: true,
            healthy_after: Some(1),
            files: RefCell::new(vec![p.exe.clone()]),
            ..Fake::default()
        };
        let ran_as = Path::new("other/aibd.exe");
        assert_eq!(wake(&p, ran_as, true, &f), Ok(true), "reason: not_started");
        assert!(!f.has("start other"), "reason: started_current");
        assert!(
            f.has(&format!("start {}", p.exe.display())),
            "reason: {:?}",
            f.calls.borrow()
        );
    }

    /// Another program, or a line naming another aibd (a test, a preview), starts nothing.
    #[test]
    fn web_from_elsewhere_starts_nothing() {
        let p = here();
        for (same, resident) in [(false, true), (true, false)] {
            // the installed copy is there and plain: only "from elsewhere" stops it
            let f = Fake {
                same,
                healthy_after: Some(1),
                files: RefCell::new(vec![p.exe.clone()]),
                ..Fake::default()
            };
            let r = wake(&p, Path::new("dl/aibd.exe"), resident, &f);
            assert!(
                r.as_ref().is_err_and(|e| e.contains("not running")),
                "reason: {r:?}"
            );
            assert!(!f.has("start"), "reason: started {same} {resident}");
        }
    }

    /// The installed aibd.exe is a link (or missing) ⇒ `ctl web` starts nothing.
    #[test]
    fn web_does_not_start_an_installed_path_that_is_a_link() {
        let p = here();
        let f = Fake {
            same: true,
            healthy_after: Some(1),
            links: vec![p.exe.clone()],
            files: RefCell::new(vec![p.exe.clone()]),
            ..Fake::default()
        };
        let r = wake(&p, &p.exe, true, &f);
        assert!(
            r.as_ref().is_err_and(|e| e.contains("plain file")),
            "reason: {r:?}"
        );
        assert!(!f.has("start"), "reason: started_link");
    }

    /// A shortcut that does not read back as written is not reported as made.
    #[test]
    fn a_shortcut_is_checked_by_reading_it_back() {
        let f = BadLnk(Fake {
            healthy_after: Some(1),
            ..Fake::default()
        });
        let mut lines = Vec::new();
        let r = run_with(&here(), Path::new("dl/AIB-Setup.exe"), &f, CMD, &mut |l| {
            lines.push(l.to_string());
        });
        assert!(r.is_ok(), "reason: {r:?}");
        assert!(
            lines.iter().any(|l| l.contains("does not read back")),
            "reason: not_checked {lines:?}"
        );
        assert!(
            !lines.iter().any(|l| l.starts_with("3/5 AIB shortcut:")),
            "reason: said_made {lines:?}"
        );
    }

    /// [`Fake`] whose shortcuts come back pointing elsewhere.
    struct BadLnk(Fake);
    impl Ops for BadLnk {
        fn elevated(&self) -> Result<bool, String> {
            self.0.elevated()
        }
        fn exists(&self, p: &Path) -> bool {
            self.0.exists(p)
        }
        fn is_link(&self, p: &Path) -> bool {
            self.0.is_link(p)
        }
        fn same_file(&self, a: &Path, b: &Path) -> bool {
            self.0.same_file(a, b)
        }
        fn make_dir(&self, d: &Path) -> Result<(), String> {
            self.0.make_dir(d)
        }
        fn copy_new(&self, a: &Path, b: &Path) -> Result<String, String> {
            self.0.copy_new(a, b)
        }
        fn hash(&self, p: &Path) -> Result<String, String> {
            self.0.hash(p)
        }
        fn remove(&self, p: &Path) {
            self.0.remove(p)
        }
        fn rename(&self, a: &Path, b: &Path) -> Result<(), String> {
            self.0.rename(a, b)
        }
        fn hello_ready(&self) -> Result<(), String> {
            self.0.hello_ready()
        }
        fn run_get(&self, n: &str) -> Result<Option<String>, String> {
            self.0.run_get(n)
        }
        fn run_set(&self, n: &str, c: &str) -> Result<(), String> {
            self.0.run_set(n, c)
        }
        fn run_delete_if(&self, n: &str, e: &str) -> Result<bool, String> {
            self.0.run_delete_if(n, e)
        }
        fn healthy(&self) -> bool {
            self.0.healthy()
        }
        fn ask_end_running(&self, l: &str) -> bool {
            self.0.ask_end_running(l)
        }
        fn end_running(&self, e: &Path) -> Result<(), String> {
            self.0.end_running(e)
        }
        fn start(&self, e: &Path) -> Result<(), String> {
            self.0.start(e)
        }
        fn pace(&self, d: Duration) {
            self.0.pace(d)
        }
        fn open_page(&self) -> Result<(), String> {
            self.0.open_page()
        }
        fn settings_get(&self, n: &str) -> Result<Option<String>, String> {
            self.0.settings_get(n)
        }
        fn settings_set(&self, n: &str, v: &str) -> Result<(), String> {
            self.0.settings_set(n, v)
        }
        fn settings_delete_if(&self, n: &str, e: &str) -> Result<bool, String> {
            self.0.settings_delete_if(n, e)
        }
        fn remove_dir_if_empty(&self, d: &Path) {
            self.0.remove_dir_if_empty(d)
        }
        fn shortcut_dirs(&self) -> Result<Vec<PathBuf>, String> {
            self.0.shortcut_dirs()
        }
        fn shortcut_read(&self, _: &Path) -> Result<(PathBuf, String), String> {
            Ok((
                PathBuf::from(r"C:\elsewhere\x.exe"),
                SHORTCUT_ARGS.to_string(),
            ))
        }
        fn shortcut_make(&self, l: &Path, e: &Path, a: &str) -> Result<(), String> {
            self.0.shortcut_make(l, e, a)
        }
        fn path_env(&self) -> Option<OsString> {
            self.0.path_env()
        }
        fn env_var(&self, n: &str) -> Option<String> {
            self.0.env_var(n)
        }
        fn user_path_read(&self) -> Result<Option<(String, PathKind)>, String> {
            self.0.user_path_read()
        }
        fn user_path_write(
            &self,
            n: &str,
            k: PathKind,
            b: Option<(&str, PathKind)>,
        ) -> Result<(), String> {
            self.0.user_path_write(n, k, b)
        }
        fn env_changed(&self) {
            self.0.env_changed()
        }
        fn note_read(&self, p: &Path) -> Option<String> {
            self.0.note_read(p)
        }
        fn note_write(&self, p: &Path, t: &str) -> Result<(), String> {
            self.0.note_write(p, t)
        }
    }

    /// 3e53b r1 wake #2 (static): the real start runs the given program directly, no shell.
    #[test]
    fn the_real_start_runs_the_program_directly_no_shell() {
        let src = include_str!("ctl.rs");
        let at = src
            .find("    fn start(&self, exe: &Path) -> Result<(), String> {")
            .expect("SetupOps::start");
        let body = &src[at..];
        let body = &body[..body.find("    fn pace(").unwrap_or(body.len())];
        assert!(
            body.contains("std::process::Command::new(exe)"),
            "reason: not_direct {body}"
        );
        for bad in ["\"cmd", "ShellExecute", "powershell", "explorer", "/c"] {
            assert!(!body.contains(bad), "reason: shell {bad}");
        }
    }

    /// The shortcut's window: two lines in each of the four languages, the code always shown.
    #[test]
    fn the_shortcut_window_says_two_plain_lines_with_the_code() {
        for lang in ["en", "ja", "ko", "zh-TW"] {
            let l = shortcut_wait_lines(lang, "K7Q2", 90);
            assert!(
                l[1].contains("K7Q2") && l[1].contains("90"),
                "reason: code_{lang}"
            );
            for w in ["http", "box", "tick"] {
                assert!(!l.concat().contains(w), "reason: technical_{lang}_{w}");
            }
            assert!(
                shortcut_close_line(lang).contains("Enter"),
                "reason: close_{lang}"
            );
        }
        assert_ne!(
            shortcut_wait_lines("zh-TW", "K", 1),
            shortcut_wait_lines("en", "K", 1),
            "reason: one_language"
        );
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
    fn uninstall_does_not_say_removed_when_the_running_program_stays() {
        let p = here();
        let f = Fake {
            files: RefCell::new(vec![p.exe.clone(), p.prev.clone()]),
            stuck: vec![p.exe.clone()],
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &f, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(
            !lines.iter().any(|l| l.starts_with("removed the program")),
            "reason: said_removed {lines:?}"
        );
        let exe = p.exe.display().to_string();
        assert!(
            lines
                .iter()
                .any(|l| l.starts_with("could not remove") && l.contains(&exe)),
            "reason: no_path {lines:?}"
        );
        assert!(!f.has("rmdir"), "reason: rmdir");
    }

    #[test]
    fn a_rollback_that_fails_half_way_puts_the_current_program_back() {
        let p = here();
        let f = Fake {
            files: RefCell::new(vec![p.exe.clone(), p.prev.clone()]),
            bad_rename: Some(p.prev.clone()),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        let r = rollback(&p, &f, &mut |l| lines.push(l.to_string()));
        assert!(
            r.as_ref()
                .is_err_and(|e| e.starts_with("installer_rollback_failed")),
            "reason: {r:?}"
        );
        assert!(f.exists(&p.exe), "reason: no_exe {:?}", f.calls.borrow());
    }

    #[test]
    fn a_rollback_with_no_program_before_does_not_point_at_a_missing_copy() {
        let p = here();
        let f = Fake {
            files: RefCell::new(vec![p.prev.clone()]),
            bad_rename: Some(p.prev.clone()),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        let r = rollback(&p, &f, &mut |l| lines.push(l.to_string()));
        let new = p.new.display().to_string();
        assert!(
            r.as_ref()
                .is_err_and(|e| e.contains("nothing changed") && !e.contains(&new)),
            "reason: {r:?}"
        );
        assert!(f.exists(&p.prev), "reason: prev_lost");
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

    fn path_of(dirs: &[&str]) -> Option<OsString> {
        Some(std::env::join_paths(dirs).unwrap())
    }

    /// PRE-READ-aib-cmd-windows §2: our folder at the END of the user's PATH, the type kept,
    /// noted for uninstall, open programs told; there already (any case) ⇒ nothing written.
    #[test]
    fn the_setup_puts_aib_beside_aibd_and_its_folder_at_the_end_of_the_path() {
        let p = here();
        let dir = p.dir.to_string_lossy().into_owned();
        let f = Fake {
            healthy_after: Some(1),
            user_path: RefCell::new(Some((r"%USERPROFILE%\bin;;C:\x".into(), PathKind::Sz))),
            ..Fake::default()
        };
        let (r, lines) = go(&f);
        assert!(r.is_ok(), "reason: {r:?}");
        assert!(f.exists(&p.aib), "reason: no_aib {:?}", f.calls.borrow());
        assert_eq!(
            *f.user_path.borrow(),
            Some((format!(r"%USERPROFILE%\bin;;C:\x;{dir}"), PathKind::Sz)),
            "reason: path_written {lines:?}"
        );
        assert_eq!(
            f.notes.borrow().get(&p.path_note),
            Some(&dir),
            "reason: not_noted"
        );
        assert!(f.has("env"), "reason: not_told");
        assert!(lines.iter().any(|l| l.starts_with("AIB-CMD ")), "{lines:?}");
        assert!(
            lines.iter().any(|l| l.starts_with("AIB-CMD-PATH")),
            "{lines:?}"
        );
        // no PATH value at all: made, as REG_EXPAND_SZ
        let none = Fake {
            healthy_after: Some(1),
            ..Fake::default()
        };
        assert!(go(&none).0.is_ok());
        assert_eq!(
            *none.user_path.borrow(),
            Some((dir.clone(), PathKind::ExpandSz)),
            "reason: made_as_sz"
        );
        // there already, other case / written with %LOCALAPPDATA%: not written, not noted
        for there in [
            dir.to_uppercase(),
            format!("%LOCALAPPDATA%{}{DIR_NAME}", std::path::MAIN_SEPARATOR),
        ] {
            let f = Fake {
                healthy_after: Some(1),
                user_path: RefCell::new(Some((format!("C:\\y;{there}"), PathKind::ExpandSz))),
                ..Fake::default()
            };
            assert!(go(&f).0.is_ok());
            assert!(
                !f.has("path "),
                "reason: added_twice {there} {:?}",
                f.calls.borrow()
            );
            assert!(
                f.notes.borrow().is_empty(),
                "reason: noted_not_ours {there}"
            );
        }
        // changed between our read and our write: read again, then written once
        let raced = Fake {
            healthy_after: Some(1),
            path_changes: RefCell::new(1),
            ..Fake::default()
        };
        assert!(go(&raced).0.is_ok());
        assert!(raced.has("path "), "reason: gave_up_after_one");
        let always = Fake {
            healthy_after: Some(1),
            path_changes: RefCell::new(5),
            ..Fake::default()
        };
        let (r, lines) = go(&always);
        assert!(r.is_ok(), "reason: path_stopped_the_setup");
        assert!(always.notes.borrow().is_empty(), "reason: note_left");
        assert!(lines
            .iter()
            .any(|l| l.starts_with("AIB-CMD-PATH") && l.contains("not on your PATH")));
    }

    /// f163c / 3e53b r1: another aib anywhere on PATH (AIB 1.x) ⇒ no aib.exe, PATH untouched;
    /// an aib.exe in our folder that is not ours ⇒ left.
    #[test]
    fn another_aib_on_the_path_or_in_our_folder_is_left_and_none_placed() {
        let p = here();
        for (before, after) in [("/one", "/two"), ("/two", "/one")] {
            let one = Path::new("/one").join("aib.cmd");
            let f = Fake {
                healthy_after: Some(1),
                path_env: path_of(&[before, after]),
                files: RefCell::new(vec![one.clone()]),
                ..Fake::default()
            };
            let (r, lines) = go(&f);
            assert!(r.is_ok(), "reason: {r:?}");
            assert!(!f.exists(&p.aib), "reason: placed_beside_1x");
            assert!(!f.has("path "), "reason: path_written_beside_1x");
            assert!(
                lines.iter().any(
                    |l| l.starts_with("AIB-CMD-LEFT") && l.contains(&one.display().to_string())
                ),
                "{lines:?}"
            );
        }
        let theirs = Fake {
            healthy_after: Some(1),
            files: RefCell::new(vec![p.aib.clone()]),
            hashes: BTreeMap::from([(p.aib.clone(), "zz".to_string())]),
            ..Fake::default()
        };
        let (r, lines) = go(&theirs);
        assert!(r.is_ok());
        assert!(
            !theirs.has(&format!("rename {}", p.aib_new.display())),
            "reason: theirs_replaced"
        );
        assert!(
            lines.iter().any(|l| l.starts_with("AIB-CMD-NOT-WRITTEN")),
            "{lines:?}"
        );
        // the older aibd.exe's copy (kept as .prev) is ours: replaced
        let older = Fake {
            healthy_after: Some(1),
            files: RefCell::new(vec![p.aib.clone(), p.exe.clone()]),
            hashes: BTreeMap::from([
                (p.aib.clone(), "old".to_string()),
                (p.prev.clone(), "old".to_string()),
            ]),
            ..Fake::default()
        };
        assert!(go(&older).0.is_ok());
        assert!(
            older.has(&format!(
                "rename {} {}",
                p.aib_new.display(),
                p.aib.display()
            )),
            "reason: older_left {:?}",
            older.calls.borrow()
        );
    }

    /// Uninstall takes out only the entry the setup noted, only when it is there once; aib.exe
    /// only when it is ours.
    #[test]
    fn uninstall_takes_out_only_our_path_entry_and_our_aib() {
        let p = here();
        let dir = p.dir.to_string_lossy().into_owned();
        let f = Fake {
            files: RefCell::new(vec![p.exe.clone(), p.aib.clone(), p.path_note.clone()]),
            user_path: RefCell::new(Some((format!(r"C:\x;;{dir}"), PathKind::ExpandSz))),
            notes: RefCell::new(BTreeMap::from([(p.path_note.clone(), dir.clone())])),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &f, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert_eq!(
            *f.user_path.borrow(),
            Some((r"C:\x;".to_string(), PathKind::ExpandSz)),
            "reason: others_changed {lines:?}"
        );
        assert!(!f.exists(&p.aib), "reason: aib_left");
        assert!(f.notes.borrow().is_empty(), "reason: note_left");
        // twice on PATH (changed since): left, said
        let twice = Fake {
            user_path: RefCell::new(Some((format!("{dir};C:\\x;{dir}"), PathKind::Sz))),
            notes: RefCell::new(BTreeMap::from([(p.path_note.clone(), dir.clone())])),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &twice, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(!twice.has("path "), "reason: removed_one_of_two");
        assert!(lines.iter().any(|l| l.contains("2 times")), "{lines:?}");
        // a note naming another folder (the note is the user's file): PATH left
        let other = Fake {
            user_path: RefCell::new(Some((r"C:\x;C:\Windows".into(), PathKind::Sz))),
            notes: RefCell::new(BTreeMap::from([(
                p.path_note.clone(),
                r"C:\Windows".into(),
            )])),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &other, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(!other.has("path "), "reason: took_another_folder {lines:?}");
        // not noted (the customer had it before AIB): left
        let theirs = Fake {
            user_path: RefCell::new(Some((dir.clone(), PathKind::Sz))),
            files: RefCell::new(vec![p.aib.clone()]),
            hashes: BTreeMap::from([(p.aib.clone(), "zz".to_string())]),
            ..Fake::default()
        };
        let mut lines = Vec::new();
        assert!(uninstall_with(&p, &theirs, CMD, &mut |l| lines.push(l.to_string())).is_ok());
        assert!(!theirs.has("path "), "reason: took_theirs");
        assert!(theirs.exists(&p.aib), "reason: removed_their_aib");
    }

    /// `aib web` from our aib.exe starts the installed aibd.exe (never aib.exe); an aib.exe
    /// that is not this aibd.exe's copy starts nothing.
    #[test]
    fn web_from_our_aib_starts_the_installed_aibd() {
        let p = here();
        let ours = Fake {
            same_as: Some(p.aib.clone()),
            healthy_after: Some(1),
            files: RefCell::new(vec![p.exe.clone(), p.aib.clone()]),
            ..Fake::default()
        };
        assert_eq!(
            wake(&p, &p.aib, true, &ours),
            Ok(true),
            "reason: not_started"
        );
        assert!(
            ours.has(&format!("start {}", p.exe.display())),
            "{:?}",
            ours.calls.borrow()
        );
        assert!(
            !ours.has(&format!("start {}", p.aib.display())),
            "reason: started_aib"
        );
        let other = Fake {
            same_as: Some(p.aib.clone()),
            healthy_after: Some(1),
            files: RefCell::new(vec![p.exe.clone(), p.aib.clone()]),
            hashes: BTreeMap::from([(p.aib.clone(), "zz".to_string())]),
            ..Fake::default()
        };
        assert!(
            wake(&p, &p.aib, true, &other).is_err(),
            "reason: started_from_another"
        );
        assert!(!other.has("start"), "reason: started_from_another");
    }
}
