//! N-webonly 第 4 件, b4297's half, slice R4a: the bundle of one run and what its head
//! brings, made without running git in the seat's copy.
//!
//! Like 送回倉 ([`crate::send_back`], 5d33a M1 there): the seat writes its copy's
//! `.git/config` and `objects/info/alternates`, so no git runs in the copy. Its object files
//! are copied, as plain files, into a bare scratch repository aibd made itself, the head is
//! checked there, and everything below runs in the scratch one.
//!
//! - The base is where the head left the registered repo's `origin/main` (their merge
//!   base): what a test machine's own copy of the public main surely holds, so the bundle
//!   only carries the seat's commits. The repo's main may have moved on since the copy was
//!   opened, so the scratch repository also reads the registered repo's object files (an
//!   `alternates` line aibd writes there: files read, none of that repo's settings). A head
//!   that shares no history with main is refused.
//! - What the head changes since the base, and the two `Cargo.lock`s, are read from git's
//!   objects and judged by [`crate::receipt_runner::brings`] before a bundle is written: a
//!   refused head leaves no file.
//! - The bundle holds one ref ([`aib_receipt_gate::plan::BUNDLE_REF`]) on the head; its
//!   length and SHA-256 are what the owner's fingerprint binds and what the gate checks on
//!   arrival.

use crate::receipt_runner::{brings, Brings};
use crate::seat_worktree::{git, repo_safe};
use crate::send_back::{sha_ok, stage, Refused};
use aib_receipt_gate::plan::BUNDLE_REF;
use sha2::{Digest, Sha256};
use std::path::Path;

/// The registered repo's ref the base is taken from.
pub const MAIN_REF: &str = "refs/remotes/origin/main";

/// A bundle that was made.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Made {
    /// The commit the bundle is made against (40 hex).
    pub base: String,
    pub len: u64,
    pub sha256: String,
    pub brings: Brings,
}

/// `git diff --name-status --no-renames -z` output as `(status letter, path)`.
pub fn status_entries(z: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut it = z.split('\0');
    while let (Some(status), Some(path)) = (it.next(), it.next()) {
        let status = status.trim_start_matches('\n');
        if !status.is_empty() && !path.is_empty() {
            out.push((status.chars().take(1).collect(), path.to_owned()));
        }
    }
    out
}

