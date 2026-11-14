// SPDX-License-Identifier: Apache-2.0
//! S73k 新閘看內容(519d0 11-14 12:03, Andy 11-14 10:39 condition): a workflow change that only
//! adds one knife step for the branch's own crate is sorted as a non-gate without the
//! `not-gate` label and reason. Everything else stays literal (a new gate). Conservative by
//! design: the file is compared as text, line by line, the step must have the one shape the
//! knife steps have (a list of what is taken, not of what is refused: a9136 review M2 / M3),
//! and any doubt is a refusal that says which rule failed. Sorting, not a defence (same-user
//! files).
//!
//! All must hold:
//! 1. the only file that changes a check is `.github/workflows/aibd.yml` (read-only token,
//!    no job with write permission: a9136 M1), present at both the merge base and the head;
//! 2. the head is the base with exactly one block of lines inserted (nothing removed or
//!    changed, so `on:`, `needs:`, `runs-on:`, `if:` and every other step are as they were);
//! 3. the block is exactly one list item `- name: …` (comment lines at its indentation may
//!    come first) with all its other lines indented deeper, by plain spaces only;
//! 4. it sits in a `steps:` list, among that list's items (not in a matrix or another list),
//!    and what follows it is the next item or the end of the list;
//! 5. the step is a knife step and nothing else: the keys `name`, `if` (the two forms the
//!    knife steps use), `shell: bash`, `timeout-minutes`, then `run: |` whose lines are only
//!    blank, `# comment`, `set -eu`, a helper `x() { python3 -I scripts/knife.py crates/<c>/…
//!    "$N"… \` + `-- cargo test -p <c> --locked … "$N"; }`, calls of such a helper with
//!    quoted text and plain names (nothing the shell expands), and `cargo test -p <c> --locked
//!    …`; at least one crate is named and every `<c>` is a crate this branch itself changes;
//! 6. it has no `${{ }}` expression.

use std::collections::BTreeSet;

/// The one workflow judged by content (rule 1).
pub const ONLY: &str = ".github/workflows/aibd.yml";

/// Where a GitHub expression starts (rule 6). A constant, so that no knife line in aibd.yml
/// has to spell it: Actions would evaluate it there.
const EXPR: &str = "${{";

/// What the shell expands inside double quotes (a `\` before `"` or `\` is taken first).
const EXPANDS: [char; 3] = ['$', '`', '\\'];

