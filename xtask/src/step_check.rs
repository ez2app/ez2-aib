//! `aib-xtask step-check <base> [--allow JOB/STEP]...`: after a merge of main that
//! resolved `.github/workflows/aibd.yml` by hand (or by rerere), every step the base
//! has is still there, and no job has two steps of the same name (S73m, RULES §25;
//! #710's stalehead knife step was deleted whole by a replayed old resolution, and a
//! knife that does not run never goes red).
//!
//! A step is named by its job and its `name:`; a step without one by `uses:<action>`
//! or `id:<id>`. The base side is read with `git show <base>:<file>`, the head side
//! from the working tree. A step the merge removes on purpose (a PR replacing it) is
//! passed with `--allow JOB/STEP` and printed as `ALLOWED`; an `--allow` that matches
//! nothing missing is refused, so a stale allow list cannot hide a later loss.
//!
//! rc 0 = nothing missing, no duplicate; 1 = missing or duplicate (each printed);
//! 2 = a side could not be read or the arguments are wrong. The
//! `git diff --numstat <base> -- <file>` line is printed for the review request.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

pub const WORKFLOW: &str = ".github/workflows/aibd.yml";

/// Indent of a line (spaces only; YAML forbids tabs for indentation).
fn indent(line: &str) -> usize {
    line.len() - line.trim_start_matches(' ').len()
}

/// `key: value` of a mapping line (after an optional `- `), the value unquoted. A
/// quoted value ends at its closing quote (a " #" inside it is kept); an unquoted one
/// at " #" (1ced7 S73m L3).
fn field<'l>(line: &'l str, key: &str) -> Option<&'l str> {
    let t = line.trim_start();
    let t = t.strip_prefix("- ").unwrap_or(t);
    let v = t.strip_prefix(key)?.strip_prefix(':')?.trim();
    for q in ['"', '\''] {
        if let Some(rest) = v.strip_prefix(q) {
            return Some(rest.find(q).map_or(rest, |e| &rest[..e]));
        }
    }
    Some(v.split(" #").next().unwrap_or(v).trim_end())
}

/// `  ID:` with nothing after the colon, right under `jobs:` ⇒ the job id.
fn job_key(line: &str) -> Option<&str> {
    let rest = line.strip_prefix("  ")?;
    if rest.starts_with(' ') || rest.starts_with('#') {
        return None;
    }
    // a comment after the key is allowed (1ced7 S73m L1)
    let rest = rest.split(" #").next().unwrap_or(rest);
    let id = rest.trim_end().strip_suffix(':')?;
    (!id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'))
    .then_some(id)
}

/// Every step as `JOB/STEP`, in file order (duplicates kept).
pub fn steps(text: &str) -> Vec<String> {
    parse(text).0
}

/// The steps, and the jobs that have `steps:` but gave no step (a layout this reader
/// does not know: never read as "nothing to compare", 1ced7 S73m L1).
pub fn parse(text: &str) -> (Vec<String>, Vec<String>) {
    let lines: Vec<&str> = text.lines().collect();
    let mut out = Vec::new();
    let mut per_job: BTreeMap<String, usize> = BTreeMap::new();
    let mut in_jobs = false;
    let mut job: Option<&str> = None;
    let mut steps_at: Option<usize> = None;
    let mut item_at: Option<usize> = None;
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        let t = line.trim_start();
        if t.is_empty() || t.starts_with('#') {
            i += 1;
            continue;
        }
        if indent(line) == 0 {
            in_jobs = line.trim_end() == "jobs:";
            job = None;
            steps_at = None;
        } else if in_jobs {
            if let Some(j) = job_key(line) {
                job = Some(j);
                steps_at = None;
            } else if let (Some(j), "steps:") = (job, t.trim_end()) {
                steps_at = Some(indent(line));
                item_at = None;
                per_job.entry(j.to_owned()).or_insert(0);
            } else if let (Some(j), Some(at)) = (job, steps_at) {
                let ind = indent(line);
                let item = t.starts_with("- ");
                if item && item_at.is_none() {
                    // the first item sets the list's indent (at, at + 2, at + 4 …)
                    item_at = Some(ind);
                }
                if ind < at || (ind == at && !item) {
                    steps_at = None;
                } else if item && item_at == Some(ind) {
                    // one step: this line and the deeper lines under it
                    let mut name = field(line, "name");
                    let mut uses = field(line, "uses");
                    let mut id = field(line, "id");
                    let mut k = i + 1;
                    while k < lines.len() {
                        let l = lines[k];
                        let lt = l.trim_start();
                        if !lt.is_empty() && !lt.starts_with('#') && indent(l) <= ind {
                            break;
                        }
                        if indent(l) == ind + 2 {
                            name = name.or_else(|| field(l, "name"));
                            uses = uses.or_else(|| field(l, "uses"));
                            id = id.or_else(|| field(l, "id"));
                        }
                        k += 1;
                    }
                    let label = match (name, uses, id) {
                        (Some(n), _, _) => n.to_owned(),
                        (None, Some(u), _) => format!("uses:{u}"),
                        (None, None, Some(d)) => format!("id:{d}"),
                        (None, None, None) => format!("line:{}", i + 1),
                    };
                    out.push(format!("{j}/{label}"));
                    *per_job.entry(j.to_owned()).or_default() += 1;
                    i = k;
                    continue;
                }
            }
        }
        i += 1;
    }
    let empty = per_job
        .into_iter()
        .filter(|(_, n)| *n == 0)
        .map(|(j, _)| j)
        .collect();
    (out, empty)
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub missing: Vec<String>,
    pub allowed: Vec<String>,
    pub duplicate: Vec<String>,
    /// `--allow` entries that match no missing step (refused).
    pub stale_allow: Vec<String>,
}