fn sha256_of(p: &Path) -> Option<(u64, String)> {
    let bytes = std::fs::read(p).ok()?;
    let hex = Sha256::digest(&bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    Some((bytes.len() as u64, hex))
}

fn make_in(
    repo: &Path,
    tree: &Path,
    head: &str,
    hooks: &Path,
    scratch: &Path,
    bundle: &Path,
) -> Result<Made, &'static str> {
    if !sha_ok(head) {
        return Err("head_not_seat_head");
    }
    // the repo's own main, read in the registered repo (never beside the copy)
    repo_safe(repo).map_err(|_| "receipt_not_allowed")?;
    let rev = format!("{MAIN_REF}^{{commit}}");
    let main = git(repo, hooks, &["rev-parse", "--verify", "-q", &rev])
        .map_err(|_| "receipt_not_allowed")?;
    if !sha_ok(&main) {
        return Err("receipt_not_allowed");
    }
    stage(tree, head, hooks, scratch).map_err(|r| match r {
        Refused::ObjectsMissing(_) => "head_not_seat_head",
        _ => "tree_refused",
    })?;
    // the repo's main may be newer than the copy: the scratch repository reads the
    // registered repo's objects too (plain files; that repo passed repo_safe above)
    let objects = repo.join(".git").join("objects");
    let line = objects
        .to_str()
        .filter(|s| !s.contains('\n'))
        .ok_or("refused")?;
    std::fs::write(
        scratch.join("objects").join("info").join("alternates"),
        format!("{line}\n"),
    )
    .map_err(|_| "refused")?;
    // where the head left the repo's main
    let base = git(scratch, hooks, &["merge-base", &main, head]).map_err(|_| "tree_refused")?;
    if !sha_ok(&base) {
        return Err("tree_refused");
    }
    // no symbolic link or submodule anywhere in the head (the gate refuses the same: its
    // fetch reads every Cargo.toml of the tree; 5d33a PREREAD-receiptrunner M1)
    let modes = git(scratch, hooks, &["ls-tree", "-r", "-z", head]).map_err(|_| "tree_refused")?;
    aib_receipt_gate::sandbox::tree_modes_ok(modes.as_bytes())?;
    let z = git(
        scratch,
        hooks,
        &[
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            &base,
            head,
            "--",
        ],
    )
    .map_err(|_| "tree_refused")?;
    // a lock that is not there reads as empty (nothing to fetch)
    let lock_at = |rev: &str| {
        git(scratch, hooks, &["show", &format!("{rev}:Cargo.lock")]).unwrap_or_default()
    };
    let b = brings(&status_entries(&z), &lock_at(&base), &lock_at(head))?;
    if let Some(dir) = bundle.parent() {
        std::fs::create_dir_all(dir).map_err(|_| "refused")?;
    }
    let Some(out) = bundle.to_str() else {
        return Err("refused");
    };
    git(scratch, hooks, &["update-ref", BUNDLE_REF, head]).map_err(|_| "refused")?;
    let not_base = format!("^{base}");
    git(
        scratch,
        hooks,
        &["bundle", "create", "-q", out, BUNDLE_REF, &not_base],
    )
    .map_err(|_| "tree_refused")?;
    let (len, sha256) = sha256_of(bundle).ok_or("refused")?;
    if len == 0 || len > aib_receipt_gate::MAX_BUNDLE {
        return Err("tree_refused");
    }
    Ok(Made {
        base,
        len,
        sha256,
        brings: b,
    })
}

/// Makes the bundle of `head` of the seat's copy at `tree` (a copy of the registered repo at
/// `repo`) into the new file `bundle`. `scratch` is a folder of aibd's own that does not
/// exist yet; it is gone afterwards. A refusal (one of the words a page may be told) leaves
/// no bundle.
pub fn make(
    repo: &Path,
    tree: &Path,
    head: &str,
    hooks: &Path,
    scratch: &Path,
    bundle: &Path,
) -> Result<Made, &'static str> {
    let got = make_in(repo, tree, head, hooks, scratch, bundle);
    let _ = std::fs::remove_dir_all(scratch);
    if got.is_err() {
        let _ = std::fs::remove_file(bundle);
    }
    got
}

/// A registered repo and a seat's copy of it, for this module's tests and the runner's.
#[cfg(test)]
pub(crate) mod fixture {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use crate::seat_worktree::{open_clone, tree_path};
    use std::path::PathBuf;
    use std::process::Command;

    pub(crate) const IO: &str = "registry+https://github.com/rust-lang/crates.io-index";

    pub(crate) fn g(dir: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args([
                "-c",
                "commit.gpgsign=false",
                "-c",
                "init.defaultBranch=main",
            ])
            .args(args)
            .status()
            .unwrap()
            .success();
        assert!(ok, "git {args:?}");
    }

    pub(crate) fn out(dir: &Path, args: &[&str]) -> String {
        let o = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(args)
            .output()
            .unwrap();
        String::from_utf8_lossy(&o.stdout).trim().to_owned()
    }

    pub(crate) fn lock(source: &str) -> String {
        format!(
            "version = 4\n\n[[package]]\nname = \"sha2\"\nversion = \"0.10.9\"\nsource = \"{source}\"\n"
        )
    }

    pub(crate) struct Fix {
        pub(crate) _repo_dir: tempfile::TempDir,
        pub(crate) data: tempfile::TempDir,
        pub(crate) repo: PathBuf,
        pub(crate) tree: PathBuf,
        pub(crate) hooks: PathBuf,
        pub(crate) main: String,
    }

