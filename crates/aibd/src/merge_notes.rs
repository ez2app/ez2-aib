//! aibd §8 8c (DESIGN-merge-6770f-1123 r1 §4b-2, §5): a merged second brain's notes copied into
//! the kept brain, and taken back by `merge_undo`.
//!
//! - The preview counts files, bytes, and cloud placeholders (a file the cloud has not put on
//!   this computer: iCloud's `.name.icloud` on a Mac; on Windows a file whose attributes say it
//!   is recalled from the cloud when opened or read, or offline). The copy checks the same as it
//!   goes: over a limit or any placeholder ⇒ refused.
//! - Copy into a fresh hidden folder inside the kept brain, then rename it in one step to
//!   `merged-from-<route key>` (`-2`, `-3`… when taken). Anything going wrong on the way removes
//!   the hidden folder: never half a copy. Nothing is ever written over; the merged brain's own
//!   folder is only read.
//! - Links are not followed (a link or a junction is skipped); on unix a file is opened with
//!   `O_NOFOLLOW`. aibd and a dgrs seat are the same user, so this keeps out mistakes, not a
//!   seat (GUIDE-same-user-ai-threat-model).
//! - Every file copied is listed with its sha256 in a manifest under aibd's data folder
//!   (`archive/merge-notes/<batch>-<seq>.json`): the step journal row has a size limit, the
//!   manifest does not. Undo moves a copied file into `archive/merge-undo/<batch>/` only while
//!   its sha256 is still the one copied; a file changed since stays and is listed.
//!
//! Windows placeholder attributes and the Windows rename: UNVERIFIED on a Windows machine
//! (only the attribute bits are unit tested on every platform).

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub const MAX_FILES: usize = 2000;
pub const MAX_BYTES: u64 = 200 * 1024 * 1024;
pub const MAX_FILE_BYTES: u64 = 5 * 1024 * 1024;
const NAME_TRIES: u32 = 99;

/// What a brain folder holds, counted without following links.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct NotesCheck {
    pub files: usize,
    pub bytes: u64,
    pub cloud_placeholders: usize,
    pub over_limit: bool,
}

const FILE_ATTRIBUTE_OFFLINE: u32 = 0x1000;
const FILE_ATTRIBUTE_RECALL_ON_OPEN: u32 = 0x0004_0000;
const FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: u32 = 0x0040_0000;

/// Windows file attributes of a file the cloud has not put on this computer.
pub fn attrs_say_cloud_only(attrs: u32) -> bool {
    attrs
        & (FILE_ATTRIBUTE_OFFLINE
            | FILE_ATTRIBUTE_RECALL_ON_OPEN
            | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        != 0
}

#[cfg(windows)]
fn cloud_only(m: &std::fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    attrs_say_cloud_only(m.file_attributes())
}

#[cfg(not(windows))]
fn cloud_only(_m: &std::fs::Metadata) -> bool {
    false
}

/// A cloud placeholder: iCloud's `.name.icloud`, or (Windows) cloud-only attributes.
pub fn is_cloud_placeholder(name: &str, m: &std::fs::Metadata) -> bool {
    (name.starts_with('.') && name.ends_with(".icloud")) || cloud_only(m)
}

/// A link, or on Windows any reparse point (a junction or a directory link too; 6770f
/// follows brain_note's fsops). Ask [`is_cloud_placeholder`] first: a cloud-only file is a
/// reparse point as well, and must refuse the copy, not be skipped as a link.
fn is_link(m: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if m.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    m.file_type().is_symlink()
}

/// Count a brain folder: regular files, bytes, placeholders; links are not followed.
pub fn scan(root: &Path) -> NotesCheck {
    let mut check = NotesCheck::default();
    let mut dirs = vec![root.to_path_buf()];
    while let Some(d) = dirs.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in entries.flatten() {
            let p = e.path();
            let Ok(meta) = std::fs::symlink_metadata(&p) else {
                continue;
            };
            let name = e.file_name().to_string_lossy().into_owned();
            if is_cloud_placeholder(&name, &meta) {
                check.cloud_placeholders += 1;
                continue;
            }
            if is_link(&meta) {
                continue;
            }
            if meta.is_dir() {
                dirs.push(p);
            } else if meta.is_file() {
                let len = meta.len();
                check.files += 1;
                check.bytes = check.bytes.saturating_add(len);
                if len > MAX_FILE_BYTES || check.files > MAX_FILES || check.bytes > MAX_BYTES {
                    check.over_limit = true;
                }
            }
        }
    }
    check
}

