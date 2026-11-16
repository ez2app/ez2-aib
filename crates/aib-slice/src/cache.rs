//! The sweep's git cache (S79-slice-incr piece 1, DESIGN-slice-incremental-1ced7-1117 r1).
//!
//! A git command whose every revision argument is an object id (a commit, a tree) answers the
//! same for ever: commits do not change. Such a command's output is kept here, keyed by the
//! command line itself, so a sweep that sees the same heads asks git nothing it asked before.
//! That covers the per-branch facts (layer B of the design) and the pairwise merge-trees
//! (layer C) without a hand-written key that could miss an input: a different head, a different
//! main or a different PR head is a different command line.
//!
//! A command that reads a ref (`origin/main`, a branch name, `--all`, `for-each-ref`, `status`,
//! `worktree`) is never cached. Only successful answers are kept; an error is asked again.
//! The file is written whole (a temporary file, then a rename, which replaces the old file on
//! Windows too); a file that cannot be read, or one written by another git or schema, counts as
//! empty, never as an error.

use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

pub const SCHEMA: &str = "aib-slice-cache.v1";
/// Outputs larger than this are not kept (a whole workflow file, ~280 KB, asked per branch
/// would make the file large for little gain).
pub const MAX_VALUE: usize = 64 * 1024;
/// The switch that turns the cache off (`0`), read with `--no-cache` (design §4).
pub const ENV_OFF: &str = "AIB_SLICE_INCREMENTAL";

/// git subcommands that read only objects (given object ids).
const PURE: &[&str] = &[
    "rev-list",
    "merge-tree",
    "diff",
    "ls-tree",
    "show",
    "cherry",
    "merge-base",
    "cat-file",
];

/// Options that make a pure subcommand read refs.
const READS_REFS: &[&str] = &[
    "--all",
    "--branches",
    "--tags",
    "--remotes",
    "--glob",
    "--exclude",
    "--reflog",
    "--stdin",
    "--bisect",
];

/// Options whose answer depends on which objects this clone has, not only on the ids named:
/// `--ignore-missing` answers "no" for a head not fetched yet, and that "no" would be kept
/// until the key changed (5d33a c8a97748 M1: scan.rs merges / covered-by queries).
const READS_STORE: &[&str] = &["--ignore-missing"];