/// The `if:` lines a knife step may carry.
const IF_OK: [&str; 2] = [
    "if: env.RUN == 'true' && runner.os == 'macOS'",
    "if: env.RUN == 'true'",
];

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
    if file.replace('\\', "/") != ONLY {
        return Err(format!("rule 1: {file} is not {ONLY}"));
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
    let (indent, first) = one_step(block)?;
    in_steps_list(&h[..at], indent)?;
    let after = h[at + block.len()..]
        .iter()
        .find(|l| !l.trim().is_empty() && !outer_comment(l, indent));
    if let Some(l) = after {
        let i = lead(l).ok_or("rule 4: the indentation after the step is not plain spaces")?;
        let next_item = i == indent && l[i..].starts_with("- ");
        if i >= indent && !next_item {
            return Err(
                "rule 4: the line after the step is not the next item or the list end".into(),
            );
        }
    }
    if block.iter().any(|l| l.contains(EXPR)) {
        return Err("rule 6: the step has a `${{ }}` expression".into());
    }
    let names = step_shape(&block[first..], indent)?;
    if let Some(n) = names.iter().find(|n| !own_crates.contains(*n)) {
        return Err(format!(
            "rule 5: the step names crate {n}, which this branch does not change"
        ));
    }
    let name = block[first]
        .trim_start()
        .trim_start_matches("- name:")
        .trim();
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

/// Column of the first character that is not a space; None when the indentation goes on with
/// other whitespace (a tab, NBSP: YAML takes ASCII spaces only, a9136 L3).
fn lead(l: &str) -> Option<usize> {
    let i = l.len() - l.trim_start_matches(' ').len();
    let plain = l[i..].chars().next().is_none_or(|c| !c.is_whitespace());
    plain.then_some(i)
}

/// A `#` line at the list's indentation or left of it: a YAML comment between items. Deeper
/// than that it may be the text of a `run: |` block, which is somebody's content (a9136 L1).
fn outer_comment(l: &str, indent: usize) -> bool {
    l.trim_start().starts_with('#') && lead(l).is_some_and(|i| i <= indent)
}

/// The block is one `- name:` item, after comment lines at its indentation at most:
/// (column of its dash, index of the `- name:` line).
fn one_step(block: &[&str]) -> Result<(usize, usize), String> {
    let spaces = "rule 3: the indentation is not plain spaces";
    let first = block
        .iter()
        .position(|l| !l.trim_start().starts_with('#'))
        .ok_or("rule 3: only comment lines were added")?;
    let i = lead(block[first]).ok_or(spaces)?;
    if !block[first][i..].starts_with("- name:") {
        return Err("rule 3: the added lines do not start with `- name:`".into());
    }
    if block[..first].iter().any(|l| lead(l) != Some(i)) {
        return Err("rule 3: a comment above the step is not at its indentation".into());
    }
    for l in &block[first + 1..] {
        if l.trim().is_empty() {
            continue;
        }
        let j = lead(l).ok_or(spaces)?;
        if j <= i {
            return Err("rule 3: more than one item, or a line outside the step".into());
        }
    }
    Ok((i, first))
}

/// Above the block: sibling items at `indent` (and their deeper lines), then `steps:`.
fn in_steps_list(above: &[&str], indent: usize) -> Result<(), String> {
    for l in above.iter().rev() {
        let t = l.trim();
        if t.is_empty() || outer_comment(l, indent) {
            continue;
        }
        let i = lead(l).ok_or("rule 4: the indentation above the step is not plain spaces")?;
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

/// Rule 5: `step[0]` is the `- name:` line at `indent`. The crates the step names.
fn step_shape(step: &[&str], indent: usize) -> Result<BTreeSet<String>, String> {
    let key = indent + 2;
    let mut run_at = None;
    for (n, l) in step.iter().enumerate().skip(1) {
        let t = l.trim();
        if t.is_empty() {
            continue;
        }
        if lead(l) != Some(key) {
            return Err(format!("rule 5: `{t}` is not a key of the step"));
        }
        if t == "run: |" {
            run_at = Some(n + 1);
            break;
        }
        if !key_ok(t) {
            return Err(format!(
                "rule 5: the step has `{t}`; a knife step has name, if, shell: bash, timeout-minutes, run: |"
            ));
        }
    }
    let body = &step[run_at.ok_or("rule 5: the step has no `run: |`")?..];
    run_lines(body, key)
}

fn key_ok(t: &str) -> bool {
    IF_OK.contains(&t)
        || t == "shell: bash"
        || t.strip_prefix("timeout-minutes: ")
            .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}

/// The lines of `run: |` (rule 5). The crates they name.
fn run_lines(body: &[&str], key: usize) -> Result<BTreeSet<String>, String> {
    let mut names = BTreeSet::new();
    let mut helpers: BTreeSet<&str> = BTreeSet::new();
    let mut cont = false; // the line before ended with `\`
    let mut open = false; // the line before opened a helper
    for l in body {
        let t = l.trim();
        if t.is_empty() && !cont && !open {
            continue;
        }
        if lead(l).is_none_or(|i| i <= key) {
            return Err(format!(
                "rule 5: `{t}` comes after `run: |` and is not in it"
            ));
        }
        if open {
            names.insert(cargo_tail(t.strip_prefix("-- ").unwrap_or(""), true)?);
            open = false;
        } else if cont {
            cont = words(t)?;
        } else if let Some((name, krate)) = helper_open(t)? {
            helpers.insert(name);
            names.insert(krate);
            open = true;
        } else if let Some(rest) = call_of(t, &helpers) {
            cont = words(rest)?;
        } else if t.starts_with("cargo ") {
            names.insert(cargo_tail(t, false)?);
        } else if !t.starts_with('#') && t != "set -eu" {
            return Err(format!(
                "rule 5: a run line is not a knife call or `cargo test -p`: {t}"
            ));
        }
    }
    if cont || open {
        return Err("rule 5: the run block ends in the middle of a command".into());
    }
    if names.is_empty() {
        return Err("rule 5: the step names no crate".into());
    }
    Ok(names)
}

/// `name() { python3 -I scripts/knife.py crates/<crate>/<path> "$N"… \`: (name, crate). None
/// when the line does not open a helper at all.
fn helper_open(t: &str) -> Result<Option<(&str, String)>, String> {
    let Some((name, rest)) = t.split_once("() { ") else {
        return Ok(None);
    };
    if !ident(name, "") || name.len() > 4 {
        return Ok(None);
    }
    let bad =
        || format!("rule 5: a helper that does not run scripts/knife.py on a crate file: {t}");
    let rest = rest
        .strip_prefix("python3 -I scripts/knife.py ")
        .ok_or_else(bad)?;
    let mut w = rest.split(' ');
    let file = w.next().ok_or_else(bad)?;
    let file = file
        .strip_prefix('"')
        .and_then(|f| f.strip_suffix('"'))
        .unwrap_or(file);
    let path = file.strip_prefix("crates/").ok_or_else(bad)?;
    let (krate, more) = path.split_once('/').ok_or_else(bad)?;
    let more = more.strip_suffix("$1").unwrap_or(more);
    if !ident(krate, "-_") || !ident(more, "-_./") || more.contains("..") {
        return Err(bad());
    }
    let params: Vec<&str> = w.collect();
    let ok = params.last() == Some(&"\\")
        && params[..params.len() - 1]
            .iter()
            .all(|p| *p == "--expect-fail" || param(p));
    if !ok {
        return Err(bad());
    }
    Ok(Some((name, krate.to_owned())))
}

/// `cargo test -p <crate> --locked …`: the crate. `params`: the line is a helper's second
/// one, so `"$N"` may stand for a name and it closes with `; }`.
fn cargo_tail(t: &str, params: bool) -> Result<String, String> {
    let bad = || format!("rule 5: not `cargo test -p <crate> --locked` with plain names: {t}");
    let rest = t.strip_prefix("cargo test -p ").ok_or_else(bad)?;
    let rest = if params {
        rest.strip_suffix("; }").ok_or_else(bad)?
    } else {
        rest
    };
    let mut w = rest.split(' ');
    let krate = w.next().filter(|k| ident(k, "-_")).ok_or_else(bad)?;
    let ok = w.next() == Some("--locked") && w.all(|x| word_ok(x, params));
    if !ok {
        return Err(bad());
    }
    Ok(krate.to_owned())
}

/// What may follow `--locked`: which tests to run, by plain names.
fn word_ok(x: &str, params: bool) -> bool {
    x == "--lib" || x == "--test" || ident(x, "_") || (params && param(x))
}

/// Not empty, ASCII letters and digits plus `extra`.
fn ident(s: &str, extra: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || extra.contains(c))
}

/// `"$1"` … `"$9"`.
fn param(p: &str) -> bool {
    p.strip_prefix("\"$")
        .and_then(|r| r.strip_suffix('"'))
        .is_some_and(|d| d.len() == 1 && d != "0" && d.bytes().all(|b| b.is_ascii_digit()))
}

/// The arguments when `t` calls one of `helpers`.
fn call_of<'a>(t: &'a str, helpers: &BTreeSet<&str>) -> Option<&'a str> {
    let (name, rest) = t.split_once(' ')?;
    helpers.contains(name).then_some(rest)
}

