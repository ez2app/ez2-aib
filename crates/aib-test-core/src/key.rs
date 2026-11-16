//! The key, read from the files on disk (r7 §1.2, SPEC r2 A). The git index is not used as a
//! shortcut in this version; a modification time only says "read it again", never "same".
//!
//! key = sha256(rule version ‖ unit definition ‖ context ‖ input list), each part a sorted
//! "name → value" list. When any part cannot be worked out the answer is
//! [`Unmeasurable`]: the unit runs for real, it never falls back to a weaker key.

use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime};

/// Bump when the way a key is made changes: every old source stops matching.
pub const RULE_VERSION: &str = "aib-test-key/1";

/// Why no key could be made. Each is a reason to run for real, written in the ledger.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Unmeasurable {
    /// Not inside a git work tree (first version: git only).
    NotAGitRepo,
    /// `git ls-files` failed.
    GitFailed(String),
    /// Two paths differ only in letter case.
    CaseAmbiguous(String, String),
    /// A file could not be read.
    Unreadable(String),
    /// A declared path matched nothing: that is "not measured", not "unchanged".
    NoPathMatched(String),
}

/// A sha256 as 64 lower-case hex digits.
pub type Hex = String;

fn hex(d: &[u8]) -> Hex {
    let mut s = String::with_capacity(d.len() * 2);
    for b in d {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// The sha256 of a sorted name → value list, one `name\0value\n` per entry.
pub fn hash_map(m: &BTreeMap<String, String>) -> Hex {
    let mut h = Sha256::new();
    for (k, v) in m {
        h.update(k.as_bytes());
        h.update([0]);
        h.update(v.as_bytes());
        h.update(b"\n");
    }
    hex(&h.finalize())
}

/// The key from its four parts.
pub fn key(unit_def: &Hex, context: &Hex, inputs: &Hex) -> Hex {
    let mut m = BTreeMap::new();
    m.insert("rule".to_string(), RULE_VERSION.to_string());
    m.insert("unit".to_string(), unit_def.clone());
    m.insert("context".to_string(), context.clone());
    m.insert("inputs".to_string(), inputs.clone());
    hash_map(&m)
}

/// The repo root, whatever folder we were started in (always from the root, r7 §1.2 2).
pub fn repo_root(start: &Path) -> Result<PathBuf, Unmeasurable> {
    let out = Command::new("git")
        .arg("-C")
        .arg(start)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .map_err(|e| Unmeasurable::GitFailed(e.to_string()))?;
    if !out.status.success() {
        return Err(Unmeasurable::NotAGitRepo);
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        return Err(Unmeasurable::NotAGitRepo);
    }
    Ok(PathBuf::from(s))
}

/// The wide default: tracked files plus untracked files that are not ignored, relative to
/// the root with `/`, sorted, no duplicates.
pub fn wide_paths(root: &Path) -> Result<Vec<String>, Unmeasurable> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args([
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ])
        .output()
        .map_err(|e| Unmeasurable::GitFailed(e.to_string()))?;
    if !out.status.success() {
        return Err(Unmeasurable::GitFailed(
            String::from_utf8_lossy(&out.stderr).trim().to_string(),
        ));
    }
    let mut v: Vec<String> = out
        .stdout
        .split(|b| *b == 0)
        .filter(|p| !p.is_empty())
        .map(|p| String::from_utf8_lossy(p).replace('\\', "/"))
        .collect();
    v.sort();
    v.dedup();
    case_check(&v)?;
    Ok(v)
}

/// Two paths that differ only in case cannot be told apart on some disks: not measured.
pub fn case_check(paths: &[String]) -> Result<(), Unmeasurable> {
    let mut seen: HashMap<String, &String> = HashMap::new();
    for p in paths {
        if let Some(other) = seen.insert(p.to_lowercase(), p) {
            if other != p {
                return Err(Unmeasurable::CaseAmbiguous(other.clone(), p.clone()));
            }
        }
    }
    Ok(())
}

/// What we remember about a file to decide whether to read it again. Any field that differs
/// ⇒ read again. It is never proof of "same".
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Stamp {
    len: u64,
    mtime_ns: u128,
    #[cfg(unix)]
    dev: u64,
    #[cfg(unix)]
    ino: u64,
    #[cfg(unix)]
    ctime_ns: i128,
}

fn stamp(md: &fs::Metadata) -> Option<Stamp> {
    let mtime_ns = md
        .modified()
        .ok()?
        .duration_since(SystemTime::UNIX_EPOCH)
        .ok()?
        .as_nanos();
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Some(Stamp {
            len: md.len(),
            mtime_ns,
            dev: md.dev(),
            ino: md.ino(),
            ctime_ns: i128::from(md.ctime()) * 1_000_000_000 + i128::from(md.ctime_nsec()),
        })
    }
    #[cfg(not(unix))]
    {
        Some(Stamp {
            len: md.len(),
            mtime_ns,
        })
    }
}