/// 40 (sha1) or 64 (sha256) lower-case hex digits.
pub fn is_oid(s: &str) -> bool {
    (s.len() == 40 || s.len() == 64) && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

fn is_rev(a: &str) -> bool {
    let a = a.strip_prefix('^').unwrap_or(a);
    if is_oid(a) {
        return true;
    }
    if let Some((x, y)) = a.split_once("...").or_else(|| a.split_once("..")) {
        return is_oid(x) && is_oid(y);
    }
    // `<oid>:<path>` (git show)
    a.split_once(':').is_some_and(|(x, _)| is_oid(x))
}

/// The command reads only objects named by id: its answer never changes.
pub fn pure(args: &[&str]) -> bool {
    let Some((cmd, rest)) = args.split_first() else {
        return false;
    };
    if !PURE.contains(cmd) {
        return false;
    }
    let mut paths = false;
    let mut revs = 0;
    for a in rest {
        if paths {
            continue;
        }
        if *a == "--" {
            paths = true;
        } else if a.starts_with('-') {
            let name = a.split('=').next().unwrap_or(a);
            if READS_REFS.contains(&name) || READS_STORE.contains(&name) {
                return false;
            }
        } else if is_rev(a) {
            revs += 1;
        } else {
            return false;
        }
    }
    revs > 0
}

fn key(args: &[&str]) -> String {
    args.join("\u{1f}")
}

/// The cache of one sweep: what was read from the file, what this sweep asked.
#[derive(Debug, Default)]
pub struct Cache {
    /// `git --version` the entries were made with; another version = empty
    pub git: String,
    map: BTreeMap<String, String>,
    used: BTreeSet<String>,
    pub hits: u64,
    pub misses: u64,
    changed: bool,
    /// when the file was last written (or the cache made): see [`Cache::flush`]
    flushed: Option<std::time::Instant>,
}

impl Cache {
    pub fn new(git: &str) -> Self {
        Cache {
            git: git.to_owned(),
            flushed: Some(std::time::Instant::now()),
            ..Cache::default()
        }
    }

    /// Reads the file; anything unreadable, another schema or another git = empty.
    pub fn load(path: &Path, git: &str) -> Self {
        let mut c = Cache::new(git);
        let Ok(text) = std::fs::read_to_string(path) else {
            return c;
        };
        let Ok(v) = serde_json::from_str::<Value>(&text) else {
            return c;
        };
        if v["schema"] != SCHEMA || v["git"] != git {
            return c;
        }
        if let Some(m) = v["entries"].as_object() {
            for (k, out) in m {
                if let Some(s) = out.as_str() {
                    c.map.insert(k.clone(), s.to_owned());
                }
            }
        }
        c
    }

    /// The kept answer of a pure command.
    pub fn get(&mut self, args: &[&str]) -> Option<String> {
        if !pure(args) {
            return None;
        }
        let k = key(args);
        match self.map.get(&k) {
            Some(v) => {
                self.hits += 1;
                self.used.insert(k);
                Some(v.clone())
            }
            None => {
                self.misses += 1;
                None
            }
        }
    }

    /// Keeps the answer of a pure command that succeeded.
    pub fn put(&mut self, args: &[&str], out: &str) {
        if !pure(args) || out.len() > MAX_VALUE {
            return;
        }
        let k = key(args);
        self.used.insert(k.clone());
        self.map.insert(k, out.to_owned());
        self.changed = true;
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// The file's JSON. `prune` = keep only what this sweep used (a whole sweep visits every
    /// branch; a `--seat` / `--fast` one does not, so it keeps everything).
    pub fn to_json(&self, prune: bool) -> Value {
        let mut m = Map::new();
        for (k, v) in &self.map {
            if !prune || self.used.contains(k) {
                m.insert(k.clone(), Value::String(v.clone()));
            }
        }
        json!({"schema": SCHEMA, "git": self.git, "entries": m})
    }

    /// Writes the file whole when something changed or `prune` drops entries.
    pub fn save(&self, path: &Path, prune: bool) -> Result<(), String> {
        let drops = prune && self.used.len() < self.map.len();
        if !self.changed && !drops {
            return Ok(());
        }
        let tmp = tmp_path(path);
        let text = format!("{}\n", self.to_json(prune));
        std::fs::write(&tmp, text).map_err(|e| format!("cache {}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, path).map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            format!("cache {}: {e}", path.display())
        })
    }
}

impl Cache {
    /// Writes what is new so far, unpruned, once `every` has passed since the last write: a
    /// sweep killed at a time limit (aib-drive refresh stops it at 590 s, a whole sweep took
    /// 650 s on 11-17) still leaves what it asked, so the next one is shorter. Returns whether
    /// it wrote.
    pub fn flush(&mut self, path: &Path, every: std::time::Duration) -> Result<bool, String> {
        let due = self.flushed.is_none_or(|t| t.elapsed() >= every);
        if !self.changed || !due {
            return Ok(false);
        }
        self.save(path, false)?;
        self.changed = false;
        self.flushed = Some(std::time::Instant::now());
        Ok(true)
    }
}

/// How often a sweep writes its cache while it runs.
pub const FLUSH_EVERY: std::time::Duration = std::time::Duration::from_secs(60);

fn tmp_path(path: &Path) -> PathBuf {
    let mut name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    name.push_str(&format!(".tmp-{}", std::process::id()));
    path.with_file_name(name)
}

/// What the file's entries were made under: the git version and a sha256 of every config
/// value (`git config --list`). Any change empties the cache: a setting such as diff.renames or
/// merge.conflictStyle changes answers, and telling which do is not worth the risk.
pub fn fingerprint(version: &str, config: &str) -> String {
    use sha2::{Digest, Sha256};
    let d = Sha256::digest(config.replace('\r', "").as_bytes());
    let hex: String = d.iter().map(|b| format!("{b:02x}")).collect();
    format!("{version} config:{hex}")
}

/// The default cache file: `slice-cache.v1.json` beside the sweep's `--out` file (the drive
/// folder aib-drive gives it), else none.
pub fn beside(out: &Path) -> Option<PathBuf> {
    out.parent()
        .filter(|p| !p.as_os_str().is_empty())
        .map(|p| p.join("slice-cache.v1.json"))
}

/// The switch: `AIB_SLICE_INCREMENTAL=0` turns the cache off.
pub fn off_by_env(v: Option<&str>) -> bool {
    v.is_some_and(|v| v.trim() == "0")
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "0123456789abcdef0123456789abcdef01234567";
    const B: &str = "89abcdef0123456789abcdef0123456789abcdef";

    #[test]
    fn only_commands_naming_objects_by_id_are_pure() {
        let ab = format!("{A}..{B}");
        let abc = format!("{A}...{B}");
        let not_a = format!("^{A}");
        let show = format!("{A}:.github/workflows/aibd.yml");
        assert!(pure(&["rev-list", &ab]));
        assert!(pure(&["rev-list", "--no-merges", &ab, &not_a]));
        assert!(pure(&["diff", "--name-status", "--no-renames", &abc]));
        assert!(pure(&["merge-tree", "--write-tree", "--name-only", A, B]));
        assert!(pure(&["show", &show]));
        assert!(pure(&["ls-tree", "-l", "-r", A, "--", ".github/workflows"]));
        assert!(pure(&["cherry", A, B, A]));
        // a ref name, a ref-reading option, a subcommand that reads refs, or no revision
        assert!(!pure(&["rev-list", "origin/main..feat/x"]));
        assert!(!pure(&["rev-list", A, "HEAD"]));
        assert!(!pure(&["rev-list", "--all", &not_a]));
        // the answer depends on which objects are here (5d33a c8a97748 M1)
        assert!(!pure(&[
            "rev-list",
            "--no-walk",
            "--merges",
            "--ignore-missing",
            A
        ]));
        assert!(!pure(&[
            "rev-list",
            "--ignore-missing",
            "--ancestry-path",
            &not_a,
            B
        ]));
        assert!(!pure(&["for-each-ref", "--contains", A]));
        assert!(!pure(&["status", "--porcelain"]));
        assert!(!pure(&[
            "rev-parse",
            "--verify",
            "-q",
            "origin/main^{commit}"
        ]));
        assert!(!pure(&["diff", "--name-only"]));
        assert!(
            !pure(&["show", "abc1234:a.rs"]),
            "a short id can come to mean another commit"
        );
        assert!(!pure(&[]));
    }

    #[test]
    fn a_kept_answer_comes_back_and_a_ref_command_is_never_kept() {
        let mut c = Cache::new("git version 2.50.1");
        let ab = format!("{A}..{B}");
        assert_eq!(c.get(&["rev-list", &ab]), None);
        c.put(&["rev-list", &ab], "c1\nc2\n");
        assert_eq!(c.get(&["rev-list", &ab]).as_deref(), Some("c1\nc2\n"));
        assert_eq!((c.hits, c.misses), (1, 1));
        c.put(&["for-each-ref", "refs/heads"], "x");
        assert_eq!(c.get(&["for-each-ref", "refs/heads"]), None);
        assert_eq!(c.len(), 1);
        c.put(&["show", &format!("{A}:big")], &"x".repeat(MAX_VALUE + 1));
        assert_eq!(c.len(), 1, "an output over MAX_VALUE is not kept");
    }

    #[test]
    fn the_file_round_trips_and_another_git_or_a_bad_file_is_empty() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("slice-cache.v1.json");
        let ab = format!("{A}..{B}");
        let mut c = Cache::new("git version 2.50.1");
        c.put(&["rev-list", &ab], "c1\n");
        c.save(&p, false).unwrap();
        let mut back = Cache::load(&p, "git version 2.50.1");
        assert_eq!(back.get(&["rev-list", &ab]).as_deref(), Some("c1\n"));
        assert!(Cache::load(&p, "git version 2.51.0").is_empty());
        std::fs::write(&p, "{not json").unwrap();
        assert!(Cache::load(&p, "git version 2.50.1").is_empty());
        assert!(Cache::load(&d.path().join("none.json"), "g").is_empty());
        // no temporary file left behind
        let left: Vec<_> = std::fs::read_dir(d.path()).unwrap().collect();
        assert_eq!(left.len(), 1);
    }

    #[test]
    fn a_whole_sweep_keeps_only_what_it_used() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("c.json");
        let (ab, ba) = (format!("{A}..{B}"), format!("{B}..{A}"));
        let mut c = Cache::new("g");
        c.put(&["rev-list", &ab], "1");
        c.put(&["rev-list", &ba], "2");
        c.save(&p, false).unwrap();
        let mut next = Cache::load(&p, "g");
        assert!(next.get(&["rev-list", &ab]).is_some());
        next.save(&p, true).unwrap();
        let mut last = Cache::load(&p, "g");
        assert!(last.get(&["rev-list", &ab]).is_some());
        assert!(
            last.get(&["rev-list", &ba]).is_none(),
            "unused entry pruned"
        );
    }

    #[test]
    fn a_sweep_cut_off_keeps_what_it_flushed() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("c.json");
        let ab = format!("{A}..{B}");
        let mut c = Cache::new("g");
        let zero = std::time::Duration::ZERO;
        assert!(!c.flush(&p, zero).unwrap(), "nothing new: nothing written");
        c.put(&["rev-list", &ab], "1");
        assert!(
            !c.flush(&p, std::time::Duration::from_secs(3600)).unwrap(),
            "not due yet"
        );
        assert!(c.flush(&p, zero).unwrap());
        // the sweep dies here: the next one reads what was flushed
        let mut next = Cache::load(&p, "g");
        assert_eq!(next.get(&["rev-list", &ab]).as_deref(), Some("1"));
        std::fs::remove_file(&p).unwrap();
        assert!(
            !c.flush(&p, zero).unwrap(),
            "nothing new since the last write"
        );
    }

    #[test]
    fn another_config_value_is_another_cache() {
        let a = fingerprint("git version 2.50.1", "diff.renames=true\n");
        assert_eq!(
            a,
            fingerprint("git version 2.50.1", "diff.renames=true\r\n")
        );
        assert_ne!(a, fingerprint("git version 2.50.1", "diff.renames=false\n"));
        assert_ne!(a, fingerprint("git version 2.51.0", "diff.renames=true\n"));
    }

    #[test]
    fn the_cache_sits_beside_the_out_file_and_the_switch_reads_zero() {
        assert_eq!(
            beside(Path::new("/r/drive/slice-sweep.json")),
            Some(PathBuf::from("/r/drive/slice-cache.v1.json"))
        );
        assert_eq!(beside(Path::new("slice-sweep.json")), None);
        assert!(off_by_env(Some("0")));
        assert!(!off_by_env(Some("1")));
        assert!(!off_by_env(None));
    }
}
