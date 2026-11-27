//! `aib web` on a new install (Andy 11-27 10:1x: 「新安裝的 aib 也應該要能用 aib web 一鍵啟用」;
//! ebab8 dispatch): `aibd install` puts an `aib` beside the installed aibd — a symbolic link to
//! it — and aibd started under that name answers `aib web` as `aibd ctl web`: the page opens in
//! the default browser with the pairing code in its address.
//!
//! - Only `web` (with `ctl web`'s own flags) is taken; anything else says what this `aib` is.
//! - An `aib` already there that is not ours (AIB 1.x's, another program, a file) is never
//!   replaced: one plain line says so and what to type instead.
//! - PATH is read, never written, on macOS: a folder not on it, or another `aib` before ours,
//!   is one plain line. The same account can change PATH or put its own `aib` first; this is
//!   convenience, not a defence (the pairing's Touch ID is).
//! - Windows (aib.exe beside aibd.exe + the user's PATH) is the setup's half: not in this file.

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};

/// The command's file name.
pub const NAME: &str = "aib";

/// Started as `aib` (by `argv[0]`'s file name: `current_exe` would follow the link to aibd).
/// Lower case exactly off Windows: the AIB app's own executable is `AIB` ([`crate::macapp`]).
pub fn is_aib(argv0: &OsStr) -> bool {
    Path::new(argv0)
        .file_stem()
        .is_some_and(|s| s == OsStr::new(NAME) || (cfg!(windows) && s.eq_ignore_ascii_case(NAME)))
}

/// What `aib <args>` runs: `web` and its flags become `ctl web …`; anything else is a line.
pub fn ctl_args<'a>(args: &[&'a str]) -> Result<Vec<&'a str>, String> {
    match args {
        ["web", rest @ ..] => {
            let mut v = vec!["web"];
            v.extend_from_slice(rest);
            Ok(v)
        }
        _ => Err(
            "this aib is AIB 2.0's: aib web opens the workbench with a pairing code \
                  (everything else: aibd ctl …)"
                .to_string(),
        ),
    }
}

fn is_link(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_symlink())
}

/// Ours: a link whose target's file name is aibd's (an install in another folder is ours too).
fn ours(link: &Path) -> bool {
    is_link(link)
        && std::fs::read_link(link).is_ok_and(|t| {
            t.file_name()
                .is_some_and(|n| n == OsStr::new(crate::install::EXE_NAME))
        })
}

/// What [`place`] found or did.
#[derive(Debug, PartialEq, Eq)]
pub enum Placed {
    Written(PathBuf),
    /// an `aib` that is not ours is there: left as it is
    NotOurs(PathBuf),
}

/// `dir/aib` → `exe`, written when nothing is there or it is ours; read back.
pub fn place(dir: &Path, exe: &Path) -> Result<Placed, String> {
    let at = dir.join(NAME);
    if std::fs::symlink_metadata(&at).is_ok() {
        if !ours(&at) {
            return Ok(Placed::NotOurs(at));
        }
        if std::fs::read_link(&at).ok().as_deref() == Some(exe) {
            return Ok(Placed::Written(at));
        }
        std::fs::remove_file(&at).map_err(|e| format!("{}: {e}", at.display()))?;
    }
    link(exe, &at)?;
    if std::fs::read_link(&at).ok().as_deref() != Some(exe) {
        return Err(format!("{} does not read back as written", at.display()));
    }
    Ok(Placed::Written(at))
}

#[cfg(unix)]
fn link(exe: &Path, at: &Path) -> Result<(), String> {
    std::os::unix::fs::symlink(exe, at).map_err(|e| format!("{}: {e}", at.display()))
}

#[cfg(not(unix))]
fn link(_: &Path, at: &Path) -> Result<(), String> {
    Err(format!("{}: the setup puts aib.exe here", at.display()))
}

/// The first `aib` on `path` (the folders in order, links not followed).
fn first_on_path(path: &OsStr) -> Option<PathBuf> {
    std::env::split_paths(path)
        .map(|d| d.join(NAME))
        .find(|p| std::fs::symlink_metadata(p).is_ok())
}

/// The lines `aibd install` prints for `aib`: written / left, then what PATH means for it.
/// Never fails the install.
pub fn place_lines(dir: &Path, exe: &Path, path: Option<&OsString>) -> Vec<String> {
    let mut out = Vec::new();
    let at = match place(dir, exe) {
        Ok(Placed::Written(at)) => {
            out.push(format!(
                "AIB-CMD {} (aib web opens the workbench)",
                at.display()
            ));
            at
        }
        Ok(Placed::NotOurs(at)) => {
            out.push(format!(
                "AIB-CMD-LEFT {} is another aib (AIB 1.x?), left as it is; open the workbench with {} ctl web",
                at.display(),
                exe.display()
            ));
            return out;
        }
        Err(e) => {
            out.push(format!("AIB-CMD-NOT-WRITTEN {e}"));
            return out;
        }
    };
    let on_path = path.is_some_and(|p| std::env::split_paths(p).any(|d| d == dir));
    if !on_path {
        out.push(format!(
            "AIB-CMD-PATH {} is not on PATH: until it is, type {} web",
            dir.display(),
            at.display()
        ));
    } else if let Some(first) = path
        .and_then(|p| first_on_path(p.as_os_str()))
        .filter(|f| *f != at)
    {
        out.push(format!(
            "AIB-CMD-PATH another aib comes first on PATH ({}): type {} web",
            first.display(),
            at.display()
        ));
    }
    out
}

