// SPDX-License-Identifier: Apache-2.0
//! S73k 新閘看內容(519d0 11-14 12:03, Andy 11-14 10:39 condition): a workflow change that only
//! adds one step for the branch's own crate is sorted as a non-gate without the `not-gate`
//! label and reason. Everything else stays literal (a new gate). Conservative by design: the
//! file is compared as text, line by line, and any doubt is a refusal that says which rule
//! failed. Sorting, not a defence (same-user files).
//!
//! All must hold:
//! 1. the only file that changes a check is one `.github/workflows/*.yml|yaml` that exists at
//!    both the merge base and the head;
//! 2. the head is the base with exactly one block of lines inserted (nothing removed or
//!    changed, so `on:`, `needs:`, `runs-on:`, `if:` and every other step are as they were);
//! 3. the block is exactly one list item `- name: …` with all its other lines indented deeper;
//! 4. it sits in a `steps:` list, among that list's items (not in a matrix or another list);
//! 5. it names crates only as `-p <name>` / `crates/<name>/`, at least one, all of them crates
//!    this branch itself changes; every line that runs `cargo` names `-p`; no `--workspace`;
//! 6. it has no `uses:`, `secrets.`, `GITHUB_ENV`, `GITHUB_PATH` (those reach past the step).

use std::collections::BTreeSet;

