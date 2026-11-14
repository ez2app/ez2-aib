//! `aib-xtask no-new-python --base <ref> [--head <ref>] [--exceptions-from <ref>]`
//!
//! Andy 11-13 21:2x: "程式只能 Rust、沒有 Python"; 11-14 (S59) a gate before push: every Python
//! file added or changed in `base...head` (the merge base, so what `base` gained meanwhile does
//! not count) is refused unless the exception list approves that exact content. A deleted file is
//! never refused: removing Python is the direction we want.
//! Only Python files are in scope: a shell or workflow line that calls an existing, frozen Python
//! tool (`python3 -I scripts/knife.py`) is not a new Python file (ebb36 S59 N3).
//!
//! The exception list (`docs/policy/python-exceptions.txt`) pins a path to one git blob. Changing
//! the file again gives another blob, which is refused again, so an exception cannot turn into a
//! permanent pass for a path. CI reads the list from the base version: a pull request cannot
//! approve its own Python by adding a line next to it.
//!
//! exit: 0 nothing refused, 1 Python refused, 2 cannot tell (git failed, bad list line, bad usage).

use std::path::Path;
use std::process::Command;

/// The exception list, relative to the repository root.
pub const LIST: &str = "docs/policy/python-exceptions.txt";

/// What counts as Python (compared without case: Windows checkouts keep `.PY` as written).
const EXTS: &[&str] = &[".py", ".pyw", ".pyi"];

pub fn is_python(path: &str) -> bool {
    let l = path.to_ascii_lowercase();
    EXTS.iter().any(|e| l.ends_with(e))
}

/// One approved file: this path with exactly this blob.
#[derive(Debug, PartialEq, Eq)]
pub struct Exception {
    pub path: String,
    pub blob: String,
}

/// Parse the list: `path<TAB>blob(40 hex)<TAB>date<TAB>where Andy said it`, `#` comments and
/// blank lines ignored. Any other line is an error, never skipped (a typo must not pass Python).
pub fn parse_list(text: &str) -> Result<Vec<Exception>, String> {
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let t = line.trim_end_matches('\r');
        if t.trim().is_empty() || t.trim_start().starts_with('#') {
            continue;
        }
        let f: Vec<&str> = t.split('\t').collect();
        let ok_blob = |b: &str| b.len() == 40 && b.bytes().all(|c| c.is_ascii_hexdigit());
        match f.as_slice() {
            [path, blob, date, why]
                if !path.is_empty()
                    && ok_blob(blob)
                    && !date.trim().is_empty()
                    && !why.trim().is_empty() =>
            {
                out.push(Exception {
                    path: (*path).to_string(),
                    blob: blob.to_ascii_lowercase(),
                });
            }
            _ => {
                return Err(format!(
                    "{LIST}:{}: want path<TAB>blob(40 hex)<TAB>date<TAB>where approved, got {t:?}",
                    i + 1
                ))
            }
        }
    }
    Ok(out)
}

/// The paths a `git diff -z --name-status` lists as added or changed (renamed / copied: the new
/// path; a type change counts as a change). Deletions are left out.
pub fn changed_paths(z: &[u8]) -> Result<Vec<String>, String> {
    Ok(changed_pairs(z)?.into_iter().map(|(_, new)| new).collect())
}

/// Like [`changed_paths`] but with the name each path had at the left side of the diff: a
/// rename or copy gives its old name, anything else its own (ebb36 S59 ui-words L1).
pub fn changed_pairs(z: &[u8]) -> Result<Vec<(String, String)>, String> {
    let mut it = z
        .split(|b| *b == 0)
        .map(|s| String::from_utf8_lossy(s).into_owned());
    let mut out = Vec::new();
    while let Some(status) = it.next() {
        if status.is_empty() {
            continue;
        }
        let kind = status.as_bytes()[0];
        // `-z` output ends with NUL, so the split ends with "": a path is never empty, and an
        // empty one means the list stopped short (a rename without its new name)
        let mut path = || {
            it.next()
                .filter(|p| !p.is_empty())
                .ok_or_else(|| format!("diff ended after {status:?}"))
        };
        match kind {
            b'A' | b'M' | b'T' => {
                let p = path()?;
                out.push((p.clone(), p));
            }
            b'R' | b'C' => {
                let old = path()?;
                out.push((old, path()?));
            }
            b'D' => {
                let _gone = path()?;
            }
            _ => return Err(format!("unknown diff status {status:?}")),
        }
    }
    Ok(out)
}