    impl Fix {
        pub(crate) fn commit(&self, file: &str, body: &str) -> String {
            let f = self.tree.join(file);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, body).unwrap();
            g(&self.tree, &["add", "-A"]);
            g(&self.tree, &["commit", "-q", "-m", file]);
            out(&self.tree, &["rev-parse", "HEAD"])
        }
        pub(crate) fn bundle(&self, n: usize) -> PathBuf {
            self.data
                .path()
                .join("receipts")
                .join(format!("b{n}.bundle"))
        }
        pub(crate) fn make(&self, head: &str, n: usize) -> Result<Made, &'static str> {
            let scratch = self.data.path().join("receipts").join(format!("s{n}"));
            let got = make(
                &self.repo,
                &self.tree,
                head,
                &self.hooks,
                &scratch,
                &self.bundle(n),
            );
            assert!(!scratch.exists(), "reason: scratch_left");
            got
        }
    }

    /// A registered repo whose `origin/main` is its one commit, and a seat's copy of it.
    pub(crate) fn fix() -> Fix {
        let repo_dir = tempfile::tempdir().unwrap();
        let repo = repo_dir.path().to_path_buf();
        g(&repo, &["init", "-q"]);
        std::fs::write(repo.join("README"), "r").unwrap();
        std::fs::write(repo.join("Cargo.lock"), lock(IO)).unwrap();
        g(&repo, &["add", "-A"]);
        g(&repo, &["commit", "-q", "-m", "first"]);
        g(&repo, &["update-ref", MAIN_REF, "HEAD"]);
        let main = out(&repo, &["rev-parse", "HEAD"]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let tree = tree_path(&data.path().join("seat-work").join("wt1"), "repo");
        open_clone(&repo, &tree, "seat/wt1-1120", "repo:repo", &hooks).unwrap();
        Fix {
            _repo_dir: repo_dir,
            data,
            repo,
            tree,
            hooks,
            main,
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::fixture::{fix, g, lock, out};
    use super::*;

    #[test]
    fn name_status_output_is_read_in_pairs() {
        assert_eq!(
            status_entries("M\0a/b.rs\0A\0build.rs\0D\0old.rs\0"),
            [
                ("M".to_owned(), "a/b.rs".to_owned()),
                ("A".to_owned(), "build.rs".to_owned()),
                ("D".to_owned(), "old.rs".to_owned()),
            ]
        );
        assert!(status_entries("").is_empty());
        // a path with a line end stays one path
        assert_eq!(
            status_entries("A\0x\n.cargo/config.toml\0"),
            [("A".to_owned(), "x\n.cargo/config.toml".to_owned())]
        );
    }

    #[test]
    fn a_bundle_carries_the_seats_commits_against_the_repos_main() {
        let f = fix();
        f.commit("src/new.rs", "fn x() {}\n");
        let head = f.commit("build.rs", "fn main() {}\n");
        let m = f.make(&head, 1).unwrap();
        assert_eq!(m.base, f.main, "reason: other_base");
        assert_eq!(m.brings.build_scripts, ["build.rs"]);
        assert_eq!(m.brings.new_code_files, 1);
        assert_eq!(m.brings.files, 2);
        let bytes = std::fs::read(f.bundle(1)).unwrap();
        assert_eq!(m.len, bytes.len() as u64);
        let hex: String = Sha256::digest(&bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(m.sha256, hex, "reason: digest_of_something_else");
        // one ref on the head, and only the seat's commits (it needs the base to be read)
        let heads = out(
            &f.repo,
            &["bundle", "list-heads", f.bundle(1).to_str().unwrap()],
        );
        assert_eq!(heads, format!("{head} {BUNDLE_REF}"));
        let text = String::from_utf8_lossy(&bytes);
        assert!(
            text.contains(&format!("-{}", f.main)),
            "reason: whole_history_sent"
        );
    }

    #[test]
    fn the_base_is_where_the_head_left_main_even_when_main_moved_on() {
        let f = fix();
        let head = f.commit("src/new.rs", "fn x() {}\n");
        // the repo's main gets a commit the copy never saw
        std::fs::write(f.repo.join("later.txt"), "later").unwrap();
        g(&f.repo, &["add", "-A"]);
        g(&f.repo, &["commit", "-q", "-m", "later"]);
        g(&f.repo, &["update-ref", MAIN_REF, "HEAD"]);
        let tip = out(&f.repo, &["rev-parse", "HEAD"]);
        assert_ne!(tip, f.main);
        let m = f.make(&head, 1).unwrap();
        assert_eq!(m.base, f.main, "reason: main_tip_is_the_base");
        // what main did afterwards is not something the head brings
        assert_eq!(m.brings.files, 1, "reason: mains_changes_counted");
        let text = String::from_utf8_lossy(&std::fs::read(f.bundle(1)).unwrap()).into_owned();
        assert!(text.contains(&format!("-{}", f.main)) && !text.contains(&format!("-{tip}")));
    }

    #[test]
    fn a_refused_head_leaves_no_bundle() {
        let f = fix();
        let head = f.commit(".cargo/config.toml", "[build]\nrustc-wrapper = \"./x\"\n");
        assert_eq!(
            f.make(&head, 1),
            Err("tree_refused"),
            "reason: cargo_config_sent"
        );
        assert!(!f.bundle(1).exists(), "reason: bundle_left");
        let head = f.commit("rust-toolchain.toml", "[toolchain]\npath = \"/tmp/x\"\n");
        assert_eq!(f.make(&head, 2), Err("tree_refused"));
        // taken out again: the head no longer differs from the base there
        g(
            &f.tree,
            &["rm", "-q", "-r", ".cargo", "rust-toolchain.toml"],
        );
        g(&f.tree, &["commit", "-q", "-m", "back"]);
        let head = out(&f.tree, &["rev-parse", "HEAD"]);
        assert!(f.make(&head, 3).is_ok());
        // a symbolic link in the head: a Cargo.toml that points at any file of the account
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink("/etc/hosts", f.tree.join("linked.toml")).unwrap();
            g(&f.tree, &["add", "-A"]);
            g(&f.tree, &["commit", "-q", "-m", "link"]);
            let head = out(&f.tree, &["rev-parse", "HEAD"]);
            assert_eq!(f.make(&head, 5), Err("tree_refused"), "reason: link_sent");
            assert!(!f.bundle(5).exists());
            g(&f.tree, &["rm", "-q", "linked.toml"]);
            g(&f.tree, &["commit", "-q", "-m", "no link"]);
        }
        // a git source in the head's lock
        let head = f.commit("Cargo.lock", &lock("git+https://example.invalid/x/y#abc"));
        assert_eq!(
            f.make(&head, 4),
            Err("tree_refused"),
            "reason: git_source_sent"
        );
        assert!(!f.bundle(4).exists());
    }

    #[test]
    fn a_head_the_copy_does_not_hold_or_a_repo_without_main_is_refused() {
        let f = fix();
        let head = f.commit("a.txt", "a");
        assert_eq!(
            f.make("0123456789abcdef0123456789abcdef01234567", 1),
            Err("head_not_seat_head")
        );
        assert_eq!(
            f.make("main", 2),
            Err("head_not_seat_head"),
            "reason: name_taken"
        );
        assert_eq!(f.make("--upload-pack=x", 3), Err("head_not_seat_head"));
        // the registered repo has no origin/main: nothing a test machine would know
        g(&f.repo, &["update-ref", "-d", MAIN_REF]);
        assert_eq!(
            f.make(&head, 4),
            Err("receipt_not_allowed"),
            "reason: no_main_sent"
        );
        assert!(!f.bundle(4).exists());
    }

    #[test]
    fn a_head_that_shares_no_history_with_main_is_refused() {
        let f = fix();
        g(&f.tree, &["checkout", "-q", "--orphan", "other"]);
        let head = f.commit("x.txt", "x");
        assert_eq!(
            f.make(&head, 1),
            Err("tree_refused"),
            "reason: unrelated_history_sent"
        );
        assert!(!f.bundle(1).exists());
    }
}
