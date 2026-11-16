//! Where aib-drive finds the programs it runs (`aib`, `aib-xtask`). aibd's launchd job hands
//! its children a short PATH, so a bare name is not enough (a9136 11-16 01:41: observe's
//! refresh never found aib-xtask; 05:28: act's `aib dm` retried "aib not run: entity not
//! found" 23 times from 11-15 11:22, so no nudge went out). Each name is looked for in its
//! own folders first, then in each `PATH` folder in order.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

/// `name` with the platform's executable suffix.
pub fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    }
}

/// The first `<dir>/<name>` that is a file: `first` in order, then each `path` folder in
/// order. None found is said with where it looked.
pub fn find(name: &str, first: &[PathBuf], path: Option<&OsStr>) -> Result<PathBuf, String> {
    let name = exe(name);
    if let Some(found) = first.iter().map(|d| d.join(&name)).find(|c| c.is_file()) {
        return Ok(found);
    }
    let dirs: Vec<PathBuf> = path
        .map(|p| std::env::split_paths(p).collect())
        .unwrap_or_default();
    if let Some(found) = dirs.iter().map(|d| d.join(&name)).find(|c| c.is_file()) {
        return Ok(found);
    }
    let tried: Vec<String> = first
        .iter()
        .map(|d| d.join(&name).display().to_string())
        .collect();
    Err(format!(
        "{name} not found: tried {} and {} PATH folders",
        tried.join(", "),
        dirs.len()
    ))
}

/// `aib`'s own folders: the state root's `bin`, then `~/.local/bin` (where `aib install`
/// links it; not on aibd's PATH).
pub fn aib_dirs(root: Option<&Path>, home: Option<&Path>) -> Vec<PathBuf> {
    let mut d: Vec<PathBuf> = root.map(|r| r.join("bin")).into_iter().collect();
    d.extend(home.map(|h| h.join(".local").join("bin")));
    d
}

/// The `aib` program to run: the first one [`find`] gives for this process's state root,
/// home and PATH, else the bare name (the spawn then says it is not found, as before).
pub fn aib_program() -> String {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    let root = crate::observe::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        home.as_deref(),
    )
    .ok();
    let dirs = aib_dirs(root.as_deref(), home.as_deref());
    find("aib", &dirs, std::env::var_os("PATH").as_deref())
        .map_or_else(|_| "aib".to_owned(), |p| p.display().to_string())
}

/// preview.7 Windows (519d0 PLAN-preview7 r2; BRIEF-s79-sender-win-7b81e-1116 option C):
/// Windows has no dm transport yet (`aib dm` is dm-ctl.sh = bash + tmux, and there is no
/// Rust sender), so act's dm sender is this marker there: every send is NotSent with this
/// reason and nothing is started.
pub const NO_DM_TRANSPORT: &str =
    "no dm transport on this platform (Windows: aib dm is bash + tmux; S79-sender-win)";

/// The program act's dm sender runs on the platform `os` (`std::env::consts::OS`):
/// `windows` ⇒ [`NO_DM_TRANSPORT`], every other one `aib` as found.
pub fn dm_program_on(os: &str, aib: impl FnOnce() -> String) -> String {
    if os == "windows" {
        return NO_DM_TRANSPORT.to_owned();
    }
    aib()
}

/// [`dm_program_on`] this platform.
pub fn dm_program() -> String {
    dm_program_on(std::env::consts::OS, aib_program)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// preview.7 Windows: Windows gets the no-transport marker and never looks for aib;
    /// macOS and Linux keep aib.
    #[test]
    fn windows_has_no_dm_transport_and_other_platforms_keep_aib() {
        let p = dm_program_on("windows", || panic!("aib looked up on Windows"));
        assert_eq!(p, NO_DM_TRANSPORT);
        for os in ["macos", "linux"] {
            assert_eq!(dm_program_on(os, || "/x/aib".to_owned()), "/x/aib", "{os}");
        }
    }

    #[test]
    fn aib_is_looked_for_in_the_state_bin_then_local_bin_then_path() {
        let d = std::env::temp_dir().join(format!("aib-drive-progpath-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let (root, home, p) = (d.join("root"), d.join("home"), d.join("p"));
        let dirs = aib_dirs(Some(&root), Some(&home));
        assert_eq!(
            dirs,
            vec![root.join("bin"), home.join(".local").join("bin")]
        );
        for dir in dirs.iter().chain([&p]) {
            std::fs::create_dir_all(dir).unwrap();
        }
        let name = exe("aib");
        let path = std::env::join_paths([&p]).unwrap();
        let miss = find("aib", &dirs, Some(&path)).unwrap_err();
        assert!(
            miss.contains(
                &home
                    .join(".local")
                    .join("bin")
                    .join(&name)
                    .display()
                    .to_string()
            ),
            "{miss}"
        );
        assert!(miss.contains("1 PATH folders"), "{miss}");
        std::fs::write(p.join(&name), "").unwrap();
        assert_eq!(find("aib", &dirs, Some(&path)), Ok(p.join(&name)));
        // aibd's PATH has no ~/.local/bin: the link aib install made there is still found
        std::fs::write(home.join(".local").join("bin").join(&name), "").unwrap();
        assert_eq!(
            find("aib", &dirs, Some(&path)),
            Ok(home.join(".local").join("bin").join(&name))
        );
        std::fs::write(root.join("bin").join(&name), "").unwrap();
        assert_eq!(
            find("aib", &dirs, Some(&path)),
            Ok(root.join("bin").join(&name))
        );
        let _ = std::fs::remove_dir_all(&d);
    }
}
