//! Where aibd's shared folders are, and keeping system indexers out of them
//! (PREREAD-shared-folder-noindex-a9136-1121, 5d33a 0/0/2/2; Andy 11:3x 「新安裝的 aib 2.0 在
//! mac/windows 如何共同讀他們對應的共用資料夾?」「noindex 的共同資料夾方法呢」).
//!
//! - New folders on macOS are named `<base>.noindex` and hold an empty `.metadata_never_index`
//!   (Spotlight skips both); on Windows they carry `FILE_ATTRIBUTE_NOT_CONTENT_INDEXED`.
//! - A folder already there under its old name keeps that name (sessions and trees record its
//!   path); it only gets the mark. Moving old folders is a later `ctl` command.
//! - The documents folder: `AIBD_PLAN_PENDING` when it passes [`override_ok`] (5d33a L1), else
//!   `~/.ez2-aib/pending` when that is a plain folder (our own machines), else
//!   `<aibd data>/docs(.noindex)` (a new install). Each aibd only sees its own; nothing is shared
//!   between machines.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// The file that tells Spotlight to skip a folder.
pub const MARK: &str = ".metadata_never_index";
/// The name ending Spotlight skips.
pub const SUFFIX: &str = ".noindex";

static DATA: OnceLock<PathBuf> = OnceLock::new();

/// `<parent>/<base>` when it is already there (an old folder keeps its name), else the new name:
/// `<base>.noindex` on macOS, `<base>` elsewhere.
pub fn folder(parent: &Path, base: &str) -> PathBuf {
    let old = parent.join(base);
    if cfg!(target_os = "macos") && std::fs::symlink_metadata(&old).is_err() {
        parent.join(format!("{base}{SUFFIX}"))
    } else {
        old
    }
}

/// Keep indexers out of `dir` (an old folder as well as a new one).
pub fn mark(dir: &Path) -> std::io::Result<()> {
    if cfg!(target_os = "macos") {
        let m = dir.join(MARK);
        if std::fs::symlink_metadata(&m).is_err() {
            std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&m)?;
        }
    }
    aib_sys::noindex::mark(dir)
}

/// `dir` made (owner only) and marked.
pub fn ensure(dir: &Path) -> std::io::Result<()> {
    crate::platform::create_private_dir(dir)?;
    mark(dir)
}

/// At start: remember the data folder for [`docs_root`], then make and mark the seat folders
/// and the documents folder. Failures only go to the log; the folders are made again on use.
pub fn prepare(data: &Path) {
    let _ = DATA.set(data.to_path_buf());
    for base in ["seat-work", "seat-git"] {
        if let Err(e) = ensure(&folder(data, base)) {
            tracing::warn!(error = %e, base, "folder not prepared");
        }
    }
    if let Some(docs) = docs_root() {
        if docs.starts_with(data) {
            if let Err(e) = ensure(&docs) {
                tracing::warn!(error = %e, "documents folder not prepared");
            }
        }
    }
}

/// The documents folder (see the module notes).
pub fn docs_root() -> Option<PathBuf> {
    let data = DATA
        .get()
        .cloned()
        .or_else(crate::platform::default_data_dir);
    docs_root_from(|k| std::env::var_os(k), data.as_deref())
}

/// [`docs_root`] with the environment and the data folder given.
pub fn docs_root_from(
    env: impl Fn(&str) -> Option<std::ffi::OsString>,
    data: Option<&Path>,
) -> Option<PathBuf> {
    let home = env("HOME")
        .or_else(|| env("USERPROFILE"))
        .map(PathBuf::from);
    if let Some(p) = env("AIBD_PLAN_PENDING").map(PathBuf::from) {
        if override_ok(&p, home.as_deref(), data) {
            return Some(p);
        }
        tracing::warn!(path = %p.display(), "AIBD_PLAN_PENDING not used: not a plain folder of this user, or inside a guarded folder");
    }
    if let Some(own) = home.as_ref().map(|h| h.join(".ez2-aib").join("pending")) {
        if plain_dir(&own) {
            return Some(own);
        }
    }
    Some(folder(data?, "docs"))
}

/// A folder itself, not a link to one.
fn plain_dir(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_dir())
}

/// 5d33a L1: the override is used only when it is absolute, with no `.git` in its path, not
/// inside `~/.ssh`, `~/Library/Keychains` or the aibd data folder, and, once it is there, a plain
/// folder (not a link) this user owns. One not there yet is kept (tests name a fresh folder):
/// nothing is read from or written to it until it is a plain folder, checked on every use.
pub fn override_ok(p: &Path, home: Option<&Path>, data: Option<&Path>) -> bool {
    if !p.is_absolute() {
        return false;
    }
    if std::fs::symlink_metadata(p).is_ok() && !(plain_dir(p) && own(p)) {
        return false;
    }
    let real = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    if real.components().any(|c| c.as_os_str() == ".git") {
        return false;
    }
    let mut guarded: Vec<PathBuf> = data.map(Path::to_path_buf).into_iter().collect();
    if let Some(h) = home {
        guarded.push(h.join(".ssh"));
        guarded.push(h.join("Library").join("Keychains"));
    }
    !guarded.iter().any(|g| {
        let g = std::fs::canonicalize(g).unwrap_or_else(|_| g.clone());
        real.starts_with(&g)
    })
}

#[cfg(unix)]
fn own(p: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    std::fs::symlink_metadata(p).is_ok_and(|m| m.uid() == nix::unistd::geteuid().as_raw())
}