/// Path → (stamp, value). A file changed within [`RACY`] of the scan start is never kept:
/// two writes in one clock tick look the same (r7 §1.2 1).
#[derive(Default)]
pub struct FileCache {
    seen: HashMap<String, (Stamp, String)>,
    /// How many values came from the cache in the last [`input_list`] (for the ledger).
    pub hits: usize,
    /// How many files were read.
    pub reads: usize,
}

/// Changed this close to the scan start ⇒ not cached.
pub const RACY: Duration = Duration::from_secs(2);

/// One file's value: kind ‖ permission bits ‖ content. A link is its target text (not
/// followed). A missing file is the word `missing` (a value, not a skip).
fn file_value(
    root: &Path,
    rel: &str,
    cache: &mut FileCache,
    scan_start: SystemTime,
) -> Result<String, Unmeasurable> {
    let p = root.join(rel);
    let md = match fs::symlink_metadata(&p) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok("missing".into()),
        Err(e) => return Err(Unmeasurable::Unreadable(format!("{rel}: {e}"))),
    };
    let ft = md.file_type();
    if ft.is_symlink() {
        let t = fs::read_link(&p).map_err(|e| Unmeasurable::Unreadable(format!("{rel}: {e}")))?;
        return Ok(format!("link:{}", t.to_string_lossy()));
    }
    if ft.is_dir() {
        // a submodule shows up as a folder; its state is checked by the caller (SPEC A1.3)
        return Ok("dir".into());
    }
    let st = stamp(&md);
    if let Some(st) = &st {
        if let Some((old, v)) = cache.seen.get(rel) {
            if old == st {
                cache.hits += 1;
                return Ok(v.clone());
            }
        }
    }
    let mut f = fs::File::open(&p).map_err(|e| Unmeasurable::Unreadable(format!("{rel}: {e}")))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = f
            .read(&mut buf)
            .map_err(|e| Unmeasurable::Unreadable(format!("{rel}: {e}")))?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    cache.reads += 1;
    let v = format!("file:{}:{}", mode(&md), hex(&h.finalize()));
    let fresh = md
        .modified()
        .ok()
        .and_then(|m| scan_start.duration_since(m).ok())
        .is_none_or(|age| age < RACY);
    if let (Some(st), false) = (st, fresh) {
        cache.seen.insert(rel.to_string(), (st, v.clone()));
    }
    Ok(v)
}

#[cfg(unix)]
fn mode(md: &fs::Metadata) -> String {
    use std::os::unix::fs::PermissionsExt;
    // only the executable bits matter to git and to a run
    if md.permissions().mode() & 0o111 != 0 {
        "x".into()
    } else {
        "-".into()
    }
}

#[cfg(not(unix))]
fn mode(md: &fs::Metadata) -> String {
    if md.permissions().readonly() {
        "r".into()
    } else {
        "-".into()
    }
}

/// The input list hash of these paths (relative to `root`). Every path is read again unless
/// the cache stamp matches; [`FileCache::hits`] / [`FileCache::reads`] say which.
pub fn input_list(
    root: &Path,
    paths: &[String],
    cache: &mut FileCache,
) -> Result<Hex, Unmeasurable> {
    cache.hits = 0;
    cache.reads = 0;
    let scan_start = SystemTime::now();
    let mut m = BTreeMap::new();
    for rel in paths {
        m.insert(rel.clone(), file_value(root, rel, cache, scan_start)?);
    }
    Ok(hash_map(&m))
}

