// SPDX-License-Identifier: Apache-2.0
//! aib-seathook — the seat-side checks in one place (DESIGN-seat-hook-56cda-1117 r4;
//! PLAN-gates-56cda-1117 片 1). This first part is the git `pre-push` check:
//!
//! 1. `freeze`: `~/.ez2-aib/pending/PUSH-FREEZE` exists ⇒ its first line is the reason (RULES §27).
//! 2. `leak`: the local leak scan (`aib-public-leak --local-sources --pre-push`) on the same refs.
//! 3. `fmt`: `cargo fmt --all -- --check` at the repository top, when there is a Cargo.toml.
//! 4. `workflow`: lines under `.github/` that a pushed ref deletes against `origin/main`
//!    (merge-theirs-drops-own-branch-content: a whole-file `--theirs` dropped #767's steps).
//!
//! Mode is `warn` (print, exit 0) unless the config says `block`; 519d0 11-17 04:00: warn first,
//! block only after the two integration PRs are in main and f1fae agrees. A check that cannot run
//! (program missing, git failed) is printed as `error` and never blocks in either mode, so a broken
//! check does not stop a push silently or loudly (design §2: one check broken never stops the others).
//!
//! No I/O in the pure parts below; `main.rs` runs the programs.

use std::fmt;

/// What one check found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Pass,
    /// Would stop the push in block mode.
    Fail(String),
    /// The check could not run; printed, never blocks.
    Error(String),
}

impl Verdict {
    pub fn word(&self) -> &'static str {
        match self {
            Verdict::Pass => "pass",
            Verdict::Fail(_) => "fail",
            Verdict::Error(_) => "error",
        }
    }
    pub fn why(&self) -> &str {
        match self {
            Verdict::Pass => "",
            Verdict::Fail(w) | Verdict::Error(w) => w,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Warn,
    Block,
}

impl fmt::Display for Mode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Mode::Warn => "warn",
            Mode::Block => "block",
        })
    }
}

/// `pre_push = "block"` (or `'block'`, spaces free) in the config text ⇒ Block; anything else,
/// a missing key or a missing file ⇒ Warn. Comments (`#`) and other keys are ignored.
pub fn mode_from_config(text: &str) -> Mode {
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        if k.trim() == "pre_push" {
            let v = v.trim().trim_matches(|c| c == '"' || c == '\'');
            return if v == "block" {
                Mode::Block
            } else {
                Mode::Warn
            };
        }
    }
    Mode::Warn
}

/// The exit code of the whole hook: block mode and at least one `Fail` ⇒ 1, otherwise 0.
/// `Error` never counts.
pub fn exit_code(mode: Mode, verdicts: &[(&str, Verdict)]) -> i32 {
    let failed = verdicts.iter().any(|(_, v)| matches!(v, Verdict::Fail(_)));
    i32::from(mode == Mode::Block && failed)
}

/// The freeze file's verdict from its contents (`None` = no file): the first non-empty line is the
/// reason; an empty file still freezes.
pub fn freeze_verdict(contents: Option<&str>) -> Verdict {
    match contents {
        None => Verdict::Pass,
        Some(t) => {
            let why = t
                .lines()
                .map(str::trim)
                .find(|l| !l.is_empty())
                .unwrap_or("(no reason written)");
            Verdict::Fail(format!("PUSH-FREEZE: {why}"))
        }
    }
}

/// One line of a pre-push hook's standard input.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pushed {
    pub local_ref: String,
    pub local_sha: String,
    pub remote_ref: String,
}

/// `<local ref> <local sha> <remote ref> <remote sha>` per line; a deleted ref (local sha all
/// zeros) is left out, it pushes no content. Lines that do not have four fields are skipped.
pub fn parse_pushed(stdin: &str) -> Vec<Pushed> {
    stdin
        .lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            if f.len() != 4 || f[1].bytes().all(|b| b == b'0') {
                return None;
            }
            Some(Pushed {
                local_ref: f[0].to_owned(),
                local_sha: f[1].to_owned(),
                remote_ref: f[2].to_owned(),
            })
        })
        .collect()
}