/// `aibd uninstall`: remove ours only. `None` = nothing of ours was there.
pub fn remove(dir: &Path) -> Option<String> {
    let at = dir.join(NAME);
    if !ours(&at) {
        return None;
    }
    Some(match std::fs::remove_file(&at) {
        Ok(()) => format!("REMOVED {}", at.display()),
        Err(e) => format!("AIB-CMD-KEPT {}: {e}", at.display()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aib_web_is_ctl_web_and_nothing_else_is_taken() {
        assert!(is_aib(OsStr::new("/Users/x/.local/bin/aib")));
        assert!(is_aib(OsStr::new("aib")));
        if cfg!(windows) {
            assert!(is_aib(OsStr::new("C:\\Users\\x\\AIB\\aib.exe")));
        }
        assert!(!is_aib(OsStr::new("/Users/x/.local/bin/aibd")));
        if !cfg!(windows) {
            let app = "/Users/x/Applications/AIB.app/Contents/MacOS/AIB";
            assert!(!is_aib(OsStr::new(app)), "reason: the_app_taken_for_aib");
        }
        assert!(!is_aib(OsStr::new("/Users/x/aib-idea")));
        assert_eq!(ctl_args(&["web"]), Ok(vec!["web"]));
        assert_eq!(ctl_args(&["web", "--keep"]), Ok(vec!["web", "--keep"]));
        for other in [&[][..], &["dm", "x"][..], &["now"][..], &["webx"][..]] {
            assert!(ctl_args(other).is_err(), "reason: taken {other:?}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn install_links_aib_to_aibd_and_leaves_another_aib() {
        let d = tempfile::tempdir().unwrap();
        let bin = d.path().join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let exe = bin.join(crate::install::EXE_NAME);
        std::fs::write(&exe, b"build").unwrap();
        let path = Some(std::env::join_paths([&bin]).unwrap());
        let lines = place_lines(&bin, &exe, path.as_ref());
        assert_eq!(lines.len(), 1, "{lines:?}");
        assert!(lines[0].starts_with("AIB-CMD "), "{lines:?}");
        assert_eq!(std::fs::read_link(bin.join("aib")).unwrap(), exe);
        // again: the same link, nothing else said
        assert_eq!(place_lines(&bin, &exe, path.as_ref()).len(), 1);
        // not on PATH: said, still written
        let other = Some(std::env::join_paths([d.path().join("elsewhere")]).unwrap());
        let lines = place_lines(&bin, &exe, other.as_ref());
        assert!(
            lines.iter().any(|l| l.starts_with("AIB-CMD-PATH")),
            "{lines:?}"
        );
        // another aib first on PATH (AIB 1.x in its own folder): said
        let one = d.path().join("one");
        std::fs::create_dir_all(&one).unwrap();
        std::fs::write(one.join("aib"), b"1.x").unwrap();
        let first = Some(std::env::join_paths([&one, &bin]).unwrap());
        let lines = place_lines(&bin, &exe, first.as_ref());
        assert!(lines.iter().any(|l| l.contains("comes first")), "{lines:?}");
        // AIB 1.x's aib in the same folder (a link to its own program, or a file): left
        let old = d.path().join("old");
        std::fs::create_dir_all(&old).unwrap();
        std::os::unix::fs::symlink(d.path().join("ez2-aib/bin/aib"), old.join("aib")).unwrap();
        let lines = place_lines(&old, &exe, path.as_ref());
        assert!(lines[0].starts_with("AIB-CMD-LEFT"), "{lines:?}");
        assert_eq!(
            std::fs::read_link(old.join("aib")).unwrap(),
            d.path().join("ez2-aib/bin/aib"),
            "reason: the_1x_aib_replaced"
        );
        assert_eq!(remove(&old), None, "reason: the_1x_aib_removed");
        std::fs::write(one.join("aib"), b"1.x").unwrap();
        assert!(place_lines(&one, &exe, path.as_ref())[0].starts_with("AIB-CMD-LEFT"));
        assert_eq!(std::fs::read(one.join("aib")).unwrap(), b"1.x");
        // uninstall: ours goes
        assert!(remove(&bin).unwrap().starts_with("REMOVED"));
        assert!(std::fs::symlink_metadata(bin.join("aib")).is_err());
        assert!(exe.exists());
    }
}
