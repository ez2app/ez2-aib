//! A8 / A9 trial merge against real throw-away git repositories: clean
//! stacks, pairwise conflicts and the stacking hint, a head that needs the
//! base first, the expected tree (match and mismatch), the `int/` prefix,
//! bad refs, pairs with no common ancestor, and that the repository is left
//! exactly as it was and its own merge config never runs.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_release::trial::{exit_code, integ_prefix_refusal, run, to_json, GitCli, Request, Verdict};
use std::path::{Path, PathBuf};
use std::process::Command;

struct Repo(PathBuf);

impl Drop for Repo {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

impl Repo {
    fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!(
            "aib-trial-test-{tag}-{}-{nanos}",
            std::process::id()
        ));
        std::fs::create_dir(&p).unwrap();
        let r = Repo(p);
        r.git(&["init", "-q", "-b", "main"]);
        r.write("a.txt", "a1\na2\na3\n");
        r.write("b.txt", "b1\nb2\nb3\n");
        r.commit("base");
        r
    }

    fn git(&self, args: &[&str]) -> String {
        let out = Command::new("git")
            .arg("-C")
            .arg(&self.0)
            .args(args)
            .env("GIT_AUTHOR_NAME", "t")
            .env("GIT_AUTHOR_EMAIL", "t@t.invalid")
            .env("GIT_COMMITTER_NAME", "t")
            .env("GIT_COMMITTER_EMAIL", "t@t.invalid")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env(
                "GIT_CONFIG_GLOBAL",
                if cfg!(windows) { "NUL" } else { "/dev/null" },
            )
            .env_remove("GIT_DIR")
            .env_remove("GIT_WORK_TREE")
            .env_remove("GIT_INDEX_FILE")
            .env_remove("GIT_OBJECT_DIRECTORY")
            .env_remove("GIT_ALTERNATE_OBJECT_DIRECTORIES")
            .env_remove("GIT_COMMON_DIR")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8_lossy(&out.stdout).trim().to_owned()
    }

    fn write(&self, f: &str, s: &str) {
        std::fs::write(self.0.join(f), s).unwrap();
    }

    fn commit(&self, msg: &str) -> String {
        self.git(&["add", "-A"]);
        self.git(&["commit", "-q", "-m", msg]);
        self.git(&["rev-parse", "HEAD"])
    }

    /// A branch off `from` that writes `file`.
    fn branch(&self, name: &str, from: &str, file: &str, body: &str) -> String {
        self.git(&["checkout", "-q", "-b", name, from]);
        self.write(file, body);
        let sha = self.commit(name);
        self.git(&["checkout", "-q", "main"]);
        sha
    }

    fn dir(&self) -> &Path {
        &self.0
    }

    /// Every object and ref, to prove the trial wrote nothing.
    fn snapshot(&self) -> (String, String) {
        (
            self.git(&["count-objects", "-v"]),
            self.git(&["for-each-ref", "--format=%(refname) %(objectname)"]),
        )
    }
}

fn req(base: &str, heads: &[&str]) -> Request {
    Request {
        base: base.to_owned(),
        heads: heads.iter().map(|s| (*s).to_owned()).collect(),
        expect_tree: None,
        integ_branch: None,
    }
}

#[test]
fn heads_on_different_files_stack_cleanly_and_match_a_real_merge() {
    let r = Repo::new("clean");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    r.branch("y", "main", "b.txt", "b1\nY\nb3\n");
    // the integration head someone reports: main + x + y, merged for real
    r.git(&["checkout", "-q", "-b", "int/m1", "main"]);
    r.git(&["merge", "-q", "--no-edit", "x"]);
    r.git(&["merge", "-q", "--no-edit", "y"]);
    r.git(&["checkout", "-q", "main"]);
    let before = r.snapshot();

    let git = GitCli::new(r.dir()).unwrap();
    let mut q = req("main", &["x", "y"]);
    q.expect_tree = Some("int/m1".into());
    let rep = run(&git, &q);
    assert_eq!(rep.verdict, Verdict::Clean, "{}", to_json(&rep));
    assert_eq!(rep.order, vec!["x", "y"]);
    assert!(rep.blocked.is_empty() && rep.pairs.is_empty());
    assert!(rep.heads.iter().all(|h| h.onto_base.clean()));
    let e = rep.expect.as_ref().unwrap();
    assert!(e.diff.is_empty());
    assert_eq!(rep.stacked_tree.as_deref(), Some(e.tree.as_str()));
    assert_eq!(exit_code(&rep), 0);
    drop(git);
    assert_eq!(r.snapshot(), before, "the trial must not write to the repo");
}