/// Lines deleted in `git diff --numstat` output; binary rows (`-`) count 0.
pub fn deleted_lines(numstat: &str) -> u64 {
    numstat
        .lines()
        .filter_map(|l| l.split('\t').nth(1)?.parse::<u64>().ok())
        .sum()
}

/// The workflow check for one ref: deleted lines > 0 ⇒ Fail naming the ref and the count.
pub fn workflow_verdict(remote_ref: &str, deleted: u64) -> Verdict {
    if deleted == 0 {
        Verdict::Pass
    } else {
        Verdict::Fail(format!(
            "{remote_ref} deletes {deleted} line(s) under .github/ against origin/main; check each one is meant"
        ))
    }
}

/// One printed line per check: `seathook pre-push <mode> <check> <verdict>[: why]`.
pub fn report_line(mode: Mode, check: &str, v: &Verdict) -> String {
    if v.why().is_empty() {
        format!("seathook pre-push {mode} {check} {}", v.word())
    } else {
        format!("seathook pre-push {mode} {check} {}: {}", v.word(), v.why())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn block_only_from_an_explicit_key() {
        assert_eq!(mode_from_config(""), Mode::Warn);
        assert_eq!(mode_from_config("pre_push = \"warn\"\n"), Mode::Warn);
        assert_eq!(mode_from_config("# pre_push = \"block\"\n"), Mode::Warn);
        assert_eq!(mode_from_config("other = \"block\"\n"), Mode::Warn);
        assert_eq!(mode_from_config("pre_push = \"block\"\n"), Mode::Block);
        assert_eq!(
            mode_from_config("  pre_push='block'  # f1fae ok\n"),
            Mode::Block
        );
    }

    #[test]
    fn warn_never_stops_and_errors_never_count() {
        let fail = [("freeze", Verdict::Fail("x".into()))];
        let err = [("leak", Verdict::Error("not run".into()))];
        assert_eq!(exit_code(Mode::Warn, &fail), 0);
        assert_eq!(exit_code(Mode::Block, &fail), 1);
        assert_eq!(exit_code(Mode::Block, &err), 0);
        assert_eq!(exit_code(Mode::Block, &[("fmt", Verdict::Pass)]), 0);
    }

    #[test]
    fn the_freeze_file_freezes_even_when_empty() {
        assert_eq!(freeze_verdict(None), Verdict::Pass);
        assert_eq!(
            freeze_verdict(Some("\n merge window 10:00 \nmore\n")),
            Verdict::Fail("PUSH-FREEZE: merge window 10:00".into())
        );
        assert!(matches!(freeze_verdict(Some("")), Verdict::Fail(_)));
    }

    #[test]
    fn pushed_refs_skip_deletions_and_bad_lines() {
        let z = "0".repeat(40);
        let a = "a".repeat(40);
        let got = parse_pushed(&format!(
            "refs/heads/x {a} refs/heads/x {z}\nrefs/heads/gone {z} refs/heads/gone {a}\nnot a line\n"
        ));
        assert_eq!(
            got,
            vec![Pushed {
                local_ref: "refs/heads/x".into(),
                local_sha: a,
                remote_ref: "refs/heads/x".into()
            }]
        );
    }

    #[test]
    fn deleted_lines_add_up_and_binary_counts_zero() {
        assert_eq!(deleted_lines(""), 0);
        assert_eq!(
            deleted_lines(
                "3\t2\t.github/workflows/aibd.yml\n-\t-\t.github/x.png\n0\t5\t.github/y.yml\n"
            ),
            7
        );
        assert_eq!(workflow_verdict("refs/heads/x", 0), Verdict::Pass);
        assert!(
            matches!(workflow_verdict("refs/heads/x", 7), Verdict::Fail(w) if w.contains("7 line"))
        );
    }

    #[test]
    fn report_lines_name_mode_check_and_reason() {
        assert_eq!(
            report_line(Mode::Warn, "fmt", &Verdict::Pass),
            "seathook pre-push warn fmt pass"
        );
        assert_eq!(
            report_line(
                Mode::Block,
                "leak",
                &Verdict::Error("aib-public-leak not found".into())
            ),
            "seathook pre-push block leak error: aib-public-leak not found"
        );
    }
}