/// What one copy made: the folder's name inside the kept brain and every file with its sha256.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Copied {
    pub folder: String,
    pub files: Vec<(String, String)>,
}

impl Copied {
    pub fn to_json(&self) -> Value {
        json!({
            "folder": self.folder,
            "files": self.files.iter().map(|(r, s)| json!([r, s])).collect::<Vec<_>>(),
        })
    }

    pub fn from_json(v: &Value) -> Option<Copied> {
        let folder = v["folder"].as_str()?.to_string();
        let files = v["files"]
            .as_array()?
            .iter()
            .map(|p| Some((p[0].as_str()?.to_string(), p[1].as_str()?.to_string())))
            .collect::<Option<Vec<_>>>()?;
        Some(Copied { folder, files })
    }
}

fn tail6() -> Result<String, &'static str> {
    const ABC: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
    let mut b = [0u8; 6];
    getrandom::fill(&mut b).map_err(|_| "notes_copy_failed")?;
    Ok(b.iter()
        .map(|x| ABC[usize::from(*x) % ABC.len()] as char)
        .collect())
}

/// `tag` as one plain folder-name piece (a route key already is; anything else becomes `_`).
fn plain_tag(tag: &str) -> String {
    let t: String = tag
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .take(40)
        .collect();
    if t.is_empty() {
        "brain".to_string()
    } else {
        t
    }
}

fn open_source(p: &Path) -> std::io::Result<std::fs::File> {
    let mut o = std::fs::OpenOptions::new();
    o.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.custom_flags(nix::fcntl::OFlag::O_NOFOLLOW.bits());
    }
    o.open(p)
}

/// Copy every regular file under `src` into `to` (a fresh folder), same layout.
fn copy_tree(src: &Path, to: &Path) -> Result<Vec<(String, String)>, &'static str> {
    use sha2::Digest;
    let mut out = Vec::new();
    let mut bytes: u64 = 0;
    let mut dirs: Vec<(PathBuf, PathBuf, String)> =
        vec![(src.to_path_buf(), to.to_path_buf(), String::new())];
    while let Some((from, into, rel)) = dirs.pop() {
        let entries = std::fs::read_dir(&from).map_err(|_| "notes_copy_failed")?;
        for e in entries {
            let e = e.map_err(|_| "notes_copy_failed")?;
            let p = e.path();
            let meta = std::fs::symlink_metadata(&p).map_err(|_| "notes_copy_failed")?;
            let name_os = e.file_name();
            let name = name_os.to_string_lossy().into_owned();
            let cloud = is_cloud_placeholder(&name, &meta);
            if cloud {
                return Err("notes_cloud_placeholder");
            }
            if is_link(&meta) {
                continue;
            }
            let r = if rel.is_empty() {
                name.clone()
            } else {
                format!("{rel}/{name}")
            };
            if meta.is_dir() {
                let d = into.join(&name_os);
                std::fs::create_dir(&d).map_err(|_| "notes_copy_failed")?;
                dirs.push((p, d, r));
            } else if meta.is_file() {
                let mut buf = Vec::new();
                open_source(&p)
                    .map_err(|_| "notes_copy_failed")?
                    .take(MAX_FILE_BYTES + 1)
                    .read_to_end(&mut buf)
                    .map_err(|_| "notes_copy_failed")?;
                let len = buf.len() as u64;
                bytes = bytes.saturating_add(len);
                if len > MAX_FILE_BYTES || out.len() >= MAX_FILES || bytes > MAX_BYTES {
                    return Err("notes_over_limit");
                }
                let mut f = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(into.join(&name_os))
                    .map_err(|_| "notes_copy_failed")?;
                f.write_all(&buf).map_err(|_| "notes_copy_failed")?;
                f.sync_all().map_err(|_| "notes_copy_failed")?;
                let sha: String = sha2::Sha256::digest(&buf)
                    .iter()
                    .map(|x| format!("{x:02x}"))
                    .collect();
                out.push((r, sha));
            }
        }
    }
    out.sort();
    Ok(out)
}