#[test]
fn two_heads_on_the_same_lines_name_the_file_and_say_stack_on_the_first() {
    let r = Repo::new("pair");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    r.branch("y", "main", "a.txt", "a1\nY\na3\n");
    r.branch("z", "main", "b.txt", "b1\nZ\nb3\n");
    let git = GitCli::new(r.dir()).unwrap();
    let rep = run(&git, &req("main", &["x", "y", "z"]));
    assert_eq!(rep.verdict, Verdict::NotReady);
    assert_eq!(exit_code(&rep), 2);
    assert_eq!(rep.pairs.len(), 1);
    assert_eq!(
        (rep.pairs[0].a.as_str(), rep.pairs[0].b.as_str()),
        ("x", "y")
    );
    assert_eq!(rep.pairs[0].conflicts, vec!["a.txt"]);
    // y still goes on main alone; it is the stack that blocks it
    assert!(rep.heads.iter().all(|h| h.onto_base.clean()));
    assert_eq!(rep.order, vec!["x", "z"]);
    assert_eq!(rep.blocked.len(), 1);
    assert_eq!(rep.blocked[0].name, "y");
    assert_eq!(rep.blocked[0].conflicts, vec!["a.txt"]);
    assert_eq!(rep.blocked[0].stack_on, vec!["x"]);
    assert!(rep.stacked_tree.is_none());
    let j = to_json(&rep);
    assert_eq!(j["verdict"], "not_ready");
    assert_eq!(j["blocked"][0]["stack_on"][0], "x");
    assert!(j["blocked"][0]["stack_on_reason"].is_null());
}

#[test]
fn a_head_that_collides_with_the_base_needs_the_base_merged_first() {
    let r = Repo::new("base");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    // main moves on the same line after x branched
    r.write("a.txt", "a1\nMAIN\na3\n");
    r.commit("main moves");
    let git = GitCli::new(r.dir()).unwrap();
    let rep = run(&git, &req("main", &["x"]));
    assert_eq!(rep.verdict, Verdict::NotReady);
    assert!(!rep.heads[0].onto_base.clean());
    assert_eq!(rep.heads[0].onto_base.conflicts, vec!["a.txt"]);
    assert!(rep.order.is_empty());
    assert_eq!(rep.blocked[0].name, "x");
    assert!(
        rep.blocked[0].stack_on.is_empty(),
        "nothing stacked to blame"
    );
    assert_eq!(to_json(&rep)["heads"][0]["onto_base"]["clean"], false);
    assert_eq!(
        to_json(&rep)["blocked"][0]["stack_on_reason"],
        "needs_the_base_merged_first"
    );
}

#[test]
fn a_reported_head_whose_tree_is_not_the_stack_lists_the_files_that_differ() {
    let r = Repo::new("expect");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    // a "resolved" head that also slipped in an extra change
    r.git(&["checkout", "-q", "-b", "claimed", "x"]);
    r.write("b.txt", "b1\nSTRAY\nb3\n");
    r.commit("stray");
    r.git(&["checkout", "-q", "main"]);
    let git = GitCli::new(r.dir()).unwrap();
    let mut q = req("main", &["x"]);
    q.expect_tree = Some("claimed".into());
    let rep = run(&git, &q);
    assert_eq!(rep.verdict, Verdict::NotReady);
    assert_eq!(exit_code(&rep), 2);
    let e = rep.expect.as_ref().unwrap();
    assert_eq!(e.diff, vec!["b.txt"]);
    assert_eq!(to_json(&rep)["expect_tree"]["match"], false);
}

#[test]
fn an_already_merged_head_stacks_as_a_no_op() {
    let r = Repo::new("noop");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    r.git(&["merge", "-q", "--no-edit", "x"]);
    let git = GitCli::new(r.dir()).unwrap();
    let mut q = req("main", &["x"]);
    q.expect_tree = Some("main".into());
    let rep = run(&git, &q);
    assert_eq!(rep.verdict, Verdict::Clean, "{}", to_json(&rep));
    assert!(rep.expect.unwrap().diff.is_empty());
}

#[test]
fn integration_branches_must_be_int_slash() {
    assert_eq!(integ_prefix_refusal("int/m1-a63ab-1110"), None);
    assert_eq!(integ_prefix_refusal("origin/int/m1"), None);
    assert_eq!(integ_prefix_refusal("refs/heads/int/m1"), None);
    assert_eq!(
        integ_prefix_refusal("integ/m1-a63ab-1110"),
        Some("integ_prefix_skips_macos_cells")
    );
    assert_eq!(
        integ_prefix_refusal("refs/remotes/origin/integ/m1"),
        Some("integ_prefix_skips_macos_cells")
    );
    assert_eq!(integ_prefix_refusal("int/"), Some("not_int_prefix"));
    assert_eq!(integ_prefix_refusal("feat/x"), Some("not_int_prefix"));

    let r = Repo::new("prefix");
    r.git(&["branch", "integ/m1", "main"]);
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    let git = GitCli::new(r.dir()).unwrap();
    // the base itself is an integration branch with the wrong prefix
    let rep = run(&git, &req("integ/m1", &["x"]));
    assert_eq!(rep.verdict, Verdict::NotReady, "{}", to_json(&rep));
    assert_eq!(rep.order, vec!["x"], "the merge itself is fine");
    assert_eq!(rep.prefixes.len(), 1);
    assert_eq!(
        rep.prefixes[0].refused,
        Some("integ_prefix_skips_macos_cells")
    );
    // a plain base is not an integration branch: no prefix rule
    let rep = run(&git, &req("main", &["x"]));
    assert_eq!(rep.verdict, Verdict::Clean);
    assert!(rep.prefixes.is_empty());
    // asked explicitly
    let mut q = req("main", &["x"]);
    q.integ_branch = Some("int/m1".into());
    let rep = run(&git, &q);
    assert_eq!(rep.verdict, Verdict::Clean);
    assert_eq!(to_json(&rep)["integ_prefix"][0]["ok"], true);
}

