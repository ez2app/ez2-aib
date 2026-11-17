//! 步驟沒少(IFACE-mergecheck-ready-7b81e-1117 r2 §2 第 9 項;DECISION-council-nextstep-519d0-1117 §1):
//! after a merge, every step main's workflow file has is still there and no job has two steps
//! of the same name. Moved here from #805 `xtask/src/step_check.rs` (ebb36, S73m; 1ced7 review
//! L1–L3 / M1 kept) so the check runs on the merge tree `ready` computes, not on a hand-merged
//! working tree. Pure: the two sides come in as text.
//!
//! A step is named by its job and its `name:`; without one by `uses:<action>` or `id:<id>`.
//! An allowed removal (`step-removed: JOB/STEP` in the PR description) only changes what
//! `ready` shows (r2 N1: the same account can edit a PR description; slice 2 never auto-merges
//! a PR whose workflow files differ, so an allow line cannot open that door).

use std::collections::BTreeMap;

/// Indent of a line (spaces only; YAML forbids tabs for indentation).
fn indent(line: &str) -> usize {
    line.len() - line.trim_start_matches(' ').len()
}

/// `key: value` of a mapping line (after an optional `- `), the value unquoted. A quoted value
/// ends at its closing quote (a " #" inside it is kept); an unquoted one at " #".
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
    let rest = rest.split(" #").next().unwrap_or(rest);
    let id = rest.trim_end().strip_suffix(':')?;
    (!id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'))
    .then_some(id)
}

/// The steps as `JOB/STEP` in file order (duplicates kept), and the jobs that have `steps:`
/// but gave no step (a layout this reader does not know: never "nothing to compare").
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
                    item_at = Some(ind);
                }
                if ind < at || (ind == at && !item) {
                    steps_at = None;
                } else if item && item_at == Some(ind) {
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
    /// allow lines that match no missing step (refused: a stale list must not hide a later loss)
    pub stale_allow: Vec<String>,
}

impl Report {
    pub fn ok(&self) -> bool {
        self.missing.is_empty() && self.duplicate.is_empty() && self.stale_allow.is_empty()
    }
}

/// main's steps against the merge tree's. `Err` when a side cannot be compared (no step
/// read, or a job whose steps were not read).
pub fn compare(main: &str, merged: &str, allow: &[String]) -> Result<Report, String> {
    for (side, text) in [("主線", main), ("合後", merged)] {
        let (s, empty) = parse(text);
        if s.is_empty() {
            return Err(format!("{side}讀不出任何一步"));
        }
        if let Some(j) = empty.first() {
            return Err(format!("{side}的 job {j} 有 steps: 但讀不出步"));
        }
    }
    let count = |text: &str| {
        let mut m: BTreeMap<String, usize> = BTreeMap::new();
        for s in parse(text).0 {
            *m.entry(s).or_default() += 1;
        }
        m
    };
    let (b, h) = (count(main), count(merged));
    let mut r = Report::default();
    for (s, n) in &b {
        let have = h.get(s).copied().unwrap_or(0);
        // fewer than main had, not only none: two `uses:actions/cache@v4` in one job and one
        // left is a loss
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
    Ok(r)
}

/// `step-removed: JOB/STEP` lines of a PR description (one per line).
pub fn allow_lines(body: &str) -> Vec<String> {
    body.lines()
        .filter_map(|l| l.trim().strip_prefix("step-removed:"))
        .map(|v| v.trim().to_owned())
        .filter(|v| v.contains('/'))
        .collect()
}

#[cfg(test)]
mod tests {
    //! #805 `xtask/src/step_check.rs` tests (37502b60d, 1ced7 S73m M1 L1–L3), ported: `steps`
    //! = `parse(..).0`, `compare` returns `Err` where #805 said rc 2 (`unreadable`); the
    //! argument tests stay behind (no command line here). 5d33a review 9fae2967 N1.
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

    fn steps(t: &str) -> Vec<String> {
        parse(t).0
    }

    fn cmp(a: &str, b: &str, allow: &[String]) -> Report {
        compare(a, b, allow).expect("both sides readable")
    }

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
        let r = cmp(BASE, &head, &[]);
        assert_eq!(r.missing, vec!["rust/fmt"]);
        assert!(!r.ok());
    }

    #[test]
    fn a_step_in_another_job_does_not_stand_in() {
        let head = BASE.replace("      - name: fmt\n        run: cargo fmt\n", "");
        assert!(steps(&head).contains(&"smoke/fmt".to_owned()));
        assert_eq!(cmp(BASE, &head, &[]).missing, vec!["rust/fmt"]);
    }

    #[test]
    fn a_step_doubled_by_the_merge_is_a_duplicate() {
        let head = BASE.replace(
            "      - name: fmt\n        run: cargo fmt\n",
            "      - name: fmt\n        run: cargo fmt\n      - name: fmt\n        run: cargo fmt\n",
        );
        let r = cmp(BASE, &head, &[]);
        assert_eq!(r.duplicate, vec!["rust/fmt"]);
        assert!(!r.ok());
    }

    #[test]
    fn an_allowed_removal_passes_and_a_stale_allow_is_refused() {
        let head = BASE.replace("      - uses: actions/checkout@v4\n", "");
        let allow = vec!["rust/uses:actions/checkout@v4".to_owned()];
        let r = cmp(BASE, &head, &allow);
        assert!(r.ok(), "{r:?}");
        assert_eq!(r.allowed, allow);
        let r = cmp(BASE, BASE, &allow);
        assert_eq!(r.stale_allow, allow);
        assert!(!r.ok());
    }

    #[test]
    fn added_steps_and_the_same_text_pass() {
        let head = BASE.replace(
            "  smoke:\n",
            "      - name: new knife\n        run: true\n  smoke:\n",
        );
        assert!(cmp(BASE, &head, &[]).ok());
        assert!(cmp(BASE, BASE, &[]).ok());
    }

    #[test]
    fn a_renamed_step_is_missing_under_its_old_name() {
        let head = BASE.replace("name: \"knives\"", "name: knives v2");
        assert_eq!(cmp(BASE, &head, &[]).missing, vec!["rust/knives"]);
    }

    #[test]
    fn one_of_two_same_named_steps_lost_is_missing() {
        let two = BASE.replace(
            "      - uses: actions/checkout@v4\n",
            "      - uses: actions/checkout@v4\n      - uses: actions/checkout@v4\n",
        );
        let r = cmp(&two, BASE, &[]);
        assert_eq!(r.missing, vec!["rust/uses:actions/checkout@v4 (2→1)"]);
        assert!(!r.ok());
        let allow = vec!["rust/uses:actions/checkout@v4".to_owned()];
        assert!(cmp(&two, BASE, &allow).ok());
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
        assert!(compare(BASE, text, &[]).is_err_and(|w| w.contains("rust")));
        assert!(compare("name: x\n", BASE, &[]).is_err_and(|w| w.contains("讀不出任何一步")));
        assert!(compare(BASE, BASE, &[]).is_ok());
    }

    #[test]
    fn a_quoted_name_keeps_a_hash_inside_it() {
        let text = "jobs:\n  rust:\n    steps:\n      - name: \"PR #12 check\" # note\n      - name: plain # note\n";
        assert_eq!(steps(text), vec!["rust/PR #12 check", "rust/plain"]);
    }

    #[test]
    fn allow_lines_are_read_one_per_line_and_need_a_job() {
        let body = "text\nstep-removed: rust/fmt\n  step-removed:  smoke/a \nstep-removed: nojob\n";
        assert_eq!(allow_lines(body), vec!["rust/fmt", "smoke/a"]);
    }
}