/// Rename the finished hidden folder to the first free `merged-from-<tag>[-n]`.
fn place(staging: &Path, keep_dir: &Path, tag: &str) -> Result<String, &'static str> {
    let base = format!("merged-from-{}", plain_tag(tag));
    for n in 1..=NAME_TRIES {
        let name = if n == 1 {
            base.clone()
        } else {
            format!("{base}-{n}")
        };
        let at = keep_dir.join(&name);
        if !claim(&at)? {
            continue;
        }
        if std::fs::rename(staging, &at).is_err() {
            #[cfg(unix)]
            let _ = std::fs::remove_dir(&at);
            return Err("notes_copy_failed");
        }
        return Ok(name);
    }
    Err("notes_name_taken")
}

/// N2 (f163c REVIEW-macts da8e773f): take the name before the rename. On unix a rename onto
/// an empty folder replaces it, so a name checked free and then made by someone else would be
/// renamed over; creating it first is one step that fails when the name is there, and the
/// rename then only replaces the empty folder made here. Windows refuses a rename onto any
/// folder, so there a free name is enough (UNVERIFIED on a Windows machine).
fn claim(at: &Path) -> Result<bool, &'static str> {
    #[cfg(unix)]
    {
        match std::fs::create_dir(at) {
            Ok(()) => Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(false),
            Err(_) => Err("notes_copy_failed"),
        }
    }
    #[cfg(not(unix))]
    {
        match std::fs::symlink_metadata(at) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(true),
            Ok(_) => Ok(false),
            Err(_) => Err("notes_copy_failed"),
        }
    }
}

fn real_dir(p: &Path) -> Result<PathBuf, &'static str> {
    let m = std::fs::symlink_metadata(p).map_err(|_| "brain_folder_missing")?;
    if m.file_type().is_symlink() || !m.is_dir() {
        return Err("brain_path_unsafe");
    }
    std::fs::canonicalize(p).map_err(|_| "brain_path_unsafe")
}

/// Copy the notes of the brain at `src` into the kept brain at `keep_dir`, all or nothing.
pub fn copy_into(src: &Path, keep_dir: &Path, tag: &str) -> Result<Copied, &'static str> {
    let (s, k) = (real_dir(src)?, real_dir(keep_dir)?);
    if s.starts_with(&k) || k.starts_with(&s) {
        return Err("notes_nested");
    }
    let staging = k.join(format!(".merge-staging-{}", tail6()?));
    std::fs::create_dir(&staging).map_err(|_| "notes_copy_failed")?;
    let made = copy_tree(&s, &staging)
        .and_then(|files| place(&staging, &k, tag).map(|folder| Copied { folder, files }));
    if made.is_err() {
        let _ = std::fs::remove_dir_all(&staging);
    }
    made
}

/// Remove a copy just made (the run failed later): the whole folder, as it was made.
pub fn remove_copy(keep_dir: &Path, c: &Copied) {
    if !piece_ok(&c.folder) {
        return;
    }
    let p = keep_dir.join(&c.folder);
    if let Ok(m) = std::fs::symlink_metadata(&p) {
        if m.is_dir() && !m.file_type().is_symlink() {
            let _ = std::fs::remove_dir_all(&p);
        }
    }
}