/// Arguments of a knife call: 'single quoted', "double quoted" (no `$`, no backtick, `\` only
/// before `"` or `\`) and plain names, one space or more apart. Nothing the shell expands or
/// runs. A final `\` goes on with the next line (true).
fn words(s: &str) -> Result<bool, String> {
    let bad = |why: &str| format!("rule 5: a knife call {why}: {s}");
    let plain = |c: char| c.is_ascii_alphanumeric() || "_.-".contains(c);
    let mut c = s.chars().peekable();
    loop {
        while c.next_if_eq(&' ').is_some() {}
        let Some(q) = c.next() else {
            return Ok(false);
        };
        match q {
            '\'' => {
                if !c.by_ref().any(|x| x == '\'') {
                    return Err(bad("has an open quote"));
                }
            }
            '"' => loop {
                match c.next() {
                    Some('"') => break,
                    Some('\\') if matches!(c.next(), Some('"' | '\\')) => {}
                    Some(x) if EXPANDS.contains(&x) => {
                        return Err(bad("expands something"));
                    }
                    None => return Err(bad("has an open quote")),
                    Some(_) => {}
                }
            },
            '\\' if c.peek().is_none() => return Ok(true),
            x if plain(x) => while c.next_if(|y| plain(*y)).is_some() {},
            _ => return Err(bad("has shell syntax")),
        }
        if c.peek().is_some_and(|x| *x != ' ') {
            return Err(bad("joins two words"));
        }
    }
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

    // raw strings that start right after the quote: a `\` line continuation would eat the
    // first line's indentation
    const BASE: &str = r#"name: aibd
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
"#;

    const STEP: &str = r#"      - name: aib-slice knives (macOS)
        if: env.RUN == 'true' && runner.os == 'macOS'
        shell: bash
        run: |
          set -eu
          sl() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs "$1" "$2" --expect-fail "$3" \
                   -- cargo test -p aib-slice --locked --lib "$3"; }
          # SK1 what the knife takes away
          sl '    if a.is_empty() {' \
             "    if a.is_empty() && b == \"x\" { // knife" \
             a_test_name
