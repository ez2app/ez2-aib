//! `AIB` in the user's Applications folder (ebab8 11-27, after Andy's clean install opened the
//! page and stopped at "no key"): clicking it opens the workbench with a pairing code, the same
//! as the Start-menu AIB on Windows ([`crate::winsetup`] `SHORTCUT_ARGS` = `ctl web`).
//!
//! `aibd install` (the default label only: the app opens the default aibd) writes
//! `~/Applications/AIB.app` with two things in it: `Contents/Info.plist` and
//! `Contents/MacOS/AIB`, a symbolic link to the installed aibd, so an installed update is the
//! app's update too. Started as `…/AIB.app/Contents/MacOS/AIB` with nothing after it, aibd runs
//! `ctl web` ([`is_app_launch`]). An `AIB.app` that is not ours (another bundle id, or a link
//! itself) is left as it is; `aibd uninstall` removes only ours.
//!
//! Same-account threat model: the app is convenience, not a defence. Anything it starts, the
//! same account can start by typing `aibd ctl web`; the pairing's Touch ID is the defence.
//! No program is run here and no shell is written: the files are written by this file.

use std::ffi::OsStr;
use std::path::{Component, Path, PathBuf};

/// The app's folder name in Applications.
pub const APP: &str = "AIB.app";
/// The app's executable name (`Contents/MacOS/AIB`).
pub const EXEC: &str = "AIB";
/// The bundle id that marks the app as ours.
pub const BUNDLE_ID: &str = "app.ez2.aib.open";

/// Started as the app: the program's own path (`argv[0]`, which LaunchServices sets to the
/// bundle's executable; `current_exe` would follow the link to aibd) ends in
/// `AIB.app/Contents/MacOS/AIB`, and nothing follows it but an old-style `-psn_…`.
pub fn is_app_launch(argv0: &OsStr, rest: &[&str]) -> bool {
    let tail: Vec<Component> = Path::new(argv0).components().rev().take(4).collect();
    let named = matches!(
        tail.as_slice(),
        [Component::Normal(e), Component::Normal(m), Component::Normal(c), Component::Normal(a)]
            if *e == OsStr::new(EXEC)
                && *m == OsStr::new("MacOS")
                && *c == OsStr::new("Contents")
                && *a == OsStr::new(APP)
    );
    named && (rest.is_empty() || (rest.len() == 1 && rest[0].starts_with("-psn_")))
}

/// The app's `Info.plist`: our bundle id, no Dock icon while it waits for the page.
pub fn info_plist(version: &str) -> String {
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
<plist version=\"1.0\">\n<dict>\n\
\t<key>CFBundleExecutable</key>\n\t<string>{EXEC}</string>\n\
\t<key>CFBundleIdentifier</key>\n\t<string>{BUNDLE_ID}</string>\n\
\t<key>CFBundleName</key>\n\t<string>AIB</string>\n\
\t<key>CFBundlePackageType</key>\n\t<string>APPL</string>\n\
\t<key>CFBundleShortVersionString</key>\n\t<string>{}</string>\n\
\t<key>LSUIElement</key>\n\t<true/>\n\
</dict>\n</plist>\n",
        crate::install::xml_escape(version)
    )
}

fn is_link(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink())
}

fn exists(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok()
}

/// Is the `AIB.app` at `app` ours: a real folder (not a link), its Info.plist a plain file
/// naming [`BUNDLE_ID`], its executable a link or nothing.
pub fn ours(app: &Path) -> bool {
    if is_link(app) || !app.is_dir() {
        return false;
    }
    let plist = app.join("Contents").join("Info.plist");
    if is_link(&plist) {
        return false;
    }
    let Ok(text) = std::fs::read_to_string(&plist) else {
        return false;
    };
    let id = crate::install::parse_plist(&text).ok().and_then(|v| {
        v.get("CFBundleIdentifier")
            .and_then(|s| s.as_str())
            .map(str::to_owned)
    });
    let exec = app.join("Contents").join("MacOS").join(EXEC);
    id.as_deref() == Some(BUNDLE_ID) && (!exists(&exec) || is_link(&exec))
}