impl Report {
    pub fn ok(&self) -> bool {
        self.missing.is_empty() && self.duplicate.is_empty() && self.stale_allow.is_empty()
    }
}

/// Compare the base's steps with the head's.
pub fn compare(base: &str, head: &str, allow: &[String]) -> Report {
    let count = |text: &str| {
        let mut m: BTreeMap<String, usize> = BTreeMap::new();
        for s in steps(text) {
            *m.entry(s).or_default() += 1;
        }
        m
    };
    let (b, h) = (count(base), count(head));
    let mut r = Report::default();
    for (s, n) in &b {
        let have = h.get(s).copied().unwrap_or(0);
        // fewer than the base had, not only none: two `uses:actions/cache@v4` in one
        // job and one left is a loss (1ced7 S73m M1)
        if have < *n {
            if allow.iter().any(|a| a == s) {
                r.allowed.push(s.clone());
            } else if *n > 1 {
                r.missing.push(format!("{s} ({n}→{have})"));
            } else {
                r.missing.push(s.clone());
            }
        } else if have > 1 && have > *n {
            r.duplicate.push(s.clone());
        }
    }
    for (s, n) in &h {
        if *n > 1 && !b.contains_key(s) {
            r.duplicate.push(s.clone());
        }
    }
    r.duplicate.sort();
    for a in allow {
        if !r.allowed.contains(a) {
            r.stale_allow.push(a.clone());
        }
    }
    r
}

fn git(root: &Path, args: &[&str]) -> Option<String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
}

pub struct Args {
    pub base: String,
    pub allow: Vec<String>,
    /// The workflow file, relative to the root (default [`WORKFLOW`]; 1ced7 S73m L2).
    pub file: String,
}

/// `step-check` arguments: the base ref, `--allow JOB/STEP` any number of times,
/// `--file PATH` once.
pub fn parse_args(rest: &[String]) -> Result<Args, String> {
    let mut base = None;
    let mut allow = Vec::new();
    let mut file = None;
    let mut it = rest.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--file" => match it.next() {
                Some(v) if file.is_none() && !v.is_empty() && !v.starts_with('-') => {
                    file = Some(v.clone())
                }
                _ => return Err("--file wants one PATH".to_owned()),
            },
            "--allow" => match it.next() {
                Some(v) if v.contains('/') => allow.push(v.clone()),
                _ => return Err("--allow wants JOB/STEP".to_owned()),
            },
            s if s.starts_with('-') => return Err(format!("unknown option {s}")),
            s if base.is_none() => base = Some(s.to_owned()),
            s => return Err(format!("one base only, got another: {s}")),
        }
    }
    base.map(|base| Args {
        base,
        allow,
        file: file.unwrap_or_else(|| WORKFLOW.to_owned()),
    })
    .ok_or_else(|| {
        "usage: aib-xtask step-check <base> [--allow JOB/STEP]... [--file PATH]".to_owned()
    })
}