fn piece_ok(p: &str) -> bool {
    !p.is_empty()
        && p != "."
        && p != ".."
        && !p.contains(['/', '\\', ':'])
        && !p.chars().any(char::is_control)
}

fn rel_ok(rel: &str) -> bool {
    rel.split('/').all(piece_ok)
}

/// Where a step's manifest lives under aibd's data folder.
pub fn manifest_path(data: &Path, batch_id: &str, seq: i64) -> Option<PathBuf> {
    piece_ok(batch_id).then(|| {
        data.join("archive")
            .join("merge-notes")
            .join(format!("{batch_id}-{seq}.json"))
    })
}

pub fn write_manifest(path: &Path, keep_dir_key: &str, c: &Copied) -> Result<(), &'static str> {
    let dir = path.parent().ok_or("notes_copy_failed")?;
    std::fs::create_dir_all(dir).map_err(|_| "notes_copy_failed")?;
    let mut v = c.to_json();
    v["keep_route"] = json!(keep_dir_key);
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| "notes_copy_failed")?;
    f.write_all(v.to_string().as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|_| "notes_copy_failed")
}

pub fn read_manifest(path: &Path) -> Option<(String, Copied)> {
    let v: Value = serde_json::from_slice(&std::fs::read(path).ok()?).ok()?;
    Some((
        v["keep_route"].as_str()?.to_string(),
        Copied::from_json(&v)?,
    ))
}

/// What undo did with one copy.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TookBack {
    pub moved: usize,
    pub gone: usize,
    pub changed: Vec<String>,
}

fn move_file(from: &Path, to: &Path) -> bool {
    if let Some(d) = to.parent() {
        if std::fs::create_dir_all(d).is_err() {
            return false;
        }
    }
    if std::fs::rename(from, to).is_ok() {
        return true;
    }
    // another volume (the brain in a cloud folder, aibd's data on the disk): copy, then remove
    std::fs::copy(from, to).is_ok() && std::fs::remove_file(from).is_ok()
}

/// Undo one copy: files still as copied go to `undo_dir`, changed ones stay and are listed,
/// then folders left empty are removed.
pub fn take_back(keep_dir: &Path, c: &Copied, undo_dir: &Path) -> TookBack {
    use sha2::Digest;
    let mut t = TookBack::default();
    if !piece_ok(&c.folder) {
        t.changed.push(c.folder.clone());
        return t;
    }
    let root = keep_dir.join(&c.folder);
    match std::fs::symlink_metadata(&root) {
        Ok(m) if m.is_dir() && !m.file_type().is_symlink() => {}
        Ok(_) => {
            t.changed.push(c.folder.clone());
            return t;
        }
        Err(_) => {
            t.gone = c.files.len();
            return t;
        }
    }
    for (rel, sha) in &c.files {
        if !rel_ok(rel) {
            t.changed.push(rel.clone());
            continue;
        }
        let p = root.join(rel);
        let Ok(m) = std::fs::symlink_metadata(&p) else {
            t.gone += 1;
            continue;
        };
        // N2: a file grown past what a copy may hold is not the copied one; never read it whole
        if !m.is_file() || is_link(&m) || m.len() > MAX_FILE_BYTES {
            t.changed.push(rel.clone());
            continue;
        }
        let same = open_source(&p)
            .and_then(|f| {
                let mut b = Vec::new();
                f.take(MAX_FILE_BYTES + 1).read_to_end(&mut b).map(|_| b)
            })
            .map(|b| {
                sha2::Sha256::digest(&b)
                    .iter()
                    .map(|x| format!("{x:02x}"))
                    .collect::<String>()
                    == *sha
            })
            .unwrap_or(false);
        if same && move_file(&p, &undo_dir.join(&c.folder).join(rel)) {
            t.moved += 1;
        } else {
            t.changed.push(rel.clone());
        }
    }
    remove_empty_dirs(&root);
    t
}

