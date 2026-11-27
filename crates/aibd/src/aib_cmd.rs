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

/// Ours: a link to this very aibd (the whole path, 3e53b REVIEW-op1 r1 N1: a link to another
/// aibd is another install's, not ours).
fn ours(link: &Path, exe: &Path) -> bool {
    is_link(link) && std::fs::read_link(link).ok().as_deref() == Some(exe)
}

/// What [`place`] found or did.
#[derive(Debug, PartialEq, Eq)]
pub enum Placed {
    Written(PathBuf),
    /// an `aib` that is not ours is in `dir` or anywhere on PATH: nothing placed
    NotOurs(PathBuf),
}

/// An `aib` that is not ours: in `dir`, or in any folder on `path` (f163c REVIEW-op2 N1 /
/// 3e53b PRE-READ-aib-cmd-windows r1: with AIB 1.x's anywhere on PATH, ours would hide it or be
/// hidden by it, so none is placed).
fn another(dir: &Path, exe: &Path, path: Option<&OsString>) -> Option<PathBuf> {
    let mut seen = vec![dir.join(NAME)];
    if let Some(p) = path {
        seen.extend(std::env::split_paths(p).map(|d| d.join(NAME)));
    }
    seen.into_iter()
        .find(|p| std::fs::symlink_metadata(p).is_ok() && !ours(p, exe))
}