/// Whether `gate_files` may be sorted as a non-gate by content. `Ok(text)` says what was added,
/// `Err(text)` names the rule that does not hold.
pub fn judge(
    gate_files: &[String],
    base: Option<&str>,
    head: Option<&str>,
    own_crates: &BTreeSet<String>,
) -> Result<String, String> {
    let [file] = gate_files else {
        return Err(format!(
            "rule 1: {} files change a check, not one workflow",
            gate_files.len()
        ));
    };
    let f = file.replace('\\', "/");
    let wf = f
        .strip_prefix(".github/workflows/")
        .is_some_and(|n| !n.contains('/') && (n.ends_with(".yml") || n.ends_with(".yaml")));
    if !wf {
        return Err(format!("rule 1: {file} is not a workflow"));
    }
    let (Some(base), Some(head)) = (base, head) else {
        return Err(format!(
            "rule 1: {file} is not in both the merge base and the head"
        ));
    };
    let b: Vec<&str> = base.lines().collect();
    let h: Vec<&str> = head.lines().collect();
    let at = inserted_block(&b, &h).ok_or("rule 2: not one block of added lines")?;
    let block = &h[at..at + (h.len() - b.len())];
    let indent = one_step(block)?;
    in_steps_list(&h[..at], indent)?;
    let after = h[at + block.len()..]
        .iter()
        .find(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#'));
    if let Some(l) = after {
        let i = lead(l).ok_or("rule 4: a tab in the indentation after the step")?;
        let next_item = i == indent && l[i..].starts_with("- ");
        if i >= indent && !next_item {
            return Err(
                "rule 4: the line after the step is not the next item or the list end".into(),
            );
        }
    }
    let names = crates_named(block)?;
    if let Some(n) = names.iter().find(|n| !own_crates.contains(*n)) {
        return Err(format!(
            "rule 5: the step names crate {n}, which this branch does not change"
        ));
    }
    let name = block[0].trim_start().trim_start_matches("- name:").trim();
    Ok(format!(
        "one step added ({name}) for {}",
        names.into_iter().collect::<Vec<_>>().join(", ")
    ))
}

/// Start (in `head`) of the one block whose insertion turns `base` into `head`.
fn inserted_block(base: &[&str], head: &[&str]) -> Option<usize> {
    if head.len() <= base.len() {
        return None;
    }
    let p = base.iter().zip(head).take_while(|(x, y)| x == y).count();
    let s = base[p..]
        .iter()
        .rev()
        .zip(head.iter().rev())
        .take_while(|(x, y)| x == y)
        .count();
    (p + s == base.len()).then_some(p)
}

/// Column of leading spaces; None when a tab is in it.
fn lead(l: &str) -> Option<usize> {
    let i = l.len() - l.trim_start().len();
    (!l[..i].contains('\t')).then_some(i)
}

/// The block is one `- name:` item; the column of its dash.
fn one_step(block: &[&str]) -> Result<usize, String> {
    let first = block[0];
    let i = lead(first).ok_or("rule 3: a tab in the indentation")?;
    if !first[i..].starts_with("- name:") {
        return Err("rule 3: the added lines do not start with `- name:`".into());
    }
    for l in &block[1..] {
        if l.trim().is_empty() {
            continue;
        }
        let j = lead(l).ok_or("rule 3: a tab in the indentation")?;
        if j <= i {
            return Err("rule 3: more than one item, or a line outside the step".into());
        }
    }
    Ok(i)
}

/// Above the block: sibling items at `indent` (and their deeper lines), then `steps:`.
fn in_steps_list(above: &[&str], indent: usize) -> Result<(), String> {
    for l in above.iter().rev() {
        let t = l.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        let i = lead(l).ok_or("rule 4: a tab in the indentation above the step")?;
        if i > indent || (i == indent && l[i..].starts_with("- ")) {
            continue;
        }
        if i < indent && t == "steps:" {
            return Ok(());
        }
        return Err("rule 4: the step is not in a `steps:` list".into());
    }
    Err("rule 4: no `steps:` above the step".into())
}

/// Crates the block names (`-p <name>`, `crates/<name>/`); the rule 5 / 6 refusals.
fn crates_named(block: &[&str]) -> Result<BTreeSet<String>, String> {
    let mut names = BTreeSet::new();
    for l in block {
        for bad in ["uses:", "secrets.", "GITHUB_ENV", "GITHUB_PATH"] {
            if l.contains(bad) {
                return Err(format!("rule 6: the step has {bad}"));
            }
        }
        if l.contains("--workspace") {
            return Err("rule 5: the step runs the whole workspace".into());
        }
        let words: Vec<&str> = l
            .split(|c: char| c.is_whitespace() || "\"'`;(){}".contains(c))
            .filter(|w| !w.is_empty())
            .collect();
        if words.contains(&"cargo") && !words.contains(&"-p") {
            return Err("rule 5: a line runs cargo without -p".into());
        }
        for (k, w) in words.iter().enumerate() {
            if *w == "-p" {
                let n = words.get(k + 1).ok_or("rule 5: -p without a name")?;
                names.insert(crate_name(n)?);
            }
        }
        let mut rest = *l;
        while let Some(at) = rest.find("crates/") {
            let tail = &rest[at + "crates/".len()..];
            let n: String = tail
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
                .collect();
            if !tail[n.len()..].starts_with('/') {
                return Err("rule 5: a `crates/` path that names no crate folder".into());
            }
            names.insert(crate_name(&n)?);
            rest = &tail[n.len()..];
        }
    }
    if names.is_empty() {
        return Err("rule 5: the step names no crate".into());
    }
    Ok(names)
}

fn crate_name(n: &str) -> Result<String, String> {
    let ok = !n.is_empty()
        && n.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
    ok.then(|| n.to_owned())
        .ok_or_else(|| format!("rule 5: {n:?} is not a crate name"))
}

/// Crate folders a branch changes: `crates/<name>/…` of merge base..head.
pub fn own_crates(files: &[String]) -> BTreeSet<String> {
    files
        .iter()
        .filter_map(|f| {
            let f = f.replace('\\', "/");
            let rest = f.strip_prefix("crates/")?;
            let (n, more) = rest.split_once('/')?;
            (!n.is_empty() && !more.is_empty()).then(|| n.to_owned())
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: &str = "\
name: aibd
on:
  push:
    branches: [main]
jobs:
  mac:
    runs-on: macos-14
    needs: [lint]
    steps:
      - uses: actions/checkout@v4
      - name: aib-foo knives (macOS)
        if: env.RUN == 'true'
        run: |
          cargo test -p aib-foo --locked
  win:
    runs-on: windows-latest
    strategy:
      matrix:
        include:
          - name: a
            os: x
    steps:
      - name: build
        run: cargo build -p aib-foo
";

    const STEP: &str = "\
      - name: aib-slice knives (macOS)
        if: env.RUN == 'true'
        shell: bash
        run: |
          sl() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs \"$1\" \"$2\" \\
                   -- cargo test -p aib-slice --locked --lib \"$3\"; }
";

    fn own() -> BTreeSet<String> {
        own_crates(&["crates/aib-slice/src/lib.rs".to_string()])
    }

    fn wf() -> Vec<String> {
        vec![".github/workflows/aibd.yml".to_string()]
    }

    /// BASE with `add` put before line `at` (0-based).
    fn insert(at: usize, add: &str) -> String {
        let mut l: Vec<&str> = BASE.lines().collect();
        let a: Vec<&str> = add.lines().collect();
        l.splice(at..at, a);
        l.join("\n") + "\n"
    }

    fn judge_head(head: &str) -> Result<String, String> {
        judge(&wf(), Some(BASE), Some(head), &own())
    }

    #[test]
    fn one_step_for_the_own_crate_is_a_non_gate() {
        // after the last mac step (before `win:`) and between two items
        for at in [14, 10] {
            let r = judge_head(&insert(at, STEP));
            assert_eq!(
                r,
                Ok("one step added (aib-slice knives (macOS)) for aib-slice".into()),
                "at {at}"
            );
        }
    }

    #[test]
    fn anything_but_one_added_step_is_literal() {
        let refused = |head: String, rule: &str| {
            let r = judge_head(&head);
            assert!(
                r.as_ref().is_err_and(|e| e.starts_with(rule)),
                "want {rule}: {r:?}"
            );
        };
        // rule 2: a changed line besides the step (needs:, on:)
        refused(
            insert(14, STEP).replace("needs: [lint]", "needs: []"),
            "rule 2",
        );
        refused(insert(14, STEP).replace("[main]", "[main, dev]"), "rule 2");
        refused(BASE.replace("--locked", ""), "rule 2");
        // rule 3: two steps, or a line back at the job level
        refused(insert(14, &format!("{STEP}{STEP}")), "rule 3");
        refused(insert(14, &format!("{STEP}    env: {{X: 1}}\n")), "rule 3");
        refused(
            insert(14, &STEP.replace("- name:", "- run: x\n        name:")),
            "rule 3",
        );
        // rule 4: in the matrix list (an item of `include:`, not of `steps:`), at the job
        // level, or as a step's child
        refused(
            insert(
                21,
                "          - name: c\n            run: cargo x -p aib-slice\n",
            ),
            "rule 4",
        );
        refused(insert(14, &STEP.replace("      ", "    ")), "rule 4");
        refused(insert(13, STEP), "rule 4");
        // rule 5: another crate, no crate, cargo without -p, the whole workspace
        refused(insert(14, &STEP.replace("aib-slice", "aib-foo")), "rule 5");
        refused(
            insert(14, "      - name: e\n        run: echo hi\n"),
            "rule 5",
        );
        refused(
            insert(14, &format!("{STEP}          cargo test --locked\n")),
            "rule 5",
        );
        refused(
            insert(
                14,
                &STEP.replace("-p aib-slice", "--workspace -p aib-slice"),
            ),
            "rule 5",
        );
        // rule 6: an action, a secret, the job's environment
        for bad in [
            "uses: x/y@v1",
            "echo ${{ secrets.T }}",
            "echo A=1 >> $GITHUB_ENV",
            "echo p >> $GITHUB_PATH",
        ] {
            refused(insert(14, &format!("{STEP}          {bad}\n")), "rule 6");
        }
    }

    #[test]
    fn only_one_workflow_present_on_both_sides_is_judged() {
        let head = insert(14, STEP);
        let r = |files: Vec<&str>, base: Option<&str>| {
            let files: Vec<String> = files.into_iter().map(str::to_owned).collect();
            judge(&files, base, Some(&head), &own())
        };
        assert!(r(
            vec![".github/workflows/aibd.yml", "xtask/src/main.rs"],
            Some(BASE)
        )
        .is_err_and(|e| e.starts_with("rule 1")));
        assert!(r(vec!["xtask/src/main.rs"], Some(BASE)).is_err_and(|e| e.starts_with("rule 1")));
        assert!(r(vec![".github/workflows/sub/x.yml"], Some(BASE))
            .is_err_and(|e| e.starts_with("rule 1")));
        assert!(r(vec![".github/workflows/aibd.yml"], None).is_err_and(|e| e.starts_with("rule 1")));
        assert!(r(vec![".github\\workflows\\aibd.yml"], Some(BASE)).is_ok());
    }

    #[test]
    fn own_crates_are_the_crate_folders_the_branch_changes() {
        let f: Vec<String> = [
            "crates/aib-a/src/x.rs",
            "crates/aib-b/Cargo.toml",
            "crates/README.md",
            "aib/x",
        ]
        .map(str::to_owned)
        .to_vec();
        assert_eq!(
            own_crates(&f).into_iter().collect::<Vec<_>>(),
            vec!["aib-a".to_string(), "aib-b".to_string()]
        );
    }
}
