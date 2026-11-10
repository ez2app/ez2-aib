//! DEV 4.6 P1 `aib-release host-build` against real throw-away git
//! repositories: main A → B → C with a side branch D off B (a PR head that
//! never merged). Each refusal is asserted by its code, not only by the exit
//! status (RULES §15.17).
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_release::host_build::{
    check, exit_code, info_in_binary, parse_info, sha256_hex, to_json, BuildInfo, Current, GitCli,
    Request, Verdict, MAIN_REF,
};
use std::path::PathBuf;
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
            "aib-host-build-test-{tag}-{}-{nanos}",
            std::process::id()
        ));
        std::fs::create_dir(&p).unwrap();
        let r = Repo(p);
        r.git(&["init", "-q", "-b", "main"]);
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

    fn commit(&self, file: &str, body: &str) -> String {
        std::fs::write(self.0.join(file), body).unwrap();
        self.git(&["add", "-A"]);
        self.git(&["commit", "-q", "-m", file]);
        self.git(&["rev-parse", "HEAD"])
    }
}

/// main A → B → C, and D on a branch off B.
struct Line {
    repo: Repo,
    a: String,
    b: String,
    c: String,
    d: String,
}

fn line(tag: &str) -> Line {
    let repo = Repo::new(tag);
    let a = repo.commit("a.txt", "a");
    let b = repo.commit("b.txt", "b");
    repo.git(&["switch", "-q", "-c", "pr-head"]);
    let d = repo.commit("d.txt", "d");
    repo.git(&["switch", "-q", "main"]);
    let c = repo.commit("c.txt", "c");
    Line { repo, a, b, c, d }
}

fn built(commit: &str) -> BuildInfo {
    BuildInfo {
        version: "0.2.0-preview.3".into(),
        commit: Some(commit.into()),
        dirty: false,
    }
}

fn ask(candidate: BuildInfo, current: Current) -> Request {
    Request {
        candidate,
        candidate_sha256: None,
        current,
        main: "main".into(),
        allow_downgrade: None,
        allow_unknown_current: None,
    }
}

#[test]
fn a_build_from_main_that_contains_the_hosts_build_may_go_on() {
    let l = line("ok");
    let git = GitCli::new(&l.repo.0);
    for (cand, cur) in [(&l.c, &l.b), (&l.c, &l.a), (&l.c, &l.c), (&l.b, &l.a)] {
        let req = ask(built(cand), Current::Known(built(cur)));
        let rep = check(&git, &req);
        assert_eq!(
            (rep.verdict, rep.code),
            (Verdict::Ok, "ok"),
            "{cand} over {cur}"
        );
        assert_eq!(exit_code(&rep), 0);
        assert!(rep.allowed.is_empty());
        assert_eq!(rep.main.as_deref(), Some(l.c.as_str()));
    }
}

/// The 11-11 B build: made from a PR head, so it is not on main. No flag lets it through.
#[test]
fn a_build_from_a_pr_head_never_goes_on() {
    let l = line("pr");
    let git = GitCli::new(&l.repo.0);
    let mut req = ask(built(&l.d), Current::Known(built(&l.b)));
    req.allow_downgrade = Some("trying the fast way".into());
    req.allow_unknown_current = Some("trying the fast way".into());
    let rep = check(&git, &req);
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "not_on_main"),
        "{rep:?}"
    );
    assert_eq!(exit_code(&rep), 2);
    // both reasons were given and neither got it through: the approval shows someone tried
    let j = to_json(&req, &rep);
    assert_eq!(j["allowed"], serde_json::json!([]));
    assert_eq!(j["unused_allow"][0]["flag"], "allow_downgrade");
    assert_eq!(j["unused_allow"][1]["flag"], "allow_unknown_current");
}

/// #650 M1 (2240e): the main line is `origin/main` as fetched, whatever is checked out. Here
/// the repository stands on the PR head, so `HEAD` (or the PR head named as main) would pass it.
#[test]
fn the_main_line_is_origin_main_and_not_what_is_checked_out() {
    let l = line("mainref");
    l.repo
        .git(&["update-ref", "refs/remotes/origin/main", &l.c]);
    l.repo.git(&["switch", "-q", "pr-head"]);
    let git = GitCli::new(&l.repo.0);
    let req = Request::for_host(built(&l.d), Current::Known(built(&l.b)));
    // compared with the constant, so knife H5 turns the PR head assertion red, not this one
    assert_eq!(req.main, MAIN_REF);
    let rep = check(&git, &req);
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "not_on_main"),
        "{rep:?}"
    );
    let j = to_json(&req, &rep);
    assert_eq!(j["main_ref"], MAIN_REF);
    assert_eq!(j["main"], l.c.as_str());
    let rep = check(
        &git,
        &Request::for_host(built(&l.c), Current::Known(built(&l.b))),
    );
    assert_eq!((rep.verdict, rep.code), (Verdict::Ok, "ok"), "{rep:?}");
}