/// rc 2 text when a side cannot be compared: no step at all, or a job whose steps
/// were not read.
fn unreadable(side: &str, text: &str) -> Option<String> {
    let (s, empty) = parse(text);
    if s.is_empty() {
        Some(format!("{side}: no step read"))
    } else if !empty.is_empty() {
        Some(format!(
            "{side}: jobs with steps: but none read: {}",
            empty.join(" ")
        ))
    } else {
        None
    }
}

pub fn run(root: &Path, a: &Args) -> u8 {
    let (base, allow, file) = (a.base.as_str(), a.allow.as_slice(), a.file.as_str());
    let Some(old) = git(root, &["show", &format!("{base}:{file}")]) else {
        eprintln!("step-check: cannot read {file} at {base}");
        return 2;
    };
    let Ok(new) = std::fs::read_to_string(root.join(file)) else {
        eprintln!("step-check: cannot read {file} in the working tree");
        return 2;
    };
    if let Some(why) = unreadable("base", &old).or_else(|| unreadable("head", &new)) {
        println!("UNREAD {why}");
        return 2;
    }
    let r = compare(&old, &new, allow);
    for s in &r.missing {
        println!("MISSING {s}");
    }
    for s in &r.duplicate {
        println!("DUPLICATE {s}");
    }
    for s in &r.allowed {
        println!("ALLOWED {s}");
    }
    for s in &r.stale_allow {
        println!("STALE-ALLOW {s} (not missing; drop it)");
    }
    let numstat = git(root, &["diff", "--numstat", base, "--", file]).unwrap_or_default();
    println!(
        "NUMSTAT {}",
        numstat.split_whitespace().collect::<Vec<_>>().join(" ")
    );
    println!(
        "STEPS base={} head={}",
        steps(&old).len(),
        steps(&new).len()
    );
    if r.ok() {
        println!("STEP-CHECK-OK");
        0
    } else {
        1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: &str = "name: x
on:
  push:
jobs:
  rust:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: fmt
        run: cargo fmt
      - id: c
        name: closure judge
        run: |
          echo hi
          - name: not a step (inside a block)
      - name: \"knives\"   # quoted
        run: true
  smoke:
    steps:
    - name: fmt
      run: true
";

    #[test]
    fn steps_are_named_by_job_and_name_or_uses() {
        assert_eq!(
            steps(BASE),
            vec![
                "rust/uses:actions/checkout@v4",
                "rust/fmt",
                "rust/closure judge",
                "rust/knives",
                "smoke/fmt",
            ]
        );
    }

    #[test]
    fn a_step_dropped_by_the_merge_is_missing() {
        let head = BASE.replace("      - name: fmt\n        run: cargo fmt\n", "");
        let r = compare(BASE, &head, &[]);
        assert_eq!(r.missing, vec!["rust/fmt"]);
        assert!(!r.ok());
    }

    #[test]
    fn a_step_in_another_job_does_not_stand_in() {
        // smoke/fmt is still there; rust/fmt is not
        let head = BASE.replace("      - name: fmt\n        run: cargo fmt\n", "");
        assert!(steps(&head).contains(&"smoke/fmt".to_owned()));
        assert_eq!(compare(BASE, &head, &[]).missing, vec!["rust/fmt"]);
    }

    #[test]
    fn a_step_doubled_by_the_merge_is_a_duplicate() {
        let head = BASE.replace(
            "      - name: fmt\n        run: cargo fmt\n",
            "      - name: fmt\n        run: cargo fmt\n      - name: fmt\n        run: cargo fmt\n",
        );
        let r = compare(BASE, &head, &[]);
        assert_eq!(r.duplicate, vec!["rust/fmt"]);
        assert!(!r.ok());
    }

    #[test]
    fn an_allowed_removal_passes_and_a_stale_allow_is_refused() {
        let head = BASE.replace("      - uses: actions/checkout@v4\n", "");
        let allow = vec!["rust/uses:actions/checkout@v4".to_owned()];
        let r = compare(BASE, &head, &allow);
        assert!(r.ok(), "{r:?}");
        assert_eq!(r.allowed, allow);
        let r = compare(BASE, BASE, &allow);
        assert_eq!(r.stale_allow, allow);
        assert!(!r.ok());
    }

    #[test]
    fn added_steps_and_the_same_text_pass() {
        let head = BASE.replace(
            "  smoke:\n",
            "      - name: new knife\n        run: true\n  smoke:\n",
        );
        assert!(compare(BASE, &head, &[]).ok());
        assert!(compare(BASE, BASE, &[]).ok());
    }

    #[test]
    fn a_renamed_step_is_missing_under_its_old_name() {
        let head = BASE.replace("name: \"knives\"", "name: knives v2");
        assert_eq!(compare(BASE, &head, &[]).missing, vec!["rust/knives"]);
    }

    #[test]
    fn args_take_one_base_and_allows() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let got = parse_args(&a(&["origin/main", "--allow", "rust/fmt"])).expect("ok");
        assert_eq!(
            (got.base.as_str(), got.allow, got.file.as_str()),
            ("origin/main", vec!["rust/fmt".to_owned()], WORKFLOW)
        );
        let got = parse_args(&a(&["b", "--file", ".github/workflows/ci.yml"])).expect("ok");
        assert_eq!(got.file, ".github/workflows/ci.yml");
        assert!(parse_args(&a(&["b", "--file"])).is_err());
        assert!(parse_args(&a(&["b", "--file", "x", "--file", "y"])).is_err());
        assert!(parse_args(&a(&[])).is_err());
        assert!(parse_args(&a(&["a", "b"])).is_err());
        assert!(parse_args(&a(&["a", "--allow", "nojob"])).is_err());
        assert!(parse_args(&a(&["a", "--x"])).is_err());
    }

    #[test]
    fn one_of_two_same_named_steps_lost_is_missing() {
        let two = BASE.replace(
            "      - uses: actions/checkout@v4\n",
            "      - uses: actions/checkout@v4\n      - uses: actions/checkout@v4\n",
        );
        let r = compare(&two, BASE, &[]);
        assert_eq!(r.missing, vec!["rust/uses:actions/checkout@v4 (2→1)"]);
        assert!(!r.ok());
        let allow = vec!["rust/uses:actions/checkout@v4".to_owned()];
        assert!(compare(&two, BASE, &allow).ok());
    }

    #[test]
    fn a_job_line_with_a_comment_and_four_space_items_are_read() {
        let text = "jobs:\n  rust: # the main job\n    steps:\n        - name: fmt\n          run: x\n        - uses: a@v1\n";
        assert_eq!(steps(text), vec!["rust/fmt", "rust/uses:a@v1"]);
        assert!(parse(text).1.is_empty());
    }

    #[test]
    fn a_job_whose_steps_are_not_read_is_unreadable_not_ok() {
        // `steps: []` is an empty list, not a layout this reader misses
        let text = "jobs:\n  rust:\n    steps: []\n  smoke:\n    steps:\n      - name: a\n";
        assert_eq!(steps(text), vec!["smoke/a"]);
        let text =
            "jobs:\n  rust:\n    steps:\n    runs-on: x\n  smoke:\n    steps:\n      - name: a\n";
        assert_eq!(parse(text).1, vec!["rust"]);
        assert!(unreadable("head", text).is_some_and(|w| w.contains("rust")));
        assert!(unreadable("base", "name: x\n").is_some_and(|w| w.contains("no step")));
        assert!(unreadable("base", BASE).is_none());
    }

    #[test]
    fn a_quoted_name_keeps_a_hash_inside_it() {
        let text = "jobs:\n  rust:\n    steps:\n      - name: \"PR #12 check\" # note\n      - name: plain # note\n";
        assert_eq!(steps(text), vec!["rust/PR #12 check", "rust/plain"]);
    }
}