"#;

    fn own() -> BTreeSet<String> {
        own_crates(&["crates/aib-slice/src/lib.rs".to_string()])
    }

    fn wf() -> Vec<String> {
        vec![ONLY.to_string()]
    }

    /// `base` with `add` put before line `at` (0-based).
    fn insert_into(base: &str, at: usize, add: &str) -> String {
        let mut l: Vec<&str> = base.lines().collect();
        let a: Vec<&str> = add.lines().collect();
        l.splice(at..at, a);
        l.join("\n") + "\n"
    }

    fn insert(at: usize, add: &str) -> String {
        insert_into(BASE, at, add)
    }

    /// STEP with one more line in its run block.
    fn with_run(line: &str) -> String {
        format!("{STEP}          {line}\n")
    }

    fn judge_head(head: &str) -> Result<String, String> {
        judge(&wf(), Some(BASE), Some(head), &own())
    }

    #[test]
    fn one_step_for_the_own_crate_is_a_non_gate() {
        let ok = Ok("one step added (aib-slice knives (macOS)) for aib-slice".to_string());
        // after the last mac step (before `win:`) and between two items
        for at in [14, 10] {
            assert_eq!(judge_head(&insert(at, STEP)), ok, "at {at}");
        }
        // comment lines above the step, a time limit, a plain cargo test line
        for step in [
            format!("      # aib-slice: what these knives are\n      # second line\n{STEP}"),
            STEP.replace("shell: bash", "shell: bash\n        timeout-minutes: 20"),
            with_run("cargo test -p aib-slice --locked --test sweep one_seat_only"),
        ] {
            assert_eq!(judge_head(&insert(14, &step)), ok, "{step}");
        }
    }

    #[test]
    fn anything_but_one_added_step_is_literal() {
        let refused = |head: String, rule: &str| {
            let r = judge_head(&head);
            assert!(
                r.as_ref().is_err_and(|e| e.starts_with(rule)),
                "want {rule}: {r:?}\n{head}"
            );
        };
        // rule 2: a changed line besides the step (needs:, on:)
        refused(
            insert(14, STEP).replace("needs: [lint]", "needs: []"),
            "rule 2",
        );
        refused(insert(14, STEP).replace("[main]", "[main, dev]"), "rule 2");
        refused(BASE.replace("--locked", ""), "rule 2");
        // rule 3: two steps, a line back at the job level, no name first, a comment at
        // another indentation, an indentation that is not plain spaces (a9136 L3)
        refused(insert(14, &format!("{STEP}{STEP}")), "rule 3");
        refused(insert(14, &format!("{STEP}    env: {{X: 1}}\n")), "rule 3");
        refused(
            insert(14, &STEP.replace("- name:", "- run: x\n        name:")),
            "rule 3",
        );
        refused(insert(14, &format!("    # note\n{STEP}")), "rule 3");
        refused(
            insert(14, &STEP.replace("        shell:", "        \u{a0}shell:")),
            "rule 3",
        );
        refused(
            insert(14, &STEP.replace("        shell:", "\t        shell:")),
            "rule 3",
        );
        // rule 4: in the matrix list (an item of `include:`, not of `steps:`), at the job
        // level, or in the middle of the step before
        refused(
            insert(
                21,
                "          - name: c\n            run: cargo x -p aib-slice\n",
            ),
            "rule 4",
        );
        refused(insert(14, &STEP.replace("      ", "    ")), "rule 4");
        refused(insert(13, STEP), "rule 4");
        // rule 5: another crate
        refused(insert(14, &STEP.replace("aib-slice", "aib-foo")), "rule 5");
        // rule 5: a run line that is not a knife call or `cargo test -p <crate> --locked`
        // (a9136 M2: a second cargo, --all, cargo by its path; M3: any other command)
        for line in [
            "echo hi",
            "cargo test --locked",
            "cargo test -p aib-slice",
            "cargo test -p aib-slice --locked --workspace",
            "cargo test -p aib-slice --locked --all",
            "cargo test -p aib-slice --locked && cargo test",
            "cargo test -p aib-slice --locked; cargo test",
            "cargo build -p aib-slice --locked",
            "$HOME/.cargo/bin/cargo test",
            "rm -rf crates/aibd/tests # crates/aib-slice/",
            "echo A=1 >> $GITHUB_ENV",
            "python3 -I scripts/knife.py crates/aib-slice/src/lib.rs a b",
            // a call whose arguments the shell would expand or run
            "sl \"$(rm -rf x)\" 'b' t",
            "sl 'a' \"`id`\" t",
            "sl 'a' 'b' t; rm -rf x",
            "sl 'a' 'b' t && id",
            "sl 'a' 'b' $T",
            "sl 'a' 'b'\"$T\"",
            "sl 'a' 'b",
            "sl 'a' \\",
            // a helper that is not the knife helper
            "x() { rm -rf crates/aib-slice/src; }",
            "x() { python3 -I scripts/knife.py crates/aib-slice/../aibd/src/lib.rs \"$1\" \\",
            "x() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs \"$1\" $2 \\",
        ] {
            refused(insert(14, &with_run(line)), "rule 5");
        }
        // rule 5: a helper whose second line runs something else
        refused(
            insert(14, &STEP.replace("--lib \"$3\"; }", "--lib \"$3\"; id; }")),
            "rule 5",
        );
        refused(
            insert(14, &STEP.replace("--locked --lib", "--locked --all --lib")),
            "rule 5",
        );
        // rule 5: a key a knife step does not have (M3: another shell, env, an action …)
        for (from, to) in [
            ("shell: bash", "shell: python {0}"),
            ("shell: bash", "shell: bash\n        env:\n          A: b"),
            ("shell: bash", "continue-on-error: true"),
            ("shell: bash", "uses: x/y@v1"),
            ("shell: bash", "working-directory: crates/aibd"),
            (
                "if: env.RUN == 'true' && runner.os == 'macOS'",
                "if: always()",
            ),
            ("        run: |\n", "        run: >\n"),
            ("        set -eu\n", "        set -eu\n        shell: sh\n"),
        ] {
            assert!(STEP.contains(from), "{from}");
            refused(insert(14, &STEP.replace(from, to)), "rule 5");
        }
        refused(
            insert(14, "      - name: e\n        shell: bash\n"),
            "rule 5",
        );
        refused(
            insert(14, "      - name: e\n        run: |\n          set -eu\n"),
            "rule 5",
        );
        // rule 6: an expression (the PR title into the script)
        refused(
            insert(
                14,
                &with_run("sl 'a' '${{ github.event.pull_request.title }}' t"),
            ),
            "rule 6",
        );
    }

    /// a9136 L1: `#` lines left of the step are comments; deeper they are the rest of the
    /// run block the step was put into.
    #[test]
    fn a_step_put_into_a_run_block_is_literal_even_before_hash_lines() {
        let base = BASE.replace("--locked\n  win:", "--locked\n          # tail\n  win:");
        assert_ne!(base, BASE);
        let r = judge(
            &wf(),
            Some(&base),
            Some(&insert_into(&base, 14, STEP)),
            &own(),
        );
        assert!(r.as_ref().is_err_and(|e| e.starts_with("rule 4")), "{r:?}");
        // a comment at the list's indentation after the step is fine
        let base = BASE.replace("--locked\n  win:", "--locked\n      # next\n  win:");
        let r = judge(
            &wf(),
            Some(&base),
            Some(&insert_into(&base, 14, STEP)),
            &own(),
        );
        assert!(r.is_ok(), "{r:?}");
    }

    #[test]
    fn only_aibd_yml_present_on_both_sides_is_judged() {
        let head = insert(14, STEP);
        let r = |files: Vec<&str>, base: Option<&str>| {
            let files: Vec<String> = files.into_iter().map(str::to_owned).collect();
            judge(&files, base, Some(&head), &own())
        };
        let rule1 = |x: Result<String, String>| x.is_err_and(|e| e.starts_with("rule 1"));
        assert!(rule1(r(vec![ONLY, "xtask/src/main.rs"], Some(BASE))));
        assert!(rule1(r(vec!["xtask/src/main.rs"], Some(BASE))));
        // a9136 M1: a workflow whose jobs may write (release) is never judged by content
        assert!(rule1(r(
            vec![".github/workflows/aibd-release.yml"],
            Some(BASE)
        )));
        assert!(rule1(r(vec![".github/workflows/sub/aibd.yml"], Some(BASE))));
        assert!(rule1(r(vec![ONLY], None)));
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