/// What the host runs is not in the candidate: going on would take something away.
#[test]
fn a_build_that_does_not_contain_the_hosts_build_is_refused_unless_a_downgrade_is_asked_for() {
    let l = line("down");
    let git = GitCli::new(&l.repo.0);
    let mut req = ask(built(&l.b), Current::Known(built(&l.c)));
    let rep = check(&git, &req);
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "not_a_descendant"),
        "{rep:?}"
    );
    req.allow_downgrade = Some("roll back the bad build of 11-11".into());
    let rep = check(&git, &req);
    assert_eq!((rep.verdict, rep.code), (Verdict::Ok, "ok"));
    assert_eq!(
        rep.allowed,
        vec![(
            "allow_downgrade",
            "roll back the bad build of 11-11".to_owned()
        )]
    );
    // the reason is in the answer the approval is made from
    let j = to_json(&req, &rep);
    assert_eq!(j["allowed"][0]["why"], "roll back the bad build of 11-11");
    // a host on the PR head (the B build) and main going back on: C does not contain D
    let req = ask(built(&l.c), Current::Known(built(&l.d)));
    assert_eq!(check(&git, &req).code, "not_a_descendant");
}

#[test]
fn a_candidate_that_does_not_say_where_it_came_from_or_was_built_dirty_is_refused() {
    let l = line("cand");
    let git = GitCli::new(&l.repo.0);
    let mut dirty = built(&l.c);
    dirty.dirty = true;
    let rep = check(&git, &ask(dirty, Current::Known(built(&l.b))));
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "candidate_dirty"),
        "{rep:?}"
    );
    let mut unknown = built(&l.c);
    unknown.commit = None;
    let rep = check(&git, &ask(unknown, Current::Known(built(&l.b))));
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "candidate_unknown")
    );
}

/// A host build from before build info existed: passed only with a reason.
#[test]
fn a_host_build_without_build_info_needs_a_reason() {
    let l = line("unknown");
    let git = GitCli::new(&l.repo.0);
    let mut req = ask(built(&l.c), Current::Unknown);
    let rep = check(&git, &req);
    assert_eq!(
        (rep.verdict, rep.code),
        (Verdict::Refused, "current_unknown"),
        "{rep:?}"
    );
    req.allow_unknown_current = Some("first build with build info".into());
    let rep = check(&git, &req);
    assert_eq!((rep.verdict, rep.code), (Verdict::Ok, "ok"));
    assert_eq!(rep.allowed[0].0, "allow_unknown_current");
    assert_eq!(to_json(&req, &rep)["current"], serde_json::Value::Null);
    // a host that answers `commit=unknown` is the same case, and its answer stays in the
    // JSON (2240e, #650 N3)
    let mut says_unknown = built(&l.b);
    says_unknown.commit = None;
    let mut req = ask(built(&l.c), Current::Known(says_unknown));
    assert_eq!(check(&git, &req).code, "current_unknown");
    req.allow_unknown_current = Some("host built before build info".into());
    let rep = check(&git, &req);
    assert_eq!((rep.verdict, rep.code), (Verdict::Ok, "ok"), "{rep:?}");
    let j = to_json(&req, &rep);
    assert_eq!(j["current"]["commit"], serde_json::Value::Null);
    assert_eq!(j["current"]["version"], "0.2.0-preview.3");
    assert_eq!(j["unused_allow"], serde_json::json!([]));
}

/// A commit the repository does not have is "fetch first" (exit 3), never a pass or a refusal.
#[test]
fn a_commit_the_repository_does_not_have_is_an_error() {
    let l = line("missing");
    let git = GitCli::new(&l.repo.0);
    let ghost = "0123456789abcdef0123456789abcdef01234567";
    let rep = check(&git, &ask(built(ghost), Current::Known(built(&l.b))));
    assert_eq!((rep.verdict, rep.code), (Verdict::Error, "git"), "{rep:?}");
    assert_eq!(exit_code(&rep), 3);
    let rep = check(&git, &ask(built(&l.c), Current::Known(built(ghost))));
    assert_eq!((rep.verdict, rep.code), (Verdict::Error, "git"), "{rep:?}");
    let mut req = ask(built(&l.c), Current::Known(built(&l.b)));
    req.main = "no-such-branch".into();
    assert_eq!(check(&git, &req).verdict, Verdict::Error);
    req.main = "--upload-pack=x".into();
    assert_eq!(check(&git, &req).verdict, Verdict::Error);
}

/// The reader takes the line `aib-cloud-host build-info` prints and finds it in a file.
#[test]
fn the_host_line_reads_back() {
    let l = line("line");
    let printed = format!(
        "aib-build-info version=0.2.0-preview.3 commit={} dirty=0;",
        l.c
    );
    assert_eq!(parse_info(&printed), Some(built(&l.c)));
    let mut file = b"\x7fELF\x02\x01\x01\0".to_vec();
    file.extend_from_slice(printed.as_bytes());
    file.extend_from_slice(b"\0\0");
    assert_eq!(info_in_binary(&file).unwrap(), built(&l.c));
    // the hash the approval and the swap step compare is the usual sha256 of the file
    assert_eq!(
        sha256_hex(b"abc"),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
}