/// What [`place`] did.
#[derive(Debug, PartialEq, Eq)]
pub enum Placed {
    Written(PathBuf),
    /// an `AIB.app` that is not ours is there: left as it is
    NotOurs(PathBuf),
}

/// Write (or rewrite) ours under `apps`, its executable a link to `exe`; read back.
pub fn place(apps: &Path, exe: &Path, version: &str) -> Result<Placed, String> {
    let app = apps.join(APP);
    if exists(&app) && !ours(&app) {
        return Ok(Placed::NotOurs(app));
    }
    if is_link(apps) {
        return Err(format!("{} is a link", apps.display()));
    }
    let macos = app.join("Contents").join("MacOS");
    std::fs::create_dir_all(&macos).map_err(|e| format!("{}: {e}", macos.display()))?;
    let plist = app.join("Contents").join("Info.plist");
    let tmp = app.join("Contents").join(".Info.plist.tmp");
    std::fs::write(&tmp, info_plist(version)).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &plist).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", plist.display())
    })?;
    let exec = macos.join(EXEC);
    if is_link(&exec) {
        std::fs::remove_file(&exec).map_err(|e| format!("{}: {e}", exec.display()))?;
    }
    link(exe, &exec)?;
    let back = std::fs::read_link(&exec).ok();
    if !ours(&app) || back.as_deref() != Some(exe) {
        return Err(format!("{} does not read back as written", app.display()));
    }
    Ok(Placed::Written(app))
}

#[cfg(unix)]
fn link(exe: &Path, at: &Path) -> Result<(), String> {
    std::os::unix::fs::symlink(exe, at).map_err(|e| format!("{}: {e}", at.display()))
}

#[cfg(not(unix))]
fn link(_: &Path, at: &Path) -> Result<(), String> {
    Err(format!("{}: no app on this system", at.display()))
}

/// The line `aibd install` prints for the app; never fails the install.
pub fn place_line(apps: &Path, exe: &Path, version: &str) -> String {
    match place(apps, exe, version) {
        Ok(Placed::Written(app)) => {
            format!("APP {} (click AIB to open the workbench)", app.display())
        }
        Ok(Placed::NotOurs(app)) => {
            format!("APP-LEFT {} is not ours; left as it is", app.display())
        }
        Err(e) => format!("APP-NOT-WRITTEN {e}"),
    }
}

/// Where a click that failed is written: `~/Library/Logs/AIB-app.log`.
pub const LOG: &str = "AIB-app.log";

/// One line for a click that failed (the reason only, never a code), appended to a plain file
/// or where nothing is (a link is not followed); a write that fails is skipped.
pub fn log_failure(why: &str) {
    let Some(home) = std::env::home_dir() else {
        return;
    };
    let p = home.join("Library").join("Logs").join(LOG);
    if is_link(&p) {
        return;
    }
    use std::io::Write as _;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&p)
    {
        let _ = writeln!(f, "aibd ctl web (AIB app): {why}");
    }
}

