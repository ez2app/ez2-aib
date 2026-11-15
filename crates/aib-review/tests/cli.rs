//! The `aib-review` binary end to end (S78 r-1) on a real git repository, under a root
//! folder whose name has a space and Chinese in it.

use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

struct World {
    _tmp: tempfile::TempDir,
    root: PathBuf,
    repo: PathBuf,
}

fn git(repo: &Path, args: &[&str]) -> String {
    let o = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@example.invalid")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@example.invalid")
        .output()
        .expect("git");
    assert!(o.status.success(), "git {args:?}: {o:?}");
    String::from_utf8_lossy(&o.stdout).trim().to_owned()
}

impl World {
    fn new() -> World {
        let tmp = tempfile::tempdir().expect("tmp");
        let root = tmp.path().join("home 有 空白").join(".ez2-aib");
        let repo = tmp.path().join("repo 倉");
        fs::create_dir_all(&repo).expect("mkdir");
        git(&repo, &["init", "-q"]);
        World {
            _tmp: tmp,
            root,
            repo,
        }
    }
    /// A new commit; returns its 40-hex id.
    fn commit(&self, name: &str) -> String {
        fs::write(self.repo.join(name), name).expect("write");
        git(&self.repo, &["add", name]);
        git(&self.repo, &["commit", "-q", "-m", name]);
        git(&self.repo, &["rev-parse", "HEAD"])
    }
    fn ledger(&self) -> PathBuf {
        self.root.join("state").join("review").join("review.jsonl")
    }
    fn text(&self) -> String {
        fs::read_to_string(self.ledger()).unwrap_or_default()
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_aib-review"))
            .args(args)
            .current_dir(&self.repo)
            .env("EZ2_AIB_HOME", &self.root)
            .env("AIB_REVIEW_NOW_MS", "1794623580000")
            .env_remove("AIB_SEAT_CODE")
            .output()
            .expect("run aib-review")
    }
    /// A review file outside the repo; returns its path.
    fn review(&self, name: &str, body: &str) -> String {
        let p = self.root.parent().expect("parent").join(name);
        fs::create_dir_all(p.parent().expect("dir")).expect("mkdir");
        fs::write(&p, body).expect("write review");
        p.to_string_lossy().into_owned()
    }
    fn submit(&self, seat: &str, file: &str, extra: &[&str]) -> Output {
        let mut a = vec![
            "submit", "--pr", "757", "--author", "043dd", "--seat", seat, "--file", file,
        ];
        a.extend_from_slice(extra);
        self.run(&a)
    }
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn err(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

fn sealed(body: &str) -> String {
    let sha: String = Sha256::digest(body.as_bytes())
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect();
    format!("{body}RECEIPT-SHA256 {sha}\n")
}

#[test]
fn a_medium_blocks_the_gate_until_the_fix_is_claimed_and_a_later_review_verifies_it() {
    let w = World::new();
    let h1 = w.commit("a");
    let r1 = w.review(
        "REVIEW-1.md",
        &format!(
            "結論: 不通過 頭: {h1} H/M/L/N: 0/1/1/0 範圍: 全文\n\n- [M1] the lock is never released\n- [L1] a name\n"
        ),
    );
    let o = w.submit("1ced7", &r1, &[]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(out(&o).starts_with(&format!("R1 757 {} H/M/L/N 0/1/1/0", &h1[..9])));

    let o = w.run(&["gate", "--pr", "757", "--head", &h1]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(
        out(&o).contains("R1-M1 中度 is open: the lock is never released"),
        "{}",
        out(&o)
    );

    // the author records the fix: claimed only
    let h2 = w.commit("b");
    let o = w.run(&[
        "fix", "--pr", "757", "--ids", "R1-M1", "--commit", &h2, "--seat", "043dd",
    ]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let o = w.run(&["gate", "--pr", "757", "--head", &h2]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(out(&o).contains("R1-M1 中度 is claimed"), "{}", out(&o));
    assert!(out(&o).contains("no seat review on head"), "{}", out(&o));

    // round 2 on the new head confirms it
    let r2 = w.review(
        "REVIEW-2.md",
        &format!("結論: 通過 頭: {h2} H/M/L/N: 0/0/0/0 範圍: {h1}..{h2}\n- **M1 已修**\n"),
    );
    let o = w.submit("1ced7", &r2, &["--verifies", "R1-M1"]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(out(&o).contains("verified R1-M1"));
    let o = w.run(&["gate", "--pr", "757", "--head", &h2]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert_eq!(out(&o), format!("gate: pass 757 {}\n", &h2[..9]));

    // the first head does not contain the fix
    let o = w.run(&["gate", "--pr", "757", "--head", &h1]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(
        out(&o).contains("which this head does not contain"),
        "{}",
        out(&o)
    );

    // with a receipt: same head passes, another head is blocked, a broken seal is blocked
    let rc = w.review(
        "TEST-ok.txt",
        &sealed(&format!("HEAD={h2}\nRC test=0\nHEAD-END {h2}\n")),
    );
    let o = w.run(&["gate", "--pr", "757", "--head", &h2, "--receipt", &rc]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(out(&o).contains("review head = receipt head"));
    let old = w.review(
        "TEST-old.txt",
        &sealed(&format!("HEAD={h1}\nHEAD-END {h1}\n")),
    );
    let o = w.run(&["gate", "--pr", "757", "--head", &h2, "--receipt", &old]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(out(&o).contains(&format!("receipt: HEAD {} is not {}", &h1[..9], &h2[..9])));
    let bad = fs::read_to_string(&rc)
        .expect("read")
        .replace("RC test=0", "RC test=1");
    let bad = w.review("TEST-bad.txt", &bad);
    let o = w.run(&["gate", "--pr", "757", "--head", &h2, "--receipt", &bad]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(out(&o).contains("RECEIPT-SHA256 does not match"));

    // status lists every round and state
    let o = w.run(&["status", "--pr", "757", "--json"]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let v: serde_json::Value = serde_json::from_str(&out(&o)).expect("json");
    assert_eq!(v["complete"], true);
    assert_eq!(v["prs"][0]["findings"][0]["state"], "verified");
    assert_eq!(v["prs"][0]["findings"][0]["by"], "1ced7");
    assert_eq!(v["prs"][0]["reviews"].as_array().map(Vec::len), Some(2));
}

#[test]
fn a_review_that_breaks_a_rule_writes_nothing() {
    let w = World::new();
    let h1 = w.commit("a");
    // the numbers do not match the findings
    let r = w.review(
        "R-count.md",
        &format!("結論: 不通過 頭: {h1} H/M/L/N: 0/2/0/0 範圍: 全文\n- [M1] one\n"),
    );
    let o = w.submit("1ced7", &r, &[]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(err(&o).contains("H/M/L/N says 0/2/0/0 but the findings are 0/1/0/0"));
    // the author reviewing its own PR
    let r = w.review(
        "R-self.md",
        &format!("結論: 通過 頭: {h1} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
    );
    let o = w.submit("043dd", &r, &[]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(err(&o).contains("the author cannot review its own PR"));
    // a head that is not in the repository
    let r = w.review(
        "R-nohead.md",
        &format!(
            "結論: 通過 頭: {} H/M/L/N: 0/0/0/0 範圍: 全文\n",
            "0".repeat(40)
        ),
    );
    let o = w.submit("1ced7", &r, &[]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(err(&o).contains("is not a commit in"));
    // a first line in another shape
    let r = w.review("R-shape.md", &format!("Verdict: pass head {h1}\n"));
    let o = w.submit("1ced7", &r, &[]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert_eq!(w.text(), "", "nothing written");
    // usage
    assert_eq!(w.run(&[]).status.code(), Some(64));
    assert_eq!(
        w.run(&["gate", "--pr", "757", "--head", "abc"])
            .status
            .code(),
        Some(64)
    );
    assert_eq!(w.run(&["--help"]).status.code(), Some(0));
}

#[test]
fn a_fix_that_is_not_in_the_reviewed_head_is_not_verified() {
    let w = World::new();
    let h1 = w.commit("a");
    let r1 = w.review(
        "R1.md",
        &format!("結論: 不通過 頭: {h1} H/M/L/N: 0/1/0/0 範圍: 全文\n- [M1] x\n"),
    );
    assert_eq!(w.submit("1ced7", &r1, &[]).status.code(), Some(0));
    // the fix is on a side branch the next review's head does not contain
    git(&w.repo, &["checkout", "-q", "-b", "side"]);
    let side = w.commit("side");
    git(&w.repo, &["checkout", "-q", "-"]);
    let h2 = w.commit("b");
    let o = w.run(&[
        "fix", "--pr", "757", "--ids", "R1-M1", "--commit", &side, "--seat", "043dd",
    ]);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let r2 = w.review(
        "R2.md",
        &format!("結論: 通過 頭: {h2} H/M/L/N: 0/0/0/0 範圍: 增量\n"),
    );
    let o = w.submit("1ced7", &r2, &["--verifies", "R1-M1"]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(err(&o).contains(&format!("the fix {} is not in 頭 {}", &side[..9], &h2[..9])));
    // only the author records a fix
    let o = w.run(&[
        "fix", "--pr", "757", "--ids", "R1-M1", "--commit", &h2, "--seat", "1ced7",
    ]);
    assert_eq!(o.status.code(), Some(3), "{o:?}");
    assert!(err(&o).contains("only the author (043dd) records a fix"));
}

#[test]
fn an_incomplete_ledger_refuses_writes_and_the_gate_says_so_with_rc_2() {
    let w = World::new();
    let h1 = w.commit("a");
    let r = w.review(
        "R.md",
        &format!("結論: 通過 頭: {h1} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
    );
    assert_eq!(w.submit("1ced7", &r, &[]).status.code(), Some(0));
    // a hand edit: the line is no longer what replay accepts
    let t = w.text().replace("\"seat\":\"1ced7\"", "\"seat\":\"043dd\"");
    fs::write(w.ledger(), t).expect("edit");
    let o = w.run(&["gate", "--pr", "757", "--head", &h1]);
    assert_eq!(o.status.code(), Some(2), "{o:?}");
    assert!(out(&o).contains("ledger incomplete"));
    assert!(err(&o).contains("line 1: the author cannot review its own PR"));
    let r2 = w.review(
        "R2.md",
        &format!("結論: 通過 頭: {h1} H/M/L/N: 0/0/0/0 範圍: 再一份\n"),
    );
    let o = w.submit("a9136", &r2, &[]);
    assert_eq!(o.status.code(), Some(2), "{o:?}");
    assert_eq!(w.run(&["status"]).status.code(), Some(2));
}