pub(crate) fn git(repo: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["-c", "core.quotepath=false", "--no-pager"])
        .args(args)
        .output()
        .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    if !o.status.success() {
        return Err(format!(
            "git {}: {}",
            args.join(" "),
            String::from_utf8_lossy(&o.stderr).trim()
        ));
    }
    Ok(o.stdout)
}

/// The blob id of `path` at `rev`.
fn blob_at(repo: &Path, rev: &str, path: &str) -> Result<String, String> {
    let spec = format!("{rev}:{path}");
    let b = git(repo, &["rev-parse", "--verify", "--quiet", &spec])?;
    Ok(String::from_utf8_lossy(&b).trim().to_ascii_lowercase())
}

/// Python paths in `base...head` the list does not approve, each with why.
pub fn refused(
    repo: &Path,
    base: &str,
    head: &str,
    list: &[Exception],
) -> Result<Vec<String>, String> {
    let range = format!("{base}...{head}");
    let z = git(
        repo,
        &[
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "-z",
            "--name-status",
            "-M",
            &range,
        ],
    )?;
    let mut out = Vec::new();
    for p in changed_paths(&z)? {
        if !is_python(&p) {
            continue;
        }
        let blob = blob_at(repo, head, &p)?;
        // a path may have several approved versions (Andy approved v1, later v2): any of them passes
        if list.iter().any(|e| e.path == p && e.blob == blob) {
            continue;
        }
        let approved: Vec<&str> = list
            .iter()
            .filter(|e| e.path == p)
            .map(|e| e.blob.as_str())
            .collect();
        if approved.is_empty() {
            out.push(format!("{p}: added or changed Python"));
        } else {
            out.push(format!(
                "{p}: content {blob} is not an approved version ({}; an exception covers one version only)",
                approved.join(", ")
            ));
        }
    }
    Ok(out)
}

/// Read the list at `rev` (a missing file is an empty list; an unreadable `rev` is an error).
pub fn list_at(repo: &Path, rev: &str) -> Result<Vec<Exception>, String> {
    let spec = format!("{rev}:{LIST}");
    match git(repo, &["cat-file", "-e", &spec]) {
        Ok(_) => parse_list(&String::from_utf8_lossy(&git(repo, &["show", &spec])?)),
        Err(_) => {
            git(
                repo,
                &[
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    &format!("{rev}^{{commit}}"),
                ],
            )?;
            Ok(Vec::new())
        }
    }
}