/// `aibd uninstall`: remove ours (its two files and its folders when empty); anything else
/// under `apps` is left. `None` = nothing of ours was there.
pub fn remove(apps: &Path) -> Option<String> {
    let app = apps.join(APP);
    if !exists(&app) {
        return None;
    }
    if !ours(&app) {
        return Some(format!(
            "APP-LEFT {} is not ours; left as it is",
            app.display()
        ));
    }
    let contents = app.join("Contents");
    let macos = contents.join("MacOS");
    let _ = std::fs::remove_file(macos.join(EXEC));
    let _ = std::fs::remove_file(contents.join("Info.plist"));
    // remove_dir refuses a folder with anything else in it
    let _ = std::fs::remove_dir(&macos);
    let _ = std::fs::remove_dir(&contents);
    let _ = std::fs::remove_dir(&app);
    Some(if exists(&app) {
        format!("APP-KEPT {} (other files in it)", app.display())
    } else {
        format!("APP-REMOVED {}", app.display())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_apps_own_executable_with_nothing_after_it_is_the_app() {
        let app = OsStr::new("/Users/x/Applications/AIB.app/Contents/MacOS/AIB");
        assert!(is_app_launch(app, &[]));
        assert!(is_app_launch(app, &["-psn_0_12345"]));
        assert!(
            !is_app_launch(app, &["ctl", "web"]),
            "reason: args_taken_as_app"
        );
        assert!(!is_app_launch(app, &["-psn_0_1", "x"]));
        for other in [
            "/Users/x/.local/bin/aibd",
            "/Users/x/Applications/AIB.app/Contents/MacOS/aibd",
            "/Users/x/Applications/Other.app/Contents/MacOS/AIB",
            "/Users/x/Applications/AIB.app/MacOS/AIB",
            "AIB",
        ] {
            assert!(!is_app_launch(OsStr::new(other), &[]), "reason: {other}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn install_writes_ours_rewrites_ours_and_leaves_anything_else() {
        let d = tempfile::tempdir().unwrap();
        let apps = d.path().join("Applications");
        let exe = d.path().join("bin").join("aibd");
        std::fs::create_dir_all(exe.parent().unwrap()).unwrap();
        std::fs::write(&exe, b"build").unwrap();
        assert_eq!(
            place(&apps, &exe, "aibd 1 <x>"),
            Ok(Placed::Written(apps.join(APP)))
        );
        let exec = apps.join("AIB.app/Contents/MacOS/AIB");
        assert_eq!(std::fs::read_link(&exec).unwrap(), exe);
        let plist = std::fs::read_to_string(apps.join("AIB.app/Contents/Info.plist")).unwrap();
        assert!(
            plist.contains(BUNDLE_ID) && plist.contains("aibd 1 &lt;x&gt;"),
            "{plist}"
        );
        // again, another place: ours is rewritten, the link follows
        let exe2 = d.path().join("bin2").join("aibd");
        assert!(matches!(place(&apps, &exe2, "2"), Ok(Placed::Written(_))));
        assert_eq!(std::fs::read_link(&exec).unwrap(), exe2);
        // somebody else's AIB.app: left byte for byte
        let theirs = d.path().join("theirs");
        let app = theirs.join(APP);
        std::fs::create_dir_all(app.join("Contents/MacOS")).unwrap();
        let other = info_plist("1").replace(BUNDLE_ID, "com.example.aib");
        std::fs::write(app.join("Contents/Info.plist"), &other).unwrap();
        std::fs::write(app.join("Contents/MacOS/AIB"), b"their program").unwrap();
        assert_eq!(place(&theirs, &exe, "1"), Ok(Placed::NotOurs(app.clone())));
        assert_eq!(
            std::fs::read(app.join("Contents/MacOS/AIB")).unwrap(),
            b"their program"
        );
        assert!(remove(&theirs).unwrap().starts_with("APP-LEFT"));
        assert!(
            app.join("Contents/Info.plist").exists(),
            "reason: theirs_removed"
        );
        // our bundle id but a real program as the executable: not ours either
        let mixed = d.path().join("mixed");
        let app = mixed.join(APP);
        std::fs::create_dir_all(app.join("Contents/MacOS")).unwrap();
        std::fs::write(app.join("Contents/Info.plist"), info_plist("1")).unwrap();
        std::fs::write(app.join("Contents/MacOS/AIB"), b"a program").unwrap();
        assert_eq!(place(&mixed, &exe, "1"), Ok(Placed::NotOurs(app)));
        // a link named AIB.app: not followed
        let linked = d.path().join("linked");
        std::fs::create_dir_all(&linked).unwrap();
        std::os::unix::fs::symlink(apps.join(APP), linked.join(APP)).unwrap();
        assert!(matches!(place(&linked, &exe, "1"), Ok(Placed::NotOurs(_))));
        // uninstall: ours goes, the installed aibd stays
        assert!(remove(&apps).unwrap().starts_with("APP-REMOVED"));
        assert!(!exists(&apps.join(APP)), "reason: ours_kept");
        assert!(exe.exists());
        assert_eq!(remove(&apps), None);
    }
}