/// Windows: the folder's owner is not read here (5d33a N2: the sandbox is not on Windows).
#[cfg(not(unix))]
fn own(_p: &Path) -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    fn env_of(pairs: Vec<(&'static str, PathBuf)>) -> impl Fn(&str) -> Option<OsString> {
        move |k| {
            pairs
                .iter()
                .find(|(n, _)| *n == k)
                .map(|(_, v)| v.clone().into_os_string())
        }
    }

    /// NI2 / NI4: a new folder gets the .noindex name on macOS; an old one keeps its name.
    /// NI7 (5d33a L2): a new install is local; an older roaming one keeps its folder.
    #[test]
    fn a_new_install_does_not_roam() {
        let d = tempfile::tempdir().unwrap();
        let (roam, local) = (d.path().join("Roaming"), d.path().join("Local"));
        assert_eq!(
            crate::platform::pick_data_dir(&roam, &local),
            local,
            "reason: roams"
        );
        std::fs::create_dir(&roam).unwrap();
        assert_eq!(
            crate::platform::pick_data_dir(&roam, &local),
            roam,
            "reason: old_install_moved"
        );
        assert_eq!(crate::platform::pick_data_dir(&local, &local), local);
    }

    #[test]
    fn a_new_folder_is_named_noindex_and_an_old_one_kept() {
        let d = tempfile::tempdir().unwrap();
        let new = folder(d.path(), "seat-work");
        if cfg!(target_os = "macos") {
            assert_eq!(
                new,
                d.path().join("seat-work.noindex"),
                "reason: no_noindex_name"
            );
        } else {
            assert_eq!(new, d.path().join("seat-work"));
        }
        std::fs::create_dir(d.path().join("seat-git")).unwrap();
        assert_eq!(
            folder(d.path(), "seat-git"),
            d.path().join("seat-git"),
            "reason: old_folder_renamed"
        );
    }

    /// NI3: a folder, new or old, holds the mark on macOS.
    #[test]
    fn a_prepared_folder_holds_the_mark() {
        let d = tempfile::tempdir().unwrap();
        let old = d.path().join("seat-work");
        std::fs::create_dir(&old).unwrap();
        ensure(&old).unwrap();
        ensure(&old).unwrap();
        let new = folder(d.path(), "docs");
        ensure(&new).unwrap();
        assert!(new.is_dir());
        if cfg!(target_os = "macos") {
            assert!(old.join(MARK).is_file(), "reason: no_mark");
            assert!(new.join(MARK).is_file(), "reason: no_mark");
        }
    }

    /// NI1 / NI6 (5d33a L1): the override must be a plain folder of this user outside guarded
    /// folders; our own pending comes next; a new install lands in the data folder.
    #[test]
    fn the_documents_folder_is_chosen_in_order() {
        let home = tempfile::tempdir().unwrap();
        let data = home.path().join("data");
        std::fs::create_dir(&data).unwrap();
        let h = home.path().to_path_buf();
        // a new install: neither the override nor our own folder
        assert_eq!(
            docs_root_from(env_of(vec![("HOME", h.clone())]), Some(&data)),
            Some(folder(&data, "docs"))
        );
        // our own machine
        let own = h.join(".ez2-aib").join("pending");
        std::fs::create_dir_all(&own).unwrap();
        assert_eq!(
            docs_root_from(env_of(vec![("HOME", h.clone())]), Some(&data)),
            Some(own.clone())
        );
        // a plain override wins
        let mine = h.join("mine");
        std::fs::create_dir(&mine).unwrap();
        assert_eq!(
            docs_root_from(
                env_of(vec![
                    ("HOME", h.clone()),
                    ("AIBD_PLAN_PENDING", mine.clone())
                ]),
                Some(&data)
            ),
            Some(mine)
        );
        // an override not made yet is kept (nothing is read or written there until it is)
        let fresh = h.join("fresh");
        assert_eq!(
            docs_root_from(
                env_of(vec![
                    ("HOME", h.clone()),
                    ("AIBD_PLAN_PENDING", fresh.clone())
                ]),
                Some(&data)
            ),
            Some(fresh)
        );
        // guarded folders are not used
        let ssh = h.join(".ssh");
        std::fs::create_dir(&ssh).unwrap();
        let git = h.join("repo").join(".git");
        std::fs::create_dir_all(&git).unwrap();
        for bad in [ssh, git, data.clone()] {
            assert_eq!(
                docs_root_from(
                    env_of(vec![
                        ("HOME", h.clone()),
                        ("AIBD_PLAN_PENDING", bad.clone())
                    ]),
                    Some(&data)
                ),
                Some(own.clone()),
                "reason: guarded_override_used {}",
                bad.display()
            );
        }
        #[cfg(unix)]
        {
            // a link, either as the override or as our own folder, is not used
            let link = h.join("link");
            std::os::unix::fs::symlink(h.join("mine"), &link).unwrap();
            assert_eq!(
                docs_root_from(
                    env_of(vec![("HOME", h.clone()), ("AIBD_PLAN_PENDING", link)]),
                    Some(&data)
                ),
                Some(own.clone()),
                "reason: link_override_used"
            );
            std::fs::remove_dir(&own).unwrap();
            std::os::unix::fs::symlink(h.join("mine"), &own).unwrap();
            assert_eq!(
                docs_root_from(env_of(vec![("HOME", h.clone())]), Some(&data)),
                Some(folder(&data, "docs")),
                "reason: link_root_used"
            );
        }
    }
}