/// `dir/aib` → `exe`, written when no other `aib` is there or on PATH; read back.
pub fn place(dir: &Path, exe: &Path, path: Option<&OsString>) -> Result<Placed, String> {
    if let Some(other) = another(dir, exe, path) {
        return Ok(Placed::NotOurs(other));
    }
    let at = dir.join(NAME);
    if ours(&at, exe) {
        return Ok(Placed::Written(at));
    }
    link(exe, &at)?;
    if !ours(&at, exe) {
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

/// The lines `aibd install` prints for `aib`: written / left, then whether PATH reaches it.
/// Never fails the install.
pub fn place_lines(dir: &Path, exe: &Path, path: Option<&OsString>) -> Vec<String> {
    let mut out = Vec::new();
    match place(dir, exe, path) {
        Ok(Placed::Written(at)) => {
            out.push(format!("AIB-CMD {} (aib web opens the workbench)", at.display()));
            let on_path = path.is_some_and(|p| std::env::split_paths(p).any(|d| d == dir));
            if !on_path {
                out.push(format!(
                    "AIB-CMD-PATH {} is not on PATH: until it is, type {} web",
                    dir.display(),
                    at.display()
                ));
            }
        }
        Ok(Placed::NotOurs(other)) => out.push(format!(
            "AIB-CMD-LEFT {} is another aib (AIB 1.x?), so none was placed; open the workbench with {} ctl web",
            other.display(),
            exe.display()
        )),
        Err(e) => out.push(format!("AIB-CMD-NOT-WRITTEN {e}")),
    }
    out
}

/// `aibd uninstall`: remove ours (the link to `exe`) only. `None` = nothing of ours was there.
pub fn remove(dir: &Path, exe: &Path) -> Option<String> {
    let at = dir.join(NAME);
    if !ours(&at, exe) {
        return None;
    }
    Some(match std::fs::remove_file(&at) {
        Ok(()) => format!("REMOVED {}", at.display()),
        Err(e) => format!("AIB-CMD-KEPT {}: {e}", at.display()),
    })
}

// ---------------------------------------------------------------- Windows: the user's PATH
// PRE-READ-aib-cmd-windows-3e53b-1127 §2-§3: what goes into / out of the user's PATH, worked out
// here so both platforms test it; the registry read / write is aib_sys::winrun's.

/// One PATH entry as compared: blanks and quotes off, trailing `\` / `/` off, case folded
/// (Windows folders are not case sensitive).
pub fn seg_key(s: &str) -> String {
    s.trim()
        .trim_matches('"')
        .trim()
        .trim_end_matches(['\\', '/'])
        .to_lowercase()
}

/// `%NAME%` replaced by `var(NAME)`; an unset one (or a lone `%`) stays as written.
pub fn expand_vars(s: &str, var: &dyn Fn(&str) -> Option<String>) -> String {
    let mut out = String::new();
    let mut rest = s;
    while let Some(i) = rest.find('%') {
        out.push_str(rest.get(..i).unwrap_or_default());
        let after = rest.get(i + 1..).unwrap_or_default();
        match after.find('%') {
            Some(j) if j > 0 => {
                let name = after.get(..j).unwrap_or_default();
                match var(name) {
                    Some(v) => out.push_str(&v),
                    None => {
                        out.push('%');
                        out.push_str(name);
                        out.push('%');
                    }
                }
                rest = after.get(j + 1..).unwrap_or_default();
            }
            _ => {
                out.push('%');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// The user PATH `old` with `dir` added at the end; `Ok(None)` when an entry is `dir` already
/// (as written or expanded, either side). Every other entry stays exactly as it was; too long
/// for Windows is an error, nothing written.
pub fn path_with(
    dir: &str,
    old: &str,
    var: &dyn Fn(&str) -> Option<String>,
) -> Result<Option<String>, String> {
    let want = [seg_key(dir), seg_key(&expand_vars(dir, var))];
    let has = old.split(';').any(|e| {
        let k = seg_key(e);
        let kx = seg_key(&expand_vars(e, var));
        !k.is_empty() && (want.contains(&k) || want.contains(&kx))
    });
    if has {
        return Ok(None);
    }
    let new = if old.is_empty() || old.ends_with(';') {
        format!("{old}{dir}")
    } else {
        format!("{old};{dir}")
    };
    if new.encode_utf16().count() > aib_sys::winrun::PATH_MAX_UNITS {
        return Err("the user PATH would be longer than Windows allows".into());
    }
    Ok(Some(new))
}

/// What [`path_without`] found.
#[derive(Debug, PartialEq, Eq)]
pub enum Without {
    /// the PATH with that one entry taken out
    Removed(String),
    NotThere,
    /// there more than once: changed since AIB added it, so left
    Many(usize),
}

/// The user PATH `old` without the entry that is exactly `seg` (what AIB added, as recorded),
/// only when it is there once; every other entry stays exactly as it was.
pub fn path_without(seg: &str, old: &str) -> Without {
    if seg.is_empty() {
        return Without::NotThere;
    }
    let parts: Vec<&str> = old.split(';').collect();
    let at: Vec<usize> = parts
        .iter()
        .enumerate()
        .filter(|(_, e)| **e == seg)
        .map(|(i, _)| i)
        .collect();
    match at.as_slice() {
        [] => Without::NotThere,
        [one] => Without::Removed(
            parts
                .iter()
                .enumerate()
                .filter(|(i, _)| i != one)
                .map(|(_, e)| *e)
                .collect::<Vec<_>>()
                .join(";"),
        ),
        many => Without::Many(many.len()),
    }
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
        assert_eq!(place_lines(&bin, &exe, path.as_ref()), lines);
        // not on PATH: said, still ours
        let other = Some(std::env::join_paths([d.path().join("elsewhere")]).unwrap());
        let l = place_lines(&bin, &exe, other.as_ref());
        assert!(l.iter().any(|l| l.starts_with("AIB-CMD-PATH")), "{l:?}");
        // AIB 1.x's aib anywhere on PATH (before or after ours): none placed, its path said
        let one = d.path().join("one");
        std::fs::create_dir_all(&one).unwrap();
        std::fs::write(one.join("aib"), b"1.x").unwrap();
        let fresh = d.path().join("fresh");
        std::fs::create_dir_all(&fresh).unwrap();
        for order in [[&one, &fresh], [&fresh, &one]] {
            let p = Some(std::env::join_paths(order).unwrap());
            let l = place_lines(&fresh, &exe, p.as_ref());
            assert!(l[0].starts_with("AIB-CMD-LEFT"), "{l:?}");
            assert!(
                l[0].contains(&one.join("aib").display().to_string()),
                "{l:?}"
            );
            assert!(
                std::fs::symlink_metadata(fresh.join("aib")).is_err(),
                "reason: placed_beside_1x"
            );
        }
        // AIB 1.x's aib in the same folder (a link to its own program): left, not removed
        let old = d.path().join("old");
        std::fs::create_dir_all(&old).unwrap();
        let theirs = d.path().join("ez2-aib/bin/aib");
        std::os::unix::fs::symlink(&theirs, old.join("aib")).unwrap();
        let l = place_lines(&old, &exe, path.as_ref());
        assert!(l[0].starts_with("AIB-CMD-LEFT"), "{l:?}");
        assert_eq!(std::fs::read_link(old.join("aib")).unwrap(), theirs);
        assert_eq!(remove(&old, &exe), None, "reason: the_1x_aib_removed");
        // a link to another aibd (another install's): not ours either
        let two = d.path().join("two");
        std::fs::create_dir_all(&two).unwrap();
        std::os::unix::fs::symlink(d.path().join("x/aibd"), two.join("aib")).unwrap();
        assert!(place_lines(&two, &exe, None)[0].starts_with("AIB-CMD-LEFT"));
        assert_eq!(
            remove(&two, &exe),
            None,
            "reason: another_installs_aib_removed"
        );
        // uninstall: ours goes
        assert!(remove(&bin, &exe).unwrap().starts_with("REMOVED"));
        assert!(std::fs::symlink_metadata(bin.join("aib")).is_err());
        assert!(exe.exists());
    }

    fn env(n: &str) -> Option<String> {
        match n {
            "LOCALAPPDATA" => Some(r"C:\Users\a\AppData\Local".into()),
            "USERPROFILE" => Some(r"C:\Users\a".into()),
            _ => None,
        }
    }

    #[test]
    fn our_folder_goes_at_the_end_once_and_nothing_else_changes() {
        let dir = r"C:\Users\a\AppData\Local\AIB";
        // at the end, after a separator; the rest byte for byte (empty / repeated / odd entries)
        let old = r"%USERPROFILE%\bin;;C:\x;C:\x;C:\ü & (y)";
        assert_eq!(
            path_with(dir, old, &env).unwrap().as_deref(),
            Some(r"%USERPROFILE%\bin;;C:\x;C:\x;C:\ü & (y);C:\Users\a\AppData\Local\AIB"),
            "reason: not_at_the_end"
        );
        assert_eq!(
            path_with(dir, r"C:\x;", &env).unwrap().as_deref(),
            Some(r"C:\x;C:\Users\a\AppData\Local\AIB"),
            "reason: double_separator"
        );
        assert_eq!(path_with(dir, "", &env).unwrap().as_deref(), Some(dir));
        // there already: quotes, a trailing \, other case, or written with %LOCALAPPDATA%
        for there in [
            r"C:\y;c:\users\A\appdata\local\aib",
            r#"C:\y;"C:\Users\a\AppData\Local\AIB\""#,
            r"%LOCALAPPDATA%\AIB;C:\y",
            r"%localappdata%\aib\",
        ] {
            assert_eq!(
                path_with(dir, there, &env),
                Ok(None),
                "reason: added_twice {there}"
            );
        }
        // an unset %VAR% stays as written and is not taken for ours
        assert!(path_with(dir, r"%NOPE%\AIB", &env).unwrap().is_some());
        // too long for Windows: not written
        let long = "C:\\x;".repeat(7000);
        assert!(path_with(dir, &long, &env).is_err(), "reason: too_long");
    }

    #[test]
    fn uninstall_takes_out_only_what_aib_added_and_only_once() {
        let seg = r"C:\Users\a\AppData\Local\AIB";
        assert_eq!(
            path_without(seg, r"%USERPROFILE%\bin;;C:\x;C:\Users\a\AppData\Local\AIB"),
            Without::Removed(r"%USERPROFILE%\bin;;C:\x".into()),
            "reason: others_changed"
        );
        assert_eq!(path_without(seg, seg), Without::Removed(String::new()));
        assert_eq!(
            path_without(seg, r"C:\Users\a\AppData\Local\AIB;C:\x"),
            Without::Removed(r"C:\x".into())
        );
        // not exactly what was added (case, trailing \): left
        assert_eq!(
            path_without(seg, r"c:\users\a\appdata\local\aib"),
            Without::NotThere
        );
        assert_eq!(
            path_without(seg, r"C:\Users\a\AppData\Local\AIB\"),
            Without::NotThere
        );
        // twice: the customer changed it; left
        assert_eq!(
            path_without(
                seg,
                r"C:\Users\a\AppData\Local\AIB;C:\x;C:\Users\a\AppData\Local\AIB"
            ),
            Without::Many(2),
            "reason: removed_one_of_two"
        );
        assert_eq!(path_without("", "a;;b"), Without::NotThere);
    }

    #[test]
    fn a_percent_name_is_expanded_only_when_set() {
        assert_eq!(
            expand_vars(r"%LOCALAPPDATA%\AIB", &env),
            r"C:\Users\a\AppData\Local\AIB"
        );
        assert_eq!(expand_vars(r"%NOPE%\x", &env), r"%NOPE%\x");
        assert_eq!(expand_vars("100% %", &env), "100% %");
        assert_eq!(expand_vars("%%x", &env), "%%x");
    }
}