fn remove_empty_dirs(d: &Path) {
    if let Ok(entries) = std::fs::read_dir(d) {
        for e in entries.flatten() {
            let p = e.path();
            if let Ok(m) = std::fs::symlink_metadata(&p) {
                if m.is_dir() && !m.file_type().is_symlink() {
                    remove_empty_dirs(&p);
                }
            }
        }
    }
    let _ = std::fs::remove_dir(d);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let t = tempfile::tempdir().unwrap();
        let src = t.path().join("old-brain");
        let keep = t.path().join("kept-brain");
        std::fs::create_dir_all(src.join("sub")).unwrap();
        std::fs::create_dir_all(&keep).unwrap();
        std::fs::write(src.join("a.md"), "hello").unwrap();
        std::fs::write(src.join("sub").join("b.md"), "world").unwrap();
        (t, src, keep)
    }

    fn names(d: &Path) -> Vec<String> {
        let mut v: Vec<String> = std::fs::read_dir(d)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        v.sort();
        v
    }

    #[test]
    fn mg13_notes_are_copied_whole_into_a_new_folder_and_never_over_one() {
        let (_t, src, keep) = tree();
        let c = copy_into(&src, &keep, "old.brain").unwrap();
        assert_eq!(c.folder, "merged-from-old_brain");
        assert_eq!(c.files.len(), 2);
        assert_eq!(c.files[0].0, "a.md");
        assert_eq!(c.files[1].0, "sub/b.md");
        assert_eq!(
            std::fs::read_to_string(keep.join(&c.folder).join("sub").join("b.md")).unwrap(),
            "world"
        );
        // the merged brain's own folder is only read
        assert_eq!(names(&src), vec!["a.md", "sub"]);
        // a second copy takes the next free name; nothing hidden is left behind
        let c2 = copy_into(&src, &keep, "old.brain").unwrap();
        assert_eq!(c2.folder, "merged-from-old_brain-2");
        assert_eq!(
            names(&keep),
            vec!["merged-from-old_brain", "merged-from-old_brain-2"]
        );
        // the manifest reads back the same
        assert_eq!(Copied::from_json(&c.to_json()), Some(c));
    }

    #[test]
    fn mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing() {
        let (_t, src, keep) = tree();
        std::fs::write(src.join(".c.md.icloud"), "x").unwrap();
        assert_eq!(scan(&src).cloud_placeholders, 1);
        assert_eq!(copy_into(&src, &keep, "k"), Err("notes_cloud_placeholder"));
        assert!(names(&keep).is_empty(), "{:?}", names(&keep));

        std::fs::remove_file(src.join(".c.md.icloud")).unwrap();
        std::fs::write(
            src.join("big.md"),
            vec![b'x'; usize::try_from(MAX_FILE_BYTES).unwrap() + 1],
        )
        .unwrap();
        assert!(scan(&src).over_limit);
        assert_eq!(copy_into(&src, &keep, "k"), Err("notes_over_limit"));
        assert!(names(&keep).is_empty(), "{:?}", names(&keep));
    }

    #[test]
    fn mg13_a_kept_brain_inside_the_merged_one_is_refused() {
        let (_t, src, _keep) = tree();
        let inner = src.join("sub");
        assert_eq!(copy_into(&src, &inner, "k"), Err("notes_nested"));
        assert_eq!(copy_into(&inner, &src, "k"), Err("notes_nested"));
    }

    #[test]
    fn windows_cloud_only_attributes_are_placeholders_and_a_pinned_file_is_not() {
        assert!(attrs_say_cloud_only(0x0040_0000 | 0x20));
        assert!(attrs_say_cloud_only(0x0004_0000));
        assert!(attrs_say_cloud_only(0x1000));
        // FILE_ATTRIBUTE_PINNED | ARCHIVE | REPARSE_POINT: on this computer
        assert!(!attrs_say_cloud_only(0x0008_0000 | 0x20 | 0x400));
    }

    #[test]
    fn mg14_undo_takes_back_what_is_unchanged_and_lists_what_was_changed() {
        let (t, src, keep) = tree();
        let c = copy_into(&src, &keep, "k").unwrap();
        std::fs::write(keep.join(&c.folder).join("a.md"), "edited").unwrap();
        let undo = t.path().join("undo");
        let r = take_back(&keep, &c, &undo);
        assert_eq!(r.moved, 1);
        assert_eq!(r.changed, vec!["a.md".to_string()]);
        assert_eq!(
            std::fs::read_to_string(undo.join(&c.folder).join("sub").join("b.md")).unwrap(),
            "world"
        );
        // the changed file stays, the emptied sub folder is gone
        assert_eq!(names(&keep.join(&c.folder)), vec!["a.md"]);
    }

    #[test]
    fn a_manifest_is_written_once_and_reads_back() {
        let (t, src, keep) = tree();
        let c = copy_into(&src, &keep, "k").unwrap();
        let p = manifest_path(t.path(), "mb_x", 3).unwrap();
        write_manifest(&p, "route-k", &c).unwrap();
        assert_eq!(read_manifest(&p), Some(("route-k".to_string(), c.clone())));
        assert_eq!(write_manifest(&p, "route-k", &c), Err("notes_copy_failed"));
        assert_eq!(manifest_path(t.path(), "../x", 1), None);
    }

    #[cfg(unix)]
    #[test]
    fn an_empty_folder_already_there_is_never_renamed_over() {
        let t = tempfile::tempdir().unwrap();
        let (from, keep) = (t.path().join("from"), t.path().join("keep"));
        std::fs::create_dir_all(&from).unwrap();
        std::fs::create_dir_all(keep.join("merged-from-rk")).unwrap();
        std::fs::write(from.join("a.md"), "a").unwrap();
        let c = copy_into(&from, &keep, "rk").unwrap();
        assert_eq!(c.folder, "merged-from-rk-2");
        assert_eq!(
            std::fs::read_dir(keep.join("merged-from-rk"))
                .unwrap()
                .count(),
            0
        );
    }

    // ebab8 11-25 19:16: on Windows the same is checked with a junction below
    #[cfg(unix)]
    #[test]
    fn links_are_not_followed() {
        let (t, src, keep) = tree();
        let outside = t.path().join("outside.md");
        std::fs::write(&outside, "secret").unwrap();
        std::os::unix::fs::symlink(&outside, src.join("link.md")).unwrap();
        let c = copy_into(&src, &keep, "k").unwrap();
        assert!(c.files.iter().all(|(r, _)| r != "link.md"), "{c:?}");
        assert!(!keep.join(&c.folder).join("link.md").exists());
    }

    /// A junction needs no privilege (a directory symlink does); made with cmd's built-in
    /// mklink /J, test only. Its target's file must not be copied, counted, or taken back.
    #[cfg(windows)]
    #[test]
    fn a_junction_is_not_followed_on_windows() {
        let (t, src, keep) = tree();
        let outside = t.path().join("outside");
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("secret.md"), "secret").unwrap();
        let j = src.join("jdir");
        let ok = std::process::Command::new("cmd")
            .arg("/C")
            .arg("mklink")
            .arg("/J")
            .arg(&j)
            .arg(&outside)
            .status()
            .unwrap()
            .success();
        assert!(ok, "mklink /J failed");
        assert!(is_link(&std::fs::symlink_metadata(&j).unwrap()));
        let before = scan(&src);
        let c = copy_into(&src, &keep, "k").unwrap();
        assert!(c.files.iter().all(|(r, _)| !r.starts_with("jdir")), "{c:?}");
        assert!(!keep.join(&c.folder).join("jdir").exists());
        assert_eq!(before.files, c.files.len(), "{before:?}");
    }
}