/// `no-new-python` arguments after the subcommand, run in `repo`. Returns the exit code.
pub fn run(repo: &Path, args: &[String]) -> u8 {
    let mut base = None;
    let mut head = "HEAD".to_string();
    let mut from = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let v = it.next();
        match (a.as_str(), v) {
            ("--base", Some(v)) => base = Some(v.clone()),
            ("--head", Some(v)) => head = v.clone(),
            ("--exceptions-from", Some(v)) => from = Some(v.clone()),
            _ => {
                eprintln!(
                    "usage: aib-xtask no-new-python --base <ref> [--head <ref>] [--exceptions-from <ref>]"
                );
                return 2;
            }
        }
    }
    let Some(base) = base else {
        eprintln!("no-new-python: --base is required");
        return 2;
    };
    // the list comes from base unless told otherwise: a change cannot approve itself
    let from = from.unwrap_or_else(|| base.clone());
    let list = match list_at(repo, &from) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("no-new-python: cannot tell — {e}");
            return 2;
        }
    };
    match refused(repo, &base, &head, &list) {
        Ok(r) if r.is_empty() => {
            println!(
                "no-new-python: ok ({base}...{head}: no added or changed Python outside {LIST} at {from})"
            );
            0
        }
        Ok(r) => {
            for x in &r {
                eprintln!("no-new-python: {x}");
            }
            eprintln!(
                "no-new-python: {} file(s) refused — Andy 11-13: 程式只能 Rust、沒有 Python;只有 {LIST} 准過的那一版能過",
                r.len()
            );
            1
        }
        Err(e) => {
            eprintln!("no-new-python: cannot tell — {e}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    struct Repo(PathBuf);
    impl Drop for Repo {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    impl Repo {
        fn new(tag: &str) -> Repo {
            let d = std::env::temp_dir().join(format!(
                "aib-xtask-nnp-{tag}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_or(0, |d| d.as_nanos())
            ));
            std::fs::create_dir_all(&d).unwrap();
            let r = Repo(d);
            r.git(&["init", "-q", "-b", "main"]);
            r.put("README.md", "x\n");
            r.put("old.py", "print(1)\n");
            r.commit("base");
            r
        }
        fn git(&self, a: &[&str]) -> String {
            let o = Command::new("git")
                .arg("-C")
                .arg(&self.0)
                .args([
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "core.autocrlf=false",
                ])
                .args(a)
                .output()
                .unwrap();
            assert!(
                o.status.success(),
                "git {a:?}: {}",
                String::from_utf8_lossy(&o.stderr)
            );
            String::from_utf8_lossy(&o.stdout).trim().to_string()
        }
        fn put(&self, p: &str, s: &str) {
            let f = self.0.join(p);
            if let Some(d) = f.parent() {
                std::fs::create_dir_all(d).unwrap();
            }
            std::fs::write(f, s).unwrap();
        }
        fn commit(&self, m: &str) -> String {
            self.git(&["add", "-A"]);
            self.git(&["commit", "-q", "--allow-empty", "-m", m]);
            self.git(&["rev-parse", "HEAD"])
        }
        fn refused(&self, base: &str, list: &[Exception]) -> Vec<String> {
            refused(&self.0, base, "HEAD", list).unwrap()
        }
    }

    #[test]
    fn an_added_python_file_is_refused() {
        let r = Repo::new("add");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("tools/new.py", "print(2)\n");
        r.commit("add");
        let got = r.refused(&b, &[]);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].starts_with("tools/new.py:"), "{got:?}");
    }

    #[test]
    fn a_changed_python_file_outside_the_list_is_refused() {
        let r = Repo::new("mod");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("old.py", "print(3)\n");
        r.commit("mod");
        let got = r.refused(&b, &[]);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].starts_with("old.py:"), "{got:?}");
    }

    #[test]
    fn a_deleted_python_file_is_not_refused() {
        let r = Repo::new("del");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.git(&["rm", "-q", "old.py"]);
        r.commit("del");
        assert!(r.refused(&b, &[]).is_empty());
    }

    #[test]
    fn a_python_file_renamed_in_is_refused_under_its_new_name() {
        let r = Repo::new("ren");
        r.put("notes.txt", "print(4)\nprint(5)\nprint(6)\n");
        let b = r.commit("txt");
        r.git(&["mv", "notes.txt", "run.py"]);
        r.commit("ren");
        let got = r.refused(&b, &[]);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].starts_with("run.py:"), "{got:?}");
    }

    #[test]
    fn an_upper_case_extension_is_python_too() {
        let r = Repo::new("case");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("Tool.PY", "print(7)\n");
        r.commit("case");
        assert_eq!(r.refused(&b, &[]).len(), 1);
    }

    #[test]
    fn the_approved_version_passes_and_the_next_change_is_refused_again() {
        let r = Repo::new("exc");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("old.py", "print(8)\n");
        r.commit("approved");
        let blob = r.git(&["rev-parse", "HEAD:old.py"]);
        let list = vec![Exception {
            path: "old.py".into(),
            blob,
        }];
        assert!(r.refused(&b, &list).is_empty(), "the approved version");
        r.put("old.py", "print(9)\n");
        r.commit("again");
        let got = r.refused(&b, &list);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].contains("one version only"), "{got:?}");
    }

    #[test]
    fn what_base_gained_meanwhile_does_not_count() {
        let r = Repo::new("mb");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.git(&["checkout", "-q", "-b", "side"]);
        r.put("side.rs", "fn main() {}\n");
        r.commit("side");
        r.git(&["checkout", "-q", "main"]);
        r.put("main_only.py", "print(10)\n");
        // a change on main to a file side never touched: a two-dot diff would list it as M on side
        // (ebb36 S59 N1); from the merge base it is not side's change
        r.put("old.py", "print(10.5)\n");
        let main_head = r.commit("main gained and changed python");
        r.git(&["checkout", "-q", "side"]);
        assert!(refused(&r.0, &main_head, "HEAD", &[]).unwrap().is_empty());
        assert!(refused(&r.0, &b, "HEAD", &[]).unwrap().is_empty());
    }

    /// ebb36 S59 L1: Andy approved v1, later v2; both lines stay and v2 must pass.
    #[test]
    fn a_path_with_two_approved_versions_passes_the_second() {
        let r = Repo::new("two");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("old.py", "print(13)\n");
        let v1 = r.commit("v1");
        r.put("old.py", "print(14)\n");
        let v2 = r.commit("v2");
        let blob = |c: &str| r.git(&["rev-parse", &format!("{c}:old.py")]);
        let list = vec![
            Exception {
                path: "old.py".into(),
                blob: blob(&v1),
            },
            Exception {
                path: "old.py".into(),
                blob: blob(&v2),
            },
        ];
        assert!(r.refused(&b, &list).is_empty(), "v2 is approved");
        r.put("old.py", "print(15)\n");
        r.commit("v3");
        let got = r.refused(&b, &list);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(
            got[0].contains(&blob(&v1)) && got[0].contains(&blob(&v2)),
            "{got:?}"
        );
    }

    #[test]
    fn the_list_is_read_from_the_given_revision_only() {
        let r = Repo::new("from");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("x.py", "print(11)\n");
        let blob_rev = r.commit("x");
        let blob = r.git(&["rev-parse", &format!("{blob_rev}:x.py")]);
        r.put(LIST, &format!("x.py\t{blob}\t11-14\tself-approved\n"));
        r.commit("list in the same change");
        assert!(list_at(&r.0, &b).unwrap().is_empty(), "base has no list");
        assert_eq!(list_at(&r.0, "HEAD").unwrap().len(), 1);
        assert!(
            list_at(&r.0, "no-such-ref").is_err(),
            "bad ref is an error, not empty"
        );
    }

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| (*s).to_string()).collect()
    }

    /// A change that adds Python and its own list line is still refused: the list is read from
    /// base unless `--exceptions-from` says otherwise.
    #[test]
    fn a_change_cannot_approve_its_own_python() {
        let r = Repo::new("self");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("x.py", "print(12)\n");
        let c = r.commit("x");
        let blob = r.git(&["rev-parse", &format!("{c}:x.py")]);
        r.put(LIST, &format!("x.py\t{blob}\t11-14\tself-approved\n"));
        r.commit("list in the same change");
        assert_eq!(run(&r.0, &args(&["--base", &b])), 1, "list from base");
        assert_eq!(
            run(&r.0, &args(&["--base", &b, "--exceptions-from", "HEAD"])),
            0,
            "the same line read from HEAD approves it"
        );
    }

    #[test]
    fn run_exit_codes() {
        let r = Repo::new("rc");
        let b = r.git(&["rev-parse", "HEAD"]);
        r.put("a.rs", "fn a() {}\n");
        r.commit("rust only");
        assert_eq!(run(&r.0, &args(&["--base", &b])), 0, "no python");
        assert_eq!(run(&r.0, &args(&[])), 2, "no --base");
        assert_eq!(run(&r.0, &args(&["--base"])), 2, "--base without value");
        assert_eq!(run(&r.0, &args(&["--base", "no-such-ref"])), 2, "bad ref");
        r.put(LIST, "broken line\n");
        r.commit("bad list");
        assert_eq!(
            run(&r.0, &args(&["--base", &b, "--exceptions-from", "HEAD"])),
            2,
            "a bad list line"
        );
    }

    #[test]
    fn a_bad_list_line_is_an_error_not_a_skip() {
        let b = "0".repeat(40);
        assert!(parse_list("# c\n\n").unwrap().is_empty());
        assert_eq!(
            parse_list(&format!("a.py\t{b}\t11-14\tDM x\n"))
                .unwrap()
                .len(),
            1
        );
        assert!(parse_list("a.py\n").is_err());
        assert!(
            parse_list(&format!("a.py\t{b}\t11-14\n")).is_err(),
            "no source"
        );
        assert!(parse_list("a.py\tnothex\t11-14\tDM x\n").is_err());
        assert!(
            parse_list(&format!("a.py\t{b}\t \tDM x\n")).is_err(),
            "blank date"
        );
    }

    #[test]
    fn diff_status_parsing() {
        let z = b"A\0n.py\0M\0m.py\0D\0d.py\0R087\0o.txt\0r.py\0C100\0s.py\0c.py\0T\0t.py\0";
        assert_eq!(
            changed_paths(z).unwrap(),
            ["n.py", "m.py", "r.py", "c.py", "t.py"]
        );
        let pairs = changed_pairs(z).unwrap();
        assert_eq!(pairs[2], ("o.txt".to_string(), "r.py".to_string()));
        assert_eq!(pairs[3], ("s.py".to_string(), "c.py".to_string()));
        assert_eq!(pairs[0], ("n.py".to_string(), "n.py".to_string()));
        assert!(changed_paths(b"X\0x.py\0").is_err());
        assert!(changed_paths(b"R100\0only-old\0").is_err());
    }
}