#[test]
fn a_ref_git_does_not_know_or_the_same_commit_twice_is_an_error_not_a_pass() {
    let r = Repo::new("bad");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    r.git(&["branch", "x2", "x"]);
    let git = GitCli::new(r.dir()).unwrap();
    for q in [
        req("main", &["nope"]),
        req("nope", &["x"]),
        req("main", &["--output=/tmp/x"]),
        req("main", &["x", "x2"]),
    ] {
        let rep = run(&git, &q);
        assert!(matches!(rep.verdict, Verdict::Error(_)), "{q:?}");
        assert_eq!(exit_code(&rep), 3);
        assert_eq!(to_json(&rep)["verdict"], "error");
    }
    let mut q = req("main", &["x"]);
    q.expect_tree = Some("nope".into());
    assert_eq!(exit_code(&run(&git, &q)), 3);
}

#[test]
fn a_directory_that_is_not_a_repository_is_refused() {
    let p = std::env::temp_dir().join(format!("aib-trial-test-norepo-{}", std::process::id()));
    std::fs::create_dir_all(&p).unwrap();
    // GIT_CEILING_DIRECTORIES is not ours to set here; the temp dir is not
    // inside a repository on any CI image
    let e = GitCli::new(&p).err();
    let _ = std::fs::remove_dir_all(&p);
    assert!(e.is_some_and(|e| e.contains("not a git repository")));
}

#[cfg(unix)]
#[test]
fn the_repository_merge_driver_and_config_never_run() {
    let r = Repo::new("driver");
    let marker = r.dir().join("DRIVER-RAN");
    // a driver that would leave a marker and "resolve" every conflict
    r.git(&[
        "config",
        "merge.mark.driver",
        &format!("touch '{}'; cp %B %A; exit 0", marker.display()),
    ]);
    r.git(&["config", "merge.conflictStyle", "diff3"]);
    r.write(".gitattributes", "a.txt merge=mark\n");
    r.commit("attrs");
    r.branch("x", "main", "a.txt", "a1\nX\na3\n");
    r.branch("y", "main", "a.txt", "a1\nY\na3\n");
    let before = r.snapshot();
    let git = GitCli::new(r.dir()).unwrap();
    let rep = run(&git, &req("main", &["x", "y"]));
    drop(git);
    assert!(!marker.exists(), "the repository's merge driver ran");
    assert_eq!(rep.pairs.len(), 1, "{}", to_json(&rep));
    assert_eq!(rep.blocked[0].name, "y");
    assert_eq!(r.snapshot(), before);
    let stray: Vec<_> = std::fs::read_dir(r.dir())
        .unwrap()
        .filter_map(Result::ok)
        .filter(|e| e.file_name().to_string_lossy().starts_with(".merge_file"))
        .collect();
    assert!(stray.is_empty(), "{stray:?}");
}

#[test]
fn a_pair_with_no_common_ancestor_does_not_stop_the_stack() {
    let r = Repo::new("unrelated");
    let first = r.git(&["rev-parse", "HEAD"]);
    r.git(&["checkout", "-q", "--orphan", "other"]);
    r.git(&["rm", "-q", "-r", "--cached", "."]);
    std::fs::remove_file(r.dir().join("a.txt")).unwrap();
    std::fs::remove_file(r.dir().join("b.txt")).unwrap();
    r.write("c.txt", "c1\n");
    let other = r.commit("other root");
    r.git(&["checkout", "-q", "-f", "main"]);
    r.git(&[
        "merge",
        "-q",
        "--no-edit",
        "--allow-unrelated-histories",
        "other",
    ]);
    let git = GitCli::new(r.dir()).unwrap();
    let rep = run(&git, &req("main", &[&first, &other]));
    assert_eq!(rep.verdict, Verdict::Clean, "{}", to_json(&rep));
    assert_eq!(rep.pair_errors.len(), 1);
    assert_eq!(rep.order.len(), 2);
}