/// Narrow `all` to the paths under the declared prefixes (relative to the root, `/`).
/// A prefix that matches nothing is [`Unmeasurable::NoPathMatched`].
pub fn declared(all: &[String], prefixes: &[String]) -> Result<Vec<String>, Unmeasurable> {
    let mut out = Vec::new();
    for pre in prefixes {
        let pre = pre.trim_start_matches("./").trim_end_matches('/');
        let hit: Vec<&String> = all
            .iter()
            .filter(|p| p.as_str() == pre || p.starts_with(&format!("{pre}/")))
            .collect();
        if hit.is_empty() {
            return Err(Unmeasurable::NoPathMatched(pre.to_string()));
        }
        out.extend(hit.into_iter().cloned());
    }
    out.sort();
    out.dedup();
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Tmp(PathBuf);
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn tmp(name: &str) -> Tmp {
        let d = std::env::temp_dir().join(format!("aib-test-core-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        Tmp(d)
    }
    fn git(d: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .arg("-C")
            .arg(d)
            .args(args)
            .output()
            .unwrap()
            .status
            .success();
        assert!(ok, "git {args:?}");
    }
    fn repo(name: &str) -> Tmp {
        let t = tmp(name);
        git(&t.0, &["init", "-q"]);
        fs::write(t.0.join(".gitignore"), "target/\n").unwrap();
        fs::create_dir_all(t.0.join("src")).unwrap();
        fs::write(t.0.join("src/a.rs"), "fn a() {}\n").unwrap();
        fs::write(t.0.join("README"), "r\n").unwrap();
        git(&t.0, &["add", "."]);
        t
    }

    #[test]
    fn the_root_is_the_same_from_a_sub_folder_and_paths_are_from_the_root() {
        let t = repo("root");
        let from_sub = repo_root(&t.0.join("src")).unwrap();
        assert_eq!(
            fs::canonicalize(from_sub).unwrap(),
            fs::canonicalize(&t.0).unwrap()
        );
        let p = wide_paths(&t.0).unwrap();
        assert_eq!(p, vec![".gitignore", "README", "src/a.rs"]);
    }

    #[test]
    fn untracked_files_count_and_ignored_ones_do_not() {
        let t = repo("untracked");
        fs::write(t.0.join("new.txt"), "n\n").unwrap();
        fs::create_dir_all(t.0.join("target")).unwrap();
        fs::write(t.0.join("target/out"), "o\n").unwrap();
        let p = wide_paths(&t.0).unwrap();
        assert!(p.contains(&"new.txt".to_string()));
        assert!(!p.iter().any(|x| x.starts_with("target/")));
    }

    #[test]
    fn a_change_on_disk_changes_the_key_even_when_the_index_says_clean() {
        let t = repo("disk");
        git(&t.0, &["update-index", "--assume-unchanged", "src/a.rs"]);
        let paths = wide_paths(&t.0).unwrap();
        let mut c = FileCache::default();
        let before = input_list(&t.0, &paths, &mut c).unwrap();
        fs::write(t.0.join("src/a.rs"), "fn a() { 1; }\n").unwrap();
        let after = input_list(&t.0, &paths, &mut c).unwrap();
        assert_ne!(before, after);
    }

    #[test]
    fn a_same_size_rewrite_keeping_the_old_time_is_still_seen() {
        let t = repo("racy");
        let paths = wide_paths(&t.0).unwrap();
        let mut c = FileCache::default();
        let before = input_list(&t.0, &paths, &mut c).unwrap();
        // same length, written right away: inside RACY, so nothing was cached to fool us
        fs::write(t.0.join("src/a.rs"), "fn b() {}\n").unwrap();
        let after = input_list(&t.0, &paths, &mut c).unwrap();
        assert_ne!(before, after);
        assert_eq!(
            c.hits, 0,
            "files changed within {RACY:?} are never served from the cache"
        );
    }

    #[test]
    fn a_missing_file_is_a_value_and_a_link_is_its_target() {
        let t = repo("missing");
        let paths = wide_paths(&t.0).unwrap();
        let mut c = FileCache::default();
        let with = input_list(&t.0, &paths, &mut c).unwrap();
        fs::remove_file(t.0.join("README")).unwrap();
        let without = input_list(&t.0, &paths, &mut c).unwrap();
        assert_ne!(with, without);
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink("src/a.rs", t.0.join("l")).unwrap();
            let v = file_value(&t.0, "l", &mut c, SystemTime::now()).unwrap();
            assert_eq!(v, "link:src/a.rs");
        }
    }

    #[cfg(unix)]
    #[test]
    fn the_executable_bit_changes_the_key() {
        use std::os::unix::fs::PermissionsExt;
        let t = repo("mode");
        let paths = wide_paths(&t.0).unwrap();
        let mut c = FileCache::default();
        let before = input_list(&t.0, &paths, &mut c).unwrap();
        fs::set_permissions(t.0.join("src/a.rs"), fs::Permissions::from_mode(0o755)).unwrap();
        let after = input_list(&t.0, &paths, &mut c).unwrap();
        assert_ne!(before, after);
    }

    #[test]
    fn paths_differing_only_in_case_are_not_measured() {
        let v = vec!["src/A.rs".to_string(), "src/a.rs".to_string()];
        assert!(matches!(
            case_check(&v),
            Err(Unmeasurable::CaseAmbiguous(_, _))
        ));
    }

    #[test]
    fn a_declared_path_that_matches_nothing_is_not_measured() {
        let all = vec![
            "src/a.rs".to_string(),
            "src/b/c.rs".to_string(),
            "srcx/d".to_string(),
        ];
        assert_eq!(
            declared(&all, &["src/b".into()]).unwrap(),
            vec!["src/b/c.rs"]
        );
        assert_eq!(
            declared(&all, &["./src/".into()]).unwrap(),
            vec!["src/a.rs", "src/b/c.rs"]
        );
        assert_eq!(
            declared(&all, &["nope".into()]),
            Err(Unmeasurable::NoPathMatched("nope".into()))
        );
    }

    #[test]
    fn outside_a_git_repo_there_is_no_key() {
        let t = tmp("nogit");
        assert_eq!(repo_root(&t.0), Err(Unmeasurable::NotAGitRepo));
    }

    #[test]
    fn the_key_moves_with_each_part() {
        let a = "a".to_string();
        let b = "b".to_string();
        let k = key(&a, &a, &a);
        assert_ne!(k, key(&b, &a, &a));
        assert_ne!(k, key(&a, &b, &a));
        assert_ne!(k, key(&a, &a, &b));
        assert_eq!(k, key(&a, &a, &a));
        assert_eq!(k.len(), 64);
    }
}
